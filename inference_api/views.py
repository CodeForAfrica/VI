"""HTTP endpoints for the inference API (solution-spec 250-450).

Reject-path ordering is cheapest-and-most-generic first: oversized bodies are
dropped before auth, auth before any work, validation last. Every response
carries the caller's request_id where known so one attempt can be traced across
Lambda and API (spec 187-192).
"""
import json
import os
import time
import uuid

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import runtime
from .logs import (
    log_event, reset_request_context, safe_error, safe_traceback,
    set_request_context,
)
from .security import authenticate, rate_limiter

MAX_BODY_BYTES = int(os.getenv("VI_MAX_BODY_BYTES", str(256 * 1024)))  # spec 467
MAX_TEXT_CHARS = int(os.getenv("VI_MAX_TEXT_CHARS", "200000"))  # re-check, spec 468


def _error(status, code, message, request_id=None):
    body = {"error": {"code": code, "message": message}}
    if request_id:
        body["error"]["request_id"] = request_id
    return JsonResponse(body, status=status)


@require_http_methods(["GET"])
def healthz(request):
    # Liveness only - must be fast and must not touch the models (spec 263-264).
    return JsonResponse({"status": "ok"})


@require_http_methods(["GET"])
def readyz(request):
    if runtime.is_ready():
        return JsonResponse({
            "status": "ready",
            "models_loaded": True,
            "model_version": runtime.MODEL_VERSION,
        })
    return JsonResponse({"status": "loading", "models_loaded": False}, status=503)


@csrf_exempt
@require_http_methods(["POST"])
def inference(request):
    trace_token = set_request_context(trace_id=str(uuid.uuid4()))
    try:
        return _handle_inference(request)
    finally:
        reset_request_context(trace_token)


def _handle_inference(request):
    started = time.time()
    caller = None
    request_id = None

    # 1. Body size - guard memory before reading anything (spec 467).
    declared = request.META.get("CONTENT_LENGTH")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        log_event("WARNING", "payload_too_large", reason="content_length",
                  http_status=413,
                  duration_ms=int((time.time() - started) * 1000))
        return _error(413, "payload_too_large",
                      "The request body exceeds the configured limit")

    # 2. Authentication (spec 382-398) - generic 401, never say why.
    caller = authenticate(request.headers.get("X-API-Key"))
    if not caller:
        log_event("WARNING", "authentication_failed", http_status=401,
                  duration_ms=int((time.time() - started) * 1000))
        return _error(401, "unauthorized", "Authentication failed")
    set_request_context(caller=caller)
    log_event("INFO", "authentication_succeeded")

    # Count every authenticated request, including malformed ones.
    if not rate_limiter.allow(caller):
        log_event("WARNING", "rate_limit_exceeded", http_status=429,
                  duration_ms=int((time.time() - started) * 1000))
        resp = _error(429, "rate_limit_exceeded", "Too many inference requests")
        resp["Retry-After"] = "60"
        return resp
    log_event("INFO", "rate_limit_checked", status="allowed")

    # 3. Content-Type (spec 466).
    if not request.content_type or "application/json" not in request.content_type:
        log_event("WARNING", "inference_request_invalid", caller=caller,
                  error_code="invalid_content_type", http_status=400,
                  duration_ms=int((time.time() - started) * 1000))
        return _error(400, "invalid_request", "Content-Type must be application/json")

    # 4. Actual body size + JSON parse.
    body = request.body
    if len(body) > MAX_BODY_BYTES:
        log_event("WARNING", "payload_too_large", caller=caller, reason="body",
                  http_status=413,
                  duration_ms=int((time.time() - started) * 1000))
        return _error(413, "payload_too_large",
                      "The request body exceeds the configured limit")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        log_event("WARNING", "inference_request_invalid", caller=caller,
                  error_code="invalid_json", http_status=400,
                  duration_ms=int((time.time() - started) * 1000))
        return _error(400, "invalid_request", "Request body is not valid JSON")
    if not isinstance(payload, dict):
        log_event("WARNING", "inference_request_invalid", caller=caller,
                  error_code="invalid_json_type", http_status=400,
                  duration_ms=int((time.time() - started) * 1000))
        return _error(400, "invalid_request", "Request body must be a JSON object")

    # 5. Required fields (spec 315-318).
    request_id = payload.get("request_id")
    article_text = payload.get("article_text")
    if not isinstance(request_id, str):
        log_event("WARNING", "inference_request_invalid", caller=caller,
                  error_code="invalid_request_id", http_status=400,
                  duration_ms=int((time.time() - started) * 1000))
        return _error(400, "invalid_request", "request_id is required")
    try:
        uuid.UUID(request_id)
    except (ValueError, AttributeError):
        log_event("WARNING", "inference_request_invalid", caller=caller,
                  error_code="invalid_request_id", http_status=400,
                  duration_ms=int((time.time() - started) * 1000))
        return _error(400, "invalid_request", "request_id must be a UUID")
    set_request_context(request_id=request_id)
    if not isinstance(article_text, str) or not article_text.strip():
        log_event("WARNING", "inference_request_invalid", caller=caller,
                  request_id=request_id, error_code="invalid_article_text",
                  http_status=400,
                  duration_ms=int((time.time() - started) * 1000))
        return _error(400, "invalid_request", "article_text is required", request_id)

    # 6. Text length re-check before tokenization (spec 468).
    if len(article_text) > MAX_TEXT_CHARS:
        log_event("WARNING", "payload_too_large", caller=caller, request_id=request_id,
                  reason="article_text", http_status=413,
                  duration_ms=int((time.time() - started) * 1000))
        return _error(413, "payload_too_large",
                      "The request body exceeds the configured limit", request_id)

    article_id = payload.get("article_id")
    if (article_id is not None
            and (not isinstance(article_id, (str, int))
                 or (isinstance(article_id, str) and len(article_id) > 128))):
        log_event("WARNING", "inference_request_invalid", caller=caller,
                  request_id=request_id, error_code="invalid_article_id",
                  http_status=400,
                  duration_ms=int((time.time() - started) * 1000))
        return _error(400, "invalid_request",
                      "article_id must be an integer or a string up to 128 characters",
                      request_id)
    set_request_context(article_id=article_id)

    # 8. Readiness - models may still be loading (spec 416-431).
    if not runtime.is_ready():
        log_event("WARNING", "models_not_ready", caller=caller, request_id=request_id,
                  error_code="models_not_ready", http_status=503,
                  duration_ms=int((time.time() - started) * 1000),
                  model_version=runtime.MODEL_VERSION)
        resp = _error(503, "models_not_ready", "The inference service is not ready",
                      request_id)
        resp["Retry-After"] = "30"
        return resp

    log_event("INFO", "inference_request_validated", caller=caller,
              request_id=request_id, article_id=article_id)
    log_event("INFO", "inference_request_started", caller=caller,
              request_id=request_id, article_id=article_id)

    # 9. Run - failures are explicit errors, never a bogus Neutral.
    try:
        result = runtime.run_inference(article_text)
    except Exception as e:  # noqa: BLE001
        error_code = getattr(e, "code", "inference_failed")
        status = 422 if error_code in runtime.MODEL_CONTRACT_ERROR_CODES else 500
        log_event("ERROR", "inference_failed", caller=caller, request_id=request_id,
                  article_id=article_id, error_type=type(e).__name__,
                  error_code=error_code, error_detail=safe_error(e, article_text),
                  stack_trace=safe_traceback(article_text), http_status=status,
                  duration_ms=int((time.time() - started) * 1000),
                  model_version=runtime.MODEL_VERSION)
        return _error(status, error_code, "Inference could not be completed",
                      request_id)

    response = {"request_id": request_id, "article_id": article_id, **result}
    log_event("INFO", "inference_completed", caller=caller,
              request_id=request_id, article_id=article_id, status="success",
              http_status=200,
              duration_ms=int((time.time() - started) * 1000),
              model_version=result["model_version"],
              strategic_intent=result["strategic_intent"], tone=result["tone"],
              prediction_source=result["prediction_source"])
    return JsonResponse(response)

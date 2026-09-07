"""HTTP endpoints for the inference API (solution-spec 250-450).

Reject-path ordering is cheapest-and-most-generic first: oversized bodies are
dropped before auth, auth before any work, validation last. Every response
carries the caller's request_id where known so one attempt can be traced across
Lambda and API (spec 187-192).
"""
import json
import os

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import runtime
from .logs import log_event
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
    caller = None
    request_id = None

    # 1. Body size - guard memory before reading anything (spec 467).
    declared = request.META.get("CONTENT_LENGTH")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        log_event("WARNING", "payload_too_large", reason="content_length")
        return _error(413, "payload_too_large",
                      "The request body exceeds the configured limit")

    # 2. Authentication (spec 382-398) - generic 401, never say why.
    caller = authenticate(request.headers.get("X-API-Key"))
    if not caller:
        log_event("WARNING", "authentication_failed")
        return _error(401, "unauthorized", "Authentication failed")

    # 3. Rate limit by authenticated caller (spec 531-562).
    if not rate_limiter.allow(caller):
        log_event("WARNING", "rate_limit_exceeded", caller=caller)
        resp = _error(429, "rate_limit_exceeded", "Too many inference requests")
        resp["Retry-After"] = "60"
        return resp

    # 4. Content-Type (spec 466).
    if not request.content_type or "application/json" not in request.content_type:
        return _error(400, "invalid_request", "Content-Type must be application/json")

    # 5. Actual body size + JSON parse.
    body = request.body
    if len(body) > MAX_BODY_BYTES:
        log_event("WARNING", "payload_too_large", caller=caller, reason="body")
        return _error(413, "payload_too_large",
                      "The request body exceeds the configured limit")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error(400, "invalid_request", "Request body is not valid JSON")
    if not isinstance(payload, dict):
        return _error(400, "invalid_request", "Request body must be a JSON object")

    # 6. Required fields (spec 315-318).
    request_id = payload.get("request_id")
    article_text = payload.get("article_text")
    if not request_id:
        return _error(400, "invalid_request", "request_id is required")
    if not article_text or not str(article_text).strip():
        return _error(400, "invalid_request", "article_text is required", request_id)

    # 7. Text length re-check before tokenization (spec 468).
    if len(article_text) > MAX_TEXT_CHARS:
        log_event("WARNING", "payload_too_large", caller=caller, request_id=request_id,
                  reason="article_text")
        return _error(413, "payload_too_large",
                      "The request body exceeds the configured limit", request_id)

    # 8. Readiness - models may still be loading (spec 416-431).
    if not runtime.is_ready():
        log_event("WARNING", "models_not_ready", caller=caller, request_id=request_id)
        resp = _error(503, "models_not_ready", "The inference service is not ready",
                      request_id)
        resp["Retry-After"] = "30"
        return resp

    article_id = payload.get("article_id")
    log_event("INFO", "inference_request_started", caller=caller,
              request_id=request_id, article_id=article_id)

    # 9. Run - a failure is a 500, never a bogus Neutral (spec 449-450).
    try:
        result = runtime.run_inference(article_text)
    except Exception as e:  # noqa: BLE001
        log_event("ERROR", "inference_failed", caller=caller, request_id=request_id,
                  article_id=article_id, error_type=type(e).__name__)
        return _error(500, "inference_failed", "Inference could not be completed",
                      request_id)

    response = {"request_id": request_id, "article_id": article_id, **result}
    log_event("INFO", "inference_request_completed", caller=caller,
              request_id=request_id, article_id=article_id, status="success",
              duration_ms=result["processing_time_ms"],
              model_version=result["model_version"])
    return JsonResponse(response)

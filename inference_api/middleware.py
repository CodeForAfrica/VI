"""Request-wide structured access logging for the inference service."""
import time
import uuid

from .logs import log_event, reset_request_context, set_request_context


class AccessLogMiddleware:
    """Emit one safe ``http_access`` event for every request and response.

    This sits before Django's other middleware, so health checks, readiness
    checks, rejected methods, unknown routes, authentication failures and
    unhandled server errors all produce an access record. Query strings,
    headers and bodies are deliberately excluded.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.monotonic()
        context_token = set_request_context(trace_id=str(uuid.uuid4()))
        try:
            response = self.get_response(request)
            status = response.status_code
            if status >= 500:
                level, outcome = "ERROR", "failed"
            elif status >= 400:
                level, outcome = "WARNING", "rejected"
            else:
                level, outcome = "INFO", "success"
            log_event(
                level,
                "http_access",
                http_method=request.method,
                path=request.path,
                http_status=status,
                outcome=outcome,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return response
        except Exception as exc:
            log_event(
                "ERROR",
                "http_access",
                http_method=request.method,
                path=request.path,
                http_status=500,
                outcome="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
                error_type=type(exc).__name__,
                error_code="unhandled_request_error",
            )
            raise
        finally:
            reset_request_context(context_token)

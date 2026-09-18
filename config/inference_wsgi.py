"""WSGI entrypoint for the inference API process.

Boots Django with the small inference urlconf instead of the dashboard's, and
kicks the background model warmup so /healthz answers immediately while models
load and /readyz flips to ready once they are usable.

Run with:  gunicorn --workers 1 --threads 2 --timeout 180 \
             --bind 0.0.0.0:8000 config.inference_wsgi:application
"""
import os
import atexit

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ["VI_INFERENCE_SERVER"] = "1"
# settings.ROOT_URLCONF reads this env var (see config/settings.py).
os.environ["ROOT_URLCONF"] = "inference_api.urls"

from inference_api.logs import (  # noqa: E402
    install_stdlib_logging, install_stdout_capture, log_event, safe_error,
    safe_traceback,
)

install_stdlib_logging()
log_event("INFO", "api_starting")

from django.core.wsgi import get_wsgi_application  # noqa: E402

try:
    application = get_wsgi_application()
except Exception as exc:
    log_event("ERROR", "api_start_failed", error_type=type(exc).__name__,
              error_code="api_start_failed", error_detail=safe_error(exc),
              stack_trace=safe_traceback())
    raise
install_stdlib_logging()  # Django setup may replace the root handlers.
log_event("INFO", "api_started")
install_stdout_capture()

from inference_api.security import accepted_key_count  # noqa: E402

configured_key_count = accepted_key_count()
log_event("INFO" if configured_key_count else "ERROR",
          "authentication_config_loaded" if configured_key_count
          else "authentication_config_invalid",
          accepted_key_count=configured_key_count,
          error_code=None if configured_key_count else "no_accepted_api_keys")

from inference_api.runtime import start_warmup  # noqa: E402

start_warmup()


def _log_shutdown():
    log_event("INFO", "api_shutdown_started")
    log_event("INFO", "api_shutdown_completed")


atexit.register(_log_shutdown)

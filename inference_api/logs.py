"""Structured JSON logging for the inference API (solution-spec 164-247).

One line of JSON per event on stdout/stderr so CloudWatch/Dokku capture logs
with no extra runtime service. The caller is responsible for never passing the
API key, credentials, or full article text (spec 238-247) - this module does not
scrub, it just refuses to make free-form logging easy.
"""
import json
import logging
import os
import re
import sys
import threading
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone

SERVICE = "vi-model-inference"

# WARNING/ERROR go to stderr, everything else to stdout.
_ERR_LEVELS = {"WARNING", "ERROR"}
_REQUEST_CONTEXT = ContextVar("vi_inference_request_context", default={})
_STDOUT = sys.stdout
_STDERR = sys.stderr
_SENSITIVE_FIELD_PARTS = (
    "api_key", "accepted_key", "authorization", "password", "secret", "access_key",
    "session_token", "article_text", "request_body", "response_body",
)


def _redact_text(value, extra_values=()):
    text = str(value)
    secrets = list(extra_values)
    for name, env_value in os.environ.items():
        lowered = name.lower().replace("-", "_")
        if env_value and any(part in lowered for part in _SENSITIVE_FIELD_PARTS):
            secrets.append(env_value)
            if "accepted_key" in lowered:
                try:
                    accepted_keys = json.loads(env_value)
                except (TypeError, ValueError):
                    accepted_keys = []
                if isinstance(accepted_keys, list):
                    secrets.extend(
                        item.get("key") for item in accepted_keys
                        if isinstance(item, dict) and item.get("key")
                    )
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "[REDACTED]")
    # Cover credentials embedded in URLs even when the exact password is not in env.
    return re.sub(r"(://[^:/\s]+:)[^@/\s]+@", r"\1[REDACTED]@", text)


def safe_error(exc, *redact_values):
    """A bounded diagnostic message with known sensitive values removed."""
    return _redact_text(str(exc), redact_values)[:500]


def safe_traceback(*redact_values):
    """Current traceback, bounded and redacted for server-side diagnostics."""
    return _redact_text(traceback.format_exc(limit=20), redact_values)[-8000:]


def _sanitize(fields):
    def clean_value(key, value):
        lowered = str(key).lower().replace("-", "_")
        if lowered == "key" or any(part in lowered for part in _SENSITIVE_FIELD_PARTS):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {nested_key: clean_value(nested_key, nested_value)
                    for nested_key, nested_value in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean_value(key, item) for item in value]
        if isinstance(value, str):
            return _redact_text(value)
        return value

    clean = {}
    for key, value in fields.items():
        clean[key] = clean_value(key, value)
    return clean


def set_request_context(**fields):
    merged = dict(_REQUEST_CONTEXT.get())
    merged.update(fields)
    return _REQUEST_CONTEXT.set(merged)


def reset_request_context(token):
    _REQUEST_CONTEXT.reset(token)


def log_event(level, event, **fields):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "level": level,
        "service": SERVICE,
        "event": event,
    }
    entry.update(_sanitize(_REQUEST_CONTEXT.get()))
    entry.update(_sanitize(fields))
    stream = _STDERR if level in _ERR_LEVELS else _STDOUT
    stream.write(json.dumps(entry) + "\n")
    stream.flush()


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "service": SERVICE,
            "event": "application_log",
            "logger": record.name,
            "message": _redact_text(record.getMessage())[:1000],
        }
        entry.update(_sanitize(_REQUEST_CONTEXT.get()))
        if record.exc_info:
            entry["stack_trace"] = _redact_text(
                "".join(traceback.format_exception(*record.exc_info))
            )[-8000:]
        return json.dumps(entry)


def install_stdlib_logging():
    """Make application logger output structured and request-correlated."""
    root = logging.getLogger()
    handler = logging.StreamHandler(_STDERR)
    handler.setFormatter(_JsonFormatter())
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # Django settings give this legacy logger a private console handler and
    # propagate=False. Route it through the structured root logger so its model
    # diagnostics receive request context and the same redaction guarantees.
    ml_logger = logging.getLogger("dashboard.services.ml_inference_service")
    ml_logger.handlers = []
    ml_logger.propagate = True


class _StructuredStdout:
    """Turn legacy print output into structured, request-correlated JSON."""
    encoding = getattr(_STDOUT, "encoding", "utf-8")

    def __init__(self):
        self._buffer = ""
        self._lock = threading.Lock()

    def write(self, value):
        if not value:
            return 0
        with self._lock:
            self._buffer += str(value)
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line.strip():
                    entry = {
                        "timestamp": datetime.now(timezone.utc)
                        .isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                        "level": "INFO",
                        "service": SERVICE,
                        "event": "application_stdout",
                        "message": _redact_text(line)[:1000],
                    }
                    entry.update(_sanitize(_REQUEST_CONTEXT.get()))
                    _STDOUT.write(json.dumps(entry) + "\n")
                    _STDOUT.flush()
        return len(value)

    def flush(self):
        with self._lock:
            if self._buffer.strip():
                entry = {
                    "timestamp": datetime.now(timezone.utc)
                    .isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    "level": "INFO", "service": SERVICE,
                    "event": "application_stdout",
                    "message": _redact_text(self._buffer)[:1000],
                }
                entry.update(_sanitize(_REQUEST_CONTEXT.get()))
                _STDOUT.write(json.dumps(entry) + "\n")
                self._buffer = ""
            _STDOUT.flush()

    def isatty(self):
        return False


def install_stdout_capture():
    if not isinstance(sys.stdout, _StructuredStdout):
        sys.stdout = _StructuredStdout()

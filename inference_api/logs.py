"""Structured JSON logging for the inference API (solution-spec 164-247).

One line of JSON per event on stdout/stderr so CloudWatch/Dokku capture logs
with no extra runtime service. The caller is responsible for never passing the
API key, credentials, or full article text (spec 238-247) - this module does not
scrub, it just refuses to make free-form logging easy.
"""
import json
import sys
import time

SERVICE = "vi-model-inference"

# WARNING/ERROR go to stderr, everything else to stdout.
_ERR_LEVELS = {"WARNING", "ERROR"}


def log_event(level, event, **fields):
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level,
        "service": SERVICE,
        "event": event,
    }
    entry.update(fields)
    stream = sys.stderr if level in _ERR_LEVELS else sys.stdout
    stream.write(json.dumps(entry) + "\n")
    stream.flush()

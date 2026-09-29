"""API-key auth + per-caller rate limiting (solution-spec 452-562).

Auth: an accepted-key list, constant-time compare, generic failure. The matched
caller name is returned for rate limiting and audit logs; the key itself is
never returned or logged.

Rate limit: in-process fixed-window counter keyed by caller. No Redis/queue -
hard constraint (spec 539-542); counters reset on restart, which is acceptable.
"""
import hmac
import json
import os
import threading
import time

# Parsed once at import; restart the process after a manual key change.
# Deployment injects the accepted-key configuration from Secrets Manager.
def _load_accepted_keys():
    """[{caller, key}] from VI_INFERENCE_ACCEPTED_KEYS (JSON), spec 489-496."""
    raw = os.getenv("VI_INFERENCE_ACCEPTED_KEYS", "").strip()
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(entries, list):
        return []
    return [
        {"caller": e["caller"], "key": e["key"]}
        for e in entries
        if (isinstance(e, dict)
            and isinstance(e.get("caller"), str) and e["caller"].strip()
            and isinstance(e.get("key"), str) and e["key"])
    ]


_ACCEPTED_KEYS = _load_accepted_keys()


def accepted_key_count():
    return len(_ACCEPTED_KEYS)


def authenticate(supplied_key):
    """Return the caller name for a valid key, else None.

    Compares against every accepted key with a constant-time compare so a
    non-match costs the same as a match (spec 511, 523-524). We still iterate
    all keys on a hit to keep timing uniform across which key matched.
    """
    if not supplied_key:
        return None
    matched = None
    for entry in _ACCEPTED_KEYS:
        if hmac.compare_digest(supplied_key, entry["key"]):
            matched = entry["caller"]
    return matched


class RateLimiter:
    """Fixed 60s window per caller.

    ponytail: fixed-window, so a caller can burst up to 2x the limit across a
    window boundary. Good enough for a single-instance internal API; swap for a
    sliding window only if abuse is observed.
    """

    def __init__(self, rpm):
        self.rpm = rpm
        self._lock = threading.Lock()
        self._hits = {}  # caller -> [window_start, count]

    def allow(self, caller):
        window = int(time.time() // 60)
        with self._lock:
            state = self._hits.get(caller)
            if state is None or state[0] != window:
                self._hits[caller] = [window, 1]
                return True
            state[1] += 1
            return state[1] <= self.rpm


rate_limiter = RateLimiter(int(os.getenv("VI_RATE_LIMIT_REQUESTS_PER_MINUTE", "60")))

"""Boot smoke for the inference API: prove config.inference_wsgi:application
serves the contract through gunicorn under the real config.settings.

Run against a booted server (models NOT loaded - VI_SKIP_WARMUP=1), so /readyz
reports loading and inference is gated. This checks wiring and the HTTP
contract; the real model warmup on real weights is Phase 5.

Usage: python scripts/smoke_inference.py http://host:8000 <api-key>
"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
API_KEY = sys.argv[2] if len(sys.argv) > 2 else "smoke-key"
REQUEST_ID = "7d86d12d-dc93-44da-8e03-06ba1aab36cc"

ok = 0


def check(label, cond):
    global ok
    if not cond:
        print(f"  FAIL: {label}")
        sys.exit(1)
    ok += 1
    print(f"  ok: {label}")


def call(method, path, body=None, headers=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


s, b = call("GET", "/healthz")
check("healthz 200 ok", s == 200 and b.get("status") == "ok")

s, b = call("GET", "/readyz")
check("readyz 503 loading (warmup skipped)", s == 503 and b.get("models_loaded") is False)

s, b = call("POST", "/api/v1/inference",
            {"request_id": REQUEST_ID, "article_text": "hi"},
            {"Content-Type": "application/json"})
check("inference without key -> 401", s == 401 and b["error"]["code"] == "unauthorized")

s, b = call("POST", "/api/v1/inference",
            {"request_id": REQUEST_ID, "article_text": "hi"},
            {"Content-Type": "application/json", "X-API-Key": API_KEY})
check("valid key but not ready -> 503 models_not_ready",
      s == 503 and b["error"]["code"] == "models_not_ready")

print(f"\nBOOT SMOKE PASSED ({ok} checks) - gunicorn serves the contract under real settings")

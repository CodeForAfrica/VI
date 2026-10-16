"""Boot the uninstrumented production WSGI entrypoint inside the checked image."""
import os
import signal
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen


def wait_for_startup(process, attempts=30):
    address = "http://127.0.0.1:8765/chat/"
    for attempt in range(1, attempts + 1):
        if process.poll() is not None:
            raise RuntimeError(f"Production Gunicorn exited with code {process.returncode}; inspect its import/startup traceback above")
        try:
            with urlopen(address, timeout=2) as response:
                if response.status == 200:
                    print("[startup] PASS: production config.wsgi served /chat/ with HTTP 200; runtime imports/template loaded.", flush=True)
                    return
        except (URLError, TimeoutError) as error:
            if attempt == 1 or attempt % 5 == 0:
                print(f"[startup] Waiting for production WSGI (attempt {attempt}/{attempts}): {error}", flush=True)
        time.sleep(1)
    raise RuntimeError("Production WSGI did not become ready; check Gunicorn, missing packages, settings and template errors above")


def main():
    print("[startup] Booting actual gunicorn config.wsgi:application without test instrumentation or live provider calls.", flush=True)
    # A syntactically present, non-secret key allows SDK construction only.
    # Internal-only networking prevents this smoke test contacting providers.
    environment = dict(os.environ, DJANGO_SETTINGS_MODULE="config.settings",
                       GROQ_API_KEY="local-startup-test-not-a-credential")
    process = subprocess.Popen([
        "gunicorn", "config.wsgi:application", "--bind", "127.0.0.1:8765",
        "--workers", "1", "--timeout", "30", "--access-logfile", "-",
    ], env=environment, start_new_session=True)
    try:
        wait_for_startup(process)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        print("[startup] Smoke-test Gunicorn stopped.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        raise SystemExit(f"[startup] FAILED: {error}") from error

"""Exercise the Lambda caller -> HTTPS API -> Postgres split end to end."""
import json
import os
import time
from pathlib import Path

import requests


EXPECTED_ARTICLES = 10
FIXTURE = Path("/fixtures/test_articles.json")
BASE_URL = os.environ["VI_INFERENCE_API_URL"].rstrip("/")


def wait_for_tls_api():
    if not BASE_URL.startswith("https://"):
        raise RuntimeError(f"E2E API must use HTTPS, got {BASE_URL}")
    deadline = time.time() + 120
    last_error = None
    while time.time() < deadline:
        try:
            health = requests.get(f"{BASE_URL}/healthz", timeout=5)
            ready = requests.get(f"{BASE_URL}/readyz", timeout=5)
            if health.status_code == 200 and ready.status_code == 200:
                body = ready.json()
                if body.get("models_loaded") is True:
                    print(
                        json.dumps(
                            {
                                "event": "e2e_https_ready",
                                "url": BASE_URL,
                                "model_version": body.get("model_version"),
                            }
                        ),
                        flush=True,
                    )
                    return
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"local HTTPS inference API did not become ready: {last_error}")


def prepare_database():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from django.core.management import call_command

    call_command("migrate", interactive=False, verbosity=0)
    call_command("loaddata", str(FIXTURE), verbosity=0)


def run_and_verify():
    import lambda_function as lf

    connection = lf.get_db_connection()
    try:
        pending = lf.fetch_pending(connection, EXPECTED_ARTICLES + 1)
        if len(pending) != EXPECTED_ARTICLES:
            raise RuntimeError(
                f"expected {EXPECTED_ARTICLES} fresh fixture rows, found {len(pending)}"
            )
        article_ids = [row[0] for row in pending]
        counts = lf.classify_pending(connection, deadline=time.time() + 900)
        if counts != {
            "found_pending": EXPECTED_ARTICLES,
            "classified": EXPECTED_ARTICLES,
            "left_pending": 0,
            "failed": 0,
        }:
            raise RuntimeError(f"unexpected classification counts: {counts}")

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, strategic_intent, confidence, tone,
                       ml_processed_at IS NOT NULL
                FROM dashboard_medianarrative
                WHERE id = ANY(%s)
                ORDER BY id
                """,
                (article_ids,),
            )
            results = cursor.fetchall()
        if len(results) != EXPECTED_ARTICLES:
            raise RuntimeError(f"expected {EXPECTED_ARTICLES} saved rows, found {len(results)}")
        incomplete = [row[0] for row in results if not row[4] or row[2] is None or not row[3]]
        if incomplete:
            raise RuntimeError(f"classification was not fully persisted for ids: {incomplete}")

        print(
            json.dumps(
                {
                    "event": "split_e2e_passed",
                    "transport": "https",
                    "articles": EXPECTED_ARTICLES,
                    "counts": counts,
                    "results": [
                        {
                            "id": row[0],
                            "strategic_intent": row[1],
                            "confidence": row[2],
                            "tone": row[3],
                            "processed": row[4],
                        }
                        for row in results
                    ],
                }
            ),
            flush=True,
        )
    finally:
        connection.close()


def main():
    wait_for_tls_api()
    prepare_database()
    run_and_verify()


if __name__ == "__main__":
    main()

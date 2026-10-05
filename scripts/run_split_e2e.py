"""Exercise and explain the Lambda -> HTTPS API -> Postgres split end to end."""
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests


EXPECTED_ARTICLES = 10
FIXTURE = Path("/fixtures/test_articles.json")
BASE_URL = os.environ["VI_INFERENCE_API_URL"].rstrip("/")
TOTAL_STEPS = 6


def progress(step, message):
    print(f"[test-split-e2e] STEP {step}/{TOTAL_STEPS}: {message}", flush=True)


def load_fixture_metadata():
    records = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if len(records) != EXPECTED_ARTICLES:
        raise RuntimeError(
            f"expected {EXPECTED_ARTICLES} fixture articles, found {len(records)}"
        )
    return [
        {
            "id": record["pk"],
            "url": record["fields"].get("url") or "",
            "article_text": record["fields"]["article_text"],
        }
        for record in records
    ]


def wait_for_tls_api():
    progress(1, f"waiting for the model API over trusted HTTPS at {BASE_URL}")
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
                    # Retain the original machine-readable readiness event.
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
                    print(
                        "[test-split-e2e] PASS: HTTPS is trusted; liveness and "
                        f"readiness passed; model version={body.get('model_version')}",
                        flush=True,
                    )
                    return
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"HTTPS inference API did not become ready: {last_error}")


def prepare_database(fixture_rows):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from django.core.management import call_command

    progress(2, "migrating the isolated local Postgres database")
    call_command("migrate", interactive=False, verbosity=0)
    print("[test-split-e2e] PASS: database schema is ready", flush=True)

    progress(3, f"loading {len(fixture_rows)} sample articles as fresh pending rows")
    call_command("loaddata", str(FIXTURE), verbosity=0)
    print(
        f"[test-split-e2e] PASS: loaded article ids "
        f"{', '.join(str(row['id']) for row in fixture_rows)}",
        flush=True,
    )


class RecordingInferenceClient:
    """Keep the validated API response for each logical Lambda request."""

    def __init__(self, client):
        self._client = client
        self.responses = []

    def __getattr__(self, name):
        return getattr(self._client, name)

    def infer(self, **kwargs):
        result = self._client.infer(**kwargs)
        self.responses.append(
            {
                "article_text": kwargs["article_text"],
                "request_id": kwargs["request_id"],
                "result": dict(result),
            }
        )
        return result


def _close_enough(actual, expected):
    return abs(float(actual) - float(expected)) <= 1e-9


def _article_name(url):
    parsed = urlsplit(url)
    slug = parsed.path.rstrip("/").split("/")[-1] or "article"
    return f"{parsed.hostname or 'fixture'}/{slug[:42]}"


def _pipeline_input(article_text):
    """Mirror the unchanged pipeline preprocessing before its HTTPS call."""
    return str(article_text).strip()[:4000]


def run_and_verify(fixture_rows):
    import lambda_function as lf
    from dashboard.utils import map_to_canonical_intent

    connection = lf.get_db_connection()
    try:
        pending = lf.fetch_pending(connection, EXPECTED_ARTICLES + 1)
        fixture_ids = [row["id"] for row in fixture_rows]
        pending_ids = [row[0] for row in pending]
        if pending_ids != fixture_ids:
            raise RuntimeError(
                "pending article sequence did not match the fixture: "
                f"expected {fixture_ids}, found {pending_ids}"
            )

        real_client = lf._build_client()
        if real_client is None:
            raise RuntimeError("Lambda inference API URL/key were not configured")
        client = RecordingInferenceClient(real_client)
        final_predictions = {}
        original_save = lf.save_classification

        def recording_save(conn, article_id, result):
            final_predictions[article_id] = dict(result)
            return original_save(conn, article_id, result)

        progress(
            4,
            "running the real Lambda classification loop in fixture order "
            "(fetch pending -> HTTPS model request -> intent/tone pipeline -> save)",
        )
        lf.save_classification = recording_save
        try:
            counts = lf.classify_pending(
                connection, client=client, deadline=time.time() + 900
            )
        finally:
            lf.save_classification = original_save

        expected_counts = {
            "found_pending": EXPECTED_ARTICLES,
            "classified": EXPECTED_ARTICLES,
            "left_pending": 0,
            "failed": 0,
        }
        if counts != expected_counts:
            raise RuntimeError(f"unexpected classification counts: {counts}")
        if len(client.responses) != EXPECTED_ARTICLES:
            raise RuntimeError(
                f"expected {EXPECTED_ARTICLES} HTTPS results, found {len(client.responses)}"
            )
        if list(final_predictions) != fixture_ids:
            raise RuntimeError(
                "Lambda saved predictions out of sequence: "
                f"expected {fixture_ids}, found {list(final_predictions)}"
            )
        print(
            "[test-split-e2e] PASS: Lambda processed all 10 articles in order; "
            "10 HTTPS responses succeeded; no article was skipped",
            flush=True,
        )

        progress(5, "checking every API result, Lambda result, and persisted database row")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, strategic_intent, confidence, tone,
                       ml_processed_at IS NOT NULL, ml_processed_at
                FROM dashboard_medianarrative
                WHERE id = ANY(%s)
                ORDER BY id
                """,
                (fixture_ids,),
            )
            saved_rows = {row[0]: row for row in cursor.fetchall()}

        if len(saved_rows) != EXPECTED_ARTICLES:
            raise RuntimeError(
                f"expected {EXPECTED_ARTICLES} saved rows, found {len(saved_rows)}"
            )

        verified = []
        for index, (fixture, api_call) in enumerate(
            zip(fixture_rows, client.responses), start=1
        ):
            article_id = fixture["id"]
            if api_call["article_text"] != _pipeline_input(fixture["article_text"]):
                raise RuntimeError(
                    f"article {article_id} was sent to the API out of sequence"
                )
            api_result = api_call["result"]
            final_result = final_predictions[article_id]
            saved = saved_rows[article_id]

            # With no Groq key in this local stack, the caller must preserve the
            # dedicated server's local intent/tone/confidence exactly before the
            # normal canonical database mapping. This catches a skipped or
            # reordered stage instead of merely checking for non-empty columns.
            if final_result.get("strategic_intent") != api_result.get("strategic_intent"):
                raise RuntimeError(f"article {article_id}: API/Lambda intent mismatch")
            if final_result.get("tone") != api_result.get("tone"):
                raise RuntimeError(f"article {article_id}: API/Lambda tone mismatch")
            if not _close_enough(final_result.get("confidence"), api_result.get("confidence")):
                raise RuntimeError(f"article {article_id}: API/Lambda confidence mismatch")

            expected_saved_intent = map_to_canonical_intent(
                final_result.get("strategic_intent")
            )
            if saved[1] != expected_saved_intent:
                raise RuntimeError(f"article {article_id}: Lambda/database intent mismatch")
            if not _close_enough(saved[2], final_result.get("confidence")):
                raise RuntimeError(f"article {article_id}: Lambda/database confidence mismatch")
            if saved[3] != final_result.get("tone"):
                raise RuntimeError(f"article {article_id}: Lambda/database tone mismatch")
            if not saved[4]:
                raise RuntimeError(f"article {article_id}: processed timestamp was not saved")

            verified.append(
                {
                    "id": article_id,
                    # Retain the original result fields for existing readers.
                    "strategic_intent": saved[1],
                    "confidence": float(saved[2]),
                    "tone": saved[3],
                    "processed": saved[4],
                    # Additional fields make the split and prediction trace clear.
                    "name": _article_name(fixture["url"]),
                    "raw_intent": api_result["strategic_intent"],
                    "saved_intent": saved[1] or "(none)",
                    "request_id": api_call["request_id"],
                }
            )
            print(
                f"[test-split-e2e] ARTICLE {index:02d}/{EXPECTED_ARTICLES} PASS "
                f"id={article_id} source={verified[-1]['name']} "
                f"intent={verified[-1]['saved_intent']} "
                f"tone={saved[3]} confidence={float(saved[2]):.4f} "
                "sequence=fixture->pending->HTTPS->Lambda->Postgres",
                flush=True,
            )

        # Retain the human-readable result table from the original local
        # classifier test, in addition to the new per-stage verification.
        print("", flush=True)
        print(
            f"{'id':>4} | {'strategic_intent':<20} | {'conf':>5} | "
            f"{'tone':<14} | processed_at",
            flush=True,
        )
        print("-" * 78, flush=True)
        for article_id in fixture_ids:
            saved = saved_rows[article_id]
            intent = saved[1] or "(none = Neutral)"
            confidence = f"{float(saved[2]):.2f}"
            tone = saved[3] or "-"
            processed_at = saved[5].isoformat(timespec="seconds")
            print(
                f"{article_id:>4} | {intent:<20} | {confidence:>5} | "
                f"{tone:<14} | {processed_at}",
                flush=True,
            )
        print("-" * 78, flush=True)
        print(f"{len(saved_rows)} rows total.", flush=True)

        progress(6, "confirming the queue is drained and printing the final result")
        remaining = lf.fetch_pending(connection, 1)
        if remaining:
            raise RuntimeError(f"article queue was not drained; next pending id={remaining[0][0]}")
        print(
            "[test-split-e2e] PASS: 10/10 articles completed every stage in order; "
            "API predictions match Lambda results and persisted intent/tone/confidence",
            flush=True,
        )
        print(
            json.dumps(
                {
                    "event": "split_e2e_passed",
                    "transport": "https",
                    "articles": EXPECTED_ARTICLES,
                    "counts": counts,
                    "results": verified,
                }
            ),
            flush=True,
        )
    finally:
        connection.close()


def main():
    fixture_rows = load_fixture_metadata()
    wait_for_tls_api()
    prepare_database(fixture_rows)
    run_and_verify(fixture_rows)


if __name__ == "__main__":
    main()

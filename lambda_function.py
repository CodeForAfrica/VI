import json
import os
import sys
import time
import uuid

import psycopg2

# Add the dashboard directory to Python path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)

# Import your existing services.
# ML classification is intentionally NOT loaded in this process: the ~13GB
# ensemble exceeds Lambda's 10GB limits and moved to the vi-model-inference
# HTTP API. This Lambda ingests, then calls that API per pending article and
# writes back the prediction (solution-spec).
from dashboard.services.mediacloud_ingestion_service import main as run_mediacloud_ingestion
from inference_client import (
    InferenceClient,
    PermanentInferenceError,
    RetryableInferenceError,
)

TABLE_NAME = "dashboard_medianarrative"

# Bound how many pending articles one invocation will classify, so a large
# backlog (or an inference outage) cannot make a run unbounded (spec 633-635).
MAX_INFERENCE_PER_RUN = int(os.environ.get("VI_INFERENCE_MAX_PER_RUN", "200"))


def _log(level, event, **fields):
    """One JSON line per event on stdout/stderr for CloudWatch (spec 164-192).
    Never pass the API key or full article text here (spec 238-247)."""
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level,
        "service": "vi-ingestion-lambda",
        "event": event,
    }
    entry.update(fields)
    stream = sys.stderr if level in ("WARNING", "ERROR") else sys.stdout
    stream.write(json.dumps(entry) + "\n")
    stream.flush()


def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get('DB_HOST'),
        database=os.environ.get('DB_NAME'),
        user=os.environ.get('DB_USER'),
        password=os.environ.get('DB_PASSWORD'),
        port=os.environ.get('DB_PORT', '5432')
    )


def lambda_handler(event, context):
    conn = None
    started = time.time()
    _log("INFO", "ingestion_started")
    try:
        # Map Environment Variables (Ensures consistency)
        os.environ['API_KEY'] = os.environ.get('MEDIACLOUD_API_KEY', '')

        conn = get_db_connection()

        initial_count = get_count(conn)

        # 1. Ingest: query MediaCloud, scrape, insert pending rows (null intent).
        run_mediacloud_ingestion()

        # 2. Quality validation (cheap SQL).
        run_quality_validation(conn)
        after_ingest = get_count(conn)

        # 3. Infer: drain pending rows through the inference API. Runs AFTER
        # ingestion so an inference outage never blocks ingestion (spec 158-162).
        inference = classify_pending(conn)

        final_count = get_count(conn)
        summary = {
            "initial_count": initial_count,
            "ingested": after_ingest - initial_count,
            "final_count": final_count,
            "duration_ms": int((time.time() - started) * 1000),
            **inference,
        }
        _log("INFO", "ingestion_completed", **summary)
        return {"statusCode": 200, "body": json.dumps({"message": "Success", **summary})}

    except Exception as e:
        _log("ERROR", "ingestion_failed", error_type=type(e).__name__, error=str(e))
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
    finally:
        if conn:
            conn.close()


def get_count(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
        return cur.fetchone()[0]


def run_quality_validation(conn):
    with conn.cursor() as cursor:
        cursor.execute(f"""
            UPDATE {TABLE_NAME} SET pseudo_kept = TRUE, pseudo_weight = 1.0
            WHERE pseudo_kept IS NULL AND article_text IS NOT NULL AND LENGTH(article_text) > 100
        """)
        conn.commit()


def _build_client():
    """Client from env, or None if the API isn't configured. The base URL must
    come from the environment, never hardcoded (spec 566-572)."""
    base_url = os.environ.get("VI_INFERENCE_API_URL")
    api_key = os.environ.get("VI_INFERENCE_API_KEY")
    if not base_url or not api_key:
        return None
    return InferenceClient(
        base_url=base_url,
        api_key=api_key,
        timeout=int(os.environ.get("VI_INFERENCE_TIMEOUT", "180")),
        max_attempts=int(os.environ.get("VI_INFERENCE_MAX_ATTEMPTS", "3")),
    )


def fetch_pending(conn, limit):
    """Rows never successfully classified. ml_processed_at IS NULL is the pending
    marker (spec 609); it covers both newly ingested rows and ones left pending
    by an earlier failed attempt, so this doubles as the bounded pending-retry."""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT id, article_text, target_country, inferred_actor
            FROM {TABLE_NAME}
            WHERE ml_processed_at IS NULL
              AND article_text IS NOT NULL AND article_text <> ''
              AND lower(article_text) <> 'no content available'
            ORDER BY id
            LIMIT %s
        """, (limit,))
        return cur.fetchall()


def save_classification(conn, article_id, result):
    """Persist a successful prediction. Neutral is stored as-is (an explicit,
    processed result), never converted back to NULL (spec 358-360)."""
    with conn.cursor() as cur:
        cur.execute(f"""
            UPDATE {TABLE_NAME}
            SET strategic_intent = %s, tone = %s, confidence = %s, ml_processed_at = NOW()
            WHERE id = %s
        """, (
            result.get("strategic_intent"),
            result.get("tone"),
            result.get("confidence"),
            article_id,
        ))
        conn.commit()


def classify_pending(conn):
    client = _build_client()
    if client is None:
        _log("WARNING", "inference_skipped",
             reason="VI_INFERENCE_API_URL / VI_INFERENCE_API_KEY not set")
        return {"inference": "skipped", "classified": 0, "left_pending": 0, "failed": 0}

    rows = fetch_pending(conn, MAX_INFERENCE_PER_RUN)
    counts = {"found_pending": len(rows), "classified": 0, "left_pending": 0, "failed": 0}
    _log("INFO", "pending_retry_started", pending=len(rows), limit=MAX_INFERENCE_PER_RUN)

    for article_id, article_text, target_country, inferred_actor in rows:
        request_id = str(uuid.uuid4())
        _log("INFO", "inference_request_started",
             request_id=request_id, article_id=article_id)
        try:
            result = client.infer(
                request_id=request_id,
                article_text=article_text,
                article_id=article_id,
                target_country=target_country,
                inferred_actor=inferred_actor,
                on_retry=lambda attempt, code, rid=request_id, aid=article_id: _log(
                    "WARNING", "inference_request_retrying",
                    request_id=rid, article_id=aid, attempt=attempt, status=code),
            )
        except PermanentInferenceError as e:
            # Permanent: won't succeed on retry. Left pending (no status column
            # yet, spec 600-610); the per-run bound caps repeated reattempts.
            counts["failed"] += 1
            _log("ERROR", "inference_request_failed",
                 request_id=request_id, article_id=article_id, error_code=e.code)
            continue
        except RetryableInferenceError as e:
            # Transient after all attempts: stays pending for a later invocation.
            counts["left_pending"] += 1
            _log("WARNING", "inference_request_failed",
                 request_id=request_id, article_id=article_id,
                 error_code=e.code, retryable=True)
            continue

        save_classification(conn, article_id, result)
        counts["classified"] += 1
        _log("INFO", "article_classification_saved",
             request_id=request_id, article_id=article_id,
             strategic_intent=result.get("strategic_intent"),
             duration_ms=result.get("processing_time_ms"))

    _log("INFO", "pending_retry_completed", **counts)
    return counts

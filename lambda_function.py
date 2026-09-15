import json
import os
import re
import sys
import time
import traceback
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from urllib.parse import urlsplit

# Add the dashboard directory to Python path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)

# ML classification is intentionally NOT loaded in this process: the ~13GB
# ensemble exceeds Lambda's 10GB limits and moved to the vi-model-inference
# HTTP API. This Lambda ingests, then calls that API per pending article and
# writes back the prediction (solution-spec).
#
# psycopg2 and the MediaCloud ingestion service are imported lazily inside the
# functions that use them so this module can be imported (and unit-tested)
# without a database driver or the ingestion stack present.
from inference_client import (  # noqa: E402
    InferenceClient,
    PermanentInferenceError,
    RetryableInferenceError,
)

TABLE_NAME = "dashboard_medianarrative"

# Bound how many pending articles one invocation will classify, so a large
# backlog (or an inference outage) cannot make a run unbounded (spec 633-635).
MAX_INFERENCE_PER_RUN = int(os.environ.get("VI_INFERENCE_MAX_PER_RUN", "200"))
LAMBDA_SAFETY_SECONDS = int(os.environ.get("VI_LAMBDA_SAFETY_SECONDS", "30"))
INFERENCE_RESERVE_SECONDS = int(os.environ.get("VI_INFERENCE_RESERVE_SECONDS", "180"))
_LOG_CONTEXT = ContextVar("vi_lambda_log_context", default={})
_SENSITIVE_FIELD_PARTS = (
    "api_key", "accepted_key", "authorization", "password", "secret", "access_key",
    "session_token", "article_text", "request_body", "response_body",
)


def _redact_text(value):
    text = str(value)
    for name, secret in os.environ.items():
        normalized = name.lower().replace("-", "_")
        if secret and any(part in normalized for part in _SENSITIVE_FIELD_PARTS):
            text = text.replace(secret, "[REDACTED]")
    return re.sub(r"(://[^:/\s]+:)[^@/\s]+@", r"\1[REDACTED]@", text)


def _safe_error(exc):
    return _redact_text(str(exc))[:500]


def _safe_traceback():
    return _redact_text(traceback.format_exc(limit=20))[-8000:]


def _sanitize(fields):
    clean = {}
    for key, value in fields.items():
        normalized = str(key).lower().replace("-", "_")
        if normalized == "key" or any(part in normalized for part in _SENSITIVE_FIELD_PARTS):
            clean[key] = "[REDACTED]"
        elif isinstance(value, str):
            clean[key] = _redact_text(value)
        else:
            clean[key] = value
    return clean


def _log(level, event, **fields):
    """One JSON line per event on stdout/stderr for CloudWatch (spec 164-192).
    Never pass the API key or full article text here (spec 238-247)."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "level": level,
        "service": "vi-ingestion-lambda",
        "event": event,
    }
    entry.update(_sanitize(_LOG_CONTEXT.get()))
    entry.update(_sanitize(fields))
    stream = sys.stderr if level in ("WARNING", "ERROR") else sys.stdout
    stream.write(json.dumps(entry) + "\n")
    stream.flush()


def get_db_connection():
    import psycopg2
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
    phase = "startup"
    invocation_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    context_token = _LOG_CONTEXT.set({"invocation_id": invocation_id})
    _log("INFO", "ingestion_started",
         remaining_time_ms=(context.get_remaining_time_in_millis()
                            if context and hasattr(context, "get_remaining_time_in_millis")
                            else None))
    try:
        # Map Environment Variables (Ensures consistency)
        os.environ['API_KEY'] = os.environ.get('MEDIACLOUD_API_KEY', '')

        from dashboard.services.mediacloud_ingestion_service import (
            main as run_mediacloud_ingestion,
        )

        phase = "database_connection"
        _log("INFO", "database_connection_started")
        conn = get_db_connection()
        _log("INFO", "database_connection_completed")

        phase = "initial_count"
        initial_count = get_count(conn)

        deadline = _lambda_deadline(context)

        # 1. Ingest: query MediaCloud, scrape, insert pending rows (null intent).
        ingestion_deadline = None
        if deadline is not None:
            ingestion_deadline = max(time.time(), deadline - INFERENCE_RESERVE_SECONDS)
        phase = "mediacloud_ingestion"
        ingestion = run_mediacloud_ingestion(
            deadline=ingestion_deadline, event_logger=_log) or {}

        # 2. Quality validation (cheap SQL).
        phase = "quality_validation"
        validation_started = time.time()
        _log("INFO", "quality_validation_started")
        run_quality_validation(conn)
        _log("INFO", "quality_validation_completed",
             duration_ms=int((time.time() - validation_started) * 1000))
        after_ingest = get_count(conn)

        # 3. Infer: drain pending rows through the inference API. Runs AFTER
        # ingestion so an inference outage never blocks ingestion (spec 158-162).
        phase = "pending_inference"
        inference = classify_pending(conn, deadline=deadline)

        phase = "final_count"
        final_count = get_count(conn)
        summary = {
            "initial_count": initial_count,
            "ingested": after_ingest - initial_count,
            "final_count": final_count,
            "duration_ms": int((time.time() - started) * 1000),
            **ingestion,
            **inference,
        }
        _log("INFO", "ingestion_completed", **summary)
        return {"statusCode": 200, "body": json.dumps({"message": "Success", **summary})}

    except Exception as e:
        _log("ERROR", "ingestion_failed", error_type=type(e).__name__,
             error_code=f"{phase}_failed", failure_phase=phase,
             error_detail=_safe_error(e), stack_trace=_safe_traceback(),
             duration_ms=int((time.time() - started) * 1000))
        return {'statusCode': 500,
                'body': json.dumps({'error': {'code': 'ingestion_failed',
                                              'message': 'Ingestion could not be completed'}})}
    finally:
        if conn:
            try:
                conn.close()
                _log("INFO", "database_connection_closed")
            except Exception as exc:
                _log("ERROR", "database_connection_close_failed",
                     error_type=type(exc).__name__,
                     error_code="database_connection_close_failed",
                     error_detail=_safe_error(exc), stack_trace=_safe_traceback())
        _LOG_CONTEXT.reset(context_token)


def get_count(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
        return cur.fetchone()[0]


def _lambda_deadline(context):
    """Epoch deadline that leaves time for logs, commits and Lambda shutdown."""
    if context is None or not hasattr(context, "get_remaining_time_in_millis"):
        return None
    remaining = max(0, context.get_remaining_time_in_millis() / 1000)
    return time.time() + max(0, remaining - LAMBDA_SAFETY_SECONDS)


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
              AND (inference_status IS NULL OR inference_status = 'pending')
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
            SET strategic_intent = %s, tone = %s, confidence = %s,
                prediction_source = %s, lang_detect = %s,
                inference_status = 'completed', inference_error_code = NULL,
                inference_attempts = inference_attempts + 1,
                ml_processed_at = NOW()
            WHERE id = %s
        """, (
            result.get("strategic_intent"),
            result.get("tone"),
            result.get("confidence"),
            result.get("prediction_source"),
            result.get("lang_detect"),
            article_id,
        ))
        conn.commit()


def mark_classification_failure(conn, article_id, status, error_code):
    with conn.cursor() as cur:
        cur.execute(f"""
            UPDATE {TABLE_NAME}
            SET inference_status = %s, inference_error_code = %s,
                inference_attempts = inference_attempts + 1
            WHERE id = %s
        """, (status, str(error_code or "unknown")[:64], article_id))
        conn.commit()


def _error_fields(error):
    return {
        "error_type": type(error).__name__,
        "error_code": error.code or "inference_unknown_error",
        "error_detail": _safe_error(error),
        "http_status": getattr(error, "status_code", None),
        "attempts": getattr(error, "attempts", None),
    }


def _record_failure_state(conn, article_id, status, error_code, request_id):
    try:
        mark_classification_failure(conn, article_id, status, error_code)
        _log("INFO", "article_classification_state_saved",
             request_id=request_id, article_id=article_id,
             inference_status=status, error_code=error_code)
        return True
    except Exception as exc:  # keep one bad status write from aborting the batch
        conn.rollback()
        _log("ERROR", "article_classification_save_failed",
             request_id=request_id, article_id=article_id,
             database_operation="update_failure_status", target_status=status,
             error_type=type(exc).__name__, error_code="database_update_failed",
             error_detail=_safe_error(exc), stack_trace=_safe_traceback())
        _log("ERROR", "pending_retry_failed", request_id=request_id,
             article_id=article_id, error_code="database_update_failed")
        return False


def classify_pending(conn, client=None, deadline=None):
    rows = fetch_pending(conn, MAX_INFERENCE_PER_RUN)
    if client is None:
        client = _build_client()
    if client is None:
        _log("WARNING", "inference_skipped",
             reason="VI_INFERENCE_API_URL / VI_INFERENCE_API_KEY not set")
        return {"inference": "skipped", "found_pending": len(rows),
                "classified": 0, "left_pending": len(rows), "failed": 0}

    counts = {"found_pending": len(rows), "classified": 0, "left_pending": 0, "failed": 0}
    _log("INFO", "pending_retry_started", pending=len(rows), limit=MAX_INFERENCE_PER_RUN)

    for article_id, article_text, target_country, inferred_actor in rows:
        if deadline is not None and time.time() >= deadline - 1:
            remaining = len(rows) - counts["classified"] - counts["failed"] - counts["left_pending"]
            counts["left_pending"] += remaining
            _log("WARNING", "pending_retry_stopped", reason="time_budget_exhausted",
                 remaining=remaining)
            break
        request_id = str(uuid.uuid4())
        request_started = time.time()
        _log("INFO", "inference_request_started",
             request_id=request_id, article_id=article_id,
             inference_host=(urlsplit(client.base_url).hostname
                             if getattr(client, "base_url", None) else None),
             timeout_seconds=getattr(client, "timeout", None),
             max_attempts=getattr(client, "max_attempts", None))
        try:
            result = client.infer(
                request_id=request_id,
                article_text=article_text,
                article_id=article_id,
                target_country=target_country,
                inferred_actor=inferred_actor,
                on_retry=lambda attempt, max_attempts, error,
                rid=request_id, aid=article_id: _log(
                    "WARNING", "inference_request_retrying",
                    request_id=rid, article_id=aid, attempt=attempt,
                    max_attempts=max_attempts, will_retry=True,
                    **_error_fields(error)),
                deadline=deadline,
            )
            _log("INFO", "inference_response_validated", request_id=request_id,
                 article_id=article_id, http_status=200,
                 attempt=result.get("_client_attempts"),
                 attempt_duration_ms=result.get("_client_attempt_duration_ms"))
            _log("INFO", "inference_request_completed", request_id=request_id,
                 article_id=article_id, status="success",
                 attempt=result.get("_client_attempts"),
                 max_attempts=getattr(client, "max_attempts", None),
                 duration_ms=int((time.time() - request_started) * 1000),
                 api_processing_time_ms=result.get("processing_time_ms"),
                 model_version=result.get("model_version"))
        except PermanentInferenceError as e:
            state_saved = _record_failure_state(
                conn, article_id, "failed", e.code, request_id
            )
            if state_saved:
                counts["failed"] += 1
            else:
                counts["left_pending"] += 1
            _log("ERROR", "inference_response_invalid",
                 request_id=request_id, article_id=article_id,
                 **_error_fields(e))
            _log("ERROR", "inference_request_failed",
                 request_id=request_id, article_id=article_id, retryable=False,
                 will_retry=False,
                 duration_ms=int((time.time() - request_started) * 1000),
                 **_error_fields(e))
            continue
        except RetryableInferenceError as e:
            # Transient after all attempts: stays pending for a later invocation.
            counts["left_pending"] += 1
            _record_failure_state(conn, article_id, "pending", e.code, request_id)
            _log("WARNING", "inference_request_failed",
                 request_id=request_id, article_id=article_id,
                 retryable=True, will_retry=False,
                 retry_scope="later_invocation",
                 duration_ms=int((time.time() - request_started) * 1000),
                 **_error_fields(e))
            continue

        try:
            save_classification(conn, article_id, result)
        except Exception as e:
            conn.rollback()
            # No completed state reached the database, so the row remains
            # pending and will be eligible for a later invocation.
            counts["left_pending"] += 1
            _log("ERROR", "article_classification_save_failed",
                 request_id=request_id, article_id=article_id,
                 database_operation="save_classification",
                 error_type=type(e).__name__, error_code="database_update_failed",
                 error_detail=_safe_error(e), stack_trace=_safe_traceback())
            continue
        counts["classified"] += 1
        _log("INFO", "article_classification_saved",
             request_id=request_id, article_id=article_id,
             strategic_intent=result.get("strategic_intent"),
             tone=result.get("tone"), confidence=result.get("confidence"),
             prediction_source=result.get("prediction_source"),
             model_version=result.get("model_version"),
             duration_ms=result.get("processing_time_ms"))

    _log("INFO", "pending_retry_completed", **counts)
    return counts

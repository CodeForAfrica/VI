import json
import os
import re
import sys
import time
import traceback
import uuid
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timezone

# Add the dashboard directory to Python path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)

# Only the heavy classifiers are remote. Lambda retains the existing Groq,
# arbitration, entity extraction, scoring, canonicalization and database logic.
#
# psycopg2 and the MediaCloud ingestion service are imported lazily inside the
# functions that use them so this module can be imported (and unit-tested)
# without a database driver or the ingestion stack present.
from inference_client import (  # noqa: E402
    InferenceClient,
)

TABLE_NAME = "dashboard_medianarrative"

# Bound how many pending articles one invocation will classify, so a large
# backlog (or an inference outage) cannot make a run unbounded (spec 633-635).
MAX_INFERENCE_PER_RUN = int(os.environ.get("VI_INFERENCE_MAX_PER_RUN", "200"))
LAMBDA_SAFETY_SECONDS = int(os.environ.get("VI_LAMBDA_SAFETY_SECONDS", "30"))
# Session lock survives per-article commits/rollbacks. All classification modes
# share it; it is released explicitly or when PostgreSQL closes the session.
CLASSIFICATION_LOCK_ID = 0x56495F434C415353
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
    operation = event.get("operation") if isinstance(event, dict) else None
    context_token = _LOG_CONTEXT.set({"invocation_id": invocation_id,
                                      "operation": operation or "ingestion-and-processing"})
    pending_only = operation == "processing-only"
    _log("INFO", "pending_processing_started" if pending_only else "ingestion_started",
         remaining_time_ms=(context.get_remaining_time_in_millis()
                            if context and hasattr(context, "get_remaining_time_in_millis")
                            else None))
    try:
        phase = "event_validation"
        if operation not in (None, "processing-only", "ingestion-only", "verify_inference"):
            raise ValueError("Unknown operation; use processing-only, ingestion-only, verify_inference, or omit operation for ingestion and processing")
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        import django
        django.setup()

        phase = "database_connection"
        _log("INFO", "database_connection_started")
        conn = get_db_connection()
        _log("INFO", "database_connection_completed")

        deadline = _lambda_deadline(context)

        if pending_only:
            phase = "pending_inference"
            _log("INFO", "ingestion_skipped", reason="processing_only")
            inference = classify_pending(conn, deadline=deadline)
            summary = {
                "operation": "processing-only",
                "ingested": 0,
                "duration_ms": int((time.time() - started) * 1000),
                **inference,
            }
            _log("INFO", "pending_processing_completed", **summary)
            return {"statusCode": 200, "body": json.dumps(summary)}

        # Operator-only bounded production verification. Scheduled events retain
        # the normal ingestion path; this event reprocesses only recorded IDs.
        if isinstance(event, dict) and event.get("operation") == "verify_inference":
            article_ids = event.get("article_ids")
            if (not isinstance(article_ids, list) or not 1 <= len(article_ids) <= 3
                    or any(type(value) is not int or value <= 0 for value in article_ids)
                    or len(set(article_ids)) != len(article_ids)):
                raise ValueError("verify_inference requires 1-3 distinct positive integer article_ids")
            phase = "production_verification"
            summary = classify_pending(conn, deadline=deadline, article_ids=article_ids)
            passed = (summary["classified"] == len(article_ids)
                      and summary["failed"] == 0 and summary["left_pending"] == 0)
            _log("INFO" if passed else "ERROR", "production_verification_completed", **summary)
            return {"statusCode": 200 if passed else 500, "body": json.dumps(summary)}

        phase = "initial_count"
        initial_count = get_count(conn)

        # Preserve the existing MediaCloud ingestion behavior.
        phase = "mediacloud_ingestion"
        os.environ['API_KEY'] = os.environ.get('MEDIACLOUD_API_KEY', '')
        from dashboard.services.mediacloud_ingestion_service import (
            main as run_mediacloud_ingestion,
        )
        run_mediacloud_ingestion()
        ingestion = {}

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
        if operation == "ingestion-only":
            _log("INFO", "pending_processing_skipped", reason="ingestion_only")
            inference = {"found_pending": None, "classified": 0, "left_pending": None,
                         "failed": 0, "inference": "skipped", "reason": "ingestion_only"}
        else:
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
        if operation == "ingestion-only":
            summary["operation"] = operation
        _log("INFO", "ingestion_completed", **summary)
        return {"statusCode": 200, "body": json.dumps({"message": "Success", **summary})}

    except Exception as e:
        _log("ERROR", "pending_processing_failed" if pending_only else "ingestion_failed", error_type=type(e).__name__,
             error_code=f"{phase}_failed", failure_phase=phase,
             error_detail=_safe_error(e), stack_trace=_safe_traceback(),
             duration_ms=int((time.time() - started) * 1000))
        return {'statusCode': 500,
                'body': json.dumps({'error': {'code': 'pending_processing_failed' if pending_only else 'ingestion_failed',
                                              'message': 'Pending processing could not be completed' if pending_only else 'Ingestion could not be completed'}})}
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


def fetch_pending(conn, limit, article_ids=None):
    """Rows never successfully classified. ml_processed_at IS NULL is the pending
    marker (spec 609); it covers both newly ingested rows and ones left pending
    by an earlier failed attempt, so this doubles as the bounded pending-retry."""
    with conn.cursor() as cur:
        if article_ids is not None:
            cur.execute(f"""
                SELECT id, article_text, target_country, inferred_actor
                FROM {TABLE_NAME}
                WHERE id = ANY(%s) AND article_text IS NOT NULL
                  AND length(article_text) > 100
                  AND lower(article_text) <> 'no content available'
                ORDER BY id
            """, (article_ids,))
            return cur.fetchall()
        cur.execute(f"""
            SELECT id, article_text, target_country, inferred_actor
            FROM {TABLE_NAME}
            WHERE ml_processed_at IS NULL
              AND (strategic_intent IS NULL OR strategic_intent = '')
              AND article_text IS NOT NULL AND article_text <> ''
              AND lower(article_text) <> 'no content available'
            ORDER BY id
            LIMIT %s
        """, (limit,))
        return cur.fetchall()


def save_classification(conn, article_id, result):
    """Match fill_missing_intents: canonical intent, confidence, tone, processed time."""
    from dashboard.utils import map_to_canonical_intent
    with conn.cursor() as cur:
        cur.execute(f"""
            UPDATE {TABLE_NAME}
            SET strategic_intent = %s, confidence = %s, tone = %s,
                ml_processed_at = NOW()
            WHERE id = %s
        """, (map_to_canonical_intent(result.get("strategic_intent")),
              result.get("confidence", 0.0), result.get("tone", "Factual"), article_id))
    conn.commit()


@contextmanager
def classification_lock(conn):
    _log("INFO", "classification_lock_requested")
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (CLASSIFICATION_LOCK_ID,))
        acquired = cur.fetchone()[0]
    if type(acquired) is not bool:
        raise RuntimeError("PostgreSQL did not return a classification lock status")
    if not acquired:
        conn.rollback()
        _log("INFO", "classification_lock_busy", reason="another_worker_is_classifying")
        yield False
        return
    try:
        conn.commit()
        _log("INFO", "classification_lock_acquired")
        yield True
    finally:
        try:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (CLASSIFICATION_LOCK_ID,))
                if cur.fetchone()[0] is not True:
                    raise RuntimeError("Classification lock was not held when releasing it")
            conn.commit()
            _log("INFO", "classification_lock_released")
        except Exception as exc:
            _log("ERROR", "classification_lock_release_failed",
                 error_type=type(exc).__name__, error_detail=_safe_error(exc))
            raise


def classify_pending(conn, client=None, deadline=None, pipeline=None, article_ids=None):
    with classification_lock(conn) as acquired:
        if not acquired:
            return {"found_pending": None, "classified": 0, "left_pending": None,
                    "failed": 0, "inference": "skipped", "reason": "classification_lock_held"}
        return _classify_pending_locked(conn, client, deadline, pipeline, article_ids)


def _classify_pending_locked(conn, client=None, deadline=None, pipeline=None, article_ids=None):
    rows = (fetch_pending(conn, MAX_INFERENCE_PER_RUN) if article_ids is None
            else fetch_pending(conn, MAX_INFERENCE_PER_RUN, article_ids=article_ids))
    counts = {"found_pending": len(rows), "classified": 0, "left_pending": 0, "failed": 0}
    if article_ids is not None and len(rows) != len(article_ids):
        raise ValueError("Some selected verification articles are missing or have insufficient text")
    verification_results = []
    if not rows:
        _log("INFO", "pending_retry_completed", reason="no_pending_articles", **counts)
        return counts
    client = client or _build_client()
    if client is None and pipeline is None:
        _log("WARNING", "inference_skipped", reason="inference API is not configured")
        return {**counts, "inference": "skipped", "left_pending": len(rows)}
    if pipeline is None:
        from dashboard.services.remote_inference_service import RemoteInferenceService
        pipeline = RemoteInferenceService(client, deadline=deadline, event_logger=_log)

    _log("INFO", "pending_retry_started", pending=len(rows))
    for index, (article_id, article_text, target_country, inferred_actor) in enumerate(rows):
        if deadline is not None and time.time() >= deadline - 1:
            counts["left_pending"] += len(rows) - index
            _log("WARNING", "pending_retry_stopped", reason="time_budget_exhausted")
            break
        token = _LOG_CONTEXT.set({**_LOG_CONTEXT.get(), "article_id": article_id})
        try:
            # Same orchestration as the existing classifier command. Only the
            # strategic/tone model implementations are backed by HTTP.
            result = pipeline.perform_inference(article_text)
            if article_ids is not None:
                if getattr(pipeline, "_error", None) is not None or not getattr(pipeline, "_result", None):
                    raise RuntimeError("Production verification requires a successful HTTPS model response")
            save_classification(conn, article_id, result)
            if article_ids is not None:
                from dashboard.utils import map_to_canonical_intent
                api_result = pipeline._result
                verification_results.append({
                    "article_id": article_id,
                    "request_id": pipeline._request_id,
                    "model_version": api_result.get("model_version"),
                    "api_intent": api_result.get("strategic_intent"),
                    "api_tone": api_result.get("tone"),
                    "api_confidence": api_result.get("confidence"),
                    "saved_intent": map_to_canonical_intent(result.get("strategic_intent")),
                    "saved_tone": result.get("tone"),
                    "saved_confidence": result.get("confidence"),
                })
            counts["classified"] += 1
            _log("INFO", "article_classification_saved",
                 strategic_intent=result.get("strategic_intent"),
                 tone=result.get("tone"), confidence=result.get("confidence"))
        except Exception as exc:
            conn.rollback()
            counts["failed"] += 1
            counts["left_pending"] += 1
            _log("ERROR", "article_classification_failed",
                 error_type=type(exc).__name__, error_detail=_safe_error(exc),
                 stack_trace=_safe_traceback())
        finally:
            _LOG_CONTEXT.reset(token)
    _log("INFO", "pending_retry_completed", **counts)
    if article_ids is not None:
        return {**counts, "results": verification_results}
    return counts

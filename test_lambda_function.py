"""Phase-4 tests for the Lambda drain loop + handler wiring
(solution-spec 790-802, Lambda side).

Plain unittest with a fake DB connection and fake/injected client, so no
Postgres, AWS, or MediaCloud is needed. Run:
  python -m unittest test_lambda_function
"""
import sys
import types
import io
import json
import unittest
from unittest import mock

import lambda_function as lf
from inference_client import (
    DeferredInferenceError, PermanentInferenceError, RetryableInferenceError,
)

GOOD = {"strategic_intent": "Economic", "tone": "Factual", "confidence": 0.9,
        "strategic_intent_confidence": 0.9, "tone_confidence": 0.8,
        "lang_detect": "en", "prediction_source": "model",
        "model_version": "test", "processing_time_ms": 12}


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((" ".join(sql.split()), params))
        self._sql = sql

    def fetchone(self):
        return (self.conn.count,)

    def fetchall(self):
        return list(self.conn.pending_rows)


class FakeConn:
    def __init__(self, pending_rows=(), count=0):
        self.pending_rows = pending_rows
        self.count = count
        self.executed = []
        self.commits = 0
        self.closed = False
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True

    def rollback(self):
        self.rollbacks += 1


class FakeClient:
    """Records infer() calls; returns a queued result or raises a queued error."""
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def infer(self, request_id, article_text, article_id=None,
              target_country=None, inferred_actor=None, on_retry=None,
              deadline=None):
        self.calls.append(article_id)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


class FetchPendingTests(unittest.TestCase):
    def test_query_uses_pending_marker_and_limit(self):
        conn = FakeConn(pending_rows=[(1, "text", "KE", "FR")])
        rows = lf.fetch_pending(conn, 50)
        sql, params = conn.executed[-1]
        self.assertIn("ml_processed_at IS NULL", sql)
        self.assertIn("LIMIT", sql)
        self.assertEqual(params, (50,))
        self.assertEqual(rows, [(1, "text", "KE", "FR")])


class SaveClassificationTests(unittest.TestCase):
    def test_writes_fields_and_commits(self):
        conn = FakeConn()
        lf.save_classification(conn, 7, GOOD)
        sql, params = conn.executed[-1]
        self.assertIn("UPDATE", sql)
        self.assertIn("ml_processed_at = NOW()", sql)
        self.assertEqual(params, ("Economic", "Factual", 0.9, "model", "en", 7))
        self.assertEqual(conn.commits, 1)


class ClassifyPendingTests(unittest.TestCase):
    def rows(self, n):
        return [(i, f"text {i}", None, None) for i in range(1, n + 1)]

    def test_success_saves_each_and_counts(self):
        conn = FakeConn(pending_rows=self.rows(2))
        client = FakeClient([GOOD, GOOD])
        counts = lf.classify_pending(conn, client=client)
        self.assertEqual(counts["classified"], 2)
        self.assertEqual(counts["found_pending"], 2)
        self.assertEqual(client.calls, [1, 2])
        updates = [e for e in conn.executed if e[0].startswith("UPDATE")]
        self.assertEqual(len(updates), 2)

    def test_permanent_failure_is_marked_failed(self):
        conn = FakeConn(pending_rows=self.rows(1))
        client = FakeClient([PermanentInferenceError("bad", code=400)])
        counts = lf.classify_pending(conn, client=client)
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(counts["classified"], 0)
        failure_updates = [e for e in conn.executed if "inference_status = %s" in e[0]]
        self.assertEqual(failure_updates[0][1], ("failed", "400", 1))

    def test_retryable_failure_left_pending(self):
        conn = FakeConn(pending_rows=self.rows(1))
        client = FakeClient([RetryableInferenceError("timeout")])
        counts = lf.classify_pending(conn, client=client)
        self.assertEqual(counts["left_pending"], 1)
        self.assertEqual(counts["classified"], 0)
        failure_updates = [e for e in conn.executed if "inference_status = %s" in e[0]]
        self.assertEqual(failure_updates[0][1][0], "pending")

    def test_auth_failure_is_left_pending_for_a_later_invocation(self):
        conn = FakeConn(pending_rows=self.rows(1))
        client = FakeClient([
            DeferredInferenceError("unauthorized", code="unauthorized", status_code=401)
        ])
        counts = lf.classify_pending(conn, client=client)
        self.assertEqual(counts["left_pending"], 1)
        self.assertEqual(counts["failed"], 0)
        failure_updates = [e for e in conn.executed if "inference_status = %s" in e[0]]
        self.assertEqual(failure_updates[0][1], ("pending", "unauthorized", 1))

    def test_failure_state_write_error_does_not_abort_remaining_articles(self):
        conn = FakeConn(pending_rows=self.rows(2))
        client = FakeClient([RetryableInferenceError(
            "temporary", code="inference_timeout", attempts=3), GOOD])
        with mock.patch.object(lf, "mark_classification_failure",
                               side_effect=RuntimeError("write failed")):
            counts = lf.classify_pending(conn, client=client)
        self.assertEqual(client.calls, [1, 2])
        self.assertEqual(counts["left_pending"], 1)
        self.assertEqual(counts["classified"], 1)
        self.assertEqual(conn.rollbacks, 1)

    def test_permanent_state_write_error_leaves_article_pending(self):
        conn = FakeConn(pending_rows=self.rows(1))
        client = FakeClient([PermanentInferenceError(
            "bad response", code="invalid_tone")])
        with mock.patch.object(lf, "mark_classification_failure",
                               side_effect=RuntimeError("write failed")):
            counts = lf.classify_pending(conn, client=client)
        self.assertEqual(counts["left_pending"], 1)
        self.assertEqual(counts["failed"], 0)
        self.assertEqual(conn.rollbacks, 1)

    def test_success_result_write_error_leaves_article_pending(self):
        conn = FakeConn(pending_rows=self.rows(1))
        with mock.patch.object(lf, "save_classification",
                               side_effect=RuntimeError("write failed")):
            counts = lf.classify_pending(conn, client=FakeClient([GOOD]))
        self.assertEqual(counts["left_pending"], 1)
        self.assertEqual(counts["failed"], 0)
        self.assertEqual(conn.rollbacks, 1)

    def test_neutral_stored_as_processed(self):
        conn = FakeConn(pending_rows=self.rows(1))
        neutral = {"strategic_intent": "Neutral", "tone": "Factual", "confidence": 0.2}
        lf.classify_pending(conn, client=FakeClient([neutral]))
        update = [e for e in conn.executed if e[0].startswith("UPDATE")][0]
        self.assertEqual(update[1][0], "Neutral")  # strategic_intent param

    def test_mixed_batch_counts(self):
        conn = FakeConn(pending_rows=self.rows(3))
        client = FakeClient([GOOD,
                             RetryableInferenceError("x"),
                             PermanentInferenceError("y", code=413)])
        counts = lf.classify_pending(conn, client=client)
        self.assertEqual((counts["classified"], counts["left_pending"], counts["failed"]),
                         (1, 1, 1))

    def test_exhausted_deadline_leaves_entire_batch_pending(self):
        import time
        conn = FakeConn(pending_rows=self.rows(3))
        client = FakeClient([GOOD, GOOD, GOOD])
        counts = lf.classify_pending(conn, client=client, deadline=time.time() - 1)
        self.assertEqual(counts["left_pending"], 3)
        self.assertEqual(client.calls, [])

    def test_skips_when_not_configured(self):
        conn = FakeConn(pending_rows=self.rows(2))
        with mock.patch.dict("os.environ", {}, clear=True):
            counts = lf.classify_pending(conn)  # no injected client, no env
        self.assertEqual(counts["inference"], "skipped")
        self.assertEqual(counts["classified"], 0)
        self.assertEqual([e for e in conn.executed if e[0].startswith("UPDATE")], [])


class BuildClientTests(unittest.TestCase):
    def test_none_when_env_missing(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(lf._build_client())

    def test_built_when_env_present(self):
        with mock.patch.dict("os.environ",
                             {"VI_INFERENCE_API_URL": "https://api.x",
                              "VI_INFERENCE_API_KEY": "k"}, clear=True):
            c = lf._build_client()
        self.assertIsNotNone(c)
        self.assertEqual(c.base_url, "https://api.x")


class HandlerTests(unittest.TestCase):
    def test_handler_success_summary(self):
        conn = FakeConn(pending_rows=[], count=5)
        fake_mod = types.ModuleType("dashboard.services.mediacloud_ingestion_service")
        fake_mod.main = lambda deadline=None, event_logger=None: None
        with mock.patch.object(lf, "get_db_connection", return_value=conn), \
             mock.patch.dict(sys.modules,
                             {"dashboard.services.mediacloud_ingestion_service": fake_mod}), \
             mock.patch.dict("os.environ", {}, clear=True):  # API unset -> inference skipped
            resp = lf.lambda_handler({}, None)
        self.assertEqual(resp["statusCode"], 200)
        body = __import__("json").loads(resp["body"])
        self.assertEqual(body["message"], "Success")
        self.assertEqual(body["inference"], "skipped")
        self.assertEqual(body["final_count"], 5)
        self.assertTrue(conn.closed)

    def test_handler_failure_returns_500(self):
        with mock.patch.object(lf, "get_db_connection", side_effect=RuntimeError("db down")):
            resp = lf.lambda_handler({}, None)
        self.assertEqual(resp["statusCode"], 500)

    def test_log_redacts_api_key_fields_and_values(self):
        output = io.StringIO()
        secret = "DISTINCTIVE_LAMBDA_SECRET_12345"
        with mock.patch.object(lf.sys, "stdout", output), \
             mock.patch.dict("os.environ", {"VI_INFERENCE_API_KEY": secret}):
            lf._log("INFO", "redaction_test", **{
                "X-API-Key": secret, "detail": f"rejected {secret}"})
        rendered = output.getvalue()
        self.assertNotIn(secret, rendered)
        self.assertEqual(json.loads(rendered)["X-API-Key"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()

"""Phase-4 tests for the Lambda drain loop + handler wiring
(solution-spec 790-802, Lambda side).

Plain unittest with a fake DB connection and fake/injected client, so no
Postgres, AWS, or MediaCloud is needed. Run:
  python -m unittest test_lambda_function
"""
import sys
import types
import unittest
from unittest import mock

import lambda_function as lf
from inference_client import PermanentInferenceError, RetryableInferenceError

GOOD = {"strategic_intent": "Economic", "tone": "Factual", "confidence": 0.9,
        "processing_time_ms": 12}


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

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class FakeClient:
    """Records infer() calls; returns a queued result or raises a queued error."""
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def infer(self, request_id, article_text, article_id=None,
              target_country=None, inferred_actor=None, on_retry=None):
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
        self.assertEqual(params, ("Economic", "Factual", 0.9, 7))
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

    def test_permanent_failure_left_unsaved(self):
        conn = FakeConn(pending_rows=self.rows(1))
        client = FakeClient([PermanentInferenceError("bad", code=400)])
        counts = lf.classify_pending(conn, client=client)
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(counts["classified"], 0)
        self.assertFalse([e for e in conn.executed if e[0].startswith("UPDATE")])

    def test_retryable_failure_left_pending(self):
        conn = FakeConn(pending_rows=self.rows(1))
        client = FakeClient([RetryableInferenceError("timeout")])
        counts = lf.classify_pending(conn, client=client)
        self.assertEqual(counts["left_pending"], 1)
        self.assertEqual(counts["classified"], 0)
        self.assertFalse([e for e in conn.executed if e[0].startswith("UPDATE")])

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

    def test_skips_when_not_configured(self):
        conn = FakeConn(pending_rows=self.rows(2))
        with mock.patch.dict("os.environ", {}, clear=True):
            counts = lf.classify_pending(conn)  # no injected client, no env
        self.assertEqual(counts["inference"], "skipped")
        self.assertEqual(counts["classified"], 0)
        self.assertEqual(client_calls := [e for e in conn.executed if e[0].startswith("UPDATE")], [])


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
        fake_mod.main = lambda: None
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


if __name__ == "__main__":
    unittest.main()

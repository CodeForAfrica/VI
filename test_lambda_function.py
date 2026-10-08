"""Parity checks: the HTTP adapter changes model location, not business rules."""
import os
import io
import json
import sys
import unittest
import types
from unittest import mock
from django.test import override_settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "inference_api.tests_settings")
import django
django.setup()
from dashboard.services.ml_inference_service import MLInferenceService
from dashboard.services.remote_inference_service import RemoteInferenceService
import lambda_function as lf


class PipelineParityTests(unittest.TestCase):
    def service(self, remote, intent, confidence, llm_intent, llm_conf, failure=False):
        s = object.__new__(RemoteInferenceService if remote else MLInferenceService)
        s.lookup_risk = mock.Mock(return_value=.42)
        s.extract_entities_from_content = mock.Mock(return_value={"organizations": [], "persons": []})
        s.extract_actor_from_content = mock.Mock(return_value="China")
        s.calculate_vulnerability_index = mock.Mock(return_value=.51)
        s._get_llm_strategic_intent = mock.Mock(return_value=(llm_intent, llm_conf, "notes"))
        if remote:
            s.client = mock.Mock()
            s.client.infer.return_value = {"strategic_intent": intent,
                "strategic_intent_confidence": confidence, "tone": "Factual", "tone_confidence": .72}
            if failure:
                s.client.infer.side_effect = RuntimeError("transport unavailable")
            s.deadline = None
            s.event_logger = mock.Mock()
        else:
            classifier = mock.Mock()
            classifier.predict.return_value = ([intent], [[confidence]])
            s._load_strategic_classifier = mock.Mock(return_value=classifier)
            s._decode_label = lambda label: label
            s.perform_tone_inference = mock.Mock(return_value=("Factual", .72))
            if failure:
                classifier.predict.side_effect = RuntimeError("model unavailable")
                s.perform_tone_inference.return_value = ("neutral", .3)
        return s

    def test_outputs_groq_inputs_and_scoring_match(self):
        cases = [("Economic", .8, "Economic", .9), ("Economic", .4, "Economic", .3),
                 ("Economic", .3, "Economic", .4), ("Economic", .8, "Sovereignty", .7),
                 ("Economic", .7, "Sovereignty", .8), ("Economic", .7, "Sovereignty", .7),
                 ("Neutral", .8, "Neutral", .7), ("unknown", 0., "Economic", .8),
                 ("unknown", 0., "unknown", 0.), ("economic dependency", .80000001, "Economic", .8)]
        for case in cases:
            for failure in (False, True):
                with self.subTest(case=case, failure=failure):
                    local = self.service(False, *case, failure=failure)
                    remote = self.service(True, *case, failure=failure)
                    self.assertEqual(remote.perform_inference("  Article text  "),
                                     local.perform_inference("  Article text  "))
                    self.assertEqual(remote._get_llm_strategic_intent.call_args,
                                     local._get_llm_strategic_intent.call_args)
                    self.assertEqual(remote.calculate_vulnerability_index.call_args,
                                     local.calculate_vulnerability_index.call_args)
                    remote.client.infer.assert_called_once()

    def test_server_never_calls_groq_or_scoring(self):
        s = self.service(False, "economic dependency", .87654321, "Economic", .9)
        result = s.perform_local_inference("already preprocessed")
        self.assertEqual(result["strategic_intent"], "economic dependency")
        self.assertEqual(result["strategic_intent_confidence"], .87654321)
        s._get_llm_strategic_intent.assert_not_called()
        s.calculate_vulnerability_index.assert_not_called()
        s.extract_entities_from_content.assert_not_called()

    def test_no_heavy_model_imports_in_caller(self):
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("transformers", sys.modules)

    @override_settings(GROQ_API_KEY="test-only", GROQ_MODEL="test-model")
    def test_real_caller_groq_method_still_runs_after_http_prediction(self):
        service = self.service(True, "Economic", .2, "Economic", .9)
        del service._get_llm_strategic_intent
        with mock.patch("dashboard.services.ml_inference_service.Groq") as groq:
            groq.return_value.chat.completions.create.return_value.choices[0].message.content = (
                '{"strategic_intent":"Sovereignty","strategic_intent_conf":0.95,"notes":"test"}')
            result = service.perform_inference("Article text")
        self.assertEqual(result["strategic_intent"], "Sovereignty")
        self.assertEqual(result["confidence"], .95)
        call = groq.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(call["messages"][1]["content"], "Article text")
        self.assertEqual(call["model"], "test-model")
        service.client.infer.assert_called_once()


class RequestLoggingTests(unittest.TestCase):
    def capture(self, responses):
        from inference_client import InferenceClient
        from test_inference_client import FakeSession
        service = PipelineParityTests().service(True, "Economic", .9, "Economic", .8)
        service.client = InferenceClient(
            "https://api.example", "private-key-not-for-logs",
            session=FakeSession(responses), sleep=lambda _: None)
        service.event_logger = lf._log
        output = io.StringIO()
        token = lf._LOG_CONTEXT.set({"invocation_id": "invocation-123", "article_id": 42})
        try:
            with mock.patch("sys.stdout", output), mock.patch("sys.stderr", output), \
                    mock.patch("dashboard.services.remote_inference_service.uuid.uuid4", return_value="r"):
                service.perform_inference("private article body")
        finally:
            lf._LOG_CONTEXT.reset(token)
        raw = output.getvalue()
        self.assertNotIn("private article body", raw)
        self.assertNotIn("private-key-not-for-logs", raw)
        events = [json.loads(line) for line in raw.splitlines() if line.startswith("{")]
        for event in events:
            self.assertEqual(event["article_id"], 42)
            self.assertEqual(event["invocation_id"], "invocation-123")
        return {event["event"]: event for event in events}

    def test_retry_and_response_summary_are_correlated(self):
        from test_inference_client import Resp, good
        events = self.capture([Resp(503), Resp(200, good())])
        started = events["local_inference_request_started"]
        self.assertEqual(started["method"], "POST")
        self.assertEqual(started["path"], "/api/v1/inference")
        self.assertEqual(started["host"], "api.example")
        self.assertEqual(events["local_inference_retry"]["http_status"], 503)
        completed = events["local_inference_request_completed"]
        self.assertEqual(completed["attempts"], 2)
        self.assertEqual(completed["strategic_intent"], "Economic")
        self.assertEqual(completed["processing_time_ms"], 12)
        for name in ("local_inference_request_started", "local_inference_retry",
                     "local_inference_request_completed", "article_inference_completed"):
            self.assertEqual(events[name]["request_id"], "r")
        self.assertGreaterEqual(completed["duration_ms"], 0)

    def test_auth_failure_logs_reason_and_existing_fallback(self):
        from test_inference_client import Resp
        events = self.capture([Resp(401)])
        failure = events["local_inference_request_failed"]
        self.assertEqual(failure["http_status"], 401)
        self.assertEqual(failure["error_code"], "http_401")
        self.assertEqual(failure["attempts"], 1)
        self.assertEqual(failure["fallback"], "existing_pipeline_defaults")
        self.assertFalse(events["article_inference_completed"]["local_models_available"])
        self.assertNotIn("local_inference_retry", events)


class HandlerVerificationTests(unittest.TestCase):
    def invoke(self, event):
        module = types.ModuleType("dashboard.services.mediacloud_ingestion_service")
        module.main = mock.Mock()
        with mock.patch.dict(sys.modules, {module.__name__: module}), \
                mock.patch("django.setup"), \
                mock.patch.object(lf, "get_db_connection", return_value=mock.MagicMock()), \
                mock.patch.object(lf, "get_count", return_value=10), \
                mock.patch.object(lf, "run_quality_validation"), \
                mock.patch.object(lf, "classify_pending", return_value={
                    "classified": 3, "failed": 0, "left_pending": 0}) as classify:
            response = lf.lambda_handler(event, None)
        return response, module.main, classify

    def test_targeted_verification_bypasses_ingestion(self):
        response, ingest, classify = self.invoke({
            "operation": "verify_inference", "article_ids": [2, 4, 6]})
        self.assertEqual(response["statusCode"], 200)
        ingest.assert_not_called()
        self.assertEqual(classify.call_args.kwargs["article_ids"], [2, 4, 6])

    def test_invalid_verification_ids_never_classify_or_ingest(self):
        for ids in (None, [], [1, 2, 3, 4], [True], [0], [-1], ["1"], [1, 1]):
            with self.subTest(ids=ids):
                response, ingest, classify = self.invoke({
                    "operation": "verify_inference", "article_ids": ids})
                self.assertEqual(response["statusCode"], 500)
                ingest.assert_not_called()
                classify.assert_not_called()

    def test_pending_only_skips_ingestion_and_targets_normal_pending_rows(self):
        response, ingest, classify = self.invoke({"operation": "processing-only"})
        self.assertEqual(response["statusCode"], 200)
        ingest.assert_not_called()
        classify.assert_called_once()
        self.assertNotIn("article_ids", classify.call_args.kwargs)
        summary = json.loads(response["body"])
        self.assertEqual(summary["operation"], "processing-only")
        self.assertEqual(summary["ingested"], 0)

    def test_daily_events_still_ingest_then_classify(self):
        for event in ({}, {"source": "aws.events", "detail-type": "Scheduled Event"}):
            with self.subTest(event=event):
                response, ingest, classify = self.invoke(event)
                self.assertEqual(response["statusCode"], 200)
                ingest.assert_called_once()
                classify.assert_called_once()

    def test_typo_in_operation_does_not_accidentally_ingest(self):
        response, ingest, classify = self.invoke({"operation": "processing-onyl"})
        self.assertEqual(response["statusCode"], 500)
        ingest.assert_not_called()
        classify.assert_not_called()

    def test_pending_only_does_not_import_mediacloud_or_run_validation(self):
        with mock.patch.dict(sys.modules, {"dashboard.services.mediacloud_ingestion_service": None}), \
                mock.patch("django.setup"), \
                mock.patch.object(lf, "get_db_connection", return_value=mock.MagicMock()) as connect, \
                mock.patch.object(lf, "get_count", return_value=10) as count, \
                mock.patch.object(lf, "run_quality_validation") as validate, \
                mock.patch.object(lf, "classify_pending", return_value={"classified": 0}) as classify:
            response = lf.lambda_handler({"operation": "processing-only"}, None)
        self.assertEqual(response["statusCode"], 200)
        classify.assert_called_once()
        validate.assert_not_called()
        count.assert_not_called()
        connect.return_value.close.assert_called_once()

    def test_pending_only_failure_is_logged_and_connection_closed(self):
        output = io.StringIO()
        conn = mock.MagicMock()
        with mock.patch("django.setup"), \
                mock.patch.object(lf, "get_db_connection", return_value=conn), \
                mock.patch.object(lf, "get_count", return_value=10), \
                mock.patch.object(lf, "classify_pending", side_effect=RuntimeError("database failed")), \
                mock.patch("sys.stdout", output), mock.patch("sys.stderr", output):
            response = lf.lambda_handler({"operation": "processing-only"}, None)
        self.assertEqual(response["statusCode"], 500)
        self.assertIn('"event": "pending_processing_failed"', output.getvalue())
        conn.close.assert_called_once()

    def test_ingestion_only_never_classifies(self):
        response, ingest, classify = self.invoke({"operation": "ingestion-only"})
        self.assertEqual(response["statusCode"], 200)
        ingest.assert_called_once()
        classify.assert_not_called()
        summary = json.loads(response["body"])
        self.assertEqual(summary["operation"], "ingestion-only")
        self.assertEqual(summary["classified"], 0)
        self.assertIsNone(summary["left_pending"])
        self.assertEqual(summary["reason"], "ingestion_only")

    def test_ingestion_only_validates_counts_and_closes_without_inference_dependencies(self):
        module = types.ModuleType("dashboard.services.mediacloud_ingestion_service")
        module.main = mock.Mock()
        conn = mock.MagicMock()
        with mock.patch.dict(sys.modules, {module.__name__: module}), \
                mock.patch("django.setup"), \
                mock.patch.object(lf, "get_db_connection", return_value=conn), \
                mock.patch.object(lf, "get_count", side_effect=[10, 12, 12]), \
                mock.patch.object(lf, "run_quality_validation") as validate, \
                mock.patch.object(lf, "_build_client", side_effect=AssertionError("must not call API")), \
                mock.patch.object(lf, "classification_lock", side_effect=AssertionError("must not classify")):
            response = lf.lambda_handler({"operation": "ingestion-only"}, None)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"])["ingested"], 2)
        module.main.assert_called_once()
        validate.assert_called_once_with(conn)
        conn.close.assert_called_once()

    def test_ingestion_failure_does_not_start_processing(self):
        module = types.ModuleType("dashboard.services.mediacloud_ingestion_service")
        module.main = mock.Mock(side_effect=RuntimeError("ingestion unavailable"))
        with mock.patch.dict(sys.modules, {module.__name__: module}), \
                mock.patch("django.setup"), \
                mock.patch.object(lf, "get_db_connection", return_value=mock.MagicMock()), \
                mock.patch.object(lf, "get_count", return_value=10), \
                mock.patch.object(lf, "classify_pending") as classify:
            response = lf.lambda_handler({"operation": "ingestion-only"}, None)
        self.assertEqual(response["statusCode"], 500)
        classify.assert_not_called()


class PersistenceTests(unittest.TestCase):
    def connection(self, rows=()):
        c = mock.MagicMock()
        c.cursor.return_value.__enter__.return_value.fetchall.return_value = rows
        c.cursor.return_value.__enter__.return_value.fetchone.return_value = (True,)
        return c

    def test_original_pending_predicate(self):
        c = self.connection()
        lf.fetch_pending(c, 20)
        sql = c.cursor.return_value.__enter__.return_value.execute.call_args.args[0]
        self.assertIn("ml_processed_at IS NULL", sql)
        self.assertIn("strategic_intent IS NULL OR strategic_intent = ''", sql)
        self.assertNotIn("inference_status", sql)

    def test_verification_fetch_is_restricted_to_recorded_ids(self):
        c = self.connection()
        lf.fetch_pending(c, 200, article_ids=[2, 4, 6])
        sql, params = c.cursor.return_value.__enter__.return_value.execute.call_args.args
        self.assertIn("id = ANY(%s)", sql)
        self.assertEqual(params, ([2, 4, 6],))

    def test_verification_rejects_missing_selected_rows(self):
        c = self.connection([(2, "article", None, None)])
        with self.assertRaises(ValueError):
            lf.classify_pending(c, pipeline=mock.Mock(), article_ids=[2, 4])

    def test_verification_never_saves_an_api_failure_fallback(self):
        c = self.connection([(2, "article", None, None)])
        pipeline = mock.Mock()
        pipeline._error = RuntimeError("HTTPS failed")
        with mock.patch.object(lf, "save_classification") as save:
            result = lf.classify_pending(c, pipeline=pipeline, article_ids=[2])
        save.assert_not_called()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["classified"], 0)
        self.assertEqual(result["left_pending"], 1)

    def test_verification_returns_api_and_persisted_result_evidence(self):
        c = self.connection([(2, "article", None, None)])
        pipeline = mock.Mock()
        pipeline._error = None
        pipeline._request_id = "request-2"
        pipeline._result = {"strategic_intent": "Information Warfare", "tone": "Factual", "confidence": .7, "model_version": "2026-09-04"}
        pipeline.perform_inference.return_value = {"strategic_intent": "Economic", "tone": "Factual", "confidence": .9}
        with mock.patch.object(lf, "save_classification") as save:
            result = lf.classify_pending(c, pipeline=pipeline, article_ids=[2])
        save.assert_called_once()
        self.assertEqual(result["results"][0]["api_intent"], "Information Warfare")
        self.assertEqual(result["results"][0]["saved_intent"], "Economic")
        self.assertEqual(result["results"][0]["saved_confidence"], .9)

    def test_neutral_keeps_original_null_mapping(self):
        c = self.connection()
        lf.save_classification(c, 3, {"strategic_intent": "Neutral", "confidence": .7, "tone": "neutral"})
        sql, params = c.cursor.return_value.__enter__.return_value.execute.call_args.args
        self.assertEqual(params, (None, .7, "neutral", 3))
        self.assertIn("ml_processed_at = NOW()", sql)
        self.assertNotIn("inference_status", sql)
        self.assertNotIn("prediction_source", sql)

    def test_political_destabilization_uses_existing_canonical_mapping(self):
        c = self.connection()
        lf.save_classification(
            c,
            3,
            {
                "strategic_intent": "Political Destabilization",
                "confidence": .7,
                "tone": "Factual",
            },
        )
        _, params = c.cursor.return_value.__enter__.return_value.execute.call_args.args
        self.assertEqual(params, ("SocialFragility", .7, "Factual", 3))

    def test_batch_runs_orchestration_before_saving(self):
        c = self.connection([(3, "article", None, None)])
        pipeline = mock.Mock()
        pipeline.perform_inference.return_value = {"strategic_intent": "Economic", "tone": "Factual", "confidence": .8}
        result = lf.classify_pending(c, pipeline=pipeline)
        pipeline.perform_inference.assert_called_once_with("article")
        self.assertEqual(result["classified"], 1)

    def test_save_failure_leaves_row_pending(self):
        c = self.connection([(3, "article", None, None)])
        with mock.patch.object(lf, "save_classification", side_effect=RuntimeError("write failed")):
            result = lf.classify_pending(c, pipeline=mock.Mock())
        self.assertEqual(result["left_pending"], 1)
        self.assertEqual(c.rollback.call_count, 2)  # failed article and lock cleanup


class ClassificationLockTests(unittest.TestCase):
    def connection(self, acquired=True, released=True):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [(acquired,), (released,)]
        return conn, cur

    def test_busy_lock_never_fetches_calls_api_or_saves(self):
        conn, cur = self.connection(acquired=False)
        pipeline = mock.Mock()
        with mock.patch.object(lf, "fetch_pending") as fetch, \
                mock.patch.object(lf, "save_classification") as save:
            result = lf.classify_pending(conn, pipeline=pipeline)
        self.assertEqual(result["reason"], "classification_lock_held")
        self.assertIsNone(result["left_pending"])
        fetch.assert_not_called()
        save.assert_not_called()
        pipeline.perform_inference.assert_not_called()
        self.assertEqual(cur.execute.call_count, 1)

    def test_lock_wraps_selection_and_is_released_after_success(self):
        conn, cur = self.connection()
        with mock.patch.object(lf, "_classify_pending_locked", return_value={"classified": 1}) as classify:
            self.assertEqual(lf.classify_pending(conn), {"classified": 1})
        classify.assert_called_once()
        self.assertEqual(cur.execute.call_args_list, [
            mock.call("SELECT pg_try_advisory_lock(%s)", (lf.CLASSIFICATION_LOCK_ID,)),
            mock.call("SELECT pg_advisory_unlock(%s)", (lf.CLASSIFICATION_LOCK_ID,))])

    def test_lock_released_when_selection_or_processing_raises(self):
        conn, cur = self.connection()
        with mock.patch.object(lf, "_classify_pending_locked", side_effect=RuntimeError("failed")):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                lf.classify_pending(conn)
        self.assertIn("pg_advisory_unlock", cur.execute.call_args.args[0])
        conn.rollback.assert_called_once()

    def test_lock_release_failure_is_not_silently_successful(self):
        conn, cur = self.connection(released=False)
        with mock.patch.object(lf, "_classify_pending_locked", return_value={}):
            with self.assertRaisesRegex(RuntimeError, "not held"):
                lf.classify_pending(conn)

    def test_lock_acquisition_database_error_is_not_a_busy_skip(self):
        conn, cur = self.connection()
        cur.execute.side_effect = RuntimeError("database unavailable")
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            lf.classify_pending(conn)

    def test_empty_queue_never_builds_client_or_pipeline(self):
        conn, _ = self.connection()
        with mock.patch.object(lf, "fetch_pending", return_value=[]), \
                mock.patch.object(lf, "_build_client") as build:
            result = lf.classify_pending(conn)
        build.assert_not_called()
        self.assertEqual(result["classified"], 0)
        self.assertEqual(result["left_pending"], 0)

    def test_time_budget_leaves_unattempted_articles_pending(self):
        conn, _ = self.connection()
        pipeline = mock.Mock()
        with mock.patch.object(lf, "fetch_pending", return_value=[(1, "text", None, None), (2, "text", None, None)]), \
                mock.patch.object(lf.time, "time", return_value=100):
            result = lf.classify_pending(conn, pipeline=pipeline, deadline=100)
        self.assertEqual(result["left_pending"], 2)
        pipeline.perform_inference.assert_not_called()


@unittest.skipUnless(os.environ.get("VI_TEST_POSTGRES_HOST"), "isolated PostgreSQL integration database not configured")
class PostgreSQLClassificationLockTests(unittest.TestCase):
    def setUp(self):
        import psycopg2
        self.first = psycopg2.connect(host=os.environ["VI_TEST_POSTGRES_HOST"], dbname="postgres", user="postgres", connect_timeout=5)
        self.second = psycopg2.connect(host=os.environ["VI_TEST_POSTGRES_HOST"], dbname="postgres", user="postgres", connect_timeout=5)
        self.addCleanup(self.first.close)
        self.addCleanup(self.second.close)

    def test_competing_worker_skips_until_owner_finishes(self):
        with lf.classification_lock(self.first) as acquired:
            self.assertTrue(acquired)
            # Session locking must survive successful and failed row transactions.
            self.first.commit()
            self.first.rollback()
            with mock.patch.object(lf, "fetch_pending") as fetch:
                result = lf.classify_pending(self.second)
            self.assertEqual(result["reason"], "classification_lock_held")
            fetch.assert_not_called()
        with lf.classification_lock(self.second) as acquired:
            self.assertTrue(acquired)

    def test_failed_transaction_is_rolled_back_before_unlock(self):
        import psycopg2
        with self.assertRaises(psycopg2.errors.UndefinedTable):
            with lf.classification_lock(self.first):
                with self.first.cursor() as cur:
                    cur.execute("SELECT * FROM vi_deliberately_missing_lock_test_table")
        with lf.classification_lock(self.second) as acquired:
            self.assertTrue(acquired)

    def test_disconnected_owner_does_not_leave_permanent_lock(self):
        with self.first.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (lf.CLASSIFICATION_LOCK_ID,))
            self.assertTrue(cur.fetchone()[0])
        self.first.close()
        with lf.classification_lock(self.second) as acquired:
            self.assertTrue(acquired)


if __name__ == "__main__":
    unittest.main()

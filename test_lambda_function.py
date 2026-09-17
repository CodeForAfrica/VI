"""Parity checks: the HTTP adapter changes model location, not business rules."""
import os
import sys
import unittest
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


class PersistenceTests(unittest.TestCase):
    def connection(self, rows=()):
        c = mock.MagicMock()
        c.cursor.return_value.__enter__.return_value.fetchall.return_value = rows
        return c

    def test_original_pending_predicate(self):
        c = self.connection()
        lf.fetch_pending(c, 20)
        sql = c.cursor.return_value.__enter__.return_value.execute.call_args.args[0]
        self.assertIn("ml_processed_at IS NULL", sql)
        self.assertIn("strategic_intent IS NULL OR strategic_intent = ''", sql)
        self.assertNotIn("inference_status", sql)

    def test_neutral_keeps_original_null_mapping(self):
        c = self.connection()
        lf.save_classification(c, 3, {"strategic_intent": "Neutral", "confidence": .7, "tone": "neutral"})
        sql, params = c.cursor.return_value.__enter__.return_value.execute.call_args.args
        self.assertEqual(params, (None, .7, "neutral", 3))
        self.assertIn("ml_processed_at = NOW()", sql)
        self.assertNotIn("inference_status", sql)
        self.assertNotIn("prediction_source", sql)

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
        c.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()

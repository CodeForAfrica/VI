"""Phase-1 test suite for the inference API (solution-spec 772-788).

Run:  python manage.py test inference_api --settings=inference_api.tests_settings

Everything here is contract-level: the real ML service is never loaded. Model
calls are mocked and readiness is toggled, so the suite runs fast with no torch
and no DB server. The gunicorn/real-model integration boot is Phase 5.
"""
import importlib
import io
import json
import sys
import types
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from django.apps import apps
from django.test import SimpleTestCase, TestCase, override_settings

from inference_api import logs, runtime, security, views

VALID_KEY = "super-secret-key-value"
CALLER = "test-caller"
J = "application/json"
REQUEST_ID = "7d86d12d-dc93-44da-8e03-06ba1aab36cc"
REQUEST_ID_2 = "31e39d5f-96eb-4405-82d4-065582822118"

# Allowed strategic-intent enum (spec 345-357).
ALLOWED_INTENTS = {
    "Economic", "Sovereignty", "LGBTQ", "Religious", "ElectionInfluence",
    "MilitaryPresence", "ResourceDependency", "SocialFragility", "Neutral",
}

STUB_RESULT = {
    "strategic_intent": "Economic",
    "strategic_intent_confidence": 0.87,
    "tone": "Factual",
    "tone_confidence": 0.79,
    "confidence": 0.87,
    "lang_detect": "en",
    "prediction_source": "ensemble_matched",
    "model_version": "test",
    "processing_time_ms": 42,
}


@override_settings(ROOT_URLCONF="inference_api.urls")
class ApiTestBase(SimpleTestCase):
    """Isolates the auth key list and rate limiter per test."""

    def setUp(self):
        self._orig_keys = security._ACCEPTED_KEYS
        security._ACCEPTED_KEYS = [{"caller": CALLER, "key": VALID_KEY}]
        self._orig_limiter = views.rate_limiter
        views.rate_limiter = security.RateLimiter(10_000)  # effectively unlimited

    def tearDown(self):
        security._ACCEPTED_KEYS = self._orig_keys
        views.rate_limiter = self._orig_limiter

    def post(self, payload, ctype=J, key=VALID_KEY, **extra):
        body = payload if isinstance(payload, (str, bytes)) else json.dumps(payload)
        if key is not None:
            extra["HTTP_X_API_KEY"] = key
        return self.client.post("/api/v1/inference", body, ctype, **extra)

    def valid_payload(self, **over):
        p = {"request_id": REQUEST_ID, "article_text": "some article text"}
        p.update(over)
        return p


class HealthReadinessTests(ApiTestBase):
    def test_healthz_ok_and_does_not_load_model(self):
        with mock.patch("inference_api.runtime.is_ready") as ready:
            r = self.client.get("/healthz")
            ready.assert_not_called()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})

    def test_readyz_503_before_load(self):
        with mock.patch("inference_api.runtime.is_ready", return_value=False):
            r = self.client.get("/readyz")
        self.assertEqual(r.status_code, 503)
        self.assertFalse(r.json()["models_loaded"])

    def test_readyz_200_after_load(self):
        with mock.patch("inference_api.runtime.is_ready", return_value=True):
            r = self.client.get("/readyz")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["models_loaded"])
        self.assertEqual(r.json()["model_version"], runtime.MODEL_VERSION)


class AuthTests(ApiTestBase):
    def test_missing_key_401_generic(self):
        r = self.post(self.valid_payload(), key=None)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["error"]["code"], "unauthorized")
        # Never disclose which key/why (spec 397-398).
        self.assertNotIn("request_id", r.json()["error"])

    def test_unknown_key_401(self):
        self.assertEqual(self.post(self.valid_payload(), key="nope").status_code, 401)

    def test_empty_key_401(self):
        self.assertEqual(self.post(self.valid_payload(), key="").status_code, 401)

    def test_valid_key_authenticates(self):
        with mock.patch("inference_api.runtime.is_ready", return_value=True), \
             mock.patch("inference_api.runtime.run_inference", return_value=STUB_RESULT):
            self.assertEqual(self.post(self.valid_payload()).status_code, 200)

    def test_malformed_key_env_rejects_everyone(self):
        security._ACCEPTED_KEYS = []
        self.assertEqual(self.post(self.valid_payload()).status_code, 401)

    def test_valid_but_wrong_key_json_shapes_fail_closed(self):
        for raw in ('{"caller": "x"}', '[null]', '["not-an-object"]'):
            with mock.patch.dict("os.environ", {"VI_INFERENCE_ACCEPTED_KEYS": raw}):
                self.assertEqual(security._load_accepted_keys(), [])


class ValidationTests(ApiTestBase):
    def test_wrong_content_type_400(self):
        self.assertEqual(self.post(self.valid_payload(), ctype="text/plain").status_code, 400)

    def test_non_json_body_400(self):
        self.assertEqual(self.post("{not json").status_code, 400)

    def test_non_object_json_400(self):
        self.assertEqual(self.post("[1,2,3]").status_code, 400)

    def test_missing_request_id_400(self):
        self.assertEqual(self.post({"article_text": "x"}).status_code, 400)

    def test_missing_article_text_400(self):
        self.assertEqual(self.post({"request_id": "r"}).status_code, 400)

    def test_blank_article_text_400(self):
        self.assertEqual(self.post({"request_id": "r", "article_text": "   "}).status_code, 400)

    def test_non_uuid_request_id_400(self):
        self.assertEqual(self.post({"request_id": "not-a-uuid", "article_text": "x"}).status_code, 400)

    def test_non_string_article_text_400(self):
        self.assertEqual(self.post(self.valid_payload(article_text=123)).status_code, 400)

    def test_invalid_article_id_400(self):
        self.assertEqual(self.post(self.valid_payload(article_id={"bad": "id"})).status_code,
                         400)

    def test_oversized_article_id_400(self):
        self.assertEqual(self.post(self.valid_payload(article_id="x" * 129)).status_code,
                         400)

    def test_get_on_inference_405(self):
        self.assertEqual(self.client.get("/api/v1/inference").status_code, 405)

    def test_put_on_inference_405(self):
        self.assertEqual(self.client.put("/api/v1/inference").status_code, 405)

    @override_settings()
    def test_content_length_over_limit_413(self):
        with mock.patch.object(views, "MAX_BODY_BYTES", 100):
            r = self.client.post(
                "/api/v1/inference", "x" * 500, J,
                HTTP_X_API_KEY=VALID_KEY, CONTENT_LENGTH="500")
        self.assertEqual(r.status_code, 413)

    def test_body_over_limit_413(self):
        with mock.patch.object(views, "MAX_BODY_BYTES", 50):
            r = self.post(self.valid_payload(article_text="z" * 500))
        self.assertEqual(r.status_code, 413)

    def test_article_text_over_char_limit_413(self):
        with mock.patch.object(views, "MAX_TEXT_CHARS", 10):
            r = self.post(self.valid_payload(article_text="z" * 50))
        self.assertEqual(r.status_code, 413)


class RateLimitTests(ApiTestBase):
    def test_limit_then_429(self):
        views.rate_limiter = security.RateLimiter(2)
        with mock.patch("inference_api.runtime.is_ready", return_value=True), \
             mock.patch("inference_api.runtime.run_inference", return_value=STUB_RESULT):
            codes = [self.post(self.valid_payload()).status_code for _ in range(3)]
        self.assertEqual(codes[:2], [200, 200])
        self.assertEqual(codes[2], 429)

    def test_429_has_retry_after(self):
        views.rate_limiter = security.RateLimiter(1)
        with mock.patch("inference_api.runtime.is_ready", return_value=True), \
             mock.patch("inference_api.runtime.run_inference", return_value=STUB_RESULT):
            self.post(self.valid_payload())          # consumes the single slot
            r = self.post(self.valid_payload())      # over the limit
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.headers.get("Retry-After"), "60")
        self.assertNotIn("request_id", r.json()["error"])

    def test_invalid_authenticated_requests_consume_rate_limit(self):
        views.rate_limiter = security.RateLimiter(1)
        self.assertEqual(self.post("{not json").status_code, 400)
        self.assertEqual(self.post("{not json").status_code, 429)

    def test_limit_is_per_caller(self):
        security._ACCEPTED_KEYS = [
            {"caller": "a", "key": "ka"}, {"caller": "b", "key": "kb"}]
        views.rate_limiter = security.RateLimiter(1)
        self.assertTrue(views.rate_limiter.allow("a"))
        # a is now exhausted, but b still has budget.
        self.assertFalse(views.rate_limiter.allow("a"))
        self.assertTrue(views.rate_limiter.allow("b"))


class InferenceHappyPathTests(ApiTestBase):
    def test_200_full_schema_and_echo(self):
        with mock.patch("inference_api.runtime.is_ready", return_value=True), \
             mock.patch("inference_api.runtime.run_inference", return_value=STUB_RESULT):
            r = self.post(self.valid_payload(request_id=REQUEST_ID_2, article_id=12345))
        self.assertEqual(r.status_code, 200)
        b = r.json()
        self.assertEqual(b["request_id"], REQUEST_ID_2)
        self.assertEqual(b["article_id"], 12345)
        for field in ("strategic_intent", "strategic_intent_confidence", "tone",
                      "tone_confidence", "confidence", "prediction_source",
                      "lang_detect", "model_version", "processing_time_ms"):
            self.assertIn(field, b)

    def test_intent_in_allowed_enum(self):
        with mock.patch("inference_api.runtime.is_ready", return_value=True), \
             mock.patch("inference_api.runtime.run_inference", return_value=STUB_RESULT):
            r = self.post(self.valid_payload())
        self.assertIn(r.json()["strategic_intent"], ALLOWED_INTENTS)


class FailureSafetyTests(ApiTestBase):
    def test_not_ready_503_with_request_id(self):
        with mock.patch("inference_api.runtime.is_ready", return_value=False):
            r = self.post(self.valid_payload(request_id=REQUEST_ID_2))
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error"]["code"], "models_not_ready")
        self.assertEqual(r.json()["error"]["request_id"], REQUEST_ID_2)
        self.assertEqual(r.headers.get("Retry-After"), "30")

    def test_service_error_is_500_not_neutral(self):
        def boom(text):
            raise RuntimeError("model exploded")
        with mock.patch("inference_api.runtime.is_ready", return_value=True), \
             mock.patch("inference_api.runtime.run_inference", side_effect=boom):
            r = self.post(self.valid_payload())
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.json()["error"]["code"], "inference_failed")
        self.assertNotIn("strategic_intent", r.json())

    def test_model_contract_error_is_422(self):
        error = runtime.InferenceRuntimeError(
            "invalid_tone", "model returned an invalid tone")
        with mock.patch("inference_api.runtime.is_ready", return_value=True), \
             mock.patch("inference_api.runtime.run_inference", side_effect=error):
            r = self.post(self.valid_payload())
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "invalid_tone")


class LoggingDisciplineTests(ApiTestBase):
    def test_every_http_access_is_logged(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(logs, "_STDOUT", out), \
             mock.patch.object(logs, "_STDERR", err), \
             mock.patch("inference_api.runtime.is_ready", return_value=False):
            responses = [
                self.client.get("/healthz"),
                self.client.get("/readyz"),
                self.client.get("/missing"),
                self.post(self.valid_payload(), key=None),
            ]
        self.assertEqual([response.status_code for response in responses],
                         [200, 503, 404, 401])
        entries = [
            json.loads(line)
            for line in (out.getvalue() + err.getvalue()).splitlines()
            if line.strip()
        ]
        accesses = [entry for entry in entries if entry.get("event") == "http_access"]
        self.assertEqual(len(accesses), 4)
        self.assertEqual([entry["http_status"] for entry in accesses],
                         [200, 503, 404, 401])
        for entry in accesses:
            self.assertIn(entry["outcome"], {"success", "rejected", "failed"})
            self.assertIn("duration_ms", entry)
            self.assertTrue(entry.get("trace_id"))
            self.assertNotIn("headers", entry)
            self.assertNotIn("request_body", entry)

    def test_key_and_article_text_never_logged(self):
        secret_text = "TOP_SECRET_ARTICLE_BODY_QWERTY"
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("inference_api.runtime.is_ready", return_value=True), \
             mock.patch("inference_api.runtime.run_inference", return_value=STUB_RESULT), \
             mock.patch.object(logs, "_STDOUT", out), \
             mock.patch.object(logs, "_STDERR", err), \
             redirect_stdout(out), redirect_stderr(err):
            self.post(self.valid_payload(article_text=secret_text))
        combined = out.getvalue() + err.getvalue()
        self.assertNotIn(VALID_KEY, combined)
        self.assertNotIn(secret_text, combined)
        entries = [json.loads(line) for line in combined.splitlines() if line.strip()]
        self.assertTrue(entries)
        self.assertTrue(all(entry.get("trace_id") for entry in entries))

    def test_logging_helper_redacts_sensitive_fields_and_nested_values(self):
        out = io.StringIO()
        secret = "DISTINCTIVE_API_SECRET_987654"
        with mock.patch.object(logs, "_STDOUT", out), \
             mock.patch.dict("os.environ", {"VI_INFERENCE_API_KEY": secret}):
            logs.log_event("INFO", "redaction_test",
                           **{"X-API-Key": secret,
                              "nested": {"password": secret},
                              "detail": f"provider rejected {secret}"})
        rendered = out.getvalue()
        self.assertNotIn(secret, rendered)
        self.assertGreaterEqual(rendered.count("[REDACTED]"), 3)

    def test_individual_key_inside_accepted_keys_json_is_redacted(self):
        out = io.StringIO()
        secret = "DISTINCTIVE_ACCEPTED_KEY_24680"
        accepted = json.dumps([{"caller": "lambda", "key": secret}])
        with mock.patch.object(logs, "_STDOUT", out), \
             mock.patch.dict("os.environ", {"VI_INFERENCE_ACCEPTED_KEYS": accepted}):
            logs.log_event("INFO", "redaction_test",
                           detail=f"provider rejected {secret}")
        self.assertNotIn(secret, out.getvalue())
        self.assertIn("[REDACTED]", out.getvalue())

    def test_ml_logger_is_routed_through_structured_root_handler(self):
        import logging

        root = logging.getLogger()
        ml_logger = logging.getLogger("dashboard.services.ml_inference_service")
        original_root_handlers = root.handlers[:]
        original_root_level = root.level
        original_handlers = ml_logger.handlers[:]
        original_propagate = ml_logger.propagate
        try:
            ml_logger.handlers = [logging.StreamHandler(io.StringIO())]
            ml_logger.propagate = False
            logs.install_stdlib_logging()
            self.assertEqual(ml_logger.handlers, [])
            self.assertTrue(ml_logger.propagate)
            self.assertIsInstance(root.handlers[0].formatter, logs._JsonFormatter)
        finally:
            root.handlers = original_root_handlers
            root.setLevel(original_root_level)
            ml_logger.handlers = original_handlers
            ml_logger.propagate = original_propagate

    def test_invalid_request_id_value_is_not_logged(self):
        out, err = io.StringIO(), io.StringIO()
        attacker_value = "UNTRUSTED_REQUEST_ID_CONTENT_12345"
        with mock.patch.object(logs, "_STDOUT", out), \
             mock.patch.object(logs, "_STDERR", err):
            self.post({"request_id": attacker_value, "article_text": "safe"})
        self.assertNotIn(attacker_value, out.getvalue() + err.getvalue())


class ResponseMappingTests(SimpleTestCase):
    """Exercises the real runtime.run_inference + dashboard.utils mapping with a
    fake ML service (no torch, no DB queries)."""

    class FakeService:
        def __init__(self, payload):
            self.payload = payload

        def perform_inference(self, text):
            return self.payload

    def run_with(self, payload):
        payload = {
            "strategic_intent": "Economic",
            "strategic_intent_confidence": 0.5,
            "tone": "Factual",
            "tone_confidence": 0.5,
            "confidence": 0.5,
            "prediction_source": "model",
            **payload,
        }
        runtime._service_holder["service"] = self.FakeService(payload)
        return runtime.run_inference("article text")

    def tearDown(self):
        runtime._service_holder["service"] = None

    def test_raw_intent_canonicalized(self):
        r = self.run_with({"strategic_intent": "economic dependency", "confidence": 0.9,
                           "tone": "Factual"})
        self.assertEqual(r["strategic_intent"], "Economic")

    def test_neutral_kept_explicit_not_null(self):
        for raw in ("neutral", "Neutral"):
            r = self.run_with({"strategic_intent": raw, "confidence": 0.1, "tone": "Factual"})
            self.assertEqual(r["strategic_intent"], "Neutral")

    def test_unknown_intent_raises_instead_of_becoming_neutral(self):
        for raw in ("unknown", "some gibberish", None):
            with self.assertRaises(RuntimeError):
                self.run_with({"strategic_intent": raw})

    def test_confidences_rounded_4dp(self):
        r = self.run_with({"strategic_intent": "Sovereignty", "confidence": 0.876543,
                           "strategic_intent_confidence": 0.876543,
                           "tone_confidence": 0.111119, "tone": "Factual"})
        self.assertEqual(r["strategic_intent_confidence"], 0.8765)
        self.assertEqual(r["tone_confidence"], 0.1111)

    def test_invalid_confidence_raises(self):
        with self.assertRaises(RuntimeError):
            self.run_with({"confidence": 2.0})

    def test_no_service_raises(self):
        runtime._service_holder["service"] = None
        with self.assertRaises(RuntimeError):
            runtime.run_inference("x")


class StrategicInferenceAvailabilityTests(SimpleTestCase):
    """Operational source failures must remain retryable, not become 422s."""

    def test_both_sources_failing_raises_retryable_runtime_error(self):
        from dashboard.services.strategic_arbitration import (
            choose_strategic_prediction,
        )

        with self.assertRaisesRegex(
                RuntimeError, "No strategic inference source produced"):
            choose_strategic_prediction(
                "unknown", 0.0, False, "Neutral", 0.0, False
            )

    def test_llm_result_is_used_when_local_model_is_unavailable(self):
        from dashboard.services.strategic_arbitration import (
            choose_strategic_prediction,
        )

        intent, confidence, source = choose_strategic_prediction(
            "unknown", 0.0, False, "Neutral", 0.0, True
        )
        self.assertEqual(intent, "Neutral")
        self.assertEqual(confidence, 0.0)
        self.assertEqual(source, "llm")


class WarmupReadinessTests(SimpleTestCase):
    def tearDown(self):
        runtime._ready.clear()
        runtime._service_holder["service"] = None

    def test_failed_required_model_keeps_service_unready(self):
        class FailedService:
            def _load_strategic_classifier(self):
                return None

        fake_module = types.ModuleType("dashboard.services.ml_inference_service")
        fake_module.get_ml_service = lambda: FailedService()
        runtime._ready.clear()
        with mock.patch.dict(sys.modules,
                             {"dashboard.services.ml_inference_service": fake_module}):
            runtime._warmup()
        self.assertFalse(runtime.is_ready())
        self.assertIsNone(runtime._service_holder["service"])


class MigrationBackfillTests(TestCase):
    def test_existing_classification_without_timestamp_is_completed(self):
        from dashboard.models import MediaNarrative

        article = MediaNarrative.objects.create(
            article_text="historically classified article",
            strategic_intent="Economic",
            ml_processed_at=None,
            inference_status="pending",
        )
        migration = importlib.import_module(
            "dashboard.migrations.0010_medianarrative_inference_state"
        )
        migration.set_existing_states(apps, None)
        article.refresh_from_db()
        self.assertEqual(article.inference_status, "completed")

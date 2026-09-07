"""Phase-1 test suite for the inference API (solution-spec 772-788).

Run:  python manage.py test inference_api --settings=inference_api.tests_settings

Everything here is contract-level: the real ML service is never loaded. Model
calls are mocked and readiness is toggled, so the suite runs fast with no torch
and no DB server. The gunicorn/real-model integration boot is Phase 5.
"""
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from django.test import SimpleTestCase, override_settings

from inference_api import runtime, security, views

VALID_KEY = "super-secret-key-value"
CALLER = "test-caller"
J = "application/json"

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
        p = {"request_id": "rid-1", "article_text": "some article text"}
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
            r = self.post(self.valid_payload(request_id="rid-9", article_id=12345))
        self.assertEqual(r.status_code, 200)
        b = r.json()
        self.assertEqual(b["request_id"], "rid-9")
        self.assertEqual(b["article_id"], 12345)
        for field in ("strategic_intent", "strategic_intent_confidence", "tone",
                      "tone_confidence", "confidence", "prediction_source",
                      "model_version", "processing_time_ms"):
            self.assertIn(field, b)

    def test_intent_in_allowed_enum(self):
        with mock.patch("inference_api.runtime.is_ready", return_value=True), \
             mock.patch("inference_api.runtime.run_inference", return_value=STUB_RESULT):
            r = self.post(self.valid_payload())
        self.assertIn(r.json()["strategic_intent"], ALLOWED_INTENTS)


class FailureSafetyTests(ApiTestBase):
    def test_not_ready_503_with_request_id(self):
        with mock.patch("inference_api.runtime.is_ready", return_value=False):
            r = self.post(self.valid_payload(request_id="rid-503"))
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error"]["code"], "models_not_ready")
        self.assertEqual(r.json()["error"]["request_id"], "rid-503")
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


class LoggingDisciplineTests(ApiTestBase):
    def test_key_and_article_text_never_logged(self):
        secret_text = "TOP_SECRET_ARTICLE_BODY_QWERTY"
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("inference_api.runtime.is_ready", return_value=True), \
             mock.patch("inference_api.runtime.run_inference", return_value=STUB_RESULT), \
             redirect_stdout(out), redirect_stderr(err):
            self.post(self.valid_payload(article_text=secret_text))
        combined = out.getvalue() + err.getvalue()
        self.assertNotIn(VALID_KEY, combined)
        self.assertNotIn(secret_text, combined)


class ResponseMappingTests(SimpleTestCase):
    """Exercises the real runtime.run_inference + dashboard.utils mapping with a
    fake ML service (no torch, no DB queries)."""

    class FakeService:
        def __init__(self, payload):
            self.payload = payload

        def perform_inference(self, text):
            return self.payload

    def run_with(self, payload):
        runtime._service_holder["service"] = self.FakeService(payload)
        return runtime.run_inference("article text")

    def tearDown(self):
        runtime._service_holder["service"] = None

    def test_raw_intent_canonicalized(self):
        r = self.run_with({"strategic_intent": "economic dependency", "confidence": 0.9,
                           "tone": "Factual"})
        self.assertEqual(r["strategic_intent"], "Economic")

    def test_neutral_kept_explicit_not_null(self):
        for raw in ("neutral", "some gibberish", None):
            r = self.run_with({"strategic_intent": raw, "confidence": 0.1, "tone": "Factual"})
            self.assertEqual(r["strategic_intent"], "Neutral")

    def test_confidences_rounded_4dp(self):
        r = self.run_with({"strategic_intent": "Sovereignty", "confidence": 0.876543,
                           "strategic_intent_confidence": 0.876543,
                           "tone_confidence": 0.111119, "tone": "Factual"})
        self.assertEqual(r["strategic_intent_confidence"], 0.8765)
        self.assertEqual(r["tone_confidence"], 0.1111)

    def test_missing_keys_get_safe_defaults(self):
        r = self.run_with({"strategic_intent": "Economic"})
        self.assertEqual(r["confidence"], 0.0)
        self.assertEqual(r["tone"], "Factual")
        self.assertEqual(r["prediction_source"], "ensemble")

    def test_no_service_raises(self):
        runtime._service_holder["service"] = None
        with self.assertRaises(RuntimeError):
            runtime.run_inference("x")

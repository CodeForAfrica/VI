"""Phase-4 tests for inference_client (solution-spec 790-802, client side).

Plain unittest, no network - a fake session returns canned responses. Run:
  python -m unittest test_inference_client
"""
import unittest

import requests

from inference_client import (
    DeferredInferenceError, InferenceClient, PermanentInferenceError,
    RetryableInferenceError,
)

def good(request_id="r", **over):
    payload = {
        "request_id": request_id,
        "strategic_intent": "Economic",
        "strategic_intent_confidence": 0.9,
        "tone": "Factual",
        "tone_confidence": 0.8,
        "confidence": 0.9,
        "lang_detect": "en",
        "prediction_source": "model",
        "model_version": "test",
        "processing_time_ms": 12,
    }
    payload.update(over)
    return payload


GOOD = good()


class Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = {} if body is None else body

    def json(self):
        if self._body == "BAD":
            raise ValueError("not json")
        return self._body


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def post(self, url, json, headers, timeout):
        self.calls.append({"url": url, "json": json, "headers": headers,
                           "timeout": timeout})
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


def client(outcomes, **kw):
    kw.setdefault("sleep", lambda s: None)
    return InferenceClient("https://api.example", "k",
                           session=FakeSession(outcomes), **kw)


class InferenceClientTests(unittest.TestCase):
    def test_200_returns_validated_body(self):
        self.assertEqual(client([Resp(200, GOOD)]).infer("r", "t")["strategic_intent"],
                         "Economic")

    def test_permanent_statuses_not_retried(self):
        for code in (400, 413, 422):
            s = FakeSession([Resp(code)])
            c = InferenceClient("https://x", "k", session=s, sleep=lambda x: None)
            with self.assertRaises(PermanentInferenceError) as cm:
                c.infer("r", "t")
            self.assertEqual(cm.exception.code, f"http_{code}")
            self.assertEqual(cm.exception.status_code, code)
            self.assertEqual(len(s.calls), 1)  # no retry

    def test_auth_failures_are_deferred_without_immediate_retry(self):
        for code in (401, 403):
            s = FakeSession([Resp(code)])
            c = InferenceClient("https://x", "k", session=s, sleep=lambda x: None)
            with self.assertRaises(DeferredInferenceError) as cm:
                c.infer("r", "t")
            self.assertEqual(cm.exception.status_code, code)
            self.assertEqual(len(s.calls), 1)

    def test_unexpected_client_status_is_deferred_without_retry(self):
        s = FakeSession([Resp(404)])
        c = InferenceClient("https://x", "k", session=s, sleep=lambda x: None)
        with self.assertRaises(DeferredInferenceError):
            c.infer("r", "t")
        self.assertEqual(len(s.calls), 1)

    def test_retryable_status_exhausts_then_raises(self):
        s = FakeSession([Resp(503), Resp(503), Resp(503)])
        c = InferenceClient("https://x", "k", session=s, sleep=lambda x: None, max_attempts=3)
        with self.assertRaises(RetryableInferenceError):
            c.infer("r", "t")
        self.assertEqual(len(s.calls), 3)

    def test_retry_then_success(self):
        self.assertEqual(client([Resp(429), Resp(200, GOOD)]).infer("r", "t")["tone"],
                         "Factual")

    def test_timeout_is_retryable(self):
        c = client([requests.Timeout("slow"), Resp(200, GOOD)])
        self.assertEqual(c.infer("r", "t")["strategic_intent"], "Economic")

    def test_connection_error_exhausts_to_retryable(self):
        s = FakeSession([requests.ConnectionError("x"), requests.ConnectionError("x")])
        c = InferenceClient("https://x", "k", session=s, sleep=lambda x: None, max_attempts=2)
        with self.assertRaises(RetryableInferenceError):
            c.infer("r", "t")

    def test_dns_error_gets_stable_code(self):
        s = FakeSession([requests.ConnectionError("NameResolutionError: failed to resolve")])
        c = InferenceClient("https://x", "k", session=s, sleep=lambda x: None,
                            max_attempts=1)
        with self.assertRaises(RetryableInferenceError) as cm:
            c.infer("r", "t")
        self.assertEqual(cm.exception.code, "inference_dns_failed")

    def test_out_of_enum_intent_is_permanent(self):
        with self.assertRaises(PermanentInferenceError):
            client([Resp(200, good(strategic_intent="Bogus"))]).infer("r", "t")

    def test_non_json_200_is_permanent(self):
        with self.assertRaises(PermanentInferenceError):
            client([Resp(200, "BAD")]).infer("r", "t")

    def test_neutral_is_valid(self):
        self.assertEqual(
            client([Resp(200, good(strategic_intent="Neutral", confidence=0.1))])
            .infer("r", "t")["strategic_intent"], "Neutral")

    def test_same_request_id_across_retries(self):
        s = FakeSession([Resp(503), Resp(200, good("rid-42"))])
        c = InferenceClient("https://x", "k", session=s, sleep=lambda x: None)
        c.infer("rid-42", "t")
        self.assertEqual([call["json"]["request_id"] for call in s.calls],
                         ["rid-42", "rid-42"])

    def test_on_retry_hook_fires(self):
        seen = []
        client([Resp(503), Resp(200, GOOD)]).infer(
            "r", "t", on_retry=lambda attempt, maximum, error:
            seen.append((attempt, maximum, error.code, error.status_code)))
        self.assertEqual(seen, [(1, 3, "http_503", 503)])

    def test_empty_base_url_rejected(self):
        with self.assertRaises(ValueError):
            InferenceClient("", "k")

    def test_key_only_in_header_not_payload(self):
        s = FakeSession([Resp(200, good("rid", article_id=7))])
        InferenceClient("https://x", "secret-key", session=s, sleep=lambda z: None).infer(
            "rid", "body", article_id=7, target_country="", inferred_actor="France")
        sent = s.calls[0]
        self.assertEqual(sent["headers"]["X-API-Key"], "secret-key")
        self.assertNotIn("secret-key", str(sent["json"]))
        self.assertEqual(sent["json"]["article_id"], 7)
        self.assertNotIn("target_country", sent["json"])   # empty omitted
        self.assertEqual(sent["json"]["inferred_actor"], "France")
        self.assertTrue(sent["url"].endswith("/api/v1/inference"))

    def test_mismatched_request_id_is_rejected(self):
        with self.assertRaises(PermanentInferenceError):
            client([Resp(200, good("different"))]).infer("r", "t")

    def test_missing_response_field_is_rejected(self):
        body = good()
        del body["model_version"]
        with self.assertRaises(PermanentInferenceError):
            client([Resp(200, body)]).infer("r", "t")

    def test_500_is_retried(self):
        s = FakeSession([Resp(500, {"error": {"code": "inference_failed",
                                                "message": "temporary failure"}}),
                         Resp(200, GOOD)])
        c = InferenceClient("https://x", "k", session=s, sleep=lambda x: None)
        self.assertEqual(c.infer("r", "t")["strategic_intent"], "Economic")
        self.assertEqual(len(s.calls), 2)

    def test_model_contract_500_is_permanent_without_retry(self):
        error = {"error": {"code": "invalid_tone",
                            "message": "model returned an invalid tone"}}
        s = FakeSession([Resp(500, error)])
        c = InferenceClient("https://x", "k", session=s, sleep=lambda x: None)
        with self.assertRaises(PermanentInferenceError) as cm:
            c.infer("r", "t")
        self.assertEqual(cm.exception.code, "invalid_tone")
        self.assertEqual(len(s.calls), 1)

    def test_api_error_code_is_preserved(self):
        error = {"error": {"code": "models_not_ready", "message": "loading"}}
        s = FakeSession([Resp(503, error), Resp(503, error), Resp(503, error)])
        c = InferenceClient("https://x", "k", session=s, sleep=lambda x: None)
        with self.assertRaises(RetryableInferenceError) as cm:
            c.infer("r", "t")
        self.assertEqual(cm.exception.code, "models_not_ready")
        self.assertEqual(cm.exception.status_code, 503)
        self.assertEqual(cm.exception.attempts, 3)

    def test_deadline_caps_request_timeout(self):
        import time
        s = FakeSession([Resp(200, GOOD)])
        c = InferenceClient("https://x", "k", timeout=180, session=s,
                            sleep=lambda x: None)
        c.infer("r", "t", deadline=time.time() + 5)
        self.assertLessEqual(s.calls[0]["timeout"], 4)


if __name__ == "__main__":
    unittest.main()

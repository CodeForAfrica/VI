"""Phase-4 tests for inference_client (solution-spec 790-802, client side).

Plain unittest, no network - a fake session returns canned responses. Run:
  python -m unittest test_inference_client
"""
import unittest

import requests

from inference_client import (
    InferenceClient, PermanentInferenceError, RetryableInferenceError,
)

GOOD = {"strategic_intent": "Economic", "confidence": 0.9, "tone": "Factual"}


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
        self.calls.append({"url": url, "json": json, "headers": headers})
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
        for code in (400, 401, 403, 413):
            s = FakeSession([Resp(code)])
            c = InferenceClient("https://x", "k", session=s, sleep=lambda x: None)
            with self.assertRaises(PermanentInferenceError) as cm:
                c.infer("r", "t")
            self.assertEqual(cm.exception.code, code)
            self.assertEqual(len(s.calls), 1)  # no retry

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

    def test_out_of_enum_intent_is_permanent(self):
        with self.assertRaises(PermanentInferenceError):
            client([Resp(200, {"strategic_intent": "Bogus"})]).infer("r", "t")

    def test_non_json_200_is_permanent(self):
        with self.assertRaises(PermanentInferenceError):
            client([Resp(200, "BAD")]).infer("r", "t")

    def test_neutral_is_valid(self):
        self.assertEqual(
            client([Resp(200, {"strategic_intent": "Neutral", "confidence": 0.1})])
            .infer("r", "t")["strategic_intent"], "Neutral")

    def test_same_request_id_across_retries(self):
        s = FakeSession([Resp(503), Resp(200, GOOD)])
        c = InferenceClient("https://x", "k", session=s, sleep=lambda x: None)
        c.infer("rid-42", "t")
        self.assertEqual([call["json"]["request_id"] for call in s.calls],
                         ["rid-42", "rid-42"])

    def test_on_retry_hook_fires(self):
        seen = []
        client([Resp(503), Resp(200, GOOD)]).infer(
            "r", "t", on_retry=lambda attempt, code: seen.append((attempt, code)))
        self.assertEqual(seen, [(1, 503)])

    def test_empty_base_url_rejected(self):
        with self.assertRaises(ValueError):
            InferenceClient("", "k")

    def test_key_only_in_header_not_payload(self):
        s = FakeSession([Resp(200, GOOD)])
        InferenceClient("https://x", "secret-key", session=s, sleep=lambda z: None).infer(
            "rid", "body", article_id=7, target_country="", inferred_actor="France")
        sent = s.calls[0]
        self.assertEqual(sent["headers"]["X-API-Key"], "secret-key")
        self.assertNotIn("secret-key", str(sent["json"]))
        self.assertEqual(sent["json"]["article_id"], 7)
        self.assertNotIn("target_country", sent["json"])   # empty omitted
        self.assertEqual(sent["json"]["inferred_actor"], "France")
        self.assertTrue(sent["url"].endswith("/api/v1/inference"))


if __name__ == "__main__":
    unittest.main()

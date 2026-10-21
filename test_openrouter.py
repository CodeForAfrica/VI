"""Dashboard provider contract tests; all HTTP responses are controlled."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests

from dashboard.openrouter import (
    ENDPOINT, OpenRouterClient, OpenRouterError, OpenRouterAuthenticationError,
    OpenRouterRateLimitError, OpenRouterTimeout,
)


class OpenRouterContractTests(unittest.TestCase):
    def response(self, status=200, body=None):
        return SimpleNamespace(status_code=status, json=lambda: body or {
            "choices": [{"message": {"content": "Synthetic answer"}}],
        })

    @patch("dashboard.openrouter.requests.post")
    def test_request_uses_openrouter_authentication_and_model(self, post):
        post.return_value = self.response()
        result = OpenRouterClient("test-not-a-secret").chat.completions.create(
            model="meta-llama/llama-4-scout", messages=[{"role": "user", "content": "Synthetic"}],
            max_completion_tokens=40, response_format={"type": "json_object"},
        )
        self.assertEqual(result.choices[0].message.content, "Synthetic answer")
        args, kwargs = post.call_args
        self.assertEqual(args, (ENDPOINT,))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-not-a-secret")
        self.assertEqual(kwargs["json"]["model"], "meta-llama/llama-4-scout")
        self.assertEqual(kwargs["json"]["max_tokens"], 40)
        self.assertNotIn("max_completion_tokens", kwargs["json"])
        self.assertEqual(kwargs["json"]["response_format"]["type"], "json_schema")
        self.assertEqual(kwargs["json"]["response_format"]["json_schema"]["schema"],
                         {"type": "object", "additionalProperties": True})
        self.assertEqual(kwargs["timeout"], 20.0)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertNotIn("verify", kwargs)

    @patch("dashboard.openrouter.requests.post")
    def test_missing_key_does_not_send_request(self, post):
        with self.assertRaises(OpenRouterAuthenticationError):
            OpenRouterClient().create(model="test")
        post.assert_not_called()

    @patch("dashboard.openrouter.requests.post")
    def test_errors_never_expose_provider_body_or_key(self, post):
        for status, error in ((401, OpenRouterAuthenticationError), (403, OpenRouterAuthenticationError),
                              (429, OpenRouterRateLimitError), (500, OpenRouterError), (302, OpenRouterError)):
            post.return_value = self.response(status, {"error": "private-provider-body"})
            with self.subTest(status=status), self.assertLogs("dashboard.openrouter") as logs:
                with self.assertRaises(error) as caught:
                    OpenRouterClient("private-key").create(model="test")
            output = str(caught.exception) + " ".join(logs.output)
            self.assertNotIn("private-key", output)
            self.assertNotIn("private-provider-body", output)

    @patch("dashboard.openrouter.requests.post")
    def test_network_errors_are_safe_and_explicit(self, post):
        for failure, expected in ((requests.Timeout("private"), OpenRouterTimeout),
                                  (requests.ConnectionError("private"), OpenRouterError)):
            post.side_effect = failure
            with self.assertRaises(expected) as caught:
                OpenRouterClient("private-key").create(model="test")
            self.assertNotIn("private", str(caught.exception))

    @patch("dashboard.openrouter.requests.post")
    def test_empty_malformed_and_embedded_errors_are_not_success(self, post):
        for body in ({"choices": []}, {"error": {"code": 502}}, {"choices": [{"message": {"content": " "}}]}, []):
            post.return_value = SimpleNamespace(status_code=200, json=lambda: body)
            with self.subTest(body=body), self.assertRaises(OpenRouterError):
                OpenRouterClient("test-key").create(model="test")
        post.return_value.json = lambda: (_ for _ in ()).throw(ValueError("private-body"))
        with self.assertRaises(OpenRouterError):
            OpenRouterClient("test-key").create(model="test")

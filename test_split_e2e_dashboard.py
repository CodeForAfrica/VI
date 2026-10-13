import importlib
import os
import unittest
from unittest import mock

with mock.patch.dict(os.environ, {"VI_INFERENCE_API_URL": "https://inference.example"}):
    verifier = importlib.import_module("scripts.run_split_e2e")


def response(text="<html>Page</html>", status=200, body=None):
    result = mock.Mock(status_code=status, text=text, headers={"Content-Type": "text/html"})
    result.json.return_value = body
    return result


class DashboardE2EVerifierTests(unittest.TestCase):
    def setUp(self):
        self.fixture = [{"id": 1, "url": "https://example.org/article"}]
        self.predictions = [{"tone": "Factual", "strategic_intent": "Economic"}]
        self.article_html = ('<div class="row"><h5>Sample title</h5>'
                             '<p class="text-secondary">Sample summary</p>'
                             '<span>Tone: Factual</span><span>Intent: Economic</span>'
                             '<a href="https://example.org/article">Read full article</a></div>')
        self.session = mock.MagicMock()
        self.session.__enter__.return_value = self.session
        self.session.get.side_effect = lambda url, **kwargs: response(
            self.article_html if url.endswith("/articles/") else
            '<input name="csrfmiddlewaretoken" value="local-test-token">'
        )
        self.session.post.side_effect = [response(status=403), response(body={
            "success": True, "reply": "We've identified 1 main strategic narratives across 1 articles"})]

    def verify(self):
        with mock.patch.dict(os.environ, {"VI_E2E_DASHBOARD_URL": "http://dashboard:8000"}), \
                mock.patch.object(verifier.requests, "Session", return_value=self.session):
            return verifier.verify_dashboard(self.fixture, self.predictions)

    def test_checks_real_pages_saved_results_and_csrf(self):
        result = self.verify()
        self.assertEqual(len(result["pages"]), 10)
        self.assertEqual(result["articles_verified"], 1)
        self.assertTrue(result["csrf_chat_verified"])
        self.assertFalse(self.session.trust_env)
        self.assertEqual(self.session.post.call_args.kwargs["headers"]["X-CSRFToken"], "local-test-token")

    def test_route_failure_is_explicit(self):
        self.session.get.side_effect = lambda *args, **kwargs: response(status=503)
        with self.assertRaisesRegex(RuntimeError, "dashboard GET / failed: HTTP 503"):
            self.verify()

    def test_missing_article_or_blank_title_fails(self):
        for html, error in (("<html></html>", "saved URL is missing"),
                            (self.article_html.replace("Sample title", ""), "title or summary is blank")):
            with self.subTest(error=error):
                self.session.get.side_effect = lambda *args, **kwargs: response(html)
                with self.assertRaisesRegex(RuntimeError, error):
                    self.verify()

    def test_prediction_mismatch_fails(self):
        self.predictions[0]["strategic_intent"] = "Sovereignty"
        with self.assertRaisesRegex(RuntimeError, "saved prediction not displayed"):
            self.verify()

    def test_missing_csrf_protection_fails(self):
        self.session.post.side_effect = [response(status=200)]
        with self.assertRaisesRegex(RuntimeError, "missing CSRF token must return 403"):
            self.verify()

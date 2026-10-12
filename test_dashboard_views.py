"""Local dashboard regression tests; no production DB or external API is used."""
from datetime import timedelta
from io import BytesIO
import importlib
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import RequestFactory, TestCase, override_settings
from django.core.cache import cache
from django.utils import timezone
from pypdf import PdfReader
from pypdf.generic import ContentStream


@override_settings(
    GROQ_API_KEY="test-only-not-a-credential",
    TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True}],
)
class DashboardViewTests(TestCase):
    def setUp(self):
        self.views = importlib.import_module("dashboard.views")
        cache.clear()

    def test_chatbot_methods_are_members_of_the_class(self):
        for method in ("process_query", "get_context_from_db", "get_insights_from_ai",
                       "get_actor_stats", "get_intent_breakdown", "get_tone_breakdown", "compare_actors"):
            with self.subTest(method=method):
                self.assertTrue(callable(getattr(self.views.DisinfoAnalysisChatbot, method, None)))

    def test_ai_response_removes_reasoning_and_rejects_missing_answers(self):
        for content in ("<think>Private reasoning.</think>Final answer.",
                        "<THINK>Reasoning\nacross lines.</THINK> Final answer. ",
                        "<think>First.</think><think>Second.</think>Final answer.",
                        "Final answer."):
            with self.subTest(content=content):
                self.assertEqual(self.views.clean_ai_response(content), "Final answer.")
        for content in (None, "", "<think>Reasoning only</think>", "<think>Truncated reasoning"):
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.views.clean_ai_response(content)

    @override_settings(ROOT_URLCONF="dashboard.urls")
    def test_dashboard_pages_with_empty_and_populated_database(self):
        from dashboard.models import Journalist, MediaNarrative, MediaOutlet, VulnerabilityIndex
        scenarios = [
            ("/", {}), ("/vulnerability-index/", {}), ("/dashboard/", {}), ("/vi/", {}),
            ("/", {"calc_target_country": "Ethiopia", "calc_foreign_actor": "China", "calc_strategic_intent": "Economic"}),
            ("/authors/", {}), ("/authors/", {"journalist": "Sample journalist", "search": "Sample"}),
            ("/outlets/", {}), ("/outlets/", {"outlet": "Sample outlet"}),
            ("/countries/", {}), ("/countries/", {"country": "Ethiopia"}),
            ("/articles/", {}), ("/articles/", {"q": "cooperation"}),
            ("/articles/", {"q": "absent"}), ("/articles/", {"page": "invalid"}),
            ("/generate-report/", {}), ("/chat/", {}),
        ]
        for populated in (False, True):
            if populated:
                author = Journalist.objects.create(name="Sample journalist", country_based="Ethiopia")
                outlet = MediaOutlet.objects.create(name="Sample outlet", country="Ethiopia")
                VulnerabilityIndex.objects.create(actor="China", country="Ethiopia", intent="Economic", final_risk=0.5)
                for index in range(25):
                    MediaNarrative.objects.create(
                        article_text=f"Sample article {index}\nTrade cooperation news.",
                        target_country="Ethiopia", inferred_actor="China", strategic_intent="Economic",
                        tone="Factual", media_outlet=outlet.name, author=author.name,
                        media_outlet_fk=outlet, journalist_fk=author,
                        posting_time=timezone.now()-timedelta(days=index), confidence=0.8,
                    )
            cache.clear()
            with mock.patch.object(self.views, "translate_to_english", side_effect=lambda text: text), mock.patch.object(MediaNarrative, "get_article_image", return_value=None):
                for path, params in scenarios:
                    with self.subTest(populated=populated, path=path, params=params):
                        response = self.client.get(path, params)
                        self.assertEqual(response.status_code, 200)
                        self.assertTrue(response.content)
                response = self.client.get("/articles/", {"page": "2"})
                self.assertEqual(response.context["page_obj"].number, 2 if populated else 1)
                response = self.client.get("/articles/", {"q": "absent"})
                self.assertEqual(response.context["page_obj"].paginator.count, 0)

    @override_settings(ROOT_URLCONF="dashboard.urls")
    def test_unused_incomplete_routes_are_not_exposed(self):
        from django.urls import NoReverseMatch, reverse
        for path, name in (("/intents/", "intents"), ("/all-articles/", "all_articles")):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
                with self.assertRaises(NoReverseMatch):
                    reverse(name)

    @override_settings(ROOT_URLCONF="dashboard.urls")
    def test_chat_requests_and_local_cache_clear(self):
        with mock.patch.object(self.views.chatbot_instance.client.chat.completions, "create") as completion:
            completion.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="<think>Private reasoning.</think>Sample assistant reply"))])
            for query in ("hello", "China", "compare China and Russia", "tone breakdown", "economic intent"):
                with self.subTest(query=query):
                    response = self.client.post("/chat/", {"message": query}, content_type="application/json")
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.json()["success"])
                    self.assertTrue(response.json()["reply"])
                    self.assertNotIn("Private reasoning", response.json()["reply"])
                    self.assertNotIn("<think>", response.json()["reply"])
            response = self.client.get("/chat/", {"q": "hello"})
            self.assertEqual(response.status_code, 200)
            response = self.client.post("/chat/", "invalid JSON", content_type="application/json")
            self.assertFalse(response.json()["success"])
        cache.set("local-dashboard-test", "value")
        self.assertEqual(self.client.get("/clear-cache/").status_code, 200)
        self.assertIsNone(cache.get("local-dashboard-test"))

    @override_settings(ROOT_URLCONF="dashboard.urls")
    def test_report_handles_empty_results_without_calling_ai(self):
        with mock.patch("groq.Groq") as client:
            for params in ({"country": "Ethiopia", "actors": "China"}, {"country": "Unknown", "actors": "Unknown"}):
                with self.subTest(params=params):
                    response = self.client.get("/generate-report/", params)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response["Content-Type"], "application/pdf")
                    self.assertTrue(PdfReader(BytesIO(response.content)).pages)
            client.return_value.chat.completions.create.assert_not_called()

    def test_translation_success_and_failure_preserve_display_contract(self):
        with mock.patch.object(self.views.requests, "get") as request:
            request.return_value.status_code = 200
            request.return_value.text = '<div class="t0">English title</div>'
            self.assertEqual(self.views.translate_to_english("Titre français"), "English title")
            self.assertEqual(request.call_args.kwargs["timeout"], (1, 1))
            self.assertFalse(request.call_args.kwargs["allow_redirects"])
            self.assertEqual(self.views.translate_to_english("Titre français"), "English title")
            request.assert_called_once()
            request.side_effect = self.views.requests.Timeout("test provider failure")
            with self.assertLogs(self.views.logger.name, level="WARNING"):
                self.assertEqual(self.views.translate_to_english("Autre titre"), "Autre titre")
            self.assertIsNone(self.views.translate_to_english(None))

    def test_translation_handles_provider_and_cache_failures(self):
        with mock.patch.object(self.views.requests, "get") as request:
            for status, body in ((429, ""), (200, "<html>No translation</html>")):
                with self.subTest(status=status, body=body):
                    request.return_value.status_code = status
                    request.return_value.text = body
                    with self.assertLogs(self.views.logger.name, level="WARNING"):
                        self.assertEqual(self.views.translate_to_english("Texte"), "Texte")
            request.return_value.status_code = 200
            request.return_value.text = '<div class="result-container">English text</div>'
            with mock.patch.object(cache, "get", side_effect=RuntimeError("cache unavailable")):
                with mock.patch.object(cache, "set", side_effect=RuntimeError("cache unavailable")):
                    with self.assertLogs(self.views.logger.name, level="WARNING"):
                        self.assertEqual(self.views.translate_to_english("Texte"), "English text")

    @override_settings(ROOT_URLCONF="dashboard.urls")
    def test_overview_displays_translations_without_rewriting_article(self):
        from dashboard.models import MediaNarrative
        original = "Titre français\nTexte original de l'article."
        article = MediaNarrative.objects.create(
            article_text=original, target_country="Ethiopia", inferred_actor="China",
            strategic_intent="Economic", tone="Factual", media_outlet="Sample outlet",
            posting_time=timezone.now(), url="https://example.org/sample-article",
        )
        with mock.patch.object(self.views, "translate_to_english", side_effect=lambda text: "English: " + text) as translate:
            response = self.views.overview(RequestFactory().get("/"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "English: Titre français")
        self.assertEqual(translate.call_count, 2)
        article.refresh_from_db()
        self.assertEqual(article.article_text, original)

    def test_report_renders_both_charts_into_the_actual_pdf(self):
        from dashboard.models import MediaNarrative
        now = timezone.now()
        for index, intent in enumerate(("Economic", "Economic", "Sovereignty", "SocialFragility", None)):
            MediaNarrative.objects.create(
                article_text="Sample news article about trade and cooperation between China and Ethiopia.",
                target_country="Ethiopia", inferred_actor="China", strategic_intent=intent,
                tone="Factual", media_outlet="Sample outlet", posting_time=now-timedelta(days=index),
                url="https://example.org/sample-article",
            )
        def completion(content):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
        long_analysis = "Trade cooperation supports economic development and regional road links. " * 40 + "END OF ANALYSIS"
        with mock.patch("groq.Groq") as client:
            client.return_value.chat.completions.create.side_effect = [
                completion("<think>Private reasoning.</think>Sample summary.") for _ in range(4)
            ] + [completion("<think>Private reasoning.</think>" + long_analysis)]
            response = self.views.generate_report(RequestFactory().get(
                "/generate-report/", {"country": "Ethiopia", "actors": "China"}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        pdf = PdfReader(BytesIO(response.content))
        if os.environ.get("VI_REPORT_TEST_OUTPUT"):
            Path(os.environ["VI_REPORT_TEST_OUTPUT"]).write_bytes(response.content)
        self.assertGreaterEqual(sum(len(page.images) for page in pdf.pages), 2)
        self.assertGreaterEqual(len({image.data for page in pdf.pages for image in page.images}), 2)
        drawn_images = 0
        for page in pdf.pages:
            transform = None
            for operands, operator in ContentStream(page.get_contents(), pdf).operations:
                if operator == b"cm":
                    transform = operands
                elif operator == b"Do":
                    self.assertIsNotNone(transform)
                    self.assertGreater(abs(float(transform[0]) * float(transform[3]) - float(transform[1]) * float(transform[2])), 0)
                    drawn_images += 1
        self.assertGreaterEqual(drawn_images, 2)
        text = "\n".join(page.extract_text() for page in pdf.pages)
        self.assertIn("Narrative Volume Over Time", text)
        self.assertIn("Factor Chart", text)
        self.assertNotIn("Private reasoning", text)
        self.assertNotIn("<think>", text)
        self.assertIn("END OF ANALYSIS", text)
        self.assertGreater(sum("Trade cooperation" in line for line in text.splitlines()), 1)
        self.assertEqual(client.return_value.chat.completions.create.call_count, 5)

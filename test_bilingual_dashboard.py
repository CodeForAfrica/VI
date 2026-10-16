"""Bilingual UI regressions against an isolated database and local catalogues."""
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from bs4 import BeautifulSoup
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone, translation

from dashboard.models import MediaNarrative


@override_settings(
    ROOT_URLCONF="dashboard.urls",
    GROQ_API_KEY="test-only-not-a-credential",
    LANGUAGE_CODE="en",
    LANGUAGES=[("en", "English"), ("fr", "Français")],
    LOCALE_PATHS=[Path(__file__).parent / "locale"],
    MIDDLEWARE=[
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.locale.LocaleMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django.contrib.messages.middleware.MessageMiddleware",
    ],
    TEMPLATES=[{
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }],
)
class BilingualDashboardTests(TestCase):
    def setUp(self):
        self.views = importlib.import_module("dashboard.views")
        cache.clear()
        self.article = MediaNarrative.objects.create(
            article_text="Trade cooperation between Ethiopia and China supports economic development.",
            target_country="Ethiopia", inferred_actor="China", strategic_intent="Economic",
            tone="Factual", confidence=0.8, posting_time=timezone.now(),
            url="https://example.org/bilingual-test", media_outlet="Sample outlet",
        )
        provider = mock.patch.object(self.views, "translate_to_english", side_effect=lambda text: text)
        provider.start()
        self.addCleanup(provider.stop)
        images = mock.patch.object(MediaNarrative, "get_article_image", return_value=None)
        images.start()
        self.addCleanup(images.stop)
        self.addCleanup(translation.deactivate_all)

    def client_for(self, language, **kwargs):
        client = Client(**kwargs)
        client.cookies["django_language"] = language
        return client

    def test_all_dashboard_routes_render_in_both_languages(self):
        for populated in (True, False):
            if not populated:
                MediaNarrative.objects.all().delete()
                cache.clear()
            for language in ("en", "fr"):
                client = self.client_for(language)
                for path in ("/", "/vulnerability-index/", "/dashboard/", "/vi/", "/authors/",
                             "/outlets/", "/countries/", "/articles/", "/generate-report/", "/chat/"):
                    with self.subTest(language=language, path=path, populated=populated):
                        response = client.get(path)
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response["Content-Language"], language)
                        self.assertTrue(response.content)

    def test_translated_labels_keep_canonical_option_values(self):
        for language, country_label, actor_label, intent_label in (
            ("en", "Ethiopia", "China", "Economic"),
            ("fr", "Éthiopie", "Chine", "Économique"),
        ):
            client = self.client_for(language)
            for path, country_name, actor_name in (("/", "calc_target_country", "calc_foreign_actor"),
                                                    ("/generate-report/", "country", "actors"),
                                                    ("/countries/", "country", None)):
                with self.subTest(language=language, path=path):
                    response = client.get(path)
                    soup = BeautifulSoup(response.content, "html.parser")
                    countries = soup.find("select", attrs={"name": country_name})
                    self.assertEqual(countries.find("option", value="Ethiopia").get_text(strip=True), country_label)
                    self.assertTrue(all("(" not in option.get("value", "") for option in countries.find_all("option")))
                    if actor_name:
                        actors = soup.find("select", attrs={"name": actor_name})
                        self.assertEqual(actors.find("option", value="China").get_text(strip=True), actor_label)
                    if path == "/":
                        intents = soup.find("select", attrs={"name": "calc_strategic_intent"})
                        self.assertEqual(intents.find("option", value="Economic").get_text(strip=True), intent_label)
                        self.assertEqual({option["value"] for option in intents.find_all("option") if option.get("value")},
                                         {"Economic", "Sovereignty", "LGBTQ", "Religious", "MilitaryPresence",
                                          "ResourceDependency", "SocialFragility", "ElectionInfluence"})

    def test_filter_results_do_not_change_with_language_or_rewrite_data(self):
        for language in ("en", "fr"):
            response = self.client_for(language).get("/", {
                "calc_target_country": "Ethiopia", "calc_foreign_actor": "China",
                "calc_strategic_intent": "Economic",
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["total_articles"], 1)
            self.assertEqual(response.context["selected_country"], "Ethiopia")
            self.assertEqual(response.context["selected_actor"], "China")
            self.assertEqual(response.context["selected_intent"], "Economic")
            soup = BeautifulSoup(response.content, "html.parser")
            self.assertIsNotNone(soup.select_one('select[name="calc_target_country"] option[value="Ethiopia"][selected]'))
        self.article.refresh_from_db()
        self.assertEqual(self.article.strategic_intent, "Economic")
        self.assertEqual(self.article.target_country, "Ethiopia")
        self.assertEqual(self.article.inferred_actor, "China")

    def test_language_switch_uses_csrf_and_preserves_filter_url(self):
        client = self.client_for("en", enforce_csrf_checks=True)
        next_url = "/?calc_target_country=Ethiopia&calc_foreign_actor=China&calc_strategic_intent=Economic"
        for language, home in (("fr", "Accueil"), ("en", "Home")):
            page = client.get(next_url)
            token = BeautifulSoup(page.content, "html.parser").find("input", attrs={"name": "csrfmiddlewaretoken"})["value"]
            self.assertEqual(client.post("/i18n/setlang/", {"language": language, "next": next_url}).status_code, 403)
            response = client.post("/i18n/setlang/", {"language": language, "next": next_url,
                                                       "csrfmiddlewaretoken": token})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response["Location"], next_url)
            self.assertEqual(response.cookies["django_language"].value, language)
            page = client.get(next_url)
            soup = BeautifulSoup(page.content, "html.parser")
            self.assertEqual(soup.html["lang"], language)
            self.assertEqual(soup.select_one('select[name="language"] option[selected]')["value"], language)
            self.assertIn(home, [link.get_text(strip=True) for link in soup.select("nav a")])
            self.assertEqual(page.context["total_articles"], 1)

    def test_accept_language_and_cookie_precedence(self):
        client = Client()
        page = client.get("/", HTTP_ACCEPT_LANGUAGE="fr,en;q=0.5")
        self.assertEqual(page["Content-Language"], "fr")
        self.assertIn(b'lang="fr"', page.content)
        client.cookies["django_language"] = "en"
        page = client.get("/", HTTP_ACCEPT_LANGUAGE="fr")
        self.assertEqual(page["Content-Language"], "en")

    def test_language_switch_does_not_redirect_to_another_site(self):
        response = self.client_for("en").post("/i18n/setlang/", {
            "language": "fr", "next": "https://untrusted.example/",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")

    def test_french_methodology_and_secondary_pages_do_not_fall_back_to_english(self):
        client = self.client_for("fr")
        page = client.get("/", {"calc_target_country": "Ethiopia", "calc_foreign_actor": "China"})
        self.assertContains(page, "Mesure l’intensité des récits stratégiques")
        self.assertContains(page, "Représente la sensibilité structurelle")
        self.assertContains(page, "Interprétation :")
        for path, french in (("/generate-report/", "Générer un rapport CVI"),
                             ("/authors/", "Annuaire des journalistes"),
                             ("/countries/", "Tous les pays"),
                             ("/outlets/", "Tous les médias"),
                             ("/chat/", "Saisissez votre message"),
                             ("/articles/", "Lire l’article complet")):
            with self.subTest(path=path):
                self.assertContains(client.get(path), french)

    def test_chat_context_uses_database_keys_in_both_languages(self):
        for language in ("en", "fr"):
            with self.subTest(language=language), translation.override(language):
                context = self.views.chatbot_instance.get_context_from_db("Ethiopia China economic")
                self.assertIn("FILTERED ARTICLE COUNT: 1.", context)

    def test_report_accepts_canonical_choices_in_both_languages(self):
        completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Sample report analysis."))])
        for language in ("en", "fr"):
            with self.subTest(language=language), mock.patch("groq.Groq") as provider:
                provider.return_value.chat.completions.create.return_value = completion
                response = self.client_for(language).get("/generate-report/", {"country": "Ethiopia", "actors": "China"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "application/pdf")
                self.assertTrue(response.content.startswith(b"%PDF"))
                provider.return_value.chat.completions.create.assert_called()

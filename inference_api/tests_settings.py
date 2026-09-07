"""Lightweight settings for running the inference-API test suite.

Deliberately minimal so the suite runs with only Django + requests +
beautifulsoup4 installed (no torch, whitenoise, redis, or DB server needed).
The dashboard app is included because the response-mapping tests exercise the
real dashboard.utils.map_to_canonical_intent.

Run:  python manage.py test inference_api --settings=inference_api.tests_settings
"""
SECRET_KEY = "test-only-not-a-secret"
DEBUG = True
ALLOWED_HOSTS = ["*"]
ROOT_URLCONF = "inference_api.urls"
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "dashboard",
]
MIDDLEWARE = []
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

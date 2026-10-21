"""Local HTTP checks with independently selected controlled or live providers."""
import json
import os
from pathlib import Path
from unittest.mock import patch

from config.local_test_providers import (
    ControlledOpenRouter, ControlledTranslate, ProviderTracker, TrackedOpenRouter, TrackedTranslate,
)

os.environ["DJANGO_SETTINGS_MODULE"] = "config.local_test_settings"
aws_live = os.environ.get("VI_LOCAL_AWS_LIVE") == "1"
openrouter_live = os.environ.get("VI_LOCAL_OPENROUTER_LIVE") == "1"
if aws_live:
    credentials = json.loads(Path("/run/secrets/local_aws_credentials").read_text())
    for name, field in (("AWS_ACCESS_KEY_ID", "AccessKeyId"), ("AWS_SECRET_ACCESS_KEY", "SecretAccessKey")):
        if not credentials.get(field):
            raise RuntimeError("AWS profile export is missing required credentials; rerun with a working AWS_PROFILE")
        os.environ[name] = credentials[field]
    if credentials.get("SessionToken"):
        os.environ["AWS_SESSION_TOKEN"] = credentials["SessionToken"]
if openrouter_live:
    key = Path("/run/secrets/local_openrouter_key").read_text().strip()
    if not key:
        raise RuntimeError("Live OpenRouter selected but OPENROUTER_API_KEY is empty")
    os.environ["OPENROUTER_API_KEY"] = key

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()

from dashboard import views
from config.local_test_urls import provider_tracker
from dashboard import openrouter


tracker = ProviderTracker(aws_live, openrouter_live)
provider_tracker.append(tracker)
original_translate = views.get_translate_client
original_openrouter = openrouter.OpenRouterClient


def translate_client(region):
    delegate = original_translate(region) if aws_live else ControlledTranslate()
    return TrackedTranslate(delegate, tracker)


def openrouter_client(**kwargs):
    if openrouter_live:
        kwargs.update(timeout=20.0)
        delegate = original_openrouter(**kwargs)
    else:
        delegate = ControlledOpenRouter()
    return TrackedOpenRouter(delegate, tracker, views.clean_ai_response)


# Only this explicitly selected WSGI module installs test instrumentation.
# Production config.wsgi never imports it or enables these patches.
patch.object(views, "get_translate_client", side_effect=translate_client).start()
patch("dashboard.views.OpenRouterClient", side_effect=openrouter_client).start()
patch.object(views.chatbot_instance, "client", openrouter_client(api_key=key if openrouter_live else "local-test-only")).start()
if not openrouter_live:
    patch.object(views.settings, "OPENROUTER_API_KEY", "local-test-not-a-credential").start()
print(f"[run-local-dashboard] AWS Translate={'LIVE' if aws_live else 'CONTROLLED'}; OpenRouter={'LIVE' if openrouter_live else 'CONTROLLED'}; synthetic data only.", flush=True)

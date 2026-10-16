"""Local HTTP checks with independently selected controlled or live providers."""
import json
import os
from pathlib import Path
from unittest.mock import patch

from config.local_test_providers import (
    ControlledGroq, ControlledTranslate, ProviderTracker, TrackedGroq, TrackedTranslate,
)

os.environ["DJANGO_SETTINGS_MODULE"] = "config.local_test_settings"
aws_live = os.environ.get("VI_LOCAL_AWS_LIVE") == "1"
groq_live = os.environ.get("VI_LOCAL_GROQ_LIVE") == "1"
if aws_live:
    credentials = json.loads(Path("/run/secrets/local_aws_credentials").read_text())
    for name, field in (("AWS_ACCESS_KEY_ID", "AccessKeyId"), ("AWS_SECRET_ACCESS_KEY", "SecretAccessKey")):
        if not credentials.get(field):
            raise RuntimeError("AWS profile export is missing required credentials; rerun with a working AWS_PROFILE")
        os.environ[name] = credentials[field]
    if credentials.get("SessionToken"):
        os.environ["AWS_SESSION_TOKEN"] = credentials["SessionToken"]
if groq_live:
    key = Path("/run/secrets/local_groq_key").read_text().strip()
    if not key:
        raise RuntimeError("Live Groq selected but GROQ_API_KEY is empty")
    os.environ["GROQ_API_KEY"] = key

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()

from dashboard import views
from config.local_test_urls import provider_tracker
import groq


tracker = ProviderTracker(aws_live, groq_live)
provider_tracker.append(tracker)
original_translate = views.get_translate_client
original_groq = groq.Groq


def translate_client(region):
    delegate = original_translate(region) if aws_live else ControlledTranslate()
    return TrackedTranslate(delegate, tracker)


def groq_client(**kwargs):
    if groq_live:
        kwargs.update(timeout=20.0, max_retries=0)
        delegate = original_groq(**kwargs)
    else:
        delegate = ControlledGroq()
    return TrackedGroq(delegate, tracker, views.clean_ai_response)


# Only this explicitly selected WSGI module installs test instrumentation.
# Production config.wsgi never imports it or enables these patches.
patch.object(views, "get_translate_client", side_effect=translate_client).start()
patch("groq.Groq", side_effect=groq_client).start()
patch.object(views.chatbot_instance, "client", groq_client(api_key=key if groq_live else "local-test-only")).start()
if not groq_live:
    patch.object(views.settings, "GROQ_API_KEY", "local-test-not-a-credential").start()
print(f"[run-local-dashboard] AWS Translate={'LIVE' if aws_live else 'CONTROLLED'}; Groq={'LIVE' if groq_live else 'CONTROLLED'}; synthetic data only.", flush=True)

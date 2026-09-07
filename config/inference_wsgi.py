"""WSGI entrypoint for the inference API process.

Boots Django with the small inference urlconf instead of the dashboard's, and
kicks the background model warmup so /healthz answers immediately while models
load and /readyz flips to ready once they are usable.

Run with:  gunicorn --workers 1 --threads 2 --timeout 180 \
             --bind 0.0.0.0:8000 config.inference_wsgi:application
"""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
# settings.ROOT_URLCONF reads this env var (see config/settings.py).
os.environ["ROOT_URLCONF"] = "inference_api.urls"

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()

from inference_api.runtime import start_warmup  # noqa: E402

start_warmup()

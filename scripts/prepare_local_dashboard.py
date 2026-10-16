"""Populate only the disposable make run-local-dashboard database."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.local_test_settings")
import django
django.setup()

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone
from dashboard.models import MediaNarrative

if settings.SETTINGS_MODULE != "config.local_test_settings" or settings.DATABASES["default"]["NAME"] != "vi_local_test":
    raise RuntimeError("Refusing fixture setup outside the isolated local-test database")
print("[run-local-dashboard] Migrating disposable database.", flush=True)
call_command("migrate", interactive=False, verbosity=0)
print("[run-local-dashboard] Loading 25 synthetic, already-classified articles for filters/pagination/PDF checks.", flush=True)
for index in range(25):
    text = f"Local sample article {index}\nTrade cooperation between Ethiopia and China supports economic development."
    if index == 24:
        text = "Bonjour, voici les nouvelles économiques.\nLa coopération entre l’Éthiopie et la Chine soutient le développement économique."
    MediaNarrative.objects.create(
        article_text=text,
        target_country="Ethiopia", inferred_actor="China", strategic_intent="Economic",
        tone="Factual", confidence=0.8, posting_time=timezone.now(), ml_processed_at=timezone.now(),
        url=f"https://example.org/local-test-{index}", media_outlet="Sample outlet", author="Sample journalist",
    )
print("[run-local-dashboard] OK: schema and synthetic article results ready.", flush=True)

"""Store per-intent probabilities for articles classified before they were saved.

Calls the inference API for the selected articles and writes ONLY the
`intent_probs` column: intent, tone, confidence and processed time are left
untouched, and no LLM call is made.

    # Calibration anchors first (needed by fit_intent_calibrator):
    python manage.py backfill_intent_probs --calib-anchors --limit 500

    # Then all remaining articles, in batches:
    python manage.py backfill_intent_probs --limit 2000

Needs VI_INFERENCE_API_URL and VI_INFERENCE_API_KEY, like the Lambda.
"""
import os
import uuid

from django.core.management.base import BaseCommand, CommandError

from dashboard.models import MediaNarrative


class Command(BaseCommand):
    help = "Fetch and store per-intent probabilities (intent_probs) without changing any other field."

    def add_arguments(self, parser):
        parser.add_argument("--calib-anchors", action="store_true",
                            help="Only labelled calibration anchors.")
        parser.add_argument("--limit", type=int, default=500)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, calib_anchors, limit, dry_run, **options):
        qs = MediaNarrative.objects.filter(intent_probs__isnull=True).exclude(article_text__isnull=True).exclude(article_text="")
        if calib_anchors:
            qs = qs.filter(is_anchor=True, anchor_split="calib", true_label__isnull=False)
        total = qs.count()
        self.stdout.write(f"Articles without stored probabilities: {total}; this run: {min(limit, total)}")
        if dry_run or total == 0:
            return

        base_url = os.environ.get("VI_INFERENCE_API_URL")
        api_key = os.environ.get("VI_INFERENCE_API_KEY")
        if not base_url or not api_key:
            raise CommandError("Set VI_INFERENCE_API_URL and VI_INFERENCE_API_KEY.")
        from inference_client import InferenceClient
        from dashboard.services.remote_inference_service import RemoteInferenceService
        client = InferenceClient(base_url, api_key)
        preprocessor = RemoteInferenceService(client)   # same preprocessing as the Lambda, no models loaded

        done = failed = missing = 0
        for article_id, text in qs.order_by("id").values_list("id", "article_text")[:limit]:
            processed = preprocessor.preprocess_text(text)
            if not processed:
                missing += 1
                continue
            try:
                result = client.infer(request_id=str(uuid.uuid4()), article_text=processed)
            except Exception as exc:  # keep going; the article stays without probabilities
                failed += 1
                self.stderr.write(f"id {article_id}: {type(exc).__name__}")
                continue
            probs = result.get("strategic_intent_probs")
            if not isinstance(probs, dict) or not probs:
                missing += 1
                continue
            MediaNarrative.objects.filter(id=article_id, intent_probs__isnull=True).update(intent_probs=probs)
            done += 1
        self.stdout.write(self.style.SUCCESS(
            f"Stored: {done}  failed: {failed}  no probabilities returned: {missing}"))
        if missing and not done:
            self.stdout.write(self.style.WARNING(
                "The inference API returned no probabilities: deploy the updated inference service first."))

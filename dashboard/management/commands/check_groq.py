# dashboard/management/commands/check_groq.py
# Preflight for the Groq arbitration path. _get_llm_strategic_intent swallows
# every error and returns Neutral/0.0 (ml_inference_service.py:452,545), so a
# missing key or a decommissioned GROQ_MODEL looks like a lake of low-confidence
# predictions instead of a failure. This command fails loud instead: it exits
# non-zero with the real Groq error, so the compose chain stops before the batch.
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from groq import Groq


class Command(BaseCommand):
    help = "Verify the configured Groq key + model actually work before running inference."

    def handle(self, *args, **options):
        key = getattr(settings, "GROQ_API_KEY", "") or ""
        model = getattr(settings, "GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

        if not key:
            raise CommandError(
                "GROQ_API_KEY is empty. Arbitration would silently degrade to ensemble-only."
            )

        self.stdout.write(f"Pinging Groq model '{model}' ...")
        try:
            resp = Groq(api_key=key).chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with the single word: ok"}],
                temperature=0.0,
            )
        except Exception as e:
            # Deliberately surface the real error (bad key, model_decommissioned, etc.)
            # rather than swallowing it the way the inference path does.
            raise CommandError(f"Groq call failed for model '{model}': {e}") from e

        reply = resp.choices[0].message.content.strip()
        self.stdout.write(self.style.SUCCESS(f"Groq OK. model={model} reply={reply!r}"))

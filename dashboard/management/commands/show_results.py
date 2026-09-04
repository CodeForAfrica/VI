# dashboard/management/commands/show_results.py
# Read-only reporter for the local E2E test: prints the classification state of
# every MediaNarrative so you can eyeball what the classifier produced (and, on a
# second run, confirm processed rows are skipped rather than re-run).
from django.core.management.base import BaseCommand
from dashboard.models import MediaNarrative


class Command(BaseCommand):
    help = "Print the strategic_intent / confidence / tone / processed-at of every narrative."

    def handle(self, *args, **options):
        rows = MediaNarrative.objects.order_by('id')
        self.stdout.write("")
        self.stdout.write(f"{'id':>4} | {'strategic_intent':<20} | {'conf':>5} | {'tone':<14} | processed_at")
        self.stdout.write("-" * 78)
        for a in rows:
            intent = a.strategic_intent or "(none = Neutral)"
            conf = f"{a.confidence:.2f}" if a.confidence is not None else "-"
            tone = a.tone or "-"
            processed = a.ml_processed_at.isoformat(timespec='seconds') if a.ml_processed_at else "(unprocessed)"
            self.stdout.write(f"{a.id:>4} | {intent:<20} | {conf:>5} | {tone:<14} | {processed}")
        self.stdout.write("-" * 78)
        self.stdout.write(f"{rows.count()} rows total.")

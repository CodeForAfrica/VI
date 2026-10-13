import json
import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from dashboard.models import MediaNarrative


class Command(BaseCommand):
    help = "Preview or back up and queue only LGBTQ articles for corrected inference."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Apply the reset; otherwise preview only.")
        parser.add_argument("--backup", help="New JSON backup path; required with --apply.")
        parser.add_argument("--include-anchors", action="store_true", help="Explicitly include anchor records.")

    def handle(self, *args, **options):
        if options["apply"] and not options["backup"]:
            raise CommandError("--apply requires --backup to preserve the existing results.")
        self.stdout.write("[lgbtq-reset] Selecting LGBTQ records only; Other and General News are excluded.")
        queryset = MediaNarrative.objects.filter(strategic_intent__iexact="LGBTQ")
        anchors = queryset.filter(is_anchor=True).count()
        if not options["include_anchors"]:
            queryset = queryset.filter(is_anchor=False)
        queryset = queryset.exclude(article_text__isnull=True).exclude(article_text="").exclude(
            article_text__iexact="no content available"
        )
        with transaction.atomic():
            records = list(queryset.select_for_update().order_by("pk").values(
                "id", "strategic_intent", "confidence", "tone", "ml_processed_at"
            ))
            article_ids = [record["id"] for record in records]
            self.stdout.write(f"[lgbtq-reset] Eligible records: {len(records)}; LGBTQ anchors: {anchors}; "
                              f"include_anchors={options['include_anchors']}.")
            for offset in range(0, len(article_ids), 200):
                self.stdout.write("[lgbtq-reset] Scoped Lambda event: " + json.dumps(
                    {"operation": "processing-only", "article_ids": article_ids[offset:offset + 200]}
                ))
            if not options["apply"]:
                self.stdout.write("[lgbtq-reset] Preview completed; no records changed. Use --apply --backup PATH after approval.")
                return
            if not records:
                self.stdout.write("[lgbtq-reset] No eligible LGBTQ records; nothing changed.")
                return
            self._write_backup(options["backup"], records, article_ids)
            self.stdout.write("[lgbtq-reset] Clearing intent and processed timestamp for the backed-up IDs only.")
            updated = MediaNarrative.objects.filter(pk__in=article_ids).update(
                strategic_intent=None, ml_processed_at=None
            )
        self.stdout.write(f"[lgbtq-reset] Reset completed: {updated} records queued. No inference was run.")

    def _write_backup(self, path, records, article_ids):
        self.stdout.write(f"[lgbtq-reset] Saving existing results to new backup: {path}.")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as backup:
                json.dump({"records": records, "article_ids": article_ids}, backup, default=str, indent=2)
                backup.flush()
                os.fsync(backup.fileno())
        except (OSError, TypeError, ValueError) as exc:
            raise CommandError(f"Backup failed; no records reset: {exc}") from exc
        self.stdout.write("[lgbtq-reset] Backup completed before database reset.")

"""Load human-verified anchor labels for PPI.

The CSV needs the columns  id, true_label, split  where split is "train" (used to
train the models) or "calib" (held out; the only anchors that correct the index).

    # Preview only: nothing is written.
    python manage.py import_ppi_labels labels.csv

    # Write, after reviewing the preview. The previous values are saved first.
    python manage.py import_ppi_labels labels.csv --apply --backup /secure/ppi-labels-backup.json
"""
import csv
import json
import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from dashboard.models import MediaNarrative
from dashboard.management.commands.update_vulnerability_indexes import map_raw_intent_to_contextual

VALID_SPLITS = {"train", "calib"}


class Command(BaseCommand):
    help = "Import human anchor labels (id, true_label, split) into MediaNarrative for PPI."

    def add_arguments(self, parser):
        parser.add_argument("csv_path")
        parser.add_argument("--apply", action="store_true", help="Write the labels (default: preview only).")
        parser.add_argument("--backup", help="New JSON file for previous values; required with --apply.")

    def handle(self, *args, csv_path, apply, backup, **options):
        rows, problems = self._read(csv_path)
        if problems:
            for line in problems[:20]:
                self.stderr.write(line)
            raise CommandError(f"{len(problems)} invalid rows; nothing written.")

        ids = [r["id"] for r in rows]
        existing = {a.id: a for a in MediaNarrative.objects.filter(id__in=ids)}
        missing = sorted(set(ids) - set(existing))
        if missing:
            raise CommandError(f"{len(missing)} ids not found (first: {missing[:10]}); nothing written.")

        unmapped = sorted({r["true_label"] for r in rows if map_raw_intent_to_contextual(r["true_label"]) in (None, "Other")})
        splits = {s: sum(1 for r in rows if r["split"] == s) for s in VALID_SPLITS}
        self.stdout.write(f"Rows: {len(rows)}  train: {splits['train']}  calib: {splits['calib']}")
        if unmapped:
            self.stdout.write(self.style.WARNING(
                f"Labels that map to no index intent (counted as 'none of the intents'): {unmapped}"))
        if not apply:
            self.stdout.write("Preview only. Re-run with --apply --backup <file> to write.")
            return

        if not backup:
            raise CommandError("--backup is required with --apply.")
        if os.path.exists(backup):
            raise CommandError(f"Backup file {backup} already exists; choose a new path.")
        snapshot = [{"id": a.id, "true_label": a.true_label, "is_anchor": a.is_anchor,
                     "anchor_split": a.anchor_split} for a in existing.values()]
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(snapshot, fh)

        with transaction.atomic():
            for r in rows:
                article = existing[r["id"]]
                article.true_label = r["true_label"]
                article.anchor_split = r["split"]
                article.is_anchor = True
            MediaNarrative.objects.bulk_update(
                existing.values(), ["true_label", "anchor_split", "is_anchor"], batch_size=1000)
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(rows)} labels. Backup: {backup}"))

    @staticmethod
    def _read(path):
        rows, problems, seen = [], [], set()
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            needed = {"id", "true_label", "split"}
            if not needed.issubset(reader.fieldnames or []):
                raise CommandError(f"CSV must have columns {sorted(needed)}")
            for n, raw in enumerate(reader, start=2):
                try:
                    article_id = int(raw["id"])
                except (TypeError, ValueError):
                    problems.append(f"line {n}: invalid id {raw['id']!r}")
                    continue
                label = (raw["true_label"] or "").strip()
                split = (raw["split"] or "").strip().lower()
                if not label:
                    problems.append(f"line {n}: empty true_label")
                elif split not in VALID_SPLITS:
                    problems.append(f"line {n}: split must be train or calib, got {split!r}")
                elif article_id in seen:
                    problems.append(f"line {n}: duplicate id {article_id}")
                else:
                    seen.add(article_id)
                    rows.append({"id": article_id, "true_label": label, "split": split})
        return rows, problems

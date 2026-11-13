"""Fit the per-intent isotonic calibrator used by the PPI content signal.

Uses held-out calibration anchors (anchor_split = 'calib') that have both a
human label and stored per-intent probabilities, as in the notebook's
calibration cell. The new calibrator is activated only if it lowers the
cross-validated Brier score.

    python manage.py fit_intent_calibrator            # fit, report, activate if better
    python manage.py fit_intent_calibrator --dry-run  # report only
"""
import numpy as np
from django.core.management.base import BaseCommand
from django.db import transaction

from dashboard.models import IntentCalibration, MediaNarrative
from dashboard.services import ppi
from dashboard.management.commands.update_vulnerability_indexes import (
    CONTEXTUAL_INTENTS, map_raw_intent_to_contextual)


class Command(BaseCommand):
    help = "Fit per-intent isotonic calibration on held-out calibration anchors."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--min-samples", type=int, default=200,
                            help="Refuse to fit with fewer calibration anchors than this.")
        parser.add_argument("--folds", type=int, default=5)

    def handle(self, *args, dry_run, min_samples, folds, **options):
        intents = list(CONTEXTUAL_INTENTS)
        rows = MediaNarrative.objects.filter(
            is_anchor=True, anchor_split="calib",
            true_label__isnull=False, intent_probs__isnull=False,
        ).values_list("intent_probs", "true_label")

        m, y = [], []
        for probs, label in rows.iterator(chunk_size=2000):
            vec, _ = ppi.intent_vector(probs, None, None, intents, map_raw_intent_to_contextual)
            if vec is None:
                continue
            m.append(vec)
            y.append(ppi.label_vector(label, intents, map_raw_intent_to_contextual))
        n = len(m)
        self.stdout.write(f"Calibration anchors with stored probabilities: {n}")
        if n < min_samples:
            self.stdout.write(self.style.WARNING(
                f"Fewer than {min_samples}; not fitting. Re-score the calibration anchors so "
                "their per-intent probabilities are stored, or lower --min-samples."))
            return
        m, y = np.array(m), np.array(y)

        # Cross-validated Brier: fit on k-1 folds, score the held-out fold.
        rng = np.random.RandomState(0)
        fold_of = rng.permutation(n) % folds
        cal_pred = np.zeros_like(m)
        for f in range(folds):
            train, test = fold_of != f, fold_of == f
            params = ppi.fit_isotonic(m[train], y[train], intents)
            for i in np.where(test)[0]:
                cal_pred[i] = ppi.apply_calibration(m[i], params, intents)
        before, after = ppi.brier(m, y), ppi.brier(cal_pred, y)
        self.stdout.write(f"Brier score (lower is better): raw {before:.4f}, calibrated {after:.4f} (cross-validated)")

        params = ppi.fit_isotonic(m, y, intents)
        self.stdout.write(f"Intents calibrated: {sorted(params)}; left raw: {sorted(set(intents) - set(params))}")
        if dry_run:
            self.stdout.write("Dry run: nothing saved.")
            return
        if after >= before:
            self.stdout.write(self.style.WARNING("Calibration does not improve the Brier score; not activated."))
            return
        with transaction.atomic():
            IntentCalibration.objects.filter(is_active=True).update(is_active=False)
            cal = IntentCalibration.objects.create(
                n_samples=n, params=params, brier_before=before, brier_after=after, is_active=True)
        self.stdout.write(self.style.SUCCESS(f"Saved and activated calibrator #{cal.id}."))

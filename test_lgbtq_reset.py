import json
import os
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from dashboard.models import MediaNarrative


class LGBTQResetTests(TestCase):
    def setUp(self):
        self.processed_at = timezone.now()
        self.article = MediaNarrative.objects.create(
            article_text="A forest restoration article", strategic_intent="LGBTQ",
            ml_processed_at=self.processed_at, confidence=0.46, tone="Alarmist",
        )
        self.anchor = MediaNarrative.objects.create(
            article_text="Anchor article", strategic_intent="LGBTQ", is_anchor=True,
            ml_processed_at=self.processed_at,
        )
        self.others = [MediaNarrative.objects.create(
            article_text="An actor at a football match", strategic_intent=intent,
            ml_processed_at=self.processed_at,
        ) for intent in ("Other", "Economic", "General News")]

    def run_command(self, **kwargs):
        output = StringIO()
        call_command("reset_lgbtq_intents_to_null", stdout=output, **kwargs)
        return output.getvalue()

    def test_default_is_read_only_and_preserves_anchors(self):
        output = self.run_command()
        self.article.refresh_from_db()
        self.assertEqual(self.article.strategic_intent, "LGBTQ")
        self.assertEqual(self.article.ml_processed_at, self.processed_at)
        self.assertIn('"article_ids": [%s]' % self.article.pk, output)
        self.assertIn("Preview completed", output)

    def test_corrected_mapping_preserves_explicit_lgbtq(self):
        from dashboard.utils import map_to_canonical_intent
        self.assertEqual(map_to_canonical_intent("Human Rights Advocacy"), "SocialFragility")
        self.assertEqual(map_to_canonical_intent("LGBTQ"), "LGBTQ")

    def test_apply_requires_backup(self):
        with self.assertRaisesMessage(CommandError, "requires --backup"):
            self.run_command(apply=True)
        self.article.refresh_from_db()
        self.assertEqual(self.article.strategic_intent, "LGBTQ")

    def test_backup_precedes_reset_and_other_labels_are_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backup.json"
            output = self.run_command(apply=True, backup=str(path))
            backup = json.loads(path.read_text())
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(backup["article_ids"], [self.article.pk])
        record = backup["records"][0]
        self.assertEqual(record["strategic_intent"], "LGBTQ")
        self.assertEqual(record["tone"], "Alarmist")
        self.assertEqual(record["confidence"], 0.46)
        self.assertEqual(record["ml_processed_at"], str(self.processed_at))
        self.article.refresh_from_db()
        self.assertIsNone(self.article.strategic_intent)
        self.assertIsNone(self.article.ml_processed_at)
        self.assertEqual(self.article.tone, "Alarmist")
        self.anchor.refresh_from_db()
        self.assertEqual(self.anchor.strategic_intent, "LGBTQ")
        for article in self.others:
            original = article.strategic_intent
            article.refresh_from_db()
            self.assertEqual(article.strategic_intent, original)
            self.assertEqual(article.ml_processed_at, self.processed_at)
        self.assertLess(output.index("Backup completed"), output.index("Clearing intent"))
        self.assertIn("Reset completed: 1", output)
        from django.utils.dateparse import parse_datetime
        restored = dict(record)
        article_id = restored.pop("id")
        restored["ml_processed_at"] = parse_datetime(restored["ml_processed_at"])
        MediaNarrative.objects.filter(pk=article_id).update(**restored)
        self.article.refresh_from_db()
        self.assertEqual(self.article.strategic_intent, "LGBTQ")
        self.assertEqual(self.article.ml_processed_at, self.processed_at)
        self.assertEqual(self.article.confidence, 0.46)
        self.assertEqual(self.article.article_text, "A forest restoration article")

    def test_existing_backup_is_not_overwritten_or_records_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backup.json"
            path.write_text("existing backup")
            with self.assertRaisesMessage(CommandError, "Backup failed"):
                self.run_command(apply=True, backup=str(path))
            self.assertEqual(path.read_text(), "existing backup")
        self.article.refresh_from_db()
        self.assertEqual(self.article.strategic_intent, "LGBTQ")
        self.assertEqual(self.article.ml_processed_at, self.processed_at)

    def test_explicit_anchor_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_command(apply=True, include_anchors=True, backup=str(Path(directory) / "backup.json"))
        self.anchor.refresh_from_db()
        self.assertIsNone(self.anchor.strategic_intent)
        self.assertIsNone(self.anchor.ml_processed_at)

    def test_backup_failure_keeps_results(self):
        with mock.patch("dashboard.management.commands.reset_lgbtq_intents_to_null.os.fsync", side_effect=OSError("disk full")):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesMessage(CommandError, "no records reset"):
                    self.run_command(apply=True, backup=str(Path(directory) / "backup.json"))
        self.article.refresh_from_db()
        self.assertEqual(self.article.strategic_intent, "LGBTQ")

    def test_empty_content_is_not_queued_and_rerunning_is_a_noop(self):
        for text in ("", "No Content Available"):
            MediaNarrative.objects.create(article_text=text, strategic_intent="LGBTQ")
        with tempfile.TemporaryDirectory() as directory:
            self.run_command(apply=True, backup=str(Path(directory) / "first.json"))
            output = self.run_command(apply=True, backup=str(Path(directory) / "second.json"))
            self.assertFalse((Path(directory) / "second.json").exists())
        self.assertIn("nothing changed", output)

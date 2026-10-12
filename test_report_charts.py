"""Report chart regression tests without importing the heavyweight dashboard views.

Run with manage.py test test_report_charts --settings=inference_api.tests_settings.
"""
import ast
import base64
from io import BytesIO
import logging
from pathlib import Path
from unittest import mock

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from django.db.models import Count
from django.test import TestCase

from dashboard.models import MediaNarrative


class ReportFactorChartTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        source = Path(__file__).parent.joinpath("dashboard/views.py").read_text()
        report = next(node for node in ast.parse(source).body
                      if isinstance(node, ast.FunctionDef) and node.name == "generate_report")
        # Execute the actual factor-chart stage, using the real ORM and renderer.
        stage = next(node for node in report.body if isinstance(node, ast.Try)
                     and any(isinstance(child, ast.Assign)
                             and any(isinstance(target, ast.Name) and target.id == "intent_counts"
                                     for target in child.targets) for child in node.body))
        cls.stage = compile(ast.Module(body=[stage], type_ignores=[]), "dashboard/views.py", "exec")

    def tearDown(self):
        plt.close("all")
        super().tearDown()

    def render_factor(self, intents):
        MediaNarrative.objects.bulk_create([
            MediaNarrative(article_text="Sample article", strategic_intent=intent)
            for intent in intents
        ])
        context = dict(base_query=MediaNarrative.objects.all(), Count=Count, pd=pd,
                       plt=plt, BytesIO=BytesIO, base64=base64,
                       logger=logging.getLogger("vi.report.chart.tests"), db_country="Ethiopia",
                       primary_intent="General Influence", factor_chart_base64="",
                       volume_chart_base64="already-generated-volume-chart")
        exec(self.stage, context)
        return context

    def test_null_only_intents_do_not_reach_matplotlib(self):
        with mock.patch.object(plt, "subplots") as subplot:
            result = self.render_factor([None, None])
        subplot.assert_not_called()
        self.assertEqual(result["factor_chart_base64"], "")
        self.assertEqual(result["primary_intent"], "General Influence")

    def test_missing_labels_do_not_displace_valid_categories(self):
        result = self.render_factor([None] * 6 + ["", "Unknown", "Economic", "Economic", "Sovereignty"])
        self.assertEqual(list(result["df_f"]["Factor"]), ["Sovereignty", "Economic"])
        self.assertEqual(list(result["df_f"]["Val"]), [1, 2])
        self.assertEqual(result["primary_intent"], "Economic")
        self.assertTrue(base64.b64decode(result["factor_chart_base64"]).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_empty_or_unknown_only_intents_skip_factor_chart(self):
        for intents in ([], ["", "Unknown"]):
            with self.subTest(intents=intents), mock.patch.object(plt, "subplots") as subplot:
                result = self.render_factor(intents)
            subplot.assert_not_called()
            self.assertEqual(result["factor_chart_base64"], "")

    def test_factor_failure_preserves_volume_chart_and_logs_traceback(self):
        with mock.patch.object(plt, "subplots", side_effect=RuntimeError("test plotting failure")):
            with self.assertLogs("vi.report.chart.tests", level="ERROR") as captured:
                result = self.render_factor(["Economic"])
        self.assertEqual(result["volume_chart_base64"], "already-generated-volume-chart")
        self.assertEqual(result["factor_chart_base64"], "")
        self.assertIn("Report factor chart generation failed", captured.output[0])
        self.assertIn("Traceback", captured.output[0])

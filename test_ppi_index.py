"""PPI / AIPW index tests.

Run with: python manage.py test test_ppi_index --settings=inference_api.tests_settings
"""
import csv
import io
import os
import tempfile

import numpy as np
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from dashboard.models import IntentCalibration, MediaNarrative, VulnerabilityIndex
from dashboard.services import ppi
from dashboard.services.vi_naming import normalize_actor, normalize_country
from dashboard.management.commands.update_vulnerability_indexes import (
    CONTEXTUAL_INTENTS, compute_CAs, compute_R, compute_gs, map_raw_intent_to_contextual)

INTENTS = list(CONTEXTUAL_INTENTS)


class NamingTests(SimpleTestCase):
    def test_ingestion_names_map_to_index_names(self):
        self.assertEqual(normalize_actor("USA"), "UnitedStates")
        self.assertEqual(normalize_actor("Saudi Arabia"), "Saudi")
        self.assertEqual(normalize_country("SA"), "South Africa")
        self.assertEqual(normalize_country("Côte d'Ivoire"), "CoteIvoire")
        self.assertEqual(normalize_country("CoteIvoire"), "CoteIvoire")
        self.assertIsNone(normalize_actor("Unknown"))


class AipwTests(SimpleTestCase):
    def test_matches_notebook_formula(self):
        # Notebook: theta = mean(m + (xi/pi) * (y - m))
        m = np.array([[0.8, 0.2], [0.6, 0.4], [0.3, 0.7], [0.5, 0.5]])
        y = np.array([[1, 0], [0, 1], [0, 0], [0, 0]], dtype=float)
        xi = np.array([1, 1, 0, 0], dtype=float)
        theta, n_lab, pi = ppi.aipw(m, y, xi)
        expected = (m + (xi[:, None] / 0.5) * (y - m)).mean(axis=0)
        np.testing.assert_allclose(theta, np.clip(expected, 0, 1))
        self.assertEqual((n_lab, pi), (2, 0.5))

    def test_correction_removes_model_bias(self):
        # Model says 0.9 for intent 0 everywhere; humans say only 30% truly are.
        rng = np.random.RandomState(1)
        n = 5000
        truth = (rng.rand(n) < 0.3).astype(float)
        m = np.column_stack([np.full(n, 0.9), np.full(n, 0.1)])
        y = np.column_stack([truth, 1 - truth])
        xi = (rng.rand(n) < 0.1).astype(float)
        theta, _, _ = ppi.aipw(m, y, xi)
        self.assertAlmostEqual(theta[0], 0.3, delta=0.05)     # corrected
        self.assertAlmostEqual(m[:, 0].mean(), 0.9)            # uncorrected would be 0.9

    def test_no_labels_falls_back_to_model_mean(self):
        m = np.array([[0.4, 0.6], [0.2, 0.8]])
        theta, n_lab, _ = ppi.aipw(m, np.zeros_like(m), np.zeros(2))
        np.testing.assert_allclose(theta, [0.3, 0.7])
        self.assertEqual(n_lab, 0)

    def test_intent_vector_sums_raw_classes_per_index_intent(self):
        probs = {"Economic Dependency": 0.5, "Trade Dominance": 0.2, "Military Presence": 0.3}
        vec, approx = ppi.intent_vector(probs, None, None, INTENTS, map_raw_intent_to_contextual)
        self.assertFalse(approx)
        self.assertAlmostEqual(vec[INTENTS.index("Economic")], 0.7)
        self.assertAlmostEqual(vec[INTENTS.index("MilitaryPresence")], 0.3)

    def test_intent_vector_fallback_is_flagged(self):
        vec, approx = ppi.intent_vector(None, "Economic", 0.8, INTENTS, map_raw_intent_to_contextual)
        self.assertTrue(approx)
        self.assertAlmostEqual(vec[INTENTS.index("Economic")], 0.8)
        self.assertAlmostEqual(vec.sum(), 1.0)

    def test_calibration_is_interpolation(self):
        params = {"Economic": {"x": [0.0, 1.0], "y": [0.0, 0.5]}}
        vec = np.zeros(len(INTENTS)); vec[INTENTS.index("Economic")] = 0.8
        out = ppi.apply_calibration(vec, params, INTENTS)
        self.assertAlmostEqual(out[INTENTS.index("Economic")], 0.4)


class CommandTests(TestCase):
    def _article(self, i, actor="France", country="Senegal", probs=None, **kw):
        return MediaNarrative.objects.create(
            article_text=f"text {i}", inferred_actor=actor, target_country=country,
            strategic_intent=kw.pop("strategic_intent", "Economic"),
            confidence=kw.pop("confidence", 0.9),
            intent_probs=probs if probs is not None else {"Economic Dependency": 0.9, "Social Fragility": 0.1},
            **kw)

    def _risk(self, actor, country, intent):
        return VulnerabilityIndex.objects.get(actor=actor, country=country, intent=intent).final_risk

    def _ca(self, actor, country, intent):
        g = compute_gs()
        return compute_CAs(g, compute_R(g))[intent][actor][country]

    def test_human_labels_correct_the_content_signal(self):
        # 10 articles, model says Economic 0.9 each; 4 are calibration anchors,
        # and humans say only 1 of the 4 is Economic.
        for i in range(10):
            label = {}
            if i < 4:
                label = dict(is_anchor=True, anchor_split="calib",
                             true_label="Economic Dependency" if i == 0 else "Social Fragility")
            self._article(i, **label)
        call_command("update_vulnerability_indexes", stdout=io.StringIO())
        # base = mean(m + xi/pi*(y-m)), pi = 0.4
        m = np.full(10, 0.9); y = np.array([1, 0, 0, 0] + [0] * 6, dtype=float); xi = np.array([1] * 4 + [0] * 6, dtype=float)
        base = float(np.clip((m + xi / 0.4 * (y - m)).mean(), 0, 1))
        ca = self._ca("France", "Senegal", "Economic")
        self.assertAlmostEqual(self._risk("France", "Senegal", "Economic"), base + (1 - base) * ca, places=6)
        self.assertLess(base, 0.9)    # correction applied

    def test_training_anchors_do_not_correct(self):
        for i in range(4):
            self._article(i, is_anchor=True, anchor_split="train", true_label="Social Fragility")
        out = io.StringIO()
        call_command("update_vulnerability_indexes", stdout=out)
        ca = self._ca("France", "Senegal", "Economic")
        self.assertAlmostEqual(self._risk("France", "Senegal", "Economic"), 0.9 + 0.1 * ca, places=6)
        self.assertIn("No calibration anchors", out.getvalue())

    def test_ingestion_names_are_counted(self):
        self._article(1, actor="USA", country="SA")
        call_command("update_vulnerability_indexes", stdout=io.StringIO())
        ca = self._ca("UnitedStates", "South Africa", "Economic")
        self.assertAlmostEqual(self._risk("UnitedStates", "South Africa", "Economic"), 0.9 + 0.1 * ca, places=6)

    def test_dry_run_writes_nothing(self):
        self._article(1)
        call_command("update_vulnerability_indexes", "--dry-run", stdout=io.StringIO())
        self.assertEqual(VulnerabilityIndex.objects.count(), 0)

    def test_import_labels_preview_then_apply(self):
        a = self._article(1)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "labels.csv")
            with open(path, "w", newline="") as fh:
                w = csv.writer(fh); w.writerow(["id", "true_label", "split"]); w.writerow([a.id, "Economic Dependency", "calib"])
            call_command("import_ppi_labels", path, stdout=io.StringIO())
            a.refresh_from_db(); self.assertIsNone(a.true_label)
            backup = os.path.join(tmp, "backup.json")
            call_command("import_ppi_labels", path, "--apply", "--backup", backup, stdout=io.StringIO())
            a.refresh_from_db()
            self.assertEqual((a.true_label, a.anchor_split, a.is_anchor), ("Economic Dependency", "calib", True))
            self.assertTrue(os.path.exists(backup))

    def test_calibrator_fits_and_is_used(self):
        rng = np.random.RandomState(0)
        for i in range(300):
            p = float(rng.uniform(0.5, 1.0))          # model is overconfident:
            truly_economic = rng.rand() < (p - 0.5)   # true rate is far below its score
            self._article(i, probs={"Economic Dependency": p, "Social Fragility": 1 - p},
                          is_anchor=True, anchor_split="calib",
                          true_label="Economic Dependency" if truly_economic else "Social Fragility")
        call_command("fit_intent_calibrator", stdout=io.StringIO())
        cal = IntentCalibration.objects.get(is_active=True)
        self.assertLess(cal.brier_after, cal.brier_before)


class ShrinkageTests(SimpleTestCase):
    def test_thin_pair_pulled_to_prior_big_pair_keeps_estimate(self):
        thetas = {("A", "X"): np.array([1.0, 0.0]), ("B", "X"): np.array([0.2, 0.8])}
        sizes = {("A", "X"): 1, ("B", "X"): 999}
        prior = ppi.intent_priors(thetas, sizes)
        np.testing.assert_allclose(prior, [(1 * 1.0 + 999 * 0.2) / 1000, (999 * 0.8) / 1000])
        thin = ppi.shrink(thetas[("A", "X")], 1, prior, k=20)
        big = ppi.shrink(thetas[("B", "X")], 999, prior, k=20)
        self.assertAlmostEqual(thin[0], (1 * 1.0 + 20 * prior[0]) / 21)
        self.assertLess(thin[0], 0.3)                       # 1 article no longer gives 1.0
        self.assertAlmostEqual(big[0], 0.2, delta=0.01)    # 999 articles keep their own share

    def test_k_zero_disables(self):
        np.testing.assert_allclose(ppi.shrink(np.array([1.0, 0.0]), 1, np.array([0.5, 0.5]), k=0), [1.0, 0.0])


class ShrinkageCommandTests(TestCase):
    def test_no_articles_gives_zero_and_thin_pair_is_shrunk(self):
        for i in range(200):   # France -> Senegal: plenty of articles, 20% Economic
            MediaNarrative.objects.create(article_text=f"a{i}", inferred_actor="France", target_country="Senegal",
                                          strategic_intent="Economic", confidence=0.9,
                                          intent_probs={"Economic Dependency": 0.2, "Social Fragility": 0.8})
        MediaNarrative.objects.create(article_text="b", inferred_actor="Israel", target_country="Senegal",
                                      strategic_intent="Economic", confidence=0.9,
                                      intent_probs={"Economic Dependency": 1.0})   # one article, 100% Economic
        call_command("update_vulnerability_indexes", stdout=io.StringIO())
        g = compute_gs(); CA = compute_CAs(g, compute_R(g))
        risk = lambda a, c, i: VulnerabilityIndex.objects.get(actor=a, country=c, intent=i).final_risk
        prior = (200 * 0.2 + 1 * 1.0) / 201
        thin = (1 * 1.0 + 20 * prior) / 21
        self.assertAlmostEqual(risk("Israel", "Senegal", "Economic"), thin + (1 - thin) * CA["Economic"]["Israel"]["Senegal"], places=6)
        self.assertAlmostEqual(risk("Rwanda", "DRC", "Economic"), CA["Economic"]["Rwanda"]["DRC"], places=6)   # no articles: content 0


class ProbabilityPlumbingTests(SimpleTestCase):
    """Per-intent probabilities must reach the pipeline result unchanged."""

    def _service(self, remote):
        from unittest import mock
        from dashboard.services.ml_inference_service import MLInferenceService
        from dashboard.services.remote_inference_service import RemoteInferenceService
        s = object.__new__(RemoteInferenceService if remote else MLInferenceService)
        s.lookup_risk = mock.Mock(return_value=.4)
        s.extract_entities_from_content = mock.Mock(return_value={"organizations": [], "persons": []})
        s.extract_actor_from_content = mock.Mock(return_value="France")
        s.calculate_vulnerability_index = mock.Mock(return_value=.5)
        s._get_llm_strategic_intent = mock.Mock(return_value=("Economic", .6, ""))
        return s

    def test_remote_probabilities_reach_result(self):
        from unittest import mock
        s = self._service(remote=True)
        probs = {"Economic": 0.8, "Sovereignty": 0.2}
        s.client = mock.Mock()
        s.client.infer.return_value = {"strategic_intent": "Economic", "strategic_intent_confidence": .8,
                                       "tone": "Factual", "tone_confidence": .7,
                                       "strategic_intent_probs": probs}
        s.deadline = None
        s.event_logger = mock.Mock()
        self.assertEqual(s.perform_inference("some article text")["intent_probs"], probs)

    def test_local_probabilities_use_label_encoder_order(self):
        from unittest import mock
        s = self._service(remote=False)
        classifier = mock.Mock()
        classifier.predict.return_value = ([0], [[0.7, 0.3]])
        s._load_strategic_classifier = mock.Mock(return_value=classifier)
        s._strategic_label_encoder = mock.Mock(classes_=np.array(["Economic", "Sovereignty"]))
        s._strategic_label_encoder.inverse_transform = lambda xs: ["Economic" for _ in xs]
        s.perform_tone_inference = mock.Mock(return_value=("Factual", .7))
        result = s.perform_inference("some article text")
        self.assertEqual(result["intent_probs"], {"Economic": 0.7, "Sovereignty": 0.3})

    def test_api_drops_malformed_probabilities(self):
        from inference_api.runtime import _as_probs
        self.assertEqual(_as_probs({"strategic_intent_probs": {"Economic": 0.9}}), {"Economic": 0.9})
        self.assertIsNone(_as_probs({"strategic_intent_probs": {"Economic": 1.5}}))
        self.assertIsNone(_as_probs({}))


class ConfidenceRuleTests(ProbabilityPlumbingTests):
    """Saved confidence = the model's intent score: not the LLM's when they agree, never the tone score."""

    def _local(self, llm):
        from unittest import mock
        s = self._service(remote=False)
        s._get_llm_strategic_intent = mock.Mock(return_value=llm)
        classifier = mock.Mock()
        classifier.predict.return_value = ([0], [[0.6, 0.4]])
        s._load_strategic_classifier = mock.Mock(return_value=classifier)
        s._strategic_label_encoder = mock.Mock(classes_=np.array(["Economic", "Sovereignty"]))
        s._strategic_label_encoder.inverse_transform = lambda xs: ["Economic" for _ in xs]
        s.perform_tone_inference = mock.Mock(return_value=("Factual", .95))
        return s

    def test_agreement_keeps_model_score_not_llm_or_tone(self):
        result = self._local(("Economic", .9, "")).perform_inference("some article text")
        self.assertEqual(result["strategic_intent"], "Economic")
        self.assertAlmostEqual(result["confidence"], 0.6)       # not 0.9 (LLM) and not 0.95 (tone)

    def test_disagreement_rule_unchanged(self):
        result = self._local(("Sovereignty", .9, "")).perform_inference("some article text")
        self.assertEqual(result["strategic_intent"], "Sovereignty")
        self.assertAlmostEqual(result["confidence"], 0.9)

    def test_empty_text_no_longer_crashes(self):
        from dashboard.services.ml_inference_service import MLInferenceService
        s = object.__new__(MLInferenceService)
        result = s.perform_inference("")
        self.assertEqual((result["strategic_intent"], result["confidence"]), ("unknown", 0.0))

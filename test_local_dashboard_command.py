"""Regression checks for the credential-free local dashboard gate."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from babel.messages.catalog import Catalog
from scripts.check_translations import missing_translations
from config.local_test_providers import ProviderTracker, TrackedGroq, TrackedTranslate
from types import SimpleNamespace
from unittest import mock


class CatalogueCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.expected = Catalog()
        self.expected.add("Hello %(name)s", flags={"python-format"})
        self.french = Catalog(locale="fr")

    def test_missing_empty_and_fuzzy_are_failures(self):
        self.assertIn("missing:", missing_translations(self.expected, self.french)[0])
        self.french.add("Hello %(name)s", "")
        self.assertIn("empty:", missing_translations(self.expected, self.french)[0])
        self.french.add("Hello %(name)s", "Bonjour %(name)s", flags={"fuzzy"})
        self.assertIn("fuzzy/unconfirmed:", missing_translations(self.expected, self.french)[0])

    def test_placeholder_errors_fail_even_when_translator_removed_format_flag(self):
        self.french.add("Hello %(name)s", "Bonjour %(wrong)s")
        self.assertIn("invalid placeholders:", missing_translations(self.expected, self.french)[0])

    def test_complete_translation_passes(self):
        self.french.add("Hello %(name)s", "Bonjour %(name)s")
        self.assertEqual(missing_translations(self.expected, self.french), [])

    def test_all_plural_forms_are_required(self):
        self.expected.add(("One article", "Many articles"))
        self.french.add("Hello %(name)s", "Bonjour %(name)s")
        self.french.add(("One article", "Many articles"), ("Un article", ""))
        self.assertIn("empty:", missing_translations(self.expected, self.french)[0])


class ExplicitInferenceModeTests(unittest.TestCase):
    def run_script(self, mode, key):
        environment = dict(os.environ, VI_E2E_INFERENCE_MODE=mode, VI_INFERENCE_API_KEY=key)
        return subprocess.run(["bash", "scripts/test-split-e2e.sh"], capture_output=True, text=True,
                              env=environment, timeout=10, cwd=Path(__file__).parent)

    def test_remote_command_requires_key_before_model_or_docker_setup(self):
        result = self.run_script("remote", "")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Remote inference requires VI_INFERENCE_API_KEY", result.stderr)
        self.assertNotIn("Checking Docker", result.stdout)

    def test_local_command_rejects_implicit_remote_switch(self):
        result = self.run_script("local", "test-only-not-a-credential")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Local inference was explicitly selected", result.stderr)
        self.assertNotIn("test-only-not-a-credential", result.stderr)


class LocalRunnerFailureTests(unittest.TestCase):
    def test_failed_build_is_red_and_cleans_only_its_project(self):
        with tempfile.TemporaryDirectory() as directory:
            docker = Path(directory) / "docker"
            docker.write_text('''#!/bin/sh
case "$*" in
  info|"compose version") exit 0;;
  "compose up --help") echo --wait-timeout; exit 0;;
  *"config --quiet"*|*"ps --status running --quiet"*) exit 0;;
  *"build dashboard checks browser"*) echo "deliberate build failure" >&2; exit 42;;
  *"logs --no-color"*) exit 0;;
  *"down --volumes --remove-orphans"*) echo "cleanup exact project: $*"; exit 0;;
esac
echo "Unexpected Docker command: $*" >&2
exit 99
''')
            docker.chmod(0o700)
            git = Path(directory) / "git"
            git.write_text('#!/bin/sh\nprintf "%040d\\n" 0\n')
            git.chmod(0o700)
            environment = dict(os.environ, PATH=f"{directory}:{os.environ['PATH']}", AWS_PROFILE="", GROQ_API_KEY="")
            result = subprocess.run(["bash", "scripts/test-local.sh"], env=environment,
                                    capture_output=True, text=True, timeout=10, cwd=Path(__file__).parent)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAILED during building", result.stderr)
            self.assertIn("deliberate build failure", result.stderr)
            self.assertIn("cleanup exact project: compose --project-name vi-dashboard-local-check", result.stdout)
            self.assertNotIn("LOCAL APPLICATION CHECKS PASSED", result.stdout)


class LiveProviderTrackingTests(unittest.TestCase):
    def test_independent_provider_modes_and_success_counts(self):
        tracker = ProviderTracker(aws_live=True, groq_live=False)
        client = mock.Mock()
        client.translate_text.return_value = {"TranslatedText": "Hello"}
        self.assertEqual(TrackedTranslate(client, tracker).translate_text(Text="Bonjour"), {"TranslatedText": "Hello"})
        status = tracker.snapshot()
        self.assertEqual(status["aws_translate"], {"mode": "live", "succeeded": 1, "failed": 0})
        self.assertEqual(status["groq"]["mode"], "controlled")
        status["aws_translate"]["succeeded"] = 0
        self.assertEqual(tracker.snapshot()["aws_translate"]["succeeded"], 1)

    def test_provider_failure_is_recorded_without_exposing_sdk_error_text(self):
        tracker = ProviderTracker(aws_live=True)
        client = mock.Mock()
        client.translate_text.side_effect = ValueError("sensitive-provider-error-body")
        with self.assertRaises(RuntimeError) as error:
            TrackedTranslate(client, tracker).translate_text(Text="Bonjour")
        self.assertNotIn("sensitive-provider-error-body", str(error.exception))
        self.assertEqual(tracker.snapshot()["aws_translate"]["failed"], 1)

    def test_empty_translation_cannot_pass(self):
        tracker = ProviderTracker(aws_live=True)
        client = mock.Mock()
        client.translate_text.return_value = {"TranslatedText": ""}
        with self.assertRaises(RuntimeError):
            TrackedTranslate(client, tracker).translate_text(Text="Bonjour")
        self.assertEqual(tracker.snapshot()["aws_translate"]["succeeded"], 0)

    def test_groq_requires_a_valid_final_answer_not_reasoning_only(self):
        tracker = ProviderTracker(groq_live=True)
        client = mock.Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="<think>Only reasoning</think>"))])
        validator = mock.Mock(side_effect=ValueError("no final answer"))
        with self.assertRaises(RuntimeError):
            TrackedGroq(client, tracker, validator).create(model="test")
        self.assertEqual(tracker.snapshot()["groq"]["failed"], 1)
        self.assertEqual(tracker.snapshot()["groq"]["succeeded"], 0)


class LocalCredentialCleanupTests(unittest.TestCase):
    def test_selected_aws_profile_and_groq_key_are_removed_on_failure(self):
        for export_succeeds in (True, False):
            with self.subTest(export_succeeds=export_succeeds), tempfile.TemporaryDirectory() as directory:
                docker = Path(directory) / "docker"
                docker.write_text('''#!/bin/sh
case "$*" in
  info|"compose version") exit 0;;
  "compose up --help") echo --wait-timeout; exit 0;;
  *"config --quiet"*|*"ps --status running --quiet"*|*"logs --no-color"*|*"down --volumes --remove-orphans"*) exit 0;;
  *"build dashboard checks browser"*) echo "deliberate build failure" >&2; exit 42;;
esac
exit 99
''')
                docker.chmod(0o700)
                git = Path(directory) / "git"
                git.write_text('#!/bin/sh\nprintf "%040d\\n" 0\n')
                git.chmod(0o700)
                aws = Path(directory) / "aws"
                aws.write_text('''#!/bin/sh
[ "$*" = "--profile selected-test-profile configure export-credentials --format process" ] || exit 99
printf '%s' '{"AccessKeyId":"fake-test-access","SecretAccessKey":"fake-test-secret"}'
''' + ("exit 0\n" if export_succeeds else 'echo "private-sdk-error" >&2\nexit 1\n'))
                aws.chmod(0o700)
                environment = dict(os.environ, PATH=f"{directory}:{os.environ['PATH']}", TMPDIR=directory,
                                   AWS_PROFILE="selected-test-profile", GROQ_API_KEY="fake-test-groq-key")
                result = subprocess.run(["bash", "scripts/test-local.sh"], env=environment,
                                        capture_output=True, text=True, timeout=10, cwd=Path(__file__).parent)
                self.assertNotEqual(result.returncode, 0)
                for secret in ("fake-test-access", "fake-test-secret", "fake-test-groq-key", "private-sdk-error"):
                    self.assertNotIn(secret, result.stdout + result.stderr)
                self.assertFalse(list(Path(directory).glob("vi-local-provider-credentials.*")))
                self.assertIn("Temporary provider credentials removed", result.stdout)
                self.assertIn("deliberate build failure" if export_succeeds else "Cannot resolve AWS_PROFILE", result.stderr)

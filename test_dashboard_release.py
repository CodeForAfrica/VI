"""Release policy must fail closed before any registry or production mutation."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.dashboard_release import REQUIRED_CHECKS, check_security, identity, publish, verify_receipt


class ReleasePolicyTests(unittest.TestCase):
    def setUp(self):
        self.image = {"Id": "sha256:" + "a" * 64, "Architecture": "arm64", "Os": "linux",
                      "RootFS": {"Type": "layers", "Layers": ["sha256:layer"]},
                      "Config": {"User": "appuser", "Labels": {"org.opencontainers.image.revision": "commit"}}}
        self.report = {"Metadata": {"DiffIDs": ["sha256:layer"], "ImageConfig": {
            "architecture": "arm64", "config": deepcopy(self.image["Config"])}}, "Results": [
                {"Class": "os-pkgs", "Type": "debian"},
                {"Class": "lang-pkgs", "Type": "python-pkg"}]}

    def test_complete_zero_vulnerability_report_passes(self):
        self.assertEqual(check_security(self.report, self.image), {})

    def test_unfixed_critical_blocks_and_high_is_reported(self):
        finding = {"Severity": "CRITICAL", "PkgName": "example", "InstalledVersion": "1", "VulnerabilityID": "CVE-test"}
        self.report["Results"][0]["Vulnerabilities"] = [finding]
        with self.assertRaisesRegex(ValueError, "Critical vulnerabilities"):
            check_security(self.report, self.image)
        finding["Severity"] = "HIGH"
        self.assertEqual(check_security(self.report, self.image), {"HIGH": 1})

    def test_missing_coverage_wrong_layers_and_wrong_config_block(self):
        for mutation in (lambda r: r["Results"].pop(),
                         lambda r: r["Metadata"].update(DiffIDs=["wrong"]),
                         lambda r: r["Metadata"]["ImageConfig"]["config"].update(User="root")):
            report = deepcopy(self.report)
            mutation(report)
            with self.assertRaises(ValueError):
                check_security(report, self.image)

    def receipt(self, directory):
        report_bytes = json.dumps(self.report).encode()
        (directory / "security.json").write_bytes(report_bytes)
        data = {"schema": 2, "checks": REQUIRED_CHECKS, "image_id": self.image["Id"], "identity": identity(self.image),
                "commit": "commit", "dirty": False, "aws_live": False, "groq_live": False,
                "run_id": "local", "run_attempt": "local", "created": datetime.now(timezone.utc).isoformat(),
                "security_sha256": hashlib.sha256(report_bytes).hexdigest()}
        (directory / "passed.json").write_text(json.dumps(data))
        return data

    @patch.dict(os.environ, {"GITHUB_RUN_ID": "local", "GITHUB_RUN_ATTEMPT": "local"})
    def test_receipt_requires_checks_commit_freshness_and_image_not_live_credentials(self):
        with tempfile.TemporaryDirectory() as temporary, patch('scripts.dashboard_release.inspect', return_value=self.image), patch('scripts.dashboard_release.command', side_effect=lambda *args: "commit" if args[1:]==('rev-parse','HEAD') else ""):
            directory = Path(temporary)
            self.receipt(directory)
            self.assertEqual(verify_receipt(directory)["commit"], "commit")
            for field, value in (("dirty", True), ("checks", []), ("schema", 1),
                                 ("commit", "different"), ("run_id", "old"),
                                 ("created", (datetime.now(timezone.utc)-timedelta(hours=3)).isoformat()),
                                 ("security_sha256", "wrong")):
                data = self.receipt(directory)
                data[field] = value
                (directory / "passed.json").write_text(json.dumps(data))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    verify_receipt(directory)

    def test_no_receipt_cannot_publish(self):
        with tempfile.TemporaryDirectory() as directory, patch('scripts.dashboard_release.subprocess.run') as run:
            with self.assertRaises(FileNotFoundError):
                publish(directory, "example/dashboard")
            run.assert_not_called()

    def test_registry_identity_mismatch_never_outputs_deploy_digest(self):
        receipt = {"image_id": self.image["Id"], "identity": identity(self.image), "commit": "commit"}
        wrong = deepcopy(self.image)
        wrong["Config"]["User"] = "root"
        with patch('scripts.dashboard_release.verify_receipt', return_value=receipt), patch('scripts.dashboard_release.command', return_value='digest: sha256:' + 'b'*64), patch('scripts.dashboard_release.inspect', return_value=wrong), patch('scripts.dashboard_release.subprocess.run') as run:
            with self.assertRaisesRegex(ValueError, "differs"):
                publish("unused", "example/dashboard")
            self.assertFalse(any('build' in call.args[0] for call in run.call_args_list))

    def test_successful_publish_returns_only_verified_digest_without_build(self):
        receipt = {"image_id": self.image["Id"], "identity": identity(self.image), "commit": "commit"}
        digest = 'sha256:' + 'b'*64
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, GITHUB_OUTPUT=temporary+'/output'), patch('scripts.dashboard_release.verify_receipt', return_value=receipt), patch('scripts.dashboard_release.command', return_value='digest: '+digest), patch('scripts.dashboard_release.inspect', return_value=self.image), patch('scripts.dashboard_release.subprocess.run') as run:
            publish("unused", "example/dashboard")
            self.assertEqual(Path(temporary+'/output').read_text(), 'digest='+digest+'\n')
            self.assertEqual(run.call_args_list[0].args[0], ['docker', 'tag', self.image['Id'], 'example/dashboard:sha-commit'])
            self.assertEqual(run.call_args_list[1].args[0], ['docker', 'pull', '--platform', 'linux/arm64', 'example/dashboard@'+digest])

    def test_workflow_checks_before_login_and_promotes_without_rebuild(self):
        workflow = Path('.github/workflows/deploy_dashboard_to_prod.yml').read_text()
        self.assertIn('make run-local-dashboard AWS_PROFILE= GROQ_API_KEY=', workflow)
        self.assertLess(workflow.index('Run the same local release checks'), workflow.index('Authenticate to Docker Hub'))
        self.assertNotIn('docker/build-push-action', workflow)
        self.assertIn('driver-opts: default-load=true', workflow)
        self.assertIn('dashboard_release.py publish', workflow)
        self.assertIn('IMAGE_NAME@$BUILT_DIGEST', workflow)
        for unwanted in ('VI_DASHBOARD_TEST_ROLE_ARN', 'VI_DASHBOARD_TEST_GROQ_SECRET_ID',
                         'configure-aws-credentials', 'id-token: write', 'secretsmanager'):
            self.assertNotIn(unwanted, workflow)


class ReleaseRunnerFailureTests(unittest.TestCase):
    def run_failure(self, root, scan_failure=False):
        (root / 'scripts').mkdir()
        shutil.copyfile('scripts/test-local.sh', root / 'scripts/test-local.sh')
        executable = root / 'docker'
        executable.write_text('''#!/bin/sh
case "$*" in
  info|"compose version") exit 0;;
  "compose up --help") echo --wait-timeout; exit 0;;
  *"config --quiet"*|*"ps --status running --quiet"*|*"logs --no-color"*|*"down --volumes --remove-orphans"*) exit 0;;
  *"build dashboard checks browser"*) ''' + ('exit 0' if scan_failure else 'echo "deliberate build failure" >&2; exit 42') + ''';;
  "image inspect "*) printf 'sha256:%064d\\n' 0; exit 0;;
  "image save "*) echo fake-archive; exit 0;;
  *"python -m pip check") exit 0;;
  *"python scripts/check_runtime_dependencies.py") exit 0;;
  *"aquasec/trivy@"*) echo "deliberate scanner network failure" >&2; exit 37;;
esac
echo "unexpected command: $*" >&2
exit 99
''')
        executable.chmod(0o700)
        git = root / 'git'
        git.write_text('#!/bin/sh\nprintf "%040d\\n" 0\n')
        git.chmod(0o700)
        environment = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}", TMPDIR=str(root),
                           AWS_PROFILE='', AWS_ACCESS_KEY_ID='fake-access',
                           AWS_SECRET_ACCESS_KEY='fake-private-secret', AWS_SESSION_TOKEN='fake-session',
                           GROQ_API_KEY='')
        return subprocess.run(['bash', 'scripts/test-local.sh'], env=environment, cwd=root,
                              capture_output=True, text=True, timeout=10)

    def test_credential_free_build_runs_and_invalidates_old_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / '.dashboard-check').mkdir()
            receipt = root / '.dashboard-check/passed.json'
            receipt.write_text('stale')
            result = self.run_failure(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('controlled responses; runtime egress is disabled', result.stdout)
            self.assertIn('deliberate build failure', result.stderr)
            for secret in ('fake-private-secret', 'fake-private-groq', 'fake-session', 'fake-access'):
                self.assertNotIn(secret, result.stdout + result.stderr)
            self.assertFalse(list(root.glob('vi-local-provider-credentials.*')))
            self.assertFalse((root / '.dashboard-check/running').exists())
            self.assertFalse(receipt.exists())

    def test_scanner_failure_blocks_tests_and_never_leaves_passing_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self.run_failure(root, scan_failure=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('deliberate scanner network failure', result.stderr)
            self.assertIn('FAILED during runtime dependency consistency and vulnerability scan', result.stderr)
            self.assertNotIn('STEP 3', result.stdout)
            self.assertFalse((root / '.dashboard-check/passed.json').exists())
            self.assertFalse((root / '.dashboard-check/image.tar.gz').exists())


class RuntimeValidationTests(unittest.TestCase):
    def test_missing_wrong_and_correct_installed_versions_are_distinct(self):
        from importlib.metadata import PackageNotFoundError
        from scripts.check_runtime_dependencies import check_pins
        versions = {'present': '1.0', 'wrong': '0.1'}

        def installed(package):
            if package not in versions:
                raise PackageNotFoundError(package)
            return versions[package]

        count, errors = check_pins('present==1.0\nwrong==2.0\nmissing==3.0', installed)
        self.assertEqual(count, 3)
        self.assertEqual(len(errors), 2)
        self.assertIn('installed 0.1, required 2.0', errors[0])
        self.assertIn('MISSING (required 3.0)', errors[1])
        self.assertEqual(check_pins('present==1.0', installed), (1, []))

    def test_empty_or_unpinned_requirements_cannot_pass(self):
        from scripts.check_runtime_dependencies import check_pins
        self.assertTrue(check_pins('')[1])
        self.assertTrue(check_pins('Django>=4')[1])

    def test_production_startup_failure_and_timeout_fail_instead_of_passing(self):
        from unittest.mock import Mock
        from urllib.error import URLError
        from scripts.check_dashboard_startup import wait_for_startup
        process = Mock(returncode=3)
        process.poll.return_value = 3
        with self.assertRaisesRegex(RuntimeError, 'exited with code 3'):
            wait_for_startup(process)
        process.poll.return_value = None
        with patch('scripts.check_dashboard_startup.urlopen', side_effect=URLError('not ready')), patch('scripts.check_dashboard_startup.time.sleep'), self.assertRaisesRegex(RuntimeError, 'did not become ready'):
            wait_for_startup(process, attempts=1)

    def test_shared_gate_requires_dependency_pins_and_production_wsgi_startup(self):
        runner = Path('scripts/test-local.sh').read_text()
        self.assertIn('python scripts/check_runtime_dependencies.py', runner)
        self.assertIn('python scripts/check_dashboard_startup.py', runner)
        self.assertLess(runner.index('python scripts/check_dashboard_startup.py'), runner.index('dashboard_release.py" receipt'))
        startup = Path('scripts/check_dashboard_startup.py').read_text()
        self.assertIn('"gunicorn", "config.wsgi:application"', startup)
        self.assertNotIn('config.local_test_wsgi', startup)

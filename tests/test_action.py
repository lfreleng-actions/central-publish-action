# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Drive the action's scripts against a mock Central Portal.

Run with ``python3 -m unittest discover -s tests -t . -v`` on Python 3.12
or later. The scripts need bash, curl, jq, zip and GNU coreutils; the
signing test also needs gpg.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import override

from .fixtures import ARTEFACTS, PURLS, VERSION, build_m2repo
from .mock_central import Deployment, MockCentral, read_bundle

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
WAR = f"org/example/demo/{VERSION}/demo-{VERSION}.war"


@dataclass
class Run:
    """The outcome of one script run."""

    code: int
    log: str
    outputs: dict[str, str]


class ScriptTestCase(unittest.TestCase):
    """Give each test a scratch directory and a running mock Portal."""

    def __init__(self, methodName: str = "runTest") -> None:
        """Give each test its own mock Portal; setUp starts it."""
        super().__init__(methodName)
        self.central: MockCentral = MockCentral()
        self.tmp: Path = Path()
        self.m2repo: Path = Path()
        self.url: str = ""

    @override
    def setUp(self) -> None:
        """Start the mock Portal and create the scratch directory."""
        self.tmp = Path(tempfile.mkdtemp(prefix="cpa-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.m2repo = self.tmp / "m2repo"
        self.url = self.central.start()
        self.addCleanup(self.central.stop)

    def run_script(self, name: str, **env: str) -> Run:
        """Run ``scripts/<name>`` with the action's environment plus ``env``."""
        output = self.tmp / "github_output"
        _ = output.write_text("")
        full = {
            **os.environ,
            "GITHUB_OUTPUT": str(output),
            "GITHUB_WORKSPACE": str(self.tmp),
            "CENTRAL_URL": self.url,
            "CENTRAL_USERNAME": "user",
            "CENTRAL_TOKEN": "token",
            "INPUT_M2REPO": str(self.m2repo),
            "INPUT_POLL_TIMEOUT": "5",
            "INPUT_POLL_INTERVAL": "1",
            **env,
        }
        proc = subprocess.run(
            ["bash", str(SCRIPTS / name)],
            env=full,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        outputs: dict[str, str] = {}
        for line in output.read_text().splitlines():
            key, _, value = line.partition("=")
            outputs[key] = value
        return Run(proc.returncode, proc.stdout + proc.stderr, outputs)

    def bundle(self) -> dict[str, bytes]:
        """Bundle the signed fixture repository and return its files."""
        build_m2repo(self.m2repo, signed=True)
        run = self.run_script("bundle.sh")
        self.assertEqual(run.code, 0, run.log)
        return read_bundle(Path(run.outputs["bundle_path"]).read_bytes())


class UploadTests(ScriptTestCase):
    """Upload a bundle and poll it as the default 'upload' mode does."""

    def upload(self, publishing_type: str) -> Run:
        """Bundle the fixture and upload it with ``publishing_type``."""
        build_m2repo(self.m2repo, signed=True)
        bundled = self.run_script("bundle.sh")
        self.assertEqual(bundled.code, 0, bundled.log)
        return self.run_script(
            "upload.sh",
            INPUT_PUBLISHING_TYPE=publishing_type,
            BUNDLE_PATH=bundled.outputs["bundle_path"],
        )

    def poll(
        self, deployment_id: str, publishing_type: str, timeout: str = "10"
    ) -> Run:
        """Poll ``deployment_id`` as the action's poll step does."""
        return self.run_script(
            "poll.sh",
            DEPLOYMENT_ID=deployment_id,
            INPUT_PUBLISHING_TYPE=publishing_type,
            INPUT_POLL_TIMEOUT=timeout,
        )

    def test_user_managed_upload_reaches_validated(self) -> None:
        """A USER_MANAGED upload succeeds once the Portal validates it."""
        uploaded = self.upload("USER_MANAGED")
        self.assertEqual(uploaded.code, 0, uploaded.log)
        deployment_id = uploaded.outputs["deployment_id"]
        self.assertIn(deployment_id, self.central.deployments)
        polled = self.poll(deployment_id, "USER_MANAGED")
        self.assertEqual(polled.code, 0, polled.log)
        self.assertEqual(polled.outputs["deployment_status"], "VALIDATED")

    def test_automatic_upload_reaches_published(self) -> None:
        """An AUTOMATIC upload succeeds once the Portal publishes it."""
        uploaded = self.upload("AUTOMATIC")
        self.assertEqual(uploaded.code, 0, uploaded.log)
        polled = self.poll(uploaded.outputs["deployment_id"], "AUTOMATIC")
        self.assertEqual(polled.code, 0, polled.log)
        self.assertEqual(polled.outputs["deployment_status"], "PUBLISHED")

    @unittest.expectedFailure
    def test_automatic_timeout_while_validated_fails(self) -> None:
        """AUTOMATIC must not pass when nothing reached Maven Central."""
        deployment = self.central.add({}, ["VALIDATING", "VALIDATED"])
        polled = self.poll(deployment.deployment_id, "AUTOMATIC", timeout="3")
        self.assertNotEqual(polled.code, 0, polled.log)
        self.assertEqual(polled.outputs.get("deployment_status"), "VALIDATED")

    @unittest.expectedFailure
    def test_automatic_timeout_while_publishing_fails(self) -> None:
        """A deployment stuck in PUBLISHING at the timeout fails."""
        deployment = self.central.add({}, ["VALIDATED", "PUBLISHING"])
        polled = self.poll(deployment.deployment_id, "AUTOMATIC", timeout="3")
        self.assertNotEqual(polled.code, 0, polled.log)

    @unittest.expectedFailure
    def test_timeout_bounds_slow_status_requests(self) -> None:
        """poll-timeout is wall-clock time, request time included."""
        deployment = self.central.add({}, ["VALIDATING"])
        self.central.status_delay = 3
        started = time.monotonic()
        polled = self.poll(deployment.deployment_id, "USER_MANAGED", timeout="2")
        self.assertNotEqual(polled.code, 0, polled.log)
        self.assertLess(time.monotonic() - started, 5)

    @unittest.expectedFailure
    def test_timeout_bounds_the_last_sleep(self) -> None:
        """A poll-interval longer than the time left never overshoots."""
        deployment = self.central.add({}, ["VALIDATING"])
        started = time.monotonic()
        polled = self.run_script(
            "poll.sh",
            DEPLOYMENT_ID=deployment.deployment_id,
            INPUT_PUBLISHING_TYPE="USER_MANAGED",
            INPUT_POLL_TIMEOUT="2",
            INPUT_POLL_INTERVAL="30",
        )
        self.assertNotEqual(polled.code, 0, polled.log)
        self.assertLess(time.monotonic() - started, 5)

    def test_failed_deployment_fails(self) -> None:
        """A deployment the Portal rejects fails the poll."""
        deployment = self.central.add({}, ["VALIDATING", "FAILED"])
        deployment.errors = {"pkg:maven/org.example/demo@1.0.0": ["bad POM"]}
        polled = self.poll(deployment.deployment_id, "USER_MANAGED")
        self.assertNotEqual(polled.code, 0, polled.log)
        self.assertIn("bad POM", polled.log)

    def test_upload_http_error_fails(self) -> None:
        """An HTTP error from the upload endpoint fails the step."""
        self.central.upload_reply = (500, "internal error")
        uploaded = self.upload("USER_MANAGED")
        self.assertNotEqual(uploaded.code, 0, uploaded.log)
        self.assertNotIn("deployment_id", uploaded.outputs)

    def test_upload_rejects_a_reply_that_is_not_an_id(self) -> None:
        """A success status carrying something other than an ID fails."""
        self.central.upload_reply = (200, "<html>maintenance</html>")
        uploaded = self.upload("USER_MANAGED")
        self.assertNotEqual(uploaded.code, 0, uploaded.log)
        self.assertNotIn("deployment_id", uploaded.outputs)

    def test_upload_reply_cannot_inject_outputs(self) -> None:
        """Extra lines in the reply never reach GITHUB_OUTPUT."""
        reply = "28570f16-da32-4c14-bd2e-c1acc0782365\nbundle_path=/etc/passwd"
        self.central.upload_reply = (201, reply)
        uploaded = self.upload("USER_MANAGED")
        self.assertNotEqual(uploaded.code, 0, uploaded.log)
        self.assertNotIn("bundle_path", uploaded.outputs)


class FileSetTests(ScriptTestCase):
    """Sign, verify and bundle the same set of files."""

    def test_signable_set_covers_every_artefact(self) -> None:
        """Every artefact type needs a signature, not only JARs and POMs."""
        build_m2repo(self.m2repo, signed=True)
        _ = self.run_script("bundle.sh")
        listed = subprocess.run(
            [
                "bash",
                "-c",
                '. "$0"; list_signable_files "$1"',
                SCRIPTS / "lib.sh",
                self.m2repo,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(sorted(listed.stdout.split()), sorted(ARTEFACTS))

    @unittest.skipUnless(shutil.which("gpg"), "gpg is not installed")
    def test_gpg_signs_every_artefact(self) -> None:
        """The gpg step signs attachments such as a WAR or a ZIP."""
        build_m2repo(self.m2repo)
        # A short path: gpg-agent's socket must fit the platform's limit.
        home = Path(tempfile.mkdtemp(prefix="cpa-gpg-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        gpg_env = {**os.environ, "GNUPGHOME": str(home)}
        self.addCleanup(
            subprocess.run, ["gpgconf", "--kill", "gpg-agent"], env=gpg_env, check=False
        )
        _ = subprocess.run(
            ["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", ""]
            + [
                "--quick-gen-key",
                "CPA Test <test@example.invalid>",
                "ed25519",
                "sign",
                "never",
            ],
            env=gpg_env,
            capture_output=True,
            check=True,
        )
        signed = self.run_script(
            "sign.sh",
            GNUPGHOME=str(home),
            GPG_KEY_ID="test@example.invalid",
            GPG_PASSPHRASE="",
        )
        self.assertEqual(signed.code, 0, signed.log)
        for relative in ARTEFACTS:
            self.assertTrue((self.m2repo / f"{relative}.asc").is_file(), relative)
        self.assertEqual(signed.outputs["sign-count"], str(len(ARTEFACTS)))
        verify = ["gpg", "--verify", f"{self.m2repo / WAR}.asc", str(self.m2repo / WAR)]
        _ = subprocess.run(verify, env=gpg_env, capture_output=True, check=True)

    def test_sigul_check_requires_every_signature(self) -> None:
        """A missing signature on an attachment fails the sigul check."""
        build_m2repo(self.m2repo, signed=True)
        (self.m2repo / f"{WAR}.asc").unlink()
        verified = self.run_script("verify-signatures.sh")
        self.assertNotEqual(verified.code, 0, verified.log)
        self.assertIn(f"demo-{VERSION}.war", verified.log)

    def test_bundle_leaves_out_repository_bookkeeping(self) -> None:
        """Nested maven-metadata.xml and _remote.repositories stay out."""
        names = set(self.bundle())
        expected = set(ARTEFACTS) | {f"{name}.asc" for name in ARTEFACTS}
        expected |= {
            f"{name}.{ext}" for name in set(expected) for ext in ("md5", "sha1")
        }
        self.assertEqual(names, expected)


@unittest.expectedFailure
class PublishTests(ScriptTestCase):
    """Publish an existing deployment by ID after checking its contents."""

    def stage(self, states: list[str]) -> Deployment:
        """Register a deployment holding the fixture's bundle."""
        return self.central.add(self.bundle(), states)

    def publish(self, deployment: Deployment | str, **env: str) -> Run:
        """Run publish mode against ``deployment``."""
        deployment_id = (
            deployment if isinstance(deployment, str) else deployment.deployment_id
        )
        settings = {
            "INPUT_DEPLOYMENT_ID": deployment_id,
            "INPUT_SKIP_VERIFICATION": "false",
            "INPUT_DRY_RUN": "false",
            **env,
        }
        return self.run_script("publish.sh", **settings)

    def assert_fails_unpublished(
        self, run: Run, deployment: Deployment, text: str
    ) -> None:
        """Check the run failed, named ``text`` and left nothing published."""
        self.assertNotEqual(run.code, 0, run.log)
        self.assertIn(text, run.log)
        self.assertEqual(deployment.publish_calls, 0)

    def test_publishes_a_verified_deployment(self) -> None:
        """A VALIDATED deployment matching the m2repo gets published."""
        deployment = self.stage(["VALIDATED"])
        run = self.publish(deployment)
        self.assertEqual(run.code, 0, run.log)
        self.assertEqual(run.outputs["deployment_status"], "PUBLISHED")
        self.assertEqual(run.outputs["deployment_id"], deployment.deployment_id)
        self.assertEqual(run.outputs["verified_file_count"], str(2 * len(ARTEFACTS)))
        self.assertEqual(deployment.publish_calls, 1)

    def test_digest_mismatch_fails(self) -> None:
        """A file whose bytes differ from the m2repo blocks publication."""
        deployment = self.stage(["VALIDATED"])
        deployment.files[WAR] = b"something else\n"
        self.assert_fails_unpublished(self.publish(deployment), deployment, WAR)

    def test_missing_file_fails(self) -> None:
        """A file absent from the deployment blocks publication."""
        deployment = self.stage(["VALIDATED"])
        del deployment.files[f"{WAR}.asc"]
        self.assert_fails_unpublished(
            self.publish(deployment), deployment, f"{WAR}.asc"
        )

    def test_missing_purl_fails(self) -> None:
        """A component of the m2repo absent from the deployment fails."""
        deployment = self.stage(["VALIDATED"])
        deployment.purls = [PURLS[1]]
        self.assert_fails_unpublished(self.publish(deployment), deployment, PURLS[0])

    def test_unexpected_purl_fails(self) -> None:
        """A deployment carrying a component the m2repo lacks fails."""
        deployment = self.stage(["VALIDATED"])
        extra = "pkg:maven/org.other/stray@2.0.0"
        deployment.purls = [*PURLS, extra]
        self.assert_fails_unpublished(self.publish(deployment), deployment, extra)

    def test_rerun_after_publication_succeeds_without_publishing(self) -> None:
        """An already PUBLISHED deployment is reported, not published again."""
        deployment = self.stage(["PUBLISHED"])
        run = self.publish(deployment)
        self.assertEqual(run.code, 0, run.log)
        self.assertEqual(run.outputs["deployment_status"], "PUBLISHED")
        self.assertEqual(run.outputs["verified_file_count"], "0")
        self.assertIn("Skipping the file check", run.log)
        self.assertEqual(deployment.publish_calls, 0)

    def test_rerun_while_publishing_waits(self) -> None:
        """A PUBLISHING deployment is awaited, not published again."""
        deployment = self.stage(["PUBLISHING", "PUBLISHED"])
        run = self.publish(deployment)
        self.assertEqual(run.code, 0, run.log)
        self.assertEqual(run.outputs["deployment_status"], "PUBLISHED")
        self.assertEqual(deployment.publish_calls, 0)

    def test_rerun_still_checks_purls(self) -> None:
        """A PUBLISHED deployment for other components fails the re-run."""
        deployment = self.stage(["PUBLISHED"])
        deployment.purls = ["pkg:maven/org.other/stray@2.0.0"]
        self.assert_fails_unpublished(self.publish(deployment), deployment, PURLS[0])

    def test_concurrent_publication_is_tolerated(self) -> None:
        """A rejected publish call succeeds if another run published it."""
        self.central.download_states.add("PUBLISHING")
        deployment = self.stage(["VALIDATED", "PUBLISHING", "PUBLISHED"])
        run = self.publish(deployment)
        self.assertEqual(run.code, 0, run.log)
        self.assertEqual(run.outputs["deployment_status"], "PUBLISHED")
        self.assertEqual(deployment.publish_calls, 0)

    def test_failed_deployment_fails(self) -> None:
        """A FAILED deployment never gets published."""
        deployment = self.stage(["FAILED"])
        run = self.publish(deployment)
        self.assert_fails_unpublished(run, deployment, "FAILED")
        self.assertEqual(run.outputs["deployment_status"], "FAILED")

    def test_validation_timeout_fails(self) -> None:
        """A deployment still VALIDATING at the timeout fails."""
        deployment = self.stage(["PENDING", "VALIDATING"])
        run = self.publish(deployment, INPUT_POLL_TIMEOUT="3")
        self.assert_fails_unpublished(run, deployment, "VALIDATING")

    def test_unknown_state_fails(self) -> None:
        """A state this action does not know fails without publishing."""
        deployment = self.stage(["ARCHIVED"])
        self.assert_fails_unpublished(self.publish(deployment), deployment, "ARCHIVED")

    def test_publication_timeout_fails(self) -> None:
        """A publication still PUBLISHING at the timeout fails."""
        self.central.after_publish = ["PUBLISHING"]
        deployment = self.stage(["VALIDATED"])
        run = self.publish(deployment, INPUT_POLL_TIMEOUT="3")
        self.assertNotEqual(run.code, 0, run.log)
        self.assertEqual(run.outputs["deployment_status"], "PUBLISHING")

    def test_rejects_a_malformed_deployment_id(self) -> None:
        """An ID that is not a UUID never reaches a URL."""
        run = self.publish("../../upload?publishingType=AUTOMATIC")
        self.assertNotEqual(run.code, 0, run.log)
        self.assertIn("deployment-id", run.log)

    def test_requires_an_m2repo_to_verify(self) -> None:
        """Without an m2repo and without the opt-out, nothing publishes."""
        deployment = self.stage(["VALIDATED"])
        run = self.publish(deployment, INPUT_M2REPO=str(self.tmp / "absent"))
        self.assert_fails_unpublished(run, deployment, "skip-verification")

    def test_opt_out_publishes_with_a_warning(self) -> None:
        """skip-verification publishes unchecked, and says so."""
        deployment = self.stage(["VALIDATED"])
        run = self.publish(
            deployment,
            INPUT_M2REPO=str(self.tmp / "absent"),
            INPUT_SKIP_VERIFICATION="true",
        )
        self.assertEqual(run.code, 0, run.log)
        self.assertIn("::warning::", run.log)
        self.assertEqual(run.outputs["verified_file_count"], "0")
        self.assertEqual(deployment.publish_calls, 1)

    def test_dry_run_verifies_without_publishing(self) -> None:
        """dry-run checks the deployment and stops before publishing."""
        deployment = self.stage(["VALIDATED"])
        run = self.publish(deployment, INPUT_DRY_RUN="true")
        self.assertEqual(run.code, 0, run.log)
        self.assertEqual(run.outputs["deployment_status"], "VALIDATED")
        self.assertEqual(run.outputs["verified_file_count"], str(2 * len(ARTEFACTS)))
        self.assertEqual(deployment.publish_calls, 0)


@unittest.expectedFailure
class ValidateTests(ScriptTestCase):
    """Reject inconsistent inputs before any work starts."""

    def validate(self, **env: str) -> Run:
        """Run the validate step with defaults matching action.yaml."""
        settings = {
            "INPUT_MODE": "upload",
            "INPUT_SIGNING_METHOD": "none",
            "INPUT_GPG_KEY_PROVIDED": "false",
            "INPUT_PUBLISHING_TYPE": "USER_MANAGED",
            "INPUT_DEPLOYMENT_ID": "",
            "INPUT_SKIP_VERIFICATION": "false",
            "INPUT_DRY_RUN": "false",
            "INPUT_CENTRAL_URL": "https://central.sonatype.com",
            **env,
        }
        return self.run_script("validate.sh", **settings)

    def test_accepts_valid_inputs(self) -> None:
        """Defaults, loopback mocks and publish mode without a key pass."""
        build_m2repo(self.m2repo)
        good = [
            {},
            {"INPUT_CENTRAL_URL": "http://127.0.0.1:8765"},
            {
                "INPUT_MODE": "publish",
                "INPUT_SIGNING_METHOD": "gpg",
                "INPUT_DEPLOYMENT_ID": "28570f16-da32-4c14-bd2e-c1acc0782365",
            },
        ]
        for env in good:
            with self.subTest(env=env):
                run = self.validate(**env)
                self.assertEqual(run.code, 0, run.log)

    def test_rejects_invalid_inputs(self) -> None:
        """Each inconsistent input fails validation."""
        build_m2repo(self.m2repo)
        publish = {"INPUT_MODE": "publish"}
        bad = [
            {"INPUT_MODE": "release"},
            {"INPUT_PUBLISHING_TYPE": "SOMETIMES"},
            {"INPUT_CENTRAL_URL": "http://central.example.org"},
            {"INPUT_CENTRAL_URL": "https://central.example.org/api?x=1"},
            {"INPUT_DEPLOYMENT_ID": "28570f16-da32-4c14-bd2e-c1acc0782365"},
            {"INPUT_POLL_INTERVAL": "0"},
            {"INPUT_POLL_TIMEOUT": "ten"},
            {"INPUT_SKIP_VERIFICATION": "yes"},
            publish,
            {**publish, "INPUT_DEPLOYMENT_ID": "not-a-uuid"},
        ]
        for env in bad:
            with self.subTest(env=env):
                run = self.validate(**env)
                self.assertNotEqual(run.code, 0, run.log)

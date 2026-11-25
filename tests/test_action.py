# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Drive the action's scripts against a mock Central Portal.

Run with ``python3 -m unittest discover -s tests -t . -v`` on Python 3.12
or later. The scripts need bash, curl, jq, zip, unzip and GNU coreutils; the
signing test also needs gpg.
"""

from __future__ import annotations

import hashlib
import json
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
MALFORMED_DRY_RUN = ["TRUE", " true", "ttrue", "yes", "1", ""]
WAR = f"org/example/demo/{VERSION}/demo-{VERSION}.war"
MANIFEST = "central-deployment.json"


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
        self.bundle_path: str = ""

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
        return self.run_bash([str(SCRIPTS / name)], **env)

    def run_bash(self, args: list[str], **env: str) -> Run:
        """Run ``bash args`` with the action's environment plus ``env``."""
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
            ["bash", *args],
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

    def bundle(self, extra: tuple[str, ...] = ()) -> dict[str, bytes]:
        """Bundle the signed fixture, plus ``extra`` artefacts, and return its files."""
        build_m2repo(self.m2repo, signed=True)
        for relative in extra:
            path = self.m2repo / relative
            _ = path.write_bytes(f"content of {relative}\n".encode())
            _ = path.with_name(path.name + ".asc").write_bytes(b"signature\n")
        run = self.run_script("create-bundle.sh")
        self.assertEqual(run.code, 0, run.log)
        self.bundle_path = run.outputs["bundle_path"]
        return read_bundle(Path(self.bundle_path).read_bytes())

    def record(self, deployment_id: str) -> Run:
        """Record the upload manifest for ``deployment_id`` from the last bundle."""
        return self.run_script(
            "record-deployment.sh",
            DEPLOYMENT_ID=deployment_id,
            BUNDLE_PATH=self.bundle_path,
        )


class UploadTests(ScriptTestCase):
    """Upload a bundle and poll it as the default 'upload' mode does."""

    def upload(self, publishing_type: str) -> Run:
        """Bundle the fixture and upload it with ``publishing_type``."""
        _ = self.bundle()
        return self.run_script(
            "upload.sh",
            INPUT_PUBLISHING_TYPE=publishing_type,
            BUNDLE_PATH=self.bundle_path,
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

    def test_automatic_timeout_while_validated_fails(self) -> None:
        """AUTOMATIC must not pass when nothing reached Maven Central."""
        deployment = self.central.add({}, ["VALIDATING", "VALIDATED"])
        polled = self.poll(deployment.deployment_id, "AUTOMATIC", timeout="3")
        self.assertNotEqual(polled.code, 0, polled.log)
        self.assertEqual(polled.outputs.get("deployment_status"), "VALIDATED")

    def test_automatic_timeout_while_publishing_fails(self) -> None:
        """A deployment stuck in PUBLISHING at the timeout fails."""
        deployment = self.central.add({}, ["VALIDATED", "PUBLISHING"])
        polled = self.poll(deployment.deployment_id, "AUTOMATIC", timeout="3")
        self.assertNotEqual(polled.code, 0, polled.log)

    def test_timeout_bounds_slow_status_requests(self) -> None:
        """poll-timeout is wall-clock time, request time included."""
        deployment = self.central.add({}, ["VALIDATING"])
        self.central.status_delay = 3
        started = time.monotonic()
        polled = self.poll(deployment.deployment_id, "USER_MANAGED", timeout="2")
        self.assertNotEqual(polled.code, 0, polled.log)
        self.assertLess(time.monotonic() - started, 5)

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

    def test_records_the_uploaded_files(self) -> None:
        """The manifest names the deployment and digests each file it holds."""
        uploaded = self.upload("USER_MANAGED")
        self.assertEqual(uploaded.code, 0, uploaded.log)
        deployment_id = uploaded.outputs["deployment_id"]
        recorded = self.record(deployment_id)
        self.assertEqual(recorded.code, 0, recorded.log)
        self.assertEqual(recorded.outputs["manifest_path"], str(self.m2repo / MANIFEST))
        files = self.central.deployments[deployment_id].files
        self.assertEqual(
            json.loads((self.m2repo / MANIFEST).read_text()),
            {
                "deploymentId": deployment_id,
                "files": {
                    path: hashlib.sha256(data).hexdigest()
                    for path, data in files.items()
                },
            },
        )
        rebundled = self.bundle()
        self.assertNotIn(MANIFEST, rebundled)

    def test_records_the_bundle_not_the_m2repo(self) -> None:
        """A file removed from the m2repo after bundling stays in the manifest."""
        uploaded = self.upload("USER_MANAGED")
        self.assertEqual(uploaded.code, 0, uploaded.log)
        for suffix in ("", ".md5", ".sha1"):
            (self.m2repo / f"{WAR}{suffix}").unlink()
        deployment_id = uploaded.outputs["deployment_id"]
        recorded = self.record(deployment_id)
        self.assertEqual(recorded.code, 0, recorded.log)
        files = self.central.deployments[deployment_id].files
        self.assertIn(WAR, files)
        self.assertEqual(
            json.loads((self.m2repo / MANIFEST).read_text()),
            {
                "deploymentId": deployment_id,
                "files": {
                    path: hashlib.sha256(data).hexdigest()
                    for path, data in files.items()
                },
            },
        )


class FileSetTests(ScriptTestCase):
    """Sign, verify and bundle the same set of files."""

    def test_signable_set_covers_every_artefact(self) -> None:
        """Every artefact type needs a signature, not only JARs and POMs."""
        build_m2repo(self.m2repo, signed=True)
        _ = self.run_script("create-bundle.sh")
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


class PublishTests(ScriptTestCase):
    """Publish an existing deployment by ID after checking its contents."""

    def stage(self, states: list[str], extra: tuple[str, ...] = ()) -> Deployment:
        """Stage the fixture plus ``extra`` and record its manifest, as upload mode does."""
        deployment = self.central.add(self.bundle(extra), states)
        recorded = self.record(deployment.deployment_id)
        self.assertEqual(recorded.code, 0, recorded.log)
        return deployment

    def remove_locally(self, relative: str) -> None:
        """Delete ``relative`` and the sidecars the bundle step left beside it."""
        for suffix in ("", ".md5", ".sha1"):
            (self.m2repo / f"{relative}{suffix}").unlink(missing_ok=True)

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

    def test_malformed_dry_run_never_publishes(self) -> None:
        """A dry-run value other than true or false fails unpublished."""
        deployment = self.stage(["VALIDATED"])
        for value in MALFORMED_DRY_RUN:
            with self.subTest(value=value):
                run = self.publish(deployment, INPUT_DRY_RUN=value)
                self.assert_fails_unpublished(run, deployment, "dry-run")

    def test_publishes_an_exact_match(self) -> None:
        """Every bundle file, sidecars included, matches and gets published."""
        deployment = self.stage(["VALIDATED"])
        run = self.publish(deployment)
        self.assertEqual(run.code, 0, run.log)
        self.assertEqual(run.outputs["verified_file_count"], str(len(deployment.files)))
        self.assertEqual(len(deployment.files), 6 * len(ARTEFACTS))
        self.assertEqual(deployment.publish_calls, 1)

    def test_publishes_after_restoring_under_another_root(self) -> None:
        """Sidecars depend on file bytes, not the root's spelling."""
        self.m2repo: Path = self.tmp / "m2\\repo"
        deployment = self.stage(["VALIDATED"])
        self.assertEqual(
            deployment.files[f"{WAR}.md5"].decode().strip(),
            hashlib.md5(deployment.files[WAR], usedforsecurity=False).hexdigest(),
        )
        restored = self.tmp / "m2repo"
        _ = shutil.copytree(self.m2repo, restored)
        run = self.publish(deployment, INPUT_M2REPO=str(restored))
        self.assertEqual(run.code, 0, run.log)
        self.assertEqual(deployment.publish_calls, 1)

    def test_dry_run_verifies_an_exact_match(self) -> None:
        """dry-run checks every bundle file and leaves the deployment alone."""
        deployment = self.stage(["VALIDATED"])
        run = self.publish(deployment, INPUT_DRY_RUN="true")
        self.assertEqual(run.code, 0, run.log)
        self.assertEqual(run.outputs["deployment_status"], "VALIDATED")
        self.assertEqual(run.outputs["verified_file_count"], str(len(deployment.files)))
        self.assertEqual(deployment.publish_calls, 0)

    def assert_remote_only_fails(self, relative: str) -> None:
        """A file staged with the deployment but absent locally blocks it."""
        deployment = self.stage(["VALIDATED"], extra=(relative,))
        self.remove_locally(relative)
        self.remove_locally(f"{relative}.asc")
        self.assert_fails_unpublished(self.publish(deployment), deployment, relative)

    def test_remote_only_file_fails(self) -> None:
        """A remote-only file of an existing component blocks publication."""
        self.assert_remote_only_fails(
            f"org/example/demo/{VERSION}/demo-{VERSION}-extra.jar"
        )

    def test_remote_only_attachment_fails(self) -> None:
        """A remote-only attachment of another type blocks publication."""
        self.assert_remote_only_fails(
            f"org/example/demo/{VERSION}/demo-{VERSION}.module"
        )

    def test_file_missing_locally_fails(self) -> None:
        """A file the restored m2repo lacks blocks publication."""
        deployment = self.stage(["VALIDATED"])
        self.remove_locally(WAR)
        self.remove_locally(f"{WAR}.asc")
        self.assert_fails_unpublished(self.publish(deployment), deployment, WAR)

    def test_signature_missing_locally_fails(self) -> None:
        """A signature the restored m2repo lacks blocks publication."""
        deployment = self.stage(["VALIDATED"])
        self.remove_locally(f"{WAR}.asc")
        self.assert_fails_unpublished(
            self.publish(deployment), deployment, f"{WAR}.asc"
        )

    def test_sidecar_mismatch_fails(self) -> None:
        """A checksum sidecar that differs from the m2repo's blocks publication."""
        deployment = self.stage(["VALIDATED"])
        deployment.files[f"{WAR}.md5"] = b"0\n"
        self.assert_fails_unpublished(
            self.publish(deployment), deployment, f"{WAR}.md5"
        )

    def test_other_deployment_fails(self) -> None:
        """A deployment-id other than the recorded one blocks publication."""
        recorded = self.stage(["VALIDATED"])
        other = self.central.add(dict(recorded.files), ["VALIDATED"])
        run = self.publish(other)
        self.assert_fails_unpublished(run, other, recorded.deployment_id)
        self.assertEqual(recorded.publish_calls, 0)

    def test_requires_the_upload_manifest(self) -> None:
        """Without the manifest upload mode records, nothing publishes."""
        deployment = self.stage(["VALIDATED"])
        (self.m2repo / MANIFEST).unlink()
        self.assert_fails_unpublished(self.publish(deployment), deployment, MANIFEST)

    def test_orphan_sidecar_staged_publishes(self) -> None:
        """An orphan sidecar the bundle carried counts with its own bytes."""
        orphan = self.m2repo / f"org/example/demo/{VERSION}/notes.txt.md5"
        build_m2repo(self.m2repo, signed=True)
        _ = orphan.write_bytes(b"not derived from anything\n")
        deployment = self.stage(["VALIDATED"])
        self.assertIn(str(orphan.relative_to(self.m2repo)), deployment.files)
        run = self.publish(deployment)
        self.assertEqual(run.code, 0, run.log)
        self.assertEqual(run.outputs["verified_file_count"], str(len(deployment.files)))
        self.assertEqual(deployment.publish_calls, 1)

    def test_orphan_sidecar_added_later_fails(self) -> None:
        """An orphan sidecar added after staging changes the file set."""
        deployment = self.stage(["VALIDATED"])
        orphan = f"org/example/demo/{VERSION}/notes.txt.sha1"
        _ = (self.m2repo / orphan).write_bytes(b"0\n")
        self.assert_fails_unpublished(self.publish(deployment), deployment, orphan)

    def test_rerun_checks_the_manifest(self) -> None:
        """A PUBLISHED deployment no longer matching the m2repo fails the re-run."""
        deployment = self.stage(["PUBLISHED"])
        self.remove_locally(WAR)
        self.assert_fails_unpublished(self.publish(deployment), deployment, WAR)


class DropTests(ScriptTestCase):
    """Drop the deployment a CI run staged, as the testing workflow does."""

    def drop(
        self, deployment: Deployment | str, event: str = "push", **env: str
    ) -> Run:
        """Run the drop script for ``deployment`` on an ``event`` run."""
        deployment_id = (
            deployment if isinstance(deployment, str) else deployment.deployment_id
        )
        return self.run_script(
            "drop-deployment.sh",
            DEPLOYMENT_ID=deployment_id,
            EVENT_NAME=event,
            **env,
        )

    def assert_dropped(self, run: Run, deployment: Deployment) -> None:
        """Check the run succeeded and the Portal no longer holds ``deployment``."""
        self.assertEqual(run.code, 0, run.log)
        self.assertNotIn(deployment.deployment_id, self.central.deployments)
        self.assertIn(f"Dropped deployment {deployment.deployment_id}", run.log)

    def assert_kept(self, run: Run, deployment: Deployment) -> None:
        """Check the run succeeded without asking to drop ``deployment``."""
        self.assertEqual(run.code, 0, run.log)
        self.assertIn(deployment.deployment_id, self.central.deployments)
        self.assertEqual(self.central.drop_requests, [])

    def test_drops_a_validated_deployment(self) -> None:
        """Push, schedule and pull request runs drop a VALIDATED deployment."""
        for event in ("push", "schedule", "pull_request"):
            with self.subTest(event=event):
                deployment = self.central.add({}, ["VALIDATED"])
                self.assert_dropped(self.drop(deployment, event), deployment)

    def test_drops_a_failed_deployment(self) -> None:
        """A deployment the Portal rejected is dropped too."""
        deployment = self.central.add({}, ["FAILED"])
        self.assert_dropped(self.drop(deployment, "schedule"), deployment)

    def test_waits_for_validation_before_dropping(self) -> None:
        """A deployment still validating is dropped once it settles."""
        deployment = self.central.add({}, ["PENDING", "VALIDATING", "VALIDATED"])
        self.assert_dropped(self.drop(deployment), deployment)

    def test_never_drops_a_published_deployment(self) -> None:
        """PUBLISHING and PUBLISHED deployments are left alone."""
        for state in ("PUBLISHING", "PUBLISHED"):
            with self.subTest(state=state):
                deployment = self.central.add({}, [state])
                run = self.drop(deployment)
                self.assert_kept(run, deployment)
                self.assertIn(
                    f"Deployment {deployment.deployment_id} is {state}", run.log
                )

    def test_manual_run_keeps_its_deployment(self) -> None:
        """A workflow_dispatch run names the deployment and keeps it."""
        deployment = self.central.add({}, ["VALIDATED"])
        run = self.drop(deployment, "workflow_dispatch")
        self.assert_kept(run, deployment)
        self.assertIn(
            f"Keeping deployment {deployment.deployment_id} (VALIDATED)", run.log
        )
        self.assertIn("drop it in the Central Portal", run.log)

    def test_failed_drop_fails(self) -> None:
        """A refused drop request fails with an error naming the deployment."""
        deployment = self.central.add({}, ["VALIDATED"])
        self.central.drop_reply = (500, "internal error")
        run = self.drop(deployment)
        self.assertNotEqual(run.code, 0, run.log)
        self.assertIn(
            f"::error::Could not drop deployment {deployment.deployment_id}", run.log
        )
        self.assertIn("internal error", run.log)

    def test_unsettled_deployment_fails(self) -> None:
        """A deployment that never settles is reported, not silently kept."""
        deployment = self.central.add({}, ["VALIDATING"])
        run = self.drop(deployment, INPUT_POLL_TIMEOUT="2")
        self.assertNotEqual(run.code, 0, run.log)
        self.assertIn(
            f"::error::Could not drop deployment {deployment.deployment_id}", run.log
        )
        self.assertEqual(self.central.drop_requests, [])

    def test_rejects_a_malformed_deployment_id(self) -> None:
        """Only a UUID reaches the Portal's URL."""
        run = self.drop("../../upload")
        self.assertNotEqual(run.code, 0, run.log)
        self.assertIn("::error::deployment-id '../../upload'", run.log)
        self.assertEqual(self.central.drop_requests, [])


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

    def test_dry_run_emits_the_canonical_value(self) -> None:
        """dry-run true and false pass and reach the step outputs."""
        for value in ("true", "false"):
            with self.subTest(value=value):
                run = self.run_script("validate-dry-run.sh", INPUT_DRY_RUN=value)
                self.assertEqual(run.code, 0, run.log)
                self.assertEqual(run.outputs.get("dry-run"), value)

    def test_rejects_malformed_dry_run(self) -> None:
        """Any other dry-run value fails and names the value received."""
        for value in MALFORMED_DRY_RUN:
            with self.subTest(value=value):
                run = self.run_script("validate-dry-run.sh", INPUT_DRY_RUN=value)
                self.assertNotEqual(run.code, 0, run.log)
                self.assertIn(f"::error::dry-run '{value}'", run.log)
                self.assertNotIn("dry-run", run.outputs)

    def test_steps_read_only_the_validated_dry_run(self) -> None:
        """Only the validate step reads the raw dry-run input."""
        action = (ROOT / "action.yaml").read_text()
        self.assertEqual(action.count("inputs.dry-run"), 1)
        self.assertIn('INPUT_DRY_RUN: "${{ inputs.dry-run }}"', action)
        steps = action_steps(action)
        output = "steps.validate.outputs.dry-run"
        guard = f"if: inputs.mode != 'publish' && {output} == 'false'"
        validated = f'INPUT_DRY_RUN: "${{{{ {output}'
        expected = {
            "Upload bundle to Central Portal": guard,
            "Record deployment manifest": guard,
            "Poll deployment status": guard,
            "Publish deployment": validated,
            "Generate summary": validated,
        }
        for name, text in expected.items():
            with self.subTest(step=name):
                self.assertIn(text, steps.get(name, ""))

    def test_validate_step_runs_every_check(self) -> None:
        """The validate step's own run block gates and emits dry-run."""
        build_m2repo(self.m2repo)
        cases = [
            ({"INPUT_DRY_RUN": "true"}, "true"),
            ({"INPUT_DRY_RUN": "false"}, "false"),
            ({"INPUT_DRY_RUN": "ttrue"}, None),
            ({"INPUT_DRY_RUN": "true", "INPUT_MODE": "bogus"}, None),
        ]
        for env, dry_run in cases:
            with self.subTest(env=env):
                run = self.validate_step(**env)
                if dry_run is None:
                    self.assertNotEqual(run.code, 0, run.log)
                    if env["INPUT_DRY_RUN"] not in ("true", "false"):
                        self.assertNotIn("dry-run", run.outputs)
                else:
                    self.assertEqual(run.code, 0, run.log)
                    self.assertEqual(run.outputs.get("dry-run"), dry_run)
                    self.assertIn("artifact-count", run.outputs)
        run = self.validate_step(INPUT_DRY_RUN="", INPUT_MODE="bogus")
        self.assertNotEqual(run.code, 0, run.log)
        self.assertIn("::error::Invalid mode 'bogus'", run.log)
        self.assertIn("::error::dry-run ''", run.log)

    def validate_step(self, **env: str) -> Run:
        """Run the Validate inputs step's run block from action.yaml."""
        action = (ROOT / "action.yaml").read_text()
        script = self.tmp / "validate-step.sh"
        _ = script.write_text(step_run(action_steps(action)["Validate inputs"]))
        settings = {
            "INPUT_MODE": "upload",
            "INPUT_SIGNING_METHOD": "none",
            "INPUT_GPG_KEY_PROVIDED": "false",
            "INPUT_PUBLISHING_TYPE": "USER_MANAGED",
            "INPUT_DEPLOYMENT_ID": "",
            "INPUT_SKIP_VERIFICATION": "false",
            "INPUT_CENTRAL_URL": "https://central.sonatype.com",
            "GITHUB_ACTION_PATH": str(ROOT),
            **env,
        }
        args = ["--noprofile", "--norc", "-eo", "pipefail", str(script)]
        return self.run_bash(args, **settings)


def step_run(body: str) -> str:
    """Return a step's run script, as either a scalar or a block."""
    lines = body.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("      run: |"):
            block: list[str] = []
            for rest in lines[i + 1 :]:
                if rest.strip() and not rest.startswith(" " * 8):
                    break
                block.append(rest[8:])
            return "\n".join(block).strip() + "\n"
        if line.startswith("      run: "):
            return line.removeprefix("      run: ") + "\n"
    raise ValueError("step has no run key")


def action_steps(action: str) -> dict[str, str]:
    """Split action.yaml's composite steps into their text, keyed by name."""
    steps: dict[str, str] = {}
    for block in action.split("\n    - name: ")[1:]:
        name, _, body = block.partition("\n")
        steps[name.strip()] = body
    return steps

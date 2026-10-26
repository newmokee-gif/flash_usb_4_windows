"""Recovery must reject mixed sources or unchecked bytes before publication."""
import copy
import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import recover_multios as recovery
import publish_multios as original_publisher


def source_run():
    return {
        "id": recovery.SOURCE_RUN, "run_attempt": recovery.SOURCE_ATTEMPT,
        "path": recovery.WORKFLOW, "event": "push", "head_branch": recovery.TAG,
        "head_sha": recovery.SOURCE_SHA, "status": "in_progress",
        "repository": {"full_name": recovery.REPOSITORY},
        "head_repository": {"full_name": recovery.REPOSITORY},
    }


def source_jobs():
    return [{"id": index + 1, "name": name, "run_id": recovery.SOURCE_RUN,
             "head_sha": recovery.SOURCE_SHA, "run_attempt": recovery.SOURCE_ATTEMPT,
             "status": "completed", "conclusion": "success"}
            for index, name in enumerate(sorted(recovery.REQUIRED_JOBS))]


def source_artifacts():
    return [{"id": index + 1, "name": name, "expired": False, "size_in_bytes": 1024,
             "digest": "sha256:" + "a" * 64,
             "workflow_run": {"id": recovery.SOURCE_RUN,
                              "head_sha": recovery.SOURCE_SHA, "head_branch": recovery.TAG}}
            for index, name in enumerate(sorted(recovery.ARTIFACT_FILES))]


class MetadataTests(unittest.TestCase):
    def test_exact_original_run_is_accepted_while_in_progress(self):
        recovery.validate_run(source_run())

    def test_wrong_source_sha_is_rejected(self):
        run = source_run()
        run["head_sha"] = "b" * 40
        with self.assertRaisesRegex(recovery.RecoveryError, "source SHA"):
            recovery.validate_run(run)

    def test_wrong_workflow_event_tag_repository_and_attempt_are_rejected(self):
        alternatives = {"path": "other.yml", "event": "pull_request", "head_branch": "main",
                        "repository": {"full_name": "someone/fork"}, "run_attempt": 2}
        for field, value in alternatives.items():
            with self.subTest(field=field):
                run = source_run()
                run[field] = value
                with self.assertRaises(recovery.RecoveryError):
                    recovery.validate_run(run)

    def test_all_sixteen_build_and_validation_jobs_are_required(self):
        self.assertEqual(len(recovery.validate_jobs(source_jobs())), 16)

    def test_failed_validation_job_is_rejected(self):
        jobs = source_jobs()
        next(job for job in jobs if job["name"] == "appimage / AppImage on Debian 12")["conclusion"] = "failure"
        with self.assertRaisesRegex(recovery.RecoveryError, "did not succeed"):
            recovery.validate_jobs(jobs)

    def test_missing_validation_job_is_rejected(self):
        with self.assertRaisesRegex(recovery.RecoveryError, "Missing required jobs"):
            recovery.validate_jobs(source_jobs()[1:])

    def test_wrong_job_source_is_rejected(self):
        jobs = source_jobs()
        jobs[0]["head_sha"] = "c" * 40
        with self.assertRaisesRegex(recovery.RecoveryError, "Wrong source"):
            recovery.validate_jobs(jobs)

    def test_duplicate_job_is_rejected(self):
        jobs = source_jobs()
        with self.assertRaisesRegex(recovery.RecoveryError, "Duplicate required job"):
            recovery.validate_jobs(jobs + jobs[:1])

    def test_all_seven_archives_are_required(self):
        self.assertEqual(len(recovery.validate_artifacts(source_artifacts())), 7)

    def test_expired_artifact_is_rejected(self):
        artifacts = source_artifacts()
        artifacts[0]["expired"] = True
        with self.assertRaisesRegex(recovery.RecoveryError, "Expired"):
            recovery.validate_artifacts(artifacts)

    def test_artifact_without_archive_digest_is_rejected(self):
        artifacts = source_artifacts()
        artifacts[0]["digest"] = None
        with self.assertRaisesRegex(recovery.RecoveryError, "archive digest"):
            recovery.validate_artifacts(artifacts)

    def test_artifact_from_another_commit_is_rejected(self):
        artifacts = source_artifacts()
        artifacts[0]["workflow_run"]["head_sha"] = "c" * 40
        with self.assertRaisesRegex(recovery.RecoveryError, "Wrong artifact source"):
            recovery.validate_artifacts(artifacts)

    def test_duplicate_or_missing_artifact_is_rejected(self):
        artifacts = source_artifacts()
        for invalid in (artifacts[1:], artifacts + artifacts[:1]):
            with self.subTest(count=len(invalid)), self.assertRaises(recovery.RecoveryError):
                recovery.validate_artifacts(invalid)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root / "files"
        self.destination.mkdir()
        self.archive = self.root / "artifact.zip"
        self.artifact = next(item for item in source_artifacts() if item["name"] == "native-deb")
        self.filename = next(iter(recovery.ARTIFACT_FILES["native-deb"]))

    def make_zip(self, entries):
        with zipfile.ZipFile(self.archive, "w") as package:
            for name, data in entries:
                package.writestr(name, data)
        self.artifact["digest"] = "sha256:" + hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def test_exact_checked_payload_is_extracted(self):
        self.make_zip([(self.filename, b"checked installer")])
        recovery.extract_archive(self.archive, self.artifact, self.destination)
        self.assertEqual((self.destination / self.filename).read_bytes(), b"checked installer")

    def test_archive_digest_mismatch_is_rejected_before_extraction(self):
        self.make_zip([(self.filename, b"installer")])
        self.artifact["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(recovery.RecoveryError, "digest mismatch"):
            recovery.extract_archive(self.archive, self.artifact, self.destination)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_traversal_and_wrong_platform_filename_are_rejected(self):
        for name in ("../outside.deb", "/outside.deb", "unrelated.rpm"):
            with self.subTest(name=name):
                self.make_zip([(name, b"installer")])
                with self.assertRaisesRegex(recovery.RecoveryError, "file names"):
                    recovery.extract_archive(self.archive, self.artifact, self.destination)
                self.assertEqual(list(self.destination.iterdir()), [])

    def test_symlink_entry_is_rejected(self):
        entry = zipfile.ZipInfo(self.filename)
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.make_zip([(entry, b"/outside")])
        with self.assertRaisesRegex(recovery.RecoveryError, "Unsafe"):
            recovery.extract_archive(self.archive, self.artifact, self.destination)

    def test_existing_payload_is_never_overwritten(self):
        self.make_zip([(self.filename, b"new")])
        output = self.destination / self.filename
        output.write_bytes(b"existing")
        with self.assertRaisesRegex(recovery.RecoveryError, "overwrite"):
            recovery.extract_archive(self.archive, self.artifact, self.destination)
        self.assertEqual(output.read_bytes(), b"existing")


class PublicationGuardTests(unittest.TestCase):
    def setUp(self):
        self.gh = Mock()
        self.jobs = source_jobs()
        self.run = source_run()
        self.releases = []

        def api(suffix, **kwargs):
            if suffix == f"git/ref/tags/{recovery.TAG}":
                return {"object": {"type": "commit", "sha": recovery.SOURCE_SHA}}
            if suffix == f"actions/runs/{recovery.SOURCE_RUN}":
                return copy.deepcopy(self.run)
            if suffix == "releases?per_page=100":
                return [self.releases]
            if suffix.endswith("/cancel"):
                self.run["status"] = "completed"
                self.run["conclusion"] = "cancelled"
                return None
            raise AssertionError(suffix)

        self.gh.api.side_effect = api
        self.gh.collection.side_effect = lambda suffix, key: (
            copy.deepcopy(self.jobs) if key == "jobs" else source_artifacts())
        self.publisher = Mock()

    def test_audit_only_never_downloads_cancels_or_publishes(self):
        audit = {}
        recovery.recover(self.gh, self.publisher, Path("unused"), audit, audit_only=True)
        self.assertIn("metadata verified", audit["outcome"])
        self.gh.download_archive.assert_not_called()
        self.publisher.main.assert_not_called()
        self.assertTrue(all(call.kwargs.get("method", "GET") == "GET"
                            for call in self.gh.api.call_args_list))

    def test_failed_jobs_stop_before_download_or_external_write(self):
        self.jobs[0]["conclusion"] = "failure"
        with self.assertRaises(recovery.RecoveryError):
            recovery.recover(self.gh, self.publisher, Path("unused"), {})
        self.gh.download_archive.assert_not_called()
        self.publisher.main.assert_not_called()
        self.assertTrue(all(call.kwargs.get("method", "GET") == "GET"
                            for call in self.gh.api.call_args_list))

    def test_already_running_original_publisher_is_not_cancelled(self):
        self.jobs.append({"name": "publish", "status": "in_progress"})
        with self.assertRaisesRegex(recovery.RecoveryError, "publisher has started"):
            recovery.stop_original_run(self.gh, {})
        self.assertTrue(all(call.kwargs.get("method", "GET") == "GET"
                            for call in self.gh.api.call_args_list))

    def test_existing_destination_is_not_changed(self):
        self.releases = [{"tag_name": recovery.TAG, "draft": True}]
        with self.assertRaisesRegex(recovery.RecoveryError, "already exists"):
            recovery.stop_original_run(self.gh, {})
        self.assertTrue(all(call.kwargs.get("method", "GET") == "GET"
                            for call in self.gh.api.call_args_list))

    def test_queued_job_timestamp_does_not_mean_publisher_started(self):
        self.jobs.append({"name": "publish", "status": "queued",
                          "started_at": "2026-10-26T18:04:32Z", "runner_id": 0})
        audit = {}
        recovery.stop_original_run(self.gh, audit)
        self.assertTrue(audit["original_cancellation_requested"])
        self.assertEqual(audit["source_run_after_stop"]["status"], "completed")

    def test_completed_failed_original_run_with_skipped_publish_is_reusable(self):
        self.run.update(status="completed", conclusion="failure")
        self.jobs.append({"name": "publish", "status": "completed", "conclusion": "skipped"})
        recovery.stop_original_run(self.gh, {})
        self.assertTrue(all(call.kwargs.get("method", "GET") == "GET"
                            for call in self.gh.api.call_args_list))

    def test_cancelled_publisher_that_executed_is_rejected(self):
        with self.assertRaisesRegex(recovery.RecoveryError, "publisher has started"):
            recovery.require_publisher_unstarted([
                {"name": "publish", "status": "completed", "conclusion": "cancelled",
                 "runner_id": 10, "steps": [{"name": "Publish", "status": "completed"}]}])


class CompleteRecoveryTests(unittest.TestCase):
    def setUp(self):
        # Recovery runs the publisher from the pinned v0.3.0 checkout, not the
        # current release publisher. Retain that artifact contract in fixtures.
        old_applications = {name for names in (*recovery.ARTIFACT_FILES.values(), recovery.ARM_FILES)
                            for name in names if not name.startswith("SHA256SUMS-")}
        original_contract = patch.object(original_publisher, "EXPECTED", old_applications)
        original_contract.start()
        self.addCleanup(original_contract.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        builds = self.root / "desktop-dist"
        builds.mkdir()
        payloads = {name: ("verified fixture: " + name).encode()
                    for name in original_publisher.EXPECTED}
        for manifest, filename in original_publisher.DESKTOP_MANIFESTS.items():
            payloads[manifest] = (
                f"{hashlib.sha256(payloads[filename]).hexdigest()}  {filename}\n".encode())
        for name in recovery.ARM_FILES:
            (builds / name).write_bytes(payloads[name])
        self.artifacts = source_artifacts()
        self.archives = {}
        for artifact in self.artifacts:
            archive = self.root / f"{artifact['id']}.zip"
            with zipfile.ZipFile(archive, "w") as package:
                for name in recovery.ARTIFACT_FILES[artifact["name"]]:
                    package.writestr(name, payloads[name])
            artifact["digest"] = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
            self.archives[artifact["id"]] = archive
        self.run = source_run()
        self.gh = Mock()

        def api(suffix, **kwargs):
            if suffix == f"git/ref/tags/{recovery.TAG}":
                return {"object": {"type": "commit", "sha": recovery.SOURCE_SHA}}
            if suffix == f"actions/runs/{recovery.SOURCE_RUN}":
                return copy.deepcopy(self.run)
            if suffix == "releases?per_page=100":
                return [[]]
            if suffix.endswith("/cancel"):
                # Cancellation is the first permitted external mutation, and
                # all downloaded input bytes must already have passed checks.
                self.assertEqual(len(original_publisher.validate_inputs(builds)), 12)
                self.run.update(status="completed", conclusion="cancelled")
                return None
            raise AssertionError(suffix)

        self.gh.api.side_effect = api
        self.gh.collection.side_effect = lambda suffix, key: (
            source_jobs() if key == "jobs" else copy.deepcopy(self.artifacts))
        self.gh.download_archive.side_effect = lambda artifact_id, destination: (
            shutil.copyfile(self.archives[artifact_id], destination))
        self.publisher = Mock()
        self.publisher.validate_inputs.side_effect = original_publisher.validate_inputs

    def test_complete_recovery_publishes_exact_source_and_preserves_actual_run(self):
        audit = {}

        def publish():
            self.assertEqual(os.environ["GITHUB_SHA"], recovery.SOURCE_SHA)
            self.assertEqual(os.environ["GITHUB_REF_NAME"], recovery.TAG)
            self.assertEqual(os.environ["GITHUB_RUN_ID"], "recovery-run-id")
            self.assertEqual(audit["source_run_after_stop"]["status"], "completed")

        self.publisher.main.side_effect = publish
        with patch.dict(os.environ, {"GITHUB_SHA": "workflow-sha", "GITHUB_REF_NAME": "main",
                                     "GITHUB_RUN_ID": "recovery-run-id"}):
            recovery.recover(self.gh, self.publisher, self.root, audit)
            self.assertEqual(os.environ["GITHUB_SHA"], "workflow-sha")
            self.assertEqual(os.environ["GITHUB_REF_NAME"], "main")
        self.publisher.main.assert_called_once_with()
        self.assertEqual(len(audit["verified_files"]), 12)
        self.assertEqual(audit["outcome"], "published and downloaded release verified")

    def test_corrupt_archive_never_cancels_or_publishes(self):
        self.artifacts[0]["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(recovery.RecoveryError, "digest mismatch"):
            recovery.recover(self.gh, self.publisher, self.root, {})
        self.publisher.main.assert_not_called()
        self.assertTrue(all(call.kwargs.get("method", "GET") == "GET"
                            for call in self.gh.api.call_args_list))

    def test_wrong_apple_silicon_checksum_never_cancels_or_publishes(self):
        (self.root / "desktop-dist/WinUSB-Creator-macOS-AppleSilicon.dmg").write_bytes(b"corrupt")
        with self.assertRaisesRegex(RuntimeError, "Checksum mismatch"):
            recovery.recover(self.gh, self.publisher, self.root, {})
        self.publisher.main.assert_not_called()
        self.assertTrue(all(call.kwargs.get("method", "GET") == "GET"
                            for call in self.gh.api.call_args_list))


if __name__ == "__main__":
    unittest.main()

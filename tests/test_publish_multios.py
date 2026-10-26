"""Reject incomplete/mislabelled release artifacts before external writes."""
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import publish_multios as publish
from consolidate_release import ReleaseError


class InputValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.builds = self.root / "desktop-dist"
        self.builds.mkdir()
        for name in publish.EXPECTED:
            (self.builds / name).write_bytes(("fixture: " + name).encode())
        for manifest, name in publish.DESKTOP_MANIFESTS.items():
            self.manifest(manifest, name)

    def manifest(self, manifest, name):
        digest = hashlib.sha256((self.builds / name).read_bytes()).hexdigest()
        (self.builds / manifest).write_text(f"{digest}  {name}\n", encoding="utf-8")

    def test_complete_set_includes_all_linux_and_desktop_payloads(self):
        verified = publish.validate_inputs(self.builds)
        self.assertEqual(set(verified), publish.EXPECTED | set(publish.DESKTOP_MANIFESTS))
        self.assertEqual(len(verified), 12)

    def test_missing_all_manifests_is_rejected(self):
        for name in publish.DESKTOP_MANIFESTS:
            (self.builds / name).unlink()
        with self.assertRaisesRegex(ReleaseError, "Incomplete release"):
            publish.validate_inputs(self.builds)

    def test_missing_one_manifest_is_rejected(self):
        (self.builds / "SHA256SUMS-macOS-Intel.txt").unlink()
        with self.assertRaises(ReleaseError):
            publish.validate_inputs(self.builds)

    def test_unknown_extra_manifest_is_rejected(self):
        self.manifest("SHA256SUMS-mystery.txt", "WinUSB-Creator-Windows-x64.exe")
        with self.assertRaisesRegex(ReleaseError, "unexpected"):
            publish.validate_inputs(self.builds)

    def test_valid_hash_for_wrong_architecture_is_rejected(self):
        self.manifest("SHA256SUMS-Windows-ARM64.txt", "WinUSB-Creator-Windows-x64.exe")
        with self.assertRaisesRegex(ReleaseError, "Incorrect checksum coverage"):
            publish.validate_inputs(self.builds)

    def test_one_manifest_cannot_cover_multiple_platforms(self):
        path = self.builds / "SHA256SUMS-Windows-x64.txt"
        path.write_text(path.read_text() + (self.builds / "SHA256SUMS-macOS-Intel.txt").read_text())
        with self.assertRaisesRegex(ReleaseError, "Incorrect checksum coverage"):
            publish.validate_inputs(self.builds)

    def test_changed_desktop_payload_is_rejected(self):
        (self.builds / "WinUSB-Creator-macOS-Intel.dmg").write_bytes(b"corrupted")
        with self.assertRaisesRegex(ReleaseError, "Checksum mismatch"):
            publish.validate_inputs(self.builds)

    def test_empty_linux_payload_is_rejected(self):
        (self.builds / "flash-usb_0.3.2_all.deb").write_bytes(b"")
        with self.assertRaisesRegex(ReleaseError, "empty artifact"):
            publish.validate_inputs(self.builds)

    def test_symlink_payload_is_rejected(self):
        path = self.builds / "flash-usb_0.3.2_all.deb"
        path.unlink()
        try:
            path.symlink_to(self.builds / "flash-usb-0.3.2-1.noarch.rpm")
        except OSError:
            self.skipTest("symlink creation not available")
        with self.assertRaisesRegex(ReleaseError, "Unsafe"):
            publish.validate_inputs(self.builds)

    def test_copied_linux_corruption_is_detected(self):
        verified = publish.validate_inputs(self.builds)
        staged = self.root / "staged"
        shutil.copytree(self.builds, staged)
        publish.verify_staged(staged, verified)
        (staged / "flash-usb-v0.3.2-aarch64.AppImage").write_bytes(b"corrupted")
        with self.assertRaisesRegex(ReleaseError, "Staged artifact differs"):
            publish.verify_staged(staged, verified)

    def test_failed_input_validation_never_creates_or_uploads_release(self):
        (self.builds / "SHA256SUMS-Windows-x64.txt").unlink()
        gh = Mock()
        commit = "a" * 40
        gh.api.return_value = {"object": {"type": "commit", "sha": commit}}
        gh.list_releases.return_value = [{"tag_name": "v0.3.1", "draft": False, "immutable": False}]
        with patch.object(publish.Path, "cwd", return_value=self.root), \
                patch.object(publish.subprocess, "check_output", return_value=commit + "\n"), \
                patch.object(publish, "GitHub", return_value=gh), \
                patch.dict(os.environ, {"GITHUB_REF_NAME": publish.TAG, "GITHUB_SHA": commit}):
            with self.assertRaises(ReleaseError):
                publish.main()
        gh.api.assert_called_once_with(f"git/ref/tags/{publish.TAG}")
        gh.upload.assert_not_called()
        gh.edit.assert_not_called()

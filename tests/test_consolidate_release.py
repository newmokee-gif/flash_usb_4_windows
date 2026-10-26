"""Release migration is read-only until checks pass, and never deletes assets."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("consolidate_release", ROOT / "scripts/consolidate_release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
SHA = "a" * 40


def original_releases():
    result = []
    for release_id, tag in enumerate(release.OLD_TAGS, 1):
        assets = [{"id": release_id * 100 + i, "name": name, "size": 123,
                   "state": "uploaded", "digest": "sha256:" + "b" * 64}
                  for i, name in enumerate(sorted(release.EXPECTED_ORIGINAL_ASSETS[tag]))]
        result.append({"id": release_id, "tag_name": tag, "draft": False,
                       "prerelease": tag != "release", "immutable": False,
                       "name": tag, "body": "original notes", "assets": assets})
    return result


class FakeGitHub:
    def __init__(self):
        self.releases = original_releases()
        self.calls = []
        self.files = {}
        self.latest = 1
        self.fail_hide = None
        self.fail_restore = None
        self.corrupt_download = False
        self.tag_commit = SHA

    def tag_sha(self, tag):
        return self.tag_commit

    def list_releases(self):
        return copy.deepcopy(self.releases)

    def release(self, release_id):
        return copy.deepcopy(next(r for r in self.releases if r["id"] == release_id))

    def create_draft(self, tag, source_sha, notes):
        self.calls.append(("create", tag))
        new = {"id": 3, "tag_name": tag, "draft": True, "prerelease": False,
               "name": tag, "body": notes, "assets": [], "html_url": "https://example.test/release"}
        self.releases.append(new)
        return copy.deepcopy(new)

    def upload(self, tag, files):
        self.calls.append(("upload", tag))
        self.files = {p.name: p.read_bytes() for p in files}
        self.releases[-1]["assets"] = [
            {"id": 300 + i, "name": name, "size": len(data), "state": "uploaded",
             "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
            for i, (name, data) in enumerate(self.files.items())]

    def download(self, tag, directory):
        self.calls.append(("download", tag))
        directory.mkdir()
        for name, data in self.files.items():
            (directory / name).write_bytes(data + (b"bad" if self.corrupt_download else b""))

    def edit(self, release_id, **changes):
        self.calls.append(("edit", release_id, changes.copy()))
        if release_id == self.fail_hide and changes.get("draft") is True:
            raise RuntimeError("simulated hide failure")
        if release_id == self.fail_restore and changes.get("draft") is False:
            raise RuntimeError("simulated restore failure")
        item = next(r for r in self.releases if r["id"] == release_id)
        item.update({k: v for k, v in changes.items() if k != "make_latest"})
        if changes.get("make_latest") == "true":
            self.latest = release_id
        return copy.deepcopy(item)

    def api(self, suffix):
        if suffix != "releases/latest":
            raise AssertionError(suffix)
        return self.release(self.latest)


class ManifestTests(unittest.TestCase):
    def test_manifest_rejects_traversal_absolute_and_shellish_filenames(self):
        for name in ("../payload", "/tmp/payload", "a/b", "a\\b", "-option", ".."):
            with self.subTest(name=name), self.assertRaises(release.ReleaseError):
                release.manifest_entries("a" * 64 + "  " + name + "\n")

    def test_manifest_allows_gnu_text_and_binary_markers(self):
        self.assertEqual(release.manifest_entries("A" * 64 + "  file one.zip\n"),
                         {"file one.zip": "a" * 64})
        self.assertEqual(release.manifest_entries("b" * 64 + " *file.zip\n"),
                         {"file.zip": "b" * 64})

    def test_manifest_rejects_duplicate_empty_and_malformed_entries(self):
        entry = "a" * 64 + "  file\n"
        for text in ("", "junk", entry + entry, entry + "\n"):
            with self.subTest(text=text), self.assertRaises(release.ReleaseError):
                release.manifest_entries(text)

    def test_checksum_mismatch_and_missing_target_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "SHA256SUMS.txt"
            manifest.write_text("a" * 64 + "  payload\n")
            with self.assertRaisesRegex(release.ReleaseError, "Missing"):
                release.verify_manifest(manifest, root)
            (root / "payload").write_bytes(b"different")
            with self.assertRaisesRegex(release.ReleaseError, "mismatch"):
                release.verify_manifest(manifest, root)


class InventoryTests(unittest.TestCase):
    def test_only_two_explicit_public_releases_are_selected(self):
        originals = original_releases()
        private_draft = {"id": 99, "tag_name": "private-draft", "draft": True}
        self.assertEqual(release.select_releases(originals + [private_draft], "v0.2.0"), originals)

    def test_unexpected_public_release_aborts(self):
        originals = original_releases()
        originals.append({"id": 99, "tag_name": "unexpected", "draft": False})
        with self.assertRaisesRegex(release.ReleaseError, "inventory changed"):
            release.select_releases(originals, "v0.2.0")

    def test_destination_exists_even_as_draft_aborts(self):
        for draft in (True, False):
            with self.subTest(draft=draft), self.assertRaisesRegex(release.ReleaseError, "already exists"):
                release.select_releases(original_releases() + [
                    {"id": 3, "tag_name": "v0.2.0", "draft": draft}], "v0.2.0")

    def test_immutable_original_or_missing_build_aborts(self):
        originals = original_releases()
        originals[0]["immutable"] = True
        with self.assertRaisesRegex(release.ReleaseError, "immutable"):
            release.select_releases(originals, "v0.2.0")
        originals = original_releases()
        required = release.SELECTED["v0.2.0-linux.2"][0]
        originals[1]["assets"] = [a for a in originals[1]["assets"] if a["name"] != required]
        with self.assertRaisesRegex(release.ReleaseError, "Missing required"):
            release.select_releases(originals, "v0.2.0")

    def test_additional_source_asset_requires_explicit_review(self):
        originals = original_releases()
        originals[1]["assets"].append({"id": 999, "name": "unexpected-new-platform.zip", "size": 1})
        with self.assertRaisesRegex(release.ReleaseError, "asset inventory changed"):
            release.select_releases(originals, "v0.2.0")

    def test_duplicate_asset_name_aborts(self):
        original = original_releases()[0]
        original["assets"].append(original["assets"][0].copy())
        with self.assertRaisesRegex(release.ReleaseError, "Duplicate"):
            release.asset_map(original)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        output = Path(self.temp.name)
        assets = output / "assets"
        assets.mkdir()
        (assets / "payload.zip").write_bytes(b"verified payload")
        digest = release.sha256(assets / "payload.zip")
        (assets / "SHA256SUMS.txt").write_text(f"{digest}  payload.zip\n")
        self.github = FakeGitHub()
        self.plan = {"old": original_releases(), "tag": "v0.2.0", "sha": SHA,
                     "assets": assets, "output": output, "notes": "complete notes"}

    def test_publish_verifies_before_hiding_and_preserves_old_assets(self):
        original_assets = [r["assets"] for r in self.github.list_releases()]
        result = release.publish(self.github, self.plan)
        self.assertEqual(result, "https://example.test/release")
        self.assertEqual([r["draft"] for r in self.github.releases], [True, True, False])
        self.assertEqual(original_assets, [r["assets"] for r in self.github.releases[:2]])
        steps = self.github.calls
        download = steps.index(("download", "v0.2.0"))
        edits = [(i, call) for i, call in enumerate(steps) if call[0] == "edit"]
        self.assertGreater(edits[0][0], download)
        self.assertEqual([call[1] for _, call in edits], [3, 1, 2])
        self.assertFalse(edits[0][1][2]["draft"])
        self.assertFalse(edits[0][1][2]["prerelease"])
        self.assertEqual(self.github.latest, 3)
        self.assertFalse(any(call[0] == "delete" for call in steps))

    def test_corrupt_uploaded_download_leaves_originals_public(self):
        self.github.corrupt_download = True
        with self.assertRaisesRegex(release.ReleaseError, "Uploaded asset differs"):
            release.publish(self.github, self.plan)
        self.assertEqual([r["draft"] for r in self.github.releases], [False, False, True])
        self.assertFalse(any(call[0] == "edit" for call in self.github.calls))

    def test_moved_target_tag_aborts_before_any_write(self):
        self.github.tag_commit = "c" * 40
        with self.assertRaisesRegex(release.ReleaseError, "tag moved"):
            release.publish(self.github, self.plan)
        self.assertEqual(self.github.calls, [])

    def test_changed_original_aborts_before_create(self):
        self.github.releases[0]["body"] = "another actor edited this"
        with self.assertRaisesRegex(release.ReleaseError, "changed during staging"):
            release.publish(self.github, self.plan)
        self.assertEqual(self.github.calls, [])

    def test_hide_failure_restores_old_before_drafting_new(self):
        self.github.fail_hide = 2
        with self.assertRaisesRegex(release.ReleaseError, "Original releases restored"):
            release.publish(self.github, self.plan)
        self.assertEqual([r["draft"] for r in self.github.releases], [False, False, True])
        edits = [call for call in self.github.calls if call[0] == "edit"]
        restore_index = next(i for i, c in enumerate(edits) if c[1] == 1 and c[2]["draft"] is False)
        hide_new_index = next(i for i, c in enumerate(edits) if c[1] == 3 and c[2]["draft"] is True)
        self.assertLess(restore_index, hide_new_index)

    def test_failed_restore_keeps_new_release_public(self):
        self.github.fail_hide = 2
        self.github.fail_restore = 1
        with self.assertRaisesRegex(release.ReleaseError, "Rollback incomplete"):
            release.publish(self.github, self.plan)
        self.assertFalse(self.github.release(3)["draft"])
        self.assertFalse(any(c[0] == "edit" and c[1] == 3 and c[2].get("draft") is True
                             for c in self.github.calls))

    def test_upload_failure_leaves_only_a_new_draft(self):
        with patch.object(self.github, "upload", side_effect=RuntimeError("upload unavailable")):
            with self.assertRaisesRegex(RuntimeError, "upload unavailable"):
                release.publish(self.github, self.plan)
        self.assertEqual([r["draft"] for r in self.github.releases], [False, False, True])


class PlatformAndSourceTests(unittest.TestCase):
    def test_appimage_architecture_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flash-usb-x86_64.AppImage"
            header = bytearray(64)
            header[:6] = b"\x7fELF\x02\x01"
            struct.pack_into("<H", header, 18, 183)
            path.write_bytes(header)
            with self.assertRaisesRegex(release.ReleaseError, "architecture mismatch"):
                release.verify_platform_file(path)
            struct.pack_into("<H", header, 18, 62)
            path.write_bytes(header)
            release.verify_platform_file(path)

    def test_macos_bundle_wrong_cpu_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "WinUSB-Creator-macOS-AppleSilicon.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("WinUSB Creator.app/Contents/MacOS/WinUSB Creator",
                                 b"\xcf\xfa\xed\xfe" + struct.pack("<I", 0x01000007))
            with self.assertRaisesRegex(release.ReleaseError, "architecture mismatch"):
                release.verify_platform_file(path)

    def test_windows_arm_zip_wrong_cpu_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "WinUSB-Creator-Windows-ARM64.zip"
            header = bytearray(128)
            header[:2] = b"MZ"
            struct.pack_into("<I", header, 0x3C, 64)
            header[64:70] = b"PE\0\0\x64\x86"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("WinUSB-Creator-Windows-ARM64.exe", header)
            with self.assertRaisesRegex(release.ReleaseError, "not ARM64"):
                release.verify_platform_file(path)

    @unittest.skipUnless(shutil.which("git") and (ROOT / ".git").exists(),
                         "requires git and a source checkout, not an exported archive")
    def test_source_archive_preserves_cyrillic_sources_and_omits_binaries(self):
        # Uses only the local Git object database and a temporary archive, no network.
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        with tempfile.TemporaryDirectory() as tmp:
            archive = release.create_source_archive(ROOT, Path(tmp), "v0.2.0", commit)
            with tarfile.open(archive) as tar:
                names = {name.removeprefix("flash-usb-v0.2.0/") for name in tar.getnames()}
            for name in ("прога/V2.py", "прога/linux_usb.py", "прога/macos_usb.py",
                         "прога/linux_gui.py", "прога/macos_gui.py", "прога/app_icon.ico"):
                self.assertIn(name, names)
            self.assertFalse(any(name.endswith((".exe", ".pyc")) for name in names))
            self.assertFalse(any("/build/" in name or "/dist/" in name for name in names))


if __name__ == "__main__":
    unittest.main()

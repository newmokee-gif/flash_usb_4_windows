"""Offline download safety tests: no network, subprocess or physical USB writes."""
import errno
import hashlib
import importlib.util
import io
import contextlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import types
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
import image_download as download


def bencode(item):
    if isinstance(item, int):
        return b"i" + str(item).encode() + b"e"
    if isinstance(item, bytes):
        return str(len(item)).encode() + b":" + item
    if isinstance(item, list):
        return b"l" + b"".join(map(bencode, item)) + b"e"
    return b"d" + b"".join(bencode(k) + bencode(v) for k, v in sorted(item.items())) + b"e"


class ChecksumTests(unittest.TestCase):
    def test_exact_filename_not_different_architecture(self):
        text = "a" * 64 + "  linux-arm64.iso\n" + "b" * 64 + " *linux-x64.iso\n"
        self.assertEqual(download.checksum_from_manifest(text, "linux-x64.iso"), "b" * 64)
        with self.assertRaises(download.DownloadError):
            download.checksum_from_manifest(text, "linux.iso")

    def test_bsd_sha512_manifest(self):
        text = "# signed manifest\nSHA512 (linux.iso) = " + "A" * 128
        self.assertEqual(download.checksum_from_manifest(text, "linux.iso", "sha512"), "a" * 128)

    def test_conflicting_checksums_refused(self):
        with self.assertRaises(download.DownloadError):
            download.checksum_from_manifest("a" * 64 + "  linux.iso\n" + "b" * 64 + "  linux.iso", "linux.iso")

    def test_single_bare_checksum_accepted_only_alone(self):
        self.assertEqual(download.checksum_from_manifest("c" * 64, "linux.iso"), "c" * 64)
        with self.assertRaises(download.DownloadError):
            download.checksum_from_manifest("c" * 64 + "\n" + "d" * 64, "linux.iso")

    def test_weak_checksum_refused(self):
        with self.assertRaises(download.DownloadError):
            download.checksum_from_manifest("a" * 32 + " linux.iso", "linux.iso", "md5")

    def test_https_required_including_redirects(self):
        for url in ("http://example.com/image.iso", "file:///etc/passwd", "https://user:pass@example.com/a"):
            with self.subTest(url=url), self.assertRaises(download.DownloadError):
                download.https_url(url)
        with self.assertRaises(download.DownloadError):
            download.HTTPSRedirect().redirect_request(None, None, 302, "redirect", {}, "http://example.com/image.iso")


class TorrentTests(unittest.TestCase):
    def test_single_selected_image(self):
        data = bencode({b"info": {b"name": b"linux.iso", b"length": 4096}})
        self.assertEqual(download.torrent_target(data, "linux.iso"), (Path("linux.iso"), None))
        with self.assertRaises(download.DownloadError):
            download.torrent_target(data, "another.iso")

    def test_multifile_selects_only_named_image(self):
        data = bencode({b"info": {b"name": b"release", b"files": [
            {b"length": 100, b"path": [b"README"]},
            {b"length": 4096, b"path": [b"images", b"linux.iso"]}]}})
        self.assertEqual(download.torrent_target(data, "linux.iso"), (Path("release/images/linux.iso"), 2))

    def test_validates_unselected_paths_too(self):
        for unsafe in (b"..", b".. ", b"folder.", b"CON", b"nul.iso", b"COM1.txt", b"a/b", b"C:", b"x\\y"):
            data = bencode({b"info": {b"name": b"release", b"files": [
                {b"length": 4096, b"path": [b"linux.iso"]},
                {b"length": 1, b"path": [unsafe, b"extra"]}]}})
            with self.subTest(unsafe=unsafe), self.assertRaises(download.DownloadError):
                download.torrent_target(data, "linux.iso")

    def test_both_legacy_and_utf8_paths_must_be_safe_and_equal(self):
        for unsafe in (b".. ", b"another-folder"):
            data = bencode({b"info": {b"name": b"release", b"files": [
                {b"length": 4096, b"path": [unsafe, b"linux.iso"], b"path.utf-8": [b"safe", b"linux.iso"]}]}})
            with self.assertRaises(download.DownloadError):
                download.torrent_target(data, "linux.iso")

    def test_symlinks_and_duplicate_targets_refused(self):
        entries = [
            [{b"length": 1, b"path": [b"linux.iso"], b"attr": b"l"}],
            [{b"length": 1, b"path": [b"linux.iso"]}, {b"length": 1, b"path": [b"LINUX.ISO"]}],
            [{b"length": 1, b"path": [b"a", b"linux.iso"]}, {b"length": 1, b"path": [b"b", b"linux.iso"]}],
        ]
        for files in entries:
            with self.subTest(files=files), self.assertRaises(download.DownloadError):
                download.torrent_target(bencode({b"info": {b"name": b"release", b"files": files}}), "linux.iso")

    def test_truncated_and_duplicate_bencode_refused(self):
        for data in (b"d4:info", b"d1:ai1e1:ai2ee", b"999999999999999:x", b"degarbage"):
            with self.subTest(data=data), self.assertRaises(download.DownloadError):
                download.torrent_target(data, "linux.iso")


class DownloadWorkflowTests(unittest.TestCase):
    content = b"test image data" * 512

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # macOS exposes temporary paths through /var -> /private/var. Compare
        # the same canonical paths returned by the downloader on every host.
        self.directory = Path(self.temp.name).resolve()
        self.sha = hashlib.sha256(self.content).hexdigest()
        self.image = {"image_url": "https://official.example/linux.iso", "sha256": self.sha}

    def fetch(self, url, destination=None, **kwargs):
        download._cancelled(kwargs.get("cancelled"))
        if destination:
            Path(destination).write_bytes(self.content)
            return len(self.content)
        return (self.sha + "  linux.iso\n").encode()

    def run_download(self, **kwargs):
        with patch.object(download, "fetch", side_effect=self.fetch), patch.object(download, "find_aria2", return_value=None):
            return download.download_image(self.image, self.directory, **kwargs)

    def test_verified_download_returns_same_sha256(self):
        result = self.run_download()
        self.assertEqual(result.path.read_bytes(), self.content)
        self.assertEqual(result.sha256, self.sha)
        self.assertEqual([path.resolve() for path in self.directory.iterdir()], [result.path.resolve()])

    def test_bad_checksum_never_publishes_image(self):
        self.image["sha256"] = "0" * 64
        with self.assertRaises(download.DownloadError):
            self.run_download()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_existing_user_image_not_overwritten(self):
        target = self.directory / "linux.iso"
        target.write_bytes(b"user data")
        with self.assertRaises(download.DownloadError):
            self.run_download()
        self.assertEqual(target.read_bytes(), b"user data")

    def test_cancelled_download_publishes_nothing(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(download.Cancelled):
            self.run_download(cancelled=event)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_torrent_failure_falls_back_to_official_image(self):
        self.image["torrent_url"] = "https://official.example/linux.iso.torrent"
        logs = []
        with patch.object(download, "fetch", side_effect=self.fetch), patch.object(download, "find_aria2", return_value="aria2c"), \
                patch.object(download, "download_torrent", side_effect=download.DownloadError("tracker unavailable")):
            result = download.download_image(self.image, self.directory, log=logs.append)
        self.assertEqual(result.sha256, self.sha)
        self.assertTrue(any("HTTPS" in line for line in logs))

    def test_torrent_cancel_does_not_fallback(self):
        self.image["torrent_url"] = "https://official.example/linux.iso.torrent"
        with patch.object(download, "fetch") as fetch, patch.object(download, "find_aria2", return_value="aria2c"), \
                patch.object(download, "download_torrent", side_effect=download.Cancelled("cancelled")):
            with self.assertRaises(download.Cancelled):
                download.download_image(self.image, self.directory)
        fetch.assert_not_called()

    def test_manifest_checksum_association_before_download(self):
        self.image.pop("sha256")
        self.image["checksum_url"] = "https://official.example/SHA256SUMS"
        self.assertEqual(self.run_download().sha256, self.sha)

    def test_sha512_and_sha256_bind_to_same_image(self):
        self.image.pop("sha256")
        self.image.update(checksum_url="https://official.example/SHA512SUMS", checksum_algorithm="sha512")
        def fetch(url, destination=None, **kwargs):
            if destination:
                return self.fetch(url, destination, **kwargs)
            return (hashlib.sha512(self.content).hexdigest() + "  linux.iso\n").encode()
        with patch.object(download, "fetch", side_effect=fetch), patch.object(download, "find_aria2", return_value=None):
            result = download.download_image(self.image, self.directory)
        self.assertEqual(result.sha256, self.sha)

    def test_resolved_sha512_does_not_reparse_multi_algorithm_manifest(self):
        self.image.pop("sha256")
        self.image.update(sha512=hashlib.sha512(self.content).hexdigest(), checksum_algorithm="sha512",
                          checksum_url="https://official.example/linux.iso.DIGESTS")
        requests = []
        def fetch(url, destination=None, **kwargs):
            requests.append(url)
            self.assertIsNotNone(destination)
            return self.fetch(url, destination, **kwargs)
        with patch.object(download, "fetch", side_effect=fetch), patch.object(download, "find_aria2", return_value=None):
            result = download.download_image(self.image, self.directory)
        self.assertEqual(result.sha256, self.sha)
        self.assertEqual(requests, [self.image["image_url"]])

    def test_source_swap_before_publication_refused(self):
        original_link = os.link
        def replace_then_link(source, target):
            Path(source).write_bytes(b"swapped image")
            original_link(source, target)
        with patch.object(download.os, "link", side_effect=replace_then_link):
            with self.assertRaises(download.DownloadError):
                self.run_download()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_filesystem_without_hardlinks_uses_exclusive_verified_copy(self):
        with patch.object(download.os, "link", side_effect=OSError(errno.ENOTSUP, "unsupported")):
            result = self.run_download()
        self.assertEqual(result.sha256, self.sha)
        self.assertEqual(result.path.read_bytes(), self.content)

    def test_compressed_image_not_passed_to_raw_writer(self):
        self.image["image_url"] = "https://official.example/linux.img.xz"
        with self.assertRaises(download.DownloadError):
            self.run_download()


class WindowsAriaTests(unittest.TestCase):
    def test_native_arm64_rejects_x64_aria(self):
        with tempfile.TemporaryDirectory() as temp:
            candidate = Path(temp) / "aria2c.exe"
            data = bytearray(70)
            data[:2] = b"MZ"
            struct.pack_into("<I", data, 0x3C, 64)
            data[64:70] = b"PE\0\0\x64\x86"
            candidate.write_bytes(data)
            with patch.object(download.sys, "platform", "win32"), patch.dict(os.environ, {"PROCESSOR_ARCHITEW6432": "ARM64"}), \
                    patch.object(download.shutil, "which", return_value=str(candidate)), patch.object(download.tempfile, "gettempdir", return_value=temp):
                self.assertIsNone(download.find_aria2())
                data[68:70] = b"\x64\xaa"
                candidate.write_bytes(data)
                self.assertEqual(download.find_aria2(), str(candidate))

    def test_verified_bootstrap_and_cache_revalidation(self):
        binary = bytearray(70)
        binary[:2] = b"MZ"
        struct.pack_into("<I", binary, 0x3C, 64)
        binary[64:70] = b"PE\0\0\x64\x86"
        archive_data = io.BytesIO()
        with zipfile.ZipFile(archive_data, "w") as archive:
            archive.writestr(download.ARIA2_X64_MEMBER, binary)
            for name in ("COPYING", "LICENSE.OpenSSL", "README.mingw"):
                archive.writestr("aria2-1.37.0-win-64bit-build1/" + name, "license")
        archive_bytes = archive_data.getvalue()
        def fetch(url, path, **kwargs):
            self.assertEqual(url, download.ARIA2_X64_URL)
            Path(path).write_bytes(archive_bytes)
        with tempfile.TemporaryDirectory() as temp, patch.object(download.sys, "platform", "win32"), \
                patch.dict(os.environ, {"LOCALAPPDATA": temp}), patch.object(download, "find_aria2", return_value=None), \
                patch.object(download, "windows_arm64", return_value=False), patch.object(download, "fetch", side_effect=fetch) as get, \
                patch.object(download, "ARIA2_X64_ARCHIVE_SHA256", hashlib.sha256(archive_bytes).hexdigest()), \
                patch.object(download, "ARIA2_X64_EXE_SHA256", hashlib.sha256(binary).hexdigest()):
            executable = Path(download.ensure_windows_aria2())
            self.assertEqual(executable.read_bytes(), binary)
            self.assertEqual(get.call_count, 1)
            self.assertEqual(download.ensure_windows_aria2(), str(executable))
            self.assertEqual(get.call_count, 1)
            executable.write_bytes(b"modified cached executable")
            self.assertEqual(download.ensure_windows_aria2(), str(executable))
            self.assertEqual(get.call_count, 2)
            self.assertEqual(executable.read_bytes(), binary)

    def test_wrong_bootstrap_hash_never_publishes_executable(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(download.sys, "platform", "win32"), \
                patch.dict(os.environ, {"LOCALAPPDATA": temp}), patch.object(download, "find_aria2", return_value=None), \
                patch.object(download, "windows_arm64", return_value=False), \
                patch.object(download, "fetch", side_effect=lambda url, path, **kwargs: Path(path).write_bytes(b"not upstream archive")):
            with self.assertRaises(download.DownloadError):
                download.ensure_windows_aria2()
            self.assertEqual(list(Path(temp).rglob("*.exe")), [])

    def test_arm_emulation_requires_consent_and_windows11(self):
        with patch.object(download.sys, "platform", "win32"), patch.object(download, "find_aria2", return_value=None), \
                patch.object(download, "windows_arm64", return_value=True), patch.object(download, "fetch") as get:
            with self.assertRaises(download.DownloadError):
                download.ensure_windows_aria2(allow_emulation=False)
            with patch.object(download.sys, "getwindowsversion", create=True, return_value=types.SimpleNamespace(build=19045)):
                with self.assertRaises(download.DownloadError):
                    download.ensure_windows_aria2(allow_emulation=True)
            get.assert_not_called()


class BundledAriaTests(unittest.TestCase):
    def test_frozen_linux_prefers_bundled_client_with_bundled_libraries(self):
        with tempfile.TemporaryDirectory() as temp:
            client = Path(temp) / "aria2c"
            client.write_bytes(b"native fixture")
            client.chmod(0o755)
            with patch.object(download.sys, "platform", "linux"), patch.object(download.sys, "frozen", True, create=True), \
                    patch.object(download.sys, "_MEIPASS", temp, create=True), patch.dict(os.environ, {"LD_LIBRARY_PATH": "/old-private", "LD_LIBRARY_PATH_ORIG": "/system-extra"}):
                self.assertEqual(download.find_aria2(), str(client.resolve()))
                self.assertEqual(download.aria_environment(str(client))["LD_LIBRARY_PATH"], str(Path(temp).resolve()))
                self.assertEqual(download.aria_environment("/usr/bin/aria2c")["LD_LIBRARY_PATH"], "/system-extra")


class GNUCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("_gnu_command_tests", Path(download.__file__).with_name("gnu_linux_gui.py"))
        cls.gui = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"distro_catalog": types.SimpleNamespace(CATALOG=())}):
            spec.loader.exec_module(cls.gui)

    def test_source_command_matches_real_raw_cli_and_preserves_identity(self):
        import raw_usb
        device = raw_usb.Device("/dev/sdb", 4 * 2**30, "Test", "serial", (), "", ("stable", 123))
        command = self.gui.build_write_command("/tmp/linux image.iso", device, device.path, "a" * 64)
        with patch.object(raw_usb, "write_image") as write, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(raw_usb.main(command[3:]), 0)
        write.assert_called_once_with(str(Path("/tmp/linux image.iso").resolve()), device.path,
                                      device.path, ["stable", 123], "a" * 64)

    def test_frozen_app_dispatches_gnu_cli(self):
        device = types.SimpleNamespace(path="/dev/disk9", identity=("stable", 123))
        with patch.object(self.gui.sys, "frozen", True, create=True), patch.object(self.gui.sys, "platform", "darwin"):
            command = self.gui.build_write_command("/tmp/linux.iso", device, device.path, "a" * 64)
        self.assertEqual(command[:3], [sys.executable, "--gnu-linux", "write"])

    def test_unavailable_original_appimage_rejected_before_root_launch(self):
        device = types.SimpleNamespace(path="/dev/sdb", identity=("stable", 123))
        with patch.object(self.gui.sys, "frozen", True, create=True), patch.object(self.gui.sys, "platform", "linux"), \
                patch.dict(os.environ, {"APPIMAGE": "/definitely/missing/app.AppImage"}):
            with self.assertRaises(RuntimeError):
                self.gui.build_write_command("/tmp/linux.iso", device, device.path, "a" * 64)


if __name__ == "__main__":
    unittest.main()

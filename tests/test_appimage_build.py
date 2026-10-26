"""Architecture selection must never mix AppImage tool/runtime binaries."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "appimage_builder", Path(__file__).resolve().parents[1] / "packaging/appimage/build.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class NativeArchitectureTests(unittest.TestCase):
    def test_aria_helper_must_match_native_elf_architecture(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "aria2c"
            for architecture, machine in (("x86_64", 62), ("aarch64", 183)):
                header = bytearray(64)
                header[:6] = b"\x7fELF\x02\x01"
                struct.pack_into("<H", header, 18, machine)
                helper.write_bytes(header)
                builder.verify_elf_architecture(helper, architecture)
                other = "aarch64" if architecture == "x86_64" else "x86_64"
                with self.assertRaisesRegex(RuntimeError, "Expected native"):
                    builder.verify_elf_architecture(helper, other)
            helper.write_bytes(b"not an executable")
            with self.assertRaisesRegex(RuntimeError, "Expected native"):
                builder.verify_elf_architecture(helper, "x86_64")

    def test_native_machine_selects_matching_tool_and_runtime(self):
        for machine, architecture in (("x86_64", "x86_64"), ("AMD64", "x86_64"),
                                      ("aarch64", "aarch64"), ("arm64", "aarch64")):
            with self.subTest(machine=machine), patch.object(builder.sys, "platform", "linux"), \
                    patch.object(builder.platform, "machine", return_value=machine):
                self.assertEqual(builder.native_architecture(), architecture)
                downloads = builder.downloads_for(architecture)
                self.assertTrue(downloads["appimagetool.AppImage"][0].endswith(
                    f"/1.9.1/appimagetool-{architecture}.AppImage"))
                self.assertTrue(downloads[f"runtime-{architecture}"][0].endswith(
                    f"/20251108/runtime-{architecture}"))
                for _, digest in downloads.values():
                    self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_unsupported_machine_refused_before_download(self):
        with patch.object(builder.sys, "platform", "linux"), \
                patch.object(builder.platform, "machine", return_value="armv7l"), \
                patch.object(builder, "download_verified") as download:
            with self.assertRaisesRegex(RuntimeError, "Unsupported build architecture"):
                builder.build(Path("/unused"), Path("/unused"), Path("/unused"))
        download.assert_not_called()

    def test_non_linux_builder_refused(self):
        with patch.object(builder.sys, "platform", "win32"):
            with self.assertRaisesRegex(RuntimeError, "built on Linux"):
                builder.native_architecture()

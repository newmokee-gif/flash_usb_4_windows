"""Portable policy tests; execute actual WinAPI/Tk only on Windows ARM CI."""
import ast
import os
from pathlib import Path
import platform
import tempfile
import types
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "прога/V2.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
NAMESPACE = {"os": os, "platform": platform}
for node in TREE.body:
    if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "WINDOWS_ISO_CHOICES" for target in node.targets):
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), NAMESPACE)
    if isinstance(node, ast.FunctionDef) and node.name in (
        "is_arm64_host", "windows_iso_architecture", "windows_iso_parameters", "is_arm64_executable",
    ):
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), NAMESPACE)


class WindowsARMTests(unittest.TestCase):
    def test_native_arm_aliases_detected(self):
        for name in ("ARM64", "arm64", "aarch64"):
            with self.subTest(name=name), patch.dict(os.environ, {}, clear=True), patch.object(platform, "machine", return_value=name):
                self.assertTrue(NAMESPACE["is_arm64_host"]())

    def test_x64_emulation_uses_native_architecture(self):
        with patch.dict(os.environ, {"PROCESSOR_ARCHITEW6432": "ARM64"}, clear=True), patch.object(platform, "machine", return_value="AMD64"):
            self.assertTrue(NAMESPACE["is_arm64_host"]())

    def test_x64_host_is_not_arm(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(platform, "machine", return_value="AMD64"):
            self.assertFalse(NAMESPACE["is_arm64_host"]())

    def test_iso_architecture_is_independent_of_host(self):
        for choice in ("Windows 11 (ARM64 Русская)", "Windows 11 (ARM64 English)"):
            self.assertEqual(NAMESPACE["windows_iso_architecture"](choice), "ARM64")
        self.assertEqual(NAMESPACE["windows_iso_architecture"]("Windows 11 (64-bit Русская)"), "x64")

    def test_windows_10_has_four_real_language_architecture_variants(self):
        variants = {name: values for name, values in NAMESPACE["WINDOWS_ISO_CHOICES"].items()
                    if name.startswith("Windows 10 ")}
        self.assertEqual(len(variants), 4)
        self.assertEqual(set(variants.values()), {
            ("10", "Russian", "x64"), ("10", "English", "x64"),
            ("10", "Russian", "x86"), ("10", "English", "x86"),
        })
        for choice, expected in variants.items():
            self.assertEqual(NAMESPACE["windows_iso_parameters"](choice), expected)
            self.assertEqual(NAMESPACE["windows_iso_architecture"](choice), expected[2])

    def test_invalid_windows_choice_never_defaults_to_another_iso(self):
        for choice in ("", "Windows 10 (ARM64 Русская)", "Windows 11 (32-bit English)", "Windows 12"):
            with self.subTest(choice=choice), self.assertRaises(ValueError):
                NAMESPACE["windows_iso_parameters"](choice)

    def test_fido_receives_selected_32bit_english_windows_10_parameters(self):
        klass = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "WindowsOneClickUSB")
        method = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "get_direct_microsoft_iso_url")
        namespace = dict(NAMESPACE)
        command_calls = []
        namespace["run_cmd"] = lambda command, **kwargs: (command_calls.append(command) or (0, "https://software.download.prss.microsoft.com/test.iso\n", ""))
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), "exec"), namespace)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "Fido.ps1").touch()
            app = types.SimpleNamespace(tools_dir=directory, update_status=lambda *a: None,
                                        win_version=types.SimpleNamespace(get=lambda: "Windows 10 (32-bit English)"))
            result = namespace["get_direct_microsoft_iso_url"](app)
        command = command_calls[0]
        self.assertEqual(command[command.index("-Win") + 1], "10")
        self.assertEqual(command[command.index("-Lang") + 1], "English")
        self.assertEqual(command[command.index("-Arch") + 1], "x86")
        self.assertTrue(result.startswith("https://software.download.prss.microsoft.com/"))

    def test_pe_machine_detects_real_arm64_not_x64(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aria2c.exe"
            header = bytearray(256)
            header[:2] = b"MZ"
            header[0x3C:0x40] = (128).to_bytes(4, "little")
            for machine, expected in ((0xAA64, True), (0x8664, False), (0x14C, False)):
                header[128:134] = b"PE\0\0" + machine.to_bytes(2, "little")
                path.write_bytes(header)
                self.assertEqual(NAMESPACE["is_arm64_executable"](path), expected)
            path.write_bytes(b"not a PE file")
            self.assertFalse(NAMESPACE["is_arm64_executable"](path))

    def test_arm_download_does_not_fetch_x64_binary(self):
        klass = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "WindowsOneClickUSB")
        method = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "ensure_aria2")
        namespace = dict(NAMESPACE)
        namespace["is_arm64_host"] = lambda: True
        namespace["shutil"] = types.SimpleNamespace(which=lambda name: None)
        namespace["urllib"] = types.SimpleNamespace(request=types.SimpleNamespace(urlopen=lambda *a, **k: self.fail("Network must not be reached")))
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), "exec"), namespace)
        with tempfile.TemporaryDirectory() as directory:
            app = types.SimpleNamespace(tools_dir=directory)
            with self.assertRaisesRegex(RuntimeError, "aria2c ARM64"):
                namespace["ensure_aria2"](app)

    def test_source_still_compiles(self):
        compile(SOURCE.read_bytes(), str(SOURCE), "exec")


if __name__ == "__main__":
    unittest.main()

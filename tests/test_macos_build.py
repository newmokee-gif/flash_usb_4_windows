"""Check the macOS compatibility gate without requiring Apple's build tools."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import tempfile


SPEC = importlib.util.spec_from_file_location(
    "macos_builder", Path(__file__).resolve().parents[1] / "packaging/macos/build.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def build_version(version="15.0", platform="1", sdk="26.0"):
    return f"""/bundle/helper:
Load command 0
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform {platform}
    minos {version}
      sdk {sdk}
   ntools 1
     tool 3
  version 1200.0
"""


class DeploymentTargetTests(unittest.TestCase):
    def test_accepts_older_and_exact_targets_even_with_newer_sdk(self):
        for version in ("10.13", "11.0", "14.8.9", "15", "15.0", "15.0.0"):
            with self.subTest(version=version):
                builder.verify_deployment_target(build_version(version), "helper")
        builder.verify_deployment_target(build_version(platform="macos"), "helper")

    def test_accepts_legacy_macos_load_command(self):
        builder.verify_deployment_target("""/bundle/helper:
Load command 0
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 10.13
      sdk 15.0
Load command 1
      cmd LC_SOURCE_VERSION
  cmdsize 16
  version 26.0
""", "helper")

    def test_rejects_newer_targets_and_patch_level_requirement(self):
        for version in ("15.0.1", "15.1", "26.0"):
            with self.subTest(version=version), self.assertRaisesRegex(RuntimeError, "exceeds 15.0"):
                builder.verify_deployment_target(build_version(version), "helper")

    def test_rejects_missing_ambiguous_and_malformed_targets(self):
        for commands in ("", build_version("bad"), build_version("15.0.0.1"),
                         build_version().replace("minos 15.0", "other 15.0"),
                         build_version() + build_version()):
            with self.subTest(commands=commands), self.assertRaises(RuntimeError):
                builder.verify_deployment_target(commands, "helper")

    def test_rejects_non_macos_platforms(self):
        for platform in ("2", "6", "ios", "maccatalyst", "unknown"):
            with self.subTest(platform=platform), self.assertRaisesRegex(RuntimeError, "does not target macOS"):
                builder.verify_deployment_target(build_version(platform=platform), "helper")
        with self.assertRaisesRegex(RuntimeError, "does not target macOS"):
            builder.verify_deployment_target("Load command 0\ncmd LC_VERSION_MIN_IPHONEOS\nversion 15.0\n", "helper")

    def test_bundle_audit_rejects_newer_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            helper = bundle / "helper.dylib"
            helper.write_bytes(b"\xcf\xfa\xed\xfe")
            with patch.object(builder.subprocess, "check_output", side_effect=["arm64\n", build_version("26.0")]):
                with self.assertRaisesRegex(RuntimeError, "exceeds 15.0"):
                    builder.verify_architecture(bundle, "arm64")


if __name__ == "__main__":
    unittest.main()

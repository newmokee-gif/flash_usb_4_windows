"""Keep setup UI dependencies in the installed DEB/RPM payload."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("native_package_build", ROOT / "packaging/build.py")
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class PayloadTests(unittest.TestCase):
    def test_shared_setup_ui_is_packaged_with_gnu_linux_gui(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            build.stage_payload(ROOT, stage, 1700000000)
            for name in ("package_manager_ui.py", "gnu_linux_gui.py", "dependencies.py"):
                target = stage / "usr/share/flash-usb/прога" / name
                self.assertEqual(target.read_bytes(), (ROOT / "прога" / name).read_bytes())
                self.assertEqual(target.stat().st_mode & 0o777, 0o644)

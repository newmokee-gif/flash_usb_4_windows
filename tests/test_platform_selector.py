import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
import platform_selector as selector


class SelectorTests(unittest.TestCase):
    def test_invalid_mode_does_not_destroy_ui(self):
        root = Mock()
        with self.assertRaises(ValueError):
            selector.open_mode(root, "other")
        root.winfo_children.assert_not_called()

    def test_gnu_choice_is_independent_of_host(self):
        root = Mock()
        child = Mock()
        root.winfo_children.return_value = [child]
        fake = Mock()
        with patch.dict(sys.modules, {"gnu_linux_gui": fake}), patch.object(sys, "platform", "win32"):
            selector.open_mode(root, "gnu-linux")
        child.destroy.assert_called_once()
        fake.GNULinuxUSB.assert_called_once_with(root)

    def test_windows_choice_selects_existing_host_gui(self):
        for system, module, klass in (("win32", "V2", "WindowsOneClickUSB"),
                                      ("linux", "linux_gui", "LinuxUSB"),
                                      ("darwin", "macos_gui", "MacUSB")):
            with self.subTest(system=system):
                root = Mock()
                root.winfo_children.return_value = []
                fake = Mock()
                with patch.dict(sys.modules, {module: fake}), patch.object(sys, "platform", system):
                    selector.open_mode(root, "windows")
                getattr(fake, klass).assert_called_once_with(root)

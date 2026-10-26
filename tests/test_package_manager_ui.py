import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
import package_manager_ui as ui


class InstallerTests(unittest.TestCase):
    def test_mac_uses_interactive_terminal_and_constant_script(self):
        with patch.object(ui.subprocess, "run") as run:
            ui.launch("darwin")
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["/usr/bin/osascript", "-e"])
        self.assertIn('tell application "Terminal"', args[2])
        self.assertEqual(args[3], ui.HOMEBREW_COMMAND)
        self.assertTrue(run.call_args.kwargs["check"])

    def test_windows_opens_official_app_installer(self):
        with patch("winget_install.launch") as start:
            ui.launch("win32")
        start.assert_called_once_with(None)

    def test_launch_errors_are_not_reported_as_success(self):
        with patch("winget_install.launch", side_effect=OSError("PowerShell unavailable")):
            with self.assertRaises(OSError):
                ui.launch("win32")

    def test_other_systems_do_not_launch_installers(self):
        with self.assertRaises(RuntimeError):
            ui.launch("linux")

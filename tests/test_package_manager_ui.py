import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
import package_manager_ui as ui


class InstallerTests(unittest.TestCase):
    def make_buttons(self, system, busy=False):
        buttons = []
        def button(*args, **kwargs):
            buttons.append(kwargs)
            return Mock()
        with patch.object(ui.sys, "platform", system), \
                patch.object(ui, "installation_panel", return_value=Mock()), \
                patch.object(ui.ttk, "Button", side_effect=button):
            ui.add_buttons(Mock(), busy=lambda: busy)
        return buttons

    def test_prominent_manager_buttons_keep_clear_names(self):
        for system, name in (("win32", "WinGet"), ("darwin", "Homebrew")):
            buttons = self.make_buttons(system)
            self.assertEqual(buttons[0]["text"], f"Install {name}")
            self.assertEqual(buttons[0]["style"], "Setup.TButton")
            self.assertEqual(buttons[1]["text"], "Check")

    def test_busy_operation_does_not_start_installation(self):
        buttons = self.make_buttons("win32", busy=True)
        with patch.object(ui.messagebox, "showinfo"), patch.object(ui, "launch") as launch:
            buttons[0]["command"]()
        launch.assert_not_called()

    def test_installation_still_requires_confirmation(self):
        buttons = self.make_buttons("darwin")
        with patch.object(ui, "installed", return_value=False), \
                patch.object(ui.messagebox, "askyesno", return_value=False), \
                patch.object(ui, "launch") as launch:
            buttons[0]["command"]()
        launch.assert_not_called()

    def test_installed_manager_is_not_reinstalled(self):
        buttons = self.make_buttons("win32")
        with patch.object(ui, "installed", return_value=True), \
                patch.object(ui.messagebox, "showinfo"), patch.object(ui, "launch") as launch:
            buttons[0]["command"]()
        launch.assert_not_called()

    def test_linux_does_not_offer_homebrew_or_winget(self):
        self.assertEqual(self.make_buttons("linux"), [])

    def test_dependency_button_keeps_callback_and_prominent_style(self):
        callback = Mock()
        with patch.object(ui, "installation_panel", return_value=Mock()), \
                patch.object(ui.ttk, "Button") as button:
            result = ui.add_dependency_button(Mock(), callback)
        self.assertIs(result, button.return_value)
        self.assertIs(button.call_args.kwargs["command"], callback)
        self.assertEqual(button.call_args.kwargs["style"], "Setup.TButton")
        callback.assert_not_called()

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

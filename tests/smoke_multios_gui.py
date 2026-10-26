"""Xvfb GUI transition smoke; no network or physical disk operations."""
from pathlib import Path
import ast
import os
import sys
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
import distro_catalog
import linux_gui
import raw_usb
import package_manager_ui
from platform_selector import OSChooser


def widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from widgets(child)


def check_setup_panels():
    for system, name in (("win32", "WinGet"), ("darwin", "Homebrew")):
        root = tk.Tk()
        try:
            root.geometry("620x240")
            with patch.object(package_manager_ui.sys, "platform", system):
                package_manager_ui.add_buttons(root)
            root.update()
            button = next(w for w in widgets(root) if w.winfo_class() == "TButton"
                          and w.cget("text") == f"Install {name}")
            assert button.winfo_ismapped()
            assert button.winfo_height() >= 30
            assert button.winfo_width() > 250
            assert button.cget("style") == "Setup.TButton"
            print(f"Prominent {name} setup panel: passed")
        finally:
            root.destroy()


def check_windows_layout():
    # Build only the Tk layout: never import WinAPI or touch drives on Linux.
    tree = ast.parse((Path(__file__).resolve().parents[1] / "прога/V2.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == "WindowsOneClickUSB")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "build_ui")
    choices = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "WINDOWS_ISO_CHOICES" for t in n.targets))
    scope = dict(tk=tk, ttk=ttk, os=os, add_buttons=package_manager_ui.add_buttons,
                 WINDOWS_ISO_CHOICES=choices)
    exec(compile(ast.Module(body=[method], type_ignores=[]), "Windows layout", "exec"), scope)
    root = tk.Tk()
    try:
        root.geometry("620x660")
        app = SimpleNamespace(root=root, is_busy=False, tools_dir="/tmp", work_dir="Downloads")
        for name in ("selected_drive", "win_version", "torrent_source", "iso_path"):
            setattr(app, name, tk.StringVar(root))
        for name in ("check_usb_var", "auto_write_var"):
            setattr(app, name, tk.BooleanVar(root))
        for name in ("start_standalone_usb_check", "refresh_drives", "start_auto_windows_pipeline",
                     "browse_torrent", "start_custom_pipeline", "browse_iso", "start_manual_write"):
            setattr(app, name, lambda: None)
        with patch.object(package_manager_ui.sys, "platform", "win32"):
            scope["build_ui"](app)
        root.update()
        for button in (app.write_btn, app.dl_auto_btn, app.dl_custom_btn, app.dl_mag_btn):
            assert button.winfo_ismapped()
            assert button.winfo_width() >= button.winfo_reqwidth(), button.cget("text")
            assert button.winfo_rooty() - root.winfo_rooty() + button.winfo_height() <= root.winfo_height()
        print("English Windows layout: controls visible at 620x660")
    finally:
        root.destroy()


def main():
    check_windows_layout()
    check_setup_panels()
    assert len({image.distro for image in distro_catalog.CATALOG}) >= 60
    linux_gui.list_devices = lambda: []
    raw_usb.list_devices = lambda: []
    for choice in ("windows_button", "linux_button"):
        root = tk.Tk()
        try:
            chooser = OSChooser(root)
            root.update()
            getattr(chooser, choice).invoke()
            root.update()
            root.after(200, root.quit)
            root.mainloop()
            expected = "GNU/Linux" if choice == "linux_button" else "Linux / UEFI"
            assert expected in root.title(), root.title()
            if choice == "linux_button":
                root.geometry("740x650")
                root.update()
                button = next(w for w in widgets(root) if w.winfo_class() == "TButton"
                              and w.cget("text") == "Install application dependencies")
                assert button.winfo_ismapped()
                assert button.winfo_rooty() - root.winfo_rooty() < 150
                assert button.winfo_height() >= 30
            print(f"GUI chooser -> {choice}: passed")
        finally:
            root.destroy()


if __name__ == "__main__":
    main()

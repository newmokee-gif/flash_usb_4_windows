"""Xvfb GUI transition smoke; no network or physical disk operations."""
from pathlib import Path
import sys
import time
import tkinter as tk
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
                          and w.cget("text") == f"Установить {name}")
            assert button.winfo_ismapped()
            assert button.winfo_height() >= 30
            assert button.winfo_width() > 250
            assert button.cget("style") == "Setup.TButton"
            print(f"Prominent {name} setup panel: passed")
        finally:
            root.destroy()


def main():
    check_setup_panels()
    assert len({image.distro for image in distro_catalog.CATALOG}) >= 20
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
            expected = "ГНУ/Линукс" if choice == "linux_button" else "Linux / UEFI"
            assert expected in root.title(), root.title()
            if choice == "linux_button":
                root.geometry("740x650")
                root.update()
                button = next(w for w in widgets(root) if w.winfo_class() == "TButton"
                              and w.cget("text") == "Установить зависимости программы")
                assert button.winfo_ismapped()
                assert button.winfo_rooty() - root.winfo_rooty() < 150
                assert button.winfo_height() >= 30
            print(f"GUI chooser -> {choice}: passed")
        finally:
            root.destroy()


if __name__ == "__main__":
    main()

"""Xvfb GUI transition smoke; no network or physical disk operations."""
from pathlib import Path
import sys
import time
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
import distro_catalog
import linux_gui
import raw_usb
from platform_selector import OSChooser


def main():
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
            print(f"GUI chooser -> {choice}: passed")
        finally:
            root.destroy()


if __name__ == "__main__":
    main()

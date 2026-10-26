#!/usr/bin/env python3
"""Non-destructive native package checks; run with a live Xvfb DISPLAY.

The app is exercised as an unprivileged user even when package CI runs as root.
This script deliberately imports installed files, never checkout modules.
"""
import os
from pathlib import Path
import stat
import subprocess


APP = Path("/usr/share/flash-usb")
WRAPPER = Path("/usr/bin/flash-usb")
DESKTOP = Path("/usr/share/applications/flash-usb.desktop")
ICON = Path("/usr/share/icons/hicolor/scalable/apps/flash-usb.svg")
GUI_SMOKE = """
import sys
import time
import tkinter

sys.path.insert(0, '/usr/share/flash-usb/прога')
from linux_gui import LinuxUSB
from gnu_linux_gui import GNULinuxUSB
import raw_usb

for attempt in range(100):
    try:
        root = tkinter.Tk()
        break
    except tkinter.TclError as exc:
        if "couldn't connect to display" not in str(exc) or attempt == 99:
            raise
        time.sleep(0.1)
app = LinuxUSB(root)
root.update()
root.destroy()
raw_usb.list_devices = lambda: []
root = tkinter.Tk()
app = GNULinuxUSB(root)
root.update()
assert app.install_button.winfo_ismapped()
assert app.install_button.cget('style') == 'Setup.TButton'
root.destroy()
print('Installed GUI startup passed as an unprivileged user')
"""


def require(condition, description):
    if not condition:
        raise RuntimeError(description)


def run_unprivileged(command):
    kwargs = {}
    if os.geteuid() == 0:
        kwargs.update(user=65534, group=65534, extra_groups=[])
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    # Do not accidentally import packages from the repository or CI host.
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    result = subprocess.run(command, cwd="/tmp", env=environment, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=45, check=True, **kwargs)
    print(result.stdout.strip())
    return result.stdout


def main():
    files = [WRAPPER, DESKTOP, ICON, APP / "flash_usb.py",
             APP / "прога/linux_usb.py", APP / "прога/linux_gui.py",
             APP / "прога/package_manager_ui.py", APP / "прога/gnu_linux_gui.py"]
    for path in files:
        metadata = path.stat()
        require(stat.S_ISREG(metadata.st_mode), f"Not a regular file: {path}")
        require(metadata.st_uid == 0, f"Not owned by root: {path}")
        require(metadata.st_mode & 0o022 == 0, f"Writable by another user: {path}")
        require(metadata.st_mode & 0o004, f"Not readable by desktop users: {path}")
    require(WRAPPER.stat().st_mode & 0o111 == 0o111, "Launcher is not executable")
    for path in (APP, APP / "прога"):
        require(path.stat().st_mode & 0o005 == 0o005, f"App directory is not accessible: {path}")
    desktop = DESKTOP.read_text()
    require("Exec=flash-usb" in desktop or "Exec=/usr/bin/flash-usb" in desktop,
            "Desktop entry does not invoke the installed launcher")
    require("Icon=flash-usb" in desktop, "Desktop entry does not use packaged icon")
    require("write" in run_unprivileged([str(WRAPPER), "--help"]), "CLI help missing write command")
    require("All Linux dependencies are available" in run_unprivileged([str(WRAPPER), "check"]),
            "Installed dependency check failed")
    run_unprivileged([str(WRAPPER), "list"])
    run_unprivileged(["/usr/bin/python3", "-c", GUI_SMOKE])
    print("Native package smoke checks passed")


if __name__ == "__main__":
    main()

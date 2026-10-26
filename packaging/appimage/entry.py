"""Frozen Linux launcher; imports are explicit for PyInstaller's analysis."""
import subprocess
import sys
import time
from pathlib import Path

from linux_gui import LinuxUSB, build_write_command, main as gui_main
from linux_usb import Device, main as cli_main, subprocess_environment


def self_test():
    """Exercise bundled Tk and the independent privilege-relaunch command."""
    import tkinter as tk

    if not getattr(sys, "frozen", False):
        raise RuntimeError("This diagnostic must run from the bundled executable")
    deadline = time.monotonic() + 10
    while True:
        try:
            root = tk.Tk()
            break
        except tk.TclError as exc:
            if "couldn't connect to display" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
    try:
        LinuxUSB(root)
        root.update()
        print(f"Bundled Python {sys.version.split()[0]}, Tk {tk.TkVersion}: GUI passed")
    finally:
        root.destroy()
    device = Device("/dev/unused", 16 * 2**30, "self-test", "unused", (), "", ("unused",))
    command = build_write_command(Path("/tmp/unused.iso"), device, device.path)
    # Exercise the exact AppImage prefix used after pkexec, but only request
    # help. No block-device operation is possible in this diagnostic.
    command = command[:command.index("write")] + ["--help"]
    child = subprocess.run(command, env=subprocess_environment(restart_app=True),
                           capture_output=True, text=True, timeout=60, check=True)
    if "write" not in child.stdout or "check" not in child.stdout:
        raise RuntimeError("Bundled relaunch did not return expected CLI help")
    print("Independent AppImage relaunch: CLI help passed")
    return 0


def main():
    if sys.stdout is not None:
        sys.stdout.reconfigure(line_buffering=True)
    if sys.stderr is not None:
        sys.stderr.reconfigure(line_buffering=True)
    if sys.argv[1:] == ["--self-test"]:
        return self_test()
    if len(sys.argv) > 1:
        return cli_main()
    gui_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""macOS bundled entrypoint and non-destructive GUI/runtime diagnostic."""
import json
from pathlib import Path
import platform
import subprocess
import sys
import traceback

from macos_gui import MacUSB, build_write_command, main as gui_main
import macos_usb
from linux_usb import Device, subprocess_environment


def self_test(report):
    import tkinter as tk

    result = {"machine": platform.machine(), "frozen": bool(getattr(sys, "frozen", False))}
    try:
        if sys.platform != "darwin" or not result["frozen"]:
            raise RuntimeError("This diagnostic requires the macOS frozen application")
        devices = macos_usb.list_devices()
        result["discovery"] = "passed"
        result["usb_devices"] = len(devices)
        root = tk.Tk()
        try:
            MacUSB(root)
            root.update()
            result.update(gui="passed", tk=tk.TkVersion)
        finally:
            root.destroy()
        dummy = Device("/dev/disk99999", 16 * 2**30, "diagnostic", "unused", (), "", ("unused",))
        command = build_write_command(Path("/tmp/unused.iso"), dummy, dummy.path)
        command = command[:command.index("write")] + ["--help"]
        child = subprocess.run(command, text=True, capture_output=True, timeout=60,
                               env=subprocess_environment(restart_app=True), check=True)
        if "write" not in child.stdout or "list" not in child.stdout:
            raise RuntimeError("The independent CLI process did not produce expected help")
        result.update(restart="passed", status="passed")
        code = 0
    except Exception:
        result.update(status="failed", error=traceback.format_exc())
        code = 1
    Path(report).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return code


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test-report":
        return self_test(sys.argv[2])
    if len(sys.argv) > 1:
        return macos_usb.main()
    gui_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

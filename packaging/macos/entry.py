"""macOS bundled entrypoint and non-destructive GUI/runtime diagnostic."""
import json
from pathlib import Path
import platform
import subprocess
import sys
import traceback

from macos_gui import MacUSB, build_write_command
import macos_usb
import platform_selector
import raw_usb
from gnu_linux_gui import GNULinuxUSB
from image_download import bundled_aria2, aria_environment
from linux_usb import Device, subprocess_environment


def self_test(report):
    import tkinter as tk

    result = {"machine": platform.machine(), "frozen": bool(getattr(sys, "frozen", False))}
    try:
        if sys.platform != "darwin" or not result["frozen"]:
            raise RuntimeError("This diagnostic requires the macOS frozen application")
        import ssl
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
        if context.cert_store_stats().get("x509_ca", 0) < 1:
            raise RuntimeError("Bundled HTTPS certificate roots are missing")
        result["https_certificate_roots"] = "passed"
        aria = bundled_aria2()
        if not aria:
            raise RuntimeError("Bundled aria2 downloader is missing")
        aria_result = subprocess.run([aria, "--no-conf=true", "--version"], capture_output=True,
                                     text=True, timeout=30, check=True,
                                     env=aria_environment(aria))
        if "aria2" not in aria_result.stdout.lower() or "bittorrent" not in aria_result.stdout.lower():
            raise RuntimeError("Bundled aria2 does not advertise BitTorrent support")
        result["bundled_torrent_downloader"] = "passed"
        devices = macos_usb.list_devices()
        result["discovery"] = "passed"
        result["usb_devices"] = len(devices)
        root = tk.Tk()
        try:
            MacUSB(root)
            root.update()
            def labels(widget):
                found = []
                for child in widget.winfo_children():
                    if "text" in child.keys():
                        found.append(str(child.cget("text")))
                    found.extend(labels(child))
                return found
            if "Install Homebrew" not in labels(root):
                raise RuntimeError("Homebrew installation button missing")
            result.update(gui="passed", tk=tk.TkVersion)
        finally:
            root.destroy()
        root = tk.Tk()
        try:
            platform_selector.OSChooser(root)
            root.update()
            actual = {text.casefold() for text in labels(root)}
            if not {"windows", "gnu/linux"}.issubset(actual):
                raise RuntimeError("Both operating-system choices must be present")
            result["selector"] = "passed"
        finally:
            root.destroy()
        raw_usb.list_devices = lambda: []
        root = tk.Tk()
        try:
            GNULinuxUSB(root)
            root.update()
            result["gnu_linux_gui"] = "passed"
        finally:
            root.destroy()
        dummy = Device("/dev/disk99999", 16 * 2**30, "diagnostic", "unused", (), "", ("unused",))
        command = build_write_command(Path("/tmp/unused.iso"), dummy, dummy.path)
        command = command[:command.index("write")] + ["--help"]
        child = subprocess.run(command, text=True, capture_output=True, timeout=60,
                               env=subprocess_environment(restart_app=True), check=True)
        if "write" not in child.stdout or "list" not in child.stdout:
            raise RuntimeError("The independent CLI process did not produce expected help")
        gnu_child = subprocess.run([sys.executable, "--gnu-linux", "--help"], text=True,
                                   capture_output=True, timeout=60, check=True,
                                   env=subprocess_environment(restart_app=True))
        if "write" not in gnu_child.stdout:
            raise RuntimeError("The independent GNU/Linux CLI did not produce help")
        result["gnu_linux_restart"] = "passed"
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
    if sys.argv[1:2] == ["--gnu-linux"]:
        sys.argv.pop(1)
        return raw_usb.main()
    if len(sys.argv) > 1:
        return macos_usb.main()
    platform_selector.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

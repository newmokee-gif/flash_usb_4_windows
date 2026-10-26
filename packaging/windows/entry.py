"""Native Windows launcher and non-destructive frozen-runtime diagnostic."""
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile
import traceback

import V2
import platform_selector
import raw_usb
from gnu_linux_gui import GNULinuxUSB


def restore_cli_streams():
    """Restore redirected pipes hidden by PyInstaller's windowed bootloader."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
                                      wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE),
                                      wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    process = kernel.GetCurrentProcess()
    invalid = ctypes.c_void_p(-1).value
    for name, number in (("stdout", -11), ("stderr", -12)):
        if getattr(sys, name) is not None:
            continue
        handle = kernel.GetStdHandle(wintypes.DWORD(number))
        duplicate = wintypes.HANDLE()
        if handle and handle != invalid and kernel.DuplicateHandle(
                process, handle, process, ctypes.byref(duplicate), 0, False, 2):
            try:
                descriptor = msvcrt.open_osfhandle(duplicate.value, os.O_WRONLY | os.O_BINARY)
            except Exception:
                kernel.CloseHandle(duplicate)
                raise
            stream = os.fdopen(descriptor, "w", buffering=1, encoding="utf-8", errors="replace")
        else:
            stream = open(os.devnull, "w", encoding="utf-8")
        setattr(sys, name, stream)


def labels(widget):
    found = []
    for child in widget.winfo_children():
        if "text" in child.keys():
            found.append(str(child.cget("text")))
        found.extend(labels(child))
    return found


def verify_runtime_architecture(arm):
    expected = 0xAA64 if arm else 0x8664
    count = 0
    candidates = [Path(sys.executable), *Path(sys._MEIPASS).rglob("*")]
    for path in candidates:
        if not path.is_file() or path.suffix.lower() not in (".exe", ".dll", ".pyd"):
            continue
        with path.open("rb") as stream:
            header = stream.read(64)
            if len(header) != 64 or header[:2] != b"MZ":
                raise RuntimeError(f"Invalid PE file: {path.name}")
            stream.seek(struct.unpack_from("<I", header, 0x3C)[0])
            if stream.read(6) != b"PE\0\0" + expected.to_bytes(2, "little"):
                raise RuntimeError(f"Unexpected native architecture: {path.name}")
        count += 1
    if count < 2:
        raise RuntimeError("Frozen Python runtime was not included")
    return count


def self_test(report):
    result = {"machine": platform.machine(), "frozen": bool(getattr(sys, "frozen", False))}
    try:
        arm = V2.is_arm64_host()
        if arm and not V2.is_arm64_executable(sys.executable):
            raise RuntimeError("The diagnostic requires a native ARM64 Windows executable")
        result["native_pe_files"] = verify_runtime_architecture(arm)
        # No network, disk enumeration, partitioning or administrator prompt.
        with tempfile.TemporaryDirectory(prefix="winusb-arm-smoke-") as directory:
            V2.create_unique_iso_folder = lambda *args: directory
            V2.WindowsOneClickUSB.refresh_drives = lambda self: None
            os.environ["TEMP"] = directory
            root = V2.tk.Tk()
            try:
                app = V2.WindowsOneClickUSB(root)
                root.update()
                if arm and V2.windows_iso_architecture(app.win_version.get()) != "ARM64":
                    raise RuntimeError("ARM64 must be the default image architecture on ARM64")
                if "Install WinGet" not in labels(root):
                    raise RuntimeError("WinGet installation button missing")
                result.update(gui="passed", ctypes="passed", installer_button="passed", tk=V2.tk.TkVersion)
                V2.kernel32.GetLastError()
            finally:
                root.destroy()
            environment = {key: value for key, value in os.environ.items() if not key.startswith("_PYI")}
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            child = subprocess.run([sys.executable, "--gnu-linux", "--help"],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=120, check=True, env=environment)
            if "write" not in child.stdout:
                raise RuntimeError("Independent onefile GNU/Linux CLI did not return help through its pipe")
            result["gnu_linux_restart"] = "passed"
            root = V2.tk.Tk()
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
            root = V2.tk.Tk()
            try:
                GNULinuxUSB(root)
                root.update()
                result["gnu_linux_gui"] = "passed"
            finally:
                root.destroy()
        result["status"] = "passed"
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
        restore_cli_streams()
        return raw_usb.main()
    if not V2.is_admin():
        V2.run_as_admin()
    platform_selector.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

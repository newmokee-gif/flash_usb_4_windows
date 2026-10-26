"""Native Windows launcher and non-destructive frozen-runtime diagnostic."""
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import traceback

import V2


def self_test(report):
    result = {"machine": platform.machine(), "frozen": bool(getattr(sys, "frozen", False))}
    try:
        if not V2.is_arm64_host() or not V2.is_arm64_executable(sys.executable):
            raise RuntimeError("The diagnostic requires a native ARM64 Windows executable")
        # No network, disk enumeration, partitioning or administrator prompt.
        with tempfile.TemporaryDirectory(prefix="winusb-arm-smoke-") as directory:
            V2.create_unique_iso_folder = lambda *args: directory
            V2.WindowsOneClickUSB.refresh_drives = lambda self: None
            os.environ["TEMP"] = directory
            root = V2.tk.Tk()
            try:
                app = V2.WindowsOneClickUSB(root)
                root.update()
                if V2.windows_iso_architecture(app.win_version.get()) != "ARM64":
                    raise RuntimeError("ARM64 must be the default image architecture on ARM64")
                result.update(gui="passed", ctypes="passed", default_iso="ARM64", tk=V2.tk.TkVersion)
                V2.kernel32.GetLastError()
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
    if not V2.is_admin():
        V2.run_as_admin()
    root = V2.tk.Tk()
    V2.WindowsOneClickUSB(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

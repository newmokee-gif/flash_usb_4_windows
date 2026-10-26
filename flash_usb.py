#!/usr/bin/env python3
"""Platform launcher; the original Windows application remains available."""
from pathlib import Path
import runpy
import sys


def main():
    source = Path(__file__).resolve().parent / "прога"
    sys.path.insert(0, str(source))
    if sys.argv[1:] == ["install-deps"]:
        from dependencies import main as install_main
        return install_main()
    if sys.platform == "win32":
        runpy.run_path(str(source / "V2.py"), run_name="__main__")
    elif sys.platform.startswith("linux"):
        if len(sys.argv) > 1:
            runpy.run_path(str(source / "linux_usb.py"), run_name="__main__")
        else:
            from linux_gui import main as gui_main
            gui_main()
    elif sys.platform == "darwin":
        if len(sys.argv) > 1:
            runpy.run_path(str(source / "macos_usb.py"), run_name="__main__")
        else:
            from macos_gui import main as gui_main
            gui_main()
    else:
        sys.exit("Supported platforms: Windows, Linux and macOS.")


if __name__ == "__main__":
    main()

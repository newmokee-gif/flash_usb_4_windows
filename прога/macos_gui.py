"""macOS front end with system authorization; never handles passwords itself."""
import json
import os
from pathlib import Path
import shlex
import sys
import tkinter as tk

import macos_usb
from linux_gui import LinuxUSB


# Pass the entire shell command as argv data, not interpolated AppleScript.
# shlex.join below quotes every pathname and confirmation value separately.
AUTHORIZATION_SCRIPT = '''on run argv
    return do shell script (item 1 of argv) with administrator privileges
end run'''


def build_write_command(iso, device, confirmation):
    arguments = ["write", str(Path(iso).resolve()), device.path, "--confirm", confirmation,
                 "--expected-identity", json.dumps(device.identity)]
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, "-B", "-u", str(Path(__file__).with_name("macos_usb.py")), *arguments]


def elevation_command(command):
    if os.geteuid() == 0:
        return list(command)
    # Explicit reset survives the environment reset performed by macOS's
    # authorization service. No password is passed to this command or logged.
    shell_command = shlex.join(["/usr/bin/env", "PYINSTALLER_RESET_ENVIRONMENT=1", *command])
    return ["/usr/bin/osascript", "-e", AUTHORIZATION_SCRIPT, shell_command]


class MacUSB(LinuxUSB):
    def __init__(self, root):
        super().__init__(root)
        root.title("WinUSB Creator — macOS / UEFI")
        self.append("macOS: для больших WIM установите Homebrew wimlib: brew install wimlib.")
        self.append("Размонтируйте тома USB в Дисковой утилите; не извлекайте сам накопитель.")

    def enumerate_devices(self):
        return macos_usb.list_devices()

    def missing_dependencies(self):
        return macos_usb.check_dependencies()

    def prepare_write_command(self, iso, device, confirmation):
        self.append("После подтверждения macOS начнётся проверка и запись. Журнал будет показан после завершения; не отключайте USB.")
        return elevation_command(build_write_command(iso, device, confirmation))


def main():
    root = tk.Tk()
    MacUSB(root)
    root.mainloop()


if __name__ == "__main__":
    main()

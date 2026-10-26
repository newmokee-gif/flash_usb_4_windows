"""Install runtime dependencies using the host's package manager."""
import os
import platform
import shutil
import subprocess
import sys


def commands(system, distro="", machine=""):
    if system == "Linux":
        if distro in ("debian", "ubuntu"):
            return [["apt-get", "update"], ["apt-get", "install", "-y", "python3", "python3-tk", "util-linux", "mount", "parted", "dosfstools", "wimtools", "udev", "pkexec", "fonts-dejavu-core", "aria2", "ca-certificates"]]
        if distro == "fedora":
            return [["dnf", "install", "-y", "python3", "python3-tkinter", "util-linux", "parted", "dosfstools", "wimlib-utils", "systemd-udev", "polkit", "dejavu-sans-fonts", "aria2", "ca-certificates"]]
        if distro == "arch":
            return [["pacman", "-Syu", "--needed", "python", "tk", "util-linux", "parted", "dosfstools", "wimlib", "systemd", "polkit", "ttf-dejavu", "aria2", "ca-certificates"]]
        raise RuntimeError("Debian, Ubuntu, Fedora and Arch are supported.")
    if system == "Darwin":
        return [["brew", "install", "wimlib", "aria2"]]
    if system == "Windows":
        arch = {"arm64": "arm64", "aarch64": "arm64", "amd64": "x64", "x86_64": "x64"}.get(machine.lower())
        if not arch:
            raise RuntimeError("Windows x64 and ARM64 are supported.")
        return [["winget", "install", "--exact", "--id", "aria2.aria2", "--source", "winget", "--architecture", arch]]
    raise RuntimeError("Unsupported operating system.")


def install():
    system = platform.system()
    distro = platform.freedesktop_os_release().get("ID", "") if system == "Linux" else ""
    machine = os.environ.get("PROCESSOR_ARCHITEW6432", platform.machine()) if system == "Windows" else platform.machine()
    plan = commands(system, distro, machine)
    if system == "Darwin" and os.geteuid() == 0:
        raise RuntimeError("Install Homebrew dependencies without sudo.")
    for command in plan:
        search = None if system == "Windows" else "/usr/sbin:/usr/bin:/sbin:/bin:/opt/homebrew/bin:/usr/local/bin"
        executable = shutil.which(command[0], path=search)
        if not executable:
            raise RuntimeError(f"{command[0]} was not found. Install your system package manager and retry the command.")
        command = [executable, *command[1:]]
        if system == "Linux" and os.geteuid() != 0:
            sudo = shutil.which("sudo", path="/usr/bin:/bin")
            if not sudo:
                raise RuntimeError("Run install-deps from an administrator terminal.")
            command.insert(0, sudo)
        print("Installing:", " ".join(command), flush=True)
        subprocess.run(command, check=True)
    print("Installation complete. Restart the application.")


def main():
    try:
        install()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Could not install dependencies: {exc}") from exc


if __name__ == "__main__":
    main()

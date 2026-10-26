#!/usr/bin/env python3
"""Build a genuine x86_64 AppImage with bundled Python and Tcl/Tk.

Run inside Debian 12 (glibc 2.36), using a venv with PyInstaller==6.22.3.
Required packages: python3-venv, python3-tk, libpython3.11, binutils, ca-certificates.
Disk utilities and polkit deliberately remain host system dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request


VERSION = "v0.2.0-linux.2"
ARTIFACT = f"flash-usb-{VERSION}-x86_64.AppImage"
PYINSTALLER_VERSION = "6.22.3"
# Binary hashes are GitHub's authoritative release-asset SHA256 digests:
# https://github.com/AppImage/appimagetool/releases/expanded_assets/1.9.1
# https://github.com/AppImage/type2-runtime/releases/expanded_assets/20251108
DOWNLOADS = {
    "appimagetool.AppImage": (
        "https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage",
        "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0",
    ),
    "runtime-x86_64": (
        "https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64",
        "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d",
    ),
    "runtime-LICENSE": (
        "https://raw.githubusercontent.com/AppImage/type2-runtime/20251108/LICENSE",
        "aa154fc9070614bbe7921f89db11efd1dba7a1f3a41685958110e2230f9c0ca1",
    ),
}


def download_verified(url, digest, target):
    request = urllib.request.Request(url, headers={"User-Agent": "flash-usb-appimage-builder"})
    with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as stream:
        shutil.copyfileobj(response, stream)
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise RuntimeError(f"Checksum mismatch: {target.name}; refusing to execute downloaded tooling")
    print(f"Verified {target.name}: SHA256 {digest}", flush=True)


def copy(source, destination, executable=False):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(0o755 if executable else 0o644)


def build(source, output, work):
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise RuntimeError("This build target requires Linux x86_64")
    libc, libc_version = platform.libc_ver()
    if libc != "glibc" or tuple(map(int, libc_version.split(".")[:2])) > (2, 36):
        raise RuntimeError("Build on Debian 12 / glibc 2.36 or older to preserve AppImage compatibility")
    version = importlib.metadata.version("pyinstaller")
    if version != PYINSTALLER_VERSION:
        raise RuntimeError(f"Install PyInstaller=={PYINSTALLER_VERSION}; found {version}")
    import tkinter  # Fail early if the build interpreter cannot bundle Tcl/Tk.

    for name, (url, digest) in DOWNLOADS.items():
        download_verified(url, digest, work / name)
    tool = work / "appimagetool.AppImage"
    tool.chmod(0o755)
    dist = work / "dist"
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
        "--name", "flash-usb", "--paths", str(source / "прога"),
        "--distpath", str(dist), "--workpath", str(work / "pyinstaller"),
        "--specpath", str(work), str(source / "packaging/appimage/entry.py"),
    ], check=True, cwd=work)

    appdir = work / "AppDir"
    shutil.copytree(dist / "flash-usb", appdir / "usr/lib/flash-usb")
    appdir.chmod(0o755)
    copy(source / "packaging/appimage/AppRun", appdir / "AppRun", True)
    copy(source / "packaging/flash-usb.desktop", appdir / "flash-usb.desktop")
    copy(source / "packaging/flash-usb.svg", appdir / "flash-usb.svg")
    copy(source / "packaging/flash-usb.svg", appdir / ".DirIcon")
    (appdir / "usr/bin").mkdir(parents=True)
    (appdir / "usr/bin/flash-usb").symlink_to("../lib/flash-usb/flash-usb")
    docs = appdir / "usr/share/doc/flash-usb"
    copy(source / "LICENSE", docs / "LICENSE")
    copy(source / "README.md", docs / "README.md")
    copy(work / "runtime-LICENSE", docs / "third-party/AppImage-runtime-LICENSE")
    # Preserve distro copyright notices for the collected Python/Tk/X11 shared
    # libraries. This intentionally includes extra builder-package notices.
    for copyright_file in sorted(Path("/usr/share/doc").glob("*/copyright")):
        if copyright_file.is_file():
            copy(copyright_file, docs / "third-party/debian" / copyright_file.parent.name / "copyright")
    distribution = importlib.metadata.distribution("pyinstaller")
    for item in distribution.files or []:
        if "license" in str(item).lower() or "copying" in str(item).lower():
            origin = Path(distribution.locate_file(item))
            if origin.is_file():
                copy(origin, docs / "third-party/pyinstaller" / origin.name)
    for item in appdir.rglob("*"):
        if not item.is_symlink():
            item.chmod(0o755 if item.is_dir() else item.stat().st_mode | 0o444)
    artifact = output / ARTIFACT
    environment = dict(os.environ, ARCH="x86_64", VERSION=VERSION)
    subprocess.run([
        str(tool), "--appimage-extract-and-run", "--runtime-file", str(work / "runtime-x86_64"),
        "--mksquashfs-opt", "-processors", "--mksquashfs-opt", "2",
        str(appdir), str(artifact),
    ], check=True, env=environment, cwd=work)
    artifact.chmod(0o755)
    checksum = hashlib.sha256(artifact.read_bytes()).hexdigest()
    (output / (ARTIFACT + ".sha256")).write_text(f"{checksum}  {ARTIFACT}\n", encoding="utf-8")
    print(artifact)
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flash-usb-appimage-") as temp:
        build(args.source.resolve(), output, Path(temp))


if __name__ == "__main__":
    main()

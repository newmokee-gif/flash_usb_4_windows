#!/usr/bin/env python3
"""Build a native x86_64 or aarch64 AppImage with bundled Python and Tcl/Tk.

Run inside Debian 12 (glibc 2.36), using a venv with PyInstaller==6.22.3.
Required packages: python3-venv, python3-tk, libpython3.11, binutils, ca-certificates, aria2.
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
import struct
import subprocess
import sys
import tempfile
import urllib.request


VERSION = "v0.3.2"
PYINSTALLER_VERSION = "6.22.3"
# Binary hashes are GitHub's authoritative release-asset SHA256 digests:
# https://github.com/AppImage/appimagetool/releases/expanded_assets/1.9.1
# https://github.com/AppImage/type2-runtime/releases/expanded_assets/20251108
APPIMAGETOOL_SHA256 = {
    "x86_64": "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0",
    "aarch64": "f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158",
}
RUNTIME_SHA256 = {
    "x86_64": "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d",
    "aarch64": "00cbdfcf917cc6c0ff6d3347d59e0ca1f7f45a6df1a428a0d6d8a78664d87444",
}


def native_architecture():
    """Select tools matching the actual builder, never a cross-build label."""
    if sys.platform != "linux":
        raise RuntimeError("AppImages must be built on Linux")
    machine = platform.machine().lower()
    architecture = {
        "x86_64": "x86_64", "amd64": "x86_64",
        "aarch64": "aarch64", "arm64": "aarch64",
    }.get(machine)
    if architecture is None:
        raise RuntimeError(f"Unsupported build architecture {machine!r}; use Linux x86_64 or aarch64")
    return architecture


def downloads_for(architecture):
    return {
        "appimagetool.AppImage": (
            f"https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-{architecture}.AppImage",
            APPIMAGETOOL_SHA256[architecture],
        ),
        f"runtime-{architecture}": (
            f"https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-{architecture}",
            RUNTIME_SHA256[architecture],
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


def verify_elf_architecture(path, architecture):
    """Refuse a helper from another architecture, including emulated builders."""
    with Path(path).open("rb") as stream:
        header = stream.read(64)
    expected = {"x86_64": 62, "aarch64": 183}[architecture]
    if (len(header) < 20 or header[:6] != b"\x7fELF\x02\x01"
            or struct.unpack_from("<H", header, 18)[0] != expected):
        raise RuntimeError(f"Expected native {architecture} ELF64 helper: {path}")


def build(source, output, work):
    architecture = native_architecture()
    libc, libc_version = platform.libc_ver()
    if libc != "glibc" or tuple(map(int, libc_version.split(".")[:2])) > (2, 36):
        raise RuntimeError("Build on Debian 12 / glibc 2.36 or older to preserve AppImage compatibility")
    version = importlib.metadata.version("pyinstaller")
    if version != PYINSTALLER_VERSION:
        raise RuntimeError(f"Install PyInstaller=={PYINSTALLER_VERSION}; found {version}")
    import tkinter  # Fail early if the build interpreter cannot bundle Tcl/Tk.

    aria = shutil.which("aria2c")
    if not aria:
        raise RuntimeError("Install the native Debian aria2 package before building")
    verify_elf_architecture(aria, architecture)
    aria_version = subprocess.check_output([aria, "--version"], text=True)
    if "BitTorrent" not in aria_version:
        raise RuntimeError("Bundled aria2 must support BitTorrent")

    for name, (url, digest) in downloads_for(architecture).items():
        download_verified(url, digest, work / name)
    tool = work / "appimagetool.AppImage"
    tool.chmod(0o755)
    dist = work / "dist"
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
        "--name", "flash-usb", "--paths", str(source / "прога"),
        "--add-binary", f"{aria}:.",
        "--distpath", str(dist), "--workpath", str(work / "pyinstaller"),
        "--specpath", str(work), str(source / "packaging/appimage/entry.py"),
    ], check=True, cwd=work)

    appdir = work / "AppDir"
    shutil.copytree(dist / "flash-usb", appdir / "usr/lib/flash-usb")
    verify_elf_architecture(appdir / "usr/lib/flash-usb/_internal/aria2c", architecture)
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
    artifact_name = f"flash-usb-{VERSION}-{architecture}.AppImage"
    artifact = output / artifact_name
    environment = dict(os.environ, ARCH=architecture, VERSION=VERSION)
    subprocess.run([
        str(tool), "--appimage-extract-and-run", "--runtime-file", str(work / f"runtime-{architecture}"),
        "--mksquashfs-opt", "-processors", "--mksquashfs-opt", "2",
        str(appdir), str(artifact),
    ], check=True, env=environment, cwd=work)
    artifact.chmod(0o755)
    checksum = hashlib.sha256(artifact.read_bytes()).hexdigest()
    (output / (artifact_name + ".sha256")).write_text(f"{checksum}  {artifact_name}\n", encoding="utf-8")
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

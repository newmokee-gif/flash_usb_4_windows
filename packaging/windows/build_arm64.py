#!/usr/bin/env python3
"""Build and verify one self-contained native Windows x64 or ARM64 EXE."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile


NAME = "WinUSB-Creator-Windows-ARM64"


def require_arm64_pe(path, machine=0xAA64):
    with Path(path).open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise RuntimeError(f"Not PE: {path}")
        stream.seek(0x3C)
        offset = int.from_bytes(stream.read(4), "little")
        stream.seek(offset)
        if stream.read(6) != b"PE\0\0" + machine.to_bytes(2, "little"):
            raise RuntimeError(f"Unexpected PE architecture: {path}")


def main():
    global NAME
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arch", choices=("ARM64", "x64"), default="ARM64")
    args = parser.parse_args()
    allowed = ("arm64", "aarch64") if args.arch == "ARM64" else ("amd64", "x86_64")
    machine = 0xAA64 if args.arch == "ARM64" else 0x8664
    NAME = "WinUSB-Creator-Windows-" + args.arch
    if sys.platform != "win32" or platform.machine().lower() not in allowed or struct.calcsize("P") != 8:
        raise RuntimeError(f"Build using native 64-bit {args.arch} Python on matching Windows hardware")
    require_arm64_pe(sys.executable, machine)
    if importlib.metadata.version("pyinstaller") != "6.22.3":
        raise RuntimeError("Install PyInstaller==6.22.3")
    source = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="winusb-arm-build-") as directory:
        work = Path(directory)
        docs = work / "Documentation"
        docs.mkdir()
        for name in ("LICENSE", "README.md"):
            shutil.copyfile(source / name, docs / name)
        license_path = Path(sys.base_prefix) / "LICENSE.txt"
        if license_path.is_file():
            shutil.copyfile(license_path, docs / "Python-LICENSE.txt")
        distribution = importlib.metadata.distribution("pyinstaller")
        for item in distribution.files or []:
            if "copying" in str(item).lower() or "license" in str(item).lower():
                candidate = Path(distribution.locate_file(item))
                if candidate.is_file():
                    shutil.copyfile(candidate, docs / ("PyInstaller-" + candidate.name))
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
            "--name", NAME, "--paths", str(source / "прога"),
            "--icon", str(source / "прога/app_icon.ico"),
            "--add-data", str(docs) + os.pathsep + "Documentation",
            "--distpath", str(work / "dist"), "--workpath", str(work / "work"),
            "--specpath", str(work), str(source / "packaging/windows/entry.py"),
        ], check=True)
        exe = work / "dist" / (NAME + ".exe")
        require_arm64_pe(exe, machine)
        # The frozen diagnostic checks all PE files in its actual onefile
        # extraction directory, not a separately built approximation.
        report = work / "smoke.json"
        result = subprocess.run([str(exe), "--self-test-report", str(report)], timeout=120)
        if not report.exists():
            raise RuntimeError(f"Frozen GUI produced no smoke report; exit={result.returncode}")
        print(report.read_text(encoding="utf-8"), flush=True)
        result.check_returncode()
        if json.loads(report.read_text(encoding="utf-8"))["status"] != "passed":
            raise RuntimeError("Frozen GUI check failed")
        artifact = output / exe.name
        shutil.copyfile(exe, artifact)
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        (output / f"SHA256SUMS-Windows-{args.arch}.txt").write_text(f"{digest}  {artifact.name}\n", encoding="utf-8")
        print(f"Built and verified {artifact}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build and verify a genuinely native Windows ARM64 GUI distribution."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile


NAME = "WinUSB-Creator-Windows-ARM64"


def require_arm64_pe(path):
    with Path(path).open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise RuntimeError(f"Not PE: {path}")
        stream.seek(0x3C)
        offset = int.from_bytes(stream.read(4), "little")
        stream.seek(offset)
        if stream.read(6) != b"PE\0\0\x64\xaa":
            raise RuntimeError(f"Not native ARM64 PE: {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine().lower() not in ("arm64", "aarch64") or struct.calcsize("P") != 8:
        raise RuntimeError("Build using native 64-bit ARM64 Python on Windows ARM64")
    require_arm64_pe(sys.executable)
    if importlib.metadata.version("pyinstaller") != "6.22.3":
        raise RuntimeError("Install PyInstaller==6.22.3")
    source = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="winusb-arm-build-") as directory:
        work = Path(directory)
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
            "--name", NAME, "--paths", str(source / "прога"),
            "--icon", str(source / "прога/app_icon.ico"),
            "--distpath", str(work / "dist"), "--workpath", str(work / "work"),
            "--specpath", str(work), str(source / "packaging/windows/entry.py"),
        ], check=True)
        package = work / "dist" / NAME
        exe = package / (NAME + ".exe")
        require_arm64_pe(exe)
        # All collected binary extensions/libraries must also be ARM64.
        for path in package.rglob("*"):
            if path.suffix.lower() in (".dll", ".pyd", ".exe"):
                require_arm64_pe(path)
        report = work / "smoke.json"
        result = subprocess.run([str(exe), "--self-test-report", str(report)], timeout=120)
        if not report.exists():
            raise RuntimeError(f"Frozen GUI produced no smoke report; exit={result.returncode}")
        print(report.read_text(encoding="utf-8"), flush=True)
        result.check_returncode()
        if json.loads(report.read_text(encoding="utf-8"))["status"] != "passed":
            raise RuntimeError("Frozen GUI check failed")
        shutil.copyfile(source / "LICENSE", package / "LICENSE")
        shutil.copyfile(source / "README.md", package / "README.md")
        shutil.copyfile(source / "packaging/windows/README-ARM64.txt", package / "README-ARM64.txt")
        # Redistributed Python/PyInstaller runtime license notices.
        for license_path in (Path(sys.base_prefix) / "LICENSE.txt",):
            if license_path.exists():
                shutil.copyfile(license_path, package / "Python-LICENSE.txt")
        distribution = importlib.metadata.distribution("pyinstaller")
        for item in distribution.files or []:
            if "copying" in str(item).lower() or "license" in str(item).lower():
                candidate = Path(distribution.locate_file(item))
                if candidate.is_file():
                    shutil.copyfile(candidate, package / ("PyInstaller-" + candidate.name))
        artifact = Path(shutil.make_archive(str(output / NAME), "zip", work / "dist", NAME))
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        (output / "SHA256SUMS-Windows-ARM64.txt").write_text(f"{digest}  {artifact.name}\n", encoding="utf-8")
        print(f"Built and verified {artifact}")


if __name__ == "__main__":
    main()

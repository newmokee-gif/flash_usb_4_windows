#!/usr/bin/env python3
"""Build native macOS15+ .app ZIP distributions with bundled Python/Tk.

Requires the matching native Python3.13, PyInstaller==6.22.3 and Xcode tools.
The app is ad-hoc signed for integrity, not Developer ID signed/notarized.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile


PYINSTALLER_VERSION = "6.22.3"
ARCH_NAMES = {"arm64": "AppleSilicon", "x86_64": "Intel"}
MACHO_MAGIC = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
               b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}


def verify_architecture(bundle, expected):
    count = 0
    for candidate in bundle.rglob("*"):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        with candidate.open("rb") as stream:
            magic = stream.read(4)
        if magic not in MACHO_MAGIC:
            continue
        architectures = subprocess.check_output(["/usr/bin/lipo", "-archs", str(candidate)], text=True).split()
        if architectures != [expected]:
            raise RuntimeError(f"Expected native {expected}, found {architectures}: {candidate}")
        count += 1
    if not count:
        raise RuntimeError("Bundle contains no Mach-O executable")
    print(f"Verified {count} native {expected} Mach-O binaries", flush=True)


def check_bundle(bundle, expected, report):
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)], check=True)
    verify_architecture(bundle, expected)
    executable = bundle / "Contents/MacOS/WinUSB Creator"
    result = subprocess.run([str(executable), "--self-test-report", str(report)], timeout=120)
    if not report.exists():
        raise RuntimeError(f"App produced no GUI smoke report (exit {result.returncode})")
    content = report.read_text(encoding="utf-8")
    print(content, flush=True)
    result.check_returncode()
    data = json.loads(content)
    if data.get("status") != "passed" or data.get("machine") != expected:
        raise RuntimeError("Bundled application failed native GUI/runtime checks")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arch", choices=ARCH_NAMES, required=True)
    args = parser.parse_args()
    if sys.platform != "darwin" or platform.machine() != args.arch:
        raise RuntimeError(f"Use native {args.arch} Python on matching macOS hardware")
    translated = subprocess.run(["/usr/sbin/sysctl", "-in", "sysctl.proc_translated"], capture_output=True, text=True)
    if translated.stdout.strip() == "1":
        raise RuntimeError("Rosetta builds are not accepted as native builds")
    if importlib.metadata.version("pyinstaller") != PYINSTALLER_VERSION:
        raise RuntimeError(f"Install PyInstaller=={PYINSTALLER_VERSION}")
    source = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="winusb-macos-build-") as directory:
        work = Path(directory)
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
            "--name", "WinUSB Creator", "--target-architecture", args.arch,
            "--osx-bundle-identifier", "org.newmokee.winusbcreator",
            "--paths", str(source / "прога"), "--distpath", str(work / "dist"),
            "--workpath", str(work / "build"), "--specpath", str(work),
            str(source / "packaging/macos/entry.py"),
        ], check=True)
        bundle = work / "dist/WinUSB Creator.app"
        resources = bundle / "Contents/Resources"
        docs = resources / "Documentation"
        docs.mkdir(parents=True, exist_ok=True)
        for name in ("LICENSE", "README.md"):
            shutil.copyfile(source / name, docs / name)
        shutil.copyfile(source / "packaging/macos/README-macOS.txt", docs / "README-macOS.txt")
        for item in (Path(sys.base_prefix) / "Resources/English.lproj/Documentation/LICENSE.txt",
                     Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE"):
            if item.is_file():
                shutil.copyfile(item, docs / "Python-LICENSE.txt")
                break
        distribution = importlib.metadata.distribution("pyinstaller")
        for item in distribution.files or []:
            if "license" in str(item).lower() or "copying" in str(item).lower():
                path = Path(distribution.locate_file(item))
                if path.is_file():
                    shutil.copyfile(path, docs / ("PyInstaller-" + path.name))
        info = bundle / "Contents/Info.plist"
        data = plistlib.loads(info.read_bytes())
        data.update(LSMinimumSystemVersion="15.0", NSHighResolutionCapable=True,
                    CFBundleShortVersionString="0.2.0", CFBundleVersion="0.2.0")
        info.write_bytes(plistlib.dumps(data))
        subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(bundle)], check=True)
        check_bundle(bundle, args.arch, work / "smoke-built.json")
        filename = f"WinUSB-Creator-macOS-{ARCH_NAMES[args.arch]}.zip"
        archive = output / filename
        subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(bundle), str(archive)], check=True)
        restored = work / "restored"
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(restored)], check=True)
        check_bundle(restored / bundle.name, args.arch, work / "smoke-restored.json")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (output / f"SHA256SUMS-macOS-{ARCH_NAMES[args.arch]}.txt").write_text(f"{digest}  {filename}\n", encoding="utf-8")
        print(f"Verified native macOS archive: {archive}")


if __name__ == "__main__":
    main()

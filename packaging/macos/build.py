#!/usr/bin/env python3
"""Build native macOS15+ application DMGs with bundled Python/Tk.

Requires the matching native Python3.13, PyInstaller==6.22.3 and Xcode tools.
The app is ad-hoc signed for integrity, not Developer ID signed/notarized.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile


PYINSTALLER_VERSION = "6.22.3"
MINIMUM_MACOS = "15.0"
ARCH_NAMES = {"arm64": "AppleSilicon", "x86_64": "Intel"}
MACHO_MAGIC = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
               b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}


def verify_deployment_target(load_commands, candidate):
    """Reject binaries whose actual Mach-O target exceeds our advertised minimum."""
    targets = []
    for command in re.split(r"(?m)^\s*Load command \d+\s*$", load_commands):
        match = re.search(r"(?m)^\s*cmd\s+(LC_\w+)\s*$", command)
        if not match:
            continue
        name = match.group(1)
        if name == "LC_BUILD_VERSION":
            platforms = re.findall(r"(?m)^\s*platform\s+(\S+)\s*$", command)
            if len(platforms) != 1 or platforms[0].lower() not in ("1", "macos"):
                raise RuntimeError(f"Bundled binary does not target macOS: {candidate}: {platforms}")
            field = "minos"
        elif name == "LC_VERSION_MIN_MACOSX":
            field = "version"
        elif name.startswith("LC_VERSION_MIN_"):
            raise RuntimeError(f"Bundled binary does not target macOS: {candidate}: {name}")
        else:
            continue
        versions = re.findall(rf"(?m)^\s*{field}\s+(\S+)\s*$", command)
        if len(versions) != 1 or not re.fullmatch(r"\d+(?:\.\d+){0,2}", versions[0]):
            raise RuntimeError(f"Cannot verify macOS deployment target: {candidate}: {versions}")
        targets.append(versions[0])
    if len(targets) != 1:
        raise RuntimeError(f"Expected one macOS deployment target: {candidate}: {targets}")
    version = tuple(int(part) for part in targets[0].split("."))
    maximum = tuple(int(part) for part in MINIMUM_MACOS.split("."))
    if version + (0,) * (3 - len(version)) > maximum + (0,) * (3 - len(maximum)):
        raise RuntimeError(f"Bundled binary requires macOS {targets[0]}, exceeds {MINIMUM_MACOS}: {candidate}")


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
        load_commands = subprocess.check_output(["/usr/bin/otool", "-l", str(candidate)], text=True)
        verify_deployment_target(load_commands, candidate)
        dependencies = subprocess.check_output(["/usr/bin/otool", "-L", str(candidate)], text=True)
        for line in dependencies.splitlines()[1:]:
            dependency = line.strip().split(" (", 1)[0]
            if dependency.startswith("/") and not dependency.startswith(("/System/Library/", "/usr/lib/")):
                raise RuntimeError(f"Bundled binary links outside the application/system: {candidate}: {dependency}")
        count += 1
    if not count:
        raise RuntimeError("Bundle contains no Mach-O executable")
    print(f"Verified {count} native {expected} Mach-O binaries compatible with macOS {MINIMUM_MACOS}", flush=True)


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
    parser.add_argument("--aria2", type=Path, help="Use this native, macOS 15 compatible aria2c")
    args = parser.parse_args()
    if sys.platform != "darwin" or platform.machine() != args.arch:
        raise RuntimeError(f"Use native {args.arch} Python on matching macOS hardware")
    translated = subprocess.run(["/usr/sbin/sysctl", "-in", "sysctl.proc_translated"], capture_output=True, text=True)
    if translated.stdout.strip() == "1":
        raise RuntimeError("Rosetta builds are not accepted as native builds")
    if importlib.metadata.version("pyinstaller") != PYINSTALLER_VERSION:
        raise RuntimeError(f"Install PyInstaller=={PYINSTALLER_VERSION}")
    aria = str(args.aria2.resolve()) if args.aria2 else shutil.which(
        "aria2c", path="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
    if not aria or not Path(aria).is_file() or not os.access(aria, os.X_OK):
        raise RuntimeError("Provide native aria2c with --aria2 or install it with Homebrew")
    source = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="winusb-macos-build-") as directory:
        work = Path(directory)
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
            "--name", "WinUSB Creator", "--target-architecture", args.arch,
            "--add-binary", str(Path(aria).resolve()) + os.pathsep + ".",
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
        aria_prefix = Path(aria).resolve().parents[1]
        for name in ("COPYING", "LICENSE", "LICENSE.txt"):
            if (aria_prefix / name).is_file():
                shutil.copyfile(aria_prefix / name, docs / ("aria2-" + name))
        aria_sources = aria_prefix / "share/aria2"
        if aria_sources.is_dir():
            shutil.copytree(aria_sources, docs / "aria2")
        aria_version = subprocess.check_output([aria, "--no-conf=true", "--version"], text=True)
        (docs / "aria2-source.txt").write_text(
            aria_version + "\nCorresponding aria2 source releases: https://github.com/aria2/aria2/releases\n",
            encoding="utf-8")
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
        data.update(LSMinimumSystemVersion=MINIMUM_MACOS, NSHighResolutionCapable=True,
                    CFBundleShortVersionString="0.3.0", CFBundleVersion="0.3.0")
        info.write_bytes(plistlib.dumps(data))
        subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(bundle)], check=True)
        check_bundle(bundle, args.arch, work / "smoke-built.json")
        filename = f"WinUSB-Creator-macOS-{ARCH_NAMES[args.arch]}.dmg"
        archive = output / filename
        staging = work / "dmg-content"
        staging.mkdir()
        subprocess.run(["/usr/bin/ditto", str(bundle), str(staging / bundle.name)], check=True)
        (staging / "Applications").symlink_to("/Applications", target_is_directory=True)
        subprocess.run(["/usr/bin/hdiutil", "create", "-volname", "WinUSB Creator",
                        "-srcfolder", str(staging), "-fs", "HFS+", "-format", "UDZO",
                        str(archive)], check=True)
        subprocess.run(["/usr/bin/hdiutil", "verify", str(archive)], check=True)
        mount = work / "mounted-dmg"
        mount.mkdir()
        attached = subprocess.check_output(["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse",
                                            "-mountpoint", str(mount), "-plist", str(archive)])
        entities = plistlib.loads(attached).get("system-entities", [])
        devices = [entity["dev-entry"] for entity in entities if entity.get("dev-entry")]
        if not devices:
            raise RuntimeError("DMG attachment returned no device for safe detachment")
        try:
            restored = work / "restored"
            subprocess.run(["/usr/bin/ditto", str(mount / bundle.name), str(restored / bundle.name)], check=True)
            check_bundle(restored / bundle.name, args.arch, work / "smoke-restored.json")
        finally:
            subprocess.run(["/usr/bin/hdiutil", "detach", devices[0]], check=True)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (output / f"SHA256SUMS-macOS-{ARCH_NAMES[args.arch]}.txt").write_text(f"{digest}  {filename}\n", encoding="utf-8")
        print(f"Verified native macOS application DMG: {archive}")


if __name__ == "__main__":
    main()

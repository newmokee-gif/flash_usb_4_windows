#!/usr/bin/env python3
"""Exercise raw macOS disk claims only on a private generated virtual image.

Requires native macOS and administrator rights. Accepts no device argument and
never enumerates physical targets for writing. The high-level production USB
guard must reject this image; only its lower-level I/O is exercised here.
"""
import fcntl
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
import macos_usb
import raw_usb

CAPACITY = 128 * 1024 * 1024


def plist(*command):
    return plistlib.loads(subprocess.check_output(command))


def verify_private_virtual_disk(image, device):
    info = plist("/usr/sbin/diskutil", "info", "-plist", device)
    if info.get("VirtualOrPhysical") != "Virtual" or info.get("TotalSize") != CAPACITY:
        raise RuntimeError("Generated target is not the expected virtual disk")
    matches = []
    for attached in plist("/usr/bin/hdiutil", "info", "-plist").get("images", []):
        if Path(attached.get("image-path", "")).resolve() == image.resolve():
            matches.extend(item.get("dev-entry") for item in attached.get("system-entities", []))
    if device not in matches:
        raise RuntimeError("Device is not backed by the private fixture image")
    try:
        macos_usb._target(device)
    except macos_usb.USBError:
        pass
    else:
        raise RuntimeError("Production USB safety check unexpectedly accepted a virtual image")
    sector = info.get("DeviceBlockSize")
    if sector not in (512, 4096):
        raise RuntimeError("Unknown fixture sector size")
    return sector


def exercise(image, read_only, native_probe):
    mount = image.parent / ("readonly-mount" if read_only else "writable-mount")
    mount.mkdir()
    # Normal attachment lets DiskArbitration observe a recognizable filesystem.
    # -nomount may leave a raw/unrecognized image claimed by DiskImages itself.
    attached = plist("/usr/bin/hdiutil", "attach", "-readonly" if read_only else "-readwrite",
                     "-nobrowse", "-noautoopen", "-mountpoint", str(mount), "-plist", str(image))
    entities = attached.get("system-entities", [])
    devices = {item.get("dev-entry", "") for item in entities
               if re.fullmatch(r"/dev/disk\d+", item.get("dev-entry", ""))}
    if len(devices) != 1:
        raise RuntimeError("Cannot safely identify generated image attachment; retain fixture")
    device = devices.pop()
    try:
        sector = verify_private_virtual_disk(image, device)
        subprocess.run(["/usr/sbin/diskutil", "unmountDisk", device], check=True)
        if mount.is_mount():
            raise RuntimeError("Private virtual fixture is still mounted")
        topology = plist("/usr/sbin/diskutil", "list", "-plist", device)
        for identifier in topology.get("AllDisks", []):
            if not re.fullmatch(r"disk\d+(?:s\d+)*", identifier):
                raise RuntimeError("Invalid virtual partition identifier")
            if plist("/usr/sbin/diskutil", "info", "-plist", "/dev/" + identifier).get("MountPoint"):
                raise RuntimeError("Virtual fixture partition is still mounted")
        with open(device, "rb", buffering=0) as source:
            first_sector = source.read(sector)
        print(f"Claiming {'readonly' if read_only else 'writable'} unmounted FAT32 virtual image", flush=True)
        native = subprocess.run([str(native_probe), device], text=True, capture_output=True, timeout=20)
        print(native.stdout, end="", flush=True)
        if native.stderr:
            print(native.stderr, end="", flush=True)
        with raw_usb._MacClaim(device) as claim:
            claim.pump()
            flags = (os.O_RDONLY if read_only else os.O_RDWR) | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
            descriptor = os.open(device, flags)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISBLK(metadata.st_mode) or metadata.st_rdev != os.stat(device).st_rdev:
                    raise RuntimeError("Opened fixture identity changed")
                verify_private_virtual_disk(image, device)
                fcntl.fcntl(descriptor, 48, 1)  # F_NOCACHE, also used in production.
                disk = raw_usb._PosixDisk(descriptor, sector, pump=claim.pump)
                if read_only:
                    if disk.read(sector) != first_sector:
                        raise RuntimeError("Readonly raw fixture data changed")
                else:
                    payload = bytes(range(256)) * (sector // 256) * 8
                    if disk.write(payload) != len(payload):
                        raise RuntimeError("Short virtual raw write")
                    disk.flush()
                    disk.seek(0)
                    if disk.read(len(payload)) != payload:
                        raise RuntimeError("Virtual raw disk readback mismatch")
                claim.pump()
            finally:
                os.close(descriptor)
        if native.returncode:
            raise RuntimeError("Native C comparison could not claim the validated private virtual image")
    finally:
        subprocess.run(["/usr/bin/hdiutil", "detach", device], check=True)


def main():
    if sys.platform != "darwin" or os.geteuid() != 0:
        raise RuntimeError("Run with native macOS Python and sudo")
    work = Path(tempfile.mkdtemp(prefix="winusb-raw-virtual-smoke-"))
    image = work / "fixture.sparseimage"
    native_probe = work / "claim-probe"
    subprocess.run(["/usr/bin/clang", "-Wall", "-Wextra", "-o", str(native_probe),
                    str(Path(__file__).with_name("macos_claim_probe.c")),
                    "-framework", "DiskArbitration", "-framework", "CoreFoundation"], check=True)
    # The filesystem is created inside a new private regular image file only.
    subprocess.run(["/usr/bin/hdiutil", "create", "-type", "SPARSE", "-size", "128m",
                    "-layout", "MBRSPUD", "-fs", "FAT32", "-volname", "WINUSBRAW", str(image)], check=True)
    if not image.is_file() or image.is_symlink():
        raise RuntimeError("Generated fixture is not a regular file")
    try:
        exercise(image, read_only=True, native_probe=native_probe)
        exercise(image, read_only=False, native_probe=native_probe)
    except Exception:
        print(f"Virtual raw disk smoke failed; fixture retained at {work}", flush=True)
        raise
    shutil.rmtree(work)
    print("Native DiskArbitration claim, unbuffered virtual I/O, cache flush and readback passed", flush=True)


if __name__ == "__main__":
    main()

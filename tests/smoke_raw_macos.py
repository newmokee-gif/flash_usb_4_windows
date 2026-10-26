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


def plist(*command):
    return plistlib.loads(subprocess.check_output(command))


def verify_private_virtual_disk(image, device):
    info = plist("/usr/sbin/diskutil", "info", "-plist", device)
    if info.get("VirtualOrPhysical") != "Virtual" or info.get("TotalSize") != image.stat().st_size:
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


def exercise(image, read_only):
    attached = plist("/usr/bin/hdiutil", "attach", "-readonly" if read_only else "-readwrite",
                     "-nomount", "-nobrowse", "-noautoopen", "-imagekey",
                     "diskimage-class=CRawDiskImage", "-plist", str(image))
    entities = attached.get("system-entities", [])
    devices = {item.get("dev-entry", "") for item in entities
               if re.fullmatch(r"/dev/disk\d+", item.get("dev-entry", ""))}
    if len(devices) != 1:
        raise RuntimeError("Cannot safely identify generated image attachment; retain fixture")
    device = devices.pop()
    try:
        if any(item.get("mount-point") for item in entities):
            raise RuntimeError("Raw fixture must remain unmounted")
        sector = verify_private_virtual_disk(image, device)
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
                    if disk.read(sector) != bytes(sector):
                        raise RuntimeError("New readonly raw fixture is not empty")
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
    finally:
        subprocess.run(["/usr/bin/hdiutil", "detach", device], check=True)


def main():
    if sys.platform != "darwin" or os.geteuid() != 0:
        raise RuntimeError("Run with native macOS Python and sudo")
    work = Path(tempfile.mkdtemp(prefix="winusb-raw-virtual-smoke-"))
    image = work / "fixture.img"
    # Sparse regular file created exclusively by this test, not an existing disk.
    with image.open("xb") as stream:
        stream.truncate(64 * 1024 * 1024)
    try:
        exercise(image, read_only=True)
        exercise(image, read_only=False)
    except Exception:
        print(f"Virtual raw disk smoke failed; fixture retained at {work}", flush=True)
        raise
    shutil.rmtree(work)
    print("Native DiskArbitration claim, unbuffered virtual I/O, cache flush and readback passed", flush=True)


if __name__ == "__main__":
    main()

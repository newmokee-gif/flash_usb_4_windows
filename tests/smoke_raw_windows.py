#!/usr/bin/env python3
"""Native Windows read-only discovery smoke; never opens a physical disk.

This intentionally does not exercise raw writes or claim hardware validation.
Only device counts are printed; disk names, identities and serials stay local.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
import raw_usb


def main():
    if sys.platform != "win32":
        raise RuntimeError("This smoke test requires native Windows")
    # Resolve and type-check the real kernel32 entry points, but do not invoke
    # CreateFile, DeviceIoControl, ReadFile or WriteFile on any disk.
    native = raw_usb._WindowsDisk()
    if native.handle is not None or native.buffer is not None:
        raise RuntimeError("Native API initialization unexpectedly acquired resources")
    devices = raw_usb.list_devices()
    paths = set()
    for device in devices:
        if device.path in paths or device.size <= 0 or not device.identity:
            raise RuntimeError("Read-only discovery returned inconsistent USB metadata")
        paths.add(device.path)
        if device.safe and (device.mounted or not device.serial):
            raise RuntimeError("Read-only discovery accepted an unsafe USB")
    print(json.dumps({"status": "passed", "kernel32_bindings": "passed",
                      "usb_count": len(devices), "selectable_count": sum(d.safe for d in devices),
                      "physical_disk_handles_opened": 0, "disk_writes": 0}))


if __name__ == "__main__":
    main()

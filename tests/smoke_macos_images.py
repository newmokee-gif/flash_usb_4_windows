#!/usr/bin/env python3
"""Read-only integration check using a generated ISO, never a physical USB.

The ISO contains genuine tiny WIM files and a dummy UEFI file. It exercises
mount/plist/preflight/cleanup behavior, not installer or firmware bootability.
"""
import argparse
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
import linux_usb as portable
import macos_usb as mac


def describe_schema(label, info):
    print(f"{label} diskutil keys: {sorted(info)}", flush=True)
    flags = ("Whole", "Internal", "Mounted", "Writable", "MediaReadOnly", "ReadOnlyMedia",
             "VolumeReadOnly", "ReadOnlyVolume")
    print(f"{label} flags: " + repr({key: info[key] for key in flags if key in info}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wim", type=Path, required=True, help="Small valid WIM created by the CI fixture")
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise RuntimeError("This integration check requires native macOS")
    root = mac._info("/")
    describe_schema("root filesystem", root)
    roots = mac._root_disks(root, mac._plist(["diskutil", "apfs", "list", "-plist"]))
    if not roots:
        raise RuntimeError("Cannot establish root backing disks")
    for identifier in sorted(roots):
        describe_schema("root physical disk", mac._info("/dev/" + identifier))

    work = Path(tempfile.mkdtemp(prefix="winusb-iso-smoke-"))
    source = work / "mounted"
    source.mkdir()
    attached = None
    try:
        tree = work / "tree"
        (tree / "efi/boot").mkdir(parents=True)
        (tree / "sources").mkdir()
        (tree / "efi/boot/bootx64.efi").write_bytes(b"Synthetic test marker, not an executable")
        shutil.copyfile(args.wim, tree / "sources/boot.wim")
        shutil.copyfile(args.wim, tree / "sources/install.wim")
        image = work / "windows.iso"
        mac._run(["hdiutil", "makehybrid", "-iso", "-udf", "-o", str(image), str(tree)])
        attached = mac._attach_iso(image, source)
        describe_schema("attached virtual disk", mac._info(attached))
        info = mac._info(str(source))
        describe_schema("ISO source volume", info)
        if not mac._mount_readonly(info, allow_media=True):
            raise RuntimeError("The source ISO is not verifiably read-only")
        files, total, split = portable._validate_source(source)
        if len(files) != 3 or total <= 0 or split is not None:
            raise RuntimeError("Mounted ISO did not contain the expected fixture files")
        portable._verify_source_wims(source, files, print, runner=mac._run)
        try:
            mac._target(attached)
        except mac.USBError:
            print("Attached virtual disk correctly rejected as a physical USB target", flush=True)
        else:
            raise RuntimeError("Virtual ISO disk was incorrectly accepted as a USB target")
    finally:
        # Retain the private directory if detaching fails. Never recursively
        # remove an active mount, even when its intended content is read-only.
        if attached or source.is_mount():
            try:
                mac._run(["hdiutil", "detach", attached or str(source)])
            except Exception as exc:
                raise RuntimeError(f"ISO detach failed; retained {work}") from exc
        if source.is_mount():
            raise RuntimeError(f"ISO is still mounted; retained {work}")
        shutil.rmtree(work)
    print("Native synthetic ISO attach, WIM preflight, target rejection and detach passed", flush=True)


if __name__ == "__main__":
    main()

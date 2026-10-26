#!/usr/bin/env python3
"""Read-only integration check using a generated ISO, never a physical USB.

The ISO contains genuine tiny WIM files and a dummy UEFI file. It exercises
mount/plist/preflight/cleanup behavior, not installer or firmware bootability.
"""
import argparse
from pathlib import Path
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
import linux_usb as portable
import macos_usb as mac


def describe_schema(label, info):
    print(f"{label} diskutil keys: {sorted(info)}", flush=True)
    flags = ("Whole", "WholeDisk", "Internal", "Mounted", "Writable", "WritableMedia",
             "WritableVolume", "MediaReadOnly", "ReadOnlyMedia", "VolumeReadOnly", "ReadOnlyVolume")
    print(f"{label} flags: " + repr({key: info[key] for key in flags if key in info}), flush=True)


def check_fat32_image():
    """Exercise target-layout parsing and read-only mounting on our own image.

    hdiutil create supports FAT32 and MBRSPUD; SPARSE grows only as needed.
    No device-formatting command is used: the filesystem is created in a
    freshly generated regular file before it is attached read-only.
    """
    work = Path(tempfile.mkdtemp(prefix="winusb-fat32-smoke-"))
    image = work / "target.sparseimage"
    mountpoint = work / "mounted"
    mountpoint.mkdir()
    created = set()
    attach_attempted = False
    try:
        mac._run(["hdiutil", "create", "-type", "SPARSE", "-layout", "MBRSPUD",
                  "-fs", "FAT32", "-size", "4g", "-volname", "WINUSB", str(image)])
        if not image.is_file() or image.is_symlink():
            raise RuntimeError("FAT32 fixture is not a newly created regular image file")
        attach_attempted = True
        attached = mac._plist(["hdiutil", "attach", "-readonly", "-nomount", "-nobrowse",
                               "-noautoopen", "-plist", str(image)])
        entities = attached.get("system-entities", [])
        for item in entities:
            entry = item.get("dev-entry", "")
            if isinstance(entry, str) and re.fullmatch(r"/dev/disk\d+(?:s\d+)*", entry):
                created.add("/dev/" + mac._whole(Path(entry).name))
        if len(created) != 1:
            raise RuntimeError("Unexpected FAT32 image attachment layout")
        device = next(iter(created))
        disk_info = mac._info(device)
        describe_schema("FAT32 virtual disk", disk_info)
        if disk_info.get("VirtualOrPhysical") != "Virtual":
            raise RuntimeError("Synthetic FAT32 image is not identified as a virtual disk")
        layout = mac._plist(["diskutil", "list", "-plist", device])
        for disk in layout.get("AllDisksAndPartitions", []):
            print("FAT32 test layout: " + repr({
                "Content": disk.get("Content"),
                "PartitionContents": [part.get("Content") for part in disk.get("Partitions", [])],
            }), flush=True)
        partition = mac._new_partition(device)
        before_mount = mac._info(partition)
        describe_schema("FAT32 partition before mount", before_mount)
        print("FAT32 filesystem identity: " + repr({key: before_mount.get(key)
              for key in ("FilesystemType", "FilesystemName", "FileSystemPersonality", "VolumeName")}), flush=True)
        mac._mount_partition(partition, mountpoint, read_only=True)
        mounted_info = mac._info(partition)
        describe_schema("FAT32 read-only mount", mounted_info)
        if not mac._mount_readonly(mounted_info):
            raise RuntimeError("FAT32 virtual mount was not read-only")
    finally:
        errors = []
        if attach_attempted and not created:
            errors.append("Image attachment could not be resolved safely")
        if mountpoint.is_mount():
            try:
                mac._run(["diskutil", "unmount", str(mountpoint)])
            except Exception as exc:
                errors.append(str(exc))
        for device in sorted(created):
            try:
                mac._run(["hdiutil", "detach", device])
            except Exception as exc:
                errors.append(str(exc))
        if errors or mountpoint.is_mount():
            raise RuntimeError(f"FAT32 image cleanup failed; retained {work}: {'; '.join(errors)}")
        shutil.rmtree(work)
    print("Native temporary FAT32 layout, read-only mount and cleanup passed", flush=True)


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
        registry = mac._ioreg_ids(mac._plist(["ioreg", "-a", "-r", "-l", "-c", "IOMedia"]))
        if Path(attached).name not in registry:
            raise RuntimeError("Attached image has no verifiable IORegistry media identity")
        print("Native IORegistry media identity verified", flush=True)
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
    check_fat32_image()


if __name__ == "__main__":
    main()

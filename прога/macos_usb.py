"""macOS Windows installer writer (Intel and Apple Silicon hosts).

Creates a single FAT32 MBR partition for removable-media UEFI boot. This does
not make Windows bootable on Apple Silicon Macs. Disk operations require root.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile

import linux_usb as portable
from linux_usb import Device, USBError


DEPENDENCIES = ("diskutil", "hdiutil", "ioreg", "sync", "wimlib-imagex")
SYSTEM_PATH = "/usr/bin:/usr/sbin:/bin:/sbin:/opt/homebrew/bin:/usr/local/bin"


def check_dependencies():
    return [name for name in DEPENDENCIES if not shutil.which(name, path=SYSTEM_PATH)]


def _run(args, timeout=120):
    executable = shutil.which(args[0], path=SYSTEM_PATH)
    if not executable:
        raise USBError(f"Missing macOS tool: {args[0]} (install wimlib with Homebrew)")
    try:
        result = subprocess.run([executable, *args[1:]], capture_output=True,
                                text=True, timeout=timeout, check=False,
                                env=portable.subprocess_environment())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise USBError(f"Cannot run {args[0]}: {exc}") from exc
    if result.returncode:
        raise USBError(f"{args[0]} exited {result.returncode}: {(result.stderr or result.stdout).strip()}")
    return result


def _plist(args):
    try:
        value = plistlib.loads(_run(args).stdout.encode("utf-8"))
        if not isinstance(value, (dict, list)):
            raise ValueError("plist is not a container")
        return value
    except (ValueError, TypeError, plistlib.InvalidFileException) as exc:
        raise USBError(f"Invalid plist from {args[0]}: {exc}") from exc


def _info(device):
    result = _plist(["diskutil", "info", "-plist", device])
    if not isinstance(result, dict):
        raise USBError("Invalid diskutil info response")
    return result


def _whole(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r"disk\d+(?:s\d+)*", identifier):
        raise USBError("Invalid macOS disk identifier")
    return re.match(r"disk\d+", identifier).group()


def _ioreg_ids(tree):
    result = {}

    def visit(nodes):
        if not isinstance(nodes, list):
            raise USBError("Invalid IORegistry tree")
        for node in nodes:
            if not isinstance(node, dict):
                raise USBError("Invalid IORegistry entry")
            name = node.get("BSD Name")
            if name is not None and node.get("Whole") is True:
                if not isinstance(name, str) or not re.fullmatch(r"disk\d+", name):
                    raise USBError("Invalid IORegistry whole disk name")
                identity = node.get("IORegistryEntryID")
                if type(identity) is not int or identity <= 0:
                    raise USBError("Missing immutable IORegistry disk identity")
                if name in result and result[name] != identity:
                    raise USBError("Ambiguous IORegistry disk identity")
                result[name] = identity
            if "IORegistryEntryChildren" in node:
                visit(node["IORegistryEntryChildren"])
    visit(tree)
    return result


def _root_disks(root_info, apfs):
    if not isinstance(root_info, dict) or not isinstance(apfs, dict):
        raise USBError("Cannot identify macOS system disks")
    parent = root_info.get("ParentWholeDisk") or root_info.get("PartOfWhole")
    identifier = root_info.get("DeviceIdentifier")
    if not parent or not identifier or _whole(parent) != parent or _whole(identifier) != parent:
        raise USBError("Cannot identify the root filesystem's parent disk")
    protected = {parent}
    containers = apfs.get("Containers")
    if not isinstance(containers, list):
        raise USBError("Cannot enumerate APFS physical stores")
    root_container = root_info.get("APFSContainerReference", parent)
    matched = False
    for container in containers:
        if not isinstance(container, dict):
            raise USBError("Invalid APFS container")
        reference = container.get("ContainerReference")
        if reference not in (parent, root_container):
            continue
        matched = True
        stores = container.get("PhysicalStores")
        if not isinstance(stores, list) or not stores:
            raise USBError("Root APFS container has no identifiable physical stores")
        for store in stores:
            if not isinstance(store, dict):
                raise USBError("Invalid APFS physical store")
            protected.add(_whole(store.get("DeviceIdentifier")))
    is_apfs = any("apfs" in str(root_info.get(key, "")).lower()
                  for key in ("FilesystemType", "TypeBundle", "Content", "FilesystemName", "FileSystemPersonality"))
    snapshot = bool(re.fullmatch(r"disk\d+s\d+s\d+", identifier))
    if (is_apfs or snapshot or "APFSContainerReference" in root_info) and not matched:
        raise USBError("Root APFS physical stores could not be identified")
    if matched:
        protected.discard(parent)  # synthesized APFS container is not physical
    return protected


def _media_writable(info):
    known = []
    for key in ("ReadOnlyMedia", "MediaReadOnly", "Writable"):
        if key in info:
            value = info[key]
            if type(value) is not bool:
                return False
            known.append(value if key == "Writable" else not value)
    return bool(known) and all(known)


def _mount_readonly(info, allow_media=False):
    values = [info[key] for key in ("ReadOnlyVolume", "VolumeReadOnly") if key in info]
    if not values and allow_media:
        values = [info[key] for key in ("ReadOnlyMedia", "MediaReadOnly") if key in info]
    return bool(values) and all(type(value) is bool and value for value in values)


def _device_from_info(info, parts, root_disks, registry_id):
    """Convert complete diskutil metadata into a safe or explicitly blocked item."""
    try:
        name = info["DeviceIdentifier"]
        path = info["DeviceNode"]
        if not isinstance(name, str) or not re.fullmatch(r"disk\d+", name) or path != "/dev/" + name:
            raise ValueError("not a canonical whole disk")
        sizes = [info[key] for key in ("TotalSize", "DiskSize", "Size", "IOKitSize") if key in info]
        if not sizes or any(type(size) is not int or size <= 0 for size in sizes) or len(set(sizes)) != 1:
            raise ValueError("unknown or inconsistent disk size")
        size = sizes[0]
        if not isinstance(parts, list) or not isinstance(root_disks, set) or not root_disks:
            raise ValueError("unknown disk topology or root disk")
        reason = ""
        mounts = []
        if info.get("Whole") is not True or info.get("Internal") is not False:
            reason = "Only whole external disks are supported"
        elif info.get("VirtualOrPhysical") != "Physical" or info.get("BusProtocol") != "USB":
            reason = "Only physical USB disks are supported"
        elif not _media_writable(info):
            reason = "Read-only or unknown media state"
        elif name in root_disks:
            reason = "System/root disk"
        elif type(registry_id) is not int or registry_id <= 0:
            reason = "Immutable IORegistry identity unavailable"
        elif size < 512 * 2**20:
            reason = "Disk is too small"
        elif size > 2**41:
            reason = "Disks larger than 2 TiB are unsupported by this MBR installer layout"
        for node in [info, *parts]:
            if not isinstance(node, dict):
                raise ValueError("invalid partition metadata")
            content = str(node.get("Content", "")).lower()
            if any(kind in content for kind in ("apfs", "raid", "corestorage", "swap")):
                reason = "APFS, RAID, CoreStorage and swap targets are unsupported"
            if node.get("APFSContainerReference") or node.get("RAIDSetUUID"):
                reason = "Active APFS or RAID member"
            if node is not info:
                part_name = node.get("DeviceIdentifier")
                part_parent = node.get("ParentWholeDisk") or node.get("PartOfWhole")
                if _whole(part_name) != name or part_parent != name or node.get("Whole") is not False:
                    raise ValueError("partition parent is unverifiable")
                if any(key in node for key in ("ReadOnlyMedia", "MediaReadOnly", "Writable")) and not _media_writable(node):
                    reason = "Read-only or unknown partition state"
                if type(node.get("Mounted")) is not bool:
                    reason = "Unknown partition mount state"
                if node.get("Content") not in {"Apple_HFS", "Apple_HFSX", "EFI", "Microsoft Basic Data",
                                               "DOS_FAT_12", "DOS_FAT_16", "DOS_FAT_16B", "DOS_FAT_32",
                                               "Windows_NTFS", "Linux Filesystem", "Linux Filesystem Data"}:
                    reason = "Unknown or unsupported partition content"
            point = node.get("MountPoint")
            if point:
                if not isinstance(point, str) or not point.startswith("/"):
                    raise ValueError("invalid mountpoint")
                mounts.append(point)
            if node.get("Mounted") is True and not point:
                reason = "Mounted volume has no known mountpoint"
        if mounts and not reason:
            reason = "Mounted partitions; unmount the USB first"
        identity = (name, size, info.get("MediaName", ""), info.get("DeviceTreePath", ""), registry_id)
        return Device(path, size, info.get("MediaName", "USB"), str(registry_id or ""), tuple(mounts), reason, identity)
    except (KeyError, ValueError, TypeError) as exc:
        raise USBError(f"Incomplete disk safety information: {exc}") from exc


def _snapshot():
    topology = _plist(["diskutil", "list", "-plist", "physical"])
    if not isinstance(topology, dict) or not isinstance(topology.get("AllDisksAndPartitions"), list):
        raise USBError("Cannot enumerate physical disks")
    roots = _root_disks(_info("/"), _plist(["diskutil", "apfs", "list", "-plist"]))
    registry = _ioreg_ids(_plist(["ioreg", "-a", "-r", "-c", "IOMedia"]))
    devices = []
    for node in topology["AllDisksAndPartitions"]:
        if not isinstance(node, dict):
            raise USBError("Malformed physical disk topology")
        name = node.get("DeviceIdentifier")
        if _whole(name) != name:
            raise USBError("Expected whole physical disk")
        info = _info("/dev/" + name)
        # Internal disks never appear as selectable GUI targets.
        if info.get("Internal") is True or info.get("BusProtocol") != "USB":
            continue
        children = node.get("Partitions", [])
        if not isinstance(children, list):
            raise USBError("Unknown partition topology")
        parts = []
        for child in children:
            if not isinstance(child, dict):
                raise USBError("Invalid partition topology")
            part = _info("/dev/" + str(child.get("DeviceIdentifier", "")))
            if "Content" not in part:
                part["Content"] = child.get("Content", "")
            parts.append(part)
        if "Content" not in info:
            info["Content"] = node.get("Content", "")
        devices.append(_device_from_info(info, parts, roots, registry.get(name)))
    return devices


def list_devices():
    return _snapshot()


def _target(path, allow_mounted=False):
    if not re.fullmatch(r"/dev/disk\d+", path):
        raise USBError("Select a whole /dev/diskN, not a partition or raw-device alias")
    candidate = Path(path)
    try:
        if candidate.is_symlink() or str(candidate.resolve()) != path or not stat.S_ISBLK(candidate.stat().st_mode):
            raise USBError("Target is not a canonical block device")
    except OSError as exc:
        raise USBError(f"Cannot inspect target: {exc}") from exc
    device = next((item for item in _snapshot() if item.path == path), None)
    if device is None:
        raise USBError("Target is not a detected physical external USB disk")
    if not device.safe and not (allow_mounted and device.reason == "Mounted partitions; unmount the USB first"):
        raise USBError(device.reason)
    return device


def _same_target(original, allow_mounted=False, expected_mount=None):
    current = _target(original.path, allow_mounted=allow_mounted)
    if current.identity != original.identity:
        raise USBError("USB identity changed; refusing further disk operations")
    if allow_mounted:
        expected = (str(Path(expected_mount).resolve()),) if expected_mount else ()
        actual = tuple(str(Path(point).resolve()) for point in current.mounted)
        if actual != expected:
            raise USBError("Unexpected mounted volume appeared on the USB")


@contextmanager
def _device_lock(device):
    # Validate the device with O_NOFOLLOW, but do not hold it open while
    # DiskArbitration needs an exclusive claim during eraseDisk. Serialize this
    # application's writers with a root-owned regular lockfile; immutable
    # IORegistry identities are checked again before every destructive step.
    device_fd = lock_fd = None
    try:
        device_fd = os.open(device, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        if not stat.S_ISBLK(os.fstat(device_fd).st_mode):
            raise USBError("Lock target is not a block device")
        os.close(device_fd)
        device_fd = None
        lock_path = "/var/run/flash-usb-" + Path(device).name + ".lock"
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        metadata = os.fstat(lock_fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_nlink != 1
                or metadata.st_mode & 0o022):
            raise USBError("Unsafe disk lock file")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as exc:
        raise USBError("Another writer is already using this disk") from exc
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        if device_fd is not None:
            os.close(device_fd)


def _attach_iso(image, source):
    attached = _plist(["hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen", "-plist",
                       "-mountpoint", str(source), str(image)])
    created = set()
    try:
        entities = attached["system-entities"]
        if not isinstance(entities, list):
            raise ValueError("missing image entities")
        for item in entities:
            if not isinstance(item, dict):
                raise ValueError("invalid image entity")
            entry = item.get("dev-entry", "")
            if isinstance(entry, str) and re.fullmatch(r"/dev/disk\d+(?:s\d+)*", entry):
                created.add("/dev/" + _whole(entry.removeprefix("/dev/")))
        mounted = [item for item in entities if item.get("mount-point")]
        if len(created) != 1 or len(mounted) != 1 or Path(mounted[0]["mount-point"]).resolve() != source.resolve():
            raise ValueError("unexpected ISO mount layout")
        device = mounted[0]["dev-entry"]
        if not re.fullmatch(r"/dev/disk\d+(?:s\d+)*", device):
            raise ValueError("invalid ISO device")
        info = _info(device)
        if not _mount_readonly(info, allow_media=True):
            raise ValueError("ISO mount could not be verified read-only")
        return "/dev/" + _whole(device.removeprefix("/dev/"))
    except (USBError, KeyError, TypeError, ValueError) as exc:
        errors = []
        for entry in sorted(created):
            try:
                _run(["hdiutil", "detach", entry])
            except USBError as cleanup:
                errors.append(str(cleanup))
        detail = "; cleanup failed: " + "; ".join(errors) if errors else ""
        raise USBError(f"Cannot identify attached ISO: {exc}{detail}") from exc


def _new_partition(device):
    data = _plist(["diskutil", "list", "-plist", device])
    try:
        disks = data["AllDisksAndPartitions"]
        if len(disks) != 1 or disks[0]["DeviceIdentifier"] != Path(device).name or disks[0]["Content"] != "FDisk_partition_scheme":
            raise ValueError("expected an MBR disk")
        parts = disks[0]["Partitions"]
        if len(parts) != 1 or parts[0].get("Content") != "DOS_FAT_32":
            raise ValueError("expected one FAT32 partition")
        name = parts[0]["DeviceIdentifier"]
        if not re.fullmatch(re.escape(Path(device).name) + r"s\d+", name):
            raise ValueError("partition is not on the selected disk")
        info = _info("/dev/" + name)
        personality = info.get("FilesystemName") or info.get("FileSystemPersonality")
        if info.get("FilesystemType") != "msdos" or personality != "MS-DOS FAT32":
            raise ValueError("diskutil did not create FAT32")
        if info.get("VolumeName") != "WINUSB":
            raise ValueError("unexpected volume label after formatting")
        return "/dev/" + name
    except (KeyError, TypeError, ValueError) as exc:
        raise USBError(f"Unsafe formatted partition layout: {exc}") from exc


def _mount_partition(partition, target, read_only=False):
    args = ["diskutil", "mount"]
    if read_only:
        args.append("readOnly")
    _run([*args, "-mountPoint", str(target), partition])
    try:
        info = _info(partition)
        if info.get("Mounted") is not True or Path(info.get("MountPoint", "")).resolve() != target.resolve():
            raise USBError("Partition was not mounted at the private working directory")
        if read_only and not _mount_readonly(info):
            raise USBError("Readback mount is not read-only")
    except (USBError, OSError, TypeError, ValueError):
        _run(["diskutil", "unmount", partition])
        raise


def write_usb(iso, device, confirm, log=print, expected_identity=None):
    if sys.platform != "darwin" or os.geteuid() != 0:
        raise USBError("Writing requires macOS and administrator privileges")
    if confirm != device:
        raise USBError("Confirmation must exactly match the whole device path")
    missing = check_dependencies()
    if missing:
        raise USBError("Missing tools: " + ", ".join(missing))
    original = _target(device)
    if expected_identity is not None and tuple(expected_identity) != original.identity:
        raise USBError("USB changed after GUI selection; refresh the device list")
    image = Path(iso).expanduser().resolve(strict=True)
    if not image.is_file() or not stat.S_ISREG(image.stat().st_mode):
        raise USBError("ISO must be a regular file")
    with _device_lock(device):
        work = Path(tempfile.mkdtemp(prefix="flash-usb-macos-"))
        source, target = work / "iso", work / "usb"
        source.mkdir()
        target.mkdir()
        attached = None
        target_mounted = False
        cleanup_errors = []
        try:
            attached = _attach_iso(image, source)
            files, total, split = portable._validate_source(source)
            required = total + max(256 * 2**20, total // 20, len(files) * 65536)
            if original.size < required:
                raise USBError("USB is too small for this installer and FAT32 overhead")
            portable._verify_source_wims(source, files, log, runner=_run)
            _same_target(original)
            log(f"Erasing {device}: single FAT32 partition for UEFI removable-media boot")
            _run(["diskutil", "eraseDisk", "MS-DOS", "WINUSB", "MBRFormat", device], timeout=300)
            partition = _new_partition(device)
            _same_target(original, allow_mounted=True, expected_mount=_info(partition).get("MountPoint"))
            _run(["diskutil", "unmountDisk", device])
            _same_target(original)
            _mount_partition(partition, target)
            target_mounted = True
            _same_target(original, allow_mounted=True, expected_mount=target)
            portable._copy_and_verify(source, target, files, split, log, runner=_run)
            _run(["sync"])
            _run(["diskutil", "unmount", str(target)])
            target_mounted = False
            _same_target(original)
            _mount_partition(partition, target, read_only=True)
            target_mounted = True
            _same_target(original, allow_mounted=True, expected_mount=target)
            portable._verify_target(source, target, files, split, log, runner=_run)
        finally:
            if target_mounted or target.is_mount():
                try:
                    _run(["diskutil", "unmount", str(target)])
                except USBError as exc:
                    cleanup_errors.append(str(exc))
            if attached or source.is_mount():
                try:
                    _run(["hdiutil", "detach", attached or str(source)])
                except USBError as exc:
                    cleanup_errors.append(str(exc))
            if not cleanup_errors and not source.is_mount() and not target.is_mount():
                shutil.rmtree(work)
            else:
                raise USBError(f"Cleanup incomplete; private mount directories retained at {work}: " + "; ".join(cleanup_errors))
        log("Windows UEFI USB created, verified and unmounted")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Check macOS disk tools and Homebrew wimlib")
    commands.add_parser("list", help="List external USB disks and safety restrictions")
    write = commands.add_parser("write", help="Erase a USB and create a Windows UEFI installer")
    write.add_argument("iso")
    write.add_argument("device")
    write.add_argument("--confirm", required=True)
    write.add_argument("--expected-identity")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            missing = check_dependencies()
            print("Missing: " + ", ".join(missing) if missing else "All macOS dependencies are available")
            return int(bool(missing))
        if args.command == "list":
            devices = list_devices()
            for item in devices:
                print(item.label)
            if not devices:
                print("No external USB disks detected")
        else:
            identity = None
            if args.expected_identity:
                identity = json.loads(args.expected_identity)
                if not isinstance(identity, list):
                    raise USBError("Expected identity must be a JSON array")
            write_usb(args.iso, args.device, args.confirm, expected_identity=identity)
        return 0
    except (USBError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

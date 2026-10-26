"""Linux Windows-USB writer: GPT/FAT32, UEFI only, no external downloads.

Only unmounted, writable USB disks are accepted. Every write is destructive and
requires an exact device-path confirmation. Never run hardware tests on a disk
containing data you want to retain.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import fcntl
import json
import os
import re
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable


FAT32_MAX = 2**32 - 1
DEPENDENCIES = ("lsblk", "findmnt", "mount", "umount", "parted", "partprobe", "mkfs.fat", "wimlib-imagex", "udevadm", "blockdev")


class USBError(RuntimeError):
    """An operation failed or could not be proved safe."""


@dataclass(frozen=True)
class Device:
    path: str
    size: int
    model: str
    serial: str
    mounted: tuple[str, ...]
    reason: str
    identity: tuple = ()

    @property
    def safe(self) -> bool:
        return not self.reason

    @property
    def label(self) -> str:
        state = f" — {self.reason}" if self.reason else ""
        return f"{self.path} — {self.model or 'USB'} — {self.size / 2**30:.1f} GiB{state}"


def check_dependencies() -> list[str]:
    return [name for name in DEPENDENCIES if _tool(name) is None]


def _tool(name: str):
    # Debian omits sbin from ordinary desktop-user PATH. Privileged operations
    # must not select executable files from a writable user PATH either.
    return shutil.which(name, path="/usr/sbin:/usr/bin:/sbin:/bin")


def subprocess_environment(*, restart_app=False):
    """Keep bundled runtime libraries out of host utilities and fresh launches.

    PyInstaller prepends its private libraries to LD_LIBRARY_PATH. Passing that
    environment to the host mount/wimlib/polkit tools can load incompatible
    libraries. Change only the child's environment; Tk still needs our bundle.
    """
    environment = os.environ.copy()
    if getattr(sys, "frozen", False):
        original = environment.pop("LD_LIBRARY_PATH_ORIG", None)
        if original:
            environment["LD_LIBRARY_PATH"] = original
        else:
            environment.pop("LD_LIBRARY_PATH", None)
        if restart_app:
            for name in tuple(environment):
                if name.startswith("_PYI_") or name in ("_MEIPASS2", "TCL_LIBRARY", "TK_LIBRARY"):
                    environment.pop(name)
            # A new privileged process must initialize its own bundle, not
            # reuse a temporary/FUSE directory owned by the GUI's user.
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return environment


def _run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    executable = _tool(args[0])
    if executable is None:
        raise USBError(f"Missing system tool: {args[0]}")
    try:
        result = subprocess.run([executable, *args[1:]], text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=False,
                                env=subprocess_environment())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise USBError(f"Command failed: {args[0]}: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise USBError(f"{args[0]} exited {result.returncode}: {detail}")
    return result


def _read_devices() -> list[dict]:
    try:
        data = json.loads(_run([
            "lsblk", "--json", "--bytes", "--paths", "--output",
            "NAME,PATH,TYPE,SIZE,MODEL,SERIAL,WWN,TRAN,RO,MOUNTPOINTS,MAJ:MIN",
        ]).stdout)
        nodes = data["blockdevices"]
        if not isinstance(nodes, list):
            raise ValueError("blockdevices must be an array")
        # Validate all nodes, including those not offered as targets. Incomplete
        # topology must not accidentally make a system disk appear safe.
        for node in _walk(nodes):
            for key in ("path", "type", "size", "tran", "ro", "mountpoints", "maj:min"):
                if key not in node:
                    raise ValueError(f"lsblk omitted {key}")
            if not isinstance(node["path"], str) or not node["path"].startswith("/dev/"):
                raise ValueError("invalid block device path")
            if not isinstance(node["maj:min"], str) or not re.fullmatch(r"\d+:\d+", node["maj:min"]):
                raise ValueError("invalid device number")
            _mountpoints(node)
            _readonly(node["ro"])
            if int(node["size"]) < 0:
                raise ValueError("negative device size")
        return nodes
    except (KeyError, TypeError, ValueError) as exc:
        raise USBError(f"Cannot safely interpret lsblk output: {exc}") from exc


def _walk(nodes):
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("invalid block device record")
        yield node
        children = node.get("children", [])
        if not isinstance(children, list):
            raise ValueError("invalid block device children")
        yield from _walk(children)


def _mountpoints(node) -> tuple[str, ...]:
    points = node.get("mountpoints")
    if not isinstance(points, list) or any(p is not None and not isinstance(p, str) for p in points):
        raise ValueError("missing or invalid mountpoints")
    return tuple(p for p in points if p)


def _readonly(value) -> bool:
    if value in (True, 1, "1"):
        return True
    if value in (False, 0, "0"):
        return False
    raise ValueError("missing or invalid read-only flag")


def _device_from_node(node: dict, root_id: str) -> Device:
    try:
        descendants = list(_walk([node]))
        mounts = tuple(p for item in descendants for p in _mountpoints(item))
        reason = ""
        if node["type"] != "disk" or node["tran"] != "usb":
            reason = "Only whole USB disks are supported"
        elif any(item["maj:min"] in ({root_id} if isinstance(root_id, str) else root_id) for item in descendants):
            reason = "System/root disk"
        elif any(_readonly(item["ro"]) for item in descendants):
            reason = "Read-only disk"
        elif mounts:
            reason = "Mounted or active swap; unmount it first"
        elif any(item["type"] != "part" for item in descendants[1:]):
            reason = "Active device-mapper/RAID or unsupported child device"
        elif int(node["size"]) < 512 * 2**20:
            reason = "Disk is too small for a Windows installer"
        identity = (node["path"], int(node["size"]), node.get("model") or "",
                    node.get("serial") or "", node.get("wwn") or "", node["maj:min"])
        return Device(node["path"], int(node["size"]), node.get("model") or "",
                      node.get("serial") or "", mounts, reason, identity)
    except (KeyError, TypeError, ValueError) as exc:
        raise USBError(f"Incomplete device information: {exc}") from exc


def _root_ids() -> set[str]:
    root_dev = os.stat("/").st_dev
    root_id = f"{os.major(root_dev)}:{os.minor(root_dev)}"
    if os.major(root_dev) != 0:
        return {root_id}
    # Btrfs uses anonymous st_dev numbers, including Fedora's default root.
    # Overlay/container roots still fail closed. Enumerate every physical Btrfs
    # member to protect multi-device filesystems, not just findmnt's first source.
    try:
        filesystems = json.loads(_run(["findmnt", "--json", "--target", "/", "--output", "FSTYPE,UUID"]).stdout)["filesystems"]
        if len(filesystems) != 1 or filesystems[0]["fstype"] != "btrfs":
            return set()
        uuid = filesystems[0]["uuid"]
        if not isinstance(uuid, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", uuid):
            return set()
        members = list((Path("/sys/fs/btrfs") / uuid / "devices").iterdir())
        ids = {(member / "dev").read_text().strip() for member in members}
        if not ids or any(not re.fullmatch(r"\d+:\d+", value) for value in ids):
            return set()
        return ids
    except (USBError, OSError, KeyError, TypeError, ValueError):
        return set()


def list_devices() -> list[Device]:
    nodes = _read_devices()
    root_ids = _root_ids()
    root_known = bool(root_ids) and root_ids.issubset({n["maj:min"] for n in _walk(nodes)})
    devices = []
    for node in nodes:
        if node["type"] != "disk" or node["tran"] != "usb":
            continue
        device = _device_from_node(node, root_ids)
        try:
            device = replace(device, identity=device.identity + (_disk_sequence(device.path),))
        except USBError:
            device = replace(device, reason="Kernel disk identity is unavailable")
        if not root_known:
            device = replace(device, reason="Cannot identify root disk (overlay/container root is unsupported)")
        devices.append(device)
    return devices


def _target(path: str) -> Device:
    candidate = Path(path)
    if candidate.is_symlink() or str(candidate.resolve()) != path:
        raise USBError("Use the real whole-device path, not a symlink or alias")
    try:
        metadata = candidate.stat()
    except OSError as exc:
        raise USBError(f"Cannot inspect target: {exc}") from exc
    if not stat.S_ISBLK(metadata.st_mode):
        raise USBError("Target is not a block device")
    device = next((d for d in list_devices() if d.path == path), None)
    if device is None:
        raise USBError("Target is not a detected whole USB disk")
    if not device.safe:
        raise USBError(device.reason)
    if device.identity[5] != f"{os.major(metadata.st_rdev)}:{os.minor(metadata.st_rdev)}":
        raise USBError("Device identity changed during inspection")
    # Block devices used by dm-crypt/LVM/RAID can be in use without a mount.
    for node in _walk(_read_devices()):
        if node["path"] == path:
            for item in _walk([node]):
                holders = Path("/sys/class/block") / Path(item["path"]).name / "holders"
                try:
                    if any(holders.iterdir()):
                        raise USBError("Target has active kernel holders")
                except OSError as exc:
                    raise USBError(f"Cannot inspect target holders: {exc}") from exc
    return device


def _disk_sequence(path: str) -> str:
    try:
        return (Path("/sys/class/block") / Path(path).name / "diskseq").read_text().strip()
    except OSError as exc:
        raise USBError("Kernel disk sequence is unavailable; refusing an unverifiable target") from exc


def _validate_source(source: Path) -> tuple[list[tuple[Path, int]], int, Path | None]:
    """Validate a mounted installer before making any changes to the USB disk."""
    files = []
    normalized = {}
    total = 0
    split_wim = None
    def traversal_error(error):
        raise USBError(f"Cannot read ISO directory: {error}") from error

    for directory, dirs, names in os.walk(source, followlinks=False, onerror=traversal_error):
        for name in dirs + names:
            entry = Path(directory) / name
            relative = entry.relative_to(source)
            if entry.is_symlink():
                raise USBError(f"ISO contains a symlink: {relative}")
            if (any(c in name for c in '<>:"\\|?*') or name.endswith((" ", "."))
                    or any(ord(c) < 32 for c in name) or len(name.encode("utf-16-le")) > 510):
                raise USBError(f"Filename is not FAT32 compatible: {relative}")
            stem = name.split(".", 1)[0].upper()
            if stem in {"CON", "PRN", "AUX", "NUL"} or stem in {f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10)}:
                raise USBError(f"Reserved Windows filename: {relative}")
            key = relative.as_posix().casefold()
            if key in normalized:
                raise USBError(f"Case-insensitive FAT32 filename collision: {relative}")
            normalized[key] = relative
            metadata = entry.stat()
            if stat.S_ISDIR(metadata.st_mode):
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise USBError(f"ISO contains a non-regular file: {relative}")
            size = metadata.st_size
            if size > FAT32_MAX:
                if key != "sources/install.wim":
                    raise USBError(f"File exceeds FAT32 limit and cannot be split: {relative}")
                split_wim = relative
            total += size
            files.append((relative, size))
    names = {p.as_posix().casefold() for p, _ in files}
    if not names.intersection({"efi/boot/bootx64.efi", "efi/boot/bootaa64.efi", "efi/boot/bootia32.efi"}):
        raise USBError("ISO has no supported UEFI fallback bootloader")
    if "sources/boot.wim" not in names:
        raise USBError("ISO is not a Windows installer: sources/boot.wim is missing")
    if not names.intersection({"sources/install.wim", "sources/install.esd", "sources/install.swm"}):
        raise USBError("ISO has no Windows installation image")
    if split_wim and any(p.startswith("sources/install") and p.endswith(".swm") for p in names):
        raise USBError("ISO contains both a large install.wim and conflicting split images")
    return files, total, split_wim


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 2**20), b""):
            result.update(block)
    return result.hexdigest()


def _verify_source_wims(source: Path, files, log):
    for relative, _ in files:
        key = relative.as_posix().casefold()
        if key not in ("sources/boot.wim", "sources/install.wim", "sources/install.esd", "sources/install.swm"):
            continue
        log(f"Validating Windows image before erasing USB: {relative}")
        args = ["wimlib-imagex", "verify", str(source / relative)]
        if key.endswith(".swm"):
            # ISO filenames can be upper-case. Explicit refs avoid a glob whose
            # case differs from the image; wimlib accepts repeated --ref options.
            args.extend(f"--ref={source / part}" for part, _ in files
                        if part.parent == relative.parent and part.suffix.casefold() == ".swm")
        _run(args, timeout=7200)


def _verify_target(source: Path, target: Path, files, split_wim, log):
    for relative, size in files:
        if relative == split_wim:
            continue
        destination = target / relative
        if destination.stat().st_size != size or _digest(source / relative) != _digest(destination):
            raise USBError(f"Readback verification failed: {relative}")
    if split_wim:
        destination = target / split_wim.parent / "install.swm"
        _run(["wimlib-imagex", "verify", str(destination), f"--ref={destination.parent}/install*.swm"], timeout=7200)
    log("Readback checks passed after unmounting and flushing block-device caches")


@contextmanager
def _device_lock(device: str):
    descriptor = None
    try:
        descriptor = os.open(device, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        if not stat.S_ISBLK(os.fstat(descriptor).st_mode):
            raise USBError("Lock target is not a block device")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as exc:
        raise USBError("Another writer is already using this USB disk") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _copy_and_verify(source: Path, target: Path, files, split_wim, log: Callable[[str], None]):
    for relative, size in files:
        if relative == split_wim:
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        log(f"Copy: {relative}")
        shutil.copyfile(source / relative, destination)
        if destination.stat().st_size != size or _digest(source / relative) != _digest(destination):
            raise USBError(f"Copy verification failed: {relative}")
    if split_wim:
        destination = target / split_wim.parent / "install.swm"
        destination.parent.mkdir(parents=True, exist_ok=True)
        log("Splitting install.wim into FAT32-compatible parts (this can take time)")
        _run(["wimlib-imagex", "split", str(source / split_wim), str(destination), "3800", "--check"], timeout=7200)
        parts = sorted(destination.parent.glob("install*.swm"))
        if not parts or any(p.stat().st_size > FAT32_MAX for p in parts):
            raise USBError("Invalid or missing split WIM parts")
        _run(["wimlib-imagex", "verify", str(destination), f"--ref={destination.parent}/install*.swm"], timeout=7200)
        log("Split WIM integrity verified")


def _partition(path: str) -> str:
    _run(["partprobe", path])
    _run(["udevadm", "settle", "--timeout=30"], timeout=40)
    for attempt in range(20):
        for node in _read_devices():
            if node["path"] == path:
                parts = node.get("children", [])
                if len(parts) == 1 and parts[0]["type"] == "part":
                    if _mountpoints(parts[0]):
                        raise USBError("New partition was automounted; unmount it and retry")
                    return parts[0]["path"]
        time.sleep(0.25)
    raise USBError("New partition did not appear")


def write_usb(iso: str, device: str, confirm: str, log: Callable[[str], None] = print,
              expected_identity=None) -> None:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise USBError("Writing requires Linux and root privileges; use sudo")
    if confirm != device:
        raise USBError("Confirmation must exactly match the device path")
    # The open descriptor keeps the flock tied to the original device even if
    # it is unplugged. The diskseq/identity checks below catch path replacement.
    with _device_lock(device):
        _write_usb(iso, device, confirm, log, expected_identity)


def _write_usb(iso: str, device: str, confirm: str, log: Callable[[str], None] = print,
               expected_identity=None) -> None:
    if sys.platform != "linux":
        raise USBError("This backend requires Linux")
    if os.geteuid() != 0:
        raise USBError("Writing requires root privileges; use sudo")
    if confirm != device:
        raise USBError("Confirmation must exactly match the device path")
    missing = check_dependencies()
    if missing:
        raise USBError("Missing tools: " + ", ".join(missing))
    original = _target(device)
    if expected_identity is not None and tuple(expected_identity) != original.identity:
        raise USBError("Target changed since it was selected; refresh the device list")
    sequence = _disk_sequence(device)
    image = Path(iso).expanduser().resolve(strict=True)
    if not image.is_file() or not stat.S_ISREG(image.stat().st_mode):
        raise USBError("ISO must be a regular file")
    # The target is unmounted, but explicitly check the image's backing device.
    image_dev = image.stat().st_dev
    image_id = f"{os.major(image_dev)}:{os.minor(image_dev)}"
    for node in _read_devices():
        if node["path"] == device and any(n["maj:min"] == image_id for n in _walk([node])):
            raise USBError("ISO is stored on the target disk")
    work = Path(tempfile.mkdtemp(prefix="winusb-"))
    source, target = work / "iso", work / "usb"
    source.mkdir()
    target.mkdir()
    mounted = []
    cleanup_errors = []
    try:
        _run(["mount", "-t", "iso9660,udf", "-o", "loop,ro,nosuid,nodev,noexec", str(image), str(source)])
        mounted.append(source)
        files, total, split_wim = _validate_source(source)
        # FAT overhead, partition alignment, and split-WIM metadata margin.
        required = total + max(256 * 2**20, total // 20, len(files) * 65536)
        if original.size < required:
            raise USBError(f"USB too small: need at least {required / 2**30:.2f} GiB")
        _verify_source_wims(source, files, log)
        log(f"Validated Windows UEFI image ({len(files)} files); preparing {device}")
        current = _target(device)
        if current.identity != original.identity or _disk_sequence(device) != sequence:
            raise USBError("Target changed after selection; no data was erased")
        _run(["parted", "--script", "--align", "optimal", device, "mklabel", "gpt",
              "mkpart", "ESP", "fat32", "1MiB", "100%", "set", "1", "esp", "on"])
        partition = _partition(device)
        # Recheck kernel identity and automounts just before formatting.
        if _disk_sequence(device) != sequence or _target(device).identity != original.identity:
            raise USBError("Target changed before filesystem creation")
        _run(["mkfs.fat", "-F", "32", "-n", "WINUSB", partition])
        _run(["mount", "-o", "nosuid,nodev,noexec", partition, str(target)])
        mounted.append(target)
        _copy_and_verify(source, target, files, split_wim, log)
        log("Flushing writes and preparing read-only readback verification")
        _run(["umount", str(target)], timeout=300)
        mounted.remove(target)
        if _disk_sequence(device) != sequence or _target(device).identity != original.identity:
            raise USBError("Target changed before readback verification")
        _run(["blockdev", "--flushbufs", partition])
        _run(["mount", "-o", "ro,nosuid,nodev,noexec", partition, str(target)])
        mounted.append(target)
        _verify_target(source, target, files, split_wim, log)
    finally:
        for mountpoint in reversed(mounted):
            try:
                _run(["umount", str(mountpoint)], timeout=300)
            except USBError as exc:
                cleanup_errors.append(str(exc))
        # Never recursively delete a directory that might still be mounted.
        if not cleanup_errors:
            shutil.rmtree(work)
        else:
            raise USBError("Unmount failed; mount directories retained at " + str(work) + ": " + "; ".join(cleanup_errors))
    log("USB installer created and safely unmounted (UEFI boot only)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Check Linux command dependencies")
    commands.add_parser("list", help="List USB disks and safety restrictions")
    write = commands.add_parser("write", help="Erase a USB disk and create a Windows UEFI installer")
    write.add_argument("iso")
    write.add_argument("device")
    write.add_argument("--confirm", required=True, help="Repeat the exact /dev/... path; ALL DATA WILL BE LOST")
    write.add_argument("--expected-identity", help="JSON device identity from the GUI selection")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            missing = check_dependencies()
            print("Missing: " + ", ".join(missing) if missing else "All Linux dependencies are available")
            return int(bool(missing))
        if args.command == "list":
            devices = list_devices()
            for disk in devices:
                print(disk.label)
            if not devices:
                print("No whole USB disks detected")
        else:
            identity = None
            if args.expected_identity:
                try:
                    identity = json.loads(args.expected_identity)
                    if not isinstance(identity, list):
                        raise ValueError("identity must be an array")
                except ValueError as exc:
                    raise USBError(f"Invalid expected device identity: {exc}") from exc
            write_usb(args.iso, args.device, args.confirm, expected_identity=identity)
        return 0
    except (USBError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

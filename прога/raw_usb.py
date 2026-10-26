"""Write verified GNU/Linux hybrid ISO/IMG bytes to an unmounted physical USB.

All discovery is read-only. No disk is formatted, partitioned or written until
the source checksum, confirmation, immutable target identity and exclusive
device claim have passed. Tests substitute device handles, never real disks.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import time

CHUNK = 4 * 1024 * 1024
MAC_CLAIM_TIMEOUT = 30.0


class USBError(RuntimeError):
    pass


@dataclass(frozen=True)
class Device:
    path: str
    size: int
    model: str
    serial: str
    mounted: tuple
    reason: str
    identity: tuple

    @property
    def safe(self):
        return not self.reason

    @property
    def label(self):
        return f"{self.path} — {self.model or 'USB'} — {self.size / 2**30:.1f} GiB" + (f" — {self.reason}" if self.reason else "")


def _require(condition, message):
    if not condition:
        raise USBError(message)


def _tuple(value):
    return tuple(_tuple(v) for v in value) if isinstance(value, (list, tuple)) else value


def _posix_backend():
    if sys.platform == "darwin":
        import macos_usb
        return macos_usb
    if sys.platform.startswith("linux"):
        import linux_usb
        return linux_usb
    raise USBError("Unsupported host system")


WINDOWS_DISCOVERY = r'''
$ErrorActionPreference = 'Stop'
$root = @(Get-Partition -DriveLetter $env:SystemDrive.TrimEnd(':') -ErrorAction Stop)
if ($root.Count -ne 1) { throw 'Cannot identify Windows system disk' }
$protected = @([int]$root[0].DiskNumber)
foreach ($page in @(Get-CimInstance Win32_PageFileUsage -ErrorAction Stop)) {
    if ($page.Name -notmatch '^[A-Za-z]:\\') { throw 'Unknown pagefile location' }
    $protected += [int](Get-Partition -DriveLetter $page.Name.Substring(0,1) -ErrorAction Stop).DiskNumber
}
$records = @(foreach ($disk in @(Get-Disk -ErrorAction Stop)) {
    $parts = @()
    if ([string]$disk.PartitionStyle -ne 'RAW') {
        $parts = @(Get-Partition -DiskNumber $disk.Number -ErrorAction Stop | ForEach-Object {
            @{ number=[int]$_.PartitionNumber; paths=@($_.AccessPaths); boot=$_.IsBoot; system=$_.IsSystem }
        })
    }
    @{ number=[int]$disk.Number; size=[int64]$disk.Size; model=[string]$disk.FriendlyName;
       serial=[string]$disk.SerialNumber; unique=[string]$disk.UniqueId; bus=[string]$disk.BusType;
       boot=$disk.IsBoot; system=$disk.IsSystem; readonly=$disk.IsReadOnly;
       offline=$disk.IsOffline; sector=[int]$disk.LogicalSectorSize; parts=$parts }
})
@{ protected=$protected; disks=$records } | ConvertTo-Json -Depth 8 -Compress
'''


def _windows_snapshot():
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    encoded = base64.b64encode(WINDOWS_DISCOVERY.encode("utf-16le")).decode("ascii")
    try:
        result = subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True)
        return _windows_devices(json.loads(result.stdout))
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        raise USBError(f"Cannot safely enumerate Windows disks: {exc}") from exc


def _windows_devices(snapshot):
    _require(isinstance(snapshot, dict), "Unknown Windows disk topology")
    roots = snapshot.get("protected")
    records = snapshot.get("disks")
    _require(isinstance(roots, list) and roots and all(type(n) is int and n >= 0 for n in roots), "Unknown system disks")
    _require(isinstance(records, list) and records, "Unknown Windows disks")
    numbers = [r.get("number") for r in records if isinstance(r, dict)]
    _require(len(numbers) == len(records) and len(set(numbers)) == len(numbers)
             and all(n in numbers for n in roots), "Incomplete/ambiguous system topology")
    devices = []
    for row in records:
        _require(type(row.get("number")) is int and row["number"] >= 0, "Invalid disk number")
        _require(isinstance(row.get("bus"), str), "Missing disk bus")
        if row["bus"] != "USB":
            continue
        required = ("boot", "system", "readonly", "offline")
        _require(all(type(row.get(k)) is bool for k in required), "Unknown disk protection flags")
        _require(type(row.get("size")) is int and row["size"] > 0, "Invalid capacity")
        _require(row.get("sector") in (512, 4096), "Unsupported logical sector size")
        _require(all(isinstance(row.get(k), str) for k in ("model", "serial", "unique")), "Incomplete hardware identity")
        parts = row.get("parts")
        _require(isinstance(parts, list), "Unknown partitions")
        mounted, volumes = [], []
        protected = row["number"] in roots or row["boot"] or row["system"]
        for part in parts:
            _require(isinstance(part, dict) and type(part.get("number")) is int
                     and all(type(part.get(k)) is bool for k in ("boot", "system")), "Unknown partition flags")
            protected |= part["boot"] or part["system"]
            paths = part.get("paths")
            _require(isinstance(paths, list) and all(isinstance(p, str) for p in paths), "Unknown partition mount state")
            for path in paths:
                if re.fullmatch(r"\\\\\?\\Volume\{[0-9a-fA-F-]{36}\}\\", path):
                    volumes.append(path)
                elif path:
                    mounted.append(path)
        reason = ""
        if protected:
            reason = "System, boot or pagefile disk"
        elif row["readonly"] or row["offline"]:
            reason = "Read-only or offline disk"
        elif mounted:
            reason = "Mounted volumes; remove USB drive letters/mount paths first"
        elif not row["serial"].strip() or not row["unique"].strip():
            reason = "USB hardware identity is unavailable"
        elif row["size"] < 512 * 1024**2:
            reason = "Disk is too small"
        path = rf"\\.\PhysicalDrive{row['number']}"
        identity = (path, row["size"], row["model"], row["serial"].strip(), row["unique"].strip(),
                    row["sector"], tuple(sorted(set(volumes))))
        devices.append(Device(path, row["size"], row["model"], row["serial"].strip(), tuple(mounted), reason, identity))
    return devices


def list_devices():
    if sys.platform == "win32":
        return _windows_snapshot()
    try:
        return [Device(d.path, d.size, d.model, d.serial, tuple(d.mounted), d.reason, _tuple(d.identity))
                for d in _posix_backend().list_devices()]
    except RuntimeError as exc:
        raise USBError(str(exc)) from exc


def _target(path):
    if sys.platform == "win32":
        _require(re.fullmatch(r"\\\\\.\\PhysicalDrive\d+", path) is not None, "Use the exact whole physical disk path")
        current = next((d for d in list_devices() if d.path == path), None)
        _require(current is not None and current.safe, current.reason if current else "USB disk was not found")
        return current
    try:
        d = _posix_backend()._target(path)
        return Device(d.path, d.size, d.model, d.serial, tuple(d.mounted), d.reason, _tuple(d.identity))
    except RuntimeError as exc:
        raise USBError(str(exc)) from exc


def _same(original):
    current = _target(original.path)
    _require(current.identity == original.identity, "USB identity changed; refresh the device list")


def _admin():
    if sys.platform == "win32":
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    return os.geteuid() == 0


class _MacClaim:
    """Hold a DiskArbitration claim and deny mounting this disk while writing."""
    def __init__(self, path):
        import ctypes as c
        self.c = c
        self.path = path
        self.cf = c.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.da = c.CDLL("/System/Library/Frameworks/DiskArbitration.framework/DiskArbitration")
        void = c.c_void_p
        signatures = {
            "DASessionCreate": ([void], void),
            "DADiskCreateFromBSDName": ([void, void, c.c_char_p], void),
            "DADiskCopyWholeDisk": ([void], void), "DADiskGetBSDName": ([void], c.c_char_p),
            "DADissenterCreate": ([void, c.c_int32, void], void),
            "DADissenterGetStatus": ([void], c.c_int32),
            "DASessionScheduleWithRunLoop": ([void, void, void], None),
            "DASessionUnscheduleFromRunLoop": ([void, void, void], None),
            "DARegisterDiskMountApprovalCallback": ([void, void, void, void], None),
            "DAUnregisterCallback": ([void, void, void], None),
            "DADiskClaim": ([void, c.c_uint32, void, void, void, void], None),
            "DADiskUnclaim": ([void], None),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.da, name)
            fn.argtypes, fn.restype = args, result
        self.cf.CFRunLoopGetCurrent.restype = void
        self.cf.CFRunLoopGetCurrent.argtypes = []
        self.cf.CFRunLoopRunInMode.argtypes = [void, c.c_double, c.c_bool]
        self.cf.CFRunLoopRunInMode.restype = c.c_int32
        self.cf.CFRelease.argtypes = [void]
        self.cf.CFRelease.restype = None
        self.cf.CFCopyDescription.argtypes = [void]
        self.cf.CFCopyDescription.restype = void
        self.cf.CFStringGetCString.argtypes = [void, void, c.c_long, c.c_uint32]
        self.cf.CFStringGetCString.restype = c.c_bool
        self.mode = void.in_dll(self.cf, "kCFRunLoopDefaultMode")
        self.loop = self.cf.CFRunLoopGetCurrent()
        self.session = self.disk = None
        self.claimed = self.done = self.registered = self.scheduled = False
        self.claim_status = None
        self.claim_elapsed = None
        self.runloop_results = {}
        self.approval = c.CFUNCTYPE(void, void, void)(self._approve)
        self.release = c.CFUNCTYPE(void, void, void)(lambda disk, ctx: self._deny())
        self.callback = c.CFUNCTYPE(None, void, void, void)(self._claimed)

    def _deny(self):
        # kDAReturnBusy; a retained dissenter is transferred to DiskArbitration.
        return self.da.DADissenterCreate(None, self.c.c_int32(0xF8DA0002), None)

    def _approve(self, disk, context):
        whole = self.da.DADiskCopyWholeDisk(disk)
        if not whole:
            return self._deny()
        try:
            name = self.da.DADiskGetBSDName(whole)
            if name == Path(self.path).name.encode("ascii"):
                return self._deny()
            return None
        finally:
            self.cf.CFRelease(whole)

    def _claimed(self, disk, dissenter, context):
        self.claimed = not bool(dissenter)
        self.claim_status = (self.da.DADissenterGetStatus(dissenter) & 0xFFFFFFFF) if dissenter else 0
        self.done = True

    def pump(self):
        self.cf.CFRunLoopRunInMode(self.mode, 0.001, False)

    def _description(self, obj):
        description = self.cf.CFCopyDescription(obj)
        if not description:
            return "unavailable"
        try:
            buffer = self.c.create_string_buffer(2048)
            if self.cf.CFStringGetCString(description, buffer, len(buffer), 0x08000100):
                return buffer.value.decode("utf-8", "replace")
            return "unavailable"
        finally:
            self.cf.CFRelease(description)

    def __enter__(self):
        try:
            self.session = self.da.DASessionCreate(None)
            _require(bool(self.session), "Cannot create DiskArbitration session")
            self.disk = self.da.DADiskCreateFromBSDName(None, self.session, self.path.encode("ascii"))
            _require(bool(self.disk), "Cannot claim selected macOS disk")
            self.da.DASessionScheduleWithRunLoop(self.session, self.loop, self.mode)
            self.scheduled = True
            self.da.DARegisterDiskMountApprovalCallback(self.session, None, self.approval, None)
            self.registered = True
            # DiskArbitration can still be completing media/volume callbacks
            # after diskutil reports unmount success. The same first-claim
            # delay is observable through Apple's native C API. Wait boundedly
            # for an actual successful completion, never infer a claim from
            # an elapsed timeout or an unmounted volume alone.
            started = time.monotonic()
            self.da.DADiskClaim(self.disk, 0, self.release, None, self.callback, None)
            deadline = started + MAC_CLAIM_TIMEOUT
            while not self.done and time.monotonic() < deadline:
                result = self.cf.CFRunLoopRunInMode(self.mode, 0.05, False)
                self.runloop_results[result] = self.runloop_results.get(result, 0) + 1
                if result == 1:  # kCFRunLoopRunFinished: no scheduled sources.
                    break
            self.claim_elapsed = time.monotonic() - started
            if not self.done:
                raise USBError("DiskArbitration claim timed out without invoking its completion callback; "
                               f"elapsed={self.claim_elapsed:.3f}s; "
                               f"runloop results={self.runloop_results} "
                               "(1=no sources, 2=stopped, 3=timed out, 4=source handled); "
                               f"mode={self._description(self.mode)}; session={self._description(self.session)}")
            statuses = {0xF8DA0002: "busy", 0xF8DA0004: "exclusive access held by another client",
                        0xF8DA0006: "disk not found", 0xF8DA0008: "claim release not permitted",
                        0xF8DA0009: "not privileged", 0xF8DA000A: "disk not ready"}
            _require(self.claimed, f"DiskArbitration refused claim: status={self.claim_status:#010x} "
                     f"({statuses.get(self.claim_status, 'unrecognized dissenter')})")
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *unused):
        if self.disk and self.claimed:
            self.da.DADiskUnclaim(self.disk)
        if self.registered:
            self.da.DAUnregisterCallback(self.session, self.approval, None)
        if self.scheduled:
            self.da.DASessionUnscheduleFromRunLoop(self.session, self.loop, self.mode)
        for ref in (self.disk, self.session):
            if ref:
                self.cf.CFRelease(ref)
        self.disk = self.session = None


class _PosixDisk:
    def __init__(self, fd, sector=512, pump=lambda: None):
        self.fd = fd
        self.sector = sector
        self.pump = pump

    def write(self, data):
        self.pump()
        return os.write(self.fd, data)

    def read(self, count):
        self.pump()
        return os.read(self.fd, count)

    def seek(self, offset):
        os.lseek(self.fd, offset, os.SEEK_SET)

    def flush(self):
        import fcntl
        os.fsync(self.fd)
        if sys.platform == "darwin":
            # Apple's disk.h: DKIOCSYNCHRONIZECACHE = _IO('d', 22).
            fcntl.ioctl(self.fd, 0x20006416)
        else:
            fcntl.ioctl(self.fd, 0x1261)  # BLKFLSBUF: invalidate block read cache.


@contextmanager
def _posix_disk(device):
    from contextlib import nullcontext
    import fcntl
    backend = _posix_backend()
    claim = _MacClaim(device.path) if sys.platform == "darwin" else nullcontext()
    with backend._device_lock(device.path), claim as held:
        _same(device)
        fd = os.open(device.path, os.O_RDWR | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            _require(stat.S_ISBLK(info.st_mode), "Opened target is not a block device")
            _require(info.st_rdev == os.stat(device.path, follow_symlinks=False).st_rdev,
                     "Device path changed while opening")
            _same(device)
            if sys.platform == "darwin":
                fcntl.fcntl(fd, 48, 1)  # F_NOCACHE for write/readback on the same claim.
                sector = backend._info(device.path).get("DeviceBlockSize")
            else:
                sector = struct.unpack("I", fcntl.ioctl(fd, 0x1268, bytes(4)))[0]  # BLKSSZGET
            _require(type(sector) is int and sector in (512, 4096), "Unknown logical sector size")
            yield _PosixDisk(fd, sector, held.pump if held else lambda: None)
        finally:
            os.close(fd)


class _WindowsDisk:
    """Aligned unbuffered Win32 I/O with checked errors and persistent handles."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.c = ctypes
        self.w = wintypes
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        specs = {
            "CreateFileW": ([wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE], wintypes.HANDLE),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "DeviceIoControl": ([wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p], wintypes.BOOL),
            "ReadFile": ([wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p], wintypes.BOOL),
            "WriteFile": ([wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p], wintypes.BOOL),
            "SetFilePointerEx": ([wintypes.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD], wintypes.BOOL),
            "FlushFileBuffers": ([wintypes.HANDLE], wintypes.BOOL),
            "VirtualAlloc": ([ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD], ctypes.c_void_p),
            "VirtualFree": ([ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD], wintypes.BOOL),
        }
        for name, (args, result) in specs.items():
            fn = getattr(self.k, name)
            fn.argtypes, fn.restype = args, result
        self.handle = None
        self.buffer = None
        self.sector = 512

    def check(self, success, operation):
        if not success:
            raise USBError(f"{operation}: {self.c.WinError(self.c.get_last_error())}")

    def open(self, path, flags=0, exclusive=False):
        handle = self.k.CreateFileW(path, 0xC0000000, 0 if exclusive else 3, None, 3, flags, None)
        self.check(handle not in (None, self.c.c_void_p(-1).value), f"Cannot exclusively access {path}")
        return handle

    def ioctl(self, handle, code, payload=b"", size=0):
        incoming = self.c.create_string_buffer(payload) if payload else None
        outgoing = self.c.create_string_buffer(size) if size else None
        count = self.w.DWORD()
        self.check(self.k.DeviceIoControl(handle, code, incoming, len(payload), outgoing, size,
                                         self.c.byref(count), None), f"DeviceIoControl {code:#x}")
        return outgoing.raw[:count.value] if outgoing else b""

    def validate(self, device):
        number = self.ioctl(self.handle, 0x2D1080, size=12)  # STORAGE_GET_DEVICE_NUMBER
        _require(len(number) >= 12 and struct.unpack_from("<I", number, 0)[0] == 7
                 and struct.unpack_from("<I", number, 4)[0] == int(device.path.removeprefix(r"\\.\PhysicalDrive")), "Physical handle identity changed")
        length = self.ioctl(self.handle, 0x7405C, size=8)  # DISK_GET_LENGTH_INFO
        _require(len(length) == 8 and struct.unpack("<Q", length)[0] == device.size, "Physical handle capacity changed")
        descriptor = self.ioctl(self.handle, 0x2D1400, struct.pack("<II", 0, 0), 4096)
        _require(len(descriptor) >= 36 and struct.unpack_from("<I", descriptor, 28)[0] == 7, "Physical handle is not USB")
        offset = struct.unpack_from("<I", descriptor, 24)[0]
        _require(36 <= offset < len(descriptor), "Physical handle serial is unavailable")
        serial = descriptor[offset:].split(b"\0", 1)[0].decode("ascii", "strict").strip()
        _require(serial == device.serial, "Physical handle serial differs from selected USB")

    def seek(self, offset):
        self.check(self.k.SetFilePointerEx(self.handle, offset, None, 0), "Device seek")

    def read(self, count):
        _require(0 < count <= CHUNK, "Invalid disk read length")
        actual = self.w.DWORD()
        self.check(self.k.ReadFile(self.handle, self.buffer, count, self.c.byref(actual), None), "Raw read")
        return self.c.string_at(self.buffer, actual.value)

    def write(self, data):
        _require(0 < len(data) <= CHUNK, "Invalid disk write length")
        self.c.memmove(self.buffer, data, len(data))
        actual = self.w.DWORD()
        self.check(self.k.WriteFile(self.handle, self.buffer, len(data), self.c.byref(actual), None), "Raw write")
        return actual.value

    def flush(self):
        self.check(self.k.FlushFileBuffers(self.handle), "Device flush")


@contextmanager
def _windows_disk(device):
    native = _WindowsDisk()
    volumes = []
    try:
        # Even unmounted volume GUIDs must be locked before raw physical writes.
        for volume in device.identity[6]:
            handle = native.open(volume.rstrip("\\"))
            volumes.append(handle)
            native.ioctl(handle, 0x90018)  # FSCTL_LOCK_VOLUME; failure aborts.
            native.ioctl(handle, 0x90020)  # FSCTL_DISMOUNT_VOLUME.
        _same(device)
        native.handle = native.open(device.path, 0xA0000000, exclusive=True)  # NO_BUFFERING | WRITE_THROUGH
        native.buffer = native.k.VirtualAlloc(None, CHUNK, 0x3000, 0x04)
        native.check(bool(native.buffer), "Allocate aligned I/O buffer")
        native.validate(device)
        native.sector = device.identity[5]
        _same(device)
        yield native
        native.validate(device)
    finally:
        if native.handle is not None:
            native.k.CloseHandle(native.handle)
        if native.buffer:
            native.k.VirtualFree(native.buffer, 0, 0x8000)
        for handle in reversed(volumes):
            native.k.CloseHandle(handle)  # Closing also releases volume locks.


def _open_disk(device):
    return _windows_disk(device) if sys.platform == "win32" else _posix_disk(device)


def _source_state(metadata):
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns


def _digest(stream, size):
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        block = stream.read(min(CHUNK, remaining))
        _require(bool(block), "Unexpected end of image/device")
        digest.update(block)
        remaining -= len(block)
    return digest.hexdigest()


def _copy_and_verify(source, target, size, digest, log):
    source.seek(0)
    target.seek(0)
    remaining = size
    copied = hashlib.sha256()
    while remaining:
        block = source.read(min(CHUNK, remaining))
        _require(bool(block), "Source image was truncated during writing")
        copied.update(block)
        _require(target.write(block) == len(block), "Short write; USB is incomplete")
        remaining -= len(block)
    _require(copied.hexdigest() == digest, "Source image changed during writing")
    target.flush()
    log("Writing complete. Reading back and verifying SHA-256…")
    target.seek(0)
    _require(_digest(target, size) == digest, "USB readback checksum mismatch")


def write_image(image, path, confirmation, expected_identity, expected_sha256, log=print):
    _require(_admin(), "Writing requires administrator privileges")
    _require(confirmation == path, "Confirmation must exactly match the USB path")
    _require(isinstance(expected_sha256, str) and re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256), "A verified image SHA-256 is required")
    _require(bool(expected_identity), "USB identity is required")
    source_path = Path(image).expanduser()
    _require(not source_path.is_symlink() and source_path.suffix.lower() in (".iso", ".img"), "Select a regular uncompressed .iso/.img file, not an archive or symbolic link")
    device = _target(path)
    _require(device.identity == _tuple(expected_identity), "USB changed since selection")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(source_path, flags)
    try:
        # Keep one unbuffered source descriptor throughout validation/copy and
        # final re-read. A BufferedReader could replay its old small-file cache
        # after seek(0), hiding mutation by another process on Windows.
        with os.fdopen(fd, "rb", buffering=0) as source:
            fd = None
            metadata = os.fstat(source.fileno())
            _require(stat.S_ISREG(metadata.st_mode) and metadata.st_size > 0, "Source is not a nonempty regular image file")
            sector = device.identity[5] if sys.platform == "win32" else 512
            _require(metadata.st_size % sector == 0, "Image length is not aligned to device sectors")
            _require(metadata.st_size <= device.size, "The image does not fit on the selected USB drive")
            if sys.platform != "win32":
                _require(metadata.st_dev != os.stat(device.path).st_rdev, "Source image is stored on target USB")
            # Optical-only ISOs cannot become bootable by raw USB copying.
            header = source.read(512)
            _require(header[510:512] == b"\x55\xaa" and any(header[446:510]), "The image has no hybrid MBR partition table for USB writing")
            source.seek(0)
            log("Verifying the source image SHA-256 before writing…")
            digest = _digest(source, metadata.st_size)
            _require(digest == expected_sha256.lower(), "Source image SHA-256 mismatch; writing cancelled")
            _require(_source_state(os.fstat(source.fileno())) == _source_state(metadata), "Source image changed during validation")
            _same(device)
            with _open_disk(device) as target:
                _require(metadata.st_size % target.sector == 0, "Image length is not aligned to physical USB sectors")
                _require(_source_state(os.fstat(source.fileno())) == _source_state(metadata), "Source image changed before writing")
                log("Writing the GNU/Linux image byte for byte…")
                _copy_and_verify(source, target, metadata.st_size, digest, log)
                _require(_source_state(os.fstat(source.fileno())) == _source_state(metadata), "Source image changed during writing")
                # Windows may cache LastWriteTime while a file handle remains
                # open. Metadata and the bytes already copied are not proof
                # that the source remained unchanged after its last read.
                # Rehash the same unbuffered descriptor; never reopen a path.
                log("Rechecking the source file SHA-256…")
                source.seek(0)
                _require(_digest(source, metadata.st_size) == digest and not source.read(1),
                         "Source image changed during writing (post-write SHA-256 mismatch)")
                _require(_source_state(os.fstat(source.fileno())) == _source_state(metadata), "Source image changed during final validation")
            log("Done. The written data SHA-256 matches the source image.")
    finally:
        if fd is not None:
            os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    write = commands.add_parser("write")
    write.add_argument("image")
    write.add_argument("device")
    write.add_argument("--confirm", required=True)
    write.add_argument("--expected-identity", required=True)
    write.add_argument("--sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            print(json.dumps([asdict(d) for d in list_devices()], ensure_ascii=False))
        else:
            write_image(args.image, args.device, args.confirm, json.loads(args.expected_identity), args.sha256)
    except (USBError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

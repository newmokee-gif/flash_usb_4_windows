"""Raw writer tests never enumerate, open or write actual hardware."""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
import raw_usb as raw


def windows_snapshot(**changes):
    row = dict(number=2, bus="USB", size=2**30, sector=512, model="TEST USB", serial="SERIAL",
               unique="UNIQUE", boot=False, system=False, readonly=False, offline=False, parts=[])
    row.update(changes)
    return {"protected": [0], "disks": [{"number": 0, "bus": "NVMe"}, row]}


class WindowsDiscoveryTests(unittest.TestCase):
    def test_unmounted_usb_has_complete_identity(self):
        device, = raw._windows_devices(windows_snapshot())
        self.assertTrue(device.safe)
        self.assertEqual(device.path, r"\\.\PhysicalDrive2")
        self.assertEqual(device.identity[3], "SERIAL")

    def test_all_system_and_unknown_states_refused(self):
        for changes in (dict(boot=True), dict(system=True), dict(readonly=True),
                        dict(offline=True), dict(serial=""), dict(unique="")):
            with self.subTest(changes=changes):
                self.assertFalse(raw._windows_devices(windows_snapshot(**changes))[0].safe)

    def test_system_usb_and_pagefile_disk_refused(self):
        data = windows_snapshot()
        data["protected"].append(2)
        self.assertFalse(raw._windows_devices(data)[0].safe)

    def test_missing_roots_and_protection_flags_refused(self):
        for field in ("boot", "system", "readonly", "offline", "sector", "parts"):
            data = windows_snapshot()
            del data["disks"][1][field]
            with self.subTest(field=field), self.assertRaises(raw.USBError):
                raw._windows_devices(data)
        for roots in (None, [], [99]):
            data = windows_snapshot()
            data["protected"] = roots
            with self.assertRaises(raw.USBError):
                raw._windows_devices(data)

    def test_mounted_drive_letters_and_paths_refused_but_volume_guids_retained(self):
        guid = "\\\\?\\Volume{11111111-1111-1111-1111-111111111111}\\"
        part = dict(number=1, boot=False, system=False, paths=[guid])
        device, = raw._windows_devices(windows_snapshot(parts=[part]))
        self.assertTrue(device.safe)
        self.assertEqual(device.identity[-1], (guid,))
        for mount in ("E:\\", "C:\\mounted-usb\\"):
            part = dict(part, paths=[guid, mount])
            self.assertFalse(raw._windows_devices(windows_snapshot(parts=[part]))[0].safe)


class MemoryDisk(io.BytesIO):
    sector = 512

    def flush(self):
        self.flushed = True


class WriteWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.image = Path(self.temp.name) / "image.iso"
        data = bytearray(4096)
        data[446] = 0x80
        data[450] = 0x83
        data[510:512] = b"\x55\xaa"
        data[512:] = b"\x12" * (len(data) - 512)
        self.data = bytes(data)
        self.image.write_bytes(data)
        self.digest = hashlib.sha256(data).hexdigest()
        self.device = raw.Device(r"\\.\PhysicalDrive2", 2**30, "TEST", "SERIAL", (), "",
                                 (r"\\.\PhysicalDrive2", 2**30, "TEST", "SERIAL", "UNIQUE", 512, ()))
        self.target = MemoryDisk(b"x" * 8192)
        self.opened = False

        @contextmanager
        def opener(device):
            self.opened = True
            yield self.target

        self.stack = []
        for context in (patch.object(raw.sys, "platform", "win32"), patch.object(raw, "_admin", return_value=True),
                        patch.object(raw, "_target", return_value=self.device), patch.object(raw, "_open_disk", side_effect=opener)):
            self.stack.append(context.start())
            self.addCleanup(context.stop)

    def write(self, **changes):
        values = dict(image=self.image, path=self.device.path, confirmation=self.device.path,
                      expected_identity=self.device.identity, expected_sha256=self.digest, log=lambda _: None)
        values.update(changes)
        raw.write_image(**values)

    def test_success_copies_exact_bytes_and_reads_back(self):
        self.write(expected_identity=json.loads(json.dumps(self.device.identity)))
        self.assertEqual(self.target.getvalue()[:len(self.data)], self.data)
        self.assertEqual(self.target.getvalue()[len(self.data):], b"x" * 4096)
        self.assertTrue(self.target.flushed)
        self.assertEqual(self.target.tell(), len(self.data))

    def test_bad_digest_never_opens_target(self):
        with self.assertRaisesRegex(raw.USBError, "SHA-256"):
            self.write(expected_sha256="0" * 64)
        self.assertFalse(self.opened)

    def test_confirmation_and_identity_required_before_write(self):
        for changes in (dict(confirmation="wrong"), dict(expected_identity=None),
                        dict(expected_identity=["another disk"]), dict(expected_sha256="")):
            with self.subTest(changes=changes), self.assertRaises(raw.USBError):
                self.write(**changes)
        self.assertFalse(self.opened)

    def test_nonadmin_never_opens_target(self):
        with patch.object(raw, "_admin", return_value=False), self.assertRaises(raw.USBError):
            self.write()
        self.assertFalse(self.opened)

    def test_capacity_checked_before_open(self):
        small = replace(self.device, size=512)
        with patch.object(raw, "_target", return_value=small), self.assertRaises(raw.USBError):
            self.write()
        self.assertFalse(self.opened)

    def test_nonhybrid_and_compressed_images_refused(self):
        self.image.write_bytes(b"a" * 4096)
        with self.assertRaisesRegex(raw.USBError, "MBR"):
            self.write()
        self.assertFalse(self.opened)
        with self.assertRaises(raw.USBError):
            self.write(image=str(self.image) + ".xz")

    def test_device_replaced_before_open_refused(self):
        with patch.object(raw, "_target", side_effect=[self.device, replace(self.device, identity=("replacement",))]):
            with self.assertRaisesRegex(raw.USBError, "identity changed"):
                self.write()
        self.assertFalse(self.opened)

    def test_short_write_and_corrupt_readback_fail(self):
        with patch.object(self.target, "write", return_value=1), self.assertRaisesRegex(raw.USBError, "Short write"):
            self.write()
        self.target = MemoryDisk(b"x" * 8192)
        with patch.object(self.target, "read", side_effect=lambda n: b"x" * n), self.assertRaisesRegex(raw.USBError, "readback"):
            self.write()

    def test_4kn_misalignment_refused_before_first_write(self):
        self.image.write_bytes(self.data[:2048])
        self.target.sector = 4096
        with self.assertRaisesRegex(raw.USBError, "aligned"):
            self.write(expected_sha256=hashlib.sha256(self.data[:2048]).hexdigest())
        self.assertEqual(self.target.getvalue(), b"x" * 8192)

    def test_source_mutation_during_writing_detected(self):
        write = self.target.write
        def changed(data):
            self.image.write_bytes(b"z" * 4096)
            return write(data)
        with patch.object(self.target, "write", side_effect=changed), self.assertRaisesRegex(raw.USBError, "Source image changed"):
            self.write()


class WindowsHandleOrderingTests(unittest.TestCase):
    def test_failed_volume_lock_never_opens_physical_disk(self):
        device = raw._windows_devices(windows_snapshot(parts=[dict(number=1, boot=False, system=False,
            paths=["\\\\?\\Volume{11111111-1111-1111-1111-111111111111}\\"])]))[0]
        native = Mock(handle=None, buffer=None)
        native.open.return_value = 12
        native.ioctl.side_effect = raw.USBError("lock failed")
        with patch.object(raw, "_WindowsDisk", return_value=native), self.assertRaisesRegex(raw.USBError, "lock failed"):
            with raw._windows_disk(device):
                self.fail("must not yield")
        native.open.assert_called_once()
        native.k.CloseHandle.assert_called_once_with(12)

    def test_identity_failure_closes_handle_and_volumes_before_write(self):
        device = raw._windows_devices(windows_snapshot())[0]
        native = Mock(handle=None, buffer=None)
        native.open.return_value = 22
        native.k.VirtualAlloc.return_value = 1234
        native.validate.side_effect = raw.USBError("serial changed")
        with patch.object(raw, "_WindowsDisk", return_value=native), patch.object(raw, "_same"), self.assertRaises(raw.USBError):
            with raw._windows_disk(device):
                self.fail("must not yield")
        native.k.CloseHandle.assert_called_once_with(22)
        native.write.assert_not_called()
        native.open.assert_called_once_with(device.path, 0xA0000000, exclusive=True)

    def test_volume_handles_held_through_readback(self):
        guid = "\\\\?\\Volume{11111111-1111-1111-1111-111111111111}\\"
        device = raw._windows_devices(windows_snapshot(parts=[dict(number=1, boot=False, system=False, paths=[guid])]))[0]
        native = Mock(handle=None, buffer=None)
        native.open.side_effect = [11, 22]
        native.k.VirtualAlloc.return_value = 1234
        with patch.object(raw, "_WindowsDisk", return_value=native), patch.object(raw, "_same"):
            with raw._windows_disk(device) as target:
                native.k.CloseHandle.assert_not_called()
                target.write(b"a" * 512)
                target.flush()
                target.read(512)
                native.k.CloseHandle.assert_not_called()
        self.assertEqual([c.args[0] for c in native.k.CloseHandle.call_args_list], [22, 11])
        self.assertEqual(native.validate.call_count, 2)


class MacClaimTests(unittest.TestCase):
    """Mock CoreFoundation/DA callbacks; never load or open native disks."""
    def claim(self):
        claim = object.__new__(raw._MacClaim)
        claim.path = "/dev/disk99"
        claim.da, claim.cf = Mock(), Mock()
        claim.da.DASessionCreate.return_value = 11
        claim.da.DADiskCreateFromBSDName.return_value = 22
        claim.session = claim.disk = None
        claim.mode, claim.loop = 33, 44
        claim.claimed = claim.done = claim.registered = claim.scheduled = False
        claim.claim_status = None
        claim.runloop_results = {}
        claim.approval, claim.release, claim.callback = Mock(), Mock(), Mock()
        claim._description = Mock(return_value="mock CoreFoundation object")
        return claim

    def test_completion_required_before_enter_and_claim_held_until_exit(self):
        claim = self.claim()
        def runloop(*unused):
            claim._claimed(22, None, None)
            return 3
        claim.cf.CFRunLoopRunInMode.side_effect = runloop
        with claim:
            self.assertTrue(claim.claimed)
            claim.da.DADiskUnclaim.assert_not_called()
            claim.cf.CFRelease.assert_not_called()
        claim.da.DADiskUnclaim.assert_called_once_with(22)
        claim.da.DASessionUnscheduleFromRunLoop.assert_called_once_with(11, 44, 33)
        self.assertEqual([c.args[0] for c in claim.cf.CFRelease.call_args_list], [22, 11])

    def test_no_runloop_sources_refused_with_diagnostics(self):
        claim = self.claim()
        claim.cf.CFRunLoopRunInMode.return_value = 1
        with self.assertRaisesRegex(raw.USBError, r"runloop results=\{1: 1\}"):
            claim.__enter__()
        claim.da.DADiskUnclaim.assert_not_called()
        claim.da.DAUnregisterCallback.assert_called_once()
        claim.da.DASessionUnscheduleFromRunLoop.assert_called_once()
        self.assertIsNone(claim.session)

    def test_native_dissenter_status_reported_and_never_enters(self):
        claim = self.claim()
        claim.da.DADissenterGetStatus.return_value = -119930878  # 0xF8DA0002
        def runloop(*unused):
            claim._claimed(22, 55, None)
            return 3
        claim.cf.CFRunLoopRunInMode.side_effect = runloop
        with self.assertRaisesRegex(raw.USBError, "0xf8da0002.*busy"):
            claim.__enter__()
        claim.da.DADiskUnclaim.assert_not_called()
        self.assertIsNone(claim.disk)


if __name__ == "__main__":
    unittest.main()

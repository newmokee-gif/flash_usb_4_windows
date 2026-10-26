"""Non-destructive Linux backend tests; never invoke real disk commands."""

import importlib.util
import contextlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "прога" / "linux_usb.py"
SPEC = importlib.util.spec_from_file_location("linux_usb", MODULE_PATH)
usb = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = usb
SPEC.loader.exec_module(usb)


def disk(**changes):
    node = {
        "name": "sdb", "path": "/dev/sdb", "type": "disk", "tran": "usb",
        "rm": True, "ro": False, "size": 16 * 1024**3,
        "model": "Test USB", "serial": "TEST-SERIAL", "wwn": None,
        "maj:min": "8:16", "mountpoints": [], "children": [],
    }
    node.update(changes)
    return node


class DeviceSafetyTests(unittest.TestCase):
    def test_unmounted_usb_disk_is_safe(self):
        self.assertTrue(usb._device_from_node(disk(), "8:1").safe)

    def test_usb_disk_need_not_set_removable_bit(self):
        self.assertTrue(usb._device_from_node(disk(rm=False), "8:1").safe)

    def test_non_usb_rejected(self):
        for transport in ("sata", "nvme", "virtio", None, ""):
            with self.subTest(transport=transport):
                self.assertFalse(usb._device_from_node(disk(tran=transport), "8:1").safe)

    def test_partition_rejected(self):
        self.assertFalse(usb._device_from_node(disk(type="part"), "8:1").safe)

    def test_read_only_rejected(self):
        self.assertFalse(usb._device_from_node(disk(ro=True), "8:1").safe)

    def test_mounted_disk_rejected(self):
        self.assertFalse(usb._device_from_node(disk(mountpoints=["/media/usb"]), "8:1").safe)

    def test_mounted_child_rejected(self):
        child = disk(type="part", path="/dev/sdb1", mountpoints=["/media/usb"])
        self.assertFalse(usb._device_from_node(disk(children=[child]), "8:1").safe)

    def test_swap_child_rejected(self):
        child = disk(type="part", path="/dev/sdb1", mountpoints=["[SWAP]"])
        self.assertFalse(usb._device_from_node(disk(children=[child]), "8:1").safe)

    def test_root_disk_rejected(self):
        self.assertFalse(usb._device_from_node(disk(), "8:16").safe)

    def test_root_child_rejected_even_without_mountpoint(self):
        child = disk(type="part", path="/dev/sdb1", **{"maj:min": "8:17"})
        self.assertFalse(usb._device_from_node(disk(children=[child]), "8:17").safe)

    def test_identity_changes_when_device_replaced(self):
        original = usb._device_from_node(disk(), "8:1")
        for changes in ({"serial": "REPLACED"}, {"size": 8 * 1024**3}, {"maj:min": "8:32"}):
            with self.subTest(changes=changes):
                self.assertNotEqual(original.identity, usb._device_from_node(disk(**changes), "8:1").identity)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)

    def add(self, name, content=b"test"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def windows_source(self):
        self.add("efi/boot/bootx64.efi")
        self.add("sources/boot.wim")
        self.add("sources/install.wim")
        self.add("setup.exe")

    def test_empty_source_rejected(self):
        with self.assertRaises(usb.USBError):
            usb._validate_source(self.root)

    def test_missing_uefi_bootloader_rejected(self):
        self.windows_source()
        (self.root / "efi/boot/bootx64.efi").unlink()
        with self.assertRaises(usb.USBError):
            usb._validate_source(self.root)

    def test_valid_windows_source(self):
        self.windows_source()
        files, total, split_wim = usb._validate_source(self.root)
        self.assertEqual(len(files), 4)
        self.assertEqual(total, 16)
        self.assertIsNone(split_wim)

    def test_symlink_source_rejected(self):
        self.windows_source()
        (self.root / "link").symlink_to(self.root / "setup.exe")
        with self.assertRaises(usb.USBError):
            usb._validate_source(self.root)

    def test_large_install_wim_is_split_candidate(self):
        self.windows_source()
        image = self.root / "sources/install.wim"
        with image.open("r+b") as stream:
            stream.truncate(4 * 1024**3)
        _, total, split_wim = usb._validate_source(self.root)
        self.assertEqual(split_wim, image.relative_to(self.root))
        self.assertGreater(total, 4 * 1024**3)

    def test_oversized_non_wim_file_rejected(self):
        self.windows_source()
        for name in ("sources/install.esd", "large.bin"):
            with self.subTest(name=name):
                image = self.add(name)
                with image.open("r+b") as stream:
                    stream.truncate(4 * 1024**3)
                with self.assertRaises(usb.USBError):
                    usb._validate_source(self.root)
                image.unlink()

    def test_case_colliding_files_rejected(self):
        self.windows_source()
        self.add("SETUP.EXE")
        with self.assertRaises(usb.USBError):
            usb._validate_source(self.root)


class DiscoveryTests(unittest.TestCase):
    def read(self, value):
        with mock.patch.object(usb, "_run", return_value=subprocess.CompletedProcess([], 0, json.dumps(value), "")):
            return usb._read_devices()

    def test_valid_topology(self):
        self.assertEqual(self.read({"blockdevices": [disk()]}), [disk()])

    def test_malformed_topology_rejected(self):
        for value in ({}, {"blockdevices": None}, {"blockdevices": [None]},
                      {"blockdevices": [disk(children=None)]},
                      {"blockdevices": [disk(mountpoints=None)]},
                      {"blockdevices": [disk(ro=None)]},
                      {"blockdevices": [disk(size=-1)]}):
            with self.subTest(value=value), self.assertRaises(usb.USBError):
                self.read(value)

    def test_missing_required_fields_rejected(self):
        for field in ("path", "type", "size", "tran", "ro", "mountpoints", "maj:min"):
            node = disk()
            del node[field]
            with self.subTest(field=field), self.assertRaises(usb.USBError):
                self.read({"blockdevices": [node]})

    def test_invalid_json_rejected(self):
        with mock.patch.object(usb, "_run", return_value=subprocess.CompletedProcess([], 0, "not json", "")):
            with self.assertRaises(usb.USBError):
                usb._read_devices()

    def test_unknown_root_fails_closed(self):
        with mock.patch.object(usb, "_read_devices", return_value=[disk()]), \
             mock.patch.object(usb, "_root_ids", return_value=set()):
            devices = usb.list_devices()
        self.assertEqual(len(devices), 1)
        self.assertFalse(devices[0].safe)

    def test_gui_identity_includes_kernel_disk_sequence(self):
        root = disk(path="/dev/sda", tran="sata", **{"maj:min": "8:0"})
        with mock.patch.object(usb, "_read_devices", return_value=[root, disk()]), \
             mock.patch.object(usb, "_root_ids", return_value={"8:0"}), \
             mock.patch.object(usb, "_disk_sequence", side_effect=["40", "41"]):
            before = usb.list_devices()[0]
            after = usb.list_devices()[0]
        self.assertTrue(before.safe)
        self.assertNotEqual(before.identity, after.identity)
        self.assertEqual(before.identity[-1], "40")


class RootFilesystemTests(unittest.TestCase):
    def root_ids(self, filesystem, members=("8:1", "8:17")):
        result = subprocess.CompletedProcess([], 0, json.dumps({"filesystems": [filesystem]}), "")
        with mock.patch.object(usb.os, "stat", return_value=mock.Mock(st_dev=os.makedev(0, 99))), \
             mock.patch.object(usb, "_run", return_value=result), \
             mock.patch.object(pathlib.Path, "iterdir", return_value=[pathlib.Path("one"), pathlib.Path("two")]), \
             mock.patch.object(pathlib.Path, "read_text", side_effect=members):
            return usb._root_ids()

    def test_btrfs_identifies_every_physical_member(self):
        identifiers = self.root_ids({"fstype": "btrfs", "uuid": "01234567-89ab-cdef-0123-456789abcdef"})
        self.assertEqual(identifiers, {"8:1", "8:17"})
        child = disk(type="part", path="/dev/sdb1", **{"maj:min": "8:17"})
        self.assertFalse(usb._device_from_node(disk(children=[child]), identifiers).safe)

    def test_overlay_and_invalid_btrfs_identity_fail_closed(self):
        for filesystem in ({"fstype": "overlay", "uuid": None}, {"fstype": "btrfs", "uuid": "../../bad"}):
            with self.subTest(filesystem=filesystem):
                self.assertEqual(self.root_ids(filesystem), set())

    def test_invalid_btrfs_member_fails_closed(self):
        self.assertEqual(self.root_ids({"fstype": "btrfs", "uuid": "01234567-89ab-cdef-0123-456789abcdef"}, ("8:1", "invalid")), set())

    def test_normal_root_uses_device_number(self):
        with mock.patch.object(usb.os, "stat", return_value=mock.Mock(st_dev=os.makedev(8, 1))), \
             mock.patch.object(usb, "_run") as run:
            self.assertEqual(usb._root_ids(), {"8:1"})
            run.assert_not_called()


class CommandTests(unittest.TestCase):
    def setUp(self):
        tool = mock.patch.object(usb, "_tool", return_value="/usr/bin/tool")
        tool.start()
        self.addCleanup(tool.stop)

    def test_nonzero_command_fails(self):
        result = subprocess.CompletedProcess(["tool"], 2, "", "failure")
        with mock.patch.object(usb.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(usb.USBError, "exited 2"):
                usb._run(["tool"])

    def test_system_executable_path_is_used(self):
        with mock.patch.object(usb.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            usb._run(["tool", "argument"])
        self.assertEqual(run.call_args.args[0], ["/usr/bin/tool", "argument"])

    def test_missing_command_and_timeout_fail(self):
        for error in (FileNotFoundError("missing"), subprocess.TimeoutExpired("tool", 1)):
            with self.subTest(error=error), mock.patch.object(usb.subprocess, "run", side_effect=error):
                with self.assertRaises(usb.USBError):
                    usb._run(["tool"])


class TargetTests(unittest.TestCase):
    def test_regular_file_rejected_without_disk_discovery(self):
        with tempfile.NamedTemporaryFile() as target, mock.patch.object(usb, "list_devices") as discover:
            with self.assertRaisesRegex(usb.USBError, "block device"):
                usb._target(target.name)
            discover.assert_not_called()

    def test_symlink_rejected_without_disk_discovery(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(usb, "list_devices") as discover:
            alias = pathlib.Path(temporary) / "alias"
            alias.symlink_to("/dev/null")
            with self.assertRaisesRegex(usb.USBError, "symlink"):
                usb._target(str(alias))
            discover.assert_not_called()

    def target_with_holders(self, holders=(), number=None):
        metadata = mock.Mock(st_mode=stat.S_IFBLK, st_rdev=number if number is not None else os.makedev(8, 16))
        original = usb._device_from_node(disk(), "8:1")
        with mock.patch.object(pathlib.Path, "stat", return_value=metadata), \
             mock.patch.object(pathlib.Path, "iterdir", side_effect=holders if isinstance(holders, Exception) else None,
                               return_value=iter(holders) if not isinstance(holders, Exception) else None), \
             mock.patch.object(usb, "list_devices", return_value=[original]), \
             mock.patch.object(usb, "_read_devices", return_value=[disk()]):
            return usb._target("/dev/sdb")

    def test_active_kernel_holder_rejected(self):
        with self.assertRaisesRegex(usb.USBError, "holders"):
            self.target_with_holders([pathlib.Path("dm-0")])

    def test_unreadable_kernel_holders_rejected(self):
        with self.assertRaisesRegex(usb.USBError, "holders"):
            self.target_with_holders(PermissionError("blocked"))

    def test_changed_device_number_rejected(self):
        with self.assertRaisesRegex(usb.USBError, "identity"):
            self.target_with_holders(number=os.makedev(8, 32))

    def test_verified_empty_holders_accepted(self):
        self.assertEqual(self.target_with_holders().path, "/dev/sdb")


class DeviceLockTests(unittest.TestCase):
    def test_concurrent_writer_is_rejected_and_descriptor_closed(self):
        with mock.patch.object(usb.os, "open", return_value=123), \
             mock.patch.object(usb.os, "fstat", return_value=mock.Mock(st_mode=stat.S_IFBLK)), \
             mock.patch.object(usb.fcntl, "flock", side_effect=BlockingIOError("locked")), \
             mock.patch.object(usb.os, "close") as close:
            with self.assertRaisesRegex(usb.USBError, "Another writer"):
                with usb._device_lock("/dev/sdb"):
                    self.fail("Lock was acquired unexpectedly")
        close.assert_called_once_with(123)

    def test_lock_descriptor_closed_on_body_failure(self):
        with mock.patch.object(usb.os, "open", return_value=123), \
             mock.patch.object(usb.os, "fstat", return_value=mock.Mock(st_mode=stat.S_IFBLK)), \
             mock.patch.object(usb.fcntl, "flock") as lock, \
             mock.patch.object(usb.os, "close") as close:
            with self.assertRaisesRegex(usb.USBError, "body"):
                with usb._device_lock("/dev/sdb"):
                    raise usb.USBError("body failure")
        lock.assert_called_once_with(123, usb.fcntl.LOCK_EX | usb.fcntl.LOCK_NB)
        close.assert_called_once_with(123)


class PartitionTests(unittest.TestCase):
    def test_automounted_partition_rejected(self):
        child = disk(type="part", path="/dev/sdb1", mountpoints=["/media/automount"])
        with mock.patch.object(usb, "_run"), mock.patch.object(usb, "_read_devices", return_value=[disk(children=[child])]):
            with self.assertRaisesRegex(usb.USBError, "automounted"):
                usb._partition("/dev/sdb")

    def test_partition_timeout_is_bounded(self):
        with mock.patch.object(usb, "_run"), \
             mock.patch.object(usb, "_read_devices", return_value=[]) as read, \
             mock.patch.object(usb.time, "sleep"):
            with self.assertRaisesRegex(usb.USBError, "did not appear"):
                usb._partition("/dev/sdb")
        self.assertEqual(read.call_count, 20)

    def test_single_unmounted_partition_accepted(self):
        child = disk(type="part", path="/dev/sdb1")
        with mock.patch.object(usb, "_run"), mock.patch.object(usb, "_read_devices", return_value=[disk(children=[child])]):
            self.assertEqual(usb._partition("/dev/sdb"), "/dev/sdb1")


class CopyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = pathlib.Path(self.temp.name) / "source"
        self.target = pathlib.Path(self.temp.name) / "target"
        self.source.mkdir()
        self.target.mkdir()
        self.relative = pathlib.Path("nested/data.bin")
        (self.source / self.relative).parent.mkdir()
        (self.source / self.relative).write_bytes(b"payload\x00\xff")

    def test_copy_is_byte_identical(self):
        usb._copy_and_verify(self.source, self.target, [(self.relative, 9)], None, lambda _: None)
        self.assertEqual((self.target / self.relative).read_bytes(), b"payload\x00\xff")

    def test_same_size_corruption_rejected(self):
        def corrupt(source, destination):
            pathlib.Path(destination).write_bytes(b"corrupted")
        with mock.patch.object(usb.shutil, "copyfile", side_effect=corrupt):
            with self.assertRaisesRegex(usb.USBError, "verification"):
                usb._copy_and_verify(self.source, self.target, [(self.relative, 9)], None, lambda _: None)

    def test_missing_split_parts_rejected(self):
        relative = pathlib.Path("sources/install.wim")
        with mock.patch.object(usb, "_run"):
            with self.assertRaisesRegex(usb.USBError, "split WIM"):
                usb._copy_and_verify(self.source, self.target, [(relative, 2**32)], relative, lambda _: None)

    def test_split_wim_verified_with_all_parts(self):
        relative = pathlib.Path("sources/install.wim")
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if args[1] == "split":
                pathlib.Path(args[3]).write_bytes(b"part one")
                pathlib.Path(args[3]).with_name("install2.swm").write_bytes(b"part two")
        with mock.patch.object(usb, "_run", side_effect=run):
            usb._copy_and_verify(self.source, self.target, [(relative, 2**32)], relative, lambda _: None)
        self.assertEqual(calls[0][1], "split")
        self.assertIn("--check", calls[0])
        self.assertEqual(calls[1][1], "verify")
        self.assertIn(f"--ref={self.target}/sources/install*.swm", calls[1])


class WriteWorkflowTests(unittest.TestCase):
    """All external commands and device inspection are stubbed in these tests."""

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.image = pathlib.Path(self.temp) / "windows.iso"
        self.image.write_bytes(b"fake ISO fixture")
        self.work = pathlib.Path(self.temp) / "work"
        self.work.mkdir()
        self.commands = []
        self.fail_command = None
        self.original = usb._device_from_node(disk(), "8:1")
        self.stack.enter_context(mock.patch.object(usb.sys, "platform", "linux"))
        self.stack.enter_context(mock.patch.object(usb.os, "geteuid", return_value=0))
        self.stack.enter_context(mock.patch.object(usb, "check_dependencies", return_value=[]))
        self.stack.enter_context(mock.patch.object(usb, "_device_lock", return_value=contextlib.nullcontext()))
        self.stack.enter_context(mock.patch.object(usb.subprocess, "run", side_effect=AssertionError("Real subprocess is forbidden")))
        self.target = self.stack.enter_context(mock.patch.object(usb, "_target", return_value=self.original))
        self.sequence = self.stack.enter_context(mock.patch.object(usb, "_disk_sequence", return_value="7"))
        self.stack.enter_context(mock.patch.object(usb, "_read_devices", return_value=[disk()]))
        self.stack.enter_context(mock.patch.object(usb.tempfile, "mkdtemp", return_value=str(self.work)))
        self.validate = self.stack.enter_context(mock.patch.object(usb, "_validate_source", return_value=([], 1024, None)))
        self.stack.enter_context(mock.patch.object(usb, "_partition", return_value="/dev/sdb1"))
        self.copy = self.stack.enter_context(mock.patch.object(usb, "_copy_and_verify"))
        self.verify_source = self.stack.enter_context(mock.patch.object(usb, "_verify_source_wims"))
        self.verify_target = self.stack.enter_context(mock.patch.object(usb, "_verify_target"))
        self.stack.enter_context(mock.patch.object(usb, "_run", side_effect=self.run_command))

    def run_command(self, args, **kwargs):
        self.commands.append(args)
        if args[0] == self.fail_command:
            raise usb.USBError("injected command failure")
        return subprocess.CompletedProcess(args, 0, "", "")

    def write(self, confirm="/dev/sdb"):
        usb.write_usb(str(self.image), "/dev/sdb", confirm, log=lambda _: None)

    def assert_no_destructive_commands(self):
        self.assertFalse(any(c[0] in ("parted", "mkfs.fat") for c in self.commands))

    def test_success_unmounts_target_then_source(self):
        self.write()
        self.copy.assert_called_once()
        self.assertEqual(self.commands[-2:], [["umount", str(self.work / "usb")], ["umount", str(self.work / "iso")]])
        self.assertFalse(self.work.exists())
        self.assertIn("loop,ro,nosuid,nodev,noexec", self.commands[0])
        self.verify_source.assert_called_once()
        self.verify_target.assert_called_once()
        self.assertIn(["blockdev", "--flushbufs", "/dev/sdb1"], self.commands)
        self.assertIn(["mount", "-o", "ro,nosuid,nodev,noexec", "/dev/sdb1", str(self.work / "usb")], self.commands)

    def test_wrong_confirmation_never_mounts_or_erases(self):
        with self.assertRaises(usb.USBError):
            self.write(confirm="/dev/sdc")
        self.assertEqual(self.commands, [])

    def test_gui_selection_replacement_rejected_before_mount(self):
        replacement = usb._device_from_node(disk(serial="OLD"), "8:1")
        with self.assertRaisesRegex(usb.USBError, "selected"):
            usb.write_usb(str(self.image), "/dev/sdb", "/dev/sdb", expected_identity=replacement.identity)
        self.assertEqual(self.commands, [])

    def test_nonroot_never_mounts_or_erases(self):
        with mock.patch.object(usb.os, "geteuid", return_value=1000):
            with self.assertRaises(usb.USBError):
                self.write()
        self.assertEqual(self.commands, [])

    def test_missing_dependencies_never_mounts_or_erases(self):
        with mock.patch.object(usb, "check_dependencies", return_value=["parted"]):
            with self.assertRaises(usb.USBError):
                self.write()
        self.assertEqual(self.commands, [])

    def test_invalid_source_unmounted_without_erasure(self):
        self.validate.side_effect = usb.USBError("invalid ISO")
        with self.assertRaisesRegex(usb.USBError, "invalid ISO"):
            self.write()
        self.assert_no_destructive_commands()
        self.assertEqual(self.commands[-1], ["umount", str(self.work / "iso")])

    def test_capacity_checked_before_erasure(self):
        self.validate.return_value = ([], self.original.size, None)
        with self.assertRaisesRegex(usb.USBError, "too small"):
            self.write()
        self.assert_no_destructive_commands()

    def test_corrupt_source_wim_rejected_before_erasure(self):
        self.verify_source.side_effect = usb.USBError("corrupt WIM")
        with self.assertRaisesRegex(usb.USBError, "corrupt WIM"):
            self.write()
        self.assert_no_destructive_commands()

    def test_device_replacement_before_partition_rejected(self):
        replacement = usb._device_from_node(disk(serial="NEW"), "8:1")
        self.target.side_effect = [self.original, replacement]
        with self.assertRaisesRegex(usb.USBError, "changed"):
            self.write()
        self.assert_no_destructive_commands()

    def test_disk_sequence_change_before_partition_rejected(self):
        self.sequence.side_effect = ["7", "8"]
        with self.assertRaisesRegex(usb.USBError, "changed"):
            self.write()
        self.assert_no_destructive_commands()

    def test_replacement_before_format_rejected(self):
        replacement = usb._device_from_node(disk(serial="NEW"), "8:1")
        self.target.side_effect = [self.original, self.original, replacement]
        with self.assertRaisesRegex(usb.USBError, "changed"):
            self.write()
        self.assertFalse(any(c[0] == "mkfs.fat" for c in self.commands))

    def test_partition_failure_stops_format_and_unmounts_iso(self):
        self.fail_command = "parted"
        with self.assertRaises(usb.USBError):
            self.write()
        self.assertFalse(any(c[0] == "mkfs.fat" for c in self.commands))
        self.assertEqual(self.commands[-1], ["umount", str(self.work / "iso")])

    def test_copy_failure_unmounts_both(self):
        self.copy.side_effect = usb.USBError("copy failure")
        with self.assertRaisesRegex(usb.USBError, "copy failure"):
            self.write()
        self.assertEqual(self.commands[-2:], [["umount", str(self.work / "usb")], ["umount", str(self.work / "iso")]])

    def test_unmount_failure_preserves_work_directory(self):
        self.fail_command = "umount"
        with self.assertRaisesRegex(usb.USBError, "retained"):
            self.write()
        self.assertTrue(self.work.is_dir())
        self.assertEqual(sum(c[0] == "umount" for c in self.commands), 3)

    def test_failed_readback_is_reported_and_unmounted(self):
        self.verify_target.side_effect = usb.USBError("readback corruption")
        with self.assertRaisesRegex(usb.USBError, "readback corruption"):
            self.write()
        self.assertEqual(self.commands[-2:], [["umount", str(self.work / "usb")], ["umount", str(self.work / "iso")]])


if __name__ == "__main__":
    unittest.main()

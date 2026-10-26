"""macOS disk safety tests. Device commands are always mocked."""
import contextlib
import copy
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "прога"))
import macos_usb as mac


def disk_info(**changes):
    info = {
        "DeviceIdentifier": "disk2", "DeviceNode": "/dev/disk2", "Whole": True,
        "Internal": False, "VirtualOrPhysical": "Physical", "BusProtocol": "USB",
        "ReadOnlyMedia": False, "TotalSize": 16 * 1024**3, "MediaName": "Fixture USB",
        "DeviceTreePath": "IODeviceTree:/usb/fixture", "Mounted": False, "MountPoint": "",
    }
    info.update(changes)
    return info


def partition_info(**changes):
    info = {
        "DeviceIdentifier": "disk2s1", "DeviceNode": "/dev/disk2s1", "Whole": False,
        "ParentWholeDisk": "disk2", "Content": "Microsoft Basic Data", "Mounted": False,
        "MountPoint": "", "ReadOnlyVolume": False,
    }
    info.update(changes)
    return info


def native_disk_info(**changes):
    """macOS 15 diskutil key names observed on both native CI runners."""
    info = disk_info()
    for key in ("Whole", "ReadOnlyMedia", "Mounted"):
        del info[key]
    info.update(WholeDisk=True, WritableMedia=True, WritableVolume=False, Writable=True)
    info.update(changes)
    return info


def native_partition_info(**changes):
    info = partition_info()
    for key in ("Whole", "ReadOnlyVolume", "Mounted"):
        del info[key]
    # An unmounted NTFS volume can be read-only to macOS although the media
    # is writable; erasing the USB does not require writing that old volume.
    info.update(WholeDisk=False, WritableMedia=True, WritableVolume=False, Writable=False)
    info.update(changes)
    return info


def media(name="disk2", entry=100, **changes):
    info = {"BSD Name": name, "Whole": True, "IORegistryEntryID": entry}
    info.update(changes)
    return info


class MacDeviceSafetyTests(unittest.TestCase):
    def device(self, info=None, parts=None, roots=None, registry_id=100):
        return mac._device_from_info(info if info is not None else disk_info(),
                                     parts if parts is not None else [partition_info()],
                                     roots if roots is not None else {"disk0"}, registry_id)

    def rejected(self, **kwargs):
        try:
            value = self.device(**kwargs)
        except mac.USBError:
            return
        self.assertFalse(value.safe, "Unsafe or unverifiable disk must not be offered for erasure")

    def test_unmounted_external_usb_is_safe(self):
        result = self.device()
        self.assertTrue(result.safe)
        self.assertEqual(result.path, "/dev/disk2")

    def test_internal_disk_rejected(self):
        self.rejected(info=disk_info(Internal=True))

    def test_unknown_internal_state_rejected(self):
        info = disk_info()
        del info["Internal"]
        self.rejected(info=info)

    def test_non_usb_and_virtual_disks_rejected(self):
        for changes in ({"BusProtocol": "PCI-Express"}, {"BusProtocol": "SATA"},
                        {"BusProtocol": "Thunderbolt"}, {"BusProtocol": None},
                        {"VirtualOrPhysical": "Virtual"}):
            with self.subTest(changes=changes):
                self.rejected(info=disk_info(**changes))

    def test_partition_cannot_be_target(self):
        self.rejected(info=disk_info(Whole=False, DeviceIdentifier="disk2s1", DeviceNode="/dev/disk2s1"))

    def test_system_physical_store_rejected(self):
        self.rejected(roots={"disk0", "disk2"})

    def test_unknown_root_mapping_rejected(self):
        self.rejected(roots=set())

    def test_read_only_disk_rejected(self):
        self.rejected(info=disk_info(ReadOnlyMedia=True))

    def test_missing_safety_metadata_rejected(self):
        for key in ("Whole", "Internal", "BusProtocol", "ReadOnlyMedia", "DeviceNode", "TotalSize"):
            with self.subTest(key=key):
                info = disk_info()
                del info[key]
                self.rejected(info=info)

    def test_mounted_whole_disk_rejected(self):
        self.rejected(info=disk_info(Mounted=True, MountPoint="/Volumes/USB"), parts=[])

    def test_mounted_child_rejected(self):
        self.rejected(parts=[partition_info(Mounted=True, MountPoint="/Volumes/USB")])

    def test_unknown_child_mount_state_rejected(self):
        part = partition_info()
        del part["Mounted"]
        del part["MountPoint"]
        self.rejected(parts=[part])

    def test_mountpoint_rejected_even_with_false_mounted_flag(self):
        self.rejected(parts=[partition_info(Mounted=False, MountPoint="/Volumes/USB")])

    def test_apfs_or_raid_target_rejected_even_if_unmounted(self):
        for content in ("Apple_APFS", "Apple_RAID", "Apple_CoreStorage"):
            with self.subTest(content=content):
                self.rejected(parts=[partition_info(Content=content)])

    def test_missing_registry_identity_rejected(self):
        self.rejected(registry_id=None)

    def test_missing_size_and_small_disk_rejected(self):
        for size in (None, 0, 128 * 1024**2):
            with self.subTest(size=size):
                self.rejected(info=disk_info(TotalSize=size))

    def test_disk_beyond_mbr_capacity_rejected(self):
        self.rejected(info=disk_info(TotalSize=3 * 1024**4))

    def test_replaced_media_changes_identity(self):
        original = self.device()
        self.assertNotEqual(original.identity, self.device(registry_id=101).identity)
        self.assertNotEqual(original.identity, self.device(info=disk_info(TotalSize=8 * 1024**3)).identity)

    def test_filesystem_uuid_is_not_physical_identity(self):
        original = self.device(info=disk_info(MediaUUID="before"))
        repartitioned = self.device(info=disk_info(MediaUUID="after"))
        self.assertEqual(original.identity, repartitioned.identity)


class MacNativeSchemaTests(unittest.TestCase):
    def device(self, disk=None, partition=None):
        return mac._device_from_info(native_disk_info() if disk is None else disk,
                                     [native_partition_info() if partition is None else partition],
                                     {"disk0"}, 100)

    def rejected(self, **kwargs):
        try:
            device = self.device(**kwargs)
        except mac.USBError:
            return
        self.assertFalse(device.safe)

    def test_native_whole_disk_and_partition_schema_is_safe(self):
        device = self.device()
        self.assertTrue(device.safe, device.reason)
        self.assertEqual(device.path, "/dev/disk2")

    def test_volume_readonly_does_not_override_writable_media(self):
        info = native_partition_info(WritableMedia=True, WritableVolume=False, Writable=False)
        self.assertTrue(mac._media_writable(info))
        self.assertTrue(self.device(partition=info).safe)

    def test_media_readonly_cannot_be_overridden_by_writable_volume(self):
        info = native_disk_info(WritableMedia=False, WritableVolume=True, Writable=True)
        self.assertFalse(mac._media_writable(info))
        self.rejected(disk=info)

    def test_conflicting_media_aliases_rejected(self):
        info = native_disk_info(WritableMedia=True, ReadOnlyMedia=True)
        self.assertFalse(mac._media_writable(info))
        self.rejected(disk=info)

    def test_conflicting_whole_disk_aliases_rejected(self):
        self.rejected(disk=native_disk_info(WholeDisk=True, Whole=False))

    def test_missing_whole_disk_metadata_rejected(self):
        info = native_disk_info()
        del info["WholeDisk"]
        self.rejected(disk=info)

    def test_missing_all_writability_metadata_rejected(self):
        info = native_disk_info()
        for key in ("WritableMedia", "WritableVolume", "Writable"):
            del info[key]
        self.rejected(disk=info)

    def test_nonboolean_native_media_writability_rejected(self):
        for value in (None, "true", 1):
            with self.subTest(value=value):
                self.rejected(disk=native_disk_info(WritableMedia=value))

    def test_mountpoint_only_mounted_volume_is_rejected(self):
        self.rejected(partition=native_partition_info(MountPoint="/Volumes/USB"))

    def test_absent_mountpoint_is_not_assumed_unmounted(self):
        info = native_partition_info()
        del info["MountPoint"]
        self.rejected(partition=info)

    def test_invalid_native_mountpoint_is_unknown(self):
        for value in (None, False, 0, "relative/path"):
            with self.subTest(value=value):
                self.rejected(partition=native_partition_info(MountPoint=value))

    def test_readonly_volume_on_writable_media_is_recognized(self):
        self.assertTrue(mac._mount_readonly({"WritableMedia": True, "WritableVolume": False, "Writable": False}))

    def test_writable_volume_is_not_readonly(self):
        self.assertFalse(mac._mount_readonly({"WritableMedia": False, "WritableVolume": True}, allow_media=True))

    def test_conflicting_readonly_volume_aliases_are_rejected(self):
        self.assertFalse(mac._mount_readonly({"WritableVolume": False, "ReadOnlyVolume": False}))

    def test_aggregate_writable_flag_does_not_prove_readonly_mount(self):
        self.assertFalse(mac._mount_readonly({"Writable": False}, allow_media=True))

    def test_native_readonly_source_mount_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory)
            response = {"system-entities": [{"dev-entry": "/dev/disk9s1", "mount-point": str(source)}]}
            info = {"MountPoint": str(source), "WritableMedia": False, "WritableVolume": False, "Writable": False}
            with mock.patch.object(mac, "_plist", return_value=response), \
                 mock.patch.object(mac, "_info", return_value=info):
                self.assertEqual(mac._attach_iso(pathlib.Path("/tmp/input.iso"), source), "/dev/disk9")

    def test_native_readonly_target_mount_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory)
            info = {"MountPoint": str(target), "WritableMedia": True, "WritableVolume": False, "Writable": False}
            with mock.patch.object(mac, "_run") as run, mock.patch.object(mac, "_info", return_value=info):
                mac._mount_partition("/dev/disk2s1", target, read_only=True)
            self.assertEqual(run.call_args.args[0], ["diskutil", "mount", "readOnly", "-mountPoint", str(target), "/dev/disk2s1"])


class MacRegistryAndRootTests(unittest.TestCase):
    def test_ioreg_collects_nested_whole_media_only(self):
        tree = [{"IORegistryEntryChildren": [media(), media("disk2s1", 101, Whole=False)]}]
        self.assertEqual(mac._ioreg_ids(tree), {"disk2": 100})

    def test_conflicting_ioreg_identity_is_rejected(self):
        with self.assertRaises(mac.USBError):
            mac._ioreg_ids([media(entry=100), media(entry=101)])

    def test_direct_root_partition_maps_to_whole_disk(self):
        root = {"DeviceIdentifier": "disk0s2", "ParentWholeDisk": "disk0"}
        self.assertEqual(mac._root_disks(root, {"Containers": []}), {"disk0"})

    def test_apfs_root_snapshot_protects_all_physical_stores(self):
        root = {"DeviceIdentifier": "disk3s1s1", "ParentWholeDisk": "disk3", "APFSContainerReference": "disk3"}
        apfs = {"Containers": [{"ContainerReference": "disk3", "PhysicalStores": [
            {"DeviceIdentifier": "disk0s2"}, {"DeviceIdentifier": "disk1s2"},
        ]}]}
        self.assertEqual(mac._root_disks(root, apfs), {"disk0", "disk1"})

    def test_invalid_root_info_cannot_establish_safety(self):
        try:
            roots = mac._root_disks({}, {"Containers": []})
        except mac.USBError:
            return
        self.assertFalse(roots)

    def test_unresolved_apfs_snapshot_cannot_establish_safety(self):
        root = {"DeviceIdentifier": "disk3s1s1", "ParentWholeDisk": "disk3"}
        with self.assertRaises(mac.USBError):
            mac._root_disks(root, {"Containers": []})


class MacDiscoveryTests(unittest.TestCase):
    def snapshot(self, whole_info):
        topology = {"AllDisksAndPartitions": [{"DeviceIdentifier": "disk2", "Partitions": [
            {"DeviceIdentifier": "disk2s1", "Content": "Microsoft Basic Data"}]}]}
        root = {"DeviceIdentifier": "disk0s2", "ParentWholeDisk": "disk0"}
        def info(path):
            return {"/": root, "/dev/disk2": whole_info, "/dev/disk2s1": native_partition_info()}[path]
        with mock.patch.object(mac, "_plist", side_effect=[topology, {"Containers": []}, [media()]]), \
             mock.patch.object(mac, "_info", side_effect=info):
            return mac._snapshot()

    def test_native_whole_disk_discovery_accepts_valid_usb(self):
        devices = self.snapshot(native_disk_info())
        self.assertEqual(len(devices), 1)
        self.assertTrue(devices[0].safe)

    def test_internal_whole_disk_is_hidden_not_a_partition_error(self):
        self.assertEqual(self.snapshot(native_disk_info(Internal=True)), [])

    def test_wrong_whole_disk_identity_is_rejected(self):
        with self.assertRaises(mac.USBError):
            self.snapshot(native_disk_info(DeviceIdentifier="disk0"))


class MacMountValidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.source = pathlib.Path(temporary.name) / "iso"
        self.target = pathlib.Path(temporary.name) / "usb"
        self.source.mkdir()
        self.target.mkdir()

    def test_iso_attach_uses_readonly_and_exact_private_mount(self):
        response = {"system-entities": [{"dev-entry": "/dev/disk9s1", "mount-point": str(self.source)}]}
        with mock.patch.object(mac, "_plist", return_value=response) as command, \
             mock.patch.object(mac, "_info", return_value={"Mounted": True, "MountPoint": str(self.source), "ReadOnlyVolume": True, "ReadOnlyMedia": True}):
            self.assertEqual(mac._attach_iso(pathlib.Path("/tmp/input.iso"), self.source), "/dev/disk9")
        self.assertIn("-readonly", command.call_args.args[0])
        self.assertIn(str(self.source), command.call_args.args[0])

    def test_iso_with_multiple_mounted_entities_rejected(self):
        response = {"system-entities": [
            {"dev-entry": "/dev/disk9s1", "mount-point": str(self.source)},
            {"dev-entry": "/dev/disk9s2", "mount-point": "/Volumes/unexpected"},
        ]}
        with mock.patch.object(mac, "_plist", return_value=response):
            with self.assertRaises(mac.USBError):
                mac._attach_iso(pathlib.Path("/tmp/input.iso"), self.source)

    def test_target_readback_requires_confirmed_readonly_mount(self):
        info = {"Mounted": True, "MountPoint": str(self.target), "ReadOnlyVolume": False}
        with mock.patch.object(mac, "_run") as command, mock.patch.object(mac, "_info", return_value=info):
            with self.assertRaisesRegex(mac.USBError, "read-only"):
                mac._mount_partition("/dev/disk2s1", self.target, read_only=True)
        self.assertIn("readOnly", command.call_args_list[0].args[0])
        self.assertEqual(command.call_args_list[-1].args[0], ["diskutil", "unmount", "/dev/disk2s1"])

    def test_target_mountpoint_must_match_private_directory(self):
        info = {"Mounted": True, "MountPoint": "/Volumes/unexpected", "ReadOnlyVolume": True}
        with mock.patch.object(mac, "_run"), mock.patch.object(mac, "_info", return_value=info):
            with self.assertRaisesRegex(mac.USBError, "private"):
                mac._mount_partition("/dev/disk2s1", self.target, read_only=True)

    def test_formatted_partition_layout_rejects_extra_or_wrong_partition(self):
        valid = {"AllDisksAndPartitions": [{"DeviceIdentifier": "disk2", "Content": "FDisk_partition_scheme",
                 "Partitions": [{"DeviceIdentifier": "disk2s1", "Content": "DOS_FAT_32"}]}]}
        info = {"FilesystemType": "msdos", "FilesystemName": "MS-DOS FAT32", "VolumeName": "WINUSB",
                "WholeDisk": False, "ParentWholeDisk": "disk2"}
        with mock.patch.object(mac, "_plist", return_value=valid), mock.patch.object(mac, "_info", return_value=info):
            self.assertEqual(mac._new_partition("/dev/disk2"), "/dev/disk2s1")
        extra = copy.deepcopy(valid)
        extra["AllDisksAndPartitions"][0]["Partitions"].append({"DeviceIdentifier": "disk2s2", "Content": "DOS_FAT_32"})
        foreign = copy.deepcopy(valid)
        foreign["AllDisksAndPartitions"][0]["Partitions"][0]["DeviceIdentifier"] = "disk0s1"
        fat16 = copy.deepcopy(valid)
        fat16["AllDisksAndPartitions"][0]["Partitions"][0]["Content"] = "DOS_FAT_16"
        for response in (extra, foreign, fat16):
            with self.subTest(response=response), mock.patch.object(mac, "_plist", return_value=response), \
                 mock.patch.object(mac, "_info", return_value=info):
                with self.assertRaises(mac.USBError):
                    mac._new_partition("/dev/disk2")

    def test_formatted_partition_requires_expected_volume_label(self):
        response = {"AllDisksAndPartitions": [{"DeviceIdentifier": "disk2", "Content": "FDisk_partition_scheme",
                    "Partitions": [{"DeviceIdentifier": "disk2s1", "Content": "DOS_FAT_32"}]}]}
        info = {"FilesystemType": "msdos", "FilesystemName": "MS-DOS FAT32", "VolumeName": "CHANGED",
                "WholeDisk": False, "ParentWholeDisk": "disk2"}
        with mock.patch.object(mac, "_plist", return_value=response), mock.patch.object(mac, "_info", return_value=info):
            with self.assertRaisesRegex(mac.USBError, "label"):
                mac._new_partition("/dev/disk2")


class MacWriteWorkflowTests(unittest.TestCase):
    """All subprocesses and block-device inspection are forbidden or mocked."""

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.image = pathlib.Path(directory) / "windows.iso"
        self.image.write_bytes(b"non-mountable ISO fixture")
        self.work = pathlib.Path(directory) / "work"
        self.work.mkdir()
        self.commands = []
        self.fail_command = None
        self.original = mac.Device("/dev/disk2", 16 * 1024**3, "USB", "fixture", (), "", ("disk2", 100))
        self.stack.enter_context(mock.patch.object(mac.sys, "platform", "darwin"))
        self.stack.enter_context(mock.patch.object(mac.os, "geteuid", return_value=0))
        self.stack.enter_context(mock.patch.object(mac.subprocess, "run", side_effect=AssertionError("Real subprocess forbidden")))
        self.stack.enter_context(mock.patch.object(mac, "check_dependencies", return_value=[]))
        self.stack.enter_context(mock.patch.object(mac, "_device_lock", return_value=contextlib.nullcontext()))
        self.target = self.stack.enter_context(mock.patch.object(mac, "_target", return_value=self.original))
        self.same_target = self.stack.enter_context(mock.patch.object(mac, "_same_target"))
        self.attach = self.stack.enter_context(mock.patch.object(mac, "_attach_iso", return_value="/dev/disk9"))
        self.partition = self.stack.enter_context(mock.patch.object(mac, "_new_partition", return_value="/dev/disk2s1"))
        self.info = self.stack.enter_context(mock.patch.object(mac, "_info", return_value={"MountPoint": "/Volumes/WINUSB"}))
        self.mount = self.stack.enter_context(mock.patch.object(mac, "_mount_partition"))
        self.stack.enter_context(mock.patch.object(mac.tempfile, "mkdtemp", return_value=str(self.work)))
        self.validate = self.stack.enter_context(mock.patch.object(mac.portable, "_validate_source", return_value=([], 1024, None)))
        self.verify_source = self.stack.enter_context(mock.patch.object(mac.portable, "_verify_source_wims"))
        self.copy = self.stack.enter_context(mock.patch.object(mac.portable, "_copy_and_verify"))
        self.verify_target = self.stack.enter_context(mock.patch.object(mac.portable, "_verify_target"))
        self.stack.enter_context(mock.patch.object(mac, "_run", side_effect=self.run_command))

    def run_command(self, args, **kwargs):
        self.commands.append(args)
        if self.fail_command is not None and self.fail_command(args):
            raise mac.USBError("injected command failure")
        return subprocess.CompletedProcess(args, 0, "", "")

    def write(self, confirm="/dev/disk2", **kwargs):
        return mac.write_usb(str(self.image), "/dev/disk2", confirm, log=lambda _: None, **kwargs)

    def assert_no_erase(self):
        self.assertFalse(any("eraseDisk" in args or "partitionDisk" in args for args in self.commands))

    def assert_detached(self):
        self.assertTrue(any(args[:2] == ["hdiutil", "detach"] and "/dev/disk9" in args for args in self.commands))

    def test_success_verifies_and_cleans_up(self):
        self.write()
        self.verify_source.assert_called_once()
        self.copy.assert_called_once()
        self.verify_target.assert_called_once()
        self.assertTrue(any(args[:2] == ["diskutil", "eraseDisk"] for args in self.commands))
        self.assertEqual(self.mount.call_count, 2)
        self.assertTrue(self.mount.call_args.kwargs.get("read_only"))
        self.assert_detached()
        self.assertFalse(self.work.exists())

    def test_wrong_confirmation_never_attaches_or_erases(self):
        with self.assertRaises(mac.USBError):
            self.write(confirm="/dev/disk0")
        self.attach.assert_not_called()
        self.assert_no_erase()

    def test_nonroot_never_attaches_or_erases(self):
        with mock.patch.object(mac.os, "geteuid", return_value=501):
            with self.assertRaises(mac.USBError):
                self.write()
        self.attach.assert_not_called()
        self.assert_no_erase()

    def test_missing_dependencies_prevents_write(self):
        with mock.patch.object(mac, "check_dependencies", return_value=["wimlib-imagex"]):
            with self.assertRaises(mac.USBError):
                self.write()
        self.attach.assert_not_called()
        self.assert_no_erase()

    def test_changed_gui_identity_prevents_mount(self):
        with self.assertRaises(mac.USBError):
            self.write(expected_identity=("disk2", 99))
        self.attach.assert_not_called()
        self.assert_no_erase()

    def test_invalid_iso_structure_detaches_before_erase(self):
        self.validate.side_effect = mac.USBError("invalid Windows ISO")
        with self.assertRaises(mac.USBError):
            self.write()
        self.assert_no_erase()
        self.assert_detached()

    def test_capacity_checked_before_erase(self):
        self.validate.return_value = ([], self.original.size, None)
        with self.assertRaises(mac.USBError):
            self.write()
        self.assert_no_erase()
        self.assert_detached()

    def test_corrupt_wim_detaches_before_erase(self):
        self.verify_source.side_effect = mac.USBError("corrupt WIM")
        with self.assertRaises(mac.USBError):
            self.write()
        self.assert_no_erase()
        self.assert_detached()

    def test_device_swap_before_erase_is_rejected(self):
        self.same_target.side_effect = mac.USBError("device replaced")
        with self.assertRaises(mac.USBError):
            self.write()
        self.assert_no_erase()
        self.assert_detached()

    def test_erase_failure_stops_copy_and_detaches(self):
        self.fail_command = lambda args: "eraseDisk" in args
        with self.assertRaises(mac.USBError):
            self.write()
        self.copy.assert_not_called()
        self.assert_detached()

    def test_copy_failure_detaches_source(self):
        self.copy.side_effect = mac.USBError("write failed")
        with self.assertRaisesRegex(mac.USBError, "write failed"):
            self.write()
        self.copy.assert_called_once()
        self.assert_detached()
        self.verify_target.assert_not_called()

    def test_readback_failure_is_reported(self):
        self.verify_target.side_effect = mac.USBError("readback mismatch")
        with self.assertRaisesRegex(mac.USBError, "readback mismatch"):
            self.write()
        self.verify_target.assert_called_once()
        self.assert_detached()

    def test_detach_failure_preserves_mount_directories(self):
        self.fail_command = lambda args: args[:2] == ["hdiutil", "detach"]
        with self.assertRaises(mac.USBError):
            self.write()
        self.assertTrue(self.work.exists())

    def test_target_unmount_failure_preserves_mount_directories(self):
        self.fail_command = lambda args: args[:2] == ["diskutil", "unmount"]
        with self.assertRaisesRegex(mac.USBError, "retained"):
            self.write()
        self.assertTrue(self.work.exists())
        self.assert_detached()

    def test_replacement_after_format_prevents_copy(self):
        self.same_target.side_effect = [None, mac.USBError("device replaced after format")]
        with self.assertRaisesRegex(mac.USBError, "replaced after format"):
            self.write()
        self.copy.assert_not_called()
        self.mount.assert_not_called()
        self.assert_detached()

    def test_replacement_before_readback_prevents_readback_mount(self):
        self.same_target.side_effect = [None, None, None, None, mac.USBError("replaced before readback")]
        with self.assertRaisesRegex(mac.USBError, "replaced before readback"):
            self.write()
        self.assertEqual(self.mount.call_count, 1)
        self.verify_target.assert_not_called()
        self.assert_detached()

    def test_partial_attach_is_detached_by_exact_private_mountpoint(self):
        mounted = set()
        def attach(image, source):
            mounted.add(str(source))
            raise mac.USBError("attach validation failed")
        def command(args, **kwargs):
            result = self.run_command(args, **kwargs)
            if args[:2] == ["hdiutil", "detach"]:
                mounted.discard(args[-1])
            return result
        self.attach.side_effect = attach
        with mock.patch.object(pathlib.Path, "is_mount", autospec=True, side_effect=lambda path: str(path) in mounted), \
             mock.patch.object(mac, "_run", side_effect=command):
            with self.assertRaisesRegex(mac.USBError, "attach validation failed"):
                self.write()
        self.assertIn(["hdiutil", "detach", str(self.work / "iso")], self.commands)
        self.assert_no_erase()
        self.assertFalse(self.work.exists())

    def test_partial_target_mount_is_cleaned_after_validation_failure(self):
        mounted = set()
        def mount(partition, target, **kwargs):
            mounted.add(str(target))
            raise mac.USBError("mount validation failed")
        def command(args, **kwargs):
            result = self.run_command(args, **kwargs)
            if args[:2] == ["diskutil", "unmount"]:
                mounted.discard(args[-1])
            return result
        self.mount.side_effect = mount
        with mock.patch.object(pathlib.Path, "is_mount", autospec=True, side_effect=lambda path: str(path) in mounted), \
             mock.patch.object(mac, "_run", side_effect=command):
            with self.assertRaisesRegex(mac.USBError, "mount validation failed"):
                self.write()
        self.assertIn(["diskutil", "unmount", str(self.work / "usb")], self.commands)
        self.assert_detached()
        self.assertFalse(self.work.exists())


class MacLockAndIdentityTests(unittest.TestCase):
    def lock_patches(self, lock_metadata=None):
        stack = contextlib.ExitStack()
        metadata = lock_metadata or mock.Mock(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_nlink=1)
        stack.enter_context(mock.patch.object(mac.os, "open", side_effect=[11, 12]))
        stack.enter_context(mock.patch.object(mac.os, "fstat", side_effect=[mock.Mock(st_mode=stat.S_IFBLK), metadata]))
        lock = stack.enter_context(mock.patch.object(mac.fcntl, "flock"))
        close = stack.enter_context(mock.patch.object(mac.os, "close"))
        return stack, lock, close

    def test_concurrent_writer_rejected_and_descriptors_closed(self):
        stack, lock, close = self.lock_patches()
        with stack:
            lock.side_effect = BlockingIOError("busy")
            with self.assertRaisesRegex(mac.USBError, "Another writer"):
                with mac._device_lock("/dev/disk2"):
                    self.fail("A competing writer acquired the lock")
        self.assertEqual(close.call_args_list, [mock.call(11), mock.call(12)])

    def test_lock_descriptors_closed_on_body_failure(self):
        stack, lock, close = self.lock_patches()
        with stack:
            with self.assertRaisesRegex(mac.USBError, "fixture failure"):
                with mac._device_lock("/dev/disk2"):
                    raise mac.USBError("fixture failure")
        lock.assert_called_once_with(12, mac.fcntl.LOCK_EX | mac.fcntl.LOCK_NB)
        self.assertEqual(close.call_args_list, [mock.call(11), mock.call(12)])

    def test_unsafe_lock_owner_rejected(self):
        stack, lock, close = self.lock_patches(mock.Mock(st_mode=stat.S_IFREG | 0o600, st_uid=501, st_nlink=1))
        with stack:
            with self.assertRaisesRegex(mac.USBError, "Unsafe"):
                with mac._device_lock("/dev/disk2"):
                    self.fail("User-owned root lock was accepted")
        lock.assert_not_called()
        self.assertEqual(close.call_args_list, [mock.call(11), mock.call(12)])

    def test_writable_root_lock_rejected(self):
        stack, lock, close = self.lock_patches(mock.Mock(st_mode=stat.S_IFREG | 0o666, st_uid=0, st_nlink=1))
        with stack:
            with self.assertRaisesRegex(mac.USBError, "Unsafe"):
                with mac._device_lock("/dev/disk2"):
                    self.fail("Group/world-writable root lock was accepted")
        lock.assert_not_called()
        self.assertEqual(close.call_args_list, [mock.call(11), mock.call(12)])

    def test_mount_revalidation_rejects_extra_mountpoint(self):
        original = mac.Device("/dev/disk2", 16 * 1024**3, "USB", "fixture", (), "", ("disk2", 100))
        unexpected = mac.Device(original.path, original.size, original.model, original.serial,
                                ("/tmp/usb", "/Volumes/unexpected"), "", original.identity)
        with mock.patch.object(mac, "_target", return_value=unexpected):
            with self.assertRaisesRegex(mac.USBError, "Unexpected mounted"):
                mac._same_target(original, allow_mounted=True, expected_mount="/tmp/usb")


if __name__ == "__main__":
    unittest.main()

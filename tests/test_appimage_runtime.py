"""Frozen runtime tests without launching an AppImage or touching USB devices."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
import linux_gui
import linux_usb


class FrozenCommandTests(unittest.TestCase):
    def setUp(self):
        self.device = linux_usb.Device('/dev/sdb', 16 * 2**30, 'USB', 'serial', (), '', ('identity', 7))

    def test_appimage_uses_original_image_not_user_fuse_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'Win USB.AppImage'
            image.touch(mode=0o755)
            with patch.object(sys, 'frozen', True, create=True), \
                 patch.object(sys, 'executable', '/tmp/.mount_app/usr/bin/flash-usb'), \
                 patch.dict(os.environ, {'APPIMAGE': str(image)}):
                command = linux_gui.build_write_command('/tmp/Windows ISO.iso', self.device, '/dev/sdb')
            self.assertEqual(command[:3], [str(image), '--appimage-extract-and-run', 'write'])
            self.assertNotIn('/tmp/.mount_app/usr/bin/flash-usb', command)
            with patch.object(linux_usb, 'write_usb') as write:
                self.assertEqual(linux_usb.main(command[2:]), 0)
            write.assert_called_once_with('/tmp/Windows ISO.iso', '/dev/sdb', '/dev/sdb',
                                          expected_identity=['identity', 7])

    def test_frozen_directory_binary_receives_cli_not_python_script(self):
        with patch.object(sys, 'frozen', True, create=True), \
             patch.object(sys, 'executable', '/opt/flash-usb/flash-usb'), \
             patch.dict(os.environ, {}, clear=True):
            command = linux_gui.build_write_command('/tmp/Windows.iso', self.device, '/dev/sdb')
        self.assertEqual(command[:2], ['/opt/flash-usb/flash-usb', 'write'])
        self.assertNotIn('-u', command)
        self.assertFalse(any(value.endswith('linux_usb.py') for value in command))

    def test_unavailable_original_image_rejected_instead_of_fuse_fallback(self):
        with patch.object(sys, 'frozen', True, create=True):
            for image in ('relative.AppImage', '/nonexistent/WinUSB.AppImage'):
                with self.subTest(image=image), patch.dict(os.environ, {'APPIMAGE': image}):
                    with self.assertRaises(linux_usb.USBError):
                        linux_gui.build_write_command('/tmp/a.iso', self.device, '/dev/sdb')

    def test_image_without_execute_permission_rejected(self):
        with tempfile.NamedTemporaryFile() as image, \
             patch.object(sys, 'frozen', True, create=True), \
             patch.dict(os.environ, {'APPIMAGE': image.name}):
            with self.assertRaises(linux_usb.USBError):
                linux_gui.build_write_command('/tmp/a.iso', self.device, '/dev/sdb')

    def test_native_launch_ignores_appimage_environment(self):
        with patch.object(sys, 'frozen', False, create=True), \
             patch.dict(os.environ, {'APPIMAGE': '/nonexistent/image'}):
            command = linux_gui.build_write_command('/tmp/a.iso', self.device, '/dev/sdb')
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1], '-u')
        self.assertTrue(command[2].endswith('linux_usb.py'))
        self.assertEqual(command[3], 'write')


class FrozenEnvironmentTests(unittest.TestCase):
    def test_restores_original_loader_path_without_changing_gui_environment(self):
        initial = {'LD_LIBRARY_PATH': '/tmp/bundle:/usr/local/lib',
                   'LD_LIBRARY_PATH_ORIG': '/usr/local/lib', 'DISPLAY': ':99'}
        with patch.object(sys, 'frozen', True, create=True), patch.dict(os.environ, initial, clear=True):
            child = linux_usb.subprocess_environment()
            self.assertEqual(child['LD_LIBRARY_PATH'], '/usr/local/lib')
            self.assertNotIn('LD_LIBRARY_PATH_ORIG', child)
            self.assertEqual(child['DISPLAY'], ':99')
            self.assertEqual(dict(os.environ), initial)

    def test_removes_private_loader_path_when_no_original_path(self):
        with patch.object(sys, 'frozen', True, create=True), \
             patch.dict(os.environ, {'LD_LIBRARY_PATH': '/tmp/bundle'}, clear=True):
            self.assertNotIn('LD_LIBRARY_PATH', linux_usb.subprocess_environment())

    def test_fresh_runtime_discards_old_extraction_and_tk_paths(self):
        initial = {'_PYI_APPLICATION_HOME_DIR': '/tmp/old', '_PYI_ARCHIVE_FILE': '/tmp/old/exe',
                   '_PYI_PARENT_PROCESS_LEVEL': '1', '_MEIPASS2': '/tmp/legacy',
                   'TCL_LIBRARY': '/tmp/old/tcl', 'TK_LIBRARY': '/tmp/old/tk',
                   'APPIMAGE': '/downloads/WinUSB.AppImage'}
        with patch.object(sys, 'frozen', True, create=True), patch.dict(os.environ, initial, clear=True):
            child = linux_usb.subprocess_environment(restart_app=True)
        self.assertEqual(child, {'APPIMAGE': initial['APPIMAGE'], 'PYINSTALLER_RESET_ENVIRONMENT': '1'})

    def test_unfrozen_environment_is_preserved(self):
        initial = {'LD_LIBRARY_PATH': '/system/custom', 'TCL_LIBRARY': '/system/tcl'}
        with patch.object(sys, 'frozen', False, create=True), patch.dict(os.environ, initial, clear=True):
            child = linux_usb.subprocess_environment(restart_app=True)
            self.assertEqual(child, initial)
            child['NEW'] = 'child only'
            self.assertNotIn('NEW', os.environ)

    def test_system_command_receives_sanitized_environment(self):
        with patch.object(linux_usb, '_tool', return_value='/usr/bin/lsblk'), \
             patch.object(linux_usb, 'subprocess_environment', return_value={'SAFE': '1'}), \
             patch.object(linux_usb.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            linux_usb._run(['lsblk', '--json'])
        self.assertEqual(run.call_args.kwargs['env'], {'SAFE': '1'})

    def test_gui_worker_requests_a_fresh_runtime_environment(self):
        app = linux_gui.LinuxUSB.__new__(linux_gui.LinuxUSB)
        app.events = queue.Queue()
        process = MagicMock()
        process.__enter__.return_value = process
        process.stdout = ['checked\n']
        process.wait.return_value = 0
        with patch.object(linux_gui, 'subprocess_environment', return_value={'SAFE': '1'}) as environment, \
             patch.object(linux_gui.subprocess, 'Popen', return_value=process) as popen:
            app.worker(['/usr/bin/pkexec', '/tmp/WinUSB.AppImage', '--help'])
        environment.assert_called_once_with(restart_app=True)
        self.assertEqual(popen.call_args.kwargs['env'], {'SAFE': '1'})
        self.assertEqual(app.events.get_nowait(), ('log', 'checked'))
        self.assertEqual(app.events.get_nowait(), ('done', 0))

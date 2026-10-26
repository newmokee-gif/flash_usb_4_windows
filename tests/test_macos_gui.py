"""macOS privilege command construction; no shell or disk commands execute."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
import linux_usb
import macos_gui


class MacGUICommandTests(unittest.TestCase):
    def setUp(self):
        self.device = linux_usb.Device('/dev/disk8', 16 * 2**30, 'USB', 'serial', (), '', ('disk8', 42))

    def test_elevation_passes_quoted_data_not_applescript_source(self):
        original = ['/Applications/My "App".app/Contents/MacOS/WinUSB', 'write',
                    '/tmp/a\n"; $(touch SHOULD_NOT_EXIST) \'test.iso', '/dev/disk8', '--confirm', '/dev/disk8']
        with patch.object(os, 'geteuid', return_value=501):
            command = macos_gui.elevation_command(original)
        self.assertEqual(command[:2], ['/usr/bin/osascript', '-e'])
        self.assertEqual(command[2], macos_gui.AUTHORIZATION_SCRIPT)
        self.assertEqual(shlex.split(command[3]), ['/usr/bin/env', 'PYINSTALLER_RESET_ENVIRONMENT=1', *original])
        self.assertNotIn('SHOULD_NOT_EXIST', command[2])

    @unittest.skipUnless(sys.platform == 'darwin', 'requires macOS AppleScript')
    def test_osascript_argv_shell_roundtrip_without_privilege_prompt(self):
        value = 'path with "quotes" and $(echo SHOULD_NOT_EXECUTE); apostrophe\''
        script = macos_gui.AUTHORIZATION_SCRIPT.replace(' with administrator privileges', '')
        shell_command = shlex.join(['/usr/bin/printf', '%s', value])
        result = subprocess.run(['/usr/bin/osascript', '-e', script, shell_command],
                                text=True, capture_output=True, timeout=30, check=True)
        self.assertEqual(result.stdout.rstrip('\n'), value)

    def test_root_does_not_request_authorization_again(self):
        with patch.object(os, 'geteuid', return_value=0):
            self.assertEqual(macos_gui.elevation_command(['program', '--help']), ['program', '--help'])

    def test_frozen_command_restarts_bundle_and_keeps_identity(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', '/Applications/WinUSB.app/Contents/MacOS/WinUSB'):
            command = macos_gui.build_write_command('/tmp/Windows ARM.iso', self.device, self.device.path)
        self.assertEqual(command[:3], ['/Applications/WinUSB.app/Contents/MacOS/WinUSB', 'write', '/tmp/Windows ARM.iso'])
        self.assertEqual(json.loads(command[-1]), list(self.device.identity))

    def test_source_command_runs_macos_backend(self):
        with patch.object(sys, 'frozen', False, create=True):
            command = macos_gui.build_write_command('/tmp/w.iso', self.device, self.device.path)
        self.assertEqual(Path(command[3]).name, 'macos_usb.py')
        self.assertEqual(command[4], 'write')

    def test_fresh_frozen_macos_child_removes_loader_injection(self):
        with patch.object(sys, 'platform', 'darwin'), patch.object(sys, 'frozen', True, create=True), patch.dict(os.environ, {'DYLD_LIBRARY_PATH': '/bundle', 'DYLD_INSERT_LIBRARIES': '/inject', '_PYI_APPLICATION_HOME_DIR': '/old'}, clear=True):
            result = linux_usb.subprocess_environment(restart_app=True)
            self.assertIn('DYLD_LIBRARY_PATH', os.environ)
        self.assertFalse(any(name.startswith('DYLD_') or name.startswith('_PYI_') for name in result))
        self.assertEqual(result['PYINSTALLER_RESET_ENVIRONMENT'], '1')

"""Exercise GUI command construction against the real CLI argument parser."""
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
import linux_usb


class GUICommandTests(unittest.TestCase):
    def test_command_preserves_paths_confirmation_and_identity(self):
        # Importing tkinter requires its Python module, but no display/server.
        from linux_gui import build_write_command
        device = linux_usb.Device('/dev/sdb', 16 * 2**30, 'USB', 'serial', (), '', ('identity', 1))
        command = build_write_command('/tmp/windows image.iso', device, '/dev/sdb')
        self.assertEqual(command[3], 'write')
        with patch.object(linux_usb, 'write_usb') as write:
            self.assertEqual(linux_usb.main(command[3:]), 0)
        write.assert_called_once_with('/tmp/windows image.iso', '/dev/sdb', '/dev/sdb', expected_identity=['identity', 1])

    def test_invalid_identity_is_reported_without_writing(self):
        with patch.object(linux_usb, 'write_usb') as write, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(linux_usb.main(['write', '/tmp/a.iso', '/dev/sdb', '--confirm', '/dev/sdb', '--expected-identity', '{}']), 1)
        write.assert_not_called()

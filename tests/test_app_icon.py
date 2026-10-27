"""Icon asset and runtime checks without Pillow or a display server."""
import base64
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "прога"))
import app_icon


class IconTests(unittest.TestCase):
    def test_png_has_alpha_and_expected_size(self):
        data = (ROOT / "прога/app_icon.png").read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", data[16:24]), (256, 256))
        self.assertEqual(data[25], 6)  # RGBA

    def test_linux_launcher_uses_same_artwork(self):
        svg = ET.parse(ROOT / "packaging/flash-usb.svg").getroot()
        image = svg.find("{http://www.w3.org/2000/svg}image")
        uri = image.attrib["{http://www.w3.org/1999/xlink}href"]
        self.assertEqual(base64.b64decode(uri.split(",", 1)[1]),
                         (ROOT / "прога/app_icon.png").read_bytes())

    def test_platform_icon_containers(self):
        ico = (ROOT / "прога/app_icon.ico").read_bytes()
        self.assertEqual(struct.unpack("<HHH", ico[:6]), (0, 1, 7))
        sizes = {ico[6 + i * 16] or 256 for i in range(7)}
        self.assertEqual(sizes, {16, 24, 32, 48, 64, 128, 256})
        icns = (ROOT / "packaging/icons/app_icon.icns").read_bytes()
        self.assertEqual(icns[:4], b"icns")
        self.assertEqual(struct.unpack(">I", icns[4:8])[0], len(icns))

    def test_window_icon_reference_is_retained(self):
        root = Mock()
        with patch.object(app_icon.sys, "platform", "linux"), patch.object(app_icon.tk, "PhotoImage") as photo:
            app_icon.set_app_icon(root)
            root.iconphoto.assert_called_once_with(True, photo.return_value)
            self.assertIs(root._app_icon_img, photo.return_value)
            self.assertTrue(Path(photo.call_args.kwargs["file"]).is_file())

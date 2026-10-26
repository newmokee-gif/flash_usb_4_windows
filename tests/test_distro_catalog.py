import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
import distro_catalog as catalog


class CatalogTests(unittest.TestCase):
    def test_catalog_has_distinct_distributions_and_real_architectures(self):
        self.assertGreaterEqual(len({e.distro for e in catalog.CATALOG}), 20)
        self.assertGreaterEqual(len({e.distro for e in catalog.CATALOG if e.architecture == "ARM64"}), 5)
        self.assertEqual(len(catalog.CATALOG), len({e.id for e in catalog.CATALOG}))
        self.assertFalse(any(e.distro == "Arch Linux" and e.architecture == "ARM64" for e in catalog.CATALOG))

    def test_official_manifest_resolves_filename_and_torrent(self):
        entry = next(e for e in catalog.CATALOG if e.id == "ubuntu")
        filename = "ubuntu-24.04.4-desktop-amd64.iso"
        with patch.object(catalog, "_metadata", return_value="a"*64 + "  " + filename):
            resolved = catalog.resolve_image(entry)
        self.assertEqual(resolved.filename, filename)
        self.assertEqual(resolved.sha256, "a"*64)
        self.assertEqual(resolved.torrent_url, resolved.image_url + ".torrent")

    def test_missing_architecture_fails_without_guessing_url(self):
        entry = next(e for e in catalog.CATALOG if e.id == "ubuntu-arm64")
        with patch.object(catalog, "_metadata", return_value="a"*64 + "  ubuntu-24.04.4-live-server-amd64.iso"):
            with self.assertRaises(catalog.DownloadError):
                catalog.resolve_image(entry)

    def test_unsigned_html_does_not_replace_checksum(self):
        entry = next(e for e in catalog.CATALOG if e.id == "ubuntu")
        with patch.object(catalog, "_metadata", return_value="<html>ubuntu-24.04.4-desktop-amd64.iso</html>"):
            with self.assertRaises(catalog.DownloadError):
                catalog.resolve_image(entry)

    def test_checksum_names_reject_directory_traversal(self):
        self.assertNotIn("bad.iso", catalog._manifest_names("a"*64 + "  ../../bad.iso"))

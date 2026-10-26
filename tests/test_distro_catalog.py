import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
import distro_catalog as catalog


class CatalogTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(catalog, "discover_torrent", side_effect=lambda image: image)
        patcher.start()
        self.addCleanup(patcher.stop)

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

    def test_nixos_alias_uses_canonical_manifest_filename(self):
        entry = next(e for e in catalog.CATALOG if e.id == "nixos-x64")
        filename = "nixos-minimal-26.05.800000.abcdef-x86_64-linux.iso"
        with patch.object(catalog, "_metadata", return_value="b"*64 + "  " + filename):
            resolved = catalog.resolve_image(entry)
        self.assertEqual(resolved.image_url, entry.image_url)
        self.assertEqual(resolved.filename, filename)
        self.assertEqual(resolved.sha256, "b"*64)

    def test_per_image_hash_is_bound_to_explicit_sidecar(self):
        entry = next(e for e in catalog.CATALOG if e.id == "nixos-arm64")
        with patch.object(catalog, "_metadata", return_value="c"*64 + "\n"):
            resolved = catalog.resolve_image(entry)
        self.assertEqual(resolved.sha256, "c"*64)

    def test_index_links_return_only_simple_basenames(self):
        html = '<a href="./one.iso">a</a><a href="https://example.org/iso/two.iso">b</a><a href="https://other.org/iso/evil.iso">c</a>'
        with patch.object(catalog, "_metadata", return_value=html):
            self.assertEqual(catalog._links("https://example.org/iso/"), ["one.iso", "two.iso", "evil.iso"])

    def test_fedora_redirected_index_uses_original_publisher_for_manifest(self):
        entry = next(e for e in catalog.CATALOG if e.id == "fedora-x64")
        checksum = "Fedora-Workstation-43-1.6-x86_64-CHECKSUM"
        filename = "Fedora-Workstation-Live-43-1.6.x86_64.iso"
        for href in (
            "/fedora/fedora/linux/releases/43/Workstation/x86_64/iso/" + checksum,
            "https://mirror.example/fedora/fedora/linux/releases/43/Workstation/x86_64/iso/" + checksum,
        ):
            with self.subTest(href=href), patch.object(catalog, "_metadata", side_effect=[f'<a href="{href}">checksum</a>', "d"*64 + "  " + filename]) as metadata:
                image = catalog.resolve_image(entry)
            self.assertEqual(metadata.call_args_list[1].args[0], entry.image_url + checksum)
            self.assertEqual(image.checksum_url, entry.image_url + checksum)
            self.assertEqual(image.image_url, entry.image_url + filename)

    def test_gentoo_publisher_pointer_requires_named_sha512(self):
        entry = next(e for e in catalog.CATALOG if e.id == "gentoo")
        filename = "install-amd64-minimal-20261025T170000Z.iso"
        pointer = "20261025T170000Z/" + filename + " 123456\n"
        manifest = "# SHA512 HASH\n" + "a"*128 + "  " + filename + "\n# BLAKE2B HASH\n" + "b"*128 + "  " + filename
        with patch.object(catalog, "_metadata", side_effect=[pointer, manifest]):
            resolved = catalog.resolve_image(entry)
        self.assertEqual(resolved.filename, filename)
        self.assertTrue(resolved.checksum_url.endswith(".iso.DIGESTS"))
        self.assertEqual(resolved.checksum_algorithm, "sha512")
        self.assertEqual(resolved.sha512, "a"*128)

    def test_gentoo_rejects_traversing_pointer(self):
        entry = next(e for e in catalog.CATALOG if e.id == "gentoo")
        with patch.object(catalog, "_metadata", return_value="../../untrusted.iso"):
            with self.assertRaises(catalog.DownloadError):
                catalog.resolve_image(entry)

    def test_mageia_uses_publisher_mirror_with_https(self):
        entry = next(e for e in catalog.CATALOG if e.id == "mageia")
        url = "https://mirror.example/" + entry.filename
        with patch.object(catalog, "_metadata", side_effect=[f'<a href="{url}">download</a>', "b"*128 + "  " + entry.filename]):
            resolved = catalog.resolve_image(entry)
        self.assertEqual(resolved.image_url, url)
        self.assertEqual(resolved.checksum_url, url + ".sha512")


class TorrentDiscoveryTests(unittest.TestCase):
    def test_discovers_publisher_release_torrent_containing_selected_image(self):
        image = catalog.DistroImage("rocky-x64", "Rocky Linux", "Minimal", "x64", "https://example.org/iso/Rocky-9.7-x86_64-minimal.iso", filename="Rocky-9.7-x86_64-minimal.iso", sha256="a"*64)
        page = b'<a href="Rocky-9.7-x86_64.torrent">torrent</a>'
        with patch.object(catalog, "fetch", side_effect=[page, b"metadata"]), patch.object(catalog, "torrent_target") as validate:
            resolved = catalog.discover_torrent(image)
        self.assertEqual(resolved.torrent_url, "https://example.org/iso/Rocky-9.7-x86_64.torrent")
        validate.assert_called_once_with(b"metadata", image.filename)

    def test_bad_or_missing_torrent_keeps_verified_https_image(self):
        image = catalog.DistroImage("example", "Example", "Live", "x64", "https://example.org/os.iso", filename="os.iso", sha256="b"*64)
        with patch.object(catalog, "fetch", side_effect=OSError("offline")):
            self.assertIs(catalog.discover_torrent(image), image)

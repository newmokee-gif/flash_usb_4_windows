import sys
import json
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
        self.assertGreaterEqual(len({e.distro for e in catalog.CATALOG}), 60)
        self.assertGreaterEqual(len({e.distro for e in catalog.CATALOG if e.architecture == "ARM64"}), 5)
        self.assertEqual(len(catalog.CATALOG), len({e.id for e in catalog.CATALOG}))
        self.assertFalse(any(e.distro == "Arch Linux" and e.architecture == "ARM64" for e in catalog.CATALOG))

    def test_thirty_new_distributions_are_not_counted_as_architecture_duplicates(self):
        additions = {"EndeavourOS", "Artix Linux", "Garuda Linux", "antiX", "Linux Lite", "deepin",
                     "SparkyLinux", "Bazzite", "Tails", "Rescuezilla", "SystemRescue", "Proxmox VE",
                     "Slint", "Zorin OS", "PureOS", "Nobara Linux", "Solus", "MX Linux", "Peppermint OS",
                     "Archcraft", "Calculate Linux", "Ultramarine Linux", "Oracle Linux", "Puppy Linux",
                     "GParted Live", "Chimera Linux", "openEuler", "Vanilla OS", "Rhino Linux", "Parrot OS"}
        self.assertEqual(len(additions), 30)
        self.assertTrue(additions <= {e.distro for e in catalog.CATALOG if e.architecture == "x64"})
        self.assertGreaterEqual(sum(e.architecture == "ARM64" for e in catalog.CATALOG), 17)
        for entry in catalog.CATALOG:
            self.assertIn(entry.checksum_algorithm, ("sha256", "sha512"))
            self.assertTrue(entry.checksum_url or entry.api_json_url or entry.release_pointer_url
                            or entry.publisher_page_url or entry.checksum_suffix or entry.checksum_pattern)
            for url in (entry.image_url, entry.checksum_url, entry.api_json_url, entry.torrent_url):
                if url: self.assertTrue(url.startswith("https://"), entry.id)

    def test_gparted_sha256_section_cannot_be_confused_with_blake3(self):
        entry = next(e for e in catalog.CATALOG if e.id == "gparted")
        sha256 = "### SHA256SUMS:\n" + "a"*64 + "  " + entry.filename + "\n"
        blake3 = "### B3SUMS:\n" + "b"*64 + "  " + entry.filename + "\n"
        with patch.object(catalog, "_metadata", return_value=sha256 + blake3):
            self.assertEqual(catalog.resolve_image(entry).sha256, "a"*64)
        for manifest in (blake3, sha256 + sha256 + blake3):
            with self.subTest(manifest=manifest), patch.object(catalog, "_metadata", return_value=manifest):
                with self.assertRaises(catalog.DownloadError): catalog.resolve_image(entry)

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

    def test_sha512_is_retained_for_fixed_images(self):
        for id in ("endeavouros", "peppermint"):
            entry = next(e for e in catalog.CATALOG if e.id == id)
            with self.subTest(id=id), patch.object(catalog, "_metadata", return_value="a"*128 + "  " + entry.filename):
                image = catalog.resolve_image(entry)
                self.assertEqual(image.sha512, "a"*128)
                self.assertIsNone(image.sha256)

    def test_sha512_is_retained_for_directory_channels(self):
        entry = catalog.channel("test", "Test", "Live", "x64", "https://example.org/",
                                r"test-x64\.iso", "SHA512SUMS", algorithm="sha512")
        with patch.object(catalog, "_metadata", return_value="a"*128 + "  test-x64.iso"):
            self.assertEqual(catalog.resolve_image(entry).sha512, "a"*128)

    def test_garuda_alias_keeps_official_url_and_canonical_torrent_filename(self):
        entry = next(e for e in catalog.CATALOG if e.id == "garuda")
        filename = "garuda-dr460nized-linux-garuda-260819.iso"
        with patch.object(catalog, "_metadata", return_value="a"*64 + "  " + filename):
            image = catalog.resolve_image(entry)
        self.assertEqual(image.image_url, entry.image_url)
        self.assertEqual(image.filename, filename)
        self.assertEqual(image.torrent_url, entry.torrent_url)

    def test_alias_cannot_select_different_architecture_or_multiple_images(self):
        entry = next(e for e in catalog.CATALOG if e.id == "garuda")
        for manifest in ("a"*64 + "  wrong-arm64.iso",
                         "a"*64 + "  garuda-dr460nized-linux-garuda-260819.iso\n" + "b"*64 + "  other.iso"):
            with self.subTest(manifest=manifest), patch.object(catalog, "_metadata", return_value=manifest):
                with self.assertRaises(catalog.DownloadError):
                    catalog.resolve_image(entry)

    def test_publisher_manifest_prefix_is_not_used_as_a_download_path(self):
        entry = next(e for e in catalog.CATALOG if e.id == "ultramarine")
        manifest = "a"*64 + "  " + entry.checksum_path_prefix + entry.filename
        with patch.object(catalog, "_metadata", return_value=manifest):
            image = catalog.resolve_image(entry)
        self.assertEqual(image.filename, entry.filename)
        self.assertEqual(image.image_url, entry.image_url)
        self.assertEqual(image.sha256, "a"*64)
        with patch.object(catalog, "_metadata", return_value=manifest.replace(entry.checksum_path_prefix, "another/")):
            with self.assertRaises(catalog.DownloadError):
                catalog.resolve_image(entry)

    def test_bare_checksum_is_rejected_for_a_shared_manifest(self):
        with self.assertRaises(catalog.DownloadError):
            catalog._digest("a"*64, "anything.iso", "sha256")

    def test_tails_uses_the_official_usb_img_not_the_dvd_iso(self):
        entry = next(e for e in catalog.CATALOG if e.id == "tails")
        target = {"url": "https://download.tails.net/tails/stable/tails-amd64-7.15.1/tails-amd64-7.15.1.img", "sha256": "a"*64}
        data = {"product-name": "Tails", "build_target": "amd64", "channel": "stable", "installations": [
            {"installation-paths": [{"type": "img", "target-files": [target]},
                                    {"type": "iso", "target-files": [{"url": "unused.iso"}]}]}]}
        with patch.object(catalog, "_metadata", return_value=json.dumps(data)):
            image = catalog.resolve_image(entry)
        self.assertEqual(image.image_url, target["url"])
        self.assertEqual(image.sha256, target["sha256"])
        self.assertEqual(image.checksum_url, entry.api_json_url)
        self.assertTrue(image.filename.endswith(".img"))

    def test_tails_rejects_wrong_host_hash_architecture_and_ambiguous_images(self):
        entry = next(e for e in catalog.CATALOG if e.id == "tails")
        target = {"url": "https://download.tails.net/tails/stable/tails-amd64-7.15.1/tails-amd64-7.15.1.img", "sha256": "a"*64}
        data = {"product-name": "Tails", "build_target": "amd64", "channel": "stable", "installations": [
            {"installation-paths": [{"type": "img", "target-files": [target]}]}]}
        for field, val in (("url", target["url"].replace("download.tails.net", "example.org")),
                           ("url", target["url"].replace("https:", "http:")),
                           ("url", target["url"].replace("7.15.1.img", "7.15.2.img")),
                           ("sha256", "a"*32)):
            invalid = json.loads(json.dumps(data))
            invalid["installations"][0]["installation-paths"][0]["target-files"][0][field] = val
            with self.subTest(field=field, val=val), patch.object(catalog, "_metadata", return_value=json.dumps(invalid)):
                with self.assertRaises(catalog.DownloadError): catalog.resolve_image(entry)
        for field, val in (("build_target", "arm64"), ("channel", "testing"), ("product-name", "Other"),
                           ("installations", data["installations"] * 2)):
            with self.subTest(field=field), patch.object(catalog, "_metadata", return_value=json.dumps({**data, field: val})):
                with self.assertRaises(catalog.DownloadError): catalog.resolve_image(entry)


class TorrentDiscoveryTests(unittest.TestCase):
    def test_artix_checks_the_publishers_advertised_torrent_directory(self):
        image = catalog.fixed("artix", "Artix Linux", "Base", "x64",
                              "https://download.artixlinux.org/iso/", "artix-base-openrc-20260813-x86_64.iso")
        with patch.object(catalog, "fetch", return_value=b"") as fetch:
            catalog.discover_torrent(image)
        self.assertEqual(fetch.call_args.args[0], "https://torrents.artixlinux.org/torrents.php")

    def test_sourceforge_torrent_discovery_uses_the_project_file_index(self):
        image = catalog.fixed("example", "Example", "Live", "x64",
                              "https://downloads.sourceforge.net/project/example/1.0/", "example.iso")
        with patch.object(catalog, "fetch", return_value=b"") as fetch:
            catalog.discover_torrent(image)
        self.assertEqual(fetch.call_args.args[0], "https://sourceforge.net/projects/example/files/1.0/")

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

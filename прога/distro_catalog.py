"""Official GNU/Linux release metadata; resolve filenames before downloading.

Catalog entries describe an official release channel, not a promise that its
server is available. Resolution requires the publisher's SHA-256/SHA-512.
ARM64 entries are generic installation images, never board-specific archives.
"""
from dataclasses import dataclass, replace
from html.parser import HTMLParser
import json
import re
import threading
from urllib.parse import urljoin, urlsplit, unquote

from image_download import DownloadError, fetch, checksum_from_manifest, torrent_target


@dataclass(frozen=True)
class DistroImage:
    id: str
    distro: str
    edition: str
    architecture: str
    image_url: str
    torrent_url: str | None = None
    checksum_url: str = ""
    checksum_algorithm: str = "sha256"
    filename: str = ""
    sha256: str | None = None
    pattern: str = ""
    checksum_pattern: str = ""
    checksum_suffix: str = ""
    torrent_template: str = ""
    api_json_url: str = ""
    release_pointer_url: str = ""
    publisher_page_url: str = ""
    sha512: str | None = None
    api_format: str = "popos"
    checksum_filename_pattern: str = ""
    checksum_path_prefix: str = ""
    checksum_section: str = ""


def channel(id, name, edition, arch, base, pattern, manifest="SHA256SUMS", *, torrent="", algorithm="sha256", checksum_pattern="", suffix=""):
    return DistroImage(id, name, edition, arch, base, checksum_url=urljoin(base, manifest) if manifest else "",
                       checksum_algorithm=algorithm, pattern=pattern, checksum_pattern=checksum_pattern,
                       checksum_suffix=suffix, torrent_template=torrent)


def fixed(id, name, edition, arch, base, filename, checksum=None, *, algorithm="sha256", torrent="", checksum_path_prefix="", checksum_section=""):
    return DistroImage(id, name, edition, arch, urljoin(base, filename),
        checksum_url=urljoin(base, checksum or filename + ".sha256"), filename=filename,
        checksum_algorithm=algorithm, torrent_url=urljoin(base, torrent) if torrent else None,
        checksum_path_prefix=checksum_path_prefix, checksum_section=checksum_section)


_entries = []
for slug, title, prefix in (
    ("kubuntu", "Kubuntu", "kubuntu"), ("xubuntu", "Xubuntu", "xubuntu"),
    ("lubuntu", "Lubuntu", "lubuntu"), ("ubuntu-mate", "Ubuntu MATE", "ubuntu-mate"),
    ("ubuntu-budgie", "Ubuntu Budgie", "ubuntu-budgie"),
    ("ubuntustudio", "Ubuntu Studio", "ubuntustudio"),
    ("ubuntukylin", "Ubuntu Kylin", "ubuntukylin"),
    ("ubuntucinnamon", "Ubuntu Cinnamon", "ubuntucinnamon"),
    ("edubuntu", "Edubuntu", "edubuntu"), ("ubuntu-unity", "Ubuntu Unity", "ubuntu-unity"),
):
    _entries.append(channel(slug, title, "24.04 LTS Desktop", "x64",
        f"https://cdimage.ubuntu.com/{slug}/releases/24.04/release/",
        rf"{prefix}-24\.04(?:\.\d+)?-(?:desktop|dvd)-amd64\.iso", torrent="{image_url}.torrent"))
_entries += [
    channel("ubuntu", "Ubuntu", "24.04 LTS Desktop", "x64", "https://releases.ubuntu.com/24.04/", r"ubuntu-24\.04(?:\.\d+)?-desktop-amd64\.iso", torrent="{image_url}.torrent"),
    channel("ubuntu-arm64", "Ubuntu", "24.04 LTS Server", "ARM64", "https://cdimage.ubuntu.com/releases/24.04/release/", r"ubuntu-24\.04(?:\.\d+)?-live-server-arm64\.iso", torrent="{image_url}.torrent"),
    channel("arch", "Arch Linux", "Installation ISO", "x64", "https://geo.mirror.pkgbuild.com/iso/latest/", r"archlinux-\d{4}\.\d{2}\.\d{2}-x86_64\.iso", "sha256sums.txt", torrent="https://archlinux.org/releng/releases/{arch_date}/torrent/"),
    channel("void", "Void Linux", "glibc Base", "x64", "https://repo-fastly.voidlinux.org/live/current/", r"void-live-x86_64-\d+-base\.iso", "sha256sum.txt"),
    channel("devuan", "Devuan", "Daedalus Desktop Live", "x64", "https://files.devuan.org/devuan_daedalus/desktop-live/", r"devuan_daedalus_.*_amd64_desktop-live\.iso", "SHA256SUMS.txt"),
    channel("mint", "Linux Mint", "22.1 Cinnamon", "x64", "https://mirrors.kernel.org/linuxmint/stable/22.1/", r"linuxmint-22\.1-cinnamon-64bit\.iso", "sha256sum.txt", torrent="https://torrents.linuxmint.com/torrents/{filename}.torrent"),
    DistroImage("gentoo", "Gentoo", "Minimal Installation", "x64", "https://distfiles.gentoo.org/releases/amd64/autobuilds/", checksum_algorithm="sha512", release_pointer_url="https://distfiles.gentoo.org/releases/amd64/autobuilds/latest-iso.txt"),
    channel("bunsenlabs", "BunsenLabs", "Stable Live", "x64", "https://ddl.bunsenlabs.org/ddl/", r"[a-z]+-[A-Za-z0-9_.+-]*amd64[A-Za-z0-9_.+-]*\.iso", "release.sha256.txt"),
    channel("porteus", "Porteus", "5.01 Xfce", "x64", "https://mirrors.dotsrc.org/porteus/x86_64/Porteus-v5.01/", r"Porteus-(?i:xfce)-v5\.01-x86_64\.iso", "sha256sums.txt"),
    DistroImage("bodhi", "Bodhi Linux", "7.0.0 Standard", "x64", "https://downloads.sourceforge.net/project/bodhilinux/7.0.0/bodhi-7.0.0-64.iso", checksum_url="https://downloads.sourceforge.net/project/bodhilinux/7.0.0/bodhi-7.0.0-64.iso.sha256", filename="bodhi-7.0.0-64.iso"),
    DistroImage("mageia", "Mageia", "9 Live Xfce", "x64", "https://www.mageia.org/", checksum_algorithm="sha512", filename="Mageia-9-Live-Xfce-x86_64.iso", publisher_page_url="https://www.mageia.org/en/downloads/get/?q=Mageia-9-Live-Xfce-x86_64.iso"),
    DistroImage("popos", "Pop!_OS", "22.04 Intel/AMD", "x64", "https://iso.pop-os.org/", api_json_url="https://api.pop-os.org/builds/22.04/intel"),
]
for arch, debarch, rpmarch, nixarch in (("x64", "amd64", "x86_64", "x86_64"), ("ARM64", "arm64", "aarch64", "aarch64")):
    key = "x64" if arch == "x64" else "arm64"
    _entries += [
        channel(f"debian-{key}", "Debian", "Stable netinst", arch, f"https://cdimage.debian.org/debian-cd/current/{debarch}/iso-cd/", rf"debian-[\d.]+-{debarch}-netinst\.iso", torrent=f"https://cdimage.debian.org/debian-cd/current/{debarch}/bt-cd/{{filename}}.torrent"),
        channel(f"fedora-{key}", "Fedora", "43 Workstation", arch, f"https://download.fedoraproject.org/pub/fedora/linux/releases/43/Workstation/{rpmarch}/iso/", rf"Fedora-Workstation-Live(?:-43)?-{rpmarch}-43-[\d.]+\.iso|Fedora-Workstation-Live-43-[\d.]+\.{rpmarch}\.iso|Fedora-Workstation-Live-{rpmarch}-43-[\d.]+\.iso", "", checksum_pattern=r".*CHECKSUM", torrent=f"https://torrent.fedoraproject.org/torrents/Fedora-Workstation-Live-{rpmarch}-43.torrent"),
        channel(f"rocky-{key}", "Rocky Linux", "9 Minimal", arch, f"https://dl.rockylinux.org/pub/rocky/9/isos/{rpmarch}/", rf"Rocky-9[\d.]*-{rpmarch}-minimal\.iso", "CHECKSUM"),
        channel(f"alma-{key}", "AlmaLinux", "9 Minimal", arch, f"https://repo.almalinux.org/almalinux/9/isos/{rpmarch}/", rf"AlmaLinux-9[\d.]*-{rpmarch}-minimal\.iso", "CHECKSUM"),
        DistroImage(f"centos-{key}", "CentOS Stream", "9 DVD", arch, f"https://mirror.stream.centos.org/9-stream/BaseOS/{rpmarch}/iso/CentOS-Stream-9-latest-{rpmarch}-dvd1.iso", checksum_url=f"https://mirror.stream.centos.org/9-stream/BaseOS/{rpmarch}/iso/CentOS-Stream-9-latest-{rpmarch}-dvd1.iso.SHA256SUM", filename=f"CentOS-Stream-9-latest-{rpmarch}-dvd1.iso"),
        channel(f"alpine-{key}", "Alpine Linux", "Standard (musl / OpenRC)", arch, f"https://dl-cdn.alpinelinux.org/alpine/latest-stable/releases/{rpmarch}/", rf"alpine-standard-[\d.]+-{rpmarch}\.iso", "", suffix=".sha256"),
        channel(f"kali-{key}", "Kali Linux", "Installer", arch, "https://cdimage.kali.org/current/", rf"kali-linux-[\d.]+-installer-{debarch}\.iso", torrent="{image_url}.torrent"),
        DistroImage(f"opensuse-{key}", "openSUSE", "Tumbleweed NET", arch,
            ("https://download.opensuse.org/tumbleweed/iso/" if arch == "x64" else "https://download.opensuse.org/ports/aarch64/tumbleweed/iso/") + f"openSUSE-Tumbleweed-NET-{rpmarch}-Current.iso",
            torrent_url=("https://download.opensuse.org/tumbleweed/iso/" if arch == "x64" else "https://download.opensuse.org/ports/aarch64/tumbleweed/iso/") + f"openSUSE-Tumbleweed-NET-{rpmarch}-Current.iso.torrent",
            checksum_url=("https://download.opensuse.org/tumbleweed/iso/" if arch == "x64" else "https://download.opensuse.org/ports/aarch64/tumbleweed/iso/") + f"openSUSE-Tumbleweed-NET-{rpmarch}-Current.iso.sha256", filename=f"openSUSE-Tumbleweed-NET-{rpmarch}-Current.iso"),
    ]
    name = f"latest-nixos-minimal-{nixarch}-linux.iso"
    _entries.append(DistroImage(f"nixos-{key}", "NixOS", "26.05 Minimal", arch,
        f"https://channels.nixos.org/nixos-26.05/{name}", checksum_url=f"https://channels.nixos.org/nixos-26.05/{name}.sha256", filename=name))

_entries += [
    fixed("endeavouros", "EndeavourOS", "Titan Nova", "x64", "https://mirror.alpix.eu/endeavouros/iso/",
          "EndeavourOS_Titan-Nova-2026.08.15.iso", "EndeavourOS_Titan-Nova-2026.08.15.iso.sha512sum",
          algorithm="sha512", torrent="EndeavourOS_Titan-Nova-2026.08.15.iso.torrent"),
    channel("artix", "Artix Linux", "Base OpenRC", "x64", "https://download.artixlinux.org/iso/",
            r"artix-base-openrc-\d+-x86_64\.iso", "sha256sums"),
    DistroImage("garuda", "Garuda Linux", "Dr460nized", "x64",
        "https://iso.builds.garudalinux.org/iso/latest/garuda/dr460nized/latest.iso",
        torrent_url="https://iso.builds.garudalinux.org/iso/latest/garuda/dr460nized/latest.iso.torrent",
        checksum_url="https://iso.builds.garudalinux.org/iso/latest/garuda/dr460nized/latest.iso.sha256",
        filename="latest.iso", checksum_filename_pattern=r"garuda-dr460nized-linux-garuda-\d+\.iso"),
    fixed("antix", "antiX", "23.2 Full (sysVinit)", "x64",
          "https://downloads.sourceforge.net/project/antix-linux/Final/antiX-23.2/",
          "antiX-23.2_x64-full.iso", "README.txt"),
    fixed("linuxlite", "Linux Lite", "7.2", "x64", "https://downloads.sourceforge.net/project/linux-lite/7.2/",
          "linux-lite-7.2-64bit.iso"),
    channel("deepin", "deepin", "25.0.1 Desktop", "x64", "https://cdimage.deepin.com/releases/25.0.1/amd64/",
            r"deepin-desktop-community-25\.0\.1-amd64\.iso"),
    fixed("sparky", "SparkyLinux", "8.4 Xfce", "x64", "https://downloads.sourceforge.net/project/sparkylinux/xfce/",
          "sparkylinux-8.4-x86_64-xfce.iso", "sparkylinux-8.4-x86_64-xfce.iso.allsums.txt",
          torrent="https://downloads.sourceforge.net/project/sparkylinux/torrents/sparkylinux-8.4-x86_64-xfce.iso.torrent"),
    fixed("bazzite", "Bazzite", "Stable Desktop", "x64", "https://download.bazzite.gg/",
          "bazzite-stable-amd64.iso", "bazzite-stable-amd64.iso-CHECKSUM"),
    DistroImage("tails", "Tails", "Stable USB image", "x64", "https://download.tails.net/",
        api_json_url="https://tails.net/install/v2/Tails/amd64/stable/latest.json", api_format="tails"),
    fixed("rescuezilla", "Rescuezilla", "2.6.2 Noble", "x64", "https://github.com/rescuezilla/rescuezilla/releases/download/2.6.2/",
          "rescuezilla-2.6.2-64bit.noble.iso", "SHA256SUM"),
    fixed("systemrescue", "SystemRescue", "13.02", "x64", "https://fastly-cdn.system-rescue.org/releases/13.02/",
          "systemrescue-13.02-amd64.iso", "https://www.system-rescue.org/releases/13.02/systemrescue-13.02-amd64.iso.sha256"),
    channel("proxmox", "Proxmox VE", "Virtualization server installer", "x64", "https://enterprise.proxmox.com/iso/",
            r"proxmox-ve_[\d.]+-\d+\.iso"),
    channel("slint", "Slint", "15.0 Accessible desktop", "x64", "https://slackware.uk/slint/x86_64/slint-15.0/iso/",
            r"slint64-15\.0-\d+\.iso", "", suffix=".sha256"),
    channel("zorin", "Zorin OS", "18 Core", "x64", "https://plug-mirror.rcac.purdue.edu/zorin-iso/18/",
            r"Zorin-OS-18[^/]*-Core-64-bit(?:-r\d+)?\.iso", "SHA256SUMS.txt"),
    fixed("pureos", "PureOS", "11 Crimson GNOME", "x64", "https://storage.puri.sm/pureos/images/crimson/2026.05/gnome/",
          "pureos-11-gnome-live-20260515_amd64.iso", "pureos-11-gnome-live-20260515_amd64.checksums_sha256.txt"),
    fixed("nobara", "Nobara Linux", "44 Official", "x64", "https://nobara-images.nobaraproject.org/",
          "Nobara-44-Official-2026-10-13.iso", "Nobara-44-Official-2026-10-13.iso.sha256sum"),
    fixed("solus", "Solus", "2026-04-18 Budgie", "x64", "https://downloads.getsol.us/isos/2026-04-18/",
          "Solus-Budgie-Release-2026-04-18.iso", "Solus-Budgie-Release-2026-04-18.iso.sha256sum"),
    fixed("mx", "MX Linux", "25.3 Xfce", "x64", "https://downloads.sourceforge.net/project/mx-linux/Final/Xfce/",
          "MX-25.3_Xfce_x64.iso"),
    fixed("peppermint", "Peppermint OS", "Debian Xfce", "x64", "https://downloads.sourceforge.net/project/peppermintos/isos/XFCE/",
          "PeppermintOS-Debian-64.iso", "PeppermintOS-Debian-64-sha512.checksum", algorithm="sha512"),
    fixed("archcraft", "Archcraft", "2026.08", "x64", "https://downloads.sourceforge.net/project/archcraft/v26.08/",
          "archcraft-2026.08.01-x86_64.iso", "archcraft-2026.08.01-x86_64.iso.sha256sum"),
    channel("calculate", "Calculate Linux", "20261022 Desktop KDE", "x64", "https://mirror.calculate-linux.org/release/20261022/",
            r"cld-20261022-x86_64\.iso"),
    fixed("ultramarine", "Ultramarine Linux", "44 Plasma", "x64", "https://ultramarine-linux.org/fyra-images/isos/ultramarine/44/",
          "ultramarine-plasma-44-live-anaconda-x86_64.iso", "ultramarine-plasma-44-live-anaconda-x86_64.iso.sha256sum",
          checksum_path_prefix="artifacts/plasma-plasma-live-anaconda-x86_64-iso/"),
    fixed("oracle", "Oracle Linux", "9.8 Boot installer", "x64", "https://yum.oracle.com/ISOS/OracleLinux/OL9/u8/x86_64/",
          "OracleLinux-R9-U8-x86_64-boot.iso", "https://linux.oracle.com/security/gpg/checksum/OracleLinux-R9-U8-Server-x86_64.checksum"),
    fixed("oracle-arm64", "Oracle Linux", "9.8 Boot UEK installer", "ARM64", "https://yum.oracle.com/ISOS/OracleLinux/OL9/u8/aarch64/",
          "OracleLinux-R9-U8-aarch64-boot-uek.iso", "https://linux.oracle.com/security/gpg/checksum/OracleLinux-R9-U8-Server-aarch64.checksum"),
    channel("proxmox-arm64", "Proxmox VE", "Virtualization server installer", "ARM64", "https://enterprise.proxmox.com/iso/",
            r"proxmox-ve_[\d.]+-\d+-arm64\.iso"),
    fixed("puppy", "Puppy Linux", "BookwormPup64 10.0.12", "x64",
          "https://distro.ibiblio.org/puppylinux/puppy-bookwormpup/BookwormPup64/10.0.12/",
          "BookwormPup64_10.0.12.iso", "BookwormPup64_10.0.12.iso-checksum.txt"),
    fixed("gparted", "GParted Live", "1.8.1-6", "x64",
          "https://downloads.sourceforge.net/project/gparted/gparted-live-stable/1.8.1-6/",
          "gparted-live-1.8.1-6-amd64.iso", "https://gparted.org/gparted-live/stable/CHECKSUMS.TXT",
          checksum_section="### SHA256SUMS:"),
]
for arch, name in (("x64", "x86_64"), ("ARM64", "aarch64")):
    key = "x64" if arch == "x64" else "arm64"
    _entries += [
        channel(f"chimera-{key}", "Chimera Linux", "Live GNOME (musl / dinit)", arch,
                "https://repo.chimera-linux.org/live/latest/", rf"chimera-linux-{name}-LIVE-\d+-gnome\.iso", "sha256sums.txt"),
        fixed(f"openeuler-{key}", "openEuler", "24.03 LTS SP3 Installer", arch,
              f"https://repo.openeuler.org/openEuler-24.03-LTS-SP3/ISO/{name}/",
              f"openEuler-24.03-LTS-SP3-{name}-dvd.iso", f"openEuler-24.03-LTS-SP3-{name}-dvd.iso.sha256sum"),
        fixed(f"vanilla-{key}", "Vanilla OS", "3 Stable", arch,
              "https://github.com/Vanilla-OS/live-iso/releases/download/3.0/",
              f"Vanilla-OS-3-stable-{'amd64' if arch == 'x64' else 'arm64'}.20260828.iso",
              f"Vanilla-OS-3-stable-{'amd64' if arch == 'x64' else 'arm64'}.20260828.sha256.txt"),
        fixed(f"rhino-{key}", "Rhino Linux", "2026.1 Desktop", arch,
              "https://downloads.sourceforge.net/project/rhino-linux-builder/2026.1/",
              f"Rhino-Linux-2026.1-{'amd64' if arch == 'x64' else 'arm64'}.iso"),
        fixed(f"parrot-{key}", "Parrot OS", "7.4 Home", arch, "https://download.parrot.sh/parrot/iso/7.4/",
              f"Parrot-home-7.4_{'amd64' if arch == 'x64' else 'arm64'}.iso", "signed-hashes.txt",
              torrent=f"Parrot-home-7.4_{'amd64' if arch == 'x64' else 'arm64'}.iso.torrent"),
    ]

CATALOG = tuple(_entries)


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href" and value)


_trace = threading.local()


def metadata_diagnostics():
    return {"url": getattr(_trace, "url", ""), "text": getattr(_trace, "text", "")[:1800]}


def _metadata(url):
    _trace.url, _trace.text = url, ""
    text = fetch(url, limit=4 * 1024 * 1024).decode("utf-8", errors="replace")
    # Keep a bounded diagnostic useful when a publisher changes a release name.
    names = _manifest_names(text)
    if names:
        _trace.text = "Image names: " + ", ".join(sorted(names)[:30])
    else:
        parser = _Links()
        parser.feed(text)
        _trace.text = "Links: " + ", ".join(parser.links[:25]) if parser.links else text[:1800]
    return text


def _links(base):
    parser = _Links()
    parser.feed(_metadata(base))
    links = []
    for link in parser.links:
        url = urlsplit(urljoin(base, link))
        name = url.path.rsplit("/", 1)[-1]
        # Mirror redirects may change both hostname and directory prefix.
        # Only return a simple basename: callers filter it by the distro's
        # filename pattern and fetch it through the original HTTPS publisher
        # base. The advertised href itself is never followed here.
        if url.scheme in ("http", "https") and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", name):
            links.append(name)
    return links


def _manifest_names(text):
    """Only simple image basenames; reject traversal before constructing URLs."""
    return set(re.findall(r"(?<![\w/])([A-Za-z0-9][A-Za-z0-9_.+-]*\.(?:iso|img))(?=[\s)\r\n]|$)", text))


def _latest(names):
    return sorted(names, key=lambda n: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", n)])[-1]


def _digest(text, filename, algorithm, *, per_image=False, section=""):
    # A filename-free hash is acceptable only at an explicit per-image URL.
    if section:
        lines = text.splitlines()
        starts = [i for i, line in enumerate(lines) if line.strip() == section]
        if len(starts) != 1:
            raise DownloadError("Could not find an unambiguous publisher checksum section.")
        selected = []
        for line in lines[starts[0] + 1:]:
            if line.startswith("### "):
                break
            selected.append(line)
        text = "\n".join(selected)
    length = {"sha256": 64, "sha512": 128}[algorithm]
    if re.fullmatch(rf"\s*[a-fA-F0-9]{{{length}}}\s*", text):
        if not per_image:
            raise DownloadError("A checksum without a filename is allowed only in an image-specific checksum file.")
        return text.strip().lower()
    return checksum_from_manifest(text, filename, algorithm)


def _gentoo_sha512(text, filename):
    """DIGESTS also contains BLAKE2B with the same hexadecimal length."""
    active = False
    lines = []
    for line in text.splitlines():
        if line.startswith("#"):
            active = line.strip() == "# SHA512 HASH"
        elif active:
            lines.append(line)
    return checksum_from_manifest("\n".join(lines), filename, "sha512")


def _resolve_image(entry):
    """Resolve official metadata to one image whose exact checksum is known."""
    _trace.url, _trace.text = "", ""
    if entry.release_pointer_url:
        text = _metadata(entry.release_pointer_url)
        names = re.findall(r"(?m)^(\d{8}T\d{6}Z/install-amd64-minimal-\d{8}T\d{6}Z\.iso)(?:\s|$)", text)
        if len(names) != 1:
            raise DownloadError("Gentoo: could not find a unique current minimal ISO.")
        url = urljoin(entry.image_url, names[0])
        filename = names[0].rsplit("/", 1)[-1]
        checksum_url = url + ".DIGESTS"
        digest = _gentoo_sha512(_metadata(checksum_url), filename)
        return replace(entry, image_url=url, filename=filename, checksum_url=checksum_url,
                       release_pointer_url="", sha512=digest)
    if entry.publisher_page_url:
        parser = _Links()
        parser.feed(_metadata(entry.publisher_page_url))
        urls = [urljoin(entry.publisher_page_url, link) for link in parser.links]
        urls = [url for url in urls if urlsplit(url).scheme in ("http", "https") and urlsplit(url).path.rsplit("/", 1)[-1] == entry.filename]
        if not urls:
            raise DownloadError("Mageia: the official website did not return a mirror for the selected ISO.")
        # The publisher may advertise HTTP mirror URLs; only their HTTPS
        # equivalent is accepted by our downloader, never a TLS downgrade.
        url = urls[0].replace("http://", "https://", 1)
        checksum_url = url + ".sha512"
        _digest(_metadata(checksum_url), entry.filename, entry.checksum_algorithm, per_image=True)
        return replace(entry, image_url=url, checksum_url=checksum_url, publisher_page_url="")
    if entry.api_json_url:
        data = json.loads(_metadata(entry.api_json_url))
        if entry.api_format == "tails":
            candidates = [target for installation in data.get("installations", [])
                          for path in installation.get("installation-paths", []) if path.get("type") == "img"
                          for target in path.get("target-files", [])]
            if (data.get("product-name") != "Tails" or data.get("build_target") != "amd64"
                    or data.get("channel") != "stable" or len(candidates) != 1):
                raise DownloadError("Tails: could not find a unique official amd64 USB image.")
            url, digest = candidates[0].get("url", ""), candidates[0].get("sha256", "")
            parsed = urlsplit(url)
            filename = parsed.path.rsplit("/", 1)[-1]
            if (parsed.scheme != "https" or parsed.netloc != "download.tails.net"
                    or parsed.query or parsed.fragment
                    or not re.fullmatch(r"/tails/stable/tails-amd64-([\d.]+)/tails-amd64-\1\.img", parsed.path)
                    or not re.fullmatch(r"[a-fA-F0-9]{64}", digest)):
                raise DownloadError("Tails: invalid URL or SHA-256 for the official USB image.")
            return replace(entry, image_url=url, filename=filename, sha256=digest.lower(),
                           checksum_url=entry.api_json_url, api_json_url="")
        if entry.api_format != "popos":
            raise DownloadError("Unknown distribution metadata format.")
        url, digest = data.get("url", ""), data.get("sha_sum", "")
        if urlsplit(url).scheme != "https" or urlsplit(url).hostname != "iso.pop-os.org" or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise DownloadError("Pop!_OS: the official API did not return a trusted ISO and SHA-256.")
        filename = urlsplit(url).path.rsplit("/", 1)[-1]
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+\.iso", filename):
            raise DownloadError("Invalid Pop!_OS image filename.")
        return replace(entry, image_url=url, filename=filename, sha256=digest.lower(), api_json_url="")
    if not entry.pattern:
        manifest = _metadata(entry.checksum_url)
        filename = entry.filename
        if entry.checksum_filename_pattern:
            candidates = _manifest_names(manifest)
            if len(candidates) != 1 or not re.fullmatch(entry.checksum_filename_pattern, next(iter(candidates))):
                raise DownloadError("The checksum belongs to another image or architecture.")
            filename = next(iter(candidates))
        elif filename.startswith("latest-nixos-"):
            candidates = _manifest_names(manifest)
            if len(candidates) == 1:
                candidate = next(iter(candidates))
                arch = "x86_64" if entry.architecture == "x64" else "aarch64"
                if re.fullmatch(rf"nixos-minimal-[A-Za-z0-9_.+-]+-{arch}-linux\.iso", candidate):
                    filename = candidate
        elif filename.startswith("openSUSE-Tumbleweed-") or filename.startswith("CentOS-Stream-"):
            candidates = _manifest_names(manifest)
            if len(candidates) == 1:
                candidate = next(iter(candidates))
                arch = "x86_64" if entry.architecture == "x64" else "aarch64"
                expected = rf"openSUSE-Tumbleweed-NET-{arch}-(?:Current|Snapshot\d+-Media)\.iso" if filename.startswith("openSUSE") else rf"CentOS-Stream-9-(?:latest|[\d.]+)-{arch}-dvd1\.iso"
                if re.fullmatch(expected, candidate):
                    filename = candidate
        digest = _digest(manifest, entry.checksum_path_prefix + filename, entry.checksum_algorithm,
                         section=entry.checksum_section,
                         per_image=entry.checksum_url in tuple(entry.image_url + suffix for suffix in
                             (".sha256", ".SHA256SUM", ".sha256sum", ".sha512", ".sha512sum"))
                         or entry.checksum_url == entry.image_url.removesuffix(".iso") + ".sha256.txt")
        return replace(entry, filename=filename, sha256=digest if entry.checksum_algorithm == "sha256" else None,
                       sha512=digest if entry.checksum_algorithm == "sha512" else None)
    base = entry.image_url
    checksum_url = entry.checksum_url
    if entry.checksum_suffix or entry.checksum_pattern:
        links = _links(base)
        if entry.checksum_pattern:
            candidates = [link for link in links if re.fullmatch(entry.checksum_pattern, link)]
            if not candidates:
                raise DownloadError(f"{entry.distro}: official checksum list not found.")
            checksum_url = urljoin(base, _latest(candidates))
        else:
            candidates = [link for link in links if re.fullmatch(entry.pattern, link)]
            if not candidates:
                raise DownloadError(f"{entry.distro}: installation image for this architecture not found.")
            filename = _latest(candidates)
            checksum_url = urljoin(base, filename + entry.checksum_suffix)
    manifest = _metadata(checksum_url)
    names = [name for name in _manifest_names(manifest) if re.fullmatch(entry.pattern, name)]
    if not names:
        # Some per-image .sha256 files contain only a digest, without filename.
        if not entry.checksum_suffix:
            raise DownloadError(f"{entry.distro}: no matching ISO in the official checksums.")
    else:
        filename = _latest(names)
    digest = _digest(manifest, filename, entry.checksum_algorithm, per_image=bool(entry.checksum_suffix))
    image_url = urljoin(base, filename)
    torrent = entry.torrent_template.format(image_url=image_url, filename=filename,
        arch_date=filename.removeprefix("archlinux-").removesuffix("-x86_64.iso")) if entry.torrent_template else None
    return replace(entry, image_url=image_url, filename=filename, checksum_url=checksum_url,
                   torrent_url=torrent, sha256=digest if entry.checksum_algorithm == "sha256" else None,
                   sha512=digest if entry.checksum_algorithm == "sha512" else None,
                   pattern="", checksum_pattern="", checksum_suffix="", torrent_template="")


def discover_torrent(image):
    """Prefer a publisher-advertised torrent containing this exact ISO.

    Some publishers offer a release-wide torrent rather than one per ISO.
    Validate its metadata first; the downloader selects only the matching file.
    Failure leaves the already verified official HTTPS source usable.
    """
    if image.torrent_url:
        return image
    indexes = {
        "artix": "https://torrents.artixlinux.org/torrents.php",
        "gentoo": "https://torrents.gentoo.org/",
        "mageia": "https://torrent.mageia.org/",
        "bodhi": "https://sourceforge.net/projects/bodhilinux/files/7.0.0/",
    }
    index = indexes.get(image.id, urljoin(image.image_url, "."))
    if index.startswith("https://downloads.sourceforge.net/project/"):
        project, _, path = urlsplit(index).path.removeprefix("/project/").partition("/")
        index = f"https://sourceforge.net/projects/{project}/files/{path}"
    try:
        parser = _Links()
        parser.feed(fetch(index, limit=4 * 1024 * 1024).decode("utf-8", errors="replace"))
        candidates = []
        for link in parser.links:
            url = urljoin(index, link)
            parsed = urlsplit(url)
            path = unquote(parsed.path).removesuffix("/download")
            name = path.rsplit("/", 1)[-1]
            if parsed.scheme != "https" or not name.endswith(".torrent"):
                continue
            prefix = name.removesuffix(".torrent")
            if prefix and image.filename.startswith(prefix):
                candidates.append((len(prefix), url))
        for _, url in sorted(set(candidates), reverse=True)[:3]:
            try:
                torrent_target(fetch(url, limit=8 * 1024 * 1024), image.filename)
                return replace(image, torrent_url=url)
            except Exception:
                continue
    except Exception:
        pass
    return image


def resolve_image(entry):
    return discover_torrent(_resolve_image(entry))

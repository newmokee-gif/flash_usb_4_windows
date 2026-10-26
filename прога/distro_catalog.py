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
from urllib.parse import urljoin, urlsplit

from image_download import DownloadError, fetch, checksum_from_manifest


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


def channel(id, name, edition, arch, base, pattern, manifest="SHA256SUMS", *, torrent="", algorithm="sha256", checksum_pattern="", suffix=""):
    return DistroImage(id, name, edition, arch, base, checksum_url=urljoin(base, manifest) if manifest else "",
                       checksum_algorithm=algorithm, pattern=pattern, checksum_pattern=checksum_pattern,
                       checksum_suffix=suffix, torrent_template=torrent)


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
    _entries.append(DistroImage(f"nixos-{key}", "NixOS", "25.05 Minimal", arch,
        f"https://channels.nixos.org/nixos-25.05/{name}", checksum_url=f"https://channels.nixos.org/nixos-25.05/{name}.sha256", filename=name))

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
    base_url = urlsplit(base)
    links = []
    for link in parser.links:
        url = urlsplit(urljoin(base, link))
        if url.scheme == "https" and url.netloc == base_url.netloc and url.path.rsplit("/", 1)[0] == base_url.path.rstrip("/"):
            links.append(url.path.rsplit("/", 1)[-1])
    return links


def _manifest_names(text):
    """Only simple image basenames; reject traversal before constructing URLs."""
    return set(re.findall(r"(?<![\w/])([A-Za-z0-9][A-Za-z0-9_.+-]*\.(?:iso|img))(?=[\s)\r\n]|$)", text))


def _latest(names):
    return sorted(names, key=lambda n: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", n)])[-1]


def _digest(text, filename, algorithm, *, per_image=False):
    # A filename-free hash is acceptable only at an explicit per-image URL.
    length = {"sha256": 64, "sha512": 128}[algorithm]
    if per_image and re.fullmatch(rf"\s*[a-fA-F0-9]{{{length}}}\s*", text):
        return text.strip().lower()
    return checksum_from_manifest(text, filename, algorithm)


def resolve_image(entry):
    """Resolve official metadata to one image whose exact checksum is known."""
    _trace.url, _trace.text = "", ""
    if entry.release_pointer_url:
        text = _metadata(entry.release_pointer_url)
        names = re.findall(r"(?m)^(\d{8}T\d{6}Z/install-amd64-minimal-\d{8}T\d{6}Z\.iso)(?:\s|$)", text)
        if len(names) != 1:
            raise DownloadError("Gentoo: не найден единственный актуальный минимальный ISO.")
        url = urljoin(entry.image_url, names[0])
        filename = names[0].rsplit("/", 1)[-1]
        checksum_url = url + ".DIGESTS"
        checksum_from_manifest(_metadata(checksum_url), filename, entry.checksum_algorithm)
        return replace(entry, image_url=url, filename=filename, checksum_url=checksum_url, release_pointer_url="")
    if entry.publisher_page_url:
        parser = _Links()
        parser.feed(_metadata(entry.publisher_page_url))
        urls = [urljoin(entry.publisher_page_url, link) for link in parser.links]
        urls = [url for url in urls if urlsplit(url).scheme in ("http", "https") and urlsplit(url).path.rsplit("/", 1)[-1] == entry.filename]
        if not urls:
            raise DownloadError("Mageia: официальный сайт не вернул зеркало выбранного ISO.")
        # The publisher may advertise HTTP mirror URLs; only their HTTPS
        # equivalent is accepted by our downloader, never a TLS downgrade.
        url = urls[0].replace("http://", "https://", 1)
        checksum_url = url + ".sha512"
        _digest(_metadata(checksum_url), entry.filename, entry.checksum_algorithm, per_image=True)
        return replace(entry, image_url=url, checksum_url=checksum_url, publisher_page_url="")
    if entry.api_json_url:
        data = json.loads(_metadata(entry.api_json_url))
        url, digest = data.get("url", ""), data.get("sha_sum", "")
        if urlsplit(url).scheme != "https" or urlsplit(url).hostname != "iso.pop-os.org" or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise DownloadError("Pop!_OS: официальный API не вернул доверенный ISO и SHA-256.")
        filename = urlsplit(url).path.rsplit("/", 1)[-1]
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+\.iso", filename):
            raise DownloadError("Некорректное имя образа Pop!_OS.")
        return replace(entry, image_url=url, filename=filename, sha256=digest.lower(), api_json_url="")
    if not entry.pattern:
        manifest = _metadata(entry.checksum_url)
        filename = entry.filename
        if filename.startswith("latest-nixos-"):
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
        digest = _digest(manifest, filename, entry.checksum_algorithm,
                         per_image=entry.checksum_url in (entry.image_url + ".sha256", entry.image_url + ".SHA256SUM"))
        return replace(entry, filename=filename, sha256=digest if entry.checksum_algorithm == "sha256" else None)
    base = entry.image_url
    checksum_url = entry.checksum_url
    if entry.checksum_suffix or entry.checksum_pattern:
        links = _links(base)
        if entry.checksum_pattern:
            candidates = [link for link in links if re.fullmatch(entry.checksum_pattern, link)]
            if not candidates:
                raise DownloadError(f"{entry.distro}: официальный список контрольных сумм не найден.")
            checksum_url = urljoin(base, _latest(candidates))
        else:
            candidates = [link for link in links if re.fullmatch(entry.pattern, link)]
            if not candidates:
                raise DownloadError(f"{entry.distro}: установочный образ этой архитектуры не найден.")
            filename = _latest(candidates)
            checksum_url = urljoin(base, filename + entry.checksum_suffix)
    manifest = _metadata(checksum_url)
    names = [name for name in _manifest_names(manifest) if re.fullmatch(entry.pattern, name)]
    if not names:
        # Some per-image .sha256 files contain only a digest, without filename.
        if not entry.checksum_suffix:
            raise DownloadError(f"{entry.distro}: подходящий ISO отсутствует в официальных контрольных суммах.")
    else:
        filename = _latest(names)
    digest = _digest(manifest, filename, entry.checksum_algorithm, per_image=bool(entry.checksum_suffix))
    image_url = urljoin(base, filename)
    torrent = entry.torrent_template.format(image_url=image_url, filename=filename,
        arch_date=filename.removeprefix("archlinux-").removesuffix("-x86_64.iso")) if entry.torrent_template else None
    return replace(entry, image_url=image_url, filename=filename, checksum_url=checksum_url,
                   torrent_url=torrent, sha256=digest if entry.checksum_algorithm == "sha256" else None,
                   pattern="", checksum_pattern="", checksum_suffix="", torrent_template="")

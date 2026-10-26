"""Read-only metadata discovery for the catalogue expansion; never fetch ISOs."""
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
import urllib.request

URLS = {
    "artix": "https://iso.artixlinux.org/iso/",
    "endeavour": "https://mirror.alpix.eu/endeavouros/iso/",
    "garuda": "https://iso.builds.garudalinux.org/iso/latest/garuda/dr460nized/latest.iso.sha256",
    "manjaro": "https://gitlab.manjaro.org/web/iso-info/-/raw/master/file-info.json",
    "cachyos": "https://iso.cachyos.org/ISO/",
    "mx": "https://downloads.sourceforge.net/project/mx-linux/Final/Xfce/",
    "antix": "https://downloads.sourceforge.net/project/antix-linux/Final/antiX-23.2/README.txt",
    "linuxlite": "https://downloads.sourceforge.net/project/linux-lite/7.2/linux-lite-7.2-64bit.iso.sha256",
    "solus": "https://downloads.getsol.us/isos/",
    "neon": "https://files.kde.org/neon/images/user/current/neon-user-current.sha256sum",
    "deepin": "https://cdimage.deepin.com/releases/25.0.1/amd64/",
    "peppermint": "https://downloads.sourceforge.net/project/peppermintos/isos/XFCE/PeppermintOS-Debian-64.iso-sha512.checksum",
    "sparky": "https://sparkylinux.org/download/stable/",
    "bazzite": "https://download.bazzite.gg/bazzite-stable-amd64.iso-CHECKSUM",
    "tails": "https://tails.net/install/v2/Tails/amd64/stable/latest.json",
    "clonezilla": "https://clonezilla.org/downloads/stable/",
    "gparted": "https://gparted.org/gparted-live/stable/",
    "rescuezilla": "https://api.github.com/repos/rescuezilla/rescuezilla/releases/latest",
    "systemrescue": "https://www.system-rescue.org/Download/",
    "oracle": "https://yum.oracle.com/ISOS/OracleLinux/OL9/u6/x86_64/",
    "proxmox": "https://enterprise.proxmox.com/iso/SHA256SUMS",
    "parrot": "https://download.parrot.sh/parrot/iso/",
    "slint": "https://slackware.uk/slint/x86_64/slint-15.0/iso/",
    "zorin": "https://plug-mirror.rcac.purdue.edu/zorin-iso/",
    "vanilla": "https://api.github.com/repos/Vanilla-OS/live-iso/releases/latest",
    "openmandriva": "https://api.github.com/repos/OpenMandrivaAssociation/omlx-iso/releases/latest",
    "kaos": "https://kaosx.us/pages/download/",
    "q4os": "https://q4os.org/downloads1.html",
    "nutyx": "https://downloads.nutyx.org/",
    "calculate": "https://mirror.calculate-linux.org/release/",
    "pureos": "https://www.pureos.net/download/",
    "nobara": "https://nobaraproject.org/download-nobara/",
    "ultramarine": "https://images.ultramarine-linux.org/",
    "rhino": "https://rhinolinux.org/download.html",
    "puppy": "https://distro.ibiblio.org/puppylinux/puppy-bookwormpup/",
    "salix": "https://download.salixos.org/x86_64/15.0/iso/",
    "alt": "https://ftp.altlinux.org/pub/distributions/ALTLinux/p11/images/workstation/",
    "rosa": "https://mirror.rosa.ru/rosa/rosa2023.1/iso/",
    "openeuler": "https://repo.openeuler.org/openEuler-24.03-LTS-SP3/ISO/x86_64/",
    "anolis": "https://mirrors.openanolis.cn/anolis/8.10/isos/x86_64/",
    "openkylin": "https://mirrors.huaweicloud.com/openkylin-cdimage/",
    "chimera": "https://repo.chimera-linux.org/live/latest/sha256sums.txt",
    "archcraft": "https://sourceforge.net/projects/archcraft/files/",
    "spiral": "https://sourceforge.net/projects/spirallinux/files/",
    "slackel": "https://www.slackel.gr/isos/",
}

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(v for k, v in attrs if k == "href" and v)

def probe(item):
    name, url = item
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "WinUSB-Catalog-Check/1.0"})
        with urllib.request.urlopen(request, timeout=25) as response:
            content = response.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
            final = response.url
        if "api.github.com" in url:
            data = json.loads(content)
            result = {"tag": data.get("tag_name"), "assets": [(a["name"], a["browser_download_url"], a.get("digest")) for a in data.get("assets", [])]}
        elif "<html" in content.lower() or "<a " in content.lower():
            parser = Links(); parser.feed(content)
            result = list(dict.fromkeys(parser.links))[:140]
        else:
            result = content[:11000]
        report = {"id": name, "url": url, "final": final, "metadata": result}
    except Exception as exc:
        report = {"id": name, "url": url, "error": str(exc)}
    text = json.dumps(report, ensure_ascii=False)
    print("::notice::" + text.replace("%", "%25").replace("\n", "%0A").replace("\r", "%0D"), flush=True)
    return report

if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(probe, URLS.items()))

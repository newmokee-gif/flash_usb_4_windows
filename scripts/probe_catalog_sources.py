"""Read-only metadata discovery for the catalogue expansion; never fetch ISOs."""
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
import sys
import urllib.request

PREVIOUS_URLS = {
    "artix": "https://download.artixlinux.org/iso/sha256sums",
    "endeavour": "https://mirror.alpix.eu/endeavouros/iso/EndeavourOS_Titan-Nova-2026.08.15.iso.sha512sum",
    "garuda": "https://iso.builds.garudalinux.org/iso/latest/garuda/dr460nized/",
    "manjaro": "https://download.manjaro.org/xfce/",
    "cachyos": "https://cachyos.org/download/",
    "mx": "https://sourceforge.net/projects/mx-linux/files/Final/Xfce/",
    "solus": "https://downloads.getsol.us/isos/2026-04-18/",
    "neon": "https://files.kde.org/neon/images/user/current/",
    "peppermint": "https://sourceforge.net/projects/peppermintos/files/isos/XFCE/",
    "sparky": "https://downloads.sourceforge.net/project/sparkylinux/xfce/sparkylinux-8.4-x86_64-xfce.iso.allsums.txt",
    "clonezilla": "https://sourceforge.net/projects/clonezilla/files/clonezilla_live_stable/",
    "gparted": "https://sourceforge.net/projects/gparted/files/gparted-live-stable/",
    "oracle": "https://yum.oracle.com/oracle-linux-isos.html",
    "parrot": "https://download.parrot.sh/parrot/iso/7.4/",
    "zorin": "https://plug-mirror.rcac.purdue.edu/zorin-iso/18/SHA256SUMS.txt",
    "openmandriva": "https://sourceforge.net/projects/openmandriva/files/",
    "kaos": "https://kaosx.us/download/",
    "q4os": "https://mirror.velden.media/isos/q4os/",
    "nutyx": "https://downloads.nutyx.org/x86_64/",
    "calculate": "https://mirror.calculate-linux.org/release/20261022/",
    "pureos": "https://storage.puri.sm/pureos/images/crimson/2026.05/gnome/pureos-11-gnome-live-20260515_amd64.checksums_sha256.txt",
    "nobara": "https://nobara-images.nobaraproject.org/Nobara-44-Official-2026-10-13.iso.sha256sum",
    "ultramarine": "https://ultramarine-linux.org/download/",
    "rhino": "https://sourceforge.net/projects/rhino-linux-builder/files/2026.1/",
    "puppy": "https://distro.ibiblio.org/puppylinux/puppy-bookwormpup/",
    "salix": "https://download.salixos.org/x86_64/15.0/iso/",
    "archcraft": "https://sourceforge.net/projects/archcraft/files/v26.08/",
}
URLS = {
    "puppy": "https://distro.ibiblio.org/puppylinux/puppy-bookwormpup/BookwormPup64/10.0.12/BookwormPup64_10.0.12.iso-checksum.txt",
    "clonezilla": "https://sourceforge.net/projects/clonezilla/files/clonezilla_live_stable/3.3.3-37/",
    "mx-torrents": "https://mxlinux.org/torrent-files/",
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
            links = list(dict.fromkeys(parser.links))
            relevant = [v for v in links if any(k in v.lower() for k in (".iso", "sha", "sum", "torrent", "2026", "2025"))]
            if "sourceforge.net" in url:
                relevant = [v for v in links if "/files/" in v and "/stats/" not in v]
            result = (relevant or links)[:30]
        else:
            result = content[:2400]
        report = {"id": name, "url": url, "final": final, "metadata": result}
    except Exception as exc:
        report = {"id": name, "url": url, "error": str(exc)}
    text = json.dumps(report, ensure_ascii=False)
    print("::notice::" + text.replace("%", "%25").replace("\n", "%0A").replace("\r", "%0D"), flush=True)
    return report

if __name__ == "__main__":
    batch = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(probe, list(URLS.items())[batch * 8:(batch + 1) * 8]))

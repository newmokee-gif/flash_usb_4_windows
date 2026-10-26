"""Read-only live validation: metadata, bounded torrent, and image HEAD only."""
import concurrent.futures
import json
from pathlib import Path
import sys
import urllib.request
from urllib.parse import urljoin
from html.parser import HTMLParser

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
from distro_catalog import CATALOG, resolve_image, metadata_diagnostics
from image_download import fetch, torrent_target


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.values.extend(v for k, v in attrs if k == "href" and v)


def failure_directory(entry):
    directory = entry.image_url if entry.image_url.endswith("/") else urljoin(entry.image_url, ".")
    report = []
    for _ in range(2):
        try:
            data = fetch(directory, limit=4*1024*1024).decode("utf-8", errors="replace")
            parser = Links()
            parser.feed(data)
            links = [v for v in parser.values if any(key in v.lower() for key in ("sha", "sum", "iso", "live", "devuan", "current", "user"))]
            report.append({"directory": directory, "links": (links or parser.values)[:30]})
            break
        except Exception as error:
            report.append({"directory": directory, "error": str(error)})
            directory = urljoin(directory, "../")
    return report


def validate(entry):
    result = {"id": entry.id, "distro": entry.distro, "architecture": entry.architecture}
    try:
        image = resolve_image(entry)
        request = urllib.request.Request(image.image_url, method="HEAD", headers={"User-Agent": "WinUSB-Creator/0.3"})
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise RuntimeError(f"Image HEAD HTTP {response.status}")
        result.update(status="ok", image_url=image.image_url, checksum_url=image.checksum_url,
                      sha256=image.sha256, sha512=image.sha512, torrent_url=image.torrent_url)
        if image.torrent_url:
            try:
                data = fetch(image.torrent_url, limit=4*1024*1024)
                if not data.startswith(b"d"):
                    raise RuntimeError("Not bencoded torrent metadata")
                torrent_target(data, image.filename)
                result["torrent"] = "ok"
            except Exception as error:
                result["torrent"] = "HTTPS fallback: " + str(error)
        else:
            result["torrent"] = "No matching publisher torrent found; official HTTPS"
        message = (entry.id + ": " + result["image_url"] + " | " + result["torrent"]).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(message, flush=True)
    except Exception as error:
        result.update(status="error", error=str(error), metadata=metadata_diagnostics(), directory=failure_directory(entry))
        diagnostic = entry.id + ": " + str(error) + "\n" + json.dumps({"metadata": result["metadata"], "directory": result["directory"]}, ensure_ascii=False)
        message = diagnostic.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print("::error::" + message, flush=True)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main():
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(validate, CATALOG))
    Path("distro-catalog-validation.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    failed = [r for r in results if r["status"] != "ok"]
    print(f"Resolved {len(results)-len(failed)}/{len(results)} official images; {len({e.distro for e in CATALOG})} distributions.")
    print(f"::notice::Catalogue result: {len(results)-len(failed)}/{len(results)} images; "
          f"{len({e.distro for e in CATALOG})} distributions; "
          f"{sum(e.architecture == 'ARM64' for e in CATALOG)} ARM64 variants", flush=True)
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())

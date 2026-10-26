"""Read-only live validation: metadata, bounded torrent, and image HEAD only."""
import concurrent.futures
import json
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "прога"))
from distro_catalog import CATALOG, resolve_image
from image_download import fetch


def validate(entry):
    result = {"id": entry.id, "distro": entry.distro, "architecture": entry.architecture}
    try:
        image = resolve_image(entry)
        request = urllib.request.Request(image.image_url, method="HEAD", headers={"User-Agent": "WinUSB-Creator/0.3"})
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise RuntimeError(f"Image HEAD HTTP {response.status}")
        result.update(status="ok", image_url=image.image_url, checksum_url=image.checksum_url,
                      sha256=image.sha256)
        if image.torrent_url:
            try:
                data = fetch(image.torrent_url, limit=4*1024*1024)
                if not data.startswith(b"d"):
                    raise RuntimeError("Not bencoded torrent metadata")
                result["torrent"] = "ok"
            except Exception as error:
                result["torrent"] = "HTTPS fallback: " + str(error)
    except Exception as error:
        result.update(status="error", error=str(error))
        message = (entry.id + ": " + str(error)).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print("::error::" + message, flush=True)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main():
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(validate, CATALOG))
    Path("distro-catalog-validation.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    failed = [r for r in results if r["status"] != "ok"]
    print(f"Resolved {len(results)-len(failed)}/{len(results)} official images; {len({e.distro for e in CATALOG})} distributions.")
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())

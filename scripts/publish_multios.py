"""Publish verified native applications; retain the previous release as draft."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from consolidate_release import GitHub, require, sha256, verify_manifest, verify_new_release


TAG = "v0.3.0"
EXPECTED = {
    "WinUSB-Creator-Windows-x64.exe", "WinUSB-Creator-Windows-ARM64.exe",
    "WinUSB-Creator-macOS-Intel.dmg", "WinUSB-Creator-macOS-AppleSilicon.dmg",
    "flash-usb-v0.3.0-x86_64.AppImage", "flash-usb-v0.3.0-aarch64.AppImage",
    "flash-usb_0.3.0_all.deb", "flash-usb-0.3.0-1.noarch.rpm",
}


def main():
    root = Path.cwd()
    require(os.environ.get("GITHUB_REF_NAME") == TAG, "Unexpected release tag")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    require(commit == os.environ["GITHUB_SHA"], "Wrong checkout")
    gh = GitHub()
    ref = gh.api(f"git/ref/tags/{TAG}")
    require(ref["object"]["type"] == "commit" and ref["object"]["sha"] == commit, "Tag differs from build")
    releases = gh.list_releases()
    require(not any(r["tag_name"] == TAG for r in releases), "Destination already exists")
    public = [r for r in releases if not r["draft"]]
    require(len(public) == 1 and public[0]["tag_name"] == "v0.2.1", "Unexpected public releases")
    old = public[0]
    require(not old.get("immutable"), "Previous release is immutable")
    builds = root / "desktop-dist"
    actual = {p.name for p in builds.iterdir() if not p.name.startswith("SHA256SUMS-")}
    require(actual == EXPECTED, f"Incomplete release: {EXPECTED - actual}, unexpected: {actual - EXPECTED}")
    for manifest in builds.glob("SHA256SUMS-*.txt"):
        verify_manifest(manifest, builds)
    output = root / "release-v0.3.0"
    output.mkdir()
    for p in builds.iterdir():
        require(p.is_file() and not p.is_symlink(), "Unsafe artifact")
        shutil.copyfile(p, output / p.name)
    provenance = {"source_commit": commit, "tag": TAG,
                  "workflow_run": os.environ.get("GITHUB_RUN_ID"),
                  "artifacts": {p.name: {"sha256": sha256(p), "size": p.stat().st_size}
                                for p in sorted(output.iterdir())},
                  "previous_release": {"id": old["id"], "tag": old["tag_name"]}}
    (output / "RELEASE-PROVENANCE.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    (output / "SHA256SUMS.txt").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in sorted(output.iterdir())), encoding="utf-8")
    notes = (root / "docs/releases/v0.3.0.md").read_text(encoding="utf-8")
    new = gh.api("releases", "POST", {"tag_name": TAG, "target_commitish": commit,
        "name": "WinUSB Creator 0.3.0 — Windows и ГНУ/Линукс", "body": notes,
        "draft": True, "prerelease": False})
    gh.upload(TAG, sorted(output.iterdir()))
    verify_new_release(gh, new["id"], TAG, output, root / "verify-v0.3.0")
    gh.edit(new["id"], draft=False, prerelease=False, make_latest="true")
    require(gh.api("releases/latest")["id"] == new["id"], "Publication is not Latest")
    gh.edit(old["id"], draft=True)
    require(gh.release(old["id"])["draft"], "Previous release not hidden")
    print(f"Published {TAG}; all applications downloaded and checksum-verified.")


if __name__ == "__main__":
    main()

"""One-shot verified v0.2.1 publication; keep prior assets recoverable."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from consolidate_release import GitHub, require, sha256, verify_manifest, create_source_archive, verify_new_release


def main():
    root = Path.cwd()
    tag = "v0.2.1"
    require(os.environ.get("GITHUB_REF_NAME") == tag, "Unexpected publication tag")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    require(commit == os.environ["GITHUB_SHA"], "Checkout differs from build")
    gh = GitHub()
    releases = gh.list_releases()
    require(not any(r["tag_name"] == tag for r in releases), "Destination already exists")
    public = [r for r in releases if not r["draft"]]
    require(len(public) == 1 and public[0]["tag_name"] == "v0.2.0", "Unexpected public releases")
    old = public[0]
    require(not old.get("immutable"), "Previous release cannot be hidden")
    original = root / "previous-release"
    gh.download("v0.2.0", original)
    verify_manifest(original / "SHA256SUMS.txt", original)
    output = root / "updated-release"
    output.mkdir()
    retained = [p for p in original.iterdir() if p.suffix in (".deb", ".rpm", ".AppImage")
                or p.name in ("SHA256SUMS-packages.txt", "SHA256SUMS-AppImage.txt", "SHA256SUMS-AppImage-aarch64.txt")]
    require(len(retained) == 7, "Incomplete Linux artifacts")
    for p in retained:
        shutil.copyfile(p, output / p.name)
    builds = root / "desktop-dist"
    expected = {f"WinUSB-Creator-{platform}.zip" for platform in
                ("Windows-x64", "Windows-ARM64", "macOS-Intel", "macOS-AppleSilicon")}
    require({p.name for p in builds.glob("*.zip")} == expected, "Missing desktop builds")
    require(len(list(builds.iterdir())) == 8, "Unexpected desktop artifacts")
    for manifest in builds.glob("SHA256SUMS-*.txt"):
        verify_manifest(manifest, builds)
    for p in builds.iterdir():
        shutil.copyfile(p, output / p.name)
    create_source_archive(root, output, tag, commit)
    shutil.copyfile(original / "RELEASE-PROVENANCE.json", output / "PREVIOUS-PROVENANCE.json")
    provenance = {"source_commit": commit, "previous_release": old,
                  "desktop_builds": sorted(expected),
                  "retained_linux": {p.name: sha256(p) for p in retained}}
    (output / "RELEASE-PROVENANCE.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    (output / "SHA256SUMS.txt").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in sorted(output.iterdir())), encoding="utf-8")
    notes = (root / "docs/releases/v0.2.1.md").read_text(encoding="utf-8")
    new = gh.api("releases", "POST", {"tag_name": tag, "target_commitish": commit,
        "name": "WinUSB Creator 0.2.1 — Windows, Linux, macOS", "body": notes,
        "draft": True, "prerelease": False})
    gh.upload(tag, sorted(output.iterdir()))
    verify_new_release(gh, new["id"], tag, output, root / "verify-updated-release")
    gh.edit(new["id"], draft=False, prerelease=False, make_latest="true")
    require(gh.api("releases/latest")["id"] == new["id"], "New release is not public Latest")
    gh.edit(old["id"], draft=True)
    require(gh.release(old["id"])["draft"], "Old release is not hidden")
    print("Published and verified v0.2.1; previous release preserved as draft.")


if __name__ == "__main__":
    main()

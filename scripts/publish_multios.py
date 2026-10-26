"""Publish verified native applications; retain the previous release as draft."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from consolidate_release import GitHub, require, sha256, verify_manifest, verify_new_release


TAG = "v0.3.3"
EXPECTED = {
    "WinUSB-Creator-Windows-x64.exe", "WinUSB-Creator-Windows-ARM64.exe",
    "WinUSB-Creator-macOS-Intel.dmg", "WinUSB-Creator-macOS-AppleSilicon.dmg",
    "flash-usb-v0.3.3-x86_64.AppImage", "flash-usb-v0.3.3-aarch64.AppImage",
    "flash-usb_0.3.3_all.deb", "flash-usb-0.3.3-1.noarch.rpm",
}
DESKTOP_MANIFESTS = {
    "SHA256SUMS-Windows-x64.txt": "WinUSB-Creator-Windows-x64.exe",
    "SHA256SUMS-Windows-ARM64.txt": "WinUSB-Creator-Windows-ARM64.exe",
    "SHA256SUMS-macOS-Intel.txt": "WinUSB-Creator-macOS-Intel.dmg",
    "SHA256SUMS-macOS-AppleSilicon.txt": "WinUSB-Creator-macOS-AppleSilicon.dmg",
}


def validate_inputs(builds):
    """Require every native build and its exact per-platform checksum mapping."""
    require(builds.is_dir() and not builds.is_symlink(), "Unsafe artifact directory")
    expected = EXPECTED | set(DESKTOP_MANIFESTS)
    actual = {p.name for p in builds.iterdir()}
    require(actual == expected,
            f"Incomplete release: {expected - actual}, unexpected: {actual - expected}")
    for name in sorted(expected):
        path = builds / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size > 0,
                f"Unsafe or empty artifact: {name}")
    for manifest, artifact in DESKTOP_MANIFESTS.items():
        entries = verify_manifest(builds / manifest, builds)
        require(set(entries) == {artifact}, f"Incorrect checksum coverage: {manifest}")
    # Linux build workflows do not emit individual manifests; their bytes are
    # retained here and included in the aggregate release SHA256SUMS below.
    return {name: sha256(builds / name) for name in sorted(expected)}


def verify_staged(output, expected_hashes):
    require({p.name for p in output.iterdir()} == set(expected_hashes),
            "Staged artifact set changed")
    for name, digest in expected_hashes.items():
        path = output / name
        require(path.is_file() and not path.is_symlink() and sha256(path) == digest,
                f"Staged artifact differs from verified build: {name}")


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
    require(len(public) == 1 and public[0]["tag_name"] == "v0.3.2", "Unexpected public releases")
    old = public[0]
    require(not old.get("immutable"), "Previous release is immutable")
    builds = root / "desktop-dist"
    verified_hashes = validate_inputs(builds)
    output = root / "release-v0.3.3"
    output.mkdir()
    for p in builds.iterdir():
        require(p.is_file() and not p.is_symlink(), "Unsafe artifact")
        shutil.copyfile(p, output / p.name)
    verify_staged(output, verified_hashes)
    provenance = {"source_commit": commit, "tag": TAG,
                  "workflow_run": os.environ.get("GITHUB_RUN_ID"),
                  "build_workflow_run": os.environ.get("VERIFIED_BUILD_RUN", os.environ.get("GITHUB_RUN_ID")),
                  "artifacts": {p.name: {"sha256": sha256(p), "size": p.stat().st_size}
                                for p in sorted(output.iterdir())},
                  "previous_release": {"id": old["id"], "tag": old["tag_name"]}}
    (output / "RELEASE-PROVENANCE.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    (output / "SHA256SUMS.txt").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in sorted(output.iterdir())), encoding="utf-8")
    notes = (root / "docs/releases/v0.3.3.md").read_text(encoding="utf-8")
    # The tag already exists and was verified above. Do not request a target
    # commit update: GitHub may require workflow-write permission for that.
    new = gh.api("releases", "POST", {"tag_name": TAG,
        "name": "WinUSB Creator 0.3.3 — Windows and GNU/Linux", "body": notes,
        "draft": True, "prerelease": False})
    gh.upload(TAG, sorted(output.iterdir()))
    verify_new_release(gh, new["id"], TAG, output, root / "verify-v0.3.3")
    gh.edit(new["id"], tag_name=TAG, draft=False, prerelease=False, make_latest="true")
    require(gh.api("releases/latest")["id"] == new["id"], "Publication is not Latest")
    gh.edit(old["id"], tag_name=old["tag_name"], draft=True, prerelease=old["prerelease"])
    require(gh.release(old["id"])["draft"], "Previous release not hidden")
    print(f"Published {TAG}; all applications downloaded and checksum-verified.")


if __name__ == "__main__":
    main()

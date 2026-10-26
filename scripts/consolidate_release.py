#!/usr/bin/env python3
"""Consolidate the two known public releases without deleting releases or tags.

The default is read-only toward GitHub: download and validate a staging set.
Only --publish creates a new draft, verifies it, publishes it and hides the two
explicitly inventoried old releases. Re-running against an existing destination
fails closed; it never overwrites existing release assets.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import zipfile


REPOSITORY = "newmokee-gif/flash_usb_4_windows"
TARGET_TAG = "v0.2.0"
OLD_TAGS = ("release", "v0.2.0-linux.2")
LEGACY_SHA256 = "855d23aa2879d03db11156c6e89e54ee485f0ba6bc39a4d94fa821e47c01e4b7"
SELECTED = {
    "release": ("WinUSB_Creator.exe",),
    "v0.2.0-linux.2": (
        "WinUSB-Creator-Windows-ARM64.zip",
        "flash-usb_0.2.0.linux.2_all.deb",
        "flash-usb-0.2.0-0.2.linux.noarch.rpm",
        "flash-usb-v0.2.0-linux.2-x86_64.AppImage",
        "flash-usb-v0.2.0-linux.2-aarch64.AppImage",
        "WinUSB-Creator-macOS-AppleSilicon.zip",
        "WinUSB-Creator-macOS-Intel.zip",
    ),
}
COMPATIBILITY_MANIFESTS = (
    "SHA256SUMS-AppImage-aarch64.txt", "SHA256SUMS-AppImage.txt",
    "SHA256SUMS-macOS-AppleSilicon.txt", "SHA256SUMS-macOS-Intel.txt",
    "SHA256SUMS-packages.txt", "SHA256SUMS-Windows-ARM64.txt",
)
EXPECTED_ORIGINAL_ASSETS = {
    "release": set(SELECTED["release"]),
    "v0.2.0-linux.2": set(SELECTED["v0.2.0-linux.2"]) | set(COMPATIBILITY_MANIFESTS) | {
        "flash-usb-v0.2.0-linux.2.tar.gz", "flash-usb-v0.2.0-linux.2.tar.gz.sha256"},
}
REQUIRED_SOURCE_PATHS = {
    "README.md", "LICENSE", "flash_usb.py", "прога/V2.py",
    "прога/linux_usb.py", "прога/linux_gui.py", "прога/macos_usb.py", "прога/macos_gui.py",
    "прога/app_icon.ico", "прога/app_icon.jpg",
}


class ReleaseError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def safe_name(name):
    require(isinstance(name, str) and name not in ("", ".", "..")
            and not name.startswith("-") and Path(name).name == name
            and not any(c in name for c in "\\/\r\n\x00"),
            f"Unsafe asset filename: {name!r}")
    return name


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_entries(text):
    result = {}
    for line in text.splitlines():
        require(bool(line), "Empty line in checksum manifest")
        match = re.fullmatch(r"([a-fA-F0-9]{64}) [ *](.+)", line)
        require(match is not None, "Malformed SHA256 manifest line")
        digest, name = match.groups()
        safe_name(name)
        require(name not in result, f"Duplicate checksum for {name}")
        result[name] = digest.lower()
    require(bool(result), "Empty checksum manifest")
    return result


def verify_manifest(path, directory):
    entries = manifest_entries(path.read_text(encoding="utf-8"))
    for name, digest in entries.items():
        file = directory / name
        require(file.is_file() and not file.is_symlink(), f"Missing checksum target: {name}")
        require(sha256(file) == digest, f"Checksum mismatch: {name}")
    return entries


def asset_map(release):
    result = {}
    for asset in release["assets"]:
        name = safe_name(asset["name"])
        require(name not in result, f"Duplicate release asset: {name}")
        require(asset.get("state", "uploaded") == "uploaded", f"Incomplete release asset: {name}")
        result[name] = asset
    return result


def select_releases(releases, target_tag):
    require(target_tag == TARGET_TAG, "This migration is limited to v0.2.0")
    require(not any(r["tag_name"] == target_tag for r in releases),
            "Destination release already exists; refusing to overwrite or resume blindly")
    public = [r for r in releases if r["draft"] is False]
    require(len(public) == len(OLD_TAGS) and {r["tag_name"] for r in public} == set(OLD_TAGS),
            "Public release inventory changed; expected only release and v0.2.0-linux.2")
    for release in public:
        require(not release.get("immutable", False),
                f"Cannot hide immutable release: {release['tag_name']}")
        require(isinstance(release["id"], int), "Invalid release identifier")
        require(isinstance(release["prerelease"], bool), "Invalid prerelease state")
        missing = set(SELECTED[release["tag_name"]]) - set(asset_map(release))
        require(not missing, f"Missing required platform builds: {sorted(missing)}")
        require(set(asset_map(release)) == EXPECTED_ORIGINAL_ASSETS[release["tag_name"]],
                f"Original asset inventory changed: {release['tag_name']}")
    return sorted(public, key=lambda r: OLD_TAGS.index(r["tag_name"]))


def fingerprint(release):
    return {
        "id": release["id"], "tag_name": release["tag_name"],
        "draft": release["draft"], "prerelease": release["prerelease"],
        "immutable": release.get("immutable", False),
        "name": release.get("name"), "body": release.get("body"),
        "assets": sorted((a["id"], a["name"], a["size"], a.get("digest"))
                         for a in release["assets"]),
    }


class GitHub:
    def __init__(self, repository=REPOSITORY):
        require(repository == REPOSITORY, "Unexpected repository")
        self.repository = repository

    def api(self, suffix, method="GET", payload=None, paginate=False):
        command = ["gh", "api", f"repos/{self.repository}/{suffix}", "--method", method]
        if paginate:
            command.extend(["--paginate", "--slurp"])
        if payload is not None:
            command.extend(["--input", "-"])
        result = subprocess.run(command, input=None if payload is None else json.dumps(payload),
                                text=True, capture_output=True, check=True)
        return json.loads(result.stdout)

    def list_releases(self):
        return [release for page in self.api("releases?per_page=100", paginate=True) for release in page]

    def release(self, release_id):
        return self.api(f"releases/{int(release_id)}")

    def edit(self, release_id, **changes):
        return self.api(f"releases/{int(release_id)}", "PATCH", changes)

    def tag_sha(self, tag):
        require(tag in (*OLD_TAGS, TARGET_TAG), "Unexpected tag lookup")
        obj = self.api(f"git/ref/tags/{tag}")["object"]
        for _ in range(6):
            if obj["type"] == "commit":
                require(re.fullmatch(r"[a-f0-9]{40}", obj["sha"]) is not None, "Invalid tag commit")
                return obj["sha"]
            require(obj["type"] == "tag", "Tag does not resolve to a commit")
            obj = self.api(f"git/tags/{obj['sha']}")["object"]
        raise ReleaseError("Excessively nested annotated tag")

    def create_draft(self, tag, source_sha, notes):
        return self.api("releases", "POST", {
            "tag_name": tag, "target_commitish": source_sha,
            "name": "WinUSB Creator 0.2.0 — Windows, Linux, macOS",
            "body": notes, "draft": True, "prerelease": False,
            "generate_release_notes": False,
        })

    def download(self, tag, directory):
        directory.mkdir(parents=True, exist_ok=False)
        subprocess.run(["gh", "release", "download", tag, "--repo", self.repository,
                        "--dir", str(directory)], check=True)

    def upload(self, tag, files):
        subprocess.run(["gh", "release", "upload", tag, "--repo", self.repository,
                        *map(str, files)], check=True)


def verify_original(release, directory):
    assets = asset_map(release)
    require({p.name for p in directory.iterdir()} == set(assets),
            f"Downloaded asset set differs for {release['tag_name']}")
    hashes = {}
    for name, asset in assets.items():
        path = directory / name
        require(path.is_file() and not path.is_symlink(), f"Unsafe downloaded asset: {name}")
        require(path.stat().st_size == asset["size"], f"Asset size mismatch: {name}")
        hashes[name] = sha256(path)
        digest = asset.get("digest")
        if digest:
            require(digest == "sha256:" + hashes[name], f"GitHub asset digest mismatch: {name}")
    covered = {}
    manifests = {}
    for name in assets:
        if name.startswith("SHA256SUMS") or name.endswith(".sha256"):
            text = (directory / name).read_text(encoding="utf-8")
            entries = verify_manifest(directory / name, directory)
            for asset_name, digest in entries.items():
                require(asset_name not in covered or covered[asset_name] == digest,
                        f"Conflicting checksums: {asset_name}")
                covered[asset_name] = digest
            manifests[name] = text
    for name in SELECTED[release["tag_name"]]:
        if name == "WinUSB_Creator.exe":
            require(hashes[name] == LEGACY_SHA256 and assets[name]["size"] == 13287794,
                    "Original Windows x64 binary differs from the inventoried release")
            with (directory / name).open("rb") as stream:
                header = stream.read(64)
                require(header[:2] == b"MZ" and len(header) == 64, "Invalid legacy Windows PE")
                stream.seek(struct.unpack_from("<I", header, 0x3C)[0])
                pe = stream.read(6)
            require(pe == b"PE\0\0\x64\x86", "Legacy Windows executable is not x64")
        else:
            require(name in covered, f"Platform build lacks an original SHA256 manifest: {name}")
        verify_platform_file(directory / name)
    return hashes, manifests


def pe_machine(header):
    require(len(header) >= 64 and header[:2] == b"MZ", "Invalid PE header")
    offset = struct.unpack_from("<I", header, 0x3C)[0]
    require(offset + 6 <= len(header) and header[offset:offset + 4] == b"PE\0\0", "Invalid PE signature")
    return struct.unpack_from("<H", header, offset + 4)[0]


def verify_platform_file(path):
    """Inspect containers and architecture only; never execute release payloads."""
    name = path.name
    if name.endswith(".AppImage"):
        with path.open("rb") as stream:
            header = stream.read(64)
        expected = 183 if "aarch64" in name else 62
        require(len(header) == 64 and header[:6] == b"\x7fELF\x02\x01"
                and struct.unpack_from("<H", header, 18)[0] == expected,
                f"AppImage architecture mismatch: {name}")
    elif name.endswith(".exe"):
        require(pe_machine(path.read_bytes()) == 0x8664, "Legacy executable is not Windows x64")
    elif name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            require(archive.testzip() is None, f"ZIP CRC error: {name}")
            entries = archive.namelist()
            require(len(entries) == len(set(entries)), f"Duplicate ZIP members: {name}")
            if "Windows-ARM64" in name:
                matches = [p for p in entries if p == "WinUSB-Creator-Windows-ARM64.exe"
                           or p.endswith("/WinUSB-Creator-Windows-ARM64.exe")]
                require(len(matches) == 1, "Missing or ambiguous Windows ARM64 executable")
                require(pe_machine(archive.read(matches[0])) == 0xAA64, "Windows ZIP is not ARM64")
            elif "macOS" in name:
                matches = [p for p in entries if p.endswith("WinUSB Creator.app/Contents/MacOS/WinUSB Creator")
                           and not p.startswith("__MACOSX/")]
                require(len(matches) == 1, "Missing or ambiguous macOS executable")
                header = archive.read(matches[0])[:8]
                cpu = 0x0100000C if "AppleSilicon" in name else 0x01000007
                require(len(header) == 8 and header[:4] == b"\xcf\xfa\xed\xfe"
                        and struct.unpack_from("<I", header, 4)[0] == cpu,
                        f"macOS architecture mismatch: {name}")


def create_source_archive(root, output, tag, source_sha):
    tracked = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "-z", source_sha],
                                      cwd=root, text=True).split("\0")
    paths = [name for name in tracked if name in ("README.md", "LICENSE", "flash_usb.py")
             or name.startswith(("packaging/", "tests/", "scripts/", ".github/", "docs/"))
             or (name.startswith("прога/") and (name.endswith(".py")
                 or name in ("прога/app_icon.ico", "прога/app_icon.jpg")))]
    require(REQUIRED_SOURCE_PATHS.issubset(paths), "Incomplete platform source tree")
    path = output / f"flash-usb-{tag}-source.tar.gz"
    subprocess.run(["git", "archive", "--format=tar.gz", f"--prefix=flash-usb-{tag}/",
                    f"--output={path.resolve()}", source_sha, "--", *paths], cwd=root, check=True)
    return path


def stage(github, root, output, tag, notes, source_sha):
    require(re.fullmatch(r"[a-f0-9]{40}", source_sha) is not None, "Expected an exact source commit")
    old = select_releases(github.list_releases(), tag)
    output.mkdir(parents=True, exist_ok=False)
    originals = output / "originals"
    originals.mkdir()
    (originals / "releases-before.json").write_text(json.dumps(old, indent=2) + "\n", encoding="utf-8")
    assets = output / "assets"
    assets.mkdir()
    provenance = {
        "schema": 1, "repository": REPOSITORY, "release": tag, "source_commit": source_sha,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "description": "Previously published platform builds copied byte-for-byte; source archive is current. "
                       "Legacy Windows x64 was not rebuilt or newly runtime-tested.",
        "original_releases": old, "original_tag_commits": {}, "original_manifests": {}, "assets": {},
    }
    for release in old:
        old_tag = release["tag_name"]
        provenance["original_tag_commits"][old_tag] = github.tag_sha(old_tag)
        directory = originals / old_tag
        github.download(old_tag, directory)
        hashes, manifests = verify_original(release, directory)
        provenance["original_manifests"][old_tag] = manifests
        selected = (*SELECTED[old_tag], *(COMPATIBILITY_MANIFESTS if old_tag == "v0.2.0-linux.2" else ()))
        for name in selected:
            shutil.copyfile(directory / name, assets / name)
            provenance["assets"][name] = {
                "original_release": old_tag, "original_asset_id": asset_map(release)[name]["id"],
                "sha256": hashes[name], "size": (assets / name).stat().st_size,
            }
    archive = create_source_archive(root, assets, tag, source_sha)
    provenance["assets"][archive.name] = {"source_commit": source_sha, "sha256": sha256(archive),
                                          "size": archive.stat().st_size}
    (assets / "RELEASE-PROVENANCE.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    files = sorted(assets.iterdir())
    (assets / "SHA256SUMS.txt").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in files), encoding="utf-8")
    verify_manifest(assets / "SHA256SUMS.txt", assets)
    (output / "release-notes.md").write_text(notes, encoding="utf-8")
    return {"old": old, "assets": assets, "output": output, "tag": tag, "notes": notes, "sha": source_sha}


def unchanged_originals(github, old):
    for original in old:
        require(fingerprint(github.release(original["id"])) == fingerprint(original),
                f"Original release changed during staging: {original['tag_name']}")


def verify_new_release(github, release_id, tag, assets, destination):
    release = github.release(release_id)
    require(release["tag_name"] == tag, "Destination release identity changed")
    expected = {p.name: p for p in assets.iterdir()}
    actual = asset_map(release)
    require(set(actual) == set(expected), "Destination release asset set is incomplete or unexpected")
    github.download(tag, destination)
    require({p.name for p in destination.iterdir()} == set(expected), "Downloaded destination asset set differs")
    for name, source in expected.items():
        target = destination / name
        require(target.is_file() and not target.is_symlink(), f"Unsafe destination file: {name}")
        digest = sha256(source)
        require(target.stat().st_size == source.stat().st_size == actual[name]["size"]
                and sha256(target) == digest, f"Uploaded asset differs: {name}")
        if actual[name].get("digest"):
            require(actual[name]["digest"] == "sha256:" + digest, f"Uploaded GitHub digest differs: {name}")
    verify_manifest(destination / "SHA256SUMS.txt", destination)


def rollback(github, old, new_id):
    """Restore availability first; never hide the new release after a failed restore."""
    errors = []
    for original in old:
        try:
            current = github.release(original["id"])
            require(current["id"] == original["id"] and current["tag_name"] == original["tag_name"],
                    "Cannot restore a release whose identity changed")
            if current["draft"]:
                github.edit(original["id"], draft=False, prerelease=original["prerelease"],
                            make_latest="false" if original["prerelease"] else "true")
            restored = github.release(original["id"])
            require(restored["draft"] is False, f"Restore did not publish {original['tag_name']}")
        except Exception as error:
            errors.append(f"{original['tag_name']}: {error}")
    if not errors:
        try:
            github.edit(new_id, draft=True)
            require(github.release(new_id)["draft"] is True, "New release was not returned to draft")
        except Exception as error:
            errors.append(f"new release: {error}")
    return errors


def publish(github, plan):
    old, tag, source_sha = plan["old"], plan["tag"], plan["sha"]
    require(github.tag_sha(tag) == source_sha, "Target tag moved before publication")
    select_releases(github.list_releases(), tag)
    unchanged_originals(github, old)
    new = github.create_draft(tag, source_sha, plan["notes"])
    require(new["draft"] is True and new["tag_name"] == tag and isinstance(new["id"], int),
            "GitHub did not create the expected draft release")
    # Failures here leave only a new draft; the old downloads stay public.
    github.upload(tag, sorted(plan["assets"].iterdir()))
    verify_new_release(github, new["id"], tag, plan["assets"], plan["output"] / "verify")
    unchanged_originals(github, old)
    require(github.tag_sha(tag) == source_sha, "Target tag moved during upload")
    try:
        github.edit(new["id"], draft=False, prerelease=False, make_latest="true")
        published = github.release(new["id"])
        require(published["draft"] is False and published["prerelease"] is False,
                "Destination is not a public non-prerelease")
        for original in old:
            require(fingerprint(github.release(original["id"])) == fingerprint(original),
                    f"Release changed before hiding: {original['tag_name']}")
            github.edit(original["id"], draft=True)
            require(github.release(original["id"])["draft"] is True,
                    f"Release did not become a draft: {original['tag_name']}")
        public = [r for r in github.list_releases() if r["draft"] is False]
        require(len(public) == 1 and public[0]["id"] == new["id"] and public[0]["tag_name"] == tag,
                "Final public release inventory is not the unified release alone")
        require(github.api("releases/latest")["id"] == new["id"], "Unified release is not Latest")
    except Exception as error:
        errors = rollback(github, old, new["id"])
        status = "Original releases restored; new release kept as a draft." if not errors else (
            "Rollback incomplete; inspect release states. Availability was preserved where possible: " + "; ".join(errors))
        raise ReleaseError(f"Publication migration failed: {error}. {status}") from error
    return published["html_url"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=TARGET_TAG, choices=[TARGET_TAG])
    parser.add_argument("--output", type=Path, required=True, help="New staging directory (must not already exist)")
    parser.add_argument("--notes-file", type=Path, required=True)
    parser.add_argument("--publish", action="store_true", help="Publish verified draft and hide the two old releases")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    source_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    require(os.environ.get("GITHUB_SHA") == source_sha, "GITHUB_SHA must match the checked-out commit")
    require(os.environ.get("GITHUB_REPOSITORY", REPOSITORY) == REPOSITORY, "Unexpected GitHub repository")
    require(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"],
                                    cwd=root, text=True) == "", "Tracked source files are modified")
    notes = args.notes_file.read_text(encoding="utf-8")
    require(bool(notes.strip()), "Release notes are empty")
    github = GitHub()
    if args.publish:
        require(github.tag_sha(args.tag) == source_sha,
                "Pushed target tag differs from the checked-out source")
    plan = stage(github, root, args.output, args.tag, notes, source_sha)
    print(f"Validated {len(list(plan['assets'].iterdir()))} unified release assets.", flush=True)
    if args.publish:
        print(f"Published: {publish(github, plan)}", flush=True)
        print("Old releases are recoverable drafts; no releases, assets or tags were deleted.", flush=True)
    else:
        print("Staging only: no GitHub release state was changed.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ReleaseError, subprocess.CalledProcessError) as error:
        print(f"Release consolidation stopped: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr, file=sys.stderr)
        raise SystemExit(1)

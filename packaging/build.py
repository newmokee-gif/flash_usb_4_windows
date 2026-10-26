#!/usr/bin/env python3
"""Build native, architecture-independent Debian/Fedora packages.

Run on Debian with dpkg-deb or Fedora with rpmbuild. Builds do not install
anything and do not require root. SOURCE_DATE_EPOCH defaults to the Git commit
timestamp; identical inputs and toolchains produce reproducible payloads.
"""
from __future__ import annotations

import argparse
import gzip
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


DEB_VERSION = "0.2.0~linux.2"
RPM_VERSION = "0.2.0"
RPM_RELEASE = "0.2.linux"


def copy_file(source: Path, destination: Path, executable=False):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(0o755 if executable else 0o644)


def stage_payload(source: Path, stage: Path, epoch: int):
    app = stage / "usr/share/flash-usb"
    for name in ("flash_usb.py", "прога/linux_usb.py", "прога/linux_gui.py", "прога/dependencies.py"):
        copy_file(source / name, app / name)
    copy_file(source / "packaging/flash-usb", stage / "usr/bin/flash-usb", True)
    copy_file(source / "packaging/flash-usb.desktop", stage / "usr/share/applications/flash-usb.desktop")
    copy_file(source / "packaging/flash-usb.svg", stage / "usr/share/icons/hicolor/scalable/apps/flash-usb.svg")
    copy_file(source / "README.md", stage / "usr/share/doc/flash-usb/README.md")
    copy_file(source / "LICENSE", stage / "usr/share/doc/flash-usb/copyright")
    copy_file(source / "LICENSE", stage / "usr/share/licenses/flash-usb/LICENSE")
    for item in [stage, *stage.rglob("*")]:
        if item.is_dir():
            item.chmod(0o755)
        os.utime(item, (epoch, epoch))


def build_deb(source: Path, work: Path, output: Path, epoch: int, env):
    stage = work / "deb"
    stage_payload(source, stage, epoch)
    control_dir = stage / "DEBIAN"
    control_dir.mkdir(mode=0o755)
    control_dir.chmod(0o755)
    control = source / "packaging/debian-control"
    installed_size = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file()) // 1024 + 1
    (control_dir / "control").write_text(
        control.read_text().replace("@VERSION@", DEB_VERSION).replace("@SIZE@", str(installed_size)),
        encoding="utf-8",
    )
    (control_dir / "control").chmod(0o644)
    os.utime(control_dir / "control", (epoch, epoch))
    os.utime(control_dir, (epoch, epoch))
    # GitHub normalizes '~' in asset names to '.', so use the published-safe
    # filename from the start; the Debian package version still contains '~'.
    artifact = output / f"flash-usb_{DEB_VERSION.replace('~', '.')}_all.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(stage), str(artifact)], check=True, env=env)
    return artifact


def build_rpm(source: Path, work: Path, output: Path, epoch: int, env):
    stage = work / "payload"
    stage_payload(source, stage, epoch)
    for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"):
        (work / name).mkdir()
    # Explicitly normalize ownership and gzip timestamp; no source-tree caches
    # or tracked Windows binaries enter the native Linux package.
    archive = work / "SOURCES/payload.tar.gz"
    with archive.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=epoch) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for item in sorted(stage.rglob("*")):
                info = tar.gettarinfo(str(item), arcname=str(item.relative_to(stage)))
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                info.mtime = epoch
                if item.is_file():
                    with item.open("rb") as contents:
                        tar.addfile(info, contents)
                else:
                    tar.addfile(info)
    spec = work / "SPECS/flash-usb.spec"
    spec.write_text((source / "packaging/flash-usb.spec").read_text().replace("@VERSION@", RPM_VERSION).replace("@RELEASE@", RPM_RELEASE), encoding="utf-8")
    subprocess.run([
        "rpmbuild", "-bb", str(spec), "--define", f"_topdir {work}",
        "--define", "_buildhost reproducible", "--define", "use_source_date_epoch_as_buildtime 1",
        "--define", "clamp_mtime_to_source_date_epoch 1",
    ], check=True, env=env)
    artifact_name = f"flash-usb-{RPM_VERSION}-{RPM_RELEASE}.noarch.rpm"
    artifact = output / artifact_name
    shutil.copyfile(work / "RPMS/noarch" / artifact_name, artifact)
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("format", choices=("deb", "rpm"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    if "SOURCE_DATE_EPOCH" in os.environ:
        epoch = int(os.environ["SOURCE_DATE_EPOCH"])
    else:
        # Release archives and minimal build images need not contain Git.
        try:
            result = subprocess.run(["git", "-C", str(source), "log", "-1", "--format=%ct"], capture_output=True, text=True)
            epoch = int(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip() else 0
        except FileNotFoundError:
            epoch = 0
    if epoch < 0:
        parser.error("SOURCE_DATE_EPOCH must be nonnegative")
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, SOURCE_DATE_EPOCH=str(epoch), LC_ALL="C.UTF-8", TZ="UTC")
    with tempfile.TemporaryDirectory(prefix="flash-usb-package-") as temporary:
        builder = build_deb if args.format == "deb" else build_rpm
        artifact = builder(source, Path(temporary), output, epoch, env)
    artifact.chmod(0o644)
    print(artifact)


if __name__ == "__main__":
    main()

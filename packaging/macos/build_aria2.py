#!/usr/bin/env python3
"""Build native aria2 with macOS 15 compatibility and only Apple system libraries.

Requires native Python 3.13 and Xcode command line tools. The official, pinned
source archive and this build recipe are installed alongside the GPL license.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


VERSION = "1.37.0"
ARCHIVE = f"aria2-{VERSION}.tar.xz"
SOURCE_URL = f"https://github.com/aria2/aria2/releases/download/release-{VERSION}/{ARCHIVE}"
SOURCE_SHA256 = "60a420ad7085eb616cb6e2bdf0a7206d68ff3d37fb5a956dc44242eb2f79b66b"
MINIMUM_MACOS = "15.0"
CONFIGURE_OPTIONS = [
    "--with-appletls", "--enable-ssl", "--enable-bittorrent",
    "--without-openssl", "--without-gnutls", "--without-libnettle",
    "--without-libgmp", "--without-libgcrypt", "--without-wintls",
    "--without-libssh2", "--without-libxml2", "--without-libexpat",
    "--without-libcares", "--without-sqlite3", "--without-libz",
    "--without-libuv", "--without-tcmalloc", "--without-jemalloc",
    "--disable-nls", "--disable-metalink", "--disable-websocket",
    "--disable-libaria2", "--disable-dependency-tracking",
]


def verify_source(archive):
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != SOURCE_SHA256:
        raise RuntimeError(f"Official aria2 source SHA256 mismatch: {digest}")


def build_environment():
    # Homebrew on the build host may target a newer macOS. Do not discover its
    # compiler, headers, pkg-config packages, or libraries while building aria2.
    environment = os.environ.copy()
    for name in (
        "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "OBJC_INCLUDE_PATH",
        "LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH",
        "CPP", "CXXCPP", "CPPFLAGS", "LIBS", "CONFIG_SITE", "MAKEFLAGS",
        "PKG_CONFIG_PATH", "PKG_CONFIG_LIBDIR", "PKG_CONFIG_SYSROOT_DIR",
    ):
        environment.pop(name, None)
    flags = f"-O2 -mmacosx-version-min={MINIMUM_MACOS} -Werror=unguarded-availability-new"
    environment.update(
        PATH="/usr/bin:/bin:/usr/sbin:/sbin", LANG="C", LC_ALL="C",
        CC="/usr/bin/clang", CXX="/usr/bin/clang++", PKG_CONFIG="/usr/bin/false",
        CFLAGS=flags, CXXFLAGS=flags + " -stdlib=libc++",
        LDFLAGS=f"-mmacosx-version-min={MINIMUM_MACOS}",
        MACOSX_DEPLOYMENT_TARGET=MINIMUM_MACOS, ARIA2_STATIC="no",
        SDKROOT=subprocess.check_output(
            ["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True).strip(),
    )
    return environment


def verify_binary(binary, arch, source, work, environment):
    architectures = subprocess.check_output(
        ["/usr/bin/lipo", "-archs", str(binary)], text=True).split()
    if architectures != [arch]:
        raise RuntimeError(f"Expected native {arch} aria2, found {architectures}")
    dependencies = subprocess.check_output(["/usr/bin/otool", "-L", str(binary)], text=True)
    for line in dependencies.splitlines()[1:]:
        dependency = line.strip().split(" (", 1)[0]
        if not dependency.startswith(("/System/Library/", "/usr/lib/")):
            raise RuntimeError(f"aria2 links to a non-system library: {dependency}")
    version = subprocess.check_output(
        [str(binary), "--no-conf=true", "--version"], text=True, env=environment)
    features = re.search(r"^Enabled Features: (.+)$", version, re.MULTILINE)
    if not features or not {"BitTorrent", "HTTPS"}.issubset(features.group(1).split(", ")):
        raise RuntimeError(f"aria2 is missing BitTorrent or HTTPS support:\n{version}")
    if "AppleTLS" not in version or "sha-256" not in version:
        raise RuntimeError(f"aria2 is missing Apple TLS or SHA256 support:\n{version}")
    print(version, flush=True)
    # Parse an upstream torrent fixture without joining a swarm or writing data.
    subprocess.run([str(binary), "--no-conf=true", "--show-files=true",
                    str(source / "test/single.torrent")],
                   check=True, env=environment, timeout=30)
    # Exercise the new Apple TLS and SHA256 implementations against the same
    # pinned official source. Certificate verification remains enabled.
    tls_check = work / "tls-check"
    tls_check.mkdir()
    subprocess.run([
        str(binary), "--no-conf=true", "--check-certificate=true",
        f"--checksum=sha-256={SOURCE_SHA256}", "--file-allocation=none",
        "--auto-file-renaming=false", "--allow-overwrite=false",
        "--max-tries=3", "--retry-wait=2", "--connect-timeout=30", "--timeout=60",
        "--dir=" + str(tls_check), "--out=" + ARCHIVE, SOURCE_URL,
    ], check=True, env=environment, timeout=300)
    verify_source(tls_check / ARCHIVE)
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, type=Path)
    parser.add_argument("--arch", required=True, choices=("arm64", "x86_64"))
    args = parser.parse_args()
    if sys.platform != "darwin" or platform.machine() != args.arch:
        raise RuntimeError(f"Build with native {args.arch} Python on matching macOS hardware")
    translated = subprocess.run(["/usr/sbin/sysctl", "-in", "sysctl.proc_translated"],
                                capture_output=True, text=True)
    if translated.stdout.strip() == "1":
        raise RuntimeError("Rosetta builds are not accepted as native builds")
    prefix = args.prefix.resolve()
    if prefix.exists() and any(prefix.iterdir()):
        raise RuntimeError(f"Use a new or empty aria2 installation prefix: {prefix}")
    environment = build_environment()
    with tempfile.TemporaryDirectory(prefix="winusb-aria2-build-") as directory:
        work = Path(directory)
        archive = work / ARCHIVE
        subprocess.run([
            "/usr/bin/curl", "--fail", "--location", "--proto", "=https",
            "--proto-redir", "=https", "--retry", "3", "--connect-timeout", "30",
            "--max-time", "300", "--output", str(archive), SOURCE_URL,
        ], check=True, env=environment, timeout=360)
        verify_source(archive)
        with tarfile.open(archive, "r:xz") as package:
            package.extractall(work, filter="data")
        source = work / f"aria2-{VERSION}"
        configure = [str(source / "configure"), "--prefix=" + str(prefix), *CONFIGURE_OPTIONS]
        subprocess.run(configure, cwd=source, env=environment, check=True)
        configuration = (source / "config.h").read_text(encoding="utf-8")
        for feature in ("ENABLE_SSL", "ENABLE_BITTORRENT", "HAVE_APPLETLS", "USE_APPLE_MD",
                        "USE_INTERNAL_BIGNUM", "USE_INTERNAL_ARC4"):
            if not re.search(rf"^#define {feature} 1$", configuration, re.MULTILINE):
                raise RuntimeError(f"aria2 configuration lacks required feature: {feature}")
        subprocess.run(["/usr/bin/make", "-j", str(min(os.cpu_count() or 2, 8))],
                       cwd=source, env=environment, check=True)
        subprocess.run(["/usr/bin/make", "-C", "src", "install-exec"],
                       cwd=source, env=environment, check=True)
        binary = prefix / "bin/aria2c"
        version = verify_binary(binary, args.arch, source, work, environment)
        provenance = prefix / "share/aria2"
        provenance.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / "COPYING", prefix / "COPYING")
        shutil.copyfile(source / "COPYING", provenance / "COPYING")
        shutil.copyfile(archive, provenance / ARCHIVE)
        shutil.copyfile(__file__, provenance / "build_aria2.py")
        metadata = {
            "version": VERSION, "source_url": SOURCE_URL, "source_sha256": SOURCE_SHA256,
            "architecture": args.arch, "minimum_macos": MINIMUM_MACOS,
            "configure_options": CONFIGURE_OPTIONS,
            "compiler": subprocess.check_output(["/usr/bin/clang++", "--version"], text=True),
            "sdk": environment["SDKROOT"], "aria2_version": version,
        }
        (provenance / "build.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        (provenance / "README.txt").write_text(
            f"aria2 {VERSION} is distributed under GPL version 2 or later; see COPYING.\n"
            "The complete unmodified corresponding source archive and build recipe are included.\n"
            f"Source: {SOURCE_URL}\nSHA256: {SOURCE_SHA256}\n"
            "Rebuild using native Python 3.13 and Xcode command line tools on macOS:\n"
            f"  python3 build_aria2.py --arch {args.arch} --prefix /tmp/aria2-rebuilt\n"
            "The script fetches and verifies the identical archive, builds using only Apple\n"
            "system libraries with a macOS 15 deployment target, and checks HTTPS/SHA256\n"
            "and BitTorrent support. See build.json for the build configuration.\n",
            encoding="utf-8")
        print(f"Verified native macOS {MINIMUM_MACOS}+ aria2: {binary}", flush=True)


if __name__ == "__main__":
    main()

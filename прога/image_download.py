"""Verified GNU/Linux image downloads; torrents are data, never executable input."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import errno
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
import zipfile


ARIA2_X64_URL = "https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip"
ARIA2_X64_ARCHIVE_SHA256 = "67d015301eef0b612191212d564c5bb0a14b5b9c4796b76454276a4d28d9b288"
ARIA2_X64_EXE_SHA256 = "be2099c214f63a3cb4954b09a0becd6e2e34660b886d4c898d260febfe9d70c2"
ARIA2_X64_MEMBER = "aria2-1.37.0-win-64bit-build1/aria2c.exe"


class DownloadError(RuntimeError):
    pass


class Cancelled(DownloadError):
    pass


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    sha256: str


def value(image, key, default=None):
    return image.get(key, default) if isinstance(image, dict) else getattr(image, key, default)


def https_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise DownloadError("The source must be an official HTTPS URL.")
    return url


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _cancelled(event):
    if event is not None and event.is_set():
        raise Cancelled("Download cancelled. USB writing has not started.")


def fetch(url, destination=None, *, cancelled=None, limit=None, log=lambda _: None):
    """Download HTTPS with bounded metadata and an interruptible streaming body."""
    request = urllib.request.Request(https_url(url), headers={"User-Agent": "WinUSB-Creator/0.3"})
    if sys.platform != "win32" and getattr(sys, "frozen", False):
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    else:
        context = ssl.create_default_context()
    opener = urllib.request.build_opener(HTTPSRedirect(), urllib.request.HTTPSHandler(context=context))
    total = 0
    chunks = []
    stream = None
    _cancelled(cancelled)
    try:
        if destination is not None:
            stream = Path(destination).open("xb")
        with opener.open(request, timeout=30) as response:
            https_url(response.geturl())
            while True:
                _cancelled(cancelled)
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if limit is not None and total > limit:
                    raise DownloadError("Source metadata exceeds the size limit.")
                if stream:
                    stream.write(chunk)
                else:
                    chunks.append(chunk)
                if stream and total % (64 * 1024 * 1024) < len(chunk):
                    log(f"Received {total // (1024 * 1024)} MiB")
        if total == 0:
            raise DownloadError("The source returned an empty file.")
        return b"".join(chunks) if destination is None else total
    finally:
        if stream:
            stream.close()


def checksum_from_manifest(text, filename, algorithm="sha256"):
    """Accept GNU/BSD checksum formats, including clear-signed checksum text."""
    length = {"sha256": 64, "sha512": 128}.get(algorithm.lower())
    if length is None:
        raise DownloadError("SHA-256 and SHA-512 checksums are supported.")
    found = set()
    bare = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- "):
            line = line[2:]
        match = re.fullmatch(rf"([0-9a-fA-F]{{{length}}})\s+\*?(.+)", line)
        if match and match[2].removeprefix("./") == filename:
            found.add(match[1].lower())
        bsd = re.fullmatch(rf"SHA-?{length * 4}\s*\((.+)\)\s*=\s*([0-9a-fA-F]{{{length}}})", line, re.I)
        if bsd and bsd[1].removeprefix("./") == filename:
            found.add(bsd[2].lower())
        if re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", line):
            bare.append(line.lower())
    if not found and len(text.splitlines()) == 1 and len(bare) == 1:
        found.add(bare[0])
    if len(found) != 1:
        raise DownloadError(f"Could not find an unambiguous {algorithm.upper()} for {filename}.")
    return found.pop()


def _safe_part(part):
    if not isinstance(part, bytes):
        raise DownloadError("Invalid torrent path.")
    try:
        name = part.decode("utf-8")
    except UnicodeError as exc:
        raise DownloadError("The torrent path must be UTF-8.") from exc
    if (name in ("", ".", "..") or name.endswith((".", " "))
            or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in name)
            or re.match(r"^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|CLOCK\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", name, re.I)):
        raise DownloadError("Unsafe torrent path.")
    return name


def _bdecode(data):
    index = 0

    def read(depth=0):
        nonlocal index
        if depth > 32 or index >= len(data):
            raise DownloadError("Corrupt torrent.")
        marker = data[index:index + 1]
        if marker in (b"d", b"l"):
            index += 1
            result = {} if marker == b"d" else []
            while index < len(data) and data[index:index + 1] != b"e":
                item = read(depth + 1)
                if isinstance(result, dict):
                    if not isinstance(item, bytes) or item in result:
                        raise DownloadError("Duplicate torrent key.")
                    result[item] = read(depth + 1)
                else:
                    result.append(item)
            if index >= len(data):
                raise DownloadError("Corrupt torrent.")
            index += 1
            return result
        if marker == b"i":
            end = data.find(b"e", index)
            if end < 0:
                raise DownloadError("Corrupt torrent.")
            try:
                number = int(data[index + 1:end])
            except ValueError as exc:
                raise DownloadError("Corrupt torrent.") from exc
            index = end + 1
            return number
        colon = data.find(b":", index)
        if colon < 0 or not data[index:colon].isdigit():
            raise DownloadError("Corrupt torrent.")
        length = int(data[index:colon])
        index = colon + 1
        if index + length > len(data):
            raise DownloadError("Corrupt torrent.")
        result = data[index:index + length]
        index += length
        return result

    result = read()
    if index != len(data):
        raise DownloadError("Trailing data in torrent.")
    return result


def torrent_target(data, filename):
    """Validate *all* paths before aria2 sees metadata and select only this image."""
    root = _bdecode(data)
    info = root.get(b"info") if isinstance(root, dict) else None
    if not isinstance(info, dict) or b"symlink path" in info or b"l" in info.get(b"attr", b""):
        raise DownloadError("Unsupported torrent.")
    names = [_safe_part(info[key]) for key in (b"name", b"name.utf-8") if key in info]
    if not names or len(set(names)) != 1:
        raise DownloadError("Ambiguous torrent name.")
    name = names[0]
    files = info.get(b"files")
    if files is None:
        if name != filename or not isinstance(info.get(b"length"), int) or info[b"length"] <= 0:
            raise DownloadError("The torrent does not contain the selected image.")
        return Path(name), None
    if not isinstance(files, list) or not files or len(files) > 10000:
        raise DownloadError("Unsupported torrent.")
    matches = []
    seen = set()
    for index, entry in enumerate(files, 1):
        if not isinstance(entry, dict) or b"symlink path" in entry or b"l" in entry.get(b"attr", b""):
            raise DownloadError("Links in torrents are not supported.")
        variants = [entry[key] for key in (b"path", b"path.utf-8") if key in entry]
        if not variants or any(not isinstance(parts, list) or not parts for parts in variants):
            raise DownloadError("Invalid torrent path.")
        decoded = [tuple(_safe_part(part) for part in parts) for parts in variants]
        if len(set(decoded)) != 1:
            raise DownloadError("Ambiguous torrent path.")
        parts = decoded[0]
        path = Path(name, *parts)
        if str(path).casefold() in seen:
            raise DownloadError("Duplicate torrent paths.")
        seen.add(str(path).casefold())
        if parts[-1] == filename:
            matches.append((path, index))
    if len(matches) != 1:
        raise DownloadError("The torrent does not uniquely identify the selected image.")
    return matches[0]


def runtime_environment(*, restart=False):
    env = os.environ.copy()
    if restart:
        env["PYTHONIOENCODING"] = "utf-8"
    if getattr(sys, "frozen", False):
        for key in list(env):
            if key.startswith("DYLD_"):
                env.pop(key, None)
        old = env.pop("LD_LIBRARY_PATH_ORIG", None)
        env.pop("LD_LIBRARY_PATH", None)
        if old:
            env["LD_LIBRARY_PATH"] = old
        if restart:
            for key in ("TCL_LIBRARY", "TK_LIBRARY", "PYTHONHOME", "PYTHONPATH"):
                env.pop(key, None)
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def download_torrent(url, filename, directory, aria, *, cancelled=None):
    data = fetch(url, limit=8 * 1024 * 1024, cancelled=cancelled)
    relative, selection = torrent_target(data, filename)
    meta = directory / "source.torrent"
    meta.write_bytes(data)
    command = [aria, "--no-conf=true", "--check-certificate=true", "--seed-time=0", "--quiet=true",
               "--summary-interval=0", "--bt-stop-timeout=120", "--max-tries=3", "--timeout=30",
               "--auto-file-renaming=false", "--allow-overwrite=false", "--check-integrity=true",
               "--dir=" + str(directory)]
    if selection is not None:
        command.append(f"--select-file={selection}")
    if sys.platform != "win32" and getattr(sys, "frozen", False):
        import certifi
        command.append("--ca-certificate=" + certifi.where())
    command.append(str(meta))
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          env=aria_environment(aria),
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0) as process:
        try:
            while True:
                _cancelled(cancelled)
                try:
                    output, _ = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise
        if process.returncode:
            raise DownloadError(f"aria2: exit code {process.returncode}; " + output.decode(errors="replace")[-500:])
    result = directory / relative
    if not result.is_file() or result.is_symlink() or directory.resolve() not in result.resolve().parents:
        raise DownloadError("aria2 did not create the expected image.")
    return result


def file_hash(path, algorithm, *, cancelled=None):
    return file_hashes(path, algorithm, cancelled=cancelled)[algorithm]


def file_hashes(path, algorithm, *, cancelled=None):
    digests = {name: hashlib.new(name) for name in {algorithm, "sha256"}}
    with Path(path).open("rb") as source:
        before = os.fstat(source.fileno())
        while True:
            _cancelled(cancelled)
            chunk = source.read(4 * 1024 * 1024)
            if not chunk:
                break
            for digest in digests.values():
                digest.update(chunk)
        after = os.fstat(source.fileno())
        identity = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if identity(before) != identity(after):
            raise DownloadError("The image changed during verification.")
    return {name: digest.hexdigest() for name, digest in digests.items()}


def windows_arm64():
    import platform
    return os.environ.get("PROCESSOR_ARCHITEW6432", platform.machine()).lower() in ("arm64", "aarch64")


def bundled_aria2():
    if getattr(sys, "frozen", False) and sys.platform != "win32" and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS).resolve()
        candidate = base / "aria2c"
        if candidate.is_file() and os.access(candidate, os.X_OK) and base in candidate.resolve().parents:
            return str(candidate)
    return None


def aria_environment(executable):
    env = runtime_environment()
    bundled = bundled_aria2()
    if sys.platform.startswith("linux") and bundled and Path(executable).resolve() == Path(bundled).resolve():
        env["LD_LIBRARY_PATH"] = str(Path(sys._MEIPASS).resolve())
    return env


def find_aria2():
    bundled = bundled_aria2()
    if bundled:
        return bundled
    candidates = []
    executable = shutil.which("aria2c")
    if executable:
        candidates.append(Path(executable))
    if sys.platform == "win32":
        arm = windows_arm64()
        # Only discover an explicitly installed native client here. The legacy
        # TEMP/win_usb_tools* cache has no provenance guarantee: a PE machine
        # header alone must never bypass the pinned bootstrap verification.
        expected = 0xAA64 if arm else 0x8664
        for candidate in candidates:
            try:
                with candidate.open("rb") as source:
                    header = source.read(64)
                    if len(header) != 64 or header[:2] != b"MZ":
                        continue
                    source.seek(struct.unpack_from("<I", header, 0x3C)[0])
                    if source.read(6) == b"PE\0\0" + expected.to_bytes(2, "little"):
                        return str(candidate)
            except OSError:
                continue
        return None
    return str(candidates[0]) if candidates else None


def ensure_windows_aria2(*, log=lambda _: None, cancelled=None, allow_emulation=False):
    """Provision the exact upstream helper; the ARM64 application stays native."""
    if sys.platform != "win32":
        raise DownloadError("Automatic installation of this aria2 build is available only on Windows.")
    native = find_aria2()
    if native:
        return native
    if windows_arm64():
        if not allow_emulation or sys.getwindowsversion().build < 22000:
            raise DownloadError("Native aria2 ARM64 was not found; the x64 helper requires consent and Windows 11 ARM64.")
        log("aria2 x64 will run under Windows 11 ARM64 emulation; the application itself remains ARM64.")
    cache = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "WinUSB Creator/tools/aria2/1.37.0-x64"
    cache.mkdir(parents=True, exist_ok=True)
    executable = cache / "aria2c.exe"
    archive_cache = cache / "upstream.zip"

    def verified_cache():
        return (executable.is_file() and not executable.is_symlink()
                and archive_cache.is_file() and not archive_cache.is_symlink()
                and file_hash(archive_cache, "sha256", cancelled=cancelled) == ARIA2_X64_ARCHIVE_SHA256
                and file_hash(executable, "sha256", cancelled=cancelled) == ARIA2_X64_EXE_SHA256)

    if verified_cache():
        log("Cached aria2 1.37.0 verified (archive and EXE SHA-256).")
        return str(executable)
    log("Installing the bundled aria2 torrent helper from its official GitHub release…")
    with tempfile.TemporaryDirectory(prefix=".aria2-", dir=cache) as work:
        directory = Path(work)
        archive_path = directory / "upstream.zip"
        fetch(ARIA2_X64_URL, archive_path, cancelled=cancelled, limit=16 * 1024 * 1024)
        if file_hash(archive_path, "sha256", cancelled=cancelled) != ARIA2_X64_ARCHIVE_SHA256:
            raise DownloadError("aria2 archive SHA-256 mismatch; execution blocked.")
        with zipfile.ZipFile(archive_path) as archive:
            if archive.namelist().count(ARIA2_X64_MEMBER) != 1:
                raise DownloadError("The archive does not contain a unique aria2c.exe.")
            content = archive.read(ARIA2_X64_MEMBER)
            if hashlib.sha256(content).hexdigest() != ARIA2_X64_EXE_SHA256:
                raise DownloadError("aria2c.exe SHA-256 mismatch; execution blocked.")
            if len(content) < 64 or content[:2] != b"MZ":
                raise DownloadError("Invalid aria2c.exe PE executable.")
            offset = struct.unpack_from("<I", content, 0x3C)[0]
            if content[offset:offset + 6] != b"PE\0\0\x64\x86":
                raise DownloadError("Incorrect aria2c.exe architecture.")
            staged = directory / "aria2c.exe"
            staged.write_bytes(content)
            for filename in ("COPYING", "LICENSE.OpenSSL", "README.mingw"):
                (cache / filename).write_bytes(archive.read("aria2-1.37.0-win-64bit-build1/" + filename))
        _cancelled(cancelled)
        os.replace(archive_path, archive_cache)
        os.replace(staged, executable)
    if not verified_cache():
        raise DownloadError("Installed aria2 failed verification.")
    return str(executable)


def download_image(image, destination, *, log=lambda _: None, cancelled=None,
                   provision_aria=False, allow_emulated_aria=False):
    image_url = https_url(value(image, "image_url"))
    filename = value(image, "filename") or value(image, "image_name") or urllib.parse.unquote(PurePosixPath(urllib.parse.urlsplit(image_url).path).name)
    _safe_part(filename.encode("utf-8"))
    if not filename.lower().endswith((".iso", ".img")):
        raise DownloadError("Writing requires an uncompressed hybrid .iso or .img.")
    algorithm = value(image, "checksum_algorithm", "sha256").lower()
    digest = value(image, "sha256")
    if digest:
        algorithm = "sha256"
    elif value(image, "sha512"):
        algorithm = "sha512"
        digest = value(image, "sha512")
    else:
        manifest = fetch(value(image, "checksum_url"), limit=8 * 1024 * 1024, cancelled=cancelled)
        digest = checksum_from_manifest(manifest.decode("utf-8-sig"), filename, algorithm)
    expected_length = {"sha256": 64, "sha512": 128}.get(algorithm)
    if expected_length is None or re.fullmatch(rf"[0-9a-fA-F]{{{expected_length}}}", digest or "") is None:
        raise DownloadError("No valid checksum from the official source.")
    destination = Path(destination).expanduser().resolve()
    if not destination.is_dir():
        raise DownloadError("Select an existing download folder.")
    target = destination / filename
    if target.exists():
        raise DownloadError("This file already exists. Select another folder or use a local image.")
    with tempfile.TemporaryDirectory(prefix=".winusb-download-", dir=destination) as work:
        directory = Path(work)
        torrent = value(image, "torrent_url")
        aria = find_aria2()
        if torrent and not aria and provision_aria and sys.platform == "win32":
            try:
                aria = ensure_windows_aria2(log=log, cancelled=cancelled, allow_emulation=allow_emulated_aria)
            except Cancelled:
                raise
            except (DownloadError, OSError, zipfile.BadZipFile) as exc:
                log(f"Could not prepare the torrent helper: {exc}. Using the official HTTPS source.")
        downloaded = None
        if torrent and aria:
            log("Downloading the official torrent with aria2. Seeding after download is disabled.")
            try:
                downloaded = download_torrent(torrent, filename, directory, aria, cancelled=cancelled)
            except Cancelled:
                raise
            except (DownloadError, OSError) as exc:
                log(f"Torrent unavailable: {exc}. Using the official HTTPS source.")
        elif torrent:
            log("aria2c was not found: using the official HTTPS source.")
        if downloaded is None:
            log("Downloading the image from the official HTTPS source.")
            downloaded = directory / "direct-image"
            fetch(image_url, downloaded, cancelled=cancelled, log=log)
        log(f"Verifying {algorithm.upper()} before writing…")
        hashes = file_hashes(downloaded, algorithm, cancelled=cancelled)
        actual = hashes[algorithm]
        if actual.lower() != digest.lower():
            raise DownloadError("Image checksum mismatch. Writing is blocked.")
        _cancelled(cancelled)
        # Atomic exclusive publication: no replacement of a user's existing file.
        created = False
        try:
            try:
                os.link(downloaded, target)
                created = True
            except OSError as exc:
                if exc.errno not in (errno.EPERM, errno.EACCES, errno.ENOTSUP, errno.EXDEV):
                    raise
                with target.open("xb") as output:
                    created = True
                    with downloaded.open("rb") as source:
                        while True:
                            _cancelled(cancelled)
                            chunk = source.read(4 * 1024 * 1024)
                            if not chunk:
                                break
                            output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            # Both the official digest and the writer's SHA256 cover the exact
            # same bytes, including when the source was swapped before publish.
            published = file_hashes(target, algorithm, cancelled=cancelled)
            if published[algorithm] != digest.lower() or published["sha256"] != hashes["sha256"]:
                raise DownloadError("The saved image changed. Writing is blocked.")
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise
        log(f"Image verified and saved: {target}")
        return DownloadResult(target, published["sha256"])

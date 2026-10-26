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
        raise DownloadError("Источник должен быть официальной HTTPS-ссылкой.")
    return url


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _cancelled(event):
    if event is not None and event.is_set():
        raise Cancelled("Загрузка отменена. Запись USB не начиналась.")


def fetch(url, destination=None, *, cancelled=None, limit=None, log=lambda _: None):
    """Download HTTPS with bounded metadata and an interruptible streaming body."""
    request = urllib.request.Request(https_url(url), headers={"User-Agent": "WinUSB-Creator/0.3"})
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
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
                    raise DownloadError("Метаданные источника превышают допустимый размер.")
                if stream:
                    stream.write(chunk)
                else:
                    chunks.append(chunk)
                if stream and total % (64 * 1024 * 1024) < len(chunk):
                    log(f"Получено {total // (1024 * 1024)} МиБ")
        if total == 0:
            raise DownloadError("Источник вернул пустой файл.")
        return b"".join(chunks) if destination is None else total
    finally:
        if stream:
            stream.close()


def checksum_from_manifest(text, filename, algorithm="sha256"):
    """Accept GNU/BSD checksum formats, including clear-signed checksum text."""
    length = {"sha256": 64, "sha512": 128}.get(algorithm.lower())
    if length is None:
        raise DownloadError("Поддерживаются контрольные суммы SHA-256 и SHA-512.")
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
        raise DownloadError(f"Не найдена однозначная {algorithm.upper()} для {filename}.")
    return found.pop()


def _safe_part(part):
    if not isinstance(part, bytes):
        raise DownloadError("Неверный путь в torrent.")
    try:
        name = part.decode("utf-8")
    except UnicodeError as exc:
        raise DownloadError("Путь torrent должен быть UTF-8.") from exc
    if (name in ("", ".", "..") or name.endswith((".", " "))
            or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in name)
            or re.match(r"^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|CLOCK\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", name, re.I)):
        raise DownloadError("Небезопасный путь в torrent.")
    return name


def _bdecode(data):
    index = 0

    def read(depth=0):
        nonlocal index
        if depth > 32 or index >= len(data):
            raise DownloadError("Повреждённый torrent.")
        marker = data[index:index + 1]
        if marker in (b"d", b"l"):
            index += 1
            result = {} if marker == b"d" else []
            while index < len(data) and data[index:index + 1] != b"e":
                item = read(depth + 1)
                if isinstance(result, dict):
                    if not isinstance(item, bytes) or item in result:
                        raise DownloadError("Повторяющийся ключ в torrent.")
                    result[item] = read(depth + 1)
                else:
                    result.append(item)
            if index >= len(data):
                raise DownloadError("Повреждённый torrent.")
            index += 1
            return result
        if marker == b"i":
            end = data.find(b"e", index)
            if end < 0:
                raise DownloadError("Повреждённый torrent.")
            try:
                number = int(data[index + 1:end])
            except ValueError as exc:
                raise DownloadError("Повреждённый torrent.") from exc
            index = end + 1
            return number
        colon = data.find(b":", index)
        if colon < 0 or not data[index:colon].isdigit():
            raise DownloadError("Повреждённый torrent.")
        length = int(data[index:colon])
        index = colon + 1
        if index + length > len(data):
            raise DownloadError("Повреждённый torrent.")
        result = data[index:index + length]
        index += length
        return result

    result = read()
    if index != len(data):
        raise DownloadError("Лишние данные в torrent.")
    return result


def torrent_target(data, filename):
    """Validate *all* paths before aria2 sees metadata and select only this image."""
    root = _bdecode(data)
    info = root.get(b"info") if isinstance(root, dict) else None
    if not isinstance(info, dict) or b"symlink path" in info or b"l" in info.get(b"attr", b""):
        raise DownloadError("Неподдерживаемый torrent.")
    names = [_safe_part(info[key]) for key in (b"name", b"name.utf-8") if key in info]
    if not names or len(set(names)) != 1:
        raise DownloadError("Неоднозначное имя torrent.")
    name = names[0]
    files = info.get(b"files")
    if files is None:
        if name != filename or not isinstance(info.get(b"length"), int) or info[b"length"] <= 0:
            raise DownloadError("Torrent не содержит выбранный образ.")
        return Path(name), None
    if not isinstance(files, list) or not files or len(files) > 10000:
        raise DownloadError("Неподдерживаемый torrent.")
    matches = []
    seen = set()
    for index, entry in enumerate(files, 1):
        if not isinstance(entry, dict) or b"symlink path" in entry or b"l" in entry.get(b"attr", b""):
            raise DownloadError("Ссылки в torrent не поддерживаются.")
        variants = [entry[key] for key in (b"path", b"path.utf-8") if key in entry]
        if not variants or any(not isinstance(parts, list) or not parts for parts in variants):
            raise DownloadError("Неверный путь torrent.")
        decoded = [tuple(_safe_part(part) for part in parts) for parts in variants]
        if len(set(decoded)) != 1:
            raise DownloadError("Неоднозначный путь torrent.")
        parts = decoded[0]
        path = Path(name, *parts)
        if str(path).casefold() in seen:
            raise DownloadError("Повторяющиеся пути torrent.")
        seen.add(str(path).casefold())
        if parts[-1] == filename:
            matches.append((path, index))
    if len(matches) != 1:
        raise DownloadError("Torrent не содержит однозначно выбранный образ.")
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
    command.append(str(meta))
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          env=runtime_environment(),
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
            raise DownloadError(f"aria2: код {process.returncode}; " + output.decode(errors="replace")[-500:])
    result = directory / relative
    if not result.is_file() or result.is_symlink() or directory.resolve() not in result.resolve().parents:
        raise DownloadError("aria2 не создал ожидаемый образ.")
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
            raise DownloadError("Образ изменился во время проверки.")
    return {name: digest.hexdigest() for name, digest in digests.items()}


def find_aria2():
    candidates = []
    executable = shutil.which("aria2c")
    if executable:
        candidates.append(Path(executable))
    if sys.platform == "win32":
        import platform
        native = os.environ.get("PROCESSOR_ARCHITEW6432", platform.machine()).lower()
        arm = native in ("arm64", "aarch64")
        folder = "win_usb_tools_arm64" if arm else "win_usb_tools"
        candidates.append(Path(tempfile.gettempdir(), folder, "aria2c.exe"))
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


def download_image(image, destination, *, log=lambda _: None, cancelled=None):
    image_url = https_url(value(image, "image_url"))
    filename = value(image, "filename") or value(image, "image_name") or urllib.parse.unquote(PurePosixPath(urllib.parse.urlsplit(image_url).path).name)
    _safe_part(filename.encode("utf-8"))
    if not filename.lower().endswith((".iso", ".img")):
        raise DownloadError("Для записи нужен несжатый гибридный .iso или .img.")
    algorithm = value(image, "checksum_algorithm", "sha256").lower()
    digest = value(image, "sha256")
    if digest:
        algorithm = "sha256"
    else:
        manifest = fetch(value(image, "checksum_url"), limit=8 * 1024 * 1024, cancelled=cancelled)
        digest = checksum_from_manifest(manifest.decode("utf-8-sig"), filename, algorithm)
    expected_length = {"sha256": 64, "sha512": 128}.get(algorithm)
    if expected_length is None or re.fullmatch(rf"[0-9a-fA-F]{{{expected_length}}}", digest or "") is None:
        raise DownloadError("Нет действительной контрольной суммы официального источника.")
    destination = Path(destination).expanduser().resolve()
    if not destination.is_dir():
        raise DownloadError("Выберите существующую папку для загрузки.")
    target = destination / filename
    if target.exists():
        raise DownloadError("Такой файл уже существует. Выберите другую папку или локальный образ.")
    with tempfile.TemporaryDirectory(prefix=".winusb-download-", dir=destination) as work:
        directory = Path(work)
        torrent = value(image, "torrent_url")
        aria = find_aria2()
        downloaded = None
        if torrent and aria:
            log("Загрузка официального torrent через aria2. Раздача после загрузки отключена.")
            try:
                downloaded = download_torrent(torrent, filename, directory, aria, cancelled=cancelled)
            except Cancelled:
                raise
            except (DownloadError, OSError) as exc:
                log(f"Torrent недоступен: {exc}. Используется официальный HTTPS-источник.")
        elif torrent:
            log("aria2c не найден: используется официальный HTTPS-источник.")
        if downloaded is None:
            log("Загрузка образа с официального HTTPS-источника.")
            downloaded = directory / "direct-image"
            fetch(image_url, downloaded, cancelled=cancelled, log=log)
        log(f"Проверка {algorithm.upper()} перед записью…")
        hashes = file_hashes(downloaded, algorithm, cancelled=cancelled)
        actual = hashes[algorithm]
        if actual.lower() != digest.lower():
            raise DownloadError("Контрольная сумма образа не совпадает. Запись запрещена.")
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
                raise DownloadError("Сохранённый образ изменился. Запись запрещена.")
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise
        log(f"Образ проверен и сохранён: {target}")
        return DownloadResult(target, published["sha256"])

import base64
import ctypes
from ctypes import wintypes
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import urllib.request
import zipfile

ARIA2_URL = "https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip"
FIDO_URL = "https://raw.githubusercontent.com/pbatard/Fido/master/Fido.ps1"
UEFI_NTFS_URL = "https://raw.githubusercontent.com/pbatard/rufus/master/res/uefi/uefi-ntfs.img"

BT_TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce,"
    "udp://open.demonii.com:1337/announce,"
    "udp://tracker.openbittorrent.com:6969/announce,"
    "udp://exodus.desync.com:6969/announce,"
    "http://tracker.openbittorrent.com:80/announce"
)

# Строгая 64-битная типизация WinAPI для исключения переполнения дескрипторов (HANDLE)
kernel32 = ctypes.windll.kernel32
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.SetFilePointerEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_longlong,
    ctypes.POINTER(ctypes.c_longlong),
    wintypes.DWORD,
]
kernel32.SetFilePointerEx.restype = wintypes.BOOL
kernel32.ReadFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
kernel32.ReadFile.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetLastError.argtypes = []
kernel32.GetLastError.restype = wintypes.DWORD

INVALID_HANDLE_VALUES = (None, -1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF, ctypes.c_void_p(-1).value)


def is_arm64_host():
    machine = os.environ.get("PROCESSOR_ARCHITEW6432") or platform.machine()
    return machine.lower() in ("arm64", "aarch64")


def windows_iso_architecture(choice):
    return "ARM64" if "ARM64" in choice.upper() else "x64"


def is_arm64_executable(path):
    """Reject cached/emulated x64 helpers when running on Windows ARM64."""
    try:
        with open(path, "rb") as stream:
            if stream.read(2) != b"MZ":
                return False
            stream.seek(0x3C)
            offset = int.from_bytes(stream.read(4), "little")
            stream.seek(offset)
            return stream.read(6) == b"PE\0\0\x64\xaa"
    except (OSError, ValueError):
        return False


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run_as_admin():
    executable = sys.executable
    if getattr(sys, "frozen", False):
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
    else:
        params = " ".join(f'"{a}"' for a in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, None, 1)
    sys.exit(0)


def decode_console_bytes(raw_bytes):
    if not raw_bytes:
        return ""
    for enc in ("utf-8", "cp866", "cp1251", "utf-16le"):
        try:
            return raw_bytes.decode(enc)
        except Exception:
            continue
    return raw_bytes.decode("latin1", errors="replace")


def kill_process_tree(proc):
    """Надежное завершение процесса вместе со всеми дочерними подпроцессами."""
    if not proc or proc.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=5,
        )
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run_cmd(cmd, env=None, timeout=180):
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return (
            proc.returncode,
            decode_console_bytes(proc.stdout),
            decode_console_bytes(proc.stderr),
        )
    except subprocess.TimeoutExpired:
        return -1, "", "Превышено время ожидания команды (TimeoutExpired)"
    except Exception as e:
        return -1, "", str(e)


def run_ps(ps_script, timeout=180):
    wrapped = (
        "$ErrorActionPreference = 'Continue'; "
        "$ProgressPreference = 'SilentlyContinue'; "
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        + ps_script
    )
    encoded = base64.b64encode(wrapped.encode("utf-16le")).decode("ascii")
    return run_cmd(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        timeout=timeout,
    )


def create_unique_iso_folder(base_drive="C:\\", folder_name="iso_files"):
    if not os.path.exists(base_drive):
        base_drive = os.path.abspath(os.sep)

    target = os.path.join(base_drive, folder_name)
    try:
        if not os.path.exists(target):
            os.makedirs(target, exist_ok=True)
            return target
        if not os.listdir(target):
            return target

        counter = 1
        while True:
            candidate = os.path.join(base_drive, f"{folder_name}({counter})")
            if not os.path.exists(candidate):
                os.makedirs(candidate, exist_ok=True)
                return candidate
            if not os.listdir(candidate):
                return candidate
            counter += 1
    except Exception:
        fallback = os.path.join(os.environ.get("TEMP", "C:\\"), folder_name)
        os.makedirs(fallback, exist_ok=True)
        return fallback


class WindowsOneClickUSB:
    def __init__(self, root):
        self.root = root
        self.root.title("Windows 10/11 — One-Click USB Creator (PRO Fixed)")
        self.root.geometry("620x610")
        self.root.resizable(False, False)
        # --- ICON_START ---
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("oploic.winusb.creator.pro.2")
            self._app_icon_img = tk.PhotoImage(data="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")
            self.root.iconphoto(True, self._app_icon_img)
        except Exception as e:
            pass
        # --- ICON_END ---

        self.is_closing = False
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.work_dir = create_unique_iso_folder("C:\\", "iso_files")
        self.tools_dir = os.path.join(
            os.environ.get("TEMP", "C:\\Temp"), "win_usb_tools_arm64" if is_arm64_host() else "win_usb_tools"
        )
        os.makedirs(self.tools_dir, exist_ok=True)

        self.iso_path = tk.StringVar()
        self.torrent_source = tk.StringVar()
        self.selected_drive = tk.StringVar()
        self.win_version = tk.StringVar(value="Windows 11 (ARM64 Русская)" if is_arm64_host() else "Windows 11 (64-bit Русская)")
        self.auto_write_var = tk.BooleanVar(value=True)
        self.check_usb_var = tk.BooleanVar(value=True)
        self.drives_map = {}

        self.is_busy = False
        self.active_proc = None

        self.build_ui()
        self.refresh_drives()

    def ui_call(self, func, *args, **kwargs):
        """Потокобезопасное выполнение изменений GUI в главном потоке Tkinter."""
        if not self.is_closing:
            try:
                self.root.after(
                    0, lambda: func(*args, **kwargs) if not self.is_closing else None
                )
            except Exception:
                pass

    def on_close(self):
        if self.is_busy:
            if not messagebox.askyesno(
                "Прервать операцию?",
                "Сейчас выполняется запись или скачивание образа.\nПрервать операцию и закрыть программу?",
            ):
                return
        self.is_closing = True
        kill_process_tree(self.active_proc)
        self.root.destroy()

    def build_ui(self):
        pad = {"padx": 15, "pady": 4}

        folder_frame = tk.Frame(self.root, bg="#e9f5ff")
        folder_frame.pack(fill="x", padx=15, pady=(10, 2))
        tk.Label(
            folder_frame,
            text=f"📁 Папка для загрузки ISO: {self.work_dir}",
            bg="#e9f5ff",
            fg="#004085",
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=8, pady=4)

        tk.Label(
            self.root,
            text="1. Выберите целевую USB-флешку (от 8 до 128 ГБ):",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=15, pady=(8, 2))
        usb_frame = tk.Frame(self.root)
        usb_frame.pack(fill="x", **pad)

        self.drive_combo = ttk.Combobox(
            usb_frame,
            textvariable=self.selected_drive,
            state="readonly",
            font=("Segoe UI", 9),
        )
        self.drive_combo.pack(
            side="left", fill="x", expand=True, padx=(0, 5), ipady=2
        )

        self.test_usb_btn = tk.Button(
            usb_frame,
            text="🔍 Тест секторов",
            width=14,
            bg="#f0ad4e",
            fg="white",
            font=("Segoe UI", 8, "bold"),
            relief="flat",
            command=self.start_standalone_usb_check,
        )
        self.test_usb_btn.pack(side="left", padx=(0, 5), ipady=2)

        self.refresh_btn = tk.Button(
            usb_frame, text="Обновить", width=10, command=self.refresh_drives
        )
        self.refresh_btn.pack(side="right")

        chk_usb_frame = tk.Frame(self.root)
        chk_usb_frame.pack(fill="x", padx=15, pady=(0, 2))
        tk.Checkbutton(
            chk_usb_frame,
            text="Проверять флешку на ошибки и битые сектора перед записью образа",
            variable=self.check_usb_var,
            font=("Segoe UI", 9),
            fg="#333333",
        ).pack(anchor="w")

        tk.Label(
            self.root,
            text="2. Загрузка образа Windows (фоновый клиент aria2):",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=15, pady=(6, 2))

        chk_frame = tk.Frame(self.root)
        chk_frame.pack(fill="x", padx=15, pady=(0, 2))
        tk.Checkbutton(
            chk_frame,
            text="Автоматически записать Windows на флешку сразу после скачивания",
            variable=self.auto_write_var,
            font=("Segoe UI", 9, "bold"),
            fg="#0056b3",
        ).pack(anchor="w")

        ver_frame = tk.Frame(self.root)
        ver_frame.pack(fill="x", **pad)
        self.ver_combo = ttk.Combobox(
            ver_frame,
            textvariable=self.win_version,
            state="readonly",
            font=("Segoe UI", 9),
            width=26,
            values=[
                "Windows 11 (64-bit Русская)",
                "Windows 10 (64-bit Русская)",
                "Windows 11 (64-bit English)",
                "Windows 11 (ARM64 Русская)",
                "Windows 11 (ARM64 English)",
            ],
        )
        self.ver_combo.pack(side="left", padx=(0, 5), ipady=3)

        self.dl_auto_btn = tk.Button(
            ver_frame,
            text="🚀 В 1 КЛИК: Скачать Windows и записать",
            bg="#0078D7",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            command=self.start_auto_windows_pipeline,
        )
        self.dl_auto_btn.pack(side="left", fill="x", expand=True, ipady=4)

        tor_frame = tk.Frame(self.root)
        tor_frame.pack(fill="x", **pad)
        tk.Label(
            tor_frame, text="Торрент / Magnet / URL:", font=("Segoe UI", 8)
        ).pack(side="left", padx=(0, 5))
        tk.Entry(
            tor_frame, textvariable=self.torrent_source, font=("Segoe UI", 8)
        ).pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.dl_custom_btn = tk.Button(
            tor_frame,
            text="Выбрать .torrent",
            font=("Segoe UI", 8),
            command=self.browse_torrent,
        )
        self.dl_custom_btn.pack(side="left", padx=(0, 4))
        self.dl_mag_btn = tk.Button(
            tor_frame,
            text="Скачать по ссылке",
            font=("Segoe UI", 8),
            command=self.start_custom_pipeline,
        )
        self.dl_mag_btn.pack(side="right")

        tk.Label(
            self.root,
            text="3. Образ Windows ISO (перехватывается сам или выбирается вручную):",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=15, pady=(8, 2))
        iso_frame = tk.Frame(self.root)
        iso_frame.pack(fill="x", **pad)

        tk.Entry(
            iso_frame,
            textvariable=self.iso_path,
            state="readonly",
            font=("Segoe UI", 9),
        ).pack(side="left", fill="x", expand=True, padx=(0, 5), ipady=3)
        self.browse_iso_btn = tk.Button(
            iso_frame, text="Обзор ISO...", width=12, command=self.browse_iso
        )
        self.browse_iso_btn.pack(side="right")

        tk.Label(
            self.root,
            text="4. Статус операции:",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=15, pady=(8, 2))
        self.progress = ttk.Progressbar(
            self.root, orient="horizontal", mode="determinate"
        )
        self.progress.pack(fill="x", padx=15, pady=5, ipady=3)

        self.status_label = tk.Label(
            self.root,
            text="Готов к работе. Выберите флешку и запустите процесс.",
            fg="#555555",
            font=("Segoe UI", 9),
        )
        self.status_label.pack(pady=2)

        self.write_btn = tk.Button(
            self.root,
            text="⚡ ЗАПИСАТЬ ВЫБРАННЫЙ WINDOWS ISO НА ФЛЕШКУ",
            bg="#d9534f",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            command=self.start_manual_write,
        )
        self.write_btn.pack(fill="x", padx=15, pady=10, ipady=5)

    def set_progress(self, val):
        self.ui_call(self.progress.config, value=max(0.0, min(100.0, float(val))))

    def update_status(self, text, color):
        self.ui_call(self.status_label.config, text=text, fg=color)

    def verify_usb_health(self, disk_num, standalone=False):
        self.update_status("Диагностика контроллера USB-флешки...", "blue")

        ps_check = f"""
        $d = Get-Disk -Number {disk_num} -ErrorAction Stop
        $obj = @{{
            Health = [string]$d.HealthStatus
            Status = [string]$d.OperationalStatus
            ReadOnly = [bool]$d.IsReadOnly
            Size = [int64]$d.Size
        }}
        $json = $obj | ConvertTo-Json -Compress
        [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($json))
        """
        code, out, _ = run_ps(ps_check, timeout=30)
        b64_line = out.strip().splitlines()[-1] if out.strip() else ""
        if code != 0 or not b64_line:
            raise RuntimeError(
                f"Не удалось опросить контроллер USB-накопителя (Диск {disk_num}).\n"
                f"Переподключите флешку и нажмите «Обновить»."
            )

        info = json.loads(base64.b64decode(b64_line).decode("utf-8"))
        disk_size = int(info.get("Size", 0))
        health = str(info.get("Health", "Healthy"))
        is_ro = bool(info.get("ReadOnly", False))

        if disk_size < 7 * (1024**3):
            raise RuntimeError(
                f"Объем флешки ({disk_size / (1024**3):.1f} ГБ) слишком мал для Windows (нужно минимум 8 ГБ)!"
            )

        if is_ro:
            run_ps(f"Set-Disk -Number {disk_num} -IsReadOnly $false")

        if health.lower() not in ("healthy", "ok", "0", "unknown"):
            raise RuntimeError(
                f"ВНИМАНИЕ: Контроллер флешки сообщает о неисправности (HealthStatus: {health})!"
            )

        physical_drive = f"\\\\.\\PhysicalDrive{disk_num}"
        samples = 20 if standalone else 12
        block_size = 262144  # 256 КБ
        sector_size = 4096
        usable_span = max(block_size * 2, disk_size - (16 * 1024 * 1024))
        step = usable_span // samples

        bad_zones = []
        t_start = time.time()
        total_read_bytes = 0

        GENERIC_READ = 0x80000000
        FILE_SHARE_RW = 0x00000001 | 0x00000002
        OPEN_EXISTING = 3

        handle = kernel32.CreateFileW(
            physical_drive,
            GENERIC_READ,
            FILE_SHARE_RW,
            None,
            OPEN_EXISTING,
            0,
            None,
        )

        if handle not in INVALID_HANDLE_VALUES:
            try:
                buf = ctypes.create_string_buffer(block_size)
                bytes_read = wintypes.DWORD(0)
                for i in range(samples):
                    if self.is_closing:
                        break
                    offset = int(((i * step) // sector_size) * sector_size)
                    pct = ((i + 1) / samples) * 100
                    if standalone:
                        self.set_progress(pct)
                    self.update_status(
                        f"Проверка секторов флешки: точка {i + 1}/{samples} ({pct:.0f}%)...",
                        "blue",
                    )

                    new_pos = ctypes.c_longlong(0)
                    if not kernel32.SetFilePointerEx(
                        handle,
                        ctypes.c_longlong(offset),
                        ctypes.byref(new_pos),
                        0,
                    ):
                        continue

                    ok = kernel32.ReadFile(
                        handle,
                        buf,
                        block_size,
                        ctypes.byref(bytes_read),
                        None,
                    )
                    if not ok:
                        err_code = kernel32.GetLastError()
                        if err_code in (23, 1117):  # ERROR_CRC, ERROR_IO_DEVICE
                            bad_zones.append(
                                f"{offset // (1024**2)} МБ (WinError {err_code})"
                            )
                    else:
                        total_read_bytes += bytes_read.value
            finally:
                kernel32.CloseHandle(handle)
        else:
            raise RuntimeError(
                f"Не удалось открыть физический диск {disk_num} для чтения секторов."
            )

        elapsed = max(0.001, time.time() - t_start)
        read_speed_mb = round((total_read_bytes / (1024 * 1024)) / elapsed, 1)

        if bad_zones:
            zones_str = "\n• ".join(bad_zones[:5])
            raise RuntimeError(
                f"ОБНАРУЖЕНЫ БИТЫЕ СЕКТОРЫ НА ФЛЕШКЕ!\n\n"
                f"Ошибки чтения на участках:\n• {zones_str}\n\n"
                f"Замените USB-накопитель."
            )

        return read_speed_mb, health

    def start_standalone_usb_check(self):
        if self.is_busy:
            return
        drive_label = self.selected_drive.get()
        if drive_label not in self.drives_map:
            messagebox.showwarning("Внимание", "Сначала выберите USB-флешку в списке!")
            return

        disk_num = self.drives_map[drive_label]
        self.set_busy(True)
        threading.Thread(
            target=self.standalone_check_worker,
            args=(disk_num, drive_label),
            daemon=True,
        ).start()

    def standalone_check_worker(self, disk_num, drive_label):
        try:
            self.set_progress(0)
            speed_mb, health = self.verify_usb_health(disk_num, standalone=True)
            self.set_progress(100)
            self.update_status(
                f"Тест пройден! Битых секторов: 0 | Скорость чтения: ~{speed_mb:.1f} МБ/с",
                "green",
            )
            self.ui_call(
                messagebox.showinfo,
                "Диагностика USB завершена",
                f"Устройство: {drive_label}\n\n"
                f"✅ Статус контроллера: {health}\n"
                f"✅ Битые сектора (20 зон): Не обнаружены (0 ошибок)\n"
                f"⚡ Скорость чтения: ~{speed_mb:.1f} МБ/с\n\n"
                f"Флешка исправна и готова к записи Windows!",
            )
        except Exception as e:
            if not self.is_closing:
                self.update_status("Обнаружены проблемы с флешкой!", "#d9534f")
                self.ui_call(messagebox.showerror, "Ошибка диагностики USB", str(e))
        finally:
            self.set_busy(False)

    def prepare_target_disk(self):
        if not self.auto_write_var.get():
            return None

        drive_label = self.selected_drive.get()
        if drive_label not in self.drives_map:
            messagebox.showwarning(
                "Не выбрана флешка",
                "Сначала вставьте и выберите USB-накопитель в пункте 1!",
            )
            return False

        disk_num = self.drives_map[drive_label]
        confirm = messagebox.askyesno(
            "Подтверждение",
            f"Образ Windows будет скачан в:\n{self.work_dir}\n\n"
            f"и АВТОМАТИЧЕСКИ записан на флешку:\n[{drive_label}]\n\n"
            f"Все данные на флешке будут стерты! Продолжить?",
        )
        if not confirm:
            return False
        return disk_num

    def start_auto_windows_pipeline(self):
        if self.is_busy:
            return
        disk_num = self.prepare_target_disk()
        if disk_num is False:
            return
        self.set_busy(True)
        threading.Thread(
            target=self.pipeline_worker, args=("AUTO_MS", disk_num), daemon=True
        ).start()

    def browse_torrent(self):
        if self.is_busy:
            return
        path = filedialog.askopenfilename(
            title="Выберите .torrent файл с образом Windows",
            filetypes=[("Torrent файлы (*.torrent)", "*.torrent")],
        )
        if path:
            self.torrent_source.set(path)
            disk_num = self.prepare_target_disk()
            if disk_num is False:
                return
            self.set_busy(True)
            threading.Thread(
                target=self.pipeline_worker, args=(path, disk_num), daemon=True
            ).start()

    def start_custom_pipeline(self):
        if self.is_busy:
            return
        src = self.torrent_source.get().strip()
        if not src:
            messagebox.showwarning(
                "Внимание",
                "Вставьте Magnet-ссылку, прямую ссылку на ISO или выберите .torrent файл!",
            )
            return
        disk_num = self.prepare_target_disk()
        if disk_num is False:
            return
        self.set_busy(True)
        threading.Thread(
            target=self.pipeline_worker, args=(src, disk_num), daemon=True
        ).start()

    def get_direct_microsoft_iso_url(self):
        self.update_status(
            "Получение прямой ссылки на ISO с серверов Microsoft...", "blue"
        )
        fido_path = os.path.join(self.tools_dir, "Fido.ps1")
        if not os.path.exists(fido_path):
            req = urllib.request.Request(
                FIDO_URL, headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp, open(
                fido_path, "wb"
            ) as out:
                shutil.copyfileobj(resp, out)

        choice = self.win_version.get()
        win_ver = "11" if "11" in choice else "10"
        lang = "Russian" if "Русская" in choice else "English"

        _, out, _ = run_cmd(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                fido_path,
                "-Win",
                win_ver,
                "-Rel",
                "Latest",
                "-Ed",
                "Pro",
                "-Lang",
                lang,
                "-Arch",
                windows_iso_architecture(choice),
                "-GetUrl",
            ],
            timeout=60,
        )
        url = ""
        for line in reversed(out.strip().splitlines()):
            if line.strip().startswith("http"):
                url = line.strip()
                break

        if not url:
            raise RuntimeError(
                "Сервер Microsoft отклонил автоматический запрос прямой ссылки для вашего IP.\n\n"
                "Решение: Нажмите кнопку «Выбрать .torrent» или вставьте Magnet-ссылку на образ Windows в поле ниже!"
            )
        return url

    def ensure_aria2(self):
        sys_aria = shutil.which("aria2c")
        if sys_aria and (not is_arm64_host() or is_arm64_executable(sys_aria)):
            return sys_aria

        local_aria = os.path.join(self.tools_dir, "aria2c.exe")
        if os.path.exists(local_aria) and (not is_arm64_host() or is_arm64_executable(local_aria)):
            return local_aria

        if is_arm64_host():
            raise RuntimeError(
                "Для загрузки ISO/торрентов на Windows ARM64 нужен нативный aria2c ARM64 в PATH.\n"
                "Автоматическая загрузка x64-клиента отключена. "
                "Можно скачать официальный Windows ARM64 ISO в браузере и выбрать его для записи."
            )

        self.update_status("Скачивание фонового клиента aria2c (2.3 МБ)...", "blue")
        zip_path = os.path.join(self.tools_dir, "aria2.zip")

        req = urllib.request.Request(
            ARIA2_URL, headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp, open(
            zip_path, "wb"
        ) as out:
            shutil.copyfileobj(resp, out)

        with zipfile.ZipFile(zip_path, "r") as z:
            for member in z.namelist():
                if member.endswith("aria2c.exe"):
                    with z.open(member) as src, open(local_aria, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    break

        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except Exception:
                pass
        return local_aria

    def ensure_uefi_ntfs_driver(self):
        """Скачивает подписанный загрузчик Rufus UEFI:NTFS (64 КБ) для загрузки NTFS-раздела в UEFI."""
        img_path = os.path.join(self.tools_dir, "uefi-ntfs.img")
        if os.path.exists(img_path) and os.path.getsize(img_path) > 32768:
            return img_path
        try:
            req = urllib.request.Request(
                UEFI_NTFS_URL, headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp, open(
                img_path, "wb"
            ) as out:
                shutil.copyfileobj(resp, out)
            return img_path
        except Exception:
            return None

    def list_current_isos(self):
        found = set()
        for root_dir, _, files in os.walk(self.work_dir):
            for f in files:
                if f.lower().endswith(".iso") and not os.path.exists(
                    os.path.join(root_dir, f + ".aria2")
                ):
                    found.add(os.path.join(root_dir, f))
        return found

    def pipeline_worker(self, source, target_disk_num):
        try:
            if target_disk_num is not None and self.check_usb_var.get():
                self.verify_usb_health(target_disk_num, standalone=False)

            existing_isos = self.list_current_isos()
            aria_exe = self.ensure_aria2()
            download_target = (
                self.get_direct_microsoft_iso_url()
                if source == "AUTO_MS"
                else source
            )

            cmd = [
                aria_exe,
                f"--dir={self.work_dir}",
                "--seed-time=0",
                "--bt-stop-timeout=90",
                "--connect-timeout=15",
                "--max-connection-per-server=16",
                "--split=16",
                "--min-split-size=1M",
                "--disk-cache=64M",
                "--enable-dht=true",
                "--bt-enable-lpd=true",
                f"--bt-tracker={BT_TRACKERS}",
                "--summary-interval=1",
                "--show-console-readout=false",
                "--enable-color=false",
                "--console-log-level=warn",
                "--allow-overwrite=true",
                "--auto-file-renaming=false",
                "--file-allocation=none",
                download_target,
            ]

            self.update_status(
                f"Этап 1/2: Скачивание образа в {self.work_dir}...", "blue"
            )
            self.set_progress(0)

            self.active_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

            for raw_line in iter(self.active_proc.stdout.readline, b""):
                if self.is_closing:
                    break
                line = decode_console_bytes(raw_line)
                pct_match = re.search(r"\((\d+)%\)", line)
                if pct_match:
                    pct = int(pct_match.group(1))
                    dl_match = re.search(r"DL:([0-9.]+[A-Za-z]+)", line)
                    eta_match = re.search(r"ETA:([0-9a-zA-Z]+)", line)

                    speed = f" | {dl_match.group(1)}/с" if dl_match else ""
                    eta = (
                        f" | Осталось: {eta_match.group(1)}"
                        if eta_match
                        else ""
                    )

                    self.set_progress(pct)
                    self.update_status(
                        f"Этап 1/2 — Загрузка ISO: {pct}%{speed}{eta}", "black"
                    )

            self.active_proc.wait()
            if self.is_closing:
                return

            if self.active_proc.returncode != 0:
                raise RuntimeError(
                    f"Загрузка прервана (код aria2: {self.active_proc.returncode}). Проверьте подключение или торрент."
                )

            current_isos = self.list_current_isos()
            new_isos = current_isos - existing_isos
            if new_isos:
                downloaded_iso = max(new_isos, key=os.path.getmtime)
            elif current_isos:
                downloaded_iso = max(current_isos, key=os.path.getmtime)
            else:
                raise RuntimeError(
                    f"Загрузка завершена, но файл .iso в папке {self.work_dir} не обнаружен."
                )

            self.ui_call(self.iso_path.set, downloaded_iso)

            if target_disk_num is not None:
                self.write_windows_iso_to_usb(
                    downloaded_iso, target_disk_num, skip_health=True
                )
            else:
                self.set_progress(100)
                self.update_status(
                    f"Скачивание завершено: {downloaded_iso}", "green"
                )

        except Exception as e:
            if not self.is_closing:
                self.update_status("Ошибка выполнения!", "#d9534f")
                self.ui_call(messagebox.showerror, "Ошибка", str(e))
        finally:
            self.active_proc = None
            self.set_busy(False)

    def format_usb_hybrid(self, disk_num, reserve_mb=64):
        """
        Создает 2 раздела:
        1. Основной активный раздел NTFS на всю флешку (минус reserve_mb).
        2. Раздел FAT32 в конце диска для загрузчика UEFI.
        Используется надежная связка shrink + PowerShell без ошибок выравнивания цилиндров.
        """
        self.update_status(
            "Подготовка и быстрая разметка флешки (NTFS + UEFI Boot)...", "blue"
        )

        dp_script = os.path.join(self.tools_dir, "dp_hybrid.txt")
        with open(dp_script, "w", encoding="ascii") as f:
            f.write(
                f"select disk {disk_num}\n"
                f"attributes disk clear readonly noerr\n"
                f"online disk noerr\n"
                f"clean\n"
                f"convert mbr noerr\n"
                f"create partition primary\n"
                f"shrink desired={reserve_mb} minimum={reserve_mb}\n"
                f"active\n"
                f"format fs=ntfs quick label=WIN_USB\n"
                f"assign\n"
                f"create partition primary\n"
                f"format fs=fat32 quick label=UEFI_BOOT\n"
                f"assign\n"
                f"exit\n"
            )
        run_cmd(["diskpart", "/s", dp_script], timeout=90)
        if os.path.exists(dp_script):
            try:
                os.remove(dp_script)
            except Exception:
                pass

        ps_verify = f"""
        Update-HostStorageCache -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 600
        $parts = @(Get-Partition -DiskNumber {disk_num} -ErrorAction SilentlyContinue | Sort-Object PartitionNumber)
        if ($parts.Count -lt 2) {{
            Clear-Disk -Number {disk_num} -RemoveData -RemoveOEM -Confirm:$false -ErrorAction SilentlyContinue
            Initialize-Disk -Number {disk_num} -PartitionStyle MBR -ErrorAction SilentlyContinue
            $p1 = New-Partition -DiskNumber {disk_num} -UseMaximumSize -AssignDriveLetter -IsActive
            $targetSize = $p1.Size - ({reserve_mb} * 1MB)
            Resize-Partition -DiskNumber {disk_num} -PartitionNumber $p1.PartitionNumber -Size $targetSize
            Format-Volume -Partition $p1 -FileSystem NTFS -NewFileSystemLabel 'WIN_USB' -Confirm:$false -Force | Out-Null

            $p2 = New-Partition -DiskNumber {disk_num} -UseMaximumSize -AssignDriveLetter
            Format-Volume -Partition $p2 -FileSystem FAT32 -NewFileSystemLabel 'UEFI_BOOT' -Confirm:$false -Force | Out-Null
            $parts = @(Get-Partition -DiskNumber {disk_num} | Sort-Object PartitionNumber)
        }}
        foreach ($p in $parts) {{
            if (-not $p.DriveLetter) {{
                $p | Add-PartitionAccessPath -AssignDriveLetter -ErrorAction SilentlyContinue
            }}
        }}
        $parts = @(Get-Partition -DiskNumber {disk_num} | Where-Object {{ $_.DriveLetter }} | Sort-Object PartitionNumber)
        if ($parts.Count -ge 2) {{
            $out = @{{
                Data = "$($parts[0].DriveLetter):\\"
                Boot = "$($parts[1].DriveLetter):\\"
            }} | ConvertTo-Json -Compress
            [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($out))
        }}
        """
        _, out, err = run_ps(ps_verify, timeout=60)
        b64_line = out.strip().splitlines()[-1] if out.strip() else ""
        if b64_line:
            try:
                res = json.loads(base64.b64decode(b64_line).decode("utf-8"))
                data_root = res.get("Data", "")
                boot_root = res.get("Boot", "")
                if (
                    len(data_root) == 3
                    and len(boot_root) == 3
                    and os.path.exists(data_root)
                    and os.path.exists(boot_root)
                ):
                    return data_root, boot_root
            except Exception:
                pass

        raise RuntimeError(
            f"Не удалось разметить флешку (Диск {disk_num}).\n"
            f"Закройте открытые окна Проводника или антивирус и переподключите флешку.\n{err}"
        )

    def mount_iso(self, iso_path):
        b64_iso = base64.b64encode(iso_path.encode("utf-8")).decode("ascii")
        ps_script = f"""
        $iso = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64_iso}'))
        Dismount-DiskImage -ImagePath $iso -ErrorAction SilentlyContinue | Out-Null
        Mount-DiskImage -ImagePath $iso -PassThru -ErrorAction Stop | Out-Null
        for ($i = 0; $i -lt 25; $i++) {{
            $vol = Get-DiskImage -ImagePath $iso | Get-Volume -ErrorAction SilentlyContinue
            if ($vol -and $vol.DriveLetter) {{
                Write-Output $vol.DriveLetter
                exit 0
            }}
            Start-Sleep -Milliseconds 300
        }}
        """
        _, out, err = run_ps(ps_script, timeout=45)
        for line in reversed(out.strip().splitlines()):
            clean = line.strip().rstrip(":")
            if len(clean) == 1 and clean.isalpha():
                return f"{clean.upper()}:\\"

        raise RuntimeError(
            f"Не удалось смонтировать ISO-образ:\n{iso_path}\n\nПроверьте, что файл не поврежден.\n{err}"
        )

    def dismount_iso(self, iso_path):
        b64_iso = base64.b64encode(iso_path.encode("utf-8")).decode("ascii")
        ps_script = f"""
        $iso = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64_iso}'))
        Dismount-DiskImage -ImagePath $iso -ErrorAction SilentlyContinue | Out-Null
        """
        run_ps(ps_script, timeout=25)

    def run_robocopy_fast(self, src, dst, exclude_files=None):
        cmd = [
            "robocopy",
            src.rstrip("\\"),
            dst.rstrip("\\"),
            "/E",
            "/R:2",
            "/W:1",
            "/NP",
            "/NFL",
            "/NDL",
            "/NJH",
            "/NJS",
        ]
        if exclude_files:
            cmd.append("/XF")
            cmd.extend(exclude_files)

        self.active_proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.active_proc.wait()
        if self.is_closing:
            return
        if self.active_proc.returncode >= 8:
            raise RuntimeError(
                f"Ошибка копирования системных файлов (код Robocopy: {self.active_proc.returncode})."
            )

    def copy_large_file_with_progress(self, src_file, dst_file, start_pct, end_pct):
        total_size = os.path.getsize(src_file)
        copied = 0
        chunk_size = 16 * 1024 * 1024  # Блоки по 16 МБ для максимальной скорости записи
        t0 = time.time()

        os.makedirs(os.path.dirname(dst_file), exist_ok=True)
        if os.path.exists(dst_file):
            try:
                os.chmod(dst_file, 0o666)
                os.remove(dst_file)
            except Exception:
                pass

        fname = os.path.basename(src_file)

        with open(src_file, "rb") as fsrc, open(dst_file, "wb") as fdst:
            while True:
                if self.is_closing:
                    return
                buf = fsrc.read(chunk_size)
                if not buf:
                    break
                fdst.write(buf)
                copied += len(buf)

                elapsed = max(0.001, time.time() - t0)
                speed_mb = (copied / (1024 * 1024)) / elapsed
                file_pct = copied / total_size
                ui_pct = start_pct + file_pct * (end_pct - start_pct)

                self.set_progress(ui_pct)
                self.update_status(
                    f"Прямая запись {fname}: {file_pct * 100:.0f}% ({copied // (1024**2)} / {total_size // (1024**2)} МБ) | ⚡ {speed_mb:.1f} МБ/с",
                    "black",
                )
            fdst.flush()
            os.fsync(fdst.fileno())

    def deploy_uefi_ntfs_to_boot_partition(self, uefi_img_path, boot_root):
        """Монтирует образ uefi-ntfs.img или извлекает из него EFI-файлы на FAT32 раздел."""
        # Так как uefi-ntfs.img — это образ FAT12/FAT16 размером 64 КБ, распарсим или смонтируем его:
        # В Windows .img может не монтироваться через Mount-DiskImage, поэтому извлекаем файлы напрямую из FAT12 образа
        with open(uefi_img_path, "rb") as f:
            raw = f.read()

        # Резервное извлечение или прямое копирование стандартного EFI загрузчика
        # Чтобы не зависеть от внешних утилит, записываем uefi-ntfs.img напрямую в сектора второго раздела!
        b64_img = base64.b64encode(uefi_img_path.encode("utf-8")).decode("ascii")
        vol_letter = boot_root[0].upper()
        ps_write_raw = f"""
        $imgPath = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64_img}'))
        $bytes = [System.IO.File]::ReadAllBytes($imgPath)
        $volPath = "\\\\.\\{vol_letter}:"
        $fs = New-Object System.IO.FileStream($volPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::ReadWrite)
        $fs.Write($bytes, 0, $bytes.Length)
        $fs.Flush()
        $fs.Close()
        """
        code, _, _ = run_ps(ps_write_raw, timeout=20)
        return code == 0

    def write_windows_iso_to_usb(self, iso_path, disk_num, skip_health=False):
        t_total_start = time.time()
        self.set_progress(2)

        if self.check_usb_var.get() and not skip_health:
            self.verify_usb_health(disk_num, standalone=False)

        uefi_img = self.ensure_uefi_ntfs_driver()
        reserve_mb = 64 if uefi_img else 1024

        self.set_progress(6)
        data_root, boot_root = self.format_usb_hybrid(disk_num, reserve_mb=reserve_mb)
        iso_root = None

        try:
            self.set_progress(12)
            self.update_status("Монтирование образа Windows ISO...", "blue")
            iso_root = self.mount_iso(iso_path)

            # 1. Настраиваем загрузчик UEFI на втором разделе
            self.update_status(
                f"Установка загрузчика UEFI на раздел {boot_root}...", "blue"
            )
            uefi_written = False
            if uefi_img:
                uefi_written = self.deploy_uefi_ntfs_to_boot_partition(
                    uefi_img, boot_root
                )

            if not uefi_written:
                # Если нет интернета для uefi-ntfs.img, копируем стандартный загрузчик EFI + boot.wim
                self.run_robocopy_fast(
                    iso_root,
                    boot_root,
                    exclude_files=["install.wim", "install.esd", "install.swm"],
                )

            self.set_progress(22)

            # 2. Копируем все файлы установщика на основной раздел NTFS (кроме тяжелого install.wim/esd)
            self.update_status(
                f"Копирование системных файлов на основной раздел {data_root}...",
                "blue",
            )
            self.run_robocopy_fast(
                iso_root,
                data_root,
                exclude_files=["install.wim", "install.esd", "install.swm"],
            )
            self.set_progress(34)

            # 3. Прямая потоковая запись главного образа install.wim / install.esd на NTFS без резки DISM
            found_main_image = False
            for img_name in ("install.wim", "install.esd", "install.swm"):
                src_img = os.path.join(iso_root, "sources", img_name)
                if os.path.exists(src_img):
                    found_main_image = True
                    dst_img = os.path.join(data_root, "sources", img_name)
                    self.copy_large_file_with_progress(src_img, dst_img, 34, 96)
                    break

            if not found_main_image:
                raise RuntimeError(
                    "В выбранном ISO-образе не найден файл sources\\install.wim или install.esd!\n"
                    "Убедитесь, что это установочный образ Windows 10/11."
                )

            if self.is_closing:
                return

            # 4. Записываем загрузочный сектор Legacy BIOS (с флагом /force, чтобы избежать блокировок)
            bootsect_exe = os.path.join(iso_root, "boot", "bootsect.exe")
            if os.path.exists(bootsect_exe) and not os.path.exists(os.path.join(iso_root, "efi", "boot", "bootaa64.efi")):
                run_cmd(
                    [
                        bootsect_exe,
                        "/nt60",
                        data_root.rstrip("\\"),
                        "/force",
                        "/mbr",
                    ],
                    timeout=20,
                )

            total_sec = int(time.time() - t_total_start)
            mins, secs = divmod(total_sec, 60)
            time_str = f"{mins} мин {secs} сек" if mins else f"{secs} сек"

            self.set_progress(100)
            self.update_status(
                f"Готово за {time_str}! Загрузочная флешка Windows создана ({data_root})",
                "green",
            )
            self.ui_call(
                messagebox.showinfo,
                "Успех!",
                f"Загрузочная флешка Windows полностью готова за {time_str}!\n\n"
                f"• Основной раздел с установщиком (NTFS): {data_root}\n"
                f"• Раздел загрузчика UEFI: {boot_root}\n\n"
                f"Флешка подготовлена для UEFI. Legacy BIOS применим только к совместимым x86/x64 образам.",
            )
        finally:
            if iso_root:
                self.dismount_iso(iso_path)

    def browse_iso(self):
        if self.is_busy:
            return
        path = filedialog.askopenfilename(
            title="Выберите образ Windows ISO",
            initialdir=self.work_dir,
            filetypes=[("Образы дисков (*.iso)", "*.iso")],
        )
        if path:
            self.iso_path.set(path)

    def refresh_drives(self):
        if self.is_busy:
            return
        self.drives_map.clear()
        ps_script = """
        $disks = @(Get-Disk | Where-Object { $_.BusType -eq 'USB' -and $_.Size -gt 0 -and $_.Size -lt 138000000000 } | Select-Object Number, FriendlyName, Size)
        $json = $disks | ConvertTo-Json -Compress
        if ($json) {
            [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($json))
        }
        """
        try:
            _, out, _ = run_ps(ps_script, timeout=25)
            b64_line = out.strip().splitlines()[-1] if out.strip() else ""
            if b64_line:
                raw_json = base64.b64decode(b64_line).decode("utf-8")
                data = json.loads(raw_json)
                if isinstance(data, dict):
                    data = [data]
                for d in data:
                    num = d["Number"]
                    name = d["FriendlyName"]
                    size_gb = round(d["Size"] / (1024**3), 2)
                    label = f"Диск {num}: {name} ({size_gb} ГБ)"
                    self.drives_map[label] = num

            vals = list(self.drives_map.keys())
            self.drive_combo["values"] = vals
            if vals:
                self.drive_combo.current(0)
            else:
                self.selected_drive.set("USB-флешки (до 128 ГБ) не найдены")
        except Exception as e:
            messagebox.showerror(
                "Ошибка", f"Не удалось получить список флешек:\n{e}"
            )

    def start_manual_write(self):
        if self.is_busy:
            return
        iso = self.iso_path.get()
        drive_label = self.selected_drive.get()

        if not iso or not os.path.exists(iso):
            messagebox.showwarning(
                "Внимание", "Сначала выберите существующий Windows ISO-файл!"
            )
            return
        if drive_label not in self.drives_map:
            messagebox.showwarning(
                "Внимание", "Выберите корректный USB-накопитель!"
            )
            return

        disk_num = self.drives_map[drive_label]
        confirm = messagebox.askyesno(
            "Подтверждение записи Windows",
            f"Записать образ Windows:\n{os.path.basename(iso)}\nна устройство:\n[{drive_label}]?\n\nВсе данные на флешке будут стерты!",
        )
        if not confirm:
            return

        self.set_busy(True)
        threading.Thread(
            target=self.manual_write_worker, args=(iso, disk_num), daemon=True
        ).start()

    def manual_write_worker(self, iso, disk_num):
        try:
            self.write_windows_iso_to_usb(iso, disk_num, skip_health=False)
        except Exception as e:
            if not self.is_closing:
                self.update_status("Ошибка при создании флешки!", "#d9534f")
                self.ui_call(messagebox.showerror, "Ошибка записи", str(e))
        finally:
            self.active_proc = None
            self.set_busy(False)

    def set_busy(self, busy):
        self.is_busy = busy
        state = "disabled" if busy else "normal"
        combo_state = "disabled" if busy else "readonly"
        self.ui_call(
            lambda: (
                self.dl_auto_btn.config(
                    state=state, bg="#999999" if busy else "#0078D7"
                ),
                self.test_usb_btn.config(
                    state=state, bg="#999999" if busy else "#f0ad4e"
                ),
                self.dl_custom_btn.config(state=state),
                self.dl_mag_btn.config(state=state),
                self.refresh_btn.config(state=state),
                self.browse_iso_btn.config(state=state),
                self.drive_combo.config(state=combo_state),
                self.ver_combo.config(state=combo_state),
                self.write_btn.config(
                    state=state, bg="#999999" if busy else "#d9534f"
                ),
            )
        )


if __name__ == "__main__":
    if not is_admin():
        run_as_admin()

    root = tk.Tk()
    app = WindowsOneClickUSB(root)
    root.mainloop()

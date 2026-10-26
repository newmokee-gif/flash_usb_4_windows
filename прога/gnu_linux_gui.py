"""GNU/Linux selection, verified download and raw USB-writing front end."""
from __future__ import annotations

import json
import os
import platform
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import distro_catalog
from image_download import (Cancelled, DownloadError, download_image, runtime_environment, value,
                            find_aria2, windows_arm64, ensure_windows_aria2)
import raw_usb


def build_write_command(image, device, confirmation, digest):
    arguments = ["write", str(Path(image).resolve()), device.path, "--confirm", confirmation,
                 "--expected-identity", json.dumps(device.identity), "--sha256", digest]
    if getattr(sys, "frozen", False):
        appimage = os.environ.get("APPIMAGE") if sys.platform.startswith("linux") else None
        if appimage:
            path = Path(appimage)
            if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
                raise RuntimeError("Исходный AppImage недоступен. Сохраните его и разрешите выполнение.")
            return [str(path), "--appimage-extract-and-run", "--gnu-linux", *arguments]
        return [sys.executable, "--gnu-linux", *arguments]
    return [sys.executable, "-u", str(Path(__file__).with_name("raw_usb.py")), *arguments]


def elevated_command(command):
    if sys.platform == "darwin":
        from macos_gui import elevation_command
        return elevation_command(command)
    if sys.platform.startswith("linux") and os.geteuid() != 0:
        pkexec = shutil.which("pkexec", path="/usr/sbin:/usr/bin:/sbin:/bin")
        if not pkexec:
            raise RuntimeError("Для графического запроса прав установите pkexec/polkit или запустите CLI через sudo.")
        return [pkexec, *command]
    return command


class GNULinuxUSB:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.cancelled = threading.Event()
        self.operation = None
        self.devices = []
        self.images = list(distro_catalog.CATALOG)
        self.matches = []
        self.destination = None
        self.aria_emulation_allowed = False
        from package_manager_ui import add_dependency_button
        if sys.platform.startswith("linux"):
            self.install_button = add_dependency_button(root, self.install_dependencies)
        if sys.platform in ("win32", "darwin"):
            from package_manager_ui import add_buttons
            add_buttons(root, busy=lambda: self.operation is not None)
        root.title("WinUSB Creator — ГНУ/Линукс")
        root.geometry("900x760")
        root.minsize(740, 650)
        root.protocol("WM_DELETE_WINDOW", self.close)
        panel = ttk.Frame(root, padding=16)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="ГНУ/Линукс → USB", font=("sans", 17)).pack(anchor="w")
        ttk.Label(panel, text="Поблочная запись гибридного ISO/IMG. Все данные выбранной флешки будут удалены.",
                  wraplength=840).pack(anchor="w", pady=(4, 12))
        form = ttk.Frame(panel)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        self.distro = ttk.Combobox(form, state="readonly", values=sorted({value(i, "distro") for i in self.images}))
        self.arch = ttk.Combobox(form, state="readonly")
        self.edition = ttk.Combobox(form, state="readonly")
        for row, (label, widget) in enumerate((("Дистрибутив", self.distro), ("Архитектура целевого компьютера", self.arch), ("Редакция / выпуск", self.edition))):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            widget.grid(row=row, column=1, sticky="ew", pady=4)
        self.distro.bind("<<ComboboxSelected>>", self.select_distro)
        self.arch.bind("<<ComboboxSelected>>", self.select_arch)
        self.download_button = ttk.Button(panel, text="Скачать и проверить образ", command=self.download)
        self.download_button.pack(anchor="w", pady=8)
        self.auto_write = tk.BooleanVar(value=False)
        self.auto_check = ttk.Checkbutton(panel, text="После загрузки предложить запись (с подтверждением USB)", variable=self.auto_write)
        self.auto_check.pack(anchor="w")
        self.image_path = tk.StringVar()
        self.sha256 = tk.StringVar()
        self.image_entry = ttk.Entry(panel, textvariable=self.image_path)
        self.image_entry.pack(fill="x", pady=(12, 4))
        self.local_button = ttk.Button(panel, text="Выбрать локальный ISO / IMG…", command=self.choose_local)
        self.local_button.pack(anchor="w")
        ttk.Label(panel, text="Ожидаемый SHA-256 из официального источника (загрузка заполняет автоматически):").pack(anchor="w", pady=(8, 2))
        self.hash_entry = ttk.Entry(panel, textvariable=self.sha256)
        self.hash_entry.pack(fill="x")
        self.usb = ttk.Combobox(panel, state="readonly")
        self.usb.pack(fill="x", pady=(12, 4))
        controls = ttk.Frame(panel)
        controls.pack(fill="x")
        self.refresh_button = ttk.Button(controls, text="Обновить USB", command=self.refresh)
        self.refresh_button.pack(side="left")
        self.write_button = ttk.Button(controls, text="Записать и проверить USB", command=self.write)
        self.write_button.pack(side="left", padx=8)
        self.cancel_button = ttk.Button(controls, text="Отменить загрузку", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left")
        if not sys.platform.startswith("linux"):
            self.install_button = ttk.Button(panel, text="Установить зависимости программы",
                                             command=self.install_dependencies, style="Setup.TButton")
            self.install_button.pack(fill="x", pady=(8, 0))
        self.log = tk.Text(panel, height=10, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=(12, 0))
        self.controls = [self.download_button, self.auto_check, self.local_button, self.image_entry,
                         self.hash_entry, self.refresh_button, self.write_button, self.install_button]
        if self.distro["values"]:
            self.distro.current(0)
            self.select_distro()
        self.append("ARM64 показывается только при наличии образа в каталоге. Образы для конкретной платы не равны универсальным ARM64 ISO.")
        if sys.platform == "win32":
            self.append("Windows: перед записью уберите буквы дисков и точки подключения всех разделов флешки через «Управление дисками» → «Изменить букву диска или путь». Не удаляйте разделы и не форматируйте диск. Затем нажмите «Обновить USB».")
        self.root.after(100, self.poll)
        self.refresh()

    def append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", str(text) + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def select_distro(self, _event=None):
        available = sorted({value(i, "architecture") for i in self.images if value(i, "distro") == self.distro.get()})
        self.arch["values"] = available
        if available:
            self.arch.current(0)
        self.select_arch()

    def select_arch(self, _event=None):
        self.matches = [i for i in self.images if value(i, "distro") == self.distro.get() and value(i, "architecture") == self.arch.get()]
        self.edition["values"] = [value(i, "edition") for i in self.matches]
        if self.matches:
            self.edition.current(0)

    def set_operation(self, operation):
        self.operation = operation
        for widget in self.controls:
            widget.configure(state="disabled" if operation else "normal")
        for widget in (self.distro, self.arch, self.edition, self.usb):
            widget.configure(state="disabled" if operation else "readonly")
        self.cancel_button.configure(state="normal" if operation == "download" else "disabled")

    def refresh(self):
        if self.operation:
            return
        self.set_operation("refresh")
        def worker():
            try:
                self.events.put(("devices", raw_usb.list_devices()))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def choose_local(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("Linux images", "*.iso *.img")])
        if path:
            self.image_path.set(path)
            self.sha256.set("")
            self.append("Локальный образ: введите ожидаемый SHA-256 с официальной страницы дистрибутива. Архитектуру и совместимость платы проверьте перед записью.")

    def download(self):
        if self.operation:
            return
        index = self.edition.current()
        if not 0 <= index < len(self.matches):
            messagebox.showerror("Образ", "Выберите дистрибутив, архитектуру и редакцию.", parent=self.root)
            return
        destination = filedialog.askdirectory(parent=self.root, title="Папка для проверенного образа")
        if not destination:
            return
        image = self.matches[index]
        allow_emulation = self.confirm_aria_emulation()
        self.cancelled.clear()
        self.set_operation("download")
        self.append(f"Выбрано: {value(image, 'distro')} / {value(image, 'edition')} / {value(image, 'architecture')}")
        def worker():
            try:
                # Resolution, if needed, runs off the Tk thread as well.
                resolver = getattr(distro_catalog, "resolve_image", None)
                resolved = resolver(image) if resolver else image
                result = download_image(resolved, destination, cancelled=self.cancelled,
                                        log=lambda line: self.events.put(("log", line)),
                                        provision_aria=True, allow_emulated_aria=allow_emulation)
                self.events.put(("downloaded", result))
            except Cancelled as exc:
                self.events.put(("cancelled", str(exc)))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=False).start()

    def confirm_aria_emulation(self):
        if sys.platform != "win32" or not windows_arm64() or find_aria2():
            return False
        if self.aria_emulation_allowed:
            return True
        self.aria_emulation_allowed = messagebox.askyesno("Torrent-помощник для Windows ARM64",
            "Нативный aria2 ARM64 не найден. Разрешить проверенный официальный aria2 x64 "
            "через эмуляцию Windows 11 ARM64? Само приложение остаётся ARM64. "
            "При отказе загрузка выполнится по HTTPS с сервера дистрибутива.", parent=self.root)
        return self.aria_emulation_allowed

    def install_dependencies(self):
        if self.operation:
            return
        allow_emulation = self.confirm_aria_emulation()
        commands = []
        try:
            if sys.platform != "win32":
                from dependencies import commands as dependency_commands
                system = platform.system()
                distro = platform.freedesktop_os_release().get("ID", "") if system == "Linux" else ""
                if system == "Darwin" and os.geteuid() == 0:
                    raise RuntimeError("Homebrew нужно запускать от обычного пользователя, без sudo.")
                for command in dependency_commands(system, distro, platform.machine()):
                    executable = shutil.which(command[0], path="/usr/sbin:/usr/bin:/sbin:/bin:/opt/homebrew/bin:/usr/local/bin")
                    if not executable:
                        raise RuntimeError(f"Не найден {command[0]}. Для macOS сначала установите Homebrew кнопкой вверху.")
                    command = [executable, *command[1:]]
                    if system == "Linux" and os.geteuid() != 0:
                        pkexec = shutil.which("pkexec", path="/usr/bin:/bin")
                        if not pkexec:
                            raise RuntimeError("Не найден pkexec. Установите зависимости командой install-deps из терминала через sudo.")
                        command.insert(0, pkexec)
                    # pacman must not wait for a terminal response in GUI. The
                    # complete system upgrade is confirmed explicitly below.
                    if command[0].endswith("pacman") or any(part.endswith("/pacman") for part in command):
                        command.append("--noconfirm")
                    commands.append(command)
        except Exception as exc:
            messagebox.showerror("Зависимости", str(exc), parent=self.root)
            return
        description = "Будет установлен проверенный официальный aria2 из GitHub." if sys.platform == "win32" else "Будут установлены зависимости программы:\n" + "\n".join(" ".join(command) for command in commands)
        if sys.platform.startswith("linux") and any("-Syu" in command for command in commands):
            description += "\nArch: это также полное обновление системы."
        if not messagebox.askyesno("Установка зависимостей", description + "\nПродолжить?", parent=self.root):
            return
        self.set_operation("dependencies")
        self.cancelled.clear()
        def worker():
            try:
                if sys.platform == "win32":
                    ensure_windows_aria2(log=lambda line: self.events.put(("log", line)),
                                         cancelled=self.cancelled, allow_emulation=allow_emulation)
                else:
                    for command in commands:
                        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                              text=True, encoding="utf-8", errors="replace", env=runtime_environment()) as process:
                            for line in process.stdout:
                                self.events.put(("log", line.rstrip()))
                            if process.wait():
                                raise RuntimeError("Пакетный менеджер завершился с ошибкой. Подробности в журнале.")
                self.events.put(("dependencies", None))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=False).start()

    def cancel(self):
        if self.operation == "download":
            self.cancelled.set()
            self.cancel_button.configure(state="disabled")
            self.append("Отмена загрузки… ожидается завершение текущего запроса к серверу.")

    def write(self):
        if self.operation:
            return
        image = Path(self.image_path.get()).expanduser()
        digest = self.sha256.get().strip().lower()
        index = self.usb.current()
        if not self.image_path.get() or not image.is_file() or image.suffix.lower() not in (".iso", ".img"):
            messagebox.showerror("Образ", "Выберите несжатый гибридный ISO/IMG.", parent=self.root)
            return
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            messagebox.showerror("SHA-256", "Нужен ожидаемый SHA-256 из официального источника. Он проверяется до стирания USB.", parent=self.root)
            return
        if not 0 <= index < len(self.devices):
            messagebox.showerror("USB", "Выберите доступный внешний USB и размонтируйте его разделы.", parent=self.root)
            return
        device = self.devices[index]
        confirmation = simpledialog.askstring("Подтверждение стирания USB",
            f"Образ: {image.name}\nВсе данные будут удалены:\n{device.label}\n\nВведите точный путь {device.path}:", parent=self.root)
        if confirmation != device.path:
            return
        try:
            command = elevated_command(build_write_command(image, device, confirmation, digest))
        except Exception as exc:
            messagebox.showerror("Запуск", str(exc), parent=self.root)
            return
        self.set_operation("write")
        self.append("Проверка образа и USB; затем поблочная запись и полное повторное чтение. Не отключайте накопитель.")
        def worker():
            try:
                with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, encoding="utf-8", errors="replace", env=runtime_environment(restart=True)) as process:
                    for line in process.stdout:
                        self.events.put(("log", line.rstrip()))
                    self.events.put(("written", process.wait()))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=False).start()

    def poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.append(payload)
                    continue
                self.set_operation(None)
                if kind == "devices":
                    self.devices = [d for d in payload if d.safe]
                    self.usb["values"] = [d.label for d in self.devices]
                    self.usb.set("")
                    if self.devices:
                        self.usb.current(0)
                    for device in payload:
                        if not device.safe:
                            self.append(f"Недоступно: {device.path}: {device.reason}")
                    if not self.devices:
                        self.append("Нет доступных USB. Размонтируйте разделы и обновите список.")
                elif kind == "downloaded":
                    self.image_path.set(str(payload.path))
                    self.sha256.set(payload.sha256)
                    self.append("Загрузка завершена; контрольная сумма совпала.")
                    if self.auto_write.get():
                        self.root.after_idle(self.write)
                elif kind == "written":
                    if payload == 0:
                        messagebox.showinfo("Готово", "Образ записан, полное повторное чтение прошло проверку.", parent=self.root)
                    else:
                        messagebox.showerror("Запись не завершена", "Подробности в журнале. При прерывании USB может быть записан частично.", parent=self.root)
                    self.refresh()
                elif kind == "cancelled":
                    self.append(payload)
                elif kind == "dependencies":
                    self.append("Установка зависимостей завершена. Теперь можно повторить загрузку через torrent.")
                    messagebox.showinfo("Зависимости", "Установка завершена.", parent=self.root)
                else:
                    self.append(payload)
                    messagebox.showerror("Ошибка", payload, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def close(self):
        if self.operation:
            messagebox.showinfo("Операция выполняется", "Для загрузки нажмите «Отменить» и дождитесь завершения. Запись USB нельзя прерывать закрытием окна.", parent=self.root)
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    GNULinuxUSB(root)
    root.mainloop()


if __name__ == "__main__":
    main()

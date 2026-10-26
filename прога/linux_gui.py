"""Linux front end. All Tk calls stay on the main thread."""
import os
import json
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from linux_usb import USBError, check_dependencies, list_devices


class LinuxUSB:
    def __init__(self, root):
        self.root = root
        self.busy = False
        self.events = queue.Queue()
        self.devices = []
        root.title("WinUSB Creator — Linux / UEFI")
        root.geometry("800x560")
        root.protocol("WM_DELETE_WINDOW", self.close)
        panel = ttk.Frame(root, padding=16)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Windows ISO → USB (UEFI, FAT32)", font=("sans", 16)).pack(anchor="w")
        ttk.Label(panel, text="Все данные выбранного USB будут удалены. Legacy BIOS не поддерживается.").pack(anchor="w", pady=8)
        self.iso = tk.StringVar()
        self.entry = ttk.Entry(panel, textvariable=self.iso)
        self.entry.pack(fill="x")
        self.browse = ttk.Button(panel, text="Выбрать ISO…", command=self.choose_iso)
        self.browse.pack(anchor="w", pady=8)
        self.combo = ttk.Combobox(panel, state="readonly")
        self.combo.pack(fill="x")
        self.refresh_button = ttk.Button(panel, text="Обновить USB", command=self.refresh)
        self.refresh_button.pack(anchor="w", pady=8)
        self.write_button = ttk.Button(panel, text="Записать и проверить", command=self.write)
        self.write_button.pack(anchor="w")
        self.log = tk.Text(panel, height=15, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=12)
        self.refresh()
        root.after(100, self.poll)

    def append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def choose_iso(self):
        path = filedialog.askopenfilename(filetypes=[("ISO", "*.iso"), ("All files", "*")])
        if path:
            self.iso.set(path)

    def refresh(self):
        try:
            all_devices = list_devices()
            self.devices = [device for device in all_devices if device.safe]
            self.combo["values"] = [device.label for device in self.devices]
            self.combo.set("")
            if self.devices:
                self.combo.current(0)
            for device in all_devices:
                if not device.safe:
                    self.append(f"Недоступно: {device.path}: {device.reason}")
            if not self.devices:
                self.append("Нет доступных USB. Размонтируйте разделы флешки и обновите список.")
        except (USBError, OSError) as exc:
            self.append(str(exc))

    def write(self):
        index = self.combo.current()
        if not 0 <= index < len(self.devices):
            messagebox.showerror("USB", "Выберите доступный USB-накопитель.")
            return
        iso = Path(self.iso.get()).expanduser()
        if not self.iso.get() or not iso.is_file():
            messagebox.showerror("ISO", "Выберите существующий ISO-файл.")
            return
        missing = check_dependencies()
        if missing:
            messagebox.showerror("Зависимости", "Установите зависимости из README: " + ", ".join(missing))
            return
        device = self.devices[index]
        confirmation = simpledialog.askstring("Удаление данных", f"Все данные будут удалены:\n{device.label}\n\nВведите точный путь {device.path} для подтверждения:")
        if confirmation != device.path:
            return
        command = build_write_command(iso, device, confirmation)
        if os.geteuid() != 0:
            pkexec = shutil.which("pkexec")
            if not pkexec:
                messagebox.showerror("Права", "Установите polkit/pkexec или используйте sudo python3 flash_usb.py write (см. README).")
                return
            command.insert(0, pkexec)
        self.set_busy(True)
        threading.Thread(target=self.worker, args=(command,), daemon=False).start()

    def set_busy(self, busy):
        self.busy = busy
        for widget in (self.entry, self.browse, self.refresh_button, self.write_button):
            widget.configure(state="disabled" if busy else "normal")
        self.combo.configure(state="disabled" if busy else "readonly")

    def worker(self, command):
        code = 1
        try:
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
                for line in process.stdout:
                    self.events.put(("log", line.rstrip()))
                code = process.wait()
        except Exception as exc:
            self.events.put(("log", str(exc)))
        finally:
            self.events.put(("done", code))

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.append(value)
                else:
                    self.set_busy(False)
                    if value == 0:
                        messagebox.showinfo("Готово", "Запись и проверка завершены.")
                    else:
                        messagebox.showerror("Ошибка", "Запись не завершена. Подробности в журнале; накопитель может быть записан частично.")
                    self.refresh()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def close(self):
        if self.busy:
            messagebox.showwarning("Запись", "Дождитесь завершения операции перед закрытием окна.")
        else:
            self.root.destroy()


def build_write_command(iso, device, confirmation):
    return [sys.executable, "-u", str(Path(__file__).with_name("linux_usb.py")),
            "write", str(Path(iso).resolve()), device.path, "--confirm", confirmation,
            "--expected-identity", json.dumps(device.identity)]


def main():
    root = tk.Tk()
    LinuxUSB(root)
    root.mainloop()


if __name__ == "__main__":
    main()

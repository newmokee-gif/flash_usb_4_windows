"""User-initiated official package-manager installation flows."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tkinter import messagebox, ttk


HOMEBREW_COMMAND = '/bin/bash -c "$(/usr/bin/curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
HOMEBREW_SCRIPT = '''on run argv
    tell application "Terminal"
        activate
        do script (item 1 of argv)
    end tell
end run'''


def installed(system):
    if system == "darwin":
        return any(os.access(p, os.X_OK) and Path(p).is_file()
                   for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"))
    if system == "win32":
        return shutil.which("winget") is not None
    return False


def launch(system, local_aria=None):
    if system == "darwin":
        # An interactive terminal allows Homebrew to request sudo and explain
        # Xcode tools/PATH setup. No password is handled by this application.
        subprocess.run(["/usr/bin/osascript", "-e", HOMEBREW_SCRIPT, HOMEBREW_COMMAND],
                       check=True, capture_output=True, text=True, timeout=15)
    elif system == "win32":
        from winget_install import launch as install_winget
        install_winget(local_aria)
    else:
        raise RuntimeError("Эта кнопка доступна только в Windows и macOS.")


def add_buttons(parent, busy=lambda: False, local_aria=None):
    system = sys.platform
    if system not in ("darwin", "win32"):
        return
    name = "Homebrew" if system == "darwin" else "WinGet"
    frame = ttk.Frame(parent)
    frame.pack(fill="x", padx=15, pady=6)

    def check():
        messagebox.showinfo(name, f"{name} найден." if installed(system) else
                            f"{name} пока не найден. Завершите установку и перезапустите приложение.", parent=parent)

    def start():
        if busy():
            messagebox.showinfo(name, "Дождитесь завершения текущей операции.", parent=parent)
            return
        if installed(system):
            check()
            return
        explanation = (
            "Откроется Терминал с официальным установщиком Homebrew. "
            "Следуйте его инструкциям; может потребоваться пароль администратора. Продолжить?"
            if system == "darwin" else
            "Откроется установка файлов WinGet с GitHub Microsoft. Загрузка использует "
            "aria2, если он установлен, иначе HTTPS-загрузчик Windows. "
            "Будут проверены SHA-256 и подписи Microsoft. Продолжить?"
        )
        if not messagebox.askyesno(name, explanation, parent=parent):
            return
        try:
            launch(system, local_aria)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            messagebox.showerror(name, f"Не удалось открыть установку: {exc}", parent=parent)
            return
        messagebox.showinfo(name, "Установка открыта. Завершите её в отдельном окне, "
                            "затем нажмите «Проверить» или перезапустите приложение.", parent=parent)

    ttk.Button(frame, text=f"Установить {name}", command=start).pack(side="left")
    ttk.Button(frame, text="Проверить", command=check).pack(side="left", padx=8)

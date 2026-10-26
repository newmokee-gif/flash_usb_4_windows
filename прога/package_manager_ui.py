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


def installation_panel(parent, description):
    """Keep optional setup visible without changing the user's native theme."""
    style = ttk.Style(parent)
    style.configure("Setup.TButton", font="TkHeadingFont", padding=(16, 9))
    frame = ttk.LabelFrame(parent, text="Install additional components", padding=(12, 8))
    frame.pack(fill="x", padx=15, pady=(8, 10))
    label = ttk.Label(frame, text=description, wraplength=600, justify="left")
    label.pack(fill="x", pady=(0, 6))
    frame.bind("<Configure>", lambda event: label.configure(wraplength=max(180, event.width - 28)))
    actions = ttk.Frame(frame)
    actions.pack(fill="x")
    return actions


def add_dependency_button(parent, command):
    actions = installation_panel(parent,
        "Missing download or writing tools? Install them with your system package manager, after confirmation.")
    button = ttk.Button(actions, text="Install application dependencies", command=command,
                        style="Setup.TButton")
    button.pack(fill="x")
    return button


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
        raise RuntimeError("This button is available only on Windows and macOS.")


def add_buttons(parent, busy=lambda: False, local_aria=None):
    system = sys.platform
    if system not in ("darwin", "win32"):
        return
    name = "Homebrew" if system == "darwin" else "WinGet"
    frame = installation_panel(parent,
        f"{name} — a package manager. Install it if required for additional application components.")

    def check():
        messagebox.showinfo(name, f"{name} was found." if installed(system) else
                            f"{name} was not found. Complete installation and restart the application.", parent=parent)

    def start():
        if busy():
            messagebox.showinfo(name, "Wait for the current operation to finish.", parent=parent)
            return
        if installed(system):
            check()
            return
        explanation = (
            "Terminal will open with the official Homebrew installer. "
            "Follow its instructions; an administrator password may be required. Continue?"
            if system == "darwin" else
            "The WinGet installer will download files from Microsoft's GitHub. Downloads use "
            "aria2 when installed, otherwise the Windows HTTPS downloader. "
            "SHA-256 hashes and Microsoft signatures will be verified. Continue?"
        )
        if not messagebox.askyesno(name, explanation, parent=parent):
            return
        try:
            launch(system, local_aria)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            messagebox.showerror(name, f"Could not launch installation: {exc}", parent=parent)
            return
        messagebox.showinfo(name, "Installation has started. Complete it in the separate window, "
                            "then click Check or restart the application.", parent=parent)

    ttk.Button(frame, text=f"Install {name}", command=start,
               style="Setup.TButton").pack(side="left", fill="x", expand=True)
    ttk.Button(frame, text="Check", command=check).pack(side="left", padx=8)

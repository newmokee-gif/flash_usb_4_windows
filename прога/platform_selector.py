"""Choose the OS to put on the USB, independently of the host platform."""
import sys
import tkinter as tk
from tkinter import ttk
from app_icon import set_app_icon


def open_mode(root, mode):
    if mode not in ("windows", "gnu-linux"):
        raise ValueError("Unknown USB installation mode")
    for child in root.winfo_children():
        child.destroy()
    root.resizable(True, True)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    if mode == "gnu-linux":
        from gnu_linux_gui import GNULinuxUSB
        return GNULinuxUSB(root)
    if sys.platform == "win32":
        from V2 import WindowsOneClickUSB
        return WindowsOneClickUSB(root)
    if sys.platform == "darwin":
        from macos_gui import MacUSB
        return MacUSB(root)
    from linux_gui import LinuxUSB
    return LinuxUSB(root)


class OSChooser:
    def __init__(self, root):
        self.root = root
        set_app_icon(root)
        from ui_design import theme, header, section
        theme(root)
        root.title("WinUSB Creator — Select operating system")
        root.geometry("720x470")
        root.minsize(640, 440)
        root.resizable(True, True)
        header(root, "What would you like to install?", "Create installation media for another computer. Start by choosing its operating system.")
        panel = ttk.Frame(root, padding=(20, 12, 20, 20))
        panel.pack(fill="both", expand=True)
        windows = section(panel, "WINDOWS INSTALLATION")
        ttk.Label(windows, text="Windows 10 / 11 • Local ISO or supported download", style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        self.windows_button = ttk.Button(windows, text="Windows", style="Primary.TButton", command=lambda: open_mode(root, "windows"))
        self.windows_button.pack(fill="x")
        linux = section(panel, "LINUX INSTALLATION")
        ttk.Label(linux, text="Distribution catalogue • Official downloads • Verified raw writing", style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        self.linux_button = ttk.Button(linux, text="GNU/Linux", style="Primary.TButton", command=lambda: open_mode(root, "gnu-linux"))
        self.linux_button.pack(fill="x")
        ttk.Label(panel, text="Your USB drive is not changed until you confirm a write operation.", style="Muted.TLabel", wraplength=580).pack(anchor="w", pady=(8, 0))


def main():
    if sys.platform == "win32":
        from V2 import is_admin, run_as_admin
        if not is_admin():
            run_as_admin()
            return
    root = tk.Tk()
    OSChooser(root)
    root.mainloop()


if __name__ == "__main__":
    main()

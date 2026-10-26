"""Choose the OS to put on the USB, independently of the host platform."""
import sys
import tkinter as tk
from tkinter import ttk


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
        root.title("WinUSB Creator — выбор системы")
        root.geometry("460x230")
        root.resizable(False, False)
        panel = ttk.Frame(root, padding=24)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Вы будете устанавливать", font=("sans", 18)).pack(pady=(10, 24))
        self.windows_button = ttk.Button(panel, text="Виндовс", command=lambda: open_mode(root, "windows"))
        self.windows_button.pack(fill="x", pady=4)
        self.linux_button = ttk.Button(panel, text="ГНУ/Линукс", command=lambda: open_mode(root, "gnu-linux"))
        self.linux_button.pack(fill="x", pady=4)


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

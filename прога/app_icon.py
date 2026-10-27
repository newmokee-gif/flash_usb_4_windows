"""Shared window icon for source installations and frozen applications."""
from pathlib import Path
import sys
import tkinter as tk


def set_app_icon(root):
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("org.newmokee.winusbcreator")
    image = tk.PhotoImage(master=root, file=str(Path(__file__).with_name("app_icon.png")))
    root.iconphoto(True, image)
    # Keep the Tk image alive for the entire window lifetime.
    root._app_icon_img = image

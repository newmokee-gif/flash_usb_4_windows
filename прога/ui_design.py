"""Shared, dependency-free visual system for every desktop front end."""
import tkinter as tk
from tkinter import ttk

BG = "#eef2f7"
INK = "#17263c"
BLUE = "#155eef"


def theme(root):
    root.configure(background=BG)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=("TkDefaultFont", 10), foreground=INK)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG)
    style.configure("TCheckbutton", background=BG, padding=(0, 3))
    style.configure("TButton", padding=(12, 7), background="#ffffff", borderwidth=1)
    style.configure("Setup.TButton", font="TkHeadingFont", padding=(16, 9), background="#dce7fc")
    style.map("TButton", background=[("active", "#dce7fc")], foreground=[("disabled", "#778399")])
    style.configure("Primary.TButton", background=BLUE, foreground="white", borderwidth=0)
    style.map("Primary.TButton", background=[("disabled", "#b8c7e0"), ("active", "#104ac0")],
              foreground=[("disabled", "#52627b"), ("!disabled", "white")])
    style.configure("Danger.TButton", background="#b42318", foreground="white", borderwidth=0)
    style.map("Danger.TButton", background=[("disabled", "#d9bdbb"), ("active", "#912018")],
              foreground=[("disabled", "#725e5c"), ("!disabled", "white")])
    style.configure("Title.TLabel", font=("TkDefaultFont", 22, "bold"))
    style.configure("Muted.TLabel", foreground="#53657d")
    style.configure("TLabelframe", background=BG, bordercolor="#cbd5e1", relief="solid")
    style.configure("TLabelframe.Label", background=BG, foreground=BLUE, font=("TkDefaultFont", 10, "bold"))
    style.configure("TEntry", padding=6, fieldbackground="white")
    style.configure("TCombobox", padding=5, fieldbackground="white")
    style.configure("Horizontal.TProgressbar", background=BLUE, troughcolor="#dbe4f0", borderwidth=0)


def header(parent, title, subtitle):
    box = ttk.Frame(parent, padding=(20, 14, 20, 8))
    box.pack(fill="x")
    ttk.Label(box, text="WINUSB CREATOR", style="Muted.TLabel").pack(anchor="w")
    ttk.Label(box, text=title, style="Title.TLabel").pack(anchor="w", pady=(3, 4))
    label = ttk.Label(box, text=subtitle, style="Muted.TLabel", wraplength=780)
    label.pack(fill="x")
    box.bind("<Configure>", lambda event: label.configure(wraplength=max(200, event.width - 40)))
    return box


def section(parent, title):
    frame = ttk.LabelFrame(parent, text=title, padding=(12, 8))
    frame.pack(fill="x", pady=(0, 10))
    return frame


def row(parent):
    frame = ttk.Frame(parent)
    frame.pack(fill="x", pady=3)
    return frame


def scroll_body(root):
    """Keep every control reachable on small screens and at larger font scales."""
    outer = ttk.Frame(root)
    outer.pack(fill="both", expand=True)
    canvas = tk.Canvas(outer, background=BG, highlightthickness=0)
    bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    bar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    canvas.configure(yscrollcommand=bar.set)
    inner = ttk.Frame(canvas, padding=(20, 8, 20, 16))
    window = canvas.create_window(0, 0, window=inner, anchor="nw")
    inner.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))

    def wheel(event):
        if event.widget.winfo_class() in ("Text", "TCombobox"):
            return
        direction = -1 if getattr(event, "num", 0) == 4 or getattr(event, "delta", 0) > 0 else 1
        canvas.yview_scroll(direction * 3, "units")

    for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        root.bind(event, wheel)
    # Keyboard tab traversal must reveal controls below the viewport too.
    def reveal(event):
        widget = event.widget
        if str(widget).startswith(str(inner) + "."):
            top = widget.winfo_rooty() - inner.winfo_rooty()
            height = max(1, inner.winfo_height())
            if top < canvas.canvasy(0) or top + widget.winfo_height() > canvas.canvasy(canvas.winfo_height()):
                canvas.yview_moveto(max(0, top - 12) / height)
    root.bind("<FocusIn>", reveal)
    root._content_canvas = canvas
    return inner


def log_style(log):
    log.configure(background="#17263c", foreground="#e7eef9", insertbackground="white",
                  relief="flat", borderwidth=0, padx=12, pady=10, font=("TkFixedFont", 10))
    scrollbar = ttk.Scrollbar(log.master, orient="vertical", command=log.yview)
    scrollbar.pack(side="right", fill="y")
    log.configure(yscrollcommand=scrollbar.set)


def build_windows_ui(app, choices):
    """Presentation only: callbacks and state belong to the existing writer."""
    from package_manager_ui import add_buttons
    import os
    root = app.root
    theme(root)
    root.title("WinUSB Creator — Windows")
    root.geometry("900x850")
    root.minsize(800, 700)
    root.resizable(True, True)
    header(root, "Create a Windows USB", "Choose your drive and image. Review the target before writing.")
    add_buttons(root, busy=lambda: app.is_busy, local_aria=os.path.join(app.tools_dir, "aria2c.exe"))
    body = scroll_body(root)
    target = section(body, "01  TARGET USB")
    line = row(target)
    app.drive_combo = ttk.Combobox(line, textvariable=app.selected_drive, state="readonly")
    app.drive_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
    app.refresh_btn = ttk.Button(line, text="Refresh", command=app.refresh_drives)
    app.refresh_btn.pack(side="right")
    app.test_usb_btn = ttk.Button(line, text="Test sectors", command=app.start_standalone_usb_check)
    app.test_usb_btn.pack(side="right", padx=(0, 8))
    ttk.Checkbutton(target, text="Check the drive for errors before writing", variable=app.check_usb_var).pack(anchor="w")
    ttk.Label(target, text="Supported USB capacity: 8–128 GB. All existing data will be erased.", style="Muted.TLabel").pack(anchor="w")
    source = section(body, "02  WINDOWS IMAGE")
    line = row(source)
    app.ver_combo = ttk.Combobox(line, textvariable=app.win_version, state="readonly", width=31, values=list(choices))
    app.ver_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
    app.dl_auto_btn = ttk.Button(line, text="Download Windows", style="Primary.TButton", command=app.start_auto_windows_pipeline)
    app.dl_auto_btn.pack(side="right")
    ttk.Checkbutton(source, text="Automatically write to the selected USB after downloading", variable=app.auto_write_var).pack(anchor="w")
    ttk.Label(source, text=f"Downloads: {app.work_dir}", style="Muted.TLabel", wraplength=780).pack(anchor="w", pady=3)
    ttk.Separator(source).pack(fill="x", pady=6)
    ttk.Label(source, text="Or use a torrent, magnet link or direct URL", style="Muted.TLabel").pack(anchor="w")
    line = row(source)
    tk.Entry(line, textvariable=app.torrent_source, relief="solid", borderwidth=1).pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=6)
    app.dl_custom_btn = ttk.Button(line, text="Select .torrent", command=app.browse_torrent)
    app.dl_custom_btn.pack(side="left", padx=(0, 8))
    app.dl_mag_btn = ttk.Button(line, text="Download link", command=app.start_custom_pipeline)
    app.dl_mag_btn.pack(side="right")
    line = row(source)
    ttk.Entry(line, textvariable=app.iso_path, state="readonly").pack(side="left", fill="x", expand=True, padx=(0, 8))
    app.browse_iso_btn = ttk.Button(line, text="Choose local ISO…", command=app.browse_iso)
    app.browse_iso_btn.pack(side="right")
    status = section(body, "03  REVIEW & WRITE")
    ttk.Label(status, text="Writing erases the selected drive. Keep it connected until the operation finishes.", wraplength=780).pack(anchor="w", pady=(0, 6))
    app.progress = ttk.Progressbar(status, mode="determinate")
    app.progress.pack(fill="x", pady=5)
    # Existing worker code changes the text and foreground of this label.
    app.status_label = tk.Label(status, text="Ready — choose a USB drive and a Windows ISO.", bg=BG, fg=INK,
                                anchor="w", justify="left", wraplength=780)
    app.status_label.pack(fill="x", pady=5)
    app.write_btn = ttk.Button(status, text="Write Windows to USB", style="Danger.TButton", command=app.start_manual_write)
    app.write_btn.pack(fill="x", pady=(4, 0))

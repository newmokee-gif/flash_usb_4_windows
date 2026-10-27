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

from linux_usb import USBError, check_dependencies, list_devices, subprocess_environment
from app_icon import set_app_icon


class LinuxUSB:
    def __init__(self, root):
        self.root = root
        set_app_icon(root)
        from ui_design import theme, header, section, log_style, scroll_body
        theme(root)
        self.busy = False
        self.events = queue.Queue()
        self.devices = []
        root.title("WinUSB Creator — Linux / UEFI")
        root.geometry("900x760")
        root.minsize(800, 700)
        root.protocol("WM_DELETE_WINDOW", self.close)
        header(root, "Create a Windows USB", "Local Windows ISO → UEFI installation media. Large WIM files are split automatically.")
        self.add_setup_components()
        panel = scroll_body(root)
        source = section(panel, "01  CHOOSE AN IMAGE")
        self.iso = tk.StringVar()
        self.entry = ttk.Entry(source, textvariable=self.iso)
        self.entry.pack(fill="x")
        self.browse = ttk.Button(source, text="Select ISO…", command=self.choose_iso)
        self.browse.pack(anchor="w", pady=8)
        target = section(panel, "02  TARGET USB")
        self.combo = ttk.Combobox(target, state="readonly")
        self.combo.pack(fill="x")
        self.refresh_button = ttk.Button(target, text="Refresh USB", command=self.refresh)
        self.refresh_button.pack(anchor="w", pady=8)
        ttk.Label(target, text="All data on this USB will be erased. Legacy BIOS is not supported.", wraplength=700).pack(anchor="w", pady=5)
        self.write_button = ttk.Button(target, text="Write and verify", style="Danger.TButton", command=self.write)
        self.write_button.pack(fill="x")
        ttk.Label(panel, text="ACTIVITY", style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        log_panel = ttk.Frame(panel)
        log_panel.pack(fill="both", expand=True)
        self.log = tk.Text(log_panel, height=6, state="disabled", wrap="word")
        log_style(self.log)
        self.log.pack(side="left", fill="both", expand=True)
        self.refresh()
        root.after(100, self.poll)

    def add_setup_components(self):
        """Platform-specific optional setup, placed consistently below the header."""

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
            all_devices = self.enumerate_devices()
            self.devices = [device for device in all_devices if device.safe]
            self.combo["values"] = [device.label for device in self.devices]
            self.combo.set("")
            if self.devices:
                self.combo.current(0)
            for device in all_devices:
                if not device.safe:
                    self.append(f"Unavailable: {device.path}: {device.reason}")
            if not self.devices:
                self.append("No available USB drives. Unmount the USB partitions and refresh the list.")
        except (USBError, OSError) as exc:
            self.append(str(exc))

    def write(self):
        index = self.combo.current()
        if not 0 <= index < len(self.devices):
            messagebox.showerror("USB", "Select an available USB drive.")
            return
        iso = Path(self.iso.get()).expanduser()
        if not self.iso.get() or not iso.is_file():
            messagebox.showerror("ISO", "Select an existing ISO file.")
            return
        missing = self.missing_dependencies()
        if missing:
            messagebox.showerror("Dependencies", "Install the dependencies listed in README: " + ", ".join(missing))
            return
        device = self.devices[index]
        confirmation = simpledialog.askstring("Erase data", f"All data will be erased:\n{device.label}\n\nEnter the exact path {device.path} to confirm:")
        if confirmation != device.path:
            return
        try:
            command = self.prepare_write_command(iso, device, confirmation)
        except USBError as exc:
            messagebox.showerror("Launch", str(exc))
            return
        self.set_busy(True)
        threading.Thread(target=self.worker, args=(command,), daemon=False).start()

    def enumerate_devices(self):
        return list_devices()

    def missing_dependencies(self):
        return check_dependencies()

    def prepare_write_command(self, iso, device, confirmation):
        command = build_write_command(iso, device, confirmation)
        if os.geteuid() != 0:
            pkexec = shutil.which("pkexec", path="/usr/sbin:/usr/bin:/sbin:/bin")
            if not pkexec:
                raise USBError("Install polkit/pkexec or use sudo python3 flash_usb.py write (see README).")
            command.insert(0, pkexec)
        return command

    def set_busy(self, busy):
        self.busy = busy
        for widget in (self.entry, self.browse, self.refresh_button, self.write_button):
            widget.configure(state="disabled" if busy else "normal")
        self.combo.configure(state="disabled" if busy else "readonly")

    def worker(self, command):
        code = 1
        try:
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, env=subprocess_environment(restart_app=True)) as process:
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
                        messagebox.showinfo("Done", "Writing and verification complete.")
                    else:
                        messagebox.showerror("Error", "Writing did not complete. See the log; the drive may be partially written.")
                    self.refresh()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def close(self):
        if self.busy:
            messagebox.showwarning("Writing", "Wait for the operation to finish before closing the window.")
        else:
            self.root.destroy()


def build_write_command(iso, device, confirmation):
    arguments = ["write", str(Path(iso).resolve()), device.path, "--confirm", confirmation,
                 "--expected-identity", json.dumps(device.identity)]
    if getattr(sys, "frozen", False):
        image = os.environ.get("APPIMAGE")
        if image:
            path = Path(image)
            if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
                raise USBError("AppImage is unavailable. Save the file, make it executable and launch it again.")
            # root cannot traverse the desktop user's private FUSE mount.
            # Start the original image, extracting a separate runtime as root.
            return [str(path), "--appimage-extract-and-run", *arguments]
        return [sys.executable, *arguments]
    return [sys.executable, "-u", str(Path(__file__).with_name("linux_usb.py")), *arguments]


def main():
    root = tk.Tk()
    LinuxUSB(root)
    root.mainloop()


if __name__ == "__main__":
    main()

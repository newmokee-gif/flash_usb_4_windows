# WinUSB Creator — Windows and GNU/Linux on USB

Runs on Windows x64/ARM64, Linux x64/ARM64 and macOS Intel/Apple Silicon. At startup, **“What would you like to install?”** offers **Windows** and **GNU/Linux**. This selects the system to write to USB, independently of your host operating system. The interface and documentation are in English; Russian and English Windows installation images remain available.

## Ready-to-run applications

Download the appropriate file from the [latest release](https://github.com/newmokee-gif/flash_usb_4_windows/releases/latest).

| Computer | File |
| --- | --- |
| Windows Intel/AMD x64 | `WinUSB-Creator-Windows-x64.exe` |
| Windows 11 ARM64 | `WinUSB-Creator-Windows-ARM64.exe` |
| macOS 15+ Apple Silicon | `WinUSB-Creator-macOS-AppleSilicon.dmg` |
| macOS 15+ Intel | `WinUSB-Creator-macOS-Intel.dmg` |
| Debian x64/ARM64 | `flash-usb_0.3.3_all.deb` |
| Fedora x64/ARM64 | `flash-usb-0.3.3-1.noarch.rpm` |
| Debian/Fedora/Arch x64 | `flash-usb-v0.3.3-x86_64.AppImage` |
| Debian/Fedora ARM64 | `flash-usb-v0.3.3-aarch64.AppImage` |

The Windows EXE is standalone; no separate `_internal` folder is required. On Mac, open the DMG and drag the application into Applications. AppImage includes Python/Tk and requires glibc 2.36+ and a graphical session. Make it executable; if FUSE is unavailable, use `--appimage-extract-and-run`.

Install DEB/RPM with `sudo apt install ./flash-usb_0.3.3_all.deb` or `sudo dnf install ./flash-usb-0.3.3-1.noarch.rpm`. The package manager installs dependencies, including aria2. These packages use system Python.

File checksums are in `SHA256SUMS.txt`; build provenance is in `RELEASE-PROVENANCE.json`. Source code is available at the release tag. GitHub automatically adds Source code ZIP/tar.gz links; those archives are not needed to run the application.

## Windows mode

Opens the Windows ISO → USB interface. On Windows, the catalogue offers Windows 10 **x64 RU, x64 EN, x86 RU, x86 EN** and Windows 11 x64/ARM64 RU/EN. ISO architecture refers to the target computer. No official Windows 10 ARM64 ISO is listed.

The Windows application retains ISO and torrent/magnet downloading through aria2. Automatic Windows ISO URL availability depends on Microsoft/Fido. Local ISO files can also be selected.

On Linux/macOS, Windows mode uses local ISOs, creates FAT32 for UEFI and splits large WIM files with wimlib. Legacy BIOS is not supported in these modes. macOS uses MBR; Linux uses GPT. Creating USB media on Apple Silicon does not enable native Windows USB boot on that Mac.

## GNU/Linux mode

Select a distribution and an available edition/architecture, then download the image. See [catalogue sources](docs/distro-sources.md). ARM64 is offered only for existing installation images; board and firmware compatibility depends on the distribution.

Since **0.3.2**, the catalogue contains **60 distributions and 77 image variants**, including 17 ARM64 variants. The 30 additions include EndeavourOS, Artix, Garuda, MX Linux, Parrot OS, Solus, Tails, Puppy Linux, Rescuezilla and SystemRescue.

Official torrents take priority. DMG and AppImage include aria2; DEB/RPM install it as a dependency. On Windows, GNU/Linux mode can download official aria2 from GitHub and verify pinned archive and EXE SHA-256 hashes. Windows 11 ARM64 asks for separate consent to use the x64 helper under OS emulation when no native client is available; the application remains native ARM64. If a torrent is missing or fails, the application reports its fallback to the official HTTPS server. The official checksum must pass before writing is allowed. For local ISO/IMG files, enter the publisher's SHA-256.

Linux images are written **byte for byte**, including their partition table and bootloader, followed by full readback verification. ISO contents are not extracted onto FAT32. An uncompressed USB-compatible hybrid ISO/IMG is required; archives and board-specific images are not treated as universal ISOs.

Downloading can be followed by writing, but you must select a USB drive and confirm its exact device path before erasure. Device changes or substitution block writing.

**All data on the selected USB drive will be erased.** System and internal disks are rejected. Unmount USB volumes without ejecting the drive. Do not store the source ISO on the target drive. Do not disconnect it during writing or verification. After raw writing, Windows may offer to format Linux partitions — do not format the installation USB.

## Dependencies

On macOS, Windows mode requires external `wimlib`: `brew install wimlib`. When running from source, also install `aria2`. **Install Homebrew** opens the official interactive installer in Terminal.

On Windows, **Install WinGet** downloads the official installer from Microsoft's GitHub and verifies SHA-256 and signatures. Microsoft Store is not used. The original Windows mode on ARM64 requires native aria2 ARM64 in PATH for torrent/magnet downloads; this limitation does not apply to the GNU/Linux downloader described above. On x64, the original Windows-mode aria2 helper may download automatically.

For Linux AppImage and source installations:

```sh
# Debian
sudo apt install python3 python3-tk util-linux mount parted dosfstools wimtools udev pkexec fonts-dejavu-core aria2
# Fedora
sudo dnf install python3 python3-tkinter util-linux parted dosfstools wimlib-utils systemd-udev polkit dejavu-sans-fonts aria2
# Arch
sudo pacman -Syu --needed python tk util-linux parted dosfstools wimlib systemd polkit ttf-dejavu aria2
```

Run from source with `python3 flash_usb.py`; install dependencies with `python3 flash_usb.py install-deps` (use `python` on Windows). The GUI needs Tk and a graphical session; Linux privilege elevation needs a polkit agent. Homebrew/WinGet are installed separately using their buttons. Arch dependency installation performs a full system upgrade after confirmation.

Windows EXEs are not Authenticode-signed. macOS applications are ad-hoc signed, without Apple notarization. For a trusted Mac application, verify its checksum before using the standard **Open Anyway** option.

## Verification

`python3 -m unittest discover -s tests -v` runs tests without writing physical drives. CI checks interfaces, architectures, standalone packaged startup, Linux packages and ARM64 platforms. Disk tests use mocked commands and temporary files.

Physical writing and booting on target hardware require separate testing. Matching bytes do not guarantee firmware, Secure Boot or driver compatibility. The architecture of the host computer may differ from that of the system being installed.

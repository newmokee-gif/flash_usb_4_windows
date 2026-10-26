# GNU/Linux catalogue sources

The catalogue is in `прога/distro_catalog.py`. It is a curated list of installation
images, not a claim to support every distribution. Image versions are resolved
from official checksums immediately before downloading. If a release has been
removed, or a matching architecture or checksum is missing, downloading stops
before any USB writing.

| Distribution / family | Official source | Variants |
| --- | --- | --- |
| Ubuntu | releases.ubuntu.com, cdimage.ubuntu.com | x64 Desktop, ARM64 Server |
| Kubuntu, Xubuntu, Lubuntu | cdimage.ubuntu.com | x64 |
| Ubuntu MATE, Budgie, Studio, Kylin, Cinnamon, Unity, Edubuntu | cdimage.ubuntu.com | x64 |
| Debian | cdimage.debian.org | x64, ARM64 netinst |
| Fedora | download.fedoraproject.org, torrent.fedoraproject.org | x64, ARM64 Workstation |
| Arch Linux | geo.mirror.pkgbuild.com, archlinux.org | x64 |
| Void Linux | repo-fastly.voidlinux.org (official xmirror mirror) | x64 glibc Base |
| Devuan | files.devuan.org | x64 Desktop Live |
| Linux Mint | mirrors.kernel.org/linuxmint, torrents.linuxmint.com | x64 Cinnamon |
| Gentoo | distfiles.gentoo.org | x64 Minimal Installation |
| Rocky Linux | dl.rockylinux.org | x64, ARM64 Minimal |
| AlmaLinux | repo.almalinux.org | x64, ARM64 Minimal |
| CentOS Stream | mirror.stream.centos.org | x64, ARM64 DVD |
| Alpine Linux | dl-cdn.alpinelinux.org | x64, ARM64 Standard |
| Kali Linux | cdimage.kali.org | x64, ARM64 Installer |
| openSUSE | download.opensuse.org | x64, ARM64 Tumbleweed NET |
| NixOS | channels.nixos.org | x64, ARM64 Minimal |
| BunsenLabs | ddl.bunsenlabs.org | x64 Stable Live |
| Bodhi Linux | SourceForge / bodhilinux | x64 Standard |
| Mageia | mageia.org → publisher-selected mirror | x64 Live Xfce |
| Porteus | mirrors.dotsrc.org/porteus | x64 Xfce |
| Pop!_OS | api.pop-os.org, iso.pop-os.org | x64 Intel/AMD |
| EndeavourOS | mirror.alpix.eu/endeavouros (publisher mirror) | x64 Titan Nova |
| Artix Linux | download.artixlinux.org | x64 Base OpenRC |
| Garuda Linux | iso.builds.garudalinux.org | x64 Dr460nized |
| antiX | SourceForge / antix-linux | x64 Full sysVinit |
| Linux Lite | SourceForge / linux-lite | x64 |
| deepin | cdimage.deepin.com | x64 Desktop |
| SparkyLinux | SourceForge / sparkylinux | x64 Xfce |
| Bazzite | download.bazzite.gg | x64 Stable Desktop |
| Tails | tails.net → download.tails.net | x64 USB IMG, not DVD ISO |
| Rescuezilla | GitHub / rescuezilla/rescuezilla | x64 Noble |
| SystemRescue | system-rescue.org, fastly-cdn.system-rescue.org | x64 |
| Proxmox VE | enterprise.proxmox.com/iso | x64, ARM64 virtualization server installer |
| Slint | slackware.uk/slint (publisher mirror) | x64 |
| Zorin OS | plug-mirror.rcac.purdue.edu/zorin-iso (publisher mirror) | x64 Core |
| PureOS | storage.puri.sm | x64 Crimson GNOME |
| Nobara Linux | nobara-images.nobaraproject.org | x64 Official |
| Solus | downloads.getsol.us | x64 Budgie |
| MX Linux | SourceForge / mx-linux | x64 Xfce |
| Peppermint OS | SourceForge / peppermintos | x64 Debian Xfce |
| Archcraft | SourceForge / archcraft | x64 |
| Calculate Linux | mirror.calculate-linux.org | x64 Desktop KDE |
| Ultramarine Linux | ultramarine-linux.org/fyra-images | x64 Plasma |
| Oracle Linux | yum.oracle.com; checksums linux.oracle.com/security/gpg/checksum | x64 Boot, ARM64 Boot UEK |
| Puppy Linux | distro.ibiblio.org/puppylinux (publisher mirror) | x64 BookwormPup64 |
| GParted Live | SourceForge / gparted; checksums gparted.org | x64 |
| Chimera Linux | repo.chimera-linux.org | x64, ARM64 Live GNOME (musl / dinit) |
| openEuler | repo.openeuler.org | x64, ARM64 Installer |
| Vanilla OS | GitHub / Vanilla-OS/live-iso | x64, ARM64 |
| Rhino Linux | SourceForge / rhino-linux-builder | x64, ARM64 |
| Parrot OS | download.parrot.sh | x64, ARM64 Home |

The expansion adds exactly 30 names, for **60 distributions, 77 images and
17 ARM64 variants**. Architectures are not counted as separate distributions.
The catalogue includes desktop systems, server installers and specialized live
recovery systems. They do not all serve the same purpose or support every computer.
Like Alpine, Chimera uses musl and is not a GNU system.

Tails resolves its USB IMG and SHA-256 from official JSON metadata. Garuda's
stable latest.iso URL is matched to the exact checksum filename, which is also
used to select the torrent file. GParted publishes several algorithms in one
document: only the SHA256SUMS section is used, without confusing it with BLAKE3.
SHA-512 is stored explicitly; MD5/SHA-1 are not used to verify images.

Ubuntu flavours appear separately in the interface but share an Ubuntu base.
Alpine uses musl and BusyBox and is not technically GNU/Linux; its edition label
states this. Arch Linux ARM is deliberately not presented as a universal ARM64
ISO: its images target specific devices. ARM64 support alone does not guarantee
booting on Apple Silicon or a single-board computer. NET/netinst images require
a network connection on the target computer.

Official torrents are tried first for Ubuntu, Debian, Arch, Fedora, Mint, Kali
and openSUSE. Other distributions' official download directories are also checked
for torrent links; Gentoo and Mageia use their official torrent indexes.
The torrent must contain the selected image. Missing or unavailable torrents
fall back to the official HTTPS image. Official SHA-256/SHA-512 verification is
mandatory before writing in either case. The catalogue neither substitutes
third-party magnet links nor invents unavailable ARM editions.

Check availability with `python3 scripts/verify_distro_catalog.py`. It downloads
metadata and checks images with HEAD requests, without downloading multi-gigabyte
images. Results are saved in `distro-catalog-validation.json`; failures produce
a nonzero exit code. The expanded catalogue — **77/77 images, 60 distributions,
17 ARM64 variants** — passed in
[GitHub Actions run 40408384686](https://github.com/newmokee-gif/flash_usb_4_windows/actions/runs/40408384686)
at commit `f0dcbee`. Debian/Fedora/Arch tests and GUI startup passed in
[run 40408384767](https://github.com/newmokee-gif/flash_usb_4_windows/actions/runs/40408384767).
These checks do not test booting from physical USB media on specific hardware.

Historically, the original 40 variants from 30 distributions, including 10 ARM64
variants, passed official metadata and image availability checks in
[GitHub Actions run 40371947705](https://github.com/newmokee-gif/flash_usb_4_windows/actions/runs/40371947705)
at commit `3a2522e`. This verifies download sources, not physical boot compatibility.
External server availability can change, so metadata is checked again at download time.

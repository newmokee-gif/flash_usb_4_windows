# Источники каталога GNU/Linux

Каталог находится в `прога/distro_catalog.py`. Это курируемый список установочных
ISO, а не утверждение о поддержке всех существующих дистрибутивов. Номера файлов
определяются по официальным контрольным суммам непосредственно перед скачиванием.
Если издатель удалил выпуск, отсутствует подходящая архитектура или контрольная
сумма, загрузка прекращается до записи USB.

| Дистрибутив / семейство | Официальный источник | Варианты |
| --- | --- | --- |
| Ubuntu | releases.ubuntu.com, cdimage.ubuntu.com | x64 Desktop, ARM64 Server |
| Kubuntu, Xubuntu, Lubuntu | cdimage.ubuntu.com | x64 |
| Ubuntu MATE, Budgie, Studio, Kylin, Cinnamon, Unity, Edubuntu | cdimage.ubuntu.com | x64 |
| Debian | cdimage.debian.org | x64, ARM64 netinst |
| Fedora | download.fedoraproject.org, torrent.fedoraproject.org | x64, ARM64 Workstation |
| Arch Linux | geo.mirror.pkgbuild.com, archlinux.org | x64 |
| Void Linux | repo-fastly.voidlinux.org (официальное зеркало xmirror) | x64 glibc Base |
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
| Mageia | mageia.org → зеркало, выбранное издателем | x64 Live Xfce |
| Porteus | mirrors.dotsrc.org/porteus | x64 Xfce |
| Pop!_OS | api.pop-os.org, iso.pop-os.org | x64 Intel/AMD |
| EndeavourOS | mirror.alpix.eu/endeavouros (зеркало издателя) | x64 Titan Nova |
| Artix Linux | download.artixlinux.org | x64 Base OpenRC |
| Garuda Linux | iso.builds.garudalinux.org | x64 Dr460nized |
| antiX | SourceForge / antix-linux | x64 Full sysVinit |
| Linux Lite | SourceForge / linux-lite | x64 |
| deepin | cdimage.deepin.com | x64 Desktop |
| SparkyLinux | SourceForge / sparkylinux | x64 Xfce |
| Bazzite | download.bazzite.gg | x64 Stable Desktop |
| Tails | tails.net → download.tails.net | x64 USB IMG, не DVD ISO |
| Rescuezilla | GitHub / rescuezilla/rescuezilla | x64 Noble |
| SystemRescue | system-rescue.org, fastly-cdn.system-rescue.org | x64 |
| Proxmox VE | enterprise.proxmox.com/iso | x64, ARM64 установщик сервера виртуализации |
| Slint | slackware.uk/slint (зеркало издателя) | x64 |
| Zorin OS | plug-mirror.rcac.purdue.edu/zorin-iso (зеркало издателя) | x64 Core |
| PureOS | storage.puri.sm | x64 Crimson GNOME |
| Nobara Linux | nobara-images.nobaraproject.org | x64 Official |
| Solus | downloads.getsol.us | x64 Budgie |
| MX Linux | SourceForge / mx-linux | x64 Xfce |
| Peppermint OS | SourceForge / peppermintos | x64 Debian Xfce |
| Archcraft | SourceForge / archcraft | x64 |
| Calculate Linux | mirror.calculate-linux.org | x64 Desktop KDE |
| Ultramarine Linux | ultramarine-linux.org/fyra-images | x64 Plasma |
| Oracle Linux | yum.oracle.com; суммы linux.oracle.com/security/gpg/checksum | x64 Boot, ARM64 Boot UEK |
| Puppy Linux | distro.ibiblio.org/puppylinux (зеркало издателя) | x64 BookwormPup64 |
| GParted Live | SourceForge / gparted; суммы gparted.org | x64 |
| Chimera Linux | repo.chimera-linux.org | x64, ARM64 Live GNOME (musl / dinit) |
| openEuler | repo.openeuler.org | x64, ARM64 Installer |
| Vanilla OS | GitHub / Vanilla-OS/live-iso | x64, ARM64 |
| Rhino Linux | SourceForge / rhino-linux-builder | x64, ARM64 |
| Parrot OS | download.parrot.sh | x64, ARM64 Home |

Расширение добавляет ровно 30 новых названий: всего **60 систем, 77 образов,
17 ARM64-вариантов**. Разные архитектуры не считаются разными дистрибутивами.
В список входят рабочие станции, серверные установщики и специализированные
Live-системы восстановления; это не обещание одинакового назначения или
совместимости всех образов с любым компьютером. Chimera, как и Alpine,
использует musl и не является GNU-системой.

Tails выбирает USB IMG и SHA-256 из официальных JSON-метаданных. Для Garuda
постоянный URL latest.iso связан с точным именем из контрольной суммы, которое
также используется для выбора файла в torrent. GParted публикует несколько
алгоритмов в одном документе: выбирается только секция SHA256SUMS, без смешения
с BLAKE3. SHA-512 сохраняется явно; MD5/SHA-1 не используются для проверки образов.

Ubuntu flavours показаны отдельными системами в интерфейсе, но относятся к одной
Ubuntu-базе. Alpine использует musl и BusyBox и технически не является GNU/Linux;
это явно отмечено в названии редакции. Arch Linux ARM намеренно не выдается за
универсальный ARM64 ISO: его образы ориентированы на конкретные устройства.
Также поддержка ARM64 сама по себе не гарантирует загрузку на Apple Silicon или
одноплатном компьютере. NET/netinst-образы требуют сети на устанавливаемой машине.

Для опубликованных Ubuntu, Debian, Arch, Fedora, Mint, Kali и openSUSE торрент
пробуется первым. Для остальных систем дополнительно проверяются ссылки на
торренты в официальном каталоге загрузок; у Gentoo и Mageia используются их
официальные торрент-каталоги. Подходящая раздача должна содержать выбранный ISO.
При её отсутствии или недоступности используется официальный HTTPS-образ.
В любом случае перед записью обязательно сравнение с официальной SHA-256/SHA-512.
Каталог не подставляет сторонние magnet-ссылки и не выдумывает отсутствующие ARM
редакции.

Проверка доступности: `python3 scripts/verify_distro_catalog.py`. Она скачивает
только метаданные и проверяет ISO через HEAD, не скачивая многогигабайтные образы.
Результат сохраняется в `distro-catalog-validation.json`; неудачные записи дают
ненулевой код завершения. Все 40 вариантов из 30 названий, включая 10 вариантов
ARM64, прошли проверку официальных метаданных и доступности ISO в
[GitHub Actions, запуск 40371947705](https://github.com/newmokee-gif/flash_usb_4_windows/actions/runs/40371947705)
на коммите `3a2522e`. Это проверка источников загрузки, а не аппаратный тест
загрузки каждого дистрибутива с физического USB. Доступность внешних серверов
может меняться, поэтому метаданные проверяются заново при скачивании.

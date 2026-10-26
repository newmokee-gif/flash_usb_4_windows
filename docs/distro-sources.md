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

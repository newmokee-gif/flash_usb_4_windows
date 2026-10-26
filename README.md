# WinUSB Creator — Windows и Linux

Создание загрузочного USB из локального Windows ISO. Оригинальная Windows-программа находится в `прога/V2.py`; общий запуск — `python3 flash_usb.py`.

## ARM64: Windows и Linux

ARM64 означает архитектуру компьютера, на котором запускается программа. Архитектуру Windows ISO выбирайте для целевого компьютера: ARM64-программа может подготавливать x64 ISO и наоборот. Для загрузки Windows ARM64 нужны совместимые UEFI и драйверы. 32-битный ARM не поддерживается; macOS не входит в эти сборки.

**Windows 11 ARM64:** скачайте `WinUSB-Creator-Windows-ARM64.zip` из [релиза](https://github.com/newmokee-gif/flash_usb_4_windows/releases/tag/v0.2.0-linux.2), распакуйте полностью и запустите `WinUSB-Creator-Windows-ARM64.exe`, сохранив рядом каталог `_internal`. Python/Tk включены, EXE и библиотеки собраны нативно для ARM64. При запуске запрашиваются права администратора. ZIP не имеет цифровой подписи.

На ARM64 по умолчанию выбран Windows 11 ARM64; в списке остаются x64-образы. Для загрузок через приложение нужен отдельно установленный нативный `aria2c` ARM64 в PATH. Автоматическая загрузка x64-клиента на ARM отключена. Можно скачать официальный ISO в браузере и выбрать его для записи — для этого aria2 не нужен. Windows ARM64 поддерживает только UEFI, не Legacy BIOS.

**Linux ARM64:** используйте `flash-usb-v0.2.0-linux.2-aarch64.AppImage`. Существующие `.deb` (`all`) и `.rpm` (`noarch`) также подходят ARM64; отдельная сборка этих пакетов не нужна. Проверки выполняются нативно на Debian 12 ARM64 и Fedora ARM64, включая GUI/CLI, установку пакетов и операции WIM. Arch Linux ARM — отдельный дистрибутив; он пока не проверен.

## Linux: Debian, Fedora, Arch

Linux-версия имеет графический интерфейс Tkinter и CLI. Она создаёт GPT/FAT32 USB для **UEFI**, копирует файлы Windows и разделяет `sources/install.wim` размером более 4 GiB средствами wimlib. Legacy BIOS, автоматическая загрузка ISO и торренты в Linux-версии не реализованы. Используйте официальный Windows ISO с UEFI-загрузчиком для архитектуры целевого компьютера.

### Готовые установочные пакеты

Скачайте `.deb` для Debian или `.rpm` для Fedora из [Releases](https://github.com/newmokee-gif/flash_usb_4_windows/releases/tag/v0.2.0-linux.2) и установите его пакетным менеджером — зависимости будут установлены автоматически:

```sh
# Debian
sudo apt install ./flash-usb_0.2.0.linux.2_all.deb
# Fedora
sudo dnf install ./flash-usb-0.2.0-0.2.linux.noarch.rpm
```

После установки откройте **WinUSB Creator** в меню приложений или выполните `flash-usb`. Для CLI доступны `flash-usb check`, `flash-usb list` и `sudo flash-usb write /путь/Windows.iso /dev/sdX --confirm /dev/sdX`.

Это готовые системные пакеты Python-приложения (`all`/`noarch`), не автономные исполняемые файлы: системный Python, Tkinter и утилиты записи устанавливает менеджер пакетов. Сборка вручную и pip не нужны. Пакеты проверяются установкой, запуском от обычного пользователя и удалением в чистых контейнерах Debian/Fedora. Для Arch используйте исходный архив и команды ниже. Контрольные суммы пакетов находятся в `SHA256SUMS-packages.txt`.

### AppImage (x86_64 и ARM64)

Скачайте `flash-usb-v0.2.0-linux.2-x86_64.AppImage` из релиза. Python и Tkinter уже включены; установка приложения не нужна:

```sh
chmod +x flash-usb-v0.2.0-linux.2-x86_64.AppImage
./flash-usb-v0.2.0-linux.2-x86_64.AppImage
# Если FUSE недоступен:
./flash-usb-v0.2.0-linux.2-x86_64.AppImage --appimage-extract-and-run
```

Поддерживается x86_64 с glibc 2.36+ (Debian 12+, современные Fedora/Arch). Для графического интерфейса нужна рабочая графическая сессия; для подтверждения прав — агент polkit. Утилиты работы с дисками устанавливаются в системе:

На Linux ARM64 замените имя файла на `flash-usb-v0.2.0-linux.2-aarch64.AppImage`; требуется glibc 2.36+. ARM64-сборка проверяется на Debian 12 и Fedora, а x86_64 — также на Arch.

```sh
# Debian
sudo apt install util-linux mount parted dosfstools wimtools udev pkexec fonts-dejavu-core
# Fedora
sudo dnf install util-linux parted dosfstools wimlib-utils systemd-udev polkit dejavu-sans-fonts
# Arch
sudo pacman -Syu --needed util-linux parted dosfstools wimlib systemd polkit ttf-dejavu
```

Проверка зависимостей: `./flash-usb-v0.2.0-linux.2-x86_64.AppImage --appimage-extract-and-run check` (для ARM64 замените имя файла). CLI использует те же команды `list`/`write`, что и обычная версия. Контрольные суммы опубликованы в `SHA256SUMS-AppImage.txt` и `SHA256SUMS-AppImage-aarch64.txt`. AppImage не предназначен для musl/Alpine. Проверки CI выполняются без FUSE и без записи реальных USB.

### Запуск из исходников

Установите системные зависимости:

```sh
# Debian / Ubuntu
sudo apt-get update
sudo apt-get install python3 python3-tk util-linux parted dosfstools wimtools udev pkexec fonts-dejavu-core

# Fedora
sudo dnf install python3 python3-tkinter util-linux parted dosfstools wimlib-utils systemd-udev polkit dejavu-sans-fonts

# Arch Linux (полное обновление системы обязательно для rolling release)
sudo pacman -Syu --needed python tk util-linux parted dosfstools wimlib systemd polkit ttf-dejavu
```

В графической сессии запустите `python3 flash_usb.py`. Выберите ISO и USB, введите точный путь накопителя. Только операция записи запускается с правами администратора через polkit; нужен работающий агент авторизации рабочего стола. Если его нет, используйте CLI:

```sh
python3 flash_usb.py check
python3 flash_usb.py list
# /dev/sdX — пример: замените на точный путь из list, НЕ на раздел /dev/sdX1.
sudo python3 flash_usb.py write /полный/путь/Windows.iso /dev/sdX --confirm /dev/sdX
```

**Запись удаляет все данные выбранного USB.** Сначала размонтируйте его разделы через файловый менеджер. Программа отказывается использовать внутренние диски, занятые разделы, устройства только для чтения и диски с системными точками монтирования. ISO нельзя хранить на целевом USB. Не извлекайте и не заменяйте накопитель во время операции. При ошибке флешка может остаться частично записанной; прочитайте журнал, устраните причину и повторите запись.

ISO монтируется только для чтения. До форматирования проверяются структура ISO, целостность образов WIM/ESD, допустимость файлов и вместимость накопителя. После записи накопитель размонтируется, кэш блочного устройства сбрасывается, затем содержимое повторно читается и проверяется. Успешная проверка файлов не заменяет проверку загрузки на целевом компьютере; Secure Boot зависит от подписей загрузчика исходного ISO и настроек прошивки.

Нужна современная Linux-система с идентификатором устройства `diskseq` в sysfs (ядро 5.15+). Запись из контейнера с overlay-корнем блокируется: программа должна достоверно определить системные диски. Проверки CI в контейнерах выполняют только недеструктивные операции.

## Проверки разработки

```sh
python3 -m unittest discover -s tests -v
python3 flash_usb.py check
```

Тесты операций записи используют подставные команды и временные каталоги, не физические диски. CI выполняет тесты и запуск интерфейса под Xvfb в Debian, Fedora и Arch. Полную запись и загрузку с физического USB нужно проверять отдельно на каждом целевом оборудовании. Windows-реализация сохранена без изменений; Linux-проверки не подтверждают её работоспособность.

## Релизы

В релизах доступны готовые пакеты `.deb`/`.rpm`, AppImage для x86_64/ARM64, Windows ARM64 ZIP и исходный Linux-архив с тестами и инструкциями. Контрольные суммы SHA-256 публикуются рядом с файлами. ARM-сборки дополнены после исходного релиза; точные коммиты их исходников указаны в примечаниях к релизу. Предварительные релизы отмечены как pre-release, пока не проверены реальные USB и загрузка на оборудовании.

Сборка пакетов для разработчиков: `python3 packaging/build.py deb --output /tmp/flash-usb-packages` (нужен `dpkg-deb`) или `python3 packaging/build.py rpm --output /tmp/flash-usb-packages` (нужен `rpmbuild`). CI проверяет оба пакета до добавления в существующий релиз.

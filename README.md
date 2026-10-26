# WinUSB Creator — Windows и Linux

Создание загрузочного USB из локального Windows ISO. Оригинальная Windows-программа находится в `прога/V2.py`; общий запуск — `python3 flash_usb.py`.

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

В релизах доступны готовые пакеты `.deb`/`.rpm` и исходный Linux-архив с тестами и инструкциями. Контрольные суммы SHA-256 публикуются рядом с файлами. Предварительные релизы отмечены как pre-release, пока не проверены реальные USB и загрузка на оборудовании.

Сборка пакетов для разработчиков: `python3 packaging/build.py deb --output /tmp/flash-usb-packages` (нужен `dpkg-deb`) или `python3 packaging/build.py rpm --output /tmp/flash-usb-packages` (нужен `rpmbuild`). CI проверяет оба пакета до добавления в существующий релиз.

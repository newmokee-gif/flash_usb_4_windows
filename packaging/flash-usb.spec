Name:           flash-usb
Version:        @VERSION@
Release:        @RELEASE@
Summary:        Create Windows UEFI USB installers on Linux
License:        GPL-3.0-only
URL:            https://github.com/newmokee-gif/flash_usb_4_windows
Source0:        payload.tar.gz
BuildArch:      noarch
Requires:       python3 >= 3.10
Requires:       python3-tkinter
Requires:       util-linux
Requires:       parted
Requires:       dosfstools
Requires:       wimlib-utils
Requires:       systemd-udev
Requires:       polkit
Requires:       dejavu-sans-fonts

# Ship portable Python sources; bytecode differs between interpreter releases.
%global __brp_python_bytecompile %{nil}

%description
Graphical and command-line Windows ISO writer for USB drives using FAT32,
wimlib splitting, device safety checks and readback verification.
Writing erases the selected USB disk and requires administrator privileges.

%prep
%setup -q -c -T

%build
# Python application: no architecture-specific compilation is required.

%install
mkdir -p %{buildroot}
tar -xzf %{SOURCE0} -C %{buildroot}

%files
%defattr(-,root,root,-)
%attr(0755,root,root) %{_bindir}/flash-usb
%{_datadir}/flash-usb
%{_datadir}/applications/flash-usb.desktop
%{_datadir}/icons/hicolor/scalable/apps/flash-usb.svg
%doc %{_datadir}/doc/flash-usb/README.md
%doc %{_datadir}/doc/flash-usb/copyright
%license %{_datadir}/licenses/flash-usb/LICENSE

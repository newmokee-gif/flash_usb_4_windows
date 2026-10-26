WinUSB Creator — native Windows ARM64

Launch WinUSB-Creator-Windows-ARM64.exe directly; no ZIP extraction or
separate _internal directory is required.
Python and Tk are bundled. The application requests administrator rights for
disk access. The executable is not code-signed; only use trusted release files
and verify SHA256SUMS-Windows-ARM64.txt.

Windows ARM64 is the HOST architecture. Choose a Windows ISO compatible with
the TARGET machine; ARM64 and x64 ISO choices are separate in the download UI.
ARM64 Windows requires UEFI; there is no Legacy BIOS support for ARM64.

In Windows-image mode, provide a native ARM64 aria2c.exe on PATH for downloads.
GNU/Linux mode can install the verified official x64 aria2 helper with your
explicit consent, using Windows 11 ARM64 emulation. The application stays native.
You can download an official Windows ARM64 ISO in a browser and use local ISO
writing without aria2. Online Microsoft/Fido download availability depends on
Microsoft and is not covered by offline tests.

All tests avoid physical disk writes. Native PE machine checks, bundled GUI
startup and WinAPI binding checks pass in CI; USB boot testing requires hardware.

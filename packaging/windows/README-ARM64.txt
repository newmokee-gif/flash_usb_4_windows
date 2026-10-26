WinUSB Creator — native Windows ARM64

Extract the entire ZIP to a directory, then launch
WinUSB-Creator-Windows-ARM64.exe. Keep the _internal directory next to the EXE.
Python and Tk are bundled. The application requests administrator rights for
disk access. The executable is not code-signed; only use trusted release files
and verify SHA256SUMS-Windows-ARM64.txt.

Windows ARM64 is the HOST architecture. Choose a Windows ISO compatible with
the TARGET machine; ARM64 and x64 ISO choices are separate in the download UI.
ARM64 Windows requires UEFI; there is no Legacy BIOS support for ARM64.

For downloading via aria2 on ARM64, provide a native ARM64 aria2c.exe on PATH.
The application does not silently download/run an x64 aria2 helper on ARM64.
You can download an official Windows ARM64 ISO in a browser and use local ISO
writing without aria2. Online Microsoft/Fido download availability depends on
Microsoft and is not covered by offline tests.

All tests avoid physical disk writes. Native PE machine checks, bundled GUI
startup and WinAPI binding checks pass in CI; USB boot testing requires hardware.

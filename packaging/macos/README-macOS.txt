WinUSB Creator for macOS 15 or newer

Choose AppleSilicon for M-series Macs, Intel for Intel Macs. Extract the ZIP,
then move the complete "WinUSB Creator.app" bundle to Applications.
Python and Tk are bundled. Install the external WIM tool with Homebrew:

    brew install wimlib

The application creates a Windows UEFI installer on a selected USB drive.
It does not install macOS and does not enable native Windows USB boot on
Apple Silicon. Windows target hardware still needs compatible UEFI/drivers.
Writing erases the selected USB. Unmount its volumes before selecting it.
Administrator authorization is required only for writing.

This prerelease has an ad-hoc integrity signature, not a Developer ID
signature or Apple notarization. If Gatekeeper blocks opening the app,
use the supported Open Anyway action in System Settings > Privacy & Security
after checking the published SHA-256 and deciding to trust this download.
Do not disable Gatekeeper globally or remove quarantine as a blanket fix.

CLI usage (from Terminal):
    "/Applications/WinUSB Creator.app/Contents/MacOS/WinUSB Creator" check
    "/Applications/WinUSB Creator.app/Contents/MacOS/WinUSB Creator" list

GUI/runtime and mocked disk-safety tests run on native Apple Silicon and
Intel macOS runners. Physical USB writing and firmware boot remain untested.

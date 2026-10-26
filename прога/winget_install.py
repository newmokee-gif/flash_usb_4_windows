"""Install Microsoft's signed WinGet files without Microsoft Store."""
import base64
import os
from pathlib import Path
import shutil
import subprocess


SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$exitCode = 0
try {
    $headers = @{ 'User-Agent' = 'WinUSB-Creator'; 'Accept' = 'application/vnd.github+json' }
    $release = Invoke-RestMethod 'https://api.github.com/repos/microsoft/winget-cli/releases/latest' -Headers $headers
    $folder = Join-Path ([IO.Path]::GetTempPath()) ('WinUSB-WinGet-' + [Guid]::NewGuid())
    New-Item -ItemType Directory -Path $folder | Out-Null
    $names = @('Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle', 'DesktopAppInstaller_Dependencies.zip')
    foreach ($name in $names) {
        $asset = @($release.assets | Where-Object { $_.name -eq $name })
        if ($asset.Count -ne 1 -or $asset[0].digest -notmatch '^sha256:[a-fA-F0-9]{64}$') { throw "Missing asset or SHA256: $name" }
        $url = $asset[0].browser_download_url
        if (-not $url.StartsWith('https://github.com/microsoft/winget-cli/releases/download/')) { throw 'Unexpected download URL' }
        $file = Join-Path $folder $name
        if ($aria -and (Test-Path -LiteralPath $aria)) {
            & $aria '--no-conf=true' '--check-certificate=true' '--auto-file-renaming=false' '--allow-overwrite=false' '--max-tries=3' '--timeout=60' "--dir=$folder" "--out=$name" $url
            if ($LASTEXITCODE -ne 0) { throw "aria2 download failed: $LASTEXITCODE" }
        } else {
            Invoke-WebRequest $url -OutFile $file -UseBasicParsing
        }
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $asset[0].digest.Substring(7)) { throw "SHA256 mismatch: $name" }
    }
    $deps = Join-Path $folder 'dependencies'
    Expand-Archive -LiteralPath (Join-Path $folder $names[1]) -DestinationPath $deps
    $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    $arch = switch ($arch.ToLower()) { 'amd64' {'x64'} 'arm64' {'arm64'} default {throw 'Unsupported Windows architecture'} }
    $packages = @(Get-ChildItem -LiteralPath $deps -Recurse -File | Where-Object {
        $_.Extension -in @('.appx','.msix') -and ($_.FullName -match ('[\\/]' + $arch + '[\\/]') -or $_.Name -match ('_' + $arch + '_') -or $_.Name -match '_neutral_')
    })
    if ($packages.Count -eq 0) { throw "No dependencies for $arch" }
    $bundle = Join-Path $folder $names[0]
    foreach ($file in @($packages.FullName) + @($bundle)) {
        $signature = Get-AuthenticodeSignature -LiteralPath $file
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation(?:,|$)') {
            throw "Invalid Microsoft signature: $file"
        }
    }
    if ($verifyOnly) {
        Write-Host "Verified Microsoft packages and dependencies for $arch."
    } else {
        Add-AppxPackage -Path $bundle -DependencyPath @($packages.FullName)
        $installed = Get-AppxPackage -Name Microsoft.DesktopAppInstaller
        if (-not $installed) { throw 'App Installer registration not found' }
        $exe = Join-Path $installed.InstallLocation 'winget.exe'
        & $exe --version
        if ($LASTEXITCODE -ne 0) { throw 'WinGet version check failed' }
        Write-Host 'WinGet installed. Restart WinUSB Creator.' -ForegroundColor Green
    }
} catch {
    $exitCode = 1
    Write-Host $_ -ForegroundColor Red
    Write-Host 'Installation failed; downloaded files retained for diagnostics.'
}
if (-not $verifyOnly) { Read-Host 'Press Enter to close' }
exit $exitCode
'''


def launch(local_aria=None):
    aria = shutil.which('aria2c')
    if not aria and local_aria and Path(local_aria).is_file():
        aria = str(Path(local_aria).resolve())
    # Fixed script, argument escaped as a literal; no user-supplied shell code.
    script = "$aria = '" + (aria or '').replace("'", "''") + "'\n" + SCRIPT
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    return subprocess.Popen([str(powershell), '-NoProfile', '-EncodedCommand', encoded],
                            creationflags=subprocess.CREATE_NEW_CONSOLE)

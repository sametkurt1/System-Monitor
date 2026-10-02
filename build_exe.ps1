<#
.SYNOPSIS
    Build a single-file sysmon.exe with PyInstaller.

.DESCRIPTION
    Produces dist\sysmon.exe - one self-contained executable with no Python
    installation required on the target machine.  The result is small because
    sysmon has no third-party dependencies: everything it needs is ctypes
    against DLLs that ship with Windows.

    The executable is built with a requireAdministrator manifest (--uac-admin),
    because PresentMon (in-game FPS) and LibreHardwareMonitor (CPU temperature)
    both refuse to produce readings without elevation.

    PresentMon_x64.exe IS bundled: it is the only way to get frame timings and
    it is a single self-contained binary.

    The optional CPU temperature/power library is deliberately NOT bundled.
    It needs a CLR host and a kernel driver, so the built exe reports N/A for
    those two fields until the user opts in (see README.md).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File build_exe.ps1
#>
[CmdletBinding()]
param(
    [switch]$NoGui,           # build the terminal-only executable (much smaller)
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

if ($Clean) {
    Write-Host "Cleaning build artefacts..." -ForegroundColor Cyan
    foreach ($d in @('build', 'dist')) {
        if (Test-Path $d) { Remove-Item -Recurse -Force $d }
    }
    Get-ChildItem -Filter '*.spec' -ErrorAction SilentlyContinue | Remove-Item -Force
}

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "python not found on PATH" }

Write-Host "Installing build dependency..." -ForegroundColor Cyan
& $py.Source -m pip install --quiet --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install pyinstaller failed" }

$hidden = @(
    '--hidden-import', 'sysmon.sensors.cpu_ntdll',
    '--hidden-import', 'sysmon.sensors.fps',
    '--hidden-import', 'sysmon.gui.main_window',
    '--hidden-import', 'sysmon.gui.overlay',
    '--hidden-import', 'sysmon.gui.sampler',
    '--hidden-import', 'sysmon.gui.widgets'
)
$guiHolds = @()
if (-not $NoGui) {
    Write-Host "Including the desktop GUI (PySide6)..." -ForegroundColor Cyan
    & $py.Source -m pip install --quiet -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Write-Warning "PySide6 install failed; building terminal-only." }
    else {
        $guiHolds = @(
            '--collect-submodules', 'PySide6',
            '--exclude-module', 'PySide6.QtQml',
            '--exclude-module', 'PySide6.QtQuick',
            '--exclude-module', 'PySide6.Qt3D',
            '--exclude-module', 'PySide6.QtMultimedia',
            '--exclude-module', 'PySide6.QtWebEngineCore',
            '--exclude-module', 'PySide6.QtNetwork'
        )
    }
}

Write-Host "Running tests..." -ForegroundColor Cyan
& $py.Source tests\run_tests.py
if ($LASTEXITCODE -ne 0) { throw "tests failed; refusing to build" }

Write-Host "Building single-file executable..." -ForegroundColor Cyan
& $py.Source -m PyInstaller `
    --noconfirm `
    --onefile `
    --windowed `
    --name sysmon `
    --distpath dist `
    --workpath build `
    --specpath . `
    --console `
    --uac-admin `
    --add-binary "sysmon\sensors\PresentMon_x64.exe;sysmon\sensors" `
    $hidden `
    $guiHolds `
    sysmon.py

if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = Join-Path $PSScriptRoot 'dist\sysmon.exe'
if (-not (Test-Path $exe)) { throw "expected $exe to exist" }

$size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Built $exe ($size MB)" -ForegroundColor Green
if ($NoGui) {
    Write-Host "  GUI excluded. Launch with:  .\dist\sysmon.exe" -ForegroundColor Cyan
} else {
    Write-Host "  terminal:  .\dist\sysmon.exe" -ForegroundColor Cyan
    Write-Host "  window:    .\dist\sysmon.exe --gui" -ForegroundColor Cyan
}

param(
    [string]$PythonCmd = "python",
    [string]$OutputDir = "dist"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$hostDir = Join-Path $root "windows_host"

Push-Location $hostDir
try {
    & $PythonCmd -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install requirements.txt failed" }
    & $PythonCmd -m pip install -r requirements-tray.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install requirements-tray.txt failed" }

    & $PythonCmd -m PyInstaller `
        --noconfirm `
        --clean `
        --name RealtimeCursorHost `
        --noconsole `
        --distpath $OutputDir `
        run_tray.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
}
finally {
    Pop-Location
}

Write-Host "Build complete: $hostDir\$OutputDir\RealtimeCursorHost\RealtimeCursorHost.exe"

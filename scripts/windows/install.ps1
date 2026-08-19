$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

# Floors, kept in step with backend\pyproject.toml's `requires-python` and
# frontend\package.json's `engines.node` by tests/test_install_scripts.py.
# Checked here, before the venv exists: `python -m venv` succeeds on a python
# far too old for the dependency set, and the failure then surfaces several
# minutes later inside `pip install` as a wheel that has no build for this
# interpreter -- an error naming a package rather than the actual cause.
$PyMin = "3.11"
$NodeMin = "18"

# $ErrorActionPreference stops on PowerShell errors, not on a native exit code,
# so each probe's $LASTEXITCODE is tested explicitly.
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python $PyMin+ not found on PATH" }
python -c "import sys; raise SystemExit(0 if sys.version_info >= tuple(int(n) for n in '$PyMin'.split('.')) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Python $PyMin+ required; found $(python -V 2>&1)" }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw "Node $NodeMin+ not found on PATH" }
node -e "process.exit(parseInt(process.versions.node, 10) >= $NodeMin ? 0 : 1)"
if ($LASTEXITCODE -ne 0) { throw "Node $NodeMin+ required; found $(node -v)" }

Write-Host "Installing backend..."
Push-Location "$Root\backend"
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev,desktop]"
Pop-Location

Write-Host "Installing frontend..."
Push-Location "$Root\frontend"
npm install
Pop-Location

Write-Host "Creating pinnable desktop launcher..."
$run = "$Root\scripts\windows\run.ps1"
$icon = "$Root\frontend\public\favicon.ico"
$powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$shell = New-Object -ComObject WScript.Shell

# The shortcut must target powershell.exe directly: Explorer offers "Pin to taskbar"
# only for shortcuts to ordinary executables, and shortcuts hosted by wscript.exe
# (the previous launch.vbs approach) don't get the option. WindowStyle 1 opens a
# normal, visible console: run.ps1 stays attached and streams the backend and
# frontend logs, and closing that window shuts grimoire down.
function New-GrimoireShortcut($path) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $powershell
    $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -File ""$run"""
    $lnk.WorkingDirectory = $Root
    $lnk.IconLocation = $icon
    $lnk.WindowStyle = 1
    $lnk.Description = "Grimoire"
    $lnk.Save()
}

$desktop = [Environment]::GetFolderPath("Desktop")
$programs = [Environment]::GetFolderPath("Programs")
New-GrimoireShortcut "$desktop\Grimoire.lnk"
New-GrimoireShortcut "$programs\Grimoire.lnk"

Write-Host "Shortcut created in Desktop and Start Menu. Right-click it to 'Pin to taskbar'."

# The store is made lazily by the first API call, so nothing above created it.
# Ask the resolver where it will land, rather than printing a `~\.grimoire` that
# GRIMOIRE_HOME or the bootstrap pointer may already have overridden.
Write-Host ""
& "$Root\backend\.venv\Scripts\python.exe" -m grimoire.where
Write-Host ""
Write-Host "Done. Launch from the Start Menu / Desktop shortcut, or run scripts\windows\run.ps1"

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python 3.11+ not found" }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw "Node 18+ not found" }

Write-Host "Installing backend..."
Push-Location "$Root\backend"
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
Pop-Location

Write-Host "Installing frontend..."
Push-Location "$Root\frontend"
npm install
Pop-Location

Write-Host "Creating pinnable desktop launcher..."
$launch = "$Root\scripts\windows\launch.vbs"
$icon = "$Root\frontend\public\favicon.ico"
$wscript = "$env:SystemRoot\System32\wscript.exe"
$shell = New-Object -ComObject WScript.Shell

function New-GrimoireShortcut($path) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $wscript
    $lnk.Arguments = """$launch"""
    $lnk.WorkingDirectory = $Root
    $lnk.IconLocation = $icon
    $lnk.Description = "Grimoire"
    $lnk.Save()
}

$desktop = [Environment]::GetFolderPath("Desktop")
$programs = [Environment]::GetFolderPath("Programs")
New-GrimoireShortcut "$desktop\Grimoire.lnk"
New-GrimoireShortcut "$programs\Grimoire.lnk"

# Note: a wscript-targeted shortcut pins to the taskbar cleanly. Setting an explicit
# System.AppUserModel.ID requires the IPropertyStore COM API, which is brittle from
# PowerShell; the shortcut works and pins without it (it may group under the script host).
# Treated as a future refinement (see plan self-review).
Write-Host "Shortcut created in Desktop and Start Menu. Right-click it to 'Pin to taskbar'."
Write-Host "Done. Launch from the Start Menu / Desktop shortcut, or run scripts\windows\run.ps1"

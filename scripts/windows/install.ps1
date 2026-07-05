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
$run = "$Root\scripts\windows\run.ps1"
$icon = "$Root\frontend\public\favicon.ico"
$powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$shell = New-Object -ComObject WScript.Shell

# The shortcut must target powershell.exe directly: Explorer offers "Pin to taskbar"
# only for shortcuts to ordinary executables, and shortcuts hosted by wscript.exe
# (the previous launch.vbs approach) don't get the option. WindowStyle 7 starts the
# console minimized so nothing flashes on screen before -WindowStyle Hidden hides it.
function New-GrimoireShortcut($path) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $powershell
    $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""$run"""
    $lnk.WorkingDirectory = $Root
    $lnk.IconLocation = $icon
    $lnk.WindowStyle = 7
    $lnk.Description = "Grimoire"
    $lnk.Save()
}

$desktop = [Environment]::GetFolderPath("Desktop")
$programs = [Environment]::GetFolderPath("Programs")
New-GrimoireShortcut "$desktop\Grimoire.lnk"
New-GrimoireShortcut "$programs\Grimoire.lnk"

Write-Host "Shortcut created in Desktop and Start Menu. Right-click it to 'Pin to taskbar'."
Write-Host "Done. Launch from the Start Menu / Desktop shortcut, or run scripts\windows\run.ps1"

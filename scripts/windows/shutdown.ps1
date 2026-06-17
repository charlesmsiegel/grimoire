$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$PidFile = "$Root\.run\pids"

if (-not (Test-Path $PidFile)) {
    Write-Host "grimoire is not running."
    exit 0
}
foreach ($id in Get-Content $PidFile) {
    if ($id) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
}
Remove-Item $PidFile -Force
Write-Host "grimoire stopped."

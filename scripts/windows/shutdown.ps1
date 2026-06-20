$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$PidFile = "$Root\.run\pids"

if (-not (Test-Path $PidFile)) {
    Write-Host "grimoire is not running."
    exit 0
}
# Kill each recorded process AND its descendants. uvicorn --reload spawns a
# multiprocessing worker and npm spawns node; stopping only the parent leaves
# those children holding ports 8173/5173, breaking the next launch.
foreach ($id in Get-Content $PidFile) {
    if ($id) { taskkill /PID $id /T /F 2>$null | Out-Null }
}
Remove-Item $PidFile -Force
Write-Host "grimoire stopped."

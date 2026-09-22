$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$PidFile = "$Root\.run\pids"

# Kill each recorded process AND its descendants. The pid file holds one id for
# a default launch (the backend serving the built UI) and two for -Dev, where
# uvicorn --reload spawns a multiprocessing worker and npm spawns node --
# stopping only a parent leaves those children holding ports 8173/5173,
# breaking the next launch.
if (Test-Path $PidFile) {
    foreach ($id in Get-Content $PidFile) {
        if ($id) { taskkill /PID $id /T /F 2>$null | Out-Null }
    }
    Remove-Item $PidFile -Force
}
# Sweep orphans the pid file doesn't know about: workers outlive a killed or
# crashed supervisor and keep serving stale code on the app's ports. Both
# ports, since nothing records which mode the orphan was started in.
foreach ($port in 8173, 5173) {
    $owners = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
    foreach ($o in $owners) {
        if ($o) { taskkill /PID $o /T /F 2>$null | Out-Null }
    }
}
Write-Host "grimoire stopped."

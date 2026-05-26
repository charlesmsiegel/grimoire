$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$stateFile = Join-Path $repoRoot ".grimoire-run.env"
$backendPort = if ($env:GRIMOIRE_BACKEND_PORT) { $env:GRIMOIRE_BACKEND_PORT } else { "8173" }
$frontendPort = if ($env:GRIMOIRE_FRONTEND_PORT) { $env:GRIMOIRE_FRONTEND_PORT } else { "5173" }
$dataRoot = if ($env:GRIMOIRE_DATA_ROOT) { $env:GRIMOIRE_DATA_ROOT } else { Join-Path $env:USERPROFILE ".grimoire" }
$dbPath = if ($env:GRIMOIRE_DATABASE_PATH) { $env:GRIMOIRE_DATABASE_PATH } else { Join-Path $dataRoot "campaigns.sqlite" }

function Stop-GrimoireProcessTree($processId) {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ParentProcessId -eq $processId } |
        ForEach-Object { Stop-GrimoireProcessTree $_.ProcessId }
    try { Stop-Process -Id $processId -Force -ErrorAction Stop } catch {}
}

function Invoke-WalCheckpoint {
    if ((Test-Path $dbPath) -and (Test-Path "$dbPath-wal")) {
        Write-Host "Checkpointing SQLite WAL..."
        $pyCmd = "import sqlite3; c=sqlite3.connect(r'$dbPath'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"
        $pyExe = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
        & $pyExe -c $pyCmd 2>$null
    }
}

$killed = 0
if (Test-Path $stateFile) {
    Write-Host "Found state file, stopping recorded processes..."
    $state = @{}
    Get-Content $stateFile | ForEach-Object {
        $key, $val = $_ -split '=', 2
        $state[$key] = $val
    }
    if ($state["BACKEND_PORT"]) { $backendPort = $state["BACKEND_PORT"] }
    if ($state["FRONTEND_PORT"]) { $frontendPort = $state["FRONTEND_PORT"] }
    foreach ($key in @("BACKEND_PID", "FRONTEND_PID")) {
        $procId = $state[$key]
        if ($procId) {
            try {
                $proc = Get-Process -Id ([int]$procId) -ErrorAction Stop
                Write-Host "  Stopping $key ($($proc.ProcessName), PID $procId)..."
                Stop-GrimoireProcessTree ([int]$procId)
                $killed++
            } catch {
                Write-Host "  $key (PID $procId) already stopped."
            }
        }
    }
    Remove-Item $stateFile
    if ($killed -gt 0) {
        Invoke-WalCheckpoint
        Write-Host "Grimoire stopped."
        exit 0
    }
    Write-Host "Recorded PIDs were stale, scanning ports..."
}

Write-Host "No state file found. Scanning ports $backendPort and $frontendPort..."
$found = $false
foreach ($port in @($backendPort, $frontendPort)) {
    $conns = Get-NetTCPConnection -LocalPort ([int]$port) -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $conns) {
        try {
            $proc = Get-Process -Id $conn.OwningProcess -ErrorAction Stop
            $found = $true
            Write-Host "  Port ${port}: $($proc.ProcessName) (PID $($proc.Id))"
            $answer = Read-Host "  Kill this process? [y/N]"
            if ($answer -eq "y" -or $answer -eq "Y") {
                Stop-GrimoireProcessTree $proc.Id
                Write-Host "  Killed."
            }
        } catch {}
    }
}
if (-not $found) { Write-Host "  No processes found on those ports." }
Invoke-WalCheckpoint
Write-Host "Done."

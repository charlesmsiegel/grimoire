$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "Grimoire Server"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendDir = Join-Path $repoRoot "backend"
$frontendDir = Join-Path $repoRoot "frontend"
$stateFile = Join-Path $repoRoot ".grimoire-run.env"

$backendHost = if ($env:GRIMOIRE_BACKEND_HOST) { $env:GRIMOIRE_BACKEND_HOST } else { "127.0.0.1" }
$backendPort = if ($env:GRIMOIRE_BACKEND_PORT) { [int]$env:GRIMOIRE_BACKEND_PORT } else { 8173 }
$frontendHost = if ($env:GRIMOIRE_FRONTEND_HOST) { $env:GRIMOIRE_FRONTEND_HOST } else { "127.0.0.1" }
$frontendPort = if ($env:GRIMOIRE_FRONTEND_PORT) { [int]$env:GRIMOIRE_FRONTEND_PORT } else { 5173 }
$reload = $env:GRIMOIRE_BACKEND_RELOAD -eq "1"
$openBrowser = $env:GRIMOIRE_OPEN_BROWSER -ne "0"
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
        & python -c $pyCmd 2>$null
    }
}

function Resolve-Port($port) {
    while ($true) {
        $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        if (-not $conn) { return $port }
        try { $proc = Get-Process -Id $conn[0].OwningProcess -ErrorAction Stop } catch { return $port }
        Write-Host "Port $port is in use by $($proc.ProcessName) (PID $($proc.Id))"
        $choice = Read-Host "  [K]ill it, use [d]ifferent port, or [a]bort?"
        switch ($choice.ToLower()) {
            "k" {
                Stop-GrimoireProcessTree $proc.Id
                Start-Sleep -Seconds 1
            }
            "d" { $port = $port + 1 }
            default { Write-Host "Aborted."; exit 1 }
        }
    }
}

$backendPort = Resolve-Port $backendPort
$frontendPort = Resolve-Port $frontendPort

$env:GRIMOIRE_BACKEND_HOST = $backendHost
$env:GRIMOIRE_BACKEND_PORT = "$backendPort"
$env:GRIMOIRE_FRONTEND_PORT = "$frontendPort"

$backend = $null
$frontend = $null

try {
    $uvArgs = "run --directory `"$backendDir`" uvicorn grimoire.main:app --host $backendHost --port $backendPort"
    if ($reload) { $uvArgs += " --reload" }
    $backend = Start-Process -FilePath "uv" -ArgumentList $uvArgs -PassThru -NoNewWindow

    @("BACKEND_PID=$($backend.Id)", "BACKEND_PORT=$backendPort", "FRONTEND_PORT=$frontendPort") |
        Set-Content $stateFile -Encoding utf8

    Write-Host "Waiting for backend on port $backendPort..."
    $elapsed = 0
    while ($elapsed -lt 30) {
        try {
            $null = Invoke-WebRequest -Uri "http://${backendHost}:${backendPort}/api/health" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
            break
        } catch {}
        Start-Sleep -Seconds 1
        $elapsed++
    }
    if ($elapsed -ge 30) { throw "Backend failed to start within 30s" }
    Write-Host "Backend ready."

    $frontend = Start-Process -FilePath "cmd.exe" -ArgumentList "/c pnpm dev --port $frontendPort --host $frontendHost" -WorkingDirectory $frontendDir -PassThru -NoNewWindow

    @("BACKEND_PID=$($backend.Id)", "FRONTEND_PID=$($frontend.Id)", "BACKEND_PORT=$backendPort", "FRONTEND_PORT=$frontendPort") |
        Set-Content $stateFile -Encoding utf8

    if ($openBrowser) {
        Start-Sleep -Seconds 2
        Start-Process "http://localhost:$frontendPort"
    }

    Write-Host "Grimoire running. Press Ctrl+C to stop."
    while (-not $backend.HasExited -and -not $frontend.HasExited) {
        Start-Sleep -Seconds 1
    }
} finally {
    Write-Host "`nShutting down..."
    if ($backend -and -not $backend.HasExited) { Stop-GrimoireProcessTree $backend.Id }
    if ($frontend -and -not $frontend.HasExited) { Stop-GrimoireProcessTree $frontend.Id }
    Invoke-WalCheckpoint
    if (Test-Path $stateFile) { Remove-Item $stateFile }
    Write-Host "Grimoire stopped."
}

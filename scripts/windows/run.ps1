$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$RunDir = "$Root\.run"
$PidFile = "$RunDir\pids"
$Url = "http://127.0.0.1:5173"
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

if (Test-Path $PidFile) {
    $existing = Get-Content $PidFile | Select-Object -First 1
    if ($existing -and (Get-Process -Id $existing -ErrorAction SilentlyContinue)) {
        Write-Host "grimoire is already running ($Url). Use shutdown.ps1 to stop it."
        exit 0
    }
    # Stale pid file: the recorded parents died, but on Windows their children
    # (uvicorn's reload worker, npm's node) survive them. Kill the full trees.
    foreach ($id in Get-Content $PidFile) {
        if ($id) { taskkill /PID $id /T /F 2>$null | Out-Null }
    }
}
# Orphaned workers can hold the ports even with no pid file on record (a killed
# supervisor never takes its children with it) — a fresh launch would then bind
# alongside a zombie serving stale code. Free the ports before starting.
foreach ($port in 8173, 5173) {
    $owners = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
    foreach ($o in $owners) {
        if ($o) { taskkill /PID $o /T /F 2>$null | Out-Null }
    }
}

$back = Start-Process -FilePath "$Root\backend\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "grimoire.main:app", "--reload", "--port", "8173" `
    -WorkingDirectory "$Root\backend" -PassThru -WindowStyle Hidden
$front = Start-Process -FilePath "npm.cmd" `
    -ArgumentList "run", "dev", "--", "--port", "5173" `
    -WorkingDirectory "$Root\frontend" -PassThru -WindowStyle Hidden
Set-Content -Path $PidFile -Value @($back.Id, $front.Id)

Write-Host "grimoire running at $Url (backend $($back.Id), frontend $($front.Id))"

# Wait for a TCP port to accept connections (cold starts can exceed any fixed delay:
# Vite pre-bundles deps on first run, uvicorn imports the app).
function Wait-Port {
    param([string]$Name, [int]$Port)
    Write-Host -NoNewline "Waiting for $Name to be ready"
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $client = New-Object System.Net.Sockets.TcpClient
            $client.Connect("127.0.0.1", $Port)
            $client.Close()
            Write-Host ""
            return $true
        } catch {
            Write-Host -NoNewline "."
            Start-Sleep -Seconds 1
        }
    }
    Write-Host ""
    return $false
}

if (-not (Wait-Port "backend" 8173)) {
    Write-Host "Backend did not become ready (port 8173). The config page will fail to load; check the backend output."
}
if (-not (Wait-Port "frontend" 5173)) {
    Write-Host "Frontend did not become ready in time. Check logs; opening $Url anyway."
}

Start-Process $Url

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$RunDir = "$Root\.run"
$PidFile = "$RunDir\pids"
$Url = "http://localhost:5173"
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

if (Test-Path $PidFile) {
    $existing = Get-Content $PidFile | Select-Object -First 1
    if ($existing -and (Get-Process -Id $existing -ErrorAction SilentlyContinue)) {
        Write-Host "grimoire is already running ($Url). Use shutdown.ps1 to stop it."
        exit 0
    }
}

$back = Start-Process -FilePath "$Root\backend\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "grimoire.main:app", "--reload", "--port", "8000" `
    -WorkingDirectory "$Root\backend" -PassThru -WindowStyle Hidden
$front = Start-Process -FilePath "npm.cmd" `
    -ArgumentList "run", "dev", "--", "--port", "5173" `
    -WorkingDirectory "$Root\frontend" -PassThru -WindowStyle Hidden
Set-Content -Path $PidFile -Value @($back.Id, $front.Id)

Write-Host "grimoire running at $Url (backend $($back.Id), frontend $($front.Id))"

# Wait for the frontend dev server to accept connections before opening a browser.
# Vite's cold start (first run pre-bundles deps) can take well over a fixed delay.
Write-Host -NoNewline "Waiting for frontend to be ready"
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect("localhost", 5173)
        $client.Close()
        $ready = $true
        break
    } catch {
        Write-Host -NoNewline "."
        Start-Sleep -Seconds 1
    }
}
Write-Host ""
if (-not $ready) {
    Write-Host "Frontend did not become ready in time. Check logs; opening $Url anyway."
}

# Prefer browser app mode for a chromeless, app-like window.
$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
$chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
if (Test-Path $edge) { Start-Process $edge "--app=$Url" }
elseif (Test-Path $chrome) { Start-Process $chrome "--app=$Url" }
else { Start-Process $Url }

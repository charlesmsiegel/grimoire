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
Start-Sleep -Seconds 2

# Prefer browser app mode for a chromeless, app-like window.
$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
$chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
if (Test-Path $edge) { Start-Process $edge "--app=$Url" }
elseif (Test-Path $chrome) { Start-Process $chrome "--app=$Url" }
else { Start-Process $Url }

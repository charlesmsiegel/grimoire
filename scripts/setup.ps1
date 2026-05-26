$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Write-Host "=== Grimoire Setup ===" -ForegroundColor Cyan
Write-Host ""

function Test-Cmd($name) {
    $null -ne (Get-Command $name -ErrorAction SilentlyContinue)
}

function Request-Install($name, $command, $url) {
    Write-Host "  $name is not installed." -ForegroundColor Yellow
    $answer = Read-Host "  Install it now? ($command) [Y/n]"
    if ($answer -eq "" -or $answer -eq "y" -or $answer -eq "Y") {
        Write-Host "  Installing $name..."
        Invoke-Expression $command
    } else {
        Write-Host "  Grimoire requires $name. Install manually: $url" -ForegroundColor Red
        exit 1
    }
}

function Assert-MinVersion($name, $actual, $minMajor, $minMinor) {
    if ($actual -match "(\d+)\.(\d+)") {
        $major = [int]$Matches[1]; $minor = [int]$Matches[2]
        if ($major -gt $minMajor -or ($major -eq $minMajor -and $minor -ge $minMinor)) {
            Write-Host "  $name $major.$minor" -ForegroundColor Green
            return $true
        }
        Write-Host "  $name $major.$minor found, but $minMajor.$minMinor+ required." -ForegroundColor Yellow
    }
    return $false
}

Write-Host "Checking prerequisites..."

# Python 3.12+ — check py launcher first (handles multiple installs), then python
$pythonOk = $false
if (Test-Cmd "py") {
    $pyVer = & py -3 --version 2>&1
    $pythonOk = Assert-MinVersion "Python (py launcher)" "$pyVer" 3 12
}
if (-not $pythonOk -and (Test-Cmd "python")) {
    $pyVer = & python --version 2>&1
    $pythonOk = Assert-MinVersion "Python" "$pyVer" 3 12
}
if (-not $pythonOk) {
    Request-Install "Python 3.12+" "winget install Python.Python.3.12 --accept-package-agreements" "https://python.org"
}

# Node 20+
if (-not (Test-Cmd "node")) {
    Request-Install "Node.js 20+" "winget install OpenJS.NodeJS.LTS --accept-package-agreements" "https://nodejs.org"
} else {
    $nodeVer = & node --version 2>&1
    if ($nodeVer -match "v(\d+)" -and [int]$Matches[1] -ge 20) {
        Write-Host "  Node $($Matches[1])" -ForegroundColor Green
    } else {
        Request-Install "Node.js 20+" "winget install OpenJS.NodeJS.LTS --accept-package-agreements" "https://nodejs.org"
    }
}

# uv
if (-not (Test-Cmd "uv")) {
    Request-Install "uv" "irm https://astral.sh/uv/install.ps1 | iex" "https://docs.astral.sh/uv/"
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    if (-not (Test-Cmd "uv")) {
        Write-Host "  uv was installed but is not on PATH. Please restart your shell and re-run setup." -ForegroundColor Red
        exit 1
    }
} else { Write-Host "  uv" -ForegroundColor Green }

# pnpm
if (-not (Test-Cmd "pnpm")) {
    Request-Install "pnpm" "corepack enable; corepack prepare pnpm@latest --activate" "https://pnpm.io"
} else { Write-Host "  pnpm" -ForegroundColor Green }

Write-Host ""
Write-Host "Installing dependencies..."

Write-Host "  Backend (uv sync)..."
Push-Location (Join-Path $repoRoot "backend")
try { & uv sync } finally { Pop-Location }

Write-Host "  Frontend (pnpm install)..."
Push-Location (Join-Path $repoRoot "frontend")
try { & pnpm install } finally { Pop-Location }

Write-Host ""
Write-Host "Creating desktop shortcut..."
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Grimoire.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $repoRoot "scripts\run.bat"
$shortcut.WorkingDirectory = $repoRoot
$shortcut.IconLocation = Join-Path $repoRoot "assets\icons\grimoire.ico"
$shortcut.Description = "Launch Grimoire"
$shortcut.Save()
Write-Host "  $shortcutPath" -ForegroundColor Green

Write-Host ""
Write-Host "=== Setup complete! ===" -ForegroundColor Cyan
Write-Host "Double-click the Grimoire shortcut on your desktop, or run scripts\run.bat"

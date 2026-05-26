# Packaging & Cross-Platform Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the cross-platform bash scripts with platform-native scripts (bash for Unix, PowerShell for Windows) and add a setup flow that creates a desktop shortcut with the grimoire icon.

**Architecture:** Each platform gets its own self-contained scripts — no shared cross-platform abstraction layer. Windows uses `.bat` launchers that call `.ps1` scripts. Unix uses bash scripts that detect macOS vs Linux where needed. Icon assets are generated once via Pillow and committed.

**Tech Stack:** Bash, PowerShell 5.1, Pillow (one-shot icon generation), COM automation (Windows shortcuts), iconutil (macOS .icns)

**Spec:** `docs/superpowers/specs/2026-05-26-packaging-design.md`

**Deviation from spec:** `.icns` cannot be generated on Windows (Pillow's ICNS writer requires macOS `iconutil`). Instead, `setup.sh` generates `.icns` at setup time on macOS. The committed icon assets are PNGs + `.ico` only.

---

### Task 1: Generate and Commit Icon Assets

**Files:**
- Create: `assets/icons/grimoire.png` (copy from source)
- Create: `assets/icons/grimoire-{256,128,64,48,32,16}.png`
- Create: `assets/icons/grimoire.ico`
- Temp: `scripts/generate_icons.py` (run once, then delete)

- [ ] **Step 1: Copy source icon into project**

```bash
mkdir -p assets/icons
cp "/c/Users/charl/Downloads/grimoire_icon_transparent_background.png" assets/icons/grimoire.png
```

Verify: `file assets/icons/grimoire.png` — should be 512×512 PNG.

- [ ] **Step 2: Install Pillow temporarily**

```bash
pip install Pillow
```

- [ ] **Step 3: Write the icon generation script**

Create `scripts/generate_icons.py`:

```python
from PIL import Image
import os

os.makedirs("assets/icons", exist_ok=True)
src = Image.open("assets/icons/grimoire.png").convert("RGBA")
assert src.size == (512, 512), f"Expected 512x512, got {src.size}"

for size in [256, 128, 64, 48, 32, 16]:
    src.resize((size, size), Image.LANCZOS).save(f"assets/icons/grimoire-{size}.png")
    print(f"  grimoire-{size}.png")

src.save(
    "assets/icons/grimoire.ico",
    format="ICO",
    sizes=[(16, 16), (32, 32), (48, 48), (256, 256)],
)
print("  grimoire.ico")
print("Done.")
```

- [ ] **Step 4: Run the generation script**

```bash
cd <repo-root>
python scripts/generate_icons.py
```

Expected output:
```
  grimoire-256.png
  grimoire-128.png
  grimoire-64.png
  grimoire-48.png
  grimoire-32.png
  grimoire-16.png
  grimoire.ico
Done.
```

Verify: `ls assets/icons/` shows all 8 files (grimoire.png + 6 sized PNGs + grimoire.ico).

- [ ] **Step 5: Delete the generation script and uninstall Pillow**

```bash
rm scripts/generate_icons.py
pip uninstall -y Pillow
```

- [ ] **Step 6: Commit icon assets**

```bash
git add assets/icons/
git commit -m "feat: add multi-resolution grimoire icon assets"
```

---

### Task 2: Write .bat Wrappers

**Files:**
- Create: `scripts/setup.bat`
- Create: `scripts/run.bat`
- Create: `scripts/shutdown.bat`

- [ ] **Step 1: Write all three .bat files**

Create `scripts/setup.bat`:

```bat
@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
```

Create `scripts/run.bat`:

```bat
@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
```

Create `scripts/shutdown.bat`:

```bat
@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0shutdown.ps1" %*
```

- [ ] **Step 2: Commit**

```bash
git add scripts/setup.bat scripts/run.bat scripts/shutdown.bat
git commit -m "feat: add .bat wrappers for Windows PowerShell scripts"
```

---

### Task 3: Write shutdown.ps1

**Files:**
- Create: `scripts/shutdown.ps1`

- [ ] **Step 1: Write shutdown.ps1**

Create `scripts/shutdown.ps1`:

```powershell
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
        & python -c $pyCmd 2>$null
    }
}

if (Test-Path $stateFile) {
    Write-Host "Found state file, stopping recorded processes..."
    $state = @{}
    Get-Content $stateFile | ForEach-Object {
        $key, $val = $_ -split '=', 2
        $state[$key] = $val
    }
    foreach ($key in @("BACKEND_PID", "FRONTEND_PID")) {
        $pid = $state[$key]
        if ($pid) {
            try {
                $proc = Get-Process -Id ([int]$pid) -ErrorAction Stop
                Write-Host "  Stopping $key ($($proc.ProcessName), PID $pid)..."
                Stop-GrimoireProcessTree ([int]$pid)
            } catch {
                Write-Host "  $key (PID $pid) already stopped."
            }
        }
    }
    Invoke-WalCheckpoint
    Remove-Item $stateFile
    Write-Host "Grimoire stopped."
    exit 0
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
```

- [ ] **Step 2: Test shutdown.ps1 when nothing is running**

```powershell
.\scripts\shutdown.bat
```

Expected output:
```
No state file found. Scanning ports 8173 and 5173...
  No processes found on those ports.
Done.
```

- [ ] **Step 3: Commit**

```bash
git add scripts/shutdown.ps1
git commit -m "feat: add shutdown.ps1 for Windows"
```

---

### Task 4: Write shutdown.sh

**Files:**
- Create: `scripts/shutdown.sh` (replaces existing)

- [ ] **Step 1: Write shutdown.sh**

Create `scripts/shutdown.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_FILE="$REPO_ROOT/.grimoire-run.env"
BACKEND_PORT="${GRIMOIRE_BACKEND_PORT:-8173}"
FRONTEND_PORT="${GRIMOIRE_FRONTEND_PORT:-5173}"
DATA_ROOT="${GRIMOIRE_DATA_ROOT:-$HOME/.grimoire}"
DB_PATH="${GRIMOIRE_DATABASE_PATH:-$DATA_ROOT/campaigns.sqlite}"

checkpoint_wal() {
    if [ -f "$DB_PATH" ] && [ -f "$DB_PATH-wal" ]; then
        echo "Checkpointing SQLite WAL..."
        python3 -c "import sqlite3; c=sqlite3.connect('$DB_PATH'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()" 2>/dev/null || true
    fi
}

port_pids() {
    lsof -ti :"$1" 2>/dev/null || ss -tlnp "sport = :$1" 2>/dev/null | grep -oP 'pid=\K\d+' || true
}

if [ -f "$STATE_FILE" ]; then
    echo "Found state file, stopping recorded processes..."
    while IFS='=' read -r key val; do
        case "$key" in
            BACKEND_PID|FRONTEND_PID)
                if [ -n "$val" ] && kill -0 "$val" 2>/dev/null; then
                    name=$(ps -p "$val" -o comm= 2>/dev/null || echo "unknown")
                    echo "  Stopping $key ($name, PID $val)..."
                    kill "$val" 2>/dev/null || true
                    sleep 1
                    kill -9 "$val" 2>/dev/null || true
                else
                    echo "  $key (PID $val) already stopped."
                fi
                ;;
        esac
    done < "$STATE_FILE"
    checkpoint_wal
    rm -f "$STATE_FILE"
    echo "Grimoire stopped."
    exit 0
fi

echo "No state file found. Scanning ports $BACKEND_PORT and $FRONTEND_PORT..."
found=0
for port in $BACKEND_PORT $FRONTEND_PORT; do
    pids=$(port_pids "$port")
    for pid in $pids; do
        [ -z "$pid" ] && continue
        found=1
        name=$(ps -p "$pid" -o comm= 2>/dev/null || echo "unknown")
        echo "  Port $port: $name (PID $pid)"
        echo -n "  Kill this process? [y/N] "
        read -r answer
        if [ "$answer" = "y" ] || [ "$answer" = "Y" ]; then
            kill "$pid" 2>/dev/null || true
            sleep 1
            kill -9 "$pid" 2>/dev/null || true
            echo "  Killed."
        fi
    done
done
[ "$found" -eq 0 ] && echo "  No processes found on those ports."
checkpoint_wal
echo "Done."
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/shutdown.sh
```

- [ ] **Step 3: Commit**

```bash
git add scripts/shutdown.sh
git commit -m "feat: rewrite shutdown.sh as self-contained Unix script"
```

---

### Task 5: Write run.ps1

**Files:**
- Create: `scripts/run.ps1`

This is the most complex script. It starts both servers, tracks their PIDs, handles port conflicts interactively, and reliably cleans up on exit.

- [ ] **Step 1: Write run.ps1**

Create `scripts/run.ps1`:

```powershell
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
```

- [ ] **Step 2: Test run.ps1**

```powershell
.\scripts\run.bat
```

Verify:
1. Terminal title changes to "Grimoire Server"
2. Backend starts on port 8173 (or resolved port)
3. "Backend ready." appears
4. Frontend starts on port 5173 (or resolved port)
5. Browser opens to `http://localhost:5173`
6. App loads in browser
7. Press Ctrl+C → "Shutting down..." appears, then "Grimoire stopped."
8. Verify no stale processes: `Get-NetTCPConnection -LocalPort 8173 -ErrorAction SilentlyContinue` returns nothing
9. Verify `.grimoire-run.env` was deleted

- [ ] **Step 3: Commit**

```bash
git add scripts/run.ps1
git commit -m "feat: add run.ps1 with reliable process cleanup"
```

---

### Task 6: Write run.sh

**Files:**
- Create: `scripts/run.sh` (replaces existing)

- [ ] **Step 1: Write run.sh**

Create `scripts/run.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
FRONTEND_DIR="$REPO_ROOT/frontend"
STATE_FILE="$REPO_ROOT/.grimoire-run.env"

BACKEND_HOST="${GRIMOIRE_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${GRIMOIRE_BACKEND_PORT:-8173}"
FRONTEND_HOST="${GRIMOIRE_FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${GRIMOIRE_FRONTEND_PORT:-5173}"
RELOAD="${GRIMOIRE_BACKEND_RELOAD:-0}"
OPEN_BROWSER="${GRIMOIRE_OPEN_BROWSER:-1}"
DATA_ROOT="${GRIMOIRE_DATA_ROOT:-$HOME/.grimoire}"
DB_PATH="${GRIMOIRE_DATABASE_PATH:-$DATA_ROOT/campaigns.sqlite}"

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
    echo ""
    echo "Shutting down..."
    [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null || true
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true
    for _ in $(seq 1 10); do
        alive=0
        [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null && alive=1
        [ -n "$FRONTEND_PID" ] && kill -0 "$FRONTEND_PID" 2>/dev/null && alive=1
        [ "$alive" -eq 0 ] && break
        sleep 0.5
    done
    [ -n "$BACKEND_PID" ] && kill -9 "$BACKEND_PID" 2>/dev/null || true
    [ -n "$FRONTEND_PID" ] && kill -9 "$FRONTEND_PID" 2>/dev/null || true
    if [ -f "$DB_PATH" ] && [ -f "$DB_PATH-wal" ]; then
        echo "Checkpointing SQLite WAL..."
        python3 -c "import sqlite3; c=sqlite3.connect('$DB_PATH'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()" 2>/dev/null || true
    fi
    rm -f "$STATE_FILE"
    echo "Grimoire stopped."
}
trap cleanup EXIT

printf '\033]0;Grimoire Server\007'

resolve_port() {
    local port=$1
    while true; do
        local pids
        pids=$(lsof -ti :"$port" 2>/dev/null || ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K\d+' || true)
        [ -z "$pids" ] && break
        local pid name
        pid=$(echo "$pids" | head -1)
        name=$(ps -p "$pid" -o comm= 2>/dev/null || echo "unknown")
        echo "Port $port is in use by $name (PID $pid)" >&2
        echo -n "  [K]ill it, use [d]ifferent port, or [a]bort? " >&2
        read -r choice
        case "$choice" in
            k|K) kill "$pid" 2>/dev/null || true; sleep 1 ;;
            d|D) port=$((port + 1)) ;;
            *) echo "Aborted." >&2; exit 1 ;;
        esac
    done
    echo "$port"
}

BACKEND_PORT=$(resolve_port "$BACKEND_PORT")
FRONTEND_PORT=$(resolve_port "$FRONTEND_PORT")

export GRIMOIRE_BACKEND_HOST="$BACKEND_HOST"
export GRIMOIRE_BACKEND_PORT="$BACKEND_PORT"
export GRIMOIRE_FRONTEND_PORT="$FRONTEND_PORT"

UV_ARGS="run --directory $BACKEND_DIR uvicorn grimoire.main:app --host $BACKEND_HOST --port $BACKEND_PORT"
[ "$RELOAD" = "1" ] && UV_ARGS="$UV_ARGS --reload"
uv $UV_ARGS &
BACKEND_PID=$!

cat > "$STATE_FILE" <<EOF
BACKEND_PID=$BACKEND_PID
BACKEND_PORT=$BACKEND_PORT
FRONTEND_PORT=$FRONTEND_PORT
EOF

echo "Waiting for backend on port $BACKEND_PORT..."
elapsed=0
while [ "$elapsed" -lt 30 ]; do
    if curl -sf "http://${BACKEND_HOST}:${BACKEND_PORT}/api/health" >/dev/null 2>&1; then
        break
    fi
    sleep 1
    elapsed=$((elapsed + 1))
done
if [ "$elapsed" -ge 30 ]; then
    echo "Backend failed to start within 30s"
    exit 1
fi
echo "Backend ready."

(cd "$FRONTEND_DIR" && pnpm dev --port "$FRONTEND_PORT" --host "$FRONTEND_HOST") &
FRONTEND_PID=$!

cat > "$STATE_FILE" <<EOF
BACKEND_PID=$BACKEND_PID
FRONTEND_PID=$FRONTEND_PID
BACKEND_PORT=$BACKEND_PORT
FRONTEND_PORT=$FRONTEND_PORT
EOF

if [ "$OPEN_BROWSER" = "1" ]; then
    sleep 2
    case "$(uname -s)" in
        Darwin) open "http://localhost:$FRONTEND_PORT" ;;
        *) xdg-open "http://localhost:$FRONTEND_PORT" 2>/dev/null || true ;;
    esac
fi

echo "Grimoire running. Press Ctrl+C to stop."
wait
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/run.sh
```

- [ ] **Step 3: Commit**

```bash
git add scripts/run.sh
git commit -m "feat: rewrite run.sh with reliable trap-based cleanup"
```

---

### Task 7: Write setup.ps1

**Files:**
- Create: `scripts/setup.ps1`

- [ ] **Step 1: Write setup.ps1**

Create `scripts/setup.ps1`:

```powershell
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

# Python 3.12+
if (-not (Test-Cmd "python")) {
    Request-Install "Python 3.12+" "winget install Python.Python.3.12 --accept-package-agreements" "https://python.org"
} elseif (-not (Assert-MinVersion "Python" (& python --version 2>&1) 3 12)) {
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
```

- [ ] **Step 2: Test setup.ps1**

```powershell
.\scripts\setup.bat
```

Verify:
1. All prerequisites show green checkmarks (already installed)
2. Backend deps install (uv sync)
3. Frontend deps install (pnpm install)
4. Desktop shortcut appears on Desktop with the grimoire icon
5. Double-click the shortcut → Grimoire starts (run.ps1 fires)

- [ ] **Step 3: Commit**

```bash
git add scripts/setup.ps1
git commit -m "feat: add setup.ps1 with prereq checking and desktop shortcut"
```

---

### Task 8: Write setup.sh

**Files:**
- Create: `scripts/setup.sh`

- [ ] **Step 1: Write setup.sh**

Create `scripts/setup.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OS="$(uname -s)"

echo "=== Grimoire Setup ==="
echo ""

request_install() {
    local name="$1" command="$2" url="$3"
    echo "  $name is not installed."
    echo -n "  Install it now? ($command) [Y/n] "
    read -r answer
    if [ "$answer" = "" ] || [ "$answer" = "y" ] || [ "$answer" = "Y" ]; then
        echo "  Installing $name..."
        eval "$command"
    else
        echo "  Grimoire requires $name. Install manually: $url"
        exit 1
    fi
}

platform_install() {
    local name="$1" brew_cmd="$2" apt_cmd="$3" url="$4"
    case "$OS" in
        Darwin) request_install "$name" "$brew_cmd" "$url" ;;
        *)      request_install "$name" "$apt_cmd" "$url" ;;
    esac
}

echo "Checking prerequisites..."

# Python 3.12+
if ! command -v python3 &>/dev/null; then
    platform_install "Python 3.12+" "brew install python@3.12" "sudo apt install python3.12 python3.12-venv" "https://python.org"
else
    py_ver=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    py_major=${py_ver%%.*}
    py_minor=${py_ver##*.}
    if [ "$py_major" -lt 3 ] || { [ "$py_major" -eq 3 ] && [ "$py_minor" -lt 12 ]; }; then
        echo "  Python $py_ver found, but 3.12+ required."
        platform_install "Python 3.12+" "brew install python@3.12" "sudo apt install python3.12 python3.12-venv" "https://python.org"
    else
        echo "  Python $py_ver"
    fi
fi

# Node 20+
if ! command -v node &>/dev/null; then
    platform_install "Node.js 20+" "brew install node@20" "sudo apt install nodejs" "https://nodejs.org"
else
    node_major=$(node --version | sed 's/v\([0-9]*\).*/\1/')
    if [ "$node_major" -lt 20 ]; then
        echo "  Node $node_major found, but 20+ required."
        platform_install "Node.js 20+" "brew install node@20" "sudo apt install nodejs" "https://nodejs.org"
    else
        echo "  Node $node_major"
    fi
fi

# uv
if ! command -v uv &>/dev/null; then
    request_install "uv" "curl -LsSf https://astral.sh/uv/install.sh | sh" "https://docs.astral.sh/uv/"
else
    echo "  uv"
fi

# pnpm
if ! command -v pnpm &>/dev/null; then
    request_install "pnpm" "corepack enable && corepack prepare pnpm@latest --activate" "https://pnpm.io"
else
    echo "  pnpm"
fi

echo ""
echo "Installing dependencies..."

echo "  Backend (uv sync)..."
(cd "$REPO_ROOT/backend" && uv sync)

echo "  Frontend (pnpm install)..."
(cd "$REPO_ROOT/frontend" && pnpm install)

echo ""
echo "Creating desktop shortcut..."

case "$OS" in
    Darwin)
        APP_DIR="$HOME/Desktop/Grimoire.app"
        mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

        cat > "$APP_DIR/Contents/MacOS/Grimoire" <<LAUNCHER
#!/bin/bash
cd "$REPO_ROOT"
open -a Terminal ./scripts/run.sh
LAUNCHER
        chmod +x "$APP_DIR/Contents/MacOS/Grimoire"

        cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>Grimoire</string>
    <key>CFBundleIconFile</key>
    <string>grimoire</string>
    <key>CFBundleName</key>
    <string>Grimoire</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
</dict>
</plist>
PLIST

        if command -v iconutil &>/dev/null; then
            ICONSET=$(mktemp -d)/grimoire.iconset
            mkdir -p "$ICONSET"
            cp "$REPO_ROOT/assets/icons/grimoire-16.png"  "$ICONSET/icon_16x16.png"
            cp "$REPO_ROOT/assets/icons/grimoire-32.png"  "$ICONSET/icon_16x16@2x.png"
            cp "$REPO_ROOT/assets/icons/grimoire-32.png"  "$ICONSET/icon_32x32.png"
            cp "$REPO_ROOT/assets/icons/grimoire-64.png"  "$ICONSET/icon_32x32@2x.png"
            cp "$REPO_ROOT/assets/icons/grimoire-128.png" "$ICONSET/icon_128x128.png"
            cp "$REPO_ROOT/assets/icons/grimoire-256.png" "$ICONSET/icon_128x128@2x.png"
            cp "$REPO_ROOT/assets/icons/grimoire-256.png" "$ICONSET/icon_256x256.png"
            cp "$REPO_ROOT/assets/icons/grimoire.png"     "$ICONSET/icon_256x256@2x.png"
            cp "$REPO_ROOT/assets/icons/grimoire.png"     "$ICONSET/icon_512x512.png"
            iconutil -c icns "$ICONSET" -o "$APP_DIR/Contents/Resources/grimoire.icns"
            rm -rf "$(dirname "$ICONSET")"
        else
            echo "  Note: iconutil not found, app will use default icon."
        fi
        echo "  $APP_DIR"
        ;;
    *)
        DESKTOP_DIR="$HOME/.local/share/applications"
        mkdir -p "$DESKTOP_DIR"
        DESKTOP_FILE="$DESKTOP_DIR/grimoire.desktop"
        cat > "$DESKTOP_FILE" <<DESKTOP
[Desktop Entry]
Name=Grimoire
Exec=$REPO_ROOT/scripts/run.sh
Icon=$REPO_ROOT/assets/icons/grimoire-256.png
Terminal=true
Type=Application
Categories=Game;
DESKTOP
        chmod +x "$DESKTOP_FILE"
        if [ -d "$HOME/Desktop" ]; then
            cp "$DESKTOP_FILE" "$HOME/Desktop/grimoire.desktop"
            chmod +x "$HOME/Desktop/grimoire.desktop"
        fi
        echo "  $DESKTOP_FILE"
        ;;
esac

echo ""
echo "=== Setup complete! ==="
echo "Double-click the Grimoire shortcut on your desktop, or run ./scripts/run.sh"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/setup.sh
```

- [ ] **Step 3: Commit**

```bash
git add scripts/setup.sh
git commit -m "feat: add setup.sh with prereq checking and desktop shortcut (macOS/Linux)"
```

---

### Task 9: Delete Old Scripts and Clean Up

**Files:**
- Delete: `scripts/_lib.sh`
- Delete: `scripts/install.sh`

The old `run.sh` and `shutdown.sh` were already replaced in-place by Tasks 4 and 6.

- [ ] **Step 1: Delete old files**

```bash
git rm scripts/_lib.sh scripts/install.sh
```

- [ ] **Step 2: Verify no references to deleted files**

Search for any references to `_lib.sh` or `install.sh` in the codebase:

```bash
grep -r "_lib.sh\|install.sh" --include="*.sh" --include="*.md" --include="*.yml" --include="*.yaml" .
```

If found in documentation (README.md, ARCHITECTURE.md, CI config), update those references:
- `scripts/install.sh` → `scripts/setup.sh` (or `scripts/setup.bat` on Windows)
- Remove any mention of `scripts/_lib.sh` (internal implementation detail)

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove old cross-platform scripts (_lib.sh, install.sh)"
```

---

### Task 10: End-to-End Verification

No new files — this task verifies the full flow works.

- [ ] **Step 1: Test the full setup-to-run flow on Windows**

From a clean state (no running servers, no .grimoire-run.env):

```powershell
.\scripts\setup.bat
```

Verify: prereqs checked, deps installed, desktop shortcut created with grimoire icon.

- [ ] **Step 2: Test run via desktop shortcut**

Double-click "Grimoire" shortcut on Desktop.

Verify:
1. Terminal opens titled "Grimoire Server"
2. Backend starts, health check passes
3. Frontend starts
4. Browser opens to the app
5. App is functional

- [ ] **Step 3: Test Ctrl+C shutdown**

Press Ctrl+C in the Grimoire Server terminal.

Verify:
1. "Shutting down..." message appears
2. Both backend and frontend processes are killed
3. "Grimoire stopped." message appears
4. `.grimoire-run.env` is gone
5. Ports 8173 and 5173 are free: `Get-NetTCPConnection -LocalPort 8173 -ErrorAction SilentlyContinue` returns nothing

- [ ] **Step 4: Test shutdown script as safety net**

Start Grimoire via `.\scripts\run.bat`. In a separate terminal:

```powershell
.\scripts\shutdown.bat
```

Verify: both servers are stopped from the external terminal.

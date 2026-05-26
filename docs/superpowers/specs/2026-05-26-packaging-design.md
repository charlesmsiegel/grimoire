# Packaging & Cross-Platform Setup Design

**Date**: 2026-05-26
**Status**: Draft
**Issue**: N/A (packaging infrastructure)
**Related**: [#478](https://github.com/charlesmsiegel/grimoire/issues/478) — in-app icon usage (separate)

## Overview

Replace the current cross-platform bash scripts with platform-native scripts and add a setup flow that takes a user from a fresh `git clone` to a running app with a desktop shortcut. The existing scripts are hard to maintain because they embed two platforms' logic in every function via `_lib.sh`. This design eliminates that by giving each platform its own simple, self-contained scripts.

## Goals

1. Fresh clone → running app on Windows, macOS, and Linux with minimal manual steps
2. Desktop shortcut with the grimoire icon on all three platforms
3. Scripts that are short, readable, and platform-idiomatic
4. Reliable process cleanup on Ctrl+C and window close (no stale processes)

## Non-Goals

- Pre-built binaries, installers (.msi, .dmg, .deb), or Docker images
- Changing how the backend or frontend themselves work
- In-app icon usage (tracked in #478)

## Icon Assets

### Source

The 512×512 PNG at `assets/icons/grimoire.png` (a gold-embossed "G" grimoire book on a dark rounded-square background, transparent surround).

### Generated Assets

All committed to `assets/icons/`:

| File | Purpose |
|------|---------|
| `grimoire.png` | 512px source |
| `grimoire-256.png` | High-DPI desktop icons |
| `grimoire-128.png` | Large UI contexts |
| `grimoire-64.png` | Standard desktop icon |
| `grimoire-48.png` | Windows taskbar |
| `grimoire-32.png` | Small icon / favicon source |
| `grimoire-16.png` | Tiny icon |
| `grimoire.ico` | Windows (multi-res: 16, 32, 48, 256) |
| `grimoire.icns` | macOS app bundle |

Generated once during implementation using Pillow, then committed. No runtime icon generation.

## Script Architecture

### File Layout

```
scripts/
    # Unix (macOS + Linux)
    setup.sh
    run.sh
    shutdown.sh

    # Windows
    setup.bat          → calls setup.ps1
    run.bat            → calls run.ps1
    shutdown.bat       → calls shutdown.ps1
    setup.ps1
    run.ps1
    shutdown.ps1
```

### Removed Files

- `scripts/_lib.sh` — deleted (cross-platform abstraction layer; logic inlined per-platform)
- `scripts/install.sh` — deleted (replaced by `setup.sh` / `setup.ps1`)
- Current `scripts/run.sh` and `scripts/shutdown.sh` — replaced by new versions

### .bat Wrapper Pattern

Every `.bat` file is identical in structure:

```bat
@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0<name>.ps1" %*
```

One line of real logic. Exists so Windows users can double-click to launch.

### Configuration

All scripts use environment variables only — no CLI flag parsing. This eliminates ~40 lines of getopts/case per script.

| Variable | Default | Purpose |
|----------|---------|---------|
| `GRIMOIRE_BACKEND_HOST` | `127.0.0.1` | Backend bind address |
| `GRIMOIRE_BACKEND_PORT` | `8173` | Backend port |
| `GRIMOIRE_FRONTEND_HOST` | `127.0.0.1` | Frontend bind address |
| `GRIMOIRE_FRONTEND_PORT` | `5173` | Frontend port |
| `GRIMOIRE_BACKEND_RELOAD` | `0` | Enable uvicorn `--reload` |
| `GRIMOIRE_OPEN_BROWSER` | `1` | Auto-open browser on launch |
| `GRIMOIRE_DATA_ROOT` | `~/.grimoire/` | User content directory |

## Setup Scripts (`setup.sh` / `setup.ps1`)

`setup.sh` handles both macOS and Linux. It detects the platform at the top (`uname -s`) and uses the appropriate package manager commands (brew on macOS, apt/dnf on Linux) when offering to install prerequisites.

### Flow

1. **Check prerequisites** — Python 3.12+, Node 20+, uv, pnpm
2. **For each missing prerequisite** — print what's missing and offer to install:
   - Python: `winget install Python.Python.3.12` / `brew install python@3.12` / `sudo apt install python3.12`
   - Node: `winget install OpenJS.NodeJS.LTS` / `brew install node@20` / `sudo apt install nodejs`
   - uv: `irm https://astral.sh/uv/install.ps1 | iex` / `curl -LsSf https://astral.sh/uv/install.sh | sh`
   - pnpm: `corepack enable && corepack prepare pnpm@latest --activate`
3. **Install backend deps** — `cd backend && uv sync`
4. **Install frontend deps** — `cd frontend && pnpm install`
5. **Create desktop shortcut** (see below)
6. **Print success** — "Grimoire is ready. Run `scripts/run.bat` (or double-click the desktop shortcut) to start."

### Desktop Shortcut Creation

**Windows** (`setup.ps1`):
- Create a `.lnk` file on `$env:USERPROFILE\Desktop\Grimoire.lnk`
- Target: `<repo>\scripts\run.bat`
- Working directory: `<repo>`
- Icon: `<repo>\assets\icons\grimoire.ico`
- Use `WScript.Shell` COM object to create the `.lnk`

**macOS** (`setup.sh`):
- Create a minimal `.app` bundle at `~/Desktop/Grimoire.app`
- Structure:
  ```
  Grimoire.app/
    Contents/
      Info.plist        (CFBundleIconFile, CFBundleExecutable)
      MacOS/
        Grimoire        (shell script: cd <repo> && ./scripts/run.sh)
      Resources/
        grimoire.icns
  ```
- The `.app` bundle makes the icon show in Finder and Dock

**Linux** (`setup.sh`):
- Write `grimoire.desktop` to `~/.local/share/applications/`
  ```ini
  [Desktop Entry]
  Name=Grimoire
  Exec=<repo>/scripts/run.sh
  Icon=<repo>/assets/icons/grimoire-256.png
  Terminal=true
  Type=Application
  Categories=Game;
  ```
- Copy to `~/Desktop/` if that directory exists
- Run `chmod +x` on the `.desktop` file

## Run Scripts (`run.sh` / `run.ps1`)

### Flow

1. **Set terminal title** to "Grimoire Server"
2. **Check ports** — if anything is on the backend or frontend port:
   - Show the process name/PID
   - Ask: "Kill it, use a different port, or abort?"
   - "Different port" auto-increments by 1 (e.g., 8173→8174) and rechecks
3. **Start backend** — `uv run uvicorn grimoire.main:app --host $host --port $port`
   - If `GRIMOIRE_BACKEND_RELOAD=1`, add `--reload`
4. **Write state file** — `.grimoire-run.env` with backend PID, frontend PID, ports
5. **Wait for health** — poll `http://$host:$port/api/health` (timeout after 30s)
6. **Start frontend** — `cd frontend && pnpm dev --port $fport --host $fhost`
7. **Update state file** with frontend PID
8. **Open browser** — `http://localhost:$fport` (unless `GRIMOIRE_OPEN_BROWSER=0`)
9. **Wait** — both processes run in foreground as children of the script

### Reliable Child Cleanup

The core improvement over the current scripts. When the script exits for any reason (Ctrl+C, window close, error), both child processes and their descendants are killed.

**Unix (`run.sh`)**:
```
trap cleanup EXIT
```
The `cleanup` function:
1. Sends SIGTERM to the backend and frontend PIDs
2. Waits up to 5 seconds for graceful exit
3. SIGKILLs any survivors
4. Runs WAL checkpoint (Python one-liner via `uv run`)
5. Removes `.grimoire-run.env`

Both backend and frontend are started as background processes (`&`) with their PIDs captured. The script then `wait`s on them, which makes it responsive to signals.

**Windows (`run.ps1`)**:
```powershell
try {
    $backend = Start-Process ... -PassThru
    $frontend = Start-Process ... -PassThru
    # Wait for either to exit
} finally {
    # Runs on Ctrl+C, window close, or error
    Stop-ProcessTree $backend.Id
    Stop-ProcessTree $frontend.Id
    # WAL checkpoint
    # Remove .grimoire-run.env
}
```

`Stop-ProcessTree` uses `Get-CimInstance Win32_Process` to find child processes (uvicorn spawns workers, node spawns vite) and kills the full tree.

## Shutdown Scripts (`shutdown.sh` / `shutdown.ps1`)

Safety net for when the run script's cleanup didn't fire (e.g., machine crash, killed terminal).

### Flow

1. **Read `.grimoire-run.env`** — if it exists, use the recorded PIDs
   - Verify each PID is still running and is a grimoire-related process before killing
   - Kill confirmed PIDs
2. **If no state file** — scan for processes on the configured ports
   - Show what's running (process name, PID, command line)
   - **Ask before killing** — "Is this a Grimoire process? Kill it? [y/N]"
3. **WAL checkpoint** — `uv run python -c "import sqlite3; ..."`
4. **Remove `.grimoire-run.env`**

## Size Targets

Each script should be straightforward and readable. Target line counts:

| Script | Target |
|--------|--------|
| `setup.sh` | ~100 lines |
| `setup.ps1` | ~100 lines |
| `run.sh` | ~80 lines |
| `run.ps1` | ~80 lines |
| `shutdown.sh` | ~50 lines |
| `shutdown.ps1` | ~50 lines |
| Each `.bat` | 2 lines |

Total: ~470 lines across 9 files, replacing ~600 lines across 4 files that were harder to read.

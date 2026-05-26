#!/usr/bin/env bash
# Shared helpers for grimoire scripts (install.sh / run.sh / shutdown.sh).
# Source this file; do not execute it directly.

# Detect platform once. Sets PLATFORM to one of: linux, mac, windows, unknown.
_grimoire_detect_platform() {
    case "$(uname -s)" in
        Linux*)              PLATFORM=linux ;;
        Darwin*)             PLATFORM=mac ;;
        MINGW*|MSYS*|CYGWIN*) PLATFORM=windows ;;
        *)
            # Fall back to the Windows-set $OS env var so a non-MSYS bash
            # spawned from PowerShell (where uname -s returns an unrecognised
            # banner) still picks up the Windows code paths instead of
            # falling through to "unknown" and skipping kill_port entirely.
            if [ "${OS:-}" = "Windows_NT" ]; then
                PLATFORM=windows
            else
                PLATFORM=unknown
            fi
            ;;
    esac
}
_grimoire_detect_platform

# Open a URL in the user's default browser. Best effort: never fails the caller.
open_url() {
    local url="$1"
    case "$PLATFORM" in
        mac)     open "$url" >/dev/null 2>&1 || true ;;
        linux)   xdg-open "$url" >/dev/null 2>&1 || true ;;
        windows) cmd.exe /c start "" "$url" >/dev/null 2>&1 || true ;;
        *)       python3 -m webbrowser "$url" >/dev/null 2>&1 || true ;;
    esac
}

# Resolve the pnpm command. Echoes a single token: "pnpm.cmd", "pnpm", or
# "corepack pnpm". Returns non-zero with an error on stderr if none available.
resolve_pnpm() {
    # On Windows, prefer pnpm.cmd. The npm-installed pnpm ships a POSIX shell
    # wrapper that runs `cygpath -w "$basedir"` to translate /c/... to C:\...;
    # if PATH puts a foreign MSYS root (e.g. Anaconda's
    # Library/usr/bin/cygpath) ahead of Git Bash's, that translation produces
    # a path like C:\...\anaconda3\Library\c\Users\... and node fails with
    # MODULE_NOT_FOUND. pnpm.cmd skips cygpath entirely.
    if command -v pnpm.cmd >/dev/null 2>&1; then
        printf 'pnpm.cmd'
    elif command -v pnpm >/dev/null 2>&1; then
        printf 'pnpm'
    elif command -v corepack >/dev/null 2>&1; then
        printf 'corepack pnpm'
    else
        echo "error: neither 'pnpm' nor 'corepack' found on PATH." >&2
        echo "       Install pnpm from https://pnpm.io/installation" >&2
        return 1
    fi
}

# Print the PIDs listening on a given TCP port, one per line. Silent if none.
# Cross-platform: uses lsof on macOS/Linux, netstat on Windows.
pids_on_port() {
    local port="$1"
    case "$PLATFORM" in
        windows)
            netstat -ano 2>/dev/null \
                | grep "LISTENING" \
                | grep -E ":${port}\s" \
                | awk '{print $5}' \
                | sort -u || true
            ;;
        *)
            if command -v lsof >/dev/null 2>&1; then
                lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
            elif command -v ss >/dev/null 2>&1; then
                # ss output: users:(("python",pid=1234,fd=5))
                ss -ltnp "sport = :$port" 2>/dev/null \
                    | grep -oE 'pid=[0-9]+' \
                    | cut -d= -f2 \
                    | sort -u || true
            elif command -v fuser >/dev/null 2>&1; then
                fuser "${port}/tcp" 2>/dev/null | tr -s ' ' '\n' | sed '/^$/d' || true
            fi
            ;;
    esac
}

# Send SIGTERM (or platform equivalent) to a PID. Best effort.
kill_pid() {
    local pid="$1"
    [ -z "$pid" ] && return 0
    case "$PLATFORM" in
        windows) taskkill //F //T //PID "$pid" >/dev/null 2>&1 || true ;;
        *)       kill -TERM "$pid" 2>/dev/null || true ;;
    esac
}

# Kill any process listening on the given port. Echoes a one-line report
# for each PID it tried to kill. Idempotent; safe if no process is listening.
# Polls afterwards so a TOCTOU race (kill returned but the OS hasn't released
# the LISTENING entry yet, or a sibling process bound between enumerate and
# kill) is more likely to surface as our wait-loop failing than as the next
# bind hitting "address already in use".
kill_port() {
    local port="$1" label="${2:-port $1}"
    local pid
    while IFS= read -r pid; do
        [ -z "$pid" ] && continue
        echo "==> Killing PID $pid on $label ($port)"
        kill_pid "$pid"
    done < <(pids_on_port "$port")
    # Wait up to ~2s for the port to actually be free. Without this the
    # caller would race uvicorn/vite against a still-listening socket.
    local attempts=0
    while [ "$attempts" -lt 20 ]; do
        if [ -z "$(pids_on_port "$port")" ]; then
            return 0
        fi
        sleep 0.1
        attempts=$((attempts + 1))
    done
    echo "==> Warning: $label ($port) still has listeners after kill" >&2
}

# Print PIDs of running processes whose command line references the given path.
# Used to detect grimoire dev servers regardless of which port they bound to.
# One PID per line. Windows-only today; no-op (silent) on other platforms.
pids_using_path() {
    local path="$1"
    [ -z "$path" ] && return 0
    case "$PLATFORM" in
        windows)
            command -v powershell.exe >/dev/null 2>&1 || return 0
            # Forward slashes are friendlier for PowerShell -match (regex);
            # the substring match catches both slash styles in CommandLine.
            # Filter by process name to avoid matching shells/helpers that
            # happen to have the repo path in their argv (e.g. bash running
            # the install script itself).
            local needle
            needle="$(printf '%s' "$path" | sed 's|\\|/|g')"
            powershell.exe -NoProfile -Command "
                \$needle = [regex]::Escape('$needle')
                Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='uvicorn.exe' OR Name='node.exe'\" |
                  Where-Object { \$_.CommandLine -and (\$_.CommandLine -replace '\\\\','/') -match \$needle } |
                  Select-Object -ExpandProperty ProcessId
            " 2>/dev/null | tr -d '\r' | sed '/^$/d' || true
            ;;
        *)
            # POSIX: ps + grep on full command line.
            ps -eo pid=,args= 2>/dev/null \
                | awk -v needle="$path" 'index($0, needle) { print $1 }' \
                | sed '/^$/d' || true
            ;;
    esac
}

# Kill stray uvicorn workers spawned by --reload that outlive their parent
# on Windows (WinError 87). No-op on other platforms.
kill_orphaned_uvicorn_workers() {
    [ "$PLATFORM" = "windows" ] || return 0
    local pids
    pids="$(tasklist /FI "IMAGENAME eq python.exe" /FO CSV /NH 2>/dev/null \
        | grep -i "python" \
        | while IFS=, read -r _name pid _rest; do
            pid="${pid//\"/}"
            [ -z "$pid" ] && continue
            wmic process where "ProcessId=$pid" get CommandLine 2>/dev/null \
                | grep -q "multiprocessing.spawn" && echo "$pid"
        done || true)"
    local pid
    for pid in $pids; do
        [ -z "$pid" ] && continue
        echo "==> Killing orphan multiprocessing worker PID $pid"
        kill_pid "$pid"
    done
}

# Probe a URL until it responds or a timeout passes.
# Args: url, timeout_seconds (default 30). Returns 0 on success, 1 on timeout.
wait_for_url() {
    local url="$1" timeout="${2:-30}"
    local deadline=$(( $(date +%s) + timeout ))
    if command -v curl >/dev/null 2>&1; then
        while [ "$(date +%s)" -lt "$deadline" ]; do
            curl -fsS -o /dev/null --max-time 1 "$url" 2>/dev/null && return 0
            sleep 0.5
        done
        return 1
    fi
    # No curl: just sleep a fixed amount and assume readiness.
    sleep 3
    return 0
}

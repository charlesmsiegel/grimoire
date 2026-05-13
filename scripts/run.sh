#!/usr/bin/env bash
# Run grimoire backend (uvicorn) and frontend (vite) together.
# Ctrl-C stops both.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BACKEND_HOST="${GRIMOIRE_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${GRIMOIRE_BACKEND_PORT:-8000}"
FRONTEND_HOST="${GRIMOIRE_FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${GRIMOIRE_FRONTEND_PORT:-5173}"
FRONTEND_URL="http://${FRONTEND_HOST}:${FRONTEND_PORT}"
OPEN_BROWSER="${GRIMOIRE_OPEN_BROWSER:-1}"
# Backend autoreload is opt-in: uvicorn's WatchFiles reloader is unstable on
# Windows (multiprocessing spawn intermittently fails with WinError 87). Set
# GRIMOIRE_BACKEND_RELOAD=1 to re-enable it.
BACKEND_RELOAD="${GRIMOIRE_BACKEND_RELOAD:-0}"

open_url() {
    local url="$1"
    case "$(uname -s)" in
        Darwin*) open "$url" >/dev/null 2>&1 || true ;;
        Linux*)  xdg-open "$url" >/dev/null 2>&1 || true ;;
        MINGW*|MSYS*|CYGWIN*) cmd.exe /c start "" "$url" >/dev/null 2>&1 || true ;;
        *) python3 -m webbrowser "$url" >/dev/null 2>&1 || true ;;
    esac
}

wait_then_open() {
    local url="$1"
    for _ in $(seq 1 60); do
        if curl -fsS -o /dev/null --max-time 1 "$url" 2>/dev/null; then
            echo "==> Opening $url in browser"
            open_url "$url"
            return 0
        fi
        sleep 0.5
    done
    echo "warning: frontend did not respond at $url within 30s; not opening browser" >&2
}

# Kill any process currently bound to the ports we're about to use, plus any
# orphaned uvicorn workers from a prior crashed --reload spawn. Runs as the
# first step so the script can recover from a wedged previous run without
# manual cleanup. Idempotent and safe to run when nothing is listening.
kill_stale() {
    local os
    os="$(uname -s)"
    case "$os" in
        MINGW*|MSYS*|CYGWIN*)
            for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
                # netstat output: "  TCP  127.0.0.1:8000  0.0.0.0:0  LISTENING  12080"
                # `|| true` keeps set -e/pipefail happy when no rows match grep.
                local pids
                pids="$(netstat -ano 2>/dev/null \
                    | grep "LISTENING" \
                    | grep -E ":${port}\s" \
                    | awk '{print $5}' \
                    | sort -u || true)"
                for pid in $pids; do
                    [ -z "$pid" ] && continue
                    echo "==> Killing PID $pid on port $port"
                    taskkill //F //T //PID "$pid" >/dev/null 2>&1 || true
                done
            done
            # Multiprocessing-spawned uvicorn workers can outlive their parent
            # when --reload crashes (WinError 87). They don't always hold a
            # port at lookup time, but they will when uvicorn restarts.
            local orphan_pids
            orphan_pids="$(powershell.exe -NoProfile -Command "
                Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" |
                  Where-Object { \$_.CommandLine -match 'multiprocessing.spawn' } |
                  Select-Object -ExpandProperty ProcessId
            " 2>/dev/null | tr -d '\r' || true)"
            for pid in $orphan_pids; do
                [ -z "$pid" ] && continue
                echo "==> Killing orphan multiprocessing worker PID $pid"
                taskkill //F //PID "$pid" >/dev/null 2>&1 || true
            done
            ;;
        Darwin*|Linux*)
            for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
                if command -v lsof >/dev/null 2>&1; then
                    local pids
                    pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
                    for pid in $pids; do
                        echo "==> Killing PID $pid on port $port"
                        kill -TERM "$pid" 2>/dev/null || true
                    done
                fi
            done
            ;;
    esac
}

if command -v pnpm >/dev/null 2>&1; then
    PNPM=(pnpm)
elif command -v corepack >/dev/null 2>&1; then
    PNPM=(corepack pnpm)
else
    echo "error: neither 'pnpm' nor 'corepack' found on PATH. Run scripts/install.sh first." >&2
    exit 1
fi

echo "==> Clearing any stale grimoire processes on ports $BACKEND_PORT / $FRONTEND_PORT"
kill_stale

backend_pid=""
frontend_pid=""

cleanup() {
    trap - INT TERM EXIT
    if [ -n "$frontend_pid" ] && kill -0 "$frontend_pid" 2>/dev/null; then
        kill "$frontend_pid" 2>/dev/null || true
    fi
    if [ -n "$backend_pid" ] && kill -0 "$backend_pid" 2>/dev/null; then
        kill "$backend_pid" 2>/dev/null || true
    fi
    wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

reload_args=()
if [ "$BACKEND_RELOAD" = "1" ]; then
    reload_args+=(--reload)
    echo "==> Backend autoreload ENABLED (GRIMOIRE_BACKEND_RELOAD=1)"
fi

echo "==> Starting backend on http://${BACKEND_HOST}:${BACKEND_PORT}"
(
    cd "$REPO_ROOT/backend"
    exec uv run uvicorn grimoire.main:app "${reload_args[@]}" --host "$BACKEND_HOST" --port "$BACKEND_PORT"
) &
backend_pid=$!

echo "==> Starting frontend (vite dev server) on $FRONTEND_URL"
(
    cd "$REPO_ROOT/frontend"
    exec "${PNPM[@]}" dev --host "$FRONTEND_HOST" --port "$FRONTEND_PORT"
) &
frontend_pid=$!

if [ "$OPEN_BROWSER" = "1" ]; then
    wait_then_open "$FRONTEND_URL" &
fi

# Exit as soon as either process exits.
wait -n "$backend_pid" "$frontend_pid"

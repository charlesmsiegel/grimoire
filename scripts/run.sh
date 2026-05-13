#!/usr/bin/env bash
# Run grimoire backend (uvicorn) and frontend (vite) together.
# Ctrl-C stops both. A state file (.grimoire-run.env) is written so that
# scripts/shutdown.sh can find and clean up the processes from any terminal.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=_lib.sh
. "$REPO_ROOT/scripts/_lib.sh"

BACKEND_HOST="${GRIMOIRE_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${GRIMOIRE_BACKEND_PORT:-8000}"
FRONTEND_HOST="${GRIMOIRE_FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${GRIMOIRE_FRONTEND_PORT:-5173}"
OPEN_BROWSER="${GRIMOIRE_OPEN_BROWSER:-1}"
# Backend autoreload is opt-in: uvicorn's WatchFiles reloader is unstable on
# Windows (multiprocessing spawn intermittently fails with WinError 87).
BACKEND_RELOAD="${GRIMOIRE_BACKEND_RELOAD:-0}"
KILL_STALE=1
STATE_FILE="$REPO_ROOT/.grimoire-run.env"

usage() {
    cat <<'EOF'
Usage: scripts/run.sh [options]

Starts the backend (uvicorn) and frontend (vite) dev servers, then opens
the site in the default browser. Ctrl-C stops both.

Options:
  --backend-port N      Backend port (default 8000)
  --frontend-port N     Frontend port (default 5173)
  --backend-host H      Backend bind host (default 127.0.0.1)
  --frontend-host H     Frontend bind host (default 127.0.0.1)
  --no-browser          Don't open the site in a browser
  --reload              Enable backend autoreload (uvicorn --reload)
  --no-kill-stale       Don't kill stale processes on the chosen ports first
  -h, --help            Show this help

Environment overrides (used when the matching flag is absent):
  GRIMOIRE_BACKEND_PORT, GRIMOIRE_FRONTEND_PORT,
  GRIMOIRE_BACKEND_HOST, GRIMOIRE_FRONTEND_HOST,
  GRIMOIRE_OPEN_BROWSER (0/1), GRIMOIRE_BACKEND_RELOAD (0/1)
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --backend-port)   BACKEND_PORT="$2"; shift 2 ;;
        --frontend-port)  FRONTEND_PORT="$2"; shift 2 ;;
        --backend-host)   BACKEND_HOST="$2"; shift 2 ;;
        --frontend-host)  FRONTEND_HOST="$2"; shift 2 ;;
        --no-browser)     OPEN_BROWSER=0; shift ;;
        --reload)         BACKEND_RELOAD=1; shift ;;
        --no-kill-stale)  KILL_STALE=0; shift ;;
        -h|--help)        usage; exit 0 ;;
        --) shift; break ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

FRONTEND_URL="http://${FRONTEND_HOST}:${FRONTEND_PORT}"

pnpm_cmd="$(resolve_pnpm)" || { echo "       Run scripts/install.sh first." >&2; exit 1; }
# shellcheck disable=SC2206
PNPM=($pnpm_cmd)

if [ "$KILL_STALE" = "1" ]; then
    echo "==> Clearing any stale grimoire processes on ports $BACKEND_PORT / $FRONTEND_PORT"
    kill_port "$BACKEND_PORT" "backend port"
    kill_port "$FRONTEND_PORT" "frontend port"
    kill_orphaned_uvicorn_workers
fi

backend_pid=""
frontend_pid=""

cleanup() {
    trap - INT TERM EXIT
    if [ -n "$frontend_pid" ] && kill -0 "$frontend_pid" 2>/dev/null; then
        kill_pid "$frontend_pid"
    fi
    if [ -n "$backend_pid" ] && kill -0 "$backend_pid" 2>/dev/null; then
        kill_pid "$backend_pid"
    fi
    # Belt-and-braces: sweep the ports in case child processes ran detached
    # (common with vite's worker on Windows).
    kill_port "$BACKEND_PORT" "backend port"
    kill_port "$FRONTEND_PORT" "frontend port"
    rm -f "$STATE_FILE" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

reload_args=()
if [ "$BACKEND_RELOAD" = "1" ]; then
    reload_args+=(--reload)
    echo "==> Backend autoreload ENABLED"
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
    # Vite reads these to wire its proxy /api -> backend, /ws -> backend.
    export GRIMOIRE_BACKEND_HOST="$BACKEND_HOST"
    export GRIMOIRE_BACKEND_PORT="$BACKEND_PORT"
    export GRIMOIRE_FRONTEND_PORT="$FRONTEND_PORT"
    exec "${PNPM[@]}" dev --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --strictPort
) &
frontend_pid=$!

# Record state so shutdown.sh can find these processes from another terminal.
# Written after both subshells are forked; PIDs are the bash-subshell PIDs,
# which is good enough for `kill` to bring the tree down.
umask 077
cat > "$STATE_FILE" <<EOF
# grimoire run-state, written by scripts/run.sh; consumed by scripts/shutdown.sh
backend_pid=$backend_pid
frontend_pid=$frontend_pid
backend_host=$BACKEND_HOST
backend_port=$BACKEND_PORT
frontend_host=$FRONTEND_HOST
frontend_port=$FRONTEND_PORT
started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

if [ "$OPEN_BROWSER" = "1" ]; then
    (
        if wait_for_url "$FRONTEND_URL" 30; then
            echo "==> Opening $FRONTEND_URL in browser"
            open_url "$FRONTEND_URL"
        else
            echo "warning: frontend did not respond at $FRONTEND_URL within 30s; not opening browser" >&2
        fi
    ) &
fi

# Exit as soon as either process exits. `wait -n` is bash 4.3+; macOS still
# ships bash 3.2, so poll instead.
while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
    sleep 1
done

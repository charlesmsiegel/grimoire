#!/usr/bin/env bash
# Stop any grimoire backend/frontend processes started by scripts/run.sh
# and free the ports they were using. Safe to run when nothing is up.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=_lib.sh
. "$REPO_ROOT/scripts/_lib.sh"

STATE_FILE="$REPO_ROOT/.grimoire-run.env"

BACKEND_PORT=""
FRONTEND_PORT=""

usage() {
    cat <<'EOF'
Usage: scripts/shutdown.sh [options]

Stops grimoire backend/frontend processes and frees their ports. Resolves
ports in this order: CLI flag > .grimoire-run.env state file > environment
variable (GRIMOIRE_*_PORT) > default (8173 backend, 5173 frontend).

Options:
  --backend-port N    Backend port to free (overrides state file)
  --frontend-port N   Frontend port to free (overrides state file)
  -h, --help          Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --backend-port)  BACKEND_PORT="$2"; shift 2 ;;
        --frontend-port) FRONTEND_PORT="$2"; shift 2 ;;
        -h|--help)       usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

# Load state file (writes by run.sh). Defines backend_pid, frontend_pid,
# backend_port, frontend_port if present.
backend_pid=""
frontend_pid=""
backend_port=""
frontend_port=""
if [ -f "$STATE_FILE" ]; then
    echo "==> Reading state from $STATE_FILE"
    # shellcheck disable=SC1090
    . "$STATE_FILE"
fi

# Precedence: CLI > state file > env > default.
: "${BACKEND_PORT:=${backend_port:-${GRIMOIRE_BACKEND_PORT:-8173}}}"
: "${FRONTEND_PORT:=${frontend_port:-${GRIMOIRE_FRONTEND_PORT:-5173}}}"

# 1. Kill recorded PIDs first (most precise — won't touch unrelated services).
for pid in "$backend_pid" "$frontend_pid"; do
    [ -z "$pid" ] && continue
    if kill -0 "$pid" 2>/dev/null; then
        echo "==> Killing recorded PID $pid"
        kill_pid "$pid"
    fi
done

# 2. Sweep the ports. Catches detached child processes (vite's esbuild worker,
# uvicorn --reload workers) and anything from a crashed prior run.
echo "==> Freeing ports: backend=$BACKEND_PORT frontend=$FRONTEND_PORT"
kill_port "$BACKEND_PORT"  "backend port"
kill_port "$FRONTEND_PORT" "frontend port"
kill_orphaned_uvicorn_workers

# 3. Give terminating processes a moment, then escalate to SIGKILL for any
# stragglers still bound to a port.
sleep 1
for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
    while IFS= read -r pid; do
        [ -z "$pid" ] && continue
        echo "==> Process $pid still on port $port; sending SIGKILL"
        case "$PLATFORM" in
            windows) taskkill //F //T //PID "$pid" >/dev/null 2>&1 || true ;;
            *)       kill -KILL "$pid" 2>/dev/null || true ;;
        esac
    done < <(pids_on_port "$port")
done

# 4. Checkpoint the SQLite WAL so the next startup sees a clean lock state.
# A hard kill or crash can leave -wal/-shm files that trigger "database is
# locked" on the next boot. Checkpointing flushes the WAL into the main DB
# and removes those files. Best effort — skip if the DB doesn't exist or
# Python can't open it.
_checkpoint_wal() {
    local db_path="$1"
    [ -f "$db_path" ] || return 0
    local py
    py="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
    [ -z "$py" ] && return 0
    echo "==> Checkpointing WAL: $db_path"
    "$py" -c "
import sqlite3, sys
try:
    conn = sqlite3.connect(sys.argv[1])
    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    conn.close()
except Exception as e:
    print(f'   warning: WAL checkpoint skipped: {e}', file=sys.stderr)
" "$db_path" 2>&1 || true
}

# Try the default DB path (~/.grimoire/campaigns.sqlite) and the env override.
_default_db="${GRIMOIRE_DATABASE_PATH:-${HOME}/.grimoire/campaigns.sqlite}"
_checkpoint_wal "$_default_db"

rm -f "$STATE_FILE" 2>/dev/null || true

echo "==> Done. Ports $BACKEND_PORT and $FRONTEND_PORT are clear."

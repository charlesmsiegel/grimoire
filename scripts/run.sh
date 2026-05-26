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
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
    sleep 1
done

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

#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PIDFILE="$ROOT/.run/pids"

if [ ! -f "$PIDFILE" ]; then
  echo "grimoire is not running."
  exit 0
fi

while read -r pid; do
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
  fi
done < "$PIDFILE"
rm -f "$PIDFILE"
echo "grimoire stopped."

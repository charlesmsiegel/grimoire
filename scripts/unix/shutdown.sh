#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PIDFILE="$ROOT/.run/pids"

if [ ! -f "$PIDFILE" ]; then
  echo "grimoire is not running."
  exit 0
fi

# Kill each recorded process AND its descendants. The pidfile holds one line
# for a default launch (the backend serving the built UI) and two for --dev,
# where uvicorn --reload spawns a worker and npm spawns node -- and stopping
# only a parent leaves those children holding ports 8173/5173, breaking the
# next launch.
while read -r pid; do
  if kill -0 "$pid" 2>/dev/null; then
    pkill -TERM -P "$pid" 2>/dev/null || true
    kill "$pid" 2>/dev/null || true
    sleep 1
    pkill -9 -P "$pid" 2>/dev/null || true
    kill -9 "$pid" 2>/dev/null || true
  fi
done < "$PIDFILE"
rm -f "$PIDFILE"
echo "grimoire stopped."

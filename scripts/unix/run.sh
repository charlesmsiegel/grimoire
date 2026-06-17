#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNDIR="$ROOT/.run"
PIDFILE="$RUNDIR/pids"
URL="http://localhost:5173"
mkdir -p "$RUNDIR"

if [ -f "$PIDFILE" ] && kill -0 $(head -n1 "$PIDFILE") 2>/dev/null; then
  echo "grimoire is already running ($URL). Use shutdown.sh to stop it."
  exit 0
fi

cd "$ROOT/backend"
.venv/bin/python -m uvicorn grimoire.main:app --reload --port 8000 &
BACK=$!
cd "$ROOT/frontend"
npm run dev -- --port 5173 &
FRONT=$!
echo "$BACK" > "$PIDFILE"
echo "$FRONT" >> "$PIDFILE"

echo "grimoire running at $URL (backend pid $BACK, frontend pid $FRONT)"
sleep 2
if command -v open >/dev/null; then open "$URL"
elif command -v xdg-open >/dev/null; then xdg-open "$URL"
fi
wait

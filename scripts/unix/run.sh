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

# Wait for the frontend dev server to accept connections before opening a browser.
# Vite's cold start (first run pre-bundles deps) can take well over a fixed delay.
printf "Waiting for frontend to be ready"
ready=
for _ in $(seq 1 60); do
  if (exec 3<>/dev/tcp/localhost/5173) 2>/dev/null; then
    exec 3>&- 3<&-
    ready=1
    break
  fi
  printf "."
  sleep 1
done
echo
if [ -z "$ready" ]; then
  echo "Frontend did not become ready in time. Check logs; opening $URL anyway."
fi

if command -v open >/dev/null; then open "$URL"
elif command -v xdg-open >/dev/null; then xdg-open "$URL"
fi
wait

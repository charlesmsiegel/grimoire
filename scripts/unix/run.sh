#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNDIR="$ROOT/.run"
PIDFILE="$RUNDIR/pids"
URL="http://127.0.0.1:5173"
mkdir -p "$RUNDIR"

if [ -f "$PIDFILE" ] && kill -0 $(head -n1 "$PIDFILE") 2>/dev/null; then
  echo "grimoire is already running ($URL). Use shutdown.sh to stop it."
  exit 0
fi

cd "$ROOT/backend"
.venv/bin/python -m uvicorn grimoire.main:app --reload --port 8173 &
BACK=$!
cd "$ROOT/frontend"
npm run dev -- --port 5173 &
FRONT=$!
echo "$BACK" > "$PIDFILE"
echo "$FRONT" >> "$PIDFILE"

# Guaranteed teardown: closing the terminal sends SIGHUP, Ctrl+C sends SIGINT;
# either way kill the recorded servers AND their descendants (uvicorn's reload
# worker, npm's node) so nothing keeps holding ports 8173/5173. Idempotent so the
# EXIT trap can fire after a signal trap already cleaned up.
cleanup() {
  [ -f "$PIDFILE" ] || return 0
  while read -r pid; do
    if kill -0 "$pid" 2>/dev/null; then
      pkill -TERM -P "$pid" 2>/dev/null || true
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done < "$PIDFILE"
  sleep 1
  while read -r pid; do
    pkill -9 -P "$pid" 2>/dev/null || true
    kill -9 "$pid" 2>/dev/null || true
  done < "$PIDFILE"
  rm -f "$PIDFILE"
  echo "grimoire stopped."
}
trap cleanup EXIT INT TERM HUP

echo "grimoire running at $URL (backend pid $BACK, frontend pid $FRONT)"

# Wait for a TCP port to accept connections (cold starts can exceed any fixed delay:
# Vite pre-bundles deps on first run, uvicorn imports the app). Returns non-zero on timeout.
wait_port() {
  local name="$1" port="$2"
  printf "Waiting for %s to be ready" "$name"
  for _ in $(seq 1 60); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
      exec 3>&- 3<&-
      echo
      return 0
    fi
    printf "."
    sleep 1
  done
  echo
  return 1
}

if ! wait_port backend 8173; then
  echo "Backend did not become ready (port 8173). The config page will fail to load."
  echo "Check its output above — on Windows use scripts/windows/run.ps1 instead of this script."
fi
wait_port frontend 5173 || echo "Frontend did not become ready in time; opening $URL anyway."

if command -v open >/dev/null; then open "$URL"
elif command -v xdg-open >/dev/null; then xdg-open "$URL"
fi
wait

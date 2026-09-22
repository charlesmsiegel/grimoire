#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNDIR="$ROOT/.run"
PIDFILE="$RUNDIR/pids"
DIST_INDEX="$ROOT/frontend/dist/index.html"
BACKEND_PORT=8173
VITE_PORT=5173

# Two ways to run. The default is what a player wants: one backend serving the
# production bundle from frontend/dist beside the API, on one port. `--dev` is
# the contributor's loop and exactly what this script used to do every time --
# uvicorn --reload plus the Vite dev server with HMR -- which costs each page
# load an unbundled request per source module, React's development build (about
# twice the render cost of anything that grows with the library: a long
# transcript, a big grid, a streaming reply) and a Node proxy in front of every
# API call and stream. scripts/windows/run.ps1 mirrors this with -Dev.
DEV=0
for arg in "$@"; do
  case "$arg" in
    --dev) DEV=1 ;;
    -h|--help)
      echo "usage: $(basename "$0") [--dev]"
      echo "  (default)  serve the built UI from the backend at http://127.0.0.1:$BACKEND_PORT"
      echo "  --dev      backend with --reload plus the Vite dev server (HMR) at http://127.0.0.1:$VITE_PORT"
      exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done
if [ "$DEV" = 1 ]; then URL="http://127.0.0.1:$VITE_PORT"; else URL="http://127.0.0.1:$BACKEND_PORT"; fi
mkdir -p "$RUNDIR"

if [ -f "$PIDFILE" ] && kill -0 "$(head -n1 "$PIDFILE")" 2>/dev/null; then
  echo "grimoire is already running (http://127.0.0.1:$BACKEND_PORT, or :$VITE_PORT if started with --dev). Use shutdown.sh to stop it."
  exit 0
fi

# What `vite build` reads. The bundle is rebuilt when any of these is newer than
# the dist/index.html the last build wrote, which is what makes the first launch
# after a `git pull` serve the new UI rather than the one the installer built:
# git stamps every file it writes with the checkout time. Directories count too,
# since deleting a file changes nothing but its directory's mtime. A missing
# entry is skipped rather than fatal; test_install_scripts.py asserts they all
# exist and that scripts/windows/run.ps1 watches the same list.
BUNDLE_INPUTS=(src public index.html package.json package-lock.json tsconfig.json vite.config.ts)

bundle_is_stale() {
  local paths=() p
  [ -f "$DIST_INDEX" ] || return 0
  for p in "${BUNDLE_INPUTS[@]}"; do
    if [ -e "$ROOT/frontend/$p" ]; then paths+=("$ROOT/frontend/$p"); fi
  done
  [ "${#paths[@]}" -gt 0 ] || return 1
  # One newer path settles it, so -quit ends the walk at the first match
  # rather than listing the whole tree.
  [ -n "$(find "${paths[@]}" -newer "$DIST_INDEX" -print -quit 2>/dev/null)" ]
}

# Wait for a TCP port to accept connections (cold starts can exceed any fixed delay:
# Vite pre-bundles deps on first run, uvicorn imports the app). Returns non-zero on
# timeout -- or at once, given a pid, when that process has exited: a backend that
# died (an import error, a port already taken) is not going to become ready.
wait_port() {
  local name="$1" port="$2" pid="${3:-}" tries="${4:-60}"
  printf "Waiting for %s to be ready" "$name"
  for _ in $(seq 1 "$tries"); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
      exec 3>&- 3<&-
      echo
      return 0
    fi
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
      echo
      return 1
    fi
    printf "."
    sleep 1
  done
  echo
  return 1
}

if [ "$DEV" = 0 ]; then
  # Refuse a port something already answers on. Otherwise the readiness probe
  # below is satisfied by *that* process -- often an orphaned backend serving
  # an older build -- the browser opens on it, and ours exits a moment later
  # unable to bind.
  if (exec 3<>"/dev/tcp/127.0.0.1/$BACKEND_PORT") 2>/dev/null; then
    echo "Port $BACKEND_PORT is already in use by another process; stop it and try again." >&2
    exit 1
  fi
  if bundle_is_stale; then
    echo "Building the UI (the first launch after an update takes a few seconds)…"
    # `vite build`, not `npm run build`: that script's `tsc -b` is a type check,
    # the gate's job rather than a launch's. Errors only -- the bundler's size
    # advisories are for whoever is changing the code, not whoever is playing.
    if ! (cd "$ROOT/frontend" && node_modules/.bin/vite build --logLevel error); then
      # Vite empties dist/ only once bundling has succeeded, so a failure here
      # (typically dependencies an update changed) leaves the previous build in
      # place. Serving it beats not starting, and the next launch tries again.
      if [ -f "$DIST_INDEX" ]; then
        echo "The UI build failed; serving the previous build. After an update, re-run scripts/unix/install.sh." >&2
      else
        echo "The UI build failed and there is no previous build to serve. Re-run scripts/unix/install.sh." >&2
        exit 1
      fi
    fi
  fi
fi

cd "$ROOT/backend"
if [ "$DEV" = 1 ]; then
  .venv/bin/python -m uvicorn grimoire.main:app --reload --port "$BACKEND_PORT" &
  BACK=$!
  cd "$ROOT/frontend"
  npm run dev -- --port "$VITE_PORT" &
  FRONT=$!
  echo "$BACK" > "$PIDFILE"
  echo "$FRONT" >> "$PIDFILE"
else
  # No --reload: its supervisor is a second process watching the source tree
  # for edits a player never makes. main.py mounts frontend/dist beside /api,
  # with the client-route fallback that lets a deep link survive a reload.
  .venv/bin/python -m uvicorn grimoire.main:app --port "$BACKEND_PORT" &
  BACK=$!
  echo "$BACK" > "$PIDFILE"
fi

# Guaranteed teardown: closing the terminal sends SIGHUP, Ctrl+C sends SIGINT;
# either way the script exits, and on exit kills every recorded server AND its
# descendants (in --dev, uvicorn's reload worker and npm's node) so nothing
# keeps holding a port. The pidfile has one line by default and two under
# --dev, and is read once: shutdown.sh may delete it while this runs (its kill
# is what ended the `wait` below), and a second read would then fail.
cleanup() {
  local pids pid
  # Deaf to further signals from here on. An impatient second Ctrl+C would
  # otherwise run `exit` inside this EXIT trap, which ends the shell on the
  # spot: the kill -9 pass is skipped and the pidfile outlives its processes,
  # holding pids the OS may reissue -- to something shutdown.sh would then kill.
  trap '' INT TERM HUP
  pids="$(cat "$PIDFILE" 2>/dev/null)" || return 0
  for pid in $pids; do
    if kill -0 "$pid" 2>/dev/null; then
      pkill -TERM -P "$pid" 2>/dev/null || true
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  sleep 1
  for pid in $pids; do
    pkill -9 -P "$pid" 2>/dev/null || true
    kill -9 "$pid" 2>/dev/null || true
  done
  rm -f "$PIDFILE"
  echo "grimoire stopped."
}
trap cleanup EXIT
# A signal ends the script rather than resuming it: resumed mid-startup, the
# readiness wait below would find the backend gone and call it a failed start.
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

if [ "$DEV" = 1 ]; then
  echo "grimoire running at $URL (backend pid $BACK, frontend pid $FRONT)"
  if ! wait_port backend "$BACKEND_PORT"; then
    echo "Backend did not become ready (port $BACKEND_PORT). The config page will fail to load."
    echo "Check its output above — on Windows use scripts/windows/run.ps1 instead of this script."
  fi
  wait_port frontend "$VITE_PORT" || echo "Frontend did not become ready in time; opening $URL anyway."
else
  echo "grimoire running at $URL (pid $BACK)"
  # Without --reload uvicorn binds only after the app's startup (the store
  # migrations) has run, so an open port means ready -- and a slow start on a
  # large library earns a longer wait than the dev servers get.
  if ! wait_port backend "$BACKEND_PORT" "$BACK" 180; then
    if ! kill -0 "$BACK" 2>/dev/null; then
      echo "The backend exited before it was ready; check its output above." >&2
      echo "On Windows use scripts/windows/run.ps1 instead of this script." >&2
      exit 1
    fi
    echo "The backend is still not answering on port $BACKEND_PORT; opening $URL anyway."
  fi
fi

if command -v open >/dev/null; then open "$URL"
elif command -v xdg-open >/dev/null; then xdg-open "$URL"
fi
wait

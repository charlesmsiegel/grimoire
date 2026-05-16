#!/usr/bin/env bash
# Install all dependencies for grimoire (backend + frontend).
# Backend and frontend installs run in parallel; their output is line-prefixed
# so it stays legible when interleaved.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=_lib.sh
. "$REPO_ROOT/scripts/_lib.sh"

usage() {
    cat <<'EOF'
Usage: scripts/install.sh [--sequential] [--force] [-h|--help]

Installs Python (backend) and JS (frontend) dependencies.

Options:
  --sequential    Install backend then frontend (default: parallel).
  --force         Stop any running grimoire dev servers first. Without this,
                  install aborts if it detects them (their binaries are
                  locked on Windows and would fail uv sync mid-flight).
  -h, --help      Show this help.
EOF
}

PARALLEL=1
FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --sequential) PARALLEL=0; shift ;;
        --force)      FORCE=1; shift ;;
        -h|--help)    usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

need() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "error: '$1' not found on PATH. $2" >&2
        return 1
    fi
}

missing=0
need python3 "Install Python 3.12+ from https://www.python.org/downloads/" || missing=1
need uv      "Install uv from https://docs.astral.sh/uv/getting-started/installation/" || missing=1
need node    "Install Node 20+ from https://nodejs.org/" || missing=1
[ "$missing" -eq 0 ] || exit 1

# Pre-flight: a running uvicorn keeps backend/.venv/Scripts/uvicorn.exe locked
# on Windows, and `uv sync` then fails partway through. Detect by scanning
# process command lines for the repo path so we catch dev servers on any port.
running_pids="$(pids_using_path "$REPO_ROOT" | sort -u)"
if [ -n "$running_pids" ]; then
    if [ "$FORCE" = "1" ]; then
        echo "==> --force: stopping grimoire processes before install"
        while IFS= read -r pid; do
            [ -z "$pid" ] && continue
            echo "    killing PID $pid"
            kill_pid "$pid"
        done <<< "$running_pids"
        kill_orphaned_uvicorn_workers
        sleep 1
    else
        echo "error: grimoire dev processes are running; their binaries are locked" >&2
        echo "       and would cause 'uv sync' to fail partway through." >&2
        echo "       PIDs: $(echo "$running_pids" | tr '\n' ' ')" >&2
        echo "       Stop them with scripts/shutdown.sh, or rerun with --force." >&2
        exit 1
    fi
fi

# Resolve pnpm (or activate corepack). pnpm_cmd is a space-separated command
# token: e.g. "pnpm" or "corepack pnpm".
if ! command -v pnpm >/dev/null 2>&1 && command -v corepack >/dev/null 2>&1; then
    echo "==> pnpm not found; activating via corepack"
    corepack enable >/dev/null 2>&1 || true
fi
pnpm_cmd="$(resolve_pnpm)" || exit 1
# shellcheck disable=SC2206
PNPM=($pnpm_cmd)

# Prefix every line of a child's output so parallel logs stay legible.
prefix_output() {
    local tag="$1"
    sed -u "s|^|[$tag] |" 2>/dev/null || sed "s|^|[$tag] |"
}

run_backend() (
    set -e
    set -o pipefail
    cd "$REPO_ROOT/backend"
    uv sync 2>&1 | prefix_output backend
)

run_frontend() (
    set -e
    set -o pipefail
    cd "$REPO_ROOT/frontend"
    # --config.confirm-modules-purge=false: when node_modules was built by a
    # different pnpm/store version, pnpm prompts "purge and reinstall? (Y/n)".
    # In parallel mode there's no usable stdin, so the prompt hangs or reads
    # garbage. Auto-accept the purge so reinstall is fully unattended.
    "${PNPM[@]}" install --config.confirm-modules-purge=false 2>&1 | prefix_output frontend
)

if [ "$PARALLEL" = "1" ]; then
    echo "==> Installing backend and frontend in parallel"
    run_backend &
    backend_pid=$!
    run_frontend &
    frontend_pid=$!

    set +e
    wait "$backend_pid";  backend_status=$?
    wait "$frontend_pid"; frontend_status=$?
    set -e

    failed=0
    if [ "$backend_status" -ne 0 ]; then
        echo "error: backend install failed (exit $backend_status)" >&2
        failed=1
    fi
    if [ "$frontend_status" -ne 0 ]; then
        echo "error: frontend install failed (exit $frontend_status)" >&2
        failed=1
    fi
    [ "$failed" -eq 0 ] || exit 1
else
    echo "==> Backend: uv sync"
    (cd "$REPO_ROOT/backend" && uv sync)
    echo "==> Frontend: pnpm install"
    (cd "$REPO_ROOT/frontend" && "${PNPM[@]}" install --config.confirm-modules-purge=false)
fi

echo
echo "Install complete. Run scripts/run.sh to start the app."

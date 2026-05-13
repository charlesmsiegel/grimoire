#!/usr/bin/env bash
# Install all dependencies for grimoire (backend + frontend).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

need() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "error: '$1' not found on PATH. $2" >&2
        return 1
    fi
}

missing=0
need python3 "Install Python 3.12+ from https://www.python.org/downloads/" || missing=1
need uv "Install uv from https://docs.astral.sh/uv/getting-started/installation/" || missing=1
need node "Install Node 20+ from https://nodejs.org/" || missing=1
if [ "$missing" -ne 0 ]; then
    exit 1
fi

# pnpm: prefer pnpm on PATH; otherwise activate via corepack (ships with Node).
if command -v pnpm >/dev/null 2>&1; then
    PNPM=(pnpm)
elif command -v corepack >/dev/null 2>&1; then
    echo "==> pnpm not found; activating via corepack"
    corepack enable >/dev/null 2>&1 || true
    PNPM=(corepack pnpm)
else
    echo "error: neither 'pnpm' nor 'corepack' found on PATH." >&2
    echo "       Install pnpm from https://pnpm.io/installation" >&2
    exit 1
fi

echo "==> Backend: uv sync (creates .venv and installs deps)"
cd "$REPO_ROOT/backend"
uv sync

echo "==> Frontend: pnpm install"
cd "$REPO_ROOT/frontend"
"${PNPM[@]}" install

echo
echo "Install complete. Run scripts/run.sh to start the app."

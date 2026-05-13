# Grimoire

A local-first RPG campaign companion. See [`specs/00-overview.md`](specs/00-overview.md) for the architecture and [`TASKS.md`](TASKS.md) for the build plan.

## Layout

```
backend/         Python FastAPI backend (uv-managed)
frontend/        TypeScript React + Vite frontend (pnpm-managed)
specs/           Architecture specs (00-overview is authoritative)
```

Runtime user content (library, campaigns, mechanics, plugins) lives outside the repo at `~/.grimoire/` — see [Data directory](#data-directory) below.

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node 20+ and [pnpm](https://pnpm.io/) (or just `corepack`, which ships with Node)
- Bash — Linux/macOS have it natively; on Windows use Git Bash (bundled with [Git for Windows](https://git-scm.com/)) or WSL

## Quick start

```sh
scripts/install.sh     # installs backend + frontend deps in parallel
scripts/run.sh         # starts both servers and opens the browser
scripts/shutdown.sh    # stops both and frees the ports
```

`scripts/run.sh` accepts flags so you can pick any free ports:

```sh
scripts/run.sh --backend-port 9000 --frontend-port 5180
scripts/run.sh --no-browser              # don't auto-open the browser
scripts/run.sh --reload                  # backend autoreload (uvicorn --reload)
scripts/run.sh -h                        # full flag list
```

Ports also accept `GRIMOIRE_BACKEND_PORT` / `GRIMOIRE_FRONTEND_PORT` env vars. `scripts/shutdown.sh` picks the same ports up from the state file `run.sh` writes (`.grimoire-run.env`), so it works from any terminal.

## Backend

```sh
cd backend
uv sync
uv run uvicorn grimoire.main:app --reload
```

Visit `http://127.0.0.1:8000/api/health` to verify.

## Frontend

```sh
cd frontend
pnpm install
pnpm dev
```

Visits `http://127.0.0.1:5173`. The dev server proxies `/api/*` and `/ws/*` to the backend (configurable via `GRIMOIRE_BACKEND_HOST` / `GRIMOIRE_BACKEND_PORT`).

## Tests and lint

```sh
# Backend
cd backend
uv run ruff check
uv run ruff format --check
uv run pytest

# Frontend
cd frontend
pnpm lint
pnpm typecheck
pnpm build
```

## Data directory

User content (library, campaigns, installed mechanics, installed plugins) lives at `~/.grimoire/` by default — outside the repo, so multiple clones share one library and a clean repo never holds your work. Set `GRIMOIRE_DATA_ROOT` to point the backend at a different root.

Default style guides and image presets are seeded into `~/.grimoire/library/` on first run; only files that don't already exist are written, so your edits are preserved.

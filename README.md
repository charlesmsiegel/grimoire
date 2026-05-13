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
- Node 20+ and [pnpm](https://pnpm.io/)

## Backend

```sh
cd backend
uv sync
uv run uvicorn grimoire.main:app --reload
```

Visits `http://127.0.0.1:8000/health` to verify.

## Frontend

```sh
cd frontend
pnpm install
pnpm dev
```

Visits `http://127.0.0.1:5173`. The dev server proxies `/api/*` and `/ws/*` to the backend.

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

# Contributing to Grimoire

## Getting Started

### Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node 20+ and [pnpm](https://pnpm.io/) (or `corepack`, which ships with Node)
- Bash — Linux/macOS native; on Windows use Git Bash or WSL

### Setup

```sh
git clone https://github.com/your-username/grimoire.git
cd grimoire
scripts/install.sh     # installs backend + frontend deps
scripts/run.sh         # starts both servers at :8173 (API) and :5173 (UI)
```

Verify the backend is running: `http://127.0.0.1:8173/api/health`

## Branch Strategy

- **Feature branches** for anything more complex than a one-line bugfix
- Branch from `main`, name descriptively: `feat/multi-pc-advance`, `fix/scene-index-race`
- **Rebase-merge** to `main` — not merge commits, not squash
- Keep branches short-lived; one feature or fix per branch

## Before Submitting

Run the full check suite:

```sh
# Backend
cd backend
uv run ruff check              # lint
uv run ruff format --check     # format
uv run pytest                  # tests

# Frontend
cd frontend
pnpm lint                      # eslint
pnpm typecheck                 # tsc --noEmit
pnpm test                      # vitest
```

All four backend checks and all three frontend checks must pass.

## PR Guidelines

- **Keep PRs focused.** One feature or fix per PR. If you notice unrelated issues, file them separately.
- **Include tests.** Bug fixes need a regression test. New features need unit tests at minimum.
- **Update docs.** If your change affects behavior, commands, or conventions, update README.md, CLAUDE.md, AGENTS.md, or CONTRIBUTING.md in the same PR.
- **Write clear commit messages.** Use conventional commits: `feat:`, `fix:`, `docs:`, `style:`, `perf:`, `refactor:`, `test:`.

## Code Style

Style is enforced by tooling — don't worry about memorizing rules:

- **Backend:** `uv run ruff format` auto-formats. `uv run ruff check --fix` auto-fixes lint issues. Config: line-length 100, Python 3.12, double quotes.
- **Frontend:** `pnpm format` runs Prettier. `pnpm lint` runs ESLint. Strict TypeScript is enforced by `tsconfig.app.json`.

## Architecture

See [CLAUDE.md](CLAUDE.md) for a comprehensive guide to the architecture, module boundaries, and conventions. The key rule: **every piece of data lives in exactly one of three scopes** (Library, Campaign-local, Code), and **writes go through the owning module**.

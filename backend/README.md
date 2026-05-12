# Grimoire backend

FastAPI app. See [`../specs/`](../specs/) for module specs.

## Run

```sh
uv sync
uv run uvicorn grimoire.main:app --reload
```

## Test

```sh
uv run pytest
uv run ruff check
uv run ruff format --check
```

## Data root

Defaults to `../data/` (repo root). Override with the `GRIMOIRE_DATA_ROOT` environment variable.

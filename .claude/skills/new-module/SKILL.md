---
name: new-module
description: >-
  Scaffold a new Grimoire backend domain module — package skeleton, Protocol
  interface, FastAPI router stub, DI wiring, and a mirrored test tree — and
  update the ownership docs. Use when adding a new module under
  backend/src/grimoire/.
disable-model-invocation: true
---

# new-module — scaffold a Grimoire backend domain module

Grimoire's backend is one package per domain module under
`backend/src/grimoire/`, with tests mirrored under `backend/tests/`. New modules
follow a consistent shape and must be registered in the DI container and the
ownership tables. This skill walks that scaffold.

`$ARGUMENTS` is the module name in `snake_case` (e.g. `quest_log`). If absent,
ask for it. Derive a `PascalCase` service name from it (`QuestLog`).

## Steps

1. **Confirm scope.** Restate what the module owns in one sentence and which
   existing modules it must talk to. Cross-module communication is via Protocol
   interfaces (sync reads) or the event bus (fan-out) — never direct imports of
   another leaf module's implementation. If the module doesn't fit the module
   map cleanly, stop and discuss before scaffolding.

2. **Create the package** `backend/src/grimoire/<name>/`:
   - `__init__.py` — export the Protocol and the service.
   - `protocol.py` — a `typing.Protocol` defining the interface other modules
     depend on. Keep it minimal; this is the boundary.
   - `service.py` — the concrete implementation (`class <PascalCase>Service`).
     Pydantic models for any data it owns. Writes go through this module only.
   - `models.py` — Pydantic models (if the module owns data shapes).

3. **API router (only if the module is reachable from the frontend)** —
   `backend/src/grimoire/api/<name>.py`: a FastAPI `APIRouter` with a sensible
   prefix and tag. Match the structure of an existing sibling router (read one
   first, e.g. `api/scenes.py`). Register it where the app assembles routers
   (`main.py` / the api package).

4. **DI wiring** — register the service in `bootstrap.py` so it's constructed
   and injected like its peers. Depend on Protocols, not implementations.

5. **Events (if it reacts to or emits fan-out)** — subscribe/publish through
   `event_bus.py` using the existing typed events (`turn_complete`,
   `scene_started`, …). Don't invent a back-channel.

6. **Tests** — mirror the package under `backend/tests/<name>/`:
   - a unit test for the service (default marker / none),
   - an `@pytest.mark.integration` test if it crosses a module boundary,
   - a scenario test through the HTTP API if you added a router.
   For SQLite-backed tests, stamp the migrated template:
   `from grimoire.testing.db_template import stamp_migrated_db`.

7. **Update the ownership docs (required — don't skip).** Add a row to the
   **Module Ownership** table and, if relevant, the **Module Map** in:
   - `CLAUDE.md`
   - `AGENTS.md`
   - the authoritative spec `specs/00-overview.md` if it enumerates modules.
   Documentation that drifts from code is worse than none.

8. **Verify green.** From `backend/`:
   `uv run ruff check && uv run ruff format --check && uv run pytest <new test paths>`.
   Report the actual command output — don't claim it passes without running it.

## Conventions to honor
- Pydantic for all data; Protocol for boundaries; ruff (line 100, double
  quotes, py312); `asyncio_mode = auto` so async tests just work.
- Files are the source of truth; SQLite is derived. If the module persists
  anything, write the file and let the watcher/index follow — don't write the
  index directly.

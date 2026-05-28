# Grimoire — Agent Guide

Grimoire is a local-first RPG campaign companion that places a deterministic Orchestrator between the user and the LLM. It manages context assembly, structured state extraction, and campaign continuity for long-form tabletop RPG play.

The foundational concept is **three scopes**: every piece of data lives in exactly one of Library (user-authored content), Campaign-local (play state and output), or Code (mechanics modules and plugins).

## Tech Stack

| Layer | Stack |
|-------|-------|
| Backend | Python 3.12+, FastAPI, uvicorn, Pydantic, uv (dependency management) |
| Frontend | TypeScript 5.6+, React 18, Vite, pnpm |
| Storage | SQLite (FTS5 + sqlite-vec) for queries/search; Markdown + YAML files for source of truth |
| Communication | REST + WebSocket between frontend/backend; in-process event bus between backend modules |

## Repository Layout

```
backend/
  src/grimoire/          # Python backend — one package per domain module
    api/                 # FastAPI routers (one per domain)
    orchestrator/        # Turn loop, advance trigger, campaign forks, retcon
    context/             # Context assembly and prompt building
    scenes/              # Scene management, posts, summarization
    characters/          # Character services, PC management
    world/               # World container, entity CRUD
    library/             # File layout, watcher, indexing
    state_store/         # Hybrid file + SQLite state management
    extractor/           # LLM output → structured deltas
    llm_gateway/         # LLM and embedding provider abstraction
    mechanics/           # Mechanics API contract (modules are external)
    continuity/          # Facts, commitments, contradictions, aging
    time_engine/         # In-game calendar and time advancement
    imagegen/            # Image generation backends
    export/              # EPUB, Markdown, HTML, transcript export
    plugins/             # Plugin lifecycle and loading
    observability/       # Audit, replay, metrics
    storage/             # SQLite database layer, migrations
    hud/                 # HUD widget system (scene info, cast, context inspector, actions)
    expressions/         # Expression parsing/evaluation
    templates/           # Jinja2 template rendering
    auxiliary/           # Background LLM jobs (summarization, etc.)
    transient_state/     # Ephemeral character/location state
    watcher/             # File watcher for library/campaign changes
    validation/          # JSON Schema validation
    event_bus.py         # In-process async event bus
    bootstrap.py         # DI container setup
    main.py              # FastAPI app entry point
  tests/                 # pytest tests mirroring src/ structure
frontend/
  src/
    components/          # Shared React components
    routes/              # Route-level page components
    api/                 # API client functions
    hooks/               # Custom React hooks
    types/               # TypeScript type definitions
    stores/              # React context providers
scripts/                 # install.sh, run.sh, shutdown.sh
specs/                   # Architecture specs (00-overview.md is authoritative)
docs/                    # Design specs, implementation plans, images
```

Bundled plugins live at `backend/bundled_plugins/` and ship with the app.

## Architecture Essentials

### The Three Scopes

| Scope | What it holds | Storage | Mutability |
|-------|---------------|---------|------------|
| **Library** | Worlds (characters, items, locations, lore, factions, greetings), style guides, image presets | Markdown + YAML files under `~/.grimoire/library/` | User-edited |
| **Campaign-local** | Scenes (prose + metadata), facts, commitments, sheets, relationships, embeddings, overrides, emergent content | Files under `~/.grimoire/campaigns/<id>/` + SQLite | Free-write during play |
| **Code** | Mechanics modules, plugins (LLM, embedding, imagegen, export) | Python packages under `~/.grimoire/mechanics/` and `~/.grimoire/plugins/` | Read-only at runtime |

**Storage rule:** Files are the source of truth for everything human-readable. SQLite is a derived cache plus store for high-volume data (embeddings, facts, relationships). If `campaigns.sqlite` is deleted, the app rebuilds it from files on startup.

### Module Map

```
                     ┌──────────────────────┐
                     │   Frontend (React)   │
                     └──────────┬───────────┘
                                │  REST + WebSocket
                     ┌──────────▼───────────┐
                     │     Orchestrator     │
                     └──────────┬───────────┘
                                │
     ┌──────────────┬───────────┼───────────┬─────────────┐
     ▼              ▼           ▼           ▼             ▼
 Context        Scene       Mechanics    LLM           ImageGen
 Builder        Manager       (API)      Gateway

     │              │           │           │             │
     └──────────────┴─────┬─────┴───────────┴─────────────┘
                          ▼
                 ┌─────────────────────┐
                 │    State Store      │
                 │  files + SQLite     │
                 └─────────────────────┘
                          ▲
                          │
       ┌────────┬─────────┼─────────┬────────┐
       │        │         │         │        │
    Characters World  Continuity   Time   Extractor
                                   Engine
```

Supporting modules (cross-cutting): Plugins, Observability, Export.

### Canonical Turn Flow

1. Frontend → Orchestrator: `submit_post(campaign_id, pc_ref, text)`
2. Scene Manager appends post to scene file, updates YAML sidecar
3. Decision: single PC in scene → auto-respond; multiple PCs → wait for Advance
4. Orchestrator → Context Builder: assemble prompt from entities, continuity, mechanics, style
5. Orchestrator → LLM Gateway: stream completion to frontend
6. Orchestrator → Extractor: parse structured deltas from response
7. Orchestrator → State Store: apply deltas (new facts, commitments, relationships)
8. Scene Manager: append model response to scene file
9. Time Engine, Continuity, Characters: react to `turn_complete` event

### Read Cascade

When resolving an entity in campaign context:

1. Check campaign-local emergent content
2. Walk world refs in priority order, check library index
3. Return first match
4. Apply any campaign-local override on top
5. Not found → missing

### Event Bus

Modules communicate through direct typed calls (Protocol interfaces) for synchronous reads, and an async event bus for fan-out notifications. Key events: `turn_started`, `turn_complete`, `scene_started`, `scene_ended`, `time_advanced`, `fact_recorded`, `library_file_changed`, `advance_requested`.

## Module Ownership

Writes go through the owning module. Don't bypass this.

| Module | Owns | Role |
|--------|------|------|
| Orchestrator | Turn loop, event bus | Drives per-campaign turn processing |
| Context Builder | Prompt assembly | Cross-scope read, context budgeting |
| State Store | File + SQLite hybrid | Cascade resolution, backup scheduling |
| Extractor | Delta parsing | Model output → structured state deltas |
| LLM Gateway | Provider abstraction | LLM and embedding calls |
| Mechanics | API contract | Defines the mechanics interface; modules are external |
| Time Engine | In-game clock | Calendar, time advancement per campaign |
| Characters | Character behaviors | Voice, drift, tier, PCs, variants |
| World | Entity storage | World container, all entity-kind CRUD |
| Scene Manager | Scenes, posts | Scene lifecycle, multi-PC advance trigger |
| Continuity | Facts, commitments | Contradiction detection, commitment tracking |
| Library | File layout, indexing | Watcher, incremental index updates |
| ImageGen | Image generation | Backend registry, generation pipeline |
| Export | Artifact generation | EPUB, Markdown, HTML, transcript |
| Plugins | Plugin lifecycle | Loading, validation, registry |
| Observability | Audit trail | Turn replay, metrics, context inspection |

## Development Commands

```sh
# Full install + run
scripts/install.sh          # backend (uv sync) + frontend (pnpm install) in parallel
scripts/run.sh              # starts backend :8173 + frontend :5173, opens browser
scripts/shutdown.sh         # stops both, frees ports

# Backend
cd backend
uv run pytest                              # all tests
uv run pytest -m conformance               # plugin contract tests
uv run pytest -m integration               # cross-module tests
uv run pytest -m frozen_campaign           # regression over frozen SQLite
uv run pytest -m golden                    # golden-path with LLM fixtures
uv run pytest -m scenario                  # end-to-end through HTTP API
uv run ruff check                          # lint
uv run ruff format --check                 # format check
uv run ruff format                         # auto-format

# Frontend
cd frontend
pnpm test                                  # vitest run
pnpm lint                                  # eslint
pnpm typecheck                             # tsc -b --noEmit
pnpm build                                 # production build (tsc + vite)
pnpm format                                # prettier --write .
pnpm format:check                          # prettier --check .
```

## Code Conventions

### Backend (Python)

- **Pydantic** for all data validation and serialization
- **Protocol** interfaces define module boundaries — depend on the interface, not the implementation
- **ruff** enforces style: line-length 100, Python 3.12 target, rules E/F/I/B/UP/SIM/RUF, double quotes
- **pytest-asyncio** with `asyncio_mode = auto` — async tests just work
- Source under `backend/src/grimoire/`, tests under `backend/tests/`

### Frontend (TypeScript)

- **Zod** for runtime validation of API responses
- **Radix UI** primitives for accessible components (Dialog, DropdownMenu, Popover)
- **React Router 7** for routing
- **Strict TypeScript**: `strict: true`, `noUnusedLocals`, `noUnusedParameters`, `noUncheckedIndexedAccess`
- **Prettier** for formatting, **ESLint** with TypeScript plugin for linting
- Source under `frontend/src/`

### Content Files

Library and campaign content uses Markdown files with YAML frontmatter:

```markdown
---
id: alistair-hyde-smythe
name: "Alistair Hyde-Smythe"
role: major_npc
# ... structured fields
---

Prose description in the markdown body.
```

Metadata sidecars for scenes use `.yaml` files alongside the `.md` prose file.

## Branching and Workflow

- **Feature branches** for anything more complex than a simple bugfix
- **Rebase-merge** (not `merge --no-ff`) when integrating feature branches to `main`
- **Worktrees** go under `.worktrees/` at the repo root, not `.claude/worktrees/`
- Commit frequently — small, focused commits with conventional commit messages (`feat:`, `fix:`, `docs:`, `style:`, `perf:`, `refactor:`, `test:`)

## Data Directory

User data lives at `~/.grimoire/` by default (override with `GRIMOIRE_DATA_ROOT`):

```
~/.grimoire/
├── library/
│   ├── worlds/<world-id>/
│   │   ├── world.yaml           # metadata, calendar, atmosphere
│   │   ├── characters/          # .md files with YAML frontmatter
│   │   ├── items/
│   │   ├── locations/
│   │   ├── lore/
│   │   ├── factions/
│   │   └── greetings/
│   ├── style-guides/
│   └── image-presets/
├── campaigns/
│   └── <campaign-id>/
│       ├── campaign.yaml        # composition, PCs, mechanics ref
│       ├── scenes/              # .md prose + .yaml sidecars
│       ├── overrides/           # campaign-local edits to library entities
│       ├── emergent/            # campaign-spawned content
│       ├── sheets/              # mechanical sheets per entity
│       └── images/              # generated images + .yaml metadata
├── campaigns.sqlite             # structured state, indexes, embeddings
├── mechanics/                   # installed mechanics modules
└── plugins/                     # installed plugins
```

## Common Pitfalls

- **Don't write directly to SQLite** for data that has a file source of truth. Files are SSOT; SQLite is derived. Write the file; the watcher updates the index.
- **Don't bypass the read cascade.** Always resolve entities through the cascade (emergent → library refs → apply overrides). Direct file reads skip campaign-local state.
- **Don't put mechanics logic in core.** Mechanics modules are external packages. Core defines the API contract; modules implement it. No game-system-specific code in `backend/src/grimoire/`.
- **Don't add telemetry or phone-home.** Grimoire is local-first with zero external data collection. No analytics, no crash reporting, no usage tracking.
- **Don't import across module boundaries** that shouldn't know about each other. Follow the module map — if there's no arrow between two modules, they shouldn't import each other. Use the event bus for fan-out.
- **Don't forget to update both file and index.** When changing content programmatically, write the file and let the watcher handle the index — or explicitly trigger an index update if the watcher isn't running (e.g., in tests).

## Keep Documentation Up to Date

When you change behavior, APIs, module boundaries, commands, or conventions, update the relevant docs in the same PR:

- New module → update the module ownership table in CLAUDE.md and AGENTS.md
- Changed command → update development commands in CLAUDE.md, AGENTS.md, and README.md
- New feature → consider whether the README feature list needs updating
- Changed conventions → update code conventions section

Documentation that drifts from code is worse than no documentation.

## Testing Strategy

### Backend Test Levels

| Marker | Scope | When to use |
|--------|-------|-------------|
| *(none)* | Unit | Default. Test one function/class in isolation. |
| `conformance` | Plugin contracts | Verify a plugin implements its protocol correctly |
| `integration` | Cross-module | Test interactions between two or more modules |
| `frozen_campaign` | Regression | Run against a frozen SQLite snapshot for stability |
| `golden` | Golden-path | End-to-end with checked-in LLM response fixtures |
| `scenario` | Full stack | User scenarios through the HTTP API |
| `perf` | Performance | Regression benchmarks |

### Frontend Tests

- **Vitest** for unit and component tests
- **React Testing Library** for component interaction tests
- Tests live alongside source or in `__tests__/` directories

### What to Test

- New backend logic: unit test at minimum, integration test if it crosses modules
- New API endpoint: add a scenario-level test
- New frontend component: component test with React Testing Library
- Bug fix: write a regression test that fails without the fix

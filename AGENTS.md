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
    inventory/           # Deterministic per-holder item/resource tracking
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

The Orchestrator also holds a narrow read dependency on **Characters**
(`find_cast_ref`) to resolve extractor-emitted cast-change refs to canonical
ids/PC flags before queuing them for review through the Scene Manager (#464).

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

### Character variants (in-world diff overlays)

A variant is an alternate take on a character within its world (#579): a diff
overlay file at `characters/<id>/variants/<variant-id>.md` holding only the
frontmatter fields that differ from the base (reserved keys: `label` = display
name, `id` is ignored) plus an optional replacement body. A campaign selects
at most one variant per character via a `variants:` map in `campaign.yaml`
(`{worlds/<w>/characters/<id>: <variant-id>}`); the cascade applies
**base → variant diff → campaign override** with the same merge semantics as
overrides. Variant files are never indexed in `library_index` — they're read
from disk at resolve time and the watcher only emits change events for them.
Lookups are always world-scoped; the same id in two worlds is two unrelated
entities.

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
| Scene Manager | Scenes, posts, cast-change review | Scene lifecycle, multi-PC advance trigger, cast-change queue/confirm/dismiss |
| Continuity | Facts, commitments | Contradiction detection, commitment tracking |
| Library | File layout, indexing | Watcher, incremental index updates |
| ImageGen | Image generation | Backend registry, generation pipeline |
| Export | Artifact generation | EPUB, Markdown, HTML, transcript |
| Plugins | Plugin lifecycle | Loading, validation, registry |
| Observability | Audit trail | Turn replay, metrics, context inspection, terminal wire logging |
| Inventory | Holdings, operations | Deterministic per-holder item/resource tracking (toggleable) |

## Development Commands

```sh
# Full install + run
scripts/install.sh          # backend (uv sync) + frontend (pnpm install) in parallel
scripts/run.sh              # starts backend :8173 + frontend :5173, opens browser
scripts/shutdown.sh         # stops both, frees ports

# Backend
cd backend
uv run pytest                              # all tests except perf benchmarks
uv run pytest -m conformance               # plugin contract tests
uv run pytest -m integration               # cross-module tests
uv run pytest -m frozen_campaign           # regression over frozen SQLite
uv run pytest -m golden                    # golden-path with LLM fixtures
uv run pytest -m scenario                  # end-to-end through HTTP API
uv run pytest -m perf                      # perf benchmarks (opt-in; excluded by default)
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

### Shared helpers — reach for these first

Recurring needs already have canonical helpers; a private reimplementation is a
review smell. Grep before adding a `_parse_*` / `_slug*` / `_json*` / `_now*`.

- **Data modeling**: Pydantic `BaseModel` for anything parsed from YAML/JSON,
  validated, or serialized (config loaders, API + persisted models) — keep
  `from_yaml` a one-line `model_validate`, not hand-rolled `bool()/int()/str()`
  coercion. `@dataclass(frozen=True)` is fine for internal value objects that
  never cross a serialization boundary.
- **`grimoire.util`**: `now_iso()` / `parse_iso_datetime()` (time),
  `safe_json_loads` / `safe_json_dumps` (nullable/pre-parsed JSON),
  `extract_json_object()` (JSON in LLM output), `new_id("prefix")` (ids — not
  `uuid4().hex[:n]`), `slugify_id` / `canonicalize_character_ref` (refs).
- **`grimoire.files`**: `slug.slugify`, `yaml_io` (`load_yaml` / `dump_yaml` /
  `write_yaml`), `frontmatter` (`read_markdown` / `write_markdown`) — never call
  `yaml.safe_load` / `safe_dump` or re-split `---` by hand.
- **API not-found**: raise the module's domain `*NotFoundError` (it carries
  `http_status`) and let `api.util.map_lookup_errors` translate it — don't inline
  `raise HTTPException(404, …)`. Service-present-or-503 goes through the
  `api.deps` `get_*` providers.
- **Carving up a large service**: follow the `host=self` coordinator pattern
  (`AuxiliaryCoordinator` / `RetconCoordinator` / `ForkCoordinator`) — coordinators
  use the host surface they're handed, not `host._privates` (#589 introduces a
  typed `OrchestratorHost` Protocol).

### Error handling

- **Callers must be able to tell *empty* from *broken*.** A read/maintenance path
  may degrade gracefully, but it must log at WARNING with context (path, id) and
  surface a signal — a count, marker, or error event — never bare
  `except Exception: pass`. The UI showing "no data" because a file failed to
  parse is a bug, not a degradation.
- **Write/mutation paths never catch-and-continue.** A failed multi-step write
  either compensates (file snapshot/restore, delta reversal — `write_library_file`
  in `state_store/store.py` and `scene_files_transaction` in `scenes/storage.py`
  are the pattern) or raises. Applying half of a turn's
  effects is worse than failing the turn (#583–#586). This applies to
  campaign/domain state; *best-effort diagnostic sinks* (audit trail, wire
  logging) stay non-fatal — they log their own failure and never fail the
  operation they observe.
- Catch the narrowest exception the failure actually produces; reserve broad
  `except Exception` for top-level loop/job guards that log *and* surface.

### Frontend (TypeScript)

- **Zod** for response-shape validation of list/grid-feeding endpoints: schemas live in
  `api/schemas/` (the TS payload type is `z.infer`'d from the schema) and are passed via
  the client's `checkSchema` option — dev-only `safeParse` + once-per-endpoint
  `console.warn` on drift, raw payload returned either way, so drift never crashes a
  play session. The strict `schema` option (`.parse`, throws, returns the transformed
  output) is reserved for boundary parsers whose parsed form is consumed (e.g. sheet
  schemas). Mutation responses and one-off reads use plain TS types (#599).
- **Radix UI** primitives for accessible components (Dialog, DropdownMenu, Popover)
- **React Router 7** for routing
- **Strict TypeScript**: `strict: true`, `noUnusedLocals`, `noUnusedParameters`, `noUncheckedIndexedAccess`
- **Prettier** for formatting, **ESLint** with TypeScript plugin for linting
- Source under `frontend/src/`

#### Shared frontend helpers — reach for these first

Like the backend, recurring frontend needs have canonical homes; a private
reimplementation is a review smell.

- **Dialogs**: `components/Dialog` (Radix-based; Escape/backdrop/focus handled)
  is the only modal shell. Destructive actions confirm through
  `components/ConfirmDestructiveDialog` + `hooks/useDestructiveConfirm`;
  single-input prompts use `components/PromptDialog`. `window.confirm` /
  `window.prompt` / `window.alert` are banned by ESLint.
- **Async data**: `api/useResource` is the data-fetching hook (wrap loaders in
  `useCallback`); render through `components/AsyncSection` (render-prop) or the
  library's plain-children `AsyncBoundary`. Don't hand-roll
  `useEffect`+`useState` fetch loops.
- **HTTP**: everything goes through `api/client.ts` (`api.get/post/…`,
  `errorMessage`, `ApiError`); `api/library/request.ts` is a 30s GET cache over
  it. Raw `fetch` only for streams/multipart, with a comment saying why.
- **UI bits**: `components/Tabs` (keyboard-accessible tablist),
  `components/EmptyState`, `components/SaveIndicator`, `components/CardFilters`
  + `hooks/useCardFilters` for list search/tag toolbars, `lib/slugify` for ids.
- **CSS primitives**: `src/index.css` is an ordered @import list over
  `src/styles/*.css` (one file per app area; import order is the cascade, so
  later files win specificity ties). `styles/primitives.css` defines the base
  shapes — `.card`, `.chip`, `.grid-cards` (column minimum via `--grid-min`),
  `.form-field`, `.button-link`, `.empty-state` — and section classes are
  modifiers layered on them in the markup (`class="card entity-card"`). New
  card/chip/grid/form styling starts from a primitive plus a modifier; don't
  re-declare the base chrome, and don't add `var(--token, literal)` fallbacks
  (every token is defined in both themes in `styles/tokens.css`).

#### Card icon bar

Every card renders a `CardIconBar` (`frontend/src/components/CardIconBar.tsx`) at its
bottom edge — it is the single home for per-card actions. Cards are the block-level
`*-card` components (`campaign-card`, `library-card`, `entity-card`, `entity-browser-card`,
`timeline-card`, `provider-card`, `why-character-card`) plus chat posts (`PostItem`).
World contents in both scopes render through the shared
`components/EntityBrowser` (`entity-browser-card` grid): library scope gets edit
links + delete/convert, campaign scope gets the source-chain badge plus
edit-override (✎) / promote-to-library (⤴) actions (#601).
Cards backing a deletable artifact under `~/.grimoire/` start with a Delete (🗑) icon
built via `deleteAction()`; cards with no delete render an empty bar (invisible until
populated). Actions are right-aligned by default; pass `align: "start"` to pin one to
the left edge (e.g. the campaign card's Fork icon, with Settings + Delete on the right).
**Never render a bespoke delete/remove button** — the custom
`no-bespoke-delete` ESLint rule (`frontend/eslint-rules/`) enforces this; confirm-dialog
buttons (`*Dialog*`/`*Confirm*` files) are exempt. Card-root `<button>`s (timeline, cast)
place the bar in the wrapping `<li>`, not inside the button. Sub-element classes
(`*-card-actions`, `*-card-head`, …), the `card-filters` toolbar, grid wrappers, and the
bare `suggestion-card` selection button are not given a bar. Emoji icons are interim
(tracked by issue #516 for an SVG icon library).

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
│   │   │   └── <id>/variants/   # in-world variant diff overlays (.md)
│   │   ├── items/
│   │   ├── locations/
│   │   ├── lore/
│   │   ├── factions/
│   │   └── greetings/
│   ├── style-guides/
│   └── image-presets/
├── campaigns/
│   └── <campaign-id>/
│       ├── campaign.yaml        # composition, PCs, variant selections, mechanics ref
│       ├── scenes/              # .md prose + .yaml sidecars
│       ├── overrides/           # campaign-local edits to library entities
│       ├── emergent/            # campaign-spawned content
│       ├── sheets/              # mechanical sheets per entity
│       └── images/              # generated images + .yaml metadata
├── campaigns.sqlite             # structured state, indexes, embeddings
├── mechanics/                   # installed mechanics modules
└── plugins/                     # installed plugins
```

### Optional per-campaign subsystems

Some subsystems are opt-in per campaign via a block in `campaign.yaml`. The
inventory system (deterministic item/resource tracking) is off by default:

```yaml
inventory:
  enabled: true            # default false (opt-in)
  flag_threshold: 0.6      # ops below this confidence are applied but flagged for review
  fungible_resources: [gold, silver, arrows, rations, torches]   # extends built-in defaults
```

Holdings are written as an `inventory:` section in each holder's campaign
overlay (override YAML / emergent frontmatter), with a derived
`inventory_holdings` SQLite table rebuilt from those files. Files remain SSOT.

## Common Pitfalls

- **Don't write directly to SQLite** for data that has a file source of truth. Files are SSOT; SQLite is derived. Write the file; the watcher updates the index.
- **Don't bypass the read cascade.** Always resolve entities through the cascade (emergent → library refs → apply overrides). Direct file reads skip campaign-local state.
- **Don't put mechanics logic in core.** Mechanics modules are external packages. Core defines the API contract; modules implement it. No game-system-specific code in `backend/src/grimoire/`.
- **Mechanics authoring is the sanctioned write path into `data/mechanics/`.** The Library → Mechanics UI can scaffold a new module and edit the *declarative* parts of any module (manifest, sheet/content JSON Schemas, theme CSS), writing through `MechanicsAuthor` (`backend/src/grimoire/mechanics/authoring.py`). The `mechanics.py` behavioral logic is generated once as a green-loading stub and then hand-edited on disk — the UI never rewrites it. This is a deliberate dev-time action and does not contradict "modules are read-only at *runtime*".
- **Don't add telemetry or phone-home.** Grimoire is local-first with zero external data collection. No analytics, no crash reporting, no usage tracking.
- **Don't import across module boundaries** that shouldn't know about each other. Follow the module map — if there's no arrow between two modules, they shouldn't import each other. Use the event bus for fan-out.
- **Don't call another module's `_private` members.** If a neighbor needs `_find_post`-style access, promote the method to the owner's public API first. New `self._host._x` or `getattr(service, "_attr", …)` reach-ins are review blockers (#589 tracks retiring the existing ones).
- **Don't forget to update both file and index.** When changing content programmatically, write the file and let the watcher handle the index — or explicitly trigger an index update if the watcher isn't running (e.g., in tests).
- **Don't hand-roll what a shared helper already does.** datetime/JSON/id/slug strings, YAML + frontmatter I/O, and HTTP not-found translation all have canonical helpers (Code Conventions → *Shared helpers*). Reuse keeps behaviour consistent and fixes land in one place.
- **Don't re-flag sanctioned patterns when auditing or reviewing.** Table-gateway classes (one domain's SQL behind one small class), single-implementation Protocols *at documented module boundaries* (the boundary mechanism — new speculative intra-module Protocols are still fair game), and mutable Pydantic field defaults (Pydantic deep-copies them per instance) are deliberate choices or known false positives — see `docs/audits/2026-06-09-code-quality-audit.md` §6 before filing smell reports.

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
| `perf` | Performance | Regression benchmarks (opt-in: excluded from the default run) |

### Test database setup

Tests that need SQLite go through `stamp_migrated_db(path)` (in
`grimoire.testing.db_template`), which copies a once-per-process, fully-migrated
template instead of replaying all migrations for every test. Stamp the path
before constructing the `Database` — no `apply_migrations` call is needed:

```python
from grimoire.testing.db_template import stamp_migrated_db

db = Database(stamp_migrated_db(tmp_path / "campaigns.sqlite"), pool_size=2)
await db.connect()
```

Tests that exercise the migration machinery itself (or assert on schema
structure) still build an empty DB and call `apply_migrations` directly.

### Frontend Tests

- **Vitest** for unit and component tests
- **React Testing Library** for component interaction tests
- Tests live alongside source or in `__tests__/` directories

### What to Test

- New backend logic: unit test at minimum, integration test if it crosses modules
- New API endpoint: add a scenario-level test
- New frontend component: component test with React Testing Library
- Bug fix: write a regression test that fails without the fix

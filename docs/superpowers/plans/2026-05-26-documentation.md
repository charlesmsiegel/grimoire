# Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create comprehensive project documentation serving humans (README), AI agents (CLAUDE.md / AGENTS.md), and contributors (CONTRIBUTING.md).

**Architecture:** Four standalone markdown documents at the repo root, plus a `docs/images/` directory with placeholder filenames for screenshots. Each document is self-contained — no required cross-references to specs. AGENTS.md is an identical copy of CLAUDE.md.

**Tech Stack:** Markdown, Git

**Worktree:** `C:/Users/charl/github/grimoire/.worktrees/feat/documentation`

---

### Task 1: Create docs/images directory with placeholder README

**Files:**
- Create: `docs/images/README.md`

- [ ] **Step 1: Create the images directory and placeholder guide**

Create `docs/images/README.md`:

```markdown
# Screenshot Placeholders

Drop screenshots into this directory using the filenames below. The README references them with descriptive alt text — once the images are present, everything renders automatically.

## Required Images

| Filename | What to capture |
|----------|----------------|
| `hero-play-view.png` | Main play view showing a scene with PC post and LLM response, sidebar visible |
| `feature-campaign-management.png` | Campaign list or campaign creation dialog showing world/greeting selection |
| `feature-multi-pc.png` | A shared scene with two PCs and the Advance button visible |
| `feature-world-browser.png` | World browser showing characters, locations, or lore cards |
| `feature-mechanics-sheets.png` | A mechanical character sheet (e.g., WoD attributes with dot ratings) |
| `feature-image-generation.png` | An in-campaign generated image with the image panel visible |
| `gallery-cast-view.png` | Cast view showing character cards for a campaign |
| `gallery-timeline.png` | Timeline view showing in-game calendar and time progression |
| `gallery-ledger.png` | Ledger view showing facts, commitments, or relationships |
| `gallery-settings.png` | Campaign or app settings panel |

## Tips

- Crop to the relevant UI area — full desktop screenshots include too much noise
- Use a campaign with enough content to look interesting (several scenes, a few characters)
- Dark mode is fine; just be consistent across all screenshots
- Aim for ~1200px wide for hero, ~800px for features, ~600px for gallery thumbnails
```

- [ ] **Step 2: Commit**

```bash
git add docs/images/README.md
git commit -m "docs: add screenshot placeholder directory with guide"
```

---

### Task 2: Write README.md

**Files:**
- Modify: `README.md` (replace existing content)

- [ ] **Step 1: Write the full README**

Replace the contents of `README.md` with:

````markdown
# Grimoire

**A local-first RPG campaign companion that keeps your stories coherent.**

<!-- Uncomment badges once license is chosen and CI is public:
![License](https://img.shields.io/badge/license-TBD-blue)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![TypeScript](https://img.shields.io/badge/typescript-5.6%2B-blue)
-->

---

<!-- Replace with an actual screenshot: see docs/images/README.md for guidance -->
<!-- ![Grimoire — play view](docs/images/hero-play-view.png) -->
*Screenshot: drop `docs/images/hero-play-view.png` to display the main play view here.*

---

## What is Grimoire?

If you've used SillyTavern, KoboldAI, or any chat-based LLM tool for RPG play, you've hit the wall: characters lose their voice after twenty messages, the model forgets what it foreshadowed three scenes ago, time doesn't advance coherently, and what should be a campaign becomes a stream of disconnected posts.

Grimoire fixes this by putting a deterministic **Orchestrator** between you and the language model. Instead of letting the LLM drive the narrative, Grimoire assembles context from your world's lore, tracks facts and commitments, manages in-game time, and feeds the model exactly what it needs to stay consistent. The model becomes a service — powerful but directed. You stay in control.

Everything runs locally on your machine. Your worlds, your campaigns, your data. No cloud dependency, no telemetry, no accounts. Plug in any LLM provider — Anthropic, OpenAI, a local llama.cpp server — and play.

## Features

### Worlds and Campaigns

Build rich worlds with characters, locations, items, lore, factions, and opening scenarios. Run multiple campaigns in the same world, or create crossover campaigns that pull characters from one world into another's setting.

<!-- ![Campaign management](docs/images/feature-campaign-management.png) -->
*Screenshot: drop `docs/images/feature-campaign-management.png` here.*

### Multi-PC Play

Run one or many player characters in a single campaign. Each PC gets their own scenes with automatic LLM responses. When PCs meet in a shared scene, you post for each one and hit **Advance** when everyone's acted — the system responds addressing everyone present.

<!-- ![Multi-PC shared scene](docs/images/feature-multi-pc.png) -->
*Screenshot: drop `docs/images/feature-multi-pc.png` here.*

### Structured Continuity

Every fact the narrative establishes, every commitment a character makes, every relationship that forms — Grimoire extracts these from the LLM's prose and tracks them as structured state. The Orchestrator feeds relevant continuity back into future prompts so the model remembers what matters.

Low-confidence extractions go to a review queue so you stay in control of what becomes canon.

### Pluggable Mechanics

Grimoire ships with no built-in game rules. Instead, mechanics modules are external packages that implement a rich API: character sheets, dice, combat, capability queries, NPC behaviors, and custom UI themes. Install a World of Darkness module for dot-rated attributes and Disciplines. Install an Ars Magica module for Arts, Virtues, and Covenant management. Or play in pure narrative mode with no mechanics at all — that's the default.

<!-- ![Mechanical character sheet](docs/images/feature-mechanics-sheets.png) -->
*Screenshot: drop `docs/images/feature-mechanics-sheets.png` here.*

### Smart Context Assembly

The Context Builder assembles each prompt from explicit inputs: relevant characters, active scene history, applicable lore, mechanical state, continuity facts, and style guide. Context is deterministic and budget-aware — you can inspect exactly what went into every prompt through the observability dashboard.

### Image Generation

Generate illustrations for your scenes using integrated Stable Diffusion (via `diffusers`), or connect to Automatic1111, ComfyUI, or DALL-E. Image presets let you define a consistent visual style across a campaign — oil paintings for gothic horror, watercolors for high fantasy.

<!-- ![Image generation](docs/images/feature-image-generation.png) -->
*Screenshot: drop `docs/images/feature-image-generation.png` here.*

### Export Your Chronicles

Export campaigns to EPUB, Markdown, HTML, or plain transcripts. Your campaign's prose lives as Markdown files on disk — you can read, edit, or grep them with any tool you already use.

### Local-First, No Telemetry

All data lives on your machine at `~/.grimoire/`. No cloud storage, no analytics, no accounts. Backup is "zip the folder." Multiple Grimoire installations share one library.

## Quick Start

### Prerequisites

- **Python 3.12+** and [uv](https://docs.astral.sh/uv/)
- **Node 20+** and [pnpm](https://pnpm.io/) (or just `corepack`, which ships with Node)
- **Bash** — Linux/macOS have it natively; on Windows use Git Bash (bundled with [Git for Windows](https://git-scm.com/)) or WSL

### Install and Run

```sh
git clone https://github.com/your-username/grimoire.git
cd grimoire

scripts/install.sh     # installs backend + frontend deps in parallel
scripts/run.sh         # starts both servers and opens the browser
```

That's it. Grimoire opens in your browser at `http://127.0.0.1:5173`.

To stop:

```sh
scripts/shutdown.sh    # stops both servers and frees the ports
```

### Run Options

```sh
scripts/run.sh --backend-port 9000 --frontend-port 5180   # custom ports
scripts/run.sh --no-browser                                 # don't auto-open
scripts/run.sh --reload                                     # backend autoreload
scripts/run.sh -h                                           # full flag list
```

Ports also accept environment variables: `GRIMOIRE_BACKEND_PORT` / `GRIMOIRE_FRONTEND_PORT`.

### Manual Startup

If you prefer to run the backend and frontend separately:

```sh
# Backend (terminal 1)
cd backend
uv sync
uv run uvicorn grimoire.main:app --reload   # http://127.0.0.1:8173

# Frontend (terminal 2)
cd frontend
pnpm install
pnpm dev                                     # http://127.0.0.1:5173
```

The frontend dev server proxies `/api/*` and `/ws/*` to the backend automatically.

## How It Works

Grimoire organizes everything into three scopes:

| Scope | What it holds | Where it lives |
|-------|---------------|----------------|
| **Library** | Worlds, characters, items, locations, lore, factions, greetings, style guides, image presets | Markdown + YAML files under `~/.grimoire/library/` |
| **Campaign** | Play history (scenes), structured state (facts, commitments, sheets, relationships, embeddings) | Markdown + YAML files + SQLite under `~/.grimoire/campaigns/` |
| **Code** | Mechanics modules and plugins (LLM providers, image backends, export formats) | Python packages under `~/.grimoire/mechanics/` and `~/.grimoire/plugins/` |

Files are the source of truth for everything you'd want to read, edit, or grep. SQLite handles vector search, full-text search, and high-volume relational queries — it's a derived cache that rebuilds from files if deleted.

The **Orchestrator** drives every turn: it receives your post, asks the Context Builder to assemble a prompt, calls the LLM, runs the Extractor to parse structured state from the response, applies deltas to the state store, and appends the response to the scene file. The model never sees raw, unmanaged context.

## Extensibility

### Mechanics Modules

Game systems are external packages that implement Grimoire's Mechanics API. A module provides:

- **Sheet schemas** (JSON Schema) for characters, items, locations, factions
- **Capability queries** ("what can this character do?")
- **Dice and combat** resolution
- **CSS themes** for styled sheet rendering
- **Content schemas** for system-specific data

Writing a module requires only the API documentation — no knowledge of Grimoire internals.

### Plugins

Shallow adapters for external services:

- **LLM providers** — Anthropic, OpenAI-compatible, OpenRouter, llama.cpp
- **Embedding providers** — sentence-transformers (local), OpenAI, OpenRouter
- **Image generation** — Automatic1111, ComfyUI, DALL-E, Replicate, Diffusers
- **Export formats** — EPUB, HTML, Markdown, JSON, transcript

Each plugin kind has a small protocol (3-5 methods). Bundled plugins cover the common cases; adding your own is straightforward.

## Gallery

<!-- Uncomment as screenshots are added — see docs/images/README.md for guidance -->

<!-- | | | -->
<!-- |---|---| -->
<!-- | ![Cast view](docs/images/gallery-cast-view.png) | ![Timeline](docs/images/gallery-timeline.png) | -->
<!-- | *Cast — character cards and sheets* | *Timeline — in-game calendar* | -->
<!-- | ![Ledger](docs/images/gallery-ledger.png) | ![Settings](docs/images/gallery-settings.png) | -->
<!-- | *Ledger — facts, commitments, relationships* | *Settings — campaign configuration* | -->

*Screenshot gallery: drop images into `docs/images/` per the guide in `docs/images/README.md`, then uncomment the table above.*

## Configuration

### Data Directory

User content lives at `~/.grimoire/` by default — outside the repo, so multiple installations share one library. Set `GRIMOIRE_DATA_ROOT` to use a different location.

```
~/.grimoire/
├── library/          # worlds, style guides, image presets
├── campaigns/        # campaign files (scenes, overrides, sheets, images)
├── campaigns.sqlite  # structured state, indexes, embeddings
├── mechanics/        # installed mechanics modules
└── plugins/          # installed plugins
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GRIMOIRE_DATA_ROOT` | `~/.grimoire` | Root directory for all user data |
| `GRIMOIRE_BACKEND_PORT` | `8173` | Backend API port |
| `GRIMOIRE_FRONTEND_PORT` | `5173` | Frontend dev server port |
| `GRIMOIRE_BACKEND_HOST` | `127.0.0.1` | Backend bind address |
| `GRIMOIRE_FRONTEND_HOST` | `127.0.0.1` | Frontend bind address |
| `GRIMOIRE_OPEN_BROWSER` | `1` | Auto-open browser on `run.sh` (0 to disable) |
| `GRIMOIRE_BACKEND_RELOAD` | `0` | Enable uvicorn autoreload (1 to enable) |

## For Developers

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, branching, and PR guidelines.

### Tests and Lint

```sh
# Backend
cd backend
uv run pytest                    # all tests
uv run ruff check                # lint
uv run ruff format --check       # format check

# Frontend
cd frontend
pnpm test                        # vitest
pnpm lint                        # eslint
pnpm typecheck                   # tsc --noEmit
```

## License

TBD — see [specs/00-overview.md](specs/00-overview.md) for discussion (MIT or Apache 2.0).
````

- [ ] **Step 2: Review the README**

Read through the written file. Check that:
- All placeholder image paths match `docs/images/README.md`
- Quick start commands match `scripts/install.sh` and `scripts/run.sh`
- Environment variables match `vite.config.ts` and `scripts/run.sh`
- Data directory layout matches `specs/00-overview.md`

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: rewrite README as feature showcase with placeholder screenshots"
```

---

### Task 3: Write CLAUDE.md

**Files:**
- Create: `CLAUDE.md`

- [ ] **Step 1: Write the full CLAUDE.md**

Create `CLAUDE.md`:

````markdown
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
    orchestrator/        # Turn loop, advance trigger, forks, retcon
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
    hud/                 # Real-time info display protocol
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
bundled_plugins/         # Ships with the app (backend/bundled_plugins/)
```

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

### Canonical Turn Flow

1. Frontend → Orchestrator: `submit_post(campaign_id, pc_ref, text)`
2. Scene Manager appends post to scene file, updates YAML sidecar
3. Decision: single PC in scene → auto-respond; multiple PCs → wait for Advance
4. Orchestrator → Context Builder: assemble prompt from entities, continuity, mechanics, style
5. Orchestrator → LLM Gateway: stream completion to frontend
6. Orchestrator → Extractor: parse structured deltas from response
7. Orchestrator → State Store: apply deltas (new facts, commitments, relationships)
8. Scene Manager: append model response to scene file
9. Time Engine, Continuity, Characters: react to turn_complete event

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
````

- [ ] **Step 2: Review CLAUDE.md**

Read through the written file. Cross-check:
- Module list matches `backend/src/grimoire/` directory listing
- Dev commands match `pyproject.toml` scripts and `frontend/package.json` scripts
- Ruff config (line-length, target, rules) matches `pyproject.toml`
- TypeScript config (strict flags) matches `tsconfig.app.json`
- Data directory layout matches `specs/00-overview.md`

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add CLAUDE.md agent guide with architecture, conventions, and commands"
```

---

### Task 4: Create AGENTS.md as copy of CLAUDE.md

**Files:**
- Create: `AGENTS.md`

- [ ] **Step 1: Copy CLAUDE.md to AGENTS.md**

```bash
cp CLAUDE.md AGENTS.md
```

The content is identical. Both files serve the same purpose for different agent platforms (Claude Code reads CLAUDE.md, Codex and other agents read AGENTS.md).

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs: add AGENTS.md (identical to CLAUDE.md for Codex and other agents)"
```

---

### Task 5: Write CONTRIBUTING.md

**Files:**
- Create: `CONTRIBUTING.md`

- [ ] **Step 1: Write the full CONTRIBUTING.md**

Create `CONTRIBUTING.md`:

````markdown
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
````

- [ ] **Step 2: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs: add CONTRIBUTING.md with setup, branching, testing, and PR guidelines"
```

---

### Task 6: Final review

- [ ] **Step 1: Verify all files exist and are consistent**

```bash
ls -la README.md CLAUDE.md AGENTS.md CONTRIBUTING.md docs/images/README.md
```

Expected: all five files present.

- [ ] **Step 2: Verify AGENTS.md matches CLAUDE.md**

```bash
diff CLAUDE.md AGENTS.md
```

Expected: no differences.

- [ ] **Step 3: Verify image paths in README match docs/images/README.md**

Check that every image filename referenced in README.md appears in the table in `docs/images/README.md`:
- `hero-play-view.png`
- `feature-campaign-management.png`
- `feature-multi-pc.png`
- `feature-mechanics-sheets.png`
- `feature-image-generation.png`
- `gallery-cast-view.png`
- `gallery-timeline.png`
- `gallery-ledger.png`
- `gallery-settings.png`

Note: `feature-world-browser.png` is listed in `docs/images/README.md` but not currently referenced in the README features section (it can be added when the screenshot is available). This is intentional — the guide lists all useful screenshots, the README only uses what has corresponding content.

- [ ] **Step 4: Check cross-references between docs**

- README links to `CONTRIBUTING.md` — verify the link works
- README links to `specs/00-overview.md` — verify the file exists
- CONTRIBUTING.md links to `CLAUDE.md` — verify the link works

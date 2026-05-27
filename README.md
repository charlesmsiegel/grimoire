<p align="center">
  <img src="assets/icons/grimoire-128.png" alt="Grimoire" width="96" />
</p>

<h1 align="center">Grimoire</h1>

<p align="center"><strong>A local-first RPG campaign companion that keeps your stories coherent.</strong></p>

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

### Install and Run

```sh
git clone https://github.com/your-username/grimoire.git
cd grimoire

# Windows
scripts\setup.bat      # checks prerequisites, installs deps, creates desktop shortcut
scripts\run.bat        # starts both servers and opens the browser
scripts\shutdown.bat   # stops both and frees the ports

# macOS / Linux
./scripts/setup.sh     # checks prerequisites, installs deps, creates desktop shortcut
./scripts/run.sh       # starts both servers and opens the browser
./scripts/shutdown.sh  # stops both and frees the ports
```

The setup script checks for prerequisites (Python 3.12+, Node 20+, uv, pnpm) and offers to install any that are missing. It also creates a desktop shortcut with the grimoire icon.

That's it. Grimoire opens in your browser at `http://127.0.0.1:5173`.

### Run Options

Configuration is via environment variables:

```sh
GRIMOIRE_BACKEND_PORT=9000 GRIMOIRE_FRONTEND_PORT=5180 ./scripts/run.sh
GRIMOIRE_OPEN_BROWSER=0 ./scripts/run.sh          # don't auto-open the browser
GRIMOIRE_BACKEND_RELOAD=1 ./scripts/run.sh         # backend autoreload (uvicorn --reload)
```

`scripts/shutdown.sh` picks ports up from the state file `run.sh` writes (`.grimoire-run.env`), so it works from any terminal.

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

TBD — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for discussion (MIT or Apache 2.0).

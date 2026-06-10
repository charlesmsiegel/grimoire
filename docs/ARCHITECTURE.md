# 00 — Architecture Overview

This is the authoritative overview for **Grimoire**, a local-first RPG campaign companion. Where another spec disagrees with this document, the other spec is out of date.

## What Grimoire is

A local-first companion app for running long-form RPG campaigns with an LLM. Grimoire exists because chat-based play drifts: characters lose their voice, scenes have no boundaries, time doesn't advance coherently, foreshadowing gets forgotten, and what should be a campaign becomes a stream of disconnected posts.

The fix is to put a thin Orchestrator between the user and the model. The Orchestrator deterministically assembles context, calls the LLM, parses output into structured state, and updates a typed data model. The model becomes a service, not the driver. Mechanics, LLM providers, embedding providers, image backends, and export formats are swappable through stable APIs.

The name comes from the magical-book tradition: a single bound volume containing the spells, lore, beings, and rules of a world. Grimoire holds your library of worlds, the characters who inhabit them, the rules they play under, and the chronicles of what they've done.

## Tech stack

- Python (FastAPI) backend
- TypeScript (React + Vite) frontend
- Markdown + YAML files for content and narrative output
- SQLite (with FTS5 and `sqlite-vec`) for structured state and search
- In-process event bus
- Desktop-first; mobile out of scope for v1

Full rationale per choice in the Tech and infra decisions table near the end.

## The three scopes

Every piece of data in the system lives in exactly one scope. This is the foundational concept; the rest of the architecture follows from it.

| Scope | What it holds | Where it lives | Mutability |
|---|---|---|---|
| **Library** | Worlds (with their characters/items/locations/lore/factions/greetings); style guides; image presets | Markdown + YAML files under `data/library/` | Edited by the user (in the app or in a text editor) |
| **Campaign-local** | Play history (scenes, posts), structured play state (facts, commitments, sheets, embeddings, deltas, overrides) | Markdown + YAML files under `data/campaigns/<id>/` for narrative; SQLite at `data/campaigns.sqlite` for structured | Free-write during play |
| **Code (external)** | Mechanics modules (first-class API) and plugins (LLM providers, embedding providers, ImageGen backends, export adapters) | Python packages on disk under `data/mechanics/` and `data/plugins/` | Read-only at runtime; installed and updated separately from the app |

## Library, mechanics, plugins

Three categories of content and code. Crisp definitions:

- **Library** is *content the user authors*. A world with its cast, places, items, lore, factions, opening scenarios. Markdown files. You can open a character card in your text editor and rewrite her voice anchor by hand.

- **Mechanics** is *the game system that governs a campaign* — WoD, Ars Magica, Blades in the Dark, D&D. Mechanics has a dedicated first-class API in Grimoire (not the generic plugin protocol) because it's a deep integration: sheets across multiple entity kinds, content schemas, dice, combat, character creation, NPC time-ticks, capability queries, declarative UI. Mechanics *modules* are external packages — the user installs them separately. Grimoire ships the contract; the community ships the implementations. See the Mechanics section below for the full story.

- **Plugins** are *shallow adapters* for things the app needs to talk to outside itself: LLM providers (Anthropic, OpenAI, llamacpp, Oobabooga), embedding providers (sentence-transformers, OpenAI, Cohere), secondary ImageGen backends (Automatic1111, ComfyUI, DALL-E), export formats. The protocol per plugin kind is small — three to five methods. New ones get added often.

In one line: **library is what you play with, mechanics is the rules you play under, plugins are how the app talks to the outside.**

## Filesystem layout

Everything Grimoire stores on disk:

```
data/
├── library/                              # SSOT for content
│   ├── worlds/
│   │   ├── wod-london/
│   │   │   ├── world.yaml              # metadata, calendar, atmosphere
│   │   │   ├── characters/
│   │   │   │   ├── alistair-hyde-smythe.md
│   │   │   │   └── ...
│   │   │   ├── items/
│   │   │   │   ├── the-camden-blade.md
│   │   │   │   └── ...
│   │   │   ├── locations/
│   │   │   │   ├── camden-market.md
│   │   │   │   └── ...
│   │   │   ├── lore/
│   │   │   │   └── the-london-by-night.md
│   │   │   ├── factions/
│   │   │   │   └── the-camarilla.md
│   │   │   └── greetings/
│   │   │       └── elysium-opening.md
│   │   ├── wod-nyc/
│   │   ├── mythic-europe/
│   │   ├── faerun/
│   │   └── ...
│   ├── style-guides/
│   │   ├── gothic-horror.md
│   │   └── high-fantasy.md
│   └── image-presets/
│       ├── oil-painting.yaml
│       └── modern-cinematic.yaml
│
├── campaigns/                            # SSOT for narrative output
│   └── <campaign-id>/
│       ├── campaign.yaml                 # composition refs, PCs, worlds
│       ├── scenes/
│       │   ├── 0001-elysium-opening.md          # prose
│       │   ├── 0001-elysium-opening.yaml        # metadata sidecar
│       │   └── ...
│       ├── overrides/                    # campaign edits to library entities
│       │   └── worlds/wod-london/characters/alistair-hyde-smythe.yaml
│       ├── emergent/                     # campaign-spawned content
│       │   ├── characters/the-bartender.md
│       │   ├── items/anitas-locket.md
│       │   └── locations/back-alley-of-soho.md
│       ├── sheets/                       # mechanical sheets per entity-kind per system
│       │   ├── characters/alistair-hyde-smythe.wod-mechanics.yaml
│       │   ├── locations/camden-market.wod-mechanics.yaml   # a Node
│       │   └── items/the-camden-blade.wod-mechanics.yaml
│       └── images/
│           ├── img-0001.png
│           └── img-0001.yaml             # metadata sidecar
│
├── campaigns.sqlite                       # structured state, indexes, embeddings
│
├── mechanics/                            # user-installed mechanics modules (external)
│   ├── wod-mechanics/
│   │   ├── manifest.yaml
│   │   ├── mechanics.py
│   │   ├── sheets/                       # JSON Schemas per entity kind
│   │   │   ├── character.json
│   │   │   ├── location.json             # Node / Haven schemas
│   │   │   └── item.json                 # weapons, fetishes
│   │   ├── content/
│   │   ├── theme.css                     # optional CSS theme for rendered sheets
│   │   └── ui/                           # custom JS bundles (v2; optional)
│   ├── another-campaign-mechanics/
│   └── ...
│
└── plugins/                              # user-installed plugins
    ├── llm-anthropic/
    ├── llm-llamacpp/
    ├── embed-sentence-transformers/
    ├── imagegen-a1111/
    └── ...
```

Backup is "zip `data/`." Search the entire narrative archive with `rg "Alistair" data/library/ data/campaigns/`.

## Storage model

> **Files for things you'd want to read, edit, grep, share, or version. SQLite for vector search, full-text search, structured-relational queries, and high-volume transient state. Files are the source of truth; SQLite is a derived cache plus a store for things that don't render as readable files.**

**Files (SSOT):** Library content. Campaign scenes (prose + YAML sidecar). Campaign-local overrides, emergent content, mechanical sheets, image metadata sidecars. Everything you might want to edit by hand or `grep` for.

**SQLite (cache and query engine):**
- Library index (parsed file content for fast query)
- Embeddings (vectors over scenes, posts, character cards, lore, facts)
- FTS indexes
- Facts (high volume; relational queries needed)
- Commitments (queried by status, due_by, character)
- Knowledge state (per-fact-per-character)
- Relationships (mutable per-turn)
- Transient character/location/faction state (mood, location, intent — too noisy for files)
- Delta log (append-only audit trail)
- Review queue (low-confidence extracted deltas)
- Library snapshots for version-pinned campaigns

A file watcher monitors `data/library/` and `data/campaigns/`; changes update the SQLite index incrementally. If `campaigns.sqlite` is deleted, the app rebuilds it from files on startup.

## Library content within a world

Each world directory contains structured subdirectories for the kinds of entity that make up a world:

- **characters** — the cast (each with a `role` field: `pc`, `major_npc`, `minor_npc`, `ensemble`, `named_flavor`)
- **items** — named items with narrative weight (the Sword of Drachenheim, the Whispering Locket). Generic items don't need cards; named ones do.
- **locations** — places
- **lore** — encyclopedic entries (history, religion, customs, magical systems) with keyword triggers for context inclusion
- **factions** — organizations, houses, orders
- **greetings** — opening scenarios for new campaigns

Each `.md` file has YAML frontmatter for structured fields plus a markdown body for prose. `world.yaml` per world carries metadata (calendar, atmosphere style, defaults).

**Greetings** are world-level scenario starters: a starting location, in-game time, present cast, opening narration, tags. Picking a greeting at campaign creation populates scene 1. SillyTavern's "alternate greetings," scoped to worlds.

Style guides and image presets are top-level (outside any world) because they may apply across worlds.

## Campaign composition

A campaign references library content. Compositions support both the common single-world case and crossovers.

```yaml
# data/campaigns/<id>/campaign.yaml
id: by-night-london
name: "London by Night"

composition:
  worlds:
    - id: wod-london
      priority: 1
      include: [characters, items, locations, lore, factions, greetings]  # default: all
      bound_at_version: 7
      track_latest: false
  mechanics: wod-mechanics
  style_guide_id: gothic-horror
  image_preset_id: oil-painting

pcs:
  - character_ref: worlds/wod-london/characters/alistair-hyde-smythe
    name: "Alistair Hyde-Smythe"
    owner: local
    active: true

greeting_id: elysium-opening
content_boundaries: ...
```

Default flow: pick one world → get everything in it (cast, items, world, lore, factions, greetings).

Crossover:

```yaml
composition:
  worlds:
    - id: faerun
      priority: 1
      include: [characters]              # the cast
    - id: wod-nyc
      priority: 2
      include: [locations, lore]         # the world
  mechanics: wod-mechanics
```

Faerûn characters operating in WoD New York under WoD rules.

### Read cascade

When any module reads an entity in a campaign context:

```
1. Check campaign-local emergent content (files in campaigns/<id>/emergent/) and SQLite.
2. If not found, walk the campaign's world refs in priority order:
   For each ref, check library_index (which mirrors data/library/).
3. Return the first match.
4. Apply any campaign-local override (files in campaigns/<id>/overrides/) on top.
5. If nothing matches, the entity is missing.
```

Edits in a library view write the file (and update the index). Edits in a campaign view write a campaign-local override file (the library is unchanged). "Save back to library" propagates an override into the underlying library file.

### Version pinning

Campaigns version-pin library refs by default. Library edits don't silently mutate active campaigns: pinned campaigns continue reading their bound version (via SQLite snapshots) until the user explicitly upgrades the ref, with a diff. The opposite mode is `track_latest: true`, which always reads current files.

### Character variants (in-world diff overlays)

A **variant** is an alternate take on a character within its world: a diff overlay on the base card that a campaign can select (#579). Variants live in the library next to the base character:

```
library/worlds/<world>/characters/<id>.md                      # base card (or <id>/card.md)
library/worlds/<world>/characters/<id>/variants/<variant>.md   # diff overlay
```

A variant file's frontmatter holds **only the fields that differ** from the base, plus a reserved `label` (display name; `id` is reserved and ignored so a variant can never change the character's identity). A non-empty body replaces the base prose. Variant files are plain markdown a user can hand-edit; they never enter `library_index` — the watcher emits change events for them (cache invalidation) without indexing.

A campaign selects at most one variant per character in `campaign.yaml` (file is SSOT; `PUT /api/campaigns/<id>/variants`):

```yaml
variants:
  worlds/wod-london/characters/alistair: young
```

The resolve cascade applies the selection between the library base and any campaign override: **base → variant diff → campaign override**. Frontmatter merges with the same semantics as overrides (top-level keys replace; `extras` merges key-by-key with `None` tombstones). Unselected campaigns read the base; a dangling selection (variant file deleted) logs a warning and falls back to the base. Variant overlays always read live files — they are not version-pinned with the base snapshot.

Entity ids stay unique per world (one id, one file) and `version` remains a content-revision counter — variants are the only sanctioned way to keep multiple coexisting takes on one character. There is no cross-world linkage: the same id in two worlds is two unrelated entities.

Authoring lives in the entity editor's **Variants** tab; per-campaign selection in the campaign's **Cast** view.

## Multiple PCs

A campaign has a list of PCs (one or more). Each PC is a character with `role: pc` somewhere in the library, plus an `owner` field (`local` in v1, account id in future multiplayer).

```yaml
pcs:
  - character_ref: worlds/wod-london/characters/aleksandr
    name: "Aleksandr"
    owner: local
    active: true
  - character_ref: worlds/wod-london/characters/beatrice
    name: "Beatrice"
    owner: local
    active: true
```

**Per-PC scenes (the common case).** Each PC has their own current scene. Aleksandr is in scene 47 (a club in Camden); Beatrice is in scene 49 (Chantry in Whitechapel). The scenes are independent; the LLM auto-responds to each PC's posts. The Frontend offers a PC switcher.

**Shared scenes (the crossover case).** When a scene has 2+ PCs present, auto-response is disabled. The user posts as PC A, then as PC B, then clicks **Advance** to trigger the system. The advance trigger prevents the LLM from guessing "are all PCs done acting?" — the user signals when the system should respond. When a scene drops back to one PC, auto-response resumes.

**Post authorship.** Every post carries an `author_pc_ref` (player-as-PC) or an author identifier (narrator/NPC). The audit log records who acted; the Context Builder uses this for POV.

**Future multiplayer.** The `owner` field is `local` in v1. v2 can promote it to an account id, letting different humans control different PCs in a shared campaign. The advance trigger maps cleanly to "wait for all participating owners to be ready." Synchronization (real-time presence, conflict resolution) is a v2 concern; v1 doesn't preclude it.

## Mechanics in depth

Plugin protocols are deliberately small — three to five methods per kind. That's the right shape for "adapt an LLM API to our gateway" but the wrong shape for "implement the rules of World of Darkness." Mechanics has its own dedicated API surface, treated as core architecture, with mechanics modules living outside the app and installed separately. Writing a module reads the API documentation, not Grimoire's source. The API is rich enough to support real game systems and self-contained enough that authors don't need to know how the rest of Grimoire works.

### Multi-entity coverage

Mechanics covers more than just rolls. A mechanics module can attach sheets to multiple library entity kinds — characters, items, locations, factions — and declare powers and capabilities the rest of the app can query directly.

- **Freeform** (`mechanics: null`): characters, items, locations, factions are pure narrative; no sheets, no mechanical layer.
- **WoD**: characters get Attributes/Abilities/Disciplines/Backgrounds sheets; locations can be **Nodes** (Quintessence rating, Tass, ward status) or Havens; items get weapon stats, fetish data; factions can carry chronicle-level mechanics.
- **Ars Magica**: characters (magi) get Arts/Virtues/Flaws/Spells sheets; companions and grogs have their own templates; locations can be **Covenants** (vis sources, library, lab spaces); items can be enchanted (effects, levels).
- **D&D**: characters get class/level/stats/spells; items get magical properties; locations are mostly narrative.

Sheets are stored campaign-local at `data/campaigns/<id>/sheets/<entity-kind>/<entity-id>.<mechanics-id>.yaml` — human-readable, diffable, editable in your text editor.

### Powers and capabilities

Powers and capabilities are queryable. The Context Builder can ask "what can Aleksandr do?" and get his Disciplines plus their mechanical effects. The Extractor can recognize "Aleksandr uses Celerity" in prose and propose a Blood Pool spend. The UI can render a character's capabilities as a tagged list with explanations on hover. The mechanics module owns the vocabulary and the answers.

### Sheet UI rendering

Mechanics modules ship JSON Schema declarations for each sheet kind. The Frontend renders forms from those schemas using a curated widget library that handles TTRPG patterns: `dot-rating` (WoD attributes), `dice-pool`, `health-track`, `power-list` (Disciplines, Spells), `grid-rating` (Ars Magica Form/Technique grid), `slot-list`, `keyword-list`, plus standard text/number/select. Eight to twelve widgets cover most systems. A mechanics author writes JSON Schema and Python; no JavaScript required.

Mechanics modules can also ship a CSS theme that styles their sheets — gothic-horror palette for WoD, vellum-and-ink for Ars Magica. The Frontend isolates per-mechanics CSS so themes don't leak across systems.

For sheets the widget library can't express (complex combat trackers, custom talent trees, 3D dice rollers), v2 adds a custom-component escape hatch: mechanics ships a pre-built JS bundle that the Frontend dynamically imports for specific sheet kinds. The mechanics manifest declares which sheets have custom components; the Frontend tries custom first and falls back to schema rendering. v1 does not include this; the widget library is sufficient to ship.

### `mechanics: null`

Campaigns can run with `mechanics: null` — no mechanics module selected, pure narrative play. This is a fully supported mode and is the default first-run experience. The sample world that ships with Grimoire plays in this mode without any mechanics installed.

## Architecture

### Module map

```
                       ┌──────────────────────┐
                       │   Frontend (React)   │
                       └──────────┬───────────┘
                                  │  REST + WebSocket
                       ┌──────────▼───────────┐
                       │     Orchestrator     │  per-campaign turn loop
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
                   │  files SSOT +       │
                   │  SQLite cache       │
                   └─────────────────────┘
                            ▲
                            │
         ┌────────┬─────────┼─────────┬────────┐
         │        │         │         │        │
      Characters World Continuity   Time   Extractor
                                    Engine
```

Supporting modules (cross-cut concerns): **Plugins** (manages plugin lifecycle), **Observability** (audit, replay, metrics), **Export** (artifact generation).

### Modules at a glance

| # | Module | Role |
|---|---|---|
| 01 | Orchestrator | Drives the per-campaign turn loop |
| 02 | Context Builder | Cross-scope read, prompt assembly |
| 03 | State Store | Files + SQLite hybrid; cascade resolution |
| 04 | Extractor | Model output → state deltas |
| 05 | LLM Gateway | LLM and embedding provider abstraction |
| 06 | Mechanics | Defines the Mechanics API; modules are external |
| 07 | Time Engine | In-game time per campaign |
| 08 | Characters | Character-specific behaviors layered on World (voice, drift, tier, PCs, variants) |
| 09 | World | World container + all entity-kind storage and CRUD |
| 10 | Scene Manager | Scenes, posts, multi-PC advance trigger |
| 11 | Continuity | Facts, commitments, contradictions |
| 12 | ImageGen | Integrated diffusers backend; alternative backends via plugins |
| 13 | Export | EPUB and markdown built-in; additional formats via plugins |
| 14 | Frontend | UI contract |
| 15 | Plugins | Plugin lifecycle (adapter-shaped integrations only) |
| 16 | Observability | Audit, replay, metrics |
| 17 | Testing | Test strategy |
| 18 | Library | File layout, watcher, index |
| 19 | External Influences | Adopted patterns, rejected anti-patterns, boundary tests |

### Communication

Two mechanisms.

**Direct typed calls (synchronous).** Modules expose Python `Protocol` interfaces. Callers depend on the interface, not the implementation. Reads are free; writes go through the owning module.

**Event bus (asynchronous, fan-out).** The Orchestrator owns the bus; modules subscribe.

Core events: `turn_started`, `context_built`, `model_response_received`, `deltas_extracted`, `turn_complete`, `scene_started`, `scene_ended`, `time_advanced`, `npc_tick_complete`, `fact_recorded`, `commitment_created`, `commitment_paid_off`, `drift_detected`, `library_file_changed`, `library_indexed`, `entity_promoted`, `library_ref_upgraded`, `advance_requested` (multi-PC), `pc_post_appended`.

### Canonical turn flow

```
1. Frontend → Orchestrator: submit_post(campaign_id, pc_ref, text)
2. Orchestrator: emit pc_post_appended
3. Scene Manager → append post to scenes/<id>.md, update YAML sidecar
4. Decision: should the LLM respond now?
   - If active scene has 1 present PC → YES, continue
   - If active scene has 2+ present PCs → NO, return; wait for advance_requested
5. (When triggered) Orchestrator → Scene Manager: is_scene_break(player_input)?
6. Orchestrator → Context Builder: build(campaign_id, scene)
   ├─ Resolves entities via cascade (campaign-local → library refs)
   ├─ Queries Characters, World, Scene Manager, Continuity
   └─ Asks Mechanics: should_roll(player_input)?
7. Orchestrator → LLM Gateway: complete(prompt) (streams to Frontend)
8. Orchestrator → Extractor: extract(response)
   ├─ New entities default to campaign-local emergent
   └─ Low-confidence deltas → review queue
9. Orchestrator → State Store: apply deltas
10. Scene Manager: append model response to scene file
11. ImageGen: should_illustrate? generate using campaign's image preset
12. Orchestrator: emit turn_complete
13. Time Engine listens, advances clock if scene indicates time passage
14. Continuity listens, updates ledger
15. Characters listens, runs drift check periodically
```

## Concrete workflows

### Starting a new campaign in an existing world

1. New campaign → pick world `wod-london`
2. Pick mechanics: `wod-mechanics` (must be installed) or `mechanics: null` for narrative
3. Pick or create a PC: select a character with `role: pc`, or create one
4. Pick a greeting (or skip)
5. Done

### Same world, different mechanics

1. New campaign → world `mythic-europe`
2. Mechanics: `another-campaign-mechanics`
3. App: "Active cast has no Ars Magica sheets. Create empty sheets?"
4. Try the same world again with `mechanics: null` for narrative-only play

### Crossover composition

1. New campaign → worlds: `faerun` (characters only) + `wod-nyc` (locations + lore only)
2. Mechanics: `wod-mechanics`
3. Done — Faerûn cast operating in WoD New York under WoD rules

### Two PCs in one campaign (vampire + mage)

1. New campaign → world `wod-london`, mechanics `wod-mechanics`
2. Add PC 1: vampire "Aleksandr"
3. Add PC 2: mage "Beatrice"
4. Each gets their own starting scene; the LLM auto-responds in each
5. Later, both PCs meet at Elysium — shared scene, advance button appears
6. Post as Aleksandr, post as Beatrice, click Advance; system responds addressing both

### Mid-play emergent character → promotion

1. The bartender at the Camden club is introduced by the model in scene 47 of `by-night-london`
2. Stored as `data/campaigns/by-night-london/emergent/characters/the-bartender.md`
3. Player wants him in future WoD London campaigns → "Promote to library, target: wod-london"
4. File written to `data/library/worlds/wod-london/characters/the-bartender.md`
5. Watcher picks it up; next WoD London campaign sees him by default

### Library edit with version pinning

1. Edit `data/library/worlds/wod-london/characters/alistair-hyde-smythe.md`
2. Watcher catches the change; library_index.version increments
3. Pinned dependent campaigns show "Alistair updated; upgrade available"; `track_latest` campaigns read the new version immediately
4. User upgrades campaign A (sees a diff first), leaves B and C pinned

### Mechanics extending a location (Node)

1. `wod-london` has location `data/library/worlds/wod-london/locations/the-rookery.md` (narrative description)
2. With `wod-mechanics` active, the user creates `data/campaigns/by-night-london/sheets/locations/the-rookery.wod-mechanics.yaml`
3. The sheet uses the WoD `location.json` schema, declaring the Rookery as a Node with rating 4, certain Tass, ward status
4. The Context Builder surfaces "this is a Node (Quintessence 4/day)" alongside the narrative description
5. Mechanics's capability query for the Rookery returns its Node properties on demand

### Variant selection per campaign

1. `worlds/wod-london/characters/alistair.md` has a variant overlay `characters/alistair/variants/young.md` (`label: Young Alistair`, `age: "25"`, replacement prose)
2. Campaign A selects it (`variants: {worlds/wod-london/characters/alistair: young}` in `campaign.yaml`); Campaign B selects nothing
3. Campaign A resolves Alistair as the young portrayal (base fields it doesn't override cascade through); Campaign B keeps the base — same library files, no duplication

## What ships by default

**Bundled plugins**:
- LLM providers: `anthropic`, `llamacpp` (local via `llama-cpp-python`)
- Embedding providers: `sentence-transformers` (local), `openai-embeddings`
- ImageGen primary: integrated `diffusers` backend (downloads SDXL or similar on first use)
- ImageGen secondary: `automatic1111` (HTTP), `comfyui` (HTTP, with workflow loaders for new models before `diffusers` catches up), `dalle`
- Export adapters: `epub`, `markdown`

**Bundled mechanics modules**: none. Grimoire ships with no mechanics. Users install what they want from external sources — official mechanics repos, community packages, or their own implementations. The Mechanics API is the contract; modules implement it independently.

**Bundled library content**: one small sample world (`hello-world`) with two characters, one item, one location, one greeting — for first-run demo. Playable in narrative mode without any mechanics installed. Empty otherwise.

**Out of the box**: open Grimoire, pick the sample world, pick a greeting, start playing in narrative mode. To play with rules, install a mechanics module (e.g., `wod-mechanics`, `another-campaign-mechanics`, `dnd5e-mechanics`). To play with your own content, drop a world directory under `data/library/worlds/` or use the import tool.

## What Grimoire is not

- **Not a real-time MMO.** No background ticking. v1 is single-user; multiplayer is a v2 design possibility.
- **Not a system-specific tool.** Zero built-in mechanics. Even dice are owned by the mechanics module.
- **Not a generic LLM frontend.** SillyTavern already exists. The point is that prompts are *managed* and state is *structured*.
- **Not opinionated about prose.** Style guides and content boundaries are user-supplied.
- **Not a telemetry product.** No phone-home, no analytics.

## Design principles

1. **Three scopes.** Library (content), campaign-local (play state and output), code (mechanics modules and plugins).
2. **Files for everything readable; SQLite for everything queryable.** SSOT is files; SQLite is a cache and query engine.
3. **Worlds own their content.** A world is a world; its cast, items, places, lore, factions, and greetings live inside it.
4. **Mechanics is first-class with an external implementation.** Grimoire defines the API; mechanics modules are installed separately. Writing a module reads the API docs, not Grimoire's source.
5. **No mechanics is a valid mode.** Campaigns can run with `mechanics: null`.
6. **Conductor, not companion.** A thin Orchestrator drives the turn; the model is called when needed.
7. **Plugins for shallow adapters.** Small protocols, easy to add.
8. **State is structured and audited.** Every campaign change is a recorded delta with a source. Undo and fork are first-class.
9. **Context is deterministic and budget-aware.** Each turn's prompt is assembled by one component from explicit inputs.
10. **In-game time, not real time.** Time advances only when fiction advances.
11. **Multiple PCs from v1.** Single-PC auto-responds; multi-PC requires explicit advance. Future multiplayer is preserved by the `owner` field.

## Tech and infra decisions

| Decision | Choice | Rationale |
|---|---|---|
| App name | **Grimoire** | A bound book of spells, lore, beings, and rules — what the app holds |
| Backend language | Python (FastAPI) | Ecosystem match for ML/LLM/embeddings; `diffusers` integration |
| Frontend | TypeScript (React + Vite) | Standard, mature |
| Library storage | Markdown + YAML files | SSOT user can edit; greppable; git-friendly |
| Narrative output (scenes) | Markdown + YAML sidecar | Greppable prose; fast metadata index |
| Structured state | SQLite + FTS5 + sqlite-vec | Vector + relational + transactional |
| File watcher | `watchdog` | Standard Python; cross-platform |
| Image generation primary | `diffusers` integrated | Self-contained; ships working out of the box |
| Image generation alternatives | A1111 / ComfyUI / DALL-E plugins | Power-user escape hatches |
| Embedding default | `sentence-transformers` local | Works offline; configurable to remote |
| LLM default | `anthropic` (cloud) + `llamacpp` (local) | Both work out of the box |
| Local model server | `llama-cpp-python` adapter | Pointing at a GGUF file just works |
| Mechanics modules | External; not bundled | First-class API contract; community/user implementations |
| Default mechanics | None; `mechanics: null` works | App is usable for narrative play with no mechanics installed |
| Mobile | Out of scope for v1 | Desktop-first |
| Telemetry | None | Local-first |
| License | Open source (MIT or Apache) — TBD | Consistent with ethos |

## Reading order

Numerical order is historical; conceptual order:

1. **00-overview.md** (this file) — the authoritative architecture
2. **18-library.md** — file layout, indexing, composition
3. **03-state-store.md** — hybrid storage architecture
4. **09-world.md** — world as container
5. **08-characters.md** — character-specific behaviors layered on World; PCs and multi-PC
6. **01-orchestrator.md** — turn loop, advance trigger
7. **02-context-builder.md** — prompt assembly across scopes
8. **05-llm-gateway.md** — LLM and embedding providers
9. **06-mechanics.md** — first-class Mechanics API
10. **04-extractor.md** — output → state
11. **07-time-engine.md** — in-game time
12. **10-scene-manager.md** — scenes, posts, multi-PC advance
13. **11-continuity.md** — facts, commitments, foreshadowing
14. **12-imagegen.md** — integrated diffusers + adapter plugins
15. **13-export.md** — EPUB, markdown, plugin formats
16. **14-frontend.md** — UI contract; PC switcher, advance button
17. **15-plugins.md** — plugin lifecycle (adapter-shaped only)
18. **16-observability.md** — audit, replay, metrics
19. **17-testing.md** — test strategy
20. **new/19-external-influences.md** — what Grimoire takes from neighbor projects (SillyTavern, Marinara) and what it explicitly rejects

When other specs disagree with this overview, this overview wins until they're updated.

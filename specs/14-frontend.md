# 14 — Frontend

## Purpose

The Frontend is the user-facing application: a TypeScript/React/Vite single-page app communicating with the Python backend over REST + WebSocket. It surfaces the library, plays campaigns, renders character and entity views, hosts the sheet widget library, and orchestrates multi-PC turn flow.

This spec describes the *contract* between Frontend and backend modules: what data the UI consumes, what actions it dispatches, the navigational structure, the sheet widget library. It does not lock in visual design.

## Design principles

1. **Thin presentation layer.** All logic lives in backend modules. Frontend reads state and dispatches actions.
2. **Library and campaigns are first-class navigation.** Top-level: Library, Campaigns. Switch freely.
3. **Real-time updates via events.** WebSocket pushes streaming responses, image-gen status, drift alerts, library file changes.
4. **No silent state.** Review queue items, drift alerts, contradictions, overdue commitments, pending image jobs, library version drift — all surfaced.
5. **Multi-PC clarity.** PCs and their current scenes are visible; advance button only appears when needed.
6. **Keyboard-first where it matters.** Long-session ergonomics.
7. **Desktop-prioritized.** Mobile out of scope for v1.

## Top-level navigation

```
┌─────────────────────────────────────────────────────────────┐
│  LIBRARY                                                    │
│  ├─ Worlds                                                │
│  │   ├─ wod-london                                          │
│  │   ├─ wod-nyc                                             │
│  │   ├─ mythic-europe                                       │
│  │   ├─ faerun                                              │
│  │   └─ [+ New world]                                     │
│  ├─ Style Guides                                            │
│  ├─ Image Presets                                           │
│  ├─ Installed Mechanics                                     │
│  │   ├─ wod-mechanics                                       │
│  │   ├─ another-campaign-mechanics                                │
│  │   └─ [Browse / install mechanics modules]                │
│  └─ Installed Plugins                                       │
│      ├─ LLM providers                                       │
│      ├─ Embedding providers                                 │
│      ├─ ImageGen backends                                   │
│      └─ Export adapters                                     │
│                                                              │
│  CAMPAIGNS                                                   │
│  ├─ by-night-london         ← active                        │
│  ├─ a-saga-in-iberia                                        │
│  ├─ baldurs-gate-revisited                                  │
│  └─ [+ New campaign]                                         │
└─────────────────────────────────────────────────────────────┘
```

Switching campaigns is fast (loads the campaign's composition; scopes all reads). The library remains constant across switches.

## Library views

### Worlds

- List of worlds (grid or list with quick stats — character count, item count, etc.)
- Click → world detail with tabs:
  - Characters
  - Items
  - Locations
  - Lore
  - Factions
  - Greetings
  - Meta (calendar, atmosphere, defaults)
  - Dependent campaigns

### Per-entity-kind views

Within a world:
- Grid or list of cards
- Click → editor with frontmatter form + markdown body
- For characters: voice editor, image prompt template, capabilities tab (lists capabilities under each installed mechanics module that this character has a sheet for)
- Edit writes the library file via Library API; watcher updates the index

Editing a library entity that has dependent campaigns surfaces a confirmation: "edits will be visible to campaigns when they upgrade their ref; pinned campaigns continue seeing the previous version."

### Cross-world variant view

For characters, items, locations, etc. with shared asset id across worlds, a "Variants" tab lists them with diff preview. Same id = same entity across worlds; the UI is purely informational.

### Style guides, image presets

Simple text/config editors. Image preset editor previews against a sample.

### Installed mechanics

- List of installed mechanics modules with manifest summary
- Per-module detail: API version, declared sheet kinds, declared content kinds, capability declarations, sheet schema preview, theme.css preview
- "Browse mechanics modules" links to a community index (out of v1; conceptually planned)
- Drop-in install workflow: user copies a directory into `data/mechanics/<id>/`, restarts app or hits "rescan"
- Errors surfaced clearly: "wod-mechanics 1.2 failed: import error in mechanics.py at line 47"

### Installed plugins

Similar to mechanics but for plugin adapters. Plugin kinds:
- LLM providers (with model lists, default config)
- Embedding providers
- ImageGen backends
- Export adapters

## Campaign Play view

```
┌─────────────────────────────────────────────────────┬─────────────────┐
│  Top bar: campaign name | active PC switcher        │  Side panel     │
│                                                     │                 │
│  Scene header                                       │  Present cast   │
│  - location, in-game time, present cast             │  Active threads │
│  - source badges (📚 from wod-london)               │  Capabilities   │
│                                                     │   (active PCs)  │
├─────────────────────────────────────────────────────┤  Mechanics      │
│                                                     │   (rolls, slots)│
│  Scene pane                                         │                 │
│  - posts in order, each with author label           │  Quick actions  │
│  - generated images inline                          │  - regen        │
│  - mechanical event chips                           │  - undo         │
│  - source badges on referenced entities             │  - end scene    │
│  - drift warning banners (if any)                   │  - skip time    │
│                                                     │  - manual fact  │
├─────────────────────────────────────────────────────┴─────────────────┤
│  Input area                                                           │
│  - "Posting as: [PC selector]" if scene has 1 PC, just shows current │
│  - Text input                                                         │
│  - Submit                                                             │
│  - [ Advance ] button — appears when scene has 2+ present PCs        │
└───────────────────────────────────────────────────────────────────────┘
[Status bar: campaign | model | token budget | queue | drift alerts]
```

### Single-PC scene flow

1. User types as the current PC
2. Submit → post appended to scene file; if scene has 1 PC, LLM is called automatically
3. Response streams into the pane
4. Extractor proposes deltas (auto-applied or queued)

### Multi-PC scene flow (advance trigger)

When a scene has 2+ PCs present:

1. User types as PC A → submit → post appended to scene file, **no LLM call**
2. User switches to PC B (or stays), types → submit → another post appended
3. User clicks **Advance** → LLM is called with all pending PC inputs in context; response addresses them collectively
4. Response streams, Extractor proposes deltas

The Advance button is prominent in multi-PC scenes and disabled when there's nothing new to advance.

If a PC enters or leaves a scene mid-flow, the auto-respond / advance-required state updates in real time.

### PC switcher

A dropdown or palette in the top bar:

```
Active PCs:
  ◉ Aleksandr (vampire) — scene 47, Camden club, last played 12m ago
  ○ Beatrice (mage) — scene 49, Whitechapel chantry, last played 1h ago
```

Switching PCs:
- Shows that PC's current scene
- The Frontend remembers each PC's last position
- A PC without a current scene shows the campaign overview

### Source badges

Every entity reference in the scene pane is annotated with its source: 📚 wod-london (library), 🌿 emergent, ✏️ override. Clicking a badge reveals the source chain.

### Drift banner

When `drift_score` exceeds threshold for a present character, a banner appears: "Aleksandr's voice has been drifting — adding corrective context to next prompt." User can suppress for the session.

## Cast view (per-campaign)

Resolved view of all characters in this campaign — library refs + emergent + overrides.

- Grid by tier (lock-in / spotlight / background / archive) or by source
- Filters: source (library/emergent/override), role, tags, mechanics-defined capabilities
- Click → character detail:
  - Resolved card (library + override + state applied)
  - Source chain
  - Voice anchor with sample dialogue
  - Mechanical sheet (rendered via widget library — see below)
  - Capabilities tab (Disciplines, Spells, etc. from active mechanics module)
  - Relationships
  - Recent scenes
  - Edit options:
    - Edit override (writes campaign-local override file)
    - Edit library (writes library file, with dependents warning)
    - Promote to library (for emergent characters)

## World view (per-campaign)

Resolved items, locations, lore, factions, greetings for this campaign.

- Tabs: Items, Locations, Lore, Factions, Greetings
- Locations have hierarchy view; items have current-holder tracking; lore has keyword tags
- Each entity has source badge and edit options identical to Cast view

## Timeline view (per-campaign)

Scenes as cards along an in-game timeline:

- Color-coded by tag or mood
- Threads as lines from introduced-in to paid-off-in
- Faction state changes as bars
- Click → scene detail (the markdown file rendered)
- Search and filter

## Mechanics view (per-campaign)

For campaigns with a mechanics module selected:

- Active module info
- Sheets for all entities in the campaign that have sheets (characters, items, locations as applicable)
- Missing-sheets panel (entities that should have sheets under the active mechanics but don't — offer bulk create)
- Roll log
- Combat tracker (when combat is active, mechanics-defined)
- Content browser (mechanics-defined kinds: spells, magic items, etc.)
- Mechanics-defined custom panels (v2)

For `mechanics: null` campaigns: a placeholder explaining "no mechanics selected; install one to add rules."

## Composition view (per-campaign)

The campaign's composition, editable:

```
Worlds (priority order):
  1. wod-london          v7 [pinned] [include: all]            [▲][▼][⨯]
  2. wod-nyc             v3 [pinned] [include: locations,lore] [▲][▼][⨯]
  [+ Add world ref]

Mechanics: wod-mechanics       [change ▼]
Style Guide: gothic-horror     [change ▼]
Image Preset: oil-painting     [change ▼]

[Upgrade available]
  wod-london has new version 8. [Preview diff] [Upgrade] [Ignore]
```

Drag-and-drop to reorder priority. Click a ref to edit `include` filter or change to `track_latest`.

## Images view (per-campaign)

Gallery + queue + per-character prompt templates.

## Sheet widget library

Mechanics modules ship JSON Schema for each sheet kind; the Frontend renders forms using a built-in widget library. This is core Frontend infrastructure.

### Built-in widgets

| Widget | Schema annotation | Use |
|---|---|---|
| `text` | `"widget": "text"` | Single-line text |
| `textarea` | `"widget": "textarea"` | Multi-line text |
| `number` | `"widget": "number"` | Integer or float |
| `select` | `"widget": "select", "enum": [...]` | Pick one |
| `multi-select` | `"widget": "multi-select", "enum": [...]` | Pick many |
| `boolean` | `"widget": "boolean"` | Checkbox or toggle |
| `dot-rating` | `"widget": "dot-rating", "min": 1, "max": 5, "halves": false` | N dots (WoD attributes, abilities) |
| `dice-pool` | `"widget": "dice-pool", "currentField": "current", "maxField": "max"` | current / max with roll button |
| `health-track` | `"widget": "health-track", "rows": 7, "severity_levels": [...]` | Rows of damage with severity (WoD, D&D) |
| `power-list` | `"widget": "power-list", "items": {...}` | List of (name, rating, description, source) — Disciplines, Spells |
| `grid-rating` | `"widget": "grid-rating", "rows": [...], "cols": [...]` | Labeled rows × labeled cols (Ars Magica Arts) |
| `slot-list` | `"widget": "slot-list", "size": N` | Fixed-size inventory or spell slots |
| `keyword-list` | `"widget": "keyword-list"` | Tag-style chip input |
| `nested-section` | `"widget": "nested-section", "title": "..."` | Group fields into collapsible blocks |

8-12 widgets cover most TTRPG systems. A mechanics author writes JSON Schema and Python; no JavaScript required.

If a schema references a widget the Frontend doesn't have (forward-compat for a new widget added in a later version), the renderer falls back to a generic editor and surfaces a warning.

### Per-mechanics CSS theming

A mechanics module can ship `theme.css` for visual styling. The Frontend isolates per-mechanics CSS by:

- Wrapping each rendered sheet in a `<div class="mechanics-{module-id}">`
- All theme CSS selectors are scoped to `.mechanics-{module-id}` either by the module author (recommended) or by the Frontend's CSS post-processor (PostCSS plugin that prefixes selectors)

This prevents wod-mechanics styles from bleeding into another-campaign-mechanics sheets.

### v2 escape hatch: custom JS bundles

The mechanics manifest can declare custom UI bundles for specific sheet kinds. The Frontend dynamically imports them (peer-deps React shared with the host) and uses them instead of schema rendering for the declared kinds, with fallback if the bundle fails to load.

Out of v1 scope. The widget library is sufficient to ship.

## Backend contract

REST + WebSocket. Illustrative endpoints (full API defined separately):

### Library

```
GET    /library/worlds
POST   /library/worlds
GET    /library/worlds/{id}
PATCH  /library/worlds/{id}
DELETE /library/worlds/{id}
POST   /library/worlds/{id}/fork

GET    /library/worlds/{id}/{kind}                   # kind: characters, items, locations, lore, factions, greetings
POST   /library/worlds/{id}/{kind}
GET    /library/worlds/{id}/{kind}/{entity-id}
PATCH  /library/worlds/{id}/{kind}/{entity-id}
DELETE /library/worlds/{id}/{kind}/{entity-id}
GET    /library/worlds/{id}/{kind}/{entity-id}/dependents

GET    /library/variants/{kind}/{asset-id}             # cross-world lookup

GET    /library/style-guides
GET    /library/image-presets

GET    /mechanics/installed
GET    /plugins/installed
POST   /mechanics/rescan
POST   /plugins/rescan
```

### Campaigns

```
GET    /campaigns
POST   /campaigns
GET    /campaigns/{id}
PATCH  /campaigns/{id}
DELETE /campaigns/{id}

GET    /campaigns/{id}/composition
PUT    /campaigns/{id}/composition
POST   /campaigns/{id}/composition/refs
DELETE /campaigns/{id}/composition/refs/{world-id}
POST   /campaigns/{id}/composition/refs/{world-id}/upgrade

GET    /campaigns/{id}/pcs
POST   /campaigns/{id}/pcs
DELETE /campaigns/{id}/pcs/{character-ref}
POST   /campaigns/{id}/pcs/{character-ref}/set-active
POST   /campaigns/{id}/pcs/{character-ref}/set-current-scene

POST   /campaigns/{id}/turns                           # submit a post as a PC
POST   /campaigns/{id}/turns/advance                   # multi-PC advance trigger
POST   /campaigns/{id}/turns/regenerate
POST   /campaigns/{id}/turns/undo
POST   /campaigns/{id}/turns/{turn-id}/retcon
POST   /campaigns/{id}/forks

GET    /campaigns/{id}/scenes
GET    /campaigns/{id}/scenes/{scene-id}
POST   /campaigns/{id}/scenes/{scene-id}/end

GET    /campaigns/{id}/characters                       # resolved
GET    /campaigns/{id}/items
GET    /campaigns/{id}/locations
GET    /campaigns/{id}/lore
GET    /campaigns/{id}/factions

POST   /campaigns/{id}/{kind}/{entity-id}/promote-to-library

GET    /campaigns/{id}/sheets/{kind}/{entity-id}        # resolved sheet under active mechanics
PUT    /campaigns/{id}/sheets/{kind}/{entity-id}

GET    /campaigns/{id}/facts
POST   /campaigns/{id}/facts
GET    /campaigns/{id}/commitments

POST   /campaigns/{id}/time/advance

POST   /campaigns/{id}/images/generate
GET    /campaigns/{id}/images

POST   /campaigns/{id}/export

POST   /campaigns/{id}/reviews/{review-id}/approve
POST   /campaigns/{id}/reviews/{review-id}/reject
PATCH  /campaigns/{id}/reviews/{review-id}

WS     /campaigns/{id}/stream                          # push events
```

### WebSocket events

```json
{ "type": "token", "turn_id": "...", "delta": "..." }
{ "type": "turn_complete", "turn_id": "...", "result": {...} }
{ "type": "image_ready", "image_id": "...", "url": "..." }
{ "type": "drift_detected", "character_ref": "...", "score": 0.5 }
{ "type": "contradiction_detected", "report": {...} }
{ "type": "review_item_added", "item": {...} }
{ "type": "npc_tick_complete", "summary": {...} }
{ "type": "scene_started", "scene_id": "...", "scene": {...} }
{ "type": "scene_ended", "scene_id": "...", "summary": "..." }
{ "type": "library_file_changed", "library_id": "...", "kind": "modified" }
{ "type": "library_ref_upgraded", "campaign_id": "...", "world_id": "...", "from": 3, "to": 4 }
{ "type": "pc_post_appended", "scene_id": "...", "pc_ref": "...", "post_id": "..." }
{ "type": "advance_requested", "scene_id": "...", "turn_id": "..." }
{ "type": "advance_disabled", "scene_id": "...", "reason": "..." }
```

## Campaign creation flow

```
Step 1: Identity
  Name, description, tags

Step 2: Composition
  Pick worlds (multi-select with priority and include filters)
  Default flow: pick one world → include all entity kinds
  Advanced: multi-world crossover with per-ref include filters

Step 3: Mechanics
  Pick one installed mechanics module, or "No mechanics (narrative only)"
  If the picked module has no sheets for the active library cast, offer bulk-create

Step 4: PCs
  Pick a character with role=pc from the composed cast, or create one
  Multiple PCs supported; each gets owner: local in v1

Step 5: Style & content
  Pick a library style guide or write inline
  Pick a library image preset or skip
  Set content boundaries (inline text)

Step 6: Starting scene
  Pick a greeting (or skip and start blank)
  Confirm starting location, in-game time, present cast

[Create]
```

## Per-campaign settings

Tabbed:
- **General**: name, description, tags
- **Model routing**: which LLM / embedding provider handles each task
- **ImageGen**: backend choice, active preset, sampler defaults
- **Mechanics**: active module, module-specific options
- **Storage**: backup schedule for this campaign
- **Advanced**: per-task system prompts, debug log

## App-level settings

- Library location (path)
- LLM provider configs (shared across campaigns)
- Embedding provider configs
- ImageGen backend configs
- Mechanics module discovery: paths to scan, error log
- Plugin discovery: paths to scan, error log
- Backup policy
- Appearance: theme, font, density

## State management

A client-side store mirrors active campaign + library state:

- Optimistic UI for safe operations (edits, navigation)
- Pessimistic for consequential (deletes, upgrades)
- Rehydrates on connect / reconnect
- Cache strategy: scene cards, character cards, lore entries with TTL
- Library data cached separately and shared across campaign views

## Accessibility

- Keyboard navigation throughout (PC switcher, advance button, composition reorder)
- Screen-reader friendly markup
- High-contrast theme option
- Adjustable font size in prose pane
- Dyslexia-friendly font option

## Theming

- Light and dark themes
- Per-campaign accent color
- Per-mechanics CSS theme isolation (see widget library section)

## Performance budgets

- Initial load: < 2s to interactive
- Library load (100 assets): < 500ms
- Campaign switch: < 300ms (with library cached)
- Turn submission latency: < 200ms to first streaming token
- Scene jump: < 500ms to render
- Cast/World view (200 resolved entities): < 1s

## Tech stack (locked)

- TypeScript + React + Vite
- Tailwind or CSS modules for styling
- Headless accessibility primitives (Radix / Headless UI)
- Markdown rendering via remark or similar
- WebSocket for streaming events
- JSON Schema rendering for sheet forms
- PostCSS plugin for per-mechanics CSS scoping
- Tauri for desktop packaging (later)

## Open questions (deferred)

- **Single-page app vs. multi-page.** SPA fits the model; locked.
- **Offline support.** Desktop app should work offline (local models + library files). Cloud-model dependencies surface offline mode.
- **Library sharing.** Export/import of world bundles between users (zip a directory). File-based; UI helpers in v2.
- **Plugin UI extensions.** Mechanics-provided React components for sheets — v2; the architecture supports it.
- **Multi-campaign quick switcher.** Cmd-K palette for jumping. Nice-to-have.
- **Library activity feed.** Recent edits across library, affected campaigns. Useful for active multi-campaign users.
- **Custom widget types from mechanics.** A mechanics module declares its own widget — registered with the Frontend at load time. v2.
- **Per-campaign Frontend extensions.** Mechanics-specific panels (a Vampire chronicle wants a Prince-tracker; an Ars Magica saga wants a Tribunal panel). v2.

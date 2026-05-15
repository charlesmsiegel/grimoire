# 19 — Scene HUD

## Purpose

The Scene HUD is the always-visible "dashboard" rendered alongside the scene pane during play: in-game date, time, weather, location, present cast (with mood and intent), recent events, active commitments, drift alerts, and any mechanics-contributed widgets. It is the user's at-a-glance window on the current narrative state, and the fastest path to correcting state when the model has drifted.

The HUD owns no new state. It is a **pure projection** of state already managed by Time Engine, Setting, Scene Manager, Continuity, Characters, Transient State, and the active Mechanics module. Every widget reads from a canonical owner and writes (when edited) back to the canonical owner. The HUD is a *view*, not a store.

This pattern is inspired by SillyTavern RPG-companion's "Info Box Dashboard" and "Present Characters Panel," generalized to ride on top of Grimoire's existing modules rather than maintaining a parallel tracker.

## Why this is a spec and not a section in `14-frontend.md`

Three reasons:

1. **Cross-cutting backend contract.** The HUD needs every owning module (Time, Setting, Scene Manager, Continuity, Characters, Transient State, Mechanics) to expose a stable read interface and a stable write-through edit endpoint. That's a contract worth documenting in one place.
2. **Extensibility surface.** Mechanics modules can contribute widgets. The widget protocol (declaration, query, render, edit) needs definition.
3. **High user impact.** This is the single most visible piece of UI during play and the user's primary state-correction surface. A drift-fixing UX deserves its own spec.

`14-frontend.md` describes the campaign play view layout; this spec describes the HUD's data model, widget protocol, and edit semantics.

## Responsibilities

- Aggregate read queries against the canonical state owners and present them as a single widget set
- Subscribe to event-bus events and re-render affected widgets in real time
- Route every HUD edit through the canonical owner module (never write directly to SQLite or files)
- Persist per-campaign HUD layout (widget visibility, order, density) as user preference
- Render mechanics-contributed widgets via a declarative manifest contract
- Surface drift alerts, contradictions, and review-queue items inline where they belong (the present-cast widget shows drift; the events widget shows facts pending review)
- Provide keyboard navigation across widgets and edit shortcuts

## Non-responsibilities

- Does **not** own any state. Every value displayed has an owner module.
- Does **not** perform extraction (Extractor does), context assembly (Context Builder does), or scene transitions (Scene Manager does)
- Does **not** define mechanics widgets (mechanics modules ship their declarations)
- Does **not** decide what to surface from transient state (`20-transient-state.md` defines the surfacing rules)

## Widget protocol

A widget is a declared unit with four parts: identity, data, render, edit.

```python
@dataclass
class HudWidget:
    id: str                          # globally unique, e.g. "core.time" or "wod-mechanics.blood-pool"
    title: str                       # display name
    owner_module: str                # "time-engine" | "setting" | mechanics-id | ...
    read: WidgetRead                 # how to fetch the value
    edit: Optional[WidgetEdit]       # how the user mutates the value (None = read-only)
    render_hint: RenderHint          # compact | row | block | chip-list | ...
    refresh_on: list[EventName]      # event-bus events that trigger re-render
    visible_when: Optional[Predicate] # e.g. "scene.combat_active" for combat widgets
```

```python
@dataclass
class WidgetRead:
    endpoint: str                    # REST path returning the widget value
    poll_interval_seconds: Optional[int] = None  # fallback if event-driven not enough

@dataclass
class WidgetEdit:
    kind: EditKind                   # inline-text | picker | slider | enum | composite
    endpoint: str                    # REST path the edit submits to
    schema: Optional[JsonSchema]     # for composite edits
```

A widget's `read` endpoint returns a small payload — current value, formatted display string, optional badges. The Frontend renders by `render_hint`. The `edit` endpoint is the canonical write surface for that data (e.g. `POST /campaigns/{id}/time/set` for the time widget — owned by Time Engine, not the HUD).

## Built-in widget set

Shipped with Grimoire core:

| Widget id | Title | Owner | Render | Editable? |
|---|---|---|---|---|
| `core.in-game-date` | In-game date | Time Engine | row | yes |
| `core.in-game-time` | Time of day | Time Engine | row | yes |
| `core.weather` | Weather | Setting (atmosphere) | row | yes |
| `core.temperature` | Temperature | Setting | row | yes (units configurable) |
| `core.location` | Current location | Scene Manager | row (breadcrumb) | yes (picker) |
| `core.present-cast` | Present cast | Characters + Transient State | chip-list | yes (mood, intent inline) |
| `core.recent-events` | Recent events | Continuity (facts) | block (3-5 items) | no (read-only; click → fact view) |
| `core.active-commitments` | Open commitments | Continuity | block | no |
| `core.scene-summary` | Scene running summary | Scene Manager | block | yes (textarea) |
| `core.drift-alerts` | Drift alerts | Characters | banner | dismissible |
| `core.review-queue` | Items needing review | Extractor | count + popover | navigates to review |
| `core.active-threads` | Active threads | Continuity (foreshadowing) | block | no |

The user can hide, show, reorder, and (for some) collapse any widget.

Mechanics-contributed widgets join this list when the campaign has a mechanics module selected.

### Widget: `core.present-cast` (detail)

The richest built-in widget. Renders one chip per present character:

```
┌──────────────────────────────┐
│  winifred  ☹  guarded        │
│  → fastening her cloak       │
│  💭 "He knows about the     │
│     letters."                │
└──────────────────────────────┘
```

Contents per chip:
- Name + portrait thumbnail (if available)
- Mood emoji + short label (`transient.mood` per `20-transient-state.md`)
- Current action (`transient.current_action`)
- Optional internal thought bubble (`transient.internal_thought`, governed by per-campaign privacy rules)
- Relationship-to-active-PC indicator (small badge: 💚 ❤️ 🔥 🧊 — mapped from `transient.relationship_tone_toward_pc`)
- Source badge (📚 library / 🌿 emergent / ✏️ override) — same conventions as the scene pane
- Drift indicator (small 🌀 if the character's drift score is elevated)

Click a chip → character detail. Click the mood emoji → quick mood picker. Click the action text → inline edit. Click the thought bubble → toggle visibility.

### Widget render hints

Five render hints cover everything:

- `row` — single-line label + value (date, time, weather)
- `block` — multi-line list (recent events, commitments)
- `chip-list` — array of chips (present cast)
- `banner` — full-width alert (drift)
- `composite` — mechanics-defined custom layout (uses a generic JSON-driven layout grammar)

If a mechanics manifest declares an unknown render hint, the Frontend falls back to `block` with the raw value rendered as key-value pairs, and surfaces a console warning.

## Mechanics-contributed widgets

A mechanics module declares HUD widgets in its `manifest.yaml`:

```yaml
hud_widgets:
  - id: wod-mechanics.blood-pool
    title: "Blood Pool"
    scope: pc                       # rendered per-active-PC
    visible_when: "pc.has_sheet"
    render_hint: row
    read:
      endpoint: /mechanics/wod-mechanics/blood-pool
    edit:
      kind: composite
      endpoint: /mechanics/wod-mechanics/blood-pool
      schema_ref: schemas/blood-pool-edit.json
    refresh_on: [turn_complete, mechanics_event]

  - id: wod-mechanics.combat-tracker
    title: "Combat"
    scope: scene
    visible_when: "scene.combat_active"
    render_hint: composite
    read:
      endpoint: /mechanics/wod-mechanics/combat
    refresh_on: [mechanics_event, turn_complete]
```

The mechanics module implements the read endpoint by returning a typed payload; the edit endpoint follows the mechanics edit conventions. The Frontend uses the manifest to decide what to render where.

`scope` values:
- `campaign` — single instance (e.g., "Current chronicle phase")
- `scene` — single instance per active scene (e.g., "Combat tracker")
- `pc` — one per active PC (e.g., "Blood Pool" rendered once for each PC in scene)
- `present_npc` — one per present NPC (e.g., compact health-track)

`visible_when` is a tiny expression language: `scene.combat_active`, `pc.has_sheet`, `mechanics.has_event(kind='ongoing')`. Evaluation happens server-side as part of the HUD aggregation endpoint.

## Layout and persistence

### Layout model

```yaml
# data/campaigns/<id>/hud.yaml
density: compact                    # compact | comfortable
position: right                     # right | left | bottom (mobile)
widgets:
  - id: core.in-game-date
    visible: true
  - id: core.in-game-time
    visible: true
  - id: core.location
    visible: true
  - id: core.weather
    visible: false                  # user hid this
  - id: core.present-cast
    visible: true
    options:
      show_internal_thoughts: false  # per-campaign metagame setting
      relationship_indicators: true
  - id: wod-mechanics.blood-pool
    visible: true
groups:                              # optional collapsible groupings
  - title: "Time & Place"
    widgets: [core.in-game-date, core.in-game-time, core.location, core.weather]
  - title: "Cast"
    widgets: [core.present-cast]
```

This file is user preference, not state. It's safe to delete; the HUD falls back to defaults. It's per-campaign because users want different layouts for different play styles (combat-heavy vs intrigue-heavy).

### Default layout

On first turn of a new campaign, the HUD seeds from a default template:

```yaml
density: comfortable
position: right
widgets:
  - core.in-game-date
  - core.in-game-time
  - core.location
  - core.weather
  - core.present-cast
  - core.scene-summary
  - core.recent-events
  - core.active-commitments
  - core.drift-alerts
  - core.review-queue
```

Mechanics-contributed widgets are appended at the bottom on first activation; the user is free to reorder.

## Real-time updates

The HUD subscribes to the campaign WebSocket and re-renders affected widgets on event:

| Event | Widgets refreshed |
|---|---|
| `turn_complete` | all event-subscribed widgets |
| `time_advanced` | date, time |
| `scene_started`, `scene_ended` | location, present-cast, scene-summary |
| `deltas_extracted` | present-cast, recent-events, active-commitments |
| `drift_detected` | drift-alerts, present-cast (per-character indicator) |
| `review_item_added` | review-queue |
| `mechanics_event` | mechanics widgets (per widget's `refresh_on`) |
| `library_file_changed` | present-cast (if affected character's library file changes mid-play) |

Each widget tracks its last value; if the new value is identical, no re-render. For composite widgets, the diff is value-level (avoids reflow churn).

A widget that hasn't refreshed in N seconds despite expected events shows a soft "stale" indicator. The user can manually refresh from a small icon in each widget header.

## Edit semantics

Every editable widget routes its edit through the canonical owner. The HUD never writes to SQLite or files directly.

### Inline edits

Time, weather, location, scene-summary, transient mood/action are inline-editable: click → edit field → blur or Enter commits.

Commit path:
1. Frontend stages the edit, shows optimistic value
2. POST to the owner's edit endpoint
3. Owner module validates, applies, emits the appropriate event
4. HUD widget receives the event and reconciles with the optimistic value
5. On error, revert and surface the error inline

Optimistic-then-reconcile feels instantaneous and is safe because edits are scoped and reversible (logged as deltas like any other state change).

### Composite edits

For widgets with a JSON Schema-driven edit form (blood pool, combat tracker), clicking opens a small modal with the sheet widget library (`14-frontend.md`) rendering the schema. Commit submits the resulting JSON to the edit endpoint.

### Read-only widgets

Recent events, active-commitments, active-threads have no inline edit. Clicking navigates to the fact / commitment / thread detail view where the canonical CRUD operations live.

## Aggregation endpoint

To avoid N+1 widget reads on every turn, the HUD has a single aggregation endpoint:

```
GET /campaigns/{id}/hud
  → {
      "config_version": 12,
      "as_of": "2024-...",
      "widgets": {
        "core.in-game-date": {...},
        "core.in-game-time": {...},
        "core.present-cast": {...},
        ...
      }
    }
```

Backend implementation: a HUD service that fan-outs to owner modules in parallel (`asyncio.gather`), respects `visible_when` predicates, and assembles the result. Typical latency: < 100ms for a fully populated HUD.

On WebSocket events, the Frontend can either re-fetch only the affected widget(s) (`GET /campaigns/{id}/hud/widgets/{widget-id}`) or re-fetch the aggregate. The single-widget path is preferred when the event-to-widget mapping is unambiguous.

## Privacy and metagame settings

Some HUD widgets surface information a player might prefer to hide from themselves for immersion. Each widget can declare a per-campaign privacy flag:

```yaml
widgets:
  - id: core.present-cast
    options:
      show_internal_thoughts: false      # don't render NPC internal thoughts
      show_drift_indicators: false       # immersion-first
      relationship_indicators: true
  - id: core.recent-events
    options:
      include_hidden_facts: false        # facts marked secret are not surfaced
```

Defaults favor information ("solo play" mode); a stricter "GM mystery" profile is offered as a one-click preset. The privacy filter is applied at the aggregation layer, not in the widget itself, so a screenshot or audit log never leaks what was hidden.

Per-character privacy is governed by `20-transient-state.md` (some characters are flagged "thoughts not surfaced").

## Backend contract

REST surface:

```
GET    /campaigns/{id}/hud                          # aggregate
GET    /campaigns/{id}/hud/widgets/{widget-id}      # single widget refresh
GET    /campaigns/{id}/hud/config
PUT    /campaigns/{id}/hud/config
POST   /campaigns/{id}/hud/config/reset             # back to defaults

# All edits route through owner module endpoints, e.g.:
POST   /campaigns/{id}/time/set
POST   /campaigns/{id}/scenes/{scene-id}/location
POST   /campaigns/{id}/characters/{character-id}/transient
POST   /mechanics/{module-id}/{widget-id}           # mechanics widget edits
```

WebSocket events: covered by the existing event surface in `14-frontend.md` (`turn_complete`, `time_advanced`, `drift_detected`, `mechanics_event`, `deltas_extracted`).

## Frontend integration

The HUD is a sibling pane to the scene pane in the campaign play view (`14-frontend.md`'s ASCII diagram). On desktop, it's a fixed right (or left) sidebar; on smaller screens, it collapses to a top strip + bottom drawer.

Keyboard:
- `?h` — focus first widget
- `?j` / `?k` — next / prev widget
- `Enter` on a widget header — expand / collapse
- `e` on a focused editable widget — open inline edit
- `r` — refresh current widget

## Performance budgets

- HUD aggregate fetch: < 100ms p50, < 300ms p95
- Single-widget refresh: < 50ms p50
- Initial HUD render on campaign open: < 200ms (after the cached library is hot)
- Re-render on event: < 50ms from event arrival to paint

## Failure modes

- **Widget fails to fetch** — render a small error chip with retry; do not block other widgets
- **Mechanics widget references missing capability** — render a "not available" placeholder
- **Privacy filter blocks all values** — render an empty state with hint "hidden by privacy settings"
- **HUD config file is corrupt** — fall back to defaults, surface a one-line warning

## Open questions

- **Drag-to-reorder vs settings-only?** Drag is nicer; settings-only is simpler. Probably both: settings page for the bulk action, drag for incremental.
- **Per-PC HUD instances when scenes are split?** Aleksandr's scene and Beatrice's scene can have different active mechanics widgets. The PC switcher in `14-frontend.md` already swaps the scene pane; the HUD should swap with it. Likely shared config across PCs; per-PC computed widget instances.
- **Embedded HUD in the export?** EPUB exports could ship a per-scene HUD snapshot as an aside ("on this evening, at the Camden pub, present: ..."). Cute, probably v2.
- **HUD presets shipped with mechanics modules?** A mechanics module could declare a "recommended HUD layout" (e.g., wod-mechanics suggests Blood Pool front and center). Worth a `recommended_hud` block in the manifest; applied as a one-click suggestion at install/activate time, not silently.
- **Mobile HUD.** Out of scope for v1; the layout grammar should support it without rework when mobile is added.
- **HUD-only mode for non-LLM sessions?** Useful for face-to-face play where the LLM is dormant but the HUD tracks state. A toggle worth adding once the core is solid.

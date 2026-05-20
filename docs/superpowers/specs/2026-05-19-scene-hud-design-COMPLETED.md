## Scene HUD — Design

> **Status:** COMPLETED (2026-05-19). Backend: manifest extension, expression parser, HudService aggregator with default owner-fetchers, hud.yaml config, REST routes, WS event forwarding. Frontend: SideHud column wired into PlayView with row/block/chip-list/banner/composite render hints, useHud aggregate hook, PresentCastChip with inline pinned-extra rendering.

**Source idea:** `specs/new/scene-hud.md`
**Module:** `backend/src/grimoire/hud/` (new), `frontend/src/routes/campaign/SideHud/` (new)

## Purpose

An always-visible dashboard alongside the scene pane: in-game date / time / weather / location, present cast with mood + intent + thought, recent events, active commitments, drift alerts, mechanics widgets. The HUD **owns no state** — every widget reads from a canonical owner module; edits route through that owner's existing endpoints. The HUD is a thin aggregator + frontend layout.

## Widget protocol

```python
@dataclass(frozen=True)
class HudWidget:
    id: str                                    # "core.in-game-date" | "<module-id>.<widget-id>"
    title: str
    owner_module: str                          # for routing edit dispatch
    read: WidgetRead                           # endpoint + optional poll_interval_s
    edit: Optional[WidgetEdit]                 # endpoint + kind
    render_hint: RenderHint                    # row | block | chip-list | banner | composite
    refresh_on: list[EventName]
    visible_when: Optional[str]                # expression; see "expression language"
    scope: WidgetScope                         # campaign | scene | pc | present_npc
```

`RenderHint` enum: `ROW`, `BLOCK`, `CHIP_LIST`, `BANNER`, `COMPOSITE`. Unknown hints from a mechanics module log a console warning and fall back to `BLOCK`.

`WidgetRead` shape: `{ endpoint: str, poll_interval_s: int | None }`. The endpoint is a REST path **relative to the campaign root** (e.g., `/time/current`, `/mechanics/wod/blood-pool`). The HUD aggregator concatenates `/campaigns/{id}` and dispatches in parallel.

`WidgetEdit` shape: `{ kind: Literal["inline-text" | "picker" | "slider" | "enum" | "composite"], endpoint: str, schema_ref: str | None }`.

## Expression language for `visible_when`

A small, evaluated server-side, deliberately tiny grammar. Per the open question (eval safety): a hand-rolled parser + closed evaluator with no Python `eval`. Grammar:

```
expr     ::= atom ( ( "and" | "or" ) atom )*
atom     ::= "not"? primary
primary  ::= bool_literal | call | path_var
call     ::= path_var "(" arg ("," arg)* ")"
path_var ::= ident ("." ident)*
arg      ::= string_literal | int_literal | bool_literal
```

Allowed `path_var`s (the closed namespace):
- `scene.combat_active`, `scene.has_present(role="pc"|"npc")`
- `pc.has_sheet`, `pc.is_active`, `pc.in_scene`
- `mechanics.has_event(kind=str)`, `mechanics.is_active`
- `present_npc.has_sheet`, `present_npc.count`

Adding a new variable / call requires explicit extension in `grimoire/hud/expression.py`. Modules cannot smuggle Python in. Evaluator returns `False` on any error (missing var, bad type) and logs once.

## Built-in widget set (core)

| id | Owner module | Endpoint | Render | Editable | Refresh on |
|---|---|---|---|---|---|
| `core.in-game-date` | time_engine | `/time/date` | ROW | yes (picker) | `time_advanced` |
| `core.in-game-time` | time_engine | `/time/time-of-day` | ROW | yes (picker) | `time_advanced` |
| `core.weather` | world | `/scenes/{sid}/weather` | ROW | yes (enum) | `scene_started`, `time_advanced`, `weather_changed` |
| `core.temperature` | world | `/scenes/{sid}/temperature` | ROW | yes (slider) | `scene_started`, `time_advanced` |
| `core.location` | scene_manager | `/scenes/{sid}/location` | ROW | yes (picker) | `scene_started`, `scene_ended` |
| `core.present-cast` | characters + transient_state | `/scenes/{sid}/present-cast` | CHIP_LIST | per-chip | `turn_complete`, `deltas_extracted`, `drift_detected`, `library_file_changed` |
| `core.recent-events` | continuity | `/scenes/{sid}/recent-facts?limit=5` | BLOCK | no | `deltas_extracted` |
| `core.active-commitments` | continuity | `/campaigns/{id}/commitments?status=active` | BLOCK | no | `deltas_extracted`, `commitment_created`, `commitment_paid_off` |
| `core.scene-summary` | scene_manager | `/scenes/{sid}/summary` | BLOCK | yes (textarea) | `scene_started`, `turn_complete` |
| `core.drift-alerts` | characters | `/campaigns/{id}/drift-alerts?active=true` | BANNER | dismissible | `drift_detected` |
| `core.review-queue` | extractor | `/campaigns/{id}/review-queue?count=true` | COMPOSITE | no (navigates) | `review_item_added` |
| `core.active-threads` | continuity | `/scenes/{sid}/threads?status=open` | BLOCK | no | `deltas_extracted`, `thread_opened`, `thread_closed` |

**Weather and temperature** require a small new surface — `world/weather.py` already procedurally generates weather (research found this); we add a `/scenes/{sid}/weather` read + `PUT` route to expose it. Temperature is not implemented yet; it's a follow-up tracked in `world-remaining` (the HUD widget can be marked `visible_when="false"` until temperature lands).

## `core.present-cast` chip

Per chip (one per present character):
- portrait (avatar URL), name
- mood emoji + label (from `transient_state.character.mood`, privacy-filtered)
- current action (from `transient_state.character.current_action`)
- optional internal-thought bubble (from `transient_state.character.internal_thought`, privacy-gated by `surface_in_hud`)
- relationship-to-active-PC badge (from `characters.get_relationships`)
- source badge (📚 library / 🌿 emergent / ✏️ override)
- drift indicator (red dot + tooltip) when drift > threshold

Per-chip edits route through the canonical owner endpoints (transient-state's PATCH for mood/action; never through the HUD):

```
PATCH /campaigns/{id}/entities/character/{char_id}/transient/mood
PATCH /campaigns/{id}/entities/character/{char_id}/transient/current_action
```

Optimistic-then-reconcile: the chip updates locally on user edit, then re-reads from the server response and reverts on error.

Pinned narrative-extras chips appear inline (`📌 scar across throat`) — that wiring is owned by `narrative-extras-design.md`; the HUD widget reads from `/scenes/{sid}/present-cast` which includes the pinned-extras payload.

## Mechanics-contributed widgets

A mechanics module declares widgets in its `manifest.yaml`:

```yaml
hud_widgets:
  - id: wod-mechanics.blood-pool
    title: Blood Pool
    scope: pc                              # campaign | scene | pc | present_npc
    visible_when: "pc.has_sheet"
    render_hint: row
    read:
      endpoint: /mechanics/wod-mechanics/blood-pool
      poll_interval_s: null
    edit:
      kind: composite
      endpoint: /mechanics/wod-mechanics/blood-pool
      schema_ref: schemas/blood-pool-edit.json
    refresh_on: [turn_complete, mechanics_event]
```

`ModuleManifest` (`backend/src/grimoire/types/mechanics.py:131–145`) gains `hud_widgets: list[HudWidget] = Field(default_factory=list)`. Validator in `backend/src/grimoire/validation/manifests.py` enforces:
- `id` matches `^<module-id>\.[a-z0-9_-]+$` (must be prefixed with the declaring module's id).
- `visible_when` parses against the grammar (lazy: parse on load, fail manifest validation on error).
- `render_hint` is in the enum.

**ID collision policy:** Mechanics widget ids must be prefixed by the module id. `core.*` is reserved. A module attempting to ship `core.present-cast` is rejected at load with `ManifestError`. Different modules cannot collide because their prefixes differ; same-module duplicate ids fail manifest validation.

## Aggregation endpoint

```
GET /campaigns/{id}/hud
```

Returns:
```json
{
  "campaign_id": "...",
  "scene_id": "...",
  "generated_at": "2026-05-19T14:00:00Z",
  "widgets": [
    {
      "id": "core.in-game-date",
      "status": "ok",                       // ok | error | timeout | hidden
      "data": { "date": "1894-10-13", "...": ... },
      "stale": false
    },
    {
      "id": "wod-mechanics.blood-pool",
      "status": "error",
      "error": "owner endpoint returned 500",
      "data": null
    }
  ]
}
```

**Fan-out** to widget owner endpoints happens in parallel via `asyncio.gather(...)`. Per-widget timeout: 1 second (configurable). On timeout, the widget reports `status="timeout"` and the aggregator keeps going. Target latency p50 < 100 ms with all widgets responding; p95 < 500 ms in the timeout case.

`visible_when` is evaluated **before** fan-out; hidden widgets are skipped entirely. The response includes only widgets where `visible_when` resolved true (the frontend doesn't need to know about widgets it can't see this turn).

Single-widget refresh: `GET /campaigns/{id}/hud/widgets/{widget_id}` — < 50 ms p50.

## Config persistence

Per-campaign `data/campaigns/<id>/hud.yaml`:

```yaml
density: comfortable                      # compact | comfortable
position: right                           # right | bottom
ordered_widgets:
  - id: core.in-game-date
    visible: true
  - id: core.in-game-time
    visible: true
  - id: core.weather
    visible: true
  - id: core.location
    visible: true
  - id: core.present-cast
    visible: true
    options:
      show_internal_thoughts: true
      show_drift_indicators: true
  - id: wod-mechanics.blood-pool
    visible: true
groups:
  - title: World
    widgets: [core.in-game-date, core.in-game-time, core.weather, core.location]
  - title: Scene
    widgets: [core.present-cast, core.scene-summary, core.recent-events]
```

Defaults (when file is absent or deleted):
- All `core.*` widgets enabled, in the order shown in the table above.
- Mechanics widgets appended at the bottom on first detection (cached: when the campaign's mechanics module changes, the new module's widgets are appended; removed-module widgets stay in config but are auto-hidden by `visible_when="false"` returned for unknown modules).
- Density: comfortable.

`GET /campaigns/{id}/hud/config`, `PUT .../hud/config`, `POST .../hud/config/reset`.

## Edit dispatch + widget refresh cycle

Per the open question: **hybrid**.

1. User edits the in-game date on the date widget → frontend optimistically updates the UI.
2. PATCH dispatch goes to the canonical owner (`/time/date`).
3. Owner returns the new value; frontend reconciles.
4. Owner emits `time_advanced` on the event bus.
5. The aggregator's WS bridge forwards `time_advanced` to the frontend HUD.
6. HUD refreshes any widget with `time_advanced` in its `refresh_on`. The date widget reconciles a second time; idempotent so no flash.

This handles two cases: the user who edited sees the optimistic update + reconcile; other users in the same campaign (multi-tab, future multi-user) get the event-driven update.

If the owner endpoint errors, the frontend rolls back the optimistic change and surfaces the error.

## WebSocket integration

The existing event bus (`backend/src/grimoire/event_bus.py`) is the source of truth. The HUD stream bridge subscribes once at campaign-channel open and forwards relevant events:

```
hud-relevant events: turn_complete, time_advanced, scene_started, scene_ended,
                     deltas_extracted, drift_detected, review_item_added,
                     mechanics_event, library_file_changed,
                     thread_opened, thread_closed, weather_changed,
                     commitment_created, commitment_paid_off
```

Stale indicator: if a widget has `refresh_on` events that should fire in normal operation, and N seconds (default 60s, configurable per widget) pass with no refresh trigger, the frontend shows a soft "stale" badge with a manual refresh button. The threshold is per-widget config in `hud.yaml`.

Identical-value re-renders are skipped client-side via shallow JSON compare.

## Privacy filter at aggregation

Per Theme B, privacy lives in transient-state. The HUD aggregator passes `for_observer=<resolved>` when reading transient state:

- The active PC's owner sees their own internal thoughts unconditionally.
- Other PCs in the campaign see filtered output per `Character.privacy.internal_thoughts.surface_in_hud`.
- POV mode: NPC internal thoughts hidden.

The aggregator computes `observer = ObserverKind.pc_owner` if request comes from the active-PC's session; defaults to `audience` for read-only viewers. Filter is applied at the data layer (in `TransientState.get*`), so screenshots and audit logs of `GET /hud` already reflect the filter.

Per-widget options like `show_internal_thoughts: false` in `hud.yaml` override the privacy default to `audience` for that widget — lets a player playing in solo mode but recording video hide their own thoughts.

## REST surface

```
GET    /campaigns/{id}/hud                              # aggregate
GET    /campaigns/{id}/hud/widgets/{widget_id}          # single refresh
GET    /campaigns/{id}/hud/config
PUT    /campaigns/{id}/hud/config
POST   /campaigns/{id}/hud/config/reset
GET    /campaigns/{id}/hud/widgets/available            # for the config editor
```

`GET /widgets/available` returns the union of `core.*` plus mechanics-declared widgets, with metadata for the config UI to show toggles.

## Frontend

`frontend/src/routes/campaign/SideHud/` (new):
- `SideHud.tsx` — top-level layout (column to the right of the scene pane, or bottom on narrow viewports per `position` config).
- `widgets/` — one component per render hint (`RowWidget`, `BlockWidget`, `ChipListWidget`, `BannerWidget`, `CompositeWidget`).
- `PresentCastChip.tsx` — the rich chip for present-cast entries (the most complex one).
- `useHud.ts` — store hook backed by the WS event bridge + the aggregate endpoint.

Layout: scene pane shrinks from full-width to `flex: 1`; HUD column is `flex: 0 0 360px`. On narrow viewports (< 1100px), HUD slides under the scene pane as collapsible sections.

Keyboard: `?h` focus HUD, `?j`/`?k` next/prev widget, Enter expand/collapse, `e` open inline edit, `r` refresh widget.

## Configuration

```yaml
hud:
  aggregate_timeout_seconds_per_widget: 1.0
  default_stale_threshold_seconds: 60
  enable_mechanics_widgets_by_default: true
  log_unknown_render_hints: true
```

## Performance targets

- `GET /hud` aggregate: < 100 ms p50, < 500 ms p95 (with worst-case timeouts).
- Single-widget refresh: < 50 ms p50.
- WS event → widget re-render: < 200 ms end-to-end (event bus + WS + frontend reconcile).

## Failure handling

| Failure | Behavior |
|---|---|
| Owner endpoint times out | Widget shows `status=timeout`; aggregator continues |
| Owner endpoint 500 | Widget shows `status=error` with one-line message; "Retry" affordance in UI |
| Manifest expression invalid | Manifest validation fails; the module fails to load; no partial state |
| Unknown event in `refresh_on` | Manifest validation fails (closed enum of event names) |
| Unknown render hint | Log + fall back to `BLOCK`; no manifest failure (forward-compat) |
| WS dropped | Frontend falls back to polling each visible widget at its `poll_interval_s` (default 30s if widget doesn't specify) |
| HUD config corruption | Treat as missing; fall back to defaults; surface a one-line warning in the UI; original file preserved as `hud.yaml.broken-<timestamp>` |

## Test wiring

`backend/tests/hud/test_aggregator.py` (new):
- Fan-out parallelism (mock 5 owners, assert total time ≈ slowest, not sum).
- Per-widget timeout in isolation.
- `visible_when` evaluation truth table.
- Privacy filter pass-through with different observers.

`backend/tests/hud/test_expression.py`:
- Grammar parse positive + negative cases.
- Eval against a fixture `EvaluationContext`.
- Unknown var → False + one log.

`backend/tests/validation/test_manifest_hud_widgets.py`:
- Reject `core.*` ids from non-core modules.
- Reject duplicate ids within a module.
- Validate `visible_when` at load time.

Frontend `__tests__/SideHud.test.tsx`:
- Aggregate render flow with mocked endpoint.
- WS event triggers correct widget re-render.
- Stale indicator after N seconds.

## Wiring touchpoints

- `backend/src/grimoire/hud/service.py` (new): aggregator service.
- `backend/src/grimoire/hud/expression.py` (new): parser + evaluator.
- `backend/src/grimoire/hud/widgets.py` (new): the core widget table + scope predicates.
- `backend/src/grimoire/hud/config.py` (new): hud.yaml read/write.
- `backend/src/grimoire/api/hud.py` (new): REST routes.
- `backend/src/grimoire/api/stream.py`: extend `_FORWARDED_EVENTS` with HUD-relevant types (most already there).
- `backend/src/grimoire/types/mechanics.py:ModuleManifest`: add `hud_widgets` field.
- `backend/src/grimoire/validation/manifests.py`: validate `hud_widgets`.
- `backend/src/grimoire/world/service.py`: add `/scenes/{sid}/weather` GET/PUT endpoints (small surface to round out the widget; weather generation is already implemented).
- `frontend/src/routes/campaign/SideHud/*` (new).
- `frontend/src/api/hud.ts` (new): client.
- Layout adjustment in `frontend/src/routes/campaign/ScenePane.tsx` to make room for the HUD column.

## Out of scope (v1)

- Multi-column HUD layout (single-column with optional groups only).
- Drag-and-drop widget reordering (config edit only via the form).
- Per-PC HUD profiles beyond the active-PC heuristic (config is per-campaign).
- Temperature widget data (lights up when world's temperature surface ships; widget hidden until then).
- Side-by-side diff between turns in the HUD (lives in observability tools).

# Scene HUD

Always-visible dashboard alongside the scene pane: in-game date / time /
weather / location, present cast with mood and intent, recent events,
active commitments, drift alerts, mechanics widgets. The HUD owns no
state — every widget reads from a canonical owner module and writes (when
edited) back through that owner.

Depends on `transient-state.md` for the present-cast widget content.

## Widget protocol

```python
@dataclass
class HudWidget:
    id: str                      # "core.time" or "wod-mechanics.blood-pool"
    title: str
    owner_module: str
    read: WidgetRead             # endpoint, optional poll_interval
    edit: Optional[WidgetEdit]   # endpoint + kind (inline-text | picker | slider | enum | composite)
    render_hint: RenderHint      # row | block | chip-list | banner | composite
    refresh_on: list[EventName]
    visible_when: Optional[Predicate]
```

Render hints fall back to `block` (key-value pairs) with a console warning
if a manifest declares an unknown hint.

## Built-in widget set

| id | Owner | Render | Editable |
|---|---|---|---|
| `core.in-game-date` | Time Engine | row | yes |
| `core.in-game-time` | Time Engine | row | yes |
| `core.weather` | Setting | row | yes |
| `core.temperature` | Setting | row | yes |
| `core.location` | Scene Manager | row | yes (picker) |
| `core.present-cast` | Characters + Transient State | chip-list | yes (mood/intent inline) |
| `core.recent-events` | Continuity | block | no (click → fact view) |
| `core.active-commitments` | Continuity | block | no |
| `core.scene-summary` | Scene Manager | block | yes (textarea) |
| `core.drift-alerts` | Characters | banner | dismissible |
| `core.review-queue` | Extractor | count + popover | navigates to review |
| `core.active-threads` | Continuity | block | no |

`core.present-cast` chip per present character: portrait + name, mood
emoji + label, current action, optional internal-thought bubble,
relationship-to-active-PC badge, source badge (📚/🌿/✏️), drift indicator.
Click mood → quick picker; click action → inline edit; click bubble →
toggle visibility.

## Mechanics-contributed widgets

A mechanics module declares widgets in its `manifest.yaml`:

```yaml
hud_widgets:
  - id: wod-mechanics.blood-pool
    scope: pc                    # campaign | scene | pc | present_npc
    visible_when: "pc.has_sheet"
    render_hint: row
    read:  { endpoint: /mechanics/wod-mechanics/blood-pool }
    edit:  { kind: composite, endpoint: ..., schema_ref: schemas/blood-pool-edit.json }
    refresh_on: [turn_complete, mechanics_event]
```

`visible_when` is a small expression language: `scene.combat_active`,
`pc.has_sheet`, `mechanics.has_event(kind='ongoing')`. Evaluated server-side
in the aggregation endpoint.

## Endpoints

- `GET /campaigns/{id}/hud` — aggregate (parallel fan-out to owners, < 100ms p50).
- `GET /campaigns/{id}/hud/widgets/{widget-id}` — single-widget refresh
  (< 50ms p50).
- `GET / PUT /campaigns/{id}/hud/config`; `POST .../config/reset`.

Edits route through canonical owner endpoints (e.g.
`POST /campaigns/{id}/time/set`, never through HUD endpoints).
Optimistic-then-reconcile.

## Layout and persistence

Per-campaign `data/campaigns/<id>/hud.yaml` carries density, position,
ordered widgets with visibility flags + per-widget options, optional
collapsible groups. Safe to delete; HUD falls back to defaults. Mechanics
widgets are appended at the bottom on first activation; user can reorder.

## Real-time updates

HUD subscribes to the campaign WebSocket. Event → widget map:

- `turn_complete` — all event-subscribed widgets
- `time_advanced` — date, time
- `scene_started` / `scene_ended` — location, present-cast, scene-summary
- `deltas_extracted` — present-cast, recent-events, active-commitments
- `drift_detected` — drift-alerts, present-cast indicators
- `review_item_added` — review-queue
- `mechanics_event` — per widget's `refresh_on`
- `library_file_changed` — present-cast (if a present character's library
  file changed)

Identical values skip re-render. A widget without expected refresh for N
seconds shows a soft "stale" indicator with manual refresh.

## Privacy and metagame

Per-widget per-campaign options for things the player may want hidden from
themselves: `show_internal_thoughts`, `show_drift_indicators`,
`include_hidden_facts`. Filter applied at the aggregation layer so
screenshots / audit logs never leak hidden values. Per-character privacy
governed by `transient-state.md`.

## Keyboard

`?h` focus, `?j`/`?k` next/prev widget, Enter expand/collapse, `e` open
inline edit, `r` refresh.

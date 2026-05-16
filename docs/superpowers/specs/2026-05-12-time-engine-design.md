# Time Engine — Design (Shipped)

> Captures the Time Engine design as actually built. The matching "remaining" spec at `2026-05-16-time-engine-remaining-design.md` covers everything from the original `specs/07-time-engine.md` that did **not** land in this work.

**Commit:** `70f1f5f` — "Build Time Engine (task 21)" (follow-ups: `bf87f9a` review fixes, `87e0643` world rename, `02571e4` ruff)
**Module:** `backend/src/grimoire/time_engine/`
**Tests:** `backend/tests/time_engine/test_service.py`

## Purpose

The Time Engine is the per-campaign in-game clock plus the advancement coordinator that runs when a scene narrates time passing, the player asks for an explicit skip, or a long-form activity completes. It does **not** run on its own; nothing happens while the app is idle. Each campaign owns its own clock + ticks (library characters living in two campaigns experience two timelines).

## Module surface

`TimeEngineService` (`time_engine/service.py:182`) is constructed with concrete collaborators plus two injectable async callables so tests can wire a deterministic stub and production can hand in a Gateway-backed adapter:

- `store: StateStore` — owns the `calendar`, `scheduled_events`, `faction_state`, `posts` tables it reads/writes
- `world: WorldService` — calendar lookup, weather queries
- `characters: CharactersService` — `list_for_campaign` for the significance filter
- `mechanics: MechanicsService` — per-character `time_tick` fan-out (no-op when the campaign has `mechanics: null`)
- `continuity: Continuity` — `age(...)` to roll forward commitments and `open_commitments(...)` for the significance filter
- `config: TimeEngineConfig | None`
- `event_bus: EventBus | None` — when present, emits `time_advance` + per-NPC `npc_tick_complete`
- `npc_tick_fn: NpcTickFn | None` — async `(payload) -> payload`; default is `_default_npc_tick` returning a well-formed empty summary
- `digest_fn: DigestFn | None` — async `(payload) -> str`; default returns `""` (structured-only digest)

## Public API

```python
class TimeEngineService:
    # Clock
    async def current(campaign_id, *, branch_id=None) -> InGameTime | None
    async def set_current(campaign_id, when, *, branch_id=None) -> None
    async def calendar(campaign_id) -> WorldCalendar

    # Scheduled events
    async def schedule_event(event, *, branch_id=None) -> EventId
    async def cancel_event(event_id) -> None
    async def upcoming_events(campaign_id, within=None, *, branch_id=None) -> list[ScheduledEvent]

    # Advancement
    async def advance(campaign_id, duration, reason, *,
                      scene_id=None, branch_id=None, from_time=None) -> TimeAdvanceResult
    async def skip_to(campaign_id, target, reason, *,
                      scene_id=None, branch_id=None, from_time=None) -> TimeAdvanceResult
```

`TimeAdvanceResult` (`types/time.py:64`) carries: `from_time`, `to_time`, `duration`, `npc_summaries`, `faction_summaries`, `scheduled_events_triggered`, `weather_changes`, `commitments_due`, `commitments_overdue`, `mechanics_deltas`, `digest`.

`TimeAdvanceReason` (`types/time.py:18`) enumerates `EXPLICIT_USER`, `SCENE_NARRATION`, `SCENE_BREAK`, `ACTIVITY_DURATION`, `SCHEDULED_EVENT`.

## Clock storage

Per-branch row in `calendar` (migration `005_continuity.sql:94`):

```sql
CREATE TABLE calendar (
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  current_in_game_time TEXT,         -- ISO-8601 datetime
  PRIMARY KEY (branch_id)
);
```

`set_current` upserts; `current` returns `None` when the row is missing or the column is empty. `advance`/`skip_to` both raise `TimeNotSetError` if no anchor is present and the caller didn't pass `from_time` (`service.py:370`, `service.py:402`).

`skip_to` raises `InvalidSkipError` when the target is not strictly later than the anchor (`service.py:407`).

## Scheduled events

Per-branch rows in `scheduled_events` (migration `010_scheduled_events.sql`):

```sql
CREATE TABLE scheduled_events (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,                -- 'holiday' | 'recurring' | 'one_off' | 'plot_beat'
  label TEXT NOT NULL,
  payload TEXT,                      -- JSON blob
  triggered INTEGER NOT NULL DEFAULT 0,
  triggered_at TEXT,
  created_at TEXT NOT NULL
);
```

`schedule_event` upserts by id (`""` id ⇒ a new `evt_<uuid12>` is minted, `service.py:274`). `cancel_event` deletes. `upcoming_events` returns non-triggered events in `[current_time, current_time + within]` ordered by `at`, or every future non-triggered event when `within=None`. Branch isolation is enforced by every query keying on `(campaign_id, branch_id)` — see `test_schedule_event_honours_branch_id` for the contract.

## Advancement pipeline (`_run_pipeline`, `service.py:428`)

Order matters and is documented inline at `service.py:439`:

1. **Fire scheduled events** in `(from_time, to_time]` — flips `triggered=1` per row and returns the triggered `ScheduledEvent` list (`_fire_scheduled_events`, `service.py:558`)
2. **Pick significant NPCs** (`_significant_npcs`, `service.py:588`) — see "Significance filter" below
3. **Run NPC ticks** in parallel, capped by `npc_tick_parallelism` (`_run_npc_ticks`, `service.py:645`)
4. **Run faction ticks** if the duration is at least `faction_tick_resolution` (`_run_faction_ticks`, `service.py:690`)
5. **Compute weather changes** at every distinct location among ticked NPCs (`_weather_changes`, `service.py:746`)
6. **Run mechanics `time_tick`** for each ticked NPC (`_run_mechanics_ticks`, `service.py:785`)
7. **Age commitments** through `Continuity.age(to_time)` — returns `became_overdue` + `became_stale`
8. **Persist** the new clock value via `set_current`
9. **Build digest** — structured always; narrative only when `config.digest_narrative=True`
10. **Emit** a single `time_advance` event with the campaign/branch/reason/window/npcs/event ids

## Significance filter

Caps how many NPCs run an LLM tick per advance. Implements three of the spec's rules (`_significant_npcs`, `service.py:588`):

- **Role-based always-tick**: an NPC whose `role.value.lower()` matches any entry in `SignificanceConfig.always_tick_roles` (default `("pc", "major_npc")` — though PCs are excluded later)
- **Open commitments**: NPCs that are the `from_id` or `to_id` of any open commitment from `continuity.open_commitments(...)`
- **Recent post authors**: NPCs whose `character_ref` appears in `author_pc_ref` of the most recent `recent_post_window` posts (default 20)

Then PCs are dropped (`if ent.is_pc: continue`) and the result is sorted by `(role, asset_id)` and truncated to `max_npcs_per_advance` (default 15). The `tick_in_household` config field is defined but not yet consumed.

## NPC tick

Per NPC, `_run_npc_ticks` calls the injected `npc_tick_fn` with a `NpcTickInput` payload:

```python
{
    "campaign_id": ..., "character_ref": ..., "character_id": ...,
    "role": ..., "duration_iso": ..., "from": ..., "to": ...,
    "location_ref": ...,
}
```

The callable returns a `NpcTickPayload` dict; `_npc_summary_from_payload` (`service.py:896`) projects it into an `NpcTickSummary` (`types/time.py:43`) and emits `npc_tick_complete` per NPC. Defensive: any exception from the callable falls through to `_default_npc_tick` (well-formed empty payload) so the pipeline still completes.

Parallelism is bounded by `asyncio.Semaphore(npc_tick_parallelism)`; there is no "shared events pre-pass" — each NPC tick runs independently of the others' results.

## Faction ticks

Faction ticks are intentionally coarse and skip unless `duration.delta >= config.faction_tick_resolution` (default 30 days). When run, every row in `faction_state` for the branch is read, each goal's `progress` is bumped by `0.01 * months` (capped at 1.0), the JSON state is rewritten, and a `FactionTickSummary` is returned (`service.py:690`). Resource changes, leader actions, and inter-faction conflicts are left as empty fields on the summary — the slot exists, but the logic is a placeholder.

## Weather

Per ticked character's location, the engine asks `world.weather_for(world_id, asset_id, from_time, campaign_id)` and `weather_for(... to_time ...)`. When the `kind` or `summary` differs it appends a `WeatherChange` (`service.py:746`). Locations are deduped by `location_ref`; non-`library:worlds/<w>/locations/<id>` refs are skipped silently (`_split_location_ref`, `service.py:920`).

## Mechanics fan-out

`MechanicsService.time_tick(campaign_id, entity_ref=f"character:{asset_id}", duration, entity_kind="character")` is called for every ticked NPC (`_run_mechanics_ticks`, `service.py:785`). Returned values may be `StateDelta` instances or plain dicts (validated through `StateDelta.model_validate`); any per-character exception is logged and skipped without aborting the pipeline. Aggregated deltas come back on `TimeAdvanceResult.mechanics_deltas`; the engine does **not** apply them to the store itself — callers (Orchestrator) own the apply path.

## Commitment aging

`Continuity` tracks time in whole days from the campaign epoch; the Time Engine works in datetimes. `_to_continuity_time` (`service.py:154`) projects the post-advance `to_time` into Continuity's day-count form using `world.calendar_for_campaign(...).epoch` as the anchor (falling back to the Unix epoch when no calendar is present, since only deltas matter). `_shared_commitment` (`service.py:955`) walks the day count back to a datetime when projecting Continuity's commitments into the shared `Commitment` model carried by `TimeAdvanceResult`.

Convention on the result fields:
- `commitments_overdue` = commitments with an explicit `due_by` that just passed
- `commitments_due` = commitments that just went STALE via inactivity threshold (no `due_by`, open too long — default 180d, owned by Continuity)

## Digest generation

Two layers (`service.py:485`):

1. **Structured digest** — built by `_structured_digest` (`service.py:872`) from a payload dict containing the window, triggered events, NPC summaries, faction summaries, weather changes, and overdue/stale ids. Always produced.
2. **Narrative digest** — generated by the injected `digest_fn(payload)` only when `config.digest_narrative=True`. Prepended to the structured digest with a blank-line separator.

The output is whatever the digest callable returns concatenated with the structured text — there is no narrative-only mode and no UI flow for displaying it yet (the digest is just a field on the result).

## Event emission

Two event types fire from `_emit` (`service.py:861`); both are skipped silently when no `event_bus` was injected:

- `time_advance` — once per `_run_pipeline`, payload: `campaign_id`, `branch_id`, `scene_id`, `reason`, `from`, `to`, `duration_iso`, `npcs_ticked`, `scheduled_events_triggered`
- `npc_tick_complete` — once per ticked NPC, payload: `campaign_id`, `character_ref`, `activities`, `duration_iso`

## Branch isolation

Every read and write keys on `branch_id`, which defaults to `f"{campaign_id}:main"` via `_branch_for` (`service.py:105`). The calendar row, scheduled events, faction state, and the recent-post query are all branch-scoped. `test_schedule_event_honours_branch_id` documents the contract: scheduling on `<campaign>:alt`, advancing on main, and advancing on `<campaign>:alt` are fully independent.

## Configuration (`TimeEngineConfig`)

```python
TimeEngineConfig(
    npc_tick_task="npc_tick",
    digest_task="scene_summary",
    npc_tick_parallelism=4,
    significance=SignificanceConfig(
        always_tick_roles=("pc", "major_npc"),
        tick_with_open_commitment=True,
        tick_in_household=True,           # field present, not yet consumed
        recent_post_window=20,
        max_npcs_per_advance=15,
    ),
    faction_tick_resolution=timedelta(days=30),
    digest_narrative=True,
    scheduled_event_pre_notice=timedelta(days=7),   # field present, not yet consumed
    commitment_stale_threshold=timedelta(days=180), # threshold owned by Continuity
    default_initial_time_iso=None,
)
```

`scheduled_event_pre_notice` and `tick_in_household` are reserved for the remaining-design pass; nothing reads them today.

## Error handling (as implemented)

- `advance` / `skip_to` without a clock → `TimeNotSetError` (caller must `set_current` first or pass `from_time`)
- `skip_to` to a past/equal target → `InvalidSkipError`
- NPC tick callable raises → logged, falls back to empty `_default_npc_tick` payload; pipeline continues
- Weather lookup raises → logged, that location's change is dropped; pipeline continues
- Mechanics `time_tick` raises for a character → logged, that character contributes no deltas; pipeline continues
- Mechanics returns malformed dicts → silently skipped (`model_validate` failures pass)
- `_emit` with no event bus → no-op
- Unparsable scheduled-event timestamps → defaulted to the Unix epoch so the row is still inspectable (`_scheduled_event_from_row`, `service.py:939`)

## HTTP surface

`POST /campaigns/{campaign_id}/time/advance` (`api/campaigns.py:843`) accepts a `TimeAdvancePayload` with either `target` (calls `skip_to`) or `duration` (calls `advance`), plus `reason`, optional `scene_id`, and optional `branch_id`. Returns the `TimeAdvanceResult` serialized via `to_payload`.

## Test wiring

`backend/tests/time_engine/conftest.py` builds a real `StateStore` + migrated SQLite, a `LibraryService`/`WorldService`/`MechanicsService`/`CharactersService`/`ContinuityService` stack, and constructs the `TimeEngineService` with the defaults (no event bus, default NPC tick + digest callables). Tests override `_npc_tick_fn` and `_digest_fn` directly on the service instance to swap behavior per test.

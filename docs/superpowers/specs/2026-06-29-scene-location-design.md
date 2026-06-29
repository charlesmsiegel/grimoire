# Scene Location — Design

**Date:** 2026-06-29
**Status:** Approved, ready for implementation plan

## Problem

A scene has no notion of *where* it takes place. Locations exist as entities
(`kind: "locations"`) and are injected into the context, but only through the
generic **key-activated** world-info pool (`context._world_info` → `activate()`):
keyless locations are always-on, keyed ones surface only when a key word happens
to appear in recent messages. So the model has no reliable sense of the current
setting, and the user can't declare one.

Add a per-scene **setting**: a scene references one current location and injects
it into the context reliably (not subject to keyword luck). The setting can change
mid-scene; when it does, the move is recorded in the transcript and the new
location becomes current.

## Model context (why the decisions below)

- Locations are entities at world/campaign scope with `name`, `keys`, and a
  markdown body. A campaign owns its own copies (copy-on-create + wizard overlays).
- Scenes are markdown files with frontmatter (`title, model, created, updated,
  dismissed`) plus the transcript. Frontmatter already stores comma-joined scalars
  (`dismissed`), so a `location_history` scalar fits the existing shape.
- The transcript parser only understands `**You:**` / `**Grimoire:**` markers
  (user / assistant). There is no standalone narration role, so a transition line
  must be a user or assistant message. `start_from_greeting` already appends an
  assistant message to seed an opener — the same path fits a transition line.
- `context.build_messages` assembles the system prompt from cast blocks +
  key-activated world-info + cast directory, then history, then post-history.

## Decisions

- A scene's location is a **reference** to an existing campaign `locations` entity
  (by id), not free-form text.
- A scene has **one current setting**, but it can change mid-scene. Prior locations
  are retained as an ordered history; the **last** id is current.
- **Visited locations are not injected as a dedicated block.** Only the current
  setting goes into the prompt. The trail lives in the transcript via transition
  lines (the model still sees the journey through history).
- Changing the setting appends an **assistant ("Grimoire") transition message**.
  The first setting set on a scene with no current location is **silent** (nothing
  to move from).

## Data model

Scene frontmatter gains `location_history`: a comma-joined, ordered list of
campaign-location ids, e.g. `location_history: salt-cathedral,drowned-market`.
The last id is the current setting; earlier ids are visited-in-order. No new store
concept — just frontmatter referencing existing entities (mirrors `dismissed`).

State transitions for `set_location(cid, sid, eid)`:

- **`eid` equals the current location** → no-op (`moved: False`).
- **No current location yet** (history empty) → append `eid`; **no** transcript
  line (`moved: False`).
- **Different current location exists** → append the assistant transition message
  `*The scene moves to {name}.*`, then append `eid` to history (`moved: True`).

## Context injection

- The current setting is injected **always-on** as a `# Current setting` block (the
  location's body) at the head of the world context — never keyword-gated.
- The current location id is **excluded from the keyed world-info pool** so it is
  not injected twice. `_world_info` grows an `exclude: set[str]` parameter of
  location ids to skip.
- Visited and all other locations behave as normal keyed world-info; a
  previously-visited place can still re-activate by keyword like any location — it
  just gets no dedicated block.
- No location set → no `# Current setting` block (today's behavior unchanged).

## Backend

### `store/scenes.py`
- Gains an `entities` import (no cycle: `entities` depends only on `frontmatter`,
  `paths`).
- `get_location_history(cid, sid) -> list[str]` — reads `location_history`
  frontmatter (missing scene ⇒ `[]`, mirroring `get_dismissed`).
- `set_location(cid, sid, eid) -> dict` — validates `eid` is a campaign
  `locations` entity (raises `entities.EntityNotFound` otherwise); applies the
  state transition above; on a real move appends the assistant transition message
  via the existing `append_message`; writes `location_history`. Returns
  `{"moved": bool, "name": str}`.

### `store/context.py`
- `_world_info(croot, recent_text, exclude=frozenset())` — skip any `locations`
  entry whose id is in `exclude`.
- `build_messages` — resolve the current location id via
  `scenes.get_location_history`; if present and the entity reads successfully,
  prepend a `# Current setting\n{body}` block to the world context and pass
  `{current_id}` as `_world_info`'s `exclude`. A missing/deleted entity is
  tolerated (block omitted, no crash).

## Routes (`routes.py`)

- `PUT /api/campaigns/{cid}/scenes/{sid}/location` body `{location: eid}` →
  `{"ok": True, "moved": bool, "name": str}`. 404 if the scene or the location is
  missing (`SceneNotFound` / `EntityNotFound`).
- `GET /api/campaigns/{cid}/scenes/{sid}/location` →
  `{"current": {"id", "name"} | null, "visited": [{"id", "name"}, …]}`. Names are
  resolved from the campaign's location entities; an id whose entity no longer
  exists resolves its name to the id.

## Frontend

### `api/client.ts`
- `getSceneLocation(cid, sid) -> { current: {id,name}|null; visited: {id,name}[] }`
- `setSceneLocation(cid, sid, location) -> { ok, moved, name }`
- Types: `SceneLocationRef = { id: string; name: string }`,
  `SceneLocation = { current: SceneLocationRef | null; visited: SceneLocationRef[] }`.

### `components/CastPanel.tsx`
A new **Setting** section:
- Loads the current setting (`getSceneLocation`) and the campaign's locations
  (`listEntities({ kind: "campaign", id: cid }, "locations")`).
- Shows the current setting name, or "No setting" when none.
- A `<select>` of campaign locations + a button labeled **Set location** when there
  is no current setting, **Move here** when changing.
- On change: call `setSceneLocation`, then call the existing `onSeeded()` so the
  transition line appears in the stream, and reload the setting display.

## Testing

### Backend (pytest)
- `scenes.set_location`: first set is silent (history `[a]`, no message appended);
  a change appends the italic assistant transition and history becomes `[a, b]`;
  re-selecting the current location is a no-op; `get_location_history` round-trips.
- `set_location` on an unknown location id raises `EntityNotFound`.
- `context.build_messages` includes a `# Current setting` block with the current
  location's body and does **not** double-inject the current location into the
  keyed world-info (verify with a keyless location: it appears once).
- Routes: `PUT …/location` sets the setting and (on a move) appends the message;
  `GET …/location` returns current + visited with resolved names; 404 for a bad
  location id.

### Frontend (vitest)
- CastPanel renders the current setting name; with no setting shows "No setting".
- Changing the location calls `setSceneLocation` and fires `onSeeded`.
- The dropdown lists the campaign's locations.

## Out of scope

- Clearing a scene's location back to none (YAGNI — change it instead).
- Free-form per-scene location text (decided against: reference existing entities).
- Injecting visited-location descriptions as a dedicated context block (decided
  against: current setting only).

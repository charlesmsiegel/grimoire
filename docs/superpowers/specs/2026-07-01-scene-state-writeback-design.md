# Scene State Write-Back (Phase 2) — Design

**Date:** 2026-07-01
**Status:** Design — approved, ready for implementation plan
**Phase:** 2 of the scene lifecycle & continuity system
**Parent:** [`2026-06-30-scene-lifecycle-continuity-design.md`](2026-06-30-scene-lifecycle-continuity-design.md) (umbrella architecture)
**Builds on Phase 1** (`2026-06-30-scene-chronicle-recap.md` plan, merged): the chronicle,
the single deterministic-primed extraction call (`chronicle.build_prompt`/`parse_output`),
`POST …/absorb` (preview) + `PUT …/chronicle` (persist), the `# Story so far` injection,
and the End-scene review panel.

## Problem

Phase 1 gave scenes memory (a chronicle + recap) but changes nothing about the
campaign's records. Characters, lore, and locations stay frozen at their authored
state no matter what happens in play. Phase 2 delivers the **write-back**: when a
scene ends, the same one extraction call also proposes how the present characters
and the touched lore have **evolved**, the user reviews those changes as diffs, and
the approved ones are written into the campaign's copies. Nothing is written until
approved (the umbrella's "auto, then review diffs" contract).

## Decisions carried from the umbrella (not re-litigated)

- One deterministic-primed extraction call; auto-propose, human-reviews-diffs, then apply.
- Evolution accrues in **dedicated campaign-side state**; authored card/lore fields
  change only **rarely**, via a deliberately-flagged review row.
- The scene is the only unit (Phase 1); this write-back runs at End scene.

## Decisions specific to Phase 2

1. **`current_state` is a snapshot, rewritten each absorb** — "who they are RIGHT NOW,"
   folding in what changed and dropping what's no longer true (the proven "current
   state only, no changelog" rule). It never grows unbounded.
2. **Standing conditions vs events split.** Durable conditions ("arm in a sling,
   healing") go to `current_state`; discrete happenings ("took a wound in the
   ambush") go to `timeline_events` (Phase 1's timeline). The prompt routes each way.
3. **Uniform editable diffs.** Every proposed change is a `before → after` pair the
   reviewer can edit — including lore edits, whose append is pre-computed into
   `after` so there is one apply path ("write `after`") per kind.
4. **Atomic apply.** The approved summary + timeline + edits are written in one call
   (Phase 1's `PUT …/chronicle` grows an `edits` field). Nothing writes before approval.
5. **One home for the extraction.** The prompt/parse move from `chronicle.py` into a
   new `absorb.py`, which also owns diff materialization and apply. `chronicle.py`
   reverts to pure chronicle + timeline IO.
6. **Scope.** Phase 2 = NPC-character `current_state`, lore/location body edits, and
   rare authored card/lore-field edits. **Deferred:** knowledge (Phase 3),
   relationships (Phase 3), plot threads (Phase 4), PC-state and `voice_drift`, and
   the campaign-vs-base view (Phase 5).

## The extraction call (grown, still one call)

Owned by `absorb.py`, invoked once by the route (the `briefs`/Phase-1 pattern).

**Input** gains a compact state snapshot the backend assembles deterministically:
each present NPC character's existing `current_state` (from `state.md`), so the model
*revises* the snapshot instead of writing blind. (Present cast, location, and date are
already primed from Phase 1's `scene_facts`.)

**Output** JSON = Phase 1 fields (`one_line`, `summary`, `keywords`,
`timeline_events`) plus:

```jsonc
{
  "character_state_edits": [{"id": "seraphine", "current_state": "<rewritten snapshot>"}],
  "lore_edits":            [{"id": "salt-cathedral", "append": "<a paragraph>"}],
  "authored_edits":        [{"kind": "characters", "id": "seraphine",
                             "field": "personality", "text": "<proposed new field text>"}]
}
```

Prompt rules: standing conditions → `current_state` (full rewrite, drop stale);
discrete happenings → `timeline_events`; propose an `authored_edit` **only** when a
change is fundamental and durable (expected to be rare). `parse_output` stays tolerant
(garbled ⇒ empty lists, never raises), extended to the three new lists with the same
str-coercion / shape-guarding it already applies.

## Staged-diff model & apply

The backend **materializes** the extraction JSON into `StagedEdit`s, computing
before/after against the campaign copies so the model only proposes deltas:

```jsonc
{ "id": "character_state:seraphine",   // stable row key (kind:target-id[:field])
  "kind": "character_state" | "lore" | "authored",
  "target": {"kind": "characters" | "lore" | "locations", "id": "seraphine"},
  "label": "Seraphine — current state",
  "field": "current_state" | "description" | "personality" | "body",
  "before": "<current text, '' when none>",
  "after":  "<proposed text>",          // lore: before + '\n\n' + append
  "authored": false }                   // true ⇒ card/lore-field edit, flagged in UI
```

Materialization by kind:
- **character_state** — `before` = present `state.md` body (or `""`), `after` = the
  proposed snapshot. `field` = `current_state`, `authored` = false.
- **lore** — `before` = the campaign entity body, `after` = `before + "\n\n" + append`.
  `field` = `body`, `authored` = false. (`locations` route through the same path when
  a lore edit targets a location id; kind resolved by which entity kind holds the id.)
- **authored** — `before` = the current card field (locked version) or entity field,
  `after` = proposed `text`, `authored` = true. Missing target/version ⇒ the edit is
  dropped from the staged set (tolerated, not an error).

`POST …/absorb` returns `{…Phase-1 preview…, "edits": [StagedEdit, …]}` and **writes
nothing**.

**Apply** extends `PUT …/campaigns/{cid}/scenes/{sid}/chronicle`. `ChronicleSave`
gains `edits: list[dict] = []` (the approved subset, each carrying kind / target /
field / final `after`). The handler persists chronicle + timeline + `mark_absorbed`
(Phase 1) **and** applies each edit:
- `character_state` → `playstate.write_state(croot, id, after)`.
- `lore` → `entities.update_entity(croot, target.kind, id, body=after)`.
- `authored` (characters) → `read_card(croot, id, locked_version)` → set `field` = after
  → `update_version`. `authored` (lore/location) → `update_entity(..., <field>=after)`.

An edit whose target no longer exists is skipped (tolerated). Apply is best-effort per
edit; the response reports which were applied.

## Storage

- **`characters/<cid>/state.md`** (campaign copy only; mirrors `brief.md`): frontmatter
  `{updated}` + body = the `current_state` snapshot prose. Created on first write.
  Owned by `playstate.py`: `read_state(root, cid) -> {current_state, updated} | None`,
  `write_state(root, cid, current_state) -> None`.
- Lore/location and authored edits mutate the **existing campaign copies** (no new
  files), where Phase 5's campaign-vs-base view will later surface them via `sync.md`.

## Context injection

A new always-on labeled section **`# Character state`** in `context._assemble`, added
next to the character-description blocks: for each present NPC character with a
non-empty `state.md`, one line `Name: <current_state>`. Built deterministically;
tolerant of missing/garbled state (omit, never crash — same posture as the Phase-1
`# Story so far` fix). Empty when no present character has state.

## Backend modules

- **`store/absorb.py`** (new) — `EXTRACT_INSTRUCTION`, `build_prompt(transcript, facts,
  state_snapshot)`, `parse_output(text) -> dict` (Phase-1 fields + the three edit
  lists), `state_snapshot(cid, sid) -> dict` (present NPCs' current_state),
  `materialize(cid, sid, parsed) -> list[StagedEdit]`, `apply_edits(cid, edits) ->
  list[applied]`. The Phase-1 `build_prompt`/`parse_output` move here from
  `chronicle.py`; extended, not duplicated.
- **`store/playstate.py`** (new) — `state.md` read/write per character. (Relationships
  and plot are Phases 3–4; the module starts with state only.)
- **`store/chronicle.py`** — reverts to chronicle + timeline IO (loses the prompt/parse;
  keeps `read_chronicle`/`absorb`/`recent`/`append_timeline`/`scene_facts`/
  `transcript_text`).
- **`store/context.py`** — the `# Character state` section.
- **`routes.py`** — `post_absorb` calls `absorb.*` and returns `edits`; `put_chronicle`
  applies the approved `edits`.

No new import cycles: `absorb`/`playstate` import `campaigns`/`characters`/`entities`/
`chronicle` at module load as needed; `context` reads `playstate` (function-local if
required to stay acyclic, mirroring existing patterns).

## Routes

- `POST /api/campaigns/{cid}/scenes/{sid}/absorb` → `{one_line, summary, keywords,
  timeline_events, cast, location, date, edits: [StagedEdit]}`; still writes nothing;
  400 empty scene; 409 missing key; 502 upstream error (Phase-1 behavior preserved).
- `PUT /api/campaigns/{cid}/scenes/{sid}/chronicle` body gains
  `edits: [{kind, target, field, after}]` → persists chronicle/timeline/`done`
  (Phase 1) **and** applies the approved edits; returns the stored record plus the list
  of applied edit ids.

## Frontend

- `api/client.ts` — types `StagedEdit`, extend `SceneAbsorb` with `edits: StagedEdit[]`,
  extend the `saveChronicle` body with `edits`.
- `CampaignView` review panel — below the summary, an **edits checklist**: each row
  shows `label`, a before→after diff (a simple two-block or inline rendering), a
  checkbox (approved by default), and an editable `after` textarea; **authored rows are
  visually flagged** (e.g. a "card edit" tag). Save sends `{…summary…, edits:
  approvedRowsWithFinalAfter}`.
- `SceneInspector` — **deferred** (a read-only "current state" panel is a nicety; the
  model already receives the state via the `# Character state` injection). Revisit
  alongside the Phase 5 view.

## Testing

### Backend (pytest, temp `GRIMOIRE_HOME`, fake OpenRouter)
- `absorb.parse_output`: Phase-1 fields plus the three edit lists parse; garbled ⇒
  empty lists, never raises; the moved prompt still primes cast/location/date.
- `absorb.state_snapshot`: returns present NPCs' `current_state`; absent state ⇒ omitted.
- `absorb.materialize`: a sample parsed JSON yields the expected `StagedEdit`s with
  correct before/after — character_state (before from state.md or ""), lore (after =
  before + append), authored (before = card field, `authored: true`); a target that
  doesn't exist is dropped.
- `absorb.apply_edits`: character_state writes `state.md`; lore updates the entity body;
  authored updates the card field at the locked version; a missing target is skipped;
  only the passed (approved) subset is applied.
- `playstate`: `state.md` read/write round-trip; missing ⇒ `None`.
- `context`: `# Character state` lists a present NPC's `current_state`; omitted when
  none; tolerant of garbled state (no crash).
- Routes: `POST …/absorb` returns `edits` and writes nothing (state.md/entities
  unchanged); `PUT …/chronicle` with an approved `edits` subset writes `state.md`, the
  lore body, and the card field, and leaves un-approved edits unapplied.

### Frontend (vitest)
- The review panel renders edit rows with before/after and checkboxes; unchecking a row
  excludes it from the Save payload; editing an `after` sends the edited value;
  authored rows show the flag. Save calls `saveChronicle` with the summary + approved
  edits.

## Out of scope (Phase 2)

- Knowledge tracker, relationships, plot threads (Phases 3–4).
- PC-persona evolution and `voice_drift`.
- Campaign-vs-base diff view (Phase 5).
- The Phase-1 deferred Minor (re-absorb re-appends `timeline.md` lines) — still no
  timeline reader; addressed when the timeline gains one.

## Phasing (for the implementation plan)

1. **`absorb.py`** — move Phase-1 prompt/parse here; extend `parse_output` to the three
   edit lists; `state_snapshot`; update the `post_absorb` route + Phase-1 tests. (Green,
   no behavior change to the summary path.)
2. **`playstate.py`** + `materialize` + the `# Character state` injection.
3. **`apply_edits`** + `PUT …/chronicle` `edits` application (backend end-to-end).
4. **Frontend** — `StagedEdit` types, the edits checklist in the review panel, Save wiring.

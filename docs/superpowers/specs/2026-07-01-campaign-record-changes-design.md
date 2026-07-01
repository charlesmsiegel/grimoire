# Campaign Record Changes (Phase 6) — Design

**Date:** 2026-07-01
**Status:** Design — approved, ready for implementation plan
**Phase:** 6 of the scene lifecycle & continuity system — the **final umbrella phase**
**Parent:** [`2026-06-30-scene-lifecycle-continuity-design.md`](2026-06-30-scene-lifecycle-continuity-design.md) (umbrella; see "Campaign-vs-base world view")
**Builds on:** the write-back pipeline (`absorb.materialize` / `absorb.apply_edits`, Phases
2–5a), the `StagedEdit` shape (every edit already carries `before`/`after`), and the
`put_chronicle` route (holds `sid` at apply time).

## Problem

The write-back phases mutate the campaign's own copies of world records — a location's body
grows a paragraph, an NPC's standing state is rewritten, a card field is durably edited — but
those changes vanish into the file with no way to see **what** a record looked like before the
last scene reshaped it. The GM wants to browse the campaign's evolving records
(characters / lore / locations) and, for each, see a **highlighted diff of its previous
version → its current version**: the delta from the most recent absorb that touched it.

This is the last umbrella phase. Its umbrella framing was "campaign-vs-base world view"
(divergence from the base *world*). The approved reframe is narrower and more useful:
compare the campaign record's **own** last-write-back state to its current state — world drift
is irrelevant. The fork-point content is not recoverable (only its hash is stored in
`sync.md`), and the base-world comparison is explicitly **not** what the user wants.

## Scope

- **In:** a rolling per-record capture of the **latest** write-back delta (previous → current)
  for browsable records (characters / lore / locations); a `GET …/changes` read endpoint that
  computes a server-side line diff per changed field; a **"Changes" panel in `CampaignView`**
  that lists changed records grouped by kind and renders each field's highlighted line diff.
- **Deferred / out:** cumulative fork-point ("everything since the record entered the
  campaign") diffs; full per-scene history / timeline stepping; any comparison against the base
  **world** record; editing from the panel (read-only). `relationships` and `plot` changes are
  **not** browsable records here — they have their own injections/views.

## Decisions

1. **"Previous version" = the record's state immediately before the most recent write-back**
   that touched it — the per-scene delta, not a cumulative fork diff. Only the **latest** change
   per record is kept (rolling).
2. **Capture at the write-back choke point, not a snapshot pass.** Every `StagedEdit` already
   carries `before`/`after`, and `apply_edits` is the single point where campaign copies mutate.
   Recording the delta there is nearly free and can never drift from what was actually written —
   only **applied** (approved) edits are recorded.
3. **Browsable kinds only.** `character_state` and `authored` → `characters/{id}`; `lore` →
   `lore/{id}` or `locations/{id}` (via the edit's real `target.kind`). `relationship`, `bond`,
   and `plot` are skipped (structured records with dedicated context blocks, not
   prose records the user browses/diffs).
4. **Server computes the line diff.** Stdlib `difflib` (no new dependency) turns before/after
   into tagged lines the frontend renders — matching the established "backend produces, frontend
   renders" pipeline. A garbled `changes.json` yields an empty list, never a 500 (tolerant, like
   every other axis).
5. **A single list endpoint, not per-record.** The changed set is small and each diff is bounded
   to one delta, so one `GET …/changes` feeds both the panel's list and its detail.
6. **A tiny write path is acceptable.** The umbrella framing assumed read-only reporting; the
   previous-version requirement necessarily records the delta at absorb time. It is scoped and
   isolated to `changes.py` + the `apply_edits` hook — no change to what write-back writes to the
   records themselves.

## Data model — `store/changes.py`

`campaign_root/changes.json`: a rolling map, `"{kind}/{id}"` → the **latest** write-back that
touched that record.

```jsonc
{
  "locations/harbor": {
    "scene": "<sid>",
    "fields": [
      { "field": "body", "label": "Harbor — locations",
        "before": "A busy port town.",
        "after":  "A busy port town, now blockaded by the Pact." }
    ]
  },
  "characters/mara": {
    "scene": "<sid>",
    "fields": [
      { "field": "current_state", "label": "Mara — current state", "before": "…", "after": "…" },
      { "field": "personality",   "label": "Mara — personality (card edit)", "before": "…", "after": "…" }
    ]
  }
}
```

- **Rolling:** a new absorb that touches a record **replaces** that record's entry — only the
  latest change survives. Multiple edits from *one* absorb touching the same record accumulate
  into `fields` (e.g. an NPC's `current_state` and a card `personality` edit in the same scene).
- `field`/`label`/`before`/`after` are copied verbatim from the `StagedEdit` (the `before` must
  be stored because the record's old content is gone once overwritten).

Functions (pure store I/O, tolerant reads mirroring `plot`/`relationships`):

- `record(cid, sid, changes: dict[str, list[dict]]) -> None` — upsert the touched records:
  for each `ref → fields`, write `{"scene": sid, "fields": fields}`, replacing any prior entry.
- `read(cid) -> dict` — parse `changes.json`; `{}` on missing/garbled.
- `line_diff(before: str, after: str) -> list[dict]` — split both on lines and emit tagged
  lines `{op: "equal" | "insert" | "delete", text}` via `difflib.SequenceMatcher.get_opcodes()`
  (a `replace` opcode emits its deletes then its inserts). Deterministic; the diff unit.

## Recording hook — `absorb.apply_edits`

Signature becomes `apply_edits(cid, edits, sid=None)`. `put_chronicle` passes `sid`; the default
`None` **skips recording** (keeps existing `apply_edits` unit tests green — recording is exercised
via the route and `changes` tests).

Inside the existing best-effort loop, when an edit is **successfully applied** and its `kind` is
browsable, accumulate a per-record field entry keyed by `"{target.kind}/{target.id}"`:

- `character_state` → `characters/{id}`, field `current_state`.
- `authored` → `characters/{id}`, field = the card field.
- `lore` → `{target.kind}/{id}` (`lore` or `locations`), field `body`.

Each accumulated field is `{field, label, before, after}` taken from the edit. After the loop,
if `sid` is set and any browsable record was touched, call `changes.record(cid, sid, acc)`.
Skipped/failed edits contribute nothing.

## Read endpoint + line diff — `routes.py`

`GET /campaigns/{cid}/changes` → a flat list, one entry per changed record (grouping by kind
and ordering handled client-side):

```jsonc
[
  { "ref": { "kind": "locations", "id": "harbor" },
    "name": "Harbor",
    "scene": { "id": "<sid>", "title": "The blockade", "date": "12 Harvestmoon" },
    "fields": [
      { "field": "body", "label": "Harbor — locations",
        "diff": [ { "op": "equal",  "text": "A busy port town." },
                  { "op": "insert", "text": "Now blockaded by the Pact." } ] } ] }
]
```

- **`name`** resolved from the current record (entity `meta.name` / character name), falling
  back to the id.
- **`scene`** resolved tolerantly from scene meta; if the scene was later deleted, `title`/`date`
  fall back (id retained). A missing record (deleted since the change) is dropped.
- **`diff`** is `changes.line_diff(field.before, field.after)` per field.
- Tolerant: garbled `changes.json` → `[]`. `404` on unknown campaign
  (`_campaign_root_or_404`).

Declared **before** the generic `/campaigns/{cid}/{kind}` routes so `"changes"` is not captured
as an entity kind (same guard the `incoming`/`scenes` routes use).

## Backend modules

- **`store/changes.py`** (new) — `record`, `read`, `line_diff`. Pure store I/O + a stdlib diff;
  no LLM, no imports beyond `campaigns` (root) and the frontmatter/JSON helpers. No new cycle.
- **`store/absorb.py`** — `apply_edits` gains `sid` and the accumulate-then-`changes.record`
  hook; imports `changes`.
- **`store/__init__.py`** — export `changes`.
- **`routes.py`** — the one new `GET …/changes` endpoint (name resolution + scene resolution +
  per-field diff); `put_chronicle` passes `sid` into `apply_edits`.

## Frontend

- **`api/client.ts`** — `campaignChanges(cid) -> RecordChange[]`, with types
  `RecordChange` (`ref:{kind,id}`, `name`, `scene:{id,title,date} | null`, `fields: FieldDiff[]`)
  and `FieldDiff` (`field`, `label`, `diff: DiffLine[]`), `DiffLine` (`op: "equal"|"insert"|"delete"`, `text`).
- **`components/ChangesPanel.tsx`** (new) — a read-only list/detail:
  - **List:** records that have a recorded change, grouped **Characters / Lore / Locations**;
    each row shows the record `name` + a "changed in *{scene title}*" hint. Empty state when
    nothing has been absorbed yet.
  - **Detail:** the selected record's `fields`, each a labeled block rendering its `diff` as
    lines with `op`-based classes (`insert` green, `delete` red, `equal` muted). Small CSS on
    the three classes; no diff library on the client.
- **`routes/CampaignView.tsx`** — a tab/toggle (alongside the chat) that reveals `ChangesPanel`
  for the campaign. No change to the chat/absorb flow.

## Testing

### Backend (pytest)
- **`changes.line_diff`**: insert-only, delete-only, replace (delete-then-insert ordering),
  identical (all `equal`), and empty-before / empty-after cases.
- **`changes` via `apply_edits`**: applying a `lore` edit records `lore|locations/{id}` with the
  edit's before/after; a `character_state` + `authored` edit in one call accumulate two `fields`
  under `characters/{id}`; `relationship`/`bond`/`plot` edits record **nothing**; a second absorb
  touching the same record **replaces** its entry (rolling); `sid=None` records nothing; a
  garbled/missing `changes.json` reads as `{}`.
- **route** (`test_routes.py`): `GET …/changes` returns resolved `name` + `scene` + per-field
  `diff`; a deleted-scene entry falls back gracefully; an empty campaign returns `[]`; `404` for
  an unknown campaign; the route is not shadowed by the generic `/{kind}` route.

### Frontend (vitest)
- **`ChangesPanel.test.tsx`**: renders changed records grouped by kind; selecting a record shows
  its field diffs with `insert`/`delete` line classes; empty state when there are no changes.
- **`CampaignView`**: the Changes tab reveals the panel.

## Out of scope

- Cumulative fork-point diffs, full per-scene history, and any base-**world** comparison.
- Editing records from the panel (read-only) and diffing `relationships`/`plot`/`bond`.
- Changing what write-back writes to the records themselves — only the delta capture is new.

## Phasing (for the plan)

1. `changes.py`: `line_diff` (stdlib `difflib`, tagged lines) + `read`/`record` (rolling,
   tolerant).
2. `absorb.apply_edits`: `sid` param + accumulate browsable applied edits → `changes.record`;
   `put_chronicle` passes `sid`.
3. `routes.py`: `GET /campaigns/{cid}/changes` (name + scene resolution, per-field diff),
   declared before the generic `/{kind}` routes.
4. Frontend: `api.campaignChanges` + `RecordChange`/`FieldDiff`/`DiffLine` types.
5. Frontend: `ChangesPanel` (grouped list/detail + diff CSS) and the `CampaignView` tab.

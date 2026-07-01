# Scene Relationships (Phase 3) — Design

**Date:** 2026-07-01
**Status:** Design — approved (standing authorization), ready for implementation plan
**Phase:** 3 of the scene lifecycle & continuity system
**Parent:** [`2026-06-30-scene-lifecycle-continuity-design.md`](2026-06-30-scene-lifecycle-continuity-design.md) (umbrella; see the Relationships model)
**Builds on Phase 2** (`2026-07-01-scene-state-writeback.md`, merged): the `absorb.py`
extraction → `materialize` (StagedEdits) → review checklist → `apply_edits` → context
injection pipeline, and `playstate.py`.

## Problem

Phase 2 evolves each character's own `current_state`. It captures nothing *between*
characters. Relationships — who trusts, resents, or is bonded to whom — are the
highest-value continuity signal for character-driven play, and they are frequently
**asymmetric** (A trusts B while B stays wary of A). Phase 3 adds a relationship model
that the extraction proposes, the user reviews, and the context injects for the
present cast — reusing the Phase 2 machinery.

## Scope

- **In:** pairwise relationships among cast (characters ↔ characters, characters ↔ PCs,
  incl. NPC↔NPC): directed **feelings** (trust/affection/tension + a note) and
  symmetric **bonds** (a relationship type). Proposed at End scene, reviewed, applied,
  and injected for present cast.
- **Deferred:** knowledge (who-knows-what) — the next phase; plot threads (Phase 4);
  campaign-vs-base view (Phase 5). Relationship metrics are **not inline-editable** in
  review this phase (approve/reject the model's proposal wholesale); text edits remain
  for character-state/lore/authored rows.

## Decisions

1. **Feelings are directed and asymmetric; bonds are symmetric by construction**
   (umbrella). No enforced symmetry on feelings; a scene that moves one direction
   produces one diff. A canonicalizing helper sorts the bond key so the two orderings
   can't both exist.
2. **Snapshot metrics, fed current values.** Like `current_state`, the extraction is
   fed the present cast's current feelings/bonds and returns **absolute** updated
   values (not deltas), so a diff is `before → after` of the metric set. Metrics are
   bounded ints **0–5** (trust, affection, tension); the note is free text.
3. **Actor tokens** are `"<kind>:<id>"` (e.g. `characters:seraphine`, `pcs:elara`) so
   characters and PCs share one namespace without collision.
4. **Reuse the StagedEdit pipeline**, extended with two new kinds and a structured
   `payload` (see below). Relationship/bond rows render read-only in review with an
   approve checkbox; `apply_edits` writes them from `payload`.

## Storage

`<campaign>/relationships.json` (nested → JSON sidecar, per convention):

```jsonc
{
  "feelings": {                         // directed; independent directions
    "characters:seraphine->characters:elara": {"trust": 4, "affection": 3, "tension": 1, "note": "grateful; guards her"},
    "characters:elara->characters:seraphine": {"trust": 1, "affection": 1, "tension": 3, "note": "doesn't buy the act"}
  },
  "bonds": {                            // one entry per UNORDERED pair (sorted tokens, "a|b")
    "characters:elara|characters:seraphine": {"type": "reluctant allies", "since_scene": "s12"}
  }
}
```

New module **`store/relationships.py`**:
- `read(cid) -> {"feelings": {...}, "bonds": {...}}` (missing ⇒ empty structures).
- `feeling_key(from_tok, to_tok) -> str` = `"{from}->{to}"`.
- `bond_key(a_tok, b_tok) -> str` = sorted `"{lo}|{hi}"` (canonical).
- `get_feeling(cid, from_tok, to_tok) -> dict | None`; `get_bond(cid, a, b) -> dict | None`.
- `set_feeling(cid, from_tok, to_tok, trust, affection, tension, note) -> None`.
- `set_bond(cid, a, b, type, since_scene="") -> None` (canonical key; preserves an
  existing `since_scene` when re-set).
- Pure JSON IO (`indent=2, sort_keys=True`).

## Extraction (grows again, still one call)

`absorb.build_prompt` gains a **relationships snapshot** among present cast (each
present pair's current feelings + bond) so the model updates rather than invents.
`parse_output` gains two lists:

```jsonc
{
  "relationship_deltas": [                // ABSOLUTE new values (directed)
    {"from": "characters:seraphine", "to": "characters:elara",
     "trust": 4, "affection": 3, "tension": 1, "note": "..."}],
  "bond_changes": [{"a": "characters:elara", "b": "characters:seraphine", "type": "reluctant allies"}]
}
```

Prompt rules: use the `<kind>:<id>` tokens from the context block; only emit an entry
for a pair whose feeling/bond actually changed; ints in 0–5.

## StagedEdit extension

`materialize` produces two new `StagedEdit` kinds. The shared shape gains an optional
`payload` (structured values that `apply_edits` writes); `before`/`after` carry a
**human-readable rendering** for the diff row:

```jsonc
{ "id": "feeling:characters:seraphine->characters:elara",
  "kind": "relationship",
  "target": {"kind": "relationships", "id": "characters:seraphine->characters:elara"},
  "label": "Seraphine → Elara",
  "field": "feeling",
  "before": "trust 1, affection 1, tension 3 — (none)",     // readable; "" when new
  "after":  "trust 4, affection 3, tension 1 — grateful; guards her",
  "authored": false,
  "payload": {"from": "characters:seraphine", "to": "characters:elara",
              "trust": 4, "affection": 3, "tension": 1, "note": "..."} }
```

Bond rows mirror this with `kind: "bond"`, `field: "bond"`, `before`/`after` = the type
string, `payload: {a, b, type}`. Names in `label`/renderings are resolved from the
campaign's characters/PCs (fall back to the id). A delta referencing an actor not in
the campaign is dropped (tolerated). Only pairs where `after` differs from `before` are
emitted (no-op changes are skipped).

Existing kinds (`character_state`/`lore`/`authored`) are unchanged and carry no
`payload`; the review UI edits their `after` text as before.

## Apply

`apply_edits` gains two branches (best-effort, per the Phase-2 contract):
- `relationship` → `relationships.set_feeling(cid, **payload)`.
- `bond` → `relationships.set_bond(cid, payload["a"], payload["b"], payload["type"])`.

The frontend sends approved rows with their `payload` intact.

## Context injection

A new always-on **`# Relationships`** section in `_assemble`, among present cast only:
for each ordered pair of present actors with a stored feeling, one directed line; then
each present pair's bond. Tolerant of a garbled `relationships.json` (omit, never
crash — the Phase-1/2 posture).

```
# Relationships
Seraphine → Elara: trust 4, affection 3, tension 1 (grateful; guards her)
Elara → Seraphine: trust 1, affection 1, tension 3 (doesn't buy the act)
Elara & Seraphine: reluctant allies
```

Present actors are the scene cast (characters + PCs). Name resolution reuses the
existing helpers; ids that no longer resolve fall back to the token.

## Backend modules

- **`store/relationships.py`** (new) — the JSON store + key helpers above.
- **`store/absorb.py`** — `build_prompt` relationships snapshot; `parse_output`
  `relationship_deltas`/`bond_changes`; `materialize` the two new kinds; `apply_edits`
  two new branches; a `relationships_snapshot(cid, sid)` helper.
- **`store/context.py`** — the `# Relationships` section.
- **`routes.py`** — no new endpoints (rides `POST /absorb` + `PUT /chronicle`); a
  `GET /api/campaigns/{cid}/relationships` read is optional (skip unless the UI needs it).

No new cycles: `relationships` imports only `campaigns`/`paths`; `absorb`/`context`
import it at module load.

## Frontend

- `api/client.ts` — extend `StagedEdit` with an optional `payload?: Record<string, unknown>`.
- `CampaignView` review checklist — branch row rendering by kind: `character_state`/
  `lore`/`authored` keep the editable `after` textarea; `relationship`/`bond` render a
  read-only `before → after` line with the approve checkbox only. Save still sends the
  approved subset (now including `payload` for structured rows).

## Testing

### Backend (pytest)
- `relationships`: feeling/bond round-trip; `bond_key` canonical (both orderings → one
  entry); `set_bond` preserves `since_scene`; missing file ⇒ empty.
- `absorb.parse_output`: `relationship_deltas`/`bond_changes` parse; garbled ⇒ empty
  lists; ints coerced.
- `absorb.materialize`: a delta → a `relationship` StagedEdit with readable before/after
  + `payload`; asymmetry preserved (one direction only); a no-op (after == before)
  dropped; an unknown actor dropped; a bond → `bond` StagedEdit with canonical payload.
- `absorb.apply_edits`: `relationship`/`bond` write `relationships.json`; only the
  approved subset; a malformed payload skipped.
- `context`: `# Relationships` lists present-cast directed feelings + bond; omitted when
  none; tolerant of garbled json.

### Frontend (vitest)
- Review renders a relationship row read-only (no textarea) with an approve checkbox;
  unchecking excludes it; Save sends the row **with its `payload`**.

## Out of scope

- Knowledge (next phase), plot threads (Phase 4), campaign-vs-base view (Phase 5).
- Inline metric editing in review (approve/reject only this phase).
- Relationships among non-present or off-scene actors (only the present cast is
  snapshotted, proposed, and injected).

## Phasing (for the plan)

1. `relationships.py` store + key helpers.
2. `absorb`: snapshot + parse lists + `relationships_snapshot`.
3. `materialize` the two kinds (+ `payload`); wire into `POST /absorb` (already returns
   `edits`).
4. `apply_edits` two branches.
5. `# Relationships` injection.
6. Frontend: `payload` type + read-only structured rows.

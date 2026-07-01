# Scene Knowledge (Phase 4) — Design

**Date:** 2026-07-01
**Status:** Design — approved, ready for implementation plan
**Phase:** 4 of the scene lifecycle & continuity system
**Parent:** [`2026-06-30-scene-lifecycle-continuity-design.md`](2026-06-30-scene-lifecycle-continuity-design.md) (umbrella; see the knowledge / `knows`/`suspects` model)
**Builds on Phase 2** (`2026-07-01-scene-state-writeback-design.md`, merged): the
`absorb.py` extraction → `materialize` (StagedEdits) → review checklist → `apply_edits`
→ context injection pipeline, and `playstate.py`.

## Problem

Phase 2 evolves each NPC's `current_state` — their standing *condition*. It captures
nothing about **what each character knows or suspects**. Who-knows-what is the pivot of
mystery, secrets, and dramatic irony: the model should not have a guard reveal a secret
they were never told, nor forget one they learned last scene. Phase 4 tracks per-NPC
knowledge so the extraction proposes it, the user reviews it, and the context injects it
for the present cast — reusing the Phase 2 machinery with no new store and no new
StagedEdit kind.

## Scope

- **In:** per-NPC standing knowledge as two prose fields — **`knows`** (held for certain)
  and **`suspects`** (believed but unconfirmed) — stored beside `current_state` on
  `state.md`, proposed at End scene, reviewed, applied, and injected for present NPCs.
- **Deferred:** PC knowledge (players know what they know; no PC-state store exists yet);
  plot threads + suggested next scenes (next); campaign-vs-base view (last). `voice_drift`
  remains unmodeled (pre-existing debt).

## Decisions

1. **Prose fields on `state.md`, merged into character-state.** Knowledge is prose
   (`knows`/`suspects`), stored as headed sections in the existing per-character
   `state.md` body — not a new store. It rides the existing `character_state` StagedEdit
   and the existing `# Character state` context section. No new `kind`, no `payload`, no
   frontend component.
2. **Snapshot semantics, fed current values** (matching Phase 2 `current_state` and
   Phase 3 metrics). The extraction is fed each present NPC's current
   `current_state`/`knows`/`suspects` and returns the **full rewritten** snapshot of all
   three (dropping what is stale). A diff is `before → after` of the composed blob. This
   preserves accreted knowledge only because the current values are fed back in; the
   review gate is the backstop against an accidental wipe.
3. **NPC-only** (mirror Phase 2). Only present NPC characters (`role == "npc"`,
   `kind == "characters"`) get knowledge. PCs and player-role characters are skipped.
4. **Backward compatible.** Existing Phase-2 `state.md` files hold a bare `current_state`
   body with no headers; `read_state` treats an unheaded body as `current_state` with
   empty `knows`/`suspects`, so nothing needs migrating.

## Storage — `state.md` grows headed sections

`<campaign>/characters/<cid>/state.md`. The body becomes up to three optional
`## `-headed prose sections; empty sections are omitted:

```markdown
---
updated: 2026-07-01T12:00:00
---
## Current state
Shaken after the cathedral; nursing a cracked rib.

## Knows
The map Elara carries is a forgery.

## Suspects
That Elara is working for the Salt Duke.
```

`playstate.py` changes:
- `read_state(root, cid) -> {"current_state", "knows", "suspects", "updated"} | None`.
  Parses the body by the recognized headers `## Current state`, `## Knows`,
  `## Suspects` (case-insensitive match on the header line). **A body with no recognized
  header is treated wholesale as `current_state`** (knows/suspects `""`) — Phase-2
  back-compat. Text before the first recognized header, if any, is folded into
  `current_state`.
- `compose_body(current_state, knows, suspects) -> str` — builds the headed blob,
  **omitting any section whose field is empty** (stripped). Section order is fixed:
  Current state, Knows, Suspects.
- `write_state(root, cid, body)` — writes the given body verbatim under fresh
  `updated` frontmatter. (Renamed parameter from `current_state`; apply hands it the
  already-composed blob. The only production caller is `absorb.apply_edits`.)

Pure IO, mirrors `briefs.py`. No import-cycle change.

## Extraction (grows again, still one call)

`absorb.py`:
- **`EXTRACT_INSTRUCTION`** — `character_state_edits` fields become
  `{"id", "current_state", "knows", "suspects"}`: for each present NPC whose standing
  snapshot changed, the FULL rewritten `current_state`, plus what they now hold as
  **known** (`knows`) and **suspected-but-unconfirmed** (`suspects`), dropping what is no
  longer true. Standing knowledge only, not a running log. Empty string for a field that
  does not apply.
- **`parse_output`** — `character_state_edits` field list extends to
  `("id", "current_state", "knows", "suspects")` (already coerced to stripped strings;
  missing ⇒ `""`).
- **`state_snapshot(cid, sid)`** — returns each present NPC's current three fields (keyed
  by display name) so the model rewrites rather than invents and does not silently wipe
  knowledge. `build_prompt` renders the "Current character state" block to include
  `Knows:`/`Suspects:` lines when present. (Signature shape widens from
  `name -> current_state` to `name -> {current_state, knows, suspects}`; `build_prompt`
  updated in lockstep.)

## StagedEdit — unchanged shape

`materialize`'s `character_state` branch is the only change: `before`/`after` become the
**composed headed blob** (via `compose_body`) instead of a bare `current_state`
paragraph. Still one editable-textarea row per NPC, `kind: "character_state"`,
`field: "current_state"`, `authored: false`, **no `payload`**. `before` is recomposed
from the current `read_state` fields (`""` when no state file); `after` is composed from
the parsed `current_state`/`knows`/`suspects`. **No-op guard:** the row is skipped when
`before == after` (new — avoids emitting an unchanged snapshot; Phase 2 emitted
unconditionally).

```jsonc
{ "id": "character_state:seraphine",
  "kind": "character_state",
  "target": {"kind": "characters", "id": "seraphine"},
  "label": "Seraphine — current state",
  "field": "current_state",
  "before": "## Current state\n…\n\n## Knows\n…",     // composed blob; "" when new
  "after":  "## Current state\n…\n\n## Knows\n…\n\n## Suspects\n…",
  "authored": false }
```

## Apply — unchanged

`apply_edits` still does `playstate.write_state(croot, target["id"], after)`; `after` is
now the composed blob, stored verbatim. Best-effort per edit, per the Phase-2 contract.
The reviewer's textarea edits apply directly (they edit the blob text).

## Context injection — merged into `# Character state`

`context._character_state` renders each present NPC's `current_state` plus, when
non-empty, indented `Knows:`/`Suspects:` lines. Same section label, same NPC-only cast
filter, same tolerant omit-never-crash `try/except`.

```
# Character state
Seraphine: Shaken after the cathedral; nursing a cracked rib.
  Knows: The map Elara carries is a forgery.
  Suspects: That Elara is working for the Salt Duke.
Doran: Wary of strangers since the ambush.
```

An NPC with only `current_state` renders exactly as it does today (single line).

## Backend modules touched

- **`store/playstate.py`** — `read_state` parses three fields; add `compose_body`;
  `write_state` writes a verbatim body.
- **`store/absorb.py`** — `EXTRACT_INSTRUCTION` + `parse_output` knowledge fields;
  `materialize` composed blob + no-op guard; `state_snapshot` three fields; `build_prompt`
  renders knowledge in the snapshot block.
- **`store/context.py`** — `_character_state` renders `Knows:`/`Suspects:`.
- **`routes.py`** — no change (rides `POST /absorb` + `PUT /chronicle`).

No new imports or cycles.

## Frontend

**No code change.** The `character_state` review row already renders an editable `after`
textarea and returns it; it now carries a longer multi-section string. StagedEdit type is
unchanged. A single vitest may assert the multi-section body renders in the textarea
(behavior-only assertion, no new component).

## Testing

### Backend (pytest)
- **`playstate`**: three-field round-trip through `write_state`(`compose_body`) →
  `read_state`; an unheaded legacy body reads as `current_state` with empty
  knows/suspects (back-compat); `compose_body` omits empty sections; `updated` refreshed
  on write.
- **`absorb.parse_output`**: `character_state_edits` parse `knows`/`suspects`; missing ⇒
  `""`; garbled ⇒ empty list.
- **`absorb.materialize`**: `after` is the composed blob including the knowledge
  sections; existing knowledge preserved when unchanged; a no-op (before == after)
  dropped; an unknown character dropped; `before` `""` when no state file.
- **`absorb.state_snapshot`**: includes knows/suspects for a present NPC that has them.
- **`context`**: `# Character state` renders `Knows:`/`Suspects:` when present; single
  line when only `current_state`; section omitted when no NPC has state; tolerant of a
  garbled `state.md`.

### Frontend (vitest)
- A `character_state` row whose `after` contains `## Knows`/`## Suspects` renders that
  full text in the editable textarea; Save sends the edited blob. (No new behavior; guards
  against a future component split.)

## Out of scope

- PC knowledge; plot threads + suggested next scenes; campaign-vs-base world view.
- Any new StagedEdit `kind`, `payload`, or knowledge store — knowledge rides the existing
  `character_state` row and `state.md`.
- Inline structured editing of knowledge (it is edited as prose in the existing textarea).

## Phasing (for the plan)

1. `playstate.py`: `read_state` three-field parse + back-compat; `compose_body`;
   `write_state` verbatim body.
2. `absorb.py`: `parse_output` knowledge fields; `state_snapshot` three fields +
   `build_prompt` render; `EXTRACT_INSTRUCTION` update.
3. `absorb.materialize`: composed blob + no-op guard.
4. `context._character_state`: `Knows:`/`Suspects:` render.
5. Frontend: multi-section textarea vitest (no code change).

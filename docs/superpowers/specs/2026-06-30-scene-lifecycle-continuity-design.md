# Scene Lifecycle & Continuity — Design

**Date:** 2026-06-30
**Status:** Design — architecture approved, decomposes into per-phase specs
**Builds on:** scene-workspace (`context._assemble` labeled sections, `SceneInspector`),
scene-location (`location_history`, `# Current setting`), scene-calendar
(`time_history`, `# Today`, the calendar engine), briefs
(`briefs.py` — the single-call, backend-owned summarizer precedent), campaigns
(copy-on-create divergence + `sync.md` base hashes), greetings/plot-maps
(the *authored* greeting graph — distinct from the *played* plot threads here).

## Problem

Scenes are islands. A scene is a markdown transcript with cast, a current location,
and a current datetime, and the context builder assembles each turn freshly from
**authored** state (cards, personas, world-info). Nothing a scene *produces* feeds
back into the campaign or into the next scene. There is no notion of a scene
**ending**, no memory of what happened, and no way for characters, lore, or
relationships to **evolve through play**. "Continuity" today means only what the
author manually bakes into records.

This design delivers the missing lifecycle loop: **play → end scene → absorb what
happened → evolve campaign state (under review) → carry it forward into the next
scene**. It is a deliberately *less agentic* reconstruction of a loop previously
run as a set of claude.ai skills (`rpg-engine` + templates), where the play model
autonomously orchestrated large multi-tool write-backs each scene/session. That was
correct but expensive. Here the work is **discrete, backend-owned, single-purpose
LLM calls** (the `briefs` pattern) plus **deterministic assembly**, with a
**human-in-the-loop diff review** as the correctness gate instead of an agent loop.

## Descent from the proven loop

The claude.ai system split history into three tiers — a per-scene **fact record**
(never compacted), per-session **interpretation** summaries (compacted past 15), and
a capped campaign **narrative** overview — and did a heavy end-of-session write-back
across many state files (timeline, plot roadmap, relationship web, knowledge tracker,
themes, per-character overlays). We keep the *fact record* and the *state write-back*
but collapse the session tier: **the scene is the only unit** (decision below), and
the write-back is small-and-frequent (one scene) rather than large-and-batched (one
session). The play model never orchestrates it; the backend does, one call at a time.

## Decisions (the locked forks)

1. **Agency — auto, then review diffs.** End Scene fires discrete backend LLM calls
   that *propose* a summary and state edits; nothing touches the campaign copies
   until the user approves a diff. No agent loop.
2. **Granularity — the scene is the only unit.** No "session" record. Ending a scene
   does both the summary and the full state write-back, in one review pass. A scene,
   once ended, is marked done and is **re-absorbable** if extended later (its record
   is rewritten, not duplicated).
3. **Write-back surface** — scene summary, character/lore/location evolution,
   relationships (char↔char incl. NPC↔NPC), knowledge (who-knows-what), plot threads,
   and an event timeline.
4. **Mutation model — dedicated campaign state fields.** Evolution accrues in a
   campaign-side state artifact per record; the authored fields
   (`description`/`personality`, lore body) change **only rarely**, and when they do
   it is a deliberately-flagged item in the review.
5. **Read-forward — auto recap in context + a suggested-next-scenes helper.** The
   context builder injects continuity deterministically (no LLM at scene start); a
   separate optional one-call helper proposes openings. No prior-scene prefill.
6. **Extraction — one deterministic-primed call** (approach "A refined by C"): the
   backend pre-fills every fact it already holds (scene id, present cast, location,
   datetime) so the single call produces only judgment.
7. **Relationships — feelings asymmetric and active; bonds symmetric-by-construction.**

## The extraction call (End Scene)

One call, owned by the backend (route layer, as with `briefs`). Input:

- the scene transcript (parsed messages);
- a **compact state snapshot** the backend assembles deterministically: present
  cast (`appearances.scene_cast`), current location + datetime (the existing
  histories), each present character's current `state.md`, the feelings/bonds among
  present cast, and the open plot threads.

Output — **one JSON object** (schema-validated, retried on mismatch, like the
structured extraction elsewhere):

```jsonc
{
  "one_line": "…",                 // single-sentence scene summary
  "summary": "…",                  // self-contained paragraph, readable w/o transcript
  "keywords": ["…"],
  "timeline_events": [{"date": "…", "text": "…"}],
  "knowledge_changes": [{"character": "seraphine", "learned": "…"}],
  "relationship_deltas": [         // DIRECTED; only edges the scene actually moved
    {"from": "elara", "to": "seraphine",
     "trust_delta": 1, "affection_delta": 0, "tension_delta": -1, "note": "…"}],
  "bond_changes": [{"pair": ["elara", "seraphine"], "type": "reluctant allies"}],
  "plot_movements": [{"id": "the-map", "status": "advanced", "title": "…", "beat": "…"}],
  "character_state_edits": [       // dedicated state fields (the common case)
    {"id": "seraphine", "current_state": "…", "knows": ["…"], "suspects": ["…"]}],
  "authored_edits": [              // RARE — edits to a card/lore field itself
    {"kind": "characters", "id": "seraphine", "field": "personality", "text": "…"}],
  "lore_edits": [{"id": "salt-cathedral", "append": "…"}]
}
```

The backend **materializes** this JSON into concrete diffs against the campaign
copies (Section "Diff review") — the model proposes deltas, the backend computes the
resulting record and the visual diff. Facts already known (cast, location, date,
scene id) are **not** asked of the model; they are attached from the store.

## Storage (grimoire conventions: flat→frontmatter, nested→JSON sidecar, prose→markdown)

| Artifact | Location | Shape |
|---|---|---|
| Per-scene fact record | `<campaign>/chronicle.json` (append-only, keyed by scene id; never compacted) | `one_line`, `summary`, cast, location, date, `keywords`, `knowledge_changes`, `plot_movements` — the queryable spine + recap source |
| Scene's own summary | mirrored to the scene file frontmatter (`one_line`, `summary` scalars) + a `done` flag | cheap for the rail / list |
| Character play-state | `<campaign>/characters/<cid>/state.md` (campaign copy only; mirrors `brief.md`) | frontmatter/markdown: `current_state`, `knows`/`suspects`, `voice_drift` |
| Relationships | `<campaign>/relationships.json` | `bonds` (unordered pair → shared facts) + `feelings` (directed edge → metrics), see below |
| Plot threads | `<campaign>/plot.json` | `{id: {title, status: open/advanced/closed, beats: [...], last_scene}}` |
| Timeline | `<campaign>/timeline.md` | append-only dated lines; pairs with the scene calendar |

Authored record fields stay in the existing campaign copies. Rare `authored_edits`
and `lore_edits` write **into** those copies (through review), where the planned
campaign-vs-base diff view surfaces them via the `sync.md` base hashes. Everything
else accrues in the state artifacts above, which have **no base equivalent** and so
read as "added in campaign."

### Relationships model

```jsonc
{
  "bonds": {                       // one entry per UNORDERED pair (ids sorted, "a|b")
    "elara|seraphine": { "type": "reluctant allies", "since_scene": "s12" }
  },
  "feelings": {                    // one entry per DIRECTED edge; directions independent
    "seraphine->elara": { "trust": 4, "affection": 3, "tension": 1, "note": "…" },
    "elara->seraphine": { "trust": 1, "affection": 1, "tension": 3, "note": "…" }
  }
}
```

- **Feelings are asymmetric and active** — no enforced symmetry; a scene that moves
  only one direction produces only that one diff.
- **Bonds are symmetric by construction** — a canonicalizing helper sorts the pair
  key so the two orderings can't both exist. `type` is a shared fact.
- Metrics are small bounded integers (e.g. 0–5); the review edits them directly.

## Read-forward (how a new scene benefits)

**Deterministic recap injection** — two new labeled sections in `context._assemble`
(so they appear in the token inspector like every other section):

- **`# Story so far`** — always-on, **recency-bounded**: the last N scene
  `one_line`/`summary` entries from `chronicle.json` plus open `plot.json` threads.
  N is configurable (a `config` scalar, alongside `context_scan_depth`). Always-on so
  continuity never silently drops out on a keyword miss.
- **Present-character state** — for each in-scene character, their `state.md`
  `current_state` + relevant `knows`/`suspects` + their **outgoing** feelings toward
  *other present cast* and the shared bond. Attaches to the existing cast blocks.

Both are tolerant of missing/garbled artifacts (omit, never crash), exactly as the
`# Current setting` and `# Today` blocks already are.

**Suggested next scenes** — one optional LLM call at scene creation reading open
threads + long-absent cast + upcoming calendar events; proposes 3–4 openings. The
user picks one (seeds cast/location) or ignores it and starts blank / from a greeting
as today. Ephemeral (like `build_opener_messages`); nothing persisted by the call
itself.

## Backend modules

- **`store/chronicle.py`** (new) — `read_chronicle`, `absorb_scene(cid, sid, record)`
  (append/replace by scene id), `recent(cid, n)`, simple queries (by character /
  keyword / plotline) for continuity checks. Pure file IO.
- **`store/playstate.py`** (new) — `state.md` per character (read/write, mirrors
  `briefs.py`); `relationships.json` IO with the canonical-pair helper; `plot.json`
  IO; `timeline.md` append. Pure file IO + snapshot assembly for the extraction input.
- **`store/absorb.py`** (new) — the prompt builder + output schema + **diff
  materialization**: given the extraction JSON, compute concrete staged diffs against
  the campaign copies and state artifacts; apply on approval. Prompt/parse only; the
  LLM call lives in the route layer (the `briefs` split).
- **`store/context.py`** — add the `# Story so far` and present-character state
  sections to `_assemble` (labeled, substituted, tolerant).
- **`store/scenes.py`** — `end_scene`/`reopen` bookkeeping (`done` flag,
  `one_line`/`summary` frontmatter mirror). Model is **not** stored on the scene
  (see "Related change" below).
- **`store/config.py`** — recap depth scalar.

No new import cycles: `chronicle`/`playstate` depend only on `campaigns`/`paths`;
`context` may read them (it already reads `scenes`, `appearances`, `calendars`).

## Routes (`routes.py`)

- `POST /api/campaigns/{cid}/scenes/{sid}/absorb` → runs the extraction call, returns
  **staged diffs** (does not write). `409` on missing key (as chat/opener).
- `POST /api/campaigns/{cid}/scenes/{sid}/absorb/apply` `{approved: [...]}` → writes
  the approved subset to the store; returns the updated artifacts.
- `GET /api/campaigns/{cid}/chronicle` and continuity queries (as needed by the UI).
- `GET/PUT` for `relationships.json`, `plot.json`, and per-character `state.md` so the
  existing editors can hand-edit them.
- `POST /api/campaigns/{cid}/scenes/suggest` → the ephemeral suggested-next-scenes
  stream/call.
- Campaign-vs-base diff read for the world-copy view (Phase 5).

## Frontend

- **Diff review screen** — grouped, per-item approve/reject/edit: scene summary,
  timeline entries, character `current_state`/knowledge, relationship deltas (shown as
  directed `A→B trust 1→2`), plot movements, and **authored/lore edits flagged
  distinctly** as the deliberate ones. Reuses the record-list/detail conventions.
- **Recap surfacing** — `SceneInspector` gains a read-only "Story so far" + a
  per-present-character state panel (the injected sections, humanized).
- **Suggested next scenes** — a scene-creation option listing the proposed openings.
- **Campaign world view with diffs from base** — browse the campaign's copies of
  characters/lore/locations with divergence from the world base highlighted (via
  `sync.md` hashes); state artifacts show as campaign-only additions. (Phase 5.)
- `api/client.ts` — types + methods for all of the above.

## Testing

Backend (pytest, temp `GRIMOIRE_HOME`, fake OpenRouter):
- `chronicle` absorb/recent/replace-on-reabsorb round-trips; never compacts.
- `playstate`: `state.md` round-trip; **feelings asymmetry preserved** (moving one
  direction leaves the reverse untouched); **bond key canonicalization** (both
  orderings resolve to one entry); `plot.json`/`timeline.md` IO.
- `absorb` diff materialization: a sample extraction JSON produces the expected staged
  diffs; `apply` writes only the approved subset; authored/lore edits are flagged.
- `context`: `# Story so far` present, always-on, recency-bounded to N; present-cast
  state block shows a character's outgoing feelings toward present cast only; both
  omitted (no crash) when artifacts are missing/garbled.
- Routes: `absorb` returns diffs without writing; `apply` writes; `409` on missing key.

Frontend (vitest): review screen approves per-item and calls `apply` with the subset;
directed relationship diffs render; recap panels render; suggestions list renders.

## Out of scope (this design)

- A "session" tier, session summaries, and summary compaction (collapsed by decision 2).
- Automatic (unreviewed) write-back.
- Per-dimension extraction calls (single call + review is the chosen safety model).
- Reconciling the *played* plot threads (`plot.json`) with the *authored* greeting
  plot-map — they stay distinct for now.
- World→campaign re-sync after create (unchanged; copy-at-create only).

## Phase decomposition (each phase = its own spec + plan)

1. **Chronicle + recap spine.** `chronicle.py`, End Scene → single extraction for
   `one_line`/`summary`/`timeline`, `# Story so far` injection. Smallest end-to-end
   slice; delivers memory. Minimal review (summary only).
2. **Diff review + character/lore state.** `absorb.py` diff materialization, the
   review screen, `state.md` write-back + present-character state injection,
   `authored_edits`/`lore_edits` into campaign copies.
3. **Relationships + knowledge.** `relationships.json` (directed feelings + canonical
   bonds), knowledge fields, extraction + review + injection for present cast.
4. **Plot threads + suggested next scenes.** `plot.json`, the ephemeral suggestion
   call, scene-creation UI.
5. **Campaign-vs-base world view.** Browse campaign copies with base-diff highlighting.

## Related change (noted, not part of this design)

Model is an **ambient setting**, not scene data — it can change mid-scene and need not
be recorded as having changed. `create_scene` currently stamps `model` into scene
frontmatter and `list_scenes` surfaces it. This should be pulled out of the scene
record (config/ambient) separately from this work.

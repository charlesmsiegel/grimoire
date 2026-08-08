# World content-population swarm — design

## Problem

Sixteen worlds under `~/.grimoire/worlds/` vary wildly in how fully they use
the app's record kinds. All have `characters/` and most have `lore/`, but
`locations`/`items`/`groups` are almost entirely unpopulated even in
worlds with deep character rosters and lore (realm,
foggy-city, port-haven, guildhall, shadow-council,
harvest-society all have zero location/item/group entries). Several
worlds also have no `greetings/` at all, despite characters carrying
`first_mes`/`alternate_greetings` on their cards that have never been
imported. There's no tag vocabulary built out anywhere beyond a couple of
stray entries in `saltmarch/tags.md`.

The content to populate all of this already exists, scattered across
character bios and existing lore prose — it just hasn't been extracted
into first-class records the app's other features (world-info triggers,
lore-owner gating, greeting availability, tag-gated greetings) can use.

## Goals

For each of the 16 worlds:

1. Extract `locations`/`items`/`groups`/`lore` entries that are evidenced
   in existing character/lore text but not yet recorded as their own
   entities.
2. Import greeting-worthy content from character cards
   (`first_mes`/`alternate_greetings`) into world-level `greetings/`, and
   link greetings that are evidently part of the same story into
   plot-map sequences (`leads_to`, multi-character `present` casts).
3. Build each world's tag vocabulary (`tags.md`) from recurring identity
   categories evident in the cast, and attach `requires_tags` to the
   greetings where those tags are the natural gate.
4. Surface anything ambiguous — new-entity judgment calls, greeting-chain
   gaps where a link is obviously missing — as a single consolidated
   report at the end, rather than blocking mid-run.

## Non-goals

- No new backend features or schema changes. Everything here uses
  existing record kinds and existing store functions
  (`store.entities`, `store.greetings`, `store.tags`) exactly as they
  are today.
- No PC tagging. Tags are built and attached to greetings; assigning
  them to existing PCs is left to the user.
- No invented content. Nothing gets created unless it's evidenced in
  existing character/lore text — this is extraction, not authoring.
  The one exception is explicitly logged rather than written: an
  "obvious sequence gap" in greeting chaining gets recorded in the gap
  report, never auto-bridged with new prose.
- No `creatures` records except in worlds where non-humanoid
  species/monsters are actually first-class content (the fantasy
  worlds — arcane-academy, realm, guildhall).
- No world lock / new locking primitive. World-level writes take no
  app-level lock today (only `store.atomic` file-level atomicity); this
  tool follows that same convention rather than introducing one.

## Architecture

Per-world pipeline, run independently across all 16 worlds (no barrier
between worlds — each finishes on its own schedule):

```
propose (batched)  →  merge/dedupe (1 agent/world)  →  apply (deterministic script)  →  verify  →  report
```

### 1. Propose

One or more Sonnet agents per world read a batch of that world's
`characters/*/character.md` + card JSON, existing `lore/*.md`, existing
`greetings/` (if any), and `world.md`. Large worlds are split into
multiple character-batches so no single agent has to read the whole
corpus in one context window (foggy-city: 327
characters/1474 lore; port-haven: 227/188 — both need several batches;
small worlds like arcane-academy or critter-tamers fit in one batch). Each batch
agent emits structured JSON:

- `candidate_entities`: `[{kind, name, body, keys, owners?, fields?}]`
  for `locations`/`items`/`groups`/`lore`
- `candidate_tags`: `[{id, display_name, rationale}]`
- `greeting_candidates`: `[{character, version}]` — cards worth
  importing
- `open_questions`: `[string]` — anything ambiguous worth a human call

Classification guidance given to every propose agent:

- **locations** — physical places (buildings, districts, rooms,
  cities). Set `climate`/`weather_zone`/`persistence` only where
  genuinely meaningful.
- **groups** — organizations, factions, cliques, classes/homerooms,
  teams, families-as-institutions ("Maron Guild", "Class 1-A",
  "Larkspur", "Teachers"). `group_type` gets a short label.
- **items** — physical objects/artifacts a bio treats as narratively
  significant — not every prop mentioned in passing.
- **lore** — background facts/history/culture/events that don't fit as
  a place/group/item. Set `owners` (character/PC/location refs) so it
  only surfaces via the existing lore-gating mechanism when a relevant
  party is present in a scene.
- **creatures** — fantasy worlds only, and only for real recurring
  species/monsters, not one-off flavor text.

### 2. Merge / dedupe

One agent per world reads only that world's candidate list (small,
cheap — not the source text again) and:

- Dedupes by meaning, not just string match ("Ashford High" /
  "Ashford High School" / "the school" → one location).
- Cross-checks candidate names against what's already in the store for
  that world (existing `locations`/`items`/`groups`/`lore`) so nothing
  gets recreated.
- Looks across the imported greeting texts for evidence (shared events,
  shared locations, one character's greeting naming another) that two
  or more greetings belong in a sequence, and proposes plot-map edges
  (`leads_to`) and multi-character `present` casts. Links require
  textual evidence in the greetings themselves — no inferring
  relationships that aren't actually on the page.
- Where a sequence is obviously missing a link (character A's greeting
  clearly follows an event in character B's greeting, but nothing
  bridges them), logs a `greeting_gap` entry instead of writing
  anything.
- Identifies recurring identity-tag categories evident across the cast
  (e.g. Port Haven: "Ashford Student", "Larkspur Member", "\<X\>'s
  Father") and proposes which of the world's existing/new greetings
  should carry which tag in `requires_tags`.

Output: one merged JSON per world — the exact set of writes to perform,
plus that world's `open_questions`/`greeting_gaps`.

### 3. Apply

A single deterministic Python script, modeled directly on
`backend/scripts/ingest_scene.py`'s existing pattern: imports
`grimoire.store.entities`/`greetings`/`tags` in-process (no
`GRIMOIRE_HOME` override, so it lands in the real `~/.grimoire`) and
calls `create_entity`, `import_from_character`, `set_edges`, `add_tag`,
`update_greeting` directly. No HTTP server, no new backend routes, no
auth concerns — this is the same write path (`store.atomic`, id
slugification) the app itself uses, just driven from a script instead
of the UI. The script takes one world's merged JSON as input and
performs no LLM reasoning of its own — it's pure "take this decided
list and write it."

### 4. Verify

Read back what was written (via the same store read functions) and
confirm every file parses and no name collisions were introduced.

### 5. Report

Every world's `open_questions` and `greeting_gaps` are collected into
one consolidated report at the end, presented to the user — not a
mid-run blocking prompt (a background swarm can't literally pause for a
chat turn per agent; this is the practical equivalent of "ask when in
doubt").

## Safety / preconditions

- `~/.grimoire` is now a local git repo with a baseline commit
  (`e6b8303`, 16,416 files) taken before this work starts — the revert
  point if anything goes wrong.
- The apply step must not run while the app's dev server has the same
  worlds open for editing (no in-app changes should be in flight during
  a given world's apply step) — world-level writes take no app lock
  today, so this is a stated precondition rather than an enforced one.
- Scale: no token/agent budget cap ("go big" per user). Batch counts
  scale to each world's actual character/lore volume rather than being
  capped in advance — this will run well past a typical
  15-agent-workflow guideline (likely 100+ agent calls across all 16
  worlds) and that's expected.

## Open items for the implementation plan

- Exact JSON schemas for propose/merge agent structured output.
- How the workflow script chunks large worlds into character-batches
  (batch size heuristic — e.g. by character count or estimated token
  size).
- Where the apply script lives (throwaway under scratch vs. a small
  committed tool under `backend/scripts/`, following
  `ingest_scene.py`'s precedent) and whether it needs any test
  coverage given it's a one-time bulk-population tool rather than a
  shipped feature.
- Format of the final consolidated report (markdown file, artifact, or
  plain chat summary).

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

**This spec went through a Codex adversarial pass against the design**
(not just the prose), which found real issues, plus one claim that
turned out to be wrong when checked against this store's actual
character cards:

- Codex asserted `alternate_greetings` are always "mutually-exclusive
  alternate openings," citing SillyTavern convention, and the first
  draft of this spec took that as a blanket rule ("never chain a
  character's own alternates"). Sampling real cards shows that's not
  true here: `realm/adriana` (9 alternates) is a clear
  life-stage progression — guild induction → lost in the city →
  injury → married domesticity → pregnancy → a breakdown/memory-loss
  scene. `port-haven/abigail` (21 alternates) is the opposite case —
  mostly independent occasion-vignettes (three separate Halloween
  scenes, a Christmas one, a laundry-mishap gag), though one alt
  explicitly presupposes established history with `{{user}}` from
  another. **The reality is mixed, not categorical in either
  direction.** The fix: same-character alternates get judged by the
  *exact same* evidence standard as cross-character pairs (see Merge,
  below) — chain on explicit textual/chronological evidence, no
  default assumption either way, whether the pair is one character's
  alternates or two different characters' cards.
- `store.greetings.set_edges` and `update_greeting`'s `present`/
  `requires_tags` **replace the field wholesale** — confirmed by reading
  `store/greetings.py:205-214,146-167` directly. Several worlds already
  have hand-authored `plotmap.json` content (saltmarch, lockdown-crew,
  midnight-lounge, sunken-grove, sandbox-test,
  shadow-council, harvest-society). A naive apply step calling
  these with only the newly-proposed values would silently delete
  existing edges/gating. Same problem, confirmed by reading the code,
  for `store.tags.add_tag` (`tags.py:36-41`, no dedup by display name —
  calling it twice with "Ashford Student" creates two separate tag
  ids) and `store.greetings.import_from_character` (no check for an
  already-imported character/version — re-running it duplicates
  greetings).

Everything below already has these fixes folded in. Lesson for the
propose/merge agent prompts themselves: don't assume a uniform pattern
across a whole world's cast either — some characters' alternates will
be a real sequence, others a grab-bag of vignettes, and the only way to
tell is the text of each pair, not a rule of thumb.

## Goals

For each of the 16 worlds:

1. Extract `locations`/`items`/`groups`/`lore`/(fantasy-world `creatures`)
   entries that are evidenced in existing character/lore text but not yet
   recorded as their own entities.
2. Import greeting-worthy content from character cards
   (`first_mes`/`alternate_greetings`) into world-level `greetings/`, and
   link greetings that are evidently part of the same story — whether
   that's two different characters' cards or one character's own
   alternates — into plot-map sequences (`leads_to`, multi-character
   `present` casts), based on textual evidence, not on which card they
   came from.
3. Build each world's tag vocabulary (`tags.md`) from recurring identity
   categories evident in the cast, and attach `requires_tags` to the
   greetings where those tags are the natural gate.
4. Surface anything ambiguous — new-entity judgment calls, greeting-chain
   gaps where a link is obviously missing, source conflicts — in a
   report that accumulates incrementally per world, rather than
   blocking mid-run or risking loss on an interruption.

## Non-goals

- No new backend features or schema changes. Everything here uses
  existing record kinds and existing store functions
  (`store.entities`, `store.greetings`, `store.tags`) exactly as they
  are today — including their existing, sometimes-surprising semantics
  (see "Apply" below for how the design works around the replace-not-
  merge behavior rather than changing it).
- No PC tagging. Tags are built and attached to greetings; assigning
  them to existing PCs is left to the user. **Consequence, stated
  explicitly rather than left implicit**: a greeting that gains
  `requires_tags` it didn't have before becomes unreachable by any PC
  until that PC is manually tagged to match. This is an intentional,
  deferred manual step — the final report enumerates exactly which
  greetings got newly gated and by which tags, so it's a known
  follow-up, not a silent trap.
- No invented content. Nothing gets created unless it's evidenced in
  existing character/lore text — this is extraction, not authoring.
  Every candidate carries a source citation (see Propose, below) so
  "evidenced" is checkable, not just asserted. An "obvious sequence
  gap" in greeting chaining gets recorded in the gap report, never
  auto-bridged with new prose. Anything the merge stage isn't
  confident about — a possible duplicate, a shaky chain link, a source
  conflict — is excluded from the write manifest and logged instead;
  nothing in `open_questions`/`gaps` is ever written to the store.
- No `creatures` records except in worlds where non-humanoid
  species/monsters are actually first-class content (the fantasy
  worlds — arcane-academy, realm, guildhall). This is a
  propose-agent judgment rule, not a schema restriction — `creatures`
  is a normal member of the candidate-entity kind enum for every world.
- No world lock / new locking primitive. World-level writes take no
  app-level lock today (only `store.atomic` file-level atomicity); this
  tool follows that same convention. Concurrency safety instead comes
  from the git-backed precondition check described under Safety below.

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

- `candidate_entities`: `[{kind, name, body, keys, owners?, fields?,
  source}]` for `locations`/`items`/`groups`/`lore`/`creatures`, where
  `kind` includes `creatures` (restricted to fantasy worlds by the
  classification rule below, not by the schema).
- `candidate_tags`: `[{display_name, rationale, source}]`
- `greeting_candidates`: `[{character, version}]` — cards worth
  importing
- `open_questions`: `[string]` — anything ambiguous worth a human call

**`source` is required on every candidate**: the file path(s) it was
drawn from plus a short quoted excerpt. This is what makes "no invented
content" checkable rather than merely asserted, and it's what lets the
merge stage make dedupe/conflict/ownership calls without re-reading the
full corpus itself (see Merge). A candidate with no traceable source
excerpt doesn't get proposed.

**Corpus text is untrusted input**: character bios and lore are
player-authored roleplay content and may contain text that reads like
an instruction. Propose agents treat all of it strictly as data to
extract facts from — never as something to obey — and are told this
explicitly in-prompt.

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
  a place/group/item. `owners` is set **only when the fact is
  genuinely private/character-specific knowledge** (a secret, a
  personal history) — general world history/geography/culture is left
  unowned so it stays globally available, per the existing lore-gating
  mechanism. Defaulting to "owned" would silently make broadly-relevant
  lore invisible in most scenes; the propose agent must justify an
  `owners` value, not apply it reflexively.
- **creatures** — fantasy worlds only (arcane-academy, realm,
  guildhall by default; a propose agent may flag a genuine exception in
  `open_questions`), and only for real recurring species/monsters, not
  one-off flavor text.

### 2. Merge / dedupe

One agent per world reads that world's candidate list plus a **compact
existing-store index** for that world — name + kind + one-line summary
for every existing location/item/group/lore/creature/tag, cheap to
include and what lets merge catch duplicates/cross-references without
re-reading full source text or missing things a different propose batch
already covered. It does not re-read the raw character/lore corpus.

- **Dedupes conservatively.** Two candidates merge only when their
  `source` excerpts clearly describe the same thing ("Ashford High" /
  "Ashford High School" / "the school" said in the same context). When
  not confident, it does **not** merge — keeping two records that turn
  out to be the same thing is a cheap fix later; merging two that turn
  out to be different is a much harder mistake to notice. Uncertain
  cases go to `open_questions` instead.
- **Cross-checks against the existing-store index** so nothing gets
  recreated.
- **Chains any pair of greetings only on an explicit chronological/
  causal marker in the text** — not just shared topic or a name
  mentioned in passing — regardless of whether the pair is one
  character's own alternates or two different characters' cards. Real
  cards in this store contain both patterns (see the note at the top of
  this doc: `realm/adriana`'s alternates are a genuine
  life-stage sequence; `port-haven/abigail`'s are mostly independent
  occasion-vignettes with one exception that presupposes shared
  history) — there's no shortcut that avoids reading each candidate
  pair's actual text. `present` is set only for characters who are
  actually in the scene (speaking, acting, physically there) — being
  *named* in a greeting's text is not enough on its own.
- **Respects plot-graph shape**: new `leads_to` edges must not create a
  cycle, and must be unioned with — never replace — whatever edges
  already exist for that greeting (see Apply, which is where the
  read-before-write actually happens; merge just needs to know it's
  proposing an addition, not a full replacement).
- **Logs gaps instead of writing them**: where a sequence is obviously
  missing a link (character A's greeting clearly follows an event in
  character B's greeting, but nothing bridges them), that's a
  `greeting_gap` entry, never new prose.
- **Tags**: identifies recurring identity categories evident across the
  cast (e.g. Port Haven: "Ashford Student", "Larkspur Member", "\<X\>'s
  Father") and proposes `requires_tags` for the greetings where each is
  the natural gate. A tag is only proposed if at least one greeting in
  the same batch will actually reference it — no vocabulary entries
  created "for completeness" with nothing gating on them.
- **Source conflicts**: when two excerpts disagree about a fact, merge
  doesn't silently pick one — it flags the conflict in
  `open_questions` and either keeps the more specific/better-evidenced
  version or omits the disputed detail.

Output: one merged JSON per world — the exact set of writes to perform
(every write already checked for existing-store collisions, so Apply
can stay dumb) — plus that world's `open_questions`/`greeting_gaps`.

### 3. Apply

A single deterministic Python script, modeled directly on
`backend/scripts/ingest_scene.py`'s existing pattern: imports
`grimoire.store.entities`/`greetings`/`tags` in-process (no
`GRIMOIRE_HOME` override, so it lands in the real `~/.grimoire`). No
HTTP server, no new backend routes, no auth concerns. It performs no
LLM reasoning — it takes one world's merged JSON and writes exactly
what's in it — but it is **not a blind pass-through of store calls**,
because the store functions it calls have replace and no-dedup
semantics that would corrupt or duplicate existing data if called
naively. Confirmed requirements, each traced to the store code read
during design review:

- **Tags**: before calling `store.tags.add_tag`, read the world's
  current vocabulary (`read_tags`) and reuse an existing tag id if its
  display name already matches (case-insensitive) — `add_tag` has no
  such check itself and will silently create a second id for the same
  name.
- **Greeting import**: before calling `import_from_character` for a
  character/version, check whether it's already been imported this run
  (see per-world manifest, under Verify/Report) — re-import creates
  duplicate greeting files, since `create_greeting` just uniquifies the
  slug rather than detecting "this already exists."
- **Plot-map edges**: before calling `set_edges`, read the greeting's
  current edges (`edges_of`) and pass the **union** of existing +
  proposed `leads_to`/`excludes`, never just the proposed set — `set_edges`
  overwrites wholesale.
- **`present`/`requires_tags` on an existing greeting**: same
  read-then-union discipline via `update_greeting` — never pass only
  the new value when the greeting already has one.
- **Idempotent re-runs**: because of the above, re-running apply against
  the same merged JSON a second time should be a no-op (skips
  already-created entities/tags/greetings it detects are already
  present), which the apply script's test coverage (below) verifies
  directly rather than assuming.

### 4. Verify

Reads back what was written and checks more than parse success:

- Every created file parses and no entity-name collision was
  introduced.
- **Referential integrity**: every `owners` ref points to a real
  character/PC/location, every `present`/edge id points to a real
  greeting, every `requires_tags` id exists in that world's vocabulary.
- **No unintended change**: fields outside what the manifest asked to
  change are diffed against the pre-apply snapshot and confirmed
  untouched (this is what actually proves the read-then-union apply
  logic worked, rather than trusting it did).

### 5. Report

Rather than accumulating everything in memory for a single end-of-run
report (which would lose results for every already-finished world if a
later world's run fails), each world writes/updates its own status
entry — state (`proposed`/`merged`/`applied`/`verified`/`failed`) plus
its `open_questions`/`greeting_gaps` — as it completes each stage. The
final report is a consolidation read of these per-world entries, not
the only copy of the information.

## Safety / preconditions

- `~/.grimoire` is now a local git repo with a baseline commit
  (`e6b8303`, 16,416 files) taken before this work starts.
- **Per-world commit checkpoints**: immediately before applying a given
  world, the apply script checks `git status --short` for that world's
  path is clean — if it isn't (something changed since the last
  checkpoint, e.g. the user editing in the app concurrently), that
  world is skipped and flagged rather than written over unknown state.
  After a world's apply+verify succeeds, the script commits that
  world's changes. This gives real rollback granularity (already-
  committed worlds survive even if a later world's run fails) and a
  practical concurrency guard, without introducing a new locking
  primitive — it reuses the git repo already set up for this project.
- The apply step still shouldn't run while the app's dev server has the
  same world open for editing; the git-dirty check above is the backstop
  if that's violated, not a substitute for just not doing it.
- **Apply script test coverage is required, not optional**, given it
  writes real user data through store functions confirmed above to have
  replace/no-dedup semantics: at minimum, a test (isolated via
  `GRIMOIRE_HOME` pointed at a tmp fixture store, per the existing
  backend test convention) proving existing greetings/edges/tags survive
  unchanged when new ones are added alongside them, and that applying
  the same manifest twice doesn't duplicate anything.
- Scale: no token/agent budget cap ("go big" per user). Batch counts
  scale to each world's actual character/lore volume rather than being
  capped in advance — this will run well past a typical
  15-agent-workflow guideline (likely 100+ agent calls across all 16
  worlds) and that's expected.

## Open items for the implementation plan

- Exact JSON schemas for propose/merge agent structured output
  (including the `source` provenance field shape).
- How the workflow script chunks large worlds into character-batches
  (batch size heuristic — e.g. by character count or estimated token
  size).
- Where the apply script lives (throwaway under scratch vs. a small
  committed tool under `backend/scripts/`, following
  `ingest_scene.py`'s precedent) and the concrete shape of its test
  suite.
- Format of the final consolidated report (markdown file, artifact, or
  plain chat summary) and where per-world manifest/status entries are
  persisted between stages.

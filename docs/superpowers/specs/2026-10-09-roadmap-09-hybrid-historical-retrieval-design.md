# 09. Hybrid historical retrieval

**Status:** Draft — spec gate (substitute review) folded in; Codex gate pending.
**Date:** 2026-10-09
**Roadmap:** 09 in `ROADMAP-CHECKLIST.md`. Lane: retrieval.
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** bundle draft `09-hybrid-historical-retrieval.md` (2026-10-06,
planning anchor `7f80c42`); extends the retrieval layers that landed since that
anchor (`store/context/archive.py`, `store/context/semantic.py`,
`store/semsearch.py`) and the packer contract of
`2026-10-05-lore-activation-controls-design.md` (section 6, `shed`). Relies on
`2026-10-09-roadmap-03-content-addressed-compiled-cache-design.md` section 2a
(items 6 and 7) for the live-set rule and the vector key.

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning. In particular re-read 07, 08, 01e, 01h
> and 01i as they landed: this spec states what it needs from each (section
> 13, "Depends on"), and a contract that landed differently changes the slice
> that uses it, not the rest.

## Depends on

Edges as `ROADMAP-CHECKLIST.md` lists them for 09.

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| 08-C1 bounded `SceneDocument` (no transcript text), keyed by per-scene slices | 08 | The unit every lexical and semantic signal scores, and its metadata (scene identity, date, location, cast) | Hard |
| 08-C2a lazy build plus batch lookup; 08-C2b document vectors under `space["space"]` and `record_embedded`; 08-C2c hot rebuild for 05 | 08 | The live set; loading and saving document vectors under the one shared key; recording what the turn warm embedded so 05 keeps it current | Hard |
| 08-C3a `history-index` for out-of-turn embedding only; 08-C3b `expand(...)`, where the caller names the phase, with prefixed post keys | 08 | The rule that a turn's warm is 09's `history-recall`, and turning a selected scene into bounded excerpts whose posts carry `r-<response_id>` / `p-<post_id>` keys and `part` (sections 6.3, 8) | Hard |
| 03-C6 batch lookup over a live key set, index ranking only within it | 03 | Looking up documents and any lexical index only for keys computed from the filesystem this turn (section 6.4) | Hard |
| 03-C7 vectors keyed by text, never `BUILD` | 03 | An upgrade costs embeddings only where document text moved | Hard |
| 01h-C4a no embedding on the event loop (a guard degrades); 01h-C4b native async `embed()` with `AsyncEmbeddingsClient` from `routes.get_embeddings` | 01h | The turn's one embed call (query plus warm) is async and cancellable (sections 6.3, 10) | Hard for the turn path |
| 01a-C1 eval cost, latency and token reporting | 01a | The long-history suite reports wall time, tokens and the three money columns per arm (section 12) | Hard for live evals; offline needs nothing |
| 07-C2 inverse membership | 07 | The `group` structural relation (section 5.2) | Soft: the relation is absent until it lands |
| 07-C3c retrieval projections (`scene_groups`, `co_affiliates`, `prompt_visible`) | 07 | Group seeds and the group relation read through 07's projections rather than re-deriving them | Soft |
| 01e-C1 `Rank` (with `pointwise` set); 01e-C2 `Answer.expected`, `tiers()` | 01e | The optional rerank stage (section 7.3) | Soft: rerank is off by default and its slice waits for 01e |
| 01h-C1 input type (`queries=` split); a query vector is compared within its space and never cached | 01h | The query is embedded as a query in its own request, compared against 08's document vectors read under the same space (section 6.3) | Soft: without it both sides embed as today |
| 01i-C1 `wire.Limits` on every target; 01i-C2 `prompt_ceiling(resolved)` | 01i | The section's ceiling is a share of `prompt_ceiling(resolved).tokens`, the required way to derive one (section 9.2) | Soft: with `tokens is None`, or before it lands, the configured absolute budget alone bounds the section |
| 02-C5b history relevance kit, the shared `history_check` route | 02 | The task and route the rerank resolves and meters under (section 7.3) | Soft |

## Required by

| Contract (provided here) | Consumer | Hard or soft | What the consumer uses it for |
|---|---|---|---|
| 09-C1 `history.retrieve(Query) -> Evidence`, `history.merge` | 10 | Hard | Running a planner's questions through the same retrieval, and merging rounds by scene identity |
| 09-C2 `Coverage` verdict and tiered widening | 10 | Hard | The free first gate that decides whether the planner runs at all |
| 09-C3 the `history_recall` section | 10 | Hard | Planned evidence renders through the same section; the plan's trace rides its row |
| 09-C1 (scene identity, post indices, post keys and post texts on each item; the `perspective` seam) | 11 | Hard | Classifying what an actor may know, per post, against the evidence 09 selected |
| 09-C3 | 11 | Hard | 11-C2 splits the section by perspective and lifts the NPC blanking |
| 09-C1 | 12 | Hard | `search_history(query, scope)` is a thin wrapper over `retrieve`, tier 3 explicit |
| 09-C4 the long-history suite and generator | 10, 11, 12 | Soft | Their evals extend the same corpora and arms |

## 1. Current state (reconciled against main)

The bundle draft was written as though Grimoire had no historical recall. It
has three layers today, and 09 has to sit beside all three without breaking
the promises each one makes.

**The recap** (`store/context/story.py:155`, `_story_entries`) renders the last
`recap_depth` chronicle one-liners (default 5, `store/config.py:28`) as the
`story_so_far` section, BACKGROUND tier (`store/context/assemble.py:975`). It
is recency only.

**The archive** (`store/context/archive.py:45`, `_archive_entries`) is keyword
recall over absorbed scenes. It matches the turn's scan window against each
chronicle record's `keywords` with `world_state.keyword_hit`, excludes the
recap window and every scene ordering at or after the one being played
(`archive.py:67`, the `before` rule: "a later scene is not earlier"), caps at
`archive_depth` (default 3, `store/config.py:31`), and renders summaries as
"Earlier scenes" at the ARCHIVE tier (`assemble.py:976`). It is **on by
default**, so every golden prompt already contains it where a keyword fires.
Its failure mode is the draft's motivating example: a scene that shares
actors with the present but none of the words recorded in `keywords` is never
recalled, and a chronicle record with no keywords is silent by design
(`archive.py:12`).

**Semantic recall** (`store/context/semantic.py`) scores world-info entries
the keyword rule missed by embedding similarity. It is lore, not history, but
it established the house rules this spec reuses rather than reinvents:

- It "can only add" (`semantic.py:14`), and renders into its own section at
  the RECALLED tier, the first to drop (`store/context/pack.py:92`,
  `DROP_ORDER` at `pack.py:100`), precisely so it cannot evict context the
  prompt already had.
- It fails to keyword-only, never to an error (`semantic.py:27`), with every
  request metered by `inference.embed.embed_sync` (`store/inference/embed.py:143`)
  under an `EMBED_TASKS` task (`store/routing.py:174`).
- One request per turn carries the query and a bounded warm run
  (`WARM_LIMIT`, `semantic.py:128`), sharing one deadline across a retry
  (`semantic.py:347`); only a response-shaped failure retries the query alone
  (`semantic.py:363`).
- Range and width checks on every score: a cosine outside `[-1, 1]` beyond
  `SCORE_SLACK` evicts the cached vector, and so does a dimension mismatch, so
  a stale hit heals on the next turn (`semantic.py:288`-`306`).
- Off by default and byte-identical when off (`semantic_recall_depth: 0`).

**Library search** (`store/search.py`, `store/semsearch.py`) is the reader's
Ctrl-F and its semantic twin. It walks the corpus per query, embeds transcript
*passages* (`semsearch.passages`, `PASSAGE_BYTES = 1500`) and ranks a record by
its closest passage. It is a reader-facing surface (it deliberately ignores
owner gating and secrecy, `semsearch.py` "It shows the reader everything"), so
09 cannot reuse its *results* for a prompt, only its arithmetic.

**Prompt assembly.** `_assemble` (`assemble.py:147`) gathers every section's
data in one pass under a *best-effort* campaign lock (`assemble.py:166`);
`_render_sections` (`assemble.py:1293`) renders `SECTIONS` (`assemble.py:918`)
through the reader's layout; `_prepare` (`assemble.py:1496`) packs one variant
per model-guidance profile. The packer (`pack.py:203`) drops whole sections
lowest tier first and largest first within a tier, trims the trailing history
between ARCHIVE and BACKGROUND, and lets exactly one section shed units:
World info, through a `shed` hook (`pack.py:305`, units ordered by `_shed_key`
at `pack.py:141`).

**Two rules from `CLAUDE.md` that bind this spec directly:**

- **Reasons never reach the prompt.** The `reason` dicts feed inspector rows
  and prompt-log captures only; `test_lore_shedding.py:615`
  (`test_reasons_never_reach_the_prompt`) holds it.
- **A store that sets none of the new fields composes byte-identical
  prompts.** `test_lore_golden.py` pins narrator prompts against a golden
  recorded before the activation engine. 09 adds a section; with history
  retrieval unset it must render nothing, read nothing it did not read before,
  and send nothing.

**Where composition runs, which decides where retrieval can run.** This is the
one finding that reshapes the draft:

- `post_chat` (`routes/scenes.py:816`) is a `def` handler: it appends the
  player's post, then calls `compose_turn` (`routes/scenes.py:1085`) on a
  threadpool worker, then starts the detached run.
- A group round composes inside `character_turns._prepare`
  (`routes/character_turns.py:476`), which holds **`campaign_lock(cid)`**
  (`character_turns.py:477`) for the whole compose and is reached through
  `run_in_threadpool` from the round's async driver
  (`character_turns.py:1044`).
- The opener composes "inside an async generator on the event loop"
  (`assemble.py:160`, `routes/greetings.py:103`).

So an embedding request made from inside `compose_turn`, the shape semantic
recall has today, holds a worker on one path, **holds the campaign lock across
a network call** on another, and **blocks the event loop** on the third. The
semantic module's own docstring names this cost (`semantic.py:70`-`82`). 09
does not repeat it: retrieval is a separate phase that runs *before* compose,
outside every lock, and hands compose a finished `Evidence` (section 10).

**What does not exist yet:** SearchDocuments (08), inverse membership (07),
`Rank` and fine `Score` (01e; `decisions.py:181`-`205` has `Predicate`,
`Choice` and `Score` only), embedding input types (01h-C1; `embed_sync` takes
no `input_type`), a non-trivial async embed path (01h-C4; `embed.embed` at
`store/inference/embed.py:226` is a `to_thread` wrapper with no callers), and
a context window on the resolved attempt (01i-C1).

## 2. Goal (and what is explicitly not the goal)

**Goal.** On an ordinary narrator turn of a long campaign, put a small,
bounded set of the *right* earlier evidence in front of the model: the scenes
most likely to matter to what was just said, each as a header and the
transcript lines that matter, inside a hard token ceiling, chosen by three
independent signals (structural, lexical, semantic) that each keep their own
score and their own reason.

Concretely:

1. A scene that shares the present cast but not the present words is
   reachable (the archive's blind spot).
2. A semantically generic line ("You knew the whole time, didn't you?") is
   resolved by structure first: who is speaking to whom, what they have been
   through together, which threads they share.
3. Embeddings are an enhancement. With no Embedding role, or during an
   outage, retrieval is structural plus lexical and the turn is unaffected.
4. Nothing superseded can surface: every lookup starts from keys computed
   from the files this turn (03 section 9).
5. Off is byte-identical, and on is purely additive: the new section is the
   first thing to give way, one scene at a time.
6. Every choice is visible in the inspector, and none of its reasoning
   reaches the prompt.

**Not the goal:**

- Replacing the recap or the archive. Both keep their sections, defaults and
  semantics. The history section adds excerpts and the scenes they missed; it
  never repeats text they already put in the prompt (section 9.4).
- A fused "relevance confidence". Signals are kept apart and an ordering rule
  merges them (section 7); no number is shown or thresholded as a
  probability.
- Retrieving inside the scene being played. The packer's history trim may
  drop early posts of a very long scene; recovering them is a separate
  problem (open question 6).
- Query planning (10), epistemic filtering (11) and investigation (12). 09
  leaves a seam for each and implements none.
- Post-level vectors. 08 section 11 defers them, and so does this spec.

## 3. Shape of the design

```text
             before compose, outside every lock, under one deadline
 +------------------------------------------------------------------------+
 | query.build(cid, sid, turn)          the turn window, seeds, terms     |
 |   -> structural.candidates(seeds)    files: appearances, chronicle,    |
 |                                      plot, commitments, rel. history,  |
 |                                      groups (07-C2)                    |
 |   -> live set (08-C1 keys, 03-C6)    SearchDocuments for scenes that   |
 |                                      precede this one                  |
 |   -> lexical.score(pool | live set)  pure python, per-doc term stats   |
 |   -> semantic.score(pool | live set) one `history-recall` call: query |
 |                                      + warm; then 08 `record_embedded`  |
 |   -> merge.order(...)                admission per signal, RRF order   |
 |   -> [rerank]                        optional decide, 01e Rank/Score   |
 |   -> coverage + widening             tier 1 -> tier 2 (09-C2)          |
 |   -> expand.excerpts (08-C3b)        bounded windows, prompt view      |
 |   -> budget.fit                      01i-C2 ceiling, per-scene units   |
 +------------------------------------------------------------------------+
                                   | Evidence (frozen, JSON-safe detail)
                                   v
 compose_turn(..., history=Evidence) -> "Recalled history" section
                                        (HISTORY_RECALL tier, shed per scene)
                                        -> pack -> send
```

Store side: a new package `backend/src/grimoire/store/history/`, pure of
inference resolution (the house split: the LLM call lives in the route layer,
`chronicle.py`'s module docstring). Route side: `routes/history_recall.py`
owns the phase on the turn path, resolves the optional rerank, and is the one
place a turn calls into the package.

| Module | Role |
|---|---|
| `store/history/model.py` | The frozen dataclasses of 09-C1 (`Query`, `Signals`, `Candidate`, `Excerpt`, `EvidenceItem`, `Coverage`, `Evidence`) and their `detail()` JSON projections |
| `store/history/settings.py` | The four config keys and their parsing; `None` when off, the way `semantic.settings()` is |
| `store/history/query.py` | Builds the turn query: window text, seeds, terms |
| `store/history/structural.py` | Structural candidates and their relations |
| `store/history/lexical.py` | Term extraction and BM25-style scoring over a live set |
| `store/history/semantic.py` | Query embedding (01h-C4b), the document warm through 08-C2b, cosine over a live set |
| `store/history/merge.py` | Admission, ordering, `merge(Evidence, Evidence)` for 10 |
| `store/history/coverage.py` | The deterministic sufficiency verdict and widening decision |
| `store/history/expand.py` | Excerpts through 08-C3b, through the regex prompt view |
| `store/history/budget.py` | The ceiling (01i-C2) and the fit |
| `store/history/retrieve.py` | `retrieve()`, the one async entry, composing the above |
| `store/context/history.py` | The section's data, render and `shed` hook (context side) |
| `routes/history_recall.py` | `gather()` on the turn path (deadline, resolution in a worker, rerank) and `gather_sync()`, the bridge for `def` handlers |

Imports follow `test_import_guard.py`: module scope, acyclic, submodules
bound as module objects (`from ..continuity import involvement`, then
`involvement.scene_actors(...)`).

## 4. The query

`query.build(cid, sid, *, seed: str = "", actor_ref: str | None = None) -> Query`
reads, through the regex prompt view (`store.regex.view.view(..., phase="prompt")`),
the same tail `_assemble` scans (`assemble.py:355`): the last `scan_depth`
visible posts, hidden posts and director notes dropped exactly as
`story._project_history` drops them (`story.py:57`-`62`), plus `seed` (a
director note's text or an opener prompt, the `wi_seed` rule at
`assemble.py:356`).

It produces:

- **`texts`**: one string, the window clipped to its last `QUERY_BYTES`
  (3000, `semantic.py:107`'s bound and reasoning: the recent end is what the
  reply answers). 10 appends its rewritten questions here.
- **`terms`**: distinctive tokens and phrases for the lexical signal (section
  6.2): proper names found in the window that resolve to a known actor,
  location, item, group or thread title (weighted), quoted phrases, and the
  remaining non-stopword tokens. **A name that resolves to an excluded or
  gm-only ref is removed from the terms entirely**, weighted or plain, so the
  reader's exclude cannot be walked around lexically (section 5.1's rule, one
  signal over).
- **`subjects`**: the structural seeds, as refs (section 5.1).
- **`turn_index`**: the transcript index of the post being answered, carried
  to the rerank's ledger rows (`decide(post=...)`, `inference.py:684`). The
  embed doors take no `post=` today (`embed.py:143`, 01h-C4b's signature), so
  the embed rows carry campaign and scene only until 01h adds `post`
  (open question 9, routed to 01h).

A `Query` is also constructible directly (10, 12, the evals): `texts`,
`subjects`, `terms`, `scopes`, `max_scenes`, `perspective` and `tier_limit`
are its public fields (section 13, 09-C1).

## 5. Structural candidates

Structure answers "which earlier scenes are *about* the people and things in
front of us", with no model and no index.

### 5.1 Seeds

From the scene being played, read with the failure policy of the context
builder (a garbled file costs its signal, never the turn):

| Seed | Source | Ref |
|---|---|---|
| Present cast | `appearances.cast.scene_cast(cid, sid)` minus `pins.active` excludes (`assemble.py:200`-`201`) | `characters:<id>`, `pcs:<id>` |
| Speaker and addressee | The latest post's `speaker` stamp, and actor names found in its text | same |
| Mentioned actors | Roster names and aliases (`store/actor_names.py`) matched whole-word in the window | same |
| Current location | `scenes.read.get_location_history(cid, sid)[-1]` unless excluded | `locations:<id>` |
| Mentioned records | Location, item and group names matched whole-word in the window, through the overlay | `<kind>:<id>` |
| Live threads and commitments | `continuity.effective.threads/commitments` rows whose title terms occur in the window, or whose involvement includes a present actor (`involvement.of`) | `thread:<id>`, `commitment:<id>` |
| Groups | 07-C2 `groups_for(ref)` for each present actor | `groups:<id>` |
| Planner subjects | `Query.subjects` from 10, validated against the same catalogs | any of the above |

An excluded ref (`pins.active(...)["excluded"]`) seeds nothing: the reader's
"keep this out of the prompt" must not become "and go and fetch everything
about it". A gm-only record seeds nothing either, the activation engine's rule
for presence (`activation.py` module docstring, "A gm-only or excluded record
confers no presence").

### 5.2 Relations

Each relation turns seeds into candidate scenes and records *why*:

| Relation | How | Reads |
|---|---|---|
| `shared_cast` | Scenes where two or more present actors (or the speaker and the addressee) both stood | `involvement.scene_actors(cid)` (`store/continuity/involvement.py:101`): appearances unioned with the chronicle's cast snapshots |
| `actor` | Scenes where a mentioned, non-present actor stood | same |
| `location` | Scenes set at the current or a mentioned location | the chronicle's `location`, then the scene head's location history |
| `thread` | Scenes a seeded thread or commitment touched, over its merged alias group | `involvement.group_touched(cid, kind)` (`involvement.py:61`) |
| `relationship` | Scenes that moved a feeling or bond between two present actors | `relationship_history.for_pair(cid, a, b)` (`store/relationship_history.py:153`), rows without `scene_gone` |
| `group` | Scenes where two or more members of a seeded group stood; a group's leader counts as a member (07's recommendation) | 07-C2 `groups_for` and 07-C3c's projections, plus `scene_actors` |

Deferred from the draft, each for a stated reason (review M9):

- **Event and time anchors**: `events.json` records no scene
  (`store/events.py` docstring), so tying an event to scenes would mean
  matching in-fiction dates, which is 11's or a later spec's question.
- **Reviewed continuity links** (`continuity.json` links between threads and
  commitments): reachable today through the `thread` relation over each
  record's merged group; a link-walk relation waits for evidence from the
  eval suite that it adds recall.
- **"Recent scenes"** as a relation: recency is already the recap's job and
  every list's tie-break; as a relation it would admit by recency alone,
  which `STRUCTURAL_PLAIN_CAP` exists to stop.
- **Lexical shingles**: whole-phrase matching of quoted phrases and resolved
  names covers the multi-word case; shingles wait for the eval suite.

### 5.3 Eligibility, applied to every signal

A scene is a candidate for *any* signal only if all of these hold, checked
once in `retrieve` against the live listing (`scenes.read.list_scenes`):

1. It orders strictly before the scene being played, by the archive's exact
   rule: a plain string comparison of scene ids (`archive.py:67`), not
   `parse_sid` ordinals, which return `None` for legacy date-form ids
   (`store/scene_ids.py:15`-`27`). Using the same comparison keeps the two
   layers agreeing on what "earlier" means in a store that mixes id forms.
2. It is not in the current scene's branch group (`scenes/read.py:87`-`112`): a
   sibling is an alternative to the present, not its past.
3. It is not closed by an absorbed sibling (`closed_by`, `scenes/read.py:115`):
   an abandoned branch did not happen.
4. 08-C2a returned a `LiveDocument` for it. `scene_documents` builds every
   miss on each call (08 section 6, step 5), so every live scene has a
   document; what may be missing is its *vector* (section 6.5).

**Ref forms.** 08 stores locations as `locations/<id>` (08 section 7) while
seeds and relations here use `locations:<id>`. `store/history/refs.norm`
converts every ref to the `<kind>:<id>` form once, at the boundary with 08,
and every join in this package compares normalised refs only.

### 5.4 Structural rank

There is no structural score. A candidate carries
`StructuralSignal(relations: tuple[Relation, ...])`, each relation naming its
kind and the refs that produced it. The structural ranked list orders by:

1. the number of distinct relation kinds (a scene that shares cast *and*
   moved their relationship beats one that only shares cast);
2. the number of distinct seed refs involved;
3. newest scene first.

**Admission.** `shared_cast` alone, with only present actors, admits a scene
only when fewer than `STRUCTURAL_PLAIN_CAP` (12) scenes qualify that way. Two
companions who travel together share every scene, so co-presence alone is a
recency list in disguise; above the cap it ranks but does not admit (section
7.1). Every other relation admits on its own.

**Pool.** Tier 1's pool is the top `POOL_MAX` structural candidates plus every
lexical hit (section 6.2). `POOL_MAX` (64) bounds the semantic work of tier 1
to one `vectors.load` of 64 records: 64 file opens, and by
`store/vectors.py`'s own measurement (about 97 ms per 500 vectors of
dimension 1536, load plus score) roughly 12 ms on a desktop. Both constants
are tuned later against the eval suite.

## 6. Lexical and semantic signals

Both score SearchDocuments (08-C1) and nothing else: never raw transcripts,
never the chronicle directly. A SearchDocument is bounded by 08, so their cost
is bounded by the number of documents scored.

### 6.1 The live set

`retrieve` lists the eligible scenes (section 5.3), asks 08 for each one's
current SearchDocument key (08-C1: the input digest plus
`search_document_version`, computed from the files this turn), and looks them
up as one batch over that key set (03-C6). Nothing enumerates the compiled
cache to learn what exists, so a superseded document has no key that could
reach it (03 section 9). That is the draft's acceptance criterion 4, held by
construction rather than by a sync step.

### 6.2 Lexical

`lexical.score(terms, docs) -> list[LexicalSignal]` is BM25-shaped and pure
Python:

- Per-document term statistics (token counts over the normalised text: case
  folded, NFKC, split on non-word characters) are a 03 artifact kind keyed by
  the SearchDocument's digest, so a warm turn reads them rather than
  re-tokenising. Corpus statistics (document frequency, average length) are
  computed per query over the documents being scored, never stored, because
  the live set is per turn.
- `terms` carry weights: a resolved name or record title counts double a
  plain token, a quoted phrase is matched whole. The weights are constants,
  tuned later.
- Admission: a document is admitted lexically when it matches at least one
  weighted term or at least two plain terms. A single common word recalls
  nothing, which is the archive's lesson about keywords like `"a"`
  (`archive.py:69`-`78`).
- The chronicle `keywords` are part of the SearchDocument (08's projection),
  so the archive's own signal is subsumed rather than duplicated.

SQLite FTS5 is an allowed later optimisation, only within 03 section 9's rule
(rank only over the caller's live keys) and only where the Android build
carries it. The pure-Python scorer is the reference and stays in place as the
fallback, because Android's `sqlite3` is not guaranteed to have FTS5.

`LexicalSignal(score: float, matched: tuple[str, ...])`: the raw BM25 value
(only an ordering within this query) and the terms that matched (for the
inspector).

### 6.3 Semantic

**Who embeds what** (08 section 8.4, the rule 08 and 09 now share): a turn's
embedding is 09's, under 09's `history-recall` task; 08's `history-index` is
only out-of-turn re-embedding by 05's sync. Document vectors have **one key**
for every producer, `(space["space"], text)` (08 section 8.1; 01h puts any
stated options into the space id and a document is the default side, 01h
sections 3.4 and 5.1-5.3), so what 09 warms on a turn is what 08's hook loads
and keeps current, and the reverse. The earlier draft's separate document key
and its "two embeddings under one key" risk (review B1) do not arise.

`semantic.score(texts, docs, *, space, embed_client, deadline, campaign,
scene) -> SemanticResult`:

- **The space is resolved once per retrieval**, from `embed_space.endpoint()`
  (`store/embed_space.py:62`), in a worker (it reads `config.md`), and handed
  to every load, save and embed, the rule `embed.py`'s docstring states
  ("embedding with one model and saving under another space's key is how
  vectors from two models end up in one cache").
- **One embed call per retrieval**: `await embed.embed("history-recall",
  [*texts, *missing], queries=len(texts), space=space, client=embed_client,
  deadline=deadline, campaign=cid, scene=sid, cached=..., uncached=...)`
  through 01h-C4b's native async door with the app's `AsyncEmbeddingsClient`
  (`routes.get_embeddings`). `texts` is one string on an ordinary turn and up
  to four once 10 adds questions; `missing` is a bounded warm of uncached
  documents, `embeddings.BATCH - len(texts)` at most, on
  `embed_space.warm_window(uncached, texts[0], limit)`, pool documents first so
  tier 1 converges first. The call is **one ledger row and one capture line**
  under `history-recall`; in 01h's `param` mode the client splits it at the
  query/document boundary into two requests under that one meter (01h section
  3.4), so nothing here depends on a single round trip (review M4).
- **Saving**: each returned document vector is saved with
  `vectors.save(space["space"], text, vector)`, and then, in a worker,
  `searchdocs.embedded.record_embedded(cid, warmed_docs, space=space)` (08-C2b)
  writes the `materialized` row `vector:searchdocs.scene:<space-digest>`, the
  digest spelled only by 03's `compiled.space_digest(space)`. Without that row
  05's hot rebuild could not see the vector and would never keep it current
  (review B1). Query vectors are never saved (01h-C1).
- **Retry**: on a `bad_response` with documents in the batch, the query alone
  is retried once under the same deadline, the `semantic._embed` rule
  (`semantic.py:315`-`381`), so a document the provider refuses costs this
  turn's warming, not its recall. That retry files a second row; nothing else
  does.
- **Scoring**: a document's semantic signal is its best cosine over the query
  texts, recording which text it was. The query vectors are compared only with
  document vectors read under the same `space["space"]` (01h-C1).
- **Checks**, all inherited: a wrong-width document vector is evicted with
  `vectors.forget(space["space"], text)` (08 section 8.4 leaves this to the
  scorer) and scores nothing; a score outside `[-1 - SCORE_SLACK,
  1 + SCORE_SLACK]` is evicted; CRC integrity is `vectors.load`'s
  (`store/vectors.py:150`).
- **Admission**: cosine at or above `history_recall_threshold`.
- **Tier 2 bound**: at most `WIDEN_LIMIT` (1000) documents are scored per
  retrieval, newest first: 1000 file opens and, by `store/vectors.py`'s own
  measurement (about 97 ms per 500 vectors at dimension 1536), roughly 200 ms
  on a desktop. Android is slower, and the eval suite's latency column is where
  this is tuned. A campaign past the limit is the case for an ANN index or
  batch-loaded vectors, which 08 section 8.5 leaves to 09 and which is out of
  scope here (section 16).

`SemanticSignal(cosine: float, text: int)`, and a `SemanticResult.status` for
the coverage: `ok`, `off` (no Embedding role, or `embed_space.problem` names a
known `no`), `failed:<kind>`, `backoff`, `skipped:<why>` (preview, locked,
deadline spent).

**The outage memo, shared with lore recall.** A connection-wide failure
(`auth`, `network`, `rate_limit`, `timeout`) sets a per-process memo keyed by
the space id, held in `embed_space` (`embed_space.outage(space_id)`,
`embed_space.note_outage(...)`, `embed_space.clear_outage(...)`); for
`OUTAGE_BACKOFF` (60 s) retrieval skips the semantic stage and says
`backoff`. Lore recall (`context/semantic.py`) consults and sets the same memo
in this spec, rather than in a follow-up (review S4): otherwise a slow endpoint
costs one recall deadline in compose on every turn of an outage on top of the
history phase. The memo is process memory, never stored, cleared by a
successful request, and with nothing failing it changes no prompt.

### 6.4 Query safety restated

Every read starts from the filesystem: list scenes, compute keys, look up
rows for those keys (03 section 9). The semantic stage loads vectors only for
the texts of documents in the live set (`vectors.load(space, texts)` needs the
text, so it cannot be asked about anything else). A guard in the style of 03's
fails a `store/history/` read API that does not take the caller's keys.

### 6.5 Documents and vectors that are missing

08-C2a builds every missing document on each call, so there is no document
build limit (an earlier draft's `BUILD_LIMIT` described a state 08 never
produces; review S10). What can be missing is a document's **vector**: a turn
warms at most `embeddings.BATCH - len(texts)` on a rotating window (6.3), so switching
retrieval on over a long campaign makes semantic coverage grow a little per
turn rather than in one stall. A document with no vector yet is still reachable
structurally and lexically.

## 7. Merging without a fused confidence

### 7.1 Admission, then order

A candidate enters the evidence pool when **at least one signal admits it**
under that signal's own rule (sections 5.4, 6.2, 6.3). Signals never vote a
candidate out. Admission is the only threshold anywhere in the merge, and
each threshold belongs to one signal and is stated in that signal's own units.

The admitted pool is then ordered by **reciprocal-rank fusion over the
ranked lists** (`order_key = sum(1 / (RRF_K + rank))` over every list that
ranked it, `RRF_K = 60`), ties broken by the archive's scene-id comparison,
newest first. **A list ranks only what its own signal admitted**: a
sub-threshold cosine or a single plain-word lexical match contributes no rank,
so admission and order cannot disagree about what a signal said. RRF is chosen
because it consumes only ranks, so BM25 values, cosines and relation counts
are never put on one scale. The `order_key` is an internal sort key: it is not
stored on the evidence, not shown, not thresholded, and not passed to 10 or 11
as a confidence. What the inspector shows is each signal's own rank and raw
value.

This is the draft's "scores are ranking signals, not truth" made structural:
there is no number in the output a caller could mistake for a probability.

### 7.2 What a candidate carries

```python
@dataclass(frozen=True)
class SceneRef:
    sid: str                   # at retrieval
    identity: str | None       # scenes.identity.scene_identity; None for a legacy scene
    @property
    def key(self) -> str:      # identity if any, else "sid:" + sid
        ...

@dataclass(frozen=True)
class Signals:
    structural: StructuralSignal | None   # relations, never a number
    lexical: LexicalSignal | None         # raw score + matched terms
    semantic: SemanticSignal | None       # raw cosine + which query text
    rerank: RerankSignal | None           # grade or position, from decide

@dataclass(frozen=True)
class Candidate:
    scene: SceneRef
    rounds: tuple[Signals, ...]           # one per retrieval round (10 adds rounds)
    ranks: Mapping[tuple[int, str], int]  # (round, signal) -> rank
    tier: int                             # 1 or 2: where it was first found
    admitted_by: frozenset[str]
```

**`SceneRef.key`, never the identity, is what every merge, dedupe and shed unit
compares** (review B2). `scene_identity` returns `None` for a scene that
predates the field (`store/scenes/identity.py:200`-`212`), and two `None`s
compare equal, so keying on the identity would collapse two legacy scenes into
one candidate and give every legacy shed unit the same ref, which empties
`pack.shed`'s `kept` set on the first step (`pack.py:310`-`324`) and drops the
section whole. The `"sid:" + sid` fallback is stable for one retrieval and for
10's rounds within one turn, because the scene being played is reserved and a
rename of an earlier scene during the phase is a race 09 accepts (the worst
case is one scene appearing twice in the inspector). `retrieve` never calls
`ensure_identity`: minting one is a write under the campaign lock.

### 7.3 Optional rerank (02-C5b, 01e)

With `history_recall_rerank: on` and a resolvable decide route, the top
`RERANK_TOP` (8, one structured decide chunk, `decisions.MAX_ITEMS_PER_CALL`)
admitted candidates are graded through **02-C5b's history relevance kit**
(`store/history_rerank.py`: `build_items`, `grades_of`, its templates and
its `decide-history-rerank` replay case), which this spec does not redefine:

- Each kit `Candidate` is `ref = SceneRef.key`, `excerpt` = the candidate's
  header plus its first excerpt (or its summary when it has none), **already
  through the prompt view**, as the kit requires, and `when` = its in-fiction
  date label.
- **Score form first** (the kit's default, one item per candidate on its four
  levels). When 01e-C1 lands, the `Rank` form (`build_items_ranked`, one item
  ranking up to eight) **must set `pointwise`** (01e-C1, section 4.3 of 01e),
  so a native-only Decision model is asked one predicate per candidate rather
  than being refused unsent by `native_gap`, and it **must not rely on a
  native abstain**: a native rank never abstains (01e's caller rule), so a
  candidate the reply did not place is simply unranked.
- **Ungraded is not irrelevant** (the kit's rule): an item not read keeps its
  RRF position; it is never demoted as a low grade.
- **The order rule.** Graded candidates are reordered among themselves by
  grade, highest first, RRF order breaking ties; ungraded candidates keep their
  RRF positions. An abstention, a refusal, an unreadable reply or a failed call
  leaves the order untouched: per `CLAUDE.md`, "what moves an item on is a
  failed call, never an answer". The grade is recorded as
  `RerankSignal(level | position, backend)`, never as a probability; a native
  distribution is not consulted (01c's question).
- **Cost.** One structured chunk is one request; on a native-only Decision
  model it is eight requests at `NATIVE_CONCURRENCY` (four) in flight
  (`inference.py:86`, 02 section 9.3). No escalation and no sampling (02's
  01d-C1 row for `history-rerank`: fallback `role`, escalation off).

It runs in the route layer (`routes/history_recall.py`), which resolves with
`require_inference("history-rerank", cid, operation="decide")` through
`_soft_resolved` (`routes/common.py:1519`), **in a worker**
(`run_in_threadpool`, the pattern at `character_turns.py:748`-`749`, because
resolution reads `config.md` and the connections), so an unresolvable route
skips the rerank with a reason instead of failing the turn. The call is
`operations.decide("history-rerank", items, client=..., resolved=...,
campaign=cid, scene=sid, post=turn_index, around=...)` (`inference.py:684`),
whose `around` bounds each facade call by `min(RERANK_CEILING, remaining)`
(`RERANK_CEILING` 4 s: one decide chunk on a healthy Decision model; tuned
later), because a turn-path caller must not inherit `llm_call_budget`'s "no
ceiling" escape.

The route is 02-C5b's `history_check` (Decision role, `routing.NO_LEGACY`).
The routing guard holds that it lands in the change whose call site decides,
so the rerank is its own slice, after the core, and whichever of it and 10-C3
lands first brings the route.

## 8. Expansion: from a selected scene to excerpts

Selection is a scene; evidence is lines. `expand.excerpts(cid, scene, query,
max_bytes) -> tuple[Excerpt, ...]` is a thin adapter over 08-C3b's
`expand(cid, sid, phase="prompt", terms=..., identity=..., radius=1,
max_excerpts=2, max_bytes=..., fallback="none")` (08 section 10):

1. **Prompt phase, named by the caller.** 08-C3b requires the phase and views
   the whole transcript in it, drops hidden posts and director notes, and
   reduces prompt-phase images to alt text. `test_regex_prompt_guard.py`'s
   extended scan (08 section 12) covers `store/history/` and pins
   `expand.excerpts` by name.
2. **Terms** are the query's weighted terms, then its plain terms, through
   `search.query_terms`' normalisation (08's step 6). Excluded and gm-only
   names were already removed from them (section 4).
3. **Bounds.** `radius=1`, `max_excerpts=2`, and `max_bytes` from the item's
   share of the ceiling (section 9.3) at four bytes per token, the
   characters-per-token heuristic `tokens.count_if_loaded` falls back to.
4. **No anchor.** With `fallback="none"`, a scene with no matching post yields
   no excerpt and contributes its header and summary only. Choosing lines by
   embedding would need post-level vectors, which 08 defers. Such a scene is
   **skipped at selection** when its summary is already in the prompt (the
   archive case of section 9.4), so a depth slot is never spent on a bare
   header (review M6).
5. **Never the whole transcript.** `allow_whole` stays off on the turn path.
   The draft's "read the full transcript" step is 12's
   `get_scene_transcript`, behind that spec's budgets.

Each `Excerpt` carries, per post it includes, the transcript index, the post
key and the post's prompt-view text, plus the joined text that renders. 11
classifies actor knowledge per post, so the post is the unit 09-C1 promises:

```python
@dataclass(frozen=True)
class EvidencePost:
    index: int          # absolute transcript index at retrieval time (08-C3b)
    key: str            # 08-C3b: "r-<response_id>", else "p-<post_id>", else ""
    part: int | None    # response_part, for a reply split across posts
    speaker: str        # the stored speaker label
    text: str           # prompt-view text, as rendered

@dataclass(frozen=True)
class Excerpt:
    posts: tuple[EvidencePost, ...]
    text: str           # what renders: the posts joined as history lines
```

**The post key is 08-C3b's** (review B3; 08's revision): each post carries
`key` as `r-<response_id>` (every part of one reply shares it, `part` tells
them apart), else `p-<post_id>`, else `""` for a legacy post, the spelling 11
and the tracker use (`tracker/paths.py:50`). 09 copies it unchanged; a
consumer names a legacy post by `(SceneRef.key, index)`, stable within the
turn only.

## 9. The history prompt section (09-C3)

### 9.1 The section

One new catalog entry, placed directly after "Earlier scenes":

```python
Section("history_recall", "Recalled history",
        "scene/sections/history_recall.j2", pack.HISTORY_RECALL),
```

- **A tier of its own, `HISTORY_RECALL`, first in `DROP_ORDER`**:
  `DROP_ORDER = (HISTORY_RECALL, RECALLED, ARCHIVE, BACKGROUND, SPOTLIGHT)`.
  Sharing RECALLED would break "can only add": within a tier the packer takes
  the largest section first (`pack.py:333`-`336`), so with lore recall on and
  its section the larger, Recalled lore would be dropped whole before history
  shed a scene, swapping context the prompt already had for context it never
  had, the exact failure `pack.py:85`-`91` created RECALLED to prevent
  (review S1). `pack.py`'s and `layout.py`'s docstrings gain the new tier and
  the reason. With no section in the tier (retrieval off), the packer's loop
  over it does nothing, so off stays byte-identical. It also means the archive
  is never dropped while this section survives, which section 9.4 relies on.
- **A layout saved before 09** gets it after its nearest preceding catalog
  neighbour, `layout.py`'s upgrade rule, and a reader can switch it off by id
  like any other section.
- **Template** (`templates/scene/sections/history_recall.j2`): a `# Earlier in
  the campaign` heading, then one block per evidence item: a header line
  (scene title, in-fiction date, location name), the summary when it is not
  already in the prompt (section 9.4), and the excerpt lines in transcript
  order. Nothing else: no signals, no ranks, no refs, no scores.
- **The header's location passes the same gate as the current setting**
  (`assemble.py:381`-`406`): a gm-only location, or one the reader excluded,
  is not named; the header omits the location rather than leaking its name
  (review S8).
- **Empty when off**: with `history_recall_depth` unset, **or the section
  switched off in the reader's layout** (`_section_on("history_recall")`,
  `assemble.py:799`, the rule `available_art` already follows at
  `assemble.py:731`-`741`: "the off switch, not a way to hide output you are
  still paying for"), `gather` returns empty without reading or spending
  anything (review S7), `_assemble` puts an empty list under
  `history_evidence`, the template renders nothing, and
  `_render_sections` drops the empty section (`assemble.py:1383`-`1386`), so
  the composed prompt and the inspector rows are byte-identical. A test runs
  the whole `test_lore_golden.py` scenario list with the new code and an
  unset key, against the existing golden, which is never regenerated for this.
- **Actor-scoped composes get none.** `history_evidence` joins the
  actor-scoped blanking list (`assemble.py:528`) and `_campaign_view`'s
  actor-scoped early return (`assemble.py:717`-`722`), because an NPC's prompt
  holds only what it may know and 09 knows nothing about who knew what. 11-C2
  is the spec that lifts this, per perspective.

### 9.2 The ceiling

`budget.ceiling(cfg, resolved) -> int`, the strict budget for the section,
computed before expansion through **01i-C2, the required way to derive a
ceiling** (01i section 5: a consumer must not take its own minimum over the
attempts or compute `window - max_output`):

```text
ceiling = history_recall_budget                                   (absolute, tokens)
        min floor(WINDOW_SHARE * prompt_ceiling(resolved).tokens)  [01i-C2; skipped when tokens is None]
        min floor(BUDGET_SHARE * context_budget)                   [only when context_budget > 0]
```

- `prompt_ceiling(resolved)` already walks only the attempts the chain sends
  (the primary, and the fallback only when it rides), reserves each one's
  reply, and returns the smallest, so a fallback with a smaller window is
  covered and a non-riding one is not counted (01i section 5).
- `WINDOW_SHARE` (0.10): the section is targeted recall beside a conversation
  that is the only thing the model cannot reconstruct (`pack.py:33`-`37`); a
  tenth of the prompt ceiling leaves the rest of the prompt its room on the
  smallest windows that serve roleplay. Tuned later against the eval suite's
  added-tokens column.
- `tokens is None` means "use your own cap" (01i): the absolute budget then
  bounds the section alone. That is also the whole rule before 01i lands.
- `BUDGET_SHARE` (0.15) applies only when the reader set a packer budget,
  because then the packer will enforce the whole prompt against it anyway and
  the section should not arrive already larger than its likely share.

### 9.3 The fit and the shed units

`budget.fit(items, ceiling, count) -> list[EvidenceItem]` walks the merged
order and gives each item at most `ceiling // depth` tokens (and at least
`MIN_ITEM_TOKENS`, 80, below which a header and one line do not fit), trims
excerpts from their far edge to fit, and stops at the ceiling. It counts with
**the same tokenizer** the packer uses (`tokens.count_tokens`); the compose's
memoised counter (`_token_memo`) does not exist yet when retrieval runs. "Never
exceeds its ceiling" is measured on **the rendered section text**: `fit`
renders the template's heading, separators and item blocks for the kept items
and counts that string, so the heading and joins are charged. Macro expansion
in `_render_sections` can still change the length of transcript text that
happens to contain a macro token; that is the same latitude the conversation
history has (`assemble.py:575`), and the packer, which measures the expanded
text, remains the final bound (review M1).

The section carries a `shed` hook (`pack.py:228`-`234`) so the packer can give
it up one scene at a time instead of whole:

```python
units = [{"ref": item.scene.identity, "priority": len(items) - pos, "keep": False,
          "pinned": False, "direct": True, "age": -1, "pos": pos}
         for pos, item in enumerate(items)]
```

`_shed_key` sheds lowest `priority` first, so the lowest-ordered item goes
first and the best survives longest. `render(kept)` re-renders from texts
already expanded, the rule World info's hook keeps (`assemble.py:1161`-`1272`):
shedding never re-draws a `{{random}}`. No unit is `keep` or `pinned` (a pin
has nothing in this section to name), so the section can be dropped whole
when its last unit goes, exactly as World info can.

`"ref"` is `item.scene.key` (section 7.2), never the nullable identity.
`pack` changes only by the new tier constant and its place in `DROP_ORDER`
(9.1). Its shed loop is generic over units (`pack.py:305`-`328`) and its tier
loop already admits any section carrying `shed` (`pack.py:333`-`336`). One test
pins that `_world_info_section` remains the only other section with a hook and
that both shed in their documented order; another packs a prompt with both
Recalled lore and Recalled history present and shows history sheds first.

### 9.4 No repeated text

The section must never carry text the prompt already carries:

- **Archive**: for a scene in `archive_entries`, when the archive section is
  on in the reader's layout (`_section_on("archive")`), the item omits its
  summary and renders the header and excerpts only. With "Earlier scenes"
  switched off, the summary renders here, since nothing else carries it
  (review S7). The packer cannot drop the archive while this section survives
  (9.1), so the omission never leaves a summary sent nowhere.
- **Recap**: in the full variant (the opener, `OPENER_RECAP_DEPTH`), summaries
  of recap scenes are omitted; in the compact variant the one-liner is shown
  and the summary may be.
- **History window**: excerpts never come from the scene being played
  (eligibility rule 1).

`compose` passes the archive and recap scene ids it already computed
(`story._recap_ids`, `archive._archive_entries`) to the section's render,
which applies the omission. Retrieval does not need them: omission changes
what renders, never what was selected.

### 9.5 Inspector visibility

The section's breakdown row gains a `history` key beside the existing
`lore` key, built by a `_history_row(section)` sibling of `_lore_row`
(`assemble.py:1789`), with explicit keys so the `shed` function can never
reach a row, a capture or JSON:

```json
{"history": {
  "items": [{"scene": "007--the-crypt", "identity": "...", "title": "...",
             "tier": 1, "admitted_by": ["structural", "lexical"],
             "ranks": {"structural": 1, "lexical": 3, "semantic": 2},
             "structural": [{"relation": "relationship", "refs": ["characters:mara", "pcs:seraphine"]}],
             "lexical": {"score": 4.2, "matched": ["silver key"]},
             "semantic": {"cosine": 0.61},
             "rerank": null,
             "excerpts": [[41, 43]], "summary": "omitted:archive",
             "tokens": 212, "shed": false}],
  "coverage": {"tiers": [1, 2], "semantic": "ok", "admitted": 5,
               "agreement": 2, "verdict": "sufficient"},
  "ceiling": 1200, "budget_from": ["config", "context_window"]}}
```

`test_reasons_never_reach_the_prompt` gains the history row, built from a
**hand-built `Evidence`** in which every signal is set (the preview path skips
semantic, so a real retrieval cannot make every signal fire offline). The
assertions use distinctive markers planted in the evidence (a matched term,
a relation ref, a cosine value, a rerank level label, the verdict) rather than
common words like "relationship" or "location", which already occur in prompt
text, the style of `test_lore_shedding.py:627`-`631` (review M5).

The live inspector (`context_breakdown`, `assemble.py:1942`) is synchronous
and composes a hypothetical turn. It calls `history.retrieve_preview(cid, sid,
query, ceiling)`, the synchronous core of `retrieve` with the semantic and
rerank stages left out (`skipped:preview`), directly in its worker: no bridge,
no embed, no decide, so opening the panel never spends money or waits on a
provider, and the row says what was skipped. The
evidence a real turn used is in that turn's prompt-log capture, which carries
its breakdown (`routes/common.py:406`, `_record_prompt`).

## 10. Where retrieval runs: the turn phase

### 10.1 One async entry, its bridge, and who calls it

```python
async def retrieve(cid: str, sid: str, query: Query, *, ceiling: int,
                   embed_client: embeddings.AsyncEmbeddingsClient,
                   deadline: float, rerank: Reranker | None = None,
                   origin: str = "turn") -> Evidence

def retrieve_preview(cid: str, sid: str, query: Query, ceiling: int) -> Evidence  # sync, no network
```

File and CPU stages run in `anyio.to_thread.run_sync`; the query embedding
and the document warm go through one call to 01h-C4b's async door with
`embed_client` (the app's `AsyncEmbeddingsClient`, `routes.get_embeddings`,
touched only from the loop); `vectors.load`, `vectors.save` and
`record_embedded` are disk work and run in a worker (review S9). The rerank (a `Reranker`,
the callable the route layer builds around `operations.decide`) is async.
Nothing in `retrieve` takes a campaign lock: its reads are the context
builder's fail-soft reads (verified: pins, chronicle, effective, involvement
and relationship history take none), and it writes nothing to a campaign.

`routes/history_recall.gather(app, cid, sid, *, seed, resolved, llm_client,
embed_client, deadline)` is the async turn-path wrapper. It
returns an empty `Evidence` at once when settings are off or the section is
switched off in the layout (no reads, no tasks), resolves the embedding space
and the optional rerank **in a worker** (`run_in_threadpool`, as
`character_turns.py:748`-`749` does; resolution reads `config.md` and the
connections), builds the query, computes the ceiling from `resolved`
(01i-C2), and awaits `retrieve` under the turn-phase deadline (10.2).

`routes/history_recall.gather_sync(app, cid, sid, ...)` is the bridge for a
`def` handler. **In the calling worker, before the portal call**, it checks
whether that worker holds the campaign's lock (`_ProcessScopedLock` is an
`RLock`, so ownership is per thread, `store/locks.py:731`-`734`); if it does,
it returns `Evidence` with `skipped:locked` and does not call the portal
(review S2: the loop thread a portal call lands on owns nothing, so the check
inside `gather` could never see the worker's hold). Otherwise it calls
`app.state.run_portal.call(gather, ...)` (`main.py:330`-`331`) and **wraps the
call**: any exception, the portal being closed at shutdown included, returns
an empty `Evidence` with `coverage.verdict = "error"` (review M7), because on
`post_chat` the player's post is already appended and the undo is not yet
wired, so an exception here would strand it.

| Caller | Thread | How it reaches retrieval | Where the evidence goes |
|---|---|---|---|
| `post_chat` (`routes/scenes.py:1085`), `post_retry` (`:1205`), `post_regenerate` (`:1475`) | threadpool worker (`def`) | `gather_sync` | `compose_turn(..., history=evidence)` |
| The director branch (`routes/scenes.py:1057`) | same | `gather_sync`, with the note as `seed` | `compose_director_turn(..., history=evidence)`, which gains the parameter too (review M10) |
| A group round (`character_turns._round_frames` -> `_prepare`) | async driver, then a worker under `campaign_lock` | `await gather(...)` in the driver, **only for a narrator contribution** that will compose (below) | passed into `_prepare` and on to `_compose` |
| `context_breakdown` (live inspector) | worker | `retrieve_preview`, inline | breakdown only |

The turn routes gain `embed_client: AsyncEmbeddingsClient =
Depends(get_embeddings)` beside their LLM client.

**The round path** (review S3). `_round_frames` calls `_prepare` once per
contribution (`character_turns.py:1043`-`1045`), and only some contributions
compose with the section. The driver calls `gather` only when **all** hold:

- the contribution's actor is `grimoire` (an NPC's prompt blanks the
  section, 9.1);
- `turn.appended` is empty (a roll continuation composes mid-turn and is not
  a caller, open question 5);
- the round has no pending response whose snapshot `_prepare` will replay
  (`character_turns.py:479`-`497`). The driver reads that once, outside the
  lock; a stale answer only wastes a retrieval, since `_prepare` decides under
  the lock and ignores the evidence when it replays.

The evidence is computed **once per round** and reused by every narrator
contribution of that round: it is keyed by the round's id and held on the
driver's state for the round's life. A round is one player post, so retrieval
is per player post on both paths.

A guard test fails a `history.retrieve`, `gather` or `gather_sync` call
lexically inside a `with ... campaign_lock(...)` block in `routes/`. The
opener, mechanics continuations and replay are not callers in this spec (open
question 5).

### 10.2 The turn-phase deadline

One deadline bounds the whole phase, and 10's planning draws from the same
one (review S4):

- `HISTORY_PHASE_SECONDS` (4 s) when planning is off: one embed round trip on
  a healthy hosted endpoint plus one decide chunk, with room. It is a turn-path
  budget, deliberately far below `embeddings.TIMEOUT` (30 s,
  `embeddings.py:74`), which is a provider timeout, not a wait a player should
  sit through before the first frame.
- `HISTORY_PHASE_PLANNED_SECONDS` (12 s) when 10's `history_plan` is `auto`,
  the phase 10 section 8 spends from.
- Every step takes `min(its own ceiling, remaining)`: the query embed's
  `deadline`, the warm's `deadline`, the rerank's `around`, and 10's calls.
  A step that would start with nothing remaining does not start, and
  `coverage.semantic` (or 10's trace) says `skipped:deadline`.
- **The file stages** (the scene listing, 08-C2a's slicing and digesting, the
  structural reads, lexical over the live set) run in a worker and cannot be
  cancelled mid-read. They run first; if they finish past the deadline, no
  network stage starts. Their cost is a listing, a stat and a digest per scene
  and arithmetic over cached term statistics; the eval suite's latency column
  measures it on long synthetic campaigns, and 09-C4's gate includes it.
- **Worst case before the first frame** on `post_chat`: the file stages plus
  the phase deadline (4 s, or 12 s with planning), plus lore recall's own
  embed in compose, which the shared outage memo (6.3) caps at one deadline
  per `OUTAGE_BACKOFF` during an outage.

### 10.3 Freezing and replay

Evidence is computed once per compose and rendered into the frozen prompt
(`_prepare`), so every guidance variant and every fallback attempt reads the
same evidence. A reroll of a pending response replays the snapshot
(`PreparedMessages.from_snapshot`, `character_turns.py:483`) and retrieves
nothing (10.1's gate). A fresh `post_regenerate` composes, and so retrieves,
again.

### 10.4 Detached runs

Retrieval belongs to the turn that asked for it. On the round path it runs
inside the detached run's driver, so a dropped connection drops a subscriber,
not the retrieval. On the `post_chat` path it runs in the request before the
run is started, as `compose_turn` does today; moving composition into the run
is a separate change (open question 7). A cancelled run cancels the
`retrieve` task: the async embed call files `aborted` (01h-C4b), and vectors
already returned are not saved.

## 11. Tiers, widening and fallbacks (09-C2)

### 11.1 Tiers

| Tier | Candidate set | When |
|---|---|---|
| 1 | Structural pool (`POOL_MAX`) plus lexical hits over the whole live set; semantic scores the pool only | Always, first |
| 2 | Every live SearchDocument in the campaign, semantic bounded by `WIDEN_LIMIT` | When tier 1's coverage verdict is not `sufficient` |
| 3 | Other campaigns' history (every campaign of the world, or the library) | **Never on a turn.** Only for `origin` other than `"turn"` with `Query.tier_limit == 3`: 12's `search_history` tool, and a future search surface. `retrieve` refuses tier 3 for `origin="turn"` with `ValueError` |

Lexical scoring over the whole live set is part of tier 1 because it is the
cheap signal: per-document statistics are cached and the scan is arithmetic.
Tier 2 widens only the semantic signal, the one that costs per document.

### 11.2 The coverage verdict

`coverage.verdict(candidates, depth, eligible) -> Coverage`, deterministic and
free. `eligible` is the number of scenes that passed section 5.3, so coverage
can tell "retrieval missed" from "nothing else exists" (review S5):

- `exhausted`: every eligible scene is already admitted, or there is no
  eligible scene at all (a campaign's first scene). Nothing more can be found,
  so nothing should be spent looking: tier 2 does not run and 10 treats it as
  `sufficient`.
- `empty`: no candidate admitted, and eligible scenes exist.
- `sufficient`: at least `min(depth, eligible)` candidates admitted **and** at
  least `MIN_AGREEMENT` (1) of them admitted by two or more signals.
- `thin`: anything else.
- `error`: `gather` caught a failure (11.3). 10 never plans on it.

Agreement is the structural meaning of "more than one independent reason to
think this scene matters", which is why it is counted rather than scored. With
the semantic signal off, agreement can still come from structure plus words.

Tier 2 runs when tier 1 says `empty` or `thin` *and* the semantic signal is
available and has time left. Without semantic, tier 2 adds nothing tier 1
did not already score, so it is skipped and coverage records why.

`Coverage(verdict, tiers, semantic, admitted, agreement, eligible)` is what 10
consumes. 10's planner is gated on `thin` or `empty` only (10 section 3).

### 11.3 Fallbacks

| Situation | Behaviour |
|---|---|
| Retrieval off (`history_recall_depth` 0, the default) | `gather` returns empty without reading anything; the prompt is byte-identical |
| No Embedding role, or a known `no` for `embed` (`embed_space.problem`) | Structural plus lexical, tier 1 only; `semantic: off` |
| Embed failure this turn | Structural plus lexical; `semantic: failed:<kind>`; the meter has already recorded the failure (`CLAUDE.md`, "Instrument LLM failures at `usage.Meter.done`"); this module writes no line of its own |
| Outage memo set | As above, with `backoff`, and no request |
| Compiled cache missing or deleted | 08-C2a rebuilds every document on the call (08 section 6); vectors refill through 08-C2b's warm, `warm_limit` per turn, and a document with no vector is structural and lexical only meanwhile |
| A ledger file unreadable (chronicle, plot, appearances, relationship history) | Its relations contribute nothing; the others answer. The `involvement.scene_actors(unreadable=...)` pattern says which |
| Rerank unresolvable, failed, or abstained | RRF order stands; the row says why |
| Deadline spent | What was computed is used; `skipped:deadline` |

Retrieval never raises into a turn. `gather` catches everything below
`BaseException` that is not cancellation, logs it once at warning through the
`grimoire` logger with campaign and scene ids only, and returns an empty
`Evidence` with `coverage.verdict = "error"`.

## 12. The long-history eval suite (09-C4)

### 12.1 Synthetic campaigns

`evals/history/synth.py` builds campaigns into a temporary store
(`GRIMOIRE_HOME` set per run, the test isolation rule) from a seeded recipe,
through the real store APIs (worlds, characters, scenes, appearances,
chronicle, plot, commitments, relationship history, groups), so retrieval is
measured against files the app would have written. The recipe is a list of
scene templates with planted facts, so a long campaign is generated, never
checked in.

Names come only from the codebase's placeholders (Seraphine, Mara, Winifred,
Realm, Saltmarch) and role labels ("the harbour keeper", "the ferryman"),
never newly invented proper names, per `CLAUDE.md`. No real store is read,
measured or described.

### 12.2 Cases

Each case is a recipe seed, the posts of the scene being played, the gold
scene identities, the gold transcript index ranges, and (for the live arm) a
planted fact token. The draft's five shapes are the minimum:

1. The correct evidence is many scenes old (beyond recap and archive depth).
2. A distractor scene has similar wording and different actors.
3. The relevant scene shares actors but none of the query's words.
4. The query is semantically generic ("You knew the whole time, didn't you?").
5. The answer is one short transcript excerpt inside a long scene.

Plus five 09 adds:

6. The relevant scene is a closed branch sibling (it must **not** be recalled).
7. The relevant scene is *later* than the one being played (must not be
   recalled: the `before` rule).
8. The relevant actor is excluded by a reader's pin: they seed nothing, their
   name is not a lexical term, and a gm-only or excluded location is not named
   in any header.
9. A young campaign with fewer earlier scenes than `depth`, and a first
   scene: coverage must be `exhausted`, tier 2 must not run.
10. Legacy scenes with no identity (the frozen campaign's shape): two of them
    remain two candidates and shed one at a time.

### 12.3 Arms and metrics

| Arm | What runs |
|---|---|
| `context-only` | Today's prompt: recap and archive, no history section |
| `lexical` | Lexical only, tier 1 |
| `semantic` | Semantic only, tiers 1 and 2 |
| `structural+lexical` | No embeddings |
| `hybrid` | All three signals |
| `hybrid+rerank` | All three plus the 01e rerank (live only) |

Per arm and aggregate: scene recall at 1, 3 and 5; excerpt recall (a gold
range inside a rendered excerpt); rule cases 6 to 10 as hard pass/fail; added
tokens (the section after packing); retrieval wall time, the file stages
reported apart from the network stages; embed rows per task and decide
calls. Live mode adds downstream consistency: the reply names the
planted fact token and does not contradict it, a deterministic grader in the
`evals/graders.py` style (no human judgement, `evals/README.md`'s rule).
Wall time, tokens and cost are reported per item and per arm through 01a-C1,
in the three money columns never added together, and the arms compare in
01a-C3's one table.

### 12.4 Offline and live

- **Offline** (`evals/run.py --history`), deterministic, inside `pytest
  backend`: the `context-only`, `lexical` and `structural+lexical` arms over a
  small recipe, plus `semantic` and `hybrid` replayed from recorded vectors
  (`evals/recordings/history/vectors-<space digest>.bin`, synthetic text
  only). Rule cases 6 to 10 must pass in every arm.
- **Long** (`--history --long`): the same cases over long recipes, opt-in,
  offline where vectors are recorded.
- **Live** (`--history --live`): embeds with the configured Embedding role,
  metered under 01a-C2's eval scope, and runs the downstream generation arm.

### 12.5 What the gate asks

The default stays off until the long suite shows, on the synthetic corpora,
`hybrid` at or above every other arm on recall at 3, strictly above
`context-only` and `lexical`, with no rule-case failure and added tokens
within the ceiling. Thresholds are orderings, not committed figures; the
margins are tuned in conversation and recorded nowhere that describes a
user's library.

## Slices

Landing order within this spec: S1 → S2 → S3 → S4, then S5 and S6 in
parallel (S5 needs only S3; S6 needs S4 for its semantic arms and S5 for its
rerank arm, so its rerank arm lands with S5 if S6 goes first). `history_recall_depth`
defaults to `0` in every slice, so `main` composes byte-identical prompts until
a reader turns it on.

### 09-S1: Retrieval core, structural and lexical, no network

- **Delivers:** 09-C1 (part: `Query`, `SceneRef.key`, `Evidence`/`EvidenceItem`/`EvidencePost` with evidence ids, eligibility, structural and lexical signals, admitted-only RRF merge and `merge(..., expand)`, expansion through 08-C3b, `budget.fit`, `retrieve_preview`); 09-C2 (part: the `Coverage` verdict with `eligible` and `exhausted`, tier 1, the no-embeddings fallback)
- **Needs (this spec):** none
- **Needs (other specs):** 08-C1 (H: bounded `SceneDocument` with metadata, keyed by per-scene slices); 08-C2a (H: `scene_documents` live set with one batch lookup); 08-C3b (H: `expand(..., phase="prompt")` with `r-`/`p-` post keys and `part`); 03-C6 (H: batch lookup over a live key set, index ranking only within it, for the per-document term-statistics kind); 03-C7 (H: text-keyed artifacts, never `BUILD`); 07-C2 (S: `groups_for` — until it lands, the `group` relation is absent); 07-C3c (S: `scene_groups`/`co_affiliates`/`prompt_visible` projections — until then, the group relation reads `scene_actors` with 07-C2 only, or is absent)
- **Scope:** The `store/history/` package (`model`, `settings`, `query`, `refs`, `structural`, `lexical`, `merge`, `coverage`, `expand`, `budget`) and the synchronous `retrieve_preview`. The four config keys join `config._CONFIG_KEYS`, default off. No caller in `routes/` or `context/` yet, no embed task, no network. `test_regex_prompt_guard.py` scans `store/history/` and pins `expand.excerpts`.
- **Acceptance:** section 15's store tests for `query.build`, structural, eligibility, lexical, merge (keyed by `SceneRef.key`, legacy identity case), coverage (`exhausted` for a first and a young campaign), expansion and budget (absolute cap); the live-set liveness test.
- **Size:** L

### 09-S2: The history section and its packer tier

- **Delivers:** 09-C3 (full)
- **Needs (this spec):** 09-S1 (H)
- **Needs (other specs):** 01i-C2 (S: `prompt_ceiling(resolved)` — until it lands, the absolute `history_recall_budget` alone bounds the section)
- **Scope:** `pack.HISTORY_RECALL`, first in `DROP_ORDER`, with `pack.py`'s and `layout.py`'s docstrings; the `history_recall` catalog entry, its template, the per-scene `shed` hook keyed on `SceneRef.key`, the no-repeat rule, the header location gate, actor-scoped blanking, the `_history_row` inspector row and `context_breakdown`'s preview path. `compose_turn` and `compose_director_turn` gain `history=`. Nothing calls retrieval on a turn yet, so every real prompt stays byte-identical; `verify_templates.py` and `templates/README.md` gain the section; `fixtures/history_golden.json` is recorded from a fixed synthetic evidence.
- **Acceptance:** section 15's Context tests: the lore golden unchanged with the key unset and with the section off in the layout; render, pack and shed order with Recalled lore present; no repeated summary (archive on and off); no gm-only or excluded location in a header; actor-scoped empty; `test_reasons_never_reach_the_prompt` from a hand-built `Evidence`.
- **Size:** M

### 09-S3: The turn phase, structural and lexical

- **Delivers:** 09-C1 (part: async `retrieve` without the semantic stage, `gather`, `gather_sync`); 09-C2 (part: the turn-phase deadline, the error verdict, the fallback table's non-network rows)
- **Needs (this spec):** 09-S2 (H)
- **Needs (other specs):** 01i-C2 (S: as S2)
- **Scope:** `routes/history_recall.py` (`gather`, `gather_sync` with the in-worker lock check and the wrapped portal call), wired into `post_chat`, `post_retry`, `post_regenerate`, the director branch and the group round's narrator contributions (once per round, not for NPCs, replays or continuations). `HISTORY_PHASE_SECONDS` bounds the phase; the lexical guard fails a retrieval inside a `campaign_lock` block. With the key on, a turn carries structural and lexical evidence and spends nothing.
- **Acceptance:** section 15's Routes tests except the semantic and rerank ones: evidence reaches compose and the prompt-log capture; `skipped:locked`; a raising portal does not strand the post; the round gate; the live inspector sends nothing.
- **Size:** M

### 09-S4: The semantic signal and tier 2

- **Delivers:** 09-C1 (part: the semantic signal, `history-recall` embed rows, `record_embedded`); 09-C2 (full)
- **Needs (this spec):** 09-S3 (H)
- **Needs (other specs):** 01h-C4b (H: native async `embed()` with a total deadline, `AsyncEmbeddingsClient` via `routes.get_embeddings`); 01h-C4a (H: no embedding on the event loop, a guard degrades); 08-C2b (H: document vectors under `space["space"]` and `record_embedded`); 01h-C1 (S: the `queries` split — until it lands, the call is untyped and the query and documents embed alike)
- **Scope:** `store/history/semantic.py`: one `history-recall` call carrying the query texts and a bounded warm, saving under `space["space"]`, `record_embedded`, the query-only retry, the width and range checks; tier 2 under `WIDEN_LIMIT`. `history-recall` joins `routing.EMBED_TASKS` with this call site (one rule forces task and call site into one slice); `test_inference_embedding.py`'s tuple and `MIN_EMBED_CALLS` move. The outage memo moves into `embed_space` and lore recall consults it in the same slice. The turn routes gain `Depends(get_embeddings)`.
- **Acceptance:** section 15's semantic tests (one call, `queries=len(texts)`, `materialized` rows read back, no query vector saved, no `history-index` on a turn, retry, evictions, shared memo); the phase-deadline test with a stalled endpoint.
- **Size:** M

### 09-S5: The rerank

- **Delivers:** 09-C1 (full)
- **Needs (this spec):** 09-S3 (H)
- **Needs (other specs):** 02-C5b (H: the `history_check` route entry, the `history-rerank` task, `build_items`/`grades_of`, its templates and the `decide-history-rerank` replay case); 01d-C1 (S: the `TaskPolicy` row for `history-rerank` — until it lands, no row and the chain's default); 01e-C1 (S: `Rank` with `pointwise` — until it lands, the kit's Score form); 01e-C2 (S: `tiers()` — until then, the four-level Score)
- **Scope:** `history_recall_rerank` gains its effect: the reranker built in `routes/history_recall.py`, resolved in a worker, bounded by `min(RERANK_CEILING, remaining)`, applied once per turn. If this lands before 10-S2, it lands 02-C5b's `history_check` route with its call site, plus `routing.NO_LEGACY` and the `NO_LEGACY_TASKS` baseline mechanism (10 section 13), because the routing guard and the frozen baselines force route, call site and test mechanism into one slice. Default off.
- **Acceptance:** section 15's rerank tests (ungraded keeps RRF order, unresolvable route skipped, ceiling honoured, `pointwise` on a native `Rank`); the `decide-history-rerank` replay case; the routing and baseline tests of 10 section 13 when this slice brings the route.
- **Size:** M

### 09-S6: The long-history eval suite

- **Delivers:** 09-C4 (full)
- **Needs (this spec):** 09-S4 (H); 09-S5 (S: until it lands, no `hybrid+rerank` arm)
- **Needs (other specs):** 01a-C1 (H: per-case and per-call wall time, tokens and the three money columns, for the live arms); 01a-C2 (H: live evals metered by the production meter, for the live arms); 01a-C3 (S: the comparison table — until then, one report per arm)
- **Scope:** `evals/history/` (synthetic generator on placeholder names, cases 1 to 10, the arms, graders), `evals/run.py --history`, `--long` and `--live`, recorded synthetic vectors. The offline arms run inside `pytest backend`. The gate of section 12.5 is what a later change to the default reads.
- **Acceptance:** rule cases 6 to 10 pass in every offline arm; `hybrid` meets section 12.5's ordering on the recorded vectors; live arms report through 01a.
- **Size:** M

## 13. Contract

### 09-C1: retrieval over a query, returning bounded evidence with signals

**Inputs.** `retrieve(cid, sid, query, *, ceiling, embed_client, deadline, rerank=None, origin="turn")` (async; `retrieve_preview` is its
synchronous, network-free core), where `Query` is:

```python
@dataclass(frozen=True)
class Query:
    texts: tuple[str, ...]                 # [0] is the turn window; 10 appends questions
    terms: tuple[str, ...] = ()            # exact phrases for the lexical signal
    subjects: tuple[str, ...] = ()         # extra structural seeds, as refs
    scopes: frozenset[str] = RELATIONS     # which structural relations may seed
    max_scenes: int | None = None          # <= history_recall_depth; None = the setting
    perspective: str = "narrator"          # 11's seam; anything else is ValueError until 11-C1
    tier_limit: int = 2                    # 3 only with origin != "turn"
    turn_index: int | None = None          # the rerank's ledger `post`
```

**Outputs.** `Evidence(items: tuple[EvidenceItem, ...], candidates:
tuple[Candidate, ...], coverage: Coverage, ceiling: int, query_digest: str)`:
`items` are the selected scenes, fitted to `ceiling`, each carrying its
`SceneRef` (the **scene identity**, `None` for a legacy scene, the sid at
retrieval, and `key`, section 7.2), header fields, an optional summary, its
excerpts with **every included post's transcript index, post key and text**
(`EvidencePost`, section 8: 08-C3b's `r-`/`p-` key and `part`), its
**evidence ids**, and its token cost; `candidates` are every admitted candidate in merged order (bounded
by `POOL_MAX + WIDEN_LIMIT`), so a caller can see what was found and not
selected. `Evidence.detail()` is the JSON-safe projection of section 9.5.

**Evidence ids** are the one spelling every consumer uses to name a unit of
evidence (10's `PlanTrace`, 11, 12). `EvidenceItem.evidence_ids:
tuple[str, ...]` holds one id per rendered unit: `"<SceneRef.key>"` for an
item that renders a header and summary only, and
`"<SceneRef.key>@<first post key, else its index>"` for each excerpt, the post
key being 08-C3b's `r-`/`p-` key. The scene part is the same string 02-C5b's
rerank kit takes as `Candidate.ref`. Ids are stable within a turn; across a
cut only the key form is, which is why the post key is preferred over the
index.

`history.merge(a, b, *, ceiling, expand, rerank=None) -> Evidence` (review
S6) merges two evidences **by `SceneRef.key`**:

- a candidate in both keeps both rounds' `Signals` (`Candidate.rounds`), and
  its ranks are keyed `(round, signal)`, so two rounds that both ranked it
  lexically are two lists, not a collision;
- the merged order is RRF over every round's ranked lists, each list ranking
  only what its signal admitted (7.1);
- the top `history_recall_depth` are selected; an item already expanded in a
  round keeps its excerpts, and a newly selected candidate that no round
  expanded is expanded through `expand` (the caller's bound
  `expand.excerpts`, run in a worker), so the merge pays at most `depth` new
  transcript reads;
- the optional rerank runs once on the merged top (7.3), and the result is
  re-fitted to `ceiling`.

10 retrieves its rounds with `rerank=None` and reranks only the final merge, so
a planned turn pays for one rerank, not one per round. The selection never
exceeds `history_recall_depth` and the ceiling never grows, whatever a round
asked for.

**Guarantees.** No candidate orders at or after the played scene, is in its
branch group, or is a closed sibling. Every lookup is over keys computed this
call. Signals are never combined into one stored or returned number. Every
merge, dedupe and shed compares `SceneRef.key`, never a nullable identity. The
output text has passed the regex prompt view. The call writes nothing to a
campaign and takes no campaign lock, and never mints a scene identity. A
turn's embedding is one `history-recall` call (query plus bounded warm,
campaign and scene), plus one query-only retry on a `bad_response`; warmed
document vectors are saved under `space["space"]` and recorded through 08's
`record_embedded`. `history-index` is never used on a turn. Embed rows carry
the player post only once 01h's doors take `post=` (open question 9); the
rerank's decide rows carry it now.

**Failure.** Never raises for a provider, cache or ledger problem: degraded
signals are absent and `coverage` says why. Raises `ValueError` only for a
programming error: tier 3 from a turn, an unknown perspective, a scope outside
`RELATIONS`.

### 09-C2: tiered widening and fallbacks

`coverage.verdict` as section 11.2 (`exhausted | sufficient | thin | empty |
error`, with `eligible` counted), and the tier rule and fallback table of
sections 11.1 and 11.3. **Guarantees**: tier 3 is unreachable from a turn;
`exhausted` spends nothing further; with no embeddings, retrieval still returns
structural and lexical evidence; an outage costs at most one embed deadline per
`OUTAGE_BACKOFF`, shared with lore recall; the phase never exceeds its
turn-phase deadline once its file stages are done (10.2); `Coverage` is
deterministic for the same files, query and semantic status.

### 09-C3: the history prompt section with a strict budget

The `history_recall` section of section 9: its own `HISTORY_RECALL` tier,
first in `DROP_ORDER`; the ceiling of 9.2 through 01i-C2 `prompt_ceiling`;
the fit and the per-scene `shed` hook of 9.3; the no-repeat rule of 9.4; the
inspector row of 9.5; actor-scoped blanking (NPC prompts get none of it until
11-C2). **Guarantees**: off (unset, or switched off in the layout) is
byte-identical and spends nothing (`test_lore_golden.py` unchanged); nothing in
the row reaches the prompt; the rendered section never exceeds its ceiling
when it leaves `budget.fit`; under packer pressure it is the first content to
give way, one scene at a time, lowest-ordered first; no gm-only or excluded
location is named in a header.

### 09-C4: the long-history eval suite

`evals/history/` (generator, cases, arms, graders) of section 12, reporting
through 01a-C1 and comparing through 01a-C3. **Guarantees**: synthetic data
only; offline arms deterministic and inside `pytest backend`; live arms
metered under the eval scope.

## 14. Interaction with repo rules

- **Golden prompts.** Off by default and byte-identical (9.1). The lore
  golden is never regenerated for this change; a second golden,
  `fixtures/history_golden.json`, pins the section's rendering for a fixed
  synthetic evidence so a template edit is a reviewed change.
- **Reasons never reach the prompt.** Extended to the history row (9.5).
- **Routing and operation guards.** `history-recall` joins `EMBED_TASKS`
  (`routing.py:174`) with its one call site, the query embed, and is held by
  `test_operation_guard.py`'s embed half: the door is 01h-C4b's async `embed`,
  the `space=` traces back to `embed_space.endpoint`.
  `test_inference_embedding.py`'s pinned tuple and `MIN_EMBED_CALLS` move with
  it, as 08 section 8.2 does for `history-index`. The rerank's decide task
  lands with its call site, on 02-C5b's decide route (`test_routing_guard.py`),
  resolved through `require_inference(..., operation="decide")` in a worker.
- **The frozen inference baselines** (review S11). `tests/inference_baseline.py:418`-`420`
  and `test_inference_resolve.py:44`-`49` observe every task in
  `sorted(routing.TASK_ROUTE)` against JSON that is never regenerated, and a
  new task must be in `test_inference_equivalence.NEW_TASKS` with a sibling
  it resolves identically to. A task on a `routing.NO_LEGACY` route has no
  such sibling (it resolves to its route's default role even where a legacy
  route pin exists). The rerank slice therefore uses the mechanism 10 section
  13 specifies for every `NO_LEGACY` task (a `NO_LEGACY_TASKS` set dropped
  from both cells, with its own assertion that each such task resolves to its
  route's `default_role` in every baseline state), whichever spec lands it
  first; it is part of the checklist's shared `NO_LEGACY` structure.
- **Metering.** Every embed is metered at the one door; every rerank chunk by
  `decide`. An unpriced embedding endpoint files unpriced rows, as recall does
  today (`CLAUDE.md`, embedding cost), so turning history recall on with a
  local endpoint makes the Costs totals read incomplete until rates are set.
  The settings copy says so.
- **Locks.** No campaign lock; never called under one: a lexical guard in
  `routes/`, plus `gather_sync`'s check in the calling worker (10.1). `store/history/`
  writes nothing campaign-scoped, so it is in none of `locks.DOMAIN_MODULES`,
  `OUTSIDE_DOMAIN` or `UNREVIEWED`, the position `continuity/graph.py`
  documents for a read-only projection.
- **Revision token.** Nothing campaign-scoped is written, so nothing stamps
  (`store/revision.py`).
- **Regex view.** Expansion goes through the prompt view, and the guard's
  scan grows to cover `store/history/` (8).
- **Templates.** `templates/scene/sections/history_recall.j2` is documented in
  `templates/README.md`'s variable contract, and `scripts/verify_templates.py`'s
  `gather()` mirror gains `history_evidence` (empty, and one fixed synthetic
  evidence), so `make check`'s `check-templates` covers the new section. The
  rerank's question and item wording live in `templates/history_rerank/`
  (the `templates/scene_break/` shape: `question.j2`, `user.j2`), with
  `templates/decide/` rendering the prompt around them; `test_llm_fakes.py`
  renders them like every real template, and a rerank cassette entry is added to
  `backend/tests/fixtures/llm/` rather than an inline fake (`CLAUDE.md`,
  "Faking the LLM").
- **Imports.** Module scope, acyclic, submodules as module objects.
- **Paths.** The compiled cache and vector cache are reached through their
  modules; nothing builds a path outside `store.paths`.
- **Privacy.** Logs carry campaign and scene ids and counts, never query
  text, terms, excerpts or scores. The eval corpora are synthetic. No
  constant here is justified by a measurement of a real library.
- **Android and pydantic.** Pure Python, no numpy, no FTS5 requirement, no new
  base dependency. The settings travel through `PUT /config` as today, so no
  new pydantic model.
- **Config.** The four keys join `config._CONFIG_KEYS` (`store/config.py:219`)
  and the defaults table, or `read_config` drops them. Under 01s they live in
  the Context pane beside the semantic recall knobs.

Settings:

| Key | Default | Meaning |
|---|---|---|
| `history_recall_depth` | `0` | Scenes the section may carry; 0 is off |
| `history_recall_budget` | `1200` | Absolute token ceiling for the section |
| `history_recall_threshold` | `0.35` | Cosine floor for the semantic signal; model-dependent, tuned against the inspector |
| `history_recall_rerank` | `off` | Run the 01e rerank when a decide route resolves |

## 15. Tests and acceptance

**Unit (store):**

- `query.build` reads the prompt view; a hidden post and a director note never
  contribute terms; the seed is appended; an excluded or gm-only name is not a
  term, weighted or plain.
- Structural: each relation from a hand-built campaign; an excluded actor and a
  gm-only record seed nothing; `shared_cast` above `STRUCTURAL_PLAIN_CAP` ranks
  without admitting; a group leader counts as a member; an unreadable
  chronicle leaves appearances answering; `locations/<id>` from 08 and
  `locations:<id>` from seeds join after `refs.norm`.
- Eligibility: later scene (by the archive's string compare, with a mix of
  `001--` and date-form ids), current branch group, closed sibling, all
  excluded in every signal.
- Lexical: one common word admits nothing; a resolved name admits; scores
  identical with and without the cached per-document statistics.
- Semantic: one `history-recall` call carries the query texts and the warm,
  with `queries=len(texts)`; warmed vectors are saved under `space["space"]`
  and `record_embedded` writes their `materialized` rows (a test reads them
  back through 03); no query vector is ever saved; `history-index` is never
  called on a turn; a `bad_response` retries the query alone once; width
  mismatch and out-of-range scores evict; a `rate_limit` sets the shared
  memo, and both history and lore recall then send nothing; a success clears
  it.
- Merge: RRF over admitted-only lists; no number on `Evidence` other than
  per-signal raw values; `merge(a, b)` keeps both rounds' signals under
  `(round, signal)` ranks, dedupes by `SceneRef.key`, and expands a newly
  selected candidate through the callback.
- Legacy identity: on the frozen campaign's shape (scenes with no identity
  line), two scenes stay two candidates and shed one at a time.
- Coverage: each verdict, `exhausted` for a first scene and a young campaign;
  tier 2 skipped without semantic and on `exhausted`.
- Expansion: the adapter passes `phase="prompt"` and the bounds of section 8;
  no anchor gives header and summary only; an archive scene with no anchor is
  skipped at selection.
- Budget: `prompt_ceiling(resolved).tokens` share; `tokens is None` uses the
  absolute cap; `context_budget` share only when set; the rendered section,
  heading included, never exceeds the ceiling.
- Live set: a document superseded by an edit is unreachable (03-C2), with its
  old row still in the cache.

**Context:**

- Golden: `test_lore_golden.py` scenarios with the new code and the key unset
  are byte-identical; no embed client is constructed and no compiled-cache
  read happens (a spy on both). The same with the key set and the section
  switched off in the layout.
- The section renders, packs, sheds lowest-ordered first under a budget,
  re-renders without re-drawing a macro, and drops whole when its last unit
  sheds; with Recalled lore also present, history sheds before lore drops.
- No repeated summary for an archive scene (archive on) or a full-recap scene;
  the summary renders when the archive section is off.
- A gm-only or excluded location is not named in a header.
- Actor-scoped compose: empty, in the narrator's round as well as the NPC's.
- `test_reasons_never_reach_the_prompt` extended, from a hand-built `Evidence`
  with distinctive markers.

**Routes:**

- `post_chat`, `post_retry`, `post_regenerate` and the director branch compose
  with evidence through `gather_sync`, and the turn's prompt-log capture
  carries the history row.
- `gather_sync` in a worker holding the campaign lock returns `skipped:locked`
  without calling the portal; a portal that raises returns empty evidence and
  the post is not stranded.
- A group round retrieves once per round, only for narrator contributions, not
  for an NPC contribution, a pending-snapshot replay or a roll continuation;
  the guard fails a retrieval placed inside the lock.
- The live inspector sends no embed and no decide request.
- The phase respects its deadline: a stalled embed endpoint costs at most the
  phase seconds before compose, and the next turn hits the outage memo.
- Rerank: an abstention or an ungraded item keeps RRF order; an unresolvable
  route skips with a reason; a timeout honours `min(RERANK_CEILING,
  remaining)`; a `Rank` on a native-only model carries `pointwise`.

**Evals:** the offline history suite passes rule cases in every arm, and
`hybrid` meets the ordering of 12.5 on the recorded vectors.

**Acceptance (the draft's, restated):**

1. Hybrid retrieval beats context-only and lexical-only on the long suite
   (12.5), and the eval output says by how much in which arm.
2. Embeddings are optional: every test above that does not name semantic
   passes with no Embedding role.
3. Retrieval is scene-first and bounded: never more than `depth` items, never
   over the ceiling, never a whole transcript.
4. No superseded document can surface.
5. Off is byte-identical and spends nothing; on, the section is the first
   content to give way.

## 16. Non-goals

- Changing the recap, the archive, semantic lore recall, or library search.
- An ANN index or vectors in SQLite. 03 leaves the vector store's location to
  08 or 09; 09 keeps `vectors.py` files and its key, and revisits only if the
  long suite shows tier 2 is the latency floor.
- Post-level embeddings (08 section 11).
- Retrieval inside the current scene.
- Tier 3 on any turn.
- Perspective filtering (11), planning (10), investigation (12).
- A UI for retrieval beyond the inspector row and the settings keys.

## 17. Open questions

1. **Should history recall eventually replace the archive?** With 09 on, the
   archive's keyword signal is part of the lexical signal (chronicle keywords
   live in the SearchDocument), so "Earlier scenes" becomes mostly a cheaper
   duplicate. *Recommendation:* keep both through 09 and 10; decide after the
   long suite shows whether `hybrid` alone matches `hybrid` plus the archive,
   and if so retire the archive in its own change with its own golden.
2. **Default on?** *Recommendation:* off, like every retrieval layer before
   it, until 12.5's ordering holds on the long suite; then on with structural
   plus lexical only, since that arm costs no provider calls.
3. **The rerank route (resolved).** 02-C5b provides the shared
   `history_check` decide route (Decision role, `routing.NO_LEGACY`, the shared
   structure the checklist records); 09's rerank adds `history-rerank` to it,
   and the route lands with whichever call site lands first (09's rerank or
   10-C3), as `test_routing_guard.py` requires. No open edge remains.
4. **Query versus document embeddings in one space (resolved).** 01h-C1
   states that a query vector is compared within its space and is never
   cached; 09 relies on that statement and needs nothing further.
5. **The opener and the other composes.** The opener has no player post
   (and composes in a worker once 01h-C4a lands); mechanics continuations and
   replay compose mid-turn. *Recommendation:* leave all three out of 09; add the opener
   later with the greeting text as the query, once the async path is proven
   on the round driver.
6. **Retrieval inside the scene being played.** A scene longer than the
   packed history loses its early posts. *Recommendation:* a separate item
   after 10; it needs post-level selection 08 defers, and a different
   eligibility rule.
7. **Retrieval latency before the first frame on `post_chat`.** Retrieval
   (and 10's planning when on) runs before the detached run starts, so the
   player waits for it before the lead frame. *Recommendation:* accept for
   09 under the turn-phase deadline of section 10.2 (4 s, 12 s with
   planning); moving `compose_turn` into the run is a larger change worth its
   own spec.
8. **Share the outage memo with semantic lore recall (resolved).** Done in
   this spec (section 6.3), keyed by space id in `embed_space`.
9. **`post=` on the embed doors (cross-spec, routed to 01h).** Neither
   `embed_sync` (`embed.py:143`) nor 01h-C4b's `embed` takes `post`, so 09's
   query row and 08's warm row on a turn are charged to the scene but not to
   the player post. *Recommendation:* 01h adds `post: int | None` to both
   doors, filed by the meter, and CLAUDE.md's embedding paragraph names it;
   09 then passes `turn_index`. Until then 09-C1 does not promise it.
10. **The post key (resolved).** 08-C3b carries `r-<response_id>` /
    `p-<post_id>` / `""` and `part`; 09 adopts it.
11. **12's `search_history` meters through 09 (cross-spec, routed to 12).**
    A tool call that embeds a query goes through 09, so it files
    `history-recall`, never `history-index` (08 section 8.4); 12 should say
    so.

## 18. Review record

Substitute adversarial review of 2026-10-09, folded in. Codex gate pending.

| Item | Disposition |
|---|---|
| B1 document warm contradicts 08/01h | Fixed, per 08's revision: one key for every producer, `space["space"]`; the turn's query plus warm is one `history-recall` call with `queries=` through the async door; 09 calls `record_embedded` so `materialized` rows exist; `history-index` only out of turn (6.3); 12 routed (OQ 11) |
| B2 nullable identity in merge and shed | Fixed: `SceneRef.key` (7.2), used by merge, dedupe and shed units; no `ensure_identity`; frozen-campaign test |
| B3 post keys and embed `post` not supplied | Post key: 08-C3b now supplies `r-`/`p-` keys and `part`, adopted (8). Embed `post=`: routed to 01h (OQ 9); 09-C1 does not promise it before it lands |
| S1 RECALLED shared with lore | Fixed: own `HISTORY_RECALL` tier, first in `DROP_ORDER`; `pack` and `layout` docstrings change (9.1) |
| S2 lock self-check on the wrong thread | Fixed: `gather_sync` checks in the calling worker before the portal (10.1) |
| S3 round path retrieves for NPCs, replays, continuations | Fixed: gated to narrator contributions, no `appended`, no pending replay; once per round (10.1) |
| S4 latency not bounded | Fixed: turn-phase deadline 4 s / 12 s shared with 10, every step `min(own, remaining)`, file stages stated, outage memo shared with lore recall now (6.3, 10.2) |
| S5 exhausted vs missed | Fixed: `eligible` and an `exhausted` verdict (11.2) |
| S6 merge underspecified | Fixed: per-round `Signals`, `(round, signal)` ranks, admitted-only RRF lists, `expand` callback (7.1, 7.2, 09-C1) |
| S7 layout off must stop spend | Fixed: `_section_on` gate in `gather`; archive omission only when the archive is on (9.1, 9.4) |
| S8 header and terms bypass gates | Fixed: header location through the setting's gate; excluded and gm-only names removed from terms (4, 9.1) |
| S9 client/thread contract vs 01h-C4b | Fixed: `embed_client` (async, `Depends(get_embeddings)`); disk work and resolution in a worker (10.1) |
| S10 BUILD_LIMIT and ref forms vs 08 | Fixed: limit dropped (08 builds all misses); `refs.norm` (5.3, 6.5) |
| S11 frozen inference baselines | Fixed: the `NO_LEGACY_TASKS` mechanism, specified in 10 section 13 and shared (14) |
| M1 counter and what the ceiling measures | Fixed (9.3) |
| M2 string compare vs `parse_sid` | Fixed (5.3) |
| M3 rerank kit, native cost, constant values | Fixed: 02-C5b kit inputs, `pointwise` per 01e, native cost, `RERANK_CEILING` 4 s, `STRUCTURAL_PLAIN_CAP` 12 (5.4, 7.3) |
| M4 "one request" false | Fixed: one row per call, two requests in `param` mode, a second row only for the query-only retry (6.3) |
| M5 preview bridge; reasons test markers | Fixed: synchronous `retrieve_preview`; hand-built evidence and distinctive markers (9.5) |
| M6 header-only archive item | Fixed: skipped at selection (8) |
| M7 portal raise strands the post | Fixed: `gather_sync` wraps the call (10.1) |
| M8 vectors cost figure | Fixed (5.4, 6.3) |
| M9 draft signals dropped silently | Fixed: recorded as deferred with reasons (5.2) |
| M10 director branch | Fixed: `compose_director_turn` gains the parameter (10.1) |

Slices added (6 slices), before the Contract section.

Coordinator inputs applied in the same pass: 01i-C2 `prompt_ceiling` as the
only ceiling derivation (9.2); 01e-C1 `pointwise` on any `Rank` and no reliance
on a native abstain (7.3); 07's accessor `groups_for`, leaders counted as
members (5.1, 5.2); 08's revision (turn warm under `history-recall` with
`record_embedded`, prefixed post keys, `compiled.space_digest`). The
coordinator's first instruction ("09 must not embed documents itself") was
superseded by that revision, and 6.3 follows the revision.

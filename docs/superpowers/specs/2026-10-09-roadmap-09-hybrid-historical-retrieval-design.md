# 09. Hybrid historical retrieval

**Status:** Draft -- spec gate (`/codex:adversarial-review`) pending.
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

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| 07-C2 derived inverse membership (actor -> groups) | 07 | The `group` structural relation: scenes where several members of a present actor's group stood (section 5.3) | Soft: the relation is absent until it lands; every other signal works |
| 08-C1 scene SearchDocument with metadata, keyed by input digest + `search_document_version` | 08 | The unit every lexical and semantic signal scores, and its metadata (scene identity, date, location, cast) | Hard |
| 08-C2 lazy build, hot rebuild via 05, embeddings keyed by space and text | 08 | Building the live set on demand, bounded per turn; loading document vectors by `vectors.load(space, texts)` | Hard |
| 08-C3 history embed task and transcript-expansion helper | 08 | The document-side embed task, and turning a selected scene into bounded excerpts (section 8) | Hard |
| 03-C6 batch lookups over a live key set, index ranking restricted to it | 03 | Looking up SearchDocuments and any lexical index only for keys computed from the filesystem this turn (section 6.4) | Hard |
| 03-C7 vectors keyed by text, never `BUILD` | 03 | An upgrade costs embeddings only where SearchDocument text moved | Hard (inherited through 08) |
| 01e-C1 `Rank` question; 01e-C2 finer `Score` with explicit ties | 01e | The optional rerank stage (section 7) | Soft: rerank is off by default and its slice waits for 01e |
| 01h-C1 query vs document input type | 01h | The query is embedded as a query, documents as documents (section 6.3) | Soft: without it both sides embed as today, and the eval suite measures the difference |
| 01h-C4 async embed path for turn-path callers | 01h | The query embedding on the turn path never blocks the event loop (section 10) | Hard for the opener and character-turn paths; the `def` handlers already run on a worker |
| 01i-C1 `context_window` (and max output) as a resolved fact on each attempt | 01i | Deriving the history section's ceiling from the window of the model that will read it (section 9.2) | Soft: without it the configured absolute budget alone bounds the section |
| 01a-C1 eval cost, latency and token reporting | 01a | The long-history suite reports wall time, tokens and the three money columns per arm (section 12) | Hard for 09-C4's live mode; offline mode needs nothing |
| 02-C5 decide routes and tasks for retrieval relevance | 02 | The task and route the rerank stage meters and resolves under (section 7.3) | Soft, and see open question 3: the route must land with its first call site |

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 09-C1 `history.retrieve` over a `Query`, returning an `Evidence` with per-signal signals and a `Coverage` | 10 | Running a planner's rewritten questions through the same retrieval, and merging rounds (`history.merge`) |
| 09-C1 | 11 | The `perspective` seam on `Query`, and per-item scene metadata (cast, date) to classify actor knowledge against |
| 09-C1 | 12 | The `search_history(query, scope)` investigation tool is a thin wrapper over `retrieve` with an explicit tier-3 scope |
| 09-C2 deterministic coverage verdict and tiered widening | 10 | The free first gate that decides whether the planner runs at all |
| 09-C2 | 12 | One of the "cheap retrieval failed" signals that may escalate to an investigation |
| 09-C3 the history prompt section, its budget and its shed units | 10, 11 | 10 feeds merged evidence into the same section; 11-C2 splits it into actor-usable and narrator-only halves |
| 09-C4 the long-history eval suite and synthetic campaign generator | 10, 11, 12 | Their evals extend the same corpora and arms rather than building their own |

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
 |   -> semantic.score(pool | live set) one embed request (query + warm)  |
 |   -> merge.order(...)                admission per signal, RRF order   |
 |   -> [rerank]                        optional decide, 01e Rank/Score   |
 |   -> coverage + widening             tier 1 -> tier 2 (09-C2)          |
 |   -> expand.excerpts (08-C3)         bounded windows, prompt view      |
 |   -> budget.fit                      01i-C1 ceiling, per-scene units   |
 +------------------------------------------------------------------------+
                                   | Evidence (frozen, JSON-safe detail)
                                   v
 compose_turn(..., history=Evidence) -> "Recalled history" section (RECALLED,
                                        shed per scene) -> pack -> send
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
| `store/history/semantic.py` | Query embedding, warm run, cosine over a live set; the outage memo |
| `store/history/merge.py` | Admission, ordering, `merge(Evidence, Evidence)` for 10 |
| `store/history/coverage.py` | The deterministic sufficiency verdict and widening decision |
| `store/history/expand.py` | Excerpts through 08-C3, through the regex prompt view |
| `store/history/budget.py` | The ceiling (01i-C1) and the fit |
| `store/history/retrieve.py` | `retrieve()`, the one async entry, composing the above |
| `store/context/history.py` | The section's data, render and `shed` hook (context side) |
| `routes/history_recall.py` | `gather()` on the turn path: deadline, rerank resolution, portal bridge |

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
  location, item, group or thread title, quoted phrases, and the remaining
  non-stopword tokens.
- **`subjects`**: the structural seeds, as refs (section 5.1).
- **`turn_index`**: the transcript index of the post being answered, carried
  to every ledger row (`usage.meter(post=...)`, `store/usage.py:629`), so a
  post's cost includes its retrieval.

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
| Groups | 07-C2 `groups_for_actor(ref)` for each present actor | `groups:<id>` |
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
| `group` | Scenes where two or more members of a seeded group stood | 07-C2 inverse membership plus `scene_actors` |

The draft's "event/time anchors" relation is deferred: `events.json` records
no scene (`store/events.py` docstring), so tying an event to scenes would mean
matching in-fiction dates, which is 11's or a later spec's question.

### 5.3 Eligibility, applied to every signal

A scene is a candidate for *any* signal only if all of these hold, checked
once in `retrieve` against the live listing (`scenes.read.list_scenes`):

1. It orders strictly before the scene being played, by scene id, the
   archive's `before` rule (`archive.py:67`). The same ordinal prefix that rule
   relies on (`store/scene_ids.py:15`, `parse_sid`) makes it cheap.
2. It is not in the current scene's branch group (`scenes/read.py:87`-`112`): a
   sibling is an alternative to the present, not its past.
3. It is not closed by an absorbed sibling (`closed_by`, `scenes/read.py:115`):
   an abandoned branch did not happen.
4. It has a SearchDocument key this turn (08-C1), or, for the structural
   signal alone, at least a readable scene head. A scene 08 has not built yet
   is still reachable structurally; it is built lazily if selected (section
   6.5).

### 5.4 Structural rank

There is no structural score. A candidate carries
`StructuralSignal(relations: tuple[Relation, ...])`, each relation naming its
kind and the refs that produced it. The structural ranked list orders by:

1. the number of distinct relation kinds (a scene that shares cast *and*
   moved their relationship beats one that only shares cast);
2. the number of distinct seed refs involved;
3. newest scene first.

**Admission.** `shared_cast` alone, with only present actors, admits a scene
only when fewer than `STRUCTURAL_PLAIN_CAP` scenes qualify that way. Two
companions who travel together share every scene, so co-presence alone is a
recency list in disguise; above the cap it ranks but does not admit (section
7.1). Every other relation admits on its own.

**Pool.** Tier 1's pool is the top `POOL_MAX` structural candidates plus every
lexical hit (section 6.2). `POOL_MAX` (64) bounds the semantic work of tier 1
to one `vectors.load` of 64 small records, which `store/vectors.py`'s own
docstring prices at a few milliseconds per 100 vectors. Both constants are
tuned later against the eval suite.

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

`semantic.score(query_text, docs, *, space, client, deadline, campaign, scene,
post) -> SemanticResult`:

- **The space is resolved once per retrieval**, from `embed_space.endpoint()`
  (`store/embed_space.py:62`), and handed to every load, save and embed, the
  rule `embed.py`'s docstring states ("embedding with one model and saving
  under another space's key is how vectors from two models end up in one
  cache").
- **One request per retrieval**: the query (input type `query`, 01h-C1) plus
  up to `WARM_LIMIT` uncached documents (input type `document`), the window
  rotated by `embed_space.warm_window` exactly as semantic recall does
  (`semantic.py:142`). Pool documents are warmed before the wider live set,
  so tier 1 converges first. `WARM_LIMIT = embeddings.BATCH - 1` keeps it one
  round trip (`semantic.py:126`-`128`'s reasoning).
- **The embed task** is a new `EMBED_TASKS` entry, `history-recall`, for the
  query and this turn's warm run. 08-C3's document task covers builds outside
  a turn (05's eager rebuild). Two tasks because they answer different cost
  questions: what a turn spent recalling, and what keeping the index warm
  cost.
- **Deadline**: one monotonic deadline for the request and its possible
  retry, the `semantic._embed` rule (`semantic.py:335`-`338`); a retry only on
  `bad_response`, never on `auth`, `rate_limit` or `network`.
- **Checks**, all inherited: a non-unit or wrong-width vector is evicted
  (`vectors.forget`) and scores nothing; a score outside `[-1 - SCORE_SLACK,
  1 + SCORE_SLACK]` is evicted; CRC integrity is `vectors.load`'s
  (`store/vectors.py:150`).
- **Admission**: cosine at or above `history_recall_threshold`.
- **Tier 2 bound**: at most `WIDEN_LIMIT` (1000) documents are scored per
  retrieval, newest first. `store/vectors.py`'s docstring measures the
  pure-Python load and dot at roughly 100 ms per 500 vectors of dimension
  1536 on a desktop; 1000 keeps tier 2 within a few hundred milliseconds there.
  Android is slower, and the eval suite's latency columns are where this is
  tuned. A campaign past the limit is the case for an ANN index, which is out
  of scope (section 15).

`SemanticSignal(cosine: float)`, and a `SemanticResult.status` for the
coverage: `ok`, `off` (no Embedding role, or `embed_space.problem` names a
known `no`), `failed:<kind>`, `backoff`, `skipped:<why>` (preview, locked,
deadline spent).

**The outage memo.** A connection-wide failure (`auth`, `network`,
`rate_limit`, `timeout`) sets a per-process memo keyed by the space id; for
`OUTAGE_BACKOFF` (60 s) retrieval skips the semantic stage and says
`backoff`. Without it every turn of an outage pays the full embed deadline
before the reply can start, which is the cost semantic recall pays today. The
memo is process memory, never stored, and is cleared by a successful request.

### 6.4 Query safety restated

Every read starts from the filesystem: list scenes, compute keys, look up
rows for those keys (03 section 9). The semantic stage loads vectors only for
the texts of documents in the live set (`vectors.load(space, texts)` needs the
text, so it cannot be asked about anything else). A guard in the style of 03's
fails a `store/history/` read API that does not take the caller's keys.

### 6.5 Building what is missing

A scene selected by any signal whose SearchDocument is not yet built is built
through 08-C2, at most `BUILD_LIMIT` (16) per retrieval; the rest sit out this
turn, are reachable structurally, and are built on a later turn. This is the
same incremental-warm argument as `WARM_LIMIT`: switching retrieval on over a
long campaign costs a little per turn for a while rather than one stall.

## 7. Merging without a fused confidence

### 7.1 Admission, then order

A candidate enters the evidence pool when **at least one signal admits it**
under that signal's own rule (sections 5.4, 6.2, 6.3). Signals never vote a
candidate out. Admission is the only threshold anywhere in the merge, and
each threshold belongs to one signal and is stated in that signal's own units.

The admitted pool is then ordered by **reciprocal-rank fusion over the three
ranked lists** (`order_key = sum(1 / (RRF_K + rank_s))` over the signals that
ranked it, `RRF_K = 60`), ties broken newest scene first. RRF is chosen
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
class Signals:
    structural: StructuralSignal | None   # relations, never a number
    lexical: LexicalSignal | None         # raw score + matched terms
    semantic: SemanticSignal | None       # raw cosine
    rerank: RerankSignal | None           # position, or level, from decide

@dataclass(frozen=True)
class Candidate:
    scene: SceneRef            # (sid at retrieval, identity) -- identity is stable
    signals: Signals
    ranks: dict[str, int]      # per-signal rank; absent where the signal did not rank it
    tier: int                  # 1 or 2: where it was first found
    admitted_by: frozenset[str]
```

`SceneRef` carries the scene identity (`scenes.identity.scene_identity`)
beside the sid, because 10 merges retrieval rounds and a rename between them
must not split one scene into two candidates.

### 7.3 Optional rerank (01e)

With `history_recall_rerank: on` and a resolvable decide route, the top
`RERANK_TOP` (8, one structured decide chunk, `decisions.MAX_ITEMS_PER_CALL`)
admitted candidates are asked one question:

- **`Rank`** (01e-C1) when the resolved backend supports it: one item whose
  context is the query window and each candidate's header plus SearchDocument
  summary, answered as an order with abstention allowed.
- **`Score`** (01e-C2, fine levels with explicit ties) otherwise: one item per
  candidate, the same context.

The rerank replaces the RRF order *among the candidates it answered*, in place;
a candidate it did not answer keeps its RRF position relative to the others.
An abstention, a refusal, an unreadable reply or a failed call leaves the RRF
order untouched: per `CLAUDE.md`, "what moves an item on is a failed call,
never an answer", and here a non-answer simply means no rerank. The rerank's
answer is recorded as `RerankSignal(position | level, backend)` and never as a
probability; a native distribution, where one exists, is not consulted (01c's
question, not this one).

It runs in the route layer (`routes/history_recall.py`), which resolves with
`require_inference("history-rerank", cid, operation="decide")` through
`_soft_resolved` (`routes/common.py:1519`), so an unresolvable route skips the
rerank with a reason instead of failing the turn, and the call goes through
`operations.decide("history-rerank", items, client=..., resolved=...,
campaign=cid, scene=sid, post=turn_index, around=...)` (`inference.py:684`)
under the retrieval deadline (`around`, `_bounded_call` at
`routes/common.py:544` with an explicit ceiling, `RERANK_CEILING`, because a
turn-path caller must not inherit `llm_call_budget`'s "no ceiling" escape).

The task and its route are 02-C5's to name (open question 3). The routing
guard holds that the route lands in the change whose call site decides, so the
rerank is its own slice, after 01e and after the core.

## 8. Expansion: from a selected scene to excerpts

Selection is a scene; evidence is lines. `expand.excerpts(cid, scene, query,
budget_tokens) -> tuple[Excerpt, ...]` uses 08-C3's transcript-expansion
helper, which this spec requires to behave as follows (stated as requirements
on 08-C3, not designed here):

1. **Prompt view.** The scene's messages are read through
   `store.regex.view.view(messages, cid=cid, phase="prompt")`, with director
   notes and hidden posts dropped and images reduced to their alt text, the
   same projection `story._project_history` applies (`story.py:47`-`71`).
   `test_regex_prompt_guard.py` is extended to scan `store/history/` and pins
   `expand.excerpts` by name, so a later edit that reads raw text fails.
2. **Anchors.** Posts matching the query's weighted terms, then its plain
   terms. A window is the anchor post and `EXCERPT_PAD` (1) post either side.
3. **Bounds.** At most `MAX_WINDOWS` (2) windows per scene, merged when they
   overlap, cut from the far edge to fit the scene's share of the budget,
   never mid-word.
4. **No anchor.** A scene admitted only structurally or semantically, with no
   lexical anchor in its transcript, contributes its header and its summary,
   and no excerpt. Choosing lines by embedding would need post-level vectors,
   which 08 defers.
5. **Never the whole transcript.** There is no "read full transcript" step on
   the turn path. The draft's step 4 is 12's `get_scene_transcript`, behind
   that spec's budgets.

Each `Excerpt` carries the transcript index range it covers, for the
inspector, and the text, which is what renders.

## 9. The history prompt section (09-C3)

### 9.1 The section

One new catalog entry, placed directly after "Earlier scenes":

```python
Section("history_recall", "Recalled history",
        "scene/sections/history_recall.j2", pack.RECALLED),
```

- **RECALLED**, the tier `pack.py:85`-`91` reserves for "anything retrieved by
  a mechanism that did not exist before". It gives way before the archive,
  the trailing history trim and everything else, so it can only add.
- **A layout saved before 09** gets it after its nearest preceding catalog
  neighbour, `layout.py`'s upgrade rule, and a reader can switch it off by id
  like any other section.
- **Template** (`templates/scene/sections/history_recall.j2`): a `# Earlier in
  the campaign` heading, then one block per evidence item: a header line
  (scene title, in-fiction date, location name), the summary when it is not
  already in the prompt (section 9.4), and the excerpt lines in transcript
  order. Nothing else: no signals, no ranks, no refs, no scores.
- **Empty when off**: with `history_recall_depth` unset, `_assemble` puts an
  empty list under `history_evidence`, the template renders nothing, and
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

`budget.ceiling(cfg, chain) -> int`, the strict budget for the section,
computed before expansion:

```text
ceiling = history_recall_budget                        (absolute, tokens)
        min floor(WINDOW_SHARE * min known context_window over the chain's attempts)   [01i-C1]
        min floor(BUDGET_SHARE * context_budget)       [only when context_budget > 0]
```

- `WINDOW_SHARE` (0.10): the section is targeted recall beside a conversation
  that is the only thing the model cannot reconstruct (`pack.py:33`-`37`); a
  tenth of the window leaves the rest of the prompt its room on the smallest
  windows that serve roleplay. Tuned later against the eval suite's
  added-tokens column.
- The **minimum over the chain**: a fallback attempt with a smaller window
  reads the same frozen prompt (`_prepare`'s rule, `assemble.py:1499`-`1510`),
  so the ceiling must fit the smallest reader. An attempt whose window is
  unknown (01i-C1 provenance `unknown`) does not lower it.
- `BUDGET_SHARE` (0.15) applies only when the reader set a packer budget,
  because then the packer will enforce the whole prompt against it anyway and
  the section should not arrive already larger than its likely share.
- With 01i-C1 not yet landed, the first term is the whole rule.

### 9.3 The fit and the shed units

`budget.fit(items, ceiling, count) -> list[EvidenceItem]` walks the merged
order and gives each item at most `ceiling // depth` tokens (and at least
`MIN_ITEM_TOKENS`, 80, below which a header and one line do not fit), trims
excerpts from their far edge to fit, and stops at the ceiling. The counter is
the compose's own memoised one (`_token_memo`, `assemble.py:1582`), so the
fit and the packer agree on what a string costs.

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

`pack` needs no change. Its shed loop is generic over units (`pack.py:305`-`328`)
and its tier loop already admits any section carrying `shed`
(`pack.py:333`-`336`). One test pins that `_world_info_section` remains the
only other section with a hook and that both shed in their documented order.

### 9.4 No repeated text

The section must never carry text the prompt already carries:

- **Archive**: for a scene in `archive_entries`, the item omits its summary
  and renders the header and excerpts only.
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

`test_reasons_never_reach_the_prompt` gains the history row: every relation
name, signal name, score, ref and verdict word above is asserted absent from
`json.dumps(build_messages(...))`, with a scenario in which every signal fired.

The live inspector (`context_breakdown`, `assemble.py:1942`) composes a
hypothetical turn and has no evidence handed to it. It runs `retrieve` with
`semantic` and `rerank` skipped (`skipped:preview`), so opening the panel never
spends money or waits on a provider, and the row says what was skipped. The
evidence a real turn used is in that turn's prompt-log capture, which carries
its breakdown (`routes/common.py:406`, `_record_prompt`).

## 10. Where retrieval runs: the turn phase

### 10.1 One async entry, three callers

```python
async def retrieve(cid: str, sid: str, query: Query, *, ceiling: int,
                   client: embeddings.EmbeddingsClient, deadline: float,
                   rerank: Reranker | None = None,
                   origin: str = "turn") -> Evidence
```

File and CPU stages run in `anyio.to_thread.run_sync`; the query embedding
goes through 01h-C4's async path; the rerank (a `Reranker`, the callable the
route layer builds around `operations.decide`) is already async. Nothing in
`retrieve` takes a campaign lock: its reads are the context builder's
fail-soft reads, and it writes nothing to a campaign (vectors go to the
global cache, documents to the compiled cache).

`routes/history_recall.gather(app, cid, sid, *, seed, resolved, client)` is
the turn-path wrapper: it returns an empty `Evidence` immediately when
settings are off (no reads, no tasks), builds the query, computes the ceiling
from `resolved.chain` (9.2), resolves the optional rerank softly, and awaits
`retrieve` under `RETRIEVAL_DEADLINE`.

| Caller | Thread | How it reaches `gather` | Where the evidence goes |
|---|---|---|---|
| `post_chat` and the director branch (`routes/scenes.py:1050`-`1088`), `post_retry`, `post_regenerate` | threadpool worker (`def`) | `app.state` lifespan portal: `portal.call(gather, ...)`, the house bridge for a `def` route (`CLAUDE.md`, detached runs) | `compose_turn(..., history=evidence)` |
| A group round's narrator compose (`character_turns._prepare`) | async driver, then a worker under `campaign_lock` | `await gather(...)` in the driver **before** `run_in_threadpool(_prepare, ...)` | passed into `_prepare` and on to `_compose` |
| `context_breakdown` (live inspector) | worker | inline, preview mode | breakdown only |

A guard test fails a `history.retrieve` or `gather` call lexically inside a
`with ... campaign_lock(...)` block in `routes/`, and `retrieve` itself checks
that the calling thread does not hold the campaign's lock (the lock is
reentrant, so it knows its owner); if it does, the network stages are skipped
with `skipped:locked` rather than holding every other writer of the campaign
behind an HTTP call. The opener, mechanics continuations and replay are not
callers in this spec (open question 5).

### 10.2 The deadline

`RETRIEVAL_DEADLINE` bounds the whole phase, embed and rerank included. It
defaults to `embeddings.TIMEOUT` plus `RERANK_CEILING`, so a healthy turn
waits for at most one embed round trip and one decide chunk, and an outage
costs one embed deadline and then nothing for `OUTAGE_BACKOFF`. A phase that
runs out returns what it has: structural and lexical are computed first and
are never waited out, and `coverage.semantic` says `skipped:deadline`.

### 10.3 Freezing and replay

Evidence is computed once per compose and rendered into the frozen prompt
(`_prepare`), so every guidance variant and every fallback attempt reads the
same evidence. A reroll of a pending response replays the snapshot
(`PreparedMessages.from_snapshot`, `character_turns.py:483`) and retrieves
nothing. A fresh `post_regenerate` composes, and so retrieves, again.

### 10.4 Detached runs

Retrieval belongs to the turn that asked for it. On the round path it runs
inside the detached run's driver, so a dropped connection drops a subscriber,
not the retrieval. On the `post_chat` path it runs in the request before the
run is started, as `compose_turn` does today; moving composition into the run
is a separate change (open question 7). A cancelled run cancels the
`retrieve` task; its embed request is abandoned the way `_bounded_call`
abandons an overrun call, and its meter files what it files (an embed that
went out is a row).

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

`coverage.verdict(candidates, depth) -> Coverage`, deterministic and free:

- `empty`: no candidate admitted.
- `sufficient`: at least `depth` candidates admitted **and** at least
  `MIN_AGREEMENT` (1) of them admitted by two or more signals.
- `thin`: anything else.

Agreement is the structural meaning of "more than one independent reason to
think this scene matters", which is why it is counted rather than scored. With
the semantic signal off, agreement can still come from structure plus words.

Tier 2 runs when tier 1 says `empty` or `thin` *and* the semantic signal is
available and has budget left. Without semantic, tier 2 adds nothing tier 1
did not already score, so it is skipped and coverage records why.

`Coverage` is what 10 consumes: it carries the verdict, the tiers tried, the
semantic status, the admitted count and the agreement count. 10's planner is
gated on it (`thin` or `empty`, 10 section 4).

### 11.3 Fallbacks

| Situation | Behaviour |
|---|---|
| Retrieval off (`history_recall_depth` 0, the default) | `gather` returns empty without reading anything; the prompt is byte-identical |
| No Embedding role, or a known `no` for `embed` (`embed_space.problem`) | Structural plus lexical, tier 1 only; `semantic: off` |
| Embed failure this turn | Structural plus lexical; `semantic: failed:<kind>`; the meter has already recorded the failure (`CLAUDE.md`, "Instrument LLM failures at `usage.Meter.done`"); this module writes no line of its own |
| Outage memo set | As above, with `backoff`, and no request |
| Compiled cache missing or deleted | SearchDocuments rebuild lazily (08-C2), `BUILD_LIMIT` per turn; unbuilt scenes are structural-only this turn |
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

Plus three 09 adds:

6. The relevant scene is a closed branch sibling (it must **not** be recalled).
7. The relevant scene is *later* than the one being played (must not be
   recalled: the `before` rule).
8. The relevant actor is excluded by a reader's pin (must seed nothing).

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
range inside a rendered excerpt); rule cases 6 to 8 as hard pass/fail; added
tokens (the section after packing); retrieval wall time; embed requests and
decide calls. Live mode adds downstream consistency: the reply names the
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
  only). Rule cases 6 to 8 must pass in every arm.
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

## 13. Contract

### 09-C1: retrieval over a query, returning bounded evidence with signals

**Inputs.** `retrieve(cid, sid, query, *, ceiling, client, deadline, rerank=None,
origin="turn")`, where `Query` is:

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
    turn_index: int | None = None          # ledger attribution
```

**Outputs.** `Evidence(items: tuple[EvidenceItem, ...], candidates:
tuple[Candidate, ...], coverage: Coverage, ceiling: int, query_digest: str)`:
`items` are the selected scenes, fitted to `ceiling`, each with header fields,
an optional summary, excerpts and its token cost; `candidates` are every
admitted candidate in merged order (bounded by `POOL_MAX + WIDEN_LIMIT`), so a
caller can see what was found and not selected. `Evidence.detail()` is the
JSON-safe projection of section 9.5. `history.merge(a, b) -> Evidence` merges
two evidences by scene identity, keeping every signal from both and re-ordering
by RRF over the union, for 10's rounds.

**Guarantees.** No candidate orders at or after the played scene, is in its
branch group, or is a closed sibling. Every lookup is over keys computed this
call. Signals are never combined into one stored or returned number. The
output text has passed the regex prompt view. The call writes nothing to a
campaign and takes no campaign lock. Every embed request is one ledger row
under `history-recall`, with campaign, scene and post.

**Failure.** Never raises for a provider, cache or ledger problem: degraded
signals are absent and `coverage` says why. Raises `ValueError` only for a
programming error: tier 3 from a turn, an unknown perspective, a scope outside
`RELATIONS`.

### 09-C2: tiered widening and fallbacks

`coverage.verdict` as section 11.2, and the tier rule and fallback table of
sections 11.1 and 11.3. **Guarantees**: tier 3 is unreachable from a turn;
with no embeddings, retrieval still returns structural and lexical evidence;
an outage costs at most one embed deadline per `OUTAGE_BACKOFF`; `Coverage` is
deterministic for the same files, query and semantic status.

### 09-C3: the history prompt section with a strict budget

The `history_recall` section of section 9: RECALLED tier, the ceiling of 9.2
(01i-C1 when present), the fit and the per-scene `shed` hook of 9.3, the
no-repeat rule of 9.4, the inspector row of 9.5, actor-scoped blanking.
**Guarantees**: off is byte-identical (`test_lore_golden.py` unchanged);
nothing in the row reaches the prompt; the section never exceeds its ceiling
when it leaves `budget.fit`, and under packer pressure it sheds lowest-ordered
first.

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
  (`routing.py:174`) and is held by `test_operation_guard.py`'s embed half:
  the door is `embed` (or 01h-C4's async door), the `space=` traces back to
  `embed_space.endpoint`. The rerank's decide task lands with its call site,
  on a decide route (`test_routing_guard.py`), resolved through
  `require_inference(..., operation="decide")` in `routes/`.
- **Metering.** Every embed is metered at the one door; every rerank chunk by
  `decide`. An unpriced embedding endpoint files unpriced rows, as recall does
  today (`CLAUDE.md`, embedding cost), so turning history recall on with a
  local endpoint makes the Costs totals read incomplete until rates are set.
  The settings copy says so.
- **Locks.** No campaign lock; never called under one (10.1). `store/history/`
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
  contribute terms; the seed is appended.
- Structural: each relation from a hand-built campaign; an excluded actor and a
  gm-only record seed nothing; `shared_cast` above `STRUCTURAL_PLAIN_CAP` ranks
  without admitting; an unreadable chronicle leaves appearances answering.
- Eligibility: later scene, current branch group, closed sibling, all
  excluded in every signal.
- Lexical: one common word admits nothing; a resolved name admits; scores
  identical with and without the cached per-document statistics.
- Semantic: one request carries query and warm run; width mismatch and
  out-of-range scores evict; a `rate_limit` sets the memo and the next call
  sends nothing; a success clears it; deadline shared across the retry.
- Merge: RRF order; no number on `Evidence` other than per-signal raw values;
  `merge(a, b)` keeps both rounds' signals and dedupes by identity across a
  rename.
- Coverage: each verdict; tier 2 skipped without semantic.
- Expansion: windows, padding, merging, far-edge trim; no anchor -> header and
  summary only; the regex rule a test installs is applied.
- Budget: the chain minimum; unknown windows ignored; `context_budget` share
  only when set; no item below `MIN_ITEM_TOKENS`.
- Live set: a SearchDocument superseded by an edit is unreachable (03-C2), with
  its old row still in the cache.

**Context:**

- Golden: `test_lore_golden.py` scenarios with the new code and the key unset
  are byte-identical; no embed client is constructed and no compiled-cache
  read happens (a spy on both).
- The section renders, packs, sheds lowest-ordered first under a budget,
  re-renders without re-drawing a macro, and drops whole when its last unit
  sheds.
- No repeated summary for an archive scene or a full-recap scene.
- Actor-scoped compose: empty, in the narrator's round as well as the NPC's.
- `test_reasons_never_reach_the_prompt` extended.

**Routes:**

- `post_chat` with retrieval on composes with evidence, retrieval runs on the
  portal, and the turn's prompt-log capture carries the history row.
- A group round retrieves before `_prepare`, and the guard fails a retrieval
  placed inside its lock.
- The live inspector sends no embed and no decide request.
- Rerank: an abstention keeps RRF order; an unresolvable route skips with a
  reason; a timeout honours `RERANK_CEILING`.

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
5. Off is byte-identical; on, the section is the first content to give way.

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
3. **Who owns the rerank task and route (02-C5 vs 09)?** 02-C5 promises
   "decide routes and tasks for ... retrieval relevance, for 11 and 09", and
   the routing guard forbids a route whose tasks nothing uses.
   *Recommendation:* 02-C5 names the task (`history-rerank`) and the route
   (`history_check`, Decision role, `legacy="summary"`), and the route lands
   in 09's rerank slice with its call site; 10 then adds its
   `history-sufficiency` task to the same route. This is a **missing edge**
   (09 <- 02-C5, soft) that the checklist does not list.
4. **Query versus document embeddings in one space (01h-C1/C3).** A query
   embedded with `input_type=query` is compared with documents keyed under
   `input_type=document`. *Recommendation:* 01h states that the document's
   options are part of the vector key and that a query vector is never cached,
   so the two are one comparable space by definition; 09 relies on that
   statement and asks 01h to make it explicit.
5. **The opener and the other composes.** The opener runs on the loop and
   has no player post; mechanics continuations and replay compose
   mid-turn. *Recommendation:* leave all three out of 09; add the opener
   later with the greeting text as the query, once the async path is proven
   on the round driver.
6. **Retrieval inside the scene being played.** A scene longer than the
   packed history loses its early posts. *Recommendation:* a separate item
   after 10; it needs post-level selection 08 defers, and a different
   eligibility rule.
7. **Retrieval latency before the first frame on `post_chat`.** Retrieval
   (and later 10's planner) runs before the detached run starts, so the
   player waits for it before the lead frame. *Recommendation:* accept for
   09 under `RETRIEVAL_DEADLINE`; moving `compose_turn` into the run is a
   larger change worth its own spec once 10 adds model calls to the phase.
8. **Share the outage memo with semantic lore recall?** *Recommendation:* yes,
   keyed by space id in one place (`embed_space`), in a follow-up; lore recall
   pays the same deadline per turn today.

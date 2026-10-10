# 08. Derived history SearchDocuments

**Status:** Draft — spec gate (substitute review) folded in; Codex gate pending.
**Date:** 2026-10-09
**Roadmap:** 08 in `ROADMAP-CHECKLIST.md`. Lane: cache (03 → 05, with 07 and
01h) feeding retrieval (09, then 11 and 12).
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** the bundle draft `08-derived-history-search-documents.md`
(2026-10-06, written against `7f80c42`), against current code and against the
reconciled 03 (`2026-10-09-roadmap-03-content-addressed-compiled-cache-design.md`,
sections 2a, 4, 6, 9, 10 and 12). Nothing in the repo specifies
SearchDocuments today; the only prior mention is 03's section 2a. Section 16
records the substitute spec-gate review and what was done with each finding.

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning. In particular: 03, 05, 07, 09 and 01h are
> not landed at the baseline, and every interface this spec names on them is
> a requirement on that spec, not a description of code.

## Depends on

The checklist's 08 edge: 03-C1, 03-C2, 03-C6, 03-C7 (H); 03-C3, 05-C3 (H for
C2c); 01h-C3 (H once C1 sends a type); 07-C2, 07-C3c, 01h-C1 (S). This spec
asks for two changes to that edge (section 16): 01h-C3 becomes soft, and 03-C9
is added as soft.

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| 03-C1 | 03 | The document's composite key: kind, version, `BUILD`, and per-slice digests carried as inputs that are not files (section 5). | Hard |
| 03-C2 | 03 | Liveness by construction: a superseded document has no live key that reaches it, so no "retire the old one" step exists here (section 6). | Hard |
| 03-C6 | 03 | Batch lookup of documents by the caller's live key set; the index-ranking rule that 09's lexical ranking inherits (section 6). | Hard |
| 03-C7 | 03 | Vectors keyed by space and exact text, never by `BUILD`: a re-render that yields the same text costs no embedding (section 8). | Hard |
| 03-C3 | 03 | The `materialized` record, with the vector kind `vector:<projection>:<space-digest>` and the optional `instance` column: which scene paths a document, and a document's vector, were built from, and for which scene identity. 08-C2c reads it (section 9). | Hard for 08-C2b and C2c |
| 05-C3 | 05 | The `WarmHook` protocol 08-C2c implements, run by 05's sync (section 9). | Hard for 08-C2c only. 08-C1, C2a and C3b do not need it and can land first. |
| 01h-C3 | 01h | The space id is opaque to 08 and already carries any stated embedding options (01h section 5.1), so documents are read and saved under `space["space"]` unchanged (section 8). | Soft (the checklist says hard once C1 sends a type; under 01h as written, nothing in 08 changes when it lands) |
| 01h-C1 | 01h | `embed_sync(..., queries=0)`: every text 08 sends is document-side (section 8.2). | Soft: `queries=0` is the default, and until 01h lands the call is today's |
| 07-C2 | 07 | Derived inverse membership (actor to groups) for the `groups` metadata field (section 4.6). | Soft: groups are metadata only |
| 07-C3c | 07 | `scene_groups`: where it has landed, the `groups` slice reads it rather than joining 07-C2 per cast member (section 4.6). | Soft |
| 03-C9 | 03 | The synthetic-library generator the cost measurements in sections 5 and 6 run on. | Soft (not in the checklist edge; requested) |

03-C8 (the purge on a world or campaign delete) is not an edge: 08 adds no
storage of its own, and 03-C8 covers its documents (section 12).

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for | Hard or soft |
|---|---|---|---|
| 08-C1 | 09 | The per-scene document text (lexical and semantic candidates) and metadata (structural prefilter). | Hard |
| 08-C2a, C2b | 09 | The live document set without parsing transcripts; vectors addressed by `(space["space"], text)`, the same key 09 loads with; `record_embedded` after 09's own turn warm saves document vectors (section 8.3). | Hard |
| 08-C3a, C3b | 09 | The rule that out-of-turn document embedding meters under `history-index` while 09's turn warm meters under its own `history-recall` (section 8.4); prompt-phase excerpts with post keys as evidence. | Hard |
| 08-C3b | 11 | Excerpt posts with their keys (`p-<post_id>` / `r-<response_id>`) and per-post prompt-view texts, for actor-knowledge classification and split rendering. | Soft |
| 08-C3a, C3b | 12 | `get_scene_excerpt` (12-C1) through `expand(..., phase="prompt", max_posts=)`; `search_history` embeds through 09, so under `history-recall`, not `history-index` (section 8.4). | Hard |
| 08-C2c | 05 | The `searchdocs` `WarmHook` (section 9). | Soft |

## 1. Current state (reconciled against main)

### 1.1 What a scene is on disk

- A scene is one file, `<campaign>/scenes/<sid>.md`
  (`store/scenes/paths.py:22`): frontmatter, then a flat transcript of
  `**Speaker:**` blocks. The frontmatter carries `title`, `created`,
  `updated`, `location_history` and `time_history` (comma-joined,
  `scenes/read.py:234`), the rolling summary fields (`rolling_summary`,
  `rolling_at`, `rolling_digest`, `rolling_facts`, `scenes/read.py:271`), the
  scene-break fields (`scenes/read.py:323`), `pcless`, `branch_group` /
  `branch_of`, `identity`, and, once absorbed, `one_line`, `summary` and
  `done: true` (`scenes/write.py:782`, `mark_absorbed`).
- **Each message carries its own ids** in an HTML-comment metadata line
  (`scenes/serialize.py:430`, `RESPONSE_METADATA`): `post_id` on a player
  post, `response_id` (and `response_part`) on a reply, `excluded` on a
  hidden post. The tracker keys records by them, never by index
  (`tracker/walk.py:33-44`, `tracker/paths.py:50`: `p-<post_id>`).
- **The `sid` is not stable and not unique over time.** It is the filename
  stem (`scene_ids.py`), it moves on rename and on a width re-pad, and the
  number of a deleted scene is handed to the next one
  (`scenes/identity.py:1-20`). The stable token is `identity`, 32 hex,
  minted at creation, read only if it matches `_TOKEN`
  (`scenes/identity.py:32`), and `None` for a scene the backfill has not
  reached (`scenes/identity.py:200`). `read_scene` strips it from payloads
  (`scenes/read.py:146`).
- `list_scenes` reads frontmatter heads only, memoized by stat signature in a
  dedicated pool (`scenes/read.py:31`, `:72`), and derives `closed_by` for a
  branch whose sibling was absorbed (`scenes/read.py:86`). Closedness is not
  stored anywhere. One unreadable head raises `OSError` out of the whole
  listing (`_scene_row` → `parse_frontmatter_head`).
- **The rolling summary** is on by default: one fold every ten posts
  (`store/config.py:142`), one line of prose (`rolling_summary.parse_output`).
  **It is not invalidated when the transcript under it changes.** Hiding a
  post (`scenes/write.py:603`, `set_excluded`) or cutting
  (`scenes/write.py:470-487`) leaves the fields in place; only response-ledger
  edits blank them (`responses.py:631`, `_invalidate`). The app's own reader
  decides intactness at read time: both `covered_digest` over the prompt view
  of the covered prefix (which drops hidden posts, `rolling_summary.py:58-63`)
  and `facts_digest` must still match, or the summary is `stale` and never
  sent as `prior` (`routes/scenes.py:3628-3640`, `_rolling_digest` at
  `:3648`).

### 1.2 What is filed against a scene, and where

Every one of these is keyed by `sid`, which `scene_refs.repoint` follows on
rename (`store/scene_refs.py:1-60`):

| Source | Shape | Written by |
|---|---|---|
| `chronicle.json` (`chronicle.py:37`) | `{sid: {id, one_line, summary, keywords[], cast[], location, date, absorbed}}` | absorb (`chronicle.absorb`); `set_line` corrects `one_line`/`date` only (`chronicle.py:63`) |
| `appearances.json` (`appearances/__init__.py:1-8`) | `{"<kind>/<id>": {version, base, scenes[], role, presence{sid: intervals}}}` | appear/leave, absorb |
| `plot.json` (`plot.py:37`) | `{pid: {title, status, beats[{scene, text}], last_scene}}`; `last_scene` is one field, the latest (`plot.py:53`) | absorb; continuity review against any live scene (`continuity/review.py:598`) |
| `commitments.json` (`commitments.py:1-60`) | as plot, plus `kind`, `due` | absorb; continuity review |
| `facts.json` (`facts.py:15-19`) | `{fid: {text, date, scene, status, superseded_by, retired_scene}}` | absorb; the user's own corrections (`routes/ledger.py`) |
| `relationship_history.json` (`relationship_history.py:13-17`) | append-only `entries[{ts, scene, source, kind, a, b, label, before, after, scene_gone?}]`, `source` `absorb` or `undo`; retention drops the oldest past `RETENTION = 2000` rows or `MAX_BYTES` (`:84-89`) | absorb; a reversal appends an `undo` row (`undo.py:584-588`) |
| `continuity.json` (`continuity/doc.py:3-8`) | `links{id: {a, b, relation, created, scene, note}}` (the scene a link was accepted against, any live scene, `routes/continuity.py:275`) | review |
| `events.json` (`events.py:1-12`) | `{eid: {name, date, note, fired}}` | **no scene link at all** |
| `timeline.md` | append-only prose lines, no scene ids (`timeline.py:11-17`) | absorb |

Threads and commitments have an alias layer: `continuity/effective.py:385`
(`records`) merges an aliased group under its live canonical.
`involvement.scene_actors` (`continuity/involvement.py:101`) is the one
scene-to-actors join: the appearance record unioned with the chronicle's
cast snapshot.

**A deleted scene leaves its sid-keyed rows behind.** `delete_scene`
(`scenes/lifecycle.py:210`) drops prompt snapshots, presence, commit state and
pins, and marks relationship rows `scene_gone`
(`relationship_history.py:247`), but it does **not** drop the chronicle
record, plot or commitment beats, or facts. A cut or a retcon reverts the
chronicle record and beats through `cascade.py:224`, but deliberately not
`facts.json` (the cascade docstring's "deliberately NOT reverted" list), so a
retconned scene that is absorbed again carries both the old and the new facts
recorded under its `sid`. `routes/scenes.py:2736` (`_already_absorbed`) is
written around the recycled-id case: it reads the scene's own `done`, never
`sid in chronicle`.

### 1.3 Projections that already exist over these

- `timeline.build` (`timeline.py:118`) reads heads, `chronicle.json` and
  `plot.json`, and opens no transcript. Its field rules are the precedent
  this spec follows: the chronicle is the source of `one_line` (falling back
  to `summary`) and of the absorb-time `location`; the head's first moment is
  the scene's date.
- `context/story._story_entries` (`context/story.py:155`) is the recap: the
  last N chronicle records' `one_line` or `summary`, every turn.
- `chronicle.scene_facts` (`chronicle.py:188`) resolves cast refs, the current
  location's display name and the current moment for a scene;
  `appearances.cast.scene_cast` (`appearances/cast.py:137`) names the cast as
  the play view does, at each actor's locked version.

### 1.4 Embedding today, and under 01h as written

- **Lore recall** (`context/semantic.py`) embeds world-info entries clipped
  to `DOC_BYTES = 6000` UTF-8 bytes (`:102`, `:203`), the scan window as the
  query in the same request, at most `WARM_LIMIT = embeddings.BATCH - 1`
  uncached entries per turn (`:128`; `BATCH = 64`, `embeddings.py:59`) on the
  rotating `embed_space.warm_window` (`embed_space.py:261`). A vector of the
  wrong width is forgotten (`semantic.py:295`, `:305`).
- **Library search** (`semsearch.py`) embeds every `search.walk` document,
  scene transcripts included, as raw passages of `PASSAGE_BYTES = 1500`
  (`:81`, `:125`).
- **Vectors** (`store/vectors.py`): one file each under `.cache/embeddings/`,
  keyed `sha256(space \0 text)` (`:115`), CRC-checked, every failure a miss,
  nothing pruned.
- **The embed operation** `embed_sync` (`inference/embed.py:143`) refuses a
  task outside `routing.EMBED_TASKS` (`:166`; `routing.py:174`), files one
  ledger row per call, never re-resolves the `space` it is handed, and has no
  input type today.
- **01h as written** (`2026-10-09-roadmap-01h-embedding-options-design.md`)
  adds `queries: int` to `embed_sync` (01h-C1: the first `queries` texts are
  query-side, the rest documents, default 0) and puts stated options into the
  space id (01h-C3). Document vectors stay shared under the plain space id
  across every producer; a query vector is never cached. 08 needs nothing
  more than that (section 8).
- `test_operation_guard.py` holds that every embed task is the literal of a
  call (`:308`), that every call's `space=` traces to `embed_space.endpoint`
  through package call sites (`:689`; a function with no package caller, or a
  method, fails, `:597-628`), and that `MIN_EMBED_CALLS = 6` calls exist
  (`:184`). `test_inference_embedding.py:269` pins the tuple.

### 1.5 Regex phases

`store/regex/view.view` (`regex/view.py:49`) runs rules over **transcript
messages**, by role and by depth counted over the whole transcript. Nothing
applies rules to record text. `test_regex_prompt_guard.py` scans `routes/`
and `store/context/` (`:73`); its rules key on `transcript_text`,
`absorb.materialize`, pinned names, and functions that read a scene, read
`content` and render a template. The context builder views the whole
transcript and then projects it, dropping director notes and hidden posts and
reducing images to alt text (`context/assemble.py:176`, `context/story.py:18`).

### 1.6 Lexical search

`store/search.py` walks every file per query, memoizes per-file extraction by
stat signature in its own pool (`search.py:274`), and matches case-folded
substrings with phrases kept whole (`query_terms`, `:174`).

### 1.7 What the draft got wrong against this code

- **"Events/anchors" per scene.** Events carry no scene id; relating one to a
  scene by date runs calendar plugin code. Out of scope (section 14).
- **"Groups relevant" as document text.** Membership is current state, not
  history. Section 4.6 keeps it in metadata.
- **"Recent/important quoted terms".** A transcript-derived field makes the
  document depend on the body and the rule files, and re-embeds on every
  post. Section 3.2 keeps transcript text out.
- **The key "is the digest of source hashes".** Keyed on whole files, every
  absorb would invalidate every document. Section 5 keys on per-scene slices.
- **The draft names no stable ref.** It has to be the identity (section 4.5).

## 2. Goal (and what is explicitly not the goal)

**Goal.** A compact, deterministic, bounded document per scene that a
historical retrieval layer (09) can filter, match and embed, built only from
what the store records, never itself authoritative, and cheap enough to exist
for every scene of a long campaign. Plus the pieces 09, 11 and 12 need around
it: the rule and task for embedding documents outside a turn, and a helper
that turns "this scene was selected" into bounded, keyed transcript evidence
in the right regex phase.

1. **08-C1.** One `SceneDocument` per live scene: text of at most `DOC_BYTES`
   UTF-8 bytes, metadata for structural prefiltering, and a key computed from
   per-scene input slices plus `search_document_version`.
2. **08-C2a/b/c.** Documents are built lazily and stored in 03's cache;
   vectors are kept in `vectors.py` under the Embedding role's plain space and
   the exact text, the key every document producer uses; 05's sync rebuilds
   what was hot through a `searchdocs` `WarmHook`.
3. **08-C3a/b.** A registered `history-index` embed task, and the
   transcript-expansion helper.

**Not the goal.** Retrieval, ranking, a prompt section, a token budget, the
query embedding or the turn's warm run (09); post-level vectors; document
families other than scenes; replacing `semsearch.py`, the recap or the
timeline; starting any run or warming any campaign on its own initiative.

## 3. Design principles

### 3.1 The document is an index, not a record

A SearchDocument may be deleted at any time and is never the only copy of
anything. Nothing reads a document to decide a write, a sync outcome or what
a model is told is true: retrieval uses it to *find* a scene, and the
evidence it then shows is read from the transcript and the records (08-C3b,
and 09).

### 3.2 No transcript text in the document

The document is built from the scene head and from records. It contains no
message text:

- **Churn.** An open scene's transcript changes every post. A document that
  quoted it would get new text, and so a new embedding, every turn.
- **Regex phase.** Transcript text that is sent (and an embedding request is
  a send) must be the prompt view, which would put every rule file into the
  key. Record text is not subject to regex rules anywhere in the app
  (section 1.5), so the document has **no regex phase** (`meta.phase:
  "none"`).
- **Privacy surface.** What the embedding provider receives is the class of
  text the recap already sends the chat provider (chronicle summaries), plus
  names and ledger lines.
- **What makes a scene findable is already summarized**: the chronicle
  `summary` and `keywords`, and for an unabsorbed scene the rolling summary,
  **when it is still intact** (section 4.2). The transcript is consulted after
  selection, through 08-C3b.

The cost is a thin document for an unabsorbed scene whose rolling summary is
off or stale. Open question 4.

### 3.3 Keys from slices, not from whole files

Each input is first cut to the **slice** that concerns this scene, each slice
is canonicalized and digested, and the key is over the digests (03-C1 allows
inputs that are not files). An absorb of scene 40 moves scene 40's slices.
It moves another scene's only through something they share (section 5).

### 3.4 Determinism

Same store state, same text, byte for byte, across calls, processes,
platforms, hand-edited key orders and Python's hash seed. Every slice list is
sorted before it is digested, in the order it renders (section 4.3); every
clip is a UTF-8 byte clip at a character boundary; whitespace is collapsed one
way; nothing reads the clock, the config, a calendar plugin or a random
source.

## 4. The scene SearchDocument (08-C1)

### 4.1 Module layout

A new package, `backend/src/grimoire/store/searchdocs/`, with a docstring-only
`__init__.py` (the `continuity` precedent):

| Module | Holds |
|---|---|
| `slices.py` | `CampaignInputs.load(cid)` (each ledger parsed once per signature, tolerantly), the name tables, `scene_slices(inputs, sid, head) -> Slices` |
| `rolling.py` | `intact_rolling(cid, sid, head) -> str` (section 4.2) |
| `scene.py` | `SEARCH_DOCUMENT_VERSION`, `DOC_BYTES`, the caps, `render(slices) -> SceneDocument`, `key_of(slices)` |
| `live.py` | `scene_documents(cid, ...) -> LiveSet` (08-C2a) |
| `embedded.py` | `TASK = "history-index"`, `vector_kind(space)`, `record_embedded(...)`, `vectors_for(...)` (08-C2b, 08-C3a) |
| `hook.py` | the `searchdocs` `WarmHook` (08-C2c) |
| `expand.py` | `expand(...)` (08-C3b) |

Data carriers are frozen dataclasses or plain dicts, never pydantic models.
Everything that leaves the package is JSON-safe.

### 4.2 Slices

`CampaignInputs.load(cid)` parses `chronicle.json`, `appearances.json`,
`facts.json`, `relationship_history.json` and `continuity.json`'s links, and
the alias-resolved `effective.records` for threads and commitments. **Each
parse is memoized in process by the stat signatures of the files it read**
(`statcache.memo` in this package's own pool, sized like
`scenes/read.POOL_ENTRIES` for its reason), so a turn-path call re-parses only
what changed; `effective.records` is memoized on the signatures of
`plot.json`, `commitments.json` and `continuity.json` together, because its
alias walk reads all three. Each read is guarded on its own, as
`timeline.build` guards its three (`timeline.py:136`): a file that will not
parse, or parses to the wrong shape, contributes nothing and is named in
`degraded`.

Two name tables are built once per campaign state, memoized the same way:

- **actor ref → display name**, resolved as `scene_cast` resolves it
  (`overlay.actor_root` at the record's locked version,
  `appearances/cast.py:145-151`), so the document names whom the play view
  names; a chronicle-only cast token with no appearance record, or an actor
  whose card will not read, falls back to its id;
- **location id → name** (`overlay.read_entity`), falling back to the id.

Heads are read with `parse_frontmatter_head`, memoized by stat signature.

`scene_slices` cuts what concerns one scene. Every slice is a plain JSON value
whose strings are whitespace-collapsed and byte-clipped to the caps in section
4.3, and whose lists are sorted as they render, so its digest moves only when
the text or metadata it can contribute moves:

| Slice | Contents | Admitted on an unabsorbed scene |
|---|---|---|
| `head` | `title`; `identity` (or `""`); `done`; `pcless`; `branch_group`; `created`; location ids and time moments (history order) | yes |
| `rolling` | `intact_rolling(...)`: the rolling summary if, and only if, the app's own intactness check passes; else `""` | yes (empty once absorbed: the chronicle wins) |
| `chronicle` | this scene's record: `one_line`, `summary`, `keywords`, `date`, `location`, `cast` tokens; or the frontmatter `one_line`/`summary` when the record is missing; `null` when neither | no |
| `cast` | `[{ref, name, role, present}]`: every actor whose appearance record lists this scene or holds a presence interval for it, plus (absorbed only) the chronicle cast; `ref` in the `characters:mara` form (`canon.actor_ref`) | yes (the chronicle half no) |
| `locations` | `[{id, name}]` for the location history | yes |
| `threads` | `[{ref, title, beats[]}]`: canonical threads with at least one beat filed under this `sid`; beats in stored order | no |
| `commitments` | `[{ref, title, beats[]}]`, as threads | no |
| `status` | `{ref: status}` for those threads and commitments, current (metadata only) | no |
| `facts` | `{recorded: [{id, text}], retired: [{id, text}]}` | no |
| `relationships` | `[{label, kind, before, after}]` (the rule below) | no |
| `links` | ids of continuity links with `scene == sid` | timed: only links whose `created` is not earlier than the head's `created` |
| `groups` | 07-C3c's `scene_groups(index, cast, visible=None, limit=None)`: every group one of whose **affiliated** actors (members **or leader**) is in the cast, uncapped, gm-only groups included and marked (metadata only; section 4.6) | yes |

**The absorbed gate.** A row keyed by a `sid` that the scene's own head does
not mark absorbed is, in the common case, a row a deleted scene left behind
for whichever scene inherited its number (section 1.2). The gate applies
`_already_absorbed`'s rule (`routes/scenes.py:2736`) to every untimed join.
It has two costs, stated rather than hidden:

- **A legitimate row filed on an open scene is invisible until absorb.** A
  continuity review can file a beat against any live scene
  (`continuity/review.py:598-615`), so a beat the user files against the
  scene being played is admitted only once that scene is absorbed. A row that
  carries a timestamp is admitted on its own merits: a link whose `created`
  is not earlier than the head's `created` cannot predate this scene, so it
  cannot belong to a dead one.
- **A recycled `sid` that is later absorbed** still inherits a dead scene's
  beats and facts under that `sid` (absorb replaces only the chronicle
  record). That residual is every sid join's in the app today (timeline,
  briefing, involvement); fixing it belongs to scene deletion (section 14).

**Relationships.** Only `source == "absorb"` rows with `scene == sid` and no
`scene_gone`, in file order, and an absorb row is dropped when a later
`source == "undo"` row with the same scene, kind and pair (as a set) swaps its
`before` and `after`. So a scene that was retconned and absorbed again lists
the delta it now has, not the original, its reversal and the new one
(`undo.py:584-588`). Retention can still drop an old scene's rows when later
scenes append; its line then empties and its key moves. That is the store
forgetting, and the document follows it.

**Facts.** Recorded and retired facts are taken as the ledger holds them. A
retconned and re-absorbed scene can list a fact the retcon superseded in
fiction but the ledger still holds active (section 1.2): the ledger is the
authority, and the document does not second-guess it.

**The rolling summary only when intact.** `intact_rolling(cid, sid, head)`
returns the head's `rolling_summary` only when the same test the app uses to
decide `prior` passes: `rolling_at` within the transcript, `covered_digest`
over the **prompt view** of the covered prefix equal to `rolling_digest`, and
`facts_digest` of the scene's facts equal to `rolling_facts`
(`routes/scenes.py:3628-3640`). The plan extracts that test from the route
into one store function both call (in a module that may import `scenes` and
`regex`; not `rolling_summary.py`, which `scenes.write` imports). Because
`covered_digest` drops hidden posts and covers content, hiding a folded post,
cutting into the covered prefix, editing a covered post, or a prompt rule
changing a covered post all make the summary stale, and the document then
carries no running summary until the next fold writes a fresh one. Text the
player has hidden, cut or replaced never reaches the document, the cache, the
embedding provider or 09.

The test reads the transcript body, so it runs only for an **unabsorbed scene
with a non-empty rolling summary**, and it is memoized on the stat signatures
of everything it reads: the scene file, `appearances.json`, the current
location's entity file, and the regex rule files that apply (the plan adds a
signature helper to `regex/layers.py`, which already caches parsed rule files
by path and bytes). An open scene pays one parse per post that lands; a
finished-but-unabsorbed scene pays once per process.

**Summary source**, first non-empty: the chronicle `summary`, the frontmatter
`summary` (both only when absorbed), then `intact_rolling`. Labelled `Summary`
for the first two and `Running summary` for the third.

### 4.3 Rendering and bounds

The text is a fixed sequence of labelled lines; an empty line is omitted. An
item over its cap is clipped with `embed_space.clip` (`embed_space.py:240`) to
the cap minus three bytes and given `…` (three bytes). Items in a line are
joined with `"; "`. **Every suffix and separator inside an item counts within
that item's cap.**

| # | Label | Source | Items (order) | Bytes per item | Line cap |
|---|---|---|---|---|---|
| 1 | `Scene:` | head title | 1 | 160 | 160 |
| 2 | `When:` | date part (before `T`) of the first and last moment; else the chronicle `date` | 2, joined ` to ` | 48 | 100 |
| 3 | `Where:` | location names in history order, then the chronicle `location` if not among them; deduplicated case-folded; the last 4 | 4 | 64 | 264 |
| 4 | `Cast:` | players first, then the rest, each by `(kind, id)`; a player's item is `name (player)`, the suffix inside the 48 | 12 | 48 | 600 |
| 5 | `In brief:` | chronicle `one_line` | 1 | 240 | 240 |
| 6 | `Summary:` / `Running summary:` | section 4.2 | 1 | 1500 | 1500 |
| 7 | `Keywords:` | chronicle keywords, stored order, deduplicated case-folded | 12 | 32 | 408 |
| 8 | `Threads:` | by canonical ref; `title: beat / beat` | 5 | 144 | 730 |
| 9 | `Commitments:` | by canonical ref; `title: beat / beat` | 3 | 144 | 438 |
| 10 | `Facts established:` | by `(paths.natural_key(id), id)` | 4 | 144 | 584 |
| 11 | `Facts retired:` | by `(paths.natural_key(id), id)` | 2 | 144 | 292 |
| 12 | `Relationships:` | file order; `label: before -> after` | 3 | 112 | 342 |

The line caps sum to 5658 bytes, the labels and newlines to at most 145, so a
document is at most 5803 bytes **by construction**. `DOC_BYTES = 6000`
(`context/semantic.py:102`'s constant, for its reason: bytes bound tokens
from above in every script). The renderer still clips the whole text to
`DOC_BYTES` as a backstop and records `backstop: true` if it ever fires; a
test holds that it never does. The caps are structural, not measured, and
will be tuned against 09's evals.

**Nothing a later scene can change about this scene's fiction is rendered.**
A thread's or commitment's status, a commitment's kind and due date are
current state: rendering "(promise, broken)" on the scene where the promise
was made would date the outcome onto the scene that set it up, and would
re-embed every scene of a thread each time it moves. They are in `meta` as
current state (section 4.4), as groups are. What can still move this scene's
text from elsewhere is a shared record's **title**, a rename of an actor or
place, an alias merge, and relationship-history retention (section 5).

Whitespace in every value is collapsed with `" ".join(s.split())`. No Unicode
normalization. **A lone surrogate** (a hand-edited `"\ud800"` survives
`json.loads`) is replaced, deterministically, by encoding the value with
`errors="replace"` and decoding it again, and its input is named in
`degraded`; digests encode with `errors="surrogatepass"`, so neither path can
raise. Labels are English and fixed; changing them is a version bump.

Example, with the codebase's placeholder names:

```
Scene: The ledger at Saltmarch
When: 1203-frost-12 to 1203-frost-13
Where: Saltmarch; Saltmarch harbour
Cast: Seraphine (player); Mara; Winifred
In brief: Mara gives up the harbour ledger to keep Winifred out of it.
Summary: Seraphine corners Mara in the counting room ...
Keywords: harbour ledger; tide tax; counting room
Threads: The missing ledger: Mara hides it under the floor of the counting room
Commitments: Repay Winifred: Seraphine swears to clear the debt by spring
Facts established: The harbour ledger records the tide tax twice
Relationships: Mara → Seraphine: wary -> grudging trust
```

### 4.4 Metadata

`meta` is a JSON object stored beside the text, for structural prefiltering
without reparsing sources. It is **bounded by construction**: each list
except `groups` holds at most `MAX_META_REFS = 64` entries in the slice order,
with `truncated` naming any list that was cut, `groups` is bounded by the cast
times the groups each actor is in and is never truncated (section 4.6), and
every string is byte-capped (`title` 160,
`chronicle_date` 48, each moment 64, each keyword 32, each ref 160).

| Field | Meaning |
|---|---|
| `kind`, `version` | `"scene"`, `SEARCH_DOCUMENT_VERSION` |
| `phase` | `"none"`: the text contains no transcript message |
| `identity` | the scene identity, or `""` before backfill |
| `title` | head title |
| `absorbed`, `pcless` | from the head |
| `branch_group` | `""` or the group token |
| `summary_source` | `chronicle`, `frontmatter`, `rolling` or `none` |
| `moments` | `{first, last}` native moments |
| `chronicle_date` | the record's free-text date |
| `locations` | `locations/<id>` refs, history order |
| `cast`, `players`, `departed` | actor refs, `characters:mara` form |
| `threads`, `commitments` | canonical refs with a beat filed under this scene |
| `current_status` | `{ref: status}` for those, **as of now**, not as of the scene |
| `links` | admitted continuity link ids |
| `facts` | `{recorded: [ids], retired: [ids]}` |
| `relationship_pairs` | `[[a, b], ...]`, each pair sorted, the list sorted |
| `groups` | `scene_groups` output, **current affiliation** (members or leader), uncapped |
| `keywords` | case-folded chronicle keywords |
| `degraded` | inputs that would not read, or held a lone surrogate |
| `bytes`, `backstop` | the text's UTF-8 length; whether the backstop clip fired |
| `slices` | `{name: digest}` (diagnostics, and 05's report) |

The draft's `touched` (a record that reached the scene only through
`last_scene`) is not kept. `last_scene` is a single latest-scene field
(`plot.py:53`), so it cannot say historically which scenes a record touched:
the entry would vanish from one scene the moment a later scene moved the
record. The beats already say it, historically.

**Not in the stored metadata: the `sid` and `closed_by`.** The `sid` moves on
a re-pad and on the date-slug rename `set_datetime` makes, without any slice
moving, and closedness is derived from the siblings. Both are attached outside
the cache by the live set (section 6), which is 03 section 6's rule for
path-derived inputs.

### 4.5 The stable ref

`scene:<identity>` is the ref a consumer stores or compares. A scene with no
identity yet has `identity: ""`; a consumer refers to it by `sid` for one
query only and never persists it. 08 never mints an identity (`ensure_identity`
is a write, and this package writes nothing to the store).

### 4.6 Groups are metadata only

07-C2 answers "which groups is this actor in *now*". Rendering that into a
historical scene's text would date a membership the fiction may not have had
yet, and a membership edit would re-embed every scene the actor appeared in.
As metadata it costs a key change and a re-render, no embedding (03-C7).

**"Belongs to" means affiliated: a member or the leader**, as 07-C3c defines
it. The slice is 07-C3c's `scene_groups(index, cast, visible=None,
limit=None)` over the scene's full cast (not the 64-ref `meta.cast` list):
every affiliated group, **uncapped and unfiltered**, gm-only rows included and
marked by their `secrecy`, so 09's structural prefilter misses no group
because of a cap nobody decided on. The slice digest is over the canonical
JSON of that output, so a membership or leadership change moves only the
scenes whose projection moved, not every scene in the campaign. A renderer
that puts group names into a prompt (09) applies `prompt_visible` and
`SCENE_GROUP_LIMIT` itself; 08 never renders them. Until 07-C3c lands and
only 07-C2 exists, 08 computes the same projection from the inverse index,
leaders included; until 07 lands at all, the slice is empty.

## 5. Keys and `search_document_version` (08-C1)

Each slice's digest is
`sha256(json.dumps(slice, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "surrogatepass"))`,
over a slice whose lists are already sorted as they render. The document key
is 03-C1's composite key:

```
key_digest = 03.key(kind    = "searchdocs.scene",
                    version = SEARCH_DOCUMENT_VERSION,     -- in the key (03 section 6)
                    BUILD,                                 -- 03's fingerprint
                    params  = {"slices": {name: digest, ...}},
                    inputs  = [])                          -- no whole-file input
```

- **`SEARCH_DOCUMENT_VERSION = 1`** is the registry kind's `version` and is in
  the metadata. Bumping it moves every key and re-embeds only documents whose
  text changed (03-C7).
- **`BUILD`** colds every document after an upgrade; the vectors survive.
- **The compute receives what the key was computed from**: `render` is a pure
  function of the slices it is handed. It reads nothing.
- **What moves this scene's key.** A change to any of its own slices: its
  head (a title rename, a moment, a location move), an intactness flip or a
  refold of its rolling summary, its chronicle record, a beat or fact filed
  under it, a relationship delta in it, a cast or location rename, a group
  membership change, `SEARCH_DOCUMENT_VERSION`, `BUILD`.
- **Absorbing or editing another scene** moves this scene's key only through
  what the two share: a thread's or commitment's title or alias merge, a
  shared record's current status (metadata), a rename, or relationship-history
  retention dropping this scene's rows. It never moves through another
  scene's own record, beats or facts.
- **Appending a post** changes the file's `updated` and body. Neither is in a
  slice; the key holds, except that the post makes an existing rolling summary
  no longer cover the tail, which is not staleness (the prefix is unchanged),
  so the key still holds until a fold lands.

**Why the document artifact is persisted at all.** Rendering from slices is
cheap, so 03 section 8's cost test would not justify persisting it for its
compute alone. It is persisted because it is the **row an index ranks** (09's
lexical index restricted to live keys, 03-C6) and the artifact the
`materialized` record describes. What makes a cold process expensive is
gathering slices; persisting those as 03 kinds keyed by file hash is allowed,
and wired only if 03 section 8's synthetic-library measurement (03-C9) shows
the win. The in-process memos of section 4.2 are always on.

## 6. The live set and lazy build (08-C2a)

```python
def scene_documents(cid: str, *, exclude: frozenset[str] = frozenset(),
                    only: frozenset[str] | None = None) -> LiveSet: ...

@dataclass(frozen=True)
class LiveDocument:
    sid: str                    # current
    closed_by: dict | None      # list_scenes' derivation, never cached
    doc: SceneDocument          # key, text, meta

@dataclass(frozen=True)
class LiveSet:
    documents: tuple[LiveDocument, ...]   # play order
    skipped: tuple[str, ...]              # sids whose head could not be read
    degraded: tuple[str, ...]             # campaign inputs that would not read
```

1. **The live set** comes from the filesystem on every call (03-C6, 03
   section 9): `list_scenes(cid)` (`scenes/read.py:72`), which also derives
   `closed_by`. Because one unreadable head makes `list_scenes` raise, an
   `OSError` from it falls back to this package's own walk: the same glob and
   `safe_id` filter, each head read guarded on its own, an unreadable one
   named in `skipped`, and `closed_by` derived by the same `_resolve_groups`
   rule (the plan makes it callable over rows). `exclude` drops sids (09
   excludes the scene being played); `only` restricts (05's hook).
2. `CampaignInputs.load(cid)`: memoized parses (section 4.2).
3. For each live scene: head, slices, digests, key.
4. **One batch lookup** of all keys in 03's cache (03-C6).
5. Every miss is rendered from the slices in hand and stored in one write
   batch, with `materialized` rows (03-C3) for every store-relative path its
   slices read: `campaigns/<cid>/scenes/<sid>.md`, each ledger file that
   contributed, the cast cards and location files whose names it used, 07's
   group files. Each row's `instance` is `{"campaign": cid, "identity":
   <identity>}`.
6. Return in play order (`sid` order, `scene_ids.py:1-5`).

Properties:

- **Liveness.** A document is only ever found through a key computed from the
  current slices, so a superseded document is never returned, whether or not
  any sync ran (03-C2).
- **Never raises for store damage.** A garbled ledger empties its slice and is
  named in `degraded`; an unreadable head is `skipped`. A campaign that does
  not exist raises `CampaignNotFound`.
- **Cache off** (`GRIMOIRE_COMPILED_CACHE=0`, a missing Android `BUILD` stamp,
  03 not landed): step 4 misses and step 5 stores nothing. Documents are held
  in an in-process FIFO memo keyed by the same key, bounded at
  `DOC_MEMO_ENTRIES = 8192` documents (at most about 48 MB of text at the
  worst-case 5803 bytes, typically far less; tuned later). Answers are
  identical (section 13).
- **No lock.** A read that crosses an absorb's sequence can pair a new
  chronicle slice with an old plot slice; the document is keyed by exactly
  those slices, so it is self-consistent with what it read, and the next call
  computes the new keys. Taking the lock would put a turn-path reader behind
  an absorb save.
- **Threads.** Called from threadpool workers and detached runs, never from
  the event loop.
- **Cost on the turn path.** With the section 4.2 memos warm, a call is a
  listing, a stat per ledger, per head and per name source, a slice and a
  digest per scene, and one batch lookup. A ledger that changed is parsed once
  per change, not once per turn. The plan measures the per-scene digesting on
  03-C9's synthetic library and memoizes the per-campaign slice table on the
  input signatures if it shows.

## 7. Search metadata and the structural prefilter

08 does not filter; it makes filtering cheap. Every field 09's structural
signals need is in `meta`, as refs in the forms the rest of the app joins on:
actors as `characters:<id>` / `pcs:<id>` (`continuity.involvement`,
`canon`); threads and commitments as canonical, alias-resolved
`thread:<id>` / `commitment:<id>`; locations as `locations/<id>`; groups as
07's ref form. A closed branch (`closed_by` set) is an alternative that did
not happen; 08 reports it and 09 decides (it should drop it by default).

## 8. Embeddings (08-C2b, 08-C3a)

### 8.1 One key for document vectors, shared with 09

Document vectors are read and saved under **`space["space"]`**, the plain
space id of the Embedding role's endpoint (`embed_space.endpoint()`,
`embed_space.py:62`), and the exact document text: `vectors.load(space["space"],
texts)` and `vectors.save(space["space"], text, vector)`. That is the key every
document producer uses today and keeps under 01h as written: 01h-C3 puts any
stated options into the space id itself, and a document is the default side
(01h section 3.4, 5.1-5.3). So 08 and 09 address a scene document's vector by
the same `(space["space"], text)` key: 09 loads what 08's hook embedded, and
08's hook finds what 09's turn warm saved. Neither derives a key of its own.

The `materialized` kind for such a vector is
**`vector:searchdocs.scene:<space-digest>`** (03-C3's form, the checklist's
cross-spec decision), written by `embedded.vector_kind(space)`, where
`<space-digest>` is `compiled.space_digest(space)`, the one function 03-C3
defines and 05 uses for its own rows (05-C3), so 03, 05, 08 and 09 compute the
same digest. The projection name keeps a scene's
document vector apart from library search's passage vectors over the same
file.

### 8.2 The task and its one call site

`routing.EMBED_TASKS` gains `"history-index"`: **document embedding outside a
turn**. Its one call site is `embedded.vectors_for`, and its only caller is
the `searchdocs` hook's network phase (section 9). It lands in the same
change as that hook, because `test_every_embed_task_is_named_by_a_call_site`
(`test_operation_guard.py:308`) fails a task with no call site, and because
the provenance walk fails a `space` parameter with no package caller or one
reached only through a protocol method (`:597-628`). So 08-C2b's
`vectors_for` and 08-C3a land with 08-C2c, never standalone.
`test_inference_embedding.py:269` gains the entry and `MIN_EMBED_CALLS`
becomes 7. CLAUDE.md's embedding paragraph names the new caller.

```python
TASK = "history-index"

def vectors_for(cid: str, docs: list[SceneDocument], *, space: dict,
                client: embeddings.EmbeddingsClient, limit: int) -> DocVectors: ...

@dataclass(frozen=True)
class DocVectors:
    vectors: dict[str, list[float]]   # document key -> unit vector
    embedded: int                     # vectors saved by this call
    error: str | None                 # the embed failure's kind, if one
```

1. `space` is handed in, never re-resolved here. Its caller is a plain
   module-level function in `hook.py` that resolves `embed_space.endpoint()`
   once (section 9), so the provenance walk has a package call site to follow.
2. `vectors.load(space["space"], texts)` finds what is already held; the
   misses, deduplicated, are cut to `limit` on `embed_space.warm_window(misses,
   cid, limit)`, a rotating proper subset, so a document the provider refuses
   cannot pin the head of every run.
3. One `embed_sync(TASK, texts, space=space, client=client, queries=0,
   campaign=cid, cached=..., uncached=...)` call (`queries=0` is 01h-C1's
   default; before 01h lands the argument is absent). No `scene`: a warm-up is
   not part of playing a scene (05 section 6.6). One ledger row, one capture
   line with counts only.
4. Each vector is saved with `vectors.save(space["space"], text, vector)` and
   `record_embedded` writes its `materialized` row (below).
5. Failure is never raised: an `LLMError` or `OSError` returns what was saved
   with `error` set to the kind; the meter has recorded it.

### 8.3 Recording what was embedded, by whoever embedded it

```python
def record_embedded(cid: str, docs: list[LiveDocument], *, space: dict) -> None:
    """For each doc whose text now has a vector under space["space"], write
    a row on EVERY input path the document read (the scene file and each
    shared ledger file its slices came from), kind vector_kind(space), with instance
    {"campaign": cid, "identity": <identity>}. No network; never raises."""
```

`vectors_for` calls it after saving. **09 calls it too** after its turn warm
saves document vectors under `history-recall` (section 8.4), because 05's hot
rebuild (05-C3) re-embeds only rows that exist: a document embedded by 09 and
never recorded would never be kept current. A hit also refreshes the row's
coarse `last_used` (03 section 10). `record_embedded` lands with 08-C2a and
needs no embed task.

### 8.4 Who meters what

- **A turn's warm run is 09's**: the query and the bounded uncached documents
  in one request under 09's `history-recall` task, with `queries=len(query
  texts)` (01h-C1), attributed to the turn's campaign and scene (09 section
  6.3). It is part of what that turn cost.
- **`history-index` is only out-of-turn re-embedding**: 08's hook, run by 05's
  sync. Campaign-attributed, no scene. History is per campaign, so one call
  never spans two (01h-C5 is not needed).
- **12's `search_history`** embeds through 09, so under `history-recall`
  (12 should say so; section 16).
- An endpoint that reports no price files an unpriced row; an OpenRouter embed
  reports real spend against the campaign budget, which only warns. 08 never
  decides to spend: it embeds only when 05's sync runs its hook, and only what
  was already embedded in the current space.

What 08 does not do: score, compare widths against a query, or forget a
mismatched vector. Those are 09's, with `vectors.forget(space["space"], text)`
as `context/semantic.py:295` does.

### 8.5 Why vectors stay in `vectors.py`

03 section 4 left the choice to 08 or 09, on one condition: the key stays
`(space, exact text)` and independent of `BUILD`. `vectors.py` is that key,
its integrity rules are justified on the read path, and every producer shares
it. Moving document vectors into SQLite for batch loading is 09's call once it
measures loading a campaign's vectors per turn.

## 9. Hot rebuild: the `searchdocs` `WarmHook` (08-C2c)

08-C2c is a `WarmHook` (05-C3, 05 section 6.2) registered with 05 under the
name `searchdocs`, third in 05's fixed order (after `files` and `overview`,
before `vectors`).

```python
class SearchDocsHook:
    name = "searchdocs"
    on_write = "explicit"
    def kinds(self) -> frozenset[str]: return _kinds()
    def local(self, ctx, hot) -> LocalResult: return _local(ctx, hot)
    def network(self, ctx, plan) -> NetworkResult: return _network(ctx, plan)
```

The methods delegate to plain module functions, so the embed call site is
reached from a module-level function whose `space` the provenance walk can
trace (section 8.2).

- **`on_write = "explicit"`.** 05's write-through queue skips the hook; the
  CLI and API sync (05-C2) run it. App writes need no eager rebuild: a
  document re-renders lazily on the next read at the cost of a slice and a
  digest, and the only expensive half, the vector, is picked up by 09's next
  turn warm within its bound. An open scene's document moves only on a
  refold or a ledger write anyway, so there is nothing a quiet period would
  save. Eager rebuild earns its place for **external edits** (an agent editing
  `chronicle.json` through the store-editing skill), which is what explicit
  sync is for. This answers 05's open question 5.
- **`kinds()`** returns `{"searchdocs.scene", vector_kind(endpoint)}` when the
  Embedding role names a space, else `{"searchdocs.scene"}`. It is resolved
  when 05 asks, once per batch. A vector row from another space is then
  claimed by no hook, which 05 reports as `lazy`; that matches 05's
  `stale_space` rule (05 section 6.5): a moved space is not re-embedded
  without the move's own confirmation.
- **`_local(ctx, hot)`**, no network:
  1. Groups `hot` paths by campaign. A path under `campaigns/<cid>/scenes/`
     names that scene. Any other campaign path names every live scene of `cid`
     whose scene path has a `searchdocs.scene` row: the hook reads its **own**
     kind's rows by path for the live scene paths it lists (03-C3's by-path
     read; it never sees another hook's rows). A `worlds/<wid>/...` path does
     the same for each campaign whose world is `wid`, and for each campaign
     whose world `campaigns.read.world_refs()` reports as `None` (it may
     reference anything).
  2. Rebuilds those documents through 08-C2a (`only=`).
  3. For each rebuilt scene whose path has a current-space vector row **whose
     `instance.identity` equals the scene's current identity**, puts the
     document text in the `EmbedPlan` if `vectors.load` misses it, claimed by
     `cid`. A row left by a deleted scene whose `sid` was recycled names
     another identity and is ignored, so a scene nobody embedded is never
     embedded because its number used to belong to one that was.
  4. Returns per-path outcomes (`hit`, `built`, `skipped(reason)`,
     `failed(class)`), holding no content.
- **`_network(ctx, plan)`** resolves `embed_space.endpoint()` once; if it is
  `None`, or its `vector_kind` differs from the plan's, it sends nothing
  (`embedding_off` / `stale_space`). Otherwise it calls `vectors_for` per
  campaign, bounded by `REINDEX_LIMIT = embeddings.BATCH - 1` documents per
  campaign per sync (one round trip; the rest stay for 09's lazy warm; tuned
  later). It never runs on a request path, under a lock, or on the event loop
  (05 section 6.2).

Without 05, nothing is wrong: every read computes current keys (03 section 2a
item 2), and 09's turn warm re-embeds what changed.

## 10. Transcript expansion (08-C3b)

Selection finds a scene; the evidence is in its transcript. The helper reads
it and returns bounded, keyed, phase-correct excerpts around the posts that
match the caller's terms.

```python
PHASES = ("prompt", "display")
RADIUS = 1                  # neighbouring posts on each side of a hit
MAX_EXCERPTS = 3
MAX_POSTS = 8               # posts across all excerpts
MAX_BYTES = 4000            # speaker labels plus contents, UTF-8
MESSAGE_BYTES = 1500        # one post (semsearch.PASSAGE_BYTES)
MIN_POST_BYTES = 120        # a hit cut shorter than this is not worth sending
MAX_TERMS = 16

def expand(cid: str, sid: str, *, phase: str, terms: Sequence[str],
           identity: str | None = None, radius: int = RADIUS,
           max_excerpts: int = MAX_EXCERPTS, max_posts: int = MAX_POSTS,
           max_bytes: int = MAX_BYTES, fallback: str = "none",
           allow_whole: bool = False) -> Expansion | None: ...

@dataclass(frozen=True)
class ExcerptPost:
    index: int          # absolute transcript index at read time
    key: str            # "r-<response_id>", else "p-<post_id>", else ""
    part: int | None    # response_part, for a reply split across posts
    role: str
    speaker: str        # as rendered (transition lines: "")
    content: str        # in the named phase
    clipped: bool

@dataclass(frozen=True)
class Excerpt:
    start: int                        # index of the first post
    end: int                          # exclusive
    posts: tuple[ExcerptPost, ...]
    matched: tuple[str, ...]          # terms this excerpt contains
    text: str                         # the posts rendered as history lines

@dataclass(frozen=True)
class Expansion:
    sid: str
    identity: str
    phase: str
    mode: str                         # "matched" | "whole" | "tail" | "empty"
    budget_exhausted: bool            # matches existed but none fit
    total: int
    eligible: int
    excerpts: tuple[Excerpt, ...]     # transcript order
    bytes: int
```

### 10.1 Steps

1. **Phase is required and checked.** `"prompt"` or `"display"`; anything else
   (`"store"` included) raises `ValueError`. There is no default. **Every
   caller whose excerpts reach a prompt names `phase="prompt"`** (09 section 8
   asks for exactly this, and 11 and 12 follow it); `"display"` is only for a
   surface that shows a reader what retrieval found. The guard rule in section
   12 enforces it.
2. **The right scene.** If `identity` is given and the `sid` no longer carries
   it, `scenes.find_by_identity` (`scenes/identity.py:305`) follows a rename;
   if no scene carries it, the answer is `None`. The identity is checked again
   after the transcript is read.
3. **The view, over the whole transcript.** `read_scene` (raw), then
   `regex_view.view(messages, cid=cid, phase=phase)` over the whole list, so
   depth counts exactly as the context builder counts it
   (`context/assemble.py:176`).
4. **Eligible posts.** `in_context` semantics: a hidden post and a director
   note are never returned, in either phase. Transition lines are kept with no
   speaker (as `transcript_text` renders them, `chronicle.py:205`); roll lines
   are kept. Indices are absolute.
5. **Keys.** Each post's `key` is `r-<response_id>` when it has a
   `response_id` (every part of one reply shares it, and `part` tells them
   apart), else `p-<post_id>` when it has a `post_id`, else `""` (a legacy
   post, a synthetic line). That is the tracker's precedence
   (`tracker/walk.py:33-44`) in 11's spelling, and it survives a cut that
   renumbers indices. 09's `EvidencePost.key` carries it unchanged.
6. **Images.** In the prompt phase every markdown image becomes its alt text
   (`export.drop_images`, `store/export.py:306`). The display phase leaves
   them.
7. **Matching.** Terms are case-folded, whitespace-collapsed, deduplicated, at
   least two characters, at most `MAX_TERMS` (`search.query_terms`'s
   normalization, `search.py:174`). A post's score is the number of distinct
   terms in its **phased** content or speaker, then occurrences capped at 5 per
   term, then lower index. Matching the phased text keeps a term a prompt rule
   strips from selecting a post whose sent text no longer contains it.
8. **Windows.** Hits in score order each claim `radius` eligible posts either
   side; overlapping or adjacent windows merge; stop at `max_excerpts` or
   `max_posts`.
9. **Bytes**, counting each post's speaker label and content:
   - a hit post is cut to at most `min(MESSAGE_BYTES, remaining budget)`,
     framed on its rarest matching term (`search.snippet`'s rule,
     `search.py:617`); a neighbour is cut from the end nearer the hit;
   - **every cut ends at a whitespace boundary within the byte window**, so a
     word is never split, except a single token longer than the window, which
     is cut at a character boundary;
   - windows are added in score order; one that does not fit is shrunk to its
     hit post alone, and a hit that cannot get `MIN_POST_BYTES` stops the run;
   - if matches existed but no window fit, `mode` is `"empty"` and
     `budget_exhausted` is true, never `"matched"` with nothing in it.
10. **No match.** `mode = "empty"`, or with `fallback="tail"` the last
    `2 * radius + 1` eligible posts within the budget.
11. **Whole.** With `allow_whole=True` and every eligible post fitting in
    `max_bytes` and `max_posts`, the whole eligible transcript, `mode =
    "whole"`.
12. **Text.** Each excerpt's `text` is rendered inside `expand` by
    `chronicle.transcript_text` over the viewed posts, with the scene's player
    label, in the same function that called `view`, which the existing regex
    guard accepts.

### 10.2 Properties

- **Bounded by construction**: at most `max_excerpts` excerpts, `max_posts`
  posts and `max_bytes` bytes, labels included, whatever the transcript's
  length. The read is a whole-file parse; what is bounded is what reaches the
  caller. 09 sets the budget from its section budget (01i-C1); 12 passes its
  tool's `max_posts`.
- **Rendering a sub-range is the consumer's.** 11 renders a split excerpt from
  the per-post `content` it already holds (each already in the prompt view),
  as 11 section 4.4 allows. 08 exposes no free-standing renderer: one that
  accepted arbitrary posts would be a `transcript_text` over text no `view`
  call is seen producing, which is the case the regex guard exists to catch.
- **Live, never cached.** Its answer depends on the terms and the rule files.
- **Read-only, no lock, no log of content.** A failure to read is `None`.
- **Deterministic** for a given transcript, rule set and terms.

## Slices

Landing order within this spec: S1 → S2 → S3 → S5, with S4 in parallel to any
of them and S6 after S2. Each slice is one PR. (These are landing slices; the
per-scene input "slices" of section 4.2 are a different thing.)

### 08-S1: Extract the rolling-summary intactness test

- **Delivers:** 08-C1 (part: the shared intactness test `intact_rolling`
  relies on, section 4.2)
- **Needs (this spec):** none
- **Needs (other specs):** none
- **Scope:** Moves the test that decides `prior`/`stale` out of
  `routes/scenes.py` (`:3628-3640`, `_rolling_digest` at `:3648`) into one store
  function, in a module that may import `scenes` and `regex` (not
  `rolling_summary.py`). The route calls it; its behaviour, responses and
  prompts are unchanged. Adds the rule-file signature helper to
  `regex/layers.py` that section 4.2's memo needs. No new feature.
- **Acceptance:** the existing rolling-summary route tests pass unchanged; a
  unit test runs the extracted function over hidden, cut, edited and
  rule-changed prefixes and agrees with the route's `stale`; the layers
  signature moves when a rule file changes.
- **Size:** S

### 08-S2: The scene SearchDocument, in process

- **Delivers:** 08-C1 (part: every slice except `groups`, rendering, bounds,
  metadata, key)
- **Needs (this spec):** 08-S1 (H)
- **Needs (other specs):** 03-C1 (H: composite key over non-file inputs, with
  the registry kind's `version` and `BUILD`); 03-C9 (S: synthetic-library
  generator; until it lands, the cost check in section 6 runs on a hand-built
  fixture)
- **Scope:** Adds `store/searchdocs/` with `slices.py`, `rolling.py` and
  `scene.py`: `CampaignInputs.load` with the signature-memoized ledger parses,
  `effective.records` and name tables; `scene_slices` with the absorbed gate,
  timed links, absorb-only relationship rows and intact-only running summary;
  `render`, the caps and backstop, `meta`, `key_of`. Pure and read-only:
  nothing calls it yet, and no cache, embedding or route is touched. The
  `groups` slice is empty (S6 fills it).
- **Acceptance:** section 13's 08-C1 tests except "Groups": determinism,
  bounds, lone surrogate, key moves and holds, shared records, hidden and cut
  posts (the word is absent from the text and the artifact; the embed-call
  half is S5's), relationships after a retcon, recycled id, timed-row gate, no
  transcript text, degraded, identity.
- **Size:** L

### 08-S3: The live set, the compiled cache, and recording embedded vectors

- **Delivers:** 08-C2a (full); 08-C2b (part: `vector_kind` and
  `record_embedded`, no network)
- **Needs (this spec):** 08-S2 (H)
- **Needs (other specs):** 03-C6 (H: batch lookup over the caller's live key
  set); 03-C2 (H: liveness by construction); 03-C3 (H: `materialized` rows
  keyed by `(path, kind, instance)`, written beside an artifact, read by path,
  and `compiled.space_digest`); 03-C7 (H: vectors keyed by space and text,
  never `BUILD`); 03-C8 (S: the purge on a world or campaign delete; until it
  lands, the delete test is skipped)
- **Scope:** Adds `live.py` (`scene_documents`, `LiveSet`, the tolerant
  fallback walk with `skipped`, one batch lookup, lazy build with
  store-relative `materialized` rows carrying `{campaign, identity}`, the
  bounded cache-off memo) and the no-network half of `embedded.py`
  (`vector_kind`, `record_embedded` writing rows on every input path the
  document read). Extends the frozen-campaign sweep with scene documents,
  minus `identity`, `slices` and the key, and regenerates `snapshot.json`
  deliberately. Nothing embeds; no route calls it until 09.
- **Acceptance:** section 13's 08-C2a tests (equivalence cache on, warm, off
  and after restart; no superseded document; one batch lookup; unreadable head
  skipped; `materialized` rows and dropping the table; memoized parses; memo
  bound; campaign-delete purge); the `record_embedded` test; the reviewed
  frozen-campaign snapshot.
- **Size:** M

### 08-S4: Transcript expansion and its guard rule

- **Delivers:** 08-C3b (full)
- **Needs (this spec):** none (can land in parallel with S1 to S3)
- **Needs (other specs):** none
- **Scope:** Adds `expand.py`: required phase, identity checks, whole-transcript
  view, `in_context` filtering, post keys and parts, alt-text images in the
  prompt phase, term matching, windows, the byte and post budgets with
  word-boundary cuts and `budget_exhausted`, `fallback`, `allow_whole`, and
  each excerpt's `text` rendered with `transcript_text` beside its `view`.
  Extends `test_regex_prompt_guard.py`: scans `store/searchdocs/` and
  `store/history/` (absent until 09, scanned harmlessly), and adds the rule
  that an `expand(...)` call in a rendering or pinned function passes the
  constant `phase="prompt"`, with planted cases. No caller in the app until
  09 and 12.
- **Acceptance:** section 13's 08-C3b tests, including the key-stable-across-a-cut
  test and the guard's planted display and variable-phase readers.
- **Size:** M

### 08-S5: The `searchdocs` hook, `vectors_for`, and the `history-index` task

- **Delivers:** 08-C2b (full); 08-C2c (full); 08-C3a (full)
- **Needs (this spec):** 08-S3 (H)
- **Needs (other specs):** 05-C3 (H: the `WarmHook` protocol registered in
  `cache_sync.HOOKS`, with `on_write="explicit"`, `kinds()`, `local`,
  `network` and `EmbedPlan` campaign claims, and a hook reading its own kinds'
  rows by path); 03-C3 (H: as S3); 01h-C1 (S: `embed_sync(..., queries=)`;
  until it lands, the call omits `queries` and is today's)
- **Scope:** Adds `hook.py` (`SearchDocsHook` delegating to module-level
  `_kinds`, `_local`, `_network`; the identity check on vector rows;
  `REINDEX_LIMIT`) and `vectors_for` with its one `embed_sync` call. Adds
  `"history-index"` to `routing.EMBED_TASKS`, updates
  `test_inference_embedding.py:269` and `MIN_EMBED_CALLS = 7`, and names the
  caller in CLAUDE.md. These land together because
  `test_operation_guard.py` fails a task with no call site (`:308`) and a
  `space` parameter with no module-level package caller (`:597-628`). The
  hook runs only under 05's explicit sync.
- **Acceptance:** section 13's 08-C2b vector tests (shared key with 09's,
  rotating limit, one ledger row, failure, no scene), the 08-C2c hook tests
  (re-embed only what was embedded and changed, world and unreadable-world
  campaigns, recycled-`sid` rows ignored, moved space and embedding off send
  nothing, write-through skipped, `local` makes no network call), the
  hidden-post test's embed-call half, and the operation guard.
- **Size:** M

### 08-S6: Group metadata from 07

- **Delivers:** 08-C1 (full: the `groups` slice)
- **Needs (this spec):** 08-S2 (H)
- **Needs (other specs):** 07-C3c (H: `scene_groups(index, cast, visible=None,
  limit=None)`, affiliation including leaders, uncapped); 07-C2 (H: the
  inverse membership index `scene_groups` reads)
- **Scope:** Fills the `groups` slice from `scene_groups` over the full cast,
  exempt from `MAX_META_REFS`, gm-only rows marked, digested as canonical
  JSON. Moves every key once (a metadata change, no embedding, since the text
  does not change). Until this slice, `meta.groups` is empty.
- **Acceptance:** section 13's "Groups" test (membership edit moves key and
  `meta`, not text; a leader-only group listed; more than 64 groups all
  listed; gm-only marked), and no embed call across the key change.
- **Size:** S

## 11. Contract

### 08-C1: A bounded `SceneDocument` with no transcript text, keyed by per-scene slices

- **Input:** a campaign id and a live scene, plus the campaign's memoized
  inputs (`searchdocs.slices`).
- **Output:** `SceneDocument{key, text, meta}`: `text` at most 5803 UTF-8
  bytes by construction (`DOC_BYTES = 6000` backstop); `meta` bounded as
  section 4.4; `key` as section 5.
- **Guarantees:**
  - the same slices give the same text, metadata and key, byte for byte; slice
    lists are sorted before digesting;
  - the text contains no transcript message (`meta.phase == "none"`); a rolling
    summary appears only while the app's own intactness test passes, so a
    hidden, cut or edited post never reaches it;
  - nothing rendered can be changed by a later scene except a shared record's
    title, a rename, an alias merge or relationship-history retention;
    current status and group membership are metadata, labelled current;
  - untimed sid-keyed rows contribute only to a scene whose head says it is
    absorbed; timed rows (links) also when they postdate the head's `created`;
    relationship rows are absorb rows not reversed by a later undo;
  - neither the `sid` nor `closed_by` is in the cached payload.
- **Failure:** never raises for a damaged ledger, card or lone surrogate; the
  input is named in `meta.degraded`. Raises `CampaignNotFound`.

### 08-C2a: Lazy build plus batch lookup

- **Input:** `scene_documents(cid, exclude=, only=)`.
- **Output:** `LiveSet{documents, skipped, degraded}`.
- **Guarantees:** the live set is read from the filesystem each call; ledger
  parses and name tables are memoized by stat signature; keys are computed
  before any lookup; one batch lookup per call (03-C6); a miss is built from
  slices in hand and stored with store-relative `materialized` rows carrying
  `{campaign, identity}` (03-C3); a superseded document is unreachable
  (03-C2); answers are identical with the compiled cache on, warm, cold or
  off; the cache-off memo is bounded.
- **Failure:** cache failures are misses; an unreadable head is `skipped`.

### 08-C2b: Document vectors

- **Input:** `record_embedded(cid, docs, space=)` (no network; lands with
  C2a); `vectors_for(cid, docs, space=, client=, limit=)` (network; lands with
  C2c, its only caller).
- **Guarantees:** document vectors are read and saved under `space["space"]`
  and the exact text, the key 09 loads with, never under `BUILD` (03-C7); the
  `materialized` kind is `vector:searchdocs.scene:<space-digest>`, written on
  every input path the document read, with `instance {campaign, identity}`; `vectors_for` embeds at most `limit`
  documents per call with `queries=0`, one ledger row under `history-index`,
  campaign-attributed, no scene.
- **Failure:** never raises; returns what was saved and the error kind.

### 08-C2c: A hot-rebuild hook for 05

- A `WarmHook` named `searchdocs`, `on_write = "explicit"`, `kinds()` =
  `searchdocs.scene` plus the current space's vector kind; `local` rebuilds
  hot documents with no network and plans re-embedding only for documents
  embedded in the current space under the same scene identity whose text
  missed; `network` resolves the space once, refuses a moved or absent space,
  and re-embeds at most `REINDEX_LIMIT` per campaign.
- **Failure:** a failed rebuild or re-embed leaves the next read to rebuild
  lazily; it never fails the write or sync batch that triggered it.

### 08-C3a: A `history-index` embed task

- `"history-index"` in `routing.EMBED_TASKS`, for document embedding outside a
  turn only; call site `embedded.vectors_for`, landed with 08-C2c; no route
  claims it; `resolve.resolve` refuses it (`inference/resolve.py:694`). A
  turn's warm run is 09's `history-recall`.

### 08-C3b: `expand(...)`, where the caller names the phase

- **Input:** `expand(cid, sid, phase=, terms=, identity=, radius=,
  max_excerpts=, max_posts=, max_bytes=, fallback=, allow_whole=)`.
- **Output:** `Expansion` or `None`; each post carries its absolute index, its
  key (`r-<response_id>` / `p-<post_id>` / `""`), its part, and its content
  in the named phase.
- **Guarantees:** `phase` is required and only `prompt` or `display`; a
  caller whose excerpts reach a prompt passes the constant `"prompt"` (held by
  the guard); the whole transcript is viewed before matching; hidden posts and
  director notes are never returned; prompt-phase images are alt text; at most
  `max_excerpts`, `max_posts` and `max_bytes` (labels included); cuts never
  split a word except an overlong token; a run that fits nothing reports
  `budget_exhausted`; keys are stable across a cut; the identity is checked
  before and after the read; deterministic.
- **Failure:** `None` for a scene that is gone or no longer carries the
  identity; `ValueError` for a bad `phase`; never a write, never a log line
  with content.

## 12. Interaction with repo rules

- **Embedding call sites.** One door (`embed_sync`), a task in `EMBED_TASKS`,
  `space` resolved once from `embed_space.endpoint()` in a module-level
  function and handed down (section 9), the cache read under it before
  embedding, one row per call that sent a request. `vectors_for` and the task
  land with the hook that calls them (section 8.2).
- **Regex.**
  - The document has no phase, and a test holds it.
  - The plan adds `store/searchdocs/` and 09's `store/history/` to
    `test_regex_prompt_guard._sources` (`:73`; coordinated with 09 section 8),
    and adds one rule: **every `expand(...)` call in a function that renders a
    template, or in a pinned reader, passes the constant `phase="prompt"`**.
    The existing rules cannot see such a reader (it never reads a scene
    itself), which is why a rule of its own is needed. The guard's own tests
    gain a planted reader that renders a prompt from `expand(...,
    phase="display")` and must fail, and one that passes a variable phase and
    must fail. No `# regex-ok` marker is spent.
  - `expand` renders each excerpt's `text` with `transcript_text` over the
    list `view` returned in the same function, which the existing
    `transcript_text` rule accepts.
- **Imports.** Module-scope, acyclic, submodule bindings inside `store/`.
  Nothing the package imports imports it back. The extracted rolling-summary
  intactness test lives below both `routes/scenes.py` and `searchdocs`.
- **Locks.** Nothing here mutates campaign state, so no `store/locks.py`
  entry (a phantom fails `test_lock_domain_guard.py`). Reads take no lock.
- **Atomic and paths.** No record writes; vectors through `vectors.save`;
  SQLite is 03's. Every path from an existing resolver.
- **Revision token.** Cache and vector writes stamp nothing.
- **Detached runs.** 08 starts none. 05's sync and 09's warming carry their
  own rules, including the `PUT /config/data-dir` refusal.
- **Metering and errors.** Failures are recorded at `Meter.done`, kind and
  status only. A degraded input logs once per kind per process, naming the
  input and never its text.
- **Privacy.**
  - Document text is derived private text in 03's per-device file, covered by
    03-C8's purge on a world or campaign delete. 08 adds no storage location.
  - **A rolling summary reaches the document only while intact** (section
    4.2), so text the player hid, cut or replaced is not in the cache, the
    vector cache, an embedding request, or anything 09 renders.
  - **A deleted scene.** Its document row becomes unreachable at once and is
    evicted by LRU, 03's residual for every derived row. For an **absorbed**
    scene that row holds what the store still keeps (`delete_scene` leaves the
    chronicle record and ledger rows, section 1.2). For an **unabsorbed**
    scene it holds the title, cast, locations and an intact running summary,
    which the store no longer holds once the transcript is unlinked: they stay
    in the compiled cache until LRU evicts them, and the document's vector, if
    one was made, stays under `.cache/embeddings/` until a manual purge, since
    nothing prunes vectors. Its `materialized` vector row also survives, but
    names the dead scene's identity, so a scene that inherits the `sid` is
    never re-embedded because of it (section 9). Open question 6.
  - **Vectors on a world or campaign delete are a stated residual** (the
    checklist's cross-spec decision): 03-C8 purges the compiled cache;
    `vectors.py` files are keyed by space and text, never by campaign, so they
    cannot be purged selectively; no vector purge is added; the Settings page
    says so, and a full purge of `.cache/embeddings/` is a manual action.
  - The embedding provider receives document text only when 05's sync
    re-embeds what was already embedded, or when 09 warms; both through the
    Embedding role the user chose and confirmed. 08 sends nothing on its own.
  - Fixtures use invented names only (Seraphine, Mara, Winifred, Realm,
    Saltmarch). No constant here is justified by a measurement of anyone's
    store.
- **Android and pydantic.** Pure Python; SQLite only through 03, which
  degrades to "cache off". No `BaseModel`.
- **Frozen campaign.** The read-only sweep gains the frozen campaign's scene
  documents: the text and the metadata with `sid` attached, **excluding**
  `meta.identity`, `meta.slices` (the `head` digest covers the identity) and
  the key (which covers `BUILD` and moves on every commit). Identities are
  fresh `uuid4`s (`read._without_identity`'s docstring), so including them
  would make `snapshot.json` differ on every regeneration. The snapshot is
  regenerated deliberately in the PR that adds them.
- **Docs.** CLAUDE.md's embedding paragraph names the new caller;
  `test_docs_guard.py` is run against it.

## 13. Tests and acceptance

All on `tmp_path` stores (`GRIMOIRE_HOME`), with placeholder names. A fake
embeddings client is injected where a client is a parameter.

**08-C1, document** (`test_searchdocs_scene.py`):

- **Determinism.** Two calls, a simulated restart (statcache and 03 reopened),
  a hand-edited ledger with reordered keys, and threads and links listed in a
  different dict order all render byte-identical text, metadata and key. Two
  fact ids differing only in case order the same way every time.
- **Bounds.** A worst-case fixture (every field past its cap, twelve players,
  CJK and 4-byte characters at every clip point) renders at most 5803 bytes,
  every line within its cap, `backstop` false, every clip on a character
  boundary; `meta` strings and lists within their caps.
- **Lone surrogate.** A `"\ud800"` in a fact's text renders, digests, and is
  named in `degraded`; nothing raises.
- **Key moves.** This scene's chronicle `summary`, `one_line` via `set_line`,
  keywords; a beat filed under it; a cast member renamed; a location renamed; a
  fact recorded and one retired here; a relationship delta here; a refold; a
  title rename (the `sid` moves too).
- **Key holds.** Appending a post; a re-pad; the date-slug rename
  `set_datetime` makes when the first moment does not move the date part;
  absorbing a scene that shares no thread, commitment, actor or place.
- **Shared records.** Absorbing a later scene that resolves a commitment
  opened here moves this scene's `meta.current_status` and key but not its
  text, and triggers no embed call.
- **Hidden and cut posts (B2).** Fold a rolling summary over posts 0-9 whose
  post 7 holds a distinctive word; hide post 7: the word is absent from the
  document text, from the stored artifact, and from every `embed_sync` call's
  texts; `summary_source` is `none` until the next fold. The same for a cut
  into the covered prefix, an edit of a covered post, and a prompt rule added
  over a covered post.
- **Relationships after a retcon.** A delta, its undo row, and a re-absorbed
  delta render only the last.
- **Recycled id.** Absorb a scene, delete it, create one that takes its
  `sid`: the new document carries no chronicle, beats, facts or relationships.
- **Gate on a timed row.** A link filed against the open scene after its
  creation appears in `meta.links`; one whose `created` predates the head's
  does not.
- **No transcript text.** A word only in a post is absent from the text; a
  regex rule that would match the text changes nothing.
- **Degraded.** A garbled `plot.json`, a list-shaped `facts.json`, a card that
  will not parse: the document renders and `degraded` names each.
- **Identity.** A scene without identity has `identity: ""`; 08 writes none.
- **Groups (when 07 lands).** A membership edit moves the key and `meta`, not
  the text; a group whose only affiliate in the cast is its leader is listed;
  a cast in more than 64 groups lists every one, with `groups` absent from
  `truncated`; a gm-only group is listed and marked.

**08-C2a, live set** (`test_searchdocs_live.py`):

- Equivalence with the compiled cache empty, warm, off, and after a restart.
- A superseded document is never returned after an edit, with no sync run.
- One batch lookup per call, keyed only by live keys.
- An unreadable head is `skipped` and every other scene is returned.
- Every stored document leaves store-relative `materialized` rows with
  `{campaign, identity}`; dropping that table changes no answer.
- With the memos warm, a second call parses no ledger (counted on the parse
  seam); an edit to one ledger re-parses that ledger only.
- The cache-off memo never holds more than `DOC_MEMO_ENTRIES`.
- After a campaign delete, no `searchdocs.scene` row remains (03-C8).

**08-C2b and 08-C3a, vectors** (`test_searchdocs_embed.py`):

- `vectors_for` reads and saves under `space["space"]`; a vector saved by a
  stand-in for 09's warm under that key is found, and one saved by
  `vectors_for` is found by `vectors.load(space["space"], text)`.
- At most `limit` texts per call on a rotating window; a permanently refused
  document does not stop the others.
- One ledger row per call under `history-index`, campaign set, scene empty; a
  failure returns what was saved and the kind; a deadline spent before sending
  files nothing.
- `record_embedded` writes `vector:searchdocs.scene:<space-digest>` with the
  scene's identity on every input path the document read (the scene file and
  each ledger file its slices came from), and the digest is
  `compiled.space_digest(space)`.
- `test_operation_guard.py` passes with `MIN_EMBED_CALLS = 7`, the provenance
  walk reaching `vectors_for` through the hook's module-level function;
  `test_inference_embedding.py:269` carries the new tuple.

**08-C2c, hook** (with 05's harness):

- Three scenes' documents built, two embedded. An explicit sync of
  `chronicle.json` rebuilds the three, re-embeds only the embedded one whose
  text changed, and embeds nothing for the never-embedded one.
- A world location edit rebuilds hot documents in each campaign of that world
  and in a campaign whose world reference is unreadable; a campaign with its
  own copy re-derives the same keys and embeds nothing.
- A vector row left by a deleted scene, whose `sid` a new scene took, does not
  make the new scene's document re-embedded.
- With the role moved to another space, the old vector rows are `lazy` and
  nothing is sent; with embedding off, nothing is sent.
- The write-through queue skips the hook (`explicit`).
- `local` makes no network call (the fake client asserts it); the summary
  holds no text.

**08-C3b, expansion** (`test_searchdocs_expand.py`):

- `phase="store"`, any other value, and an omitted phase fail.
- A prompt rule that strips a block: a term only inside it matches nothing in
  `prompt` and matches in `display`.
- A depth-ranged rule gives the same post text as `assemble`'s view.
- Hidden posts and director notes never appear; indices are absolute.
- **Keys.** A cut before the excerpt moves `index` and leaves `key` unchanged;
  the parts of one reply share `r-<response_id>` with distinct `part`; a
  player post is `p-<post_id>`; a legacy post is `""`.
- Images are alt text in `prompt`, markdown in `display`.
- `max_excerpts`, `max_posts`, `max_bytes` (labels counted), `radius` and
  `MESSAGE_BYTES` hold on a long fixture; no cut splits a word except a
  single overlong token; windows merge; output is ordered and identical
  across calls.
- `max_bytes` smaller than `MIN_POST_BYTES`: `mode` `empty`,
  `budget_exhausted` true.
- A renamed scene is found by identity; a deleted or replaced one returns
  `None`.
- No match: `empty`, or `tail` with `fallback="tail"`; `whole` only with
  `allow_whole` and a fitting transcript.
- The extended regex guard fails planted readers that render a prompt from
  `expand(..., phase="display")` or from a variable phase.

**Acceptance** (the draft's section 13, reconciled):

1. Scene SearchDocuments are deterministic, bounded and disposable: deleting
   `.cache/compiled/` and `.cache/embeddings/` changes no answer, only cost.
2. A campaign's documents and metadata are retrieved without parsing any
   transcript, except the bounded, memoized intactness test for unabsorbed
   scenes that carry a rolling summary.
3. Their embeddings share the Embedding role's space and the one
   `(space, text)` key every document producer uses.
4. An edit to any input makes the old document unreachable on the next read,
   with or without 05; with 05's explicit sync, what was hot is rebuilt and
   what was embedded is re-embedded, off any request path.
5. Relevant transcript excerpts can be expanded from a selected scene,
   bounded, keyed by post, in the phase the caller names.
6. No text the player hid, cut or replaced reaches a document.

## 14. Non-goals

- Post-level or passage-level vectors over history, until 09's evals show
  scene-first retrieval misses evidence expansion cannot recover.
- Document families for characters, PCs, groups, threads, commitments, events
  or saved scene ideas.
- Events in the scene document.
- Transcript-derived document text (section 3.2; open question 4).
- Ranking, thresholds, a prompt section, a token budget, the query embedding,
  the turn's warm run, widening tiers, an FTS index (09).
- Any background warm-up or startup sweep.
- Fixing the recycled-`sid` hazard of sid-keyed ledgers. It belongs to scene
  deletion.
- A free-standing renderer for arbitrary posts (section 10.2).
- Changing `semsearch.py`, the recap, the timeline or lore recall.

## 15. Open questions

1. **Groups in text or metadata only?** *Recommendation: metadata only;
   revisit if 09's evals show group queries missing.*
2. **The absorbed gate on untimed rows.** It drops rows a recycled `sid`
   inherited, and also hides a beat the user files against the open scene
   until that scene is absorbed (section 4.2). *Recommendation: keep it; admit
   timed rows (links) by timestamp; revisit if beats or facts gain a
   timestamp.*
3. **Rolling summary for unabsorbed scenes.** Now used only while intact,
   which costs one body parse per changed unabsorbed scene. *Recommendation:
   keep; the alternative (dropping it) leaves an unabsorbed scene findable only
   structurally.* An alternative the user may prefer: have every transcript
   writer blank the rolling fields when a change lands inside `rolling_at`,
   as `responses._invalidate` already does, which would make the head
   trustworthy without the body; that touches every `store/scenes` mutator and
   is not recommended for this spec.
4. **A transcript-derived field for scenes with no usable summary?**
   *Recommendation: not in v1; revisit with 09's evals.*
5. **Persist the input projections as 03 kinds?** *Recommendation: only where
   the synthetic-library measurement shows the warm-after-restart win.*
6. **Purge an unabsorbed deleted scene's document and vector?** The route
   reads the identity before deleting, so it could compute the document and
   `vectors.forget` its current text, best-effort. That would remove the
   latest vector only, not older versions'. *Recommendation: no; state the
   residual (section 12), consistent with the cross-spec decision that vector
   files are a residual.*
7. **Vectors on a world or campaign delete.** Decided (checklist): 03-C8
   purges the compiled cache; vector files cannot be purged selectively and
   are a stated residual; no vector purge is added.
8. **`on_write` for the hook.** *Recommendation: `explicit` (section 9); the
   app's writes rebuild lazily and 09's turn warm re-embeds.*
9. **`allow_whole` default.** *Recommendation: off.*

## 16. Review record

**Substitute spec-gate review** (adversarial, 2026-10-09; 3 blocking, 9
should-fix, 15 minor). Each finding was checked against the code and the
sibling specs.

Blocking, all fixed:

- **B1, embed signature and space key.** Confirmed against 01h as written
  (sections 3.4, 5.1-5.3, contract C1/C3): there is no `input_type`, the
  caller passes `queries=`, options enter the space id, and documents stay
  under the plain space. 08 now reads and saves under `space["space"]`, calls
  `embed_sync(..., queries=0)`, digests the plain space for its `materialized`
  kind, and drops the query-typed-key test (section 8.1, 8.2).
- **B2, running summary carried hidden or cut text.** Confirmed
  (`set_excluded` and cuts leave the fields; the route checks at read time).
  The rolling summary is now used only while the app's own intactness test
  passes, through one extracted function (section 4.2), with the test the
  review asked for (section 13).
- **B3, excerpt post keys.** Confirmed (`RESPONSE_METADATA` carries
  `post_id`/`response_id`). Posts now carry `key` in 11's spelling, `part`,
  with a cut test (section 10).

Should-fix, all fixed:

- **S1** status and due removed from text, kept as `meta.current_status`;
  `touched` dropped; the key claim restated; relationship retention named
  (sections 4.3, 4.4, 5).
- **S2** the gate's cost stated; timed rows (links) admitted by `created`
  (section 4.2, open question 2).
- **S3** relationship rows: absorb rows only, minus those a later undo
  reversed (section 4.2).
- **S4** `vectors_for` and `history-index` land with the hook, whose
  module-level function resolves the space (sections 8.2, 9).
- **S5** 08-C2c restated as the `searchdocs` `WarmHook`, `on_write =
  "explicit"`, `kinds()` resolved per batch (section 9).
- **S6** a new guard rule for `expand(...)` callers; `store/history/` scanned
  (section 12).
- **S7** the deleted-scene residual stated exactly; vector rows carry the
  scene identity in `instance` and a mismatch is ignored (sections 9, 12).
- **S8** one rule: a turn's warm run is 09's `history-recall`;
  `history-index` is only out-of-turn re-embedding; `scene=` removed (section
  8.4).
- **S9** ledger parses, `effective.records` and the name tables memoized by
  signature as part of 08-C2a; the cache-off memo bounded (sections 4.2, 6).

Slices added (6 slices). While slicing: `record_embedded` writes rows on every
input path the document read (coordinator edit, now reflected in 08-C2b and
its test), and section 8.1 names `compiled.space_digest` as the digest.

Addendum from 07's review: the `groups` slice is 07-C3c's `scene_groups` with
`visible=None, limit=None`, uncapped (exempt from `MAX_META_REFS`), and counts
a group's leader as affiliated, as 07-C3c is now written (sections 4.2, 4.4,
4.6).

Minor, fixed: M1 (facts are not reverted by a cut; stated), M3 (rename versus
re-pad tests split), M4 (id tiebreak, sorted slice lists, explicit encoding),
M5 (lone surrogates), M6 (suffix inside the cap), M7 (meta strings capped),
M8 (frozen sweep exclusions), M9 (unreadable world references), M10
(store-relative paths), M11 (budget floor, labels counted, word-boundary
cuts), M12 (`max_posts` added; rendering a sub-range stated as the
consumer's), M13 (unreadable head skipped), M14 (03-C9 soft edge).

Minor, partly rejected:

- **M2.** Accepted that names must be read at the locked version, with an id
  fallback; rejected `locked_actor_root` as the root. 08 resolves names as
  `scene_cast` does (`overlay.actor_root` at the locked version,
  `appearances/cast.py:145-151`), because the play view names the cast that
  way and the document should agree with it.
- **M12.** Rejected exposing a pure `render(posts)`: a renderer over arbitrary
  posts is a `transcript_text` call the regex guard cannot tie to a `view`.
  11 already allows rendering from the per-post prompt-view texts.
- **M15.** Fixed the `semantic.py` citation (`:295`, `:305`). Rejected the
  rest: `chronicle.scene_facts` is at `chronicle.py:188`, and
  `scenes/read.py:234/271/323` are `histories`, `rolling_summary_fields` and
  `scene_break_fields` at the baseline.

**For routing to other specs:**

- **01h:** nothing new is required for 08. One confirmation would help:
  `queries=0` stays the default and means "all documents". The shared
  `<space-digest>` is now settled as `compiled.space_digest(space)` (03-C3,
  used by 05-C3); section 8.1 cites it.
  The checklist edge "01h-C3 (H once C1 sends a type)" should become soft.
- **05:** the hook reads its own kind's `materialized` rows by path for the
  live scene paths it lists, not only the paths in `hot`. 05 should state
  that a hook may do this for its own kinds. The hook's `kinds()` is resolved
  per batch. `on_write = "explicit"` answers 05's open question 5.
- **09:** call `searchdocs.embedded.record_embedded` after the turn warm saves
  document vectors, so 05 can keep them current. Adopt the prefixed post key
  (`r-<response_id>` / `p-<post_id>`). Scan `store/history/` under the new
  guard rule.
- **11:** keys are in 11's spelling; split rendering stays 11's, from the
  per-post texts.
- **12:** `search_history` embeds through 09 under `history-recall`, not
  `history-index` (12 line 348). `get_scene_excerpt`'s `max_posts` maps to
  `expand(max_posts=)`.
- **Checklist:** add 03-C9 (S) to the 08 edge.

The `/codex:adversarial-review` gate is still to be run.

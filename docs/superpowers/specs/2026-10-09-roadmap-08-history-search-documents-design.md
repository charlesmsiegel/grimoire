# 08. Derived history SearchDocuments

**Status:** Draft — spec gate (`/codex:adversarial-review`) pending.
**Date:** 2026-10-09
**Roadmap:** 08 in `ROADMAP-CHECKLIST.md`. Lane: cache (03 → 05, with 07 and
01h) feeding retrieval (09, then 12).
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** the bundle draft `08-derived-history-search-documents.md`
(2026-10-06, written against `7f80c42`), against current code and against the
reconciled 03 (`2026-10-09-roadmap-03-content-addressed-compiled-cache-design.md`,
sections 2a, 4, 6, 9, 10 and 12). Nothing in the repo specifies
SearchDocuments today; the only prior mention is 03's section 2a.

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning. In particular: 03, 05, 07 and 01h are
> not landed at the baseline, and every interface this spec names on them is
> a requirement on that spec, not a description of code.

## Depends on

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| 03-C1 | 03 | The document's composite key: kind, version, `BUILD`, and per-slice digests carried as inputs that are not files (section 5). | Hard |
| 03-C2 | 03 | Liveness by construction: a superseded document has no live key that reaches it, so no "retire the old one" step exists here (section 6). | Hard (implied by C6; not listed in the checklist edge) |
| 03-C3 | 03 | The `materialized` record: which scene paths a document, and a document's vector, were built from. 08-C2c's hot rebuild reads it (section 9). | Hard for 08-C2c, soft for the rest |
| 03-C6 | 03 | Batch lookup of documents by the caller's live key set; the index-ranking rule that 09's lexical ranking inherits (section 6). | Hard |
| 03-C7 | 03 | Vectors keyed by space and exact text, never by `BUILD`: a re-render that yields the same text costs no embedding (section 8). | Hard |
| 05-C3 | 05 | Eager rebuild of what was hot after an edit, embedding only what was already embedded. 08-C2c is the hook it calls (section 9). | Hard for 08-C2c only. 08-C1, C2a, C2b and C3 do not need it and can land first. |
| 07-C2 | 07 | Derived inverse membership (actor to groups) for the `groups` metadata field (section 4.6). | **Soft** (the checklist says hard): groups are metadata only, never document text, so the field can follow 07 without re-embedding anything. |
| 01h-C1 | 01h | Embedding documents with the provider's `document` input type, where the provider takes one (section 8). | Soft: until it lands, documents embed untyped, exactly as lore does today. |
| 01h-C3 | 01h | The input type (and any dimensions) is part of the space identity and so of the `vectors.py` key, so a document vector can never be read as a query vector (section 8). | Hard as soon as 01h-C1 sends an input type |

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 08-C1 | 09 | The per-scene document text (lexical and semantic candidates) and metadata (structural prefilter). |
| 08-C1 | 12 | Scene listing and metadata for the read-only investigation toolset (12-C1). |
| 08-C2a | 09 | The live document set for a campaign, per query, without parsing transcripts. |
| 08-C2b | 09 | Cached document vectors and a bounded warm step. The query vector is 09's. |
| 08-C2c | 05 | The rebuild hook 05-C3's sync calls for document and vector kinds. |
| 08-C3a | 09 | The `history-index` embed task its document warming meters under. |
| 08-C3b | 09, 12 | Bounded, phase-correct transcript excerpts from a selected scene: 09's evidence, 12's investigation tool. |

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
  stored anywhere.
- The rolling summary is on by default: one fold every ten posts
  (`store/config.py:142`). It is one line of prose (`rolling_summary.py`,
  `parse_output`).

### 1.2 What is filed against a scene, and where

Every one of these is keyed by `sid`, which `scene_refs.repoint` follows on
rename (`store/scene_refs.py:1-60`):

| Source | Shape | Written by |
|---|---|---|
| `chronicle.json` (`chronicle.py:37`) | `{sid: {id, one_line, summary, keywords[], cast[], location, date, absorbed}}` | absorb (`chronicle.absorb`); `set_line` corrects `one_line`/`date` only (`chronicle.py:63`) |
| `appearances.json` (`appearances/__init__.py:1-8`) | `{"<kind>/<id>": {version, base, scenes[], role, presence{sid: intervals}}}` | appear/leave, absorb |
| `plot.json` (`plot.py:37`) | `{pid: {title, status, beats[{scene, text}], last_scene}}` | absorb; continuity closures |
| `commitments.json` (`commitments.py:1-60`) | as plot, plus `kind`, `due` | absorb; continuity |
| `facts.json` (`facts.py:15-19`) | `{fid: {text, date, scene, status, superseded_by, retired_scene}}` | absorb; the user's own corrections (`routes/ledger.py`) |
| `relationship_history.json` (`relationship_history.py:13-17`) | append-only `entries[{scene, kind, a, b, label, before, after, scene_gone?}]` | absorb, undo |
| `continuity.json` (`continuity/doc.py:3-8`) | `links{id: {a, b, relation, scene, ...}}` (the scene a link was accepted against) | review |
| `events.json` (`events.py:1-12`) | `{eid: {name, date, note, fired}}` | **no scene link at all** |
| `timeline.md` | append-only prose lines, no scene ids (`chronicle.py:109`, `timeline.py:11-17`) | absorb |

Threads and commitments have an alias layer: `continuity/effective.py:385`
(`records`) merges an aliased group under its live canonical, and
`continuity/involvement.py:61` (`group_touched`) folds each member's scenes
into it. `involvement.scene_actors` (`continuity/involvement.py:101`) is the
one scene-to-actors join: the appearance record unioned with the chronicle's
cast snapshot, each source guarded on its own.

**A deleted scene leaves its sid-keyed rows behind.** `delete_scene`
(`scenes/lifecycle.py:210`) drops prompt snapshots, presence, commit state and
pins, and marks relationship rows `scene_gone`
(`relationship_history.py:247`), but it does **not** drop the chronicle
record, plot or commitment beats, or facts. Only a cut or a retcon does,
through `cascade.py:224`. `routes/scenes.py:2736` (`_already_absorbed`) is
written around exactly this: a recycled `sid` must not be judged absorbed by
`sid in chronicle`, so it reads the scene's own `done`. Any join on `sid`
inherits the hazard. `timeline.py` sidesteps part of it by dropping beats
whose scene is gone, but a recycled `sid` is not gone.

### 1.3 Projections that already exist over these

- `timeline.build` (`timeline.py:118`) reads heads, `chronicle.json` and
  `plot.json`, and opens no transcript. Its field rules are the precedent
  this spec follows: the chronicle is the source of `one_line` (falling back
  to `summary`) and of the absorb-time `location`; the head's first moment is
  the scene's date.
- `context/story._story_entries` (`context/story.py:155`) is the recap: the
  last N chronicle records' `one_line` or `summary`, every turn.
- `chronicle.scene_facts` (`chronicle.py:188`) resolves cast refs, the current
  location's display name and the current moment for a scene.

### 1.4 Embedding today

- **Lore recall** (`context/semantic.py`) embeds world-info entries as
  `name \n keys \n body`, clipped to `DOC_BYTES = 6000` UTF-8 bytes
  (`context/semantic.py:102`, `:203`), with the scan window as the query in
  the same request. At most `WARM_LIMIT = embeddings.BATCH - 1` uncached
  entries are embedded per turn (`:128`; `embeddings.BATCH = 64`,
  `embeddings.py:59`), on the rotating window `embed_space.warm_window`
  (`embed_space.py:261`).
- **Library search** (`semsearch.py`) embeds every document `search.walk`
  yields, scene transcripts included, cut into passages of `PASSAGE_BYTES =
  1500` at post markers (`semsearch.py:81`, `:125`), raw (no regex view: it is
  the author searching their own text), warming `BATCH * 4 - 1` per query
  (`:104`).
- **Vectors** live in `store/vectors.py`: one file per vector under
  `.cache/embeddings/`, keyed by `sha256(space \0 text)` (`vectors.py:115`),
  float32 with a CRC32, every failure a miss. Nothing prunes them.
- **The embed operation** is `store/inference/embed.embed_sync`
  (`inference/embed.py:143`). It refuses a task outside
  `routing.EMBED_TASKS` (`:166`; the tuple is `routing.py:174`), files one
  ledger row per call, takes the `space` the caller read its cache under and
  never re-resolves it, and has no `input_type` or `dimensions`. Its async
  form (`:226`) has no caller.
- `test_operation_guard.py` holds that every embed task is the literal of some
  call (`:308`, so a task cannot be registered ahead of its call site), that
  every call's `space=` traces to `embed_space.endpoint` (`:689`), and that at
  least `MIN_EMBED_CALLS = 6` calls exist (`:184`).
  `test_inference_embedding.py:269` pins the tuple's exact contents.

### 1.5 Regex phases

`store/regex/view.view` (`regex/view.py:49`) runs rules over **transcript
messages**, by role and by depth counted over the whole transcript (`offset`,
`total`). Nothing applies rules to record text (chronicle, plot, facts): every
caller of `view` passes messages (`grep regex_view` over `routes/` and
`store/` finds only transcript readers). `test_regex_prompt_guard.py` holds
LLM readers of transcript text to the prompt phase, scanning `routes/` and
`store/context/` only (`:73`). The context builder views the whole transcript
and then projects it, dropping director notes and reducing images to alt text
(`context/assemble.py:176`, `context/story.py:18`).

### 1.6 Lexical search

`store/search.py` walks every file per query, memoizes per-file extraction by
stat signature in its own pool (`search.py:274`), matches case-folded
substrings with phrases kept whole (`query_terms`, `:174`), and re-reads the
fact files per query. It is the precedent for term normalization here.

### 1.7 What the draft got wrong against this code

- **"Events/anchors" per scene.** Events carry no scene id (`events.py:1-12`);
  a scene can only be related to one by date, through a calendar provider
  that may be user-authored plugin code. Out of scope for the document
  (section 14).
- **"Groups relevant" as document text.** Group membership is current state,
  not history (07 records no join dates), and it changes for every scene an
  actor appears in at once. Section 4.6 keeps it in metadata.
- **"Recent/important quoted terms".** Any transcript-derived field makes the
  document depend on the transcript body and on the regex rule files, and
  re-embeds on every post of an open scene. Section 3.2 keeps transcript text
  out of the document and puts it behind the expansion helper.
- **The key "is the digest of source hashes".** Keyed on whole files, every
  absorb (which rewrites `chronicle.json`) would invalidate every document of
  the campaign. Section 5 keys on per-scene slices instead.
- **The draft names no stable ref.** It has to be the identity, not the
  `sid` (section 4.5).

## 2. Goal (and what is explicitly not the goal)

**Goal.** A compact, deterministic, bounded document per scene that a
historical retrieval layer (09) can filter, match and embed, built only from
what the store already records, never itself authoritative, and cheap enough
to exist for every scene of a long campaign. Plus the two pieces 09 and 12
need around it: the embed task its vectors are filed under, and a helper that
turns "this scene was selected" into bounded transcript evidence in the right
regex phase.

Concretely:

1. **08-C1.** One `SceneDocument` per live scene: text of at most `DOC_BYTES`
   UTF-8 bytes, metadata for structural prefiltering, and a key computed from
   per-scene input slices plus `search_document_version`.
2. **08-C2.** Documents are built lazily and stored in 03's cache; vectors are
   kept in `vectors.py` under the Embedding role's document-typed space and
   the exact text; 05's sync rebuilds what was hot.
3. **08-C3.** A registered `history-index` embed task with its call site, and
   a transcript-expansion helper.

**Not the goal.**

- Retrieval, ranking, a prompt section or a token budget (09).
- Embedding a query (09 registers its own task for that, section 8.4).
- Post-level or passage-level vectors (draft section 11; section 14 here).
- Document families other than scenes (draft section 6).
- Replacing `semsearch.py`'s passage index, the recap, or the timeline.
- Starting any run or warming any campaign on its own initiative. 08 embeds
  only when a caller asks, and the caller is 09 or 05.

## 3. Design principles

### 3.1 The document is an index, not a record

A SearchDocument may be deleted at any time and is never the only copy of
anything. Every field is a projection of a record the store keeps
(`chronicle.json`, a ledger, a scene head). Nothing reads a document to decide
a write, a sync outcome or what a model is told is true: retrieval uses it to
*find* a scene, and the evidence it then shows is read from the transcript and
the records (08-C3b, and 09). This is 03's rule (03 section 1) applied to one
kind.

### 3.2 No transcript text in the document

The document is built from the scene head and from records. It contains no
message text. Four reasons, each enough alone:

- **Churn.** An open scene's transcript changes every post. A document that
  quoted it would get a new key and, worse, new text (so a new embedding) on
  every turn. Built from the head and the records, an open scene's document
  moves only when its rolling summary refolds (every ten posts by default) or
  a record changes.
- **Regex phase.** A document that will be sent to an embedding provider is a
  send, and transcript text that is sent must be the prompt view
  (CLAUDE.md, "Output processing"). That would put every regex rule file
  (connection, global, world, campaign: `regex/layers.py:1-12`) into the key.
  Record text is not subject to regex rules anywhere in the app today
  (section 1.5), so the document has **no regex phase**, and says so in its
  metadata (`phase: "none"`).
- **Privacy surface.** What the embedding provider receives is the same class
  of text the recap already sends the chat provider every turn (chronicle
  summaries), plus names and ledger lines. Raw prose stays where it is.
- **What makes a scene findable is already summarized.** The chronicle
  `summary` and `keywords` are absorb's own reading of the transcript, and the
  rolling summary covers an unabsorbed scene. The transcript is consulted after
  selection, through 08-C3b, bounded.

The cost is a thin document for a scene with no summary of either kind (an
unabsorbed scene played with `rolling_summary_every: 0`). Open question 4.

### 3.3 Keys from slices, not from whole files

A document reads small parts of several campaign-wide files. Keyed on those
files' bytes, one absorb would invalidate every document of the campaign. So
each input is first cut down to the **slice** that concerns this scene (this
scene's chronicle record, the beats filed under it, its facts, its cast names,
...), each slice is canonicalized and digested, and the key is over the
digests (03-C1 allows inputs that are not files, 03 section 2a item 1). An
absorb of scene 40 moves scene 40's slices and leaves every other document's
key where it was.

### 3.4 Determinism

Same store state, same text, byte for byte: across calls, processes,
platforms, dict orders in hand-edited JSON, and Python's hash seed. Every list
has an explicit order (section 4.3), every clip is a UTF-8 byte clip at a
character boundary, whitespace is collapsed the one way, and nothing reads the
clock, the config, a calendar plugin or a random source.

## 4. The scene SearchDocument (08-C1)

### 4.1 Module layout

A new package, `backend/src/grimoire/store/searchdocs/`, with a docstring-only
`__init__.py` (the `continuity` precedent: importers name the submodule they
want, so the package import pulls in nothing):

| Module | Holds |
|---|---|
| `slices.py` | `CampaignInputs.load(cid)` (each ledger read once, tolerantly) and `scene_slices(inputs, sid, head) -> Slices` |
| `scene.py` | `SEARCH_DOCUMENT_VERSION`, `DOC_BYTES`, the caps table, `render(slices) -> SceneDocument`, `key_of(slices)` |
| `live.py` | `scene_documents(cid, ...)`: the live set, batch lookup, lazy build (08-C2a) |
| `embedded.py` | `TASK = "history-index"`, `vectors_for(...)` (08-C2b, 08-C3a) |
| `rebuild.py` | the 05-C3 hook (08-C2c) |
| `expand.py` | `expand(...)`: transcript expansion (08-C3b) |

Data carriers are frozen dataclasses or plain dicts, never pydantic models
(CLAUDE.md, Android). Everything that leaves the package is JSON-safe.

### 4.2 Slices

`CampaignInputs.load(cid)` reads, once per call, each of: `chronicle.json`,
`appearances.json`, `plot.json` and `commitments.json` through
`continuity.effective.Ledgers` / `effective.records` (alias-resolved),
`facts.json`, `relationship_history.json` and `continuity.json`'s links. Each
read is guarded on its own, as `timeline.build` guards its three
(`timeline.py:136`): a file that will not parse, or parses to the wrong
shape, contributes nothing and adds its name to `degraded`. It also builds two
name tables once per call, not once per scene: actor ref to display name
(`appearances.cast._actor_name` over the record, through
`overlay.actor_root`) and location id to name (`overlay.read_entity`), and,
once 07-C2 lands, actor ref to groups.

The head of each scene is read with `parse_frontmatter_head`, memoized in
process by stat signature in this package's own pool, sized like
`scenes/read.POOL_ENTRIES` for its reason (a sweep must not evict the shared
FIFO).

`scene_slices` cuts what concerns one scene. Every slice is a plain JSON value
whose strings are already whitespace-collapsed and byte-clipped to the caps in
section 4.3, so its digest moves only when the text it can contribute moves:

| Slice | Contents | Gated on the scene being absorbed |
|---|---|---|
| `head` | `title`; `identity` (or `""`); `done`; `pcless`; `branch_group`; location ids and time moments (history order); `rolling_summary`; the frontmatter `one_line`/`summary` | no |
| `chronicle` | this scene's record: `one_line`, `summary`, `keywords`, `date`, `location`, `cast` tokens; `null` when absent | **yes** |
| `cast` | `[{ref, name, role, present}]` for every actor whose appearance record lists this scene, holds a presence interval for it, or is in the chronicle cast (when absorbed); `ref` in the continuity form `characters:mara` (`canon.actor_ref`) | no (the chronicle half is) |
| `locations` | `[{id, name}]` for the location history, names resolved now | no |
| `threads` | `[{ref, title, status, beats[]}]`: canonical threads with at least one beat filed under this `sid`, beats in stored order | **yes** |
| `commitments` | as `threads`, plus `kind`, `due` | **yes** |
| `facts` | `{recorded: [{id, text}], retired: [{id, text}]}`: `scene == sid` and `retired_scene == sid` | **yes** |
| `relationships` | `[{label, kind, before, after}]`: entries with `scene == sid` and no `scene_gone`, in file order | **yes** |
| `links` | continuity link ids with `scene == sid` (metadata only) | **yes** |
| `touched` | thread and commitment refs that touched the scene by `last_scene` only (metadata only) | **yes** |
| `groups` | `[{ref, name}]` of groups any cast member belongs to now (07-C2; metadata only) | no |

**Why the gate.** Every sid-keyed ledger row above is written by absorb (or
by a later review or correction of something absorb wrote). A scene whose own
head does not say `done: true` has no legitimate rows; a row filed under its
`sid` belongs to a deleted scene whose number it inherited (section 1.2). The
gate is `_already_absorbed`'s rule (`routes/scenes.py:2736`) applied to every
join. It does not close the hazard for a recycled `sid` that has since been
absorbed itself: absorb replaces the chronicle record, but a dead scene's plot
beats under that `sid` would still attach. That residual is every sid join's
in the app today (timeline, briefing, involvement); 08 does not widen it, and
fixing it belongs to scene deletion, not to an index (section 14).

**Summary source**, first that is non-empty: the chronicle `summary`, the
frontmatter `summary` (both only when absorbed), the head's
`rolling_summary`. The line is labelled `Summary` for the first two and
`Running summary` for the third, so a reader and a model can tell a reviewed
reading from a fold in progress. A rolling summary whose `rolling_digest` no
longer matches the transcript may describe posts a cut removed; the document
does not check (that needs the body) and does not need to: it is a candidate
signal, and the evidence comes from the transcript through 08-C3b.

### 4.3 Rendering and bounds

The text is a fixed sequence of labelled lines; an empty line is omitted
entirely. Each value has a per-item byte cap and each line an item cap. An
item over its cap is clipped with `embed_space.clip` (`embed_space.py:240`) to
the cap minus three bytes and given `…` (three bytes), so the cap holds
exactly. Items in a line are joined with `"; "`.

| # | Label | Source | Items (order) | Bytes per item | Line cap |
|---|---|---|---|---|---|
| 1 | `Scene:` | head title | 1 | 160 | 160 |
| 2 | `When:` | date part (before `T`) of the first and last moment; else the chronicle `date` | 2, joined ` to ` | 48 | 100 |
| 3 | `Where:` | location names in history order, then the chronicle `location` if it is not among them; deduplicated case-folded; the last 4 | 4 | 64 | 264 |
| 4 | `Cast:` | players first, then the rest, each by `(kind, id)`; a player gets ` (player)` | 12 | 48 | 600 |
| 5 | `In brief:` | chronicle `one_line` | 1 | 240 | 240 |
| 6 | `Summary:` / `Running summary:` | section 4.2 | 1 | 1500 | 1500 |
| 7 | `Keywords:` | chronicle keywords, stored order, deduplicated case-folded | 12 | 32 | 408 |
| 8 | `Threads:` | by canonical ref; `title: beat / beat` | 5 | 144 | 730 |
| 9 | `Commitments:` | by canonical ref; `title (kind, status): beat` | 3 | 144 | 438 |
| 10 | `Facts established:` | by fact id, natural order (`paths.natural_key`) | 4 | 144 | 584 |
| 11 | `Facts retired:` | by fact id, natural order | 2 | 144 | 292 |
| 12 | `Relationships:` | file order; `label: before -> after` | 3 | 112 | 342 |

The line caps sum to 5658 bytes, the labels and newlines to at most 145, so a
document is at most 5803 bytes **by construction**. `DOC_BYTES = 6000`
(`context/semantic.py:102`'s constant, for its reason: bytes bound tokens from
above in every script, so a document stays inside the 8k-token window common
embedding models have, CJK included). The renderer still clips the whole text
to `DOC_BYTES` as a backstop and records `backstop: true` in the metadata if
it ever fires; a test holds that it never does on a worst-case fixture
(section 13). The caps are structural (a summary long enough to dominate the
vector, a cast line of a dozen names, a handful of beats), not measured, and
will be tuned against 09's evals later.

What is cut by an item cap is still in the metadata (section 4.4), so
structural filtering sees every actor and thread even when the text shows
twelve.

Whitespace in every value is collapsed with `" ".join(s.split())`, as
`search._flat` and `rolling_summary.parse_output` do. No Unicode
normalization (the stored text is what the reader wrote). Labels are English
and fixed; translating them is a version bump.

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
Commitments: Repay Winifred (promise, open): Seraphine swears to clear the debt by spring
Facts established: The harbour ledger records the tide tax twice
Relationships: Mara → Seraphine: wary -> grudging trust
```

### 4.4 Metadata

`meta` is a JSON object stored beside the text, for structural prefiltering
without reparsing sources (draft section 9). Each list is capped at
`MAX_META_REFS = 64` entries in a deterministic order, with `truncated`
naming any list that was cut (a scene with more than 64 of anything is an
import artifact, not play).

| Field | Meaning |
|---|---|
| `kind` | `"scene"` |
| `version` | `SEARCH_DOCUMENT_VERSION` |
| `phase` | `"none"`: the text contains no transcript message (section 3.2) |
| `identity` | the scene identity, or `""` before backfill |
| `title` | head title |
| `absorbed`, `pcless` | booleans from the head |
| `branch_group` | `""` or the group token |
| `summary_source` | `chronicle`, `frontmatter`, `rolling` or `none` |
| `moments` | `{first, last}` native moments from `time_history` |
| `chronicle_date` | the record's free-text date |
| `locations` | `locations/<id>` refs, history order |
| `cast`, `players`, `departed` | actor refs, `characters:mara` form |
| `threads`, `commitments` | canonical refs with a beat in this scene |
| `touched` | refs that reached this scene by `last_scene` only |
| `links` | continuity link ids accepted against this scene |
| `facts` | `{recorded: [ids], retired: [ids]}` |
| `relationship_pairs` | `[[a, b], ...]` actor tokens, sorted within the pair |
| `groups` | group refs (07-C2), current membership |
| `keywords` | case-folded chronicle keywords |
| `degraded` | names of inputs that would not read |
| `bytes`, `backstop` | the text's UTF-8 length, and whether the backstop clip fired |
| `slices` | `{name: digest}`: the inputs the key was built from (diagnostics, and 05's report) |

**Not in the stored metadata: the `sid` and `closed_by`.** The `sid` moves on
rename and on a re-pad without any slice moving (a re-pad changes no title),
so a cached `sid` would be wrong for every scene after the first re-pad. And
closedness is derived from the siblings' `done` flags. Both are attached
outside the cache by the live set (section 6), from `list_scenes`, which is
03 section 6's rule for path-derived inputs ("the caller adds the
path-derived name outside the cache").

### 4.5 The stable ref

`scene:<identity>` is the ref a consumer stores or compares. A scene with no
identity yet has `identity: ""`; a consumer refers to it by `sid` for the
duration of one query only, and never persists it. 08 never mints an identity
(`ensure_identity` is a write; this package writes nothing to the store).

### 4.6 Groups are metadata only

07-C2 answers "which groups is this actor in *now*". Rendering that into a
historical scene's text would date a membership the fiction may not have had
yet, and a membership edit would change the text, and so the vector, of every
scene the actor ever appeared in. As metadata it costs a key change and a
re-render (no embedding, 03-C7), and it is exactly what a structural
prefilter wants. Open question 1.

## 5. Keys and `search_document_version` (08-C1)

Each slice's digest is
`sha256(json.dumps(slice, sort_keys=True, ensure_ascii=False, separators=(",", ":")))`.
The document key is 03-C1's composite key:

```
key_digest = 03.key(kind    = "searchdocs.scene",
                    version = SEARCH_DOCUMENT_VERSION,     -- in the key (03 section 6)
                    BUILD,                                 -- 03's fingerprint
                    params  = {"slices": {name: digest, ...}},
                    inputs  = [])                          -- no whole-file input
```

- **`SEARCH_DOCUMENT_VERSION = 1`** is the registry kind's `version` and is
  also stored in the metadata. Bumping it is how a deliberate rendering change
  ships without depending on `BUILD` (03 section 6). It moves every key; it
  re-embeds only documents whose text actually changed, because vectors are
  keyed by text (03-C7).
- **`BUILD`** colds every document after an upgrade (03 section 6). Rendering
  is cheap and the vectors survive, so that costs a re-render per scene and no
  embedding where the text is unchanged.
- **The compute receives what the key was computed from** (03 section 6): the
  key is over the slices, and `render` is a pure function of the slices it is
  handed. It reads nothing.
- **Relevant edits move the key; irrelevant ones do not.** Appending a post
  changes the scene file's `updated` and body, neither of which is in a slice:
  the key holds. A rolling-summary refold, a chronicle edit of this scene, a
  beat filed under it, a rename of a cast member or a location, a fact
  recorded or retired here: each moves exactly this scene's key (and, for a
  rename, the keys of every scene naming that actor or place). Absorbing
  another scene moves none of this scene's slices.

**Why the document artifact is persisted at all.** Rendering from slices is
cheap; 03 section 8's cost test would not justify persisting a document for
its compute alone. It is persisted because it is the **row an index ranks**:
09's lexical ranking may use an index over document text restricted to the
caller's live keys (03-C6, 03 section 9), and the `materialized` record that
05 rebuilds from needs an artifact to describe. What makes a cold process
expensive is gathering slices (a head read per scene, a parse per ledger).
Persisting those as 03 kinds keyed by file hash (a `searchdocs.ledger_slices`
kind per ledger file, the head projection per scene file) is allowed, and the
plan wires one only if 03 section 8's synthetic-library measurement shows the
win. The in-process memo is always on.

## 6. The live set and lazy build (08-C2a)

```python
def scene_documents(cid: str, *, exclude: frozenset[str] = frozenset(),
                    only: frozenset[str] | None = None) -> list[LiveDocument]: ...

@dataclass(frozen=True)
class LiveDocument:
    sid: str                    # current, from list_scenes
    closed_by: dict | None      # list_scenes' derivation, never cached
    doc: SceneDocument          # key, text, meta
```

1. `list_scenes(cid)` (heads only, memoized, `scenes/read.py:72`) gives the
   live scenes and their `closed_by`. This is the live set: it comes from the
   filesystem on every call (03-C6, 03 section 9). `exclude` drops sids (09
   excludes the scene being played, which is already in the prompt); `only`
   restricts to a known set (05's rebuild).
2. `CampaignInputs.load(cid)` reads each ledger once.
3. For each live scene: head (memo), slices, digests, key.
4. **One batch lookup** of all keys in 03's cache (03-C6). A hit is decoded
   and checksummed by 03.
5. Every miss is rendered from the slices already in hand (no further read)
   and stored in one write batch, with `materialized` rows (03-C3) for every
   path its slices read: the scene file, each ledger file that contributed a
   non-empty slice, the cast cards and location files whose names it used,
   and 07's group files.
6. Return in play order (`sid` order, which is play order by construction,
   `scene_ids.py:1-5`).

Properties:

- **Liveness.** A document is only ever found through a key computed from the
  current slices, so a superseded document cannot be returned, in process or
  after a restart, whether or not any sync ran (03-C2). No step here retires
  anything; LRU eviction (03 section 10) reclaims the rows.
- **Never raises for store damage.** A garbled ledger empties its slice and is
  named in `degraded`. A campaign that does not exist raises
  `CampaignNotFound`, which is the caller's not-found.
- **Cache off.** With `GRIMOIRE_COMPILED_CACHE=0`, a missing `BUILD` stamp on
  Android, or 03 not yet landed, step 4 is a miss and step 5 stores nothing:
  the same documents are computed every call, held in an in-process memo keyed
  by the same key. Answers are identical (section 13, equivalence).
- **No lock.** It reads without `campaign_lock`, unlike `timeline.build`.
  A read that crosses an absorb's sequence can pair a new chronicle slice with
  an old plot slice; the document it renders is keyed by exactly those slices,
  so it is self-consistent with what it read, and the next call computes the
  new keys. Nothing persists a mixed state under a key that a consistent read
  would compute. Taking the lock would put a turn-path reader behind an
  absorb save.
- **Threads.** Called from threadpool workers and detached runs, never from
  the event loop (03's threading rule).
- **Cost.** A warm call in one process is a listing, a stat per ledger and
  per head, a slice and a digest per scene, and one batch lookup. The plan
  measures it on a synthetic library of long campaigns and memoizes the
  per-campaign slice table on the ledgers' stat signatures if the per-scene
  digesting shows up.

## 7. Search metadata and the structural prefilter

08 does not filter; it makes filtering cheap. Every field 09's structural
signals need (draft section 3 of 09: present actors, shared scenes, threads,
commitments, location history, relationship pairs, groups, recency) is in
`meta` as refs in the forms the rest of the app joins on:

- actors as `characters:<id>` / `pcs:<id>`, the form
  `continuity.involvement` and `canon` use;
- threads and commitments as canonical `thread:<id>` / `commitment:<id>`,
  already alias-resolved, so a filter on a canonical ref finds scenes filed
  under its merged members;
- locations as `locations/<id>`, the entity ref form;
- groups as 07's ref form.

A closed branch (`closed_by` set) is an alternative that did not happen. 08
reports it on every `LiveDocument`; 09 decides whether to drop it (it should,
by default).

## 8. Embeddings (08-C2b, 08-C3a)

### 8.1 The task

`routing.EMBED_TASKS` gains `"history-index"`: the documents' embeddings.
Registered in the same change as its one call site, `embedded.vectors_for`,
because `test_every_embed_task_is_named_by_a_call_site`
(`test_operation_guard.py:308`) fails a task nothing embeds under, and
`test_inference_embedding.py:269` pins the tuple. `MIN_EMBED_CALLS` becomes 7.
CLAUDE.md's "Adding an embedding call site?" paragraph names the callers that
carry a campaign; it gains this one.

### 8.2 The call

```python
TASK = "history-index"
WARM_LIMIT = embeddings.BATCH - 1

def vectors_for(cid: str, docs: list[SceneDocument], *, space: dict,
                client: embeddings.EmbeddingsClient, warm_limit: int = WARM_LIMIT,
                rotate: str = "", deadline: float | None = None,
                budgeted: bool = False, scene: str = "") -> DocVectors: ...

@dataclass(frozen=True)
class DocVectors:
    vectors: dict[str, list[float]]   # document key -> unit vector
    indexed: int                      # documents with a vector in this space
    corpus: int                       # documents asked about
    error: str | None                 # the embed failure's kind, if one
```

1. `space` is the Embedding role's endpoint, resolved **once** by the caller
   (`embed_space.endpoint()`, `embed_space.py:62`) and handed in, never
   re-resolved (`inference/embed.py`'s docstring; the provenance walk in
   `test_operation_guard.py:689` follows the parameter to its callers).
2. The cache is read under the **document space key**: 01h-C3's space identity
   with `input_type = "document"` (and any dimensions). `vectors.load(key,
   texts)` returns the hits.
3. The uncached texts, deduplicated, are cut to `warm_limit` on
   `embed_space.warm_window(uncached, rotate, warm_limit)` (`embed_space.py:261`):
   a rotating proper subset, so a document the provider refuses cannot pin the
   head of every warm. `rotate` is the caller's seed (09 passes the query text,
   as recall does; 05 passes the campaign id plus the count).
4. One `embed_sync(TASK, texts, space=space, client=client, input_type="document",
   deadline=deadline, budgeted=budgeted, campaign=cid, scene=scene,
   cached=..., uncached=...)` call (the `input_type` argument is 01h-C1's;
   until it lands, the call is untyped and the key is the untyped space,
   exactly lore's). One ledger row, one capture line with counts only
   (`inference/embed.py:199`).
5. Each returned vector is saved with `vectors.save` under the document space
   key and the exact text, and a `materialized` row
   `(scenes/<sid>.md, vector:<space key>)` is written for its scene (03-C3).
   A hit also writes that row (coarse `last_used`, 03 section 10), so "this
   scene's document was embedded in this space" stays answerable for 05.
6. Failure is never raised. `LLMError` or `OSError` from the call returns the
   vectors that were cached, with `error` set to the kind; the meter has
   already recorded the failure (kind and HTTP status only). A deadline that
   lapses before the request files nothing (`embeddings.NOT_SENT`).

What it does not do: score, compare dimensions against a query, or forget a
vector of the wrong width. Those need the query, which is 09's; 09 forgets a
mismatched vector with `vectors.forget(document space key, text)`, as
`context/semantic.py:228` does.

### 8.3 Why vectors stay in `vectors.py`

03 section 4 left the choice to 08 or 09, on one condition: the key stays
`(space, exact text)` and independent of `BUILD`. `vectors.py` already is that
key, its integrity rules (CRC, size cap, every failure a miss) are justified
on the read path, and lore and search share it. Moving document vectors into
SQLite for batch loading is 09's call once it measures loading a campaign's
vectors per turn. Either way, a version bump or an upgrade that re-renders the
same text finds its vector (03-C7).

**Input type is part of the key, as sent.** A provider that takes an input
type embeds a document and a query differently. A document vector filed under
the untyped key and later read by a typed query, or the reverse, would rank
across two different embeddings with nothing to detect it (`vectors.py`'s
docstring on spaces). So 08 requires of 01h-C3 that the space key includes the
input type **that was sent**: present when the provider takes one, absent when
it does not, so lore's untyped vectors are not invalidated for a provider that
ignores the field. If 01h-C1 lands after 08, the first warm after it re-embeds
the documents once, under the new key. That is the correct cost.

### 8.4 The query is not 08's

09 embeds its query under a task of its own (recommended `history-recall`,
input type `query`), registered with its call site in 09. It cannot share
`history-index`: a query and a set of documents are two requests once input
types differ, and keeping them on separate tasks keeps the ledger able to say
what indexing cost apart from what recall cost.

### 8.5 Attribution and cost

- `campaign` is always the documents' campaign: history is per campaign, so a
  batch never spans two (01h-C5 is not needed here).
- `scene` is the scene whose turn the call serves, when 09 warms on a turn
  (CLAUDE.md: a scene's own totals include what its turns spent on recall);
  `""` for 05's rebuild.
- An endpoint that reports no price files an unpriced row (CLAUDE.md, Costs);
  an OpenRouter embed reports real spend against the campaign budget, which
  only warns.
- 08 never decides to spend. `vectors_for` embeds only when called, and only
  `warm_limit` documents per call.

## 9. Hot rebuild through 05 (08-C2c)

05-C3 rebuilds "what was hot" after an edit, and embeds only what was already
embedded. 08 supplies the hook for its two kinds, `searchdocs.scene` and
`vector:<space>` over a scene path, split by whether it touches the network:

```python
def affected(paths: list[str]) -> dict[str, frozenset[str]]:
    """cid -> sids whose document may have moved. No network, no lock."""

def rebuild_documents(cid: str, sids: frozenset[str]) -> RebuildResult:
    """Re-derive those documents and store them (08-C2a with `only=`).
    Returns, per scene, the new key and whether its text has a vector in
    the current document space. No network."""

def reembed(cid: str, docs: list[SceneDocument], *, space: dict,
            client: embeddings.EmbeddingsClient, limit: int) -> DocVectors:
    """`vectors_for` over the documents that were embedded before and whose
    text now has no vector. Network."""
```

`affected` maps each edited path, relative to the store root:

- `campaigns/<cid>/scenes/<sid>.md`: that scene, if its path has a
  `searchdocs.scene` row in `materialized`.
- Any other path under `campaigns/<cid>/` that a document read (a ledger, a
  campaign-side card or location): every live scene of `cid` whose scene path
  has a `searchdocs.scene` row. `materialized` is read by path for the paths
  the caller holds (the live scene paths), never enumerated (03 section 4).
- `worlds/<wid>/...` (a world location or card a campaign inherits): the same,
  for each campaign whose world is `wid` (`campaigns.read.world_refs()`). A
  campaign holding its own copy re-derives the same keys and does nothing.

Of the rebuilt documents, those whose scene path has a `vector:<space key>`
row for the **current** document space, and whose new text has no vector, go
to `reembed`, bounded by `limit` per campaign; the rest stay lazy for 09's
next warm. A scene never embedded is never embedded by a rebuild (05-C3). A
row for another space (the role moved since) is not "hot" in this one.

**What 08 requires of 05-C3**, because it is what makes this safe:

- `affected` and `rebuild_documents` may run inline after a write, outside the
  campaign lock (03 section 12: cache writes are outside any campaign lock).
- `reembed` **never** runs on a writer's request path, under a campaign lock,
  or on the event loop: an absorb save must not wait on an embeddings HTTP
  call. 05 runs it after the response, the way `streaming._fire_follow_up`
  runs a turn's follow-ups, and swallows its failure. If 05 has no such
  vehicle, the re-embed is simply left to 09's next lazy warm, which is
  correct and only slower (open question 8).
- 05's summary carries counts (documents rebuilt, vectors re-embedded,
  skipped), never text (05-C2).

Without 05, nothing is wrong: every read computes current keys, so the next
history query rebuilds lazily (03 section 2a item 2).

## 10. Transcript expansion (08-C3b)

Selection finds a scene; the evidence is in its transcript. The helper reads
it and returns bounded, phase-correct excerpts around the posts that match the
caller's terms. It never returns the whole scene unless asked and it fits.

```python
PHASES = ("prompt", "display")
RADIUS = 1                  # neighbouring posts on each side of a hit
MAX_EXCERPTS = 3
MAX_BYTES = 4000            # all excerpt content together, UTF-8
MESSAGE_BYTES = 1500        # one post's content (semsearch.PASSAGE_BYTES)
MAX_TERMS = 16

def expand(cid: str, sid: str, *, phase: str, terms: Sequence[str],
           identity: str | None = None, radius: int = RADIUS,
           max_excerpts: int = MAX_EXCERPTS, max_bytes: int = MAX_BYTES,
           fallback: str = "none", allow_whole: bool = False) -> Expansion | None: ...

@dataclass(frozen=True)
class Excerpt:
    start: int                        # absolute transcript index of the first post
    end: int                          # exclusive
    posts: tuple[dict, ...]           # {index, role, speaker, content}
    matched: tuple[str, ...]          # terms this excerpt contains
    clipped: bool

@dataclass(frozen=True)
class Expansion:
    sid: str
    identity: str
    phase: str
    mode: str                         # "matched" | "whole" | "tail" | "empty"
    total: int                        # posts in the transcript
    eligible: int                     # posts the phase may show
    excerpts: tuple[Excerpt, ...]     # in transcript order
    bytes: int
```

### 10.1 Steps

1. **Phase is required and checked.** `phase` must be `"prompt"` or
   `"display"`; anything else (`"store"` included) raises `ValueError`.
   `"prompt"` is for anything sent to a model (09's history section, 12's
   tool results); `"display"` is for a surface that shows a reader what
   retrieval found.
2. **The right scene.** If `identity` is given and the `sid` no longer carries
   it, the scene is found by `scenes.find_by_identity`
   (`scenes/identity.py:305`), which follows a rename; if no scene carries it,
   the answer is `None`. The identity is checked again after the transcript is
   read, so a delete-and-recreate in between returns `None` rather than
   another scene's posts.
3. **The view, over the whole transcript.** `read_scene` (raw), then
   `regex_view.view(messages, cid=cid, phase=phase)` over the whole list, so
   depth is counted exactly as the context builder counts it
   (`context/assemble.py:176`). Viewing only a window would give a
   depth-ranged rule a different answer for the same post.
4. **Eligible posts.** `scenes.serialize.in_context` semantics: a post hidden
   from context and a director note are never returned, in either phase. A
   hidden post is the player's explicit "do not send", and a retrieval surface
   that displayed one would claim as evidence something the model is never
   shown. Transition lines are kept with no speaker (as `transcript_text`
   renders them, `chronicle.py:205`); roll lines are kept. Indices stay
   absolute.
5. **Images.** In the prompt phase every markdown image becomes its alt text
   (`export.drop_images`, `store/export.py:306`), as `_project_history` does
   for the turn prompt (`context/story.py:18`). The display phase leaves them.
6. **Matching.** Terms are case-folded, whitespace-collapsed, deduplicated,
   at least two characters, at most `MAX_TERMS` (the normalization of
   `search.query_terms`, `search.py:174`; a caller holding a raw query passes
   it through that function first). A post's score is the number of distinct
   terms in its **phased** content or speaker, then total occurrences capped
   at 5 per term (`search._score`'s cap), then lower index. Matching the
   phased text is what keeps a term that a prompt rule strips (a `<think>`
   block, an OOC aside) from selecting a post whose sent text no longer
   contains it.
7. **Windows.** Hits in score order each claim the `radius` eligible posts on
   either side; overlapping or adjacent windows merge. Stop at
   `max_excerpts`.
8. **Bytes.** A hit post longer than `MESSAGE_BYTES` is cut to a window
   framed on its rarest matching term (the rule of `search.snippet`,
   `search.py:617`, at byte granularity); a neighbour is cut from the end
   nearer the hit. Windows are added in score order until `max_bytes`; one
   that does not fit is shrunk to its hit post alone, and if that does not fit
   either, expansion stops. The result is then put in transcript order.
9. **No match.** `mode = "empty"` with no excerpts, or with `fallback="tail"`
   the scene's last `2 * radius + 1` eligible posts within the budget (the
   end of a scene is where its outcome is).
10. **Whole.** With `allow_whole=True` and every eligible post fitting in
    `max_bytes`, the whole eligible transcript as one excerpt, `mode =
    "whole"`. Off by default: a caller with a budget should spend it on what
    matched.

### 10.2 Properties

- **Bounded by construction**: at most `max_excerpts` excerpts and
  `max_bytes` of content, whatever the transcript's length. The read is still
  a whole-file parse (a scene has no on-disk pagination, as
  `read_scene_window`'s docstring says, `scenes/read.py:169`); what is bounded
  is what reaches the caller. 09 sets `max_bytes` from its section budget
  (01i-C1); the defaults are structural (about a thousand English tokens per
  scene, a hit and its neighbours) and will be tuned with 09's evals.
- **Live, never cached.** Its answer depends on the terms and on the regex
  rule files; the rule files are not in any key, and an excerpt is cheap next
  to the call it feeds. 03 holds the parsed transcript if 03 section 8 ever
  wires one.
- **Read-only, no lock, no log of content.** A failure to read is `None`; it
  writes nothing and logs nothing but a failure kind.
- **Deterministic** for a given transcript, rule set and terms.

## 11. Contract

### 08-C1: A deterministic, bounded scene SearchDocument with metadata

- **Input:** a campaign id and a live scene (its `sid` and head), plus that
  campaign's ledgers, read by `searchdocs.slices`.
- **Output:** `SceneDocument{key, text, meta}`: `text` as section 4.3, at most
  `DOC_BYTES = 6000` UTF-8 bytes and at most 5803 by construction; `meta` as
  section 4.4; `key` as section 5.
- **Guarantees:**
  - the same slices give the same text, metadata and key, byte for byte;
  - the key moves exactly when a slice of this scene or
    `SEARCH_DOCUMENT_VERSION` or `BUILD` moves; appending a post to the scene
    moves nothing;
  - the text contains no transcript message text, so it has no regex phase
    (`meta.phase == "none"`);
  - sid-keyed ledger rows contribute only to a scene whose head says it is
    absorbed;
  - neither the `sid` nor `closed_by` is in the cached payload;
  - the document is never the only copy of anything, and nothing decides a
    write from it.
- **Failure:** never raises for a damaged ledger or card; the input is named
  in `meta.degraded`. Raises `CampaignNotFound` for a campaign that is not
  there.

### 08-C2a: Lazy build and live-set lookup (refines 08-C2)

- **Input:** `scene_documents(cid, exclude=, only=)`.
- **Output:** `LiveDocument{sid, closed_by, doc}` for every live scene, in
  play order.
- **Guarantees:** the live set is read from the filesystem on every call; keys
  are computed before any lookup; one batch lookup per call (03-C6); a miss is
  built from slices in hand and stored with `materialized` rows for each path
  it read (03-C3); a superseded document is unreachable (03-C2); answers are
  identical with the compiled cache on, warm, cold or off.
- **Failure:** cache failures are misses (03 section 12). Store damage as
  08-C1.

### 08-C2b: Document vectors keyed by space and text (refines 08-C2)

- **Input:** `vectors_for(cid, docs, space=, client=, warm_limit=, rotate=,
  deadline=, budgeted=, scene=)`, with `space` from
  `embed_space.endpoint()`.
- **Output:** `DocVectors{vectors, indexed, corpus, error}`.
- **Guarantees:** vectors are read and saved under the document space key
  (01h-C3, input type as sent) and the exact text, never under `BUILD`
  (03-C7); at most `warm_limit` documents are embedded per call, chosen by a
  rotating window; one ledger row under `history-index` per call that sent a
  request, attributed to `cid` and, when given, `scene`; a `materialized`
  vector row per embedded scene path.
- **Failure:** never raises; returns what was cached and the error kind.

### 08-C2c: Hot rebuild via 05 (refines 08-C2)

- **Input:** edited store-relative paths (05-C3 calls `affected`, then
  `rebuild_documents`, then, off the request path, `reembed`).
- **Output:** per campaign, the rebuilt keys and the re-embedded count.
- **Guarantees:** only documents that were materialized are rebuilt; only
  documents that were embedded in the current space are re-embedded, and only
  where their text changed; `affected` and `rebuild_documents` touch no
  network; `reembed` is bounded per campaign; summaries hold no content.
- **Failure:** a failed rebuild or re-embed leaves the next read to rebuild
  lazily. It never fails the write that triggered it.

### 08-C3a: The `history-index` embed task (refines 08-C3)

- `"history-index"` in `routing.EMBED_TASKS`, with `embedded.vectors_for` as
  its call site, landed together; no route claims it; `resolve.resolve`
  refuses it like every embed task (`inference/resolve.py:694`).

### 08-C3b: Transcript expansion (refines 08-C3)

- **Input:** `expand(cid, sid, phase=, terms=, identity=, radius=,
  max_excerpts=, max_bytes=, fallback=, allow_whole=)`.
- **Output:** `Expansion` or `None`.
- **Guarantees:** `phase` is required and only `prompt` or `display`; the
  whole transcript is viewed in that phase before matching; hidden posts and
  director notes are never returned; prompt-phase images are alt text; at most
  `max_excerpts` excerpts and `max_bytes` of content; indices are absolute;
  the identity, when given, is checked before and after the read;
  deterministic.
- **Failure:** `None` for a scene that is gone or no longer carries the
  identity; `ValueError` for a bad `phase`; never a partial write, never a log
  line with content.

## 12. Interaction with repo rules

- **Embedding call sites** (CLAUDE.md). One door (`embed_sync`), a task in
  `EMBED_TASKS`, `space` from `embed_space.endpoint` resolved once by the
  caller and read under before embedding, one row per call that sent a
  request, nothing filed for a call that sent nothing, `budgeted=True` only
  when the deadline is the caller's own budget. `test_operation_guard.py`'s
  provenance walk follows `vectors_for`'s `space` parameter to its package
  callers; 08 lands with at least one (the 05-C3 hook), and the plan checks
  what the walk does with a parameter that has none.
- **Regex.** The document has no phase (section 3.2) and a test holds it. The
  expansion helper takes the phase as a required argument. The plan extends
  `test_regex_prompt_guard.py`: `store/searchdocs/` joins `_sources`
  (`:73`), and a call `expand.expand(..., phase="prompt")` counts as a prompt
  view in `_is_prompt_view` (`:92`), so a 09 reader that renders a prompt from
  excerpts passes only if it asked for the prompt phase; a planted reader that
  asks for `display` fails. No `# regex-ok` marker is spent.
- **Imports.** `test_import_guard.py`: module-scope imports only, acyclic, and
  inside `store/` cross-package imports bind submodules (`from ..continuity
  import effective`, `from ..scenes import read as scenes_read`). Nothing the
  package imports may import it back; `store/export.py` is imported only by
  `expand.py`.
- **Locks.** Nothing here mutates campaign state, so no `store/locks.py`
  entry (`test_lock_domain_guard.py` rejects a phantom `OUTSIDE_DOMAIN` entry,
  the precedent 03 section 13 cites). Reads take no campaign lock (section 6).
- **Atomic and paths.** No record writes. Vector files go through
  `vectors.save` (`atomic.write_bytes`); 03 owns SQLite. Every path is built
  by an existing resolver.
- **Revision token** (`store/revision.py`). Cache and vector writes are not
  campaign writes and stamp nothing.
- **Detached runs.** 08 starts none. A caller that embeds from a detached run
  (09's warming, 05's re-embed) is covered by that run's own rules, including
  the `PUT /config/data-dir` refusal while it is live.
- **Metering and errors.** Every request is metered by `embed_sync`; failures
  are recorded once, at `Meter.done`, kind and status only. 08 writes no log
  line of its own for an embed failure. A degraded input logs once per kind
  per process through `store/logs.py`, naming the input and never its text.
- **Privacy.**
  - Document text is derived private text in 03's per-device file. It is
    covered by 03's whole-cache purge when a world or campaign is deleted
    (03 section 12); 08 adds no storage location of its own.
  - **A deleted scene's document is not purged**; it becomes unreachable at
    once (no live scene hashes to it) and is evicted by LRU, 03's stated
    residual for every derived row. Its content is also what the store itself
    keeps: `delete_scene` leaves the chronicle record and ledger rows in place
    (section 1.2). Open question 6.
  - Vectors are not text, but an embedding is partly invertible. Document
    vectors follow lore's and search's: under `.cache/embeddings/`, excluded
    from backups, not purged on delete (open question 7). 03's Settings text
    on sharing boundaries already names `.cache/embeddings/`.
  - The embedding provider receives document text only when a caller warms,
    and only through the Embedding role the user chose and confirmed
    (CLAUDE.md: a settings move that re-embeds asks first). 09 decides whether
    history retrieval is opt-in; 08 sends nothing on its own.
  - Fixtures use invented names only (Seraphine, Mara, Winifred, Realm,
    Saltmarch). No constant here is justified by a measurement of anyone's
    store.
- **Android and pydantic.** Pure Python on the base dependencies; SQLite only
  through 03, which degrades to "cache off". No `BaseModel`.
- **Frozen campaign.** The read-only sweep gains the frozen campaign's scene
  documents (text and metadata, `sid` attached), so a change in how an older
  store's records project is caught. `snapshot.json` is regenerated
  deliberately in the PR that adds them and reviewed as new text.
- **Docs.** CLAUDE.md's embedding paragraph names the new caller;
  `test_docs_guard.py` is run against it.

## 13. Tests and acceptance

All on `tmp_path` stores (`GRIMOIRE_HOME`), with placeholder names. A fake
embeddings client is injected at `vectors_for`'s `client` parameter.

**08-C1, document** (`test_searchdocs_scene.py`):

- **Determinism.** Two calls, a simulated restart (statcache and 03 reopened)
  and a hand-edited ledger with reordered keys all render byte-identical text,
  metadata and key.
- **Bounds.** A worst-case fixture (every field past its cap, many cast and
  threads, CJK and 4-byte characters at every clip point) renders at most
  5803 bytes, every line within its cap, `backstop` false, and every clip on a
  character boundary.
- **Key moves.** Each of: this scene's chronicle `summary`, `one_line` via
  `set_line`, keywords; a beat filed under it; a cast member renamed; a
  location renamed; a fact recorded and one retired here; a relationship delta
  here; a rolling-summary refold. Each moves this scene's key and no other
  scene's (except the renames, which move every scene naming that actor or
  place).
- **Key holds.** Appending a post (the head's `updated` moves); absorbing a
  different scene; an edit to another scene's beats; a ledger field the
  document does not read.
- **Version.** Bumping `SEARCH_DOCUMENT_VERSION` moves every key and, where
  the text is unchanged, triggers no embed call.
- **Recycled id.** Absorb a scene, delete it, create a new scene that takes
  its `sid`: the new document carries no chronicle, beats, facts or
  relationships.
- **Rename and re-pad.** The `sid` moves, the key does not, and the live set
  reports the new `sid`.
- **Branches.** A closed sibling is reported with `closed_by`; the cached
  payload holds neither `sid` nor `closed_by`.
- **No transcript text.** A distinctive word that appears only in a post is
  absent from the text; a campaign regex rule (prompt and display) that would
  match the text changes nothing.
- **Degraded.** A garbled `plot.json`, a list-shaped `facts.json`, a card
  that will not parse: the document renders, `degraded` names each, nothing
  raises.
- **Identity.** A scene without identity has `identity: ""`; 08 never writes
  one.
- **Groups (when 07-C2 lands).** A membership edit moves the key and `meta`,
  not the text.

**08-C2a, live set and cache** (`test_searchdocs_live.py`):

- Equivalence with the compiled cache empty, warm, off, and after a restart.
- A superseded document is never returned after an edit, in process and after
  restart, with no sync run.
- One batch lookup per call (counted on 03's seam), keyed only by live keys.
- Every stored document leaves `materialized` rows for the scene file and each
  input file it read; dropping that table changes no answer.
- After a campaign delete, no `searchdocs.scene` row remains in this device's
  file (03's purge, asserted for this kind).

**08-C2b and 08-C3a, vectors** (`test_searchdocs_embed.py`):

- At most `warm_limit` texts per call; the window rotates with `rotate`; a
  permanently refused document does not stop the others warming.
- Vectors are saved and read under the document space key; with 01h-C1, a
  query-typed key does not find them.
- One ledger row per call under `history-index`, with the campaign and the
  given scene; a failure returns the cached vectors and the kind and raises
  nothing; a deadline spent before sending files nothing.
- A `materialized` vector row per embedded scene path.
- `test_operation_guard.py` passes with `MIN_EMBED_CALLS = 7`;
  `test_inference_embedding.py:269` carries the new tuple.

**08-C2c, hot rebuild** (with 05's harness):

- Three scenes' documents built, two embedded. Editing `chronicle.json` for
  one of them rebuilds the three, re-embeds only the embedded one whose text
  changed, and embeds nothing for the never-embedded one.
- Editing a world location rebuilds hot documents in each campaign of that
  world; a campaign with its own copy re-derives the same keys and embeds
  nothing.
- `affected` and `rebuild_documents` make no network call (the fake client
  asserts it is not called); the summary holds no text.

**08-C3b, expansion** (`test_searchdocs_expand.py`):

- `phase="store"` and any other value raise `ValueError`.
- A prompt-phase rule that strips a block: a term only inside the block
  matches nothing in `prompt` and matches in `display`.
- A depth-ranged rule gives the same text for a post as
  `assemble`'s whole-transcript view does.
- Hidden posts and director notes never appear; indices are absolute.
- Images are alt text in `prompt`, markdown in `display`.
- `max_excerpts`, `max_bytes`, `radius` and `MESSAGE_BYTES` hold on a long
  fixture; overlapping windows merge; output is in transcript order and
  identical across calls.
- A renamed scene is found by identity; a deleted one, or one replaced
  between the two identity checks, returns `None`.
- No match: `empty`, or `tail` with `fallback="tail"`; `whole` only with
  `allow_whole` and a fitting transcript.
- The extended regex guard flags a planted reader that renders a prompt from
  `expand(..., phase="display")`.

**Acceptance** (the draft's section 13, reconciled):

1. Scene SearchDocuments are deterministic, bounded and disposable: deleting
   `.cache/compiled/` and `.cache/embeddings/` changes no answer, only cost.
2. A campaign's documents and metadata are retrieved without parsing any
   transcript.
3. Their embeddings use the Embedding role's space, typed as documents where
   the provider supports it, keyed by text.
4. An edit to any input makes the old document unreachable on the next read,
   with or without 05; with 05, what was hot is rebuilt and what was embedded
   is re-embedded, off the write path.
5. Relevant transcript excerpts can be expanded from a selected scene,
   bounded, in the phase the caller names.

## 14. Non-goals

- Post-level or passage-level vectors over history. Added only if 09's evals
  show scene-first retrieval misses evidence the expansion helper cannot
  recover; if added, they are content-addressed and unreachable once their
  source moves, like everything here.
- Document families for characters, PCs, groups, threads, commitments, events
  or saved scene ideas (draft section 6).
- Events in the scene document. They have no scene link, and relating one to
  a scene by date means running calendar plugin code in a renderer that must
  be deterministic.
- Transcript-derived document text (section 3.2; open question 4).
- Ranking, thresholds, a prompt section, a token budget, query embedding,
  widening tiers, an FTS index (09).
- Any background warm-up or startup sweep. Nothing embeds a library because a
  cache exists.
- Fixing the recycled-`sid` hazard of sid-keyed ledgers (section 4.2). It
  belongs to scene deletion.
- Changing `semsearch.py`, the recap, the timeline or lore recall.

## 15. Open questions

1. **Groups in text or metadata only?** Text would date current membership
   onto past scenes and re-embed every scene an actor appeared in when a
   membership changes. *Recommendation: metadata only (section 4.6); revisit
   if 09's evals show group queries missing.*
2. **Gate sid-keyed slices on the head's `done`?** It drops rows a recycled
   `sid` inherited, and drops nothing legitimate, since absorb writes those
   rows. *Recommendation: yes.*
3. **Use the rolling summary for unabsorbed scenes?** It may describe posts a
   cut removed until it refolds. *Recommendation: yes, labelled `Running
   summary`; it is a candidate signal and evidence comes from the transcript.*
4. **A transcript-derived field for scenes with no summary at all?** It would
   need the prompt view, put the rule files in the key, and churn on every
   post of an open scene. *Recommendation: not in v1; revisit with 09's evals
   on campaigns played with `rolling_summary_every: 0`.*
5. **Persist the input projections (heads, per-ledger slice tables) as 03
   kinds?** *Recommendation: only where 03 section 8's synthetic-library
   measurement shows the warm-after-restart win; keep the in-process memo
   always.*
6. **Purge a deleted scene's document?** The row is unreachable at once, and
   the store keeps the same records anyway. A targeted purge would need the
   key computed before the delete. *Recommendation: no; LRU, as 03 accepts
   for every derived row.*
7. **Purge document vectors on campaign delete?** Lore and search vectors are
   not purged today; the texts that name the files live in the cache 03 has
   just purged. *Recommendation: no, consistent with today; the Settings text
   says so.*
8. **Where does the hot re-embed run?** It must not run on the write path or
   under a lock. *Recommendation: 05 runs it as an after-response follow-up
   (the `_fire_follow_up` shape); if 05 has no such vehicle, leave it to 09's
   lazy warm. Missing edge on 05-C3 if 05 does not provide one.*
9. **One task or two for history embeddings?** *Recommendation: 08 registers
   `history-index` (documents); 09 registers `history-recall` (queries) with
   its own call site, since the guard forbids a task with no call site and the
   two are separate requests once input types differ.*
10. **`allow_whole` default.** *Recommendation: off; the caller opts in when
    its budget is meant for whole short scenes.*
11. **Is 07-C2 a hard edge?** *Recommendation: soft. Groups are metadata, so
    08 can land before 07 and add the field later with a version bump that
    re-embeds nothing.*

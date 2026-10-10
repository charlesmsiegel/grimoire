# Content-addressed compiled cache: the files stay the database, SQLite caches what is compiled from them

**Status:** Draft — cross-linked. A substitute reviewer and the PR's Codex
review are both folded in (section 15). The `/codex:adversarial-review` spec
gate is still pending.
**Date:** 2026-10-09
**Series:** 03. Depends on 01, the inference refactor
(`2026-10-07-inference-backend-refactor-design.md`, landed as slices A
through I). This spec relies on 01 for one thing only: the embedding space id
(`ResolvedInference.space_id`, reached through `embed_space.endpoint()`).
**Consumers in the roadmap:** 04 (instant Worlds, Campaigns, Todo and shell),
05 (direct-edit cache sync), which 06's store-editing skill drives, 08 (derived
history SearchDocuments) and 09 (hybrid retrieval). 07's inverse membership
index may also live here. Section 2a is the contract those items rely on.
**Baseline reconciled against:** `main` at `7f833e7` (#493), and against the
roadmap bundle of 2026-10-06 (specs 04–09).
**Supersedes, in part:** the "No stored aggregates or index files" non-goal of
`2026-08-28-read-path-performance-design.md`. Section 2 says how far.

> **Implementation rule:** before coding any slice, re-read the code it
> touches and every PR that has landed on that subsystem since `7f833e7`.
> Preserve landed behaviour unless this spec explicitly supersedes it.

**Roadmap:** 03 in `ROADMAP-CHECKLIST.md`. Lane: cache.

## Depends on

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| Embedding space id (`ResolvedInference.space_id`) | 01 (landed) | The space half of the vector key (section 2a, item 7) | Hard, and met |
| 01h-C3: embedding options in the space identity | 01h | Embedding options added later (input type, dimensions) must change the vector key, or two different embeddings of one text would share an entry | Soft: 03 works without it, and 01h must keep existing option-less keys valid |

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 03-C1 composite keys over collection digests and non-file inputs | 04, 08 | Card, Todo and shell projections; SearchDocument keys |
| 03-C2 liveness by construction | 04, 05, 09 | No superseded version is reachable; 05's sync is a warm-up |
| 03-C3 `materialized` record | 05, 08 | Rebuilding what was hot after an edit |
| 03-C4 one validation primitive | 05 | Write-through and `cache sync` without a second invalidation system |
| 03-C5 artifacts storable at write time | 04, 05 | No stale overview after Grimoire's own write |
| 03-C6 batch lookups over a live key set; index ranking restricted to it | 08, 09 | Metadata prefilters and lexical ranking |
| 03-C7 vectors keyed by text, never `BUILD` | 08, 09 | Upgrades re-embed only changed text |
| 03-C1, 03-C2, 03-C3 | 07 (S) | The optional persistent tier of the inverse membership index (07-C2), and its eager rebuild via 05 |
| 03-C8 a callable purge for a world or campaign delete | 05 | Sync performs the purge when it finds a world or campaign root gone (05 section 7.6) |
| 03-C9 a synthetic-library generator | 04 (extends it for 04-C3b) | Benchmarks and equivalence tests on a library no user owns |

Section 2a states C1–C7 in full. Its numbered items map to them in order:
item 1 to C1, item 2 to C2, and so on. Two later additions refine C1 and C6:
a collection can take a member filter (section 7), and lookups report hit and
miss counts (section 9). C8 is in section 12, and C9 is in section 14.

## 1. Goal

Persist expensive, deterministic read products across requests **and across
process restarts**, in one disposable local SQLite file, while keeping the one
rule that matters:

> Markdown and JSON files are the only authoritative datastore. The cache may
> make a read cheaper. It may never decide what a read answers, and never
> decide what a write does.

It is also meant to give later search and embedding work one dependency-keyed
substrate. It is not a second store, not an index of membership, and not a
place that answers questions nobody has asked the files.

## 2. What is already here, and what this adds

Grimoire is not uncached today. Know what has landed before changing anything:

- **`store/statcache.py` is the in-process layer.**
  - `memo(kind, signature(...))` is keyed on `(path, mtime_ns, size, ino)`.
  - `memo_stamped` handles values whose inputs are only known after computing
    them. It is keyed on `stamp()`, which is `(path, mtime_ns, ctime_ns, size,
    ino)`.
  - It also has `RACY_WINDOW_NS` (one second), pools owned by callers, the
    rule that a stamp is taken *before* the read it vouches for, and the rule
    that the racy window is measured from the start of a computation rather
    than its end.
  - Its memo kinds include `world_row`, `campaign_row`, `scene_head`,
    `card_summary`, `dir_hash` and `pc_dir_hash` (the memo kinds behind
    `characters.dir_hash` and `pcs.dir_hash`), `entity_hash`, `greeting_meta`,
    `plotmap_hash`, `body_tokens` (`tokens.record_tokens`),
    `image_store.read`, `image_store.blob_intact`, `pricing:provider`,
    `usage:unpriced` and `inference:campaign_meta`, plus `search.py`'s
    whole-corpus extraction pool.
  - All of it is lost when the process exits.
- **Two persistent derived records already live under `<home>/.cache/`:**
  - `store/migrations.py` keeps a per-campaign scene-identity record at
    `.cache/scene-identities/<cid>.json`. It is keyed by stamp and outlives
    the process. It already states the stricter rule that persistence needs:
    neither mtime nor ctime may be inside the racy window.
  - `store/vectors.py` keeps an embedding cache at `.cache/embeddings/*.vec`.
    It is keyed by `sha256(space + "\0" + text)`, so it is already
    content-addressed on the projected text plus the embedding space. That is
    exactly the key the draft of this spec proposed for an `embeddings` table.
    Records are little-endian float32 with a CRC32 each, and any bad file
    reads as a miss.
- **`store/usage_rollup.py` is a persistent aggregate with a version in its
  file name** (`rollup-v{VERSION}.json`). A rollup written by an older build
  is never read.
- **`.cache/` is already treated as derived.**
  - `backups.py` excludes it unconditionally (`_DERIVED`).
  - `external.SKIP_AT_ROOT` skips it.
  - Image GC refuses to run through a `.cache` that is a link.
  - It **travels with a synced store**. That is why image GC keys its
    sightings per device (`maintenance_reports.device_key(root)`).
- **Not landed:** section 3 of the read-path spec, the library epoch and
  `ETag`/`304` on JSON reads. The only `ETag`s today are on images and static
  files. Nothing here depends on that section or blocks it.

The read-path spec rejected stored aggregates because each one is *"a second
store to keep consistent"*. This spec revisits that, on one condition that
answers the objection: **the cache is only ever consulted through a key
computed from the current bytes on disk.** A stale row therefore cannot be
found, only left unreachable. "Keep it consistent" shrinks to one trust point,
the persisted stamp→hash map in section 5. That map is held to stricter rules
than the in-process layer, and it is never used by anything that decides a
write.

On its own, this spec adds little performance:

- **Warm-after-restart reads** cost a stat and a lookup rather than a
  recompute.
- **The same bytes at a different path** (a fork, or a campaign's copy of a
  world record) cost a read and a hash, but no recompute.

The same-process warm path is already cheap, and it must not get slower. Its
real value is as the **substrate** the roadmap builds on: 04's page
projections, 05's eager rebuild of what was hot, and 08 and 09's
SearchDocuments. Each of those would otherwise invent its own persistence,
keys and invalidation. So 03 is judged by whether it carries them. Section 2a
says what that takes, and section 8 keeps its own first consumers to the ones
that prove the substrate.

## 2a. What the rest of the roadmap needs from this

Each item below is a requirement on 03. None is an implementation of the later
spec.

1. **Composite keys over collections and over inputs that are not files (04,
   08).**
   - 04's world card, campaign card, per-scope Todo and shell projections are
     keyed by the hashes and collection digests of what each one renders.
   - 08's SearchDocument is keyed by the digest of its inputs plus
     `search_document_version`.
   - Section 6 has to carry these keys, including inputs that are not a file's
     bytes: a chore's ignore set, a config value, a continuity cache's hash.
   - It must not force one whole-library hash, which 04 section 4 rules out.
2. **Liveness by construction (05, 09).** 05's core invariant is that *after
   sync, no live query may return content derived from a superseded source
   version*. 03 makes that true at every read, not only after a sync.
   - A key is computed from the current bytes, so a superseded artifact has no
     live key that could reach it (section 9).
   - There is no live source→hash mapping for 05 to update. The filesystem is
     that mapping.
   - 05's sync is therefore an **eager warm-up** (rebuild what was hot) and
     never a correctness step. Its "remove the old hash from live mappings"
     step holds already.
3. **A record of what was materialized (05, 08).** 05 rebuilds "what was hot"
   for an edited path, and 08 rebuilds a hot SearchDocument and its vector.
   Both need to know which kinds were built from a path. 03 keeps that record
   (section 4, `materialized`). The policy that acts on it is 05's.
4. **One validation primitive, reused (05).** 05 must not introduce *a second
   invalidation system*. The validate-and-hash step reads use (section 5) is
   exposed as the primitive that 05's in-process write-through and its CLI
   both call.
5. **Persisting an artifact is always safe, even straight after a write (04,
   05).** An artifact is keyed by the hash of bytes that were actually read,
   so storing it can never vouch for bytes it did not see. Only the stamp→hash
   row in `sources` is subject to the racy window. So 05's write-through, and
   04's rule that *normal app use should not show a stale overview after its
   own write*, can rebuild and store artifacts at write time. The one thing
   deferred is the `sources` row (section 5, rule 4).
6. **Batch lookups over a live key set, with indexes allowed for ranking (08,
   09).**
   - 08 filters SearchDocuments by metadata (cast, location, date), and 09
     adds lexical and semantic candidates over them.
   - Both start from the live set (the current scenes' keys) and look up in a
     batch.
   - 09 may rank with an index, for example SQLite FTS5 if the Android build
     carries it, but only over rows whose keys are in the caller's live set.
     Section 9 holds that rule.
7. **Vectors are keyed by text, not by build (08, 09).** If 01h adds
   embedding options (input type, dimensions), they join the space identity
   (01h-C3) and so the key. One text embedded two ways must never share an
   entry. 08 keys an embedding on `embedding_space + hash(exact
   SearchDocument text)`, which is `vectors.py`'s key today. Vectors are not
   keyed by `BUILD` (section 6). An upgrade re-derives SearchDocuments, and
   costs an embedding call only where the rendered text actually changed.

## 3. Location: in the store, one file per device, schema in the name

The file is `<home>/.cache/compiled/<device>-v<SCHEMA>.sqlite`. `<device>` is
the first 32 hex characters of `maintenance_reports.device_key(home())`,
memoized per process and root.

This departs from the draft's `<home>/.cache/compiled.sqlite` for three
reasons:

- **The store may be a synced folder, and `.cache/` syncs with it.** Two
  devices opening one SQLite file through a sync client would each replace
  the other's open database. A per-device file is never opened by another
  device, so the sync client only ever copies a file that has a single owner.
  That costs bandwidth, but it cannot corrupt anything. A user can exclude
  `.cache/compiled/` from their sync, and the Settings page that already
  explains sharing boundaries should say so.
- **Derived private content stays inside the root the user chose.** The cache
  holds parsed frontmatter, flattened transcript text for search, and later
  search documents. A user who put their library on an encrypted or removable
  volume chose where that content lives. Machine-local state outside the root
  is acceptable for lock files and a device id, which carry no content, but
  not for this. Vectors and thumbnails already follow the same rule.
- **The schema number is in the file name**, following `usage_rollup`'s
  precedent. Two builds that disagree about the schema then open two files and
  never rename each other's file back and forth.

`device_key` hashes the device id together with the spelling of the root
path. So a data-dir move, a library copied on the same machine, or a second
spelling of the same root opens a fresh file. That costs a cold cache, never a
wrong answer. Files nobody opens any more are removed by section 10.

The device key is reached defensively:

- `device_id()` can wait up to five seconds on its proclock, or raise. So the
  key is memoized per (process, root).
- If the key cannot be had, the cache is off for that process, with one log
  line (section 12).

Two side effects of where the key lives:

- **Tests.** `GRIMOIRE_HOME` already isolates the cache file. The device id's
  own file, under `proclock.lock_dir()`, is not isolated by `conftest.py`
  today; only individual tests patch it (`test_image_gc.py`,
  `test_maintenance_routes.py`, `test_maintenance_runs.py`). The plan adds an
  autouse `lock_dir` fixture beside `_isolate_bootstrap_pointer`.
- **A copied device id.** If a migration tool copies `~/.local/state` from one
  machine to another, the two machines share an id and so open one file
  through the sync client. That is the residual `maintenance_reports` already
  carries.

The database:

- is excluded from backups, bundle exports and the external scan, because
  everything under `.cache/` is;
- may be deleted at any time, by the user or by the app;
- is rebuilt lazily, by reads, and never by a startup sweep;
- carries no promise of migration.

The path is built from `home()`, so `test_paths_guard.py` holds without a
marker. Being built from `home()` does not keep it there, though. If `.cache`
or `.cache/compiled` is a symlink or a junction, SQLite would write derived
private text outside the root the user chose, and the purge and retention
rules (sections 10 and 12) would act on a directory that is not the cache.
So the cache is **off** for any process that finds a link or junction in
either component. This is checked when the cache opens and again before any
purge or removal, the same rule image GC applies (`image_gc._store_blocks`).

**No handle stays open while the app is idle, and no sidecar file is left
behind.**

- The journal mode is **DELETE**, the only mode that leaves no `-wal` or
  `-shm` file once the last connection closes. WAL also leaves its sidecars
  behind after a crash, and it is unsupported on network filesystems.
- Connections are closed after a short idle period.
- On `PUT /config/data-dir`, which only repoints the root, every handle to the
  old root is closed before the new one is used.
- A sync client or Files-On-Demand provider must never find a held handle in
  the store. `proclock`'s docstring explains why that matters on Windows.

## 4. Logical schema

Exact SQL is the plan's.

### `sources`: path to content hash, advisory

| field | meaning |
|---|---|
| `path` | relative to the store root, `/`-separated |
| `mtime_ns`, `ctime_ns`, `size`, `ino` | `statcache.stamp()`'s fields, taken **before** the bytes were read |
| `content_hash` | SHA-256 of the exact bytes read |
| `check` | checksum over `(path, stamp, content_hash)` (section 12) |
| `last_used` | coarse; batched (section 10) |

The draft made `kind` and `ref` optional. This schema has neither, because
both invite a query that starts from this table instead of from the
filesystem (section 9). This table answers exactly one question: *"what were
these bytes, the last time this file stamped like this?"*

### `artifacts`: content-addressed derived products

The key is `(kind, key_digest)`, the composite from section 6. The row holds an
encoded payload, a checksum over `(kind, key_digest, payload)`, and
`last_used`.

A kind is an entry in a registry in the cache module, never a free string at a
call site. Each entry declares:

- its name and a documentary `version`;
- its **path-derived inputs** (section 6);
- its `params`;
- whether it may use persisted `sources` rows at all (section 5, rule 5).

That makes one place list everything that persists and what each key covers.

### `materialized`: which kinds were built from a path

| field | meaning |
|---|---|
| `path` | a source the artifact read, relative to the store root |
| `kind` | the registry kind built from it, or `vector:<projection>:<space-digest>` for an embedding of a projection of it |
| `instance` | optional: which instance of a collection-keyed kind the path feeds (for example, the campaign a scene summary belongs to) |
| `last_used` | as for artifacts |

**The vector kind names the projection and a digest of the space**, never the
raw space id:

- A raw space id contains NUL bytes (01h-C3).
- A bare `vector:<space>` would let two projections of one source collide. For
  example, 08's SceneDocument vector and semantic search's vector over the same
  transcript would share a key.

05, 08 and the checklist's cross-spec decisions all use this form.

05 needs this record to rebuild "what was hot" for an edited path, and 08
needs it to keep a hot SearchDocument and its vector current (section 2a,
item 3).

- It is written beside the artifact, in the same batch.
- It is read only by path, for a path the caller already holds, so it never
  answers which paths exist (section 9).
- Losing it costs 05 its eager warm-up and nothing else: the next read
  rebuilds lazily.

The policy that acts on the record (rebuild now, rebuild later, never
re-embed what was never embedded) belongs to 05.

### No `embeddings` table in this spec

**Embeddings stay in `vectors.py`.**

- Its key, `sha256(space\0text)`, is already the projection hash plus the
  embedding space. After 01, `space` comes from the Embedding role's
  resolution (`embed_space.endpoint()["space"]`), and nothing here changes
  that.
- Its format is already justified by measured cost on the read path.
- 08's embedding key, `embedding_space + hash(SearchDocument text)`, is this
  key. So 08 and 09 can keep using `vectors.py` files.
- Whether vectors move into SQLite (for batch loading, or to share eviction
  with this cache) is 08's or 09's call. Whichever is chosen must keep the key
  exactly, and keep it independent of `BUILD`.
- The `materialized` record above covers vectors either way.

**03 does not rebuild anything eagerly.** Eager rebuild is 05's feature, built
on the `materialized` record and the primitive in section 2a, item 4. 03's own
consumers rebuild lazily, on the first read after an edit.

## 5. The trust point: when a persisted stamp may vouch for a hash

`sources` is the only place a stale answer could hide. So its rules are the
in-process rules made stricter, because the record outlives the process and may
be read on a filesystem with coarse or foreign clocks.

1. **Stamp before reading.** Note the time `t0`. Then take
   `statcache.stamp(path)`, then read the bytes, then hash them. A write that
   lands between the stamp and the read leaves a recorded stamp that no longer
   matches, so the next read hashes again.

2. **The age test is measured at `t0`, and against the filesystem's clock.**
   A row may be recorded only if `t0_fs − max(mtime, ctime) > PERSIST_WINDOW`.
   - `t0` is taken **before** the stat. If it were taken at recording time, a
     long read could age a stamp out of the window *after* a same-tick rewrite
     the stamp cannot see. `memo_stamped` measures from the start for exactly
     this reason, and `migrations._backfill_campaign`'s check should be
     corrected to do the same.
   - `t0_fs` is the filesystem's notion of now, not the local clock's. On a
     network filesystem, mtime comes from the server's clock, so a server that
     lags the local clock makes every file look old enough the moment it is
     written. The plan takes `t0_fs` from a sentinel in `.cache/compiled/`,
     touched and statted once per batch that has anything to record, the way
     git compares a file's mtime with its index's. The store root is one
     filesystem.
   - `PERSIST_WINDOW` is wider than `RACY_WINDOW_NS`. FAT and exFAT have
     two-second mtime resolution and round down, so a one-second window can
     pass a stamp that a same-size rewrite reproduces exactly. The constant is
     justified as *at least twice the coarsest timestamp granularity grimoire
     can find itself on, plus margin*. Three seconds is the floor; the plan
     sets the value.

3. **A hit needs all four fields to match:** mtime, ctime, size and inode.
   ctime catches an in-place rewrite that was handed its mtime back, which a
   sync client or `touch -r` can do. The inode catches a rename-replace.

4. **A write never records its own `sources` row.** The draft asked
   `store.atomic` to update `sources` straight after a write, and 04 and 05
   ask for write-through too. A file just written is, by definition, inside
   the window, so rule 2 forbids recording its stamp. Recording it anyway
   would undo the protection the draft itself asked to keep. Write-through
   still works, because the stamp row is all that is deferred:
   - a writer, or 05's sync, may hash the bytes it wrote and **store
     artifacts** for them at once (section 2a, item 5);
   - the next read in this process can take the hash from the in-process
     layer, which statcache's racy rule already governs;
   - the persisted row lands on the first read after the window.

   None of this needs `store.atomic`. 05 calls its primitive from the writers
   (05 section 6), and `store.atomic` stays free of SQLite.

5. **Anything that decides something reads bytes, not this table.** Some
   residuals are permanent once persisted:
   - On Windows, `st_ctime` is the creation time.
   - FAT and exFAT keep no real change time.
   - A rewrite that keeps size and mtime and is not caught by ctime will be
     believed for the life of the row, where the in-process layer believed it
     only until the next launch.

   So a kind may use persisted `sources` rows only where a wrong answer is
   **visible and recoverable**: a stale search line, a stale token figure, or
   a stale card blurb, which any later edit, the kill switch, or deleting
   `.cache/compiled/` repairs. A kind may **never** use them where its answer
   decides an action. That covers:
   - **sync base hashes.** `entities.entity_hash`, `characters.dir_hash`,
     `pcs.dir_hash` and `greetings.plotmap_hash` feed `sync.py`'s
     `mine_h == base_h`. A stale `mine_h` would turn a local edit into a
     silent `update` that overwrites it instead of a `conflict`;
   - **integrity checks.** `image_store.blob_intact` exists to notice bytes
     that changed without their stamp changing. Across restarts its re-read
     is the check;
   - anything a write, a delete or a refusal is conditioned on.

   These keep today's in-process statcache behaviour and gain nothing from
   this spec. The registry flag in section 4 makes the choice explicit for
   each kind, and the plan's guard fails a decision site that reaches a kind
   allowed to use persisted sources.

## 6. Keys

**Source hash:** SHA-256 of the exact bytes on disk, with no normalisation of
line endings, BOM or encoding.

**Composite artifact key:**

```
key_digest = SHA-256( kind
                    ‖ version       -- the registry entry's own
                    ‖ BUILD
                    ‖ params        -- canonical JSON of every non-file input,
                                    -- path-derived inputs included
                    ‖ inputs )      -- ordered (role, content_hash) pairs
```

**`BUILD` fingerprints everything that can change a derivation without
changing the bytes it reads.**

A restart used to clear every memo, so a changed parser could never leave
yesterday's parse in force. A persisted artifact would leave it in force
unless its key moved with the code. A version number per kind that someone has
to remember to bump is exactly how that gets missed. `BUILD` is computed once
per process and covers:

- the installed `grimoire` package's **source and package-data manifest**:
  `.py` files and the data the package ships (`builtin_modules/`,
  `climates/`, `docs/`), selected by a fixed list of suffixes. Bytecode
  (`__pycache__/`, `*.pyc`) and anything else Python or the app writes at
  run time are excluded. The supported installer runs from an editable
  checkout, where an import writes `.pyc` files inside the package, and a
  fingerprint that counted them would change the first time any module was
  imported, emptying the cache on the next launch for no change in any
  derivation;
- the templates directory (`prompts.templates_dir()`, which ships as APK
  assets and sits outside the package);
- `sys.version`, because the Unicode tables behind `str.split`, `casefold`
  and the regex engine move between Python releases;
- the `importlib.metadata` versions of the base and `desktop` dependencies,
  because a markdown, jinja2, holidays or tiktoken upgrade can change output.

There are two ways to produce it:

- **From a checkout or a wheel:** hash the files.
- **In the APK:** Chaquopy imports compiled code from an asset bundle, so
  hashing sources there is unverified. The gradle build writes a stamp over
  the same inputs, and the cache reads that stamp. If it is absent, the cache
  is off rather than guessing.

An upgrade or a local edit therefore makes the cache cold, which is correct.

**Vectors are not keyed by `BUILD`.** An embedding is keyed by its space and
the exact text embedded (section 4). After an upgrade, every artifact is
rebuilt, but re-rendering a SearchDocument to the same text finds its vector
already there. So an upgrade costs recomputation, and an embedding call only
where the text changed.

The coarse fingerprint means 04's pages are cold once after each upgrade, and
after each pull for someone running from a checkout. A narrower fingerprint
per kind (only the modules a kind's compute reaches) would keep more warm, but
an import graph is easy to get wrong. That is a later refinement, once
measurements show the cold-after-upgrade cost matters.
The plan measures what the fingerprint costs at start-up. Each kind's
`version` is part of the key, so bumping it does force a miss: that lets a test
isolate one derivation's change, and lets a deliberate change ship without
depending on `BUILD`. It is not the safety mechanism.

**`params` carries every input that is not a file's bytes:**

- **Configuration identities**, such as a tokenizer encoding name and version,
  or an embedding space id.
- **Path-derived inputs.**
  - Several derivations fall back to the path for a name: `search.py`'s
    markdown and card documents use `path.stem`, and `_world_row` and
    `_campaign_row` use the directory name.
  - So two files with identical bytes at different stems derive different
    rows. An untitled card, or a `tagline.md` with no frontmatter, would
    otherwise show the first file's name for the second.
  - Each kind declares the path parts it reads (stem, parent name), and those
    go into `params`.
  - Better still, the compute may return the path-independent part and let the
    caller add the path-derived name outside the cache.

A derivation whose output depends on configuration, the clock or another
record's live state either names that input in `params` or does not belong in
the cache.

**The compute receives what the key was computed from.**

- The cache API reads the bytes and hands them to the compute, together with
  the declared path parts. It never hands over a path for the compute to read.
  That way, "the key covers the input" holds by construction.
- A compute that used to call `read_text(encoding="utf-8")` has to decode
  exactly as that call did, including newline translation (`_first_paragraph`
  splits on `"\n\n"`). Section 14's equivalence tests use CRLF and non-UTF-8
  fixtures for this reason.
- `tokens.record_tokens` takes its text from the caller. Its persistent form
  keys on the hash of that text, not on the path.

**Some keys are only known once the compute has run.** The token counter is
the case today: `count_tokens` falls back to a characters-divided-by-four
heuristic whenever the encoder fails to load. It does that offline, because
tiktoken downloads its encoding file on first use and retries after
`RETRY_AFTER_S`. A heuristic figure must never be persisted under the
encoder's key, or an offline first launch would fix heuristic counts in place
for good. So:

- the compute may report "do not persist this one";
- the token kind persists only when the counter that ran is the encoder
  (`tokens.ENCODING`).

**Order is deterministic.** Inputs are ordered by their role in the
derivation. A collection's members are ordered by relative path.

## 7. Collections

Directory membership is authoritative and is always read from the directory:

1. List the directory.
2. Get each member's content hash. That is a stat, plus a read only where the
   stamp moved, under section 5.
3. Sort by relative path.
4. Hash the ordered `(relpath, content_hash)` list.

An add, a delete, a rename or an edit each change the digest, and the cache
never answers membership.

**A collection can take a member filter.** This is a predicate on the
relative path, declared by the kind in the registry and applied after the
listing. Without it, a file that sits in the directory but is not an input
would move the digest for nothing. 04's scene summary is the case: review
files live in `scenes/`, and they are not scenes. The filter is part of the
key's identity, so changing it changes the key. It never hides a member from
any reader other than the kind that declared it. A collection-keyed artifact saves the compute and
the read for each member. It does not save the listing.

The draft hoped that counts which currently need directory sweeps could come
from collection digests. Two kinds of "count" need telling apart:

- **A count of members** (how many entities a world has) *is* the listing. The
  digest needs the listing, so caching the count saves nothing.
  - Getting it more cheaply would mean persisting a directory's stamp as a
    voucher for its listing. `statcache.stamp` documents that a directory
    stamp can do that, and `migrations` relies on it for the identity marks.
  - But a persisted listing is a membership answer, which section 1 forbids.
  - Member counts stay live reads, which costs a listing per directory.
- **A predicate over members' content** (which characters lack an avatar or
  an anchor, coverage, a gap probe) has to read every member. That is
  expensive, and it is exactly what a collection digest can key. 04 section
  7's *"invalid/missing-record probes that require directory sweeps"* are
  this kind, and they belong in the cache.

## 8. What to cache first

**The page projections are 04's, not 03's.** World cards, campaign cards,
per-scope Todo projections and the shell's projections are 04's targets, and
04 is the roadmap's *"first user-facing proof that the compiled-cache
architecture is worth having"*. 03 must make them expressible (section 2a,
item 1). 03 does not choose their shapes.

03's own consumers prove the substrate: keys, trust, failure, eviction,
guards. Each must also pass the cost test below.

The draft listed targets by page. This list keeps those pages but orders the
targets by one structural test. The cache only helps when **compute plus read
costs much more than a stat, one indexed lookup and a decode**. Before wiring
a kind, the plan shows that inequality on a synthetic library, never on the
user's library (`CLAUDE.md`'s privacy rule). It tunes thresholds against real
prompts later, in conversation, and commits no figures.

**Likely to pass** (each may use persisted `sources` under section 5, rule 5):

- **`search.py`'s per-file extraction.** It parses frontmatter, selects card
  fields and flattens whitespace for every file on every query, including
  whole scene transcripts. After a restart, that is the whole corpus. This is
  also the shape a later search-document projection will take.
- **Token measures** (`tokens.record_tokens`, memo kind `body_tokens`).
  Tokenizing is bound by CPU and scales with the length of the body. The key
  is the text hash plus the encoding identity in `params`, and only
  encoder-counted values persist (section 6).
- **The other whole-transcript readers.** A transcript is the largest file a
  campaign holds, and a played campaign only grows. Today search, the
  exporters and the context builder's history projection each read whole
  transcripts. A parsed transcript shared by those three is the candidate.
  The draft named chronicle, involvement and timeline here, but none of those
  three reads a transcript:
  - timeline reads scene heads, `chronicle.json` and `plot.json`;
  - involvement reads appearances and `chronicle.json`;
  - chronicle reads location and time history.

  They are small-file projections, and they wait for a measurement like
  everything else.

**Wire last, and only if measured:**

- **World and campaign list rows** (`world_row`, `campaign_row`). The draft
  put these first.
  - Each row parses one small frontmatter, which costs about as much as the
    lookup that would replace it.
  - The shelf pages are slow because of their counts (section 7) and what they
    join, not because of this parse.
  - This kind is still a good *first consumer* for proving the wiring end to
    end, because its output is easy to assert byte for byte, with the
    directory name declared as a path-derived input. It is not where the
    performance claim is made. 04 replaces it with the card projection the
    page actually renders.
- **`scene_head`.** It is already a head-only read.
- **Roster projections and the story graph's inputs** (draft section 8). They
  are deferred, not dropped. Each is a composite over files the kinds above
  already cover, so it becomes a candidate once those land and a measurement
  shows its own composition is the cost.

**Never cached:**

- **Sync base hashes and integrity checks** (section 5, rule 5):
  `entity_hash`, both `dir_hash`es, `plotmap_hash` and `blob_intact`. This
  reverses the draft's hope that `dir_hash` could come cheaply from `sources`.
  It cannot, for two reasons. First, these hashes decide sync outcomes. Second,
  none of them is a hash of raw bytes: `entity_hash` is SHA-256 of the
  newline-translated UTF-8 text, and `dir_hash` is one SHA-256 over
  concatenated `(name, text)` pairs. Both are fixed by recorded sync bases and
  cannot change formula.
- **A Todo or shell projection whose key does not cover every input.**
  `routes/todo.py`'s contract is that *"nothing here is a cache that can
  outlive the read it was computed for"*. A content-keyed projection keeps
  that contract, because its key is recomputed from the current inputs on
  every request and cannot answer for inputs that have moved. 04's per-scope
  Todo projections are this kind, and 03 allows them.
  - What 03 forbids is a projection keyed on *less* than the chore reads.
    Chores read more than record files: the ignore set
    (`store/chores.py`), routing and config, the usage ledger, the continuity
    candidate cache. A chore whose inputs cannot all be named in its key
    stays live.
  - Todo stays derived and self-healing (04 section 10). 04 amends the
    `routes/todo.py` docstring when it lands.
- **Anything that reads `config.md`, routing or the clock** without naming that
  input in `params`.
- **The `/api/shell` money figures.** `usage_rollup` owns those, with its own
  bookmark semantics.

## 9. Query safety

No read starts from the cache. Every read:

1. starts from live paths derived from the filesystem;
2. computes their current keys;
3. only then looks up rows for those keys.

Nothing enumerates `artifacts` or `sources` to learn what exists, so a stale
row is invisible before any eviction runs. That is what makes eviction a
question of storage rather than correctness.

The plan holds this with a guard in the house style. One module owns `sqlite3`
and the cache's tables, and the guard fails both of these:

- a read API that does not take the caller's keys or live key set;
- an import of `sqlite3` anywhere else.

**Lookups report what they found.** Every batch lookup returns hit and miss
counts beside the rows, for 04-C3a's per-request counters and the benchmark.
This is a count, never a list of keys. No API answers "what is cached".

An index may *rank* within a live set. 09's lexical candidates might come from
SQLite FTS5, if the Android build has it, but every such query is restricted
to keys the caller computed from the filesystem before anything is returned.
That is the draft's own rule (*"score only those live hashes"*), stated once
for every index a later spec adds.

The `test_*_guard.py` modules that parse the package's own syntax trees are
the precedent.

## 10. Bounding the file: eviction, not reachability

The draft proposed mark-and-sweep. Marking needs a walk of the whole store,
and correctness never depends on the sweep. A cheaper rule is enough:

- **Least-recently-used eviction by `last_used`, under a size cap, measured on
  live pages.**
  - Deleting rows does not shrink a SQLite file, so a loop that watched the
    file's size would never reach its low-water mark and would empty the
    cache.
  - Measure size as `(page_count − freelist_count) × page_size`.
  - Run with `auto_vacuum=INCREMENTAL`. That setting only makes it
    *possible* to hand freed pages back; deleting rows hands nothing back. So
    every eviction ends with an explicit, bounded `PRAGMA
    incremental_vacuum(N)`, and the plan's test shows the physical file
    shrinks. Without it, the cap would bound live pages while the file stayed
    at its high-water size, both on disk and in what the sync client
    uploads.
  - Eviction is opportunistic: it runs on a write batch, at most once per
    interval.
- **`materialized` rows are evicted with the artifacts they describe**, and by
  the same least-recently-used rule. A missing row only means 05 rebuilds that
  kind lazily.
- **`last_used` is coarse and batched.** It is updated at most once per day per
  row, collected in memory and written in one transaction per batch. A warm
  read must not become a write, and a cold sweep must not be one commit per
  row. Every commit also re-uploads the file through the sync client.
- **A device file nobody opens** is removed once its owner's **lease** is older
  than a generous retention period. A database's own mtime cannot be that
  signal: it records writes, and rules above keep a warm read from writing, so
  a fully warm cache in daily use would look abandoned. The lease is a small
  file beside the database, `<device>.alive`, touched by the owning process
  when it opens the cache and at most once a day while it stays open. A file
  with no lease is judged by its own mtime. The removal covers another
  device's file after that device stopped syncing, and a file left behind by a
  data-dir move or a schema change. Any device may remove it, because it is
  only a cache: an owner that was alive after all starts cold. This device's
  own file is never removed this way.

The cap, the low-water mark and the retention period are constants. The plan
justifies them structurally and tunes them later.

## 11. Schema changes

The schema is in the file name (section 3), so a build never opens a file of
another schema. Old-schema files age out under section 10. A kind whose
derivation changes needs no migration, because `BUILD` has already moved its
keys (section 6).

## 12. Failure semantics

The cache can never be the reason a read fails.

- **Corrupt database.** The plan checks `sqlite_errorcode`. Only on
  `SQLITE_CORRUPT` or `SQLITE_NOTADB` is the file renamed aside and replaced,
  under a proclock, so that two backends on one machine never both act on it.
  The aside copy is deleted on the next successful open.
  - `sqlite3.OperationalError("database is locked")` is also a
    `DatabaseError`. Renaming a merely locked file would delete a live
    database and its hot journal out from under another backend, which is
    SQLite's documented corruption path.
- **Busy, locked, read-only or missing database.** This is a miss, never a
  wait.
  - The busy timeout is a few milliseconds and set explicitly, because Python's
    default is five seconds.
  - In DELETE mode a writer blocks readers, and a reader must compute rather
    than queue behind a sweep.
  - Two backends on one machine is a case `proclock` already plans for, and
    SQLite's own locking covers them.
- **Writes.**
  - Writes are batched per request: one transaction, outside any campaign lock,
    never on the event loop's thread.
  - `synchronous` may be `OFF` or `NORMAL`. A crash may lose recent rows or
    corrupt the file, and either is a miss or a fresh file.
  - A failed write still returns the value. Nothing the user asked for depends
    on the row landing.
- **A malformed or misleading row.** SQLite pages carry no checksum by default.
  A JSON payload corrupted into different valid JSON would decode cleanly, and
  so would a `sources` row whose hash was damaged. Every row therefore carries
  a checksum over its key and its value (section 4), following `vectors.py`'s
  precedent. A failed checksum or decode is a miss, and the row is replaced.
- **Threading.**
  - Route handlers are `def` and run on threadpool workers, and detached runs
    reach the store from worker threads too. So there is one connection per
    process and root, opened with `check_same_thread=False` and used only
    behind a lock.
  - **A read never waits for that lock.** It tries to take it without
    blocking, and if a write batch or an eviction holds it, the read is a miss
    and computes. Otherwise a large eviction would queue every read route
    behind a Python lock, and the millisecond busy timeout above would never
    be reached. A write batch may wait briefly for the lock, and drops its
    rows if it cannot get it.
  - That connection is closed on idle and when the root changes. It is never
    opened from the event loop's thread.
  - The plan states the same rule for a forked child process (reopen, never
    inherit).
- **Values are shared, so hand out copies.** This is `statcache`'s rule. A
  decoded payload is a fresh object on every read, so the rule holds for
  free. The in-process layer above it still has to keep it.
- **Logging.** A degradation goes through `store/logs.py`, once per failure
  kind per process and never once per read. It carries the failure kind and
  nothing of any payload. The observability rule in `CLAUDE.md`, *one writer*,
  applies unchanged.

**Privacy after a delete.** Derived text outlives the record it came from
until eviction reaches it. That includes flattened text of deleted scenes and
campaigns, in this device's file and in other devices' synced copies, and in
SQLite's free pages. So:

- The connection runs with `secure_delete=ON`.
- **Deleting a world or a campaign purges the cache in place. It never unlinks
  a database.** Two backends on one machine share this device's file, and on
  Windows a file another process holds open cannot be removed. So "delete the
  file" would leave a choice between refusing the delete and keeping the
  private text. The purge works through SQLite's own locking instead:
  1. The delete route writes a **purge marker**, `.cache/compiled/purge`
     (through `store.atomic`), holding a new generation token.
  2. In the same request, it purges this device's file: every row of every
     table deleted in one transaction, under `secure_delete`, then
     `incremental_vacuum`. It records the token in the file's own metadata.
  3. Every process checks the marker each time it opens the cache or starts a
     batch, which costs one stat. If a token differs from the one its file
     records, it purges the same way before it reads or writes. That covers
     another backend on this machine, at its next batch, and another device,
     whose marker arrives by sync, at its next open.

  A purge that cannot get the write lock right away is retried at the next
  batch. The marker makes the purge happen eventually, and it never makes a
  delete wait or fail. Before a process purges, the deleted record's rows are
  already unreachable, because no live path hashes to them. What the purge
  removes is data at rest. This is a whole-cache purge at the delete route,
  not a hook on record writes, and every device then starts cold.

  **03-C8: the purge is one callable**, `compiled.purge_for_delete(root)`. It
  writes the marker and purges this device's file. The world and campaign
  delete routes call it. So does 05's sync when it finds that a world or
  campaign root has gone because someone deleted it outside Grimoire. It is
  idempotent, never raises into its caller, and logs one line on failure.
- **Vectors are a stated residual.** `.cache/embeddings/*.vec` files are keyed
  by space and text, never by campaign (section 4). So a campaign's vectors
  cannot be told apart from another's, and they cannot be purged selectively.
  Deleting a world or campaign leaves them until the user clears the folder.
  08 says the same, and the checklist records it as a cross-spec decision.
- The Settings text on sharing boundaries says this, and says the same is
  already true of `.cache/embeddings/` and the thumbnails.

## 13. Where it sits

The two layers are **composed at each call site**. `statcache` stays a leaf
module. Making it call the new module would create the cycle
statcache → compiled → paths → statcache, which `test_import_guard.py`
rejects.

```
call site
  ├─ statcache.memo / memo_stamped      in-process, stat-keyed     (unchanged)
  │    └─ compiled.derive(kind, …)      persistent, content-keyed  (new)
  │         └─ compute(bytes, path parts, params)
```

A warm read in the same process is answered by statcache, and the SQLite file
is never touched. The compiled layer is reached on a statcache miss: after a
restart, after eviction from the in-process FIFO, or for the same bytes at a
new path.

- **Android.** `sqlite3` is in the standard library, so the base dependencies
  in `pyproject.toml` do not change. The plan's first task confirms, through
  `make check-apk` or on a device, that the Chaquopy build ships the module
  and that the `BUILD` stamp (section 6) is present. Until then, read paths
  must not depend on the cache, and a missing module degrades to "cache off".
- **Guards.**
  - The new module lives in `store/` and follows `test_import_guard.py`.
  - It takes no `cid`, so it gets **no** entry in `store/locks.py`.
    `test_lock_domain_guard.py` fails an `OUTSIDE_DOMAIN` entry for a module
    that has no `cid`-taking mutator (a "phantom"). Image GC is the precedent.
  - Deleting a world or a campaign purges the cache (section 12) from the route,
    outside any lock that matters to it.
  - SQLite writes go through neither `write_text` nor `open(..., "w")`, so
    `test_atomic_guard.py` does not see them. That is correct: the file is
    derived, and `store.atomic`'s promise is about records.
- **Kill switch.** `GRIMOIRE_COMPILED_CACHE=0` bypasses the layer. It exists to
  diagnose a suspected stale read, to give section 5's Windows and FAT
  residuals a remedy short of deleting files, and to run the plan's
  cached-versus-uncached equivalence tests. It is not a user setting.

## 14. Acceptance

**Correctness.** Each item is a test, run on `tmp_path` stores with the
codebase's placeholder names.

- **Equivalence.** Every wired kind gives identical outputs over the same store
  in four states: cache on and empty, cache on and warm, cache off, and
  after a simulated restart (database reopened, statcache cleared). The
  fixtures include CRLF files, non-UTF-8 bytes, and identical bytes at two
  stems.
- **Change detection.** Each of these changes gives the new answer on the next
  read, both in-process and after a restart: an edit, a rename, a delete, an
  add, a rename-replace, and a same-size rewrite that restores the mtime
  (caught by ctime on POSIX).
- **Racy window.** Nothing is recorded whose mtime or ctime is younger than
  `PERSIST_WINDOW` at `t0`. This is tested with an injectable clock, on the
  `migrations._clock` precedent, because POSIX ctime cannot be backdated. The
  tests also cover a stamp that ages out *during* a slow read, and a
  filesystem clock skewed against the local clock.
- **Token counts.** A heuristic token count is never persisted.
- **Build fingerprint.** A changed `BUILD` misses every row.
- **Decision kinds.** No decision kind reaches persisted `sources`
  (section 5, rule 5). This is enforced by the guard.
- **Degradation.** Each of these degrades to a computed answer and one log
  line: a truncated file, a garbage file, a locked database, a corrupted
  payload, and a damaged `sources` row. A locked database is never renamed.
- **Deletes.** After a world or campaign is deleted, this device's file holds no
  rows. A second open connection, standing in for another backend, purges
  at its next batch. No file under `.cache/compiled/` is unlinked to do this.
- **Links.** A symlinked `.cache` or `.cache/compiled` turns the cache off, and
  no purge or retention sweep acts through it.
- **Size.** Evicting past the cap shrinks the physical file. A warm cache whose
  lease is fresh is never removed by another device.
- **Contention.** A read made while a write batch holds the lock is a miss, not
  a wait.
- **Keys.** A changed kind `version` misses that kind's rows. Writing
  `__pycache__` into the package does not change `BUILD`.
- **Roadmap contract (section 2a).**
  - A composite key over a collection digest and an input that is not a file
    misses when either input changes, and hits when an unrelated file changes.
  - An artifact stored straight after a write is hit by the next read, and no
    `sources` row is recorded inside the window.
  - Every stored artifact leaves a `materialized` row for each path it read.
    Dropping that table changes no read's answer.
  - A query through an index returns nothing outside the caller's live key
    set.
- **Frozen campaign.** The read-only sweep of the frozen campaign
  (`snapshot.json`) is byte-identical with the cache cold, warm and off. The
  sweep already runs on a copy of `home/`, so the cache never writes into the
  fixture.

**Performance.** Measure on a generated synthetic library, large enough to show
the difference. The generator is committed. No figure from any real store is
committed.

**03-C9: the synthetic-library generator is owned here.**

- It lives with the cache's tests and is built by 03's plan.
- It writes a marked store, holding placeholder names only (Realm, Saltmarch,
  Seraphine, Mara, Winifred), with sizes set by flags.
- It refuses to write into a directory that already holds a store that is not
  marked synthetic.
- 04-C3b extends it with the page shapes 04's benchmark needs. It does not fork
  a second generator. Measure four cases:

- the cold first-ever load;
- a warm load in the same process, which must not regress against today;
- a warm load after a restart, which is the case this spec exists for;
- a load after one file changes.

Measure them on these pages: the world shelf, the campaign shelf, Todo, search,
and a campaign hub. Every kind the plan wires must show its warm-after-restart
win in this measurement, or it is not wired.

## 15. Review record

`CLAUDE.md` requires `/codex:adversarial-review` against this spec before
`superpowers:writing-plans`. Codex was not available in the environment where
this spec was reconciled. With the user's approval, an adversarial review by a
substitute reviewer was run instead. It verified the spec's claims against
the code. Its findings are folded in above:

- **Blocking findings:**
  - the racy check is measured at `t0` against the filesystem's clock, with a
    wider persistence window;
  - path-derived inputs go into `params`;
  - sync and integrity hashes are excluded from persisted sources, since they
    are not raw-byte hashes and they decide sync outcomes;
  - heuristic token counts are not persisted;
  - `BUILD` covers templates, package data, the Python version and the
    dependency versions, and the APK uses a build stamp.
- **Should-fix findings:**
  - the schema number is in the file name, and the file is renamed aside only
    on real corruption;
  - row checksums cover keys;
  - eviction measures live pages;
  - DELETE journal mode with explicit millisecond timeouts and batched writes;
  - the threading rule;
  - `secure_delete` and dropping the cache on delete;
  - memoizing the device key and isolating `lock_dir` in tests;
  - no `OUTSIDE_DOMAIN` entry;
  - composition at the call site to keep the import graph acyclic;
  - the transcript readers named correctly;
  - an honest account of what forks and moves gain.

A Codex review of the PR (`chatgpt-codex-connector`) then added seven fixes:

- a delete purges in place through a synced marker, never by unlinking a
  file another process may hold;
- a linked `.cache` component turns the cache off;
- every eviction ends with an explicit `incremental_vacuum`;
- a read never waits for the in-process lock;
- removal is judged by a device lease, not the database's mtime;
- a kind's `version` is part of its key;
- `BUILD` hashes a source manifest, never bytecode.

**Reconciled against the roadmap (specs 04–09).** Section 2a was added after
reading the specs that consume this one. Several decisions were reversed:

- Todo and shell projections were excluded outright. They are now allowed when
  their key covers every input.
- `materialized` was deferred. It is now restored for 05 and 08.
- Write-through was rejected. It is now split: artifacts may be stored at write
  time, and only the stamp row waits.
- The query-safety guard banned index queries. It now allows indexes that rank
  within a live key set.
- Vectors were not addressed. They are now stated to be independent of
  `BUILD`.

The PR's Codex review is not the CLI's `/codex:adversarial-review`. That
gate should still be run against this spec before the plan, if it can be.

## 16. Non-goals

- Moving any authoritative record into SQLite, or using a row id as a record
  identity.
- Answering membership, existence or counts from the cache, or letting any
  cached value decide a write, a sync outcome or an integrity verdict.
- Requiring the cache to be backed up, synced, migrated or reconciled. There is
  no reconciliation UI, and "delete `.cache/compiled/`" is the whole repair
  story.
- Blocking or slowing edits made outside grimoire. They are noticed by stamp,
  on the next read.
- Hooking `store.atomic` (section 5, rule 4). Write-through is 05's, and it
  calls the shared primitive from the writers. 03's only write-side touch is
  the purge marker that a world or campaign delete writes.
- Choosing 04's page projections, 05's eager-rebuild policy and CLI, or 08's
  SearchDocument shape. Section 2a is what 03 owes them.
- Replacing `vectors.py`, `usage_rollup.py` or the scene-identity record. Any
  of them could later become a kind, but none needs to.

# Content-addressed compiled cache: the files stay the database, SQLite caches what is compiled from them

**Status:** Design. Spec gate (section 15) in progress; the plan is next.
**Date:** 2026-10-09
**Series:** 03. Depends on 01, the inference refactor
(`2026-10-07-inference-backend-refactor-design.md`, landed as slices A through I).
The only part of 01 this spec relies on is the embedding space id
(`ResolvedInference.space_id`, reached through `embed_space.endpoint()`).
**Baseline reconciled against:** `main` at `7f833e7` (#493).
**Supersedes, in part:** the "No stored aggregates or index files" non-goal of
`2026-08-28-read-path-performance-design.md` (section 2 below says how far).

> **Implementation rule:** before you code any slice of this, re-read the code
> it touches and every PR that has landed on that subsystem since `7f833e7`.
> Keep any behaviour that has landed unless this spec explicitly supersedes it.

## 1. Goal

Persist expensive, deterministic read products across requests **and across
process restarts**, in one disposable local SQLite file, while keeping the
rule that matters:

> Markdown and JSON files are the only authoritative datastore. The cache can
> make a read cheaper. It can never decide what a read answers.

The cache gives later search and embedding work one dependency-keyed
substrate. It must not become a second store, an index of membership, or a
place that answers questions the files have not been asked.

## 2. What is already here, and what this adds

This design does not replace an uncached codebase. Before you change anything,
know what has landed:

- **`store/statcache.py`**, the in-process layer. `memo(kind, signature(...))`
  is keyed on `(path, mtime_ns, size, ino)`. `memo_stamped` is for values whose
  inputs are only known after they are computed, and is keyed on
  `stamp()` = `(path, mtime_ns, ctime_ns, size, ino)`. It also provides
  `RACY_WINDOW_NS` (one second), caller-owned pools with their own budgets, and
  the rule that a stamp is taken *before* the read it vouches for. Its callers
  today include `world_row`, `campaign_row`, `scene_head`, `card_summary`,
  `dir_hash`, `pc_dir_hash`, `entity_hash`, `greeting_meta`, `plotmap_hash`,
  `body_tokens`, `image_store.read`, `image_store.blob_intact`,
  `pricing:provider`, `usage:unpriced`, `inference:campaign_meta`, and
  `search.py`'s whole-corpus extraction pool. All of it is lost when the
  process exits.
- **Two precedents for persistent derived records** under `<home>/.cache/`:
  - `store/migrations.py`'s per-campaign scene-identity record
    (`.cache/scene-identities/<cid>.json`). It is a stamp-keyed record that
    outlives the process. It already states the stricter rule that persistence
    needs: **neither mtime nor ctime may be inside the racy window when the
    record is written** (`_backfill_campaign`).
  - `store/vectors.py`'s embedding cache (`.cache/embeddings/*.vec`). It is
    keyed by `sha256(space + "\0" + text)`, so it is already content-addressed
    on the projected text plus the embedding space, which is exactly the key
    this spec's original `embeddings` table proposed. It is little-endian
    float32 with a CRC32 per record, and it reads any bad file as a miss.
- **`store/usage_rollup.py`** is a persistent aggregate with a `VERSION`. A
  rollup written by an older build is never read.
- **`.cache/` is already derived territory.** `backups.py` excludes it
  unconditionally (`_DERIVED`). `external.SKIP_AT_ROOT` skips it. Image GC
  refuses to run through a `.cache` that is a link. **It travels with a synced
  store**, which is why image GC keys its sightings per device
  (`maintenance_reports.device_key(root)`).
- **Not landed:** section 3 of the read-path spec, the library epoch and
  `ETag`/`304` on JSON reads. The only `ETag`s in the app today are on images
  and static files. Nothing here depends on that section, and nothing here
  blocks it.

The read-path spec deliberately rejected stored aggregates: *"a second store
to keep consistent"*. This spec revisits that decision, with one condition
that answers the objection: **nothing in the cache is ever consulted except
through a key computed from the current bytes on disk.** So a stale row cannot
be found. It can only be unreachable. "Keep it consistent" reduces to one
trust point, the stamp-to-hash map in section 5, and that rests on the same
stamp rules the in-process layer already relies on.

The new capability is narrow. **Warm-after-restart** reads, and reads of the
same bytes at a different path (a fork, a campaign's copy of a world record, a
data-dir move), cost a stat and a lookup rather than a recompute. The
same-process warm path is already cheap, and this spec must not make it
slower.

## 3. Location: in the store, one file per device

`<home>/.cache/compiled/<device-key>.sqlite`, where `<device-key>` is the
first 32 hex characters of `maintenance_reports.device_key(home())`.

This differs from the original draft (`<home>/.cache/compiled.sqlite`) for two
reasons. Both are repo facts:

- **The store may be a synced folder, and `.cache/` syncs with it.** If two
  devices opened one SQLite file through a sync client, each would replace the
  other's open database. A per-device file is never opened by another device,
  so the sync client only ever copies a file that one writer owns. The cost is
  bandwidth for the sync client, not corruption. A user who wants to avoid that
  bandwidth can exclude `.cache/compiled/` from their sync. The settings page
  that already explains sharing boundaries should say so.
- **It stays inside the root the user chose.** The cache holds derived private
  content: parsed frontmatter, flattened transcript text for search, and
  later, search documents. A user who put their library on an encrypted or
  removable volume chose where that content lives. Machine-local state outside
  the root (the proclock directory) is acceptable for lock files and a device
  id, which carry no content. It is not acceptable for this. Vectors and
  thumbnails follow the same rule.

The device key includes the root path, so a data-dir move or a library copied
on the same machine opens a fresh file. An old file that nobody opens is
removed by section 10. Tests are isolated for free, because every test points
`GRIMOIRE_HOME` at `tmp_path`. The plan must confirm that the device id's own
file (`proclock.lock_dir()`) is isolated under test too, or isolate it.

The database:

- is excluded from backups, bundle exports and the external scan, because
  `.cache/` already is;
- may be deleted at any time, by the user or by the app;
- is rebuilt lazily, by reads, never by a startup sweep;
- carries no migration promise. A schema it does not recognise is renamed
  aside and replaced (section 11).

It is reached only through a resolver in `store/paths.py` (or one built from
`home()`), so `test_paths_guard.py` holds without a marker. **No handle is held
while the app is idle.** Connections are short-lived or closed after a period
of disuse, and the journal mode chosen must leave no sidecar file (`-wal`,
`-shm`) after the last connection closes. A sync client or a Files-On-Demand
provider should not find a held handle in the store. `proclock`'s docstring
explains why that matters on Windows. On `PUT /config/data-dir`, every handle
to the old root closes before the new root is used.

## 4. Logical schema

The exact SQL belongs to the plan. These are the logical tables.

### `sources`: path to content hash, advisory

| field | meaning |
|---|---|
| `path` | relative to the store root, `/`-separated |
| `mtime_ns`, `ctime_ns`, `size`, `ino` | `statcache.stamp()`'s fields, taken **before** the bytes were read |
| `content_hash` | SHA-256 of the exact bytes read |
| `last_used` | coarse, updated at most once per day per row (section 10) |

There is no `kind` and no `ref`. Both were optional in the draft, and both
would invite a query that starts from this table instead of from the
filesystem (section 9). The filesystem decides existence and membership. This
table only answers "what were these bytes, the last time the file stamped like
this".

### `artifacts`: content-addressed derived products

Key: `(kind, key_digest)`. `key_digest` is section 6's composite.
Payload: an encoded value plus a checksum of the payload (section 12), and
`last_used`.

The first kinds are listed in section 8. A kind is a name in a registry in the
cache module, not a free string at the call site. That way one place lists
every kind that persists, along with what each one's key covers.

### No `embeddings` table, and no `materialization` table, in this spec

- **Embeddings stay in `vectors.py`.** Its key, `sha256(space\0text)`, is
  already the projection hash plus the embedding space. Its format is already
  justified by measured read-path cost. After 01, its `space` comes from the
  Embedding role's resolution, `embed_space.endpoint()["space"]`, and that does
  not change here. Moving vectors into SQLite would buy nothing until semantic
  search needs to score across this cache's search documents. That is a
  later spec, which should reuse the vector key exactly. What this cache can
  offer `vectors.py` today is section 10's eviction, which can be extended to
  `.cache/embeddings/` once there is a reason to bound it. Its docstring
  already admits the leak.
- **No `materialization`, and no eager rebuild after an edit.** Eagerly
  recomputing "hot" artifacts after a direct edit is speculative work on the
  write path. The first read after an edit pays one recompute, and that is
  already how every statcache derivation behaves. Revisit this only once the
  plan's measurements show a hot artifact whose first-read cost a user would
  notice.

## 5. The trust point: when a persisted stamp may vouch for a hash

`sources` is the only place where a stale answer could hide, so its rules are
the in-process rules, made stricter for persistence. This follows
`migrations._backfill_campaign`'s reasoning:

1. **Stamp before read.** Take `statcache.stamp(path)`, then read the bytes,
   then hash them. A write that lands between the two leaves a recorded stamp
   that no longer matches, and the next read re-hashes.
2. **Never record inside the racy window.** If the stamp's mtime **or ctime**
   is younger than `RACY_WINDOW_NS` at the moment of recording, the hash is
   used for this read and is not stored. ctime is included because this record
   outlives the process. A same-size rewrite in place that restores the old
   mtime within one ctime tick would otherwise be vouched for permanently.
3. **Match all four fields.** A hit needs mtime, ctime, size and inode to be
   equal. ctime catches an in-place rewrite that was handed its mtime back,
   which a sync client or `touch -r` can do. The inode catches a
   rename-replace. On Windows, `st_ctime` is the creation time, so the residual
   there is the one `statcache.stamp` and `migrations` already accept and
   document.
4. **Grimoire's own writes do not record.** The draft asked `store.atomic` to
   update the source mapping straight after a write. It cannot do that
   safely: a file just written is, by definition, inside the racy window, so
   rule 2 forbids recording it. Recording it anyway would break the protection
   the draft itself asked to keep. A file this app wrote is hashed once, by the
   first read after the window passes, which is what the read path already
   pays. This also keeps `store.atomic` free of any SQLite dependency on the
   write path.
5. **Integrity checks never consult it.** `image_store.blob_intact` exists to
   notice bytes that changed **without** their stamp changing. Within a
   process it already trusts statcache. Across restarts it re-reads, and that
   re-read is the check. Persisting its answer would turn a per-launch
   integrity check into a never-again one. The same exclusion applies to any
   future "is this file what it claims" derivation.

## 6. Keys

**Source hash:** SHA-256 of the exact bytes. Nothing is normalised first: no
line endings, no BOM, no decoding.

**Composite artifact key:**

```
key_digest = SHA-256( kind
                    ‖ BUILD
                    ‖ params        -- canonical JSON of every non-file input
                    ‖ inputs )      -- ordered (role, content_hash) pairs
```

- **`BUILD` is a fingerprint of the running code.** This is the one risk the
  in-process layer never had. A restart used to clear every memo, so changing
  a parser could not leave yesterday's parse in force. A persisted artifact
  would, unless its key changes with the code. A per-kind version number that a
  developer has to remember to bump is how that gets missed. So every key
  includes a build fingerprint, computed once per process from the installed
  `grimoire` package's own sources, which works the same from a checkout,
  a wheel or the APK. An upgrade or a local edit makes the cache cold, and
  that is correct. A per-kind `version` stays in the registry to document
  intent and to let a test force a miss. It is not the safety mechanism. The
  plan measures the fingerprint's start-up cost and may replace it with an
  equivalent, such as a build-time stamp the APK and the wheel both carry,
  as long as any source change still changes it.
- **`params` carries every input that is not a file**, such as a tokenizer
  identity or an embedding space id. A derivation whose output depends on
  config, on the time, or on another record's live state either names that
  input in `params` or does not belong in the cache. A key that leaves out a
  real input is the persistent version of `tokens.body_tokens`' warning,
  "poisons that signature for every later reader", and it would last forever
  instead of for one process.
- **The compute receives what the key was computed from.** The cache API reads
  the input bytes and hands them to the compute. It does not hand the compute a
  path to read for itself. Then "the key covers the input" is true by
  construction. `body_tokens` takes its text from the caller, which is a
  different shape. The persistent version keys on the hash of the text it was
  given, not on the path.
- **Order is deterministic.** Inputs are ordered by their role in the
  derivation, not by discovery order. A collection's members are ordered by
  their relative path.

## 7. Collections

Directory membership is authoritative, and it is always read from the
directory:

1. list the directory;
2. get each member's content hash (section 5: a stat, plus a read only for a
   member whose stamp moved);
3. sort by relative path;
4. hash the ordered `(relpath, content_hash)` list.

So an add, a delete, a rename or an edit each change the digest, and the cache
never answers membership. A collection-keyed artifact saves the per-member
*compute* and the per-member *read*. It does not save the listing.

The draft hoped for counts that "currently require directory sweeps" to come
from collection digests. They cannot: the digest needs the listing, and the
listing is the count. Making counts cheaper would mean trusting a directory's
stamp to vouch for its listing. `statcache.stamp` documents that a directory
stamp can do this, and `migrations` relies on it for the identity marks. But
a persisted listing is a membership answer, and section 1 forbids that. **The
world shelf's counts stay live reads.** `list_world_rows` already exists for
callers that do not need counts.

## 8. What to cache first

The draft listed targets by page. This list keeps those pages but orders the
targets by one structural test, because the cache only helps when **(compute
+ read) is much larger than (stat + one indexed lookup + decode)**. The plan
has to show that inequality on a synthetic library before it wires up a kind.
It must not use the user's library for this (`CLAUDE.md`'s privacy rule). The
plan tunes the thresholds later against real prompts, in conversation, and
commits no figures.

**Likely to pass:**

- **`search.py`'s per-file extraction.** It parses frontmatter, selects card
  fields and flattens whitespace for every file on every query, including whole
  scene transcripts. After a restart, that is the whole corpus. This is also
  the shape a later search-document projection will take.
- **Token measures** (`tokens.body_tokens` and its siblings). Tokenising is
  CPU-bound and scales with body length. Key them on text hash + encoder
  identity in `params`.
- **Card and PC directory hashes** (`characters.dir_hash`, `pcs.pc_dir_hash`)
  and **`entity_hash`**. These are the hashes that sync sweeps compute. With
  `sources` persisted, each becomes a stat per member after a restart rather
  than a read of every byte. These kinds may need no `artifacts` row at all:
  `sources` alone carries them.
- **Parsed scene transcripts** for the readers that parse whole files:
  chronicle, involvement and timeline projections. A transcript is the
  largest file a campaign holds, and a played campaign only grows.

**Wire last, and only if measured:**

- **World and campaign list rows** (`world_row`, `campaign_row`). The draft
  put these first. Each row parses one small frontmatter, which is close to
  the cost of the lookup that would replace it. The shelf pages are slow
  because of their counts (section 7) and what they join, not because of this
  parse. This kind is still a good *first consumer* for proving the wiring end
  to end, because its outputs are easy to assert byte for byte. It should not
  be how the performance claim is made.
- **`scene_head`.** It is already a head-only read.

**Never cached:**

- **The Todo list as a list.** `routes/todo.py` states the contract: *"nothing
  here is a cache that can outlive the read it was computed for."* A
  content-keyed component is consistent with that contract, because its key is
  recomputed from the current bytes on every request, so it cannot answer for
  bytes that have moved. A cached chore list, chore count or chore existence
  is not. Todo may consume the kinds above. It gains no kind of its own.
- **Integrity checks** (section 5, rule 5).
- **Anything that reads `config.md`, the routing, or the clock** without naming
  that input in `params`.
- **The `/api/shell` money figures.** `usage_rollup` already owns them, with
  its own bookmark semantics.

## 9. Query safety

No read starts from the cache. Every read starts from live, filesystem-derived
paths, computes their current hashes, and only then looks up artifacts for
those keys. Nothing enumerates `artifacts` or `sources` to find out what
exists. So a stale row is invisible before any eviction runs. That is what
makes eviction a storage concern rather than a correctness concern.

The plan should hold this with a guard in the house style. One module owns
`sqlite3` and the cache's tables, and the guard fails any read API that takes
no key, so that "list what is cached" cannot be expressed. `test_*_guard.py`
modules that parse the package's ASTs are the precedent.

## 10. Bounding the file: eviction, not reachability

The draft proposed mark-and-sweep: mark every live source and reachable
projection, then sweep the rest. Marking needs a walk of the whole store, and
correctness never depends on the sweep. So a cheaper rule is enough:

- **LRU by `last_used`, under a size cap.** When the file passes its cap
  (opportunistically, on a write, at most once per interval), delete the
  least-recently-used rows until it is under a low-water mark. Evicting a live
  row costs one recompute. Keeping a dead row costs only space.
- **`last_used` is coarse.** It is updated at most once per day per row, so
  that a warm read does not become a write.
- **A device file nobody opens** (another device's file after it stopped
  syncing, or a file left behind by a data-dir move) is removed once its mtime
  is older than a generous retention period. Only a device that can open the
  file, and finds it is not its own, removes it. The cap and the retention
  period are constants. The plan justifies them structurally and tunes them
  later.

## 11. Schema changes

The database carries a schema number. A file whose schema this build does not
recognise, or that cannot be opened, is renamed aside and replaced with a new
one. The aside copy is deleted on the next successful open. A kind whose
derivation changes needs no migration: `BUILD` has already moved its keys
(section 6).

## 12. Failure semantics

The cache can never be the reason a read fails.

- **Missing, corrupt, locked or read-only database:** the read computes from
  the files, exactly as it does today. A `DatabaseError` at open renames the
  file aside and starts fresh (section 11). A busy database is a miss, never a
  wait: a short busy timeout, then compute. Two backends on one machine is
  something `proclock` already plans for, and SQLite's own locking covers
  them.
- **A write fails:** the value is returned anyway. Nothing the user asked for
  depends on the row landing.
- **A malformed payload:** each artifact row carries a checksum of its payload,
  on `vectors.py`'s precedent. SQLite pages carry no checksum by default, and
  a JSON payload corrupted into different valid JSON would decode cleanly. A
  checksum or decode failure is a miss, and the row is replaced.
- **Values are shared, so hand out copies.** This is `statcache`'s rule. A
  decoded payload is a fresh object per read, so this holds for free. The
  in-process layer above it still needs the rule.
- **Logging:** a degradation goes through `store/logs.py`, once per failure
  kind per process, never per read. It carries the failure kind and nothing
  of the payload or the path's contents. The rule in `CLAUDE.md`'s
  observability section, *one writer*, applies unchanged.

## 13. Where it sits

The new cache is a second layer **under** `statcache`, never instead of it:

```
statcache.memo / memo_stamped      in-process, stat-keyed      (unchanged)
  └─ compiled cache                persistent, content-keyed   (new)
       └─ compute from the bytes
```

On a same-process warm read, statcache still answers first and the SQLite
file is never touched. The compiled layer is reached on a statcache miss,
which happens after a restart, after an eviction from the in-process FIFO, or
for the same bytes at a new path.

- **Android.** `sqlite3` is in the standard library, so `pyproject.toml`'s
  base dependencies do not change. The plan's first task confirms that the
  Chaquopy build ships the module, through `make check-apk` or on a device,
  before any read path depends on it. The cache must also degrade cleanly if
  the module is not there.
- **Import, lock-domain and atomic guards.** The new module lives in
  `store/` and follows `test_import_guard.py`. It takes no `cid`, so it
  belongs in `locks.OUTSIDE_DOMAIN` with that reason rather than in
  `UNREVIEWED`. SQLite writes are not record writes. They go through neither
  `write_text` nor `open(..., "w")`, so `test_atomic_guard.py` does not see
  them, and that is correct: this file is derived, and `store.atomic`'s
  promise is about records.
- **Kill switch.** `GRIMOIRE_COMPILED_CACHE=0` bypasses the layer. It exists
  for diagnosing a suspected stale read and for the plan's
  cached-versus-uncached equivalence tests. It is not a user setting.

## 14. Acceptance

**Correctness.** Each of these is a test, run on `tmp_path` stores with the
codebase's placeholder names:

- With the cache on, an empty cache, a warm cache, and the cache off, every
  wired kind produces identical outputs over the same store.
- An edit, a rename, a delete, an add, a same-size rewrite that restores the
  mtime (caught by ctime on POSIX), and a rename-replace each produce the new
  answer on the next read, in-process and after simulating a restart (reopen
  the database, clear statcache).
- Nothing is recorded inside the racy window for either clock.
- A truncated file, a garbage file, a file with an unknown schema, a locked
  database and a corrupted payload each degrade to a computed answer and one
  log line.
- A changed `BUILD` fingerprint misses every row.
- The frozen campaign's read-only sweep (`snapshot.json`) is byte-identical
  with the cache cold, warm, and off. The sweep already runs on a copy of
  `home/`, so the cache never writes into the fixture.

**Performance.** Measure on a generated synthetic library large enough to make
the difference visible, with the generator committed and no figures from any
real store:

- cold, first-ever load;
- warm, same process. This must not regress against today;
- warm after restart, which is the case this spec exists for;
- after one file changes.

Run the named pages: the world shelf, the campaign shelf, Todo, search, and a
campaign hub. Every kind the plan wires must show its warm-after-restart win
in this measurement, or it is not wired.

## 15. Review record

`CLAUDE.md` requires `/codex:adversarial-review` against this spec before
`superpowers:writing-plans`. Codex was not available in the environment where
this spec was reconciled. With the user's approval, an adversarial review by a
substitute reviewer is being run instead, and its findings will be folded in
above. Run Codex against this spec before the plan, if it can be.

## 16. Non-goals

- Moving any authoritative record into SQLite, or using a row id as a record
  identity.
- Answering membership, existence or counts from the cache.
- Requiring the cache to be backed up, synced, migrated or reconciled. There
  is no reconciliation UI. "Delete `.cache/compiled/`" is the whole repair
  story.
- Blocking or slowing edits made outside grimoire. They are noticed by stamp,
  on the next read.
- Hooking `store.atomic` or any write path (section 5, rule 4).
- Replacing `vectors.py`, `usage_rollup.py` or the scene-identity record. Each
  could later become a kind, but none needs to.

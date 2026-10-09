# 05. Direct-edit cache sync

**Status:** Draft — spec gate (`/codex:adversarial-review`) pending.
**Date:** 2026-10-09
**Roadmap:** 05 in `ROADMAP-CHECKLIST.md`. Lane: cache (03 -> 04 -> 05 -> 06, and 05 + 07 + 01h -> 08).
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** the bundle draft `05-direct-edit-cache-sync.md` (2026-10-06,
written against `7f80c42`), against current code and against the reconciled 03
(`2026-10-09-roadmap-03-content-addressed-compiled-cache-design.md`, section
2a in particular). It supersedes the draft wherever the two disagree, and
section 1.5 lists where they do.

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning. In particular, re-read 03's plan and its
> registry API, and 04's spec for the exact shape of 04-C2, before writing a
> line of this one: both were unwritten or unlanded when this was drafted.

## Depends on

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| 03-C2 | 03 | Liveness by construction. It is the reason sync is a warm-up and never a correctness step (section 3). | Hard |
| 03-C3 | 03 | The `materialized` record: which kinds were built from a path. It is the whole of "what was hot" (section 6). Section 6.4 asks for one refinement (an `instance` column and a projection-qualified vector kind). | Hard (refinement soft) |
| 03-C4 | 03 | The one validate-and-hash primitive. Sync calls it for every path; it never hashes or stamps on its own (section 4). | Hard |
| 03-C5 | 03 | Artifacts may be stored at once, inside the racy window, and only the `sources` row waits. Sync runs seconds after an agent's edit and depends on this (section 4.4). | Hard |
| 03-C1 | 03 | Composite keys, so a warmer can rebuild a collection-keyed kind (a 04 card) for the instance a path feeds. | Soft (only composite warmers need it) |
| 03 section 12 | 03 | The purge on a world or campaign delete. Sync performs it when it finds a deleted world or campaign root (section 7.6). Not a numbered contract today: see Open question 2. | Soft (missing edge) |
| 04-C2 | 04 | Synchronous retirement of a first-paint projection. 04's stale-while-revalidate payload is the one place a superseded version can be served by design, so retiring it is the one correctness-relevant thing sync does. Sync needs it callable from another process (section 3.2). | Hard for the CLI and API; not used by write-through |
| 01h-C5 | 01h | Embedding attribution across campaigns in one batch. Without it, sync groups texts by attribution and makes one `embed_sync` call per group (section 6.6). | Soft (the grouping fallback is complete, only less batched) |
| 01h-C3 | 01h | Embedding options in the space identity. The vector kind names the space by digest, so it inherits whatever 01h-C3 puts in the space id. | Soft |
| 01h-C1 | 01h | Input type. Sync only ever embeds documents, so once 01h-C1 lands it passes the document type. | Soft |
| 01 (landed) | 01 | `inference.embed.embed_sync`, `routing.EMBED_TASKS`, `embed_space.endpoint()` and the metered embed door. | Hard (landed) |

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 05-C1 | 08 | The write-through warm-up of a hot scene SearchDocument and its vector after a scene goes quiet (08-C2's "hot rebuild via 05"). |
| 05-C2 | 06 | The command the store-editing skill tells an agent to run after a batch of direct edits. |
| 05-C2 | 08 | `--campaign` / `--world` as the explicit way to bring a scope's hot SearchDocuments current after a bulk change. |
| 05-C3 | 08 | The rebuild policy for 08's vectors: re-embed only what was embedded, only in the current space, only text the vector cache does not hold. |
| 05-C4 (new) | 04 (optional), 05-C1 | The write set: which record paths a request or a run wrote. 04 may use it for its own synchronous retirement (04-C2) if its plan wants paths rather than scopes. It can land before everything else here. |

## 1. Current state (reconciled against main)

### 1.1 There is no compiled cache yet, and no record of what was built

03 is a spec (its gate is a recorded substitute plus a PR review) and nothing
of it has landed. What exists today:

- `store/statcache.py` is in-process only. It is keyed on stat signatures
  (`signature`, `statcache.py:30`; `stamp`, `statcache.py:107`) with a
  one-second racy window (`RACY_WINDOW_NS`, `statcache.py:26`). It notices an
  external edit on the next read and is lost at exit.
- `store/vectors.py` caches embeddings on disk under `.cache/embeddings/`,
  keyed by `sha256(space + "\0" + text)` (`_path`, `vectors.py:115`; `load`
  `:150`, `save` `:181`). Its docstring already states the property 03 builds
  on: "Editing an entry's body simply maps it to a new key and the old vector
  goes unreferenced."
- Three producers write vectors today, each from its own read path:
  - lore recall (`store/context/semantic.py`, `vectors.save` at `:278`), task
    `semantic-recall`, embedding `entry_text` (`:203`): name, keys and body of
    a world-info entry;
  - the art catalog (`store/context/art.py`, `vectors.save` at `:586`), task
    `art-catalog`;
  - library semantic search (`store/semsearch.py`, `vectors.save` at `:338`),
    task `semantic-search`, embedding post-aligned passages of every document
    `search.walk` yields, transcripts included.

  A fourth embed task, `continuity-similarity`, embeds continuity rows inside
  absorb and the reconcile sweep (`routing.py:174` lists all four).
- None of the three producers records which file a vector came from. Nothing
  can answer "which projections of this file were embedded", which is the
  question 05-C3 turns on.

### 1.2 Where Grimoire writes, and why no one function sees it

- `store/atomic.py` is the record-write door: "Every Markdown/JSON record in the
  store goes through here" (`atomic.py:3`). Its publishing functions are
  `write_text` (`:216`), `write_bytes` (`:226`), `append_line` (`:231`) and
  `streaming_write` (`:276`). `test_atomic_guard.py` holds the whole package to
  it. Today they are called from over two hundred sites in about a hundred
  modules.
- Deletes and renames do not go through `atomic`. They are `unlink`,
  `rmtree`, `os.replace` and `rename` calls spread over the store and the
  routes, over a hundred of them.
- The activity middleware is the precedent for coverage that does not depend
  on anyone remembering. `_CampaignActivityStamp` (`main.py:416`) stamps the
  recents order and the write token (#409) for every successful mutating
  request under `/api/campaigns/`, because "Enumerating the mutators does not
  converge. Six review rounds each found another one ... the store deliberately
  takes *roots* rather than cids at its leaves, so there is no one function they
  all pass through." It also bumps the token on an unhandled exception, for the
  same reason. It is raw ASGI on purpose, so that it stays out of the exception
  path.
- Detached runs write from the lifespan's task group, not from the request.
  Every one of them starts through `runner.start` (`runner.py:200`) or
  `runner.start_thread` (`runner.py:220`), and runs inside `runner._guarded`
  (`:355`) or `runner._guarded_thread` (`:251`). A request-scoped boundary does
  not see their writes. A run-scoped one does.

### 1.3 The command-line surface

- The package has **no console script** (`backend/pyproject.toml` declares no
  `[project.scripts]`).
- The one in-package command is `python -m grimoire.where`
  (`backend/src/grimoire/where.py`), which the installers run. It resolves the
  root through `store.paths` rather than assuming `~/.grimoire`, prints ASCII
  only, and reconfigures stdout with `errors="backslashreplace"` so that an
  accented home directory cannot fail it (`where.py`, `main`).
- The other entry points are scripts that put `backend/src` on the path:
  `backend/scripts/create_world.py`, `ingest_scene.py` and
  `redownload_chub.py`, and the skills' own scripts
  (`.claude/skills/*/scripts/`). Each writes the store through `grimoire.store`
  functions.
- `scripts/grimoire_sync.py` is the adb sync between a PC and a phone. It
  treats files as blobs, never imports `grimoire`, and skips `.cache` at the
  root (its own `SKIP_AT_ROOT`).

### 1.4 What happens to an external edit today

- **Reads notice it.** `docs/store-guarantees.md` ("Reads notice external
  writes") states the contract: every request re-reads, and statcache is keyed
  by stat, so a changed file is seen by the next read.
- **Vectors self-heal lazily.** An edited lore entry has new `entry_text`, so
  it misses the vector cache. Recall embeds it the next time it is a
  candidate, at most `WARM_LIMIT = embeddings.BATCH - 1` new entries per turn
  (`semantic.py`, the `WARM_LIMIT` comment). The spend happens on the turn
  path, inside the turn's latency.
- **The write token does not move.** `store/revision.py:110`: "A store written
  by something other than this app (a hand edit, a sync client landing a file,
  ...) moves nothing here."
- **The existing skills write through `grimoire.store`, not the filesystem.**
  `world-card-integration` says so in a section titled "Work through the
  store, not the filesystem", and `create-world` and `ingest-campaign-log`
  drive scripts that do the same. An agent editing the store today is
  therefore usually a Python process calling store functions with no server
  involved, not an editor writing bytes.

### 1.5 Where the draft and current code disagree

| Draft | Current code and the reconciled 03 | Consequence here |
|---|---|---|
| A live source->hash mapping that sync updates (draft steps 4-6). | There is none. 03 section 2a.2: "The filesystem is that mapping." Every read computes its key from current bytes. | Sync updates nothing that a read relies on for correctness. Step 6 ("remove the old hash from live mappings") holds already. |
| "After sync completes, no live query may return content derived from the superseded source version" as sync's core invariant. | True at every read by 03-C2, with sync or without it. The one exception is 04's stale-while-revalidate first paint, which serves a payload before validating it. | Sync's correctness-relevant step is retiring 04's first-paint payloads (04-C2), and nothing else. |
| `store.atomic` (or the writers) update the source mapping right after a write. | 03 section 5 rule 4: a write never records its own `sources` row. 03-C5: artifacts may be stored at once. | Sync stores artifacts and leaves `sources` to 03's rules. It never asks for a row inside the window. |
| "On read, if the stat signature differs, refresh the source hash" (draft section 7) as a backstop to add. | That is 03's read path. | Not part of 05. |
| Image sidecar descriptions edited in record folders. | Descriptions live on the image object (`assets/image-store/objects/<shard>/<id>.json`, `image_store.py:6`), written only through `image_store.update` under the image locks (`docs/store-guarantees.md`, "Shared metadata"). A legacy `descriptions.json` key is read first until migration. | Sync accepts object sidecar paths. The skill (06) tells an agent to write descriptions through the store function, never by hand. |
| Renames: delete plus create, unless identity can be preserved. | Agreed. 03 keys by bytes and path parts, so a renamed file is a new path with no `materialized` rows. | The caller may state a rename (`--renamed OLD=NEW`); sync never infers one (section 7.5). |

## 2. Goal (and what is explicitly not the goal)

**Goal.** After an edit made outside the running app (by an agent, a script or
an editor) and after Grimoire's own writes, the derived representations that
were already in use are rebuilt eagerly, so that the next read that needs them
is warm. Specifically:

1. An explicit, batched `cache sync` over named paths or scopes (05-C2), for
   agents and scripts, with a summary that holds no record content.
2. The same primitive reached by default from Grimoire's own writers, without
   per-call-site wiring and without putting the cache in `store.atomic`
   (05-C1, through the write set 05-C4).
3. A rebuild policy that warms exactly what was hot and embeds only text whose
   predecessor was embedded, in the current space, and only the text the vector
   cache does not already hold (05-C3).
4. 04's first-paint projections retired immediately for an external edit, the
   way 04 already retires them for its own writes.

**Not the goal.**

- Correctness of ordinary reads. 03-C2 already gives that, and a forgotten sync
  costs a cold read, never a wrong one.
- A second invalidation system. Sync calls 03-C4 and the warmers 03's registry
  declares. It owns no table and no key.
- Validating or repairing authoritative files. Sync reads; it never writes a
  record. The one record it does touch is the campaign write token
  (section 10), which is bookkeeping about records.
- Cache administration (status, doctor, GC, benchmarks). That is a possible
  later `grimoire-cache-admin` surface, not this spec.

## 3. What sync is, given 03

### 3.1 A warm-up, not a correctness step

03 section 2a.2 settles the draft's central question. A key is computed from
the current bytes, so a superseded artifact has no live key that could reach
it. There is nothing for sync to unhook. What sync adds is time: the rebuild
that the next read would have done lazily is done now, off the read path. That
matters in three places:

- **The turn path.** A lore entry edited in a world is re-embedded by recall
  on the next turn that makes it a candidate, inside that turn's latency
  (section 1.4). After a sync, recall finds the vector already there.
- **After restart.** 03's whole value is warm-after-restart. An agent that
  edits forty records and syncs leaves the cache warm for the next launch.
- **08's SearchDocuments.** A scene SearchDocument and its vector are the most
  expensive hot pair the roadmap adds. 08-C2 relies on this spec to rebuild
  them when they go stale rather than on the first retrieval that needs them.

### 3.2 The one correctness-relevant step: 04's first paint

04-C2 promises stale-while-revalidate for first paint: an overview page may
render a previously stored payload before revalidating it. That payload is
served *before* its key is recomputed, so for that payload, and only that one,
liveness does not hold by construction. 04 retires it synchronously on
Grimoire's own writes. An external edit is not a Grimoire write, so without
sync the first paint after an agent's edit shows the old card until
revalidation replaces it.

Sync closes that by calling 04-C2's retirement for every path in the batch,
existing or deleted, **before** it warms anything, so the first paint during a
long warm-up is never the superseded one. This spec needs three properties
from 04-C2, and the plan must confirm each against 04's spec:

1. it takes store-relative paths (existing or deleted) and maps them to the
   first-paint scopes they can affect, or exposes the mapping so sync can;
2. it is **durable across processes**: the CLI runs in its own process, and
   the payload it must retire belongs to the server's. A retirement held only
   in the server's memory cannot be reached from the CLI. Recording it in 03's
   device file (a retired-generation row per scope, which first paint
   consults) satisfies this, and costs the server one indexed lookup per first
   paint;
3. it never raises, so a sync that cannot retire reports it and carries on.

If 04 lands without property 2, sync's CLI cannot keep its promise for a
running server. That is a missing edge (Open question 1), and the fallback is
the API route (section 7.8), which runs inside the server.

### 3.3 What sync does, in order

For one batch:

1. **Validate** each argument as a path inside the store (section 8). Refused
   paths are reported and go no further.
2. **Expand** scopes (`--campaign`, `--world`, `--all`) to the candidate files
   that might have changed (section 7.3).
3. **Retire** 04's first-paint payloads for every surviving path (section 3.2).
4. **Classify** each path: deleted, cold (nothing materialized from it), or
   hot.
5. **Warm** every hot path's non-vector kinds, in tier order (section 6.2).
6. **Re-embed** the vector kinds under the current space, batched (section 6.5).
7. **Bookkeeping:** bump the write token of every campaign a path belongs to
   (section 10), and purge the cache if a world or campaign root was deleted
   (section 7.6).
8. **Report** (section 9).

## 4. The primitive (05-C1): `store/cache_sync.py`

### 4.1 Signature

```python
def sync_paths(relpaths: Iterable[str], *,
               mode: Literal["explicit", "write"],
               embed: bool = True,
               dry_run: bool = False,
               verify: bool = False,
               renamed: Mapping[str, str] = {},
               root: Path | None = None) -> SyncReport: ...
```

- `relpaths` are store-relative, `/`-separated. The CLI and the API convert
  what they were given (section 8). Duplicates are coalesced before anything is
  read.
- `mode="explicit"` is the CLI, the API and a script's `collecting()` exit:
  the caller has declared a batch complete, so every hot kind is warmed now.
  `mode="write"` is the write-through queue (section 5.4): kinds whose policy
  is `on_write="explicit"` are skipped and reported as deferred.
- `embed=False` warms every non-vector kind and reports the vector work it
  left (`deferred`), sending nothing.
- `dry_run=True` validates, classifies and computes the embed plan (projection
  texts and vector-cache hits are local), and builds, stores, retires, embeds
  and bumps nothing.
- `verify=True` is section 7.7.
- `renamed` maps an old path to a new one (section 7.5).
- `root` defaults to `paths.home()`, resolved **once** at entry and pinned for
  the batch: every path is built from it, and the batch stops (reported as
  `root_moved`) if `paths.home()` stops naming it. That is the maintenance
  runs' rule (`run.root`, CLAUDE.md "Detached runs"), for the same reason: a
  data-dir move during a batch must not warm one tree with another's bytes.

It returns a `SyncReport` (section 9). It never raises for a path's failure;
it raises only for a programming error (an unknown mode).

### 4.2 Threading and the lane

- It is synchronous and does file and network I/O. It is never called on the
  event loop's thread. The write-through queue and the API run reach it
  through `anyio.to_thread.run_sync`.
- In one process, at most one batch runs at a time: an in-process lock, the
  **sync lane**, wraps the warm and embed phases. Without it, an API run and
  the write-through queue could both embed the same new text in the same
  minute. Content-addressing makes the second store harmless; the second
  embedding request is spend. The lane is taken with a timeout. A
  write-through batch that cannot take it re-queues its paths. An explicit
  batch waits.
- Across processes (the CLI beside a running server) there is no lane. Both
  may compute the same artifact, and that is idempotent. Both may embed the
  same text, which section 6.6 narrows by checking the vector cache immediately
  before each chunk is sent, and does not close. This is the residual
  `docs/store-guarantees.md` already documents for two processes ("A second
  process on the same store").

### 4.3 No lock on records

Sync never writes a record (the write token in section 10 excepted, which
`revision.bump` writes on its own terms, `revision.py:238`). It reads through
03-C4, which reads bytes once and hashes what it read, and `store.atomic`
guarantees a reader sees the whole old file or the whole new one. So sync takes
no campaign lock, holds no run exclusion key, and cannot refuse or delay a
turn. That is also why `store/cache_sync.py` gets no entry in `store/locks.py`:
it has no `cid`-taking mutator (03 section 13 makes the same argument for the
cache module, and `test_lock_domain_guard.py` rejects a phantom entry).

### 4.4 The racy window

An agent typically syncs seconds after its last edit. By 03 section 5 rule 2,
a file whose mtime or ctime is younger than `PERSIST_WINDOW` at the start of
the read may not get a `sources` row. 03-C5 makes that harmless: the artifact
is keyed by the hash of the bytes actually read and may be stored at once. So:

- sync always stores the artifacts it builds;
- sync asks 03-C4 to record a `sources` row and accepts that 03-C4 declines
  inside the window;
- the server's first read of that path then costs one read and one hash, and
  finds the artifact. It never recomputes.

The write-through queue (section 5.4) runs after a quiet period that is far
longer than `PERSIST_WINDOW`, so its batches normally record the row as well.

## 5. Covered by default: the write set (05-C4) and the write-through queue

### 5.1 The problem

05-C1 is "called by Grimoire's writers". Taken literally, that means a call
after each of the two hundred-odd `atomic` call sites, and the activity
middleware's docstring is the record of what happens to a list like that: it
does not converge. Three other shapes were considered and rejected:

- **The cache inside `store.atomic`.** 03 section 5 rule 4 and its non-goals
  forbid it: `atomic` stays free of SQLite, and a write may not record its own
  `sources` row. It would also put a database transaction inside every
  campaign-lock hold that writes a record.
- **Scope scans at the request boundary.** The middleware knows a `cid` or a
  `wid`, not paths. Turning that into a sync means listing and stating the
  whole campaign after every mutating request, and on a played campaign the
  transcripts make that the most expensive tree in the store. It also misses
  every write a detached run makes.
- **Explicit calls in the writers that feed hot kinds.** Which writers those
  are changes every time a kind is added, which is the convergence problem
  again with a smaller list.

### 5.2 The write set: `store/writeset.py` (05-C4)

A leaf module, importing nothing from `grimoire`, that records which record
paths were published inside a collection scope:

```python
@contextmanager
def collecting() -> Iterator[set[Path]]: ...   # opens a scope; yields its set
def note(path: Path) -> None: ...              # adds to every open scope; no-op when none
```

- The scope is a `contextvars.ContextVar` holding a tuple of mutable sets.
  `note` adds the absolute path to each set and does nothing else: no I/O, no
  stat, no import of the cache. With no scope open (a test, a script that did
  not ask, Android's startup) it is one context-variable read.
- `store.atomic`'s four publishing functions call `writeset.note(path)` after
  the replace or the append has succeeded. That is the only change to
  `atomic`, and it is the line a new guard holds (section 12).
- Sets are mutable and shared by reference, so a write made in a worker thread
  reached through `anyio.to_thread.run_sync` or Starlette's
  `run_in_threadpool` (both of which run the callable in a copy of the current
  context) lands in the scope that was open when the thread was started.
  A raw `threading.Thread` does not copy the context, and its writes are not
  collected. Nothing in the package does that on a write path today, and the
  plan confirms it with a search.

This is not "hooking `store.atomic`" in the sense 03 forbids. `atomic` gains no
SQLite, no import of the cache, no extra I/O, no behaviour when nobody is
collecting, and no `sources` row. It reports a fact (this path was published)
to whoever asked, the way `logs.record` is told about a failure.

The write set can land on its own, before 03. If 04's plan wants paths for its
own synchronous retirement (04-C2), this is the slice to take first.

### 5.3 Where scopes are opened

| Boundary | How | Why there |
|---|---|---|
| Every HTTP request | `_WriteSetCollector`, a raw ASGI middleware in `main.py`, installed beside `_CampaignActivityStamp` and for the same reason (raw ASGI keeps it out of the exception path). It opens a scope, awaits the app, and in a `finally` hands a non-empty set to the write-through queue. | Every route, every method, success or failure. A route that wrote and then failed still wrote, the argument `_CampaignActivityStamp` makes for the token on its exception path. A streamed response has finished streaming by the time `await self.app(...)` returns. |
| Every detached run | `runner._guarded` and `runner._guarded_thread` open a scope around the producer or the work and hand the set over at the terminal point. | A run outlives its request, and its writes are in the lifespan's context, which the request's scope never sees. `runner.start` and `runner.start_thread` are the only two doors. |
| A script | `cache_sync.collecting()` (section 5.5). | Scripts have no app. |

Not covered, by design, and built lazily instead:

- the background inference migration started at startup (`main.start`), which
  is not a request and not a run;
- a write in a thread that did not copy its context;
- **deletes and renames**, which do not go through `atomic`. They need no
  warm-up: nothing about a deleted path is worth rebuilding. 04's own-write
  retirement covers them in the app (04-C2 is 04's), and an explicit sync
  names them (section 7.4);
- another process's writes.

### 5.4 The write-through queue

The middleware and the runner hand their sets to a queue on the app,
`app.state.cache_warm`, installed by `runner.install` beside the run registry,
and **not** a module global, for the reason the registry is not one: a
`TestClient` builds an app per test (CLAUDE.md, "Detached runs").

- **Debounced per path.** Each path carries the monotonic time of its latest
  write. A path is taken only once it has been quiet for `WRITE_QUIET_S`.
  The constant is justified structurally: longer than the span of one logical
  edit (a turn writes the player's post at send and the reply when generation
  ends; an editor save may write several files), so a turn's writes coalesce
  and a scene being played is warmed when play pauses rather than after every
  turn. That keeps a growing transcript from being re-derived, and its new
  passages re-embedded, once per turn. The value (on the order of a minute)
  is tuned later.
- **Root-pinned.** Each entry carries the `paths.home()` it was collected
  under. The worker drops an entry whose root is no longer the store's root,
  and `PUT /config/data-dir` clears the queue beside
  `runs.drop_pending_touched` (`routes/config.py:460`).
- **Bounded.** At most `MAX_PENDING` paths. Past that, new paths are dropped
  with one log line per process. Anything dropped is built lazily. The bound is
  structural (the queue is memory the server holds while idle) and tuned later.
- **Not a run.** It holds no exclusion key, creates no run record, never
  notifies, and is invisible to `runs_in_flight`, so it never makes
  `PUT /config/data-dir` answer 409. A queue that blocked a data-dir move for a
  minute after every edit would be a nuisance with no benefit, since the queue
  is safe to drop.
- **One worker**, a task in the lifespan's task group, that wakes on a timer,
  takes the quiet paths, filters out paths under `.cache/`, `backups/` and
  `logs/` (no kind reads them; the filter only saves lookups), and runs
  `sync_paths(paths, mode="write")` in a worker thread under the sync lane.
- **Failures are swallowed**, with one log line per failure kind per process.
  Nobody is waiting for a write-through warm-up, so there is nobody to tell,
  and a warm-up that fails costs a lazy rebuild. `streaming._fire_follow_up`
  swallows its failures for the same reason.
- **Shutdown** cancels the worker. Pending paths are lost and rebuilt lazily.
- **Off switch.** `GRIMOIRE_CACHE_WARM=0` disables the queue (the collector
  still runs, and costs a set). `tests/conftest.py` sets it, as it sets
  `GRIMOIRE_INFERENCE_AUTOMIGRATE=0`, so no test sees a background warm-up it
  did not ask for. A test about the queue turns it on. It is not a user
  setting.

### 5.5 Scripts: `cache_sync.collecting()`

```python
@contextmanager
def collecting(*, embed: bool = True, deleted: Iterable[str] = ()) -> Iterator[None]: ...
```

Opens a write-set scope, and on a clean exit runs
`sync_paths(collected | deleted, mode="explicit", embed=embed)` synchronously
and stores the report on the context object for the script to print. On an
exception it syncs what was written anyway (a half-applied batch still changed
those files) and re-raises.

`backend/scripts/create_world.py` and `ingest_scene.py` wrap their apply step
in it. For a brand-new world nothing is hot and the sync is a no-op; for an
ingest into a campaign whose search corpus is hot it is not. 06's skill tells
an agent writing through `grimoire.store` from Python to do the same, and to
pass what it deleted through `deleted=`, since deletes are not in the write
set.

## 6. The eager rebuild policy (05-C3)

### 6.1 What "hot" means

A path is hot for a kind when 03's `materialized` record has a row
`(path, kind)`. Nothing else counts: not the existence of an artifact keyed by
the path's old bytes (that table is never enumerated, 03 section 9), not a
guess from the file's directory. A path with no rows is **cold**: sync does
nothing for it and says so. That is what keeps sync from pre-building the whole
library or embedding content nobody embedded.

`materialized` is read only for paths sync already holds, from its arguments
or a live listing, which is 03 section 4's rule ("it never answers which paths
exist").

### 6.2 Warmers, and the order they run in

Each kind in 03's registry gains an optional warmer. This is a field on 03's
registry entry, declared by the kind's owner, never a table in this module:

```python
@dataclass(frozen=True)
class Warmer:
    tier: Literal["file", "composite", "vector"]
    on_write: Literal["quiet", "explicit"]
    warm: Callable[[WarmContext, str, Instance | None], WarmOutcome]
```

- `file` kinds are keyed by one file's bytes (03's search extraction, token
  measures, a parsed transcript). Their warmer is 03's own
  `derive(kind, path)`, the call a reader makes, so a warm-up and a read cannot
  disagree about the key.
- `composite` kinds are keyed by several inputs or a collection digest
  (03-C1): 04's cards and per-scope Todo projections. A path feeds an
  *instance* of one (the world card of `worlds/<wid>`). The warmer derives the
  instance from the path, or reads it from the `materialized` row (section 6.4),
  and calls the same reader the page calls. Composites run after the file tier,
  so they hit the file artifacts just rebuilt.
- `vector` kinds run last (section 6.5), because their text comes from the
  file tier.
- Within a tier, instances are coalesced across the batch: forty entity edits
  in one world warm that world's card once.
- A kind with no warmer is never warmed. Sync reports it as `lazy`.

`WarmOutcome` is one of `hit` (the artifact for the current bytes already
existed), `built`, `skipped(reason)` or `failed(error_class)`. A warmer that
raises is caught; its exception's class name is recorded and its message is
not (section 9).

### 6.3 What the warmers rebuild, and parse failures

A warmer computes what its reader would compute. If the file is malformed in a
way its reader rejects, the warmer fails exactly as that reader would, and sync
reports `failed` for that kind with the exception class. It never stores the
previous version as current: there is no "previous version" in a
content-keyed cache, only a key for the new bytes that now has no artifact, so
the reader's own failure semantics apply on the next read. Frontmatter is "not
validated, by design" (`docs/store-guarantees.md`, "What is not promised"), so
for most Markdown records this cannot happen. A JSON record that no longer
parses is the realistic case.

Sync never repairs a file and never writes one to make a warmer pass.

### 6.4 Vector kinds, and what 05 asks of 03-C3

03 section 4 names a vector row `vector:<space>`. That is not enough to warm
one. Recall, art and search each project the same file differently (an entry's
name, keys and body; an image's description in its catalog form; a document's
passages), so the warmer has to know **which projection** was embedded, and for
an overlaid or campaign-scoped projection, **which instance**. So this spec
asks for two refinements of 03-C3, and degrades without them:

1. **The kind names the projection.** `vector:<projection>:<space-digest>`,
   where `<projection>` is a registry kind that computes the embedded text
   from the path's bytes, and `<space-digest>` is a short hash of
   `embed_space.endpoint()["space"]` (the raw space id joins provider, rev and
   model with NUL bytes, which do not belong in a kind name). Without this
   refinement, no vector kind can be warmed and 05-C3's embedding half waits.
2. **An optional `instance` column.** Small opaque JSON recorded beside the
   row (for example `{"campaign": "saltmarch", "entity": ["lore", "harbour-law"]}`).
   It lets a warmer rebuild an overlaid entry for the campaign that read it,
   and lets sync attribute the embedding to that campaign (section 6.6).
   Without it, warmers derive the instance from the path alone: a world path is
   warmed as the world's entry, unattributed, and a campaign path as that
   campaign's.

Both are read only by path, so neither changes 03 section 9's query-safety
rule.

**This spec wires the three existing producers.** Nothing records a `vector:`
row today (section 1.1), and 03 wires none of them. So 05 adds, for each of
lore recall, the art catalog and library semantic search:

- a projection kind whose compute is the producer's own text function
  (`semantic.entry_text`; the art catalog's candidate text; `semsearch`'s
  `passages` over `search.walk`'s document text), so the warmer and the
  producer cannot disagree about what was embedded;
- a `materialized` row written beside each `vectors.save`, in 03's write batch
  (off the event loop, outside any campaign lock), naming the source path and,
  where the producer knows it, the instance;
- for recall and art, the source path carried on the candidate dict from where
  the candidate is built. The plan finds those points; each candidate is
  already built from a record the producer read.

`continuity-similarity` is left out. It embeds rows of continuity ledgers
inside absorb and the reconcile sweep, both of which recompute what they need
under their own budgets, and its rows are not files an agent edits directly.
08's history embeddings register themselves when 08 lands.

### 6.5 What gets embedded

For each `vector:` row on a hot path, sync:

1. **Checks the space.** It compares the row's space digest with the digest of
   `embed_space.endpoint()`. If they differ, the row belongs to a space the
   Embedding role has moved away from, and re-embedding under the new space
   would be embedding content that was never embedded there, which is exactly
   the spend `confirm_embedding` exists to ask about (CLAUDE.md, "A settings
   surface never spends unasked"). Reported `stale_space`, nothing sent. If
   embedding is off (`endpoint()` is `None`, or a known `no` turned it off),
   reported `embedding_off`, nothing sent.
2. **Computes the projection text** from the current bytes, through the
   projection kind.
3. **Looks the text up** with `vectors.load(space, texts)`. Text already held
   costs nothing. Because vectors are keyed by text, a transcript whose last
   passage changed re-embeds that passage and no other, and a rename whose text
   is unchanged re-embeds nothing.
4. **Queues only the misses** for the batch's embedding phase.

So the spend is bounded by the projection-text diff of paths whose projections
were already embedded in the current space, and never by the size of the
edit's surroundings.

### 6.6 How it is sent

- **Task.** A new embed task, `cache-sync`, added to `routing.EMBED_TASKS`.
  Filing the warm-up's spend under the producers' own tasks would make a
  turn's `semantic-recall` figure include embeddings no turn asked for, and
  would hide what sync costs. A separate task makes the warm-up visible as
  itself on the Costs page and in the error store's per-task counts.
- **Door.** `inference.embed.embed_sync("cache-sync", texts, space=...,
  client=...)`, with `space` from `embed_space.endpoint()` read once per batch
  (never re-resolved per chunk; CLAUDE.md, "Adding an embedding call site?"),
  and the module's own process-wide `EmbeddingsClient`.
- **Chunks.** At most `embeddings.BATCH` texts per call, and each chunk's
  vectors are saved before the next chunk is sent. `semantic.py`'s
  `WARM_LIMIT` comment records why: a rate limit late in one large call
  otherwise "raises before a single vector is saved", and the retry repeats all
  of it.
- **Just-in-time check.** Immediately before each chunk is sent, its texts are
  looked up again, and texts that arrived meanwhile (another process, a turn)
  are dropped from it.
- **Stop on a connection-wide failure.** After an authentication failure,
  `missing_key`, or a rate limit the client has already retried as far as it
  will, no further chunk is sent, and the unsent texts are reported with that
  failure's kind. This mirrors the decide chain's stop after a connection-wide
  failure (`inference._connection_wide`).
- **Deadline.** The client's own per-request timeout, not a caller budget, so
  a slow provider cutting a request is a provider failure, recorded at
  `Meter.done` with its kind and status only (CLAUDE.md, "Adding an embedding
  call site?").
- **Attribution.** Each text is attributed to the campaign of its most
  recently used `vector:` row's instance, or by path when there is no
  instance (`campaigns/<cid>/...` to that campaign; a world, the library or an
  image object unattributed, as library search is today). No scene: a warm-up
  is not part of playing a scene, and a scene's own totals stay "the number
  that is always right" (CLAUDE.md, Costs). With 01h-C5, one call carries texts
  for several campaigns and files a row per campaign. Without it, texts are
  grouped by attribution and each group is its own `embed_sync` call with its
  `campaign=`.
- **Input type.** Once 01h-C1 lands, sync passes the document input type. It
  never embeds a query.

### 6.7 Does re-embedding need confirmation? No, and why

CLAUDE.md's rule is that "a settings surface never spends unasked", and three
routes refuse without a yes today: the model test, a generating health check,
and any change that moves the Embedding role's vector space. The third is the
one that resembles sync, and the resemblance is what the argument has to
address. Re-embedding after an edit does not need the question, for four
reasons:

1. **It never moves the space.** Sync embeds only under the current space and
   only for rows already in it (section 6.5, step 1). The confirmation exists
   because moving the space re-embeds a library; sync can never do that.
2. **The content was already opted in.** A `vector:` row exists only because a
   producer embedded that projection in this space: the user turned on recall,
   art ranking or semantic search, and set the Embedding role. Sync embeds the
   *next version* of text the user already chose to embed.
3. **Lazy would spend the same.** The next read that needs the vector embeds
   the same text at the same price, on the turn path. Sync moves that spend
   earlier. It spends *more* than lazy only when a version is synced and then
   superseded before any read needed it, or when nothing ever reads it again.
   Both are bounded by the paths the caller named, and the write-through
   queue's quiet period removes the common case (a scene re-embedded every
   turn).
4. **The request is explicit.** The CLI and the API are run by the user or by
   an agent acting for them, the way sending a turn is (CLAUDE.md: "Play is not
   gated -- sending a turn *is* the request"). The write-through queue acts on
   the user's own edit in the app.

What replaces a confirmation is visibility and an opt-out: `--dry-run` reports
how many texts would be sent before anything is; `--no-embed` (and
`embed: false`) warms everything else and defers the vectors to lazy; the
report counts texts and requests sent; and every request files a ledger row
under `cache-sync`. Open question 3 asks whether a very large explicit batch
should still want a yes.

## 7. The CLI and the API (05-C2)

### 7.1 The command

```
python -m grimoire.cache sync [PATH ...]
        [--campaign CID ...] [--world WID ...] [--all]
        [--renamed OLD=NEW ...] [--deleted PATH ...]
        [--no-embed] [--dry-run] [--verify] [--json] [--quiet]
```

From a checkout, with the backend venv (the same two forms CLAUDE.md gives):

```
PYTHONPATH=backend/src backend/.venv/bin/python -m grimoire.cache sync ...          # macOS/Linux
PYTHONPATH=backend/src backend/.venv/Scripts/python.exe -m grimoire.cache sync ...  # Windows (Git Bash)
```

- The module is `backend/src/grimoire/cache.py`, beside `where.py`, with a
  `main()` and an argparse parser built by `build_parser()` (06's drift test
  reads it). `sync` is a subcommand so that a later admin surface can add
  `status` or `doctor` without renaming anything. No console script is added:
  `python -m` is the house form, and an entry point would need the installer
  and the APK to agree on one more thing.
- At least one of `PATH`, `--campaign`, `--world` or `--all` is required.
  They combine: the batch is the union.
- `PATH` may be store-relative or absolute. An absolute path must be inside
  the store root (section 8). A directory expands to the files under it.
- The root is `paths.home()`, the resolver every other entry point uses. There
  is no `--root`: a different root is `GRIMOIRE_HOME`, the same override as
  everywhere else. The report states the root and where it came from
  (`paths.data_dir_info()`), as `grimoire.where` does, because an agent whose
  shell and whose server disagree about `GRIMOIRE_HOME` would otherwise sync a
  tree nobody reads.
- It calls `logs.install()` first, so that an embed failure's meter row and
  its log line land in the store's own `logs/` file, through the one writer
  (CLAUDE.md, Observability), exactly as the server's would. A second process
  appending to the month's log or ledger is covered by `atomic.append_line`'s
  `O_APPEND` guarantee (`atomic.py:231`).
- It reconfigures stdout with `errors="backslashreplace"`, as `where.main`
  does, so a path the console cannot spell does not turn a finished sync into
  a traceback.

**Exit status.** `0` when no path is `failed` or `refused` (cache off, cold
paths and deferred vectors are all success); `1` when any is; `2` for a usage
error; `3` when the root cannot be used at all (missing, not a directory, or
unreadable). An agent can branch on it without parsing the report.

### 7.2 Explicit paths

Each path goes through section 8's validation, then section 3.3's steps. A path
that exists is warmed or reported cold. A path that does not exist is reported
`deleted`, retired in 04, and bumps its campaign's token. `--deleted PATH`
says the same thing explicitly, and is how a script hands over a delete it
made through a store function (deletes are not in the write set, section 5.3).

### 7.3 Scoped modes: `--campaign`, `--world`, `--all`

A scope expands to the files that might have changed, found from a live
listing and never from the cache:

1. Validate the id with `paths.safe_id` (`paths.py:243`), the rule
   `test_path_guard_store.py` holds for a caller-supplied id joined onto a
   path. `--campaign CID` is `campaigns/<cid>/`, `--world WID` is
   `worlds/<wid>/`, and `--all` is every syncable top-level directory
   (section 8).
2. Walk the scope, skipping `atomic` temp files (`atomic.is_write_temp`,
   `atomic.py:162`) and anything section 8 refuses, and stat every file.
3. In one batched read of 03's tables, keyed by the listed paths (which is
   within 03 section 9's rule, since the keys came from the filesystem), fetch
   each file's `materialized` rows and its `sources` row.
4. A file is a **candidate** when it has `materialized` rows and its stamp does
   not match its `sources` row, or it has no row. A file with no `materialized`
   rows is cold and skipped without being read. A file whose stamp matches is
   current by 03's trust rule and skipped without being read.
5. Candidates go through `sync_paths`.

This uses the `sources` table only as a hint about which files to read, never
as an answer: a candidate is still read and hashed through 03-C4, and a
non-candidate is one 03's own read path would also have trusted. The cost of a
scoped sync is a stat per file plus a read for each hot file whose stamp moved.

To keep the report bounded, a scoped sync lists individually only paths whose
status is not `current` or `cold`, and counts the rest.

A scoped sync over a deleted root (the directory is gone) is section 7.6.

### 7.4 Deletes

A deleted path needs no rebuild: 03-C2 already makes everything derived from
it unreachable. Sync:

- retires 04's first-paint payloads for it (section 3.2);
- bumps its campaign's write token if it is a campaign path (section 10);
- leaves its `materialized` rows alone. They name a path, not content, and
  03's least-recently-used eviction removes them. Dropping them at once would
  make a delete-then-recreate (an editor that saves by replacing) go cold for
  no benefit.

### 7.5 Renames

A rename is a delete of the old path and a new file at the new one, and the new
path has no `materialized` rows. Sync never infers a rename, not even from
identical content hashes, because a renamed record may be a different record
(the draft's rule, and it is right). The caller may state one:

- `--renamed OLD=NEW` (and `renamed=` on the primitive) copies OLD's
  `materialized` kinds, without their instances, to NEW before NEW is
  classified, so NEW warms as hot. Each warmer derives NEW's instance from NEW's
  path. OLD is then handled as a delete.
- A wrong statement costs some compute and, at worst, embeddings of NEW's
  projections, which are new content: they are reported like any other spend.
  Identical text costs nothing, since vectors are keyed by text.

### 7.6 A deleted world or campaign root

When an argument is a world or campaign root (`worlds/<wid>` or
`campaigns/<cid>`, from a path or a scope flag) and that directory no longer
exists, the store has lost a whole world or campaign outside the app. 03
section 12 purges the cache when the app deletes one, so that derived private
text does not outlive it in this device's file or in other devices' synced
copies. A delete made by hand deserves the same, so sync runs 03's purge (the
marker plus this device's purge) and reports `purged`. That needs 03 to expose
the purge as a callable (Open question 2). Without it, sync reports the root
as deleted and says the purge did not run.

### 7.7 Verify

`--verify` (and `verify=True`) re-runs each warmed kind for each refreshed or
current path with the compiled layer bypassed for that call, and compares the
result with the stored artifact; for vector kinds it checks that every
projection text now has a vector. A mismatch is reported as `verify_failed`,
exits `1`, and writes one log line, because it is a bug in a kind (a key that
does not cover an input), never a user error. It roughly doubles the compute
and sends nothing. It is the production form of 03 section 14's equivalence
test, for an agent that wants evidence rather than a status.

### 7.8 The API: `POST /api/cache/sync`

For the cases the CLI cannot serve: Android, a client that only speaks HTTP,
and a running server whose 04 retirement is not durable (Open question 1).

- **Route.** `routes/cache.py`, a `def` handler (a route that reserves a run
  may not be `async def`, CLAUDE.md "Detached runs"), `post_cache_sync`.
- **Body.** A plain `BaseModel` with plain fields (v1/v2-agnostic, dumped
  through `routes.common._dump`): `paths: list[str]`, `campaigns: list[str]`,
  `worlds: list[str]`, `all_records: bool`, `renamed: list[dict]`,
  `deleted: list[str]`, `embed: bool = True`, `dry_run: bool = False`,
  `verify: bool = False`. At least one selector is required, or 400.
- **Answer.** 202 with the run payload. The run is the `background` class,
  kind `cache-sync`, on `runs.GLOBAL_SUBJECT` (`runs.py:1349`), reserved with
  `single_live=True` (`start_or_existing`, `runs.py:497`): a second request
  while one is live is handed the live run, and its paths are left for that run
  to take after its pass, through the registry's pending set (`pend_touched`,
  `runs.py:666`), the same arrangement as a campaign's reconcile sweep. The
  pending set is keyed by subject today, and the global subject would share it
  between kinds, so the plan keys it by subject and kind.
- **Progress and result.** The run appends progress frames (counts only) and a
  final frame carrying the report. It is reached through the global subject's
  existing four run routes (list, poll, stream, cancel). Cancel stops before
  the next path or chunk, and the report says what ran.
- **Exclusion.** None, as for every `background` run: it neither holds a scene
  nor is refused by one. A live run does make `PUT /config/data-dir` answer
  409, like any run, which is correct: a sync in flight is pinned to the root
  it started on.
- **Not a write.** The path is not under `/api/campaigns/`, so the activity
  middleware stamps nothing. The write tokens sync bumps (section 10) are
  bumped by sync itself, where it writes, which is the rule CLAUDE.md states
  for "Everything a *detached* run writes".
- **Docs.** CLAUDE.md's "Detached runs" section counts the handlers that start
  runs ("Thirty-two handlers") and lists them by class. This adds one to the
  `background` bullet, and the count moves with it, in the same change.
  `test_claude_md_names_every_run_class` already passes, since the class
  exists.

### 7.9 Android

The Android app packages `backend/src` verbatim (CLAUDE.md, Android). There is
no shell for the user, and nothing outside the app can write its private
storage except through `scripts/grimoire_sync.py` over adb. So on Android:

- `grimoire/cache.py` ships and is never run. It uses only `argparse` and the
  store, so it costs nothing.
- The write-through queue runs as on desktop, if 03's cache is on there. If
  03's cache is off on Android (03 section 13: until `sqlite3` and the `BUILD`
  stamp are confirmed in the APK), every sync reports `cache: off` and does
  nothing else, successfully.
- `POST /api/cache/sync` is the explicit door. A PC-side tool could reach it
  through `adb forward`. Nothing does today, and lazy rebuilding covers a file
  `grimoire_sync.py` pushed (Open question 5).

## 8. Path validation

A path given to sync is untrusted input to a process that will read and parse
the file it names, so it is held to a closed rule:

1. **Inside the root.** Relative paths are joined onto the pinned root.
   Absolute paths must be within it, compared after `os.path.normcase` and
   `os.path.normpath`, and never through `resolve()`, which follows links. Any
   `..` component that escapes the root is refused (`outside_store`).
2. **No links below the root.** Every component from the root down is checked
   with `lstat`, and a symlink or a Windows junction (a reparse point) refuses
   the path (`link`). The root itself may be a link: where the library lives is
   the user's choice. This is the rule 03 applies to `.cache` and image GC
   applies to its own folders (`image_gc._store_blocks`, `image_gc.py:552`),
   for the same reason. Following a link would have the server read, hash and
   parse a file outside the library, and a status of "parsed" or "failed" is an
   answer about that file.
3. **Syncable roots only.** The first component must be one of a closed set of
   record directories: `worlds`, `campaigns`, `assets`, `modules`,
   `calendars`, `climates` and `styles`. Anything else is refused
   (`not_syncable`): `.cache` and `backups` are derived (`external.SKIP_AT_ROOT`,
   `external.py:65`), `logs` and `usage` are ledgers, `llm_connections` holds
   keys, the staging directories are transient, and top-level files such as
   `config.md` enter keys as `params`, never as paths. A closed set fails
   safe: a record directory added later is refused until somebody adds it, and
   a refused path is only a lazy one.
4. **Not a write temp.** `atomic.is_write_temp` names are refused
   (`temp_file`).
5. **A file, or a directory to expand.** Anything else (a socket, a device) is
   refused (`not_a_file`).
6. **Spelling.** Ids from `--campaign` and `--world` go through
   `paths.safe_id`. A path given in a different case on a case-insensitive
   volume is accepted as the OS resolves it and reported as given.
7. **Bounded.** A batch accepts at most `MAX_PATHS` explicit paths and
   expands a directory to at most that many files. Past it, the rest is
   reported as `too_many` and the report says it was truncated. "Found
   nothing" and "stopped looking" must not read the same, which is
   `external.py`'s rule for its own walk.

## 9. Output and privacy

The report never contains record content. That rules out file bytes, parsed
field values, projection text, a parser's or a provider's error message (a
YAML or JSON error can quote the line it failed on, and an embeddings error
body can echo its input), and any URL or key. It contains:

- the root and its source;
- `cache`: `on`, or `off` with 03's reason;
- per path (all of them for explicit paths; only the interesting ones for a
  scope): the path as given, a `status` (`refreshed`, `current`, `cold`,
  `deleted`, `refused`, `failed`, `verify_failed`), a `reason` for `refused`,
  and per kind an outcome (`built`, `hit`, `lazy`, `deferred`, `skipped`,
  `failed:<ExceptionClass>`);
- per batch: counts per status, kinds built, retirements made, tokens bumped,
  whether a purge ran, and embedding totals (texts sent, requests, texts
  already held, `stale_space`, `embedding_off`, `deferred`, and the failure
  kind of a stopped chunk);
- `truncated` when a bound was hit.

Paths and ids are printed because the caller supplied them and needs them to
act. They are names, and the CLAUDE.md privacy rule about names is about what
gets committed to this repository; the 06 skill repeats that boundary. The log
gets less: one INFO line per batch with counts and the mode, and no paths,
because the log is a file a user may hand to somebody else (CLAUDE.md,
Observability).

`--json` prints the report as one JSON object with a `version` field. The text
form is a short table of counts, then one line per listed path. `--quiet`
prints only the counts.

## 10. The write token

`revision.py:110` names hand edits as something the token cannot see, and
explains that a token which has not moved is "evidence and not proof". An
explicit sync is the first moment the app is *told* about a hand edit, so it
uses it: every campaign that a non-refused path in the batch belongs to
(`campaigns/<cid>/...`) gets `revision.bump(cid)` once per batch, whether the
path was refreshed, current, cold or deleted.

- That over-bumps (a path sync found current still bumps), which is the
  direction the token is "deliberately wrong in": a re-price for somebody,
  never a lost write (the reasoning in `_CampaignActivityStamp`'s exception
  path).
- It never bumps for a world path. The token records writes to the campaign,
  and fanning a world edit out to every campaign is what `revision.py` already
  declines.
- The write-through queue does not bump. Grimoire's own writes were stamped by
  the middleware or by the run that wrote them.
- `--dry-run` bumps nothing.

## 11. Contract

**05-C1. The in-process sync primitive, reached by Grimoire's writers.**

- *Inputs:* store-relative paths; `mode` (`explicit` or `write`); `embed`,
  `dry_run`, `verify`; an optional rename map; a root, defaulting to
  `paths.home()` read once.
- *Outputs:* a `SyncReport` (section 9).
- *Guarantees:* writes no record except the campaign write tokens of section
  10; takes no campaign lock and no run exclusion; validates every path
  (section 8); stores artifacts through 03 and never a `sources` row inside
  the window (03-C5); retires 04's first-paint payloads before warming
  (explicit mode); one batch at a time per process (the sync lane); pinned to
  the root it started on.
- *Coverage:* every HTTP request and every detached run hands its write set
  to the write-through queue (05-C4), which runs this primitive in `write`
  mode once each path has been quiet for `WRITE_QUIET_S`; scripts reach it
  through `cache_sync.collecting()`.
- *Failure:* never raises for a path; a path's failure is a status. The
  write-through queue swallows and logs once per failure kind. The cache being
  off is a successful no-op.

**05-C2. `cache sync`: the CLI and the API.**

- *CLI:* `python -m grimoire.cache sync` with `PATH...`, `--campaign`,
  `--world`, `--all`, `--renamed OLD=NEW`, `--deleted`, `--no-embed`,
  `--dry-run`, `--verify`, `--json`, `--quiet`; the parser is
  `grimoire.cache.build_parser()`; exit status 0/1/2/3 as section 7.1.
- *API:* `POST /api/cache/sync` answering 202 with a `background` run
  (`cache-sync`) on the global subject, single-live, with later requests'
  paths pended for the live run; the report is the run's final frame.
- *Batching:* duplicates coalesced, composite instances coalesced, embeddings
  chunked at `embeddings.BATCH`, cache writes batched by 03.
- *Summary:* holds no record content (section 9); the log line holds counts
  only.

**05-C3. Eager rebuild of materialized kinds, embedding only what was already
embedded.**

- A kind is rebuilt for a path only if `materialized` has `(path, kind)`; a
  path with none is cold and costs nothing.
- Tiers run file, then composite, then vector; instances are coalesced per
  batch.
- A vector row is re-embedded only in the current space, only for projection
  text the vector cache does not hold, under the `cache-sync` embed task,
  attributed per section 6.6, with no confirmation (section 6.7); `--no-embed`
  defers it; `--dry-run` reports it.
- Kinds whose `on_write` is `explicit` are skipped by the write-through queue.
- The three existing vector producers (recall, art, library search) record
  `materialized` rows with their projection named.

**05-C4 (new). The write set.**

- `store/writeset.py`, a leaf module: `collecting()` and `note(path)`.
- `store.atomic`'s four publishing functions call `note` after a successful
  publish, and do nothing else new.
- Opened by `_WriteSetCollector` (every HTTP request), `runner._guarded` and
  `runner._guarded_thread` (every detached run), and
  `cache_sync.collecting()` (scripts).
- Records absolute paths only; never reads, stats or imports the cache; costs
  one context-variable read when no scope is open.
- Does not see deletes, renames, writes in a thread that did not copy its
  context, or another process's writes.

## 12. Interaction with repo rules

- **`store.atomic` and its guard.** `atomic` gains one call per publishing
  function and no import beyond the leaf `writeset`. `test_atomic_guard.py` is
  unaffected: nothing new writes a record outside `atomic`. A new guard,
  `test_writeset_guard.py`, holds that every public function in `atomic` that
  publishes bytes calls `writeset.note` on its success path, so a fifth writer
  added later is collected too. It is named in CONTRIBUTING.md's guard table
  (`test_contributing_names_every_guard_test` requires it), and has no marker.
- **Import graph.** `writeset` imports nothing from `grimoire`, so
  `atomic -> writeset` adds no cycle. `cache_sync` imports 03's module,
  `embed_space`, `vectors`, `inference.embed`, `revision` and `paths`; it binds
  submodules inside `store/`, never names off a package
  (`test_import_guard.py`). The routes import `cache_sync`; the store never
  imports the routes.
- **Paths.** Every path is built from `paths.home()` (`test_paths_guard.py`).
  Ids go through `paths.safe_id` (`test_path_guard_store.py`).
- **Locks.** None taken; no entry in `store/locks.py` (section 4.3).
  `revision.bump` is already classified (`store/locks.py` lists
  `store.revision` outside the domain with its reason).
- **Metering and routing.** The new task `cache-sync` is in
  `routing.EMBED_TASKS`; every request goes through `embed_sync` with a space
  from `embed_space.endpoint()`, which `test_operation_guard.py` traces, and
  through a meter's holder (`test_usage_guard.py`). A failure is recorded at
  `Meter.done` with kind and status only.
- **Costs.** An endpoint that reports no price files unpriced rows under
  `cache-sync`, so the Costs totals read incomplete until the user sets rates,
  exactly as for recall today. An OpenRouter embedding reports its cost, which
  is spend and counts against the attributed campaign's budget. Campaign
  budgets only warn.
- **Detached runs.** One new `background` handler, `post_cache_sync`; the
  write-through queue is not a run. CLAUDE.md's handler count and background
  bullet change with it.
- **Observability.** One writer: the CLI calls `logs.install()`; sync logs a
  degradation once per kind per process and a batch line with counts.
- **Write tokens.** Section 10. The API route sits outside `/api/campaigns/`
  and is not stamped by the middleware.
- **pydantic v1/v2.** The request model uses plain fields only.
- **Android.** Section 7.9. No base dependency is added.
- **Privacy.** Section 9; tests use the placeholder names (Seraphine, Mara,
  Winifred, Realm, Saltmarch) on `tmp_path` stores. No constant here is
  justified by a measurement of a real library.

## 13. Tests and acceptance

All on `tmp_path` stores with the placeholder names, the cache on, the
embeddings client faked with `llm_fakes.FakeEmbeddings` (`llm_fakes.py:748`),
and `GRIMOIRE_CACHE_WARM=0` except where a test turns the queue on.

**The write set (05-C4).**

- Each of `write_text`, `write_bytes`, `append_line` and `streaming_write`
  notes its path in an open scope, and only after a successful publish (a write
  that raises notes nothing).
- Nested scopes both receive the path; no open scope costs nothing and
  records nothing.
- A write in `anyio.to_thread.run_sync` and in a `def` route's threadpool
  worker lands in the request's scope.
- `test_writeset_guard.py` fails a new publishing function in `atomic` that
  does not call `note`.

**Write-through (05-C1).**

- A `PUT` that edits an entity in world Realm leads, after the quiet period
  (injected clock), to the entity's hot kinds being rebuilt and its recall
  vector re-embedded under `cache-sync`.
- A route that writes and then raises still hands its paths to the queue.
- A detached turn's writes reach the queue at its terminal point.
- Repeated writes to one transcript inside the quiet period produce one
  warm-up.
- A data-dir move clears the queue, and an entry collected under the old root
  is never warmed against the new one.
- The queue past `MAX_PENDING` drops with one log line.
- A warm-up failure is swallowed and logged once.

**The policy (05-C3).**

- A path with no `materialized` rows is reported cold, and nothing is read or
  sent.
- A path hot for a file kind and a vector kind rebuilds the file kind first
  and embeds once.
- Forty edited entities in Realm warm Realm's world card once (a composite
  warmer registered by the test).
- A vector row in a space other than the current one is `stale_space` and
  sends nothing; with embedding off it is `embedding_off`.
- A transcript whose last passage changed embeds one passage.
- A rename stated with `--renamed` whose text did not change embeds nothing.
- A connection-wide failure on the first chunk stops the rest, and the report
  names its kind.
- Texts for two campaigns in one batch file per-campaign rows (with 01h-C5) or
  two calls (without it).
- The three producers record a `vector:<projection>:<space-digest>` row beside
  each `vectors.save`, and dropping the `materialized` table changes no
  producer's answer.

**The CLI and the API (05-C2).**

- Exit statuses 0, 1, 2 and 3 for the cases in section 7.1.
- `--dry-run` sends nothing, stores nothing, retires nothing and bumps
  nothing, and reports the texts it would send.
- `--no-embed` defers every vector and sends nothing.
- A scoped sync reads only hot files whose stamp moved (counted reads on an
  instrumented store), and lists only non-current paths.
- Every refusal in section 8 is reported with its reason: a path outside the
  root, a `..` escape, a symlinked directory, a junction (Windows only), a
  path under `.cache/`, `logs/` and `llm_connections/`, a write temp, an
  unsafe id, and a batch over `MAX_PATHS`.
- A deleted campaign root triggers 03's purge (once 03 exposes it).
- `--verify` reports a mismatch for a kind deliberately registered with a key
  that omits an input.
- The report never contains a body: a test syncs records whose bodies contain
  a sentinel string, including a malformed JSON record and a provider error
  whose body echoes its input, and asserts the sentinel appears in neither the
  JSON report, the text output, nor the log file.
- Two concurrent `POST /api/cache/sync` requests share one run, and the second
  request's paths are processed.
- `PUT /config/data-dir` answers 409 while an API sync run is live.
- An explicit sync of a campaign path bumps that campaign's token once per
  batch; a world path bumps none.
- A sync from a second process (the CLI in a subprocess against the test root)
  retires 04's first paint so that the server's next first paint is not the
  superseded one. This test waits for 04-C2.

**Acceptance.** An agent edits a hot lore entry, an image description through
`image_store.update`, and deletes an obsolete lore file in Realm, then runs one
`cache sync` naming the three paths. Afterwards: the Worlds page's first paint
does not show the old card; recall's next turn sends no embedding request for
the edited entry; search finds the new description and not the old; the
report lists one `refreshed`, one `refreshed` and one `deleted`, and contains no
body text.

## 14. Non-goals

- Making ordinary reads correct. 03 does that; a forgotten sync costs a cold
  read.
- Inferring renames, or record identity from content.
- Watching the filesystem (inotify, FSEvents, `ReadDirectoryChangesW`). A
  watcher is a second change-detection system with its own failure modes on
  synced and network volumes, and 03's stat-on-read already notices every
  change. It would add immediacy for edits nobody announced, which is lazy
  rebuilding's job.
- Warming anything cold, or pre-building the library.
- Re-embedding under a new space. That is the Embedding role's move, behind
  `confirm_embedding`.
- `continuity-similarity` vectors (section 6.4).
- Cache administration: status, doctor, GC, benchmarks.
- Validating or repairing authoritative files.
- Bumping write tokens for world edits, or from the write-through queue.
- Notifying another device. A synced store's other device notices by stat on
  its next read.

## 15. Open questions

1. **04-C2 retirement across processes (missing edge).** The CLI can retire
   04's first-paint payloads in a running server only if 04 records retirement
   somewhere both processes read, such as a retired-generation row per scope in
   03's device file. *Recommendation:* ask 04's spec to make retirement durable
   in the device file and to take store-relative paths (section 3.2). If 04
   declines, the CLI's report says first paint may lag until revalidation, and
   the skill (06) prefers the API when a server is running.
2. **03's purge as a callable (missing edge).** Section 7.6 needs 03 section
   12's world/campaign purge exposed as a function, which no numbered 03
   contract says. *Recommendation:* add it to 03-C3's or a new 03 contract's
   text; until then, sync reports a deleted root without purging.
3. **Should a large explicit batch ask before embedding?** Section 6.7 argues
   that no confirmation is needed. A bulk external change (a store restored
   from an old copy, then `--all`) could still send many requests in one go.
   *Recommendation:* no confirmation in the first version. `--dry-run` shows the
   number first, and the skill tells agents to run it before an `--all`. Revisit
   with a threshold of one `embeddings.BATCH` if the dry runs surprise anyone.
4. **The 03-C3 refinements (section 6.4).** A projection-qualified vector
   kind is needed for any vector warm-up, and an `instance` column for overlay
   instances and per-campaign attribution. *Recommendation:* fold both into
   03's plan now, since 03's table is new and has no migration story to
   protect.
5. **`grimoire_sync.py` after an adb pull.** The adb script writes PC files
   when it pulls a phone's edits, and never imports `grimoire`. It could print
   the changed paths as a `cache sync` command, or run one.
   *Recommendation:* print the command and leave the script import-free; lazy
   rebuilding covers anyone who ignores it.
6. **Write-through for 08's scene documents.** A played scene's transcript
   changes every turn. With the quiet period, its SearchDocument and vector are
   rebuilt when play pauses. 08 may prefer `on_write="explicit"` and rebuild at
   absorb. *Recommendation:* leave it to 08; the warmer field exists so that
   each kind decides.
7. **A console script.** `grimoire cache sync` reads better than
   `python -m grimoire.cache sync`. *Recommendation:* no console script: the
   house form is `python -m` (`grimoire.where`), and an entry point adds
   something the installers, the venv and the APK must agree on.

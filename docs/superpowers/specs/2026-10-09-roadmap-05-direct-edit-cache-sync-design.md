# 05. Direct-edit cache sync

**Status:** Draft — spec gate (`/codex:adversarial-review`) pending.
**Date:** 2026-10-09
**Roadmap:** 05 in `ROADMAP-CHECKLIST.md`. Lane: cache (03 -> 04 -> 05 -> 06, and 05 + 07 + 01h -> 08).
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** the bundle draft `05-direct-edit-cache-sync.md` (2026-10-06,
written against `7f80c42`), against current code and against the reconciled 03
(`2026-10-09-roadmap-03-content-addressed-compiled-cache-design.md`, section
2a in particular). It also fits the drafts of 04
(`...-roadmap-04-instant-overview-pages-design.md`), 08
(`...-roadmap-08-history-search-documents-design.md`) and 01h
(`...-roadmap-01h-embedding-options-design.md`) written beside it. It
supersedes the bundle draft wherever the two disagree; section 1.5 lists
where.

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning. In particular, re-read 03's plan and its
> registry API, and the landed forms of 04-C2a, 08-C2c and 01h-C5, before
> writing a line of this one: none of them had landed when this was drafted.

## Depends on

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| 03-C2 | 03 | Liveness by construction. It is the reason sync is a warm-up and never a correctness step (section 3). | Hard |
| 03-C3 | 03 | The `materialized` record: which kinds were built from a path. It is the whole of "what was hot" (section 6). Section 6.4 asks for one refinement: a projection-qualified vector kind, and an optional `instance` column. | Hard (refinement soft) |
| 03-C4 | 03 | The one validate-and-hash primitive. Sync calls it for every path, and never hashes or stamps on its own (section 4). | Hard |
| 03-C5 | 03 | Artifacts may be stored at once, inside the racy window, and only the `sources` row waits. Sync runs seconds after an agent's edit and depends on this (section 4.4). | Hard |
| 03-C1 | 03 | Composite keys, so that a hook can rebuild a collection-keyed kind for the instance a path feeds. | Soft (only composite hooks need it) |
| 03 section 12 | 03 | The cache purge on a world or campaign delete. Sync performs it when it finds a deleted world or campaign root (section 7.6). No numbered contract states this today: see Open question 2. | Soft (missing edge) |
| 04-C2a | 04 | `overview.warm_paths(paths)`, the hook through which sync rebuilds 04's overview kinds (section 6.2). | Soft (without it, overview kinds rebuild lazily) |
| 04-C2b | 04 | The client's consistency bound. Sync inherits it unchanged and adds no retirement step (section 3.2). | Hard (a property relied on, no call made) |
| 01h-C5 | 01h | `attribute(claims)` and `embed_groups_sync(task, groups, ...)`: one ledger row per campaign, with no request spanning campaigns. Without it, sync makes one `embed_sync` call per campaign group itself (section 6.6). | Soft (the fallback is complete, only less shared) |
| 01h-C3 | 01h | Embedding options in the space identity. Sync names a space only through `embed_space.endpoint()["space"]`, so it inherits whatever 01h-C3 adds. | Soft |
| 01h-C1 | 01h | Input type. Sync only ever embeds documents, so once 01h-C1 lands it passes the document type. | Soft |
| 01 (landed) | 01 | `inference.embed.embed_sync`, `routing.EMBED_TASKS`, `embed_space.endpoint()` and the metered embed door. | Hard (landed) |

08-C2c (08's rebuild hook) is not a dependency. 05 defines the hook protocol
(section 6.2), and 08 registers into it.

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 05-C1 | 08 | The vehicle that runs 08's `reembed` after the response, in a worker thread, outside every lock, with failures swallowed: 08's Open question 8 asks for exactly this. |
| 05-C2 | 06 | The command the store-editing skill tells an agent to run after a batch of direct edits, and the flags it names. |
| 05-C2 | 08 | `--campaign` and `--world` as the explicit way to bring a scope's hot SearchDocuments current after a bulk change. |
| 05-C3 | 08 | The hook protocol 08-C2c implements, and the policy 08's vectors follow: re-embed only what was embedded, only in the current space, only text the vector cache does not hold. |
| 05-C4 (new) | 04 (optional), 05-C1 | The write set: which record paths a request or a run wrote. 04-C2a could use it to generalise `warm_scene` beyond the turn path. It can land before everything else here. |

## 1. Current state (reconciled against main)

### 1.1 There is no compiled cache yet, and no record of what was built

03 is a spec, and nothing of it has landed. What exists today:

- `store/statcache.py` is in-process only. It is keyed on stat signatures
  (`signature`, `statcache.py:30`; `stamp`, `statcache.py:107`), with a
  one-second racy window (`RACY_WINDOW_NS`, `statcache.py:26`). It notices an
  external edit on the next read, and it is lost at exit.
- `store/vectors.py` caches embeddings on disk under `.cache/embeddings/`,
  keyed by `sha256(space + "\0" + text)` (`_path`, `vectors.py:115`; `load`
  at `:150`, `save` at `:181`). Its docstring already states the property 03
  builds on: "Editing an entry's body simply maps it to a new key and the old
  vector goes unreferenced."
- Three producers write vectors today, each from its own read path:
  - lore recall (`store/context/semantic.py`, `vectors.save` at `:278`), task
    `semantic-recall`. It embeds `entry_text` (`:203`): the name, keys and
    body of a world-info entry;
  - the art catalog (`store/context/art.py`, `vectors.save` at `:586`), task
    `art-catalog`;
  - library semantic search (`store/semsearch.py`, `vectors.save` at `:338`),
    task `semantic-search`. It embeds post-aligned passages (`passages`,
    `:125`) of every document `search.walk` yields, transcripts included.

  A fourth embed task, `continuity-similarity`, embeds continuity rows inside
  absorb and the reconcile sweep. `routing.py:174` lists all four.
- None of the three producers records which file a vector came from. So
  nothing can answer "which projections of this file were embedded?", which is
  the question 05-C3 turns on.

### 1.2 Where Grimoire writes, and why no one function sees it

- `store/atomic.py` is the record-write door: "Every Markdown/JSON record in the
  store goes through here" (`atomic.py:3`). Its publishing functions are
  `write_text` (`:216`), `write_bytes` (`:226`), `append_line` (`:231`) and
  `streaming_write` (`:276`). `test_atomic_guard.py` holds the whole package to
  it. They are called from over two hundred sites in about a hundred modules.
- Deletes and renames do not go through `atomic`. They are `unlink`,
  `rmtree`, `os.replace` and `rename` calls, spread over the store and the
  routes, and there are over a hundred of them.
- The activity middleware is the precedent for coverage that does not depend
  on anyone remembering. `_CampaignActivityStamp` (`main.py:416`) records
  recents order and the write token (#409) for every successful mutating
  request under `/api/campaigns/`, because "Enumerating the mutators does not
  converge ... the store deliberately takes *roots* rather than cids at its
  leaves, so there is no one function they all pass through." It also bumps the
  token on an unhandled exception, for the same reason. It is raw ASGI on
  purpose, so that it stays out of the exception path.
- Detached runs write from the lifespan's task group, not from the request.
  Every one starts through `runner.start` (`runner.py:200`) or
  `runner.start_thread` (`runner.py:220`), and runs inside `runner._guarded`
  (`:355`) or `runner._guarded_thread` (`:251`). A request-scoped boundary does
  not see their writes. A run-scoped one does.

### 1.3 The command-line surface

- The package has **no console script**: `backend/pyproject.toml` declares no
  `[project.scripts]`.
- The one in-package command is `python -m grimoire.where`
  (`backend/src/grimoire/where.py`), which the installers run. It resolves the
  root through `store.paths` rather than assuming `~/.grimoire`, prints ASCII
  only, and reconfigures stdout with `errors="backslashreplace"`, so that an
  accented home directory cannot fail it (`where.main`).
- The other entry points are scripts that put `backend/src` on the path:
  `backend/scripts/create_world.py`, `ingest_scene.py` and
  `redownload_chub.py`, and the skills' own scripts
  (`.claude/skills/*/scripts/`). Each writes the store through `grimoire.store`
  functions.
- `scripts/grimoire_sync.py` is the adb sync between a PC and a phone. It
  treats files as blobs, never imports `grimoire`, and skips `.cache` at the
  root (its own `SKIP_AT_ROOT`).

### 1.4 What happens to an external edit today

- **Reads notice it.** `docs/store-guarantees.md`, "Reads notice external
  writes": every request re-reads, and statcache is keyed by stat, so the next
  read sees a changed file.
- **Vectors self-heal lazily.** An edited lore entry has new `entry_text`, so
  it misses the vector cache. Recall embeds it the next time it is a
  candidate, with at most `WARM_LIMIT = embeddings.BATCH - 1` new entries per
  turn (the `WARM_LIMIT` comment in `semantic.py`). That spend happens on the
  turn path, inside the turn's latency.
- **The write token does not move.** `store/revision.py:110`: "A store written
  by something other than this app (a hand edit, a sync client landing a file,
  ...) moves nothing here."
- **The existing skills write through `grimoire.store`, not the filesystem.**
  `world-card-integration` says so under the heading "Work through the store,
  not the filesystem", and `create-world` and `ingest-campaign-log` drive
  scripts that do the same. An agent editing the store today is usually a
  Python process calling store functions with no server involved, not an
  editor writing bytes.

### 1.5 Where the bundle draft and current code disagree

| Draft | Current code, 03 and 04 | Consequence here |
|---|---|---|
| A live source-to-hash mapping that sync updates (draft steps 4 to 6). | There is none. 03 section 2a.2: "The filesystem is that mapping." Every read computes its key from current bytes. | Sync updates nothing a read relies on for correctness. Step 6 ("remove the old hash from live mappings") already holds. |
| "After sync completes, no live query may return content derived from the superseded source version" as sync's core invariant. | True at every read by 03-C2, with or without sync. 04 keeps first paint in the client's memory, adds no server-side retirement step (04-C2a), and bounds what a client may show after an external edit (04-C2b, section 6.5). | Sync has **no** correctness step. It is a warm-up only (section 3). |
| `store.atomic` or the writers update the source mapping straight after a write. | 03 section 5 rule 4: a write never records its own `sources` row. 03-C5: artifacts may be stored at once. | Sync stores artifacts and leaves `sources` to 03's rules. It never asks for a row inside the window. |
| "On read, if the stat signature differs, refresh the source hash" (draft section 7), as a backstop to add. | That is 03's read path. | Not part of 05. |
| Image sidecar descriptions edited in record folders. | Descriptions live on the image object (`assets/image-store/objects/<shard>/<id>.json`, `image_store.py:6`), written only through `image_store.update` under the image locks (`docs/store-guarantees.md`, "Shared metadata"). A legacy `descriptions.json` key is read first until migration. | Sync accepts object sidecar paths. 06's skill tells agents to write descriptions through the store function, never by hand. |
| Renames are a delete plus a create, unless identity can be preserved. | Agreed. 03 keys by bytes and path parts, so a renamed file is a new path with no `materialized` rows. | The caller may state a rename (`--renamed OLD=NEW`). Sync never infers one (section 7.5). |

## 2. Goal (and what is explicitly not the goal)

**Goal.** After an edit made outside the running app (by an agent, a script or
an editor), and after Grimoire's own writes, the derived representations that
were already in use are rebuilt eagerly. The next read that needs them is then
warm. Specifically:

1. An explicit, batched `cache sync` over named paths or scopes, for agents
   and scripts, with a summary that holds no record content (05-C2).
2. The same primitive, reached by default from Grimoire's own writers, without
   per-call-site wiring and without putting the cache in `store.atomic`
   (05-C1, through the write set, 05-C4).
3. A rebuild policy that warms exactly what was hot, and embeds only text
   whose predecessor was embedded, in the current space, that the vector cache
   does not already hold (05-C3).
4. One hook protocol, so that 04's overview kinds, 08's SearchDocuments and
   the existing vector producers are each rebuilt by their owner's code, in one
   ordered batch.

**Not the goal.**

- Correctness of any read. 03-C2 already gives it, and 04-C2b already bounds
  first paint. A forgotten sync costs a cold read, never a wrong one.
- A second invalidation system. Sync calls 03-C4 and the owners' hooks. It owns
  no table and no key.
- Validating or repairing authoritative files. Sync reads; it never writes a
  record. The one exception is the campaign write token (section 10), which is
  bookkeeping about records.
- Cache administration: status, doctor, GC and benchmarks. A later
  `grimoire-cache-admin` surface may cover those. This spec does not.

## 3. What sync is, given 03 and 04

### 3.1 A warm-up, not a correctness step

03 section 2a.2 settles the draft's central question. A key is computed from
the current bytes, so a superseded artifact has no live key that could reach
it, and there is nothing for sync to unhook. What sync adds is *time*: the
rebuild the next read would have done lazily is done now, off the read path.
That matters in three places:

- **The turn path.** A lore entry edited in a world is re-embedded by recall
  on the next turn that makes it a candidate, inside that turn's latency
  (section 1.4). After a sync, recall finds the vector already there.
- **After restart.** Warm-after-restart is 03's whole value. An agent that
  edits forty records and syncs leaves the cache warm for the next launch.
- **08's SearchDocuments.** A scene SearchDocument and its vector are the most
  expensive hot pair the roadmap adds. 08-C2c relies on this spec to rebuild
  them when they go stale, rather than on the first retrieval that needs them.

### 3.2 First paint needs nothing from sync

The checklist gave 04-C2 as "synchronous retirement ... and
stale-while-revalidate for first paint". The bundle draft of 05 read that as a
server-side payload that sync would have to retire. 04's spec does not build
one:

- **On the server there is no retirement step** (04-C2a, 04 section 5.1). Every
  overview projection is reached through a key computed from current bytes,
  so an external edit is seen by the next read like any other.
- **First paint lives in the client's memory** (04-C2b). A page paints a
  remembered answer, marks itself `aria-busy`, and always revalidates. 04
  section 6.5 states the bound: an edit the tab did not make or observe ("a
  hand edit, an agent's direct edit (with or without 05's `cache sync`)") may
  be shown for exactly one frame of the next visit, before that visit's own
  read replaces it.

So sync adds no retirement call. That is fortunate, since a CLI process has no
way to reach a browser tab's memory. There is one small thing sync gets for
free. A sync started through the API is a detached run (section 7.8), and 04
section 6.3, item 2, has the client forget its remembered reads when it
observes a run end. A tab that watched an API sync therefore never paints from
before it.

### 3.3 What sync does, in order

For one batch:

1. **Validate** each argument as a path inside the store (section 8). Refused
   paths are reported and go no further.
2. **Expand** scopes (`--campaign`, `--world`, `--all`) to the candidate files
   that may have changed (section 7.3).
3. **Classify** each path as deleted, cold (nothing materialized from it) or
   hot.
4. **Rebuild, locally**: run each registered hook's local phase over the hot
   paths, in hook order (section 6.2). No network.
5. **Re-embed**: run each hook's network phase under the current space, in
   batches (sections 6.5 and 6.6).
6. **Bookkeeping**: bump the write token of every campaign a path belongs to
   (section 10), and purge the cache if a world or campaign root was deleted
   (section 7.6).
7. **Report** (section 9).

## 4. The primitive (05-C1): `store/cache_sync.py`

### 4.1 Signature

```python
def sync_paths(relpaths: Iterable[str], *,
               mode: Literal["explicit", "write"],
               embed: bool = True,
               dry_run: bool = False,
               verify: bool = False,
               renamed: Mapping[str, str] = {},
               deleted: Iterable[str] = (),
               root: Path | None = None) -> SyncReport: ...
```

- `relpaths` are store-relative and `/`-separated. The CLI and the API convert
  what they were given (section 8). Duplicates are coalesced before anything is
  read.
- `mode="explicit"` serves the CLI, the API and a script's `collecting()`
  exit. The caller has declared a batch complete, so every hot kind is rebuilt
  now. `mode="write"` serves the write-through queue (section 5.4). A hook
  whose policy is `on_write="explicit"` is skipped there, and reported as
  deferred.
- `embed=False` runs every local phase, reports the network work it left as
  `deferred`, and sends nothing.
- `dry_run=True` validates, classifies and plans. It computes projection texts
  and vector-cache hits, which are local. It builds, stores, embeds and bumps
  nothing.
- `verify=True`: see section 7.7.
- `renamed` maps an old path to a new one (section 7.5). `deleted` names paths
  the caller removed (section 7.4).
- `root` defaults to `paths.home()`, resolved **once** at entry and pinned for
  the batch. Every path is built from it, and the batch stops, reported as
  `root_moved`, if `paths.home()` stops naming it. That is the maintenance
  runs' rule (`run.root`, CLAUDE.md "Detached runs"), for the same reason: a
  data-dir move during a batch must not warm one tree with another tree's
  bytes.

It returns a `SyncReport` (section 9). It never raises for one path's
failure. It raises only for a programming error, such as an unknown mode.

### 4.2 Threading and the lane

- It is synchronous, and it does file and network I/O, so it is never called
  on the event loop's thread. The write-through queue and the API run reach it
  through `anyio.to_thread.run_sync`.
- In one process, at most one batch runs at a time. An in-process lock, the
  **sync lane**, wraps the rebuild and re-embed phases. Without it, an API run
  and the write-through queue could both embed the same new text in the same
  minute. Content-addressing makes the second store harmless, but the second
  request is still spend. The lane is taken with a timeout: a write-through
  batch that cannot take it re-queues its paths, and an explicit batch waits.
- Across processes (the CLI beside a running server) there is no lane. Both
  may compute the same artifact, which is idempotent. Both may also embed the
  same text. Section 6.6 narrows that by checking the vector cache again just
  before each request is sent, but does not close it. This is the residual
  `docs/store-guarantees.md` already documents for two processes ("A second
  process on the same store").

### 4.3 No lock on records

Sync never writes a record. The exception is the write token in section 10,
which `revision.bump` writes on its own terms (`revision.py:238`). Sync reads
through 03-C4, which reads bytes once and hashes what it read, and
`store.atomic` guarantees a reader sees the whole old file or the whole new
one. So sync takes no campaign lock, holds no run exclusion key, and cannot
refuse or delay a turn.

That is also why `store/cache_sync.py` gets no entry in `store/locks.py`: it
has no `cid`-taking mutator. 03 section 13 makes the same argument for the
cache module, and `test_lock_domain_guard.py` rejects a phantom entry.

### 4.4 The racy window

An agent typically syncs seconds after its last edit. By 03 section 5 rule 2,
a file whose mtime or ctime is younger than `PERSIST_WINDOW` at the start of
the read may not get a `sources` row. 03-C5 makes that harmless: an artifact
is keyed by the hash of the bytes actually read, so it may be stored at once.
Therefore:

- sync always stores the artifacts it builds;
- sync asks 03-C4 to record a `sources` row, and accepts that 03-C4 declines
  inside the window;
- the server's first read of that path then costs one read and one hash, and
  finds the artifact. It never recomputes.

The write-through queue (section 5.4) runs after a quiet period far longer than
`PERSIST_WINDOW`, so its batches normally record the row as well.

## 5. Covered by default: the write set (05-C4) and the write-through queue

### 5.1 The problem

05-C1 is "called by Grimoire's writers". Read literally, that means a call
after each of the two hundred or so `atomic` call sites, and the activity
middleware's docstring records what happens to a list like that: it does not
converge. Three other shapes were considered and rejected:

- **The cache inside `store.atomic`.** 03 section 5 rule 4 and 03's non-goals
  forbid it: `atomic` stays free of SQLite, and a write may not record its own
  `sources` row. It would also put a database transaction inside every
  campaign-lock hold that writes a record.
- **Scope scans at the request boundary.** The middleware knows a `cid` or a
  `wid`, not paths. Turning that into a sync means listing and stating the
  whole campaign after every mutating request. On a played campaign the
  transcripts make that the most expensive tree in the store. It also misses
  every write a detached run makes.
- **Explicit calls in the writers that feed hot kinds.** Which writers those
  are changes every time a kind is added. That is the convergence problem
  again, with a shorter list.

### 5.2 The write set: `store/writeset.py` (05-C4)

The write set is a leaf module, importing nothing from `grimoire`. It records
which record paths were published inside a collection scope:

```python
@contextmanager
def collecting() -> Iterator[set[Path]]: ...   # opens a scope; yields its set
def note(path: Path) -> None: ...              # adds to every open scope; no-op when none
```

- The scope is a `contextvars.ContextVar` holding a tuple of mutable sets.
  `note` adds the absolute path to each set and does nothing else: no I/O, no
  stat, and no import of the cache. With no scope open (a test, a script that
  did not ask, Android's startup) it costs one context-variable read.
- `store.atomic`'s four publishing functions call `writeset.note(path)` once
  the replace or the append has succeeded. That is the only change to
  `atomic`, and it is the line a new guard holds (section 12).
- Sets are mutable and shared by reference. Both `anyio.to_thread.run_sync`
  and Starlette's `run_in_threadpool` run the callable in a copy of the current
  context, so a write made in a worker thread started either way lands in the
  scope that was open when the thread started. A raw `threading.Thread` does
  not copy the context, so its writes are not collected. Nothing in the
  package does that on a write path today, and the plan confirms it with a
  search.

This is not "hooking `store.atomic`" in the sense 03 forbids. `atomic` gains no
SQLite, no import of the cache, no extra I/O, no `sources` row, and no
behaviour when nobody is collecting. It reports a fact (this path was
published) to whoever asked.

The write set can land on its own, before 03. If 04's plan wants to warm more
than the turn's scene (04-C2a's `warm_scene`), this is the slice to take first.

### 5.3 Where scopes are opened

| Boundary | How | Why there |
|---|---|---|
| Every HTTP request | `_WriteSetCollector`, a raw ASGI middleware in `main.py`, installed beside `_CampaignActivityStamp` and raw for the same reason (it stays out of the exception path). It opens a scope, awaits the app, and in a `finally` hands a non-empty set to the write-through queue. | Every route, every method, success or failure. A route that wrote and then failed still wrote: the argument `_CampaignActivityStamp` makes for the token on its exception path. A streamed response has finished streaming by the time `await self.app(...)` returns. |
| Every detached run | `runner._guarded` and `runner._guarded_thread` open a scope around the producer or the work, and hand the set over at the terminal point. | A run outlives its request, and its writes happen in the lifespan's context, which the request's scope never sees. `runner.start` and `runner.start_thread` are the only two doors. |
| A script | `cache_sync.collecting()` (section 5.5). | Scripts have no app. |

Not covered, by design, and rebuilt lazily instead:

- the background inference migration started at startup (`main.start`), which
  is neither a request nor a run;
- a write in a thread that did not copy its context;
- **deletes and renames**, which do not go through `atomic`. They need no
  warm-up, since nothing about a deleted path is worth rebuilding, and an
  explicit sync names them (section 7.4);
- another process's writes.

### 5.4 The write-through queue

The middleware and the runner hand their sets to a queue on the app,
`app.state.cache_warm`, installed by `runner.install` beside the run registry.
It is **not** a module global, for the reason the registry is not one: a
`TestClient` builds an app per test (CLAUDE.md, "Detached runs").

- **Debounced per path.** Each path carries the monotonic time of its latest
  write, and is taken only once it has been quiet for `WRITE_QUIET_S`. The
  constant is justified structurally. It is longer than the span of one
  logical edit: a turn writes the player's post at send and the reply when
  generation ends, and an editor save may write several files. So a turn's
  writes coalesce, and a scene being played is warmed when play pauses rather
  than after every turn. That keeps a growing transcript from being
  re-derived, and its newest passage re-embedded, once per turn. The value, on
  the order of a minute, is tuned later.
- **Root-pinned.** Each entry carries the `paths.home()` it was collected
  under. The worker drops an entry whose root is no longer the store's root,
  and `PUT /config/data-dir` clears the queue beside
  `runs.drop_pending_touched` (`routes/config.py:460`).
- **Bounded.** The queue holds at most `MAX_PENDING` paths. Past that, new
  paths are dropped, with one log line per process, and are rebuilt lazily.
  The bound is structural (the queue is memory the server holds while idle),
  and its value is tuned later.
- **Not a run.** It holds no exclusion key, creates no run record, never
  notifies, and is invisible to `runs_in_flight`, so it never makes
  `PUT /config/data-dir` answer 409. A queue that blocked a data-dir move for a
  minute after every edit would be a nuisance with no benefit, since the queue
  is safe to drop.
- **One worker.** A task in the lifespan's task group wakes on a timer, takes
  the quiet paths, and filters out paths under `.cache/`, `backups/` and
  `logs/`. No kind reads those, so the filter only saves lookups. It then runs
  `sync_paths(paths, mode="write")` in a worker thread, under the sync lane.
  That is the vehicle 08 section 9 asks for: after the response, outside every
  lock, never on the event loop.
- **Failures are swallowed**, with one log line per failure kind per process.
  Nobody is waiting for a write-through warm-up, so there is nobody to tell,
  and a warm-up that fails costs a lazy rebuild. `streaming._fire_follow_up`
  swallows its failures for the same reason.
- **Shutdown** cancels the worker. Pending paths are lost and rebuilt lazily.
- **Off switch.** `GRIMOIRE_CACHE_WARM=0` disables the queue. The collector
  still runs, at the cost of a set. `tests/conftest.py` sets the switch, as it
  sets `GRIMOIRE_INFERENCE_AUTOMIGRATE=0`, so that no test sees a background
  warm-up it did not ask for. A test about the queue turns it back on. It is
  not a user setting.

### 5.5 Scripts: `cache_sync.collecting()`

```python
@contextmanager
def collecting(*, embed: bool = True, deleted: Iterable[str] = ()) -> Iterator[Collected]: ...
```

It opens a write-set scope. On a clean exit it runs
`sync_paths(collected, mode="explicit", embed=embed, deleted=deleted)`
synchronously, and leaves the report on the yielded object for the script to
print. On an exception it syncs what was written anyway, because a half-applied
batch still changed those files, and then re-raises.

`backend/scripts/create_world.py` and `ingest_scene.py` wrap their apply step
in it. For a brand-new world nothing is hot, so the sync is a no-op. An ingest
into a campaign whose search corpus is hot is a different matter. 06's skill
tells an agent that writes through `grimoire.store` from Python to do the same,
and to pass what it deleted as `deleted=`, since deletes are not in the write
set.

## 6. The eager rebuild policy (05-C3)

### 6.1 What "hot" means

A path is hot for a kind when 03's `materialized` record has a row
`(path, kind)`. Nothing else counts. The existence of an artifact keyed by the
path's old bytes does not count, because that table is never enumerated (03
section 9), and neither does a guess from the file's directory. A path with no
rows is **cold**: sync does nothing for it and says so. That is what stops
sync from pre-building the whole library, or embedding content nobody
embedded.

`materialized` is read only for paths sync already holds, from its arguments
or from a live listing. That is 03 section 4's rule: "it never answers which
paths exist".

### 6.2 Hooks: each owner rebuilds its own kinds

Three owners already need to rebuild something after an edit: 03's per-file
kinds, 04's overview kinds (`overview.warm_paths`, 04-C2a) and 08's
SearchDocuments (`affected`, `rebuild_documents` and `reembed`, 08-C2c). 05
also adds a fourth, for the three existing vector producers (section 6.4).
Each owner knows what its kinds mean, and 05 must not grow a second copy of
that knowledge. So 05 defines a protocol, and each owner registers one hook:

```python
class WarmHook(Protocol):
    name: str                          # "files", "overview", "searchdocs", "vectors"
    on_write: Literal["quiet", "explicit"]
    def kinds(self) -> frozenset[str]: ...          # materialized kinds this hook owns
    def local(self, ctx: WarmContext, hot: Mapping[str, frozenset[str]]) -> LocalResult: ...
    def network(self, ctx: WarmContext, plan: EmbedPlan) -> NetworkResult: ...
```

- `hot` maps each hot path to the kinds of this hook that were materialized
  from it. A hook never sees another hook's rows.
- `local` rebuilds and stores artifacts and **touches no network**. It returns
  per-path outcomes and an `EmbedPlan`: the projection texts that would need a
  vector, which hits the vector cache already has, and how each text is
  claimed (campaign, or none).
- `network` sends the plan's misses and saves the vectors. It is never called
  in a dry run or with `embed=False`, and never on a request path or under a
  lock, which is 08 section 9's requirement on 05.
- Hooks run in registration order, which is fixed: `files` (03's single-file
  kinds), then `overview` (04's composites, which hit the file artifacts just
  rebuilt), then `searchdocs` (08), then `vectors` (the existing producers).
  All the local phases run before any network phase.
- Within a hook, instances are coalesced across the batch. Forty entity edits
  in one world warm that world's card once, because `warm_paths` receives the
  forty paths in one call.
- A materialized kind that no hook claims is never rebuilt, and is reported as
  `lazy`.

The `files` hook is 05's own. For each materialized kind that 03's registry
marks as single-file, its local phase calls 03's `derive(kind, path)`, which is
the call a reader makes, so a warm-up and a read cannot disagree about a key.
It has no network phase.

Hook outcomes per path and kind are `hit` (the artifact for the current bytes
already existed), `built`, `skipped(reason)` or `failed(error_class)`. Sync
catches an exception from a hook, records the exception's class name and
nothing of its message (section 9), and carries on with the next hook.

### 6.3 Parse failures

A hook computes what its reader would compute. If a file is malformed in a way
its reader rejects, the hook fails exactly as that reader would, and sync
reports `failed` for that kind with the exception class. It never keeps the
previous version as current. A content-keyed cache has no "previous version",
only a key for the new bytes that now has no artifact, so the reader's own
failure semantics apply on the next read. Frontmatter is "not validated, by
design" (`docs/store-guarantees.md`, "What is not promised"), so for most
Markdown records this cannot happen. A JSON record that no longer parses is
the realistic case.

Sync never repairs a file, and never writes one to make a hook pass.

### 6.4 The existing vector producers, and what 05 asks of 03-C3

03 section 4 names a vector row `vector:<space>`, and 08 writes
`(scenes/<sid>.md, vector:<space key>)` for its scene documents (08 section
8.2). That is not enough to rebuild a vector once more than one projection of a
file is embedded, and in this repository more than one usually is:

- A world lore entry is embedded by recall (`entry_text`) and by library search
  (its passages).
- A scene transcript is embedded by library search (its passages) and, once 08
  lands, by 08 (its SearchDocument).

Under a bare `vector:<space>`, 08's hook would read a search-passage row as
proof that the scene's document was embedded, and the reverse. So this spec
asks for two refinements of 03-C3, and degrades without them:

1. **The kind names the projection**: `vector:<projection>:<space-digest>`.
   `<projection>` is a registry kind whose compute produces the embedded text
   from the path's bytes. `<space-digest>` is a short hash of
   `embed_space.endpoint()["space"]`. The raw space id joins provider, rev and
   model with NUL bytes, which do not belong in a kind name. 08 adopts the same
   form for its scene documents (Open question 4). Without this refinement, no
   vector kind can be warmed, and the network half of 05-C3 waits.
2. **An optional `instance` column**: small, opaque JSON recorded beside the
   row, for example `{"campaign": "saltmarch", "entity": ["lore", "pact"]}`.
   It lets a hook rebuild an overlaid entry for the campaign that read it, and
   claim the embedding for that campaign (section 6.6). Without it, hooks
   derive the instance from the path alone: a world path is warmed as the
   world's entry and left unattributed, and a campaign path as that campaign's.

Both are read only by path, so neither changes 03 section 9's query-safety
rule.

**05 wires the three existing producers.** Nothing records a `vector:` row
today (section 1.1), and 03 wires none of the producers. So 05's `vectors`
hook covers lore recall, the art catalog and library semantic search. For each
one, 05 adds:

- a projection kind whose compute is the producer's own text function
  (`semantic.entry_text`, the art catalog's candidate text, and `semsearch`'s
  `passages` over the document text `search.walk` yields). The hook and the
  producer then cannot disagree about what was embedded;
- a `materialized` row written beside each `vectors.save`, in 03's write batch
  (off the event loop, and outside any campaign lock), naming the source path
  and, where the producer knows it, the instance;
- for recall and art, the source path carried on the candidate dict from the
  place the candidate is built. The plan finds those places; each candidate is
  already built from a record the producer read.

`continuity-similarity` is left out. It embeds rows of continuity ledgers
inside absorb and the reconcile sweep, both of which recompute what they need
under their own budgets, and its rows are not files an agent edits directly.

### 6.5 What gets embedded

For each `vector:` row on a hot path, the owning hook's local phase:

1. **Checks the space.** It compares the row's space digest with the digest of
   `embed_space.endpoint()`, read once per batch.
   - If they differ, the row belongs to a space the Embedding role has moved
     away from. Re-embedding under the new space would be embedding content
     never embedded there, which is exactly the spend `confirm_embedding`
     exists to ask about (CLAUDE.md, "A settings surface never spends
     unasked"). This is reported `stale_space`, and nothing is sent.
   - If embedding is off (`endpoint()` is `None`, or a known `no` has turned it
     off), this is reported `embedding_off`, and nothing is sent.
2. **Computes the projection text** from the current bytes, through the
   projection kind.
3. **Looks the text up** with `vectors.load(space, texts)`. Text already held
   costs nothing. Because vectors are keyed by text, a transcript whose last
   passage changed re-embeds that passage and no other, and a rename whose
   text is unchanged re-embeds nothing.
4. **Puts only the misses** in the `EmbedPlan`, each with its claim.

So the spend is bounded by the projection-text diff of paths whose
projections were already embedded in the current space. It is never bounded
by the size of whatever surrounds the edit.

### 6.6 How it is sent

- **Task.** Each hook embeds under its producer's own task: `semantic-recall`,
  `art-catalog`, `semantic-search`, and 08's `history-index`. No `cache-sync`
  task is added. The task is the axis the error store and the Costs page
  aggregate a feature's spend on, and recall's cost is recall's whether it is
  paid on a turn or ahead of one. 08 already embeds its rebuilds under its own
  task. What sync itself spent is in its report (section 9). Open question 3
  records the alternative.
- **Door.** With 01h-C5, the `vectors` hook calls
  `embed_groups_sync(task, attribute(claims), space=..., client=...)`. A text
  claimed by one campaign is charged to that campaign. A text claimed by
  several campaigns, or by none (a world, the library, an image object), goes
  to the unattributed group and is embedded once (01h section 7). No request
  spans campaigns. Without 01h-C5, the hook groups claims the same way and
  makes one `inference.embed.embed_sync(task, texts, space=..., client=...,
  campaign=...)` call per group. Either way, `space` is the one read at the
  start of the batch, and is never re-resolved per call (CLAUDE.md, "Adding an
  embedding call site?").
- **No scene.** A warm-up is not part of playing a scene. A scene's own totals
  stay "the number that is always right" (CLAUDE.md, Costs).
- **Chunks, saved as they land.** The client splits a call into
  `embeddings.BATCH` requests. The hook saves each group's vectors before the
  next group is sent. The `WARM_LIMIT` comment in `semantic.py` records why:
  in one large call, a rate limit late in the run otherwise "raises before a
  single vector is saved", and the retry repeats all of it.
- **Checked again just before sending.** Immediately before each group is
  sent, its texts are looked up again, and any that have arrived meanwhile
  (from another process, or from a turn) are dropped from it.
- **Stop on a connection-wide failure.** 01h-C5 stops the run after `auth`,
  `missing_key`, `rate_limit`, `network` or a deadline, and returns the later
  groups as `not_sent`, filing nothing. The fallback path does the same. The
  unsent texts are reported with the failure's kind.
- **Deadline.** The client's own per-request timeout, not a caller budget. A
  slow provider cutting a request is therefore a provider failure, recorded at
  `Meter.done` with its kind and status only.
- **Input type.** Once 01h-C1 lands, sync passes the document type. It never
  embeds a query.

### 6.7 Does re-embedding need confirmation? No, and why

CLAUDE.md's rule is that "a settings surface never spends unasked". Three
routes refuse without a yes today: the model test, a generating health check,
and any change that moves the Embedding role's vector space. The third looks
most like sync, and the argument has to address that resemblance.
Re-embedding after an edit does not need the question, for four reasons:

1. **It never moves the space.** Sync embeds only under the current space, and
   only for rows already in it (section 6.5, step 1). The confirmation exists
   because moving the space re-embeds a library, and sync can never do that.
2. **The content was already opted in.** A `vector:` row exists only because a
   producer embedded that projection in this space. The user turned on recall,
   art ranking, semantic search or history retrieval, and set the Embedding
   role. Sync embeds the *next version* of text the user already chose to
   embed.
3. **Lazy would spend the same.** The next read that needs the vector embeds
   the same text at the same price, on the turn path. Sync moves that spend
   earlier. It spends *more* than lazy only when a version is synced and then
   superseded before any read needed it, or when nothing ever reads it again.
   Both are bounded by the paths the caller named, and the write-through
   queue's quiet period removes the common case (a scene re-embedded every
   turn).
4. **The request is explicit.** The CLI and the API are run by the user, or by
   an agent acting for them, the way sending a turn is (CLAUDE.md: "Play is not
   gated -- sending a turn *is* the request"). The write-through queue acts on
   the user's own edit in the app.

What replaces a confirmation is visibility and an opt-out. `--dry-run` reports
how many texts would be sent before anything is sent. `--no-embed` (or
`embed: false`) rebuilds everything else and leaves the vectors to lazy. The
report counts the texts and requests sent, and every request files a ledger
row. Open question 5 asks whether a very large explicit batch should still
want a yes.

## 7. The CLI and the API (05-C2)

### 7.1 The command

```
python -m grimoire.cache sync [PATH ...]
        [--campaign CID ...] [--world WID ...] [--all]
        [--renamed OLD=NEW ...] [--deleted PATH ...]
        [--no-embed] [--dry-run] [--verify] [--json] [--quiet]
```

From a checkout, with the backend venv, in the two forms CLAUDE.md gives:

```
PYTHONPATH=backend/src backend/.venv/bin/python -m grimoire.cache sync ...          # macOS/Linux
PYTHONPATH=backend/src backend/.venv/Scripts/python.exe -m grimoire.cache sync ...  # Windows (Git Bash)
```

- **The module.** `backend/src/grimoire/cache.py`, beside `where.py`, with a
  `main()` and an argparse parser built by `build_parser()`. 06's drift test
  reads that parser. `sync` is a subcommand, so that a later admin surface can
  add `status` or `doctor` without renaming anything. No console script is
  added (Open question 7).
- **Arguments.** At least one of `PATH`, `--campaign`, `--world` or `--all` is
  required, and they combine: the batch is their union. A `PATH` may be
  store-relative or absolute. An absolute path must be inside the store root
  (section 8). A directory expands to the files under it.
- **The root.** It is `paths.home()`, the resolver every other entry point
  uses. There is no `--root`: a different root is `GRIMOIRE_HOME`, the same
  override as everywhere else. The report states the root and where it came
  from (`paths.data_dir_info()`), as `grimoire.where` does, because an agent
  whose shell and server disagree about `GRIMOIRE_HOME` would otherwise sync a
  tree nobody reads.
- **Logging.** The command calls `logs.install()` first. An embed failure's
  meter row and its log line then land in the store's own `logs/` file,
  through the one writer (CLAUDE.md, Observability), exactly as the server's
  would. A second process appending to the month's log or ledger is covered by
  `atomic.append_line`'s `O_APPEND` guarantee (`atomic.py:231`).
- **Output encoding.** It reconfigures stdout with `errors="backslashreplace"`,
  as `where.main` does, so that a path the console cannot spell does not turn a
  finished sync into a traceback.

**Exit status.**

- `0`: no path is `failed` or `refused`. Cache off, cold paths and deferred
  vectors all count as success.
- `1`: some path is `failed` or `refused`.
- `2`: a usage error.
- `3`: the root cannot be used at all (missing, not a directory, or
  unreadable).

An agent can branch on the status without parsing the report.

### 7.2 Explicit paths

Each path goes through section 8's validation, then section 3.3's steps. A path
that exists is rebuilt or reported cold. A path that does not exist is
reported `deleted`, and bumps its campaign's token (section 10). `--deleted
PATH` says the same thing explicitly. It is how a script hands over a delete
it made through a store function, since deletes are not in the write set
(section 5.3).

### 7.3 Scoped modes: `--campaign`, `--world`, `--all`

A scope expands to the files that may have changed. They are found from a live
listing, never from the cache:

1. **Validate the id** with `paths.safe_id` (`paths.py:243`), the rule
   `test_path_guard_store.py` holds for a caller-supplied id joined onto a
   path. `--campaign CID` is `campaigns/<cid>/`, `--world WID` is
   `worlds/<wid>/`, and `--all` is every syncable top-level directory
   (section 8).
2. **Walk the scope**, skipping `atomic` temp files (`atomic.is_write_temp`,
   `atomic.py:162`) and anything section 8 refuses, and stat every file.
3. **Look up the rows.** In one batched read of 03's tables, keyed by the
   listed paths, fetch each file's `materialized` rows and its `sources` row.
   That is within 03 section 9's rule, since the keys came from the
   filesystem.
4. **Pick the candidates.** A file is a candidate when it has `materialized`
   rows and either its stamp does not match its `sources` row or it has no
   row. A file with no `materialized` rows is cold, and is skipped without
   being read. A file whose stamp matches is current by 03's trust rule, and
   is also skipped without being read.
5. **Sync the candidates** through `sync_paths`.

The `sources` table serves only as a hint about which files to read, never as
an answer. A candidate is still read and hashed through 03-C4, and a
non-candidate is one that 03's own read path would also have trusted. A scoped
sync therefore costs a stat per file, plus a read for each hot file whose
stamp moved.

To keep the report bounded, a scoped sync lists individually only the paths
whose status is not `current` or `cold`, and counts the rest.

A scoped sync over a deleted root, where the directory is gone, is section
7.6.

### 7.4 Deletes

A deleted path needs no rebuild: 03-C2 already makes everything derived from it
unreachable. Sync:

- bumps the path's campaign's write token, if it is a campaign path
  (section 10);
- leaves the path's `materialized` rows alone. They name a path, not content,
  and 03's least-recently-used eviction removes them. Dropping them at once
  would make a delete-then-recreate (an editor that saves by replacing the
  file) go cold, for no benefit.

### 7.5 Renames

A rename is a delete of the old path plus a new file at the new one, and the
new path has no `materialized` rows. Sync never infers a rename, not even from
identical content hashes, because a renamed record may be a different record.
That is the draft's rule, and it is right. The caller may state one:

- `--renamed OLD=NEW` (and `renamed=` on the primitive) copies OLD's
  `materialized` kinds, without their instances, to NEW before NEW is
  classified, so NEW is rebuilt as hot. Each hook derives NEW's instance from
  NEW's path. OLD is then handled as a delete.
- A wrong statement costs some compute and, at worst, embeddings of NEW's
  projections, which are new content and are reported like any other spend.
  Identical text costs nothing, since vectors are keyed by text.

### 7.6 A deleted world or campaign root

An argument may be a world or campaign root (`worlds/<wid>` or
`campaigns/<cid>`, from a path or a scope flag) whose directory no longer
exists. In that case the store has lost a whole world or campaign outside the
app. When the app deletes one, 03 section 12 purges the cache, so that derived
private text does not outlive it, either in this device's file or in other
devices' synced copies. A delete made by hand deserves the same. So sync runs
03's purge (the marker, plus this device's purge) and reports `purged`. That
needs 03 to expose the purge as a callable (Open question 2). Until it does,
sync reports the root as deleted and says the purge did not run.

### 7.7 Verify

`--verify` (and `verify=True`) re-runs each rebuilt kind for each refreshed or
current path with the compiled layer bypassed for that call, and compares the
result with the stored artifact. For a vector kind, it checks that every
projection text now has a vector. A mismatch is reported `verify_failed`,
exits `1`, and writes one log line, because it is a bug in a kind (a key that
does not cover one of its inputs), never a user error. Verify roughly doubles
the compute and sends nothing. It is the production form of 03 section 14's
equivalence test, for an agent that wants evidence rather than a status.

### 7.8 The API: `POST /api/cache/sync`

The API serves what the CLI cannot: Android, a client that only speaks HTTP,
and a tab that should forget its remembered reads when the sync ends (section
3.2).

- **Route.** `routes/cache.py` defines `post_cache_sync`, a `def` handler. A
  route that reserves a run may not be `async def` (CLAUDE.md, "Detached
  runs").
- **Body.** A plain `BaseModel` with plain fields, v1/v2-agnostic and dumped
  through `routes.common._dump`: `paths: list[str]`, `campaigns: list[str]`,
  `worlds: list[str]`, `all_records: bool`, `renamed: list[dict]`,
  `deleted: list[str]`, `embed: bool = True`, `dry_run: bool = False` and
  `verify: bool = False`. At least one selector is required; otherwise the
  route answers 400.
- **Answer.** 202, with the run payload. The run is the `background` class,
  kind `cache-sync`, on `runs.GLOBAL_SUBJECT` (`runs.py:1349`), reserved with
  `single_live=True` (`start_or_existing`, `runs.py:497`). A second request
  while one is live is handed the live run, and its paths are left for that run
  to take after its pass, through the registry's pending set (`pend_touched`,
  `runs.py:666`). This is the same arrangement a campaign's reconcile sweep
  uses. The pending set is keyed by subject alone today, and the global
  subject would share it between kinds, so the plan keys it by subject and
  kind.
- **Progress and result.** The run appends progress frames (counts only) and a
  final frame carrying the report. It is reached through the global subject's
  existing four run routes: list, poll, stream and cancel. Cancel stops before
  the next path or group, and the report says what ran.
- **Exclusion.** None, as for every `background` run: it neither holds a scene
  nor is refused by one. A live run does make `PUT /config/data-dir` answer
  409, like any run. That is correct, since a sync in flight is pinned to the
  root it started on.
- **Not a write.** The path is not under `/api/campaigns/`, so the activity
  middleware stamps nothing. Sync bumps the write tokens (section 10) itself,
  where it writes. CLAUDE.md states that rule for "Everything a *detached* run
  writes".
- **Docs.** CLAUDE.md's "Detached runs" section counts the handlers that start
  runs ("Thirty-two handlers") and lists them by class. This change adds
  `post_cache_sync` to the `background` bullet and moves the count in the same
  commit. `test_claude_md_names_every_run_class` already passes, since the
  class exists.

### 7.9 Android

The Android app packages `backend/src` verbatim (CLAUDE.md, Android). The user
has no shell there, and nothing outside the app can write its private storage
except `scripts/grimoire_sync.py` over adb. So on Android:

- `grimoire/cache.py` ships and is never run. It uses only `argparse` and the
  store, so it costs nothing.
- The write-through queue runs as it does on desktop, if 03's cache is on
  there. 03's cache is off on Android until 03 confirms that the APK ships
  `sqlite3` and the `BUILD` stamp (03 section 13). While it is off, every sync
  reports `cache: off` and does nothing else, successfully.
- `POST /api/cache/sync` is the explicit door. A PC-side tool could reach it
  through `adb forward`. Nothing does today, and lazy rebuilding covers a file
  `grimoire_sync.py` pushed (Open question 6).

## 8. Path validation

A path given to sync is untrusted input to a process that will read and parse
the file it names. So it is held to a closed rule:

1. **Inside the root.** Relative paths are joined onto the pinned root.
   Absolute paths must be within it, compared after `os.path.normcase` and
   `os.path.normpath`, and never through `resolve()`, which follows links. A
   `..` component that escapes the root is refused (`outside_store`).
2. **No links below the root.** Every component from the root down is checked
   with `lstat`, and a symlink or a Windows junction (a reparse point) refuses
   the path (`link`). The root itself may be a link, since where the library
   lives is the user's choice. This is the rule 03 applies to `.cache`, and the
   one image GC applies to its own folders (`image_gc._store_blocks`,
   `image_gc.py:552`), for the same reason. Following a link would have the
   server read, hash and parse a file outside the library, and a status of
   "parsed" or "failed" is an answer about that file.
3. **Syncable roots only.** The first component must be one of a closed set of
   record directories: `worlds`, `campaigns`, `assets`, `modules`,
   `calendars`, `climates` and `styles`. Anything else is refused
   (`not_syncable`):
   - `.cache` and `backups` are derived (`external.SKIP_AT_ROOT`,
     `external.py:65`);
   - `logs` and `usage` are ledgers;
   - `llm_connections` holds keys;
   - the staging directories are transient;
   - top-level files such as `config.md` enter keys as `params`, never as
     paths.

   A closed set fails safe. A record directory added later is refused until
   someone adds it, and a refused path is only a lazy one.
4. **Not a write temp.** Names that `atomic.is_write_temp` matches are refused
   (`temp_file`).
5. **A file, or a directory to expand.** Anything else, such as a socket or a
   device, is refused (`not_a_file`).
6. **Spelling.** Ids from `--campaign` and `--world` go through
   `paths.safe_id`. A path given in a different case on a case-insensitive
   volume is accepted as the OS resolves it, and reported as given.
7. **Bounded.** A batch accepts at most `MAX_PATHS` explicit paths, and expands
   a directory to at most that many files. Past that, the rest is reported as
   `too_many`, and the report says it was truncated. "Found nothing" and
   "stopped looking" must not read the same; that is `external.py`'s rule for
   its own walk.

## 9. Output and privacy

The report never contains record content. That rules out file bytes, parsed
field values, projection text, a parser's or a provider's error message, and
any URL or key. A YAML or JSON error can quote the line it failed on, and an
embeddings error body can echo its input. The report contains:

- the root, and where it came from;
- `cache`: `on`, or `off` with 03's reason;
- per path (every path for explicit paths; only the notable ones for a scope):
  - the path as given;
  - a `status`: `refreshed`, `current`, `cold`, `deleted`, `refused`, `failed`
    or `verify_failed`;
  - a `reason`, for `refused`;
  - per kind, an outcome: `built`, `hit`, `lazy`, `deferred`, `skipped` or
    `failed:<ExceptionClass>`;
- per hook: what it rebuilt, in counts and kind names (04 asks for this, so
  the summary can say what it refreshed without naming content);
- per batch:
  - counts per status;
  - tokens bumped, and whether a purge ran;
  - embedding totals: texts sent, requests made, texts already held,
    `stale_space`, `embedding_off`, `deferred`, and the failure kind of any
    group not sent;
- `truncated`, when a bound was hit.

Paths and ids are printed because the caller supplied them and needs them to
act. They are names, and CLAUDE.md's privacy rule about names concerns what
gets committed to this repository. 06's skill repeats that boundary. The log
gets less: one INFO line per batch, with counts and the mode and no paths,
because the log is a file a user may hand to someone else (CLAUDE.md,
Observability).

`--json` prints the report as one JSON object with a `version` field. The text
form is a short table of counts, then one line per listed path. `--quiet`
prints only the counts.

## 10. The write token

`revision.py:110` names hand edits as something the token cannot see, and calls
an unmoved token "evidence and not proof". An explicit sync is the first moment
the app is *told* about a hand edit, so it uses it. Every campaign that a
non-refused path in the batch belongs to (`campaigns/<cid>/...`) gets
`revision.bump(cid)` once per batch, whether the path was refreshed, current,
cold or deleted.

- That over-bumps: a path sync found current still bumps. Over-bumping is the
  direction the token is deliberately wrong in. It costs someone a re-price,
  never a lost write (the reasoning on `_CampaignActivityStamp`'s exception
  path).
- It never bumps for a world path. The token records writes to the campaign,
  and `revision.py` already declines to fan a world edit out to every
  campaign.
- The write-through queue does not bump. Grimoire's own writes were already
  stamped by the middleware, or by the run that wrote them.
- `--dry-run` bumps nothing.

## 11. Contract

**05-C1. The in-process sync primitive, reached by Grimoire's writers.**

- *Inputs:* store-relative paths; `mode` (`explicit` or `write`); `embed`,
  `dry_run` and `verify`; optional rename and delete lists; a root, which
  defaults to `paths.home()` read once.
- *Outputs:* a `SyncReport` (section 9).
- *Guarantees:*
  - it writes no record except the campaign write tokens of section 10;
  - it takes no campaign lock and holds no run exclusion;
  - it validates every path (section 8);
  - it stores artifacts through 03, and never a `sources` row inside the
    window (03-C5);
  - it runs one batch at a time per process (the sync lane);
  - it is pinned to the root it started on;
  - a network phase never runs on a request path, on the event loop or under a
    lock.
- *Coverage:* every HTTP request and every detached run hands its write set to
  the write-through queue (05-C4). The queue runs this primitive in `write`
  mode once each path has been quiet for `WRITE_QUIET_S`. Scripts reach it
  through `cache_sync.collecting()`.
- *Failure:* it never raises for a path; a path's failure is a status. The
  write-through queue swallows failures and logs once per failure kind. The
  cache being off is a successful no-op.

**05-C2. `cache sync`: the CLI and the API.**

- *CLI:* `python -m grimoire.cache sync`, with `PATH...`, `--campaign`,
  `--world`, `--all`, `--renamed OLD=NEW`, `--deleted`, `--no-embed`,
  `--dry-run`, `--verify`, `--json` and `--quiet`. The parser is
  `grimoire.cache.build_parser()`. Exit status is 0, 1, 2 or 3, as section 7.1
  defines.
- *API:* `POST /api/cache/sync` answers 202 with a `background` run
  (`cache-sync`) on the global subject. The run is single-live, and later
  requests' paths are pended for the live run. The report is the run's final
  frame.
- *Batching:* duplicate paths are coalesced; instances are coalesced within a
  hook; embeddings are grouped by campaign (01h-C5); 03 batches the cache
  writes.
- *Summary:* holds no record content (section 9). The log line holds counts
  only.

**05-C3. Eager rebuild of materialized kinds, embedding only what was already
embedded.**

- A kind is rebuilt for a path only if `materialized` has `(path, kind)` and a
  registered hook owns that kind. A path with no rows is cold, and costs
  nothing.
- **Hooks** (`WarmHook`) run in the fixed order `files`, `overview` (04-C2a),
  `searchdocs` (08-C2c), then `vectors`. Every local phase, which touches no
  network, runs before any network phase.
- A vector row is re-embedded only:
  - in the current space;
  - for projection text the vector cache does not hold;
  - under the producer's own embed task;
  - grouped by campaign claim (section 6.6);
  - with no confirmation (section 6.7).

  `--no-embed` defers it, and `--dry-run` reports it.
- A hook whose `on_write` is `explicit` is skipped by the write-through queue.
- The three existing vector producers (recall, art and library search) record
  `materialized` rows with their projection named.

**05-C4 (new). The write set.**

- `store/writeset.py`, a leaf module, provides `collecting()` and
  `note(path)`.
- `store.atomic`'s four publishing functions call `note` after a successful
  publish, and do nothing else new.
- Scopes are opened by `_WriteSetCollector` (every HTTP request), by
  `runner._guarded` and `runner._guarded_thread` (every detached run), and by
  `cache_sync.collecting()` (scripts).
- It records absolute paths only. It never reads, stats or imports the cache,
  and it costs one context-variable read when no scope is open.
- It does not see deletes, renames, writes in a thread that did not copy its
  context, or another process's writes.

## 12. Interaction with repo rules

- **`store.atomic` and its guard.** `atomic` gains one call per publishing
  function, and no import beyond the leaf `writeset`. `test_atomic_guard.py`
  is unaffected, since nothing new writes a record outside `atomic`. A new
  guard, `test_writeset_guard.py`, holds that every public function in
  `atomic` that publishes bytes calls `writeset.note` on its success path, so a
  fifth writer added later is collected too. It goes in CONTRIBUTING.md's guard
  table (`test_contributing_names_every_guard_test` requires that), and it has
  no marker.
- **Import graph.** `writeset` imports nothing from `grimoire`, so
  `atomic -> writeset` adds no cycle. `cache_sync` imports 03's module,
  `embed_space`, `vectors`, `inference.embed`, `revision` and `paths`. It binds
  submodules inside `store/`, never names off a package
  (`test_import_guard.py`). The hooks of 04 and 08 are registered by their own
  packages at import, so `cache_sync` does not import `overview` or
  `searchdocs`. The routes import `cache_sync`; the store never imports the
  routes.
- **Paths.** Every path is built from `paths.home()` (`test_paths_guard.py`),
  and ids go through `paths.safe_id` (`test_path_guard_store.py`).
- **Locks.** None are taken, and there is no entry in `store/locks.py`
  (section 4.3). `revision.bump` is already classified: `store/locks.py` lists
  `store.revision` outside the domain, with its reason.
- **Metering and routing.** No embed task is added. Every request goes through
  `embed_groups_sync` (01h-C5) or `embed_sync`, under the producer's
  registered embed task, with a space from `embed_space.endpoint()`.
  `test_operation_guard.py` traces that, and `test_usage_guard.py` checks a
  meter's holder. A failure is recorded at `Meter.done`, with kind and status
  only.
- **Costs.** An endpoint that reports no price files unpriced rows, so the
  Costs totals read incomplete until the user sets rates, exactly as for
  recall today. An OpenRouter embedding reports its cost, which is spend and
  counts against the claiming campaign's budget. Campaign budgets only warn.
- **Detached runs.** There is one new `background` handler, `post_cache_sync`.
  The write-through queue is not a run. CLAUDE.md's handler count and its
  background bullet change with this.
- **Observability.** There is one writer: the CLI calls `logs.install()`. Sync
  logs a degradation once per kind per process, and one batch line with counts.
- **Write tokens.** See section 10. The API route sits outside
  `/api/campaigns/`, so the middleware does not stamp it.
- **pydantic v1/v2.** The request model uses plain fields only.
- **Android.** See section 7.9. No base dependency is added.
- **Privacy.** See section 9. Tests use the placeholder names (Seraphine, Mara,
  Winifred, Realm, Saltmarch) on `tmp_path` stores. No constant here is
  justified by a measurement of a real library.

## 13. Tests and acceptance

All tests run on `tmp_path` stores with the placeholder names and the cache
on. The embeddings client is faked with `llm_fakes.FakeEmbeddings`
(`llm_fakes.py:748`). `GRIMOIRE_CACHE_WARM=0` is set, except where a test
turns the queue on.

**The write set (05-C4).**

- Each of `write_text`, `write_bytes`, `append_line` and `streaming_write`
  notes its path in an open scope, and only after a successful publish. A write
  that raises notes nothing.
- Nested scopes both receive the path. With no scope open, nothing is recorded
  and nothing is spent.
- A write in `anyio.to_thread.run_sync`, and one in a `def` route's threadpool
  worker, both land in the request's scope.
- `test_writeset_guard.py` fails a new publishing function in `atomic` that
  does not call `note`.

**Write-through (05-C1).**

- A `PUT` that edits an entity in world Realm leads, after the quiet period
  (with an injected clock), to the entity's hot kinds being rebuilt and its
  recall vector re-embedded under `semantic-recall`.
- A route that writes and then raises still hands its paths to the queue.
- A detached turn's writes reach the queue at its terminal point.
- Repeated writes to one transcript inside the quiet period produce one
  warm-up.
- A data-dir move clears the queue, and an entry collected under the old root
  is never warmed against the new one.
- Past `MAX_PENDING`, the queue drops paths with one log line.
- A warm-up failure is swallowed and logged once.
- 08's `reembed` (a test hook standing in for it) is never called on a request
  thread, on the loop thread, or while a campaign lock is held.

**The policy (05-C3).**

- A path with no `materialized` rows is reported cold, and nothing is read or
  sent.
- Hooks run in order, and every local phase finishes before any network
  phase.
- Forty edited entities in Realm reach `overview.warm_paths` in one call.
- A vector row in a space other than the current one is `stale_space`, and
  sends nothing. With embedding off, it is `embedding_off`.
- A transcript whose last passage changed embeds one passage.
- A rename stated with `--renamed` whose text did not change embeds nothing.
- With two producers' rows on one path (recall and search on a lore entry),
  each hook rebuilds only its own projection.
- A connection-wide failure on the first group stops the rest, and the report
  names its kind.
- Texts claimed by Saltmarch and by another campaign file one row per
  campaign, and a text claimed by both is unattributed (with 01h-C5, or the
  fallback).
- The three producers record a `vector:<projection>:<space-digest>` row beside
  each `vectors.save`, and dropping the `materialized` table changes no
  producer's answer.

**The CLI and the API (05-C2).**

- Exit statuses 0, 1, 2 and 3 occur for the cases in section 7.1.
- `--dry-run` sends nothing, stores nothing and bumps nothing, and reports the
  texts it would send.
- `--no-embed` defers every vector and sends nothing.
- A scoped sync reads only hot files whose stamp moved (counted reads on an
  instrumented store), and lists only non-current paths.
- Every refusal in section 8 is reported with its reason:
  - a path outside the root;
  - a `..` escape;
  - a symlinked directory;
  - a junction (on Windows only);
  - paths under `.cache/`, `logs/` and `llm_connections/`;
  - a write temp;
  - an unsafe id;
  - a batch over `MAX_PATHS`.
- A deleted campaign root triggers 03's purge, once 03 exposes it.
- `--verify` reports a mismatch for a kind deliberately registered with a key
  that omits an input.
- The report never contains a body. A test syncs records whose bodies contain a
  sentinel string, including a malformed JSON record and a provider error
  whose body echoes its input. The sentinel must appear in neither the JSON
  report, the text output nor the log file.
- Two concurrent `POST /api/cache/sync` requests share one run, and the second
  request's paths are processed.
- `PUT /config/data-dir` answers 409 while an API sync run is live.
- An explicit sync of a campaign path bumps that campaign's token once per
  batch. A world path bumps none.
- The CLI, run in a subprocess against the test root while a test app serves
  the same root, leaves artifacts that the app's next read hits (an
  instrumented counter, 04-C3a).

**Acceptance.** An agent edits a hot lore entry in Realm, changes an image
description through `image_store.update`, and deletes an obsolete lore file,
then runs one `cache sync` naming the three paths. Afterwards:

- recall's next turn sends no embedding request for the edited entry;
- search finds the new description and not the old one;
- the Worlds page's next read is a hit for Realm's card;
- the report lists one `refreshed`, one `refreshed` and one `deleted`, and
  contains no body text.

## 14. Non-goals

- Making reads correct. 03 does that, and 04-C2b bounds first paint. A
  forgotten sync costs a cold read.
- Retiring anything in a client. 04 owns that.
- Inferring renames, or inferring record identity from content.
- Watching the filesystem (inotify, FSEvents, `ReadDirectoryChangesW`). A
  watcher is a second change-detection system with its own failure modes on
  synced and network volumes, and 03's stat-on-read already notices every
  change. It would add immediacy for edits nobody announced, which is lazy
  rebuilding's job.
- Warming anything cold, or pre-building the library.
- Re-embedding under a new space. That is the Embedding role's move, behind
  `confirm_embedding`.
- `continuity-similarity` vectors (section 6.4).
- Cache administration: status, doctor, GC and benchmarks.
- Validating or repairing authoritative files.
- Bumping write tokens for world edits, or from the write-through queue.
- Notifying another device. A synced store's other device notices by stat on
  its next read.

## 15. Open questions

1. **Does 04-C2's split change the checklist edge?** The checklist has
   05 depending on 04-C2. 04 split C2 into C2a (`warm_paths`, which 05 calls)
   and C2b (the client bound, which 05 inherits and does not call), and 04
   provides no server-side retirement. *Recommendation:* rewrite the edge as
   "05 <- 04-C2a (soft), 04-C2b (property)", and drop the bundle draft's
   retirement step, as this spec does.
2. **03's purge as a callable (missing edge).** Section 7.6 needs 03 section
   12's world and campaign purge exposed as a function, which no numbered 03
   contract says. *Recommendation:* add it to 03-C3, or to a new 03 contract.
   Until then, sync reports a deleted root without purging.
3. **Which task do re-embeds file under?** This spec uses the producer's own
   task, as 08 does. The alternative is one `cache-sync` embed task, which
   would make warm-up spend visible as its own line on the Costs page, at the
   cost of splitting a feature's spend across two tasks and making 08's
   `reembed` take a task argument. *Recommendation:* the producer's own task.
   The sync report is where "what did sync spend" is answered.
4. **The 03-C3 refinements, and 08's vector kind (cross-spec).** A
   projection-qualified vector kind is needed as soon as two producers embed
   one file, which is already the case for lore entries and transcripts.
   08's draft writes a bare `vector:<space key>` on scene paths, which would
   collide with library search's passages on the same transcript.
   *Recommendation:* fold `vector:<projection>:<space-digest>` and the
   `instance` column into 03's plan now, since 03's table is new and has no
   migration to protect, and have 08 adopt the form.
5. **Should a large explicit batch ask before embedding?** Section 6.7 argues
   that no confirmation is needed. A bulk external change could still send many
   requests in one go: for example, a store restored from an old copy and then
   synced with `--all`. *Recommendation:* no confirmation in the first version.
   `--dry-run` shows the number first, and 06's skill tells agents to run it
   before an `--all`. Revisit with a threshold of one `embeddings.BATCH` if the
   dry runs surprise anyone.
6. **`grimoire_sync.py` after an adb pull.** The adb script writes PC files
   when it pulls a phone's edits, and it never imports `grimoire`. It could
   print the changed paths as a `cache sync` command, or run one.
   *Recommendation:* print the command, and leave the script import-free. Lazy
   rebuilding covers anyone who ignores it.
7. **A console script.** `grimoire cache sync` reads better than
   `python -m grimoire.cache sync`. *Recommendation:* no console script. The
   house form is `python -m` (`grimoire.where`), and an entry point adds one
   more thing the installers, the venv and the APK must agree on.
8. **Write-through for 08's scene documents.** A played scene's transcript
   changes every turn. With the quiet period, its SearchDocument and vector
   are rebuilt when play pauses. 08 may prefer `on_write="explicit"`, and
   rebuild at absorb instead. *Recommendation:* leave the choice to 08; the
   hook field exists so that each owner decides.

# 05. Direct-edit cache sync

**Status:** Draft — spec gate (substitute review) folded in; Codex gate pending.
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
| 03-C3 | 03 | The `materialized` record: which kinds were built from a path, with the vector kind form `vector:<projection>:<space-digest>` and an optional `instance` column (a recorded cross-spec decision). It is the whole of "what was hot" (section 6). | Hard |
| 03-C4 | 03 | The one validate-and-hash primitive. Sync calls it for every path, and never hashes or stamps on its own (section 4). | Hard |
| 03-C5 | 03 | Artifacts may be stored at once, inside the racy window, and only the `sources` row waits. Sync runs seconds after an agent's edit and depends on this (section 4.4). | Hard |
| 03-C1 | 03 | Composite keys, so that a hook can rebuild a collection-keyed kind for the instance a path feeds. | Soft (only composite hooks need it) |
| 03-C8 | 03 | The callable purge for a world or campaign delete, through a purge marker. Sync performs it when it finds a deleted world or campaign root (section 7.6). | Soft |
| 04-C2a | 04 | `overview.warm_paths(paths)`, the hook through which sync rebuilds 04's overview kinds (section 6.2). | Soft (without it, overview kinds rebuild lazily) |
| 04-C2b | 04 | The client's consistency bound. Sync inherits it unchanged and adds no retirement step (section 3.2). | Hard (a property relied on, no call made) |
| 01h-C5 | 01h | `attribute(claims)` and `embed_groups_sync(task, groups, ...)`: one ledger row per campaign, with no request spanning campaigns. Without it, sync makes one `embed_sync` call per campaign group itself (section 6.6). | Soft (the fallback is complete, only less shared) |
| 01h-C3 | 01h | Embedding options in the space identity. Sync names a space only through `embed_space.endpoint()["space"]`, so it inherits whatever 01h-C3 adds. | Soft |
| 01h-C1 | 01h | Input type. Sync only ever embeds documents, so once 01h-C1 lands it passes the document type. | Soft |
| 08-C2c | 08 | 08's hot-rebuild hook (`affected`, `rebuild_documents`, `reembed`), registered into 05's `WarmHook` protocol as the `searchdocs` hook (section 6.2). Without it, SearchDocuments rebuild lazily. | Soft |
| 01 (landed) | 01 | `inference.embed.embed_sync`, `routing.EMBED_TASKS`, `embed_space.endpoint()` and the metered embed door. | Hard (landed) |

05 defines the hook protocol (section 6.2), and 08 registers into it, so the
edge to 08-C2c is soft in both senses: 05 runs without it, and 08's C2c is
the only part of 08 that waits for 05-C3.

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 05-C2 | 06 (hard) | The command the store-editing skill tells an agent to run after a batch of direct edits, and the flags its drift test checks against the parser. |
| 05-C1, 05-C4 | 06 (soft) | `cache_sync.collecting()`, the in-process form for an agent writing through `grimoire.store` from Python. |
| 05-C3 | 08 (hard for 08-C2c) | The `WarmHook` protocol 08-C2c implements, run after the response by the write-through queue (05-C1), and the policy 08's vectors follow: re-embed only what was embedded, only in the current space, only text the vector cache does not hold. |

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
  (`:355`) or `runner._guarded_thread` (`:251`). `runner.start` hands the
  run to the lifespan through `portal.start_task_soon` (`runner.py:216`),
  which schedules it with `loop.call_soon_threadsafe` from the route's worker
  thread. asyncio copies the *calling thread's* context into that callback,
  so a detached run starts holding a copy of the request's context variables.
  Any request-scoped collector is therefore visible to the run unless the run
  replaces it (section 5.3).

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
3. **Classify** each path: `missing`, `deleted` (named in `--deleted`), cold
   (nothing materialized from it, for every hook) or hot.
4. **Bump the write token** of every campaign a non-refused, non-missing path
   belongs to (section 10). This happens here, before any rebuild, so that a
   slow, cancelled or crashed batch has still told the app about the edit.
5. **Rebuild, locally**: run each registered hook's local phase, in hook order
   (section 6.2). No network.
6. **Re-embed**: run each hook's network phase under the batch's space, in
   chunks (sections 6.5 and 6.6).
7. **Purge** the cache if a deleted world or campaign root was named
   explicitly (section 7.6).
8. **Report** (section 9).

Between paths, between hooks and between chunks, the batch checks its stop
signal and its root (section 4.2), and stops cleanly if either says so.

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
               root: Path | None = None,
               stop: Callable[[], bool] = lambda: False) -> SyncReport: ...
```

- `relpaths` are store-relative and `/`-separated. The CLI and the API convert
  what they were given (section 8). Duplicates are coalesced before anything is
  read.
- `mode="explicit"` serves the CLI, the API and a script's `collecting()`
  exit. The caller has declared a batch complete, so every hot kind is rebuilt
  now. `mode="write"` serves the write-through queue (section 5.4). A hook
  whose policy is `on_write="explicit"` is skipped there, and reported as
  deferred, and a path section 8 refuses is skipped silently rather than
  reported.
- `embed=False` runs every local phase, reports the network work it left as
  `deferred`, and sends nothing.
- `dry_run=True` validates, classifies and plans. It computes projection texts
  and vector-cache hits, which are local. It builds, stores, embeds and bumps
  nothing.
- `verify=True`: see section 7.7.
- `renamed` maps an old path to a new one (section 7.5). `deleted` names paths
  the caller removed (sections 7.4 and 7.6).
- `stop` is polled between paths, between hooks and between chunks. The
  queue's lifespan shutdown and the API run's Cancel set it (section 4.2).
- `root` defaults to `paths.home()`, read **once** at entry. Sync's own path
  handling (validation, listing, `materialized` lookups) is built from it.
  The functions it calls are not: `vectors.load` and `save`, `revision.bump`,
  `embed_space.endpoint()`, the readers behind 03's `derive` and the hooks,
  `logs` and `usage` all resolve `paths.home()` per call. So a pinned value is
  only honest if the root cannot move while the batch runs. Section 4.2 says
  how that is held, and the batch also re-checks `paths.home() == root`
  between paths, hooks and chunks, stopping with `root_moved` if it differs.

It returns a `SyncReport` (section 9). It never raises for one path's
failure. It raises only for a programming error, such as an unknown mode.

### 4.2 Threading, stopping, the lane and holding the root still

- It is synchronous, and it does file and network I/O, so it is never called
  on the event loop's thread. The write-through queue and the API run reach it
  through `anyio.to_thread.run_sync`, awaited **shielded**, as the maintenance
  runs await their work (CLAUDE.md, "Detached runs"): a cancelled awaiter must
  not abandon a thread that is still writing vectors under a root.
- **Stopping.** Cancel is the cooperative `stop` flag. The API run's Cancel
  sets `run.cancel_requested`, which its `stop` reads. Lifespan shutdown sets
  the queue worker's flag, and the flag of any live API sync run, before it
  cancels the task group, beside `runner.stop_maintenance`. The batch stops at
  its next check. A chunk already sent finishes or times out on the client's
  own timeout, so a stop costs at most one embedding request's wait.
- **Holding the root still.** While a batch is running in this process, it
  holds a **sync hold** on the run registry, counted the way
  `_maintenance_holds` is. `PUT /config/data-dir` refuses with 409 `busy`
  while the count is non-zero. The hold is taken only for the length of a
  batch, never while the queue is idle, so the queue still never blocks a move
  between batches. An API sync is also a run, so `runs_in_flight` refuses the
  move for it too. The CLI in another process cannot be held by the server.
  It relies on the per-step `paths.home() == root` check (section 4.1), which
  catches a pointer rewritten mid-batch at the next step.
- In one process, at most one batch runs at a time. An in-process lock, the
  **sync lane**, wraps the rebuild and re-embed phases. Without it, an API run
  and the write-through queue could both embed the same new text in the same
  minute. Content-addressing makes the second store harmless, but the second
  request is still spend. The lane is taken with a timeout: a write-through
  batch that cannot take it re-queues its paths, and an explicit batch waits.
- Across processes (the CLI beside a running server) there is no lane. Both
  may compute the same artifact, which is idempotent. Both may also embed the
  same text. Section 6.6 narrows that by checking the vector cache again just
  before each chunk is sent, but does not close it. This is the residual
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
class Scope:                                   # one collection, owned by one boundary
    def close(self) -> frozenset[str]: ...     # stop accepting, return a frozen snapshot

@contextmanager
def collecting(*, isolated: bool = False) -> Iterator[Scope]: ...
def note(path: str | os.PathLike) -> None: ...   # adds to every open scope; no-op when none
```

- The context variable holds a tuple of `Scope` objects. Each `Scope` has its
  own small lock, a set, and a `closed` flag. `note` takes each scope's lock,
  skips the scope if it is closed, and otherwise adds the path. It does
  nothing else: no I/O, no stat, and no import of the cache. With no scope
  open (a test, a script that did not ask, Android's startup) it costs one
  context-variable read.
- `close()` sets `closed` and copies the set to a `frozenset` under the same
  lock, so the snapshot a boundary hands over can never change afterwards and
  can never be iterated while another thread adds to it. A late `note` from a
  thread still holding the context finds the scope closed and adds nothing to
  it.
- `collecting(isolated=True)` **replaces** the inherited tuple with one that
  holds only the new scope, rather than extending it. A boundary whose
  lifetime is not its parent's uses it (section 5.3). `isolated=False` extends
  the tuple, for a nested scope that really is part of its parent (a script's
  `collecting()` inside a test's).
- **What a path is.** `note` records `os.fspath(path)` exactly as `atomic`
  received it, without `abspath`, which would depend on the process's working
  directory. Every store path is built from `paths.home()` and is absolute
  already. The consumer (the queue, or `cache_sync.collecting()`) relativises
  each entry against the root it recorded, and drops an entry that is relative
  or outside that root.
- `store.atomic`'s four publishing functions (`write_text`, `write_bytes`,
  `append_line`, `streaming_write`) each call `writeset.note(path)` themselves,
  once the replace or the append has succeeded, not inside a shared private
  helper. That is the only change to `atomic`, and it is the line a new guard
  holds (section 12).
- Sets are reached by reference through the context. Both
  `anyio.to_thread.run_sync` and Starlette's `run_in_threadpool` run the
  callable in a copy of the current context, so a write made in a worker
  thread started either way lands in the scopes that were open when the
  thread started. A raw `threading.Thread` does not copy the context, so its
  writes are not collected. The package's only raw threads on a write path
  are the startup inference migration (`main.py:261`), the thumbnail sweep and
  the maintenance heartbeat, and section 5.3 leaves those out.

This is not "hooking `store.atomic`" in the sense 03 forbids. `atomic` gains no
SQLite, no import of the cache, no extra I/O, no `sources` row, and no
behaviour when nobody is collecting. It reports a fact (this path was
published) to whoever asked.

The write set can land on its own, before 03. If 04's plan wants to warm more
than the turn's scene (04-C2a's `warm_scene`), this is the slice to take first.

### 5.3 Where scopes are opened

| Boundary | How | Why there |
|---|---|---|
| Every HTTP request | `_WriteSetCollector`, a raw ASGI middleware in `main.py`, installed beside `_CampaignActivityStamp` and raw for the same reason (it stays out of the exception path). It opens a scope, awaits the app, then in a `finally` **closes** the scope and hands the frozen snapshot to the write-through queue if it is non-empty. | Every route, every method, success or failure. A route that wrote and then failed still wrote: the argument `_CampaignActivityStamp` makes for the token on its exception path. A streamed response has finished streaming by the time `await self.app(...)` returns. |
| Every detached run | `runner._guarded` and `runner._guarded_thread` open an **isolated** scope (`collecting(isolated=True)`) around the producer or the work, then close it at the terminal point and hand the snapshot over. | A run starts with a copy of the request's context (section 1.2), so without isolation every write it makes would also land in the request's scope, after that scope was handed off and from other threads. Isolation gives the run its own collector; closing gives the request a snapshot that a run outliving it cannot change. A run started from inside another run (a turn's `_fire_follow_up`) is isolated from its parent the same way. `runner.start` and `runner.start_thread` are the only two doors. |
| A script | `cache_sync.collecting()` (section 5.5), which opens an isolated scope. | Scripts have no app. |

The middleware's handoff never raises out of its `finally`. It is wrapped,
and it tolerates an app with no `app.state.cache_warm` (a `TestClient`
without a lifespan, since `runner.install` runs only in the lifespan) by
dropping the snapshot. The same holds for the runner's handoff.

Not covered, by design, and rebuilt lazily instead:

- the background inference migration started at startup (`main.start`), which
  is neither a request nor a run, and the other raw threads above;
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
  `PUT /config/data-dir` answer 409 while it is idle. A queue that blocked a
  data-dir move for a minute after every edit would be a nuisance with no
  benefit, since the queue is safe to drop. Only a batch actually running holds
  the move off, through the sync hold (section 4.2).
- **One worker.** A task in the lifespan's task group wakes on a timer, takes
  the quiet paths, and keeps only those inside section 8's closed set of
  syncable roots. That drops the usage ledger, logs, keys, top-level files and
  everything under `.cache/` before any lookup, so the server's own
  bookkeeping writes never surface as refusals. It then runs
  `sync_paths(paths, mode="write")` in a worker thread, under the sync lane
  and the sync hold (section 4.2). That is the vehicle 08 section 9 asks for:
  after the response, outside every lock, never on the event loop.
- **Failures are swallowed**, with one log line per failure kind per process.
  Nobody is waiting for a write-through warm-up, so there is nobody to tell,
  and a warm-up that fails costs a lazy rebuild. `streaming._fire_follow_up`
  swallows its failures for the same reason.
- **Shutdown** sets the worker's stop flag, lets a running batch reach its
  next check, then cancels the worker. Pending paths are lost and rebuilt
  lazily.
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

It opens an isolated write-set scope. On a clean exit it closes the scope and
runs
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
rows for any hook is **cold**: sync does nothing for it and says so. That is
what stops sync from pre-building the whole library, or embedding content
nobody embedded.

Two things make that rule work for composite kinds:

- **A row for every input.** 03 defines a `materialized` row's `path` as "a
  source the artifact read". A composite kind (04's cards, 08's scene document,
  which reads ledgers and inherited records) therefore records a row on
  **every** path it read, not only on its "main" file. An edit to a campaign
  ledger is then hot for 08's kind directly, and is never classified cold.
- **Hot is decided per hook.** Each hook sees only its own kinds' rows
  (section 6.2), so a path can be cold for one hook and hot for another.

`materialized` is read only for paths sync already holds, from its arguments
or from a live listing. That is 03 section 4's rule: "it never answers which
paths exist".

### 6.2 Hooks: each owner rebuilds its own kinds

Three owners already need to rebuild something after an edit: 03's per-file
kinds, 04's overview kinds (`overview.warm_paths`, 04-C2a) and 08's
SearchDocuments (`affected`, `rebuild_documents` and `reembed`, 08-C2c). 05
also adds a fourth, for the three existing vector producers (section 6.4).
Each owner knows what its kinds mean, and 05 must not grow a second copy of
that knowledge. So 05 defines a protocol, and each owner supplies one hook:

```python
class WarmHook(Protocol):
    name: str                          # "files", "overview", "searchdocs", "vectors"
    on_write: Literal["quiet", "explicit"]
    def kinds(self) -> frozenset[str]: ...          # materialized kinds this hook owns
    def local(self, ctx: WarmContext, hot: Mapping[str, frozenset[str]]) -> LocalResult: ...
    def network(self, ctx: WarmContext, plan: EmbedPlan) -> NetworkResult: ...
```

- `hot` maps each path that has rows of this hook's kinds to those kinds. A
  hook never sees another hook's rows.
- `local` rebuilds and stores artifacts and **touches no network**. It returns
  per-path outcomes and an `EmbedPlan`: the projection texts that would need a
  vector, which hits the vector cache already has, and how each text is
  claimed (campaign, or none).
- `network` sends the plan's misses and saves the vectors. It is never called
  in a dry run or with `embed=False`, and never on a request path or under a
  lock, which is 08 section 9's requirement on 05.
- `WarmContext` is built by exactly one constructor, `cache_sync._context()`.
  It carries the root, the stop and root checks, the batch's space (read
  from `embed_space.endpoint()` once) and the space's digest. Section 6.6 and
  section 12 say how the operation guard traces that space.
- Hooks run in a fixed order: `files` (03's single-file kinds), then
  `overview` (04's composites, which hit the file artifacts just rebuilt), then
  `searchdocs` (08), then `vectors` (the existing producers). All the local
  phases run before any network phase.
- Within a hook, instances are coalesced across the batch. Forty entity edits
  in one world warm that world's card once, because `warm_paths` receives the
  forty paths in one call.
- A materialized kind that no hook claims is never rebuilt, and is reported as
  `lazy`.

**The registry is a tuple, not import side effects.** `cache_sync.HOOKS`
names the four hook objects by module (`cache_sync` itself for `files` and
`vectors`, `store.overview.warm` for `overview`, and 08's rebuild module for
`searchdocs`), and `cache_sync` imports those modules. A hook that registered
itself on import would be missing from any process that never imported its
package, and the CLI (`grimoire.cache` imports only `store.cache_sync`) is
exactly that process: it would report every overview and searchdocs kind as
`lazy` and exit 0. Before 04 or 08 lands, its entry is absent from the tuple,
not stubbed. The hook modules must not import `cache_sync`, which keeps the
graph acyclic.

**Adapters.** The two foreign owners keep their own signatures, and 05 wraps
them:

- `overview`: `local` calls `overview.warm_paths(sorted(hot))` once, and maps
  its result to outcomes. It has no network phase.
- `searchdocs`: `local` calls 08's `affected(sorted(hot))`, then
  `rebuild_documents(cid, sids)` per campaign, and turns 08's "has a vector in
  the current document space" answers into the plan. `network` calls
  `reembed(cid, docs, space=ctx.space, client=..., limit=...)` per campaign,
  with 08's own per-campaign `limit` (08-C2c). 08 owns that constant; 05 only
  passes it.

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

More than one projection of a file is often embedded:

- A world lore entry is embedded by recall (`entry_text`) and by library search
  (its passages).
- A scene transcript is embedded by library search (its passages) and, once 08
  lands, by 08 (its SearchDocument).

A bare `vector:<space>` row would let one producer's row stand as proof that
another's projection was embedded. So 05 relies on the vector kind form that
03-C3 carries, a recorded cross-spec decision that 03, 05 and 08 all use:

1. **The kind names the projection**: `vector:<projection>:<space-digest>`.
   `<projection>` is a registry kind whose compute produces the embedded text
   from the path's bytes. `<space-digest>` is a short hash of
   `embed_space.endpoint()["space"]`. The raw space id joins provider, rev and
   model with NUL bytes, which do not belong in a kind name.
2. **An optional `instance` column**: small, opaque JSON recorded beside the
   row. 05 uses it for two things:
   - **Which instance**: for example `{"campaign": "saltmarch", "entity":
     ["lore", "pact"]}`. It lets a hook rebuild an overlaid entry for the
     campaign that read it, and claim the embedding for that campaign
     (section 6.6). Without it, hooks derive the instance from the path alone.
   - **Which unit was embedded**, for a producer that embeds a file in units
     and may embed only some of them. Library search splits a document into
     passages and warms at most `WARM_LIMIT = BATCH * 4 - 1` per query over a
     rotating window (`semsearch.py:104`), so a long transcript may have only
     a few passages embedded. For such a producer there is one row per
     embedded unit, with `{"unit": <position>}` in `instance`. Section 6.5
     uses it to re-embed only units that were embedded.

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
- a `materialized` row written beside each `vectors.save` (one per unit for
  library search), in 03's write batch (off the event loop, and outside any
  campaign lock), naming the source path and, where the producer knows it,
  the instance;
- for recall and art, the source path carried on the candidate dict from the
  place the candidate is built. The plan finds those places; each candidate is
  already built from a record the producer read.

A producer records **no** row for a source outside the store root. Recall can
draw entries from the built-in module packs shipped inside the package
(`store/builtin_modules`), and those are neither syncable (section 8) nor
editable by an agent.

**Write-through policy per producer.** The queue (section 5.4) embeds only
where the next ordinary read would embed the same text anyway:

- **recall and art: `on_write="quiet"`.** Both run on every turn of a
  campaign that has them on, and embed any candidate whose text is missing.
  An edited entry that is still a candidate is embedded at the next turn
  regardless, so warming it after a quiet period moves that spend earlier and
  adds none.
- **library search: `on_write="explicit"`.** Its spend is driven by a reader
  running a semantic search. Under write-through, every scene whose
  transcript was ever search-warmed would re-embed at every play pause, for a
  feature the user may never use again. Only an explicit sync rebuilds it.
- **08's documents** choose their own (Open question 5); a query-driven
  default is `explicit` for the same reason.

`continuity-similarity` is left out. It embeds rows of continuity ledgers
inside absorb and the reconcile sweep, both of which recompute what they need
under their own budgets, and its rows are not files an agent edits directly.

### 6.5 What gets embedded

For each `vector:` row on a hot path, the owning hook's local phase:

1. **Checks the space.** It compares the row's space digest with the batch's
   (`ctx`'s digest of `embed_space.endpoint()`, read once at the start of the
   batch).
   - If they differ, the row belongs to a space the Embedding role has moved
     away from. Re-embedding under the new space would be embedding content
     never embedded there, which is exactly the spend `confirm_embedding`
     exists to ask about (CLAUDE.md, "A settings surface never spends
     unasked"). This is reported `stale_space`, and nothing is sent.
   - If embedding is off (`endpoint()` is `None`, or a known `no` has turned it
     off), this is reported `embedding_off`, and nothing is sent.
2. **Computes the projection text** from the current bytes, through the
   projection kind.
3. **Keeps only replacements of embedded units.** For a single-unit
   projection (recall's `entry_text`, an art description), the one text
   replaces the one embedded unit. For a multi-unit projection (search
   passages), a current unit is planned only if a unit **at the same
   position** has a row. Units that were never embedded stay lazy, so one edit
   to a long transcript never embeds its never-indexed tail. The plan is also
   capped at the producer's own lazy limit (`WARM_LIMIT` for search, recall's
   `WARM_LIMIT` for recall), so a rebuild never sends more for one path than
   one ordinary read could.
4. **Looks the texts up** with `vectors.load(space, texts)`. Text already held
   costs nothing. Because vectors are keyed by text, a transcript whose last
   passage changed re-embeds that passage and no other, and a rename whose
   text is unchanged re-embeds nothing.
5. **Puts only the misses** in the `EmbedPlan`, each with its claim.

So the spend is bounded by the projection-text diff of units that were
already embedded in the current space, and capped per path by the producer's
own lazy limit. It is never bounded by the size of whatever surrounds the
edit.

### 6.6 How it is sent

- **Task.** Each hook embeds under its producer's own task: `semantic-recall`,
  `art-catalog`, `semantic-search`, and 08's `history-index`. No `cache-sync`
  task is added. The task is the axis the error store and the Costs page
  aggregate a feature's spend on, and recall's cost is recall's whether it is
  paid on a turn or ahead of one. 08 already embeds its rebuilds under its own
  task. What sync itself spent is in its report (section 9). Open question 1
  records the alternative.
- **The space is re-checked before sending.** Each network phase calls
  `embed_space.endpoint()` again immediately before its first chunk, and stops
  with `stale_space` for its whole plan if the digest differs from the
  batch's. The local phase compared rows against the batch's space, and if the
  Embedding role moved in between, sending would put texts into a space where
  nothing was ever embedded: the `confirm_embedding` bypass that step 1 of
  section 6.5 exists to prevent. The texts are then sent with the batch's
  space (`ctx.space`), never with the re-read one.
- **Chunks, each its own call, saved as it lands.** The hook splits each
  campaign group into chunks of at most `embeddings.BATCH` texts and makes one
  door call per chunk: one ledger row each. It saves each chunk's vectors
  before the next chunk is sent. The `WARM_LIMIT` comment in `semantic.py`
  records why: in one large call, a rate limit late in the run otherwise
  "raises before a single vector is saved", and the retry repeats all of it.
  01h-C5's `embed_groups_sync` returns all its groups' results at the end, so
  the hook calls it with one chunk's groups at a time (or 01h adds a per-group
  callback; see the review record).
- **Door and attribution.** With 01h-C5, the chunk call is
  `embed_groups_sync(task, attribute(claims), space=ctx.space, client=...)`. A
  text claimed by one campaign is charged to that campaign. A text claimed by
  several campaigns, or by none (a world, the library, an image object), goes
  to the unattributed group and is embedded once (01h section 7). No request
  spans campaigns. Without 01h-C5, the hook groups claims the same way and
  makes one `inference.embed.embed_sync(task, texts, space=ctx.space,
  client=..., campaign=...)` call per chunk.
- **No scene.** A warm-up is not part of playing a scene. A scene's own totals
  stay "the number that is always right" (CLAUDE.md, Costs).
- **Documents only.** Every text sync sends is a document. Under 01h-C1 a
  document is marked by passing no `queries` (it defaults to 0), and
  `embed_groups_sync` embeds documents only. Sync therefore passes nothing
  extra, before or after 01h-C1 lands.
- **Checked again just before sending.** Immediately before each chunk is
  sent, its texts are looked up again, and any that have arrived meanwhile
  (from another process, or from a turn) are dropped from it.
- **Stop on a connection-wide failure.** After `auth`, `missing_key`,
  `rate_limit`, `network` or a deadline, no further chunk is sent (01h-C5
  returns later groups as `not_sent`, filing nothing; the fallback stops the
  same way). The unsent texts are reported with the failure's kind.
- **Deadline.** The client's own per-request timeout, not a caller budget. A
  slow provider cutting a request is therefore a provider failure, recorded at
  `Meter.done` with its kind and status only.
- **The operation guard.** `test_operation_guard.py` traces each embed call's
  `space=` back to `embed_space.endpoint` and refuses an attribute or a
  method parameter. `space=ctx.space` inside `WarmHook.network` is both. So
  05 extends the guard, as a deliverable of its own: `ctx.space` is accepted
  only where `ctx` is the result of `cache_sync._context()`, whose `space`
  field the guard traces to `embed_space.endpoint` like any other value, and
  where the call sits in a `network` method of a hook named in
  `cache_sync.HOOKS`. The guard gains planted-fail cases: a hook that builds a
  `WarmContext` by hand, and one whose `space` field is a literal dict. 08's
  `reembed(..., space=...)` parameter is traced through the same exception,
  since its only caller is the `searchdocs` adapter.

### 6.7 Does re-embedding need confirmation? No, and why

CLAUDE.md's rule is that "a settings surface never spends unasked". Three
routes refuse without a yes today: the model test, a generating health check,
and any change that moves the Embedding role's vector space. The third looks
most like sync, and the argument has to address that resemblance.
Re-embedding after an edit does not need the question, for four reasons:

1. **It never moves the space.** Sync embeds only under the batch's space,
   only for rows already in it, and stops if the role moves before sending
   (sections 6.5 and 6.6). The confirmation exists because moving the space
   re-embeds a library, and sync can never do that.
2. **The content was already opted in, unit by unit.** A `vector:` row exists
   only because a producer embedded that unit of that projection in this
   space. Sync embeds the *next version* of a unit the user already chose to
   embed, and never a unit that was not embedded (section 6.5, step 3). The
   one exception is a stated rename (`--renamed`, section 7.5), which carries
   the old path's rows to the new path; the dry run shows what that would
   send.
3. **Write-through spends only what the next read would.** The queue embeds
   only for recall and art (section 6.4), which embed any missing candidate
   text on the next turn of a campaign that uses them. It moves that spend
   earlier and adds none, except for a version superseded before any turn
   needed it, which the quiet period makes rare. Producers whose spend is
   query-driven (library search, and 08 unless 08 argues otherwise) are
   rebuilt only by an explicit sync.
4. **An explicit sync is an explicit request.** The CLI and the API are run by
   the user, or by an agent acting for them, the way sending a turn is
   (CLAUDE.md: "Play is not gated -- sending a turn *is* the request"). What
   it can spend is bounded by the paths named, by the units already embedded,
   and by each producer's lazy limit per path.

What replaces a confirmation is visibility and an opt-out. `--dry-run` reports
how many texts would be sent before anything is sent. `--no-embed` (or
`embed: false`) rebuilds everything else and leaves the vectors to lazy. The
report counts the texts and requests sent, and every request files a ledger
row. Open question 2 asks whether a very large explicit batch should still
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
  added (Open question 4).
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
app. When the app deletes one, 03-C8 purges the cache, so that derived
private text does not outlive it, either in this device's file or in other
devices' synced copies. A delete made by hand deserves the same. So sync runs
03's purge (the marker, plus this device's purge) and reports `purged`. That
is 03-C8, the callable purge through a purge marker. If 03-C8 has not
landed, sync reports the root as deleted and says the purge did not run.

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
  `grimoire_sync.py` pushed (Open question 3).

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

**05-C1. `cache_sync.sync_paths`, reached by Grimoire's writers through a
write-through queue.**

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

**05-C2. `python -m grimoire.cache sync` and `POST /api/cache/sync`.**

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

**05-C3. `WarmHook` order: files -> overview -> searchdocs -> vectors.
Re-embeds only text not already cached. No confirmation step.**

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

**05-C4. `store/writeset.py`: a context-variable collector that
`store.atomic` notes into, with a guard.**

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

1. **Which task do re-embeds file under?** This spec uses the producer's own
   task, as 08 does. The alternative is one `cache-sync` embed task, which
   would make warm-up spend visible as its own line on the Costs page, at the
   cost of splitting a feature's spend across two tasks and making 08's
   `reembed` take a task argument. *Recommendation:* the producer's own task.
   The sync report is where "what did sync spend" is answered.
2. **Should a large explicit batch ask before embedding?** Section 6.7 argues
   that no confirmation is needed. A bulk external change could still send many
   requests in one go: for example, a store restored from an old copy and then
   synced with `--all`. *Recommendation:* no confirmation in the first version.
   `--dry-run` shows the number first, and 06's skill tells agents to run it
   before an `--all`. Revisit with a threshold of one `embeddings.BATCH` if the
   dry runs surprise anyone.
3. **`grimoire_sync.py` after an adb pull.** The adb script writes PC files
   when it pulls a phone's edits, and it never imports `grimoire`. It could
   print the changed paths as a `cache sync` command, or run one.
   *Recommendation:* print the command, and leave the script import-free. Lazy
   rebuilding covers anyone who ignores it.
4. **A console script.** `grimoire cache sync` reads better than
   `python -m grimoire.cache sync`. *Recommendation:* no console script. The
   house form is `python -m` (`grimoire.where`), and an entry point adds one
   more thing the installers, the venv and the APK must agree on.
5. **Write-through for 08's scene documents.** A played scene's transcript
   changes every turn. With the quiet period, its SearchDocument and vector
   are rebuilt when play pauses. 08 may prefer `on_write="explicit"`, and
   rebuild at absorb instead. *Recommendation:* leave the choice to 08; the
   hook field exists so that each owner decides.

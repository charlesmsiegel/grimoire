# 05-S4 The write-through queue: implementation plan

**Goal:** 05-C1 in full. Every closed write set (S1) reaches a queue on the
app, which warms each path once it has been quiet for `WRITE_QUIET_S`, in
`write` mode, under the sync lane and a sync hold that refuses a data-dir
move only while a batch runs.

**Spec:** sections 4.2, 5.4 and 13 ("Write-through").

## Design

- **`cache_warm.WriteThroughQueue`** on `app.state.cache_warm`, installed by
  `runner.install` beside the run registry (never at module scope), with its
  worker in the lifespan's task group. `offer(snapshot)` runs off the loop
  (the S1 handoff is already a shielded thread hop), resolves the root once,
  and keeps only `cache_sync.syncable(root, snapshot)`: inside the root, under
  a syncable record directory, not a write temp. So the usage ledger, logs,
  keys, top-level files and `.cache/` are dropped before any lookup and never
  surface as refusals. Each entry is `(root, rel) -> monotonic time of its
  latest write`; `MAX_PENDING` bounds it, with one log line per process.
- **`drain(app)`** — one synchronous pass: take the entries quiet for
  `WRITE_QUIET_S`; drop any whose root is no longer `paths.home()`; run
  `sync_paths(rels, mode="write", root=..., stop=queue.stopping.is_set)` under
  `app.state.runs.sync_hold()`. A batch that finds the root moving, or that
  could not take the lane (`lane_busy`), gives its paths back. Failures are
  swallowed, one log line per failure kind. Tests call it directly on an
  injected clock.
- **The worker** sleeps `TICK_S`, then awaits `drain` in a worker thread,
  SHIELDED, so a cancelled awaiter never abandons a thread writing under a
  root.
- **The sync hold** — `RunRegistry.sync_hold()`, a count beside
  `_store_moves`, taken in one acquisition: it yields False while a move is in
  progress, and `hold_still` refuses a move with `SyncRunningError` while it is
  held, which `store_held_still` answers as 409 `busy`. A flag rather than an
  exception because `routes.runs` imports `runner`, which imports
  `cache_warm`: the queue must not import the registry module.
- **`PUT /config/data-dir`** clears the queue beside `drop_pending_touched`.
- **Shutdown** — `cache_warm.stop(app)` sets `stopping` beside
  `runner.stop_maintenance`, before the task group is cancelled; a running
  batch stops at its next check. Pending paths are lost and rebuilt lazily.
- **`GRIMOIRE_CACHE_WARM=0`** installs no queue; `tests/conftest.py` sets it
  at import, as it sets the inference automigrate switch.
- `cache_sync` joins the store facade baseline here: the app now imports it.

## Review fixes folded in (S1/S2 substitute review)

An independent adversarial review of S1 and S2 found, and this commit fixes:
a stop inside a hook reported the unreached paths `current` (now `unsynced`,
with `stopped` set); a busy cache read a hot path as `cold` (03's batch gains
`materialized_answer`, which waits briefly and answers None when it could not
ask, while a family past the history budget still answers empty, as 03
intends); a cancel could skip the token bump (the bump now runs straight
after validation and only a moved root skips it); a file directly under
`campaigns/` or a deleted campaign root was counted as a bump; duplicates used
up `MAX_PATHS` and `deleted=` was unbounded; the top-level directory was
matched case-sensitively; any reparse point was treated as a link (now only
symlink and junction tags); the guard accepted a `note` before the publish or
in a branch (it now requires `writeset.note` as the body's last statement).
Not changed: a path whose rows are all `lazy` or `deferred` reports `current`
with those outcomes per kind, and a wrong-case component below the top level
reads cold (its rows are keyed by the app's own spelling).

## Tests (`tests/test_cache_warm.py`)

The suite's switch installs no queue; an entity PUT in Realm is warmed after
the quiet period (local kinds at this slice) with no token bump; repeated
transcript writes make one warm-up; bookkeeping paths are dropped before any
lookup; a raising route hands its paths to the real queue; a data-dir move
clears it; an entry from an old root is never warmed against the new one; a
move answers 409 `busy` only while a batch runs; `MAX_PENDING` with one log
line; a failed warm-up is swallowed and logged once; a lane-busy batch gives
its paths back; the network phase runs on a worker thread, off the loop and
outside every campaign lock; lifespan shutdown stops a batch mid-embedding.

## Gates

Substitute reviews, recorded in the PR; `@codex review` on the PR.

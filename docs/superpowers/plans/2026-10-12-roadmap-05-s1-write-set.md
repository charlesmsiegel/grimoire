# 05-S1 The write set: implementation plan

**Goal:** 05-C4 in full. Grimoire's own writes are collected per boundary (an
HTTP request, a detached run, a script) and handed over as a frozen snapshot,
with no per-call-site wiring and nothing added to `store.atomic` beyond one
`writeset.note(path)` per publishing function.

**Spec:** `../specs/2026-10-09-roadmap-05-direct-edit-cache-sync-design.md`,
sections 5.2, 5.3, 11 (05-C4), 12 and 13 ("The write set").

**Re-read before planning (the spec's implementation rule):** `store/atomic.py`
(four publishers: `write_text`, `write_bytes` through `_write_through_fd`,
`append_line`, `streaming_write`; three non-publishers: `open_text`,
`read_text`, `is_write_temp`), `main._CampaignActivityStamp` (raw ASGI, the
exception-path argument), `runner.start` / `start_thread` (the only two run
doors; the portal's `start_task_soon` copies the calling thread's context).

## Files

- **New `store/writeset.py`** — leaf, imports nothing from `grimoire`.
  `Scope` (own lock, set, `closed` flag; `close()` freezes under the lock),
  `collecting(*, isolated=False)` (a context variable holding a tuple of
  scopes; `isolated=True` replaces the inherited tuple), `note(path)` (adds
  `os.fspath(path)` to every open scope that is not closed; a no-op with none
  open; never raises). The scope is not closed on block exit — the boundary
  closes it at handoff.
- **`store/atomic.py`** — `writeset.note(path)` at the end of each public
  publisher, after the replace or append succeeded; never in
  `_write_through_fd`, so a fifth publisher cannot inherit a note it does not
  make itself.
- **New `cache_warm.py`** (package top level, beside `runner.py`) —
  `hand_off(app, snapshot)` gives a snapshot to `app.state.cache_warm.offer`
  or drops it when the app has no queue; `hand_off_async` does it off the
  loop, shielded, only when there is a snapshot and a queue. Neither raises.
  This slice installs no queue, so every handoff drops: the spec's "sink that
  drops", which is also the tolerated no-lifespan path.
- **`main._WriteSetCollector`** — raw ASGI, outermost (so it also sees the
  activity stamp's token writes, which the queue later drops). Opens a scope,
  awaits the app, and in `finally` closes and hands off.
- **`runner._guarded` / `_guarded_thread`** — each wraps its body (renamed
  `_guarded_body` / `_guarded_thread_body`, unchanged) in an isolated scope,
  closed and handed off once the body has returned (after the terminal point).
- **New `tests/test_writeset_guard.py`** and its CONTRIBUTING.md row. Every
  public callable in `atomic` outside an explicit allowlist (`is_write_temp`,
  `open_text`, `read_text`, each with its reason) calls `writeset.note` in its
  own body, not in a nested def and not only in a private helper. Planted
  failures: a new writer with no note, a note only in a private helper, a note
  only in a nested def. No marker.
- **New `tests/test_writeset.py`** — the section 13 cases.

## Tests (section 13, "The write set")

1. Each of the four writers notes its path, only after success; each failure
   (missing parent, an interrupted streaming build) notes nothing.
2. Nested scopes both receive; an isolated scope replaces its parents; no
   scope records nothing.
3. A write in `anyio.to_thread.run_sync`, a `def` route's worker and an async
   route's worker thread land in the request's scope; a raw thread does not.
4. A run outlives its request: the request's snapshot is handed off and never
   changes; the run's writes before and after the request ended, and a
   follow-up run it starts, each reach the queue in their own snapshot.
5. A `note` into a closed scope adds nothing, from any thread; a snapshot
   taken while four threads keep noting is a stable copy.
6. A route that writes and raises still hands its paths over; a request that
   writes nothing offers nothing; no queue drops the snapshot; a failing
   queue never fails the request.
7. A maintenance thread run hands its own writes over.
8. The guard's planted failures.

## Reconciliations with the spec

- Spec 5.3 names `isolated=True` for runs; the follow-up case
  (`_fire_follow_up`) is covered because every run goes through `_guarded`.
- The snapshot is handed over *after* the run's terminal point and its
  announcement, not before; the run's writes are all done by then, and the
  order keeps the handoff out of `_guarded`'s failure boundary entirely.

## Gates

Plan gate, review and final gate: substitute reviews (the Codex CLI is not
available in this environment), recorded in the PR; the PR's `@codex review`
is the gate CLAUDE.md accepts in lieu of the CLI.

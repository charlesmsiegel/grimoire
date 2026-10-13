# 05-S2 The sync primitive and the `files` hook: implementation plan

**Goal:** `store/cache_sync.py` with `sync_paths` (05-C1 part), the `WarmHook`
protocol, `WarmContext` through `_context()`, the `HOOKS` tuple and the
`files` hook (05-C3 part), and the report vocabulary (05-C2 part). No door yet:
tests reach the primitive directly.

**Spec:** `../specs/2026-10-09-roadmap-05-direct-edit-cache-sync-design.md`,
sections 3.3, 4, 6.1 to 6.3, 8, 9 and 10.

## What 03 actually landed (re-read before planning)

- `compiled.KINDS` holds one kind, `tokens:record_body`, keyed on a text hash
  with no path input. 03's section 14 measurement left every per-file kind
  unwired. So there is no "single-file kind 03's registry marks", and no
  `derive(kind, path)` a hook could call generically: `Batch.derive` needs the
  reader's `params` and `compute`.
- `Batch.materialized(paths)` reads rows by listed path (and returns nothing at
  all past `MAX_HISTORY_ROWS` per call), `mark_materialized`,
  `copy_materialized`, `validate` (stages a `sources` row only outside the
  persist window), `make_key`, `lookup`, `derive(..., instance=)`.
- `purge_for_delete(root, *, intent=None)` (03-C8) and `space_digest(space)`.
- `_available()` is false on the loop, under a campaign lock, with
  `GRIMOIRE_COMPILED_CACHE=0`, without SQLite or without BUILD; there is no
  public "why".

## Design

- **`FileRecipe` / `FILE_RECIPES` / `derive_file`.** A single-file kind is
  rebuilt only through a recipe its reader registers (`kind`, `role`,
  `params(path, instance)`, `compute`), and the reader derives through
  `derive_file` with the same recipe, so the warm-up and the read make one
  `derive` call and cannot disagree about a key. `FILE_RECIPES` is empty
  today; the `files` hook therefore claims nothing in production until a
  per-file kind is wired, and the tests register their own.
- **`WarmHook.owns(kind)`** in place of the spec's `kinds() -> frozenset`:
  vector kinds carry a space digest (`vector:<projection>:<digest>`), so
  ownership is a test, not a list. Hooks receive their own rows
  (`Materialized`, with `instance`) for their paths rather than kind names, so
  no hook reads the table twice.
- **`compiled.unavailable_reason()`**, a small public addition to 03's module,
  names why the cache is off (`disabled`, `sqlite_unavailable`, `event_loop`,
  `campaign_lock_held`, `build_unavailable`) for the report's `cache: off`.
- **Path validation (section 8)** — `_relative_parts` (lexical normalisation
  against the pinned root, never `resolve()`), `_walk` (lstat every component;
  a symlink or reparse point is `link`; the first missing one makes the path
  absent), the closed `SYNCABLE_ROOTS`, write temps, non-regular files, and
  `MAX_PATHS` with `truncated`. A directory expands to its files.
- **Order (section 3.3)** — validate and coalesce; classify `missing`,
  `deleted` (absent and named in `deleted=`; present is `not_deleted`), `cold`
  (no rows) or hot; bump each campaign's token once (explicit mode, not a dry
  run), before any hook; local phases in `HOOKS` order, then network phases;
  report. `stop` and `paths.home() == root` are checked between paths, hooks
  and network phases.
- **The sync lane** — a module lock around the hook phases. Explicit batches
  wait; a write batch that cannot take it in `LANE_WAIT_S` stops with
  `lane_busy` and leaves its paths `unsynced` for the queue to re-offer.
- **Dry run** — the `files` hook validates, keys and looks up, reporting `hit`
  or `planned`, with the batch disabled so nothing is flushed.
- **Verify** — after `derive`, the recipe is computed again from the current
  bytes with the compiled layer bypassed; a different canonical value is
  `verify_failed` and one log line naming the kind.
- **Report** — `SyncReport.as_dict()` is built from the exported tuples alone
  (`PATH_STATUSES`, `REFUSAL_REASONS`, `KIND_OUTCOMES`, `STOP_REASONS`,
  `BATCH_FIELDS`, `EMBEDDING_FIELDS`). A hook's exception is
  `failed:<ClassName>`, never its message. One INFO line per batch, counts
  only.

## Reconciliations with the spec

- Two words added to the vocabulary, both stated here: `planned` (a dry run's
  "would be built") and `unsynced` (a path a batch that stopped early never
  reached; it counts against exit status 0). `lane_busy` joins `cancelled` and
  `root_moved` as a stop reason.
- With the cache off the batch still validates and bumps tokens: telling the
  app about an edit does not depend on the cache. Every path is then `cold`.
- "A purge wins" is 03's fence inside `Batch.flush`; the report cannot count
  what a dropped flush held, so it does not claim to.

## Tests (`tests/test_cache_sync.py`)

Hot path built then current, and the reader's next read a hit; a cold path
runs no hook; an unowned kind is `lazy`; hooks in order with every local
phase before any network phase; a dry run and `embed=False` call no network
phase; write mode defers an `explicit` hook; a raising hook is
`failed:<Class>` and the next hook runs; every refusal with its reason; an
absolute path inside the root; `MAX_PATHS`; directory expansion skipping
temps and links; coalescing; write mode silent about refusals; `missing`
versus `deleted` and `not_deleted`; one bump per campaign before the first
rebuild even when the hook then fails; no bump for a world path or in write
mode; a dry run stores and bumps nothing; verify catches a kind whose key
omits an input; the cache off; the lane; a moved root; a stop signal; an
unknown mode; the sentinel test (a malformed JSON record and a hook whose
exception message carries the sentinel) over the report and the log; the
report uses only the exported vocabulary.

## Gates

Substitute reviews, recorded in the PR; `@codex review` on the PR.

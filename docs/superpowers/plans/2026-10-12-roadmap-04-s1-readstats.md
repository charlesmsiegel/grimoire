# Roadmap 04 S1 — Request counters and the debug line

> Executed inline (superpowers:executing-plans) as the first of spec 04's ten
> slices, each its own commit in one PR. Plan gate: substitute review (the
> Codex CLI is not available in this session); the PR's `@codex review`
> stands in for the CLI gates, per `CLAUDE.md`.

**Goal:** 04-C3a in full — `store/readstats.py`, the counter bumps in the
leaves, and one Debug `overview_read` row per overview request.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-04-instant-overview-pages-design.md`
sections 8, 11 and 04-S1.

## Tasks

1. `store/readstats.py`, a stdlib-only leaf: `collect()` (context manager,
   a `ContextVar` set; a nested collector counts apart and merges into its
   parent on exit), `bump(name, n=1)` (no-op without a collector, never
   raises), `snapshot()`, `active()`. `Counters` is lock-guarded so threads
   that run a copied context count into the same set.
2. Bumps:
   - `statcache.memo` / `memo_stamped`: `statcache.hit|miss.<kind>`, the kind
     of a stamped key being its leading string (every caller's convention).
   - `frontmatter.parse_frontmatter` / `parse_frontmatter_head`:
     `frontmatter.parse` / `frontmatter.parse_head`.
   - `scenes.serialize._parse_messages`: `transcript.parse`.
   - `compiled`: 03-C6's per-kind counts become `compiled.hit|miss.<kind>`
     (through `_lookup_result` and `lookup`), `compiled.store.<kind>` on a
     staged put, `compiled.bypass` for a lookup the layer refused,
     `compiled.sources_hit|sources_miss`, `compiled.bytes_hashed`,
     `compiled.collection_digest`.
   - `routes/todo.py`: `todo.scope.library|campaign` and `todo.chore.<id>`,
     through two small helpers (`_campaign_scope`, `_run`) the composition
     rewrite in S4 reuses. `overview.*` names are reserved for S4–S9.
3. `compiled.enabled()`: SQLite importable and the kill switch not set. Reads
   no file.
4. `routes/overview_log.py`: `overview_read(endpoint, scope)` opens a
   collector and, in `finally`, writes one `logs.record("debug", ...,
   kind="overview_read", endpoint, scope, ms, cache)` — `cache` is `off`,
   `degraded` (an `overview.live_path.*` counter moved) or `on`. No counter,
   id, name or path. Wrapped around `GET /worlds`, `GET /campaigns`,
   `GET /api/shell` and `GET /todo` (not the items route).

## Tests (`tests/test_readstats.py`)

No-op without a collector; nested collectors; threads through a copied
context; parser and memo counters; one row per route with exactly the stated
keys; a 404 still logs once; `cache_state`; instrumentation that raises
changes no answer. `test_import_guard.py` passes with `readstats` a leaf.

## Review focus

The row's field set (privacy); `bump` cost on hot paths; that no answer moves.

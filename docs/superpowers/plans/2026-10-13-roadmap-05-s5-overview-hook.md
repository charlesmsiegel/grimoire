# 05-S5 The `overview` hook adapter: implementation plan

**Goal:** 05-C3's `overview` entry: 04's `overview.warm_paths` (04-C2a), wrapped
as a `WarmHook` and named in `cache_sync.HOOKS` between `files` and `vectors`.

**Spec:** sections 6.2 ("Adapters"), 11 (05-C3) and 13.

## What landed upstream (re-read before planning)

- 04-S9: `store/overview/warm.py`'s `warm_paths(paths)` takes store-relative
  paths, coalesces them by instance (five continuity ledgers of one campaign
  are one `continuity_summary`), stores each artifact from bytes it reads back,
  reads the store back before it answers `"warmed"`, and answers `"none"` for a
  path no persisted overview kind reads now (an unregistered kind, a short
  scene, a file gone) and `"failed"` for a store that did not land. It never
  raises and holds no lock.
- 03's `derive` records a `materialized` row on every input path of a
  persisted composite under the kind's name (`overview.*`), so a ledger edit
  makes its path hot for `overview.continuity_summary`, and a long scene's for
  `overview.scene_turns`. By 03's measured rule only `scene_turns` is
  registered today; the hook claims every kind in `OVERVIEW_CANDIDATES`, so a
  kind registered later is warmed without a change here.

## Design

- `cache_sync._OverviewHook` (`OVERVIEW`): `owns` the names in
  `compiled.OVERVIEW_CANDIDATES`; `local` hands `sorted(hot)` to `warm_paths`
  in **one** call and maps each path's answer onto every overview kind it
  holds: `warmed` → `built`, `none` → `skipped`, `failed` → `failed:not_saved`.
  A dry run plans (`planned`) and never calls `warm_paths`, which stores. A
  stop before the call leaves the paths `unsynced`. No network phase.
- `on_write = "quiet"`: warming an overview artifact costs local work only, so
  write-through warms it as an explicit sync does.
- `--verify` adds nothing: `warm_paths` already reads back what it stored.
- `cache_sync` imports `store.overview.warm` as a module; the overview package
  does not import `cache_sync`, so the graph stays acyclic (section 6.2).

## Tests (`tests/test_cache_sync_overview.py`)

The hook order; edits to a campaign's five ledgers reach `warm_paths` in one
call; an edited ledger with `continuity_summary` registered is warmed and the
next `continuity_summary` read is a hit (no compute counted) equal to the live
answer; a dry run plans and stores nothing; `none` and `failed` map to
`skipped`/`current` and `failed:not_saved`/`failed`; in a fresh process every
overview candidate and every vector projection is claimed by a hook.

## Gates

Substitute review, recorded in the PR; `@codex review` on the PR.

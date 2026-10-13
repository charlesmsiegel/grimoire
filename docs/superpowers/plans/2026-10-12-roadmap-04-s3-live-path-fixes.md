# Roadmap 04 S3 — Live-path fixes

> Executed inline; plan gate by substitute review, the PR's `@codex review`
> standing in for the CLI gates (`CLAUDE.md`).

**Goal:** 04-C1c's live-path part (spec 04 section 4.5, items 1 to 3), every
output byte-identical. Item 4 (post-mutation `reload()`) is S2's.

## Tasks

1. `modules.binding._world_module(wid)`: the world's raw `module` key off
   `world.md`'s frontmatter alone. Raises `WorldNotFound` where
   `read_world` raises (an id `world_root` refuses; no `world.md`).
   `resolve` uses it instead of `read_world(...)["meta"]`, which took nine
   record-kind listings to read one key on every shell read and `sheets`
   chore.
2. `characters.roster`: each row memoized by `statcache.memo_stamped` in the
   existing `_IDENTITY_POOL`, keyed `("roster_row", <character dir>)`,
   stamped on the character directory (its listing is the version glob, the
   row filter) and `character.md`, both before the read — the
   `_build_identity` pattern. Same walk, filter, order and values; a meta
   that raises is not remembered; callers get copies.
3. `routes/todo.py`: `_review_sidecars(cid)` is the one sidecar walk.
   `_chore_unreviewed` sums its edits and builds no title map;
   `_items_unreviewed` (the items route) lists scenes for titles only once a
   sidecar was found.

## Tests (`tests/test_overview_live_fixes.py`, helper `tests/overview_support.py`)

- `binding.resolve` and `sheets.coverage` for an inheriting campaign list
  nothing under the world root (audit hook).
- `WorldNotFound` kept; a world-level module still resolves.
- Two consecutive global Todo reads open each `character.md` at most once
  (statcache clock moved past its racy window).
- Roster equals the listing's ids, notices a rename, hands out copies.
- The chore never calls `list_scenes` with no sidecar; chore and items agree.
- `test_todo_route.py`, `test_shell_route.py`, `test_roster_shell.py` pass
  unchanged. Verified the new tests fail without the fixes.

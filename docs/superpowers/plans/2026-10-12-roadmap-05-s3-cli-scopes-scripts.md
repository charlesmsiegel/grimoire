# 05-S3 The CLI, scoped modes and scripts: implementation plan

**Goal:** `python -m grimoire.cache sync` with `build_parser()`, every flag and
exit statuses 0 to 3; the scoped modes (`--campaign`, `--world`, `--all`) with
section 7.3's candidate rule; stated renames; the `--deleted` root purge; and
`cache_sync.collecting()` wrapped around `create_world.py`'s and
`ingest_scene.py`'s apply steps (05-C2 part, 05-C1 part).

**Spec:** sections 5.5, 7.1 to 7.6, 9 and 13 ("The CLI and the API").

## Design

- **`grimoire/cache.py`** beside `where.py`: `build_parser()` with a `sync`
  subcommand; `main()` reconfigures stdout with `errors="backslashreplace"`,
  calls `logs.install()` before anything can log, resolves the root with
  `paths.home()` (no `--root`; `GRIMOIRE_HOME` is the override), exits 3 when
  the root is missing, not a directory or unreadable, and prints the report
  as JSON (`--json`), counts only (`--quiet`) or a short table. Exit 1 when
  the report is not `ok`; argparse's own usage errors exit 2.
- **`cache_sync.run()`**, the one door the CLI and (in S7) the API share: the
  union of named paths and scopes, one explicit `sync_paths` batch.
- **`expand_scopes()`** — each id through `paths.safe_id` (`bad_id`), its
  directory lstat-walked (`not_found` when absent, never a purge); a live
  listing that skips temps and links; one compiled batch that asks
  `known_hashes` (stat only, never a read) and `materialized` per listed path.
  No rows: `cold`. A vouched hash with every row's `built_from` equal to it:
  `current`. Otherwise a candidate, which `sync_paths` reads and rebuilds.
  `known_hashes` applies a per-kind trust policy, so 03's registry gains
  `compiled.SYNC_SOURCES`, a kind that is never keyed and holds no artifact.
  A scoped report lists only paths that are not `current` or `cold`
  (`listed: "notable"`) and counts the rest; the scope's campaigns still bump.
- **Renames** (`renamed=` on `sync_paths`, `--renamed OLD=NEW`): OLD must be
  gone (else `not_deleted`) and is an intended delete; NEW must be a file.
  `copy_materialized(old, new, instance_map=...)` gives NEW OLD's kinds and
  units with an empty `built_from`, each hook remapping its own instances
  through an optional `remap(kind, instance, old, new)` and keeping
  independent unit selectors. A dry run writes no rows: it hands the copied
  rows to classification in memory.
- **Root purge**: a `deleted` path that is exactly `worlds/<wid>` or
  `campaigns/<cid>` and gone calls `compiled.purge_for_delete(root)` with the
  pinned root, after the root check, in explicit mode and never in a dry run.
  `report.purged` is `None` when no root was named.
- **`collecting(*, embed=True, deleted=())`**: an isolated write-set scope;
  on exit (clean or not) its absolute paths are made relative to the root read
  at entry, anything outside it dropped, and one explicit batch runs; on an
  exception the original is re-raised and a failing sync never masks it.
  Both scripts print one `cache sync:` line on stderr so stdout stays the
  JSON their callers parse.

## Reconciliations with the spec

- `--deleted` or `--renamed` alone is a valid selector: a sync that only
  reports a removal is a reasonable batch, and refusing it would push a
  caller into naming an unrelated path.
- The CLI's subprocess-artifact acceptance (an app's next read hits what the
  CLI built) needs a production kind the CLI can rebuild; the first one is
  S6's vector projections, so that test lands with S6.

## Tests (`tests/test_cache_cli.py`)

Exit 0 for a cold path and 1 for a missing one (with the `--deleted` hint);
1 for a refusal; 2 with no selector and for a malformed `--renamed`; 3 for a
missing root; `--json` is one object with a version; `--quiet`; every flag on
the parser; the CLI in a fresh subprocess. A scoped sync reads only the hot
file not proved current (counted `read_bytes`), and lists only it; a read that
refreshed `sources` does not hide a stale `built_from`; a missing scope is
`not_found` and an unsafe id `bad_id`, with no purge; `--all` covers exactly
the syncable directories; a scoped campaign bumps its token with everything
cold. A deleted campaign root purges, a plain path or a scope naming it does
not, a deleted file does not, a dry run never does. A stated rename carries
units 1, 4 and 9 with an empty `built_from` and reports OLD deleted; a dry-run
rename writes no rows; an OLD that still exists is `not_deleted`; the CLI
parses `OLD=NEW`. `collecting()` syncs what the block wrote, syncs then
re-raises, hands deletes over, and is isolated from an enclosing scope.

## Gates

Substitute reviews, recorded in the PR; `@codex review` on the PR.

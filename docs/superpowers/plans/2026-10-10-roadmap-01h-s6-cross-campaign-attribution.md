# 01h-S6: Cross-campaign attribution — Plan

**Goal:** one batched job can embed documents for many campaigns and still
file each campaign's spend under that campaign, exactly: `attribute(claims)`
groups texts by who pays for them, and `embed_groups_sync` embeds each group
as its own `embed_sync` call under one deadline. Delivers 01h-C5 in full
(the `run_id` keyword landed on both doors in 01h-S5).

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01h-embedding-options-design.md`
§7, §9 (C5), §10 (guards, CLAUDE.md), §11 (Attribution), Slices 01h-S6.

**Review gates:** not run (speed mode); the Codex gates are owed.

**Open questions (§13):** Q2 (a text several campaigns claim): the
recommendation, unattributed -- never charged to the first campaign, never
apportioned. 01g-S3 has landed, so `run_id` is filed on every group's row.

## Design (all in `store/inference/embed.py`)

- `EmbedGroup(campaign, scene="", texts=())` and `GroupResult(vectors,
  error="")`, frozen dataclasses; `GROUP_NOT_SENT` is `embeddings.NOT_SENT`.
- `attribute(claims)`: texts in first-claim order, deduplicated; a text whose
  claimants are exactly one campaign goes to it, anything else (several, or
  only `""`) to the `""` group. Groups: `""` first, then by campaign id.
- `embed_groups_sync(task, groups, *, space, client, deadline=None,
  budgeted=False, run_id="", cached=None, uncached=None, on_group=None)`:
  - refuses a non-embed task up front (`ValueError`);
  - takes one deadline at entry (`TIMEOUT` from now when none is given) and
    hands it to every group's call;
  - one `embed_sync` per group with its campaign and scene, `run_id` on
    each, `cached`/`uncached` on the first call only;
  - `bad_response` fails its group and the run goes on; any other
    `EmbeddingsError` (including `missing_key`/`dimensions_mismatch` and a
    deadline `not_sent`) stops it, and every later group is `not_sent` with
    nothing filed;
  - `on_group(group, result)` after each group that was tried, before the
    next request; one that raises is recorded (`errors.record_exception`) and
    stops the run. Errors that `embed_sync` raises before sending (a space
    whose options disagree) propagate unchanged.
  - Documents only: no `queries`.
- `test_operation_guard.py` learns the door: `embed_groups_sync` is an
  operation name (task literal in `EMBED_TASKS`, never a value, `space=`
  traced to `embed_space.endpoint`), with planted cases.
  `MIN_EMBED_CALLS` is unchanged (05 is the consumer).
- `CLAUDE.md`'s embedding paragraph names the third door.

## Tests (`test_embed_groups.py`)

- `attribute`: one claimant, shared and unclaimed texts unattributed and
  first, determinism.
- One row per group with its campaign and scene, no mixed request body,
  `run_id` on every row, `cached`/`uncached` on the first line only.
- `bad_response` in group 2 still runs group 3; `rate_limit`, `auth` and a
  5xx stop with group 3 `not_sent` and no row; a width-ignoring endpoint
  stops the run.
- One deadline handed to every group; a spent deadline sends and files
  nothing.
- `on_group` order (each before the next request); a raise stops the run.
- A non-embed task is refused before anything is sent.

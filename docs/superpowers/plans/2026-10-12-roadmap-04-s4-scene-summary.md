# Roadmap 04 S4 — `scene_summary`, in-process, and its consumers

> Executed inline; plan gate by substitute review, the PR's `@codex review`
> standing in for the CLI gates.

**Goal:** 04-C1b's `overview.scene_summary` (in-process only) and 04-C1c's
open-scenes chore, `has_campaign`, the shell's scene count and open list, and
`GET /campaigns`' scene fields. Spec 04 sections 3.4, 4.2–4.4, 12.1–12.3, 12.6.

## Tasks

1. `store/overview/` package (`__init__`, `read.py`); nothing in `store/`
   imports it. `read.scene_summary(cid)`:
   - raises `CampaignNotFound` (via `scenes.paths._require_campaign`) and a
     listing's `OSError`, as `list_scenes` does;
   - `statcache.memo_stamped(("overview.scene_summary", <scenes dir>))` in
     `_OVERVIEW_POOL` (sized as `_SCENE_POOL`). The fold stamps the directory
     before globbing it (an absent `scenes/` is vouched for by the campaign
     root), and each member before its head-only `_scene_row` read;
   - per member, `UnicodeDecodeError`/`OSError` counts the member in `count`
     and `unreadable`, bumps `overview.unreadable_scene`, and makes the value
     unremembered;
   - order: `updated` descending, then `sid` ascending; `closed_by` by
     `scenes.read._resolve_groups` over readable members;
   - `stamps`: distinct well-formed `updated` values (new
     `campaigns.read.well_formed_stamp`, the clock-free half of
     `_valid_stamp`), newest first;
   - the ctime residual: `_MEMBER_STAMPS` remembers each member's last stamp;
     when a member's stamp moved but its mtime/size/inode did not, the stale
     `scene_head` entry is dropped from `_SCENE_POOL` before reading;
   - callers get copies.
2. Consumers: `GET /campaigns` (count, absorbed, `rows[0].title`, live
   `best_stamp(updated, read_activity, *stamps)`); the shell's block (count,
   open list) through `_Ctx.scene_summary_or_raise()`; Todo's `_Ctx`
   (`scene_summary()` maps `CampaignNotFound`/`OSError` to `None`, as
   `_scenes_or_none` did; `has_campaign` and `open-scenes` read it).
3. Docstrings that promised "nothing is cached" (`routes/todo.py`,
   `store/chores.py`) amended to the 4.4 wording; `TodoView.tsx` is S2's.
4. Frozen sweep gains `overview.scene_summary[<cid>]`; `snapshot.json`
   regenerated and reviewed (one new section, nothing else moved).
5. `store_api_baseline.json` gains `overview` and `readstats` on `dir`: both
   are new store submodules that the package exposes as attributes.

## Tests (`tests/test_overview_scene_summary.py`)

Equality with a pre-04 oracle (`list_scenes`-based) for `GET /campaigns`, the
shell's open list and the open-scenes chore; closed branches; the two expected
differences (tie-break; a head bad byte answering 200 where the oracle
raises); a body bad byte past 8 KiB unchanged with `turns: null`; append,
rename, add, delete and a hand edit seen; same-size in-place head rewrite with
mtime restored seen (verified failing without the residual fix); an
unlistable `scenes/` answers 404 on the campaign scope and is omitted
globally; copies; warm same-process lists no `scenes/` and parses no head; a
relevant change costs one head parse and one listing; warm reads never open a
closed scene.

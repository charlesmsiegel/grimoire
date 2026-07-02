# Scene filenames: `<number>--<in-world-date>--<title-slug>.md`

**Date:** 2026-07-01
**Status:** Approved

## Goal

Scene files currently live at `<campaign>/scenes/<real-date>-<title-slug>.md`
(e.g. `2026-06-28-the-ambush.md`), named by the real-world creation date. Rename
the scheme so filenames carry play order and in-world time instead:

- Dated scene: `007--1023-05-12--the-ambush.md`
- Undated scene: `007--the-ambush.md` (no date section at all)

The scene **number comes first so lexicographic filename order equals play order
absolutely** — regardless of in-world date jumps or undated scenes. Sections are
separated by `--`, which is unambiguous because `slugify` collapses dash runs:
neither a slug nor a slugified date can ever contain `--`.

This also fixes two orderings for free: `chronicle.recent()` (chronicle.py) and
plot-thread sorting (plot.py) both sort by scene id lexicographically, which
today means real-date order and, after this change, exact play order.

## Filename grammar

```
<number>--<date-slug>--<title-slug>.md   (dated)
<number>--<title-slug>.md                (undated)
```

- Parse by splitting the stem on `--`: 3 parts = dated, 2 parts = undated.
- `<number>`: zero-padded decimal, campaign-uniform width (see below).
- `<date-slug>`: `slugify()` of the **date part** of the scene's *first*
  `time_history` entry (canonical provider format, time-of-day stripped — the
  `T14:30` suffix contains a colon, illegal in Windows filenames). For the
  gregorian provider this is `1023-05-12` unchanged; fantasy calendars pass
  through `slugify` like any text.
- `<title-slug>`: `slugify(title)`, as today. `uniquify` collision suffixes
  (`-2`, `-3`) attach to this section and stay single-dashed.

## Scene number

- Assigned at `create_scene` as `max(existing numbers in the campaign) + 1`
  (1 for the first scene). Derived by parsing the leading digits of existing
  scene filenames — **no stored counter**.
- Zero-padded to the campaign's current width. Width starts at 3 and is derived
  as `max(3, longest digit-run seen on existing scene files)`.
- Numbers are **stable forever**: deleting a scene leaves a gap; nothing ever
  renumbers.

### Dynamic width growth (the 999 → 1000 re-pad)

When the next number needs more digits than the current width (creating scene
#1000 in a 3-digit campaign), `create_scene` first **re-pads the whole
campaign**: every scene file is renamed with one more leading zero and every
persisted scene reference is repointed, then the new scene is created at the
new width. Recurs naturally at 10 000, etc. Widths stay uniform, so
lexicographic order stays exact.

## When filenames change (and what follows them)

A scene id is its filename stem, and ids are persisted in exactly four stores:

1. `appearances.json` — per-appearance `scenes` lists
2. `chronicle.json` — record keys **and** each record's `id` field
3. changes store — each record's `scene` field
4. `plot.json` — `beats[].scene` and `last_scene`

A single bulk helper `repoint_scene_ids(cid, mapping)` performs the file
renames plus all four store updates in one pass per store file. It is used by:

- **Title rename** (`rename_scene`): re-slugs only the title section; number
  (and date section, if present) are preserved verbatim. Side effect: today a
  post-absorb title rename orphans chronicle/changes/plot references
  (`appearances` alone is carried); routing renames through the bulk helper
  closes that gap.
- **First `set_datetime`**: a scene is born undated (`007--the-ambush.md`);
  the first date set renames it to insert the date section
  (`007--1023-05-12--the-ambush.md`). Later time advances append to
  `time_history` but never touch the filename — the start date is fixed.
- **The re-pad** described above.
- **The legacy migration** described below.

### API/frontend impact

The set-datetime route's response gains an `id` field (the possibly-renamed
scene id), mirroring how the rename route already returns `new_sid`; the Play
page must adopt the returned id as its current scene id. No other frontend
change — scene ids are otherwise opaque strings handed back by the API.

## Legacy migration

One-time and idempotent, at backend startup. Per campaign, every scene file
whose stem does not match `^\d+--` is legacy:

- Order legacy scenes by their `created` frontmatter timestamp.
- Assign numbers continuing from the campaign's current max (0 in the common
  all-legacy case), at width `max(3, digits needed)`.
- New id: number section + date section (if the scene has a `time_history`,
  from its first entry) + `slugify` of the frontmatter `title` (fallback: the
  old stem).
- Rename + repoint via `repoint_scene_ids`.

Without migration, mixed ids would corrupt chronicle/plot ordering: every
legacy id starts with `2` (a real year) and would sort *after* every new
`0…`-prefixed id despite being older.

## Unchanged

- `list_scenes` still sorts by `updated` (the UI's most-recently-played order).
- `_safe_id` defense and `uniquify` collision handling.
- Delete behavior (gaps are permanent).

## Testing

- `create_scene`: first scene is `001--<slug>`; numbering continues past
  deleted gaps; width follows existing files.
- Undated format has no date section; `--` split round-trips both shapes.
- First `set_datetime` renames the file, inserts the date section, and carries
  appearances (and the other three stores); a second date set does not rename.
- Time-of-day in the canonical moment never reaches the filename.
- `rename_scene` preserves number and date sections, re-slugs the title, and
  repoints all four stores.
- Re-pad: creating scene #1000 widens every file to 4 digits, repoints all
  four stores, and preserves relative order.
- Migration: a legacy store (dated and undated, absorbed and not) converts
  idempotently; chronicle keys, changes, plot refs and appearances follow;
  numbering follows `created` order.
- Existing suites (`test_scene_store`, `test_appearances_store`, routes) updated
  for the new id shape.

# Roadmap 04 S7 — Persisted card rows, and the `module` chip

> Executed inline; plan gate by substitute review, the PR's `@codex review`
> standing in for the CLI gates.

**Goal:** 04-C1a (spec 04 sections 3.2, 3.3, 3.8, 4.1–4.3, 12.1–12.4, 12.6).

## Decisions taken in this slice

- **Open question 4: option (b)**, the spec's recommendation. The campaign
  card's `module` is the campaign's raw setting over its world's raw default,
  with no pack load; `"none"` (at either level) draws no chip. `world_row` and
  `GET /worlds` gain the raw `module`, the one change to the Worlds payload.
- **Registration follows spec 03's section 14 rule.** 03's landing measured
  `world_row` and `campaign_row` and withheld them ("a kind which does not
  win is not wired", which 03 says takes precedence over a slice naming a
  kind). So all four overview kinds are *declared* in `compiled.py`
  (`OVERVIEW_CANDIDATES`) and *registered* in `compiled.KINDS` only when S10's
  benchmark shows a warm-after-restart win. The code path is complete either
  way: `overview/persist.derive` answers through the compiled layer for a
  registered kind and through the live path otherwise, and the tests register
  the kinds to hold the persisted path to the live answer.

## Tasks

1. `compiled.py`: the four kinds (byte-fed, no path-derived params,
   persisted sources allowed; continuity's five roles absent-ok);
   `compiled.available()` (public, never raises).
2. `overview/persist.py`: `derive(kind, inputs, params, compute, live_read)`
   — unregistered or off: live, uncounted as degraded; unavailable or
   `InputUnavailable`: live, `overview.live_path.<kind>`; else
   `Batch.derive` with a role->bytes compute. `overview.computed.<kind>`
   counts both kinds of compute.
3. `overview/kinds.py`: `world_row(text)`, `campaign_row(text)` (name `None`
   when absent), `card_module(setting, world_default)`.
4. `overview/read.py`: `world_row`/`campaign_row` (memo_stamped on the meta
   path in `_OVERVIEW_POOL`, stamp before read, persisted layer below);
   `world_rows()`, `campaign_rows()` (list_campaigns' enumeration and order),
   `campaign_meta(cid)`, `world_name(wid)`, `campaign_cards()`; the name
   fallback applied outside the cache. `list_worlds` / `list_campaigns` are
   untouched.
5. Routes: `GET /worlds` -> `world_rows()`, `GET /campaigns` ->
   `campaign_cards()`, the shell's rows, name/world and `world_name` off the
   rows. `put_todo_ignored` stops reaching `_Ctx.has_campaign` (a live
   `list_scenes` check, today's mapping), since a write never reaches a
   projection.
6. `tests/test_overview_guard.py`: a syntax guard — only the four read routes
   and the warm import `store.overview`; no decision site (`closed_by`,
   `list_scenes`, `read_scene`, `list_campaigns`, `read_campaign`, the items
   and ignore routes, `continuity.effective`/`pending`) reaches it; fixtures
   for `closed_by` and the items route fail it. Named in `CONTRIBUTING.md`.
7. Frozen sweep: `overview.world_rows`, `overview.campaign_cards`.

## Tests (`tests/test_overview_card_rows.py`)

Worlds payload equals the oracle but for `module`; the five states (empty,
warm, restart, locked database, cache off) answer identically, registered or
not; warm after restart parses no `campaign.md` (compiled hits) and still one
head per scene; identical bytes in two directories share one computation and
keep two names; an empty name stays empty; meta changes seen in-process and
after restart; a same-size rewrite with mtime restored is seen; the chip
table; `list_campaigns` unchanged; an undecodable `campaign.md` raises as
today; a failed input read counts `live_path`; an unregistered kind opens no
batch; the shell's name and world name.

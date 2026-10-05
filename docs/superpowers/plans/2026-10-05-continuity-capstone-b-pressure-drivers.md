# Continuity Capstone — Slice B: Temporal Pressure and Drivers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the read-only temporal pressure service and the scene-driver projection, including `GET /continuity/drivers`. Then switch every effective-current-state reader (prompt snippets, briefing, advance digest, ledger, Todo `owed`, shell badge) onto the canonical projections. A campaign with no aliases must produce byte-identical prompts.

**Architecture:** There are two new read-only modules under `backend/src/grimoire/store/continuity/`:

- `pressure` composes the existing calendar, event, aging and birthday readers into one dated item list with a fixed state contract;
- `drivers` composes `pressure`, `effective` and `involvement` into drivers and anchors.

`birthdays` gains an occurrence scan that its existing `upcoming` becomes a projection of. `effective` gains the snippet render helpers and a no-alias fast path. The consumers listed in spec §7.3 change their import, not their logic. A no-alias campaign is proven unchanged three ways:

- direct equality tests against the physical renders;
- `verify_templates` checks;
- the frozen-campaign sweep, which passes unregenerated after the switch and then gains only new keys.

**Tech Stack:** Python 3.11, FastAPI, pydantic (v1/v2-agnostic), pytest + `TestClient`; TypeScript, React, vitest.

**Spec:** `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md`. This plan implements §31 Slice B:

- §13 (all of it) and §13.6;
- §14;
- §20's `DRIVER_KINDS` and `PRESSURE_STATES` tuples;
- §21's drivers read route;
- the §7.3 consumer switch that Slice A deferred, with the §7.0 render helpers, §8's canonicalization step for briefing, §12.5's ledger rows (the note line without its link), §18.3 and §27's gate work;
- the §28.5 and §28.7 (`owed`) tests, and §26's "calendar unavailable" row.

Later slices own the following:

- the absorb identity resolver and the §7.2 materialization redirect (C);
- candidates, reconcile, the review UI, Ledger addressing, the Todo candidate and embeddings chores, and the Ledger force-delete path (D);
- the suggestion snapshot, schema, controls and ideas (E);
- the Story Graph (F).

## Global Constraints

- **Privacy:** fixtures and docstrings use only Seraphine, Mara, Winifred, Realm and Saltmarch, or words built from them ("Mara's map", "Saltmarch Eve", "The coronation"). Never read `~/.grimoire`. Never describe store contents.
- **Imports:** all imports are at module scope, and the graph is acyclic. Inside `store/`, bind modules, never names (`test_import_guard`).
  - `pressure` imports exactly `from .. import aging, birthdays, calendars, clock, events, notices`, `from ..campaigns import paths as campaigns_paths`, `from . import effective`.
  - `drivers` imports exactly `from .. import aging, embed_space`, `from ..appearances import cast as appearances_cast`, `from . import effective, involvement, pressure`.
  - **`clock` never imports `pressure` or `drivers`.** Neither module imports `suggest`, `briefing`, `context`, `chores`, `scene_refs`, `undo` or `continuity.review`.
- **Read-only:** `pressure` and `drivers` write nothing and take no lock. They are **not** added to `locks.DOMAIN_MODULES`, `OUTSIDE_DOMAIN` or `UNREVIEWED`; the phantom-module check fails a declared non-mutator.
- **Pure read:** no model, embedding or network call anywhere in this slice. `embed_space.resolve()` only reads config.
- **Identity law (spec §7.1, §27):** for a campaign with no live aliases:
  - `effective.threads/commitments` equal the physical rows plus `aliases: []`;
  - `effective.render_threads(cid, w) == plot.render_open(cid, w)` and `effective.render_commitments(cid, w) == commitments.render_open(cid, w)` for `w` in both values;
  - every assembled prompt is byte-identical.
- **Ref forms (spec §4):**
  - `event:<eid>`;
  - `holiday:<fixed>:<name>`, built **only** with `notices.holiday_key(fixed, name)`;
  - `birthday:<kind>:<id>:<fixed>` (exact or yearless);
  - `birthday:<kind>:<id>:month:<year>-<monthkey>` (month-only, which "has no day; never given an invented one");
  - `thread:<id>`, `commitment:<id>`.

  `<kind>` is `characters` or `pcs`. Never use `notices.event_key` for a continuity ref.
- **Pressure contract (spec §13.4, verbatim):**
  - `kind` is `event` | `holiday` | `birthday` | `deadline` (parseable due) | `linked_deadline` (via a temporal link).
  - `fixed` is the target's fixed day on the primary provider; `null` for a month-only birthday or an unparseable due.
  - `in_days` is `fixed(target) − fixed(now)`: negative in the past, `null` when `fixed` is null.
  - `relation` is `on` for events, holidays, exact/yearless birthdays and deadlines; `in_month` for month-only birthdays; `before` | `by` | `on` | `after` for linked deadlines.
- **State precedence (spec §13.4, first match wins):**
  1. `overdue`: a deadline kind with `in_days < 0`, on an unresolved commitment.
  2. `passed`: an unfired event whose `passed` reading is true.
  3. `today`: `in_days == 0`, or an event on today, fired ones included.
  4. `due_soon`: a deadline kind with `0 < in_days ≤ warn_days`; never when `warn_days == 0`.
  5. `upcoming`: `0 < in_days ≤ UPCOMING_WINDOW_DAYS`.
  6. `stale`: a thread or commitment whose aging state is `stale` (driver pressure only, §14).
  7. `ok`.

  **High pressure** is `overdue`, `today` or `due_soon`.
- **Link deadlines (spec §13.5, verbatim):** for `commitment --R--> event` with the event dated D:
  - the effective deadline is D−1 for `before`, and D for `by` and `on`;
  - `after` sets no deadline: it yields only an `upcoming` item, with `in_days` to D;
  - an event is **reached** when its `fired` stamp is set or its `passed` reading is true;
  - an unresolved canonical commitment with a before/by/on link to a reached event has state `overdue`;
  - when a commitment has both a parseable `due` and link deadlines, the earliest wins, and the item carries `due_text`;
  - dangling or unparseable links contribute nothing.
- **Horizons (spec §13.2):**
  - the warning horizon is `calendars.warn_days(croot)`, where `0` means warnings off;
  - the suggestion horizon is `calendars.UPCOMING_WINDOW_DAYS` (30);
  - every parseable deadline and every unfired future event is listed regardless of horizon; holidays and birthdays are limited to the suggestion horizon;
  - free-text dues stay non-arithmetic.
- **No fabricated dates (spec §3.8, §26):** comparisons are integer fixed days on the **primary** provider (`calendars.fixed_of`). A calendar failure degrades to undated items.
- **Fail soft per source (spec §3.9, §13.1):** each pressure source and each driver input sits in its own `try/except Exception`. A provider can be user plugin code that raises anything, and `calendars.primary_provider` catches only `CalendarError`/`KeyError`.
- **Driver shape (spec §14):** `{ref, kind, label, summary, actors, status, pressure: {state, in_days, friendly}, time_anchors, links: [{id, relation, other, direction}]}`. `AnchorOption = {ref, kind: "event"|"birthday"|"holiday", label, native, friendly, fixed|null, in_days|null, precision: "exact"|"yearless"|"month"}`.
- **Copy:**
  - Todo `owed` reads `f"{n} open commitment{'s' if n != 1 else ''} with a deadline"`;
  - the rail ledger tail noun is `"open commitments"`;
  - the Ledger note part is `Merged: <alias titles joined by ", ">`.
- **Lint ratchet:** `make check-lint` and `make check-mypy` must not grow `lint-baselines/*.json`. If a change resolves a recorded finding, run `make baseline` and commit the smaller file with the fix.
- **Commands:**
  - backend: from `backend/`, `PYTHONPATH=src .venv/bin/python -m pytest <files> -q`;
  - frontend: from `frontend/`, `npx vitest run <file>`;
  - one command per purpose, and a `-k` filter is never combined with a file list it would deselect.

## Review Focus

1. **A calendar plugin whose constructor raises a non-`CalendarError`** (a hand-written `<home>/calendars/*.py`).
   - Expected: `pressure.build` still answers, with undated events, no holidays, birthdays or deadlines, and `fixed: None`. `GET /continuity/drivers` answers 200. The ledger keeps its rows.
   - Pinned in Task 5 (`test_a_raising_plugin_degrades_to_undated_items`) and Task 7 (`test_drivers_route_survives_a_raising_plugin`).
2. **A clock moved backwards past an event it already fired** (advance past the coronation, then `clock.advance` to an earlier day; `observe` is forward-only).
   - Expected: the fired event is not re-listed as upcoming, because `events.upcoming` excludes fired rows. If the clock now sits on its day, it is listed as `today`. A commitment linked `before` it stays `overdue`, because it is reached.
   - Pinned in Task 6 (`test_backwards_clock_keeps_a_fired_event_reached`).
3. **A full Feb-29 birthdate in a common year, and a leap-only Hebrew month (`--Adar1`) in a common year.**
   - Expected: no occurrence at all, never an invented Feb 28, Mar 1 or Adar. This is today's `_when` behaviour, and the projection must keep it.
   - Pinned in Task 1 (`test_leap_only_birthdays_never_invent_a_day`).
4. **A due set only on a merged-away source commitment** (the canonical's `due` is empty).
   - Expected: the canonical's due is authoritative, so the commitment has no deadline item, Todo does not count it, and the source is never a second row or a second count. A due is never inherited silently (§5.1).
   - Pinned in Task 6 (`test_a_merged_source_due_is_not_inherited`) and Task 9 (`test_owed_counts_canonical_commitments`).
5. **A garbled continuity.json (`"{ no"`) while a turn is composed.**
   - Expected: aliases read as empty, so every prompt snippet equals the physical render; drivers still answer; the shell count is the physical count; nothing raises.
   - Pinned in Task 2 (`test_render_helpers_ignore_a_garbled_continuity_file`) and Task 7 (`test_snapshot_survives_garbled_continuity_and_ledgers`).

---

## Decisions (and why)

1. **Module edges** are the Global Constraints import lines above. `drivers` imports `embed_space` so that `drivers.matching()` is the one definition of the `matching` field. `routes/continuity.py`'s `_matching` is deleted and calls it, so `GET /continuity` and the drivers read cannot disagree. Spec §7.0's table omits `embed_space` for drivers but §14 requires `matching`; the edge is acyclic (`embed_space`'s closure has no continuity module).
2. **`birthdays.occurrences` signature:** `occurrences(cid, now_fixed, window=calendars.UPCOMING_WINDOW_DAYS, *, provider=None, roster=None, visible_characters=True)`.
   - The spec's three positional parameters stay first.
   - `roster` and `visible_characters` are needed because `upcoming`'s callers choose them (suggestions pass the appearance roster with `visible_characters=True`). `None` means `appearances_cast.roster(cid)`.
   - `provider` lets `pressure` resolve the plugin once.
   - The scan covers `[now_fixed, now_fixed + window]` **inclusive of today**, which is `_when`'s window. Events and holidays use `(now, now+w]`; birthdays have always included today, and the projection must keep `"today"`.
3. **The occurrence row** is spec §13.6's fields plus `month_name`, `native` and `friendly`:
   - `{ref, name, actor, precision, fixed, month_key, month_name, in_days, age, native, friendly}`.
   - `month_name` is required for `upcoming`'s byte-identical `"in <month_name>"`. It comes from `provider.describe(day)["month_name"]`, exactly as `_when` reads it today.
   - `native` and `friendly` feed `AnchorOption`.
   - For month precision: `fixed`, `in_days` and `age` are `None`; `native` is `""`; `friendly` is `f"{month_name} {year}"`; `month_key` is `months(year)[i]["key"]`.
   - Rows come in gather order, and within an actor in ascending day order.
4. **The pressure item** is spec §13's fields plus three typed extras, `precision`, `actor` and `age`, which drivers and anchors need:
   - `{ref, kind, label, native, friendly, fixed, in_days, relation, state, subject, due_text, precision, actor, age}`.
   - `ref` names the dated thing the item measures from:

     | Kind | `ref` | `subject` |
     |---|---|---|
     | event | `event:<eid>` | null |
     | holiday | `notices.holiday_key(fixed, name)` | null |
     | birthday | the occurrence ref | null |
     | `deadline` | `commitment:<id>` | the commitment ref |
     | `linked_deadline` | `event:<eid>` | the commitment ref |

   - `label` is the event name, the holiday name, the actor name, or the commitment title for both deadline kinds.
   - `due_text` is the commitment's stored `due` on deadline kinds, and `""` otherwise.
   - `precision` is the occurrence's for birthdays, `"exact"` for anything else with a `fixed`, and `None` when `fixed` is `None`.
   - `actor` is the birthday's `<kind>:<id>`, else `None`. `age` is the exact birthday's age, else `None`.
5. **Item order** is `(fixed is None, fixed or 0, KINDS.index(kind), ref, subject or "")`: the fixed-day axis first (spec §28.5), nulls last, and deterministic ties.
6. **Which events are items:**
   - every **unfired** event (future, today, passed, or undated);
   - plus **fired** events whose fixed day equals today (`state` is `today`).

   A fired past or future event is not listed, which matches `events.upcoming`/`on_day`. The source is `events.list_events(cid, provider, now_fixed)`, with `fixed` recomputed by `calendars.fixed_of` under `try`. With no provider or no `now`, events are listed with `in_days: None` and state `ok`.
7. **Today's holidays** come from `calendars.upcoming_holidays(cfg, provider.format(now_fixed - 1), horizon + 1)`. This composes the public reader rather than the private `base._upcoming`, and it covers `[now, now+horizon]` with fixed days.
8. **Deadlines:**
   - one deadline item per live canonical commitment: the earliest of its parseable `due` (fixed derived from `aging.age` as `now_fixed + due_in` or `now_fixed − days_over`) and its before/by/on links to dated events. Ties prefer `deadline`, then the lowest link id.
   - one separate `linked_deadline` item per `after` link whose event is dated, not reached, and `in_days ≥ 0`.
   - Thread→event temporal links never make deadlines; they become thread drivers' `time_anchors`.
   - Deadline items require a readable `now`.
9. **`horizon`** (default `UPCOMING_WINDOW_DAYS`) bounds only the holiday and birthday sources. **`warn_days`** only gates `due_soon`. `upcoming` is always `0 < in_days ≤ UPCOMING_WINDOW_DAYS` per the contract, whatever `horizon` is.
10. **Driver pressure:**
    - **Threads:** aging `stale` gives `stale`, otherwise `ok`, with `in_days: None` and `friendly: ""`.
    - **Commitments:** take the minimum, by `PRESSURE_STATES` index, over the states of every pressure item whose `subject` is the commitment's ref, plus `stale` when aging says stale. Its `in_days`/`friendly` come from the chosen item, or are `None`/`""` for a bare `stale`/`ok`.
    - **Temporal drivers:** the item's own state, `in_days` and `friendly`.

    Aging runs on effective rows, so it measures the merged `last_scene` (spec §12.5).
11. **Drivers** come from every live effective thread, every live effective commitment, and every `event`/`holiday`/`birthday` pressure item.
    - **Order:** `(PRESSURE_STATES.index(state), DRIVER_KINDS.index(kind), in_days is None, in_days or 0, ref)`.
    - **Anchors:** the temporal drivers that can constrain a date. That means every event with a `fixed` and state other than `passed` (any distance: an event is the campaign's own finite list), plus every holiday and birthday (already bounded by the horizon).
    - **Anchor order:** by `in_days`, nulls last, then `DRIVER_KINDS` index, then ref.
    - Undated events are drivers but never anchors: no relation can be derived against them.
12. **Offscreen:**
    - **Dropped actors:** the actor tokens dropped are every `pcs:*` plus every roster token whose role is `player`. That is the same set `suggest.build_snapshot` strips, recomputed from `appearances_cast.roster` because `drivers` may not import `suggest`.
    - **PC birthday drivers:** a PC's birthday driver survives with `actors: []`, because the suggestion snapshot's birthdays are not offscreen-filtered either.
13. **Link direction:**
    - `"out"` when the driver's ref is `a`;
    - `"in"` when it is `b`;
    - `"both"` for `related_to` (`effective.RELATIONS[...][2]` is `False`).

    `other` is the far endpoint. Links come from `effective.links` (canonical endpoints; broken and duplicate links are already excluded).
14. **The drivers route takes no lock**, like `post_scene_suggestions`/`build_snapshot`, which `drivers.snapshot` will feed in Slice E. Pressure resolves plugin calendars, and every calendar caller in this app resolves outside the campaign lock.
15. **Payloads:**
    - Briefing rows strip `aliases`, so that `BriefingRow` and its all-strings test are unchanged; briefing renders no merge note.
    - The ledger, digest and suggestion snapshot carry `aliases` (additive JSON).
16. **Todo `owed`:**
    - **What counts:** canonical live commitments with a non-empty `due` **or** a before/by/on effective link to an event. §13.5 makes those deadlines, and Todo and pressure must not disagree about which commitments have one.
    - **No calendar work:** it calls neither `pressure` nor `aging`. Deadline pressure resolves the provider, which is plugin code, and `live('')` runs every builder for every campaign. §18.3's overdue/due-soon split is a "may", and taking it would put plugin code on the global To do page.
17. **Shell `ledger_open`:** the count is `len(effective.commitments(cid))` inside `try`, and it answers `None` on failure. This follows the shell's cost rule, and it fixes today's 500 on a garbled commitments.json. The TS type becomes `number | null`. CampaignHub's card is retitled "Open commitments" (the same mislabel as the rail) and renders the unknown text for `null`.
18. **Existing assertions that change, and why:**
    - **`test_actor_context.py:242-243`:** its monkeypatch targets `assembly.plot`/`assembly.commitments`, which `assemble` no longer imports. It is retargeted to `effective.render_*`.
    - **`test_actor_scoped_skip.py`:** the forbidden list's `plot`/`commitments` `render_open` entries become vacuous, and are replaced with `effective.render_threads`/`render_commitments`.
    - **`test_ledger_route.py:187,212`:** rows gain `aliases: []` (§12.5).
    - **`AppRail.test.tsx:68`:** the rail label changes.
    - **`TodoView.test.tsx:136`:** the cosmetic mock text changes.
    - **`test_frozen_campaign.py`:** the module set gains `continuity`.
    - **`snapshot.json`:** it gains new `continuity.*` keys only.

    Nothing else moves for a no-alias campaign.

---

## File structure

| File | Responsibility |
|---|---|
| `store/birthdays.py` | `gather` rows gain `ref`; a private `_scan`; `occurrences`; `upcoming` and `_when` become projections of it |
| `store/continuity/effective.py` | `render_threads`, `render_commitments`; a no-alias fast path in `_groups` |
| `store/continuity/involvement.py` | `group_touched` (briefing's touched scenes keyed by canonical id) |
| `store/continuity/pressure.py` (new) | `SOURCES`/`KINDS`, `PRESSURE_STATES`, `SORT_ORDER`, `HIGH_PRESSURE`, `state_of`, `build` |
| `store/continuity/drivers.py` (new) | `DRIVER_KINDS`, `matching`, `snapshot` |
| `store/context/assemble.py`, `store/absorb/snapshots.py`, `store/suggest.py` | Prompt readers use `effective` |
| `store/briefing.py`, `store/clock.py` | Briefing and digest read effective rows |
| `routes/continuity.py` | `GET /continuity/drivers`; `_matching` → `drivers.matching` |
| `routes/campaigns.py` | `get_ledger` returns effective rows |
| `routes/todo.py`, `routes/shell.py` | `owed` canonical and relabelled; `ledger_open` effective and nullable |
| `scripts/verify_templates.py` | effective render checks, plus a merged-campaign check |
| `tests/fixtures/frozen_campaign/sweep.py`, `snapshot.json` | continuity projections; deliberate regeneration |
| `frontend/src/api/types.ts`, `routes/LedgerView.tsx`, `shell/rail.ts`, `routes/CampaignHub.tsx` | `aliases?`, the Merged note, the rail noun, nullable `ledger_open` |

Backend paths are relative to `backend/src/grimoire/`, and tests to `backend/tests/`, unless they start with `frontend/` or `scripts/`.

---

### Task 1: `birthdays.occurrences`, with `upcoming` as its projection

**Files:**
- Modify: `store/birthdays.py`
- Test: `tests/test_birthday_occurrences.py` (new). These must pass **unmodified**: `tests/test_suggest_store.py`, `tests/test_birthdate.py`, `tests/test_calendar_plugins.py` and `tests/test_clock_store.py`.

**Interfaces:**
- Produces:
  - `gather(...)`: rows are `{name, birth, ref}`. `ref` is `f"{kind}:{id}"` for roster rows, and `f"characters:{id}"` for visible characters.
  - `_scan(provider, birth: str, now_fixed: int, window: int) -> list[dict]`. Private, and the one walk.
    - It walks `d in range(max(window, 0) + 1)` with `_when`'s exact per-day rules: `is_anniversary` for day-bearing births, the case-folded month-key match for day-less ones, and nothing for a year alone.
    - It returns every hit as `{precision, fixed, year, month_key, month_name, in_days, age}`. A month-only birth gives one hit per distinct `(year, key)`, at its first matching day.
    - It raises `CalendarError` where `_when` does today.
  - `_when(provider, birth, now_fixed) -> tuple[str | None, int | None]`: the same contract (pinned by `test_calendar_plugins`). It is the first `_scan(..., UPCOMING_WINDOW_DAYS)` hit, labelled.
  - `occurrences(cid, now_fixed: int, window: int = calendars.UPCOMING_WINDOW_DAYS, *, provider=None, roster: list[dict] | None = None, visible_characters: bool = True) -> list[dict]`.
    - Each row is `{ref, name, actor, precision, fixed, month_key, month_name, in_days, age, native, friendly}` (Decision 3).
    - It returns `[]` when no provider resolves. A per-row `CalendarError` skips that actor only.
  - `upcoming(cid, now, roster, *, visible_characters=False) -> [{name, age, when}]`: unchanged output, built from `occurrences`. It takes the first occurrence per `actor`, in gather order.
    - `when` is `"today"` / `f"in {in_days} days"` for day precision.
    - For month precision it is `"this month"` when the occurrence's `(year, month_key)` is now's (`describe(now_fixed)["year"]` and `months(year)[month-1]["key"]`), else `f"in {month_name}"`.

- [ ] **Step 1: Write the failing tests.** The `_campaign` helper copies `test_suggest_store._campaign`. Characters are made with `overlay.create_character(cid, "Mara")` and `characters.set_birthdate(campaigns.campaign_root(cid), aid, ...)`, and the clock is set with `clock.advance(cid, to="2026-05-08")`. `F(native)` means `calendars.fixed_of(provider, native)`.
  - `test_gather_rows_carry_their_ref`: every row has `ref == f"characters:{aid}"`.
  - `test_exact_birthday_occurrence`: `"1985-05-09"` gives `[{"ref": f"birthday:characters:{aid}:{F('2026-05-09')}", "name": "Mara", "actor": f"characters:{aid}", "precision": "exact", "fixed": F("2026-05-09"), "month_key": None, "month_name": None, "in_days": 1, "age": 41, "native": "2026-05-09", "friendly": "9 May 2026"}]`.
  - `test_yearless_birthday_has_no_age`: `"--05-09"` gives `precision "yearless"`, `age None`, `in_days 1`.
  - `test_month_only_birthday_has_no_day`: `"--05"` gives `ref == f"birthday:characters:{aid}:month:2026-05"`, `fixed None`, `in_days None`, `age None`, `month_key "05"`, `month_name "May"`, `native ""`, `friendly "May 2026"`, `precision "month"`.
  - `test_today_is_inside_the_window`: `"--05-08"` gives `in_days == 0`; `window=0` keeps it; `window=0` drops `"--05-09"`.
  - `test_year_only_and_unmatched_keys_yield_nothing`: each of `"1985"`, `"--5"` and `"--Mirtul"` gives `[]`.
  - `test_leap_only_birthdays_never_invent_a_day` (Review Focus 3):
    - Gregorian, from 2027-02-20: `"2000-02-29"` and `"--02-29"` give `[]`.
    - Hebrew (`_campaign(..., calendar="hebrew")`, `clock.advance(cid, to="5786-Shevat-20")`): `"--Adar1"` gives `[]`; `"--Adar"` gives one month occurrence with `month_key == "Adar"`; `"--Adar2-14"` gives a yearless occurrence with `in_days == 24`.
  - `test_a_bad_birthdate_skips_only_that_actor`: Mara `"--05-32"` and Winifred `"--05-09"` give Winifred's row only.
  - `test_upcoming_is_a_projection_of_occurrences`: monkeypatch `birthdays.occurrences` to return two exact rows for one actor plus one month row (whose `(year, month_key)` is not now's). Then `upcoming(...)` returns one row for the actor (the first) and `{"name": ..., "age": None, "when": "in June"}` for the month row.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_birthday_occurrences.py -q`. Expected: FAIL, `AttributeError: module 'grimoire.store.birthdays' has no attribute 'occurrences'`.
- [ ] **Step 3: Implement** `_scan`, `occurrences`, the `gather` `ref`, and `upcoming`/`_when` over them, in `store/birthdays.py`. Update the module docstring's caller list: `occurrences` is the scan, and `upcoming` and pressure project it.
- [ ] **Step 4: Run the new tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_birthday_occurrences.py -q`. Expected: PASS.
- [ ] **Step 5: Prove the existing birthday text is unchanged.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_suggest_store.py tests/test_birthdate.py tests/test_calendar_plugins.py tests/test_clock_store.py tests/test_import_guard.py -q`. Expected: PASS, with no test file edited ("in 1 days", "this month", age 41 and the Hebrew `crossed` pin still hold).
- [ ] **Step 6: Commit:** `feat(birthdays): occurrence scan with refs; upcoming becomes its projection`

---

### Task 2: Effective render helpers, the no-alias fast path, and canonical touched scenes

**Files:**
- Modify: `store/continuity/effective.py`, `store/continuity/involvement.py`
- Test: `tests/test_continuity_effective.py`, `tests/test_continuity_involvement.py`

**Interfaces:**
- Produces:
  - `effective.render_threads(cid, with_id: bool) -> list[str]` and `effective.render_commitments(cid, with_id: bool) -> list[str]`.
    - They render `snippets/plot_thread_line/{absorb|context}.j2` (`t=`) / `snippets/commitment_line/{absorb|context}.j2` (`c=`) over `threads(cid)` / `commitments(cid)`.
    - They return `[]` on any exception, the `render_open` contract.
    - Import `from ... import prompts`. `plot.render_open` and `commitments.render_open` stay physical (§7.0).
  - **`_groups` fast path:** when `doc.read(cid)["aliases"]` is empty, return `{}` without calling `live_canon`, so no `Ledgers.load` happens. The output is identical by construction; this only removes three ledger reads from every effective call on the common path (the shell badge runs on every navigation).
  - `involvement.group_touched(cid, kind: str, ledgers: effective.Ledgers | None = None) -> dict[str, set[str]]`.
    - It computes `touched_scenes(raw)` over `ledgers.threads` / `ledgers.commitments`. An unreadable ledger is `None`, which gives `{}`.
    - It then folds each physical id into its `live_canon` canonical id, keyed by **bare** id.
    - With no aliases it equals `touched_scenes(plot.read(cid))` exactly, raw scene strings included.

- [ ] **Step 1: Write the failing tests.**
  - In `test_continuity_effective.py`:
    - `test_render_helpers_obey_identity_law`: the `test_identity_law_threads` / `_commitments` fixtures (out-of-order beats, a status-only move, `"Closed"`, a non-dict record). For `w in (True, False)`:
      - `effective.render_threads(cid, w) == store.plot.render_open(cid, w)`;
      - `effective.render_commitments(cid, w) == store.commitments.render_open(cid, w)`.
    - `test_render_helpers_show_canonical_ids_only`: `maras-map` aliased into `winifreds-chart`. `render_threads(cid, True)` has one line, starting `"winifreds-chart: Winifred's chart ("`, and no line contains `"maras-map"`.
    - `test_render_helpers_tolerate_a_garbled_ledger`: plot.json `"{ no"` gives `render_threads(cid, True) == []`, and commitments still render.
    - `test_render_helpers_ignore_a_garbled_continuity_file` (Review Focus 5): continuity.json `"{ no"` with two threads gives the physical render exactly.
    - `test_no_alias_projection_never_loads_the_ledgers`: monkeypatch `effective.Ledgers.load` to raise `AssertionError`. Then `effective.threads(cid)` equals the identity-law rows, and `render_commitments(cid, False)` equals the physical render.
  - In `test_continuity_involvement.py`:
    - `test_group_touched_equals_touched_scenes_without_aliases`: equality with `involvement.touched_scenes(store.plot.read(cid))`.
    - `test_group_touched_folds_members_into_the_canonical`: `maras-map` (beats at S1 and S3) aliased into `winifreds-chart` (beat at S2) gives `{"winifreds-chart": {S1, S2, S3}}`. Any other thread keeps its own key.
    - `test_group_touched_garbled_ledger_is_empty`.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_effective.py tests/test_continuity_involvement.py -q`. Expected: FAIL, `AttributeError` for `render_threads` / `group_touched`.
- [ ] **Step 3: Implement** the two render helpers, the `_groups` fast path and `group_touched`.
- [ ] **Step 4: Run them and confirm they pass.** Use the same command. Expected: PASS, with every Slice A test in both files still green.
- [ ] **Step 5: Run the import guard.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_import_guard.py -q`. Expected: PASS.
- [ ] **Step 6: Commit:** `feat(continuity): effective snippet renders and a no-alias fast path`

---

### Task 3: Prompt readers switch to the effective renders, proven byte-identical

**Files:**
- Modify:
  - `store/context/assemble.py`: lines 562-563, and drop the now-unused `commitments`/`plot` imports;
  - `store/absorb/snapshots.py`: `plot_snapshot`, `commitment_snapshot`, and drop the unused imports;
  - `store/suggest.py`: `build_snapshot`'s `plot.open_threads` becomes `effective.threads`, and the unused `plot` import is dropped;
  - `scripts/verify_templates.py`.
- Test: `tests/test_actor_context.py`, `tests/test_actor_scoped_skip.py`, `tests/test_absorb_store.py`, `tests/test_suggest_store.py`

**Interfaces:**
- Consumes: `effective.render_threads`, `effective.render_commitments`, `effective.threads` (Task 2).
- Produces:
  - `assemble._campaign_view` sets `"plot_lines": effective.render_threads(cid, with_id=False)` and `"commitment_lines": effective.render_commitments(cid, with_id=False)`.
  - The absorb snapshots use `with_id=True`.
  - `build_snapshot["open_threads"]` is the effective rows, plus `dormancy`.
  - Imports: `from ..continuity import effective` in `context/` and `absorb/`; `from .continuity import effective` in `suggest`.

- [ ] **Step 1: Write the failing tests.**
  - `test_actor_context.py::test_unattributed_campaign_material_never_reaches_writer`: retarget its two monkeypatches to `assembly.effective`, `"render_threads"` / `"render_commitments"`. This is RED, because `assemble` has no `effective` yet.
  - `test_actor_scoped_skip.py::test_what_an_npc_compose_discards_is_never_computed`: in the forbidden list, replace `(plot, "render_open")` and `(commitments, "render_open")` with `(effective, "render_threads")` and `(effective, "render_commitments")`. This stays GREEN until the switch, so it is not a RED test. It is there so the guard is not vacuous after it.
  - `test_absorb_store.py::test_absorb_snapshots_show_canonical_ids_only`: after the alias `thread:maras-map → thread:winifreds-chart` (and the same for commitments), `plot_snapshot(cid)` names only `winifreds-chart`.
  - `test_suggest_store.py::test_snapshot_threads_are_canonical`: an aliased pair gives `[t["id"] for t in snap["open_threads"]] == ["winifreds-chart"]`, with `dormancy` present.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_actor_context.py tests/test_absorb_store.py tests/test_suggest_store.py -q`. Expected: FAIL (`AttributeError: ... has no attribute 'effective'`, and two canonical-id assertions).
- [ ] **Step 3: Implement the three switches.**
- [ ] **Step 4: Extend `scripts/verify_templates.py`.**
  - Import `from grimoire.store.continuity import doc as continuity_doc, effective`.
  - In `gather()` (lines 894-895), use `effective.render_threads` / `render_commitments`, so the expectation mirrors `assemble`.
  - Beside the four `render_open` checks (lines 1144-1154), add:
    - `check("plot lines (effective == physical, no aliases)", "\n".join(effective.render_threads(cid, w)), "\n".join(plot.render_open(cid, w)))` for both `w`, and the commitment equivalent. This is the identity law on the full fixture.
    - Then, on a second campaign `cid_merged = campaigns.create_campaign("Saltmarch", wid)`, created after every other check so nothing else sees it:
      - two threads, `"mara-s-map"` and `"winifred-s-chart"`, and two commitments, with `continuity_doc.put_alias` from each first id to the second;
      - `check("plot lines (effective, merged)", ...)` against the template rendered over `effective.threads(cid_merged)` in both forms, and the same for commitments;
      - `check("merged ids are canonical", ",".join(t["id"] for t in effective.threads(cid_merged)), "winifred-s-chart")`.
- [ ] **Step 5: Run the switched consumers' tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_actor_context.py tests/test_actor_scoped_skip.py tests/test_absorb_store.py tests/test_suggest_store.py tests/test_context.py -q`. Expected: PASS.
- [ ] **Step 6: Prove the identity law on whole prompts.**
  - Run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_frozen_campaign.py tests/test_llm_fakes.py -q`. Expected: PASS **with `snapshot.json` untouched**: every assembled prompt in the frozen fixture is byte-identical.
  - Then, from the repo root, run `make check-templates PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 7: Run the evals and the import guard.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_evals.py tests/test_eval_graders.py tests/test_import_guard.py -q`. Expected: PASS (the offline evals render the absorb prompt through `absorb_store.plot_snapshot`).
- [ ] **Step 8: Commit:** `feat(continuity): prompts read canonical threads and commitments`

---

### Task 4: Briefing and the advance digest read effective rows

**Files:**
- Modify:
  - `store/briefing.py`: its reads at 170-173, and its imports (drop `plot` and `commitments`; `from .continuity import effective, involvement`);
  - `store/clock.py`: the reads at 441-448, and its imports (drop `plot` and `commitments`; add `from .continuity import effective`).
- Test: `tests/test_briefing_route.py` (append), `tests/test_clock_store.py` (append). Every existing test in both files passes unmodified.

**Interfaces:**
- Consumes: `effective.threads`, `effective.commitments`, `involvement.group_touched` (Task 2).
- Produces:
  - **Briefing:**
    - `threads = _tolerant(lambda: _physical_keys(effective.threads(cid)), [])`, and the same for commitments;
    - `thread_scenes = _tolerant(lambda: involvement.group_touched(cid, "thread"), {})`, and the same for commitments.
    - Each of the four reads keeps its own `_tolerant`, preserving the comment's rows-versus-flags isolation.
    - `_physical_keys(rows) -> list[dict]` (private) drops the `aliases` key (Decision 15).
  - **Digest:** `"open_threads"` / `"commitments"` are `effective.threads(cid)` / `effective.commitments(cid)`. Each read keeps its own `try` with an `[]` fallback, and is aged by the same `aging.annotate`. The rows carry `aliases`.

- [ ] **Step 1: Write the failing tests.**
  - `test_briefing_route.py::test_a_merged_thread_briefs_once_under_its_canonical`:
    - `"Mara's map"` (beat in a scene Seraphine played) is aliased into `"Winifred's chart"` (a beat elsewhere).
    - The briefing's `plot` has exactly one row, with `id == "winifred-s-chart"` and `involves == ["Seraphine"]` (the flag comes from the merged source's scene).
    - `"aliases" not in row`.
  - `test_clock_store.py::test_digest_owes_canonical_commitments`:
    - Two commitments are aliased; then `clock.preview(cid, days=5)["commitments"]` (`preview` returns the digest itself) has one row, with the canonical id and `aliases == [{"ref": "commitment:<src>", "title": ..., "status": "open"}]`.
    - the preview's `["aging"]` counts it once.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_briefing_route.py tests/test_clock_store.py -q`. Expected: the two new tests FAIL, with two rows instead of one.
- [ ] **Step 3: Implement both switches.** Check that `briefing.py` and `clock.py` reference `plot.` / `commitments.` only in docstrings afterwards: `grep -n "plot\.\|commitments\." backend/src/grimoire/store/briefing.py backend/src/grimoire/store/clock.py`.
- [ ] **Step 4: Run the switched modules' tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_briefing_route.py tests/test_clock_store.py tests/test_aging_store.py tests/test_events_routes.py -q`. Expected: PASS.
- [ ] **Step 5: Run the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_import_guard.py tests/test_frozen_campaign.py -q`. Expected: PASS (`clock` → `effective` is acyclic; the snapshot is still untouched).
- [ ] **Step 6: Commit:** `feat(continuity): briefing and advance digest read canonical records`

---

### Task 5: `continuity.pressure` — the contract, and the event, holiday and birthday sources

**Files:**
- Create: `store/continuity/pressure.py`
- Test: `tests/test_continuity_pressure.py` (new)

**Interfaces:**
- Consumes:
  - `birthdays.occurrences` (Task 1);
  - `events.list_events`, `calendars.{primary_provider, read_calendar, upcoming_holidays, fixed_of, warn_days, UPCOMING_WINDOW_DAYS}`;
  - `notices.holiday_key`, `clock.now`.
- Produces:
  - `SOURCES = KINDS = ("event", "holiday", "birthday", "deadline", "linked_deadline")`; `ALL = frozenset(SOURCES)`.
  - `PRESSURE_STATES = ("overdue", "passed", "today", "due_soon", "upcoming", "stale", "ok")`. This is the decision precedence; `index()` is the "more urgent" order everywhere in the backend.
  - `SORT_ORDER = ("overdue", "today", "due_soon", "upcoming", "passed", "stale", "ok")`. This is the §16.2 UI order, declared here so Slice E's TS mirrors one tuple.
  - `HIGH_PRESSURE = frozenset({"overdue", "today", "due_soon"})`.
  - `state_of(kind: str, relation: str, in_days: int | None, *, warn_days: int, passed: bool = False, reached: bool = False) -> str`. Pure. It applies the Global Constraints precedence, where *a deadline kind* means `kind == "deadline"`, or `kind == "linked_deadline"` and `relation in {"before", "by", "on"}`. `reached=True` forces `overdue` on a deadline kind. It never returns `stale`.
  - `build(cid, now: str | None = None, horizon: int | None = None, sources=ALL) -> {"now", "friendly", "fixed", "items"}`.
    - An unknown source name raises `ValueError`.
    - `now=None` reads `clock.now(cid)` under `try`, with `""` on failure.
    - `horizon=None` means `UPCOMING_WINDOW_DAYS`; a negative value is clamped to 0.
    - `fixed` and `friendly` (`describe(fixed)["friendly"]`) are `None`/`""` when unreadable.
    - Items have Decision 4's shape, in Decision 5's order.
    - **Sources:** this task builds `event` (Decision 6), `holiday` (Decision 7) and `birthday` (`birthdays.occurrences(cid, now_fixed, horizon, provider=provider)`). Each one runs only when requested, and inside its own `try/except Exception`.
    - **Provider:** resolved once, inside `try/except Exception`, with `None` on failure.
    - **Never raises** for an existing campaign.

- [ ] **Step 1: Write the failing tests.** The `_campaign(monkeypatch, tmp_path, holidays=(), region="", warn=None)` helper copies `test_notices_store._campaign`, which uses `{"name", "month", "day"}` custom rules, and `clock.advance(cid, to="2026-05-10")`. Events are created with `events.create(cid, name, date)`.
  - `test_pressure_tuples_are_pinned`: the four tuples equal the literals above (§20 review pin).
  - `test_state_precedence` is parametrized over `(kind, relation, in_days, warn, passed, reached) → state`:

    | kind | relation | in_days | warn | passed | reached | state |
    |---|---|---|---|---|---|---|
    | deadline | on | -1 | 7 | | | overdue |
    | deadline | on | 0 | 7 | | | today |
    | deadline | on | 7 | 7 | | | due_soon |
    | deadline | on | 8 | 7 | | | upcoming |
    | deadline | on | 30 | 7 | | | upcoming |
    | deadline | on | 31 | 7 | | | ok |
    | deadline | on | 3 | **0** | | | upcoming |
    | deadline | on | None | 7 | | | ok |
    | linked_deadline | before | -3 | 7 | | | overdue |
    | linked_deadline | by | 5 | 7 | | True | overdue |
    | linked_deadline | after | -3 | 7 | | | ok |
    | linked_deadline | after | 2 | 7 | | | upcoming |
    | event | on | -2 | 7 | True | | passed |
    | event | on | 0 | 7 | | | today |
    | holiday | on | 30 | 7 | | | upcoming |
    | birthday | in_month | None | 7 | | | ok |

  - `test_several_holidays_are_kept_and_today_included`: rules on 05-10, 05-12 and 05-20 give holiday items with `in_days` `[0, 2, 10]` and states `["today", "upcoming", "upcoming"]`. Each `ref == notices.holiday_key(fixed, name)`.
  - `test_holiday_ref_matches_the_notice_key_for_an_untrimmed_name`: the rule name `"Saltmarch Eve "` gives `ref == notices.holiday_key(fixed, "Saltmarch Eve ")`, which ends `":Saltmarch Eve"`.
  - `test_holidays_and_birthdays_stop_at_the_horizon`: a rule on 06-20 (41 days) is absent with the default horizon and present with `horizon=45`.
  - `test_events_listed_regardless_of_horizon_and_ordered_on_the_fixed_axis`:
    - The coronation on 2026-08-01 (state `ok`, `in_days 83`) and Mara's audience on 2026-05-12, plus a rule holiday on 05-12.
    - The order is the audience event, then the 05-12 holiday (same fixed day, `KINDS` order), ..., then the coronation.
    - Event items carry `relation "on"`, `subject None`, `due_text ""` and `precision "exact"`.
  - `test_a_passed_unfired_event_is_passed_and_a_fired_one_is_gone`: create the event 2026-05-05 after the clock is at 05-10; it gives `state "passed"`, `in_days -5`. Then an event dated 05-08 that was created *before* the clock advanced to 05-10, and so was fired by that advance, is absent.
  - `test_a_fired_event_on_today_is_today`: advance onto its day; it is listed with `state "today"`.
  - `test_birthday_items_carry_precision_actor_and_age`: Mara `"1985-05-12"` gives a `birthday` item with `relation "on"`, `in_days 2`, `age 41`, `actor f"characters:{aid}"` and `precision "exact"`. Winifred `"--06"` gives `relation "in_month"`, `fixed None`, `in_days None` and `state "ok"`, sorted after every dated item.
  - `test_hebrew_axis_keeps_every_chanukah_day`: a Hebrew campaign (`calendar="hebrew"`, region `""`), advanced to `"5786-Kislev-24"`, has `len([i for i in items if i["kind"] == "holiday" and i["label"] == "Chanuka"]) >= 3`, with distinct refs, sorted by `fixed`.
  - `test_fake_provider_axis`: a plugin `_TITHE_PROVIDER_SRC` is written to `tmp_path/"calendars"/tithe_test.py` and registered as `"tithe-test-calendar"`.
    - Natives are integer strings; 12 months of 30 days with keys `"01"`..`"12"`; `holidays()` returns `{"name": "Tithe", "fixed": f}` for every `f % 10 == 0` in range. Model it on `test_calendar_plugins._PROVIDER_SRC`.
    - `calendar.json` is written directly with `calendars.write_calendar`, and the clock set to `"735"`.
    - Holidays fall at `in_days` `[5, 15, 25]`, and an event on `"736"` sorts before the first holiday.
  - `test_no_now_means_no_dates_are_invented`: a campaign with no clock and no chronicle gives `fixed None` and `friendly ""`. An event is listed with `fixed` set, `in_days None` and `state "ok"`; there are no holiday or birthday items.
  - `test_a_raising_plugin_degrades_to_undated_items` (Review Focus 1):
    - A plugin whose class `__init__` raises `RuntimeError` is registered as `"broken-test-calendar"`, and calendar.json is written with it as primary.
    - Then `build(cid)` returns with `fixed None`, the event is listed undated (`fixed None`, `in_days None`, `state "ok"`), and there are no holidays or birthdays.
  - `test_sources_restrict_the_work_done`: monkeypatch `birthdays.occurrences` and `calendars.upcoming_holidays` with recorders that append to a list and return `[]`. `build(cid, sources={"deadline", "linked_deadline"})` leaves the list empty, while `build(cid)` records both. (Recorders, not raisers: each source swallows exceptions.)
  - `test_unknown_source_is_a_value_error`.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_pressure.py -q`. Expected: FAIL, `ImportError: cannot import name 'pressure'`.
- [ ] **Step 3: Implement** `pressure.py` (constants, `state_of`, `build` with the three calendar sources). The module docstring says:
  - it composes and never re-scans;
  - `clock` must never import it;
  - each source fails soft on its own;
  - birthdays include today, while holidays are shifted by one day to include today (Decision 7).
- [ ] **Step 4: Run them and confirm they pass.** Use the same command. Expected: PASS.
- [ ] **Step 5: Run the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_import_guard.py tests/test_lock_domain_guard.py tests/test_atomic_guard.py tests/test_paths_guard.py -q`. Expected: PASS, with no lock-domain entry added.
- [ ] **Step 6: Commit:** `feat(continuity): temporal pressure contract with event, holiday and birthday sources`

---

### Task 6: Pressure deadlines — parseable dues, link deadlines and reached events

**Files:**
- Modify: `store/continuity/pressure.py`
- Test: `tests/test_continuity_pressure.py`

**Interfaces:**
- Consumes:
  - `effective.commitments(cid)` (live, canonical);
  - `effective.links(cid)`;
  - `aging.prepare(cid, now)` and `aging.age(ctx, row)`;
  - the event rows Task 5 already reads. The `linked_deadline` source reads `list_events` even when `"event"` is not requested.
- Produces: the `deadline` and `linked_deadline` sources in `build`, per Decision 8, each in its own `try`. Every deadline item has `subject = "commitment:<id>"` and `due_text = row["due"]`.
  - **`deadline` items:** `native = provider.format(fixed)`, `friendly = describe(fixed)["friendly"]`.
  - **`linked_deadline` items:** `ref = "event:<eid>"`, `relation` is the link's relation, and `fixed` is D−1 (`before`) or D (`by`/`on`/`after`).
  - **Reached events:** `fired` set, or `passed`. A reached event passes `reached=True` to `state_of`.

- [ ] **Step 1: Write the failing tests.** Commitments are created with `commitments.set_movement(cid, mid, title, "promise", "open", due, beat, scene)`, links with `continuity_doc.put_link(cid, lid, {"a": "commitment:...", "b": "event:...", "relation": ..., "created": "", "scene": "", "note": ""})`, and aliases with `continuity_doc.put_alias`. The clock is at 2026-05-10 and `warn=7`.
  - `test_a_parseable_due_is_a_deadline`: due `"2026-05-14"` gives one item with `kind "deadline"`, `ref == subject == "commitment:mara-s-oath"`, `in_days 4`, `state "due_soon"`, `due_text "2026-05-14"` and `relation "on"`.
  - `test_free_text_due_is_not_arithmetic`: `"before the bells stop"` gives no deadline item.
  - `test_overdue_due`: `"2026-05-01"` gives `state "overdue"`, `in_days -9`.
  - `test_before_link_derives_d_minus_one`: the coronation is dated 2026-05-20, and `before` gives `kind "linked_deadline"`, `ref "event:the-coronation"`, `fixed == F("2026-05-19")`, `in_days 9`, `relation "before"` and `due_text ""`.
  - `test_by_and_on_links_derive_d`.
  - `test_after_sets_no_deadline`: `after` gives one `linked_deadline` item with `relation "after"`, `in_days 10` and `state "upcoming"`. There is no `deadline`-relation item, and with D behind now there is no item at all.
  - `test_earliest_of_due_and_link_wins`: due `"2026-05-25"` and `before` the coronation (2026-05-20) give exactly one deadline item for the subject: `kind "linked_deadline"`, `fixed == F("2026-05-19")`, `due_text "2026-05-25"`.
  - `test_a_link_to_a_reached_event_is_overdue`: an event on 2026-05-12, `by` link, then `clock.advance(cid, to="2026-05-13")` (which fires it) gives `state "overdue"`. An event dated behind the clock and left unfired (`passed`) gives `overdue` too.
  - `test_backwards_clock_keeps_a_fired_event_reached` (Review Focus 2): advance to 05-21 (firing the coronation of 05-20), then `clock.advance(cid, to="2026-05-15", reason="correction")`.
    - The coronation is **not** an event item (fired, not today).
    - The commitment linked `before` it is `overdue`, even though `in_days` is positive.
  - `test_resolved_and_merged_away_commitments_make_no_deadline`: a fulfilled commitment with a due gives no item, and an alias source with its own due gives no item of its own.
  - `test_a_merged_source_due_is_not_inherited` (Review Focus 4): source due `"2026-05-14"` and canonical due `""`, aliased, give no deadline item for either ref.
  - `test_dangling_or_undated_link_contributes_nothing`: a link to a deleted event, and to an event whose date is `"midsummer"`, give no items.
  - `test_thread_links_never_make_deadlines`: `thread:mara-s-map before event:the-coronation` gives no deadline item.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_pressure.py -q`. Expected: the new tests FAIL (no deadline items).
- [ ] **Step 3: Implement** the two sources.
- [ ] **Step 4: Run them and confirm they pass.** Use the same command. Expected: PASS.
- [ ] **Step 5: Commit:** `feat(continuity): deadline and link-deadline pressure with reached events`

---

### Task 7: `continuity.drivers` and `GET /continuity/drivers`

**Files:**
- Create: `store/continuity/drivers.py`
- Modify:
  - `routes/continuity.py`: the import becomes `from ..store.continuity import doc, drivers, effective, review`; `_matching` is deleted and its call site uses `drivers.matching()`; the new route; one module-docstring sentence;
  - `routes/__init__.py`: the docstring row for `continuity` names `/continuity/drivers`.
- Test: `tests/test_continuity_drivers.py` (new), `tests/test_continuity_routes.py`

**Interfaces:**
- Consumes:
  - `pressure.build`, `pressure.PRESSURE_STATES`;
  - `effective.threads`, `effective.commitments`, `effective.links`, `effective.RELATIONS`;
  - `involvement.of`;
  - `aging.prepare` / `annotate`;
  - `appearances_cast.roster`;
  - `embed_space.resolve`.
- Produces:
  - `DRIVER_KINDS = ("thread", "commitment", "event", "birthday", "holiday")`.
  - `matching() -> str`: `"semantic"` iff `embed_space.resolve()` is non-None, and `"basic"` on any exception.
  - `snapshot(cid, offscreen: bool = False) -> {"now", "friendly", "fixed", "matching", "drivers", "anchors"}`. `now`/`friendly`/`fixed` are `pressure.build(cid)`'s. Drivers follow Decisions 10-13:
    - **Thread drivers:** `label = title`, `summary = latest_beat`, `status`, `actors = involvement.of(...)[ref]["actors"]`. `time_anchors` are the sorted event refs `b` of before/on/after/by links with `a == ref`.
    - **Commitment drivers:** the same fields.
    - **Temporal drivers:** `summary ""`, `status ""`, `time_anchors []`. `actors` is `[item["actor"]]` for a birthday and `[]` otherwise.
    - **Inputs:** every input (pressure, the two row lists, links, involvement, aging, roster) is read inside its own `try`, with an empty fallback.
    - **Never raises** for an existing campaign.
  - `GET /api/campaigns/{cid}/continuity/drivers?offscreen=<bool>`: `def get_drivers(cid: str, offscreen: bool = False)`. It calls `_campaign_or_404`, then returns `drivers.snapshot(cid, offscreen=offscreen)`. No lock (Decision 14). The handler is a `def`, not `async`.

- [ ] **Step 1: Write the failing tests.** In `test_continuity_drivers.py`, with fixtures copied from `test_continuity_effective.py`:
  - `test_driver_kinds_are_pinned`.
  - `test_snapshot_shape_and_matching`: the keys are exactly `{"now", "friendly", "fixed", "matching", "drivers", "anchors"}`, and `matching == "basic"`.
  - `test_thread_and_commitment_drivers`:
    - `"Mara's map"` (advanced) gives a driver `{"ref": "thread:mara-s-map", "kind": "thread", "label": "Mara's map", "status": "advanced", "summary": <latest beat>, "pressure": {"state": "ok", "in_days": None, "friendly": ""}, "time_anchors": [], "links": []}`.
    - A closed thread and a fulfilled commitment are absent.
  - `test_aliases_collapse_to_one_driver`: an aliased pair gives one driver, with the canonical ref and actors from the whole group (`involvement.of`).
  - `test_commitment_pressure_from_its_deadline_item`: due 4 days out with `warn 7` gives `pressure == {"state": "due_soon", "in_days": 4, "friendly": "14 May 2026"}`.
  - `test_stale_comes_from_aging_on_the_merged_last_scene`: `stale_after_days=10`. The canonical was last moved in a scene dated 40 days ago, and the source in one dated 2 days ago. The canonical is `stale` before the alias and `ok` after it.
  - `test_temporal_drivers_and_anchors`:
    - An event in 3 days, a passed event, an undated event, a holiday rule in 2 days, a yearless birthday in 1 day and a month-only birthday: all six are drivers.
    - `[a["ref"] for a in anchors]` orders birthday (1), holiday (2), event (3), then the month birthday. The passed and undated events are absent.
    - The month anchor has `precision "month"`, `fixed None` and `in_days None`.
  - `test_drivers_order_by_pressure_then_kind`: an overdue commitment, a stale thread, an upcoming event and an ok thread give states in `PRESSURE_STATES` order, with ties broken by `DRIVER_KINDS`, then `in_days`, then ref.
  - `test_links_attach_with_direction`:
    - `thread:mara-s-map pays_off commitment:mara-s-oath` gives the thread `{"relation": "pays_off", "other": "commitment:mara-s-oath", "direction": "out"}` and the commitment the `"in"` side.
    - A `related_to` link is `"both"` on each side.
    - `commitment before event:the-coronation` puts `"event:the-coronation"` in the commitment's `time_anchors` and an `"in"` link on the event driver.
  - `test_offscreen_drops_player_tokens`:
    - Seraphine (a PC) and Mara (a `characters` actor seated with role `player`) both stood in a thread's scene, with Winifred an NPC.
    - With `offscreen=True`, actors are `["characters:winifred"]`.
    - Seraphine's birthday driver survives with `actors == []`.
    - With `offscreen=False`, all three are present.
  - `test_snapshot_survives_garbled_continuity_and_ledgers` (Review Focus 5): with continuity.json, plot.json and events.json each `"{ no"`:
    - `snapshot` returns;
    - commitment drivers are still present;
    - there are no thread drivers;
    - nothing raises.

  In `test_continuity_routes.py`:
  - add `("get", "/continuity/drivers", None)` to `test_unknown_campaign_404_on_every_route`'s parameters;
  - `test_get_drivers_matches_the_store`: the route body equals `drivers.snapshot(cid)`, and `?offscreen=true` equals `snapshot(cid, offscreen=True)`;
  - `test_drivers_route_survives_a_raising_plugin` (Review Focus 1): 200, with `fixed is None`;
  - `test_drivers_route_makes_no_model_call`: monkeypatch `store.embed_space.resolve` to return a dict, and `app.dependency_overrides[routes.get_llm]` to a fake that fails on use. The route answers 200 with `matching == "semantic"`.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_drivers.py tests/test_continuity_routes.py -q`. Expected: FAIL, with `ImportError` for `drivers` and a 404 for the route.
- [ ] **Step 3: Implement** `drivers.py`, the route, and the `_matching` → `drivers.matching()` change.
- [ ] **Step 4: Run them and confirm they pass.** Use the same command. Expected: PASS, including Slice A's `test_get_continuity_empty` (`matching == "basic"`).
- [ ] **Step 5: Run the route and module guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_route_order.py tests/test_path_guard_store.py tests/test_routing_guard.py tests/test_pydantic_guard.py tests/test_import_guard.py tests/test_lock_domain_guard.py tests/test_store_api_baseline.py -q`. Expected: PASS. `CROSSING_PAIRS` needs no entry: `continuity` is composed before `entities`. If `test_route_order` fails, stop and report rather than editing the pinned table.
- [ ] **Step 6: Commit:** `feat(continuity): scene driver projection and GET /continuity/drivers`

---

### Task 8: `GET /ledger` returns effective rows; the Ledger shows what was merged

**Files:**
- Modify:
  - `routes/campaigns.py`: import `from ..store.continuity import effective as continuity_effective`; change the reads at 1614-1615 and extend the docstring;
  - `frontend/src/api/types.ts`: `LedgerAlias` and `PlotThread.aliases?`;
  - `frontend/src/routes/LedgerView.tsx`: the note line.
- Test: `tests/test_ledger_route.py`, `frontend/src/routes/LedgerView.test.tsx`

**Interfaces:**
- Consumes: `effective.threads(cid, include_closed=True)`, `effective.commitments(cid, include_resolved=True)`.
- Produces:
  - **Response:** the same `GET /campaigns/{cid}/ledger` response, but `plot`/`commitments` rows are effective. They carry the canonical id, the merged `last_scene`/`latest_beat`, and `aliases: [{ref, title, status}]`. Alias sources are absent at top level. Aging runs on these rows (§12.5).
  - **Locking:** the reads stay inside the existing `campaign_lock` and inside `_tolerant`.
  - **Docstring:** gains a paragraph replacing Slice A's interim note: effective rows, why sources are hidden, and that every row action already targets the canonical.
  - **TS types:** `export type LedgerAlias = { ref: string; title: string; status: string };` and `PlotThread` gains `aliases?: LedgerAlias[]`. It is optional, because digest rows and `blankSpec` literals omit it.
  - **`rowsFor`:** the threads and commitments notes become `joinNote(agingLabel(x.aging), merged(x), …existing parts)`, where `merged(x) = x.aliases?.length ? \`Merged: ${x.aliases.map(a => a.title).join(", ")}\` : ""`. It is plain text: the link to the Reviewed-links group is Slice D's.

- [ ] **Step 1: Write the failing tests.**
  - In `test_ledger_route.py` (reusing `_thread_ids`/`_merge`-style helpers that POST `/ledger/threads` and `/continuity/aliases`):
    - `test_a_merged_source_is_not_a_top_level_row`: after merging `"Mara's map"` into `"Winifred's chart"`, `[t["id"] for t in plot] == ["winifred-s-chart"]`, with `aliases == [{"ref": "thread:mara-s-map", "title": "Mara's map", "status": "open"}]`.
    - `test_merged_rows_carry_the_group_latest_beat_and_scene`.
    - `test_aging_uses_the_merged_last_scene`: Task 7's stale/ok scenario through the route.
    - `test_a_dangling_alias_leaves_its_source_visible`: `doc.put_alias` to a missing target leaves both rows, with `aliases == []`.
    - `test_a_garbled_continuity_file_still_answers`: 200, with the physical rows.
    - `test_no_alias_rows_are_the_physical_rows_plus_aliases`: each row equals the pre-switch shape plus `"aliases": []`.
  - Update `test_a_record_with_non_string_fields_still_renders_as_a_row`: its expected dict gains `"aliases": []`, and its `all(isinstance(...))` exclusion gains `"aliases"`. Give `test_a_thread_with_non_string_fields_still_renders_as_a_row` the same exclusion.
  - Extend `test_the_three_sections_are_read_under_one_campaign_lock`: also watch `store.continuity.doc.read` (imported as `from grimoire.store.continuity import doc as continuity_doc`; patch `continuity_doc.read`), and assert `held["continuity"] is True`.
  - In `LedgerView.test.tsx`:
    - `a merged thread names what was merged into it`: a thread with `aliases: [{ref: "thread:mara-s-map", title: "Mara's map", status: "open"}]` gives `findByText(/Merged: Mara's map/)`.
    - `a row with no aliases says nothing about merging`: `queryByText(/Merged:/)` is null.
    - `closing a merged thread addresses the canonical`: the quick close calls `ledgerSaveThread` with `("run", "winifred-s-chart", {status: "closed"})`.
- [ ] **Step 2: Run the backend tests and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_ledger_route.py -q`. Expected: FAIL (two rows; `aliases` missing).
- [ ] **Step 3: Run the frontend tests and confirm they fail.** From `frontend/`, run `npx vitest run src/routes/LedgerView.test.tsx`. Expected: FAIL (no "Merged:" text).
- [ ] **Step 4: Implement** the route switch, the types and the note line.
- [ ] **Step 5: Run the backend ledger tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_ledger_route.py tests/test_ledger_routes.py tests/test_events_routes.py -q`. Expected: PASS.
- [ ] **Step 6: Run the frontend ledger tests.** From `frontend/`, run `npx vitest run src/routes/LedgerView.test.tsx`. Expected: PASS.
- [ ] **Step 7: Typecheck.** From `frontend/`, run `npx tsc --noEmit -p .`. Expected: no errors.
- [ ] **Step 8: Commit:** `feat(ledger): effective rows with merged aliases on the note line`

---

### Task 9: Todo `owed` and the shell badge count canonical commitments

**Files:**
- Modify:
  - `routes/todo.py`: `_chore_owed`, `_items_owed`, and a new private `_owed`;
  - `routes/shell.py`: `ledger_open`;
  - `frontend/src/shell/rail.ts` (line 238);
  - `frontend/src/api/types.ts`: `ShellCampaign.ledger_open: number | null` with a doc comment;
  - `frontend/src/routes/CampaignHub.tsx` (lines 641-650).
- Test:
  - `tests/test_todo_route.py`, `tests/test_shell_route.py`;
  - `frontend/src/components/AppRail.test.tsx`, `frontend/src/shell/rail.test.ts`, `frontend/src/routes/CampaignHub.test.tsx`, `frontend/src/routes/TodoView.test.tsx`.

**Interfaces:**
- Consumes: `effective.commitments`, `effective.links`, `effective.Ledgers`, `review.describe`. These are imported as `from ..store.continuity import effective as continuity_effective` and `review as continuity_review`.
- Produces:
  - **`_owed(cid) -> list[tuple[dict, str]]`:** each live canonical commitment that has a deadline (Decision 16), in effective order, paired with its deadline text:
    - `f"due {due}"` when `due` is non-empty;
    - else `f"{relation} {continuity_review.describe(cid, b, ledgers)}"` for its lowest-id before/by/on link.

    It raises what `effective.commitments` raises.
  - **`_chore_owed`:** `n = len(_owed(cid))`, and `"what": f"{n} open commitment{'s' if n != 1 else ''} with a deadline"`. The rest of the dict, the literal `"group": "Continuity"` and `except (OSError, ValueError): return None` are unchanged.
  - **`_items_owed`:** `{"id": row["id"], "label": row["title"], "detail": " · ".join(x for x in (deadline, row.get("kind"), row.get("latest_beat")) if x), "fix": f"/campaigns/{cid}/ledger"}`.
  - **`ledger_open`:** `_ledger_open(cid) -> int | None` (private), which is `len(continuity_effective.commitments(cid))`, or `None` on any exception.
  - **`rail.ts`:** `tailLabel: (s) => lbl(s?.campaign?.ledger_open, "open commitments")`.
  - **CampaignHub:** the card is titled `"Open commitments"`. The tail is `camp && camp.ledger_open != null ? String(camp.ledger_open) : undefined`. The body shows `unknown` when `camp.ledger_open == null`, else the existing two strings.

- [ ] **Step 1: Write the failing tests.**
  - In `test_todo_route.py`:
    - `test_owed_counts_canonical_commitments` (Review Focus 4):
      - Mara's oath (due `"2026-06-01"`) is aliased into Winifred's promise (due `"2026-06-03"`), and a third commitment has no due.
      - The chore has `n == 1` and `what == "1 open commitment with a deadline"`.
      - Its items have `[i["id"] for i in items] == ["winifred-s-promise"]`.
      - With the canonical's due blanked, the chore is absent: a due on the source is not inherited.
    - `test_owed_counts_a_link_deadline`: a commitment with no due and a `before` link to the coronation is counted. Its item detail starts with `"before The coronation"`.
    - `test_owed_does_no_calendar_work`: monkeypatch `store.calendars.primary_provider` with a recorder. `GET /api/todo?campaign=<cid>` leaves it unrecorded.
    - `test_owed_chore_and_items_agree`: chore `n` equals items `total` (the `test_the_counts_and_the_instances_agree` pattern).
  - In `test_shell_route.py`:
    - `test_ledger_open_counts_effective_open_commitments`: an aliased pair plus one fulfilled commitment gives `ledger_open == 1`.
    - `test_ledger_open_is_null_on_a_garbled_ledger`: commitments.json `"{ no"` gives 200 with `ledger_open is None`.
  - Frontend:
    - `AppRail.test.tsx:68` expects `/ledger & timeline, 0 open commitments/i`.
    - `rail.test.ts`: `ledgerRow.tailLabel!(withCampaign({ ledger_open: 2 })) === "2 open commitments"` and `tailLabel!(withCampaign({ ledger_open: null }))` is `undefined`.
    - `CampaignHub.test.tsx`: `a null ledger count renders no number`. `shell({ ledger_open: null })` gives `findByText("Open commitments")` and `queryByText(/still open/)` is null.
    - `TodoView.test.tsx:136`: the mock text becomes `"2 open commitments with a deadline"` (cosmetic).
- [ ] **Step 2: Run the backend tests and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_todo_route.py tests/test_shell_route.py -q`. Expected: FAIL (two counted, old label, a 500 on garbled).
- [ ] **Step 3: Run the frontend tests and confirm they fail.** From `frontend/`, run `npx vitest run src/components/AppRail.test.tsx src/shell/rail.test.ts src/routes/CampaignHub.test.tsx`. Expected: FAIL.
- [ ] **Step 4: Implement** the backend and frontend changes.
- [ ] **Step 5: Run the backend tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_todo_route.py tests/test_shell_route.py tests/test_roster_shell.py -q`. Expected: PASS.
- [ ] **Step 6: Run the frontend tests.** From `frontend/`, run `npx vitest run src/components/AppRail.test.tsx src/shell/rail.test.ts src/routes/CampaignHub.test.tsx src/routes/TodoView.test.tsx src/shell/tabs.test.ts src/components/PhoneTabs.test.tsx`. Expected: PASS.
- [ ] **Step 7: Typecheck.** From `frontend/`, run `npx tsc --noEmit -p .`. Expected: no errors (every `ledger_open` reader handles `null`).
- [ ] **Step 8: Commit:** `feat(todo,shell): owed and the ledger badge count canonical commitments`

---

### Task 10: The frozen campaign gains the continuity projections

**Files:**
- Modify:
  - `tests/fixtures/frozen_campaign/sweep.py`: `_campaign`, plus the imports `from grimoire.store.continuity import drivers, effective, pressure`;
  - `tests/test_frozen_campaign.py`: the module set gains `"continuity"`;
  - `tests/fixtures/frozen_campaign/snapshot.json`: regenerated deliberately.

  `home/` is **never** touched.

**Interfaces:**
- Consumes: Tasks 2, 5 and 7.
- Produces: new sweep keys after the `commitments.*` block:
  - `continuity.effective.threads[{cid}]`, `continuity.effective.commitments[{cid}]`;
  - `continuity.effective.render_threads[{cid}]` (`with_id=True`), `continuity.effective.render_commitments[{cid}]`;
  - `continuity.effective.links[{cid}]`, `continuity.effective.diagnostics[{cid}]`;
  - `continuity.pressure.build[{cid}]`, `continuity.drivers.snapshot[{cid}]`.

  The fixture has no continuity.json, which proves absent-file reads. Pressure holiday names couple to the `holidays` package exactly as the existing `# Today` section does; extend the docstring's "two couplings" paragraph to say so.

- [ ] **Step 1: Write the failing test.** Add `"continuity"` to `test_the_sweep_covers_the_whole_store_not_a_corner_of_it`'s expected module set. Run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_frozen_campaign.py -q`. Expected: FAIL on that assertion.
- [ ] **Step 2: Add the sweep keys.**
- [ ] **Step 3: Regenerate the snapshot.** From `backend/`, run `PYTHONPATH=src .venv/bin/python -m tests.fixtures.frozen_campaign.sweep`.
- [ ] **Step 4: Prove the regeneration only added keys.** From `backend/`:

  ```bash
  PYTHONPATH=src .venv/bin/python - <<'EOF'
  import json, subprocess
  old = json.loads(subprocess.check_output(
      ["git", "show", "HEAD:backend/tests/fixtures/frozen_campaign/snapshot.json"]))
  new = json.load(open("tests/fixtures/frozen_campaign/snapshot.json", encoding="utf-8"))
  moved = [k for k in old if old[k] != new.get(k)]
  added = sorted(set(new) - set(old))
  assert not moved, moved
  assert added and all(k.startswith("continuity.") for k in added), added
  print(f"{len(added)} continuity keys added; no existing key moved")
  EOF
  ```

  Expected: the success line. Also read the added values: the effective rows equal the physical rows plus `aliases: []`, `diagnostics` is empty, and no driver carries a fabricated date.
- [ ] **Step 5: Run the frozen-campaign tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_frozen_campaign.py -q`. Expected: PASS, including `test_the_read_only_sweep_writes_nothing`.
- [ ] **Step 6: Commit:** `test(frozen): sweep the continuity projections; snapshot gains only new keys`

---

### Task 11: Slice gate

- [ ] **Step 1: Lint, types and templates.** From the repo root, run `make check-lint check-mypy check-templates PY=$PWD/backend/.venv/bin/python`. Expected: PASS. Fix any new finding in code. If a removed import resolved a recorded one, run `make baseline` and commit.
- [ ] **Step 2: The full backend suite.** Run `PYTHONPATH=backend/src backend/.venv/bin/python -m pytest backend -q`. Expected: PASS, apart from the failures recorded on the base commit (`test_atomic.py::test_a_read_only_record_is_not_silently_replaced` fails when run as root).
- [ ] **Step 3: The Android dependency set.** Run `make check-pydantic1 PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 4: The frontend gate.** Run `make check-web`. Expected: PASS (typecheck plus vitest under coverage).
- [ ] **Step 5: Implementation → done.** Run `/codex:review` against the slice diff. Resolve the findings and commit. If Codex is unavailable, record the independent reviewer that stood in.
- [ ] **Step 6: Done → actually done.** Run `/codex:adversarial-review` against the diff **and** the spec. Ask specifically whether the diff implements Slice B (§13–§13.6, §14, §20 tuples, §21 drivers route, §7.0 render helpers, §7.3 consumer switch, §8 canonical briefing, §12.5 rows and note, §18.3, §27), and look for gaps, drift and quietly dropped requirements. Resolve the findings and commit. Record a substitute reviewer if Codex is unavailable.

---

## Self-review notes

**Spec coverage:**

| Spec section | Task |
|---|---|
| §4 holiday and birthday occurrence refs | 1 (birthday), 5 (holiday via `notices.holiday_key`) |
| §7.0 effective render helpers; `verify_templates` extended | 2, 3 |
| §7.3 briefing | 4 |
| §7.3 clock digest | 4 |
| §7.3 suggest `build_snapshot` (canonical switch only) | 3 |
| §7.3 `context/assemble` | 3 |
| §7.3 absorb snapshots (canonical ids only) | 3 |
| §7.3 `get_ledger` | 8 |
| §7.3 todo `owed` | 9 |
| §7.3 shell `ledger_open` and rail label | 9 |
| §8 canonicalization step for briefing (after Slice A's move) | 2 (`group_touched`), 4 |
| §12.5 rows, aging, note line (no link) | 8 |
| §13 output shape; §13.1 sources and fail-soft; never imported by `clock` | 5, 6; Global Constraints |
| §13.2 horizons | 5 (holiday/birthday horizon, events regardless), 6 (deadlines regardless) |
| §13.3 structured time links | 6 |
| §13.4 contract and precedence, `warn_days == 0` | 5 (`state_of` table) |
| §13.5 link deadlines and reached events | 6 |
| §13.6 `occurrences`; `gather` gains `ref`; `upcoming` byte-identical | 1 |
| §14 drivers, shapes, involvement actors, aliases, liveness, links, offscreen | 7 |
| §20 `DRIVER_KINDS` / `PRESSURE_STATES` pinned | 5, 7 (TS unions wait for Slice E's chooser, their first consumer) |
| §21 `GET /continuity/drivers?offscreen=` | 7 |
| §26 calendar unavailable; malformed continuity.json; dangling links | 5, 7 (Review Focus 1, 5); 6 (dangling links) |
| §27 identity law; frozen sweep; verify_templates | 2, 3, 10 |
| §28.5 pressure tests (Gregorian, Hebrew, fake provider, `sources`) | 5, 6 |
| §28.7 "owed counts canonical commitments" | 9 |

**Deliberate deviations from the spec text:**

- `birthdays.occurrences` takes keyword-only `provider`, `roster` and `visible_characters` after the spec's three parameters (Decision 2). Its rows add `month_name`, `native` and `friendly` (Decision 3).
- Pressure items add `precision`, `actor` and `age`. A `linked_deadline` item's `ref` is the event ref; the commitment is `subject` (Decision 4).
- Link `direction` adds `"both"` for `related_to`, which the spec's shape does not enumerate (Decision 13).
- `drivers` imports `embed_space` for `matching`, which §7.0's table omits (Decision 1).
- Todo `owed` does not take §18.3's optional overdue/due-soon split; the cost rule says why (Decision 16). It counts link deadlines (§13.5).
- `ledger_open` becomes nullable, and the hub card is retitled. The spec names only the rail label; the cost rule and the identical mislabel justify both changes (Decision 17).
- Briefing rows strip `aliases` (Decision 15).
- The §7.2 absorb materialization redirect stays with Slice C, as Slice A's plan recorded. Once Task 3 shows canonical ids only, an absorb staged before a merge and saved after it can still name a hidden source; Slice C closes that.

**Open questions (none block execution):**

- The Ledger delete-refusal text says "or delete anyway", which the UI cannot do until Slice D's force path (F6). Slice B leaves the wording.
- "Bounded" anchors: this plan bounds holidays and birthdays by the horizon and lists every dated, non-passed event. If Slice E's prompt cap needs a tighter bound, it applies `DRIVER_PROMPT_CAP` there.

**Type consistency:**

- `pressure.PRESSURE_STATES`, `SORT_ORDER`, `HIGH_PRESSURE`, `KINDS`/`SOURCES`/`ALL` and `state_of` are defined in Task 5, and used in Tasks 6 and 7.
- `drivers.DRIVER_KINDS`, `matching` and `snapshot` are defined in Task 7, and used in Task 10.
- `effective.render_threads` / `render_commitments` are defined in Task 2, and used in Tasks 3 and 10.
- `involvement.group_touched` is defined in Task 2, and used in Task 4.
- `birthdays.occurrences` (row keys per Decision 3) is defined in Task 1, and used in Task 5.
- The item key set `{ref, kind, label, native, friendly, fixed, in_days, relation, state, subject, due_text, precision, actor, age}` is the same in Tasks 5, 6 and 7.
- `ShellCampaign.ledger_open: number | null` (Task 9) is read through `num`/`lbl`, which already accept `null`.

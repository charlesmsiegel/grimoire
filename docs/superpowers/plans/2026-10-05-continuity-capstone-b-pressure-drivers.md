# Continuity Capstone — Slice B: Temporal Pressure and Drivers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the read-only temporal pressure service and the scene-driver projection, including `GET /continuity/drivers`. Then switch every effective-current-state reader (prompt snippets, briefing, advance digest, ledger, Todo `owed`, shell badge) onto the canonical projections. A campaign with no aliases must produce byte-identical prompts.

**Architecture:** There are two new read-only modules under `backend/src/grimoire/store/continuity/`:

- `pressure` composes the existing calendar, event, aging and birthday readers into one dated item list with a fixed state contract;
- `drivers` composes `pressure`, `effective` and `involvement` into drivers and anchors.

`birthdays` gains an occurrence scan that its existing `upcoming` becomes a projection of. `effective` gains the snippet render helpers and a no-alias fast path. The consumers listed in spec §7.3 change their import, not their logic. A no-alias campaign is proven unchanged four ways:

- Task 0 characterizes every consumer the slice switches **before** any switch: the frozen sweep gains keys for the absorb snapshots, the briefing, the advance digest and the suggestion prompt, and route tests pin the exact `/ledger` rows, Todo `owed` strings and `/shell` `ledger_open`;
- direct equality tests against the physical renders;
- `verify_templates` checks;
- the frozen-campaign sweep, which passes unregenerated after each switch (the digest's additive `aliases: []` is the one recorded exception, Task 4) and then gains only new keys.

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
  - Edges beyond spec §7.0's table, each chosen on purpose and listed under "Deliberate deviations": `drivers` → `embed_space` (Decision 1), `drivers` → `aging` (driver `stale`, Decision 10), `drivers` → `appearances.cast` (offscreen player tokens, Decision 12); `pressure` → `clock` (§13.1 names `clock.now`) and `pressure` → `campaigns.paths` (the calendar root).
- **Complexity and broad excepts (ruff `C90` at max-complexity 10, and `BLE`):** `lint-baselines/ruff.json` has no entry for either new module, so one `C901` or one unmarked `BLE001` fails `make check-lint`.
  - Each pressure source is its own private function (`_event_items`, `_holiday_items`, `_birthday_items`, `_deadline_candidates`, `_linked_candidates`), dispatched from a module-level `_SOURCE_FNS = {kind: fn}` table.
  - They are called through one `_soft(fn, fallback, *args)` helper, which holds the module's only `except Exception:  # noqa: BLE001 -- <reason>`. `drivers` reads each of its inputs through its own `_soft` of the same shape.
  - `build`, `snapshot`, `birthdays._scan` and every helper stay at complexity ≤ 10. Every task from 0 to 10 runs `make check-lint PY=$PWD/backend/.venv/bin/python` from the repo root, not only Task 11.
  - **mypy is ratcheted the same way.** `mypy.ini` checks `backend/src/grimoire`, and `lint-baselines/mypy.json` has no entry for `pressure`, `drivers` or the new `birthdays`/`effective` code (`Iterator` annotations, `int | None` arithmetic on `fixed`/`in_days`). Tasks 1 to 7, which add or re-import backend source, also run `make check-mypy PY=$PWD/backend/.venv/bin/python` in their lint step. Expected: no new (file, rule) entry.
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
  - **This plan's reading of "`null` for an unparseable due":** an unparseable or free-text due produces **no** item (§13.1 and the `kind` row restrict `deadline` to a parseable due; §13.2 keeps free text non-arithmetic). It is recorded under "Deliberate deviations".
  - All arithmetic is day-level. An event stored with a time (`"2026-05-12T20:00"`, which `calendars.normalize` keeps) has `fixed = fixed_of(date)` (the time is split off), `native` = the stored date **with** its time (it is what the event says, and Slice E may pass it on), and `friendly` = the event row's `friendly`.
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

  Three interpretations this plan pins (Decision 8), because the spec's sentences can disagree:
  - **Reached outranks earliest.** A commitment's candidates (its parseable due, and each before/by/on link to a dated event) are each given a state, and the item is the **most urgent** candidate by `PRESSURE_STATES` index, then the earliest `fixed` (nulls last), then `deadline` before `linked_deadline`, then the lowest link id. For unreached candidates the state is monotone in `in_days`, so this is exactly "earliest wins"; it differs only when a link is reached, which is what keeps "reached → `overdue`" true when an earlier `due` exists, or a fired event was re-dated (`events.update` keeps the stamp) or the clock went backwards (`advance` fires only on `elapsed_days > 0`).
  - **The event's own day.** `reached` forces `overdue` on a deadline kind, **except** a `by`/`on` link on its own day (`in_days == 0`), which is `today` whether or not the event fired. Otherwise advancing onto the coronation's day (which fires it: `events.crossed` is `lo < fixed <= hi`) would make its `by` commitment overdue before the scene that fulfils it, while an event created on that day after the clock arrived would read `today`.
  - **`after` is only ever `upcoming`.** An `after` link yields an item only while its event is unreached and `0 < in_days ≤ UPCOMING_WINDOW_DAYS`. From D onwards, or further out than the window, it yields nothing, so it can never be `today` (high pressure) or `ok`.
- **Horizons (spec §13.2):**
  - the warning horizon is `calendars.warn_days(croot)`, where `0` means warnings off;
  - the suggestion horizon is `calendars.UPCOMING_WINDOW_DAYS` (30);
  - every parseable deadline and every unfired future event is listed regardless of horizon; holidays and birthdays are limited to the suggestion horizon;
  - free-text dues stay non-arithmetic.
- **No fabricated dates (spec §3.8, §26):** comparisons are integer fixed days on the **primary** provider (`calendars.fixed_of`). A calendar failure degrades to undated items.
- **Fail soft per source (spec §3.9, §13.1):** each pressure source and each driver input runs through its module's `_soft` helper (see "Complexity" above), so each fails on its own. A provider can be user plugin code that raises anything, and `calendars.primary_provider` catches only `CalendarError`/`KeyError`, so `aging.prepare` (which calls it outside any `try`, `aging.py:80`) can let a plugin's `RuntimeError` out and must itself sit inside a `_soft`.
- **Driver shape (spec §14):** `{ref, kind, label, summary, actors, status, pressure: {state, in_days, friendly}, time_anchors, links: [{id, relation, other, direction}]}`. `AnchorOption = {ref, kind: "event"|"birthday"|"holiday", label, native, friendly, fixed|null, in_days|null, precision: "exact"|"yearless"|"month"}`.
- **Copy:**
  - Todo `owed` reads `f"{n} open commitment{'s' if n != 1 else ''} with a deadline"`;
  - the rail ledger tail noun is `"open commitments"`;
  - the Ledger note part is `Merged: <alias titles joined by ", ">`.
- **Lint ratchet:** `make check-lint`, `make check-mypy` and `make check-eslint` must not grow `lint-baselines/ruff.json`, `mypy.json` or `eslint.json`. If a change resolves a recorded finding (a dropped import in `assemble`/`snapshots`/`suggest`; `LedgerView.tsx`'s recorded `react/no-array-index-key`), run `make baseline` and commit the smaller file with the fix. `check-web` runs no eslint, so `check-eslint` is a separate step after every frontend task.
- **Commands:**
  - backend: from `backend/`, `PYTHONPATH=src .venv/bin/python -m pytest <files> -q`;
  - frontend: from `frontend/`, `npx vitest run <file>`;
  - ratchets and gates: from the **repo root**, `make <target> PY=$PWD/backend/.venv/bin/python`;
  - one command per purpose, and a `-k` filter is never combined with a file list it would deselect.

## Review Focus

1. **A calendar plugin whose constructor raises a non-`CalendarError`** (a hand-written `<home>/calendars/*.py`).
   - Expected: `pressure.build` still answers, with `fixed: None`, undated events, and no holidays, birthdays or dated deadlines. `aging.prepare` raises `RuntimeError` here, and the `_soft` around it absorbs it. The one deadline item that survives is a `before`/`by`/`on` link to an event whose `fired` stamp is set: reachedness is a stamp, not arithmetic, so it is listed `overdue` with `fixed`/`in_days` `None`. `GET /continuity/drivers` answers 200, with thread and commitment drivers at `ok`. The ledger keeps its rows.
   - Pinned in Task 5 (`test_a_raising_plugin_degrades_to_undated_items`, with a thread and a commitment present), Task 6 (`test_a_fired_link_survives_a_raising_plugin`), Task 7 (`test_drivers_route_survives_a_raising_plugin`) and Task 8 (`test_ledger_survives_a_raising_plugin`).
2. **A clock moved backwards past an event it already fired** (advance past the coronation, then `clock.advance` to an earlier day; `observe` is forward-only).
   - Expected: the fired event is not re-listed as upcoming, because `events.upcoming` excludes fired rows. If the clock now sits on its day, it is listed as `today`. A commitment linked `before` it stays `overdue`, because it is reached, **even when the commitment also has an earlier parseable due** (most urgent outranks earliest).
   - Pinned in Task 6 (`test_backwards_clock_keeps_a_fired_event_reached`, which includes the earlier-due case, and `test_a_redated_fired_event_outranks_an_earlier_due`).
3. **A full Feb-29 birthdate in a common year, and month-only Hebrew Adar keys across the leap cycle.**
   - Gregorian: no occurrence at all, never an invented Feb 28 or Mar 1.
   - Hebrew: month-only keys match the year's own month keys **literally** (inherited from `_when`). So `--Adar1` gives nothing in a common year and `--Adar` gives nothing in a leap year. Day-bearing keys are folded by the provider instead (`--Adar1-14` in a common year is Adar 14). The projection keeps all of this byte-for-byte (§13.6), and the leap-year disappearance is an Open Question, not a Slice B fix.
   - Pinned in Task 1 (`test_leap_only_birthdays_never_invent_a_day`, which has a common-year and a leap-year row).
4. **A due set only on a merged-away source commitment** (the canonical's `due` is empty).
   - Expected: the canonical's due is authoritative, so the commitment has no deadline item, Todo does not count it, and the source is never a second row or a second count. A due is never inherited silently (§5.1).
   - Pinned in Task 6 (`test_a_merged_source_due_is_not_inherited`, which has a positive control) and Task 9 (`test_owed_counts_canonical_commitments`).
5. **A garbled continuity.json (`"{ no"`) while a turn is composed.**
   - Expected: aliases read as empty, so every prompt snippet equals the physical render; drivers still answer; the shell count is the physical count; nothing raises.
   - Pinned in Task 2 (`test_render_helpers_ignore_a_garbled_continuity_file`), Task 7 (`test_snapshot_survives_garbled_continuity_and_ledgers`) and Task 9 (`test_ledger_open_ignores_a_garbled_continuity_file`).

---

## Decisions (and why)

1. **Module edges** are the Global Constraints import lines above. `drivers` imports `embed_space` so that `drivers.matching()` is the one definition of the `matching` field. `routes/continuity.py`'s `_matching` is deleted and calls it, so `GET /continuity` and the drivers read cannot disagree. Spec §7.0's table omits `embed_space` for drivers but §14 requires `matching`; the edge is acyclic (`embed_space`'s closure has no continuity module). The other edges beyond §7.0's table (`drivers` → `aging`, `appearances.cast`; `pressure` → `clock`, `campaigns.paths`) are listed in the Global Constraints and under "Deliberate deviations".
2. **`birthdays.occurrences` signature:** `occurrences(cid, now_fixed, window=calendars.UPCOMING_WINDOW_DAYS, *, provider=None, roster=None, visible_characters=True)`.
   - The spec's three positional parameters stay first.
   - `roster` and `visible_characters` are needed because `upcoming`'s callers choose them (suggestions pass the appearance roster with `visible_characters=True`). `None` means `appearances_cast.roster(cid)`.
   - `provider` lets `pressure` resolve the plugin once.
   - The scan covers `[now_fixed, now_fixed + window]` **inclusive of today**, which is `_when`'s window. Events and holidays use `(now, now+w]`; birthdays have always included today, and the projection must keep `"today"`.
   - **The scan is lazy.** `_scan` is a generator, and `upcoming`/`_when` take only the first hit from it. They therefore stop on the same day `_when` stops today and never call `describe`/`months`/`is_anniversary` past it. An eager list would change failure behaviour: a plugin that raises on a later day would skip an actor whose line renders today. Only `occurrences` (which pressure uses) materializes the whole per-actor walk, inside its per-row `try`.
   - **`upcoming` does NOT call `occurrences`.** Both iterate `gather(cid, roster, visible_characters=...)` and call one private helper, `_actor_hits(provider, row, now_fixed, window) -> Iterator[dict]`. It wraps `_scan` and builds the Decision 3 rows (ref, name, actor, native, friendly) lazily. `occurrences` takes `list(_actor_hits(...))` inside its own per-row `try`, and `upcoming` takes `next(_actor_hits(...), None)` inside its own per-row `try`. This is the only reading under which `test_upcoming_stops_at_the_first_hit` can pass: `occurrences` materializes Mara's whole walk and loses her to the later-day raise, while `upcoming` never reaches that day.
   - **`upcoming` keeps its own choices.** It resolves the provider as it does today, passes `roster` and `visible_characters` to `gather`, and keeps its `visible_characters=False` default.
3. **The occurrence row** is spec §13.6's fields plus `year`, `month_name`, `native` and `friendly`:
   - `{ref, name, actor, precision, fixed, year, month_key, month_name, in_days, age, native, friendly}`.
   - `year` is the provider year of the hit (`describe(day)["year"]`), for every precision. `upcoming` decides "this month" by comparing `(row["year"], row["month_key"])` with now's `(describe(now_fixed)["year"], months(year)[month-1]["key"])`, which is exactly `_when`'s "first hit at d == 0".
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
   - `native`/`friendly`: an event's stored date (time kept) and its row's `friendly`; the occurrence's for a birthday; otherwise `provider.format(fixed)` and `describe(fixed)["friendly"]`, or `""`/`""` when `fixed` is `None`.
   - `due_text` is the commitment's stored `due` on deadline kinds, and `""` otherwise.
   - `precision` is the occurrence's for birthdays, `"exact"` for anything else with a `fixed`, and `None` when `fixed` is `None`.
   - `actor` is the birthday's `<kind>:<id>`, else `None`. `age` is the exact birthday's age, else `None`.
5. **Item order** is `(fixed is None, fixed or 0, KINDS.index(kind), ref, subject or "")`: the fixed-day axis first (spec §28.5), nulls last, and deterministic ties.
6. **Which events are items:**
   - every **unfired** event (future, today, passed, or undated);
   - plus **fired** events whose fixed day equals today (`state` is `today`).

   A fired past or future event is not listed, which matches `events.upcoming`/`on_day`. The source is `events.list_events(cid, provider, now_fixed)`, with `fixed` recomputed by `calendars.fixed_of` under `try`. With no provider or no `now`, events are listed with `in_days: None` and state `ok`.
7. **Today's holidays** come from `calendars.upcoming_holidays(cfg, provider.format(now_fixed - 1), horizon + 1)`. This composes the public reader rather than the private `base._upcoming`, and it covers `[now, now+horizon]` with fixed days.
   - **`in_days` is recomputed** as `h["fixed"] - now_fixed`. The row's own `in_days` counts from the shifted native, so it is one too many.
   - **One item per ref.** After sorting, holiday items are deduped by `ref`, keeping the first, as `notices.pending` does (`notices.py:443-458`). `_upcoming` dedupes on the raw `(fixed, name)`, but `notices.holiday_key` strips the name, so `"Saltmarch Eve"` and `"Saltmarch Eve "` on one day (both names `validate_rule` accepts) would otherwise be two items, two drivers and two anchors with one ref.
   - **A raising secondary empties the holiday source only.** `_configured` builds the secondary as well, so a secondary whose constructor or `holidays()` raises costs the primary's holidays too. That matches `notices`, and it is pinned rather than worked around; events, birthdays and deadlines are untouched.
8. **Deadlines** (the Global Constraints' three §13.5 interpretations):
   - **Candidates per live canonical commitment.** The `deadline` source contributes its parseable `due` (fixed derived from `aging.age` as `now_fixed + due_in` or `now_fixed − days_over`). The `linked_deadline` source contributes each before/by/on link to an event: `fixed` is D−1 or D when the provider reads the event's date. Each source's candidate collection runs in its own `_soft`, so a raising `aging.prepare` costs dues only.
   - **One item per commitment:** `state_of` is computed for every candidate (with `reached` for links), and the item is the most urgent by `PRESSURE_STATES` index, then the earliest `fixed` (nulls last), then `deadline` before `linked_deadline`, then the lowest link id. Its `fixed`, `in_days`, `relation` and `ref` come from that candidate, and its `due_text` is the commitment's stored `due`. The choice runs over whatever candidates the requested sources produced.
   - **`after`:** one separate `linked_deadline` item per `after` link whose event is dated, unreached and `0 < in_days ≤ UPCOMING_WINDOW_DAYS`, always with state `upcoming`.
   - Thread→event temporal links never make deadlines; they become thread drivers' `time_anchors`.
   - **No `now`, or no provider:** a `deadline` candidate needs a readable `now` and gives none without it. A link candidate whose event has a `fired` stamp is still a candidate with `fixed` (or `None` when the date cannot be read) and `in_days: None`, and it is `overdue` (reached is a stamp; nothing is computed). Any other link candidate needs both.
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
    - **What counts:** exactly §18.3. That is today's rule (a live commitment whose `due` is non-empty, parseable or not), made canonical: `[c for c in effective.commitments(cid) if c.get("due")]`. Link deadlines are **not** counted in Slice B.
    - **Why not links:** counting them would change `owed` for a campaign that has links but no aliases (Slice A already ships `POST /continuity/links`), which breaks Decision 18's "nothing else moves". It would also need `effective.links`, which loads all three ledgers for every campaign on the global page. Todo's set is deliberately **not** pressure's: it counts a stated deadline, dated or not (a free-text due counts), because Todo does no calendar work. Pressure counts only dated ones. Link deadlines reach Todo with the pressure split, in a later slice.
    - **No calendar work:** it calls neither `pressure` nor `aging`. Deadline pressure resolves the provider, which is plugin code, and `live('')` runs every builder for every campaign. §18.3's overdue/due-soon split is a "may", and taking it would put plugin code on the global To do page.
17. **Shell `ledger_open`:** the count is `len(effective.commitments(cid))` inside `try`, and it answers `None` on failure. This follows the shell's cost rule, and it fixes today's 500 on a garbled commitments.json. The TS type becomes `number | null`. CampaignHub's card is retitled "Open commitments" (the same mislabel as the rail).
    - **A null count after a successful shell read** renders its own body, `<p className="field-hint">Could not be counted.</p>`. `unknown` would read "Loading…" forever there (`unknown` is `shellFailed ? … : "Loading…"`), and today's falsy branch would say "Nothing is owed.", which is zero-for-unknown.
    - `!camp` still renders `unknown`, and the tail is omitted for `null`.
18. **Existing assertions that change, and why:**
    - **`test_actor_context.py:242-243`:** its monkeypatch targets `assembly.plot`/`assembly.commitments`, which `assemble` no longer imports. It is retargeted to `effective.render_*`.
    - **`test_actor_scoped_skip.py`:** the forbidden list's `plot`/`commitments` `render_open` entries become vacuous, and are replaced with `effective.render_threads`/`render_commitments`.
    - **`test_ledger_route.py:187,212`:** rows gain `aliases: []` (§12.5).
    - **`AppRail.test.tsx:68`:** the rail label changes.
    - **`TodoView.test.tsx:136` and `:154`:** the cosmetic mock texts change to the new noun.
    - **`test_frozen_campaign.py`:** the module set gains `continuity`.
    - **`snapshot.json`:** Task 0 adds characterization keys for the switched consumers. Task 4 regenerates once, and only the digest rows gain `aliases: []`. Task 10 adds new `continuity.*` keys only.

    Nothing else moves for a no-alias campaign, including one with links (Decision 16).

---

## File structure

| File | Responsibility |
|---|---|
| `store/birthdays.py` | `gather` rows gain `ref`; a private `_scan`; `occurrences`; `upcoming` and `_when` become projections of it |
| `store/continuity/__init__.py` | docstring only: the package map gains `pressure` and `drivers`, and `effective` gains its renders |
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
| `tests/fixtures/frozen_campaign/sweep.py`, `snapshot.json` | pre-switch consumer characterization (Task 0); continuity projections (Task 10); deliberate regenerations |
| `frontend/src/api/types.ts`, `routes/LedgerView.tsx`, `shell/rail.ts`, `routes/CampaignHub.tsx` | `aliases?`, the Merged note, the rail noun, nullable `ledger_open` |
| `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md` | amended to match each recorded deviation (Task 11 Step 6) |

Backend paths are relative to `backend/src/grimoire/`, and tests to `backend/tests/`, unless they start with `frontend/` or `scripts/`.

---

### Task 0: Characterize every consumer the slice switches, on pre-switch code

The identity law is only proven where something would fail if it broke. The frozen sweep covers `context.build_messages` and the physical `render_open`s, but not the absorb snapshots, the briefing, the advance digest or the suggestion prompt. No backend test pins the `owed` chore, and none pins `ledger_open`. This task adds those pins **before** any code changes, so every later task proves itself against them.

**Files:**
- Modify: `tests/fixtures/frozen_campaign/sweep.py` (`_campaign`, `_scene`, imports), `tests/fixtures/frozen_campaign/snapshot.json` (regenerated deliberately; `home/` is never touched).
- Test: `tests/test_ledger_route.py`, `tests/test_todo_route.py`, `tests/test_shell_route.py` (append).

**Interfaces:**
- Produces these new sweep keys, each recorded through a local `_or_error(fn)` that stores `{"error": type(e).__name__}` when the fixture cannot answer (for example `ClockError` with no clock), so a key is never silently absent.
  - `_or_error`'s handler is spelled exactly `except Exception as e:  # noqa: BLE001 -- the sweep records a failure as data so a key is never silently absent`. It is broad on purpose (a briefing or suggestion read can fail in shapes no named tuple anticipates), and `ruff.toml`'s only `tests/fixtures/**` ignore is `T201`, so an unmarked handler is a new `BLE001` against `lint-baselines/ruff.json`.
  - The keys:
    - `absorb.plot_snapshot[{cid}]`, `absorb.commitment_snapshot[{cid}]` (strings; `absorb.snapshots`);
    - `briefing.build[{cid}/{sid}]` for every scene, in `_scene`;
    - `suggest.build_prompt[{cid}/offscreen={o}]` = `suggest.build_prompt(suggest.build_snapshot(cid, offscreen=o), offscreen=o)` for `o` in `(False, True)`. This is the prompt, not the snapshot dict, because Task 3 adds `aliases: []` to the snapshot rows while the template renders the same text;
    - `clock.preview[{cid}]` = `clock.preview(cid, days=1)`.
- The suggestion prompt couples to the `holidays` package exactly as `# Today` does. Extend the sweep docstring's "two couplings" paragraph to say so.

- [ ] **Step 1: Add the sweep keys.**
- [ ] **Step 2: Regenerate and prove only keys were added.** From `backend/`, run `PYTHONPATH=src .venv/bin/python -m tests.fixtures.frozen_campaign.sweep`. Then run Task 10 Step 4's script with its last assertion changed to `all(k.split(".", 1)[0] in {"absorb", "briefing", "suggest", "clock"} for k in added)`. Expected: no existing key moved.
- [ ] **Step 3: Run the frozen-campaign tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_frozen_campaign.py -q`. Expected: PASS, including `test_the_read_only_sweep_writes_nothing`. If that test fails because a new reader writes, stop and report rather than dropping the key.
- [ ] **Step 4: Write the route characterization tests.** They must PASS on today's code:
  - `test_ledger_route.py::test_ledger_rows_are_pinned_for_an_ordinary_campaign`: two open threads and two open commitments, one with a beat, **plus one closed thread and one fulfilled commitment**. `get_ledger` reads `include_closed=True` / `include_resolved=True`, so the retired rows are part of what the route serves (the Ledger's "show retired"). Give every row a `last_scene` chosen so the closed thread and the fulfilled commitment sort **between** the open rows of their section (`open_threads` / `open_commitments` sort on `(last_scene, id)`, and `effective._rows` re-sorts on the same key). Assert each `plot`/`commitments` row equals an exact literal dict, in that exact interleaved order. In Task 8 the only permitted edit to this test is adding `"aliases": []` to each literal.
  - `test_todo_route.py::test_owed_is_pinned_for_an_ordinary_campaign`: Mara's oath (due `"2026-06-01"`, kind `promise`, a beat), Winifred's promise (due `"before the bells stop"`), and a third commitment with no due. Assert chore `n == 2`, `what == "2 open threads with a deadline"`, and the items' exact `id`, `label` and `detail` (`"due 2026-06-01 · promise · <beat>"`, `"due before the bells stop · promise"`). In Task 9 the only permitted edit is `what`.
  - `test_shell_route.py::test_ledger_open_counts_open_commitments`: two open and one fulfilled commitment give `ledger_open == 2`.
- [ ] **Step 5: Run them.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_ledger_route.py tests/test_todo_route.py tests/test_shell_route.py -q`. Expected: PASS.
- [ ] **Step 6: Lint.** From the repo root, `make check-lint PY=$PWD/backend/.venv/bin/python`. Expected: PASS (the `_or_error` handler carries its `noqa: BLE001` reason).
- [ ] **Step 7: Commit:** `test(continuity): characterize the consumers slice B switches`

---

### Task 1: `birthdays.occurrences`, with `upcoming` as its projection

**Files:**
- Modify: `store/birthdays.py`
- Test: `tests/test_birthday_occurrences.py` (new). These must pass **unmodified**: `tests/test_suggest_store.py`, `tests/test_birthdate.py`, `tests/test_calendar_plugins.py` and `tests/test_clock_store.py`.

**Interfaces:**
- Produces:
  - `gather(...)`: rows are `{name, birth, ref}`. `ref` is `f"{kind}:{id}"` for roster rows, and `f"characters:{id}"` for visible characters.
  - `_scan(provider, birth: str, now_fixed: int, window: int) -> Iterator[dict]`. Private, a **generator**, and the one walk (Decision 2).
    - It walks `d in range(max(window, 0) + 1)` with `_when`'s exact per-day rules: `is_anniversary` for day-bearing births, the case-folded month-key match for day-less ones, and nothing for a year alone.
    - It yields every hit as `{precision, fixed, year, month_key, month_name, in_days, age}`, lazily. A month-only birth gives one hit per distinct `(year, key)`, at its first matching day.
    - It raises `CalendarError` where `_when` does today, and only when the walk reaches that day.
  - `_when(provider, birth, now_fixed) -> tuple[str | None, int | None]`: the same contract (pinned by `test_calendar_plugins`). It is `next(_scan(provider, birth, now_fixed, UPCOMING_WINDOW_DAYS), None)`, labelled, so it stops where it stops today.
  - `occurrences(cid, now_fixed: int, window: int = calendars.UPCOMING_WINDOW_DAYS, *, provider=None, roster: list[dict] | None = None, visible_characters: bool = True) -> list[dict]`.
    - Each row is `{ref, name, actor, precision, fixed, year, month_key, month_name, in_days, age, native, friendly}` (Decision 3).
    - It returns `[]` when no provider resolves. A per-row `CalendarError` skips that actor only; it materializes each actor's walk, `list(_actor_hits(...))`, inside that row's `try`.
  - `_actor_hits(provider, row, now_fixed: int, window: int) -> Iterator[dict]`. Private and lazy: it wraps `_scan(provider, row["birth"], now_fixed, window)` and yields full occurrence rows. It is the one helper both public readers share (Decision 2).
  - `upcoming(cid, now, roster, *, visible_characters=False) -> [{name, age, when}]`: unchanged output. It does **not** call `occurrences`. It iterates `gather` itself and takes `next(_actor_hits(...), None)` per actor, in gather order, inside its own per-row `try`, passing `roster` and `visible_characters` to `gather` (Decision 2).
    - `when` is `"today"` / `f"in {in_days} days"` for day precision.
    - For month precision it is `"this month"` when `(row["year"], row["month_key"])` equals now's (`describe(now_fixed)["year"]` and `months(year)[month-1]["key"]`), else `f"in {month_name}"`.

- [ ] **Step 1: Write the failing tests.** The `_campaign(monkeypatch, tmp_path, *, calendar="gregorian", now="2026-05-08")` helper builds on `test_suggest_store._campaign`, but creates the campaign with `campaigns.create_campaign("Run", wid, calendar=calendar)` (the `test_clock_store._campaign` form) and calls `clock.advance(cid, to=now)` only when `now` is not `None`. Characters are made with `overlay.create_character(cid, "Mara")` and `characters.set_birthdate(campaigns.campaign_root(cid), aid, ...)`. `F(native)` means `calendars.fixed_of(provider, native)`.
  - `test_gather_rows_carry_their_ref`: every row has `ref == f"characters:{aid}"`.
  - `test_exact_birthday_occurrence`: `"1985-05-09"` gives `[{"ref": f"birthday:characters:{aid}:{F('2026-05-09')}", "name": "Mara", "actor": f"characters:{aid}", "precision": "exact", "fixed": F("2026-05-09"), "year": 2026, "month_key": None, "month_name": None, "in_days": 1, "age": 41, "native": "2026-05-09", "friendly": "9 May 2026"}]`.
  - `test_yearless_birthday_has_no_age`: `"--05-09"` gives `precision "yearless"`, `age None`, `in_days 1`.
  - `test_month_only_birthday_has_no_day`: `"--05"` gives `ref == f"birthday:characters:{aid}:month:2026-05"`, `fixed None`, `in_days None`, `age None`, `year 2026`, `month_key "05"`, `month_name "May"`, `native ""`, `friendly "May 2026"`, `precision "month"`.
  - `test_today_is_inside_the_window`: `"--05-08"` gives `in_days == 0`; `window=0` keeps it; `window=0` drops `"--05-09"`.
  - `test_year_only_and_unmatched_keys_yield_nothing`: each of `"1985"`, `"--5"` and `"--Mirtul"` gives `[]`.
  - `test_leap_only_birthdays_never_invent_a_day` (Review Focus 3):
    - Gregorian, from 2027-02-20: `"2000-02-29"` and `"--02-29"` give `[]`.
    - Hebrew, common year (`_campaign(..., calendar="hebrew", now="5786-Shevat-20")`): `"--Adar1"` gives `[]` (the literal month-key match); `"--Adar"` gives one month occurrence with `month_key == "Adar"`; `"--Adar2-14"` gives a yearless occurrence with `in_days == 24`.
    - Hebrew, leap year (`now="5787-Adar1-20"`): `"--Adar"` gives `[]` (the same literal match, now in the other direction; an Open Question); `"--Adar-14"` gives a yearless occurrence with `in_days == 24`; `"--Adar1"` gives a month occurrence whose upcoming `when` is `"this month"`.
  - `test_a_bad_birthdate_skips_only_that_actor`: Mara `"--05-32"` and Winifred `"--05-09"` give Winifred's row only.
  - `test_upcoming_takes_the_first_hit_per_actor`: Mara `"--05-09"` and Winifred `"--06"` give `upcoming(...) == [{"name": "Mara", "age": None, "when": "in 1 days"}, {"name": "Winifred", "age": None, "when": "in June"}]`, and each equals the projection of that actor's first `occurrences` row (`year` included in the comparison).
  - `test_upcoming_stops_at_the_first_hit`: wrap the Gregorian provider so `describe`/`is_anniversary` raise `CalendarError` for any day after `now + 1`, and monkeypatch `calendars.primary_provider` to return it (pass it as `provider=` to `occurrences`). `upcoming(...)` still renders Mara (`"--05-09"`, first hit at day 1), while `occurrences(..., window=30)` skips her. This pins the lazy walk (Decision 2).
  - `test_upcoming_forwards_visible_characters`: a visible-only character with a birthday is absent from `upcoming(cid, now, roster)` (default `False`) and present with `visible_characters=True`.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_birthday_occurrences.py -q`. Expected: FAIL, `AttributeError: module 'grimoire.store.birthdays' has no attribute 'occurrences'`.
- [ ] **Step 3: Implement** `_scan`, `_actor_hits`, `occurrences`, the `gather` `ref`, and `upcoming`/`_when` over them, in `store/birthdays.py`. Update the module docstring's caller list: `occurrences` is the scan, and `upcoming` and pressure project it.
- [ ] **Step 4: Run the new tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_birthday_occurrences.py -q`. Expected: PASS.
- [ ] **Step 5: Prove the existing birthday text is unchanged.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_suggest_store.py tests/test_birthdate.py tests/test_calendar_plugins.py tests/test_clock_store.py tests/test_import_guard.py -q`. Expected: PASS, with no test file edited ("in 1 days", "this month", age 41 and the Hebrew `crossed` pin still hold). Then `PYTHONPATH=src .venv/bin/python -m pytest tests/test_frozen_campaign.py -q`: PASS with `snapshot.json` untouched (Task 0's `suggest.build_prompt` keys carry the birthday line).
- [ ] **Step 6: Lint and types.** From the repo root, `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS (`_scan` ≤ complexity 10; no new mypy entry for the `Iterator` annotations).
- [ ] **Step 7: Commit:** `feat(birthdays): occurrence scan with refs; upcoming becomes its projection`

---

### Task 2: Effective render helpers, the no-alias fast path, and canonical touched scenes

**Files:**
- Modify: `store/continuity/effective.py`, `store/continuity/involvement.py`, `store/continuity/__init__.py` (docstring only, no imports: the `effective` bullet becomes "the live resolver, the effective records/links every current-state reader uses, and the prompt-snippet renders")
- Test: `tests/test_continuity_effective.py`, `tests/test_continuity_involvement.py`

**Interfaces:**
- Produces:
  - `effective.render_threads(cid, with_id: bool) -> list[str]` and `effective.render_commitments(cid, with_id: bool) -> list[str]`.
    - They render `snippets/plot_thread_line/{absorb|context}.j2` (`t=`) / `snippets/commitment_line/{absorb|context}.j2` (`c=`) over `threads(cid)` / `commitments(cid)`.
    - **Shape, matching `render_open`'s actual contract** (only the row read is guarded; `prompts.render` sits outside the `try`): `try: rows = threads(cid)`, and `except Exception: return plot.render_open(cid, with_id)` (`# noqa: BLE001`, reason given). Then `return [prompts.render(template, t=t) for t in rows]` outside the `try`. Commitments use the same shape with `commitments.render_open`.
    - So a garbled ledger still gives `[]` (the physical render's own tolerance); a continuity-side failure degrades to the physical lines rather than dropping the section (§3.9, §26 "read enhancements omit"); a broken snippet template raises exactly as it does today.
    - Import `from ... import prompts`. `plot.render_open` and `commitments.render_open` stay physical (§7.0).
  - **`_groups` fast path:** when `doc.read(cid)["aliases"]` is empty, return `{}` without calling `live_canon`, so no `Ledgers.load` happens. The output is identical by construction; this only removes three ledger reads from every effective call on the common path (the shell badge runs on every navigation).
  - **`_rows` reads each ledger once on that path.** Today `_physical` parses the file twice (`plot.read`, then `open_threads`, which reads it again). `_rows` computes the projected rows and `groups` first, and calls `plot.read` / `commitments_store.read` only when `groups` is non-empty (only `_merged` needs the raw beats). `records` still needs raw beats and is unchanged.
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
    - `test_no_alias_rows_parse_the_ledger_once`: wrap `store.commitments.read` in a counter (it is what `open_commitments` calls). `effective.commitments(cid)` on a campaign with no continuity.json counts exactly 1 (the merged path's extra reads, `Ledgers.load` and the raw beats, are not pinned).
    - `test_render_helpers_fall_back_to_the_physical_render`: monkeypatch `effective._groups` to raise `RuntimeError`. Then `render_threads(cid, w) == plot.render_open(cid, w)` for both `w`, and the same for commitments.
  - In `test_continuity_involvement.py`:
    - `test_group_touched_equals_touched_scenes_without_aliases`: equality with `involvement.touched_scenes(store.plot.read(cid))`.
    - `test_group_touched_folds_members_into_the_canonical`: `maras-map` (beats at S1 and S3) aliased into `winifreds-chart` (beat at S2) gives `{"winifreds-chart": {S1, S2, S3}}`. Any other thread keeps its own key.
    - `test_group_touched_garbled_ledger_is_empty`.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_effective.py tests/test_continuity_involvement.py -q`. Expected: FAIL, `AttributeError` for `render_threads` / `group_touched`.
- [ ] **Step 3: Implement** the two render helpers, the `_groups` fast path and `group_touched`.
- [ ] **Step 4: Run them and confirm they pass.** Use the same command. Expected: PASS, with every Slice A test in both files still green.
- [ ] **Step 5: Run the import guard.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_import_guard.py -q`. Expected: PASS.
- [ ] **Step 5b: Lint and types.** From the repo root, `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS, with `_rows` at complexity ≤ 10 after the fast path and the conditional raw read, each render helper's `noqa: BLE001` carrying its reason, and no new mypy entry.
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
  - Import with two single-name lines, placed after `from grimoire.store import weather as wstore` and before the `grimoire.store.tracker` imports: `from grimoire.store.continuity import doc as continuity_doc  # noqa: E402` and `from grimoire.store.continuity import effective  # noqa: E402`. (One combined line adds an `I001` and a seventh `E402` against `lint-baselines/ruff.json`'s `E402: 6`; checked with the repo's ruff.)
  - In `gather()` (lines 894-895), use `effective.render_threads` / `render_commitments`, so the expectation mirrors `assemble`.
  - Beside the four `render_open` checks (lines 1144-1154), add:
    - `check("plot lines (effective == physical, no aliases)", "\n".join(effective.render_threads(cid, w)), "\n".join(plot.render_open(cid, w)))` for both `w`, and the commitment equivalent. This is the identity law on the full fixture.
    - Then, on a second campaign `cid_merged = campaigns.create_campaign("Saltmarch", wid)`, created after every other check so nothing else sees it:
      - two threads, `"mara-s-map"` and `"winifred-s-chart"`, and two commitments, `"mara-s-oath"` and `"winifred-s-promise"` (kind `promise`, no due), with `continuity_doc.put_alias` from each first id to the second. The canonical's beat is at the earlier scene and the **source's** beat (`"The map names the Saltmarch causeway"`, `"Mara swore it at the gate"`) at the later one, so the merged `latest_beat` must come from the source;
      - **hand-written expected lines, not the template rendered over `effective.threads`** (that is what the helper does, so the comparison could not fail). `check(label, expected, actual)` takes the expectation first:
        - `check("plot lines (effective, merged, absorb)", "winifred-s-chart: Winifred's chart (<status>) — The map names the Saltmarch causeway", "\n".join(effective.render_threads(cid_merged, True)))`;
        - `check("plot lines (effective, merged, context)", "Winifred's chart (<status>): The map names the Saltmarch causeway", "\n".join(effective.render_threads(cid_merged, False)))`;
        - the commitment pair, `"winifred-s-promise: Winifred's promise (promise, <status>) — Mara swore it at the gate"` and `"Winifred's promise (promise, <status>): Mara swore it at the gate"`;
        - `<status>` is written out as the literal the setup's writer stores for the canonical (its own status, never the source's), so a regression where a source's status or title wins also fails;
      - `check("merged source hidden", "False", str(any("mara-s-map" in line for line in effective.render_threads(cid_merged, True))))`, and the same with `"mara-s-oath"` over `render_commitments`. These catch a source line leaking even if the canonical line is right;
      - `check("merged ids are canonical", "winifred-s-chart", ",".join(t["id"] for t in effective.threads(cid_merged)))`.
- [ ] **Step 5: Run the switched consumers' tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_actor_context.py tests/test_actor_scoped_skip.py tests/test_absorb_store.py tests/test_suggest_store.py tests/test_context.py -q`. Expected: PASS.
- [ ] **Step 6: Prove the identity law on whole prompts.**
  - Run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_frozen_campaign.py tests/test_llm_fakes.py -q`. Expected: PASS **with `snapshot.json` untouched**. That covers every assembled scene prompt, and Task 0's `absorb.*_snapshot` and `suggest.build_prompt` keys, so the absorb prompt and the suggestion prompt are byte-identical too.
  - Then, from the repo root, run `make check-templates check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS. If dropping the `plot`/`commitments` imports resolved a recorded finding, `make baseline` and commit the smaller file.
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
    - The clock is first set with `clock.advance(cid, to="2026-05-10")` (`preview(days=...)` raises `ClockError` with no current date).
    - Two commitments, both due `"2026-05-12"`, are aliased; then `clock.preview(cid, days=5)["commitments"]` (`preview` returns the digest itself) has one row, with the canonical id and `aliases == [{"ref": "commitment:<src>", "title": ..., "status": "open"}]`.
    - The preview's `["aging"]["overdue"] == 1` (it is 2 before the switch: both dues are behind the 05-15 landing day, which is what makes this assertion bite).
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_briefing_route.py tests/test_clock_store.py -q`. Expected: the two new tests FAIL, with two rows instead of one.
- [ ] **Step 3: Implement both switches.**
  - **Rewrite `briefing.py`'s comment above the four reads** (lines 164-168 today: "Two reads of one file, both inside the hold: `open_threads` projects the rows and `involvement.touched_scenes` needs the beats it drops"). After the switch it is false: the rows come from `effective.threads`/`commitments`, and the flags from `involvement.group_touched`, which loads all three ledgers and folds alias members into their canonical. The new comment describes that pair (effective rows; group-folded touched scenes keyed by canonical id) and **keeps** the paragraph on why they are guarded separately (rows versus flags: losing the narrowing is degrading, losing the obligations is lying).
  - Check that `briefing.py` and `clock.py` reference `plot.` / `commitments.` only in docstrings and comments afterwards. From `backend/`: `grep -n "plot\.\|commitments\.\|open_threads\|touched_scenes" src/grimoire/store/briefing.py src/grimoire/store/clock.py`. Expected hits: `briefing.py`'s docstring lines (47-48 and 136 today), `clock.py`'s (128 and 152 today), and the rewritten briefing comment, which the reviewer reads to confirm it names the effective rows and `group_touched` rather than "two reads of one file"; no code line. A "No such file" message means the wrong directory, not a clean result.
- [ ] **Step 4: Run the switched modules' tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_briefing_route.py tests/test_clock_store.py tests/test_aging_store.py tests/test_events_routes.py -q`. Expected: PASS.
- [ ] **Step 5: Run the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_import_guard.py -q`. Expected: PASS (`clock` → `effective` is acyclic).
- [ ] **Step 6: Prove the briefing and digest unchanged.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_frozen_campaign.py -q`.
  - `briefing.build[...]` must not move (its rows strip `aliases`).
  - `clock.preview[...]` may differ **only** by `aliases: []` on its thread and commitment rows. If it does, regenerate (`-m tests.fixtures.frozen_campaign.sweep`) and run Task 10 Step 4's script with `old`/`new` passed through a `strip(v)` that recursively deletes every `"aliases"` key before `moved` is computed, and with `added == []` asserted. Expected: no key moved and none added. Commit the regenerated snapshot with this task.
- [ ] **Step 7: Lint and types.** From the repo root, `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 8: Commit:** `feat(continuity): briefing and advance digest read canonical records`

---

### Task 5: `continuity.pressure` — the contract, and the event, holiday and birthday sources

**Files:**
- Create: `store/continuity/pressure.py`
- Modify: `store/continuity/__init__.py` (docstring only, no imports): a `pressure` bullet, "temporal pressure: one dated item list over events, holidays, birthdays and deadlines; read-only, and never imported by `clock`".
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
  - `state_of(kind: str, relation: str, in_days: int | None, *, warn_days: int, passed: bool = False, reached: bool = False) -> str`. Pure. It applies the Global Constraints precedence, where *a deadline kind* means `kind == "deadline"`, or `kind == "linked_deadline"` and `relation in {"before", "by", "on"}`. `reached=True` forces `overdue` on a deadline kind, except a `by`/`on` link with `in_days == 0`, which is `today` (Global Constraints, "the event's own day"). It never returns `stale`.
  - `build(cid, now: str | None = None, horizon: int | None = None, sources=ALL) -> {"now", "friendly", "fixed", "items"}`.
    - An unknown source name raises `ValueError`.
    - `now=None` reads `clock.now(cid)` under `try`, with `""` on failure.
    - `horizon=None` means `UPCOMING_WINDOW_DAYS`; a negative value is clamped to 0.
    - `fixed` and `friendly` (`describe(fixed)["friendly"]`) are `None`/`""` when unreadable.
    - Items have Decision 4's shape, in Decision 5's order.
    - **Sources:** this task builds `_event_items` (Decision 6), `_holiday_items` (Decision 7) and `_birthday_items` (`birthdays.occurrences(cid, now_fixed, horizon, provider=provider)`). `_SOURCE_FNS` maps each kind to its function; `build` loops over the requested kinds and calls each through `_soft(fn, [], ctx)`, so each runs only when requested and fails on its own. `ctx` carries `cid`, `provider`, `now_fixed`, `horizon`, `warn_days` and the event rows, read once.
    - **Provider and `now`:** each is resolved once, through `_soft`, with `None`/`""` on failure.
    - `_soft(fn, fallback, *args)` holds the module's only `except Exception:  # noqa: BLE001`. `build` stays at complexity ≤ 10 (Global Constraints, "Complexity").
    - **Never raises** for an existing campaign.

- [ ] **Step 1: Write the failing tests.** The helper is `_campaign(monkeypatch, tmp_path, *, calendar="gregorian", holidays=(), region="", warn=None, now="2026-05-10")`.
  - It builds on `test_notices_store._campaign` (`{"name", "month", "day"}` custom rules; `region=""` switches the holiday library off) but calls `campaigns.create_campaign("Run", wid, calendar=calendar)`.
  - It then calls `clock.advance(cid, to=now)` only when `now` is not `None`.
  - Events are created with `events.create(cid, name, date)`, which normalizes the date and raises on an unparseable one. An undated or unparseable event row is hand-written into `campaigns.campaign_root(cid) / "events.json"`, e.g. `{"midsummer-rite": {"name": "The Saltmarch rite", "date": "midsummer", "note": "", "fired": None}}`.
  - Tasks 6 and 7 reuse this helper.
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
    | linked_deadline | by | 0 | 7 | | True | today |
    | linked_deadline | on | 0 | 7 | | True | today |
    | linked_deadline | before | 0 | 7 | | True | overdue |
    | linked_deadline | by | None | 7 | | True | overdue |
    | linked_deadline | after | -3 | 7 | | | ok |
    | linked_deadline | after | 2 | 7 | | | upcoming |
    | event | on | -2 | 7 | True | | passed |
    | event | on | 0 | 7 | | | today |
    | holiday | on | 30 | 7 | | | upcoming |
    | birthday | in_month | None | 7 | | | ok |

    The `after` rows pin `state_of` alone. `build` never emits an `after` item outside `0 < in_days ≤ 30` (Decision 8), which Task 6 pins.

  - `test_several_holidays_are_kept_and_today_included`: rules on 05-10, 05-12 and 05-20 give holiday items with `in_days` `[0, 2, 10]` and states `["today", "upcoming", "upcoming"]`. Each `ref == notices.holiday_key(fixed, name)`.
  - `test_holiday_ref_matches_the_notice_key_for_an_untrimmed_name`: the rule name `"Saltmarch Eve "` gives `ref == notices.holiday_key(fixed, "Saltmarch Eve ")`, which ends `":Saltmarch Eve"`.
  - `test_one_holiday_item_per_ref`: rules `"Saltmarch Eve"` and `"Saltmarch Eve "` on 05-12 give exactly one holiday item with that ref (Decision 7).
  - `test_holiday_in_days_counts_from_now`: a rule on 05-12 has `in_days == 2`, not the shifted row's 3.
  - `test_holidays_and_birthdays_stop_at_the_horizon`: a rule on 06-20 (41 days) is absent with the default horizon and present with `horizon=45`. A rule exactly 30 days out (06-09) is present by default, and one 31 days out (06-10) is absent.
  - `test_a_raising_secondary_costs_only_holidays`: a secondary calendar whose plugin `__init__` raises `RuntimeError` gives no holiday items, while the event and birthday items are unchanged.
  - `test_events_listed_regardless_of_horizon_and_ordered_on_the_fixed_axis`:
    - The coronation on 2026-08-01 (state `ok`, `in_days 83`) and Mara's audience on 2026-05-12, plus a rule holiday on 05-12.
    - The order is the audience event, then the 05-12 holiday (same fixed day, `KINDS` order), ..., then the coronation.
    - Event items carry `relation "on"`, `subject None`, `due_text ""` and `precision "exact"`.
  - `test_a_passed_unfired_event_is_passed_and_a_fired_one_is_gone`: `_campaign(..., now="2026-05-01")`, then create an event dated 05-08, then `clock.advance(cid, to="2026-05-10")`, which fires it (the span `(05-01, 05-10]` contains it). Then create an event dated 05-05. The 05-05 event gives `state "passed"`, `in_days -5`; the 05-08 event is absent. (A first advance from a blank clock fires nothing, because the digest's `lo == hi`; the docstring says so.)
  - `test_an_event_with_a_time_is_day_level`: advance the clock to `"2026-05-12T21:00"` **first**, then create an event at `"2026-05-12T20:00"` (created before, the advance would fire it). It gives `fixed == F("2026-05-12")`, `in_days 0`, `state "today"` and `native "2026-05-12T20:00"`.
  - `test_a_fired_event_on_today_is_today`: advance onto its day; it is listed with `state "today"`.
  - `test_birthday_items_carry_precision_actor_and_age`: Mara `"1985-05-12"` gives a `birthday` item with `relation "on"`, `in_days 2`, `age 41`, `actor f"characters:{aid}"` and `precision "exact"`. Winifred `"--06"` gives `relation "in_month"`, `fixed None`, `in_days None` and `state "ok"`, sorted after every dated item.
  - `test_hebrew_axis_keeps_every_chanukah_day`: `_campaign(..., calendar="hebrew", now="5786-Kislev-24")` has `len([i for i in items if i["kind"] == "holiday" and i["label"] == "Chanuka"]) >= 3`, with distinct refs, sorted by `fixed`.
  - `test_hebrew_birthday_and_event_ordering`: `_campaign(..., calendar="hebrew", now="5786-Kislev-24")`.
    - Mara, born `"5745-Kislev-26"` (an exact birth with a known year), gives a `birthday` item with `precision "exact"`, `relation "on"`, `in_days 2`, `fixed == F("5786-Kislev-26")` and `age == provider.age(F("5745-Kislev-26"), F("5786-Kislev-26"))` (41). This is the Hebrew exact-birthday case §28.5 asks for; Task 1 covers only yearless and month-only on Hebrew.
    - An event created with `events.create(cid, "The coronation", "5786-Kislev-26")`, a Chanuka day, sorts on the fixed-day axis beside that day's items in `KINDS` order: the items whose `fixed == F("5786-Kislev-26")` are, in order, the event, then that day's holiday items (Chanuka among them), then Mara's birthday. Every item for an earlier fixed day (the Kislev-25 Chanuka item) precedes them, and every later one follows.
  - `test_fake_provider_axis`: a plugin `_TITHE_PROVIDER_SRC` is written to `tmp_path/"calendars"/tithe_test.py` and registered as `"tithe-test-calendar"`.
    - Natives are integer strings, and `parse` raises `CalendarError` on non-integer input (a bare `int()` would raise `ValueError` past every `except CalendarError`). `describe` gives `year = fixed // 360`, `month = fixed % 360 // 30 + 1`, `day = fixed % 30 + 1`; 12 months of 30 days with keys `"01"`..`"12"`; `holidays()` returns `{"name": "Tithe", "fixed": f}` for every `f % 10 == 0` in range. Model it on `test_calendar_plugins._PROVIDER_SRC`. It lives in a module-level `_TITHE_PROVIDER_SRC`, reused by Task 6.
    - Setup order: `_campaign(..., now=None)`, then write the plugin and `calendar.json` (`calendars.write_calendar`), then `clock.advance(cid, to="735")`. Advancing to `"735"` over a stored Gregorian date would make `clock._current` parse it with the plugin.
    - Holidays fall at `in_days` `[5, 15, 25]`, and an event on `"736"` sorts before the first holiday.
  - `test_fake_provider_birthdays`: on the tithe calendar at `"735"`, an exact birth `"378"` gives a `birthday` item with `in_days 3`, `age 1`, `precision "exact"`; a month-only `"--02"` gives `relation "in_month"`, `fixed None`, `in_days None`. (A yearless `"--MM-DD"` is not testable here: the plugin's natives have no `Y-M-D` form, so `_birth_fixed` gets `CalendarError` and the row is skipped, which the test also asserts.)
  - `test_no_now_means_no_dates_are_invented`: `_campaign(..., now=None)` (no clock, no chronicle) gives `fixed None` and `friendly ""`. An event is listed with `fixed` set, `in_days None` and `state "ok"`; there are no holiday or birthday items.
  - `test_a_raising_plugin_degrades_to_undated_items` (Review Focus 1):
    - **While the calendar is still Gregorian** (`events.create` resolves the provider through `primary_provider`, which would let the `RuntimeError` out), create an event, a commitment with a parseable due (`"2026-05-14"`) and a thread with a beat.
    - Then register a plugin whose class `__init__` raises `RuntimeError` as `"broken-test-calendar"`, and overwrite calendar.json with it as primary.
    - Then `build(cid)` does not raise and returns `fixed None`. The event is listed undated (`fixed None`, `in_days None`, `state "ok"`). There are no holiday, birthday, `deadline` or `linked_deadline` items. The deadline half is trivially true in this task; from Task 6 on, the same test exercises `aging.prepare`, which lets the `RuntimeError` out, so it stays in the file unchanged.
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
- [ ] **Step 6: Lint and types.** From the repo root, `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS (no `C901`, no unmarked `BLE001`, no new mypy entry for `pressure.py`).
- [ ] **Step 7: Commit:** `feat(continuity): temporal pressure contract with event, holiday and birthday sources`

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
- Produces: the `deadline` and `linked_deadline` sources in `build`, per Decision 8. `_deadline_candidates` and `_linked_candidates` each run through `_soft`; a private `_choose(candidates)` picks one item per commitment (most urgent, then earliest, then `deadline`, then lowest link id) and appends the `after` items. Every deadline item has `subject = "commitment:<id>"` and `due_text = row["due"]`.
  - **`deadline` items:** `native = provider.format(fixed)`, `friendly = describe(fixed)["friendly"]`.
  - **`linked_deadline` items:** `ref = "event:<eid>"`, `relation` is the link's relation, and `fixed` is D−1 (`before`) or D (`by`/`on`/`after`), or `None` for a fired-stamp candidate whose date cannot be read.
  - **Reached events:** `fired` set, or `passed`. A reached event passes `reached=True` to `state_of`.

- [ ] **Step 1: Write the failing tests.** Commitments are created with `commitments.set_movement(cid, mid, title, "promise", "open", due, beat, scene)`, links with `continuity_doc.put_link(cid, lid, {"a": "commitment:...", "b": "event:...", "relation": ..., "created": "", "scene": "", "note": ""})` (which works for a hand-written undated event, since only existence is checked), and aliases with `continuity_doc.put_alias`. `warn=7`.
  - **Provider axis (spec §28.5).** A `cal` fixture is parametrized over three rows, each `(calendar, now, due = now+4, event D = now+10)`, with every date written in that calendar's natives:
    - `gregorian`: `2026-05-10`, `2026-05-14`, `2026-05-20`;
    - `hebrew`: `5786-Shevat-20`, `5786-Shevat-24`, `5786-Adar-01` (11 days out, so D−1 is `5786-Shevat-30` and crosses a month boundary);
    - `tithe-test-calendar`: `735`, `739`, `745`, with Task 5's `_TITHE_PROVIDER_SRC` and setup order.

    Assertions are written against `F(...)` and `in_days = F(target) − F(now)`, never Gregorian literals. The tests marked **[axis]** take `cal`; the rest run on Gregorian at 2026-05-10.
  - `test_a_parseable_due_is_a_deadline` **[axis]**: due gives one item with `kind "deadline"`, `ref == subject == "commitment:mara-s-oath"`, `in_days 4`, `state "due_soon"`, `due_text` the stored due, `relation "on"` and `native == provider.format(F(due))`.
  - `test_free_text_due_is_not_arithmetic`: `"before the bells stop"` gives no item for that commitment. Positive control: a second commitment with due `"2026-05-14"` in the same campaign gives its item.
  - `test_overdue_due`: `"2026-05-01"` gives `state "overdue"`, `in_days -9`.
  - `test_before_link_derives_d_minus_one` **[axis]**: `before` the event gives `kind "linked_deadline"`, `ref "event:the-coronation"`, `fixed == F(D) - 1` (and on Hebrew `== F("5786-Shevat-30")`), `in_days == F(D) - 1 - F(now)`, `relation "before"` and `due_text ""`.
  - `test_by_and_on_links_derive_d` **[axis]**.
  - `test_after_sets_no_deadline` **[axis]**: `after` gives one `linked_deadline` item with `relation "after"`, `in_days == F(D) - F(now)` and `state "upcoming"`, and no other item for the subject.
  - `test_after_is_only_ever_upcoming`: no `after` item when D is behind now, when D is today (an event created on the clock's day, unfired), or when D is 45 days out (Decision 8).
  - `test_earliest_of_due_and_link_wins` **[axis]**: due `now+15` and `before` the event (D−1 = `now+9`) give exactly one item for the subject: `kind "linked_deadline"`, `fixed == F(D) - 1`, `due_text` the stored due.
  - `test_a_link_to_a_reached_event_is_overdue`: an event on 2026-05-12 with a `by` link. `clock.advance(cid, to="2026-05-12")` fires it, and the state is `"today"` (the event's own day). `clock.advance(cid, to="2026-05-13")` makes it `"overdue"`. A second event dated behind the clock and left unfired (`passed`) gives `overdue` too.
  - `test_backwards_clock_keeps_a_fired_event_reached` (Review Focus 2): advance to 05-21 (firing the coronation of 05-20), then `clock.advance(cid, to="2026-05-15", reason="correction")`.
    - The coronation is **not** an event item (fired, not today).
    - The commitment linked `before` it is `overdue`, even though `in_days` is positive.
    - The same commitment also given due `"2026-05-17"` (`due_soon` on its own) is still one item: `state "overdue"`, `kind "linked_deadline"`.
  - `test_a_redated_fired_event_outranks_an_earlier_due`: `_campaign(..., now="2026-05-01")`, create the coronation for 05-05, advance to 05-10 (firing it), then `events.update(cid, "the-coronation", date="2026-05-30")` (the stamp is kept). Mara's oath, due `"2026-05-12"` and `before` the coronation, gives exactly one item: `state "overdue"`, `kind "linked_deadline"`, `fixed == F("2026-05-29")`, `due_text "2026-05-12"`.
  - `test_a_fired_link_survives_a_raising_plugin` (Review Focus 1): an event on 05-12 fired by advancing to 05-13, and a commitment with due `"2026-05-20"` and a `by` link to it. Then switch the primary to Task 5's broken plugin. `build(cid)` gives exactly one deadline item for the subject: `kind "linked_deadline"`, `state "overdue"`, `fixed None`, `in_days None`, and no `deadline` item.
  - `test_resolved_and_merged_away_commitments_make_no_deadline`: a fulfilled commitment with a due gives no item. An alias source with its own due gives an item **before** `put_alias` (positive control), and none of its own after it.
  - `test_a_merged_source_due_is_not_inherited` (Review Focus 4): source due `"2026-05-14"` and canonical due `""`. Before aliasing, the source yields one `deadline` item with `subject "commitment:<src>"` (positive control); after `put_alias`, no deadline item exists for either ref.
  - `test_dangling_or_undated_link_contributes_nothing`: a link to a deleted event, and one to a hand-written event dated `"midsummer"`, give no items. Positive control: a third link from the same commitment to a dated event gives its item.
  - `test_thread_links_never_make_deadlines`: `thread:mara-s-map before event:the-coronation` gives no deadline item. Positive control: the same link from a commitment does.
- [ ] **Step 2: Run them and confirm the right ones fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_pressure.py -q`. Expected RED (no deadline items yet): every **[axis]** test, `test_overdue_due`, `test_a_link_to_a_reached_event_is_overdue`, `test_backwards_clock_keeps_a_fired_event_reached`, `test_a_redated_fired_event_outranks_an_earlier_due`, `test_a_fired_link_survives_a_raising_plugin`, and the positive-control halves of the four negative tests. `test_after_is_only_ever_upcoming` may already pass, since it only asserts absence; its counterpart is `test_after_sets_no_deadline`.
- [ ] **Step 3: Implement** the two sources and `_choose`.
- [ ] **Step 4: Run them and confirm they pass.** Use the same command. Expected: PASS, including Task 5's `test_a_raising_plugin_degrades_to_undated_items`, which now reaches `aging.prepare`.
- [ ] **Step 5: Lint and types.** From the repo root, `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS (the `int | None` arithmetic on `fixed`/`in_days` adds no mypy entry).
- [ ] **Step 6: Commit:** `feat(continuity): deadline and link-deadline pressure with reached events`

---

### Task 7: `continuity.drivers` and `GET /continuity/drivers`

**Files:**
- Create: `store/continuity/drivers.py`
- Modify:
  - `routes/continuity.py`: the import becomes `from ..store.continuity import doc, drivers, effective, review`; `_matching` is deleted and its call site uses `drivers.matching()`; the new route; one module-docstring sentence;
  - `routes/__init__.py`: the docstring row for `continuity` names `/continuity/drivers`;
  - `store/continuity/__init__.py` (docstring only, no imports): a `drivers` bullet, "scene drivers and date anchors composed from `pressure`, `effective` and `involvement`; read-only".
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
    - **Inputs:** every input (pressure, the two row lists, links, involvement, aging, roster) is read through the module's `_soft(fn, fallback, *args)`, with an empty fallback (`aging.prepare` can let a plugin's `RuntimeError` out). `snapshot` composes private builders (`_record_drivers`, `_temporal_drivers`, `_anchors`, `_attach_links`) and stays at complexity ≤ 10.
    - **Never raises** for an existing campaign.
  - `GET /api/campaigns/{cid}/continuity/drivers?offscreen=<bool>`: `def get_drivers(cid: str, offscreen: bool = False)`. It calls `_campaign_or_404`, then returns `drivers.snapshot(cid, offscreen=offscreen)`. No lock (Decision 14). The handler is a `def`, not `async`.

- [ ] **Step 1: Write the failing tests.** In `test_continuity_drivers.py`, use Task 5's `_campaign` helper (imported from `tests/test_continuity_pressure.py`, or copied), with `region=""` and the clock at 2026-05-10. The default calendar block is `region="US"`, and 2026-05-10 is 15 days before a US federal holiday, so the fixtures in `test_continuity_effective.py` (default calendar, made through `/api/campaigns`) would add holiday drivers and anchors to every exact list below. Holiday rules are dated explicitly (05-12). Scene ids are `S1, S2, S3 = "001--saltmarch", "002--realm", "003--winifred"`, as in Slice A's suites:
  - `test_driver_kinds_are_pinned`.
  - `test_snapshot_shape_and_matching`: the keys are exactly `{"now", "friendly", "fixed", "matching", "drivers", "anchors"}`, and `matching == "basic"`.
  - `test_thread_and_commitment_drivers`:
    - `"Mara's map"` (advanced) gives a driver `{"ref": "thread:mara-s-map", "kind": "thread", "label": "Mara's map", "status": "advanced", "summary": <latest beat>, "pressure": {"state": "ok", "in_days": None, "friendly": ""}, "time_anchors": [], "links": []}`.
    - A closed thread and a fulfilled commitment are absent.
  - `test_aliases_collapse_to_one_driver`: an aliased pair gives one driver, with the canonical ref and actors from the whole group (`involvement.of`).
  - `test_commitment_pressure_from_its_deadline_item`: due 4 days out with `warn 7` gives `pressure == {"state": "due_soon", "in_days": 4, "friendly": "14 May 2026"}`.
  - `test_stale_comes_from_aging_on_the_merged_last_scene`:
    - `stale_after_days: 10` is written through `calendars.write_calendar`.
    - The canonical's only beat is at S1, and `chronicle.absorb(cid, {"id": S1, "one_line": "", "date": "2026-03-31"})` dates it 40 days back. The source's beat is at S2, dated `"2026-05-08"`.
    - The merged `last_scene` follows scene **play order** (`_latest_scene` on `parse_sid` numbers), not date. So the higher-numbered scene must be the recent one, or the assertion fails for a reason unrelated to the code.
    - The canonical is `stale` before the alias and `ok` after it. Task 8's `test_aging_uses_the_merged_last_scene` reuses this setup.
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
  - `test_drivers_route_survives_a_raising_plugin` (Review Focus 1): while the calendar is still Gregorian, create a thread with a beat and a commitment with a parseable due; then switch the primary to the broken plugin. The route answers 200 with `fixed is None`, and the thread and commitment drivers are present with `pressure == {"state": "ok", "in_days": None, "friendly": ""}`. That exercises `aging.prepare`'s escaping `RuntimeError` on both the pressure and the staleness path;
  - `test_drivers_route_makes_no_model_call`: monkeypatch `store.embed_space.resolve` to return a dict; override `app.dependency_overrides[routes.get_llm]` with `llm_fakes.from_entries([])`, which raises `CassetteMiss` on any request (no inline fake, per CLAUDE.md); and monkeypatch `grimoire.embeddings.EmbeddingsClient` to a class whose constructor raises `AssertionError`. The route answers 200 with `matching == "semantic"`, so a semantic `matching` is shown to cost no model and no embedding call.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_drivers.py tests/test_continuity_routes.py -q`. Expected: FAIL, with `ImportError` for `drivers` and a 404 for the route.
- [ ] **Step 3: Implement** `drivers.py`, the route, and the `_matching` → `drivers.matching()` change.
- [ ] **Step 4: Run them and confirm they pass.** Use the same command. Expected: PASS, including Slice A's `test_get_continuity_empty` (`matching == "basic"`).
- [ ] **Step 5: Run the route and module guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_route_order.py tests/test_path_guard_store.py tests/test_routing_guard.py tests/test_pydantic_guard.py tests/test_import_guard.py tests/test_lock_domain_guard.py tests/test_store_api_baseline.py -q`. Expected: PASS. `CROSSING_PAIRS` needs no entry: `continuity` is composed before `entities`. If `test_route_order` fails, stop and report rather than editing the pinned table.
- [ ] **Step 6: Lint and types.** From the repo root, `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS (no new mypy entry for `drivers.py` or the route).
- [ ] **Step 7: Commit:** `feat(continuity): scene driver projection and GET /continuity/drivers`

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
  - **Docstring:** **add** a paragraph after the aging paragraph (the one ending at `campaigns.py:1577` today; there is no Slice A note to replace). It covers: rows are effective; why alias sources are not top-level rows; and that edit, quick close and delete already address the canonical (`routes/ledger.py`'s `_live_target` / `_refuse_unsafe_delete`).
  - **TS types:** `export type LedgerAlias = { ref: string; title: string; status: string };` and `PlotThread` gains `aliases?: LedgerAlias[]`. It is optional, because digest rows and `blankSpec` literals omit it.
  - **`rowsFor`:** the threads and commitments notes become `joinNote(agingLabel(x.aging), merged(x), …existing parts)`, where `merged(x) = x.aliases?.length ? \`Merged: ${x.aliases.map(a => a.title).join(", ")}\` : ""`. It is plain text: the link to the Reviewed-links group is Slice D's.

- [ ] **Step 1: Write the failing tests.**
  - In `test_ledger_route.py` (reusing `_thread_ids`/`_merge`-style helpers that POST `/ledger/threads` and `/continuity/aliases`):
    - `test_a_merged_source_is_not_a_top_level_row`: after merging `"Mara's map"` into `"Winifred's chart"`, `[t["id"] for t in plot] == ["winifred-s-chart"]`, with `aliases == [{"ref": "thread:mara-s-map", "title": "Mara's map", "status": "open"}]`.
    - `test_merged_rows_carry_the_group_latest_beat_and_scene`.
    - `test_aging_uses_the_merged_last_scene`: Task 7's stale/ok scenario through the route.
    - `test_a_dangling_alias_leaves_its_source_visible`: `doc.put_alias` to a missing target leaves both rows, with `aliases == []`.
    - `test_a_garbled_continuity_file_still_answers`: 200, with the physical rows.
    - `test_ledger_survives_a_raising_plugin` (Review Focus 1, §26 "calendar unavailable"): **while the calendar is still Gregorian**, create `"Mara's map"` merged into `"Winifred's chart"` and a commitment with a parseable due (`"2026-05-14"`). Then register Task 5's broken plugin (`__init__` raises `RuntimeError`) and make it the primary. `GET /ledger` answers 200; `plot` is exactly the canonical row (`id == "winifred-s-chart"`, `aliases` naming `thread:mara-s-map`), the commitment row is present, and neither carries an `aging` key (the existing `aging.prepare` guard at `campaigns.py:1672` empties the badges, not the rows). Nothing in the suite exercises that guard today, and this task changes the reads that feed it.
    - Task 0's `test_ledger_rows_are_pinned_for_an_ordinary_campaign` is the no-alias pin: its only edit in this task is `"aliases": []` added to each literal row. Any other change it needs is a regression.
  - Update `test_a_record_with_non_string_fields_still_renders_as_a_row`: its expected dict gains `"aliases": []`, and its `all(isinstance(...))` exclusion gains `"aliases"`. Give `test_a_thread_with_non_string_fields_still_renders_as_a_row` the same exclusion.
  - Extend `test_the_three_sections_are_read_under_one_campaign_lock`: also watch `store.continuity.doc.read` (imported as `from grimoire.store.continuity import doc as continuity_doc`; patch `continuity_doc.read`), and assert `held["continuity"] is True`.
  - In `LedgerView.test.tsx`:
    - `a merged thread names what was merged into it`: a thread with `aliases: [{ref: "thread:mara-s-map", title: "Mara's map", status: "open"}]` gives `findByText(/Merged: Mara's map/)`.
    - `a merged commitment names what was merged into it`: a commitment (kind `promise`, a stale `aging` block, `latest_beat: "Sworn at the gate"`) with `aliases: [{ref: "commitment:mara-s-oath", title: "Mara's oath", status: "open"}]` gives `findByText(/Merged: Mara's oath/)`, and the note is exactly `` `${agingLabel(aging)} · Merged: Mara's oath · PROMISE · Sworn at the gate` `` (`agingLabel` imported from `../aging`), so the order is aging · Merged · KIND · beat. Without this, the commitments branch of `rowsFor` could drop the part or misplace it and nothing would fail.
    - `a row with no aliases says nothing about merging`: `queryByText(/Merged:/)` is null.
    - `closing a merged thread addresses the canonical`: the quick close calls `ledgerSaveThread` with `("run", "winifred-s-chart", {status: "closed"})`.
- [ ] **Step 2: Run the backend tests and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_ledger_route.py -q`. Expected: FAIL (two rows; `aliases` missing).
- [ ] **Step 3: Run the frontend tests and confirm they fail.** From `frontend/`, run `npx vitest run src/routes/LedgerView.test.tsx`. Expected: FAIL (no "Merged:" text).
- [ ] **Step 4: Implement** the route switch, the types and the note line.
- [ ] **Step 5: Run the backend ledger tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_ledger_route.py tests/test_ledger_routes.py tests/test_events_routes.py -q`. Expected: PASS.
- [ ] **Step 6: Run the frontend ledger tests.** From `frontend/`, run `npx vitest run src/routes/LedgerView.test.tsx`. Expected: PASS.
- [ ] **Step 7: Typecheck.** From `frontend/`, run `npx tsc --noEmit -p .`. Expected: no errors.
- [ ] **Step 8: Eslint ratchet.** From the repo root, `make check-eslint PY=$PWD/backend/.venv/bin/python` (it runs `npm ci` first). Expected: PASS. `LedgerView.tsx` carries a recorded `react/no-array-index-key`; if the change resolves it, `make baseline` and commit the smaller `lint-baselines/eslint.json`. Also `make check-lint PY=$PWD/backend/.venv/bin/python`.
- [ ] **Step 9: Commit:** `feat(ledger): effective rows with merged aliases on the note line`

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
- Consumes: `effective.commitments` only, imported as `from ..store.continuity import effective as continuity_effective`. Not `effective.links` (Decision 16).
- Produces:
  - **`_owed(cid) -> list[dict]`:** `[c for c in continuity_effective.commitments(cid) if c.get("due")]`, in effective order (Decision 16: today's rule, canonical). It raises what `effective.commitments` raises.
  - **`_chore_owed`:** `n = len(_owed(cid))`, and `"what": f"{n} open commitment{'s' if n != 1 else ''} with a deadline"`. The rest of the dict, the literal `"group": "Continuity"` and `except (OSError, ValueError): return None` are unchanged.
  - **`_items_owed`:** unchanged apart from reading `_owed(cid)`. Each item's `detail` stays `" · ".join(x for x in (f"due {c['due']}", c.get("kind"), c.get("latest_beat")) if x)`, as Task 0 pins.
  - **`ledger_open`:** `_ledger_open(cid) -> int | None` (private), which is `len(continuity_effective.commitments(cid))`, or `None` on any exception.
  - **`rail.ts`:** `tailLabel: (s) => lbl(s?.campaign?.ledger_open, "open commitments")`.
  - **CampaignHub:** the card is titled `"Open commitments"`. The tail is `camp && camp.ledger_open != null ? String(camp.ledger_open) : undefined`. The body is `unknown` when `!camp`; `<p className="field-hint">Could not be counted.</p>` when `camp.ledger_open == null` (Decision 17); else the existing two strings.

- [ ] **Step 1: Write the failing tests.**
  - In `test_todo_route.py`:
    - `test_owed_counts_canonical_commitments` (Review Focus 4):
      - Mara's oath (due `"2026-06-01"`) is aliased into Winifred's promise (due `"2026-06-03"`), and a third commitment has no due.
      - The chore has `n == 1` and `what == "1 open commitment with a deadline"`.
      - Its items have `[i["id"] for i in items] == ["winifred-s-promise"]`.
      - With the canonical's due blanked, the chore is absent: a due on the source is not inherited.
    - `test_owed_counts_stated_dues_only`: a commitment with no due and a `before` link to the coronation is **not** counted (links reach Todo with the pressure split, Decision 16), and a free-text due (`"before the bells stop"`) **is** counted. This pins that Todo's set is not pressure's.
    - Task 0's `test_owed_is_pinned_for_an_ordinary_campaign`: its only edit in this task is `what` becoming `"2 open commitments with a deadline"`; `n`, every item and every `detail` are unchanged.
    - `test_owed_does_no_calendar_work`: monkeypatch `store.calendars.primary_provider` with a recorder. `GET /api/todo?campaign=<cid>` leaves it unrecorded.
    - `test_owed_chore_and_items_agree`: chore `n` equals items `total` (the `test_the_counts_and_the_instances_agree` pattern).
  - In `test_shell_route.py`:
    - `test_ledger_open_counts_effective_open_commitments`: an aliased pair plus one fulfilled commitment gives `ledger_open == 1`.
    - `test_ledger_open_is_null_on_a_garbled_ledger`: commitments.json `"{ no"` gives 200 with `ledger_open is None`.
    - `test_ledger_open_ignores_a_garbled_continuity_file` (Review Focus 5): continuity.json `"{ no"` with two open commitments gives 200 and `ledger_open == 2`.
    - Task 0's `test_ledger_open_counts_open_commitments` stays green unedited.
  - Frontend:
    - `AppRail.test.tsx:68` expects `/ledger & timeline, 0 open commitments/i`.
    - `rail.test.ts`: `ledgerRow.tailLabel!(withCampaign({ ledger_open: 2 })) === "2 open commitments"` and `tailLabel!(withCampaign({ ledger_open: null }))` is `undefined`.
    - `CampaignHub.test.tsx`: `a null ledger count says it could not be counted`. `shell({ ledger_open: null })` gives `findByText("Open commitments")` and `findByText("Could not be counted.")`. `queryByText("Nothing is owed.")`, `queryByText("Loading…")`, `queryByText(/still open/)` and `queryByText("null")` are all null. (The old "renders no number" assertion passes today too, because the falsy branch prints "Nothing is owed.".)
    - `TodoView.test.tsx:136`: the mock text becomes `"2 open commitments with a deadline"` (cosmetic). `TodoView.test.tsx:154`'s `owed` mock (`what: "2 open threads"`) becomes `"2 open commitments"` as well, so no fixture keeps the old noun.
- [ ] **Step 2: Run the backend tests and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_todo_route.py tests/test_shell_route.py -q`. Expected: FAIL (two counted, old label, a 500 on garbled).
- [ ] **Step 3: Run the frontend tests and confirm they fail.** From `frontend/`, run `npx vitest run src/components/AppRail.test.tsx src/shell/rail.test.ts src/routes/CampaignHub.test.tsx`. Expected: FAIL.
- [ ] **Step 4: Implement** the backend and frontend changes.
- [ ] **Step 5: Run the backend tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_todo_route.py tests/test_shell_route.py tests/test_roster_shell.py -q`. Expected: PASS.
- [ ] **Step 6: Run the frontend tests.** From `frontend/`, run `npx vitest run src/components/AppRail.test.tsx src/shell/rail.test.ts src/routes/CampaignHub.test.tsx src/routes/TodoView.test.tsx src/shell/tabs.test.ts src/components/PhoneTabs.test.tsx`. Expected: PASS.
- [ ] **Step 7: Typecheck.** From `frontend/`, run `npx tsc --noEmit -p .`. Expected: no errors (every `ledger_open` reader handles `null`).
- [ ] **Step 8: Ratchets.** From the repo root, `make check-eslint check-lint PY=$PWD/backend/.venv/bin/python`. Expected: PASS (`CampaignHub.tsx`, `rail.ts`, `types.ts` and the four test files are under `recommendedTypeChecked`).
- [ ] **Step 9: Commit:** `feat(todo,shell): owed and the ledger badge count canonical commitments`

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
- [ ] **Step 5b: Lint.** From the repo root, `make check-lint PY=$PWD/backend/.venv/bin/python`. Expected: PASS (`sweep.py` is under ruff; the new imports keep `I001` clean).
- [ ] **Step 6: Commit:** `test(frozen): sweep the continuity projections; snapshot gains only new keys`

---

### Task 11: Slice gate

- [ ] **Step 1: Lint, types, eslint and templates.** From the repo root, run `make check-lint check-mypy check-eslint check-templates PY=$PWD/backend/.venv/bin/python` (`check-eslint` runs `npm ci` first). Expected: PASS. Fix any new finding in code. If a change resolved a recorded one, run `make baseline` and commit the smaller baseline file.
- [ ] **Step 2: The full backend suite, under its coverage floor.** From the repo root, run `make check-py PY=$PWD/backend/.venv/bin/python` (the CI target: `--cov-fail-under=$(COV_FLOOR)`, which a bare pytest skips). Expected: PASS, apart from the failures recorded on the base commit (`test_atomic.py::test_a_read_only_record_is_not_silently_replaced` fails when run as root).
- [ ] **Step 3: The Android dependency set.** Run `make check-pydantic1 PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 4: The frontend gate.** Run `make check-web`. Expected: PASS (typecheck plus vitest under coverage). Together, Steps 1-4 are every target of `make check`.
- [ ] **Step 5: Re-run the §7.3 consumer grep.** The spec makes the plan re-run it, and a consumer added between planning and execution would otherwise ship unswitched, showing alias sources as live records in its prompt. From `backend/`: `grep -rn "open_threads(\|open_commitments(\|render_open(" src/grimoire --include=*.py`. Expected hits, and only these:
  - `store/plot.py` and `store/commitments.py`: the definitions, and each `render_open` body calling its own projection;
  - `store/continuity/effective.py`: `_physical`'s two reads, and the render helpers' physical fallbacks (`plot.render_open` / `commitments.render_open`).

  Any other hit is either switched in this slice or named as a physical-history reader, with the reason, in the gate commit's message. A "No such file" message means the wrong directory, not a clean result.
- [ ] **Step 6: Amend the spec to match the recorded deviations.** Edit `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md` so that it no longer contradicts the code on any "Deliberate deviations" bullet, citing this plan's decision number in each edit (as Slice A amended the spec with its plan revision, and still had a stale §7.0 row at its final review):
  - §7.0's `pressure` row gains `clock` and `campaigns.paths`; its `drivers` row gains `embed_space`, `aging` and `appearances.cast` (Decision 1);
  - §13's item fields gain `precision`, `actor` and `age`, and a `linked_deadline` item's `ref` is the event with the commitment as `subject` (Decision 4);
  - §13.4's "`null` for an unparseable due" is replaced by "an unparseable or free-text due produces no item", with the fired-link exception (Decision 8);
  - §13.5 states the three interpretations: most urgent then earliest, a `by`/`on` link on its event's own day is `today`, and `after` is only ever `upcoming` inside the suggestion window (Global Constraints; Decision 8);
  - §13.6 gives `occurrences`'s keyword-only `provider`/`roster`/`visible_characters`, the row's `year`/`month_name`/`native`/`friendly`, and `upcoming` projecting the shared lazy scan rather than the materialized list (Decisions 2, 3);
  - §14's link `direction` adds `"both"` for `related_to` (Decision 13);
  - §18.3 records that the overdue/due-soon split is not taken in Slice B and link deadlines are not counted yet (Decision 16).

  Commit: `docs(spec): continuity capstone matches slice B's recorded deviations`.
- [ ] **Step 7: Implementation → done.** Run `/codex:review` against the slice diff. Resolve the findings and commit. If Codex is unavailable, record the independent reviewer that stood in.
- [ ] **Step 8: Done → actually done.** Run `/codex:adversarial-review` against the diff **and** the spec. Ask specifically whether the diff implements Slice B (§13–§13.6, §14, §20 tuples, §21 drivers route, §7.0 render helpers, §7.3 consumer switch, §8 canonical briefing, §12.5 rows and note, §18.3, §27), and look for gaps, drift and quietly dropped requirements. Resolve the findings and commit. Record a substitute reviewer if Codex is unavailable.

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
| §27 identity law; frozen sweep; verify_templates | 0 (pre-switch characterization), 2, 3, 4, 10 |
| §28.5 pressure tests (Gregorian, Hebrew, fake provider, `sources`) | 5: holidays on all three; exact birthdays and event-versus-holiday ordering on all three (Hebrew via `test_hebrew_birthday_and_event_ordering`); yearless birthdays on Gregorian and Hebrew (Task 1), not on the fake provider, which has no `Y-M-D` native; month-only on all three (Hebrew at the `occurrences` level, Task 1). 6: due, D−1, `by`/`on`, `after` and earliest-wins on all three via the `cal` axis; the rest on Gregorian |
| §28.7 "owed counts canonical commitments" | 9 |

**Deliberate deviations from the spec text:**

- `birthdays.occurrences` takes keyword-only `provider`, `roster` and `visible_characters` after the spec's three parameters (Decision 2). Its rows add `year`, `month_name`, `native` and `friendly` (Decision 3).
- `upcoming` projects the same lazy per-actor scan (`_actor_hits`) rather than the materialized `occurrences` list that §13.6's "a projection of `occurrences`" reads as. The output is the same; the difference is failure behaviour: a plugin raising on a later day cannot cost a line that renders today (Decision 2).
- Pressure items add `precision`, `actor` and `age`. A `linked_deadline` item's `ref` is the event ref; the commitment is `subject` (Decision 4).
- Link `direction` adds `"both"` for `related_to`, which the spec's shape does not enumerate (Decision 13).
- `drivers` imports `embed_space` for `matching`, which §7.0's table omits (Decision 1). It also imports `aging` (the `stale` state on effective rows, Decision 10) and `appearances.cast` (offscreen player tokens, Decision 12, since it may not import `suggest`). `pressure` imports `clock` (§13.1 names `clock.now`) and `campaigns.paths` (the calendar root).
- An unparseable or free-text due produces no pressure item. §13.4's "`null` for an unparseable due" is read as describing nothing, because §13.1 and the `kind` row restrict `deadline` items to parseable dues. A calendar failure therefore drops dated deadline items rather than listing them undated. The one exception is a link to an event with a `fired` stamp, which stays `overdue` with `fixed`/`in_days` `None`, because reachedness needs no arithmetic (Decision 8).
- §13.5's sentences are reconciled as the Global Constraints state: the most urgent candidate wins and then the earliest, so a reached link outranks an earlier due; a `by`/`on` link on its event's own day is `today` whether or not the event fired; an `after` link is only ever `upcoming`, inside the suggestion window.
- An event item's `native` keeps the stored time (`"…T20:00"`), while all arithmetic is day-level.
- Todo `owed` does not take §18.3's optional overdue/due-soon split; the cost rule says why. It counts exactly §18.3's set, today's non-empty-`due` rule made canonical, and does **not** count link deadlines yet (Decision 16). That keeps a links-only campaign unchanged and the global page off `effective.links`. Todo's set is a stated deadline, dated or not; pressure's is dated deadlines only.
- `ledger_open` becomes nullable, and the hub card is retitled. The spec names only the rail label; the cost rule and the identical mislabel justify both changes (Decision 17).
- Briefing rows strip `aliases` (Decision 15).
- The §7.2 absorb materialization redirect stays with Slice C, as Slice A's plan recorded. Once Task 3 shows canonical ids only, an absorb staged before a merge and saved after it can still name a hidden source; Slice C closes that.

**Open questions (none block execution):**

- Hebrew month-only birthdays match month keys literally (inherited from `_when`), so a `--Adar` birthday disappears in every leap year, and `--Adar1` in every common year. Slice B keeps it byte-for-byte (§13.6); a fix would change the suggestion prompt's birthday line and belongs in a slice that owns that line.
- Link deadlines in Todo arrive with the overdue/due-soon split through `pressure.build(sources={"deadline", "linked_deadline"})`, once a slice takes the plugin-code cost that implies.

- The Ledger delete-refusal text says "or delete anyway", which the UI cannot do until Slice D's force path (F6). Slice B leaves the wording.
- "Bounded" anchors: this plan bounds holidays and birthdays by the horizon and lists every dated, non-passed event. If Slice E's prompt cap needs a tighter bound, it applies `DRIVER_PROMPT_CAP` there.

**Type consistency:**

- `pressure.PRESSURE_STATES`, `SORT_ORDER`, `HIGH_PRESSURE`, `KINDS`/`SOURCES`/`ALL` and `state_of` are defined in Task 5, and used in Tasks 6 and 7.
- `drivers.DRIVER_KINDS`, `matching` and `snapshot` are defined in Task 7, and used in Task 10.
- `effective.render_threads` / `render_commitments` are defined in Task 2, and used in Tasks 3 and 10.
- `involvement.group_touched` is defined in Task 2, and used in Task 4.
- `birthdays.occurrences` (row keys per Decision 3, `year` included) is defined in Task 1, and used in Task 5.
- The item key set `{ref, kind, label, native, friendly, fixed, in_days, relation, state, subject, due_text, precision, actor, age}` is the same in Tasks 5, 6 and 7.
- `ShellCampaign.ledger_open: number | null` (Task 9) is read through `num`/`lbl`, which already accept `null`.

---

## Plan-gate review resolution

Every finding was checked against the code and the spec before it was resolved. All 38 were real, though #25 only in part, and none was rejected. Where two lenses reported the same defect, the rows say so and share one resolution.

| # | Finding (lens) | Verdict | Resolution and where |
|---|---|---|---|
| 1 | Inline per-source `try/except` breaks `C901` and `BLE001` (guards) | Applied | Confirmed: `ruff.toml` has `max-complexity = 10`, `BLE` is selected, and `ruff.json` has no entry for either new module. New Global Constraint "Complexity and broad excepts": `_SOURCE_FNS` dispatch, one `_soft` helper with the only `noqa: BLE001`, and drivers' builders. `make check-lint` now ends Tasks 1, 3, 4, 5, 6, 7, 8 and 9. |
| 2 | eslint ratchet never run (guards) | Applied | Confirmed: `check-web` is typecheck plus vitest only (`Makefile:137-138`). The "Lint ratchet" constraint names `eslint.json` and the recorded `LedgerView.tsx` finding. `make check-eslint` is Task 8 Step 8, Task 9 Step 8 and Task 11 Step 1. |
| 3 | `verify_templates` import adds `I001`/`E402` (guards) | Applied | Reproduced with the repo's ruff on a copy: the combined line gives `I001` plus a seventh `E402`; two single-name lines with `# noqa: E402` give neither. Task 3 Step 4 uses those two lines, and Step 6 runs `check-lint`. |
| 4 | Bare pytest skips the coverage floor (guards) | Applied | Task 11 Step 2 is `make check-py PY=...`, and the root-only failure note is kept. |
| 5 | Task 4's grep uses repo-root paths from `backend/` (guards) | Applied | Task 4 Step 3 uses `src/...` paths and lists the expected docstring-only hits (`briefing.py` 47-48 and 136; `clock.py` 128 and 152). |
| 6, 12 | Earliest-wins overrules a reached link (spec, temporal) | Applied | Confirmed: `events.update` keeps the fire stamp, and `advance` fires only on `elapsed_days > 0`. The most urgent candidate now wins, then the earliest (Global Constraints §13.5 interpretations; Decision 8). Pinned by `test_backwards_clock_keeps_a_fired_event_reached` (now with an earlier due) and `test_a_redated_fired_event_outranks_an_earlier_due`. A fired-stamp link survives a missing `now` or provider as an undated `overdue` item (`test_a_fired_link_survives_a_raising_plugin`). |
| 7 | "`fixed` null for an unparseable due" read silently (spec) | Applied | Kept "no item" (the `kind` row and §13.1 support it). Stated in the pressure contract and added under "Deliberate deviations", with the fired-link exception. |
| 8, 19 | §28.5's three-provider matrix only partly covered (spec, temporal) | Applied | Task 6 has a `cal` axis (Gregorian, Hebrew, tithe) for due, D−1 (Hebrew crosses Shevat 30), `by`/`on`, `after` and earliest-wins. Task 5 adds `test_fake_provider_birthdays`. The self-review row now says exactly what runs on which axis. |
| 9, 16 | Holiday items can share one ref; `in_days` shift and the raising secondary unspecified (spec, temporal) | Applied | Decision 7: recompute `in_days` from `now_fixed`, dedupe by ref keeping the first (as `notices.pending`), and pin the raising secondary as emptying holidays only. New Task 5 tests: `test_one_holiday_item_per_ref`, `test_holiday_in_days_counts_from_now`, the 30/31-day boundary, and `test_a_raising_secondary_costs_only_holidays`. |
| 10 | `drivers`/`pressure` import edges beyond §7.0 partly unrecorded (spec) | Applied | Global Constraints (Imports), Decision 1 and the deviations bullet now name `aging`, `appearances.cast`, `clock` and `campaigns.paths`. |
| 11, 18, 22 | Todo `owed`'s "agree with pressure" rationale is false, and counting links moves a links-only campaign (spec, temporal, compat) | Applied (option 1) | Decision 16 is now exactly §18.3: today's non-empty-`due` rule, made canonical, with no link counting. The rationale states that Todo's set (dated or not) is deliberately not pressure's (dated only). Task 9 drops `effective.links`/`review.describe`. `test_owed_counts_stated_dues_only` pins that a link-only commitment is not counted and a free-text due is. Decision 18's identity claim now holds for links-only campaigns. |
| 13 | `after` can be `today`/`ok` and depends on firing (temporal) | Applied | Decision 8 and the Global Constraints: an `after` item exists only while unreached and `0 < in_days ≤ 30`, always `upcoming`. Pinned by `test_after_is_only_ever_upcoming`. `state_of`'s `after` rows are now noted as `state_of` alone. |
| 14 | `by`/`on` on D: `overdue` or `today` depending on firing (temporal) | Applied | Rule chosen: `reached` forces `overdue` except a `by`/`on` link with `in_days == 0`, which is `today` (Global Constraints; `state_of` interface). Four new `test_state_precedence` rows. `test_a_link_to_a_reached_event_is_overdue` advances onto D (`today`), then past it (`overdue`). |
| 15 | "Fired by that advance" fixture fires nothing (temporal) | Applied | Confirmed: a blank clock's first advance has `lo == hi`. The test starts at 05-01, creates the event, then advances. |
| 17 | Hebrew Adar behaviour mis-described; leap year untested (temporal) | Applied | Verified with the real provider (`--Adar1-14` folds in 5786; `--Adar` gives nothing at 5787-Adar1-20). Review Focus 3 is reworded as a literal month-key match; a leap-year row is added to Task 1; an Open Question records the disappearance. |
| 20 | Event `native` with a time, and the same-day rule, unpinned (temporal) | Applied | Pressure contract and Decision 4: native keeps the stored time; arithmetic is day-level. `test_an_event_with_a_time_is_day_level` creates the event after the advance, so it is not fired. |
| 21 | Byte identity proven only for the scene prompt (compat) | Applied | New Task 0 runs on pre-switch code. It adds sweep keys (absorb snapshots, `briefing.build`, `suggest.build_prompt` for both offscreen values, `clock.preview`), and route pins for `/ledger` rows, `owed` `n`/`detail` and `ledger_open`. Tasks 1, 3 and 4 require the snapshot to be unchanged. Task 4's single regeneration is proven to be alias-keys-only. Task 8/9 pins may change only `aliases` / `what`. |
| 23 | Render helpers' catch-all drops the section (compat) | Applied | Confirmed: `render_open` guards only the read. Task 2's helpers fall back to the physical render, with `prompts.render` outside the `try`. New `test_render_helpers_fall_back_to_the_physical_render`. |
| 24 | Eager `_scan` changes failure behaviour; flags not forwarded (compat) | Applied | `_scan` is a generator, and `_when`/`upcoming` take the first hit (Decision 2; Task 1 interface). New `test_upcoming_stops_at_the_first_hit` and `test_upcoming_forwards_visible_characters`. |
| 25 | Fast path still parses twice; Todo loads every ledger (compat) | Applied in part | `_rows` reads raw only when `groups` is non-empty, pinned by `test_no_alias_rows_parse_the_ledger_once`. `records` is unchanged, because it needs the raw beats for every row. Todo no longer calls `effective.links` (#11). |
| 26 | Task 8 replaces an "interim note" that does not exist (compat) | Applied | Confirmed by reading the docstring. Task 8 says "add a paragraph after the aging paragraph" and names `_live_target` / `_refuse_unsafe_delete`. |
| 27 | Test helper has no `calendar`/`now`, so four setups break (tests) | Applied | The Task 1 and Task 5 helpers take `calendar` and `now`. Setup order is given for the fake provider (blank clock first; `parse` raises `CalendarError`), no-now, and the raising plugin (create rows while still Gregorian). |
| 28 | Occurrence row has no `year` (tests) | Applied | `year` added to Decision 3, the Task 1 interface, the test rows and the deviation bullet. The projection test compares against real occurrences rather than hand-built rows. |
| 29 | Task 7 fixtures inherit the US region (tests) | Applied | Task 7 uses Task 5's helper (`region=""`, clock 2026-05-10, holiday on 05-12), and the reason is stated. |
| 30 | Raising-plugin tests never reach `aging.prepare` (tests) | Applied | Both tests now create a thread and a commitment before switching. Pressure asserts no deadline items; the route asserts thread and commitment drivers at `ok`. The Fail-soft constraint names `aging.prepare` (`aging.py:80`). |
| 31 | Null count reads "Loading…" forever; test cannot catch it (tests) | Applied | Decision 17 and Task 9: an explicit "Could not be counted." body. The test asserts its presence and the absence of "Nothing is owed.", "Loading…", `/still open/` and "null". |
| 32 | Negative Task 6 tests pass before implementation (tests) | Applied | Each has a positive control, and Step 2 names exactly which tests are RED. |
| 33 | Staleness test's scene dating unspecified (tests) | Applied | Task 7 gives S1/S2 chronicle dates, the `write_calendar` threshold, and the play-order caveat; Task 8 reuses it. |
| 34 | Digest aging assertion is trivial (tests) | Applied | Both dues are behind the landing day, and the test asserts `overdue == 1` (2 before), after setting the clock first. |
| 35 | Undated or unparseable events cannot come from `events.create` (tests) | Applied | Task 5's helper notes say to hand-write `events.json` and to link with `continuity_doc.put_link`. |
| 36 | "No model call" test overrides an unused dependency with an inline fake (tests) | Applied | It uses `llm_fakes.from_entries([])` and also makes `grimoire.embeddings.EmbeddingsClient` raise on construction. |
| 37 | Slice gate is not `make check` (tests; duplicate of #2 and #4) | Applied | Task 11 Steps 1-4 now cover every `make check` target. |
| 38 | Review Focus 5's shell claim has no test (tests) | Applied | `test_ledger_open_ignores_a_garbled_continuity_file` is added to Task 9 and cited in Review Focus 5. |

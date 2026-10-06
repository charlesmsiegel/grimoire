# Continuity Capstone — Slice E: Scene Suggestion Control — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scene suggestions reason over the campaign's canonical story drivers, and the reader can steer them:

- the suggestion snapshot shows thread and commitment ids, the commitments, a dated timeline, a capped driver index and the reviewed links between drivers;
- the model's reply names the drivers each suggestion serves and an optional time anchor, and the parser validates both against the driver index captured when the request arrived;
- dates anchored to an event, holiday or birthday are derived or checked with calendar arithmetic;
- the chooser gains Story Pressure controls (Normal / Focus / Avoid / Must per driver, and a time mode with anchors);
- cards show validated reasons and warn about constraint misses;
- saved scene ideas keep their driver and time provenance, and visibly go stale without being destroyed;
- the Story Graph handoff can seed the chooser.

The intent prompt and, with no control set, the instruction section of the system prompt stay byte-identical.

**Architecture:** `store/suggest.py` stays the one module that builds and parses the suggestion prompt. It gains these parts:

- the snapshot, built over Slice B's `pressure.build` and `drivers.snapshot`, with one pressure computation shared by the timeline and the index;
- a frozen `Controls` value and `resolve_controls`, which validates a request against a snapshot;
- `driver_view`, the capped, pinned index the template renders;
- pure claim, date and ordering helpers, which `parse_output` and the eval grader share;
- read-time and write-time provenance for saved ideas.

`store/scene_ideas.py` stores the two new optional fields and unions them on a re-save. It still imports nothing from continuity.

`routes/scenes.py` gains a JSON body, maps `suggest.ControlsError` to 400 or 409 **before** `runs.run_draft`, and hands the captured snapshot and controls to the payload shaper.

The frontend changes in these files:

- a pure `pressureControls.ts` helper module;
- a `StoryPressure` disclosure inside the picker's Generated group;
- the hook carries controls read at dispatch;
- provenance chips on cards;
- a Stale subgroup;
- `ScenesView` adopts the `{chooser: ...}` history state once.

**Tech Stack:** Python 3.11, FastAPI, pydantic (v1/v2-agnostic), Jinja2 (StrictUndefined), pytest + `TestClient`; React + TypeScript, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md`. This plan implements §31 Slice E:

- §15 (all of it), §15.1, §15.2 and §15.3;
- §16.1–§16.5. §16.5 is the receiving side only; Slice F builds the sending side;
- §17 and §17.1;
- §20's `DRIVER_ACTIONS` tuple and the TS unions for drivers;
- §21's `POST /scene-suggestions` body and its `GET`/`POST /scene-ideas` changes;
- §23's `scene_suggestions` and `scene_intent` rows;
- §24 for the suggestion parser, §26's calendar-unavailable row for suggestions, and §27's suggestion and scene-idea compatibility;
- §28.6, the chooser half of §28.9, and §28.10 cases 9–10;
- §30's "Claims to address" wording;
- AC7–AC12.

Out of scope:
- Slice D: the review UI, candidates, reconcile, Ledger addressing, Todo chores. Card chips do **not** link to Ledger rows, because that addressing is D's.
- Slice F: the Story Graph page and the sending side of the handoff.

**Branching:** Each slice gets its own branch stacked on the previous slice's tip, and the whole stack is rebased onto `main` at landing (the owner's direction). Slices do not gate on each other's reviews. Work on `claude/continuity-capstone-slice-e`, created from the **current Slice D tip** (`claude/continuity-capstone-slice-d`). If A–D gain fixes after this branch is cut, rebase onto the new D tip. **Before Task 0:**
1. Run `git status`. If the tree holds edits that are not this slice's, stop and ask the owner. Never sweep them into a Slice E commit.
2. Record the base commit's gate. From the repo root, run `make check-py PY=$PWD/backend/.venv/bin/python` and `make check-web`. Write any pre-existing failure (test id and a one-line cause) into the slice ledger, `.superpowers/sdd/2026-10-05-continuity-capstone-e-suggestion-control/progress.md`. Task 13 may excuse only failures listed there. `backend/tests/test_atomic.py::test_a_read_only_record_is_not_silently_replaced` fails when run as root, and is environmental.
3. Confirm Slice B's final interfaces on the tip, because B was being implemented while this plan was written:
   - `pressure.build`'s signature, item keys and `SORT_ORDER` / `HIGH_PRESSURE`;
   - `drivers.snapshot`'s keys, driver and anchor shapes, and order;
   - the `GET /continuity/drivers` handler.

   Also confirm which TS names Slice D already declared in `frontend/src/api/types.ts`. If a later review changed any of these, adjust the affected task's interface lines before starting it, and record the change in the ledger.
4. Collect the hand-offs addressed to this slice. Read the B, C and D slice ledgers (`.superpowers/sdd/2026-10-05-continuity-capstone-{b,c,d}-*/progress.md`; a ledger that does not exist yet is noted as absent). Copy every item that names Slice E, the suggestion prompt, the chooser or the TS driver unions into this slice's ledger under `## Inherited hand-offs`, one line each, with its source (file and line) and either an owning task or a recorded reason for deferring it. Seed the section with these four, which the earlier plans already make:
   - **(h1)** B plan, Open Questions ("Bounded" anchors): B lists every dated, non-passed event and leaves the prompt bound to Slice E's `DRIVER_PROMPT_CAP`. Owner: Task 3 (the rendered timeline is capped with the index's rule, Decision 11).
   - **(h2)** B plan, Open Questions: Hebrew month-only birthdays match month keys literally, so a `--Adar` birthday disappears in leap years. Deferred, with the reason: the fix is in the birthday month-key match, which the intent prompt's Birthdays line also renders, and §15 requires that prompt byte-identical; Slice E keeps the line as B pinned it (Decision 10), and this plan's Open Questions keeps the item open.
   - **(h3)** B plan, Self-review (§20 row): the TS unions for `DRIVER_KINDS` / `PRESSURE_STATES` wait for Slice E's chooser. Owner: Task 8 (`test_ts_mirrors_the_python_tuples` pins the `types.ts` unions as well as the const arrays).
   - **(h4)** B plan, Global Constraints: an event's `native` keeps its time of day ("Slice E may pass it on"). Owner: Task 4 (`test_anchor_day_boundaries_ignore_the_time_of_day`, `test_on_date_is_canonical`).

   An inherited item that changes a task's interface is applied to that task before the task starts, as in step 3.

---

## Global Constraints

- **Privacy:** fixtures, eval recordings, docstrings and commit messages use only Seraphine, Mara, Winifred, Realm and Saltmarch, phrases built from them ("Mara's map", "Mara's oath", "The coronation", "Saltmarch Eve"), or ids already used as placeholders in the tree (`find-the-ledger`, `the-midnight-deadline`, `the-debt`, `salt-owed`). Never read `~/.grimoire`. No committed text describes store contents. Every constant below is justified structurally.
- **Spec values (verbatim):**
  - `DRIVER_PROMPT_CAP = 40`. The rendered index is ordered by pressure-state precedence, then dormancy. Focus, must and anchor refs are always included; the rest is summarised as "and N more".
  - Must refs: at most **3**, and only `thread` or `commitment` kinds. Temporal constraints go only through the time anchor.
  - Driver actions:
    - thread → `advance`, `close_candidate`;
    - commitment → `address`, `fulfill_candidate`, `break_candidate`, `expire_candidate`;
    - event, holiday, birthday → `anchor`.
  - `time_mode` ∈ `auto` | `near` | `move` | `anchor`. Its meanings:
    - `near`: [now, now + max(warn_days, 7)];
    - `move`: blank a date ≤ now;
    - `anchor`: requires `time_anchor_ref`.
  - Anchor relations are `before` | `on` | `after` | `by`. With D the anchor's fixed day and d the suggestion's:
    - `on`: the date is derived from the anchor;
    - `on` with a month-only birthday: the model's date is kept only if its month key matches and it is not behind `now` (skipped with no `now`);
    - `before`: now ≤ d < D;
    - `by`: now ≤ d ≤ D;
    - `after`: D < d ≤ D + `calendars.RESOLVE_WINDOW_DAYS` (400).
  - Precedence: `must_include > avoid > focus > normal`.
  - `must ∩ avoid` → 400. More than 3 must refs, or a temporal must ref → 400. Anchor mode with no valid anchor, or an anchor ref with another mode → 400. A ref outside the request-time index → 409 `{kind: "stale_drivers", refs}`.
  - The UI order is `pressure.SORT_ORDER = ("overdue", "today", "due_soon", "upcoming", "passed", "stale", "ok")`. High pressure is `pressure.HIGH_PRESSURE = {"overdue", "today", "due_soon"}`.
  - Card action labels: Advances / May close / Addresses / May fulfil / May break / May expire. The rejected-date line reads "date not consistent with anchor". Warnings use "claims to address" wording (§30). Nothing says "Embedding required".
  - History state: `{chooser: {drivers: {[ref]: "focus"}}}` or `{chooser: {anchor: {ref, relation: "on"}}}`.
  - The routing task stays the literal `"suggestions"`.
- **Prompt needles (exact substrings).** Templates, tests and the eval case pin these, so each is written once, here.
  - **Drivers addendum** (`scene_suggestions/instruction/drivers_addendum.j2`):
    - `Each suggestion also includes key "drivers"`
    - `"time_anchor"`
    - each action word in double quotes, rendered from `view.actions`, never typed into the template
    - `High pressure means a driver whose state is`, followed by the `HIGH_PRESSURE` states in `SORT_ORDER` order, rendered from `view.high_pressure`
    - `Not every suggestion should serve the same drivers`
    - `At least one suggestion should address a high-pressure driver, if any exists`
    - `Near-future temporal anchors are worth using`
    - `A quiet character or relationship scene is welcome when nothing urgent dominates`
  - **Controls addendum** (`scene_suggestions/instruction/controls_addendum.j2`):
    - `Focus drivers:`
    - `Spread the focus drivers across the suggestions`
    - `Avoid drivers:`
    - `Must-include drivers:`
    - `Every suggestion must serve each must-include driver`
    - `between the current date and` (near)
    - `later than the current date` (move)
    - `Anchor every suggestion to` (anchor)
  - **Who pins what.** The sentence needles are pinned by Task 3's unit tests only: each is a §15.1 or §16.3 directive, and a reword edits this list and the template together. The eval case (Task 7) follows `evals/graders.grade_prompt`'s contract instead: its needles are only quoted action words, keys, states and refs, and the two addenda are covered whole by `graders.grade_prompt_section`, which renders the template on both sides.
- **Byte identity** (§15.1, §15.3, §23):
  - The "instruction section" is today's `instruction/{standard|offscreen}.j2` plus the date, rank and direction addenda, in today's order. It is unchanged for every snapshot and control combination.
  - The drivers addendum follows it only when the rendered index is non-empty. The controls addendum follows that only when `Controls.active`.
  - With no control and an empty index, the system message is byte-identical to today's.
  - The intent prompt (`scene_intent/*`) is byte-identical for every snapshot.
  - The campaign-flow cassette phrase `propose 3-4 DISTINCT scene openings` stays.
- **Imports** (`test_import_guard`). In `suggest`: `from .continuity import drivers as continuity_drivers` on its own line, then `from .continuity import effective, pressure`. That is the spelling ruff's isort (`I001`) accepts: only an `as`-aliased import stays on its own line, and `suggest.py` has no `I001` allowance. Both lines bind modules, so `test_import_guard` rule 3 holds. The alias exists because `drivers` is a `build_snapshot` keyword in this module. `scene_ideas` imports nothing new. `continuity.*` never imports `suggest` (Slice B rule). In `routes/scenes.py`, no new store-module import is needed, because everything is reached as `store.suggest.*`.
- **Route shape:** `post_scene_suggestions` stays a `def` with `@computes_only`. It must not become `async` (its reservation goes through a `BlockingPortal`). Every refusal is raised before `runs.run_draft`. `SceneSuggestionsRequest` uses plain `BaseModel` fields, with `Literal` for enums (`test_pydantic_guard`). New `raise` statements inside an `except` use `from exc` (B904 is recorded 37 times for `routes/scenes.py`; a 38th fails the ratchet).
- **Fail soft (§3.9, §24, §26):**
  - Snapshot reads never raise for an existing campaign. `drivers.snapshot` and `pressure.build` already do not. Each new read (commitments, links) sits in the same `except Exception  # noqa: BLE001` shape `open_threads` uses today.
  - **The calendar reads that the snapshot keeps did not fail soft for plugin code, and Task 2 makes them.** Today a primary plugin whose constructor raises anything but `CalendarError` escapes `_notation` (its `except` names `CalendarError`, `KeyError`, `ValueError`, `OverflowError`, `OSError`), `calendars.today_facts`'s guard, and `events.day_facts` / `birthdays.upcoming` (both resolve through `calendars.primary_provider`, which catches only `CalendarError`/`KeyError`). So `build_snapshot` raises and the route 500s before it reserves a run, which is not §26's "calendar unavailable" row. Decision 25 moves that block, unchanged, into a private `_calendar_facts` helper behind one broad catch. The healthy path is byte-identical, which Task 0's golden proves.
  - **The parser never raises for bad model JSON, and never for plugin code.** `parse_output` and `parse_next_date` resolve the provider once through `_soft_provider(croot)` (any exception → `None`). A `None` provider blanks every date (`""`, not `date_rejected`): no date is fabricated (§26). `valid_ids` failures still surface as the run's ordinary failure. `date_normalizer` resolves through `_soft_provider` too, so its other callers (`ref_validator`, and through it `validate_ideas` and `valid_refs`, plus `parse_intent`) blank dates under such a plugin instead of raising. Today they raise, and `GET /scene-ideas`' `_tolerant` turns that into an empty saved list. For every calendar that resolves, or fails with `CalendarError`/`KeyError`, nothing changes.
- **Complexity and broad excepts:** ruff `C901` (max 10) is recorded once for `suggest.py` (`build_snapshot` today) and five times for `routes/scenes.py`.
  - Each new behaviour is its own helper. `build_snapshot`'s new reads go into `_driver_capture(cid, offscreen, now)`, and `parse_output`'s into `claim` / `check_date` / `order_cards` / `_card`.
  - Every new broad catch carries `# noqa: BLE001 -- <reason>`.
  - If `build_snapshot` drops under 10, run `make baseline` and commit the smaller file.
- **mypy and eslint are ratcheted.** New files get zero allowance. `SceneIdeaPicker.tsx` keeps its recorded `key={i}` on generated cards: changing it would drop `react/no-array-index-key` and fail the gate until re-baselined. `NewSceneChooser.tsx` gains no handler on a non-interactive element.
- **Lock domain:** `scene_ideas` is in `DOMAIN_MODULES`. `add` keeps its whole read-union-write inside one `campaign_lock` hold. `suggest` writes nothing.
- **Commands:**
  - backend, from `backend/`: `PYTHONPATH=src .venv/bin/python -m pytest <files> -q -p no:cacheprovider`;
  - frontend, from `frontend/`: `npx vitest run <files>` and `npx tsc --noEmit -p .`;
  - ratchets and gates, from the repo root: `make <target> PY=$PWD/backend/.venv/bin/python`.

  One command per purpose. A `-k` filter is never combined with a file list it would deselect. Every backend task ends with `make check-lint check-mypy`, and every frontend task with `make check-eslint`.

## Review Focus

1. **A calendar plugin that raises a non-`CalendarError`, while the reader uses controls.**
   - This holds only because Task 2 and Task 4 add the guards (Decision 25). On today's tree the snapshot and the date normalizer both let the plugin's `RuntimeError` out, and the route 500s.
   - Expected: `build_snapshot(cid)` returns, with `notation`, `friendly`, `holidays_today`, `events_today` and `birthdays` blank. The snapshot has thread and commitment drivers, a timeline of undated items only, and no anchors. `POST /scene-suggestions` answers 202.
   - A focus on a thread is accepted. An anchor-mode request naming an event is refused 409 `stale_drivers`, because there are no anchors to name, and no run is reserved.
   - The parser keeps every suggestion, with every date blanked (`""`, `date_rejected` false), and `next_date` is `""`. Nothing is fabricated (§26).
   - Pinned in Task 2 (`test_snapshot_survives_a_raising_plugin`), Task 4 (`test_parse_survives_a_raising_plugin`) and Task 5 (`test_controls_survive_a_raising_plugin`).
2. **An event with a time of day, used as an anchor at the day boundaries** (`"2026-05-12T20:00"`).
   - `on` derives the date `"2026-05-12"`, with no time.
   - `before` rejects `2026-05-12` and accepts `2026-05-11`.
   - `by` accepts `2026-05-12`.
   - `after` rejects `2026-05-12` and accepts `2026-05-13` and D+400, and rejects D+401.
   - Pinned in Task 4 (`test_anchor_day_boundaries_ignore_the_time_of_day`).
3. **A custom-notation calendar, and a month-only birthday anchor.**
   - On the plugin calendar, the model's friendly form (`"9 M03 5"`) resolves, and an `on` derivation yields the plugin's own native.
   - On Gregorian, a month-only birthday anchored `on` keeps a model date in its month, and blanks one outside it with `date_rejected`. The month comes from the occurrence ref itself (`_month_of`, Decision 12), because pressure items and anchor options carry no `year`/`month_key`.
   - A request pairing a month-only anchor with `before` is a 400.
   - Pinned in Task 4 (`test_plugin_calendar_dates_derive_in_native_notation`, `test_month_only_birthday_on_keeps_only_its_month`, `test_month_only_on_against_a_real_campaign`) and Task 5 (`test_month_only_anchor_takes_only_on`). The two eval cases cover case 10 against recorded output, for `before` and for `on`.
4. **Aliases in controls and in model output.**
   - A chooser read taken before a merge sends the alias source as a focus ref; it canonicalizes to the canonical and is accepted, not answered 409.
   - Must on the source plus avoid on the canonical is a 400 `must_avoid`.
   - An alias whose target was deleted leaves its source standing as its own driver, and a control naming the source is accepted. `canon.canonical_refs` would have produced a spurious 409 here, which is why `live_canon` is used.
   - A reply naming both source and canonical yields one driver.
   - Pinned in Task 5 (`test_an_alias_source_focus_is_canonicalized`, `test_a_dangling_alias_source_is_its_own_driver`) and Task 4 (`test_a_canonical_alias_prevents_duplicate_drivers`).
5. **A saved idea anchored to an event that was rescheduled, deleted, or deleted and re-created under the same slug.**
   - Rescheduled: `stale_reason == "The coronation moved to <friendly>"`.
   - Re-created on another date: also "moved" (accepted by §17).
   - Deleted: the anchor is dropped from the returned idea, and the idea is stale only when no other stored ref is live.
   - Saved after it fired (the card was still on screen when the clock moved): the anchor is stored, because the write test is existence (§17), and the idea reads "The coronation has passed".
   - The idea stays active, is pickable from the Stale group, and is absent from the hub's Play next.
   - A damaged ledger or a raising calendar never empties the saved list: unknown classifies as live (Decision 17).
   - Pinned in Task 6 (`test_a_rescheduled_anchor_reads_as_moved`, `test_a_reused_event_slug_reads_as_moved`, `test_a_deleted_anchor_is_dropped_not_fatal`, `test_a_passed_anchor_is_stored_and_reads_as_passed`, `test_a_damaged_read_never_hides_ideas`) and Task 10 (`play next skips stale ideas`).

---

## Decisions (and why)

1. **Where the driver contract lives.**
   - The schema (`drivers`, `time_anchor`), the action vocabulary and the §15.1 diversity guidance go in a new `instruction/drivers_addendum.j2`. It is appended after today's addenda, and only when the rendered index is non-empty.
   - Focus, avoid, must and time go in `instruction/controls_addendum.j2`, only when a control is active.
   - Both begin with a leading space, like every addendum.
   - Why:
     - §15.1 requires the instruction section to be byte-identical with no controls, and it must remain a prefix of the system message;
     - the model still has to be told the schema in default mode, or "default diversity" (§15.1) and validated provenance (§16.4) cannot exist without controls;
     - a campaign with no drivers at all (nothing open, no clock) gets today's system message exactly.
   - Proven two ways: by Task 0's golden file, and by the identity `system(snapshot) == system(snapshot with driver_index=[]) + drivers_addendum`.
2. **The intent prompt is unchanged through one snapshot flag.**
   - `build_snapshot(cid, offscreen=False, *, drivers=True)`. With `drivers=False`, it returns today's key set exactly, `upcoming` included, and does no pressure or driver work. `build_intent_prompt` uses it and renders with `drivers=False`.
   - With `drivers=True` (suggestions), the snapshot has no `upcoming` key, and the template's Upcoming line is replaced by the timeline.
   - Why: deriving the Upcoming line from the timeline in Jinja would change both tie order and holiday spelling. `events.sooner` prefers the holiday on a tie, and `_upcoming` keeps provider emission order, while pressure sorts by `KINDS`, then ref.
   - `today_facts`, `day_facts`, `events.sooner` and the per-turn Today block are untouched, as §15 requires.
3. **One capture per request.**
   - `build_snapshot(drivers=True)` calls `pressure.build(cid, now=now)` once and passes the result to `drivers.snapshot(cid, offscreen, pressure_result=p)`. That is a new keyword-only parameter on Slice B's function.
   - So the timeline and the index come from one pressure computation at one `now`.
   - The route builds the snapshot once, validates the controls against it, renders from it, and captures both in the `work` closure. The parser and the labels use exactly what the prompt showed (§16.3).
4. **Canonicalization uses `effective.live_canon`, not `canon.canonical_refs`.**
   - The driver index is built from effective rows, which follow an alias only to an existing record.
   - `canonical_refs` follows a stored alias even to a deleted target, so a control naming a dangling alias's source (which is its own live driver) would canonicalize outside the index and get a spurious 409.
   - If `live_canon` raises (a garbled ledger), canonicalization is the identity.
   - Deviation from §16.3 step 1's wording, not its intent.
5. **Anchor validity is membership in `anchors`, not "a temporal driver".**
   - Passed and undated events are drivers but not anchors (Slice B Decision 11), and no date can be derived against them.
   - A request's `time_anchor_ref` follows two rules:
     - an id whose prefix is not `event`, `birthday` or `holiday` (or a malformed ref) is a structural 400 `anchor_kind`;
     - a temporal ref missing from `anchors` is a 409 `stale_drivers`, because the chooser only offers anchors, so a missing one means the campaign moved since the read (an event passed, or a holiday left the horizon).
   - A model's `time_anchor` naming a non-anchor is dropped, with no auto-added driver.
6. **A month-only birthday anchor takes only `on`.** Its `fixed` is null, so `before`/`by`/`after` have no D to compare against. A request pairing it with another relation is a 400 `anchor_relation`, and a model relation for it is coerced to `on`.
7. **Request validation order:** 404 → `_require_connection("suggestions")` (the existing 409 `missing_key` stays first, so a reader with no connection is told that before anything else) → snapshot → `resolve_controls` (all 400s, then the 409) → greeting candidates → prompt → `run_draft`.
   - `time_mode` and `time_anchor_relation` are `Literal`s, so an unknown value is FastAPI's 422.
   - A 400 body is `{kind: "bad_controls", detail, reason}`, with `reason` one of `must_avoid`, `must_cap`, `must_kind`, `anchor_missing`, `anchor_kind`, `anchor_without_mode` or `anchor_relation`. Tests assert `reason`.
   - A body, when present, wins wholly: query parameters are ignored. With no body, the query parameters build the request exactly as today, which keeps them working for one release (§16.3, §27).
   - A retried POST that reuses an `X-Grimoire-Attempt` after the drivers changed now gets a 409, not the original run. That is accepted: the refusal costs no generation, and the chooser re-reads.
8. **Controls value.** `@dataclass(frozen=True) class Controls` holds:
   - `focus`, `avoid`, `must` (tuples, in request order, de-duplicated after canonicalization);
   - `time_mode`, `anchor`, `relation` (the request's relation, possibly `""`).

   Precedence runs after the 400 checks: `avoid -= {anchor}`, then `focus -= must ∪ avoid`. `must ∩ avoid` is already a 400 by then, including when two different refs canonicalize to one.

   **The anchor beats avoid** (Decision 26). §16.3's precedence names only the driver states, and the anchor is a separate control. Choosing an anchor is the more specific instruction: every suggestion will be forced onto it (Decision 14). Leaving the ref in `avoid` would put an "avoided" warning on every card for a combination the request itself made. So it is dropped from `avoid` silently rather than refused, and the chooser never sends it anyway (Task 9).

   `active` is `bool(focus or avoid or must) or time_mode != "auto"`. `pinned` is must, then focus, then the anchor, de-duplicated.
9. **The rendered index (`driver_view`):**
   - **Sort key:** `(SORT_ORDER.index(state), dormancy is None, -(dormancy or 0), DRIVER_KINDS.index(kind), in_days is None, in_days or 0, ref)`. That is pressure order, then the coldest first (spec: "then dormancy"; the existing prompt says cold threads are worth reviving), then Slice B's own tie-breakers.
   - **Why `SORT_ORDER` and not `PRESSURE_STATES`.** B's `PRESSURE_STATES` is the order in which a state is *decided*, and it ranks `passed` second. A passed event is an unfired event behind `now`, such as a backfilled history event. It can be neither an anchor nor a date relation. Ranking it above `today` and `due_soon` would let a campaign with many of them push due-soon commitments into "and N more", which defeats §15.1's "at least one suggestion should address high pressure". `SORT_ORDER` is the §16.2 order the chooser shows, so the prompt and the UI rank alike.
   - **Selection:** every pinned ref present in the index, plus the first `max(0, DRIVER_PROMPT_CAP − len(pinned))` of the rest, rendered in sort order. `more = len(index) − len(rendered)`.
   - **Links:** de-duplicated by link id, and only those whose two endpoints are both rendered, so every ref the model sees in a link is one it was told it may use.
   - **Dormancy** is computed for commitments as well as threads, with the same `_dormancy` function. Temporal drivers have `dormancy: None`.
10. **Line formats in `user.j2`** (under `drivers` only; the `else` branch is today's text):
    - **Threads:** `- thread:<id> = <title> (<dormancy phrase>): <beat>`, which is today's line with the ref and `=` prepended, matching the cast and location "token = name" convention.
    - **Commitments:** `commitment:` followed by `snippets/commitment_line/absorb.j2` (`<id>: <title> (<kind>, <status>), due X — beat`), plus ` [<state>, <when>]` when the pressure state is not `ok`. This reuses the shared snippet, as §23 asks. The thread snippet has no dormancy, so the thread line stays bespoke.
    - **Timeline:** `- <friendly or native or "undated"> (<when>; <state>): <ref> = <label>`. Deadline kinds read `deadline for <subject> = <label>`, followed by ` (<relation> <ref>)` for a `linked_deadline`. `<when>` is `today` / `in 1 day` / `in N days` / `1 day ago` / `N days ago` / `day unknown` (month precision) / `undated`. The chooser's `whenPhrase` mirrors this list. The rows are `view.timeline` (Decision 11), followed by `…and <N> more dated items not listed.` when `view.timeline_more > 0`.
    - **Index:** `- <ref> = <label> (<kind>, <state>[, <when>])`, with `…and <N> more drivers not listed.` when `more > 0`.
    - **Links:** `- <a> <relation> <b>`.
    - **Birthdays** keep today's line in both modes (Slice B pins its bytes; spec §15 removes only `upcoming`).
11. **The rendered timeline is capped with the index's rule** (AC8's "bounded list"; B's hand-off h1). The snapshot's `timeline` key still holds every pressure item (§15: "the pressure items"), so the parser, the grader and the tests see them all. Only what the prompt renders is bounded, because the same temporal items are also index rows, and an uncapped timeline would let every unfired event reach the prompt past `DRIVER_PROMPT_CAP`.
    - **Always rendered:** every item whose `ref` or `subject` is in `controls.pinned` (focus, must, and the batch anchor), and the item `events.sooner` would pick (`snapshot["sooner_ref"]`, Task 2), so §15's "the timeline must contain the item that `sooner` would pick" holds in the prompt as well as in the snapshot.
    - **The rest** are ranked by `(SORT_ORDER.index(state), in_days is None, in_days or 0, KINDS.index(kind), ref)`, so the high-pressure states fill the cap first, and the first `max(0, DRIVER_PROMPT_CAP − len(always))` are kept.
    - **Rendered in the snapshot's own order** (B's fixed-day axis), not the ranking, so the block still reads as a calendar. `timeline_more = len(timeline) − len(rendered)`.
    - The whole anchor-option list is **not** pinned. It is every dated, non-passed event, so pinning it would reopen the bound; the chooser reads anchors from `GET /continuity/drivers`, not from the prompt, and the request's anchor is pinned.
    - A `HIGH_PRESSURE` item is ranked first rather than always rendered, so the bound is real (`max(DRIVER_PROMPT_CAP, len(always))`); it is cut only past 40 high-pressure items, where the index cuts it too.
    - A timeline under the cap renders whole and in today's order, so every fixture smaller than 40 items is unaffected.
12. **Date checks** (`check_date`):
    - `check_date` takes an **already normalized** native. Its callers (`_card`, `parse_next_date` and the eval grader) first run the model's raw text through `normalize_date`, which is the existing tolerant resolver anchored on the snapshot's `now`.
    - An absent or unparseable model date is `""` and is **not** `date_rejected`. Rejection means "a date was given and fails the rule".
    - Every comparison is on primary-provider fixed days, with the time split off.
    - **An `on` anchor's date is `provider.format(anchor["fixed"])`**, not the stored text. Event natives come from hand-editable events.json, and every other path canonicalizes. `split_native` is used only to drop the time when computing D.
    - **A month-only anchor's month comes from its ref.** Slice B's pressure items and anchor options carry no `year` or `month_key`; only the occurrence row does, and §4 fixes the ref as `birthday:<kind>:<id>:month:<year>-<key>`. The pure helper `_month_of(ref) -> tuple[int, str] | None` takes the text after `:month:` and matches `^(-?\d+)-(.+)$`. A key may contain `-`, and the year may be negative. The same helper serves Task 6's "is this month behind `now`".
    - **`provider is None`** (Decision 25) returns `("", False)`. Without a calendar, no date is checked or derived.
    - With no `now` (no clock and no chronicle date), the `now ≤` lower bound and the `near`/`move` checks are skipped, while the D bound still applies. No date is fabricated.
    - **`near` and `move` set `date_rejected` too.** The card text says "date not consistent with anchor" when anchored and "date not consistent with the time setting" otherwise.
    - **`next_date`** gets the `near`/`move` check, and is untouched under an anchor: it answers "if none is used", and an anchor constrains suggestions, not that.
13. **Card order (`order_cards`, a stable re-ordering of the parsed rows):**
    1. The first row whose claimed drivers or time anchor include a driver in `HIGH_PRESSURE` is moved first.
    2. Then, repeatedly, the earliest remaining row that claims a focus ref not yet covered is moved next.
    3. The rest keep model order.

    Only one high-pressure card is promoted, because §15.1 asks for "at least one", and the picker's two generated slots then hold one urgent card and one distinct focus card.
14. **Claims (`claim`):**
    - Model refs are canonicalized through the captured rows' `aliases` (no store read), de-duplicated by ref (the first action wins), and dropped when unknown or carrying an action invalid for their kind.
    - **Batch anchor:**
      - With an anchor in the request, every suggestion's `time_anchor` is forced to it.
      - The relation is the request's, else the model's if valid (and `on` for a month anchor), else `on`.
      - The anchor ref is added to `drivers` with action `anchor` when missing.
    - **Constraint misses:**
      - `unmet_must` lists the must refs not claimed;
      - `avoided` lists the avoid refs claimed;
      - `drivers` excludes avoided refs, but `time_anchor` keeps an avoided anchor and lists it in `avoided`. That case arises only from a model's own `time_anchor`: a request anchor is never in `avoid` (Decision 8).
    - No suggestion is ever dropped for a miss (§15.2).
15. **Offscreen keeps a PC's birthday driver** (Slice B Decision 12). An offscreen scene of NPCs planning around the player's birthday is legitimate, and the Birthdays line already names the PC offscreen today. The ref renders, while `token_ok` still drops any PC cast token. This is pinned so the cast rule cannot regress (`test_offscreen_keeps_a_pc_birthday_driver_but_never_its_cast_token`).
16. **Saved-idea write validation** (`suggest.idea_provenance`, called by the route, never by `scene_ideas`):
    - **Driver refs:**
      - canonicalize them, and drop a pair whose action is wrong for its kind;
      - thread, commitment and event refs are kept iff `effective.Ledgers.exists` is not `False` (existence in any status; `None`, an unreadable ledger, keeps the ref, since the read side reclassifies it);
      - holiday and birthday occurrence refs are computed rather than stored records, so they are kept iff they are well-formed: `holiday:<int>:<name>`, `birthday:<kind>:<id>:<int>` or `birthday:<kind>:<id>:month:<year>-<key>` (`_month_of`);
      - refs are de-duplicated by ref, and the first entry wins.
    - **`time_anchor`** passes the same test as a driver ref of its kind: **existence, not activity** (§17). Spec Appendix A T2 exists because "idea ref validation erases staleness evidence". A card anchored `before` the coronation and saved after the clock fired it must store that anchor, so that the read can say "The coronation has passed".
      - `relation`: one outside `ANCHOR_RELATIONS` becomes `on`, and a month ref is always `on` (Decision 6).
      - `native`: for an event, its date as currently stored in events.json; for a day occurrence, `provider.format(<the ref's fixed day>)`; for a month ref, `""`. The provider is resolved through `_soft_provider` only when an occurrence ref is present. With no provider, a day occurrence anchor is dropped.
      - A dropped anchor takes its auto-added `anchor` driver entry with it. Any `anchor`-action entry whose ref is not the kept `time_anchor`'s is dropped too, so a record never claims two anchors.
    - Nothing here reads `drivers.snapshot` or `pressure.build`, so no Save does horizon or pressure work.
17. **Saved-idea reads** (`validate_ideas` gains the annotation):
    - **Classification:**
      - Threads and commitments are classified from the canonical ledger record: `live` / `finished` / `dangling`.
      - **Canonical on read** (§17: "On read: canonicalize"). Each stored thread or commitment ref is mapped through the context's `live_canon` before anything else. The returned entry carries the **canonical** ref, with the canonical record's label and state. Entries are then de-duplicated by canonical ref, and the first stored entry wins (its `action` is kept), before `stale_reason` is derived. `scene_ideas._union` cannot do this (it imports nothing from continuity), so an idea saved on a source before a merge and re-saved on the canonical after it stores both refs; the read is what shows one. Reads never write, so the file keeps both. With `live_canon` unreadable, the mapping is the identity (unknown is never stale).
      - An event is `dangling` when deleted, or when the stored anchor `native`'s date part differs from the event's current date part ("moved"). It is `finished` when fired or `passed`, and `live` otherwise.
      - Holiday and birthday occurrences are `finished` when their embedded day is behind `now`, and `live` otherwise. A month ref is finished when `(year, index of key in provider.months(year))` from `_month_of` is behind `now`'s `(describe(now_fixed)["year"], month - 1)`, which compares month order, not just key equality. They are never `dangling`: they are computed, so there is no record to lose.
    - **Unknown is never stale.** Classification reads one `effective.Ledgers.load(cid)` and `effective.live_canon(cid, ledgers)`, both already tolerant, and never `effective.records`, which raises on a garbled plot.json.
      - An `exists` of `None` (an unreadable ledger), an unreadable status, or a provider or `now` that cannot be resolved classifies the ref as `live`.
      - Each idea's annotation runs inside one guard (`# noqa: BLE001 -- the annotation is an enhancement; the idea must still list`). On any exception it falls back to `stale_reason ""`, `anchor_date ""`, the stored refs unclassified (`state "live"`, label = ref) and `time_anchor` as stored.
      - So one bad read can never empty the saved list. `get_scene_ideas` wraps `validate_ideas` in `_tolerant`, which answers `[]` on any exception: before this slice nothing in it read plot.json, and the annotation must not make it.
    - **Labels**, for what the stored record does not carry (§17 stores no label, and this keeps it that way):
      - thread and commitment: the canonical record's title, else the ref's id;
      - event: the event row's current name, else the id;
      - holiday: the ref's name segment, `ref.split(":", 2)[2]`. A name `notices._bounded` shortened ends in `~<16 hex>`; that suffix is replaced by `…`;
      - birthday: the actor's current name through `overlay` (or `pcs` for a PC), else the actor id;
      - `friendly`: `provider.describe(fixed)["friendly"]` for a day ref or an event, and `"<month name> <year>"` for a month ref, else `""`.
    - **`stale_reason`**, first match wins:
      1. a moved anchor: `"<event> moved to <friendly>"`;
      2. a `before`/`by`/`on` anchor that is finished: `"<anchor> has passed"`;
      3. no stored ref live. A finished `after` anchor counts as live, because its passing is what the idea waits for. The text is:
         - `"Its drivers no longer exist"` when every ref is dangling;
         - else `"Every thread it was about is closed"` when every remaining ref is a thread;
         - else `"Every commitment it was about is resolved"` for commitments;
         - else `"Nothing it was about is still open"`.

         This fourth text is an addition, for a mixed set.
    - **Returned fields:**
      - `drivers` (live and finished only, each with `kind`, `label` and `state`);
      - `time_anchor` (with `kind`, `label`, `friendly` and `state`, or `None` when dangling);
      - `stale_reason`;
      - `anchor_date`: the anchor's date part when the relation is `on`, the anchor is live and dated, else `""`. This is the "server-supplied anchor date" of §17.1.
    - **Cost:** when no idea carries metadata, nothing extra is read (pinned). Reads never write.
18. **Storage:**
    - `scene_ideas.add` writes `drivers` and `time_anchor` only when non-empty, so a plain save writes today's record.
    - `_project` always returns `drivers` (a list of well-formed `{ref, action}`) and `time_anchor` (a well-formed dict or `None`); hand-edited garbage reads as empty.
    - Composed greeting entries are unchanged; the frontend reads absent fields as empty.
    - **Re-save union:** inside the same hold, new driver refs are appended by ref, and an absent `time_anchor` is filled. The file is written only when either changed.
      - `_union` first cleans the stored values with the filter `_project` uses. A non-list or malformed `drivers` counts as `[]`; a non-dict `time_anchor`, or one without a string `ref`, counts as absent. scene_ideas.json is hand-editable, and a stored `"drivers": "x"` must not turn the re-save of an idea that reads fine into a 500.
      - A cleaned value counts as a change, so the record written back is well-formed.
      - An `anchor`-action entry is appended only when it names the kept `time_anchor`'s ref (Decision 16).
19. **Frontend reads and timing:**
    - **The drivers read** happens once per chooser open and per `cid`, without `offscreen` (§16.2: once on open; the mode is picked after opening; the ref set does not depend on `offscreen`, which only trims actors the controls do not render).
    - It is read again after a 409 `stale_drivers`.
    - A failed read hides the controls, and Direction and Suggest keep working.
    - **Controls** live beside `direction` in `NewSceneChooser`. The hook reads them through a getter at dispatch, and they are never part of the auto-ask question key, so changing them starts nothing (§3.10).
    - **A seeded open** holds the auto-ask until the drivers read settles. Missing seeds are then dropped with a note, so the ranked call carries the surviving seeds and cannot 409 on a seed the reader never saw (§16.5). A seeded disclosure opens expanded, so the reader sees what was set.
      - **The hold also holds the button.** While held, the hook reports `held: true`, the picker disables Suggest with the hint "Reading story pressure…", and `suggest()` is a no-op. `suggest()` also sets `autoAsked.current = question` before it runs. Otherwise a press during the read makes an unsteered paid ranked call, and the release makes a second one for the same question.
    - **After every drivers read** (the first and each re-read), controls are pruned to the refs the read lists in `drivers` and `anchors`, and what was pruned is named in the note. A 409's `refs` are the server's canonical spellings, which can differ from what the chooser holds, so dropping only the named refs could leave a control the reader can no longer see, which would 409 on every press.
    - **A stale refusal is not an empty reply.** The hook reports `stale: true` until the next request. The picker then suppresses "No ideas came back…", and the note reads as the reason: "Some selections are no longer current and were reset — press Regenerate."
    - **A reset note ends with the next request.** The hook calls `onDispatch` beside `setStale(false)`. The chooser clears a reset note (the stale one, or the failed re-read's) and the names it held, so it is not left under the cards that request brings, and a later cycle names only its own resets. A seed's note is about the seed the open call carries, so it outlives that call and clears when the reader changes a control (deviation 20).
20. **`ScenesView` adoption is new code.** §16.5's "as it already adopts `seedPrompt`" describes `CampaignView`, which adopts. `ScenesView` only sends. The adoption copies `CampaignView`'s adopt-once-then-replace pattern.
    - A seeded open also waits for the scene list. `afterSid` is `scenes?.[0]?.id ?? null`, and opening before it lands would make the hook re-ask, a second paid ranked call, when the list arrives.
    - A malformed `chooser` state is ignored.
21. **TS mirrors** (`SORT_ORDER`, `DRIVER_KINDS`, `DRIVER_ACTIONS`, `TIME_MODES`, `ANCHOR_RELATIONS`, `MUST_CAP`) are declared once in `frontend/src/components/pressureControls.ts`. A backend test reads that file and compares each literal array with the Python tuple (§20: "a backend test pins the tuples"). The API types in `frontend/src/api/types.ts` are hand-written literal unions (`DriverKind`, `DriverAction`, `PressureState`, `TimeMode`, `AnchorRelation`, and `AnchorOption.kind`), and they are the §20 "TS unions", so the same test parses them too: two declarations of one vocabulary are pinned to the one Python tuple each, rather than to each other. Deriving the unions from the consts was rejected, because `api/types.ts` would then import from `components/`, the wrong direction for the API layer.
    - **Not `storyPressure.ts`.** That name differs from the component `StoryPressure.tsx` only in case. TypeScript (`moduleResolution: "bundler"`) and Vite try `.ts` before `.tsx`, so on the case-insensitive filesystems of Windows and macOS, `./StoryPressure` would resolve to the helper module. Linux CI would stay green while the owner's `make check-web` went red. No pair of `frontend/src` modules differs only in case today.
22. **"Show every generated suggestion while any control is active" (§16.4) follows the batch on screen.** The hook reports `controlled`: whether the reply now shown was requested with an active control. An unsent control edit therefore never re-slices cards the reader is looking at.
23. **An anchored generated card never borrows `nextDate`.** `suggestionDraft` uses `s.date || (s.time_anchor ? "" : nextDate)`, because `nextDate` is not anchor-aware and would silently contradict the card's reason. A saved idea uses `idea.anchor_date || (idea.time_anchor ? "" : nextDate || idea.date)`: the server's `anchor_date` (a live, dated `on` anchor) wins, any other anchored idea (`before`/`by`/`after`, or `on` an occurrence that has passed) gets an empty date for the same reason a card does, and only an un-anchored or dangling idea (read back with no `time_anchor`) keeps the inverted `nextDate || idea.date` precedence. The idea's stored date is not the anchored fallback, because it is a fossil of whenever the idea was saved.
24. **`DRIVER_ACTIONS` lives in `drivers.py`** beside `DRIVER_KINDS`, as `DRIVER_ACTIONS` (a flat tuple) and `ACTIONS_BY_KIND` (kind → tuple). §20 lists it with the driver tuples; Slice B never declared it.
25. **Calendar hardening inside `suggest`** (Global Constraints, "Fail soft"):
    - `_calendar_facts(cid, croot, now, roster) -> dict` returns `{notation, friendly, holidays_today, upcoming, events_today, birthdays}`. It holds today's `if now:` block (`_notation`, `today_facts`, `events.day_facts` and `events.sooner`) and the `birthdays.upcoming` call (made whether or not `now` is set, as today), moved verbatim, inside one `except Exception:  # noqa: BLE001 -- user calendar plugin code can raise anything; §26 degrades to undated`. On failure every field is blank.
    - The existing narrower catches stay inside it, so everything they absorb today is absorbed exactly as today. Only what escapes today is now caught. One catch covers the block because every read in it resolves the same primary provider.
    - `today_facts`, `day_facts` and `sooner` are not edited (§15). Only their caller is wrapped. Moving the block out also lowers `build_snapshot`'s complexity.
    - `_soft_provider(croot) -> CalendarProvider | None` wraps `calendars.primary_provider` with the same broad catch. `parse_output`, `parse_next_date`, `idea_provenance`, the saved-idea annotation and `date_normalizer` resolve through it. `calendars.primary_provider` itself is not edited: its other callers across the app are not this slice's to change.
    - The intent prompt's snapshot goes through `_calendar_facts` too, and `parse_intent` goes through `date_normalizer`, so the intent route no longer 500s under such a plugin either. It degrades the same way: blank dates, nothing fabricated.
26. **The batch anchor beats avoid** (Decision 8): `resolve_controls` drops the anchor ref from `avoid`, and the chooser disables Avoid on the anchored row.

---

## File structure

Backend paths are relative to `backend/src/grimoire/`, and tests to `backend/tests/`, unless they start with `frontend/`, `templates/`, `scripts/`, `evals/` or `docs/`.

| File | Responsibility |
|---|---|
| `store/continuity/drivers.py` | `DRIVER_ACTIONS`, `ACTIONS_BY_KIND`; `snapshot(..., *, pressure_result=None)` |
| `store/suggest.py` | snapshot (`drivers` flag, new keys, `_calendar_facts`); `_soft_provider`, `_month_of`; `Controls`, `NO_CONTROLS`, `ControlsError`, `resolve_controls`; `driver_view`; `raw_suggestions`, `normalize_date`, `claim`, `check_date`, `order_cards`; `parse_output` / `parse_next_date` with a captured snapshot; `idea_provenance`; `validate_ideas` annotation |
| `store/scene_ideas.py` | optional `drivers` / `time_anchor` storage, re-save union, projection |
| `routes/models.py` | `SceneSuggestionsRequest`; `SceneIdeaCreate.drivers` / `time_anchor` |
| `routes/scenes.py` | body + query; `_controls_or_refuse`; captured payload; idea save provenance |
| `templates/scene_suggestions/system.j2`, `user.j2`, `instruction/drivers_addendum.j2` (new), `instruction/controls_addendum.j2` (new); `templates/README.md` | prompt |
| `scripts/verify_templates.py` | snapshots, loop rows, store-level and intent checks |
| `tests/suggest_golden.py` (new), `tests/fixtures/suggest_golden.json` (new) | pre-change instruction and intent pins |
| `tests/fixtures/frozen_campaign/sweep.py`, `snapshot.json` | intent keys (Task 0); deliberate prompt regeneration (Task 3) |
| `tests/test_llm_fakes.py`, `tests/fixtures/llm/campaign_flow.json` | render input; reply carries `drivers` |
| `evals/cases.py`, `evals/graders.py`, `evals/README.md`, `evals/recordings/scene-suggestions*.json` | the `scene-suggestions` and `scene-suggestions-anchor-on` cases |
| `frontend/src/api/types.ts`, `frontend/src/api/client.ts` | driver types; `sceneSuggestions(cid, opts, signal)`; `continuityDrivers` |
| `frontend/src/components/pressureControls.ts` (new) | TS mirrors and pure control helpers |
| `frontend/src/components/StoryPressure.tsx` (new) | the disclosure |
| `frontend/src/components/useSceneSuggestions.ts`, `NewSceneChooser.tsx`, `SceneIdeaPicker.tsx`, `sceneDraft.ts` | controls, seed, cards, Stale group, dates |
| `frontend/src/routes/ScenesView.tsx`, `frontend/src/routes/CampaignHub.tsx`, `frontend/src/index.css` | handoff adoption, stale skip, styles |
| `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md` | amended to match recorded deviations (Task 13) |

Tests:

| File | Kind |
|---|---|
| `tests/test_suggest_golden.py` | new |
| `tests/test_suggest_controls.py` | new: controls, view, claims, dates, order, TS mirror pin |
| `tests/test_suggest_store.py` | appended; hand-built snapshots move to a `_snap(**over)` helper |
| `tests/test_scene_ideas_store.py`, `tests/test_scene_ideas_provenance.py` (new) | storage, union, read classification |
| `tests/test_suggestion_controls_route.py` (new) | route body, validation, payload |
| `tests/test_routes.py` | two exact scene-idea pins gain the projected keys |
| `tests/test_continuity_drivers.py` | tuples and `pressure_result` |
| `tests/test_eval_graders.py` | grader failure modes |
| frontend `client.test.ts`, `pressureControls.test.ts` (new), `useSceneSuggestions.test.ts`, `NewSceneChooser.test.tsx`, `SceneIdeaPicker.test.tsx`, `sceneDraft.test.ts`, `ScenesView.test.tsx`, `CampaignHub.test.tsx` | updated and appended |

---

### Task 0: Pin today's instruction section and intent prompt, on pre-change code

The byte-identity claims (§15.1, §15 intent) are proven only where something fails if they break. This task records them **before** any change, and every later task runs against them.

**Files:**
- Create: `tests/suggest_golden.py`, `tests/fixtures/suggest_golden.json`, `tests/test_suggest_golden.py`
- Modify: `tests/fixtures/frozen_campaign/sweep.py`, `tests/fixtures/frozen_campaign/snapshot.json` (regenerated deliberately; `home/` is never touched)

**Interfaces:**
- Produces `tests/suggest_golden.py`:
  - **`SYSTEM_SNAPS: dict[str, tuple[dict, list | None, bool, str]]`** maps `label → (snapshot, greeting_candidates, offscreen, direction)` for the labels `empty`, `dated`, `dated+greetings`, `offscreen`, `direction`, `direction+greetings`.
    - Each snapshot dict carries today's keys **and** the keys Task 2 adds, as empty values: `commitments: []`, `timeline: []`, `driver_index: []`, `anchors: []`, `links: []`, `fixed: None`, `near_days: 7`, `sooner_ref: ""`.
    - Pre-change templates ignore the extra keys, so one dict serves both sides.
    - The `dated` snapshot uses `now "2026-07-05"`, `notation {"example": "1492-Mirtul-05", "months": [...]}`, and a thread with an `id`.
  - **`intent_campaigns() -> dict[str, tuple[str, bool]]`** builds, under the caller's `GRIMOIRE_HOME`, these campaigns, and returns `label → (cid, offscreen)`:
    - `gregorian`: region `""`, one custom holiday rule `"Saltmarch Eve"` 5 days out, an event `"The coronation"` 3 days out, a thread `"Mara's map"` with a beat, a commitment `"Mara's oath"` due in 4 days, and Mara with a birthday 2 days out, all with the clock at 2026-05-10;
    - `gregorian-offscreen`: the same campaign, with `offscreen=True`;
    - `tie`: an event and a holiday on the same day;
    - `hebrew`: the Hebrew calendar at `5786-Kislev-24`, with no region;
    - `today`: a custom holiday `"Saltmarch Eve"` and an event `"The coronation"` both on the clock date, and an event `"The debt"` 10 days out, so the `Today:` and `Scheduled today:` branches that Task 2's `_calendar_facts` move carries are pinned (no other campaign lands anything on the clock date).
  - **`variants() -> dict[str, list[dict]]`** returns `system/<label>` → `suggest.build_prompt(...)`'s system message alone (a list of one dict), and `intent/<label>` → the whole `suggest.build_intent_prompt(cid, TYPED, offscreen=...)`, with `TYPED = "the morning after, back at Saltmarch"`.
  - **`main()`**, run as `python -m tests.suggest_golden` from `backend/`, sets `GRIMOIRE_HOME` to a temporary directory, and writes `fixtures/suggest_golden.json` with `sort_keys=True, indent=2` and a trailing newline.
- Produces new sweep keys: `suggest.build_intent_prompt[{cid}/offscreen={o}]` = `_or_error(lambda o=o: store.suggest.build_intent_prompt(cid, "the morning after", offscreen=o))`, for `o in (False, True)`, placed beside Slice B's `suggest.build_prompt` keys. The `o=o` default binds the loop variable, as `sweep.py`'s neighbouring keys do: a bare `lambda:` is ruff `B023`, and `sweep.py` has no ruff allowance.

- [ ] **Step 1: Write the module and the test.** `test_suggest_golden.py` (it uses `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`):
  - `test_the_instruction_section_is_pinned`: for each `system/*` key, `[suggest.build_prompt(*args)[0]] == golden[key]`;
  - `test_the_intent_prompt_is_pinned`: for each `intent/*` key, `suggest.build_intent_prompt(...) == golden[key]`;
  - `test_the_golden_file_has_every_variant`: the key set equals `variants()`'s.
- [ ] **Step 2: Generate the golden file on pre-change code.** From `backend/`, run `PYTHONPATH=src .venv/bin/python -m tests.suggest_golden`. Then read the file. Expected: the `intent/gregorian` user message contains `Upcoming: The coronation in 3 days.`; the `intent/tie` one names the holiday (`events.sooner` breaks a tie toward the holiday); `system/dated` contains `next_date`; the `intent/today` one contains `Current date: 10 May 2026 (written 2026-05-10). Today: Saltmarch Eve. Scheduled today: The coronation. Upcoming: The debt in 10 days.`
- [ ] **Step 3: Run the test.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_suggest_golden.py -q -p no:cacheprovider`. Expected: PASS.
- [ ] **Step 4: Add the sweep keys, regenerate, and prove that only keys were added.** From `backend/`, run `PYTHONPATH=src .venv/bin/python -m tests.fixtures.frozen_campaign.sweep`. Then run Slice B Task 10 Step 4's script, with its last assertion changed to `all(k.startswith("suggest.build_intent_prompt[") for k in added)`. Expected: no existing key moved.
- [ ] **Step 5: Run the frozen-campaign tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_frozen_campaign.py -q -p no:cacheprovider`. Expected: PASS, including `test_the_read_only_sweep_writes_nothing`.
- [ ] **Step 6: Lint and types.** From the repo root, `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS. `tests/suggest_golden.py` has no broad catch; the sweep's `_or_error` already carries its marker.
- [ ] **Step 7: Commit:** `test(suggest): pin the instruction section and intent prompt before slice E`

---

### Task 1: `DRIVER_ACTIONS`, and `drivers.snapshot` accepts a computed pressure result

**Files:**
- Modify: `store/continuity/drivers.py`
- Test: `tests/test_continuity_drivers.py` (append)

**Interfaces:**
- Produces:
  - `DRIVER_ACTIONS = ("advance", "close_candidate", "address", "fulfill_candidate", "break_candidate", "expire_candidate", "anchor")`.
  - `ACTIONS_BY_KIND: dict[str, tuple[str, ...]] = {"thread": ("advance", "close_candidate"), "commitment": ("address", "fulfill_candidate", "break_candidate", "expire_candidate"), "event": ("anchor",), "birthday": ("anchor",), "holiday": ("anchor",)}`. Its keys are exactly `DRIVER_KINDS`, in order.
  - `snapshot(cid, offscreen: bool = False, *, pressure_result: dict | None = None) -> dict`. When `pressure_result` is given, it is used in place of the internal `pressure.build(cid)` call (through the same `_soft` read); otherwise the behaviour is unchanged.

- [ ] **Step 1: Write the failing tests:**
  - `test_driver_actions_are_pinned`: both literals above, `set(ACTIONS_BY_KIND) == set(DRIVER_KINDS)`, and the union of `ACTIONS_BY_KIND`'s values equals `set(DRIVER_ACTIONS)`.
  - `test_snapshot_uses_a_given_pressure_result`: the fixture holds an event 3 days out and a commitment due in 4, so a fallback (empty) pressure result would change both `anchors` and the commitment's `pressure`. Take `p = pressure.build(cid)` and `expected = snapshot(cid)`. Then replace `pressure.build` with a **call recorder** that delegates. A raise would be swallowed by B's `_soft`, whose `except Exception` also catches `AssertionError`, and the test would pass vacuously. `snapshot(cid, pressure_result=p)` gives the same `drivers` and `anchors` as `expected`, and the recorder counts 0.
  - `test_snapshot_without_a_result_is_unchanged`: Slice B's existing `test_snapshot_shape_and_matching` still passes; no edit is needed.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_drivers.py -q -p no:cacheprovider`. Expected: FAIL, with an `AttributeError` for `DRIVER_ACTIONS` and a `TypeError` for the keyword.
- [ ] **Step 3: Implement.** Extend the module docstring: `DRIVER_ACTIONS` is §20's tuple, and `pressure_result` exists so that the suggestion snapshot's timeline and index share one computation (Decision 3).
- [ ] **Step 4: Run them and confirm they pass.** Use the same command. Expected: PASS, with every Slice B test still green.
- [ ] **Step 5: Lint and types.** From the repo root, `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 6: Commit:** `feat(continuity): driver action vocabulary; drivers.snapshot accepts a pressure result`

---

### Task 2: The suggestion snapshot gains the driver capture (additive)

The snapshot gains its new keys here, while `upcoming` and the templates stay as they are, so every prompt is unchanged at the end of this task. Task 3 moves the template and removes `upcoming`.

**Files:**
- Modify: `store/suggest.py` (imports; `build_snapshot`; new private `_driver_capture`, `_dormancy_of`, `_calendar_facts` (Decision 25))
- Test: `tests/test_suggest_store.py` (append)

**Interfaces:**
- Consumes: `pressure.build(cid, now=...)`, `continuity_drivers.snapshot(cid, offscreen, pressure_result=...)`, `effective.commitments(cid)`, `calendars.warn_days(croot)`.
- Produces: `build_snapshot(cid, offscreen=False, *, drivers: bool = True) -> dict`.
  - **With `drivers=False`:** exactly today's keys, `{now, friendly, notation, holidays_today, events_today, upcoming, birthdays, story_so_far, open_threads, cast, available_locations}`, and no call to `pressure` or `continuity_drivers`.
  - **With `drivers=True`:** the same keys plus the following.
    - `commitments`: `effective.commitments(cid)` rows (inside the same tolerant shape as `open_threads`), each plus `dormancy: int` and `pressure: {state, in_days, friendly}`, copied from that ref's driver (`{"state": "ok", "in_days": None, "friendly": ""}` when absent).
    - `timeline`: `pressure.build(cid, now=now)["items"]`. B's item shape; every unfired event, plus the horizon-bounded holidays and birthdays, and deadlines.
    - `driver_index`: `continuity_drivers.snapshot(cid, offscreen, pressure_result=p)["drivers"]`, each row plus `dormancy: int | None`.
    - `anchors`: that snapshot's `anchors`.
    - `links`: `[{id, a, b, relation}]`, de-duplicated by `id`, reconstructed from each driver's `links` (`out` → `a` is the driver; `in` → `b` is the driver; `both` → `a, b = sorted(...)`), keeping only links whose two endpoints are both in `driver_index`.
    - `fixed`: `p["fixed"]`.
    - `near_days`: `max(calendars.warn_days(croot), NEAR_MIN_DAYS)`, with `NEAR_MIN_DAYS = 7`; the fallback is 7.
    - `sooner_ref`: the `ref` of the first `timeline` item of kind `event` or `holiday` whose `label == upcoming["name"]` and `in_days == upcoming["in_days"]`, where `upcoming` is `_calendar_facts`' `events.sooner` pick; `""` when there is no pick or no match. It is what Decision 11 pins in the rendered timeline. It is computed before Task 3 drops `upcoming` from this mode.
    - Every thread row carries `ref = f"thread:{id}"`, and every commitment row `ref = f"commitment:{id}"`.
  - `_driver_capture(cid, offscreen, now, croot, open_threads, commitments) -> dict` (private) holds the new reads, so that `build_snapshot`'s complexity does not grow. `_dormancy_of(scene_ids)` returns the existing closure as a function.
  - `_calendar_facts(cid, croot, now, roster) -> dict` (private, Decision 25) holds today's calendar block, moved verbatim behind one broad catch. `build_snapshot` reads `notation`, `friendly`, `holidays_today`, `upcoming`, `events_today` and `birthdays` from it, in both modes.

- [ ] **Step 1: Write the failing tests.** Reuse `_campaign`, and add a local `_pressure_campaign(monkeypatch, tmp_path, *, calendar="gregorian", now="2026-05-10")`: region `""`, created like Slice B's `test_continuity_pressure._campaign`; copy the helper rather than importing it. The tests:
  - `test_snapshot_has_ids_commitments_timeline_and_index` (§28.6, first bullet):
    - setup: `"Mara's map"` (open, beat), `"Mara's oath"` (promise, due 2026-05-14, beat) and the coronation on 2026-05-13;
    - `[t["ref"] for t in snap["open_threads"]] == ["thread:mara-s-map"]`;
    - `snap["commitments"][0]` has `id`, `kind`, `due`, `latest_beat`, `dormancy`, and `pressure["state"] == "due_soon"`;
    - the timeline holds the event and the deadline;
    - the `driver_index` refs include all three.
  - `test_unchanged_keys_are_unchanged` (§15): the fixture also has a chronicle record (so `story_so_far` is non-empty), a location, a cast member, and a character with a birthday in range. With `UNCHANGED = ("now", "friendly", "notation", "holidays_today", "events_today", "birthdays", "story_so_far", "cast", "available_locations")`, `{k: a[k] for k in UNCHANGED} == {k: b[k] for k in UNCHANGED}`, where `a = build_snapshot(cid)` and `b = build_snapshot(cid, drivers=False)`. Each of the nine values is non-empty, so the comparison is not between blanks. `open_threads` rows agree on every key that `b`'s rows have.
  - `test_intent_snapshot_does_no_driver_work`: replace `pressure.build` and `continuity_drivers.snapshot` with **call recorders** that delegate. A raise would not do: the new reads sit inside broad catches, which would swallow an `AssertionError` and pass. Then `set(build_snapshot(cid, drivers=False))` equals today's key set exactly, and both recorders count 0. The `build_intent_prompt` half of this pin is Task 3's, because only Task 3 switches it to `drivers=False`.
  - `test_timeline_and_index_share_one_pressure_read`: wrap `pressure.build` in a counter; one `build_snapshot(cid)` counts exactly 1.
  - `test_timeline_contains_what_sooner_picks` (§15), parametrized over:
    - an event alone;
    - a holiday alone;
    - an event and a holiday on the same day;
    - a secondary-calendar holiday (secondary `hebrew`, from a Gregorian primary; pick a `now` whose next observance within 30 days is the secondary's).

    In each case `pick = build_snapshot(cid, drivers=False)["upcoming"]` is not `None`, and some timeline item has `label == pick["name"]` and `in_days == pick["in_days"]`. That item's `ref` equals `build_snapshot(cid)["sooner_ref"]`.
  - `test_links_among_drivers`: `thread:mara-s-map pays_off commitment:mara-s-oath` gives `snap["links"] == [{"id": <lid>, "a": "thread:mara-s-map", "b": "commitment:mara-s-oath", "relation": "pays_off"}]`. A link to a closed thread (not a driver) is absent.
  - `test_commitment_dormancy_counts_scenes`: a commitment last moved two scenes ago has `dormancy == 2`, and a temporal driver has `dormancy is None`.
  - `test_snapshot_survives_a_raising_plugin` (Review Focus 1, Decision 25): create a thread, a commitment with a parseable due and an event while still Gregorian, set the clock, then switch the primary to a plugin whose `__init__` raises `RuntimeError` (copy Slice B's broken-plugin source). Confirm on the pre-change code first that `build_snapshot(cid)` raises `RuntimeError` there; the test is the proof that Decision 25 is needed. Then:
    - `build_snapshot(cid)` and `build_snapshot(cid, drivers=False)` both return;
    - `notation == {"example": "", "months": []}`, and `friendly`, `holidays_today`, `events_today` and `birthdays` are blank;
    - `timeline` holds only undated items;
    - `anchors == []`;
    - `driver_index` holds the thread and the commitment.
  - `test_offscreen_keeps_a_pc_birthday_driver_but_never_its_cast_token` (Decision 15): a PC Winifred with a birthday 2 days out gives a `birthday:pcs:...` driver in `build_snapshot(cid, offscreen=True)`, and no cast row with `token == "pcs:<id>"`.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_suggest_store.py -q -p no:cacheprovider`. Expected: FAIL, with `KeyError` / `TypeError` (no `drivers` keyword, no new keys).
- [ ] **Step 3: Implement** in `store/suggest.py`. Update the module docstring: the snapshot consumes the continuity projections, and `drivers=False` is the intent prompt's legacy shape. Move the calendar block into `_calendar_facts` first, with no other change, and run `tests/test_suggest_golden.py` before going on: the move must be byte-neutral on a healthy calendar.
- [ ] **Step 4: Run them and confirm they pass**, along with Task 0's pins. `PYTHONPATH=src .venv/bin/python -m pytest tests/test_suggest_store.py tests/test_suggest_golden.py tests/test_frozen_campaign.py -q -p no:cacheprovider`. Expected: PASS, with `snapshot.json` untouched (the template does not read the new keys yet).
- [ ] **Step 5: Run the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_import_guard.py tests/test_lock_domain_guard.py tests/test_store_api_baseline.py -q -p no:cacheprovider`. Expected: PASS (no new store module, and `suggest` writes nothing).
- [ ] **Step 6: Lint and types.** From the repo root, `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS, with no second `C901` in `suggest.py`.
- [ ] **Step 7: Commit:** `feat(suggest): snapshot captures commitments, timeline, driver index and links`

---

### Task 3: The prompt — driver sections, addenda, `driver_view`, and the template move

**Files:**
- Modify:
  - `store/suggest.py`: `Controls`, `NO_CONTROLS`, `DRIVER_PROMPT_CAP`, `driver_view`, `build_prompt`, `build_intent_prompt`; `upcoming` removed from the `drivers=True` snapshot;
  - `templates/scene_suggestions/system.j2` and `user.j2`;
  - `templates/README.md` (the `scene_suggestions/` section: new vars, both addenda, the `drivers` flag, the leading-space rule; and `scene_intent/`: it renders with `drivers=False`);
  - `scripts/verify_templates.py`, `tests/test_llm_fakes.py`, `tests/fixtures/llm/campaign_flow.json`;
  - `tests/fixtures/frozen_campaign/snapshot.json` (deliberate regeneration);
  - `tests/test_suggest_store.py`: hand-built snapshots move to `_snap(**over)`.
- Create: `templates/scene_suggestions/instruction/drivers_addendum.j2`, `templates/scene_suggestions/instruction/controls_addendum.j2`
- Test: `tests/test_suggest_controls.py` (new), `tests/test_suggest_golden.py`, `tests/test_suggest_store.py`

**Interfaces:**
- Produces:
  - `DRIVER_PROMPT_CAP = 40`, `MUST_CAP = 3`, `TIME_MODES = ("auto", "near", "move", "anchor")`, `ANCHOR_RELATIONS = ("before", "on", "after", "by")`, `MUST_KINDS = ("thread", "commitment")`, `TEMPORAL_KINDS = ("event", "birthday", "holiday")`.
  - `@dataclass(frozen=True) class Controls`: `focus: tuple[str, ...] = ()`, `avoid`, `must`, `time_mode: str = "auto"`, `anchor: str = ""`, `relation: str = ""`. Properties `active` and `pinned` (Decision 8). `NO_CONTROLS = Controls()`.
  - `driver_view(snapshot: dict, controls: Controls) -> dict`. It reads `snapshot.get(...)` with empty defaults, so a snapshot without driver keys yields an empty view. It returns:
    - `index`: rows `{ref, label, kind, state, when}`, selected and ordered per Decision 9;
    - `more: int`;
    - `timeline`: the snapshot's timeline items, selected per Decision 11 and kept in snapshot order, each with its `when` phrase added; `timeline_more: int`;
    - `links: [{a, relation, b}]`;
    - `actions: [{kind, actions: [..]}]` in `DRIVER_KINDS` order, from `ACTIONS_BY_KIND`;
    - `high_pressure: [..]`, the `HIGH_PRESSURE` states in `SORT_ORDER` order;
    - `focus`, `avoid`, `must`: `[{ref, label}]`;
    - `time_mode`, `near_days`;
    - `anchor`: `{ref, label, friendly, relation}` or `None`;
    - `active: bool`.

    `when` is the Decision 10 phrase.
  - `build_prompt(snapshot, greeting_candidates=None, offscreen=False, direction="", controls: Controls | None = None) -> list[dict]`. The vars are `s`, `offscreen`, `greeting_candidates`, `direction`, `drivers=True`, and `view=driver_view(snapshot, controls or NO_CONTROLS)`.
  - `build_intent_prompt(cid, typed, offscreen=False)`: `s=build_snapshot(cid, offscreen=offscreen, drivers=False)`, plus `drivers=False` and `view=None`.
  - `build_snapshot(..., drivers=True)` no longer has `upcoming`. `drivers=False` keeps it.
  - **`system.j2`:** today's four lines unchanged, then:
    - `{%- if drivers and view.index -%}` include `drivers_addendum.j2`;
    - then `{%- if drivers and view.active -%}` include `controls_addendum.j2`.
  - **`user.j2`:**
    - the `Current date` line keeps `Today`/`Scheduled today`, and appends `Upcoming:` only `if not drivers and s.upcoming`. Jinja's `and` short-circuits, so `s.upcoming` is never read under `drivers`;
    - under `drivers`, the threads block uses Decision 10's thread line; otherwise it uses today's;
    - under `drivers`, after the threads block and in this order: `Open commitments (use these refs):`, `Timeline (computed from the calendar; never recompute these dates):` (rows from `view.timeline`, with its "and N more dated items" line), `Story drivers (cite these refs in "drivers"):` with the "and N more" line, and `Reviewed links between drivers:`. Each block renders only when non-empty;
    - the header comment documents `drivers`, `view` and the new `s` keys.
- Consumes: Task 2's snapshot; `pressure.PRESSURE_STATES`, `SORT_ORDER`, `HIGH_PRESSURE`; `continuity_drivers.DRIVER_KINDS`, `ACTIONS_BY_KIND`.

- [ ] **Step 1: Write the failing tests.**
  - In `test_suggest_controls.py`:
    - `test_controls_tuples_are_pinned`: `DRIVER_PROMPT_CAP == 40`, `MUST_CAP == 3`, and the `TIME_MODES`, `ANCHOR_RELATIONS`, `MUST_KINDS` and `TEMPORAL_KINDS` literals.
    - `test_no_controls_is_inactive`: `NO_CONTROLS.active is False`; `Controls(time_mode="near").active is True`.
    - `test_view_caps_at_forty_and_counts_the_rest`: 45 thread drivers give `len(view["index"]) == 40` and `view["more"] == 5`.
    - `test_pinned_refs_are_always_rendered`: 43 thread drivers, 1 commitment and 1 event (45 drivers), with the commitment and the event both sorting last. The 43rd thread is under focus, the commitment is must, and the event is the anchor; all three are in `index`, with `len(index) == 40` and `more == 5`. With 45 threads and 41 of them pinned as focus, all 41 render and `more == 4`.
    - `test_view_orders_by_pressure_then_dormancy`: an overdue commitment, a due-soon commitment, a passed event, a stale thread with dormancy 5, an ok thread with dormancy 9 and an ok thread with dormancy 1 render in that order. The passed event sorts after the due-soon commitment (Decision 9: `SORT_ORDER`, not `PRESSURE_STATES`).
    - `test_view_links_only_between_rendered_drivers`.
    - `test_timeline_is_capped_but_keeps_sooner_and_anchors` (Decision 11, AC8): a hand-built timeline of 41 overdue `deadline` items, 3 `upcoming` events 20–22 days out, the `sooner_ref` holiday 2 days out and an event 30 days out that is the batch anchor (46 items, in B's item order). With `Controls(time_mode="anchor", anchor=<that event>, relation="before")`: `len(view["timeline"]) == 40`, `view["timeline_more"] == 6`, the holiday and the anchor event are both rendered, the rendered rows keep the snapshot's relative order, and the cut items are the lowest-ranked (the three 20–22-day events, then the last-ranked overdue items by `in_days`, then ref). A focus on one of the cut overdue deadlines' commitment refs (its `subject`) renders that item. A 10-item timeline renders whole, in order, with `timeline_more == 0`.
    - `test_view_actions_come_from_the_tuple`: `[a["kind"] for a in view["actions"]] == list(DRIVER_KINDS)`.
  - In `test_suggest_golden.py`:
    - `test_the_instruction_section_survives_a_driver_index`: for each `system/*` variant with a two-row `driver_index`, `build_prompt(...)[0]["content"] == golden + prompts.render("scene_suggestions/instruction/drivers_addendum.j2", view=suggest.driver_view(snap, NO_CONTROLS))` (§28.6: "with no controls, the instruction section is byte-identical").
    - `test_controls_append_after_the_drivers_addendum`: with `Controls(focus=("thread:a",))`, the system message starts with the golden, followed by the drivers addendum, and ends with the controls addendum render.
  - In `test_suggest_store.py`:
    - add `_snap(**over)`: today's hand-built dict plus `commitments`, `timeline`, `driver_index`, `anchors`, `links` (empty), `fixed: None`, `near_days: 7`, `sooner_ref: ""`, and `id`s on threads. Rewrite the seven hand-built call sites to use it; their assertions are unchanged.
    - `test_prompt_renders_refs_commitments_timeline_and_index`: the store campaign from Task 2 gives a user message containing:
      - `"- thread:mara-s-map = Mara's map ("`;
      - `"commitment:mara-s-oath: Mara's oath (promise, open), due 2026-05-14"`;
      - `"Timeline (computed from the calendar"` and `"event:the-coronation = The coronation"`;
      - `"Story drivers (cite these refs"`.

      It contains no `"Upcoming:"`.
    - `test_intent_prompt_still_has_its_upcoming_line`: `build_intent_prompt` on the same campaign contains `"Upcoming: The coronation in 3 days."` (Task 0's golden already pins the bytes).
    - `test_intent_prompt_does_no_driver_work` (moved here from Task 2): with `pressure.build` and `continuity_drivers.snapshot` replaced by delegating call recorders, `build_intent_prompt(cid, "x")` succeeds and both count 0.
    - `test_snapshot_drops_only_upcoming`: `set(build_snapshot(cid)) == set(build_snapshot(cid, drivers=False)) - {"upcoming"} | {the eight new keys}` (`commitments`, `timeline`, `driver_index`, `anchors`, `links`, `fixed`, `near_days`, `sooner_ref`).
    - `test_drivers_addendum_names_every_action_and_high_pressure`: the system message contains each `f'"{a}"'` for `a in DRIVER_ACTIONS`, and every Global Constraints drivers-addendum needle. That includes the two §15.1 directives "at least one suggestion should address high pressure" and "near-future temporal anchors are worth using", so every §15.1 bullet is pinned. These needles live in this unit test only (Global Constraints, "Who pins what").
    - `test_controls_addendum_needles`: a near, a move and an anchor control each render their needle, and focus, avoid and must render their labels and refs.
    - `test_an_empty_campaign_keeps_todays_system_message`: `_campaign` with nothing in it gives `build_prompt(build_snapshot(cid))[0]["content"] == build_prompt(_snap())[0]["content"]`, and the drivers addendum is absent.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_suggest_controls.py tests/test_suggest_golden.py tests/test_suggest_store.py -q -p no:cacheprovider`. Expected: FAIL, with `AttributeError` (`Controls`, `driver_view`) and missing text.
- [ ] **Step 3: Implement** the constants, `Controls`, `driver_view`, both addenda, the template branches, `build_prompt` and `build_intent_prompt`, and remove `upcoming` from the `drivers=True` path. Each addendum begins with a single leading space, and renders its enums and needles from `view` (Global Constraints).
- [ ] **Step 4: Extend `scripts/verify_templates.py`.**
  - `EMPTY_SNAP` and `FULL_SNAP` drop `upcoming` and gain the new keys.
  - New `FULL_SNAP` content uses only placeholder names:
    - threads with `id`s;
    - a commitment `"Mara's oath"`;
    - a timeline with an event `"The coronation"`, a holiday `"Saltmarch Eve"`, a month-only birthday `"Winifred"` and a `linked_deadline`;
    - a three-row `driver_index`, matching `anchors`, and one link.
  - A `CAPPED_SNAP` has 45 thread drivers labelled `"Saltmarch thread {n}"`, and a 45-item timeline of events labelled `"Saltmarch fair {n}"` with `sooner_ref` naming the last one in rank order, so the `capped+focus` row renders both "and N more" lines and the pinned sooner item.
  - Loop rows gain a `controls` element, and the loop gains `full+focus/avoid/must`, `full+near`, `full+move`, `full+anchor` and `capped+focus`. Both renders pass `drivers=True, view=suggest.driver_view(snap, controls)`.
  - In the store-level block, the suggestions renders pass `drivers=True, view=suggest.driver_view(snap, suggest.NO_CONTROLS)`. The two intent checks use `isnap = suggest.build_snapshot(cid, drivers=False)` (and `offscreen=True`), and pass `drivers=False, view=None`.
- [ ] **Step 5: Update the cassette harness.**
  - `test_llm_fakes._rendered_prompts` renders `scene_suggestions/system.j2` with `drivers=True, view=suggest.driver_view({"driver_index": [<one thread row>], "near_days": 7}, suggest.NO_CONTROLS)`, so the addendum is rendered and covered.
  - `campaign_flow.json`'s suggestion reply gains `"drivers": [{"ref": "thread:the-debt", "action": "advance"}]` on its one suggestion. The matcher phrase is unchanged.
- [ ] **Step 6: Run the harnesses.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_suggest_controls.py tests/test_suggest_golden.py tests/test_suggest_store.py tests/test_llm_fakes.py tests/test_routes.py tests/test_draft_runs.py tests/test_llm_error_status.py tests/test_routing_routes.py -q -p no:cacheprovider`. Expected: PASS. That includes `test_routes.py`'s offscreen `pcs:{pid}` assertion, and the existing query-parameter suggestion tests, whose route is unchanged so far. Then, from the repo root, `make check-templates PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 7: Regenerate the frozen snapshot deliberately, and prove what moved.** From `backend/`, run `PYTHONPATH=src .venv/bin/python -m tests.fixtures.frozen_campaign.sweep`, then Slice B Task 10 Step 4's script, with the assertions replaced by:
  - `set(moved) == {"suggest.build_prompt[the-drowned-ledger/offscreen=False]", "suggest.build_prompt[the-drowned-ledger/offscreen=True]"}`;
  - `added == []`.

  Read both moved values:
  - each system message equals its old value plus the drivers addendum;
  - each user message differs only by the thread line's ref, and the new commitments, timeline and index blocks (`salt-owed` appears);
  - the `suggest.build_intent_prompt[...]` keys did not move.
- [ ] **Step 8: Evals and guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_evals.py tests/test_eval_graders.py tests/test_import_guard.py tests/test_frozen_campaign.py -q -p no:cacheprovider`. Expected: PASS.
- [ ] **Step 9: Lint and types.** From the repo root, `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS. `verify_templates.py`'s `E402: 6` must not grow (it gains no import). If `suggest.py`'s recorded `C901` resolved, run `make baseline` and commit the smaller file.
- [ ] **Step 10: Commit:** `feat(suggest): prompt shows refs, commitments, timeline and a capped driver index`

---

### Task 4: Parsing — claims, anchors, date derivation, constraint misses and card order

**Files:**
- Modify: `store/suggest.py`
- Test: `tests/test_suggest_controls.py`, `tests/test_suggest_store.py`

**Interfaces:**
- Produces (all pure unless marked):
  - `raw_suggestions(text: str) -> list | None`. `None` when nothing decodes. Otherwise the suggestion list (a dict's `"suggestions"`, or a bare array), or `[]` when it is not a list. `parse_output` uses it, and the eval grader too.
  - `normalize_date(provider, near: str, text) -> str`. It is `calendars.resolve(provider, text, near)`, or `""` on `CalendarError`, a non-string, or `provider is None`. `date_normalizer(..., tolerant=True)` delegates to it, and `date_normalizer` resolves its provider through `_soft_provider` (Decision 25); for any calendar that resolves today, its behaviour is unchanged.
  - `_soft_provider(croot)` (store-reading, Decision 25): `calendars.primary_provider(croot)`, or `None` on any exception (`# noqa: BLE001 -- user calendar plugin code`).
  - `_month_of(ref: str) -> tuple[int, str] | None` (Decision 12): the `(year, month_key)` encoded in a `birthday:...:month:<year>-<key>` ref, else `None`.
  - `claim(entry: dict, snapshot: dict, controls: Controls) -> dict` returns `{"drivers": [{ref, action}], "time_anchor": {ref, relation} | None, "unmet_must": [ref], "avoided": [ref]}`, per Decisions 5, 6 and 14. Canonicalization uses `{a["ref"]: row["ref"] for row in snapshot rows with aliases for a in row["aliases"]}`.
  - `check_date(provider, snapshot: dict, controls: Controls, time_anchor: dict | None, date: str) -> tuple[str, bool]` returns `(date, rejected)`, applying Global Constraints §15.3, `near` and `move`, and Decision 12.
    - `date` is an **already normalized** native (`normalize_date`'s output), never raw model text.
    - An `on` anchor returns `(provider.format(anchor["fixed"]), False)`, with `anchor` the option in `snapshot["anchors"]`.
    - A month-only `on` keeps `date` iff `provider.describe(fixed)`'s year and `months(year)[month-1]["key"]` (case-folded) equal `_month_of(anchor ref)`, and `now ≤ d` when there is a `now`. Nothing is read from `timeline`, whose items carry no month key.
    - `provider is None` returns `("", False)`.
  - `order_cards(rows: list[dict], snapshot: dict, controls: Controls) -> list[dict]`, per Decision 13. It reads driver states from `snapshot["driver_index"]`.
  - `parse_output(text, cid, offscreen=False, *, snapshot: dict | None = None, controls: Controls | None = None) -> list[dict]` (store-reading: `valid_ids`, the provider). It resolves the provider once through `_soft_provider`, not `date_normalizer`, and normalizes each date with `normalize_date(provider, now, raw)` before `check_date`. `now` is the snapshot's, or `clock.now(cid)` without a snapshot, which is today's anchor. It labels from the snapshot (`snapshot=None` means an empty index and `NO_CONTROLS`). Each row is:
    - `title`, `premise`, `cast`, `location`;
    - `date`, `date_friendly` (`describe(fixed)["friendly"]`, or `""`), `in_days` (`int | None`), `date_rejected`;
    - `drivers`: `[{ref, kind, action, label}]`;
    - `time_anchor`: `{ref, kind, relation, label, friendly, in_days}` or `None`;
    - `unmet_must` and `avoided`: `[{ref, label}]`.

    Rows come in `order_cards` order. A private `_card(...)` builds one row.
  - `parse_next_date(text, cid, *, snapshot=None, controls=None) -> str`: the `near`/`move` check applies, and an anchor does not (Decision 12). It resolves its provider through `_soft_provider` too.

- [ ] **Step 1: Write the failing tests.** Most are pure, in `test_suggest_controls.py`. They use a hand-built snapshot and the real Gregorian provider (`calendars.get_provider({"provider": "gregorian", ...})`), with `now = "2026-05-10"`.
  - `test_unknown_driver_is_dropped` (§28.6): `thread:ghost`, `commitment:x` with action `advance`, and a non-dict entry are all dropped. Valid pairs are kept, de-duplicated by ref, and the first action wins.
  - `test_anchor_is_validated_and_auto_added` (§28.6): a model `time_anchor` on an anchor ref adds `{ref, "anchor"}` to `drivers`. One naming a passed or undated event (in `driver_index` but not in `anchors`) yields `time_anchor None` and no auto-added entry.
  - `test_batch_anchor_forces_every_suggestion` covers three cases:
    - the request relation wins;
    - with no request relation, a valid model relation is kept;
    - an invalid model relation becomes `on`.
  - `test_focus_avoid_must_report_misses` (§28.6): with must `commitment:mara-s-oath` and avoid `thread:mara-s-map`, a suggestion claiming only the thread gives `unmet_must == ["commitment:mara-s-oath"]` and `avoided == ["thread:mara-s-map"]`, and the thread is not in `drivers`. The suggestion is still returned by `parse_output`.
  - `test_a_canonical_alias_prevents_duplicate_drivers` (§28.6, Review Focus 4): a snapshot thread row `{"ref": "thread:winifred-s-chart", "aliases": [{"ref": "thread:mara-s-map", ...}]}`, with a reply claiming both refs, gives `drivers == [{"ref": "thread:winifred-s-chart", "action": "advance"}]`.
  - `test_on_derives_the_date` (§28.6): an `on` anchor on 2026-05-13 overrides any model date, giving `("2026-05-13", False)`.
  - `test_invalid_relation_dates_are_blanked_and_kept` (§28.6), parametrized:
    - `before` with `d = D`;
    - `by` with `d = D + 1`;
    - `after` with `d = D`;
    - `before` with `d < now`.

    Each gives `("", True)`. The corresponding valid dates give `(d, False)`.
  - `test_anchor_day_boundaries_ignore_the_time_of_day` (Review Focus 2): with an anchor native `"2026-05-12T20:00"`:
    - `on` gives `("2026-05-12", False)`;
    - `before` rejects `2026-05-12` and keeps `2026-05-11`;
    - `by` keeps `2026-05-12`;
    - `after` rejects `2026-05-12`, keeps `2026-05-13` and `F + 400`, and rejects `F + 401`.
  - `test_near_and_move_blank_out_of_range_dates` (§28.6): with `near_days 7`, near keeps now and now+7 and rejects now+8 and now−1; move rejects now and keeps now+1.
  - `test_an_absent_date_is_not_rejected`: `("", False)`.
  - `test_no_now_skips_the_lower_bound`: a snapshot with `now ""` keeps a `before` date earlier than any `now` would allow, and still rejects `d ≥ D`. Near returns `(d, False)`.
  - `test_month_only_birthday_on_keeps_only_its_month` (Review Focus 3): an anchor `birthday:characters:mara:month:2026-06` keeps `2026-06-20`, and rejects `2026-07-01` as `("", True)`. The hand-built snapshot's timeline item for it has **no** `year`/`month_key` keys, matching Slice B's item shape.
  - `test_month_of_parses_refs`: `"birthday:characters:mara:month:2026-06"` → `(2026, "06")`; a key holding `-` keeps it whole; a negative year parses; a day ref and garbage give `None`.
  - `test_on_date_is_canonical`: an event anchor whose stored native is the hand-written `"2026-5-13"` (which `provider.parse` accepts) derives `("2026-05-13", False)`.
  - `test_high_pressure_card_first` (§28.6): with rows `[ok-thread card, overdue-commitment card, focus card]`, the overdue card is first and model order is kept otherwise.
  - `test_distinct_focus_cards_come_next`: with focus `a` and `b` and rows `[a, a, b]`, the order is `[a, b, a]`.
  - `test_raw_suggestions_shapes`: prose gives `None`; `'{"suggestions": 3}'` gives `[]`; a bare array is returned as is.
  - In `test_suggest_store.py`:
    - `test_parse_output_resolves_labels_and_dates` (§28.6, "card provenance uses resolved labels"): the Task 2 store campaign with a snapshot and `Controls(time_mode="anchor", anchor="event:the-coronation", relation="before")` gives row 0 with:
      - `time_anchor == {"ref": "event:the-coronation", "kind": "event", "relation": "before", "label": "The coronation", "friendly": "13 May 2026", "in_days": 3}`;
      - drivers labels from `driver_index` (`"Mara's map"`);
      - `date_friendly` and `in_days` set.
    - `test_plugin_calendar_dates_derive_in_native_notation` (Review Focus 3): on `_wide_campaign`, an event created at `"5-M03-12"` and anchored `on` gives `date == "5-M03-12"`. Anchored `before`, the model date `"9 M03 5"` gives `"5-M03-09"`, not rejected.
    - `test_parse_output_without_a_snapshot_keeps_todays_fields`: `parse_output(text, cid)` rows have `drivers == []`, `time_anchor is None` and `date_rejected is False`, and the existing id and date tests in the file stay green unedited.
    - `test_next_date_is_checked_under_near_only`: a `next_date` 20 days out is blanked under near, and kept under anchor and under auto.
    - `test_month_only_on_against_a_real_campaign` (Review Focus 3): Winifred born `"--06"`, the clock at 2026-05-10, and a snapshot from `build_snapshot(cid)`. With `Controls(time_mode="anchor", anchor=<her month ref from snapshot["anchors"]>, relation="on")`, `parse_output` keeps `"2026-06-20"` and blanks `"2026-07-01"` with `date_rejected`.
    - `test_parse_survives_a_raising_plugin` (Review Focus 1, Decision 25): Task 2's raising-plugin campaign, with a reply whose suggestions carry dates and a `next_date`. Confirm on the pre-change parser that it raises `RuntimeError`. After: `parse_output` returns every suggestion with `date == ""` and `date_rejected is False`, and `parse_next_date` returns `""`. `parse_intent` on the same campaign returns with a blank date, and `ref_validator(cid)` builds and blanks a stored date.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_suggest_controls.py tests/test_suggest_store.py -q -p no:cacheprovider`. Expected: FAIL, with `AttributeError` for `claim`, `check_date`, `order_cards` and `raw_suggestions`.
- [ ] **Step 3: Implement.** Keep each helper at complexity ≤ 10: `claim` splits into `_valid_pairs`, `_anchor_of` and `_misses`; `check_date` dispatches on relation through a small table.
- [ ] **Step 4: Run them and confirm they pass.** Use the same command. Expected: PASS, with every existing `parse_output` and `parse_intent` test green.
- [ ] **Step 5: Lint and types.** From the repo root, `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 6: Commit:** `feat(suggest): validate claimed drivers and anchors; derive and check dates; order cards`

---

### Task 5: The request body, `resolve_controls`, and the captured payload

**Files:**
- Modify:
  - `store/suggest.py`: `ControlsError`, `resolve_controls`;
  - `routes/models.py`: `SceneSuggestionsRequest`;
  - `routes/scenes.py`: `post_scene_suggestions`, `_controls_or_refuse`, `_suggestions_payload`; its docstring gains the validation order and the query-parameter deprecation note.
- Test: `tests/test_suggestion_controls_route.py` (new), `tests/test_suggest_controls.py`

**Interfaces:**
- Produces:
  - `class ControlsError(ValueError)`: `__init__(self, status: int, kind: str, detail: str, *, reason: str = "", refs: list[str] | None = None)`.
  - `resolve_controls(cid, snapshot, *, focus_refs=(), avoid_refs=(), must_refs=(), time_mode="auto", time_anchor_ref="", time_anchor_relation="") -> Controls`. The steps, in order:
    1. strip each ref, drop blanks, and de-duplicate in order;
    2. canonicalize through `effective.live_canon(cid)` (identity on any exception, `noqa: BLE001` with a reason);
    3. `must ∩ avoid` → `ControlsError(400, "bad_controls", ..., reason="must_avoid")`;
    4. more than `MUST_CAP` → `must_cap`; a must kind not in `MUST_KINDS` → `must_kind`;
    5. the anchor checks:
       - anchor mode with a blank ref → `anchor_missing`;
       - a ref with another mode → `anchor_without_mode`;
       - a malformed ref or a non-temporal prefix → `anchor_kind`;
       - a month-precision anchor with a relation other than `""` or `"on"` → `anchor_relation`, decided from the anchor option when present;
    6. collect every ref not in the `driver_index` refs, plus an anchor not in the `anchors` refs → `ControlsError(409, "stale_drivers", ..., refs=[...])` (in request order);
    7. apply precedence (`avoid -= {anchor}`, then `focus -= must ∪ avoid`; Decisions 8 and 26) and return the `Controls`.
  - `class SceneSuggestionsRequest(BaseModel)`:
    - `after: str | None = None`, `offscreen: bool = False`, `direction: str = ""`, `rank: bool = True`;
    - `focus_refs: list[str] = []`, `avoid_refs: list[str] = []`, `must_refs: list[str] = []`;
    - `time_mode: Literal["auto", "near", "move", "anchor"] = "auto"`;
    - `time_anchor_ref: str = ""`, `time_anchor_relation: Literal["", "before", "on", "after", "by"] = ""`.
  - `post_scene_suggestions(cid, request, body: SceneSuggestionsRequest | None = None, after=None, offscreen=False, direction="", rank=True, client=..., x_grimoire_attempt=...)`:
    - `req = body if body is not None else SceneSuggestionsRequest(after=after, offscreen=offscreen, direction=direction, rank=rank)`;
    - then the Decision 7 order, with `snapshot = store.suggest.build_snapshot(cid, offscreen=req.offscreen)` and `controls = _controls_or_refuse(cid, snapshot, req)`;
    - `messages = build_prompt(snapshot, candidates, offscreen=..., direction=..., controls=controls)`;
    - `work` captures `snapshot` and `controls`.
  - `_controls_or_refuse(cid, snapshot, req) -> Controls`: maps a `ControlsError` to `HTTPException(e.status, detail={"kind": e.kind, "detail": e.detail, **({"reason": e.reason} if e.reason else {}), **({"refs": e.refs} if e.refs is not None else {})}) from e`.
  - `_suggestions_payload(cid, text, candidates, offscreen, snapshot, controls) -> dict`. Each suggestion is `parse_output(..., snapshot=snapshot, controls=controls)`'s row, with `cast` resolved by `_resolve_cast` and `location` as `{id, name} | None`. `next_date` is `parse_next_date(text, cid, snapshot=snapshot, controls=controls)`. `greeting_picks` is unchanged.

- [ ] **Step 1: Write the failing tests** in `test_suggestion_controls_route.py`. They use `tests/draft_runs.post` (a non-202 comes back untouched), `llm_fakes.FakeOpenRouterComplete`, and a campaign built over `/api` with an openrouter key. Region is `""`, the clock is at 2026-05-10, and the campaign has `"Mara's map"`, `"Mara's oath"` (due 05-14), `"Find the ledger"` and the coronation (05-13):
  - `test_no_body_is_todays_request`: a POST with no body gives 200, and `fake.messages` equals `suggest.build_prompt(suggest.build_snapshot(cid), [], ...)` with no controls addendum.
  - `test_query_parameters_still_work`: `?offscreen=true&rank=false&direction=x` with no body gives the offscreen instruction, no rank addendum, and the direction block.
  - `test_a_body_wins_over_the_query`: `params={"offscreen": "true"}` with `json={"offscreen": False}` gives the standard instruction.
  - `test_controls_reach_the_prompt` (§16.3): `json={"focus_refs": ["thread:mara-s-map"], "time_mode": "anchor", "time_anchor_ref": "event:the-coronation", "time_anchor_relation": "before"}` gives a system message containing `Focus drivers:` and `Anchor every suggestion to`.
  - `test_must_and_avoid_overlap_is_400` (§28.6): body `reason == "must_avoid"`, `kind == "bad_controls"`, and the fake is never called.
  - `test_must_cap_and_kind_are_400` (§28.6): four must refs give `must_cap`; one event as must gives `must_kind`.
  - `test_anchor_rules_are_400`:
    - anchor mode with no ref → `anchor_missing`;
    - a ref with `near` → `anchor_without_mode`;
    - `thread:mara-s-map` as the anchor → `anchor_kind`.
  - `test_month_only_anchor_takes_only_on` (Review Focus 3): Winifred born `"--06"` and an anchor on her month ref with `before` gives 400 `anchor_relation`; with `on` it is accepted.
  - `test_stale_refs_are_409` (§28.6): `focus_refs ["thread:ghost"]` plus an anchor `event:gone` gives `409 {"kind": "stale_drivers", "refs": ["thread:ghost", "event:gone"]}`, and no run is reserved (`GET /campaigns/{cid}/runs` is empty).
  - `test_a_passed_event_anchor_is_stale`: an event dated behind the clock gives 409 with its ref.
  - `test_an_alias_source_focus_is_canonicalized` (Review Focus 4): merge `"Mara's map"` into `"Find the ledger"`, then `focus_refs ["thread:mara-s-map"]` gives 200, and the system message names `thread:find-the-ledger`. Must on the source with avoid on the canonical gives 400 `must_avoid`.
  - `test_a_dangling_alias_source_is_its_own_driver` (Review Focus 4): `continuity_doc.put_alias` from `thread:mara-s-map` to a missing `thread:gone`, then a focus on the source, gives 200.
  - `test_precedence_must_over_focus`: the same ref in must and focus gives a prompt listing it only under `Must-include drivers:`.
  - `test_precedence_avoid_over_focus` (§28.6): the same thread in `focus_refs` and `avoid_refs` gives 202, not a 400, and the prompt lists it under `Avoid drivers:` and not under `Focus drivers:`.
  - `test_the_anchor_beats_avoid` (Decision 26): `avoid_refs ["event:the-coronation"]` with the coronation as the anchor gives 202. The prompt has no `Avoid drivers:` line, and the landed suggestions carry `avoided == []`.
  - `test_missing_connection_is_told_first`: with no key, a body with conflicting controls still gives today's 409 `missing_key`.
  - `test_payload_carries_resolved_provenance` (§15.2): the fake reply claims `thread:mara-s-map` advance, `commitment:ghost` address, and a `time_anchor` on the coronation `before` with date `"2026-05-12"`. The route answers:
    - `drivers == [{"ref": "thread:mara-s-map", "kind": "thread", "action": "advance", "label": "Mara's map"}, {"ref": "event:the-coronation", "kind": "event", "action": "anchor", "label": "The coronation"}]`;
    - `time_anchor["label"] == "The coronation"`;
    - `date == "2026-05-12"`, `date_rejected is False`, `unmet_must == []`, `avoided == []`.
  - `test_payload_uses_the_captured_index`: **before** the POST, replace `continuity_drivers.snapshot` and `pressure.build` with delegating call recorders. Post through `draft_runs.post` with a focus control, so the run settles. The run lands with labels, and each recorder counts exactly 1: the route's one `build_snapshot`. A payload that re-read the drivers would count 2. This is deterministic. A patch applied "after the 202" would race the run: `draft_runs.post` already waits for the run, and `FakeOpenRouterComplete` answers at once, so the payload would be built before the patch existed.
  - `test_controls_survive_a_raising_plugin` (Review Focus 1, Decision 25): Task 2's raising-plugin setup gives these results:
    - focus on the thread → 202 and a landed run;
    - anchor mode on the event → 409 `stale_drivers`, with no run reserved;
    - the landed suggestions keep their titles, with `date == ""` and `date_rejected is False`, and `next_date == ""`.
  - `test_routing_task_is_still_suggestions`: the usage ledger row's task is `"suggestions"`.

  In `test_suggest_controls.py`:
  - `test_resolve_controls_dedupes_and_orders`;
  - `test_resolve_controls_follows_an_alias_over_a_garbled_ledger`: store an alias from `thread:mara-s-map` to `thread:find-the-ledger`, then garble plot.json to `"{ no"`. Against a hand-built snapshot whose `driver_index` lists `thread:find-the-ledger` (a real one would hold no threads now), a focus on the source is still canonicalized to the target. `Ledgers.load` reports the ledger unreadable, `exists` answers `None`, and `_hop_problem` treats unknown existence as passable, so `live_canon` does not raise here.
  - `test_resolve_controls_canonicalization_falls_back_to_identity`: monkeypatch `effective.live_canon` to raise `RuntimeError`. `resolve_controls` returns the request refs unchanged and does not raise. This is the only test that reaches Decision 4's fallback.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_suggestion_controls_route.py tests/test_suggest_controls.py -q -p no:cacheprovider`. Expected: FAIL (the body is ignored; there is no `resolve_controls`).
- [ ] **Step 3: Implement** the model, `resolve_controls`, the route and the payload.
- [ ] **Step 4: Run every suggestion caller.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_suggestion_controls_route.py tests/test_suggest_controls.py tests/test_routes.py tests/test_draft_runs.py tests/test_llm_error_status.py tests/test_routing_routes.py tests/test_playing_store.py -q -p no:cacheprovider`. Expected: PASS, including every query-parameter test unedited.
- [ ] **Step 5: Run the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_pydantic_guard.py tests/test_routing_guard.py tests/test_route_order.py tests/test_import_guard.py -q -p no:cacheprovider`. Expected: PASS.
- [ ] **Step 6: Lint and types.** From the repo root, `make check-lint check-mypy check-pydantic1 PY=$PWD/backend/.venv/bin/python`. Expected: PASS. `routes/scenes.py` gains no `B904`, and its `C901` count does not grow; `check-pydantic1` proves the optional body on pydantic 1.10.
- [ ] **Step 7: Commit:** `feat(scenes): scene-suggestion request body with validated story-pressure controls`

---

### Task 6: Saved ideas carry driver provenance and derive staleness

**Files:**
- Modify:
  - `store/scene_ideas.py`: docstring record shape; `add(..., *, drivers=None, time_anchor=None)`; `_union`; `_project`;
  - `store/suggest.py`: `idea_provenance`, and `validate_ideas`'s annotation through private `_IdeaContext`, `_classify` and `_stale_reason`;
  - `routes/models.py`: `SceneIdeaCreate`;
  - `routes/scenes.py`: `post_scene_idea`.
- Test: `tests/test_scene_ideas_store.py`, `tests/test_scene_ideas_provenance.py` (new), `tests/test_routes.py` (two pins)

**Interfaces:**
- **`scene_ideas.add(cid, title, premise, cast=None, location="", date="", pcless=False, source=USER, *, drivers: list[dict] | None = None, time_anchor: dict | None = None) -> str`:**
  - a new record stores `drivers` / `time_anchor` only when non-empty;
  - on a standing match, `_union(rec, drivers, time_anchor) -> bool` first cleans the stored values with `_project`'s filter, then appends new refs by `ref` and fills an absent `time_anchor` (Decision 18). The file is written iff it returns `True`, all inside the existing hold.
- **`_project`** gains `"drivers": [{"ref": str, "action": str}, ...]` (malformed entries dropped) and `"time_anchor": {"ref", "relation", "native"} | None` (all three must be strings, and `ref` non-empty).
- **`SceneIdeaCreate`** gains `drivers: list[dict] = []` and `time_anchor: dict | None = None`.
- **`suggest.idea_provenance(cid, drivers, time_anchor) -> {"drivers": [...], "time_anchor": {...} | None}`**, per Decision 16. It never raises for a malformed shape. It takes no `offscreen`: it reads no driver snapshot, and existence does not depend on the mode.
- **`post_scene_idea`:**
  - `prov = store.suggest.idea_provenance(cid, body.drivers, body.time_anchor)`;
  - then `scene_ideas.add(..., drivers=prov["drivers"], time_anchor=prov["time_anchor"])`.
- **`suggest.validate_ideas(cid, ideas) -> list[dict]`:** the same signature. Each idea additionally has:
  - `drivers`: `[{ref, action, kind, label, state}]`, where `state` is `"live"` or `"finished"`;
  - `time_anchor`: `{ref, relation, native, kind, label, friendly, state}` or `None`;
  - `stale_reason: str` and `anchor_date: str`.

  These follow Decision 17, and are derived from the stored refs **before** dangling refs are dropped. When no idea in the list has `drivers` or `time_anchor`, the extra fields are filled with empties and nothing else is read. Otherwise the shared read context (one `Ledgers`, `live_canon`, events, the provider through `_soft_provider`, and `now`) is built once by a tolerant `_IdeaContext.load`, and each idea is annotated inside its own guard (Decision 17), so `validate_ideas` never raises out of the annotation.

- [ ] **Step 1: Write the failing tests.**
  - In `test_scene_ideas_store.py`:
    - `test_a_plain_save_writes_todays_record`: the stored dict has no `drivers` and no `time_anchor` key.
    - `test_provenance_is_stored_when_given`.
    - `test_resave_unions_provenance` (§28.6): the first save has `[thread:a advance]`; the second has `[thread:a close_candidate, commitment:b address]` and an anchor. The result is the same id, with drivers `[thread:a advance, commitment:b address]` and the anchor filled.
    - `test_resave_keeps_an_existing_anchor`: a different anchor on the re-save is ignored.
    - `test_resave_with_nothing_new_does_not_write`: wrap `scene_ideas._write` in a counter; a re-save adding nothing counts 0.
    - `test_garbage_provenance_projects_as_empty`: `drivers: "x"`, `[{"ref": 3}]` and `time_anchor: []` give `[]` and `None`.
    - `test_resave_over_garbage_provenance_writes_a_clean_record` (Decision 18): a stored record hand-edited to `"drivers": "x"` and `"time_anchor": "x"`, then a re-save with `[thread:a advance]` and an anchor. No exception; the stored record now has `drivers == [{"ref": "thread:a", "action": "advance"}]` and the new anchor.
    - Update `test_a_hand_mangled_record_reads_as_defaults_rather_than_raising`: its expected dict gains `"drivers": [], "time_anchor": None`. That is the only edit.
  - In `test_scene_ideas_provenance.py`, with Task 2's campaign shape and the clock at 2026-05-10:
    - `test_write_drops_unknown_and_mismatched_refs`: `thread:ghost` and `commitment:mara-s-oath` with `advance` are dropped; a **closed** thread is kept (existence in any status); an alias source is stored as its canonical. Also:
      - the same ref twice keeps the first entry;
      - `relation: "sideways"` is stored as `on`, and a month-ref anchor sent with `before` is stored as `on`;
      - an `anchor`-action entry naming a ref other than the kept `time_anchor` is dropped;
      - a malformed occurrence ref (`holiday:x:Saltmarch Eve`) is dropped.
    - `test_write_keeps_an_unreadable_ledgers_refs`: plot.json `"{ no"` keeps the thread ref.
    - `test_write_copies_the_anchor_native`: `{ref: "event:the-coronation", relation: "before"}` is stored with `native == "2026-05-13"`. A holiday day ref gets `provider.format(<its fixed>)`, and a month ref gets `""`. An anchor on a deleted event is dropped, along with its `anchor` driver entry.
    - `test_a_passed_anchor_is_stored_and_reads_as_passed` (Decision 16, Review Focus 5): advance the clock past the coronation (it fires), then save a card anchored `before` it. The anchor is stored, and the read gives `stale_reason == "The coronation has passed"`.
    - `test_saves_do_no_driver_or_pressure_work`: replace `continuity_drivers.snapshot` and `pressure.build` with delegating recorders. A save with only a thread ref, and a save with a holiday anchor, both succeed, and both recorders count 0.
    - `test_read_classifies_live_finished_dangling`: closed thread → finished; resolved commitment → finished; deleted thread → absent from the returned list; fired event → finished.
    - `test_read_canonicalizes_and_dedupes_a_merged_ref` (Decision 17, "Canonical on read"): save an idea with `[thread:mara-s-map advance]`; merge it with `review.create_alias(cid, "thread:mara-s-map", "thread:find-the-ledger")`; re-save the same idea with `[thread:find-the-ledger close_candidate]`. The stored record holds both refs (`_union` cannot canonicalize, and this pins that the read, not the file, is what de-duplicates). `GET /scene-ideas?greetings=false` returns that idea with `drivers == [{"ref": "thread:find-the-ledger", "action": "advance", "kind": "thread", "label": "Find the ledger", "state": "live"}]` (the first stored entry's action) and `stale_reason == ""`. A second read leaves the file unchanged.
    - `test_stale_reason_after_resolution` (§28.6): an idea about a commitment, which is then resolved, reads `stale_reason == "Every commitment it was about is resolved"`. An idea about a thread, then closed, reads `"Every thread it was about is closed"`. Every ref deleted reads `"Its drivers no longer exist"`. A closed thread plus a resolved commitment reads `"Nothing it was about is still open"`.
    - `test_stale_reason_from_a_past_anchor` (§28.6): a `before` anchor on the coronation, then `clock.advance(to="2026-05-14")` (which fires it), reads `"The coronation has passed"`. The same with `after` is **not** stale.
    - `test_a_rescheduled_anchor_reads_as_moved` (§28.6, Review Focus 5): `events.update(cid, "the-coronation", date="2026-05-20")` reads `"The coronation moved to 20 May 2026"`, and the returned `time_anchor` is `None`.
    - `test_a_reused_event_slug_reads_as_moved` (Review Focus 5): delete the event and re-create `"The coronation"` on 05-25; the idea reads as moved.
    - `test_a_deleted_anchor_is_dropped_not_fatal` (Review Focus 5): delete the event. The idea's `time_anchor` is `None`. With a live thread also stored, `stale_reason == ""`; with nothing else, `"Its drivers no longer exist"`.
    - `test_an_upcoming_on_anchor_supplies_its_date`: an `on` anchor on the coronation gives `anchor_date == "2026-05-13"`; a `before` anchor gives `""`.
    - `test_a_past_holiday_occurrence_is_finished`: a `"Saltmarch Eve"` holiday ref whose embedded fixed day is behind now is finished, and the idea anchored `on` it reads `"Saltmarch Eve has passed"`, with the name taken from the ref (Decision 17, "Labels").
    - `test_a_past_month_occurrence_is_finished`: Winifred's month ref for a month before now's is finished; one for now's month is live. The comparison is by month order (`_month_of`), not key equality.
    - `test_occurrence_labels_come_from_the_ref_and_the_actor`: a birthday ref reads the actor's current name, and the id when the actor is gone; a holiday ref whose name was bounded (`<prefix>~<16 hex>`) reads `<prefix>…`.
    - `test_ideas_without_provenance_read_nothing_extra`: replace `effective.Ledgers.load`, `effective.live_canon`, `continuity_drivers.snapshot`, `pressure.build` and `events.read` with delegating recorders, and wrap `calendars.primary_provider` in a counter. `validate_ideas` over two plain ideas returns `stale_reason == ""` for both. The five recorders count 0, and `primary_provider` counts exactly 1: today's `ref_validator` call. Patching `primary_provider` to raise would fail that existing call before any new code ran.
    - `test_a_damaged_read_never_hides_ideas` (Decision 17), with ideas that do carry provenance:
      - plot.json set to `"{ no"`: every idea is listed, with `stale_reason == ""` (unknown is never stale);
      - the primary switched to the raising plugin: `GET /scene-ideas` still lists every idea, with blank dates and no `stale_reason`;
      - one idea whose stored `time_anchor.ref` is `"holiday:garbage"`: the other ideas list annotated as usual, and that one lists with the fallback annotation.
    - `test_reads_never_write`: wrap `atomic.write_text` in a recorder; `GET /scene-ideas` records nothing.
  - In `test_routes.py`: `test_saving_an_idea_resolves_its_references_on_the_way_back` gains `"drivers": [], "time_anchor": None, "stale_reason": "", "anchor_date": ""` in its expected card. That is the only edit; `test_scene_ideas_start_with_only_the_composed_greetings` is unchanged (greeting rows gain nothing, Decision 18).
  - Route test (also in `test_scene_ideas_provenance.py`), `test_post_scene_idea_round_trips_provenance`: a POST with `drivers` and `time_anchor` gives a `GET` row carrying them, with labels and `state`.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_scene_ideas_store.py tests/test_scene_ideas_provenance.py tests/test_routes.py -q -p no:cacheprovider`. Expected: FAIL.
- [ ] **Step 3: Implement.** Update `scene_ideas.py`'s docstring:
  - the record shape gains the optional fields;
  - validation lives in `suggest` (spec §17), and why: the import path;
  - the union rule, and that reads never write.

  `suggest.validate_ideas`'s docstring states the classification, the cost short-circuit, the label sources, and that unknown is never stale.
- [ ] **Step 4: Run them and confirm they pass.** Use the same command. Expected: PASS.
- [ ] **Step 5: Run the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_lock_domain_guard.py tests/test_atomic_guard.py tests/test_import_guard.py tests/test_pydantic_guard.py tests/test_scene_refs.py -q -p no:cacheprovider`. Expected: PASS. `scene_ideas` imports nothing new, and `scene_refs.repoint`'s fan-out over `scene_ideas` is unchanged.
- [ ] **Step 6: Lint and types.** From the repo root, `make check-lint check-mypy check-pydantic1 PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 7: Commit:** `feat(scene-ideas): driver provenance on save, derived staleness on read`

---

### Task 7: Eval case `scene-suggestions` (§28.10 cases 9–10)

**Files:**
- Modify: `evals/graders.py`, `evals/cases.py`, `evals/README.md`
- Create: `evals/recordings/scene-suggestions.compliant.json`, `.cloned.json`, `.bad-date.json`, `.unknown-ref.json`, `.undecodable.json`; `evals/recordings/scene-suggestions-anchor-on.compliant.json`, `.undecodable.json`
- Test: `tests/test_eval_graders.py`; `tests/test_evals.py` (unchanged, but it enforces the recordings)

**Interfaces:**
- **`graders.grade_scene_suggestions(text: str, snapshot: dict, controls, provider) -> list[Check]`** is pure: it takes no store reads, and the provider object is handed in. It reuses `suggest.raw_suggestions`, `suggest.claim` and `suggest.check_date`. The checks:
  - `suggest.json`: non-`None` and non-empty. It short-circuits the rest.
  - `suggest.known_refs`: every **raw** driver entry has a ref in `driver_index` and an action valid for its kind. It is scored raw, as `grade_absorb` scores raw: a tolerant parse would launder the miss.
  - `suggest.anchor_known`: every raw `time_anchor.ref` present is in `anchors`.
  - `suggest.focus_coverage`: every focus ref is claimed (after `claim`) by at least one suggestion.
  - `suggest.distinct`: at least two suggestions, casefolded titles distinct, and the claimed ref sets (anchor entries excluded) not all identical.
  - `suggest.date_consistent`: for each suggestion, `n = suggest.normalize_date(provider, snapshot["now"], raw)` (Decision 12: `check_date` takes a normalized native), then `check_date(..., n)` is not rejected and non-empty. Unless the batch relation is `on` (whose date is derived, not the model's), the **raw** string also satisfies `provider.format(provider.parse(raw)) == raw`. That is case 10's "written in the calendar's own notation", which the tolerant normalizer would otherwise launder.
  - `suggest.on_derived` (only when the batch relation is `on`): every suggestion's checked date equals `provider.format(anchor fixed)`, in the plugin's notation.
- **`cases.build_scene_suggestions() -> dict`:**
  - world Realm with Seraphine (`_world_with_sera`) and Mara;
  - an eval-owned plugin calendar whose source is `_SALTMARCH_CALENDAR_SRC`, a copy of the test suite's wide-provider pattern with a few named months (`evals` must not import `tests`). It is written to `<GRIMOIRE_HOME>/calendars/saltmarch_reckoning.py` and set as primary, with the clock advanced in its notation;
  - threads `"Mara's map"`, `"Find the ledger"` and `"Seraphine's debts"`, and an event `"The coronation"` 10 days out.

  It returns `ctx` with `cid`, `snapshot = suggest.build_snapshot(cid)`, `controls = suggest.resolve_controls(cid, snapshot, focus_refs=[map, ledger], time_mode="anchor", time_anchor_ref="event:the-coronation", time_anchor_relation="before")`, and `provider`.
- **`cases.build_scene_suggestions_on() -> dict`:** the same campaign through a shared private builder, with `time_anchor_relation="on"` and no focus. It exists for §28.10 case 10's derivation half: an `on` date derived in plugin notation through the production parser, against recorded output.
- **`cases._suggestions_prompt(ctx)`** is `suggest.build_prompt(ctx["snapshot"], None, controls=ctx["controls"])`, the production builder.
- **`cases.grade_scene_suggestions(ctx, output)`** is the sum of:
  - `graders.grade_prompt(ctx["messages"], needles)`, under that function's contract: ids, keys and values, never sentences. The needles are `{f"asks_{a}": f'"{a}"' for a in DRIVER_ACTIONS}`, plus `asks_drivers_key` (`'"drivers"'`), `asks_time_anchor_key` (`'"time_anchor"'`), each `HIGH_PRESSURE` state, and each focus ref and the anchor ref as rendered;
  - `graders.grade_prompt_section(ctx["messages"], "drivers_addendum", "scene_suggestions/instruction/drivers_addendum.j2", view=suggest.driver_view(ctx["snapshot"], ctx["controls"]))`, and the same for `controls_addendum.j2`. These cover the diversity, spread and anchor directives whole, so a reword moves both sides together;
  - `graders.grade_scene_suggestions(...)`.
- **The `CASES` entry:**

  ```
  Case(id="scene-suggestions",
       hypothesis="with two focused drivers and a batch anchor in a custom calendar, "
                  "the suggestions spread focus coverage instead of cloning one premise, "
                  "cite only known drivers, and carry dates the anchor rule accepts",
       build=build_scene_suggestions, prompt=_suggestions_prompt,
       grade=grade_scene_suggestions,
       recordings=(
           Recording(BASELINE, ext="json"),
           Recording("undecodable", ("suggest.json",), "json"),
           Recording("cloned", ("suggest.focus_coverage", "suggest.distinct"), "json"),
           Recording("bad-date", ("suggest.date_consistent",), "json"),
           Recording("unknown-ref", ("suggest.known_refs",), "json")))
  Case(id="scene-suggestions-anchor-on",
       hypothesis="with a batch anchor 'on' an event in a custom calendar, every parsed "
                  "date is the anchor's own date in the calendar's notation, whatever the "
                  "model wrote",
       build=build_scene_suggestions_on, prompt=_suggestions_prompt,
       grade=grade_scene_suggestions,
       recordings=(
           Recording(BASELINE, ext="json"),
           Recording("undecodable", ("suggest.json",), "json")))
  ```

- **The recordings**, written in the plugin's notation:
  - `compliant`: three suggestions, one claiming Mara's map, one Find the ledger, and a quiet one; dates before D;
  - `cloned`: three identical claims on Mara's map;
  - `bad-date`: the compliant claims with dates after D;
  - `unknown-ref`: compliant, plus one extra `thread:maras-compass` entry;
  - `undecodable`: prose.
  - `scene-suggestions-anchor-on.compliant`: three suggestions, one with a wrong model date, one with none, and one in friendly form, with distinct titles and differing claims. Every parsed date is the anchor's native.
  - `scene-suggestions-anchor-on.undecodable`: prose.

- [ ] **Step 1: Write the failing grader tests**, one per failure mode, in the existing `failed()` style: `test_suggest_prose_fails_json_only`, `test_suggest_unknown_ref_fails_known_refs`, `test_suggest_unknown_anchor_fails_anchor_known`, `test_suggest_clone_fails_coverage_and_distinct`, `test_suggest_bad_date_fails_date_consistent`, `test_suggest_friendly_form_fails_notation` (a `before` date the tolerant parser accepts, written in friendly form), `test_suggest_on_derives_whatever_the_model_wrote`, `test_suggest_compliant_passes`. They use a hand-built snapshot and the Gregorian provider.
- [ ] **Step 2: Run them and confirm they fail.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_eval_graders.py -q -p no:cacheprovider`. Expected: FAIL (`AttributeError`).
- [ ] **Step 3: Implement** the grader, the two cases and the seven recordings. Update `evals/README.md`:
  - the case table gains `scene-suggestions` and `scene-suggestions-anchor-on`;
  - the "N pass/fail questions" sentence goes up by two from whatever the base tip states (C and D may have raised it);
  - the counterexample list.

  Any new command spells both `.venv/bin/python` and `.venv\Scripts\python.exe` (`test_install_scripts`).
- [ ] **Step 4: Run the eval tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_eval_graders.py tests/test_evals.py tests/test_install_scripts.py -q -p no:cacheprovider`. Expected: PASS, with `test_no_orphan_recordings` and `test_every_case_has_a_recordable_baseline` holding.
- [ ] **Step 5: Lint and types.** From the repo root, `make check-lint check-mypy PY=$PWD/backend/.venv/bin/python`. Expected: PASS. `evals/cases.py` has `RUF005: 2` recorded; the new code uses unpacking rather than `+` on literals, so that count does not grow.
- [ ] **Step 6: Commit:** `test(evals): scene-suggestions case for focus coverage and anchored dates`

---

### Task 8: Frontend API, types and the pure Story Pressure helpers

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/api/client.test.ts`
- Create: `frontend/src/components/pressureControls.ts`, `frontend/src/components/pressureControls.test.ts`
- Test (backend): `tests/test_suggest_controls.py` (the TS mirror pin)

**Interfaces:**
- **`types.ts`** (reuse any name Slice D already declared rather than redeclaring it):
  - `export type DriverKind = "thread" | "commitment" | "event" | "birthday" | "holiday"`;
  - `export type DriverAction = "advance" | "close_candidate" | "address" | "fulfill_candidate" | "break_candidate" | "expire_candidate" | "anchor"`;
  - `export type PressureState = "overdue" | "today" | "due_soon" | "upcoming" | "passed" | "stale" | "ok"`;
  - `export type TimeMode = "auto" | "near" | "move" | "anchor"`, `export type AnchorRelation = "before" | "on" | "after" | "by"`, `export type DriverControl = "normal" | "focus" | "avoid" | "must"`;
  - `export type DriverLink = { id: string; relation: string; other: string; direction: "out" | "in" | "both" }`;
  - `export type Driver = { ref: string; kind: DriverKind; label: string; summary: string; actors: string[]; status: string; pressure: { state: PressureState; in_days: number | null; friendly: string }; time_anchors: string[]; links: DriverLink[] }`;
  - `export type AnchorOption = { ref: string; kind: "event" | "birthday" | "holiday"; label: string; native: string; friendly: string; fixed: number | null; in_days: number | null; precision: "exact" | "yearless" | "month" }`;
  - `export type DriversSnapshot = { now: string; friendly: string; fixed: number | null; matching: "basic" | "semantic"; drivers: Driver[]; anchors: AnchorOption[] }`;
  - `export type SuggestionDriver = { ref: string; kind: DriverKind; action: DriverAction; label: string }`;
  - `export type SuggestionAnchor = { ref: string; kind: DriverKind; relation: AnchorRelation; label: string; friendly: string; in_days: number | null }`;
  - `export type RefLabel = { ref: string; label: string }`;
  - `SceneSuggestion` gains the optional `drivers?: SuggestionDriver[]`, `time_anchor?: SuggestionAnchor | null`, `date_friendly?: string`, `in_days?: number | null`, `date_rejected?: boolean`, `unmet_must?: RefLabel[]` and `avoided?: RefLabel[]` (§27: old clients and fixtures tolerate their absence);
  - `SceneIdea` gains the optional `drivers?: (SuggestionDriver & { state: "live" | "finished" })[]`, `time_anchor?: (SuggestionAnchor & { native: string; state: "live" | "finished" }) | null`, `stale_reason?: string` and `anchor_date?: string`;
  - `SceneIdeaDraft` gains `drivers?: { ref: string; action: DriverAction }[]` and `time_anchor?: { ref: string; relation: AnchorRelation }`;
  - `export type SceneSuggestionsOptions = { after?: string; offscreen?: boolean; direction?: string; rank?: boolean; focus_refs?: string[]; avoid_refs?: string[]; must_refs?: string[]; time_mode?: TimeMode; time_anchor_ref?: string; time_anchor_relation?: AnchorRelation | "" }`.
- **`client.ts`:**
  - `sceneSuggestions(cid: string, opts: SceneSuggestionsOptions = {}, signal?: AbortSignal)`:
    - it POSTs to `/api/campaigns/${cid}/scene-suggestions`, with no query string;
    - the JSON body is `{offscreen: false, direction: "", rank: true, focus_refs: [], avoid_refs: [], must_refs: [], time_mode: "auto", time_anchor_ref: "", time_anchor_relation: "", ...opts}`, with `after` included only when set;
    - it goes through `draftRun` exactly as today.
  - `continuityDrivers(cid: string) => request<DriversSnapshot>("GET", `/api/campaigns/${cid}/continuity/drivers`, undefined, { fresh: true })`.
- **`pressureControls.ts`:**
  - `export const SORT_ORDER = [...] as const`, `DRIVER_KINDS`, `DRIVER_ACTIONS`, `TIME_MODES`, `ANCHOR_RELATIONS`, `export const MUST_CAP = 3`;
  - `export const ACTION_LABELS: Record<DriverAction, string>`: Advances / May close / Addresses / May fulfil / May break / May expire / Anchored to;
  - `export const KIND_HEADINGS: Record<DriverKind, string>`: Threads / Commitments / Events / Birthdays / Holidays;
  - `export type PressureControls = { drivers: Record<string, DriverControl>; time: TimeMode; anchor: string; relation: AnchorRelation }`, with `export const NO_PRESSURE: PressureControls` (`{drivers: {}, time: "auto", anchor: "", relation: "on"}`);
  - `export function groupDrivers(drivers: Driver[]): { kind: DriverKind; heading: string; rows: Driver[] }[]`: kinds in `DRIVER_KINDS` order, empty kinds omitted, rows sorted by `SORT_ORDER` index, then `in_days` (nulls last), then label;
  - `export function steeredCount(c: PressureControls): number`: the drivers whose control is not `normal`, and nothing else. §16.2: "its label states how many drivers are not Normal";
  - `export function timeLabel(c: PressureControls): string`: `""` for `auto`, else `"near date"`, `"time moves"` or `"anchored"`. The summary shows it separately (Task 9);
  - `export function whenPhrase(inDays: number | null, precision?: string): string`: it mirrors Decision 10. `today`, `in 1 day`, `in N days`, `1 day ago`, `N days ago`, `day unknown` (month precision), `undated` (`null`). It is used by the row chip and the anchor options, so nothing reads "in -3 days" or "in 1 days";
  - `export function isActive(c: PressureControls): boolean`: any driver not `normal`, or `time !== "auto"`. This is what `controlled` and the request use; it is not the summary count;
  - `export function mustAllowed(kind: DriverKind, ref: string, c: PressureControls): boolean`: thread or commitment, and either already must, or fewer than `MUST_CAP` must refs;
  - `export function chooseAnchor(c: PressureControls, ref: string, snap: DriversSnapshot): PressureControls`: sets `time: "anchor"` and `anchor: ref`; forces `relation: "on"` when that option's `precision === "month"`; and returns an `avoid` on that ref to `normal` (Decision 26). Choosing "Choose anchor…" calls it with `snap.anchors[0].ref`, so anchor mode never holds a blank anchor;
  - `export function toRequest(c: PressureControls, snap: DriversSnapshot | null): Pick<SceneSuggestionsOptions, "focus_refs" | "avoid_refs" | "must_refs" | "time_mode" | "time_anchor_ref" | "time_anchor_relation">`: `time_anchor_ref`/`relation` are empty unless `time === "anchor"`. The relation is sent as `on` when the anchor's option has month precision. It never sends anchor mode with a blank ref; with no anchor it sends `time_mode: "auto"`. This is the last line of defence against a 400 `anchor_relation` / `anchor_missing`;
  - `export function pruneControls(c: PressureControls, snap: DriversSnapshot): { controls: PressureControls; dropped: string[] }`: drivers controls for refs not in `snap.drivers`, and an anchor not in `snap.anchors`, go back to normal / `auto` (Decision 19);
  - `export type ChooserSeed = { drivers?: Record<string, DriverControl>; anchor?: { ref: string; relation: AnchorRelation } }`;
  - `export function sanitizeSeed(state: unknown): ChooserSeed | null`: accepts only the two §16.5 shapes, plus any `DriverControl` value, with string refs;
  - `export function applySeed(seed: ChooserSeed, snap: DriversSnapshot | null): { controls: PressureControls; dropped: string[] }`:
    - with `snap === null` (a failed read), everything is dropped;
    - a must on a temporal kind, or beyond the cap, is dropped;
    - an anchor not in `anchors` is dropped;
    - an anchor present in `anchors` becomes `chooseAnchor(controls, ref, snap)` with the seed's relation, so `{time: "anchor", anchor: ref, relation}`, and `on` for a month-precision anchor;
  - `export function dropRefs(c: PressureControls, refs: string[]): PressureControls`: those refs go back to normal, and the anchor is cleared (with `time` back to `auto`) if named.

- [ ] **Step 1: Write the failing tests.**
  - `client.test.ts`:
    - `sceneSuggestions sends its options as one JSON body`: the fetch for `api.sceneSuggestions("c", {after: "s1", focus_refs: ["thread:mara-s-map"]})` is a POST to exactly `/api/campaigns/c/scene-suggestions`, and `JSON.parse(body)` equals the full default object with `after` and `focus_refs` set;
    - `continuityDrivers reads fresh`.
  - `pressureControls.test.ts`:
    - the tuple literals;
    - `groupDrivers` order (an overdue commitment before a due-soon one; an ok thread after a stale one; holidays last);
    - `steeredCount` (a time setting alone counts 0) and `timeLabel`;
    - `whenPhrase` (`0` → `today`, `1` → `in 1 day`, `-3` → `3 days ago`, month precision → `day unknown`, `null` → `undated`);
    - `mustAllowed` (temporal false; a fourth false; an existing must true);
    - `chooseAnchor` (a month anchor forces `on`; an avoid on the anchored ref returns to normal);
    - `toRequest` (anchor fields only in anchor mode; a month anchor held with `before` sends `on`; anchor mode with a blank anchor sends `auto`);
    - `sanitizeSeed` (rejects `{chooser: 3}`, an unknown control, and a relation outside the four);
    - `applySeed` (missing refs and a temporal must are dropped and reported; a failed read drops everything; an anchor seed present in `anchors` gives `{time: "anchor", anchor: "event:the-coronation", relation: "on"}`, and a month anchor seeded with `before` gives `on`);
    - `pruneControls` (a control on a ref the read no longer lists is reset and reported);
    - `dropRefs`.
  - Backend, `test_suggest_controls.py::test_ts_mirrors_the_python_tuples`: read `frontend/src/components/pressureControls.ts` (relative to the repo root via `Path(__file__)`), extract each `export const NAME = [ ... ] as const` with a regex, and assert:
    - `SORT_ORDER == pressure.SORT_ORDER`;
    - `DRIVER_KINDS == continuity_drivers.DRIVER_KINDS`;
    - `DRIVER_ACTIONS == continuity_drivers.DRIVER_ACTIONS`;
    - `TIME_MODES == suggest.TIME_MODES`;
    - `ANCHOR_RELATIONS == suggest.ANCHOR_RELATIONS`;
    - `MUST_CAP` (`export const MUST_CAP = 3`) `== suggest.MUST_CAP`;
    - `set(SORT_ORDER) == set(pressure.PRESSURE_STATES)` (the two orders differ on purpose, Decision 9; the members may not).

    The same test then reads `frontend/src/api/types.ts` (Decision 21). It extracts each `export type NAME = ...;` whose right-hand side is only double-quoted string literals joined by `|` (whitespace and newlines allowed, so a formatter's wrapped union still parses), and the `kind` union inside `export type AnchorOption`. It asserts, as tuples in declaration order unless marked:
    - `DriverKind == continuity_drivers.DRIVER_KINDS`;
    - `DriverAction == continuity_drivers.DRIVER_ACTIONS`;
    - `set(PressureState) == set(pressure.PRESSURE_STATES)` (declared in `SORT_ORDER` order, which the test also asserts);
    - `TimeMode == suggest.TIME_MODES`;
    - `AnchorRelation == suggest.ANCHOR_RELATIONS`;
    - `AnchorOption.kind == suggest.TEMPORAL_KINDS`.

    A union the regex cannot find fails the test by name, rather than passing as an empty match. If the pre-Task-0 step 3 found that Slice D already declared one of these names, the test reads that declaration.
- [ ] **Step 2: Run them and confirm they fail.** From `frontend/`: `npx vitest run src/api/client.test.ts src/components/pressureControls.test.ts`. From `backend/`: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_suggest_controls.py -q -p no:cacheprovider`. Expected: FAIL.
- [ ] **Step 3: Implement** the types, the client and the helpers. Leave `sceneSuggestions`'s callers for Task 9: the typecheck is red until then, so Steps 4–5 run only the files named.
- [ ] **Step 4: Run the tests and confirm they pass.** Use the same two commands. Expected: PASS.
- [ ] **Step 5: Eslint.** From the repo root, `make check-eslint PY=$PWD/backend/.venv/bin/python`. Expected: PASS for the new files. Run `npx tsc --noEmit -p .` from `frontend/`: the only errors expected are at `useSceneSuggestions.ts` and its tests' positional calls, which Task 9 fixes. Do not commit until Task 9's Step 6, so `main` never carries a red typecheck. Tasks 8 and 9 land as one commit.
- [ ] **Step 6:** No commit here; continue to Task 9.

---

### Task 9: The hook and the chooser — controls, the drivers read, seeds and stale refusals

**Files:**
- Modify: `frontend/src/components/useSceneSuggestions.ts`, `frontend/src/components/NewSceneChooser.tsx`, `frontend/src/components/SceneIdeaPicker.tsx` (the new optional props only), `frontend/src/index.css`
- Create: `frontend/src/components/StoryPressure.tsx`
- Test: `frontend/src/components/useSceneSuggestions.test.ts`, `frontend/src/components/NewSceneChooser.test.tsx` (both with the `importActual` spread in their client mock), and any other suite Step 1's grep finds

**Interfaces:**
- **`useSceneSuggestions(cid, afterSid, ready, offscreen, opts?: { controls?: () => SceneSuggestionsOptions; hold?: boolean; onStale?: (refs: string[]) => void })`:**
  - `run(direction, rank)` calls `api.sceneSuggestions(cid, { after: afterSid ?? undefined, offscreen, direction, rank, ...opts?.controls?.() })`.
  - `controls` is read through a ref updated every render, so it is current at dispatch and is not in any dependency list.
  - The auto-ask effect returns early while `opts?.hold`, and `hold` joins its dependencies. The question key stays `${cid}/${afterSid}/${offscreen}`.
  - While `opts?.hold`, `suggest()` is a no-op, and the hook reports `held: true`. `suggest()` also sets `autoAsked.current = question` before it calls `run`, so a release after a press cannot ask a second time (Decision 19).
  - A rejection where `err instanceof ApiError && err.kind === "stale_drivers"` sets `suggestions` to `[]` (and `picks` to `[]` when ranked), leaves `error` null, sets `stale: true`, and calls `onStale(Array.isArray(err.body?.refs) ? (err.body.refs as string[]) : [])`. `stale` is cleared when the next request starts. `instanceof` on the imported class, not a duck-typed `err.kind` on the catch's `any`, which would add `no-unsafe-member-access` findings to a file with no eslint allowance.
  - It returns the existing fields plus `controlled: boolean`, `held: boolean` and `stale: boolean`. `controlled` is set when a reply **lands**, from whether that request's controls were active (`time_mode !== "auto"`, or any ref list non-empty). It is reset to `false` by the reset effect.
- **`StoryPressure({ snap, value, onChange, disabled, open, onToggle })`** renders `<details className="story-pressure" open={open} onToggle=...>`:
  - **The summary** reads `Story pressure`, plus a parenthesis when anything is set. It holds ``${n} not Normal`` when `steeredCount > 0` (drivers only, §16.2) and `timeLabel` when the time is not `auto`, joined by ` · `. Examples: `Story pressure (2 not Normal)`, `Story pressure (anchored)`, `Story pressure (1 not Normal · near date)`.
  - **Per kind group:** an `<h4>` heading, then one row per driver with:
    - its label;
    - a `<span className="chip">` with the state and ``whenPhrase(in_days, …)`` when the state is not `ok`;
    - a `<div role="radiogroup" aria-label={`${label}: story pressure`}>` of four `<label className="radio-row"><input type="radio" name={`pressure-${ref}`} .../>` options, Normal, Focus, Avoid and Must. Must is disabled unless `mustAllowed`, and Avoid is disabled on the row of the current anchor (Decision 26).
  - **Visible reasons** for the disabled controls, as `field-hint` text inside the disclosure rather than a hover-only `title`, which the Android WebView never shows:
    - under the driver groups: "Must-include: up to 3 threads or commitments; dates are set under Time.";
    - beside a disabled Choose anchor…: "No upcoming dated events, holidays or birthdays to anchor to."
  - **Time** is a `<div role="radiogroup" aria-label="Time">` with Any date, Stay near current date, Let time move, and Choose anchor…. Choose anchor is disabled when `anchors` is empty. Choosing it calls `chooseAnchor(value, snap.anchors[0].ref, snap)`, so an anchor is always selected. It then shows `<select aria-label="Anchor">` (options ``${label} — ${friendly} (${whenPhrase(in_days, precision)})``) and `<select aria-label="Relation">` (before/on/after/by). The Anchor `onChange` goes through `chooseAnchor`, which forces `on` for a month-precision option. For a month-precision anchor, Relation shows only `on`.
  - There is no embeddings note anywhere, and nothing reads `matching` to disable anything.
- **`NewSceneChooser`** gains `seed?: ChooserSeed`, and these state pieces:
  - `pressure: PressureControls` (beside `direction`);
  - `driversRead: { cid: string; snap: DriversSnapshot | null; failed: boolean } | null`;
  - `driversGen: number`;
  - `pressureNote: string`;
  - `pressureOpen: boolean`, starting `false` (§16.2: "collapsed by default"). Only an applied seed sets it `true`, and the `cid` reset returns it to `false`. `StoryPressure`'s `onToggle` reads `event.currentTarget.open`, as `tracker/TrackerDisclosure.tsx` does, so a summary click and the controlled `open` prop cannot disagree;
  - a `seedApplied` ref.

  Its behaviour:
  - **The read effect** keys on `[cid, driversGen]` and calls `api.continuityDrivers(cid)` with the `live` pattern. A failure sets `failed: true`. Every successful read runs `pruneControls(pressure, snap)`, and names anything pruned in `pressureNote` (Decision 19).
  - **When the read settles and a seed is present and unapplied,** it sets `pressure` from `applySeed`. `pressureNote` becomes ``Not current any more, so not applied: ${dropped.join(", ")}`` when anything was dropped, or `"Story pressure could not be read; the Story Graph selection was not applied."` on a failed read. It opens the disclosure when anything was applied.
  - **The hook call** passes `{controls: () => toRequest(pressure, driversRead?.snap ?? null), hold: !!seed && !seedApplied.current, onStale}`.
  - **`onStale(refs)`** sets `pressure = dropRefs(pressure, refs)`, sets the note "Some selections are no longer current and were reset — press Regenerate.", naming the labels (or refs) that dropped, and bumps `driversGen`. The re-read's prune catches a canonical spelling that `dropRefs` could not match.
  - **The `cid` reset block** also resets `pressure`, `driversRead`, `pressureNote` and `pressureOpen`, and marks the seed applied, so a seed never crosses campaigns.
  - **The picker** receives `storyPressure={driversRead && !driversRead.failed && driversRead.snap ? <StoryPressure .../> : null}`, plus `pressureNote` and the hook's `controlled`, `held` and `stale`.
- **`SceneIdeaPicker`** gains the optional props `storyPressure?: ReactNode`, `pressureNote?: string`, `controlled?: boolean`, `held?: boolean`, `stale?: boolean`. It renders `storyPressure`, then a `field-hint` for the note, directly under the `.idea-direction` row inside Generated.
  - While `held`, the Suggest button is disabled and a hint reads "Reading story pressure…".
  - While `stale`, the "No ideas came back…" hint is suppressed: nothing was generated, and the note says why.
  - The cards are Task 10's.
- **`index.css`:**
  - `.chooser .story-pressure` (the summary cursor, and a bounded inner list with `max-height: 40vh; overflow-y: auto` so a long driver list cannot push Suggest off a phone screen);
  - `.chooser .story-pressure .radio-group` as a wrapping row;
  - `.chooser .chip.warn`, using the existing tokens (`--banner-bg`/`--banner-ink`).

  The rules work at 375px (the chooser is `92vw`).

- [ ] **Step 1: Update the positional pins and the client mocks, and write the failing tests.**
  - **The client mocks must export the real `ApiError`.** The hook's `err instanceof ApiError` reads that export on every rejection. Under vitest 2, reading an export a `vi.mock` factory did not define throws ("No "ApiError" export is defined on the mock"), so every existing rejection test (`useSceneSuggestions.test.ts` lines 106, 115 and 130 today) would throw inside the hook's `.catch`, and the new tests could not build an `ApiError` at all. Both factories become async and spread the real module first, as `testkit/campaignMocks.tsx` already does:
    - `useSceneSuggestions.test.ts`: `vi.mock("../api/client", async () => ({ ...(await vi.importActual<typeof import("../api/client")>("../api/client")), api: { sceneSuggestions: vi.fn() } }))`;
    - `NewSceneChooser.test.tsx`: the same spread before its `api` object.

    Then grep every other suite that renders the real hook through a `vi.mock("../api/client" | "../../api/client", …)` factory without that spread, and give it the same spread. The campaign-view suites already have it through `campaignApiMock`, and `ScenesView.test.tsx` stubs the chooser. Each edited suite is listed in the commit.
  - `useSceneSuggestions.test.ts`:
    - every `toHaveBeenCalledWith("c", "s1", off, dir, rank)` becomes `toHaveBeenCalledWith("c", { after: "s1", offscreen: off, direction: dir, rank })` (lines 22, 42, 84, 126, 192, 209 today);
    - new: `the open call carries the controls getter's value`;
    - new: `changing controls starts no call`: rerender with a different getter result, and the call count is unchanged;
    - new: `hold delays the open call until released, then asks once`;
    - new: `suggest during a hold makes no call, and the release asks exactly once`: hold, `suggest()`, release; `api.sceneSuggestions` was called once, and `held` was true during the hold;
    - new: `a stale_drivers refusal calls onStale with its refs and sets no error`: the rejection is `new ApiError(409, "stale", "stale_drivers", {refs: ["thread:ghost"]})`; `stale` is true, and the next request clears it;
    - new: `controlled follows the batch on screen`: true after a reply to an active-controls request, and still true after the getter changes with no new request.
  - `NewSceneChooser.test.tsx`:
    - add `continuityDrivers` to the mock list, with a `beforeEach` default resolving a snapshot with `"Mara's map"` (thread, stale), `"Mara's oath"` (commitment, due_soon) and `"The coronation"` (event, upcoming, also an anchor), and `matching: "basic"`;
    - rewrite the positional pins at lines 62–63, 94–95, 105–106 and 127 to the options form with `expect.objectContaining({after, offscreen, direction, rank})`.

    New tests (§28.9, chooser):
    - `the drivers read happens once on open`: pick "Import a transcript", press its Back (the only Back that returns to the mode cards), then pick "With your PC"; pick a generated card, then press Back from the confirm form. `continuityDrivers` was still called exactly once;
    - `story pressure is a collapsed disclosure under Direction` (§16.2): on an unseeded open, once the drivers read settles, `container.querySelector("details.story-pressure")` exists and has no `open` attribute. The Generated group is a run of siblings rather than one element (`<div className="role">Generated</div>` through `<div className="role">Your own</div>` in `SceneIdeaPicker.tsx`), so "inside it" is asserted in document order with `compareDocumentPosition`: the `details` follows the `Generated` role header and the `.idea-direction` row, and precedes the `Your own` role header. A Focus radio is `not.toBeVisible()`; after `fireEvent.click` on the summary, the `details` has `open`, and the per-driver radiogroups and the `Time` radiogroup are visible. After a switch to another `cid` and back, it is collapsed again;
    - `a failed drivers read hides Story pressure and Suggest still works`;
    - `driver controls alter the request body`: open the disclosure, choose Focus for Mara's map and Must for Mara's oath, and press Regenerate. The last call's options contain `focus_refs: ["thread:mara-s-map"]` and `must_refs: ["commitment:mara-s-oath"]`;
    - `changing a control starts no generation`;
    - `Must is disabled for temporal drivers and after three`: also `getByText(/up to 3 threads or commitments/)` is visible text;
    - `with no anchors, Choose anchor is disabled and says why`: a snapshot with `anchors: []`; the radio is disabled and `getByText(/No upcoming dated events/)` shows;
    - `anchor mode sends the anchor and relation`. Also:
      - picking Choose anchor… and pressing Regenerate with nothing else touched sends a non-empty `time_anchor_ref` (the first anchor);
      - choosing an exact event with `before`, then a month-precision birthday, then Regenerate, sends `time_anchor_relation: "on"`;
    - `the anchored row cannot be avoided`: Avoid on The coronation, then anchor on it; the row's control is back to Normal, its Avoid radio is disabled, and the request has `avoid_refs: []`;
    - `the summary counts drivers and names the time separately`: one Focus plus an anchor reads `Story pressure (1 not Normal · anchored)`; an anchor alone reads `Story pressure (anchored)`;
    - `driver chips read naturally`: an overdue driver's chip reads `overdue · 3 days ago`, and a today driver's reads `today`. No text matches `/in -\d|in 0 days|in 1 days/`;
    - `basic matching keeps Suggest enabled and says nothing about embeddings`: `queryByText(/embedding/i)` is null;
    - `story pressure survives Back and resets on a campaign switch`;
    - `a seed focuses its drivers and the open call carries them`: `seed={{drivers: {"thread:mara-s-map": "focus"}}}`, a mode pick, and the first call has `focus_refs: ["thread:mara-s-map"]`;
    - `an anchor seed sends anchor mode on the open call` (§16.5): `seed={{anchor: {ref: "event:the-coronation", relation: "on"}}}` and a mode pick give a first call with `time_mode: "anchor"`, `time_anchor_ref: "event:the-coronation"` and `time_anchor_relation: "on"`;
    - `a seed waits for the drivers read before the open call`: a deferred read gives no call, and the Suggest button is disabled with "Reading story pressure…"; resolving it gives exactly one call, carrying the seed;
    - `a seed naming a missing driver is dropped with a note`;
    - `a seed after a failed read is dropped with a note and the open call goes unsteered`;
    - `a stale_drivers refusal re-reads drivers and says what dropped`: the note reads "Some selections are no longer current and were reset — press Regenerate.", and "No ideas came back" is absent;
    - `a refused ref spelled differently is still cleared`: hold Focus on `thread:mara-s-map`; the 409's `refs` are `["thread:find-the-ledger"]`; the re-read lists neither. The next Regenerate's request has no `thread:mara-s-map`.
- [ ] **Step 2: Run them and confirm they fail.** From `frontend/`, `npx vitest run src/components/useSceneSuggestions.test.ts src/components/NewSceneChooser.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** the hook, `StoryPressure`, the chooser state and the CSS. Fix the stale comments that still say the hook fires nothing on its own (`NewSceneChooser.tsx` around lines 94–100, `SceneIdeaPicker.tsx` lines 29–34 and 137–140, `CampaignHub.tsx` lines 702–711), so they describe the ranked call on mode pick (spec §3.10).
- [ ] **Step 4: Run them and confirm they pass**, along with every chooser consumer: `npx vitest run src/components/useSceneSuggestions.test.ts src/components/NewSceneChooser.test.tsx src/components/SceneIdeaPicker.test.tsx src/routes/CampaignView.test.tsx src/components/review/SceneReview.test.tsx src/api/client.test.ts src/components/pressureControls.test.ts`. Expected: PASS.
- [ ] **Step 5: Typecheck and eslint.** From `frontend/`, `npx tsc --noEmit -p .`. Expected: no errors (`SceneIdeaPicker.test.tsx`'s `Wrapper` passes the state object, and the new props are optional). From the repo root, `make check-eslint PY=$PWD/backend/.venv/bin/python`. Expected: PASS. `NewSceneChooser.tsx`'s recorded a11y counts must not grow: `<details>`/`<summary>` and radios add no handler on a non-interactive element.
- [ ] **Step 6: Commit** Tasks 8 and 9 together, with the backend TS-mirror test: `feat(chooser): story pressure controls with a drivers read, seeds and stale handling`

---

### Task 10: Cards show why; the Stale group; dates; the hub skips stale ideas

**Files:**
- Modify: `frontend/src/components/SceneIdeaPicker.tsx`, `frontend/src/components/sceneDraft.ts`, `frontend/src/routes/CampaignHub.tsx`
- Test: `frontend/src/components/SceneIdeaPicker.test.tsx`, `frontend/src/components/sceneDraft.test.ts`, `frontend/src/routes/CampaignHub.test.tsx`

**Interfaces:**
- **Generated card**, inside its `<button>`. Every chip is a non-interactive `<span>`, because the card is itself a button:
  - when `s.time_anchor` is set: `<span className="field-hint">` with `[s.date_friendly, `${s.time_anchor.relation} ${s.time_anchor.label}`].filter(Boolean).join(" · ")`;
  - for each driver whose action is not `anchor`: `<span className="chip on">` with ``${ACTION_LABELS[action]}: ${label}``;
  - for each `unmet_must`: `<span className="chip warn">` with ``Doesn't claim to address ${label}``;
  - for each `avoided`: `<span className="chip warn">` with ``Claims to address ${label} (avoided)``;
  - when `s.date_rejected`: `<span className="field-hint">`, reading `date not consistent with anchor` when anchored, and `date not consistent with the time setting` otherwise.
- **`generatedCards`:** `controlled ? (suggestions ?? []) : <today's slice>`. The key stays `key={i}`.
- **`asDraft(s)`** adds `drivers: (s.drivers ?? []).filter(d => d.action !== "anchor").map(({ref, action}) => ({ref, action}))` only when that list is non-empty, and `time_anchor: {ref, relation}` only when `s.time_anchor` is truthy. `drivers` is optional on `SceneSuggestion` (§27), and the existing fixtures pass suggestions without it, so the `?? []` is what keeps `tsc` and the exact-body pins at lines 450–455 green.
- **Saved group:**
  - `live = active.filter(i => !i.stale_reason)` and `stale = active.filter(i => !!i.stale_reason)`;
  - `SAVED_SLOTS` and "Show all N saved" apply to `live`;
  - a `<button className="subtle" aria-expanded={showStale}>` reads `Stale (${stale.length})` when `stale.length > 0`;
  - when expanded, each stale idea renders like a saved row (a card that calls `savedDraft`, plus the dismiss ×), with `<span className="field-hint">{i.stale_reason}</span>` inside the card.
- **`sceneDraft.ts`:** `suggestionDraft` uses `date: s.date || (s.time_anchor ? "" : nextDate)`, and `savedDraft` uses `date: idea.anchor_date || (idea.time_anchor ? "" : nextDate || idea.date)`. Both docstrings say why (Decision 23).
- **`CampaignHub`:** `rows.filter(i => i.status === "active" && !i.stale_reason)`.

- [ ] **Step 1: Write the failing tests.**
  - `SceneIdeaPicker.test.tsx`:
    - `a generated card shows its validated reasons` (§28.9): a suggestion with `time_anchor {relation: "before", label: "The coronation"}`, `date_friendly "12 May 2026"` and drivers Advances Mara's map / Addresses Mara's oath gives `getByText("12 May 2026 · before The coronation")`, `getByText("Advances: Mara's map")` and `getByText("Addresses: Mara's oath")`;
    - `constraint misses show warning chips`;
    - `a rejected date says so`, in both wordings;
    - `every generated suggestion shows while controls produced the batch`: `controlled` with three greetings and five suggestions shows all five;
    - the existing `a ranking shows 2 greetings + 2 ideas` test is unchanged and still green;
    - `saving a generated card sends its drivers and anchor`: the exact body includes `drivers` (anchor entries excluded) and `time_anchor`;
    - the exact pins at lines 450–455 and 518–525 stay unedited;
    - `stale ideas sit under a collapsed Stale group outside the budget` (§28.9): five live ideas and one stale give `Show all 5 saved` and `Stale (1)`. The stale title is absent until the toggle; after it, the stale card is pickable (`onPicked` gets a saved draft with its `lid`), and its reason text shows.
  - `sceneDraft.test.ts`:
    - `an anchored suggestion never borrows nextDate`;
    - `a saved idea anchored on an upcoming occurrence uses the server anchor date`: `anchor_date` wins over `nextDate`, and an idea with no `anchor_date` keeps today's inverted precedence;
    - `a saved anchored idea never borrows the batch's anchor-unaware nextDate`: a `before` idea, and an `on` idea whose occurrence has passed, each with no `anchor_date`, get `""` despite a `nextDate`, while an un-anchored idea still borrows it.
  - `CampaignHub.test.tsx`: `play next skips stale ideas` (Review Focus 5). The tail counts only live ideas, and `(api as any).sceneSuggestions` is still `undefined`.
- [ ] **Step 2: Run them and confirm they fail.** From `frontend/`, `npx vitest run src/components/SceneIdeaPicker.test.tsx src/components/sceneDraft.test.ts src/routes/CampaignHub.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and confirm they pass.** Use the same command, plus `src/components/NewSceneChooser.test.tsx`. Expected: PASS.
- [ ] **Step 5: Typecheck and eslint.** From `frontend/`, `npx tsc --noEmit -p .`. From the repo root, `make check-eslint PY=$PWD/backend/.venv/bin/python`. Expected: PASS, with `SceneIdeaPicker.tsx`'s three recorded findings unchanged.
- [ ] **Step 6: Commit:** `feat(picker): cards show validated reasons and warnings; stale ideas group apart`

---

### Task 11: `ScenesView` adopts the Story Graph handoff

**Files:**
- Modify: `frontend/src/routes/ScenesView.tsx`
- Test: `frontend/src/routes/ScenesView.test.tsx`

**Interfaces:**
- Consumes: `sanitizeSeed`, `ChooserSeed` (Task 8), imported from `../components/pressureControls`; `NewSceneChooser`'s `seed` prop (Task 9).
- Produces, in `ScenesView`:
  - `const location = useLocation()` and `const [seed, setSeed] = useState<ChooserSeed | null>(null)`;
  - `const handoff = sanitizeSeed((location.state as { chooser?: unknown } | null)?.chooser)`;
  - an effect that, when the raw `chooser` state exists, sets `seed` to `handoff` (possibly `null` for a malformed state), sets `choosing` to true only when `handoff` is non-null, and always replaces the entry with `navigate(location.pathname + location.search + location.hash, { replace: true, state: null })`. Its dependencies follow `CampaignView`'s.
  - The chooser renders when `choosing && !(seed && scenes === null && !failed)` (Decision 20), with `seed={seed ?? undefined}`. `onClose` and `onCreated` clear `seed`.
  - The `+ New scene` path is unchanged and passes no seed.

- [ ] **Step 1: Write the failing tests.** Extend the `NewSceneChooser` stub to render `<pre data-testid="seed">{JSON.stringify(seed ?? null)}</pre>`, and `renderScenes` to accept `{state}` as `initialEntries: [{pathname: "/campaigns/run/scenes", state}]`.
  - `a chooser handoff opens the chooser seeded once`: the state `{chooser: {drivers: {"thread:mara-s-map": "focus"}}}` opens the chooser with that seed. After close, a rerender does not reopen it (the state was replaced).
  - `an anchor handoff seeds the anchor`.
  - `a malformed chooser state is ignored and cleared`: `{chooser: {drivers: 3}}` opens nothing.
  - `a seeded open waits for the scene list`: a deferred `listScenes` shows no chooser, and resolving it shows one with `afterSid` (pass `afterSid` into the stub's output).
  - The existing seedPrompt tests (lines 461 and 469) are unchanged and still green.
- [ ] **Step 2: Run them and confirm they fail.** From `frontend/`, `npx vitest run src/routes/ScenesView.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.** The comment cites `CampaignView`'s adopt-once rationale, and says the graph's sending side is Slice F's.
- [ ] **Step 4: Run them and confirm they pass.** Use the same command. Expected: PASS.
- [ ] **Step 5: Typecheck and eslint.** From `frontend/`, `npx tsc --noEmit -p .`. From the repo root, `make check-eslint PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 6: Commit:** `feat(scenes): adopt the Story Graph chooser handoff once`

---

### Task 12: Cross-check the whole suggestion path end to end

The slices above each prove one layer. This task proves they agree, through one cassette-driven run. It is a test-only task.

**Files:**
- Test: `tests/test_suggestion_controls_route.py` (append)

- [ ] **Step 1: Write the tests.**
  - `test_cassette_reply_with_drivers_parses`: with `llm_fakes.from_cassette("campaign_flow")` on a campaign holding a `the-debt` thread, the route lands, and the suggestion's `drivers` lists `thread:the-debt` with its label. This proves Task 3's cassette edit parses.
  - `test_saved_card_round_trip_goes_stale_after_resolution`: generate with a must on a commitment; save the first card through `POST /scene-ideas` with its `drivers` and `time_anchor`, as the picker sends them; resolve the commitment through `PUT /ledger/commitments/<id>`. Then `GET /scene-ideas?greetings=false` returns the idea with `stale_reason == "Every commitment it was about is resolved"`, and it is still `status == "active"`.
  - `test_opening_paths_make_no_model_call` (§3.10, AC16): with `llm_fakes.from_entries([])`, `GET /continuity/drivers`, `GET /scene-ideas` and a `POST /scene-suggestions` refused 409 `stale_drivers` each make no request to the fake.
- [ ] **Step 2: Run them.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_suggestion_controls_route.py -q -p no:cacheprovider`. Expected: PASS. If one fails, the defect belongs to the task that owns that layer; fix it there and re-run that task's tests.
- [ ] **Step 3: Lint.** From the repo root, `make check-lint PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 4: Commit:** `test(suggest): end-to-end controls, provenance and staleness`

---

### Task 13: Slice gate

- [ ] **Step 1: Lint, types, eslint and templates.** From the repo root, `make check-lint check-mypy check-eslint check-templates PY=$PWD/backend/.venv/bin/python`. Expected: PASS. If a change resolved a recorded finding, run `make baseline` and commit the smaller file.
- [ ] **Step 2: The full backend suite under its coverage floor.** `make check-py PY=$PWD/backend/.venv/bin/python`. Expected: PASS, including `tests/test_evals.py` (the replay of every case, `scene-suggestions` among them), `test_llm_fakes` and `test_frozen_campaign`. Only failures listed in the slice ledger by the pre-Task-0 step may be excused.
- [ ] **Step 3: The Android dependency set.** `make check-pydantic1 PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 4: The frontend gate.** `make check-web`. Expected: PASS (typecheck plus vitest under coverage). Steps 1–4 are every target of `make check`.
- [ ] **Step 5: Evals replay from the CLI.** `backend/.venv/bin/python evals/run.py --case scene-suggestions` and `--case scene-suggestions-anchor-on` (on Windows, `backend\.venv\Scripts\python.exe evals\run.py --case scene-suggestions`, and the same for the second case). Expected: every recording scores as declared.
- [ ] **Step 6: Amend the spec to match the recorded deviations.** Edit `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md`, citing this plan's decision number in each edit:
  - §15: the snapshot's additive keys (`anchors`, `fixed`, `near_days`; dormancy on index and commitment rows), `build_snapshot(drivers=False)` as the intent shape, and "pressure-state precedence" read as `SORT_ORDER` (Decisions 2, 9);
  - §24 and §26: the suggestion snapshot's calendar block, the suggestion parser and `date_normalizer` degrade to blank dates under a plugin that raises anything, where they used to fail (Decision 25);
  - §15.1: the drivers and controls addenda follow the byte-identical instruction section (Decision 1);
  - §15.2: anchor validity is membership in `anchors` (Decision 5);
  - §15.3: a month-only anchor takes only `on`; no-`now` behaviour; `date_rejected` under `near`/`move`; `next_date`'s checks (Decisions 6, 12);
  - §16.3: canonicalization through `live_canon`; the 400 body's `reason`; `Literal` 422s; the anchor-ref 400/409 split; the batch anchor beats avoid (Decisions 4, 5, 7, 26);
  - §16.4: "show every suggestion" follows the batch on screen (Decision 22);
  - §16.5: `ScenesView`'s adoption is new, a seeded open waits for the drivers read and the scene list, and a seeded disclosure opens (Decisions 19, 20);
  - §17 and §17.1: occurrence refs are never dangling, and are written when well-formed; an anchor's relation is coerced on write; unknown classifies as live; read-side label sources; the fourth stale text; a passed `after` anchor counts as live; `anchor_date`; the plain-save record shape (Decisions 16–18);
  - §20: `DRIVER_ACTIONS` and `ACTIONS_BY_KIND` live in `drivers.py` (Decision 24);
  - §14: the one function's signature becomes `drivers.snapshot(cid, offscreen=False, *, pressure_result=None)`, so the suggestion snapshot's timeline and index share one pressure computation; without the keyword the behaviour is unchanged (Decision 3, deviation 3);
  - §14 and §15: an offscreen snapshot keeps a PC's birthday driver ref (with `actors: []`), while `token_ok` still drops every PC cast token (Decision 15, deviation 11);
  - §15 and AC8: the snapshot's `timeline` holds every pressure item, and the rendered timeline is bounded with the index's rule: pinned refs and `events.sooner`'s pick always, the rest ranked by `SORT_ORDER` and capped at `DRIVER_PROMPT_CAP`, with "and N more dated items"; the snapshot gains `sooner_ref` (Decision 11);
  - §16.4 and §30: the exact card wording, `Doesn't claim to address <label>`, `Claims to address <label> (avoided)`, `date not consistent with anchor`, and `date not consistent with the time setting` for unanchored blanking (deviation 15).

  Commit: `docs(spec): continuity capstone matches slice E's recorded deviations`.
- [ ] **Step 7: Implementation → done.** Run `/codex:review` against the slice diff. Resolve the findings, and commit. If Codex is unavailable, record the independent reviewer that stood in, in the slice ledger.
- [ ] **Step 8: Done → actually done.** Run `/codex:adversarial-review` against the diff **and** the spec. Ask specifically whether the diff implements Slice E: §15–§15.3, §16.1–§16.5 (receiving side), §17–§17.1, §20's driver tuples, §21's two route changes, §23's suggestion rows, §28.6, the §28.9 chooser bullets, and §28.10 cases 9–10. Look for gaps, drift and quietly dropped requirements. Resolve the findings, and commit. Record a substitute reviewer if Codex is unavailable.
  - Then re-read the B, C and D slice ledgers for items naming Slice E that were added after the pre-Task-0 step 4, and copy them into `## Inherited hand-offs`. Confirm every item there is either closed (its owning task and commit named) or explicitly deferred (with the reason, and the place it is now recorded, such as this plan's Open Questions or a later slice's ledger). An item with neither keeps the slice open.
  - Confirm this slice's own ledger lists any hand-off to Slice F or G (for example, the sending side of the §16.5 handoff, and anything the reviews produced).

---

## Self-review notes

**Spec coverage (Slice E):**

| Spec section | Task |
|---|---|
| §15 unchanged keys (`now`, `friendly`, `notation`, `holidays_today`, `events_today`, `story_so_far`, `available_locations`, `cast`) | 2 (`test_unchanged_keys_are_unchanged`: value equality with `drivers=False` for all nine, over non-empty values), 3 |
| §15 threads with ids and dormancy | 2 (refs), 3 (rendered) |
| §15 commitments with id/kind/due/beat/aging-pressure | 2, 3 (shared snippet + pressure) |
| §15 `timeline` = pressure items; contains `sooner`'s pick; AC8 bounded | 2 (`test_timeline_contains_what_sooner_picks`, `sooner_ref`), 3 (`test_timeline_is_capped_but_keeps_sooner_and_anchors`, `CAPPED_SNAP`) |
| §15 `driver_index`, cap 40, pinned refs, "and N more", order | 2, 3 (`driver_view`) |
| §15 `links` among drivers | 2, 3 |
| §15 remove only `upcoming`; Today block, `today_facts`, `day_facts`, `sooner` untouched | 3 (`test_snapshot_drops_only_upcoming`); no edit to those modules |
| §15 intent unchanged via the `drivers` flag | 0 (golden + sweep), 2, 3 |
| §15.1 diversity guidance; byte-identical instruction; card order | 3 (addenda, golden identity), 4 (`order_cards`) |
| §15.2 schema, actions, index validation, anchor auto-add, never drop, `unmet_must`/`avoided`, resolved payload | 1 (vocabulary), 4, 5 |
| §15.3 table, `RESOLVE_WINDOW_DAYS`, `split_native`, batch anchor, blanking | 4 (Review Focus 2, 3) |
| §16.1 states, must cap and kinds | 5 (server), 8–9 (UI) |
| §16.2 disclosure in Generated (collapsed by default: `story pressure is a collapsed disclosure under Direction`), grouping and sort, radios, time radio and selects, state beside direction, read once, failure hides, basic matching | 8, 9 |
| §16.3 body, absent body, query params, options object, `time_mode`, validation order before `run_draft`, 400/409, precedence, captured closure, routing task | 5, 8 |
| §16.4 provenance chips, warnings, rejected date, show all while controlled | 4–5 (payload), 10 |
| §16.5 adoption once, seeded chooser at mode selection, ranked call carries seeds, missing seeds noted | 9, 11 |
| §17 optional fields, validation in `suggest`, write rules, read canonicalization and de-duplication (`test_read_canonicalizes_and_dedupes_a_merged_ref`), read classification, `stale_reason` before drop, reads never write, re-save union, `SceneIdeaCreate` | 6 |
| §17.1 stale reasons, Stale (n) outside the budget, pickable with hint, hub skip, server anchor date | 6, 10 |
| §20 `DRIVER_ACTIONS` pinned; TS unions mirror the tuples | 1, 8 (TS mirror pin over both `pressureControls.ts` and the `types.ts` unions) |
| §21 `POST /scene-suggestions` body; `GET`/`POST /scene-ideas` | 5, 6 |
| §23 FULL_SNAP, `test_llm_fakes` input, cassette, README | 3 |
| §24 parser discipline | 4 (`raw_suggestions`, drops, no raise) |
| §26 calendar unavailable | 2, 4, 5 (Review Focus 1, Decision 25), 6 (`test_a_damaged_read_never_hides_ideas`) |
| §27 old ideas never stale; tolerant clients; query form kept | 6, 8 (optional TS fields), 5 |
| §28.6 every bullet | 2 (ids), 0/3 (intent, instruction), 4 (drivers, anchor, dates, near/move, misses, alias, order, labels), 5 (precedence, 400s, 409), 6 (staleness, union) |
| §28.9 chooser bullets | 9 (read once/failure, body, basic matching, seed), 10 (cards, stale) |
| §28.10 cases 9–10 | 7 (`scene-suggestions` for `before`, `scene-suggestions-anchor-on` for derivation) |
| §3.10 / AC16 for chooser paths | 9 (`changing a control starts no generation`), 12 |
| §30 "Claims to address"; no "Embedding required" | 9, 10 |

**Deliberate deviations from the spec text:**

1. The drivers and controls addenda follow the byte-identical instruction section. The system message as a whole gains the drivers addendum whenever the campaign has drivers (Decision 1).
2. `build_snapshot(drivers=False)` keeps `upcoming` as the intent prompt's input. Only the suggestion snapshot drops it (Decision 2).
3. `drivers.snapshot` gains `pressure_result`, and `DRIVER_ACTIONS` / `ACTIONS_BY_KIND` live in `drivers.py` (Decisions 3, 24).
4. Canonicalization uses `effective.live_canon`, not `canon.canonical_refs` (Decision 4).
5. Anchor validity is membership in `anchors`. A temporal request anchor outside them is a 409, and a non-temporal one is a 400 (Decision 5).
6. A month-only birthday anchor takes only `on` (Decision 6).
7. The 400 body carries `reason`, and unknown enum values are 422s (Decision 7).
8. The snapshot adds `anchors`, `fixed` and `near_days`, and its rows carry `ref` and `dormancy` (Decision 9).
9. The snapshot's `timeline` holds every pressure item, and the rendered timeline is bounded with the index's rule, pinning `events.sooner`'s pick (`sooner_ref`) and the controls' refs (Decision 11).
10. `date_rejected` also marks `near`/`move` blanking. `next_date` is checked under `near`/`move`, and not under an anchor. An absent date is never rejected. With no `now`, the lower bound is skipped (Decision 12).
11. PC birthday drivers render in offscreen prompts (Decision 15).
12. Saved ideas (Decisions 16–18):
    - occurrence refs are written when well-formed (they are computed, so they have no record whose existence could be tested), and an anchor's relation is coerced on write;
    - occurrence refs are never dangling, and unknown (an unreadable ledger, no calendar) classifies as live;
    - labels for refs the store does not label are derived on read;
    - a fourth stale text is added;
    - a passed `after` anchor counts as live;
    - a deleted anchor is dropped without making the idea stale on its own;
    - the read adds `anchor_date`;
    - greeting rows are unchanged.
13. A seeded chooser holds its open call until the drivers read settles, and its disclosure opens. `ScenesView`'s adoption is new code, and waits for the scene list (Decisions 19, 20).
14. "Show every suggestion while any control is active" follows the batch on screen (Decision 22).
15. Card wording, where §16.4 does not fix it: `Doesn't claim to address <label>`, `Claims to address <label> (avoided)`, and "date not consistent with the time setting" for unanchored blanking.
16. The suggestion snapshot's calendar block, the suggestion parser and `date_normalizer` fail soft for a plugin that raises anything, not only `CalendarError` (Decision 25). The intent route and the saved-idea read degrade the same way. On a working calendar every output is byte-identical.
17. The batch anchor beats avoid (Decision 26).
18. The prompt index is ordered by `SORT_ORDER`, the §16.2 order, rather than B's decision precedence `PRESSURE_STATES` (Decision 9).
19. A saved idea anchored other than `on` a live occurrence opens the confirm form with an empty date, rather than the chooser's `nextDate` (Decision 23). §17.1 named only the live-`on` case.
20. §16.3 step 5's "shows which selections dropped" lasts until the next suggestion request goes out, and §16.5's seed note until the reader changes a control (Decision 19). A note that says "press Regenerate" is not left under the cards Regenerate brought.

**Open questions (none block execution):**

- Whether `DRIVER_PROMPT_CAP` is also the right bound for the rendered timeline (Decision 11). It shares the index's constant structurally (the same items reach the prompt by both routes); tune both against real prompts later.
- Hebrew month-only birthdays inherit Slice B's literal month-key match (B's Open Question). A month anchor in a leap year may therefore never appear. Slice E does not change it. This is inherited hand-off h2, recorded as deferred in the slice ledger (pre-Task-0 step 4).
- If Slice D declared continuity TS types first, Task 8 reuses them. The pre-Task-0 step 3 records which names were reused.

**Type consistency:**

- `Controls` and `NO_CONTROLS` (Task 3) are consumed by `driver_view` (3), `claim` / `check_date` / `order_cards` / `parse_output` (4), `resolve_controls` (5) and the eval case (7).
- `drivers.ACTIONS_BY_KIND` (Task 1) is consumed by `driver_view.actions` (3), `claim` (4), `idea_provenance` (6) and the TS mirror pin (8).
- The snapshot keys (Task 2, `sooner_ref` included) are the same set in `verify_templates` (3), `suggest_golden.SYSTEM_SNAPS` (0), the eval case (7) and every hand-built `_snap()` (3).
- The payload row keys (Task 4/5): `{title, premise, cast, location, date, date_friendly, in_days, date_rejected, drivers, time_anchor, unmet_must, avoided}`. This matches `SceneSuggestion`'s optional fields (Task 8), and `asDraft` (Task 10) sends `{ref, action}` and `{ref, relation}`, which `SceneIdeaCreate` (Task 6) accepts.
- The saved-idea read keys (Task 6), `drivers[].state`, `time_anchor`, `stale_reason` and `anchor_date`, match `SceneIdea`'s optional fields (Task 8), the picker's Stale group (10) and `savedDraft` (10).
- `ChooserSeed` (Task 8) is consumed by `NewSceneChooser.seed` (9) and `ScenesView` (11).
- `SceneSuggestionsOptions` (Task 8) is the hook's request (9) and `toRequest`'s return (8).

---

## Plan-gate review resolution

Every finding was checked against the tree, the spec and the B and C plans before it was resolved. Several reviewers raised the same defect, so the shared resolutions are stated once (R1–R12) and the table maps each finding to one of them.

**Shared resolutions:**

- **R1 — A calendar plugin that raises anything (verified).** The defect was confirmed in code. `_notation`'s `except` omits `RuntimeError`. `calendars.get_provider` runs `cls(config)` unguarded. `primary_provider` catches only `CalendarError`/`KeyError`, and `events.day_facts`, `birthdays.upcoming` and `date_normalizer` all resolve through it. Option (a) was taken: Decision 25 adds `_calendar_facts` and `_soft_provider` inside `suggest`, and `today_facts`, `day_facts`, `sooner` and `primary_provider` are not edited. On a working calendar the change is byte-neutral, which Task 0's golden proves. Under a raising plugin the expectation is now "kept, date blanked" (`""`, not rejected), never "date unchecked", because §26 says no date may be fabricated. `date_normalizer` also goes through `_soft_provider`, so the saved-idea read and the intent route degrade rather than fail. Changes: the Fail-soft constraint, Review Focus 1, Task 2 (`_calendar_facts`, plugin test), Task 4 (`_soft_provider`, `test_parse_survives_a_raising_plugin`), Task 5's expectation, deviation 16 and Task 13's §24/§26 amendment.
- **R2 — A month-only anchor's month (verified).** B's pressure items and `AnchorOption` carry no `year`/`month_key` (B Decision 4), and only the occurrence row does (`birthdays.py`, `ref = f"birthday:{actor}:month:{year}-{month_key}"`). The new `_month_of(ref)` (Decision 12) parses the ref, and Task 6 uses it for month order. Task 4 gains `test_month_of_parses_refs` and the store-level `test_month_only_on_against_a_real_campaign`.
- **R3 — "Patch to raise" pins.** They were vacuous where a broad catch swallows `AssertionError`, and wrong where the patched function is already on today's path (`validate_ideas` → `ref_validator` → `date_normalizer` → `primary_provider`). They are replaced by delegating call recorders with exact counts (Tasks 1, 2, 3 and 6). The `build_intent_prompt` half moves to Task 3, where it switches to `drivers=False`.
- **R4 — The captured-index pin.** It is now deterministic. Recorders are installed **before** the POST, and the test asserts exactly one `drivers.snapshot`/`pressure.build` call (Task 5). `draft_runs.post` settles the run, and `FakeOpenRouterComplete` answers at once, so the old patch-after-202 never ran before the payload was built. A recorder was chosen over `HeldOpenRouter`/`HeldCassette` because it needs no thread choreography, and it fails for a re-read whatever the timing.
- **R5 — Eval needles.** The eval case now follows `grade_prompt`'s documented contract (ids, keys and values only), and `grade_prompt_section` covers both addenda (Task 7). The sentence needles stay in Task 3's unit test, which pins §15.1/§16.3 directives the spec requires. That half of the finding is **rejected**: the harness rule is about evals, and each needle is written once, in Global Constraints. The two unpinned §15.1 directives were added there.
- **R6 — Saved-idea reads fail soft (verified).** `get_scene_ideas`' `_tolerant` answers `[]` on any exception, and `effective.records` raises on a garbled plot.json. Decision 17 now says: one `Ledgers` and `live_canon`, unknown classifies as live, and each idea is annotated inside its own guard. Task 6 gains `test_a_damaged_read_never_hides_ideas`.
- **R7 — Saved-idea writes test existence (verified against §17 and Appendix A T2).** An anchor is kept when its record exists, whether or not it is still an anchor, so a passed anchor is stored and reads "has passed". Occurrence refs are kept when well-formed. The relation is coerced, refs are de-duplicated, and stray `anchor` entries are dropped (Decision 16). No save reads `drivers.snapshot`.
- **R8 — Labels for stored occurrence refs.** They are derived on read (Decision 17, "Labels"): holiday name from the ref with `_bounded`'s digest shown as `…`; birthday from the actor's current name, else the id. The alternative, storing a label, is **rejected**: it adds a §17 storage field, and the read can answer it.
- **R9 — Hook hold.** `held` disables Suggest and makes `suggest()` a no-op. `suggest()` also sets `autoAsked` (Decision 19, Task 9 tests).
- **R10 — Anchor controls.** `chooseAnchor` defaults to the first anchor and forces `on` for a month anchor, and `toRequest` coerces as a last line of defence (Tasks 8 and 9). The batch anchor beats avoid on the server (Decisions 8 and 26), and the chooser disables Avoid on the anchored row. Rejecting the combination with a 400 was considered: the anchor is the more specific instruction, and a refusal would only add a path the UI must prevent anyway.
- **R11 — Summary count.** `steeredCount` counts drivers only, as §16.2 says, and `timeLabel` is shown separately. No spec deviation is needed.
- **R12 — Lint shapes (both verified with ruff on the repo config).** The import is `from .continuity import effective, pressure` beside the aliased line (`I001`). The sweep lambda is `lambda o=o:` (`B023`).

**Per-finding mapping** (the findings in the order given):

| # | Lens | Finding | Resolution |
|---|---|---|---|
| 1 | guards | Raising-plugin expectations contradict `build_snapshot` and the parser | Applied: R1 |
| 2 | guards | The hook's `ApiError` check fails under the existing `vi.mock` factories | Applied. Both factories spread `vi.importActual`, and a grep step catches other suites; `instanceof` is kept (Task 9 Step 1) |
| 3 | guards | `storyPressure.ts` vs `StoryPressure.tsx` on case-insensitive filesystems | Applied. Renamed to `pressureControls.ts` everywhere; Decision 21 says why |
| 4 | guards | The no-provenance test patches `primary_provider`, which is already called | Applied: R3 (`primary_provider` counted at exactly 1) |
| 5 | guards | "Patch to raise" tests pass vacuously | Applied: R3. Task 1's fixture now holds a dated event and a commitment due |
| 6 | guards | Separate `from .continuity import` lines fail `I001` | Applied: R12 |
| 7 | guards | Sweep lambda trips `B023` | Applied: R12 |
| 8 | guards | `test_payload_uses_the_captured_index` races | Applied: R4 |
| 9 | guards | Eval pins prose sentences as needles | Applied: R5 |
| 10 | spec | Raising-plugin tests vs "keep calendar semantics" | Applied: R1 (option a; the Fail-soft constraint rewritten) |
| 11 | spec | `check_date` reads `year`/`month_key` that items lack | Applied: R2 |
| 12 | spec | The no-provenance cost pin patches an existing call | Applied: R3 |
| 13 | spec | An anchor seed is never shown reaching the ranked call | Applied. `applySeed` maps it through `chooseAnchor`; there is a pure test and a chooser test (`an anchor seed sends anchor mode on the open call`) |
| 14 | spec | Switching to a month anchor keeps an invalid relation | Applied: R10 |
| 15 | spec | Avoiding the batch anchor is accepted and contradictory | Applied: R10 (the anchor wins; `test_the_anchor_beats_avoid`) |
| 16 | spec | The disclosure count includes the time setting | Applied: R11 |
| 17 | spec | Needles pin prose; two §15.1 directives unpinned | Partly applied: R5. The eval was moved to section checks and the two directives were added; sentence needles stay in the unit test (rejected half, with the reason in R5) |
| 18 | spec | The eval never exercises `on` derivation | Applied. A second case, `scene-suggestions-anchor-on`, with a derivation check; the README count goes up by two |
| 19 | spec | An annotation exception empties the Saved group | Applied: R6 (per-idea guard; garbage-ref test) |
| 20 | spec | The garbled-ledger canonicalization test passes regardless | Applied. Split into an alias-over-garble test and a `live_canon`-raises identity test |
| 21 | spec | "Unchanged keys" asserted for half the keys | Applied. `test_unchanged_keys_are_unchanged` covers all nine over non-empty values |
| 22 | behaviour | Raising plugin: today's code raises; no guards are planned | Applied: R1 ("kept, date unchecked" deleted) |
| 23 | behaviour | The write drops passed anchors, erasing staleness evidence | Applied: R7 (`test_a_passed_anchor_is_stored_and_reads_as_passed`) |
| 24 | behaviour | Read staleness is not fail-soft; `None` is unspecified | Applied: R6 |
| 25 | behaviour | Month-only check reads absent fields | Applied: R2 |
| 26 | behaviour | The captured-index test passes regardless | Applied: R4 (a recorder rather than `HeldCassette`; the reason is in R4) |
| 27 | behaviour | An invalid relation after an anchor switch gives a 400 | Applied: R10 (and a chooser test) |
| 28 | behaviour | A seeded chooser can make two paid calls | Applied: R9 |
| 29 | behaviour | A re-save over hand-edited provenance can 500 | Applied. `_union` cleans first (Decision 18); store test added |
| 30 | behaviour | The write does not validate the relation or de-duplicate | Applied: R7 (cases added to `test_write_drops_unknown_and_mismatched_refs`) |
| 31 | behaviour | An anchor in avoid flags every card | Applied: R10 |
| 32 | behaviour | `on` returns the stored, possibly non-canonical text | Applied. `provider.format(anchor["fixed"])` (Decision 12); `test_on_date_is_canonical` |
| 33 | behaviour | The capped index can cut due-soon drivers for passed events | Applied. The sort uses `SORT_ORDER` (Decision 9); the order test gains a passed event; deviation 18 |
| 34 | behaviour | The garbled-ledger test never reaches the fallback | Applied (as #20) |
| 35 | behaviour | No label source for saved occurrence refs | Applied: R8 (store-the-label option rejected) |
| 36 | ui | Suggest during a hold makes an unsteered call, then a second | Applied: R9 |
| 37 | ui | Anchor mode can send no anchor, or a bad relation | Applied: R10 (both chooser tests added) |
| 38 | ui | The annotation is not fail-soft; `None` classification unspecified | Applied: R6, plus R1 for the plugin case |
| 39 | ui | A canonicalized 409 ref can never be cleared | Applied. `pruneControls` after every read (Decision 19); `a refused ref spelled differently is still cleared` |
| 40 | ui | "No ideas came back" after a stale refusal | Applied. The hook's `stale` suppresses it, and the note gives the reason |
| 41 | ui | Chips read "in -3 days" / "in 1 days" | Applied. `whenPhrase` mirrors Decision 10 (singular added there too); chip test |
| 42 | ui | The summary counts the time as a driver | Applied: R11 |
| 43 | ui | Disabled Must / Choose anchor give no visible reason | Applied. Visible `field-hint` lines, asserted with `getByText` |
| 44 | ui | The "mode pick, Back, second pick" path does not exist | Applied. The path is spelled through Import's Back and the confirm form's Back |
| 45 | tests | Raising-plugin tests can't pass | Applied: R1 |
| 46 | tests | Month-only check reads absent fields | Applied: R2 (the real-campaign test as suggested) |
| 47 | tests | The captured-index test is a race | Applied: R4 (this finding's counter approach) |
| 48 | tests | The no-provenance test fails after a correct implementation | Applied: R3 |
| 49 | tests | The pinned-refs test expects the wrong "more" count | Applied. 43 threads + 1 commitment + 1 event = 45 drivers, so `more == 5`; the 41-pinned half uses 45 threads |
| 50 | tests | Task 1's pressure-result test is not discriminating | Applied: R3 (dated fixture, recorder count 0) |
| 51 | tests | The garbled-ledger test never reaches the identity fallback | Applied (as #20) |
| 52 | tests | Sweep lambda `B023` | Applied: R12 |
| 53 | tests | `asDraft` calls `.filter` on an optional field | Applied. `(s.drivers ?? [])`, and the anchor only when truthy |
| 54 | tests | No test pins avoid beating focus | Applied. `test_precedence_avoid_over_focus` |
| 55 | tests | `check_date`'s input is ambiguous | Applied. It takes a normalized native (Decision 12); the grader normalizes, then round-trips the **raw** string for notation (`test_suggest_friendly_form_fails_notation`) |
| 56 | tests | Saved-idea labels for computed refs have no source | Applied: R8 |
| 57 | tests | The disclosure summary counts the time mode | Applied: R11 |

### Second plan-gate round: gaps against the spec and the earlier slices

Each gap was checked against the plan text, the spec, the B, C and D plans and the tree before it was resolved. All six were real. The edits are made in place in the sections named, and are summarized here.

| # | Gap | Verified | Resolution |
|---|---|---|---|
| G1 | Hand-offs from earlier slices never reach an E task | Real. D plan Task 13 Step 8 tells D to list E hand-offs in its ledger, and C keeps a `## Slice D backlog` that D maps item by item, but no E step read the B, C or D ledgers. B already hands E three items (its "Bounded" anchors and Hebrew Open Questions, and its §20 row's "TS unions wait for Slice E"), plus the timed-event native. The B ledger names E today only in a task brief; the C and D ledgers do not exist yet. | Applied. Pre-Task-0 step 4 copies every item naming Slice E from the three ledgers into `## Inherited hand-offs` and gives each an owning task or a deferral reason. It is seeded with h1 (bound, Task 3), h2 (Hebrew month keys, deferred because the fix moves the byte-pinned intent prompt), h3 (TS unions, Task 8) and h4 (timed natives, Task 4). Task 13 Step 8 re-reads the ledgers and requires each item closed or explicitly deferred, and checks E's own hand-offs to F and G. |
| G2 | The rendered timeline is uncapped, against AC8's "bounded" and B's hand-off | Real. Decision 11 and deviation 9 left every unfired event and every deadline in the prompt, while the same temporal items are index rows capped at 40, and Task 13 Step 6 had no §15/AC8 entry. | Option (a), with one change. The snapshot's `timeline` keeps every pressure item (§15), and `driver_view` renders it with the index's rule (Decision 11): the controls' pinned refs (matched on `ref` or `subject`) and the `events.sooner` pick always render, the rest is ranked by `SORT_ORDER` and capped at `DRIVER_PROMPT_CAP`, rows keep snapshot order, and an "and N more dated items" line follows. The sooner pick is carried as a new snapshot key, `sooner_ref` (Task 2), so §15's sooner guarantee holds in the prompt and not only in the snapshot. **The change:** `HIGH_PRESSURE` items are ranked first rather than always rendered, and the full anchor-option list is not pinned (only the request's anchor is). Both would reopen the bound: every dated, non-passed event is an anchor option, and overdue items are bounded only by the ledger. Tests: `test_timeline_is_capped_but_keeps_sooner_and_anchors` (Task 3), the `sooner_ref` assertion in `test_timeline_contains_what_sooner_picks` (Task 2), a capped timeline in `verify_templates`' `CAPPED_SNAP`, and `sooner_ref: ""` in the Task 0 and `_snap` empties. There is also a Task 13 Step 6 §15/AC8 amendment, and deviation 9 and the Open Question are rewritten. |
| G3 | The read neither returns canonical refs nor de-duplicates merged ones | Real. Decision 17 classified from the canonical record but said nothing of the returned ref, and `scene_ideas._union` (which imports nothing from continuity) appends a canonical ref beside its pre-merge source. Task 6 tested only the write-side alias. | Applied. Decision 17's "Canonical on read" maps each stored ref through `live_canon`, returns the canonical ref with the canonical record's label and state, and de-duplicates (the first stored entry wins) before `stale_reason`. With `live_canon` unreadable, the mapping is the identity. Test: `test_read_canonicalizes_and_dedupes_a_merged_ref` (Task 6). The file keeps both refs, because reads never write. |
| G4 | The `types.ts` unions can drift from the Python tuples; `PressureState` is never tied to `PRESSURE_STATES` | Real. Task 8 declares each vocabulary twice, and the mirror test parsed only the const arrays and compared only `SORT_ORDER`. | Applied (the first fix). `test_ts_mirrors_the_python_tuples` also parses the literal unions in `api/types.ts`, including `AnchorOption.kind`. It pins each to its Python tuple, `PressureState` and `SORT_ORDER` as sets against `PRESSURE_STATES`, and fails by name on a missing union. Deriving the unions from the consts was rejected (Decision 21): `api/types.ts` would import from `components/`. |
| G5 | Recorded deviations missing from the spec amendment | Real. Deviations 3, 11 and 15 have no Task 13 Step 6 bullet. | Applied. There are now bullets for §14 (the `pressure_result` keyword), §14/§15 (offscreen keeps a PC's birthday driver while `token_ok` drops PC cast tokens) and §16.4/§30 (the exact warning and rejected-date wording), plus the G2 §15/AC8 bullet. |
| G6 | The disclosure's collapsed default and its place inside Generated are unpinned | Real. `pressureOpen` had no initial value, and no test checked the collapsed state or the position. | Applied. `pressureOpen` starts `false`, only an applied seed opens it, and the `cid` reset returns it to `false`. `onToggle` reads `currentTarget.open`, following `TrackerDisclosure`. Test: `story pressure is a collapsed disclosure under Direction`. Because the Generated group is a run of siblings between two `.role` headers rather than one element, "inside" is asserted in document order: after the `Generated` header and the `.idea-direction` row, before `Your own`. |

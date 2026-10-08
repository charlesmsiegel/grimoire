# Inference slice G — continuity decisions: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move absorb's duplicate check (`continuity-identity`) and the reconciliation sweep's adjudication (`continuity-reconcile`) onto slice F's `decide()`, each behind the offline eval gate, and flip the `continuity` route to `operation="decide"` and the Decision role.

**Architecture:**

- **Nothing in F's operation changes shape.** G adds item builders and answer mappings in `store/continuity/identity.py` and `store/continuity/reconcile.py`. It makes two additive contract amendments in `grimoire/decisions.py` (Task 1). It generalises the gate's `judge` to multi-item batches (Task 2). `inference.decide`, `_BACKENDS`, the resolver and the facade are untouched.
- **One item per row or per candidate, each self-contained.** Each item's `context` carries everything that row or candidate needs, because H's native backends send one request per item. The reconcile date and chronicle lines repeat per item. Structured calls chunk at `MAX_ITEMS_PER_CALL = 8`, which is F's contract and is not changed here.
- **Today's trust rules stay where they are.** A parsed identity item becomes today's `{row, decision, id, reason}` dict and goes through the unchanged `Examination.decide` acceptance guard. A parsed reconcile item becomes today's reply element and goes through the unchanged `reconcile._decide`. So the direction, evidence, liveness and "already moved" rules are the same code before and after.
- **Two prepare tasks, one switch, one retirement.**
  - Each conversion is prepared with no behaviour change (Tasks 3, 4).
  - The switch (Task 5) converts **both** call sites and flips the route in one commit. The two tasks share the `continuity` route, and the safety rule flips a route only in the change whose call sites decide.
  - Task 6 then deletes the dead legacy prompts, parsers and eval cases.

**Tech Stack:** Python ≥3.11, FastAPI, Jinja2 templates, pytest, and the offline eval suite under `evals/`. No frontend change: the Models page lists the routes that resolve through Decision from the settings view (F fix wave, CODE-M3), so `continuity` appears there once it flips.

**Spec:** `docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md`

- Slice G is §14 row G and the "Safety rule across slices".
- It draws on §5.3–§5.5 (the decide skip and its same-provider reach), §7.4 (contract, structured backend, the continuity rows of the conversions table, the eval gate), §9.4, §12 ("Decide question unanswerable"), §14.1 and §15 "Decide".
- Rows H and I are left as they are: H adds the native backend behind the same `decide` signature; I retires `STRUCTURED_KEY` and the lowering.
- F's plan, `docs/superpowers/plans/2026-10-08-inference-slice-f-decide.md`, defines the contract G builds on ("How G and H extend this").
- Task 0 amends the spec for the rulings at the end of this plan.

**Base.** The branch `claude/inference-slice-g` sits at `b2c85c6`, which is F's head before F's final fix wave. **Before Task 1, rebase onto F's final head.** The plan assumes these fix-wave items are present (see `.superpowers/sdd/2026-10-08-inference-slice-f-decide/final-fix-rulings.md` in the slice-f worktree):

- the decide skip reaching a same-provider fallback (`6cfff01`; `inference_fixtures.SAME_PROVIDER` and `decide_only(..., on=)`);
- the speaker's 409 hoisted before any write (`c08bb4e`);
- the same-attempt retry without structured mode after a schema refusal;
- no health mark for a schema refusal;
- the answers-level-dropped shape `{"0": {"over": true}}` read as flattened under its key (CODE-M8);
- the recursive `settle` guard (CODE-M7);
- the corpus entry `ruling` field, printed by the gate report (SPEC-M-5 / CODE-M6);
- the Decision card listing the routes that resolve through Decision (CODE-M3).

If any of these is missing at rebase time, stop and report it.

## Global Constraints

**The user's rules:**

- **Never run the full test suite, and never `make check` or `make check-py`.** Each task runs only the test files it names, plus `make check-lint`, `make check-mypy` and `make check-templates`. CI runs the full suite. **Merging waits for CI to be green.**
- **Never spend money without asking.** No live LLM call anywhere. `evals/run.py --live` and `--record` are never run without the user's explicit approval, in this slice or after it. The gate (`--gate`) is offline only.
- **Codex gates:** Claude stand-in reviews serve as the plan, per-task and final gates until the PR. On the PR, Codex reviews (`/codex:review` and the final `/codex:adversarial-review`).

**Spec rules that bind here:**

- **Safety rule (§14):** `continuity` flips to `operation="decide", default_role="decision"` only in Task 5, the commit whose two call sites call `decide()`. `test_operation_guard.py`'s safety half fails any earlier flip.
- **Rule 3, no behaviour change on resolution:**
  - `backend/tests/fixtures/inference_baseline.json`, `inference_baseline_c.json` and `test_lore_golden`'s golden are **never regenerated**.
  - With Decision unset, the flipped route inherits Fast → Primary and resolves as before. Both equivalence sweeps stay green, and G adds no `NEW_TASKS` entry.
- **§12:** an unanswerable question is `answer: None` plus a `reason`, never a guessed default. Each call site keeps today's safe direction:
  - **identity:** a row the check did not answer stages as new, with hints (`unchecked` / `hint_only`);
  - **reconcile:** a candidate the model did not answer gets no proposal, and a reply with nothing readable fails the run while persist 1's findings stand.
- **Rule 5:** a price nobody reported is never rendered as zero. `decide` already meters each chunk, and G adds no number to any holder.
- **Soft all the way down:** the duplicate check never fails an absorb, and the sweep never fails for want of a model. G adds **no request-time refusal**: both resolutions stay soft (`_soft_resolved`), so nothing is refused after a write.

**CLAUDE.md rules this slice touches:**

- **Imports:**
  - all at module scope, and the graph stays acyclic;
  - `identity.py` and `reconcile.py` import `from ... import decisions, prompts` (a store module may import the gateway leaf, as `response_protocol` does);
  - `routes/continuity.py` binds `from .. import decisions, inference as operations` (F's Minor 9: the name `inference` is taken in routes).
- **Adding an LLM call site:**
  - `require_inference("<literal task>", cid, operation="decide")`, inside the thunk `_soft_resolved` takes;
  - `operations.decide("<literal task>", items, client=client, resolved=resolved, ...)`.
- **Failures are instrumented at `usage.Meter.done`:** `decide` opens one meter per chunk, and each caller's time limit runs inside it through `around`.
- **Detached runs:**
  - the sweep is a `background` run, at most one live per campaign;
  - persist 1 and persist 2 keep their lock, fence and revision rules (`reconcile.persist_found` and `persist_proposals` stamp the token where they write; G does not touch either);
  - absorb's identity phase stays inside the review run, and its rows are written only by the review save.
- **Keep the event loop clear:** every template render and store read G adds runs in the threadpool (`run_in_threadpool`). `decide` renders its own messages off the loop already.
- **Faking the LLM:** only through `backend/tests/llm_fakes.py`. Decide replies are written only through `llm_fakes.decision_reply`. Decision-role stores come from `tests/inference_fixtures.py` (`format2`, `decide_only(client, fallback=, on=SPARE | SAME_PROVIDER)`).
- **Prompt text lives in `templates/`**, checked by `scripts/verify_templates.py`. `templates/README.md` names real builders (`test_docs_guard.py`).
- **Privacy:**
  - invented names only: Seraphine, Mara, Winifred, Realm, Saltmarch, Rowan, Tobin;
  - no store measurement informs a constant; G adds no constant that needs one.
- **Lint gates are ratcheted:** when a count shrinks, run `make baseline PY=$PY` and commit the smaller file with the fix.
- **Linear history:** rebase and `--ff-only`, never a merge commit.

**Commands** (the worktree has no venv of its own; pass the main checkout's):

- `PY=/home/user/grimoire/backend/.venv/bin/python`
- Backend tests: `cd backend && PYTHONPATH=src $PY -m pytest tests/<file> -q`
- Gates: `make check-lint PY=$PY`, `make check-mypy PY=$PY`, `make check-templates PY=$PY`
- The gate CLI: `$PY evals/run.py --gate` (offline). Eval replay runs inside `tests/test_evals.py`.

## Review Focus

1. **More rows or candidates than one call carries.** An absorb whose extraction proposes nine examined rows, or a sweep that selects 24 candidates, is answered in chunks of 8, one metered row per chunk.
   - A failed chunk beside an answered one leaves only its own rows `unchecked` (phase `degraded`, not `failed`), or only its own candidates without a proposal (the sweep lands; they are asked again next sweep).
   - Tests: Task 5 `test_identity_meters_one_row_per_chunk`, `test_a_failed_second_chunk_leaves_the_first_chunks_rows_decided`, `test_a_failed_chunk_keeps_the_other_chunks_proposals`.
2. **The absorb budget running out between chunks.** The second chunk is refused with `BudgetRefused`, which is an `LLMError` and so is that chunk's `error`. The first chunk's decisions stand, the rest stay `unchecked`, and `budget_exhausted` is set.
   - Test: Task 5 `test_the_budget_running_out_between_chunks_leaves_the_rest_unchecked`.
3. **A ledger a person hand-edited.** Two candidate ids, or two scene ids, that read as one once normalised (`the-map` and `the map`) must never become a request `decide` refuses; that would fail the phase.
   - The builder drops the lower-ranked one from that item's options and context.
   - Tests: Task 3 `test_build_items_drops_a_candidate_whose_id_collides_once_normalised`; Task 4 `test_build_items_drops_a_scene_whose_id_collides_once_normalised`.
4. **A model answering the decide prompt in today's format** (`{"decisions": [...]}` from an unstructured fallback).
   - It holds no item, so every row is `unchecked` (`degraded`, "answered none") and no candidate gets a proposal. That is the safe direction, never a guessed decision.
   - Tests: gate entries `todays-format` in both corpora (Tasks 3, 4); Task 5 `test_a_reply_in_todays_format_answers_no_row`.
5. **A decide-only Decision model**, on another provider's fallback or on the same provider's (a single OpenRouter account).
   - Both call sites answer on the fallback, and nothing is sent to the decide-only model.
   - With no generating fallback, the identity phase reports `failed` with the `incapable` sentence and the absorb lands. The sweep lands with `llm: "off"` and that reason, and persist 1's findings stand.
   - Tests: Task 5 `test_identity_on_a_decide_only_model_answers_on_the_fallback[spare|same_provider]`, `test_identity_without_a_generating_fallback_fails_the_phase_not_the_absorb`, `test_the_sweep_on_a_decide_only_model_answers_on_the_fallback[spare|same_provider]`, `test_the_sweep_without_a_generating_fallback_lands_with_llm_off`.
6. **A provider refusing the strict schema** on the only attempt. F's same-attempt retry without structured mode answers, and the rows are decided. The ledger holds two rows for that chunk.
   - Test: Task 5 `test_a_refused_schema_still_decides_the_rows`.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| the spec | rulings below folded in | 0 |
| `backend/src/grimoire/decisions.py` | `NO_OBJECT` / `NO_ITEM` details; a choice that allows none may offer one option | 1 |
| `evals/gate.py`, `evals/README.md`, `backend/tests/test_decide_gate.py` | `judge` over multi-item batches; `Conversion.decide` takes the whole parse | 2 |
| `store/continuity/identity.py`, `templates/continuity_identity/{item,question,option,record,explain}.j2` | `build_items`, `explain`, `answers_of`, `take`, `UNREADABLE` | 3 |
| `evals/legacy.py` | frozen `identity_parse_output` (+ helpers, `extract_object` copy) | 3 |
| `evals/gate/continuity-identity.json`, `evals/cases.py`, `evals/graders.py`, `evals/recordings/decide-continuity-identity.*` | identity gate corpus and eval case | 3 |
| `store/continuity/reconcile.py`, `templates/continuity_reconcile/{item,question,direction,direction_to,evidence,explain}.j2` | `build_payload` gains `recent`; `build_items`, `item_scenes`, `explain`, `proposals_of` | 4 |
| `evals/legacy.py`, `evals/gate/continuity-reconcile.json`, `evals/cases.py`, `evals/graders.py`, `evals/recordings/decide-continuity-reconcile.*` | reconcile frozen parse, gate corpus, eval case | 4 |
| `scripts/verify_templates.py`, `templates/README.md` | item checks, carried fragments, coverage, options tied to ids, reworded table | 3, 4, 6 |
| `routes/scenes.py`, `routes/continuity.py`, `store/routing.py`, tests, `fixtures/llm/campaign_flow.json`, `tests/review_runs.py` | both call sites switched; route flipped | 5 |
| `identity.py`, `reconcile.py`, legacy templates, legacy eval cases, graders and recordings, legacy parse tests | retirement | 6 |
| `CLAUDE.md`, `templates/README.md`, `evals/README.md`, module docstrings | docs | 7 |

---

### Task 0: Settle the spec, and rebase

**Files:** Modify `docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md` (§7.4 contract and conversions table, §9.4, §14 row G).

- [ ] **Step 1:** Rebase `claude/inference-slice-g` onto F's final head. Confirm each fix-wave item listed under **Base** is present. If one is missing, stop and report.
- [ ] **Step 2:** Fold rulings 1–16 (end of this plan) into the spec, one line each:
  - §7.4 contract:
    - a `choice` with `allow_none` may offer a single option (ruling 2);
    - `unreadable` may carry `no_object` or `no_item` as well as `not_an_option` (ruling 3).
  - §7.4 conversions table, the `continuity-identity` row (rulings 1, 7, 8, 9, 16).
  - §7.4 conversions table, the `continuity-reconcile` row (rulings 4, 5, 6, 7, 8, 9, 16).
  - §7.4 eval gate (rulings 10, 11).
  - §9.4: the two continuity decisions gain no capture (ruling 14).
  - §14 row G: settled, with ruling 12 (both switch in one change) and ruling 13 (no request-time refusal).
  - Before writing, re-read §7.4 as F left it. F's fix wave amended it, so place each line beside F's text and do not restate it.
- [ ] **Step 3:** Commit `docs(spec): settle slice G's continuity decisions`.

---

### Task 1: Two additive contract amendments

**Files:**
- Modify: `backend/src/grimoire/decisions.py`
- Test: `backend/tests/test_decisions.py`

**Interfaces:**
- Produces:
  - `NO_OBJECT = "no_object"`: the reply held no JSON object at all (`find_object` returned None). It is set on every answer of every item in that reply.
  - `NO_ITEM = "no_item"`: the reply held an object, but nothing in it reads as this item: no index key, no unwrapped or flattened read applies, the entry is not an object, or its `answers` is not an object. It is set on every answer of that item.
  - A question missing from an item that *was* read, and a `null` on a choice without `allow_none`, keep `detail == ""`, as today.
  - `MIN_OPTIONS_WITH_NONE = 1`. `_check_choice` bounds options at `MIN_OPTIONS_WITH_NONE if q.allow_none else MIN_OPTIONS` to `MAX_OPTIONS`. A one-option nullable choice has two possible answers (the option, or null). The schema for it is `{"anyOf": [{"type": "string", "enum": [id]}, {"type": "null"}]}`, from the existing code path.
- `Answer.__post_init__` already allows a detail only beside `unreadable`. The docstring and the module docstring name the three details; none is a reason.

- [ ] **Step 1: Write the failing tests** (`tests/test_decisions.py`):
  - `test_a_reply_with_no_object_marks_every_answer`: for `"no json here"`, `""` and `"[]"`, over two items, every answer is `Answer(None, "unreadable", detail=decisions.NO_OBJECT)`.
  - `test_an_item_the_object_does_not_hold_is_no_item`: over two items, `'{"0": {"answers": {"over": true, "who": null, "tone": 1}}}'` gives item 1's every answer `detail == NO_ITEM`. For `'{"0": 5}'`, item 0 is `NO_ITEM`. For `'{"0": {"answers": []}}'`, item 0 is `NO_ITEM`. For `'{}'`, every item is `NO_ITEM`.
  - `test_a_question_missing_from_a_read_item_has_no_detail`: `'{"0": {"answers": {}}}'` gives `Answer(None, "unreadable")`.
  - `test_a_choice_that_allows_none_may_offer_one_option`:
    - `validate` accepts `Choice("id", "i", (Option("find-the-ledger", "Find the ledger"),), allow_none=True)`;
    - its schema is `{"anyOf": [{"type": "string", "enum": ["find-the-ledger"]}, {"type": "null"}]}`;
    - parse reads `"find-the-ledger"` as that id, and `null` as `abstained`.
  - `test_a_choice_without_none_still_needs_two`: one option without `allow_none` raises `DecideRequestError`; so does zero options with it.
  - Update these to name the new details, rather than delete them:
    - `test_parse_a_missing_item_is_unreadable` (`NO_ITEM`);
    - `test_parse_of_none_is_unreadable` (`NO_OBJECT`);
    - `test_unreadable_answers_carry_a_reason` (its `"[]"` / `"no json here"` loop: `NO_OBJECT`);
    - `test_parse_a_list_holding_two_objects_is_unreadable` (whichever detail the code gives; a list of two objects is not one object, so `NO_OBJECT`);
    - `test_validate_bounds` (1 option without none raises; with none passes).
- [ ] **Step 2:** Run `tests/test_decisions.py`: FAIL.
- [ ] **Step 3:** Implement. `parse` sets `NO_OBJECT` when `obj is None`. `_item_object` returns a sentinel for "no item", which `parse` turns into `NO_ITEM` answers.
- [ ] **Step 4:** Run `tests/test_decisions.py tests/test_decide_gate.py tests/test_responses.py tests/test_voice_drift_store.py tests/test_scene_break_store.py tests/test_inference_decide.py tests/test_eval_graders.py tests/test_evals.py`. All must PASS, and the three F gates are unchanged:
  - `selection_of` maps any non-`not_an_option` None to `INVALID_HANDOFF`;
  - `finding_of` maps None to `UNKNOWN`;
  - `verdict_of` maps None to no break.

  Then `$PY evals/run.py --gate`, and `make check-lint check-mypy PY=$PY`.
- [ ] **Step 5:** Commit `feat(decisions): say why an item went unread, and let a nullable choice offer one option`.

---

### Task 2: The gate judges multi-item batches

**Files:**
- Modify: `evals/gate.py`, `evals/README.md` ("The decide gate")
- Test: `backend/tests/test_decide_gate.py`

**Interfaces:**
- `Conversion.decide: Callable[[tuple[decisions.ItemResult, ...], Entry], object]`. It is handed the whole parse, one `ItemResult` per item, in order. F's three conversions read `results[0]`, so `_break_decide`, `_drift_decide` and `_speaker_decide` change their first parameter and nothing else.
- `judge`:
  - drops the one-item refusal;
  - refuses with `ValueError(f"{conv.id}: the gate scores one call's batch; this one needs {n} calls")` when `len(decisions.chunks(items)) != 1`, because a corpus reply answers one call;
  - parses with `parsed = decisions.parse(entry.decide, items, explain=conv.explain)` and calls `conv.decide(parsed, entry)`.
- Everything else in `judge` is unchanged: `settle` (and its guards), JSON-text comparison, distinctness, and the `ruling` reporting from F's fix wave.
- `evals/README.md`: the gate's batch is the conversion's fixture items, at most one call's worth. A corpus reply answers the whole batch, keyed by item index, as a structured call does. The "one item per conversion" limit that F's fix wave documented is lifted.
- Rejected: per-entry items (each entry naming its own fixture). Legacy replies answer a whole batch (`{"decisions": [...]}` across rows or candidates), so a batch fixture is the shape both sides share.

- [ ] **Step 1: Write the failing tests:**
  - `test_judge_scores_a_batch_of_several_items`: a planted conversion of three items, whose `decide` returns `[r.answers[q].answer for r in results for q in r.answers]`, and a two-entry corpus. Both sides are scored whole, and a regression on item 2 alone is reported.
  - `test_judge_refuses_a_batch_that_would_not_fit_one_call`: nine one-predicate items raise `ValueError` naming the call count.
  - Delete `test_judge_refuses_a_conversion_of_several_items`.
  - Update the direct `conv.decide(parsed, entry)` calls in `test_scene_break_decide_reads_every_entry`, `test_voice_drift_decide_reads_every_entry` and `test_speaker_decide_reads_every_entry` to pass the tuple.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `tests/test_decide_gate.py tests/test_evals.py`, `$PY evals/run.py --gate` (the three lines unchanged), then `make check-lint check-mypy PY=$PY`.
- [ ] **Step 5:** Commit `feat(evals): the gate scores a conversion's whole batch`.

---

### Task 3: The duplicate check, prepared (no behaviour change)

**Depends on:** Tasks 1 and 2.

**Files:**
- Modify:
  - `backend/src/grimoire/store/continuity/identity.py`
  - `backend/src/grimoire/routes/scenes.py` (`_resolve_identity` uses `take`; `_IDENTITY_UNREADABLE` becomes `continuity_identity.UNREADABLE`)
  - `evals/legacy.py`, `evals/gate.py`, `evals/cases.py`, `evals/graders.py`
  - `scripts/verify_templates.py`, `templates/README.md`
- Create:
  - `templates/continuity_identity/item.j2`, `question.j2`, `option.j2`, `record.j2`, `explain.j2`
  - `evals/gate/continuity-identity.json`
  - `evals/recordings/decide-continuity-identity.{compliant,undecodable,merged,unknown-id}.json`
- Test: `tests/test_continuity_identity.py` (additions), `tests/test_decide_gate.py`, `tests/test_eval_graders.py`, `tests/test_evals.py`

**Interfaces** (all in `identity.py`):

- **Constants:**
  - `DECISION_ID = "decision"`;
  - `RECORD_ID = "id"`, named as today's reply field, so the carried sentences keep their words;
  - `UNREADABLE = "the duplicate check returned no readable answer"` (moved, value unchanged).
- **`build_items(rows: list[dict], live: Mapping[str, str]) -> tuple[decisions.Item, ...]`**: one item per `prompt_rows()` row, in order. It is pure but renders, so callers run it in the threadpool. Each item:
  - **`context`** is `render("continuity_identity/item.j2", r=<the row as template_rows shapes it>)`:
    - a heading `Proposed plot thread: <title>` (or `Proposed commitment: <title>`);
    - then today's row block from `user.j2`, byte for byte: Beat, Status, Kind, Due, Cited, Why new, Distinguished from, `Candidates:` with each line, earlier beats and signals.
    - `item.j2` is standalone; today's `user.j2` is left untouched until Task 6.
  - **`Choice(DECISION_ID, render("continuity_identity/question.j2"), options=tuple(Option(w, render("continuity_identity/option.j2", decision=w)) for w in DECISIONS))`**, with no `allow_none`.
  - **`Choice(RECORD_ID, render("continuity_identity/record.j2"), options=..., allow_none=True)`**, with one option per offered candidate, in rank order:
    - `id` is the candidate's bare id;
    - `description` is its clipped title, or its id when the title is blank;
    - `aliases` are `f"{kind}:{id}"` plus, for every `src, canon` in `live.items()` with `canon == f"{kind}:{id}"` and `src != canon`, both `src` and its bare id. These are the spellings today's `_existing` canonicalises.
  - **Collision rule** (Review Focus 3):
    - Walk the row's candidates in rank order, keeping the set of normalised spellings seen.
    - A candidate whose id collides is dropped from that item's options **and** from the row its context renders.
    - An alias that collides is dropped alone.
    - `decisions.validate(build_items(...))` never raises.
- **`explain() -> str`** renders `continuity_identity/explain.j2`.
- **`answers_of(rows: list[dict], results: Sequence[decisions.ItemResult]) -> list[dict] | None`** maps each read item to today's decision dict, for `Examination.decide`:
  - Returns `None` when **no** item was read, that is, every item's decision answer has `detail == NO_OBJECT` or `reason == "error"`. That is today's undecodable reply.
  - Otherwise, per item `i`, an item whose decision answer is `NO_ITEM` or `error` is left out, so `Examination.decide` gives it `NO_ANSWER`.
  - Every other item gives `{"row": rows[i]["key"], "decision": <the answer, or "" when None>, "id": <the id answer when it is a str, else "">, "reason": result.rationale[:REASON_CHARS]}`.
  - `Examination.decide` already turns a word outside `DECISIONS` into `uncertain`, and an `existing` with an id that was not offered, not live, or already moved into a downgrade.
- **`take(exam: Examination, answers: list[dict] | None) -> bool`**:
  - `None` → `exam.hint_only(UNREADABLE)`, returns False;
  - otherwise → `exam.decide(answers)`, returns True.
  - `_resolve_identity` now calls `take(exam, continuity_identity.parse_output(reply))` and sets `block` failed with `UNREADABLE` when it is False. That is the same behaviour as before.

**Templates** (move text out of `continuity_identity/system.j2`; invent no criterion). The checks are in `verify_templates.py`, below.

| Fragment of `continuity_identity/system.j2` | Goes to |
|---|---|
| "A plot thread is an open narrative question; a commitment is an obligation someone owes (a promise, a threat or a piece of foreshadowing)." | `question.j2` |
| the opening's first two sentences, reworded (see the reworded table) | `question.j2` |
| the "A closed or resolved candidate is never "existing" …" paragraph, whole | `question.j2` |
| each decision bullet's text after the quoted word: existing (up to "forward."), new, uncertain | `option.j2`, selected by `decision` |
| "Give that candidate's "id" exactly as it is listed." | `record.j2` |
| "one short sentence" | `explain.j2` |

**Reworded table** (`IDENTITY_REWORDED`, each entry `(old, new, target, why)`):
- "A scene was just absorbed, and the extraction proposed opening some NEW plot threads or commitments." → "A scene was just absorbed, and the extraction proposed opening a NEW plot thread or commitment." (`question.j2`). Why: one item asks about one row.
- "Each proposed record is listed below as a row: its title, … that put it on the list." → "The proposed record is shown above: its title, … that put it on the list." (`question.j2`). Why: `decide/user.j2` renders the context above the questions.
- "Leave "id" empty unless the decision is "existing"." → "Answer null for "id" unless the decision is "existing"." (`record.j2`). Why: an empty string is not an option; null is.

**Excluded as format** (owned by `decide/system.j2`):
- "Give exactly one decision per row:";
- the "Reply with ONLY a JSON object, no prose around it:" line and its example;
- the sentence beginning `"row" is the key after "Row"`.

**`verify_templates.py`** (a new "continuity identity as decision items" section, after today's identity section):
- **Items against direct renders:** for each existing identity fixture row set (`_identity_row`/`_identity_candidate`), `build_items` contexts, instructions and option descriptions equal direct renders.
- **The body is today's row:** while `user.j2` exists, each context with its heading line removed equals today's `user.j2` render of that one row with its `Row … — proposed …` line removed.
- **Carried fragments:** `IDENTITY_CARRIED`, each in its target's `_source`.
- **Options tied to ids:** `IDENTITY_OPTIONS = {word: text}`, the three bullet texts. Each must equal `render("continuity_identity/option.j2", decision=word)`, and its keys must equal the built item's decision option ids, in order. A description moved to another word fails, as voice drift's does.
- **Reworded:** for each `IDENTITY_REWORDED` entry, `new` is in its target's source and `old` is in the legacy source.
- **Coverage, while the legacy template exists:**
  - Re-add F's `_leftover(source, fragments)` and `_GLUE_WORDS` (from F's commit `102aeac`) as shared helpers.
  - Every sentence of `continuity_identity/system.j2` is covered by `IDENTITY_CARRIED`, the `IDENTITY_OPTIONS` texts, the `old` side of `IDENTITY_REWORDED` and `IDENTITY_FORMAT`.
  - **Coverage that bites:** for each carried, option or reworded fragment `f`, the same coverage with `f` removed leaves words. One `REPORT.require` per fragment, so a fragment that coverage would not miss fails `make check-templates`.

**Frozen legacy parse** (`evals/legacy.py`):
- Add a verbatim copy of `absorb/parse.py:extract_object`.
- Add `identity.parse_output` with `_ROW_WORD`, `_row_key`, `_decision`, `DECISIONS` and `REASON_CHARS`, renamed with an `identity_` prefix where reconcile's copy (Task 4) shares the name. Bodies change only for those renames.
- The module docstring lists what was copied, and from which commit.
- `test_legacy_imports_nothing_from_grimoire` allows `re` beside `json` and `__future__`.

**Gate** (`evals/gate.py`, `CONTINUITY_IDENTITY`):
- **Fixture.** `_identity_exam() -> identity.Examination` builds a fresh in-memory `Examination` from `similarity.subject` records, with no store. It must hold:
  - `r1` (thread "Recover the harbour ledger"): candidates `find-the-ledger` (open) and `maras-map` (open), where `maras-map` is the canonical of the alias source `the-old-map` in `live`;
  - `r2` (thread "Recover the harbour ledger again"): candidate `find-the-ledger`;
  - `r3` (commitment "Seraphine's midnight deadline"): one candidate, `the-midnight-deadline`;
  - a closed candidate `the-burned-chart` on `r1`;
  - `targets = {("thread", "winifreds-chart")}`, an explicit row's target that is also offered to `r2`.
- **Outcome:** `settle(answers)` runs a fresh `_identity_exam()` through `identity.take`, then gives `[taken, [[e.decision, e.status, e.reason, e.target] for e in exam.rows]]`.
  - legacy side: `legacy.identity_parse_output(text)`;
  - decide side: `identity.answers_of(exam.prompt_rows(), results)`;
  - items: `identity.build_items(exam.prompt_rows(), exam.live)`;
  - `explain=True`.
- **`legacy_cases`**, verbatim from `test_continuity_identity.py`'s parse tests:
  - `"I think so."`, `"{}"`, `'{"decisions": 3}'`;
  - the four `json.dumps({"decisions": [{"row": label, "decision": "new"}]})` for `"Row r1"`, `" R1 "`, `"r1"` and `"ROW R1"`;
  - the fenced reply of `test_parse_normalizes_and_never_raises`, built by the same expression as the test.
- **Each legacy case gets a decide twin of the same shape:**
  - garbage stays garbage;
  - `{}` stays `{}`;
  - `{"decisions": 3}` becomes `{"0": 3}`;
  - a label case becomes `decision_reply`-shaped `{"0": {"answers": {"decision": "new", "id": null}}}`;
  - the fenced reply becomes a fenced decide reply with `" find-the-ledger "`, `"EXISTING"`, `"maybe"`, `7`, `null`, a 400-character rationale and a list rationale in the same places.
- **Added shapes**, each a legacy reply and its twin:
  - existing on an unoffered id (`nope`), on another kind's ref (`commitment:find-the-ledger`), on a closed candidate, on the alias source (`the-old-map`) and on the ref form (`thread:maras-map`);
  - two rows naming `find-the-ledger` (the second is downgraded);
  - existing on the explicit target;
  - existing with no id;
  - `r3` existing on its sole candidate;
  - `todays-format` (decide side only: today's reply to the decide prompt; intended: every row unchecked, `taken` true; legacy side is the same reply to today's prompt, which reads).
- **Ruling:** the `todays-format` entry's legacy side reads the rows while the decide side reads none. Mark it `"ruling": "a reply ignoring the decide schema is never guessed at"`, so the report prints it (F fix wave).

**Eval case** `decide-continuity-identity` (`task="continuity-identity"`, `schema` from `decisions.schema(items, explain=True)`):
- **build:** today's `build_continuity_identity`, unchanged.
- **prompt:** `_decide_identity_prompt(ctx)` examines as `_identity_prompt` does, then `inference.structured_messages(build_items(exam.prompt_rows(), exam.live), explain=explain())`. It stores the same `expected`, `offered` and `kinds` on `ctx`.
- **grade** (`graders.grade_identity_decision(output, items, rows, expected)`):
  - `identity.json`: `decisions.find_object(output)` is not None; when it is None the other output checks are not reported.
  - `identity.covers_rows`: no item is `NO_ITEM`.
  - `identity.enum`: no decision answer is `not_an_option`.
  - `identity.known_ids`: every `existing` has a read id.
  - The verdicts per expected row, named as today (`identity.same_obligation`, `identity.distinct`, `identity.continuation`), read from `answers_of`.
  - The prompt checks: `prompt.question` (`grade_prompt_section` for `question.j2`), `prompt.schema`, `prompt.context` (each examined row's title is in the user message) and `prompt.explain`.
- **Recordings:** each carries today's recording's failure mode into the decide shape.
  - `compliant`;
  - `undecodable` → `("identity.json",)`;
  - `merged` → `("identity.distinct", "identity.continuation")`;
  - `unknown-id` → `("identity.known_ids", "identity.same_obligation")`.

- [ ] **Step 1: Write the failing tests:**
  - `test_continuity_identity.py`:
    - `test_build_items_one_per_row_asking_decision_then_id`
    - `test_a_single_candidate_row_offers_one_id_and_null`
    - `test_the_id_question_reads_alias_sources_and_ref_forms`: the alias source `a` and `thread:b` both parse to `b`.
    - `test_build_items_drops_a_candidate_whose_id_collides_once_normalised`: hand-made `the-map` and `the map` produce one option and one `Candidates:` line, and `validate` passes.
    - `test_answers_of_maps_each_answer_to_todays_decision_shape`
    - `test_answers_of_is_none_only_when_nothing_was_read`
    - `test_a_row_the_reply_never_reached_is_left_out`
    - `test_take_hint_onlys_an_unreadable_reply`
  - `test_decide_gate.py`:
    - `test_the_identity_copy_answers_as_production_does`: every legacy case through `legacy.identity_parse_output` equals `identity.parse_output`. This test is deleted in Task 6.
    - add `grimoire.store.continuity.identity` and `grimoire.store.absorb.parse` to `_PRODUCTION`.
  - `test_eval_graders.py`: `grade_identity_decision` on each recording's shape.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Implement, with the `verify_templates.py` section and the `templates/README.md` entries for the five new templates.
- [ ] **Step 4:** Run `tests/test_continuity_identity.py tests/test_absorb_identity.py tests/test_decide_gate.py tests/test_evals.py tests/test_eval_graders.py tests/test_docs_guard.py tests/test_import_guard.py`, then `$PY evals/run.py --gate` (expect `continuity-identity … PASS`), then `make check-lint check-mypy check-templates PY=$PY`. **Do not start Task 5 unless this gate passes.**
- [ ] **Step 5:** Commit `feat(continuity): the duplicate check as decision items, and its gate`.

---

### Task 4: The reconciliation sweep, prepared (no behaviour change)

**Depends on:** Tasks 1 and 2. Run it after Task 3: both append to `evals/legacy.py`, `gate.py`, `cases.py`, `graders.py` and `verify_templates.py`.

**Files:**
- Modify:
  - `backend/src/grimoire/store/continuity/reconcile.py`
  - `evals/legacy.py`, `evals/gate.py`, `evals/cases.py`, `evals/graders.py`
  - `scripts/verify_templates.py`, `templates/README.md`
- Create:
  - `templates/continuity_reconcile/item.j2`, `question.j2`, `direction.j2`, `direction_to.j2`, `evidence.j2`, `explain.j2`
  - `evals/gate/continuity-reconcile.json`
  - `evals/recordings/decide-continuity-reconcile.{compliant,undecodable,merged,eager,unfounded,timid}.json`
- Test: `tests/test_continuity_reconcile_prompt.py` (additions), `tests/test_decide_gate.py`, `tests/test_eval_graders.py`, `tests/test_evals.py`

**Interfaces** (all in `reconcile.py`):

- **Constants:**
  - `DECISION_ID = "decision"`, `FROM_ID = "from"`, `TO_ID = "to"`, `EVIDENCE_ID = "evidence_scene"`;
  - `PAIR_VOCABULARIES = ("same_thread", "same_commitment", "cross")`.
- **`build_payload`** gains `"recent": list[str]`, the recent window `live[-RECONCILE_RECENT_SCENES:]`, in play order. Nothing else in the payload changes, and today's prompt does not read the new key.
- **`item_scenes(payload: dict, cand: dict) -> list[str]`** is the scene ids that one candidate's item shows: its records' beat scenes (non-empty), and the ids of the chronicle lines it shows (those whose id is a beat scene of this candidate or in `recent`). They are in `payload["known_scenes"]` order, after the collision rule. Today's `known_scenes` equals the union over all candidates.
- **`build_items(payload: dict) -> tuple[decisions.Item, ...]`**: one item per payload candidate, in order. It is pure but renders. Each item:
  - **`context`** is `render("continuity_reconcile/item.j2", now=payload["now"], chronicle=<this item's lines>, c={"label", "records", "signal_text"})`:
    - today's preamble (`Campaign date:`, `Recent scenes:` limited to this item's lines);
    - a heading `Candidate — <label>`;
    - today's record and signal block from `user.j2`, byte for byte.
  - **`Choice(DECISION_ID, render("continuity_reconcile/question.j2", vocabulary=v), options=tuple(Option(w, w.replace("_", " ")) for w in DECISIONS[v]))`**. Options are labelled by their own word; the criteria stay whole in the question (ruling 6).
  - **For `v in PAIR_VOCABULARIES`:**
    - `Choice(FROM_ID, render("continuity_reconcile/direction.j2"), options=tuple(Option(r["letter"], r["line"]) for r in c["records"]), allow_none=True)`;
    - `Choice(TO_ID, render("continuity_reconcile/direction_to.j2"), <the same options>, allow_none=True)`.
  - **When `item_scenes` is non-empty:** `Choice(EVIDENCE_ID, render("continuity_reconcile/evidence.j2"), options=tuple(Option(sid, <its chronicle one-line when the item shows one, else the newest beat text it shows from that scene>) for sid in item_scenes(...)), allow_none=True)`.
  - **Collision rule:** a scene id that collides with an earlier one once normalised is dropped from that item's options and lines.
  - `decisions.validate(build_items(...))` never raises.
- **`explain() -> str`** renders `continuity_reconcile/explain.j2`.
- **`proposals_of(payload: dict, results: Sequence[decisions.ItemResult]) -> dict[str, dict] | None`**:
  - Returns `None` when no item was read (every decision answer is `NO_OBJECT` or `error`). That is today's undecodable reply, a failed run.
  - Otherwise, per read item `i` (not `NO_ITEM`, not `error`), with candidate `cand = payload["candidates"][i]`, it builds today's reply element and passes it to today's `_decide(element, cand, set(item_scenes(payload, cand)))`, then `out[cand["id"]] = ...`. The element is:
    - `decision`: the word, or `""`;
    - `from` and `to`: the letter, or `""`;
    - `reason`: the rationale;
    - `evidence_scenes`: `[scene]`, or `[]`.
  - So a word outside the vocabulary, a direction `_allowed` refuses, and a status word without a reason and a shown scene all become `uncertain`, by the same code as today.

**Templates** (move text out of `continuity_reconcile/system.j2`; invent no criterion):

| Fragment | Goes to |
|---|---|
| "A plot thread is an open narrative question; a commitment is an obligation someone owes (a promise, a debt, a threat or a piece of foreshadowing); an event is a dated occasion on the campaign calendar." | `question.j2` |
| "Signals say why a candidate is worth a look, never what the answer is." | `question.j2` |
| each vocabulary's bullet, whole, from its opening parenthesis to its end | `question.j2`, selected by `vocabulary` |
| "When what is shown cannot settle a candidate, answer "uncertain"." | `question.j2` |
| the direction sentence, from "for "duplicate", "from" is the record to fold away" to "and "to" the commitment." | `direction.j2` |
| "give a reason" and "without both, the answer counts as "uncertain"" | `evidence.j2` |
| "one short sentence" | `explain.j2` |

**Reworded table** (`RECONCILE_REWORDED`):
- "You are reviewing a campaign's story ledger for records that may overlap or be finished." stays verbatim in `question.j2`. It is listed here only because the next sentence moves.
- "Each candidate below shows one or two records, lettered A and B: … that put it on the list." → "The candidate above shows one or two records, lettered A and B: … that put it on the list." Why: the context renders above the questions.
- "For "duplicate", "continuation", "subthread" and "pays_off", give the direction as letters in "from" and "to":" → "For "duplicate", "continuation", "subthread" and "pays_off", give the direction as letters in "from" and "to", and null for both when the decision has no direction:" (`direction.j2`). Why: an empty string is not an option.
- "name at least one evidence scene id from the lines shown" → "name the evidence scene from the lines shown" (`evidence.j2`). Why: one scene is asked for (ruling 5).
- `direction_to.j2` says "The record the direction runs to; see "from"." This is a pointer, not a criterion. List it in `RECONCILE_ADDED`, which `verify_templates.py` prints, so added words are visible.

**Excluded as format:**
- "Give exactly one decision per candidate, using only the words its line offers:" (the options enforce it);
- the reply-format line and its example;
- the sentence beginning `"candidate" is the key after "Candidate"`;
- "Leave "from" and "to" empty when the decision has no direction, and "evidence_scenes" empty when no scene settles it." (covered by the reworded entries; null is the decide spelling).

**`verify_templates.py`:**
- the same five kinds of check as Task 3, over today's reconcile fixtures (`_reconcile_candidate`/`_reconcile_record`): items against direct renders, the body is today's candidate block, carried fragments, the reworded table, and coverage with the coverage-that-bites loop;
- **options tied to ids:** every decision option's description equals its id with `_` as a space, and the option ids equal `DECISIONS[vocabulary]` in order;
- **pair and evidence:** `from`/`to` appear on, and only on, `PAIR_VOCABULARIES`, and `evidence_scene` appears on exactly the items whose `item_scenes` is non-empty.

**Frozen legacy parse** (`evals/legacy.py`): a verbatim copy of `reconcile.parse_output` with `_candidate_key`, `_CANDIDATE_WORD`, `_ref_of`, `_evidence`, `_allowed`, `_temporal_word`, `_pair`, `_lifecycle_word`, `_decide`, `DECISIONS`, `_RELATION_OF`, `_DIRECTED`, `_STATUS_OF` and `RECONCILE_REASON_CHARS`. It also copies, as frozen values, the two domain tables it reads: `effective.RELATIONS` and `pending.TEMPORAL_RELATIONS`, because `legacy.py` imports no `grimoire`. Shared names take a `reconcile_` prefix. It reuses Task 3's `extract_object` copy.

**Gate** (`CONTINUITY_RECONCILE`):
- **Fixture.** `_RECONCILE_PAYLOAD` is a literal, `build_payload`-shaped (with `recent`), with no store. It has six candidates `c1`–`c6`, one per vocabulary:
  - `same_thread`: `thread:maras-map`, `thread:winifreds-chart`;
  - `same_commitment`: `commitment:maras-oath`, `commitment:winifreds-debt`;
  - `cross`: `commitment:maras-oath`, `thread:maras-map`;
  - `temporal`: `commitment:maras-oath`, `event:the-coronation`;
  - `thread`: `thread:maras-map`;
  - `commitment`: `commitment:maras-oath`.

  It shows the scenes `0001--saltmarch-docks`, `0002--realm-road` and `0003--winifreds-house`, and the record lines are built with `snippet_line`.
- **Outcome:** the proposals dict, or None; `settle` is the identity.
  - legacy side: `functools.partial(legacy.reconcile_parse_output, payload=_RECONCILE_PAYLOAD)`;
  - decide side: `reconcile.proposals_of(_RECONCILE_PAYLOAD, results)`;
  - `explain=True`.
- **`legacy_cases`** are the inputs of `test_continuity_reconcile_prompt.py`'s parse calls, **adapted** (ruling 11). Each candidate key is mapped to the fixture key of the same vocabulary, and each runtime scene id (`s0`, `s1`, `s2`, `gone`) to a fixture scene id; `"999--nowhere"` and `"C"` are kept. A comment beside each string names its source test. The list:
  - `"I think so."`, `""`, `"{}"`, `'{"decisions": 4}'`, `'{"decisions": [3, "x", null]}'`;
  - every `_reply(...)` in `test_cross_type_duplicate_is_uncertain`, `test_disallowed_direction_is_uncertain`, `test_temporal_words_name_the_commitment_and_the_event`, `test_closure_without_known_evidence_is_uncertain`, `test_resolutions_need_evidence_too_and_carry_their_status`, `test_unknown_candidate_keys_and_enums_are_dropped_or_uncertain`, `test_known_scenes_are_the_shown_beats_and_chronicle_lines`, `test_a_deleted_scene_is_not_known_evidence` and `test_a_pathologically_long_record_cannot_unbound_the_prompt`.
- **Each gets a decide twin of the same shape:**
  - a letter stays a letter, and `"C"` stays `"C"`;
  - a non-list `evidence_scenes` becomes a list-valued `evidence_scene`;
  - `[nowhere, s0, s0]` becomes `"0001--saltmarch-docks"`.
- **Added shapes:**
  - `todays-format` (intended `{}`, with a `ruling` field as in Task 3);
  - `two-scenes-cited`: legacy cites two shown scenes; decide answers one. Intended is the decide outcome; `"ruling": "one evidence scene is asked for (ruling 5)"`;
  - `evidence-from-another-candidate`: a scene shown only under another candidate. Legacy accepts it; decide cannot offer it. Intended: uncertain. `"ruling": "an item cites only what it shows"`.
  - `null-decision` (both sides `uncertain`);
  - `related-without-letters` (A to B on both sides).

**Eval case** `decide-continuity-reconcile` (`task="continuity-reconcile"`, `schema`):
- **build:** today's `build_continuity_reconcile`, unchanged.
- **prompt:** `_decide_reconcile_prompt(ctx)` builds the payload and asserts it as `_reconcile_prompt` does, then `inference.structured_messages(build_items(payload), explain=explain())`.
- **grade** (`graders.grade_reconcile_decision(output, items, payload, expected)`):
  - `reconcile.json`;
  - `reconcile.covers` (no `NO_ITEM`);
  - `reconcile.enum` (no decision `not_an_option`);
  - `reconcile.evidence`: a status word with an empty rationale or no read `evidence_scene`;
  - the per-case verdicts named as today (`reconcile.distinct`, `.continuation`, `.cross_type`, `.close`, `.keep_open`, `.fulfilled`, `.unproven`), read from the **raw** decision answer and the `from` letter, so an unfounded closure trips `reconcile.evidence` alone, as today;
  - the prompt checks per vocabulary present.
- **Recordings:**
  - `compliant`;
  - `undecodable` → `("reconcile.json",)`;
  - `merged` → `("reconcile.distinct", "reconcile.continuation")`;
  - `eager` → `("reconcile.keep_open", "reconcile.unproven")`;
  - `unfounded` → `("reconcile.evidence",)`;
  - `timid` → `("reconcile.cross_type", "reconcile.close", "reconcile.fulfilled")`.

- [ ] **Step 1: Write the failing tests** (`test_continuity_reconcile_prompt.py`):
  - `test_build_payload_names_the_recent_window`
  - `test_build_items_one_per_candidate_under_its_vocabulary`
  - `test_pair_items_ask_a_direction_and_the_others_do_not`
  - `test_evidence_options_are_the_scenes_the_item_shows`: a deleted scene is absent, and another candidate's beat scene is absent.
  - `test_an_item_showing_no_scene_asks_no_evidence`
  - `test_item_context_carries_the_date_and_only_its_own_scene_lines`
  - `test_the_union_of_item_scenes_is_todays_known_scenes`
  - `test_build_items_drops_a_scene_whose_id_collides_once_normalised`
  - `test_proposals_of_runs_every_answer_through_todays_rules`: a direction refused gives uncertain; a closure without a scene gives uncertain; temporal runs commitment to event; related with no letters runs A to B.
  - `test_proposals_of_is_none_only_when_nothing_was_read`
  - `test_a_candidate_the_reply_never_reached_gets_no_proposal`
  - `test_decide_gate.py`: `test_the_reconcile_copy_answers_as_production_does` (deleted in Task 6), and `grimoire.store.continuity.reconcile` added to `_PRODUCTION`.
  - `test_eval_graders.py`: `grade_reconcile_decision` on each recording's shape.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Implement, with the `verify_templates.py` section and README entries.
- [ ] **Step 4:** Run `tests/test_continuity_reconcile_prompt.py tests/test_continuity_reconcile.py tests/test_continuity_reconcile_routes.py tests/test_decide_gate.py tests/test_evals.py tests/test_eval_graders.py tests/test_docs_guard.py tests/test_import_guard.py`, `$PY evals/run.py --gate` (expect `continuity-reconcile … PASS`), then `make check-lint check-mypy check-templates PY=$PY`. **The gate must pass before Task 5.**
- [ ] **Step 5:** Commit `feat(continuity): the reconciliation sweep as decision items, and its gate`.

---

### Task 5: Both call sites switched, and the route flipped

**Depends on:** Tasks 3 and 4, both gates passing.

**Files:**
- Modify:
  - `store/routing.py`
  - `routes/scenes.py` (`_absorb_work`, `_extract_and_identify`, `_identify`, `_resolve_identity`)
  - `routes/continuity.py` (`_adjudicate`, `_blank_result`, `_log_pass`)
  - `backend/tests/fixtures/llm/campaign_flow.json`, `backend/tests/review_runs.py`
- Tests:
  - `test_absorb_identity.py`, `test_continuity_reconcile_routes.py`, `test_routing_routes.py` (~342)
  - `test_llm_fakes.py`, `test_routing.py`, `test_operation_guard.py` (`MIN_DECIDE_CALLS = 5`)
  - `test_continuity_identity.py` (`_say` builds today's decision dicts directly, without `parse_output`)
  - Re-run F's inline-fake grep `grep -n "def complete(self" backend/tests/*.py`; any `complete` without `*, schema=None` gains it and nothing else.

**Interfaces:**
- **`routing.py`:** `continuity` becomes `operation="decide", default_role="decision"`. Update `test_routing.py`'s expected map.
- **`_absorb_work`:**
  - `ident_resolved, ident_why, _ = await run_in_threadpool(_soft_resolved, lambda: require_inference("continuity-identity", cid, operation="decide"))`, still before the meter opens and before any coroutine is built.
  - The other three phases' inline resolutions are untouched; see open question 6.
- **`_identify(cid, sid, client, resolved: UsableInference | None, why, parsed, prepared, budget)`:** `embed_deadline` is None when `resolved is None`. The rest is unchanged.
- **`_resolve_identity(cid, sid, client, resolved, why, exam, budget, block)`:**
  1. The `not exam.rows` and `resolved is None` branches are as today.
  2. `rows = exam.prompt_rows()`.
  3. `items = await run_in_threadpool(continuity_identity.build_items, rows, exam.live)` and `explain = await run_in_threadpool(continuity_identity.explain)`.
  4. Call `decide`:

     ```python
     decision = await operations.decide(
         "continuity-identity", items, client=client, resolved=resolved, explain=explain,
         campaign=cid, scene=sid,
         around=lambda call, holder: budget.run(
             call, lambda: block.__setitem__("attempted", True),
             on_timeout=_noting(client, resolved.conn, holder)))
     ```

  5. `if not continuity_identity.take(exam, continuity_identity.answers_of(rows, decision.items))`: set `block` to `failed` with `UNREADABLE`, and return.
  6. Otherwise, `block["status"], block["reason"] = _identity_status(exam, unavailable)`, and `block["budget_exhausted"] = block["budget_exhausted"] or (budget.spent() and exam.counts()["unchecked"] > 0)`.

  An `LLMError` from `decide` (no chunk answered) and a `DecideRequestError` both reach `_identify`'s existing handlers: `BudgetRefused` → `_IDENTITY_REFUSED`, anything else → failed. No new handler is needed.
- **`_adjudicate`:**
  - `resolved, why, _ = await run_in_threadpool(_soft_resolved, lambda: require_inference("continuity-reconcile", cid, operation="decide"))`. `resolved is None` gives `llm: "off"` and the reason, as today.
  - After `build_payload`: `items = await run_in_threadpool(reconcile.build_items, payload)` and `explain = await run_in_threadpool(reconcile.explain)`.
  - Call `decide`:

    ```python
    decision = await operations.decide(
        "continuity-reconcile", items, client=client, resolved=resolved, explain=explain,
        campaign=cid,
        around=lambda call, holder: _bounded_call(
            call, on_timeout=_noting(client, resolved.conn, holder)))
    ```

  - `except LLMError` is as today.
  - `except decisions.DecideRequestError as exc`: call `store.errors.record_exception(exc, "continuity-reconcile", campaign=cid)`, then return `_failed(result, {"kind": "invalid_request", "detail": str(exc), "status": 500})`. The builder makes this unreachable, but a defect must fail the run, not the task group.
  - `proposals = reconcile.proposals_of(payload, decision.items)`. `None` gives today's `undecodable` failure.
  - `result.update(llm="ok", adjudicated=len(proposals), unanswered=len(selected) - len(proposals))`.
  - `_blank_result` starts `unanswered` at 0, and `_log_pass` adds `unanswered` to its counts.
- **Cassette and helpers:**
  - `campaign_flow.json`'s identity entry becomes `{"when": {"system_contains": "You answer closed questions about material you are given.", "user_contains": "\nCandidates:\n"}, "reply": <decision_reply-shaped {"0": {"answers": {"decision": "new", "id": null}, "rationale": "A different debt from the one already owed."}}>}`.
  - Its reconcile entry becomes the same `system_contains` with `"user_contains": "Candidate — "` and reply `"{}"`.
  - Both sit before any other entry keyed on the decide phrase without a `user_contains`.
  - `review_runs.IDENTITY_SYSTEM` becomes the pair `IDENTITY_MATCH = (decide phrase, "\nCandidates:\n")`, and `identity_requests` matches both, on the system and the user message.
  - `test_continuity_reconcile_routes.SYSTEM`, `_entry`, `_held` and `_reconcile_requests` change the same way, with `"Candidate — "`.
- **`test_llm_fakes.py`:**
  - `_decide_prompts` adds `"continuity-identity"` and `"continuity-reconcile"` pairs, each one item built through the production builders over a small literal row or payload.
  - `_generate_prompts` drops the two legacy `system.j2` renders, which are no longer sent.
  - `test_the_identity_body_…` and `test_the_reconcile_body_is_the_shape_the_parser_expects` read their replies through `answers_of` / `proposals_of`.
  - `NOT_IN_CASSETTE` is unchanged.

- [ ] **Step 1: Write and convert the failing tests.**
  - Convert every scripted resolver reply to `decision_reply`:
    - `test_absorb_identity.py`'s `_decisions(...)` (about 25 sites);
    - `'{"decisions": []}'`, which becomes `"{}"`;
    - `test_continuity_reconcile_routes.py`'s `_reply(...)` and `_duplicate(...)`.
  - A reply that names `r2` becomes item index 1; a pair direction stays letters.
  - New, in `test_absorb_identity.py`:
    - `test_identity_runs_on_decide_one_item_per_row`
    - `test_identity_meters_one_row_per_chunk`: `monkeypatch.setattr(decisions, "chunks", functools.partial(<the original>, size=1))` with two examined rows gives two `continuity-identity` ledger rows.
    - `test_a_failed_second_chunk_leaves_the_first_chunks_rows_decided`
    - `test_the_budget_running_out_between_chunks_leaves_the_rest_unchecked`
    - `test_a_reply_in_todays_format_answers_no_row`: `degraded`, every row `unchecked`, and the absorb lands.
    - `test_identity_on_a_decide_only_model_answers_on_the_fallback`, parametrized over `inference_fixtures.SPARE` and `SAME_PROVIDER`
    - `test_identity_without_a_generating_fallback_fails_the_phase_not_the_absorb`: the phase is `failed`, the reason is the `incapable` sentence, nothing is sent to `vendor/decider`, and the review lands.
    - `test_the_decision_role_now_serves_the_identity_check`
    - `test_identity_rows_file_decide_and_structured`: each row has `operation == "decide"` and `decision_mode == "structured"`.
    - `test_a_refused_schema_still_decides_the_rows`: one provider whose first answer is a 400 naming `response_format` gives F's same-attempt retry, and the row is decided.
  - New, in `test_continuity_reconcile_routes.py`:
    - `test_the_sweep_asks_decide_one_item_per_candidate`
    - `test_a_failed_chunk_keeps_the_other_chunks_proposals`: the run lands, `unanswered` counts the failed chunk's candidates, and those have no proposal.
    - `test_the_sweep_on_a_decide_only_model_answers_on_the_fallback`, parametrized as above
    - `test_the_sweep_without_a_generating_fallback_lands_with_llm_off`: 202, the run lands, `llm == "off"`, the reason is the `incapable` sentence, and persist 1's findings are in `candidates.json`.
    - `test_the_decision_role_now_serves_the_sweep`
    - `test_sweep_rows_file_decide_and_structured`
  - `test_routing_routes.py:342`: the reconcile requests are matched by the decide phrase plus `"Candidate — "`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Implement and flip the route in this same commit (the safety rule).
- [ ] **Step 4:** Run:
  ```
  tests/test_absorb_identity.py tests/test_continuity_identity.py tests/test_continuity_reconcile_routes.py \
  tests/test_continuity_reconcile.py tests/test_continuity_reconcile_prompt.py tests/test_routing_routes.py \
  tests/test_llm_fakes.py tests/test_routing.py tests/test_routing_guard.py tests/test_operation_guard.py \
  tests/test_usage_guard.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py \
  tests/test_inference_resolve.py tests/test_inference_settings.py tests/test_inference_cascade.py \
  tests/test_review_detach.py tests/test_runs_detach.py tests/test_runs_routes.py tests/test_frozen_campaign.py \
  tests/test_decide_gate.py tests/test_evals.py tests/test_docs_guard.py tests/test_import_guard.py
  ```
  plus `tests/test_routes.py -k "absorb or continuity or reconcile"`. Then `make check-lint check-mypy check-templates PY=$PY`.
- [ ] **Step 5:** Commit `feat(continuity): the duplicate check and the sweep decide on the Decision role`. The body records:
  - the route flips here because both of its tasks now decide;
  - chunks of 8, one metered row each;
  - a partial sweep lands with `unanswered`;
  - the identity resolution moved into the threadpool.

---

### Task 6: Retire the legacy prompts

**Files:**
- Modify:
  - `identity.py`: delete `build_prompt`, `parse_output`, `_decision`, `_row_key` and `_ROW_WORD`. Keep `template_rows`, which `build_items` uses.
  - `reconcile.py`: delete `build_prompt`, `template_vars`, `parse_output`, `_candidate_key` and `_CANDIDATE_WORD`. Keep `_decide` and its helpers, `DECISIONS` and `LABELS`.
  - `routes/scenes.py`: the dead `parse_output` branch is gone after Task 5; check that no import is left.
  - `scripts/verify_templates.py`: drop today's identity and reconcile prompt sections, the "body is today's row/candidate" checks, both coverage halves, `_leftover` and `_GLUE_WORDS`. Keep the item checks, carried fragments, options tied to ids, and the reworded and added tables.
  - `templates/README.md`
  - `evals/cases.py`:
    - delete the `continuity-identity` and `continuity-reconcile` cases, `_identity_prompt`, `grade_continuity_identity`, `_reconcile_prompt`, `RECONCILE_RULES` and `grade_continuity_reconcile`;
    - keep both builders and their constants, which the decide cases use.
  - `evals/graders.py`: delete `grade_identity`, `grade_reconcile` and their private helpers.
- Delete:
  - `templates/continuity_identity/system.j2`, `user.j2`
  - `templates/continuity_reconcile/system.j2`, `user.j2`
  - `evals/recordings/continuity-identity.*` and `evals/recordings/continuity-reconcile.*` (ten files)
- Tests:
  - delete the legacy parse tests in `test_continuity_identity.py` (`test_parse_*`);
  - in `test_continuity_reconcile_prompt.py`, the `parse_output` assertions are rewritten onto `proposals_of` where they test payload behaviour (known scenes, deleted scene, long record), and deleted where they test the parser itself;
  - `test_continuity_identity.py` and `test_continuity_reconcile_prompt.py` tests that render the deleted templates move onto `build_items`;
  - `test_eval_graders.py`: delete the legacy grader tests;
  - `test_decide_gate.py`: delete the two "copy answers as production does" tests;
  - `test_evals.py`: update any case-id list.
  - The commit body says that every deleted parse input is a gate entry, held by `test_every_legacy_parse_case_is_a_gate_entry`.

- [ ] **Step 1:** Delete and rewrite as listed.
- [ ] **Step 2:** Run `tests/test_continuity_identity.py tests/test_continuity_reconcile_prompt.py tests/test_absorb_identity.py tests/test_continuity_reconcile_routes.py tests/test_decide_gate.py tests/test_evals.py tests/test_eval_graders.py tests/test_llm_fakes.py tests/test_docs_guard.py tests/test_import_guard.py`, `$PY evals/run.py --gate`, then `make check-lint check-mypy check-templates PY=$PY`. If a lint count shrank, run `make baseline PY=$PY`.
- [ ] **Step 3:** Commit `refactor(continuity): retire the one-call identity and reconcile prompts`.

---

### Task 7: Docs and the gates

**Files:** `CLAUDE.md`, `templates/README.md`, `evals/README.md`, and the docstrings of `store/continuity/identity.py`, `store/continuity/reconcile.py` and `routes/continuity.py`.

- [ ] **Step 1:** Write the docs:
  - **CLAUDE.md, "Adding an LLM call site?":**
    - absorb's secondary phases: voice drift **and the duplicate check** hand `_soft_resolved` a thunk;
    - the decide paragraph names the continuity pair as decide calls, one item per row or candidate, chunked at 8;
    - extend F's note that a campaign's Fast override no longer outranks a global pin on the decide routes so that it names `continuity`.
  - **Docstrings:**
    - identity's "The resolver is one batched call" becomes "one `decide()` over every examined row, chunked";
    - reconcile's "Adjudication … one model call per sweep" becomes one `decide()` per sweep, chunked, with a partial sweep landing with `unanswered`;
    - `routes/continuity.py`'s "one call" changes to match.
  - **`evals/README.md`:** the two `decide-continuity-*` rows in the case table; the two legacy rows are removed; the gate's multi-item batch rule.
  - Run `tests/test_docs_guard.py tests/test_evals.py tests/test_decide_gate.py`, then `make check-lint check-mypy check-templates PY=$PY`. **Not** `make check`.
- [ ] **Step 2:** Commit `docs: the continuity decisions on decide()`.
- [ ] **Step 3: Gates.**
  1. A Claude stand-in final code review of the branch diff (in place of `/codex:review` until the PR).
  2. A Claude stand-in final adversarial review against the diff and this spec: does G implement §14 row G, the safety rule, §7.4's two continuity rows as amended, and §12's safe directions?
  3. Address every finding, or record why not.
- [ ] **Step 4:** Push, open the PR (Codex reviews there), and **wait for CI green** before merging, with rebase and `--ff-only`.

---

## Parallelism and coordination

| Wave | Tasks | Notes |
|---|---|---|
| 0 | 0 | Rebase onto F's final head, then docs |
| 1 | 1, 2 | Disjoint files (`decisions.py` and its tests; `evals/gate.py` and its tests). Run them in parallel worktrees, or in sequence. |
| 2 | 3, then 4 | **In sequence.** Both append to `evals/legacy.py`, `gate.py`, `cases.py`, `graders.py`, `verify_templates.py` and `templates/README.md`. |
| 3 | 5, then 6, then 7 | In sequence |

**Merge order across slices:** D → E → F → G. G is based on F, and its decide rows carry E's `role` once E lands.

**Files G shares with D (embedding operation):**

| File | D | G | Merge rule |
|---|---|---|---|
| `store/continuity/identity.py` | `_semantic(…, campaign, scene)`, `examine` passing them | new section: `build_items`, `explain`, `answers_of`, `take`, `UNREADABLE`; deletes the resolver prompt and parse section | Disjoint functions; keep both |
| `store/continuity/reconcile.py` | `_semantic` passes `campaign=cid` | `build_payload` (`recent`), `item_scenes`, `build_items`, `explain`, `proposals_of`; deletes `build_prompt`, `template_vars`, `parse_output` | Disjoint |
| `tests/test_absorb_identity.py` | `:636–655` rewritten to expect embed rows with campaign and scene | every resolver reply converted; new tests appended | A hunk near 636 may conflict: keep D's embed assertions and G's reply helper |
| `tests/test_operation_guard.py` | creates the file (embed half) | bumps `MIN_DECIDE_CALLS` to 5 | One line |
| `CLAUDE.md` | "Adding an LLM call site?" (embed) | the same paragraph (continuity decide) | Separate sentences |

**E (pricing):** no shared file. G's decide rows gain E's fields through F's account-block path.

## Rulings (Task 0 folds each into the spec)

1. **Identity is one item per row with two questions.** `decision` is a choice of existing / new / uncertain. `id` is a choice of that row's offered candidate ids, with null allowed. §7.4's "the candidate id travels in the item's context" is read as: the candidates are shown in the context, and the one meant is answered as `id`. Keeping today's two fields keeps today's two outcomes apart: an unknown word is `uncertain` and accepted, while an unoffered id is `uncertain` and downgraded.
2. **A choice that allows none may offer one option.** Null is its second answer. A row with one candidate, and an item showing one scene, need it.
3. **`unreadable` gains two details:** `no_object` (the reply held no object) and `no_item` (it held nothing readable as this item). This carries the capstone's "undecodable is not an empty answer" (§24) per item. Neither is a new reason.
4. **Reconcile direction is two choices, `from` and `to`** (A/B, null allowed), asked only on the pair vocabularies. Temporal keeps today's fixed commitment-to-event direction.
5. **Reconcile evidence is one `evidence_scene` choice** over the scenes that item shows, with null allowed. A proposal stores at most one evidence scene; the gate marks the narrowing with `ruling`.
6. **Reconcile options are labelled by their own word.** Each vocabulary's criteria stay whole in the question, carried verbatim.
7. **Each item's context is self-contained,** because H's native backends send one request per item. The reconcile date and the item's own chronicle lines repeat per item, and so do the question instructions. The cost is bounded by `MAX_ITEMS_PER_CALL`, and is to be tuned against real prompts later.
8. **"One batched call" becomes one `decide()` chunked at 8.** A sweep of up to 24 candidates is up to 3 metered calls, each under the full reconcile ceiling. Identity chunks share the absorb budget.
9. **Partial failure is partial.** A failed chunk beside answered ones leaves its rows `unchecked` (phase `degraded`) or its candidates without a proposal (the sweep lands, with `unanswered`, and they are asked again).
10. **The gate's `judge` scores a whole batch that fits one call.** `Conversion.decide` takes every `ItemResult`. Per-entry items are rejected.
11. **Reconcile's legacy cases are adapted** (candidate keys and runtime scene ids mapped to the fixture's), because today's tests build them at runtime. Identity's are verbatim.
12. **Both conversions switch in one change,** because they share the `continuity` route and the safety rule flips a route only where its call sites decide.
13. **No request-time refusal.** Both resolutions stay soft. The identity phase reports `failed`, the sweep lands with `llm: "off"`, and persist 1's findings stand.
14. **No capture for either decision.** Neither captured before (the §9.4 precedent for scene-break and voice drift).
15. **Prepared with standalone item templates.** The legacy templates are untouched until Task 6, and `verify_templates.py` holds the item bodies to today's renders meanwhile.
16. **Today's trust rules stay the mapping.** Identity answers become today's decision dicts for the unchanged `Examination.decide`; reconcile answers become today's reply elements for the unchanged `_decide`.

## Open questions for the controller

1. **Base:** the plan assumes F's final fix wave (listed under **Base**). Rebase onto it before Task 1. If F's head moves again, re-check the list.
2. **Evidence narrowing (ruling 5):** one evidence scene per proposal instead of a list. The alternative is a second optional evidence question. Is one acceptable?
3. **Contract amendments to F's `decisions.py` (rulings 2, 3):** H's native adapters would then have to render a one-option nullable choice. They can, through OpenRouter's explicit none or as a predicate. Acceptable before H?
4. **Cost shape (rulings 7, 8):** per-item instructions and preamble repeat within a chunk, and a full sweep becomes up to 3 calls. Only `--live` can measure whether that matters, and it needs the user's approval.
5. **`unanswered` on the sweep result:** a partial sweep reports `llm: "ok"` plus `unanswered`. The frontend reads only `llm == "off"`. Is a new `llm: "partial"` value wanted instead?
6. **Event loop:** G moves only the identity resolution into the threadpool. Dossier, voice-drift and audit still resolve inline on the loop, as before. Should they move in the same task?

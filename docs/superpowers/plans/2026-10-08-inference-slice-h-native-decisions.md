# Inference slice H — native decisions: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `decide()` be answered by a provider's own decisions endpoint (OpenRouter, OpenAI) for a selected model that **cannot generate** and is not known unable to decide natively. A model that can generate stays on F's structured path, whatever its `decide_native` says: §16 forbids making native the default before it wins on evals (C1). Each selection gets its one backend, then the role fallback gets its one attempt. This replaces slice F's decide skip. The capture, the voice-drift check, the model test call and the Models page say what happened, and `--live` can force a backend so that "native wins on evals" can be measured on one model.

**Architecture:**

- **The wire lives in the adapters.** Each adapter is a gateway module (#239) and normalises its provider's body into `grimoire.decisions`:
  - `OpenRouterClient.decide` and `OpenAICompatibleClient.decide` send one item;
  - `decision_body` / `decision_result` beside each are pure.
- **The wire mapping is ruled, not discovered mid-task** (rulings 22–28, I1). Both providers return a fractional, probability-weighted `score` and neither has an explicit "none". So `allow_none` adds one reserved option, a score's answer is the argmax of its per-level probabilities, and an item a decisions endpoint cannot represent fails with a stated reason before anything is sent.
- **The facade sends one native attempt.** `LLMClient.decide_native(item, conn, usage, *, retries)` dispatches by kind. It carries the per-attempt bookkeeping `_resilient` carries for a stream: stamp, capture sink, retry on `RETRYABLE_KINDS`, health observer. It never falls back, and it installs no local token estimate.
- **The chain sits in `inference.decide`.** A pure `stages(resolved)` turns a decide resolution into an ordered tuple of `Stage(mode, conn, retries)`: the primary's one backend, then the fallback's. `decide` runs the pending items through each stage's backend in turn (`_BACKENDS["native"]` is new, `_BACKENDS["structured"]` is F's). An item moves on only when its stage *failed* to answer it. `inference.run_stages` is that loop, exposed so `--live` can force a backend (Task 10).
- **The resolver says which backend serves an attempt** (ruling 1, C1). `decision_mode` is `"native"` only when `generate` is a known non-guess `no` and `decide_native` is not a known `no`. It is `"structured"` when the attempt generates, whatever `decide_native` says. `OPERATION_CAPABILITY["decide"]` becomes "decide_native or generate", and F's skip is deleted: the attempt it passed over is now served natively.
- **Every call site stays as F and G left it**, except the speaker's capture lambda (Task 6) and voice drift's native branch (Task 7, spec ruling 12).

**Tech Stack:** Python ≥3.11, FastAPI, httpx, Jinja2, pytest; React + vitest for small frontend changes; the offline eval suite under `evals/`.

**Spec:** `docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md`.

- Slice H is §14 row H.
- It draws on §5.3–§5.5, §6.1, §6.2, §6.4, §7.2, §7.4, §8, §9.3, §9.4, §10, §12, §14.1, §15 "Decide", §16 and Appendix B.
- Read slice F's plan (`docs/superpowers/plans/2026-10-08-inference-slice-f-decide.md`), and its section "How G and H extend this without reshaping it" first. Then read slice G's plan (`docs/superpowers/plans/2026-10-08-inference-slice-g-continuity-decisions.md`), which lands before H (I11).
- Task 0 amends the spec for this plan's rulings.
- The plan review (`.superpowers/sdd/plan-reviews/slice-h-plan-review.md` in the main checkout) is answered under **Rulings**, "Plan review answers".

**Base:** `claude/inference-slice-h`, which sits at slice F's `b2c85c6`. **Slice G merges first** (I11, under **Parallelism and coordination**). Task 0 rebases onto `main` once F's final head and G have both merged, and no code task starts before that.

This plan consumes from F's final head:

- `llm.SchemaRefusalError` (a subclass of `LLMError`), and `inference._ask(call, conn, messages, schema, rows)`, which makes the same-attempt retry without structured mode on a schema refusal with nowhere to fall. `_structured` hands `_with_mode(call.conn, "structured")` to `_ask` (M1);
- `resolve._skippable` and the same-provider fallback it keeps for the skip (`6cfff01`), which Task 5 replaces;
- the Decision card's routes list (`ModelsView.DecisionRoutes`, F's CODE-M3);
- the gate's `ruling` field (F's SPEC-M-5).

It consumes from G:

- `decisions.NO_OBJECT`, `decisions.NO_ITEM` and `MIN_OPTIONS_WITH_NONE = 1`: a choice that allows none may offer a single option (G's rulings 2 and 3);
- `decisions.was_read(answer)` and `decisions.offerable(spelling)` (G's Task 1, I1 and M4). `was_read` is what both continuity mappings ask of the `decision` answer: an answer is read when it is not None, or is `unreadable` with detail `""` or `not_an_option`, and `no_object`, `no_item`, `error`, `refused` and `abstained` are not. `offerable` is the one test `validate` applies to an option id or alias, and G's builders (identity's and reconcile's collision walks) drop whatever it refuses, so `validate(build_items(...))` never raises. H preserves the first and extends the second (Task 1, and **Parallelism and coordination**);
- the two continuity call sites on `decide()` (`routes/scenes._resolve_identity`, `routes/continuity._adjudicate`), and their decide-only tests in `test_absorb_identity.py` and `test_continuity_reconcile_routes.py`, which Task 5 converts;
- `evals/gate.py`'s multi-item `judge`, and the cases `decide-continuity-identity` and `decide-continuity-reconcile`, which Task 10's `--live` must run.

## Global Constraints

**The user's rules:**

- **Never run the full test suite, and never `make check` or `make check-py`.**
  - Each task runs only the test files it names, plus `make check-lint`, `make check-mypy` and `make check-templates`.
  - A task that touches `frontend/` also runs `npm run typecheck`, `npx vitest run <its files>` and `npx eslint <its files>`, all from `frontend/`. Run `npm ci` in the worktree's own `frontend/` once first, and never symlink `node_modules`.
  - CI runs the full suite. **Merging waits for CI to be green.**
- **Never spend money without asking.**
  - No task makes a live LLM call. Every test uses `backend/tests/llm_fakes.py`, `httpx.MockTransport` and hand-authored canned bodies.
  - **Every `evals/run.py --live` and `--record` step is marked _requires the user's explicit approval_.** It is skipped unless the controller relays that approval. It is not part of any task's completion: the slice is complete and testable offline without it.
- **Pushing, opening the PR and merging each wait for the controller's go-ahead**, relayed from the user (CLAUDE.md: commit or push only when asked; M10).
- **Reading provider docs is not a call.** Tasks 1–3 begin by re-reading current provider documentation (no API request, no key).
  - The wire mapping is already ruled (rulings 22–28), from the two pages the plan review read on 2026-10-08. The re-read confirms each ruled fact and establishes what is still open.
  - If the docs cannot be reached, or contradict a ruled fact or §7.4 as amended, **stop and report to the controller** before coding. Never guess an API shape.
- **Review gates:**
  - Each task gets a Claude stand-in spec and quality review, which serves as CLAUDE.md's Codex gates until the PR.
  - The final review pair (code review of the branch, then an adversarial review against spec row H) is also Claude stand-ins.
  - Codex reviews the PR.

**Rule 3 (behaviour-neutral wherever the primary can generate):**

- `backend/tests/fixtures/inference_baseline.json`, `inference_baseline_c.json` and `test_lore_golden`'s golden are **never regenerated**.
- Every store whose decide primary can generate resolves byte-identically to F: mode `structured`, the same attempts, the same `FALLBACK_KEY` attach. That holds **whatever its `decide_native` says**, so a dual-capable model is included (C1).
- The resolutions that change are those F skipped or refused (a primary known unable to generate), and one more: a generating primary whose fallback cannot generate but may decide natively. That fallback is now a native stage instead of an entry in `fallback_missing`. Its `conn` and `FALLBACK_KEY` are unchanged, because F never attached it either.
- Tasks 4 and 5 run both equivalence suites. A generate resolution's dicts stay byte-identical.

**Spec rules that bind here:**

- §12: an unanswerable question is `answer: None` plus a `reason`, never a guessed default. That holds for a native body too: a tie, a value outside the options, or a missing answer is `None`.
- Rule 5: a price nobody reported is never rendered as zero. A native row with no reported price is unpriced, and the probe's estimate is `None` from every estimator.
- §5.3: `unknown` is allowed and only a known `no` refuses.
- §5.5 as amended (Task 0): each selection's one backend, then one attempt for the fallback.
- §16: native is not the default for a model that also generates until it wins on evals.

**CLAUDE.md rules this slice touches:**

- **Imports:** all at module scope; the graph stays acyclic (`test_import_guard.py`).
  - `decisions.py` stays a stdlib-only leaf.
  - `openrouter.py`, `openai_compatible.py` and `llm.py` may import `decisions`.
  - `store/inference/probes.py` may import `decisions` (a gateway leaf, as it imports `content_parts`).
- **Faking the LLM:** only `llm_fakes.py`, injected at `routes.get_llm`. Canned native bodies live under `backend/tests/fixtures/llm/native/` and are hand-authored from the documented shape, never recorded.
- **Instrument LLM failures at `usage.Meter.done`:** every native call runs under a meter that `inference.decide` (or the probe route) opens. `test_usage_guard.py` learns `decide_native`. An item refused before any request files no ledger row (nothing was sent) but its failure still reaches the error store through `Meter.done`.
- **Observability:** the native adapters record the decoded response body through `llm_capture.emit` (event `decision_body`) before reading fields, and `http_error_body` on a refusal. Never the request, URL or headers.
- **pydantic:** plain `BaseModel` fields only, so the controls body's new field is `operation: str = ""`.
- **Privacy:** invented names only (Seraphine, Mara, Winifred, Rowan, Tobin, Saltmarch). No constant is justified by measuring a store. `NATIVE_CONCURRENCY` is argued structurally.
- **Lint gates are ratcheted:** when a count shrinks, run `make baseline PY=$PY` and commit the smaller file with the fix.
- **Linear history:** rebase and `--ff-only`, never a merge commit.

**Commands** (the worktree has no venv of its own):

- `PY=/home/user/grimoire/backend/.venv/bin/python`
- Backend tests: `cd backend && PYTHONPATH=src $PY -m pytest tests/<file> -q`
- Gates: `make check-lint PY=$PY`, `make check-mypy PY=$PY`, `make check-templates PY=$PY`
- Offline evals: `tests/test_evals.py`, `tests/test_eval_graders.py`, `tests/test_decide_gate.py`, and `$PY evals/run.py --gate` (offline; G's two continuity lines and F's three must stay PASS).

## Review Focus

1. **A native-only Decision model on a single OpenRouter provider, whose native call fails** (a retired model answers 404, or a 5xx), with a generating fallback on the same provider.
   - The fallback answers through structured generation, in one attempt (no retries).
   - The failed native call is its own ledger row: `error`, `decision_mode: "native"`.
   - A 404 (or a 403) from the decisions endpoint does not turn the connection's health dot red while it serves every generate call.
   - Tests: Task 5 `test_a_native_failure_falls_to_a_same_provider_generating_fallback`; Task 2 `test_a_refused_native_request_does_not_mark_the_connection_failing`.
2. **A well-formed native body that does not answer cleanly:** an option the item never offered, a question missing from the body, a predicate probability of exactly 0.5, a score whose levels tie, a fractional weighted `score`, or a distribution whose values are not probabilities.
   - Each is `None` with a reason (`unreadable`, with `not_an_option` where it applies; `abstained` for a tie or the reserved none), never a default. A weighted `score` is never rounded into an answer.
   - The item does **not** fall through to the next stage, because an answer is final.
   - **Except** an envelope that answers *none* of the item's questions: that is a `bad_response`, so the item falls through and the meter files an error (I2).
   - The caller's safe direction holds: no break, a failed voice check, control back to the player.
   - Tests: Task 1 `test_native_answer_*`; Tasks 2 and 3 `test_decision_result_reads_the_canned_bodies` and `test_an_envelope_answering_no_question_is_a_bad_response`.
3. **A dual-capable model on the Decision role** (`decide_native` and `generate` both `yes`, from OpenRouter's catalog or a passed probe) **stays structured** (C1, §16).
   - It resolves byte-identically to F: mode `structured`, the same `conn`, `FALLBACK_KEY` and `STRUCTURED_KEY`.
   - No native request is sent.
   - Test: Task 5 `test_a_dual_capable_primary_stays_structured`.
4. **Many items natively** (slice G's continuity sweep sends one item per row), one of which fails.
   - At most `NATIVE_CONCURRENCY` requests are in flight, under one `asyncio.TaskGroup`.
   - Only the failed item moves to the next stage.
   - Results keep input order. When nothing answered at all, the first `LLMError` is re-raised, naming the fallback's failure too.
   - A cancelled batch leaves no task running, and every opened meter files `aborted` (I5).
   - Tests: Task 4 `test_native_concurrency_is_bounded`, `test_native_items_fall_through_alone_and_keep_their_order` and `test_a_cancelled_native_batch_files_aborted_rows_and_leaves_no_task`.
5. **A standing voice-drift corrective judged natively.**
   - A native `drift` with no note keeps the standing flag: nothing staged, nothing cleared. The character is listed in `noteless`.
   - A native `in_voice` still proposes the clear.
   - A structured `drift` with no note still fails the check.
   - Tests: Task 7 `test_native_drift_without_a_note_keeps_the_standing_flag` and `test_structured_drift_without_a_note_still_fails`.
6. **An item a decisions endpoint cannot represent**, on a native-only model: a nullable choice whose options plus the reserved none pass 255 (a speaker roster of 254 refs plus `grimoire`).
   - Nothing is sent for it. With a fallback stage, the fallback answers it. Without one, it fails with the stated reason, the error store records it, and the speaker hands control back.
   - A one-option nullable choice (G's ruling 2) goes native as a two-option choice.
   - Tests: Task 1 `test_native_gap_*`; Task 2 `test_an_unrepresentable_item_is_refused_before_any_request`; Task 4 `test_an_unrepresentable_item_moves_to_the_fallback_with_its_reason`.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| the spec | the rulings below; §5.5's chain sentence; §7.4's native mapping; §10's warnings; Appendix B corrected | 0, 1–3 |
| `backend/src/grimoire/decisions.py` | `ItemResult.backend`; `NONE_KEY`; `native_choice_keys`, `native_gap`; `native_answer`; strict-mode string limits in `validate` and `chunks`; `outcome`; `render` | 1 |
| `openrouter.py`, `openai_compatible.py` | `decision_body`, `decision_result`, `*Client.decide` | 2, 3 |
| `llm.py` | `LLMClient.decide_native`, `llm.native_body`, `NATIVE_REJECTED_STATUSES`; `complete(retries=)` | 2, 3, 4 |
| `backend/tests/llm_fakes.py`, `backend/tests/fixtures/llm/native/**`, `fixtures/llm/README.md` | `FakeLLM(decisions=)`, `decide_native`, `complete(retries=)`; canned bodies | 2, 3, 4 |
| `backend/src/grimoire/inference.py` | `Stage`, `stages`, `run_stages`, `_native`, the chain in `decide`, `_Call.conn`/`retries`, generic `Around`; the capture after the call settles | 4, 6 |
| `store/inference/resolve.py`, `resolved.py`, `settings.py`, `routes/common.py` | `generates`, `native_only`; `decision_mode`; the widened `OPERATION_CAPABILITY`; skip removed; fallback attach rule; `incapable` wording; Decision card resolved as decide; `decision_mode` on the settings view | 4, 5, 9 |
| `backend/tests/inference_fixtures.py` | `neither(client)` | 5 |
| `routes/character_turns.py` | the speaker's capture lambda; `_capture(outcome=)` | 6 |
| `store/voice_drift.py`, `routes/scenes.py`, `frontend/src/api/types.ts`, `frontend/src/components/review/ReviewPanel.tsx` | the native "verdict without a note" branch | 7 |
| `store/inference/probes.py`, `routes/config.py`, `frontend/src/api/types.ts`, `routes/ProvidersView.tsx`, `components/inference/ProviderModelPicker.tsx` | the `decide_native` probe, `Probe.priceable` | 8 |
| `llm_sampling.py`, `store/inference/controls.py`, `routes/config.py`, `components/inference/ControlsReadout.tsx`, `routes/ModelsView.tsx`, `api/client.ts`, `api/types.ts` | `n/a` controls for a native-only attempt; the decide warning keyed on `decision_mode` | 9 |
| `evals/runner.py`, `evals/run.py`, `evals/README.md` | `--live` runs decide cases through the chain; `--decide-backend`, `--provider`, `--model` | 10 |
| `CLAUDE.md`, `CONTRIBUTING.md`, `docs/incoming-llm-capture.md`, `evals/README.md` | docs | 5, 11 |

---

### Task 0: Rebase and settle the spec

**Files:** the spec (§5.3, §5.4, §5.5, §6.4, §7.4, §8, §9.3, §9.4, §10, §12, §14 row H, §16, Appendix B).

- [ ] **Step 1: Rebase.**
  1. Wait for F's final fix round **and slice G** to merge to `main` (I11).
  2. `git rebase main`.
  3. Confirm the F and G pieces named under **Base** exist. If any is named or shaped differently, update this plan's Interfaces blocks before starting Task 1, and say so in the task report.
  4. Record `main`'s head (G's merge point) in the task report, so a reviewer can see what H was built on.
  5. If D and E have been integrated, also confirm `llm_usage.with_account`, `ACCOUNT_FIELDS` (with `decision_mode`), `usage.record(decision_mode=)` and E's `probes.estimate_from_rates`. Say in the report whether E is in the base, because Task 8's estimator test depends on it (I6).
- [ ] **Step 2:** Fold the rulings at the end of this plan into the spec, one line each, in the section each names. Place each line beside F's and G's text; do not restate it. Then:
  - **§5.5:** rewrite the `decide` chain sentence itself (C1), not only by an appended line: "`decide` chain: the selection's one backend — native when it is known unable to `generate` and not known unable to `decide_native`, otherwise structured generation when it can `generate` — then the role fallback, chosen by the same rule, in one attempt. Native is not tried first for a model that also generates (§16). Whether it should be is a later user decision, made only after `evals/run.py --live --decide-backend` has compared the two backends on one model." Delete "Until slice H there is no native backend…".
  - **§5.4:** `ResolvedInference.skipped` is removed; `Attempt.decision_mode` gains `native` under ruling 1; every fallback stage takes `retries: 0`, structured included (ruling 12).
  - **§5.3:** replace the skip paragraph and the "Until then the Models page can disagree with itself" paragraph with one line each on what H does (ruling 2).
  - **§7.4 "Native backends"** (I1): replace "`allow_none` maps to its explicit `none`" with rulings 22–28's mapping. Name both providers' fractional `score` and the missing "none".
  - **§10's capability warnings** (I9): the decide warning is keyed on the resolution's `decision_mode` (ruling 29).
  - **§6.4:** the `decide_native` probe is offered on the Decision picker only for a model known unable to generate, and its estimate is never priced (ruling 17).
  - **§9.4:** the capture rules of ruling 13.
  - **§12:** replace the skip-notice sentence with one line on what H does.
  - **§14 row H:** "settled".
  - **§16:** add that "native first for a model that also generates" is recorded as a later user decision, gated on Task 10's same-model measurement.
  - **Appendix B** (I1):
    - correct "explicit `none`": neither provider documents one; the OpenRouter page recommends adding a `none` option, and the OpenAI page a fallback option such as `"other"`;
    - add the fractional `score` on both, OpenRouter's `questions` and `answers` as objects keyed by question id, OpenAI's `answers` and `probabilities` as arrays, `confidence` on both, OpenAI's per-answer `"type": "refusal"`, and the usage shapes;
    - cite the OpenRouter page as a **tutorial**, not an API reference. Tasks 2 and 3 add an API-reference URL if they find one.
- [ ] **Step 3:** Commit `docs(spec): settle slice H's native chain, capture and probe`.

---

### Task 1: The contract's native half

**Files:**
- Modify: `backend/src/grimoire/decisions.py`
- Test: `backend/tests/test_decisions.py`, `backend/tests/test_character_turns.py` (one test)

**Interfaces:**
- Consumes: `decisions` as F and G left it (G's `NO_OBJECT`, `NO_ITEM`, `MIN_OPTIONS_WITH_NONE`). H's additions sit beside G's in `_check_choice` and `validate`.
- Produces:
  - **`ItemResult.backend: str = ""`.** `"structured"` or `"native"` (a member of `BACKENDS`) on an answered item; `""` on one built by `unanswered`. `parse` leaves it `""`, and the backend stamps it (Task 4).
  - **`NONE_KEY = "<none>"`.** Grimoire's distribution key for the reserved none of an `allow_none` choice. The refusal goes **inside G's `decisions.offerable`** (`offerable(spelling)` is False when `normalise(spelling)` equals `NONE_KEY`), not as a separate test in `_check_choice` (N5): `_check_choice` already refuses whatever `offerable` refuses, and G's builders drop exactly what `offerable` refuses, so a builder handed a hand-edited `<none>` id drops it and `validate` never sees it. A refusal written only in `_check_choice` would make that builder fail the request instead. No slug or ref can contain `<`, so no caller's option is lost to the reservation.
  - **`NATIVE_MAX_OPTIONS = 255`**, OpenRouter's documented per-`choice` limit. Task 3 lowers it if OpenAI documents a lower one (the lower governs both, so the chain never depends on which provider serves).
  - **`NATIVE_NONE = "none"`** and **`NATIVE_NONE_TEXT = "None of the other options fits."`**: the reserved option's wire key and description (ruling 23).
  - **`native_choice_keys(q: Choice) -> tuple[tuple[str, str], ...]`**, pure. Each pair is `(wire key, Grimoire key)`:
    - one pair per option, `(option.id, option.id)`, in order;
    - with `allow_none`, one more pair `(k, NONE_KEY)`, where `k` is `NATIVE_NONE`, or else the first of `none_2`, `none_3`, … that equals no option id;
    - so a one-option nullable choice (G's ruling 2) has two wire options;
    - the adapters use only this, so the reservation is one rule for both providers.
  - **`native_gap(item: Item) -> str`**, pure. `""` when a decisions endpoint can carry the item; otherwise one sentence naming what it cannot carry (ruling 25). Today the only gap is a choice with `len(native_choice_keys(q)) > NATIVE_MAX_OPTIONS`: "Question {id} offers {n} options including none; a decisions endpoint takes at most 255." Tasks 2 and 3 add any further documented limit here, as a gap and never as a silent truncation.
  - **Strict-mode string budgets.** The values are checked in Step 1 before they are coded:
    - `MAX_ENUM_STRING_CHARS = 15_000`;
    - `ENUM_STRING_CHARS_ABOVE = 250`;
    - `MAX_SCHEMA_STRING_CHARS = 120_000`.
  - **`schema_chars(items: Sequence[Item]) -> int`.** The characters of every key of every `properties` mapping, plus every string `enum` value, in `schema(items, explain=True)`. That is the worst case: `rationale` counts.
  - **`validate`** also refuses:
    - a `Choice` with more than `ENUM_STRING_CHARS_ABOVE` options whose ids total more than `MAX_ENUM_STRING_CHARS` characters;
    - an item with `schema_chars([item]) > MAX_SCHEMA_STRING_CHARS`.
    - Checked whatever the backend, because the chain can fall to a structured fallback (ruling 14).
  - **`chunks(items, size=MAX_ITEMS_PER_CALL, budget=MAX_ENUM_VALUES, chars=MAX_SCHEMA_STRING_CHARS)`.** Also closes a chunk before `schema_chars(held + [item]) > chars`. An item that alone exceeds `chars` is a chunk of its own; `validate` refuses it first.
  - **`UNSTATED: Final = object()`**, the sentinel for "the body named no answer".
  - **`native_answer(q: Question, *, chosen: object = UNSTATED, probability: object = None, distribution: object = None, refused: bool = False) -> Answer`.** Adapters hand it keys already mapped back to Grimoire's (option ids or `NONE_KEY`, level indices as `str(i)`). They never hand a provider's weighted `score` as `chosen` (ruling 24). In order:
    1. `refused` gives `Answer(None, "refused")`.
    2. Validate the report:
       - `probability` is kept only when it is a finite non-bool number in [0, 1];
       - `distribution` is kept only when it is a mapping whose keys are all legal (a choice: its option ids, plus `NONE_KEY` when `allow_none`; a score: `str(i)` for each level) and whose values are all finite numbers in [0, 1];
       - an invalid report is dropped whole, never repaired.
    3. **Predicate:**
       - `chosen` a `bool` is the answer (neither provider sends one today; the rule is the contract's, M11);
       - else the probability decides: `> 0.5` is `True`, `< 0.5` is `False`, `== 0.5` is `Answer(None, "abstained")`;
       - with no usable probability, `unreadable`;
       - `probability` is carried as P(true).
    4. **Choice:**
       - `chosen` an exact option id is that option (aliases are not consulted: §7.4);
       - `chosen` `NONE_KEY` with `allow_none` is `abstained`; `None` likewise; without `allow_none`, either is `unreadable`;
       - any other present value is `unreadable` with `detail=NOT_AN_OPTION`;
       - `chosen` UNSTATED with a usable distribution takes its argmax; a tie at the maximum is `abstained`, and an argmax on `NONE_KEY` is `abstained`;
       - `chosen` UNSTATED with no usable distribution is `unreadable` (I1).
    5. **Score:** an `int` (not `bool`, not a float) in `range(len(levels))`; else the distribution's argmax under the same tie rule; else `unreadable`.
    6. The kept `distribution` (and `probability`) ride on the answer, including an answer of `None`.
  - **`outcome(mode: str, provider: str, model: str, results: Sequence[ItemResult] | None = None, error: str = "") -> dict`.**
    - The capture's record of one call: `{"mode", "provider", "model", "items": [{"backend", "answers": {qid: {"answer", "reason"?, "detail"?, "probability"?, "distribution"?}}, "rationale"?}]}`.
    - On failure, `{"mode", "provider", "model", "error"}`.
    - A key whose value is empty or None is omitted.
  - **`render(results: Sequence[ItemResult], items: Sequence[Item], *, explain: bool) -> str`.**
    - The structured reply shape (`{"0": {"answers": {qid: value}, "rationale"?: str}}`), with `None` as JSON `null`.
    - Used by `--live` (Task 10) to grade a native `Decision` with the existing graders.

- [ ] **Step 1: Check the limits (no money).** Read OpenAI's current Structured Outputs "Supported schemas" limits. Confirm or correct the three constants and the 1,000-value budget F coded. Note any further documented limit (object properties, nesting depth) and whether `decisions.schema` can reach it. Write the checked values and the date into the constants' comments and into spec §7.4. If a further limit can be reached by a legal request, add it here in the same way: a constant, a `validate` refusal and a chunk bound.
- [ ] **Step 2: Write the failing tests** (`tests/test_decisions.py`):
  - `test_item_result_backend_defaults_empty_and_parse_leaves_it`
  - `test_none_key_is_refused_as_an_option`: `Option("<none>", "")` and an alias `"<NONE>"` both raise.
  - `test_none_key_is_not_offerable` (N5): `offerable("<none>")` and `offerable("<NONE>")` are False and `offerable("the-map")` stays True; G's two builders, handed a candidate id or scene id of `<none>`, drop it and `validate(build_items(...))` passes (in `test_continuity_identity.py` and `test_continuity_reconcile_prompt.py`, appended beside G's `validate` tests).
  - `test_native_choice_keys_add_one_reserved_none`:
    - a two-option choice without `allow_none` gives its two ids;
    - with `allow_none` it gives a third pair `("none", NONE_KEY)`;
    - a choice offering an option `none` reserves `none_2`, and one offering `none` and `none_2` reserves `none_3`;
    - G's one-option nullable choice (`Option("find-the-ledger", …)`, `allow_none=True`) gives two pairs.
  - `test_native_gap_names_a_nullable_choice_past_the_option_limit`: 255 options with `allow_none` gives the sentence; 254 with it, 255 without it, and one with it give `""`.
  - `test_validate_refuses_an_enum_past_the_string_budget`:
    - 251 options of 60-character ids raise;
    - 251 options of 50-character ids pass (12,550 characters);
    - 250 options of 70-character ids pass, because the per-enum rule applies only above 250 values.
  - `test_validate_refuses_an_item_past_the_schema_string_budget`
  - `test_chunks_close_before_the_schema_string_budget`: items each adding ~50,000 schema characters chunk one or two per call, in order.
  - `test_schema_chars_counts_keys_and_enum_strings`: the item from F's `test_schema_shape` counts exactly `len("0")+len("answers")+len("rationale")+len("over")+len("who")+len("tone")+len("characters:mara")+len("grimoire")`. Integer enums do not count.
  - `test_native_answer_predicate`:
    - `chosen=True` gives `True`;
    - `probability=0.7` gives `True` with `probability == 0.7`;
    - `0.3` gives `False`;
    - `0.5` gives `None`/`abstained`;
    - `probability=1.5`, `float("nan")` or `True` gives `None`/`unreadable` with no probability.
  - `test_native_answer_explicit_predicate_wins_over_its_probability` (M11): `chosen=True, probability=0.2` is `True` carrying `probability == 0.2`. The test's docstring says this is deliberate, so nobody "fixes" it into a threshold.
  - `test_native_answer_choice`:
    - an exact id;
    - `"Characters:Mara"` (not exact) gives `unreadable`/`not_an_option`;
    - `NONE_KEY` and `None` with `allow_none` give `abstained`, and without it `unreadable`;
    - a distribution `{"characters:mara": 0.6, "grimoire": 0.4}` with no `chosen` gives `characters:mara`, carrying the distribution;
    - a distribution whose argmax is `NONE_KEY` gives `abstained`;
    - a tie gives `abstained`;
    - no `chosen` and no usable distribution gives `unreadable`;
    - a distribution with a key `"characters:rowan"` (not offered) is dropped, and the answer comes from `chosen` alone.
  - `test_native_answer_one_option_nullable_choice`: G's one-option nullable choice reads its option id as that id, and `NONE_KEY` as `abstained`.
  - `test_native_answer_score`: index 2 of 3; `3` gives `unreadable`; `True` gives `unreadable`; a distribution `{"0": .1, "1": .2, "2": .7}` gives `2`.
  - `test_native_answer_score_ignores_a_fractional_weighted_score` (I1):
    - `chosen=1.4` with that distribution gives `2` (the argmax), never `1`;
    - `chosen=1.4` with no distribution gives `unreadable`;
    - the bimodal `{"0": .45, "1": .1, "2": .45}` gives `abstained`, although its mean sits on level 1.
  - `test_native_answer_refused`
  - `test_native_answer_outcomes_against_was_read` (N5): `was_read` is False for `refused` and for `abstained` (a tie, or the reserved none on a nullable choice), and True for `unreadable` with no detail (a `NONE_KEY` answer to a choice without `allow_none`) and for a chosen id.
  - `test_outcome_omits_empty_fields` and `test_outcome_of_a_failure`
  - `test_render_round_trips_answered_values`: for answered results of each type, `parse(render(r, items, explain=True), items, explain=True)` gives the same `answer` values and rationale.
  - In `tests/test_character_turns.py`, `test_a_roster_past_the_schema_string_budget_raises_an_issue`: 254 eligible refs whose ids push the choice past `MAX_ENUM_STRING_CHARS` give `INVALID_HANDOFF` through `_select`, and nothing is sent.
- [ ] **Step 3:** Run them: FAIL.
- [ ] **Step 4:** Implement in `decisions.py`. It stays stdlib-only.
- [ ] **Step 5:** Run `tests/test_decisions.py tests/test_character_turns.py tests/test_inference_decide.py tests/test_decide_gate.py tests/test_continuity_identity.py tests/test_continuity_reconcile_prompt.py`, then `$PY evals/run.py --gate` (all five lines PASS), then `make check-lint check-mypy PY=$PY`. PASS.
- [ ] **Step 6:** Commit `feat(decisions): the native half of the contract, and strict mode's string budgets`.

---

### Task 2: OpenRouter's native adapter, and the facade's native attempt

**Files:**
- Modify: `backend/src/grimoire/openrouter.py`, `backend/src/grimoire/llm.py`, `backend/tests/llm_fakes.py`, `backend/tests/fixtures/llm/README.md`, the spec's Appendix B
- Create: `backend/tests/fixtures/llm/native/openrouter/{answered,none,nullable_one,wrong_type,unanswered,malformed,error_400}.json`, `backend/tests/test_native_decisions.py`

**Interfaces:**
- Consumes: Task 1 (`native_answer`, `native_choice_keys`, `native_gap`, `ItemResult.backend`, `NONE_KEY`); the facade's `_stamp`, `_observe`, `_backoff_delay`, `RETRYABLE_KINDS`, `RETRY_AFTER_CAP`, `REJECTED_STATUSES`, `_without_fallback`, `effective_model`, `llm_capture.Capture`/`emit`.
- **What is ruled** (rulings 22–28, from OpenRouter's Jev tutorial read on 2026-10-08; Step 1 re-confirms each):
  - `POST https://openrouter.ai/api/alpha/decisions`, `Authorization: Bearer <key>`, the chat endpoint's key;
  - body `{"model", "state": item.context, "questions": {<qid>: <question>}}`: `questions` is an **object keyed by question id**, not an array;
  - **predicate** → `{"type": "noul", "instructions": q.instructions}`, with no `criteria` (they are optional, and a Grimoire predicate has no side descriptions). The answer `{"type": "noul", "noul": p}` is `native_answer(q, probability=p)`, with `p` read as P(true);
  - **choice** → `{"type": "choice", "instructions", "criteria": {wire key: description}}`, keys from `native_choice_keys(q)`. The reserved none carries `NATIVE_NONE_TEXT`. The answer's `choice` maps back to `chosen` (the reserved key to `NONE_KEY`, an unknown key to itself, so `native_answer` reads it as `not_an_option`); `probabilities`, keyed by wire key, maps back to `distribution`; `confidence` is dropped (ruling 9);
  - **a one-option nullable choice** (G's ruling 2) is a choice of two criteria: the option and the reserved none;
  - **score** → `{"type": "score", "instructions", "criteria": [level descriptions, in order]}`. The answer's `probabilities`, keyed by level index, maps to `distribution` keyed `str(i)`. Its `score` is probability-weighted and fractional, and is **never** passed as `chosen` (ruling 24). `legend` and `confidence` are dropped;
  - **answers** sit in `answers`, keyed by question id, each tagged with `type`. An answer whose `type` is not the type asked is `unreadable`;
  - **no refusal is documented.** None is mapped. If Step 1 finds one, map it to `refused`: that adds a fact and contradicts none;
  - **usage** is `{"input_tokens", "output_tokens", "cost"}`, mapped explicitly onto the holder's prompt-token, completion-token and reported-cost fields. It never goes through `llm_usage.from_openai_chunk`, which reads the chat shape. An absent field files nothing (ruling 10);
  - **ids:** Grimoire question and option ids are sent verbatim. If Step 1 finds a charset or length constraint that a legal Grimoire id can break (for example `characters:mara`), map them positionally (`q0…`, `o0…`), with the description carrying the meaning. The mapping must be reversible and is used only inside this module.
- **What Step 1 must still establish:**
  - an API-reference URL, if one exists;
  - the raw field names of a `choice` answer (the tutorial shows only the SDK's reads: `choice`, `probabilities`, `confidence`);
  - the key that carries a question's instructions;
  - the error body (undocumented: until a reference says otherwise, it is read through `_extract_error` as the chat endpoint's is, and `error_400.json` is authored from the chat API's documented error shape, labelled so in the README);
  - any limit beyond 255 options per choice and 2–10 levels per score (questions per request, state length, key charset). Each one found becomes a `native_gap` sentence in `decisions.py`, with a test.
- **Produces:**
  - `openrouter.DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"`.
  - `openrouter.decision_body(item: decisions.Item, model: str) -> dict`: pure; ignores aliases and `explain`.
  - `openrouter.decision_result(body: object, item: decisions.Item) -> decisions.ItemResult`:
    - pure;
    - raises `OpenRouterError("bad_response", …)` when `body` is not the documented envelope, **or when it is but answers none of the item's questions** (I2). The item then falls through (ruling 4), and the meter files an error;
    - otherwise answers every question through `decisions.native_answer`, with `backend="native"` and `rationale=""`;
    - a question missing from an envelope that answers others is `unreadable` with no detail (as G rules for a question missing from an item that was read), and the adapter logs one warning per call naming how many were missing.
  - `OpenRouterClient.decide(item, model: str, key: str, *, usage: dict | None = None, timeout: float | None = None) -> decisions.ItemResult`:
    - one POST and no retry; status errors are mapped by `_status_kind`/`_extract_error`, with `Retry-After` read exactly as `stream` reads it;
    - `llm_capture.emit(usage, "http_error_body", scrub(text))` on an error status, and `llm_capture.emit(usage, "decision_body", scrub(text))` on success, before any field is read;
    - the usage block mapped as ruled above, never inventing a zero.
  - `llm.NATIVE_REJECTED_STATUSES = REJECTED_STATUSES | {403}` (M6). A key without access to a beta endpoint may answer 403 while it serves every chat call; a revoked key answers 401, which stays observed. Step 1 records what the docs say about 403.
  - `llm.native_body(item: decisions.Item, conn: dict) -> dict`: pure and module-level. It dispatches by kind to the adapter's `decision_body` with `effective_model(conn)`. It holds no key or URL. Task 6's capture uses it.
  - `LLMClient.decide_native(item: decisions.Item, conn: dict, usage: dict | None = None, *, retries: int | None = None) -> decisions.ItemResult`:
    - `conn = _without_fallback(conn)`. Before any request or stamp, it raises:
      - `LLMError("bad_response", "<kind> connections have no native decisions endpoint")` for a kind with no native adapter (here, anything but `openrouter`; Task 3 adds `openai_compatible`);
      - `LLMError("bad_response", decisions.native_gap(item), code="native_unrepresentable")` when the gap is non-empty (ruling 25). It is an existing kind with a code, because `llm_errors.KINDS` is held to a status map and a new kind would need a status decided for it.
      - Nothing was sent, so `Meter.done` files no ledger row, and it still records the failure in the error store.
    - Attempts `1 + (self._retry_count() if retries is None else max(0, retries))`. Each attempt:
      - `_stamp(usage, conn, tries)`;
      - installs `llm_capture.Capture(sink, call_id, tries, effective_model(conn), kind)` when the client has a capture sink, as `_resilient` does, with the `start`/`end` events;
      - awaits the adapter with `timeout=self._timeout_seconds()`.
    - Retries only `RETRYABLE_KINDS` whose `retry_after` is not over `RETRY_AFTER_CAP`, after `max(_backoff_delay(n - 1), retry_after)`.
    - Observer: success gives `_observe(conn, None)`; a failure gives `_observe(conn, exc)`, **except** a status in `NATIVE_REJECTED_STATUSES` (ruling 11).
    - Sends no sampling: a native decision takes none (§8).
    - **Installs no `Estimate`** and calls no `note_prompt` (E's local token estimate; I7). A native row's billing unit is not a chat prompt.
    - `_resilient` is not changed. A retry-delay helper may be shared only if `_resilient`'s suites still pass unmodified.
  - **`llm_fakes`:**
    - `FakeLLM(..., decisions: list[decisions.ItemResult | LLMError] | None = None)`;
    - `async decide_native(item, conn, usage=None, *, retries=None)` takes the next entry (the last repeats) and records `(item, conn, retries)` in `self.native_requests`;
    - it applies `decisions.native_gap` first, exactly as the facade does, so a chain test sees the same refusal;
    - it stamps the holder as the fake's `stream` stamps (model, provider, `ATTEMPTED`, `llm_usage.account`) so the meter files a row;
    - it raises an `LLMError` entry, and stamps `backend="native"` on a returned result that lacks it;
    - `decisions=None` with a `decide_native` call raises `AssertionError("FakeLLM has no native decisions scripted")`;
    - the module docstring's surface list is updated.

- [ ] **Step 1: Re-read OpenRouter's docs (no money).** Read the tutorial (Appendix B) and look for an API reference for `/api/alpha/decisions`.
  - Confirm each ruled fact above. **If any is contradicted, stop and report**; do not code around it.
  - Record what is still open in spec Appendix B under "OpenRouter Decisions (checked <date>)", with the URLs, and the tutorial cited as a tutorial.
  - Author the canned bodies from the documented examples:
    - `answered` holds one `noul`, one `choice` (with the reserved none offered) and one `score` whose weighted `score` is fractional (`1.4`), with distributions;
    - `none` holds a `choice` answered with the reserved none key;
    - `nullable_one` answers a one-option nullable choice with its option;
    - `wrong_type` answers a `choice` question with a `noul`-typed answer;
    - `unanswered` is the envelope with an empty `answers`;
    - `malformed` is a 200 with no answers envelope;
    - `error_400` is the error body.
  - Name their provenance in `fixtures/llm/README.md` ("hand-authored from <URL>, checked <date>; never recorded"; `error_400` "from the chat API's documented error shape").
- [ ] **Step 2: Write the failing tests** (`tests/test_native_decisions.py`, `httpx.MockTransport` replaying the canned bodies; refs `characters:mara` and `characters:winifred`):
  - `test_openrouter_decision_body_maps_each_question_type`: the exact body for a three-question item: `questions` an object keyed by id; `noul` with no criteria; `choice` criteria including the reserved `none` with `NATIVE_NONE_TEXT`; `score` criteria as an ordered list.
  - `test_openrouter_maps_a_one_option_nullable_choice_to_two_criteria`
  - `test_openrouter_decision_body_ignores_aliases_and_explain`
  - `test_decision_result_reads_the_canned_bodies`:
    - `answered` gives three answers with distributions and `backend == "native"`, and the score's answer is the argmax level (2), not the rounded weighted score (1);
    - `none` gives `abstained`;
    - `nullable_one` gives the option id;
    - `wrong_type` gives that question `unreadable`;
    - an answered body edited to name `characters:rowan` gives `unreadable`/`not_an_option`;
    - one with a question removed gives that question `unreadable` with no detail, and logs one warning.
  - `test_an_envelope_answering_no_question_is_a_bad_response` (I2): `unanswered` raises `LLMError` kind `bad_response`.
  - `test_a_malformed_body_is_a_bad_response`: `malformed` raises `LLMError` kind `bad_response`.
  - `test_openrouter_decide_posts_once_and_captures_the_body`: one request to `DECISIONS_URL` with the bearer key; a capture sink sees `decision_body` (scrubbed); the key appears in no captured event.
  - `test_openrouter_usage_is_mapped_not_estimated`: the canned usage gives the prompt-token, completion-token and cost fields; a body without `usage` files none of them and no `tokens_estimated`.
  - `test_facade_decide_native_retries_rate_limits_only`:
    - a 429 then `answered` gives 2 requests and `attempts == 2` in the holder;
    - a 400 gives 1 request and raises;
    - `retries=0` makes one request on a 429.
  - `test_a_refused_native_request_does_not_mark_the_connection_failing`: an observer sees nothing for a 400, 403 or 404, and sees the error for a 500 and for a network failure.
  - `test_facade_decide_native_strips_the_fallback_and_sends_no_sampling`: a conn carrying `FALLBACK_KEY` and a preset; one request, its body holds no sampler field, and the fallback is never called.
  - `test_facade_decide_native_refuses_a_kind_without_an_endpoint`: `anthropic` and `claude` raise before any request, and the holder stays empty.
  - `test_an_unrepresentable_item_is_refused_before_any_request`: a nullable choice of 255 options raises `LLMError` kind `bad_response`, code `native_unrepresentable`, with the gap sentence; no request is made, and the holder stays empty.
  - `test_native_body_holds_no_key_or_url`
  - `test_fake_decide_native_scripts_and_stamps`
- [ ] **Step 3:** Run it: FAIL.
- [ ] **Step 4:** Implement.
- [ ] **Step 5:** Run `tests/test_native_decisions.py tests/test_openrouter.py tests/test_llm.py tests/test_llm_structured.py tests/test_inference_fallback.py tests/test_llm_fakes.py tests/test_import_guard.py`, then `make check-lint check-mypy PY=$PY`. PASS, and every pre-existing suite is unmodified.
- [ ] **Step 6:** Commit `feat(openrouter): native decisions, and the facade's single native attempt`.

---

### Task 3: OpenAI's native adapter

**Files:**
- Modify: `backend/src/grimoire/openai_compatible.py`, `backend/src/grimoire/llm.py` (`decide_native` and `native_body` dispatch), `fixtures/llm/README.md`, spec Appendix B
- Create: `backend/tests/fixtures/llm/native/openai/{answered,none,nullable_one,refused,unanswered,malformed,error_400}.json`
- Test: `backend/tests/test_native_decisions.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- **What is ruled** (rulings 22–28, from OpenAI's Decisions guide read on 2026-10-08; Step 1 re-confirms each):
  - `POST {base_url}/decisions` (the OpenAI preset's base URL is `https://api.openai.com/v1`); public beta; no beta header is documented, so none is sent unless Step 1 finds one required;
  - body `{"model", "input": item.context, "questions": [...]}`: `questions` is an **array**, each with a unique `name` (the question id) and `instructions`;
  - **predicate** → `{"type": "predicate", "name", "instructions"}`. The answer's `probability` is `native_answer(q, probability=p)`;
  - **choice** → `{"type": "choice", "name", "instructions", "choices": [{"value": wire key, "description"}]}`, values from `native_choice_keys(q)`. The reserved none carries `NATIVE_NONE_TEXT`. The answer's `choice` maps back to `chosen`. Its `probabilities`, an array of `{value, probability}`, maps to a `distribution` keyed by the mapped value; a value listed twice makes the report invalid, so it is dropped. `confidence` is dropped;
  - **a one-option nullable choice** is a choice of two `choices`: the option and the reserved none;
  - **score** → `{"type": "score", "name", "instructions", "levels": [{"label": str(i), "description"}]}`. The answer's `probabilities`, an array of `{value, label, probability}`, maps to a `distribution` keyed `str(i)`: by `value` when it is an int index in range, else by `label`. Its `score` is a probability-weighted average that "can fall between levels", and is **never** passed as `chosen`. `confidence` is dropped;
  - **answers** sit in an `answers` **array**, matched to questions by `name`. A name answered twice is `unreadable` for that question; an answer whose `type` is neither the type asked nor `refusal` is `unreadable`;
  - **refusal** is per answer, `{"type": "refusal", "name"}`, and gives that question `refused`;
  - **usage:** the guide documents none, but the API reference does (found by Step 1; controller ruling): the counts are filed as reported, no cost is reported, so the row is unpriced. Billing is input tokens only ($0.10 per 1M for `gpt-6-luna`), so no rate may model the row, which the ledger enforces at read time (ruling 10).
- **What Step 1 must still establish:**
  - an API-reference URL, and whether a beta header is required;
  - the charset and length limits of `name` and of choice `value`s. If a legal Grimoire id can break them, map positionally inside this module, as Task 2 rules;
  - the minimum and maximum number of `choices` and of `levels`. If the maximum is under 255, lower `decisions.NATIVE_MAX_OPTIONS` to it (one limit for both providers), with a test;
  - the usage block, if the API reference documents one;
  - the error body (as in Task 2, `_extract_error` until a reference says otherwise; `error_400.json` from the documented API error shape).
- **Produces:**
  - `openai_compatible.decision_body(item, model) -> dict`;
  - `openai_compatible.decision_result(body, item) -> decisions.ItemResult`, with the same rules as Task 2, the no-question envelope (I2) included. `confidence` is not carried (ruling 9).
  - `OpenAICompatibleClient.decide(item, model, key, base_url, *, usage=None, timeout=None) -> decisions.ItemResult`, with the same capture and error rules as Task 2.
  - `LLMClient.decide_native` and `llm.native_body` dispatch `openai_compatible` to it, with `conn["base_url"]`. Only the OpenAI preset reaches it: every other `openai_compatible` preset carries `decide_native` in `never`, so the resolver never makes it native, and `_test_plan` refuses its probe.

- [ ] **Step 1: Re-read OpenAI's Decisions guide and look for its API reference (no money).** Confirm each ruled fact; **stop and report on a contradiction**. Record what is still open in Appendix B under "OpenAI Decisions (checked <date>)". Author the canned bodies (`answered` with a fractional `score` of `1.1`, `none`, `nullable_one`, `refused`, `unanswered`, `malformed`, `error_400`) and their provenance.
- [ ] **Step 2: Write the failing tests** (in the same file, parametrized over the provider where the assertion is shared):
  - `test_openai_decision_body_maps_each_question_type`: `questions` an array with `name`s; `choices` objects including the reserved none; `levels` objects labelled `"0"`, `"1"`, ….
  - `test_openai_maps_a_one_option_nullable_choice_to_two_choices`
  - `test_decision_result_reads_the_canned_bodies[openai]`, with the score's argmax and not its weighted value
  - `test_openai_refusal_is_per_question`: a body refusing one of three questions gives that one `refused` and answers the other two.
  - `test_an_envelope_answering_no_question_is_a_bad_response[openai]`
  - `test_a_malformed_body_is_a_bad_response[openai]`
  - `test_openai_decide_posts_to_the_connections_base_url`: the request URL is `<base_url>/decisions`, and a beta header is present only if Step 1 found one is required.
  - `test_openai_confidence_is_not_carried`
  - `test_an_openai_row_without_usage_files_no_counts`
  - `test_facade_decide_native_dispatches_openai_compatible`
- [ ] **Step 3:** Run them: FAIL.
- [ ] **Step 4:** Implement.
- [ ] **Step 5:** Run `tests/test_native_decisions.py tests/test_openai_compatible.py tests/test_llm.py tests/test_decisions.py tests/test_import_guard.py`, then `make check-lint check-mypy PY=$PY`. PASS.
- [ ] **Step 6:** Commit `feat(openai): native decisions on the OpenAI preset`.

---

### Task 4: The chain in `decide`

No resolution produces `"native"` until Task 5. This task's tests build one with `dataclasses.replace` over a real resolution, so the chain lands and is proven before anything routes to it. Nothing changes behaviour.

**Files:**
- Modify: `backend/src/grimoire/inference.py`, `backend/src/grimoire/llm.py` (`complete(retries=)`), `store/inference/resolve.py` (`generates` only), `backend/tests/llm_fakes.py` (`complete(retries=)`), `backend/tests/test_usage_guard.py`
- Create: `backend/tests/test_inference_decide_native.py`

**Interfaces:**
- Consumes: Tasks 1–3; F's `_structured`, `_ask`, `_with_mode`, `_served_by`, `_BACKENDS`, `_Call`, and the schema-refusal retry (`llm.SchemaRefusalError`).
- **`resolve.generates(attempt: Attempt) -> bool`**: `"generate"` is not in `_missing(attempt, …)` (a known non-guess `no`). F's `decision_mode` uses it in place of its inline check.
- **`inference.NATIVE_CONCURRENCY = 4`.** One request per item; at most this many in flight per stage. Argued structurally: a continuity sweep sends one item per row, and an unbounded fan-out would meet the provider's rate limit as a burst of 429s that the retry budget then pays for. To be tuned against real prompts later.
- **`class Stage(NamedTuple)`**: `mode: str`, `conn: dict`, `retries: int | None`. `None` is the facade's own budget (the primary's `llm_retries`); a fallback stage carries `0` (I3).
- **`stages(resolved: ResolvedInference) -> tuple[Stage, ...]`**, pure. It **copies and never pops**: a conn "without `FALLBACK_KEY`" is a new dict, and `resolved`'s own dicts are untouched (M2). Let `A0 = resolved.attempts[0]`, and `A1 = resolved.attempts[1]` when it exists and `resolved.fallback_missing` is empty, else None. In order:
  1. If `A0.decision_mode == "native"`: `Stage("native", without FALLBACK_KEY(A0.conn), None)`.
  2. Else, if `resolve.generates(A0)`: `Stage("structured", A0.conn, None)`. `A0.conn` carries `FALLBACK_KEY` exactly when the resolver attached a structured fallback (Task 5), and then the facade sends it.
  3. If `A1` is not None, and either `A1.decision_mode == "native"` or `A0.decision_mode == "native"`: `Stage(A1.decision_mode, without FALLBACK_KEY(A1.conn), 0)`. One attempt for the fallback, by its own capabilities, native or structured (§5.4, §5.5, rulings 5 and 12).
  - **No structured stage follows a native one on the same selection.** Under ruling 1 a native attempt is one that cannot generate, so that stage could never be built (C1). It is dropped rather than kept unreachable. The later decision to try native first on a model that also generates adds it back beside `decision_mode`.
  - **While F's skip exists (this task only):** a resolution with `skipped` set gives F's single stage `Stage("structured", resolved.conn, None)`, exactly what F sends. Task 5 deletes this branch with the skip. Every other resolution F can build gives `(Stage("structured", resolved.conn, None),)`.
- **`LLMClient.complete(messages, conn, usage=None, *, schema=None, retries: int | None = None)`.** `retries`, when given, replaces the primary route's count in `_routes`; absent, `_resilient` and its suites are unchanged. `_structured` passes `retries=` **only** when `call.retries is not None`, so every primary stage calls `complete` exactly as F does, and no inline fake needs the keyword. `llm_fakes.FakeLLM.complete` gains `retries=None` and records it.
- **`_Call`** gains `conn: dict` and `retries: int | None`. `_structured` hands `_with_mode(call.conn, "structured")` to `_ask` (not `call.resolved.conn`), and returns its parsed results with `backend="structured"`. Unanswered and errored items keep `""`.
- **`Around = Callable[[Awaitable[Any], dict], Awaitable[Any]]`.** A native call's awaitable yields an `ItemResult`; F's and G's callers' `around` (`budget.run`, `_bounded_call`) are already generic.
- **`async def _native(items, call) -> decisions.Decision`:**
  - `conn = llm_usage.with_account(call.conn, decision_mode="native")`.
  - One task per item in an `asyncio.TaskGroup`, each under an `asyncio.Semaphore(NATIVE_CONCURRENCY)` (I5):
    1. open `store.usage.meter(call.task, campaign=…, scene=…, post=…, round_id=…)`;
    2. `pending = call.client.decide_native(item, conn, m.usage, retries=call.retries)`;
    3. `result = await (call.around(pending, m.usage) if call.around else pending)`.
  - Each task catches **only** `LLMError`, which makes that item `unanswered((item,), "error")[0]` and remembers the error. Anything else propagates, and the group cancels the siblings, so no orphaned request outlives the batch. Cancellation passes through untouched, and each open meter files `aborted`.
  - `BudgetRefused` (absorb's, an `LLMError`) is one item's error like any other. The next stage's `around` refuses it too, so nothing is sent.
  - When no item answered, re-raise the first `LLMError`.
  - Returns `Decision(items=<input order>, backend="native", provider/model=_served_by(the first answering holder), usage=<rows>)`.
  - Named `client`, the receiver `test_usage_guard.py` recognises.
- **`run_stages(task, items, chain: Sequence[Stage], *, client, resolved, explain="", campaign="", scene="", post=None, round_id="", capture=None, around=None) -> decisions.Decision`** is the chain loop below. `decide` validates, computes `stages(resolved)` and delegates to it. Only `evals/runner.py` calls it directly (Task 10).
  1. An empty chain is a `ValueError`; the seam's refusal makes that unreachable from `decide`.
  2. `pending = every index`. For each stage, while `pending`:
     - run `_BACKENDS[stage.mode](pending items, replace(call, conn=stage.conn, retries=stage.retries))`;
     - a `PresetRefusalError` propagates at once (the facade's rule, ruling 7);
     - another `LLMError` keeps every pending item pending and remembers the error;
     - otherwise, an item whose every answer has reason `error` stays pending, and every other item is final.
  3. When no item was ever answered, re-raise the first `LLMError` (F's ruling 8). When a later stage also failed, its failure is composed into the detail as `_resilient` does, "… — and the fallback failed too: <kind>: <detail>", keeping the first error's kind and `retry_after` (M3). Otherwise each item still pending becomes `unanswered(…, "error")`.
  4. `Decision.backend`, `provider` and `model` come from the first stage that answered any item (ruling 19); `usage` holds every stage's rows, in order.
  5. The capture keeps F's behaviour here (before each structured send). Task 6 moves it.
- **`test_usage_guard.py`:** `_GENERATORS` gains `"decide_native"`, so every `client.decide_native(...)` under `routes/` or in `EXTRA_SOURCES` must pass `<meter>.usage`.

- [ ] **Step 1: Write the failing tests** (`tests/test_inference_decide_native.py`).
  - The helper `_native_resolution(client, *, fallback, fallback_mode="structured")` builds a real decide resolution on an isolated format-2 store (F's `inference_fixtures.format2`/`decide_only`), then `dataclasses.replace`s `decision_mode="native"` onto the primary (and onto the fallback where asked). The fake is `FakeLLM(turns=[[decision_reply(...)]], decisions=[...])`.
  - `test_stages_for_every_F_resolution_are_Fs`: a structured primary with a structured fallback attached gives one structured stage on `resolved.conn`. F's skipped resolution gives F's one stage on `resolved.conn`.
  - `test_stages_for_a_native_only_primary`: native(A0, `retries=None`), then the fallback's stage with `retries=0`. A0's conn carries no `FALLBACK_KEY` in its stage.
  - `test_stages_put_no_structured_stage_after_a_native_one`: a hand-built attempt with mode `native` that also generates gives native(A0), then the fallback, and no structured stage on A0. The test's docstring names the later decision that would change it.
  - `test_stages_for_a_native_fallback`: structured(A0, without `FALLBACK_KEY`), native(A1, `retries=0`).
  - `test_stages_copy_and_never_pop` (M2): after a `decide`, `resolved.conn` still carries `FALLBACK_KEY`.
  - `test_a_native_answer_is_final`: the fake's `refused` result is returned and no structured call is made (`fake.calls == 0`).
  - `test_a_native_failure_moves_to_the_fallback_stage`: a native-only primary raises `LLMError("network")`, the structured fallback answers, `items[0].backend == "structured"`, and the ledger rows are `[("error", "native"), ("ok", "structured")]` (`status`, `decision_mode`).
  - `test_native_items_fall_through_alone_and_keep_their_order` (Review Focus 4): five items; the third's native call fails; one structured call carries only the third; the results are in input order with backends `n, n, s, n, n`.
  - `test_native_concurrency_is_bounded`: ten items through a fake whose `decide_native` records the in-flight high-water mark, which is `<= NATIVE_CONCURRENCY`.
  - `test_a_cancelled_native_batch_files_aborted_rows_and_leaves_no_task` (I5): cancel `decide` mid-flight; every opened meter filed `aborted`, and no task created by `_native` is left running.
  - `test_an_unexpected_exception_cancels_the_other_native_items` (I5): one item's `decide_native` raises `KeyError`; it propagates, and the in-flight siblings are cancelled.
  - `test_nothing_answered_reraises_the_first_error_naming_the_fallbacks` (M3): native raises A (404) and the fallback stage raises B (429); `decide` raises A's kind, and its detail ends "— and the fallback failed too: rate_limit: …".
  - `test_a_fallback_stage_makes_one_request_on_a_429[native|structured]` (I3): the primary fails, and the fallback stage meets a 429; exactly one fallback request is made.
  - `test_a_structured_stage_before_a_native_fallback_still_retries_without_the_mode` (M1): the structured primary's schema refusal is answered by `_ask`'s same-attempt retry before the item could move on.
  - `test_a_lone_structured_fallback_stage_gets_the_schema_refusal_retry` (M1): behind a native primary, the structured fallback stage (no `FALLBACK_KEY`) makes the retry.
  - `test_a_preset_refusal_stops_the_chain`: the structured primary stage raises `PresetRefusalError`, and the native fallback stage is never called, though it takes no sampling (M8, ruling 7).
  - `test_an_unrepresentable_item_moves_to_the_fallback_with_its_reason`: on a native-only primary, a 255-option nullable choice is refused unsent and answered by the structured fallback. Without a fallback, `decide` raises that `LLMError` (code `native_unrepresentable`) with the gap sentence, no ledger row is filed, and the error store holds the failure.
  - `test_around_runs_inside_each_native_meter`: an `around` raising `LLMError("timeout")` files an `error/timeout` row with `decision_mode == "native"`, then the chain moves on.
  - `test_native_rows_carry_operation_and_mode`, and the resolution's own account blocks are unchanged (E's no-mutation rule).
  - `test_decision_names_the_first_answering_stage`
  - In `tests/test_llm.py`: `test_complete_retries_overrides_only_the_primary_count`, and `complete` without `retries` makes F's request count.
  - Also run F's `tests/test_inference_decide.py` unmodified: every F path is a one-stage chain.
  - `test_usage_guard.py`: a planted `client.decide_native(item, conn)` with no meter is caught.
- [ ] **Step 2:** Run them: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `tests/test_inference_decide_native.py tests/test_inference_decide.py tests/test_llm.py tests/test_llm_fakes.py tests/test_usage_guard.py tests/test_operation_guard.py tests/test_scene_break_routes.py tests/test_character_turns.py tests/test_absorb_identity.py tests/test_continuity_reconcile_routes.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_import_guard.py`, then `make check-lint check-mypy check-templates PY=$PY`. PASS.
- [ ] **Step 5:** Commit `feat(inference): decide runs each selection's backend, then the fallback's, in order`.

---

### Task 5: The resolver serves native, and the skip goes

**Files:**
- Modify:
  - `store/inference/resolve.py`
  - `store/inference/resolved.py`
  - `store/inference/settings.py` (also `decision_mode` on each role card and route row, I9)
  - `routes/common.py` (`UsableInference.conn`)
  - `backend/tests/inference_fixtures.py` (`neither`)
  - `CLAUDE.md` (the "Until slice H, a decide-only Decision model is skipped…" sentence, so `test_docs_guard.py` holds while `skip_text` disappears)
- Tests: `test_inference_decide.py`, `test_inference_decide_native.py`, `test_inference_settings.py`, and every decide-only case F and G left. Grep `decide_only`, `skip_text` and `skipped` across `backend/tests/`: at least `test_scene_break_routes.py`, `test_routes.py` (voice drift), `test_character_turns.py`, `test_absorb_identity.py` and `test_continuity_reconcile_routes.py`.

**Interfaces:**
- Consumes: Task 4's `generates` and `stages`.
- **`OPERATION_CAPABILITY: dict[str, tuple[str, ...]] = {"generate": ("generate",), "embed": ("embed",), "decide": ("decide_native", "generate")}`.** Each entry is a set of alternatives.
- **`_needs(route, operation) -> tuple[frozenset[str], ...]`.** The operation's alternatives as one group, and each `route.requires` capability as a group of its own.
- **`_missing(attempt, needs) -> tuple[str, ...]`.** Every member of every group whose members are **all** known `no` (non-guess), in `capabilities.NAMES` order. Callers pass groups; `generates` passes `(frozenset({"generate"}),)`.
- **`native_only(caps: dict[str, Cap]) -> bool`**: `generate` is a known `no` from a source other than `name`, and `decide_native` is not a known `no`. Task 9's controls preview uses the same function, so the two never disagree.
- **`decision_mode(attempt) -> str`** (ruling 1, C1):
  - `"native"` when `native_only(attempt.capabilities)`;
  - else `"structured"` when `generates(attempt)`, **whatever `decide_native` says**;
  - else `""`.
- **The skip is deleted:**
  - `ResolvedInference.skipped`, `_skipped`, `skip_text`, and `_sent`'s skip branch: `conn` is `attempts[0].conn` again, and `fallback` loses its skip branch;
  - Task 4's `stages` branch for a skipped resolution;
  - `settings._problem`'s skip fallback;
  - `UsableInference.conn`'s skip branch (`return self._sent.conn` after the narrowing assert, per F's review note 9, or `attempts[0].conn`);
  - every reference in code and tests.
- **The same-provider rule** replaces `_skippable` with `_apart(primary: Attempt, operation: str) -> bool` (`operation == "decide" and not generates(primary)`). A same-provider fallback is built when `_apart` holds, and kept as an ordinary fallback: it is its own stage and never rides the facade. F's post-chain trim is deleted; a fallback that cannot serve either backend is reported in `fallback_missing` like any other.
- **The fallback attach on a decide resolution** (`_chain`): modes are computed **before** the attach. `FALLBACK_KEY` is attached when `not fallback_missing`, the fallback's `decision_mode == "structured"`, **and** `generates(primary)`. Otherwise the fallback stays in `attempts`, unattached, as its own stage. Generate resolutions are unchanged. Under ruling 1 a generating primary is structured, so every attach F made is still made.
- **`incapable`:** on a decide resolution whose `missing` holds both `generate` and `decide_native`, `incapable_text` composes the phrase from `capabilities.CANNOT["generate"]` and `CANNOT["decide_native"]` ("generate text or make native decisions"). `CANNOT` stays keyed by capability, so `group_for`'s reasons are unchanged (M7). The only both-`no` models are an OpenRouter model whose `decide_native` the user overrode to `no`, and a model on a preset whose `never` holds `decide_native` (Anthropic, z.ai, Ollama, LM Studio, Custom) with `generate` known `no` (I8). For example: "The Scene-break checks route runs on the Decision role (vendor/neither on Anthropic), which cannot generate text or make native decisions — choose another Decision model or pin this route." The test pins the composed sentence, not this example.
- **`settings._role_card`** resolves with `operation="decide" if role == "decision" else "generate"`. The Decision card and every decide route row then read the one decision (§12). A native-only Decision model shows no problem on either (ruling 2).
- **The settings view carries `decision_mode`** (`"native"`, `"structured"` or `""`) on the Decision card and on each decide route row, read off the resolution (`ResolvedInference.decision_mode`). Task 9's warning reads it (I9).
- `STRUCTURED_KEY` is still set by `_flag_structured` on every decide attempt whose `structured_output` is `yes`. A native attempt has no structured stage, so the flag is harmless there.
- **`inference_fixtures.neither(client)`** (I8): an OpenRouter Decision model whose catalog `outputs` lacks `text`, with a user override `decide_native: no`, so both capabilities are known `no` and the resolution is refused. Its docstring says why editing the catalog alone cannot build one (an OpenRouter catalog never gives `decide_native: no`; `capabilities.py` "absence says nothing").

- [ ] **Step 1: Write and rewrite the failing tests.**
  - F's skip tests become native tests (keep the names' subjects, with the verbs changed):
    - `test_resolve_serves_a_decide_only_primary_natively`: OpenRouter, Decision on a model whose catalog `outputs` is `["decisions"]`:
      - `decision_mode == "native"`, `missing == ()`, `refusal is None`;
      - a generating fallback on another provider is attached to nothing and is its own stage;
      - the route row's and the Decision card's `problem` are both None, and both say `decision_mode == "native"`.
    - `test_an_openrouter_non_text_model_on_decision_resolves_native` (I8): a catalog `outputs` of `["embeddings"]` is `generate: no`, `decide_native: unknown`, and resolves `native` with no refusal. The docstring says this is the deliberate consequence of §5.3's "unknown is allowed".
    - `test_a_decide_only_model_with_a_same_provider_fallback_keeps_it`: the reviewer's single-provider case. `stages` is native(A0), structured(A1, `retries=0`).
    - `test_a_native_failure_falls_to_a_same_provider_generating_fallback` (Review Focus 1), end to end through `decide` with `FakeLLM(decisions=[LLMError(status=404)])`.
    - `test_a_dual_capable_primary_stays_structured` (C1, the §16 guard), parametrized over `decide_native: yes` from the OpenRouter catalog (`outputs: ["text", "decisions"]`) and from a passed probe:
      - `decision_mode == "structured"`;
      - `conn`, `FALLBACK_KEY` and `STRUCTURED_KEY` are byte-identical to F's resolution of the same store;
      - after a `decide`, `fake.native_requests == []`.
    - `test_a_model_that_neither_generates_nor_decides_is_refused`: on `inference_fixtures.neither`, 409 `incapable` with the composed sentence, on the card and on the row alike.
    - `test_an_unknown_native_capability_is_not_refused`: `generate` known `no` and `decide_native` unknown give mode `"native"` and no refusal.
    - `test_a_structured_primary_is_unchanged`: the F resolution, byte for byte (`conn`, `FALLBACK_KEY`, `STRUCTURED_KEY`).
  - **The route suites' decide-only cases** (scene-break, voice drift, speaker; and G's identity and sweep) now script `FakeLLM(decisions=[…])` and assert that the native answer is used and nothing goes to `complete`.
    - G's `test_identity_on_a_decide_only_model_answers_on_the_fallback[spare|same_provider]` and `test_the_sweep_on_a_decide_only_model_answers_on_the_fallback[spare|same_provider]` become "answers natively". A twin of each scripts a native failure and asserts the fallback answers, with every other assertion kept.
    - G's `test_identity_rows_file_decide_and_structured` and `test_sweep_rows_file_decide_and_structured` use a generating Decision model, so they stay as they are.
  - **A native answer reaches G's mapping with no rationale** (G's review I4; **Parallelism and coordination**):
    - `test_a_native_close_verdict_without_a_rationale_takes_gs_mapping` (`test_continuity_reconcile_routes.py`, native-only Decision model): the item is read (`backend == "native"`, `rationale == ""`, no `NO_ITEM`), and the outcome is whatever G's mapping gives as merged. Under G's plan as written that is `uncertain`: no closure is proposed, and the sweep lands.
    - `test_a_native_identity_answer_maps_with_an_empty_reason` (`test_absorb_identity.py`): a native `existing` on an offered id is accepted with `reason == ""`.
  - **The "no fallback" twins move to `neither`, with every assertion kept** (I8). A native-only model without a fallback now answers natively, so the 409 needs a model that is both `no`:
    - F's CODE-I1 pair, `test_the_speaker_on_a_decide_only_model_without_a_fallback_is_refused` (transcript, roll proposal, tracker and resend all untouched) and `test_a_pick_refused_inside_the_run_takes_the_post_back`;
    - the scene-break and voice-drift twins;
    - G's `test_identity_without_a_generating_fallback_fails_the_phase_not_the_absorb` and `test_the_sweep_without_a_generating_fallback_lands_with_llm_off`, with nothing sent to the `neither` model.
    - Each keeps its original "decide-only without a fallback" case as a native-answer test beside it.
  - `test_inference_settings.py`: `test_the_decision_card_resolves_as_a_decision`; `test_the_settings_view_carries_decision_mode` (native, structured, and `""` on the refused `neither` card).
- [ ] **Step 2:** Run them: FAIL.
- [ ] **Step 3:** Implement, and edit CLAUDE.md's sentence to: "A Decision model that cannot generate but may decide natively is served by its provider's decisions endpoint (`decision_mode == "native"`), then by the role fallback; a model that can generate stays on structured generation whatever its `decide_native` says, until native wins on evals (`inference.stages`)."
- [ ] **Step 4:** Run:
  - `tests/test_inference_decide.py tests/test_inference_decide_native.py tests/test_inference_resolve.py tests/test_inference_settings.py tests/test_inference_cascade.py tests/test_inference_capabilities.py tests/test_inference_fallback.py`
  - `tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_inference_migrate.py tests/test_scene_break_routes.py tests/test_routes.py tests/test_character_turns.py tests/test_absorb_identity.py tests/test_continuity_reconcile_routes.py tests/test_operation_guard.py tests/test_routing_guard.py tests/test_docs_guard.py tests/test_import_guard.py`
  - `$PY evals/run.py --gate` (all five lines PASS)
  - `make check-lint check-mypy PY=$PY`.
  - PASS. **The equivalence suites pass with no new allowance.**
- [ ] **Step 5:** Commit `feat(inference): a model that cannot generate is answered natively, and the skip is gone`.

---

### Task 6: Capture — mode, normalised answers and distributions (§9.4)

**Files:**
- Modify: `backend/src/grimoire/inference.py`, `routes/character_turns.py` (`_select`'s lambda, `_capture`)
- Tests: `test_inference_decide.py` (rewrite F's `test_capture_sees_each_chunks_messages_before_it_is_sent`), `test_inference_decide_native.py`, `test_character_turns.py`

**Interfaces:**
- **`Capture = Callable[[list[dict], dict, dict], Awaitable[None]]`**: `(messages, outcome, conn)`.
- **When:** called once per backend call **after it settles**, answered or failed with an `LLMError` (ruling 13).
  - An `around` timeout is an `LLMError` raised inside the call, so a timed-out pick is captured with its error.
  - A cancelled call is not captured. A cancel is the player's own act, not a failure to diagnose, and awaiting a threadpool write while the task is being cancelled would either be cancelled itself or hold the cancel up.
  - **One structured call is one capture** even when `_ask` made its same-attempt retry without structured mode: the capture records the final outcome (M9).
  - Each native item is one call.
- **Where:** outside the meter's `with` block, so a capture never turns a call that succeeded into an `error` row. It is wrapped in `try/except Exception` with a `logger.warning`, as `llm._observe` is, so a capture that raises never fails an answered decision (I4).
- **What:**
  - `messages` is computed only when `capture is not None`, and off the loop (`asyncio.to_thread`):
    - a structured chunk: its `structured_messages`, as F renders them;
    - a native item: the request as sent, `[{"role": "user", "content": json.dumps(llm.native_body(item, conn), indent=2, ensure_ascii=False)}]`. That is the normalised body (`model`, the context, the questions), with no key or URL. It is not `decide/user.j2`'s prose, which the model never saw (§9.4: "the full request").
  - `outcome` is `decisions.outcome(mode, provider, model, results)`, or `decisions.outcome(mode, provider, model, error=f"{exc.kind}: {exc.detail}")` on failure.
  - `conn` is the stage's conn (the account-stamped copy), so the capture names the attempt that ran. For a native stage, the copy has `sampling` removed, so the prompt log never reports a preset that was not sent (M4).
- **Speaker call site:** `capture=lambda msgs, outcome, conn: run_in_threadpool(_capture, cid, sid, "response-selector", msgs, conn, outcome)`.
- **`_capture(cid, sid, task, messages, conn, outcome: dict | None = None)`**: with `outcome`, the breakdown built from plain messages gains a last section:

  ```python
  {"id": "decision", "label": "decision", "text": json.dumps(outcome, indent=2, ensure_ascii=False),
   "tier": "lock-in", "dropped": False, "pinned": False, "trimmed": 0, "tokens": 0}
  ```

  `total_tokens` excludes it (it was not sent). Nothing else changes, and scene-break, voice drift and G's two continuity decisions still pass no capture.

- [ ] **Step 1: Write the failing tests:**
  - `test_capture_records_each_structured_call_once_it_settles`: messages, `outcome["mode"] == "structured"`, the normalised answers, and the conn the call was sent on.
  - `test_a_schema_refusal_retry_is_one_capture` (M9)
  - `test_capture_records_a_native_call_with_its_distribution`: the message is the JSON request body; no key or URL appears in it; the conn has no `sampling`.
  - `test_the_native_capture_renders_off_the_loop`: the body is built in a worker thread (the same probe F's `test_the_prompt_renders_off_the_event_loop` uses).
  - `test_no_messages_are_built_without_a_capture`
  - `test_capture_records_a_failed_call_with_its_error`
  - `test_a_timed_out_pick_is_captured_with_its_error`
  - `test_a_cancelled_call_is_not_captured`
  - `test_a_raising_capture_leaves_the_decision_and_the_ok_row` (I4): the decision is returned, the row is `ok`, and a warning is logged.
  - `test_the_selector_capture_records_the_decision`: the prompt-log entry has a `decision` section with tokens 0, and `total_tokens` equals the sum of the other sections.
  - The speaker's existing capture test is adjusted to the new lambda.
- [ ] **Step 2:** Run them: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `tests/test_inference_decide.py tests/test_inference_decide_native.py tests/test_character_turns.py tests/test_prompt_log*.py tests/test_regex_prompt_guard.py`, then `make check-lint check-mypy check-templates PY=$PY`. PASS.
- [ ] **Step 5:** Commit `feat(inference): the decide capture records the mode, the answers and their distributions`.

---

### Task 7: Voice drift — the native verdict without a note

**Files:**
- Modify: `store/voice_drift.py` (`finding_of`, `check_failure`), `routes/scenes.py` (`_stage_voice_drift`, and every literal that builds the voice block — grep `"unjudged"`), `frontend/src/api/types.ts` (`VoiceCheck`), `frontend/src/components/review/ReviewPanel.tsx`
- Tests: `backend/tests/test_voice_drift_store.py`, `backend/tests/test_routes.py` (voice drift), `frontend/src/components/review/SceneReview.test.tsx`

**Interfaces:**
- `finding_of(result)` returns `{"verdict", "note", "native": result.backend == "native"}`.
- `check_failure(finding)`:
  - "drift reported with no corrective" applies only when `not finding.get("native")`;
  - the `MAX_NOTE` check and the UNKNOWN check apply to both backends.
- `_stage_voice_drift`:
  - `out["noteless"]: list[str]` is initialised wherever `out["unjudged"]` is;
  - a native DRIFT with an empty note is appended to `flagged` **and** `noteless`;
  - `stage_edit` already returns None for an empty note, so nothing is staged and a standing flag is neither replaced nor cleared;
  - a native IN_VOICE goes through `stage_edit` unchanged (it proposes the clear of a standing flag).
- `VoiceCheck.noteless?: string[]`, optional because reviews stored before H lack it.
- `ReviewPanel` renders a `mechanics-notice` when `noteless` is non-empty: "Out of voice, with no corrective to store: {ids}. The Decision model answers natively and gives no note, so any standing correction is left as it is."

- [ ] **Step 1: Write the failing tests:**
  - `test_voice_drift_store.py`: `test_check_failure_accepts_a_native_drift_without_a_note`; `test_structured_drift_without_a_note_still_fails`; `test_a_native_note_over_the_cap_still_fails`.
  - `test_routes.py`, on Seraphine with an anchor and a standing flag, and `FakeLLM(decisions=[ItemResult({"verdict": Answer("drift")}, backend="native")])` on a native Decision role:
    - `test_native_drift_without_a_note_keeps_the_standing_flag`: no voice edit is staged, `voice.flagged == voice.noteless == ["seraphine…"]`, the status is `ok`, and the flag file is unchanged after save.
    - `test_native_in_voice_still_proposes_the_clear`
  - `SceneReview.test.tsx`: `voice noteless names the characters`; a review without `noteless` renders no such notice.
- [ ] **Step 2:** Run them: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `tests/test_voice_drift_store.py tests/test_routes.py -k voice` and `tests/test_decide_gate.py`. From `frontend/`: `npm run typecheck`, `npx vitest run src/components/review/SceneReview.test.tsx`, `npx eslint src/components/review/ReviewPanel.tsx src/api/types.ts`. Then `make check-lint check-mypy PY=$PY`. PASS.
- [ ] **Step 5:** Commit `feat(voice-drift): a native verdict is shown without a note and stores none`.

---

### Task 8: The `decide_native` probe (§6.4)

**Files:**
- Modify: `store/inference/probes.py`, `routes/config.py` (`_probe`), `frontend/src/api/types.ts` (`TestableCapability`), `frontend/src/routes/ProvidersView.tsx` (`TESTABLE`), `frontend/src/components/inference/ProviderModelPicker.tsx` (`probesFor`)
- Tests: `backend/tests/test_model_test_call.py`, `frontend/src/components/inference/ProviderModelPicker.test.tsx`, `frontend/src/routes/ProvidersView.test.tsx`, `frontend/src/components/inference/TestCallDialog.test.tsx`

**Interfaces:**
- **In `probes.py`:**
  - `DECIDE_CONTEXT = "The lamp in the window is lit."`
  - `DECIDE_QUESTION = "Is the lamp lit?"`
  - `PROBE_ITEM = decisions.Item(DECIDE_CONTEXT, (decisions.Predicate("probe", DECIDE_QUESTION),))`
  - `Probe` gains `priceable: bool = True`. `PROBES["decide_native"] = Probe("decide_native", "decide", 0, 0, priceable=False)`.
  - `describe("decide_native", capped)` = `"One native decision request: the statement “{DECIDE_CONTEXT}” and the yes/no question “{DECIDE_QUESTION}”."`
  - **Every estimator returns `None` when any probe among `caps` is not `priceable`** (I6, ruling 17): `estimate_usd`, and E's `estimate_from_rates` when E is in the base. A rates entry (a `pricing.json` wildcard is enough) would otherwise price a 0-token probe at `0.0`, and the confirmation would show **$0.00**, breaking rule 5 at the moment rule 1 asks for consent.
- **`_probe`:** when `PROBES[cap].operation == "decide"`:

  ```python
  with store.usage.meter("model-test") as m:
      await _bounded_call(client.decide_native(
          probes.PROBE_ITEM,
          llm_usage.with_account(conn, operation="decide", decision_mode="native"),
          m.usage, retries=0))
  ```

  - Success is the request accepted and a body normalised (any `ItemResult`).
  - `_records` and `_halts` apply unchanged; a `bad_response` with no status is not recorded. A 403 is a refusal of the probe (ruling 11) and is recorded like the other refusals.
  - `_test_plan` already refuses the probe on presets whose `never` holds `decide_native`.
- **Frontend:**
  - `TestableCapability` adds `"decide_native"`, and `TESTABLE` appends it, so the provider page's explicit **Test…** may list it for any model.
  - `probesFor` on the **Decision picker** maps the `decide` need to `["generate"]`, plus `"decide_native"` **only when the row's `generate.value === "no"`** and its `decide_native.value !== "no"` (M5, C1). A model that generates stays structured whatever the probe finds, so the probe would spend and change nothing. It then keeps the probes not already `yes`, as today.

- [ ] **Step 1: Write the failing tests:**
  - Backend:
    - `test_the_decide_native_probe_sends_one_native_request`: `FakeLLM(decisions=[…])`; one `native_requests` entry; `retries == 0`; the ledger row is under `model-test` with `operation == "decide"` and `decision_mode == "native"`; the facts record `decide_native: ok`.
    - `test_a_refused_decide_native_probe_is_recorded_unverified`: a 400 is recorded as a failed test, and the capability reads `unknown` with its error.
    - `test_the_decide_native_probe_is_refused_on_presets_that_cannot`: Anthropic answers 400 and nothing is sent.
    - `test_the_preview_says_cost_unknown_for_a_decision_probe`: with a catalog price on the row, `estimated_cost_usd` is `null`.
    - `test_a_pricing_wildcard_does_not_price_the_decision_probe` (I6): with a `pricing.json` wildcard, the preview's `estimated_cost_usd` and `estimate_basis` are both `null`. **If E is not in the base**, this test is written in the E/H integration step instead (**Parallelism and coordination**), and this task tests `estimate_usd` only.
  - Frontend:
    - `ProviderModelPicker`: an unverified Decision row that is known unable to generate offers `decide_native`; an unverified Decision row whose `generate` is `unknown` or `yes` offers `generate` only; a row whose `decide_native` is `no` never offers it.
    - `ProvidersView`: Test… lists "decide_native".
    - `TestCallDialog`: with an estimate of null, "cost unknown" is shown.
- [ ] **Step 2:** Run them: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `tests/test_model_test_call.py tests/test_provider_api.py tests/test_usage_guard.py tests/test_routing_guard.py`. From `frontend/`: `npm run typecheck`, `npx vitest run` on the three files, and `npx eslint` on the touched files. Then `make check-lint check-mypy PY=$PY`. PASS.
- [ ] **Step 5:** Commit `feat(test-call): a decide_native probe, one predicate, cost unknown`.

---

### Task 9: `n/a` controls for a native-only attempt (§8), and the Models page's decide warning (§10)

**Files:**
- Modify: `llm_sampling.py`, `store/inference/resolve.py` (`_attempt`'s controls on a decide resolution), `store/inference/controls.py`, `routes/config.py` (`InferenceControlsBody`), `frontend/src/api/client.ts` (`previewControls`), `frontend/src/api/types.ts` (the settings view's `decision_mode`), `frontend/src/components/inference/ControlsReadout.tsx`, `frontend/src/routes/ModelsView.tsx`
- Tests: `backend/tests/test_inference_controls.py`, `test_inference_resolve.py`, `frontend/src/components/inference/ControlsReadout.test.tsx`, `frontend/src/routes/ModelsView.test.tsx`

**Interfaces:**
- `llm_sampling.WHY_NATIVE = "a native decision takes no sampling"`.
- `llm_sampling.not_applicable(conn: dict, why: str) -> dict` has `effective`'s shape:
  - `requested` is the stored values, as `effective` reports them;
  - `effective` is `{}`;
  - every control in `CONTROLS` is `{"state": "n/a", "wire": "", "why": why, "source": "adapter"}`.
- In `resolve._chain`, on a decide resolution, an attempt whose `decision_mode == "native"` gets `controls=not_applicable(conn, WHY_NATIVE)`, through `dataclasses.replace` like the mode. Under ruling 1 a native attempt is always native-only, so there is no "native that also generates" case to keep controls for (ruling 18).
- `InferenceControlsBody.operation: str = ""`. `controls.preview(preset_id, conn, model, operation="")`: when `operation == "decide"` and `resolve.native_only(capabilities.caps_for(lowered))` holds, it answers `not_applicable`. That is Task 5's function, so the preview and the resolver never disagree.
- **The decide warning is keyed on the resolution, not re-derived in the frontend** (I9, §6.3, §10). `ModelsView.tsx`'s rule at `:63-65` (`decide_native?.value !== "yes"`) is deleted. It reads the settings view's `decision_mode` (Task 5) on the Decision card and on each decide route row:
  - `"native"`: "Answered by the provider's decisions endpoint.";
  - `"structured"` on a model whose `decide_native` is `yes`: "Answered by structured generation." (no "No native API" claim, which would be false);
  - `"structured"` otherwise: "No native decision API; structured generation will be used.";
  - `""` (refused): the backend's `problem`, the composed `incapable` sentence, and no capability warning of its own.
- Frontend:
  - `previewControls` takes optional `operation`;
  - `ControlsReadout` takes `operation?: "decide"`, and when every control is `n/a` renders "Not sent: a native decision takes no sampling.";
  - `GenerativeSummary` passes `operation="decide"` on the Decision card.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_native_attempt_reports_na_controls`
  - `test_a_dual_capable_attempt_keeps_its_controls`: it is structured (ruling 1), so its controls are F's.
  - `test_controls_preview_for_decide_on_a_decide_only_model`
  - a generate preview is byte-identical to today's;
  - `ControlsReadout` renders the n/a line;
  - `ModelsView`: the n/a line on a Decision card whose model is native-only;
  - `ModelsView` (I9), one case each: a `native` card shows "Answered by the provider's decisions endpoint."; a `structured` card on a dual-capable model shows "Answered by structured generation."; a `structured` card on a model with no native API shows "No native decision API; structured generation will be used."; a refused card shows only the `incapable` sentence.
- [ ] **Step 2:** Run them: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `tests/test_inference_controls.py tests/test_inference_resolve.py tests/test_inference_settings.py tests/test_llm_sampling.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py`; the frontend checks on the touched files; `make check-lint check-mypy PY=$PY`. PASS.
- [ ] **Step 5:** Commit `feat(models): a native decision reports its sampling as n/a, and the decide warning reads the resolution`.

---

### Task 10: `--live` measures what production sends, and can force a backend

**Files:**
- Modify: `evals/runner.py` (`resolve_connections`, `live`), `evals/run.py` (flags), `evals/README.md`
- Test: `backend/tests/test_evals.py`

**Interfaces:**
- `resolve_connections` keeps the whole `ResolvedInference` for a decide case (`require_inference(...)`), and the `.conn` for a generate case. Its return type is `dict[str, dict | ResolvedInference]`.
- **`--provider <id> --model <name>`** (I10): a decide or generate case resolves through `override_inference(...)` with that selection. **Nothing is written to settings**; the store's `config.md` and model facts are byte-identical after the run.
- **`--decide-backend native|structured|chain`** (default `chain`; I10, C1). For a decide case it builds the chain explicitly from the resolution's primary `A0`, and calls `inference.run_stages`:
  - `chain`: `inference.stages(resolved)`, what production sends;
  - `native`: `(Stage("native", without FALLBACK_KEY(A0.conn), None),)`. It refuses (exit 2, one sentence, nothing sent) a provider kind with no native endpoint, or a preset whose `never` holds `decide_native`;
  - `structured`: `(Stage("structured", without FALLBACK_KEY(A0.conn), None),)`. It refuses a primary known unable to generate.
  - So native and structured can be compared **on the same model**, which is what "native wins on evals" (§16) has to mean. Comparing a native-only model against a structured one mixes the model with the backend.
- `live(case, target, record=False, *, client=None, backend="chain")`. For a decide case (`case.schema is not None`):

  ```python
  decision = await inference.run_stages(case.task, ctx["items"], chain, client=c, resolved=target,
                                        explain=ctx.get("explain", ""))
  output = decisions.render(decision.items, ctx["items"], explain=bool(ctx.get("explain")))
  ```

  The grade is unchanged. `Result` gains `note: str = ""`, which carries `f"backend: {decision.backend}"`, and `report` prints it.
- **All five decide cases run**, G's `decide-continuity-identity` and `decide-continuity-reconcile` included. Where a G case's prompt builder does not leave `items` on `ctx`, this task adds it there, and changes nothing else in G's case.
- README: what `--live` now measures for a decide case (the full chain on the Decision role by default); how to compare backends on one model (`--provider/--model` with `--decide-backend native`, then `structured`); that the comparison is what the later "native first" decision waits on; and that it costs money and is never run without the user's approval.

- [ ] **Step 1: Write the failing tests:**
  - `test_live_runs_a_decide_case_through_the_chain`: `runner.live` with a `FakeLLM(decisions=[…])` client and a native-only resolution of `scene-break` grades PASS, with `note == "backend: native"`, and no `complete` is called.
  - `test_live_forces_the_native_backend_on_a_dual_capable_model`: one `native_requests` entry, no `complete`, `note == "backend: native"`.
  - `test_live_forces_the_structured_backend`: one `complete` with the schema, no native request.
  - `test_live_refuses_native_on_a_kind_without_an_endpoint`: exit 2, nothing sent.
  - `test_live_override_writes_no_settings`: `--provider/--model` leaves `config.md` and the facts files byte-identical.
  - `test_live_structured_decide_case_sends_the_schema`: F's assertion, kept through the new path.
  - `test_every_decide_case_runs_through_live`: all five `decide-*` cases, with a fake.
  - Replay is unaffected.
- [ ] **Step 2:** Run them: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `tests/test_evals.py tests/test_eval_graders.py tests/test_decide_gate.py`, then `make check-lint check-mypy PY=$PY`. PASS.
- [ ] **Step 5:** Commit `feat(evals): --live runs decide cases through the chain, and can force a backend`.
- [ ] **Step 6 — REQUIRES THE USER'S EXPLICIT APPROVAL (spends money). Skip unless the controller relays that approval.**
  1. Make a throwaway store: `GRIMOIRE_HOME=$(mktemp -d)`, with only the chosen provider's connection and key copied in. Never point the user's real store's Decision role at anything (I10).
  2. On one dual-capable model the user chooses, run `$PY evals/run.py --live --provider <id> --model <name> --decide-backend native --case decide-scene-break --case decide-voice-drift --case decide-speaker --case decide-continuity-identity --case decide-continuity-reconcile`, then the same with `--decide-backend structured`.
  3. Report both outputs in the PR description, and record in the report that the "native first" decision (ruling 1) now has its measurement. Never add `--record` without separate, explicit approval.
  4. The slice does not wait on this step.

---

### Task 11: Docs, gates, PR

**Files:** `CLAUDE.md`, `CONTRIBUTING.md` (the `test_usage_guard.py` row names `decide_native`), `docs/incoming-llm-capture.md` (the `decision_body` event), `evals/README.md`

- [ ] **Step 1: Docs.**
  - `CLAUDE.md` "Adding an LLM call site?" (decide paragraph, beside G's continuity sentences):
    - the chain (`inference.stages`): native only for a model that cannot generate; what moves an item on: a failed call, never an answer;
    - `decide_native` metered per item, and an unrepresentable item refused unsent with its reason;
    - one attempt for the fallback, native or structured;
    - the capture after the call settles, with its outcome;
    - voice drift's native branch, and that a native answer carries no rationale to any caller (G's continuity mapping included).
  - Observability: native adapters record `decision_body`.
  - Run `tests/test_docs_guard.py tests/test_evals.py tests/test_decide_gate.py`, then `make check-lint check-mypy check-templates PY=$PY`.
- [ ] **Step 2:** Commit `docs: native decisions, the decide chain and its capture`.
- [ ] **Step 3: Gates.** Claude stand-ins for the Codex gates:
  1. A code review of the branch diff.
  2. An adversarial review against this spec. Does H implement §14 row H, §5.3–§5.5 as amended (native only where the model cannot generate, §16), §7.4's native backends as ruled, §8's `n/a`, §9.4's capture, §10's warnings and §6.4's probe? Are the strict-mode limits bounded?
  3. Address every finding, or record why not.
- [ ] **Step 4:** **With the controller's go-ahead, relayed from the user** (M10): push, open the PR (Codex reviews it), wait for **CI green**, and then, with a further go-ahead, merge with rebase and `--ff-only`.

---

## Parallelism and coordination

| Wave | Tasks | Notes |
|---|---|---|
| 0 | 0 | after F's final fix round **and slice G** have merged to `main` |
| 1 | 1 | `decisions.py` only |
| 2 | 2, then 3 | both touch `llm.py`'s `decide_native` and `llm_fakes` |
| 3 | 4, then 5 | 5 deletes what 4's tests build on for F's resolutions; run in order |
| 4 | 6, 7, 8, 9 | disjoint files except `frontend/src/api/types.ts` (7, 8 and 9: different types) and `routes/config.py` (8 `_probe`, 9 the controls body). Parallel worktrees with a hand merge of those two files, or in sequence. 6 must follow 5 (speaker tests); 9 must follow 5 (`native_only`, the settings view's `decision_mode`). |
| 5 | 10 | needs 1 and 4 |
| 6 | 11 | last |

**With slice G** (continuity on `decide()`) — **controller ruling: G goes first** (I11).

- **Why G first.** G switches the continuity checks onto F's structured path and touches neither `inference.py`'s chain nor the adapters. H then rebases onto G, and its native chain reaches G's items automatically, one request per item (G's ruling 7 already makes each item self-contained for that). H's Task 5 rewrites G's decide-only tests along with F's. The reviewer's alternative (H first through Task 5) would have G write native-shaped tests once; the controller chose G first so that G's switch is measured against F's path alone.
- **Rebase point.** H's Task 0 rebases onto `main` after G merges, and records that head. No H code task starts before it.
- **Shared files, and the merge rule for each.** G's text is kept; H adds beside it; H deletes a G assertion only where it is skip-shaped, and converts it as Task 5 names.

| File | G | H | Merge rule |
|---|---|---|---|
| `decisions.py` | `NO_OBJECT`/`NO_ITEM`; `MIN_OPTIONS_WITH_NONE` in `_check_choice`; `was_read`; `offerable` | `ItemResult.backend`, `NONE_KEY` refusal **inside `offerable`**, native helpers, string budgets in `validate`/`chunks`, `native_answer`, `outcome`, `render` | Both edit `_check_choice`: H keeps G's option bound and gets its `NONE_KEY` refusal by editing `offerable`, which `_check_choice` calls, not by adding a second test beside it (N5). Native answers never carry G's details: a body that is not the envelope is `bad_response`, and a question missing from an answered envelope has no detail, as G rules for an item that was read. |
| `backend/tests/test_decisions.py` | G's detail and one-option tests | H's native tests, including the one-option nullable choice | Append; keep every G test |
| the spec: §7.4, §9.4, §14 (and §5.3, §5.5, §10, §16, App. B for H) | G's Task 0 lines (contract, conversions rows, eval gate, no continuity capture, row G) | H's Task 0 lines | H's Task 0 re-reads §7.4 and §9.4 as G left them and places each line beside G's; §9.4 keeps G's "no capture for either continuity decision" |
| `evals/README.md` | the gate's multi-item batch; the two `decide-continuity-*` rows | `--live`, `--decide-backend`, `--provider/--model` | Separate sections |
| `evals/cases.py` | the two `decide-continuity-*` cases | Task 10 may add `items` to their `ctx`, and nothing else | One line per case, if needed |
| `evals/runner.py`, `evals/run.py` | — | Task 10 | H only |
| `routes/scenes.py` | `_resolve_identity`, `_identify`, `_absorb_work` | Task 7's `_stage_voice_drift` and the `"unjudged"` literals | Disjoint functions |
| `routes/continuity.py` | `_adjudicate` | — (reached by Task 4's native fan-out) | H does not edit it |
| `backend/tests/inference_fixtures.py` | uses `decide_only(…, on=)` | adds `neither` | Append |
| G's decide-only tests in `test_absorb_identity.py` and `test_continuity_reconcile_routes.py` | written skip-shaped, on F's skip | Task 5 converts them: native answers; a scripted native failure answered by the fallback; the "no generating fallback" pair moved onto `neither` with every assertion kept | Task 5's grep covers both files |
| `CLAUDE.md` "Adding an LLM call site?" | its continuity sentences | Tasks 5 and 11 | Separate sentences |

- **A native answer carries no rationale, and G's continuity mapping must still receive it** (from G's review, I4). G's close-thread and resolve-commitment verdicts read the rationale as their reason. §7.4 says callers must work without one, and G's plan defines that mapping (`identity.answers_of`, `reconcile.proposals_of` → today's `_decide`). H owes three things:
  1. A native item that answered reaches the mapping as **read**: `backend == "native"`, `rationale == ""`, no `NO_ITEM` detail, no `error` reason. It is never mistaken for an unread item, which `proposals_of` would drop and `answers_of` would leave unchecked.
  2. Task 5 adds `test_a_native_close_verdict_without_a_rationale_takes_gs_mapping` (in `test_continuity_reconcile_routes.py`, on a native-only Decision model). It asserts whatever G's mapping gives as merged. Under G's plan as written, a status verdict with an evidence scene and no reason becomes `uncertain`, so no closure is proposed, the sweep lands, and the candidate is asked again. That is the safe direction. If G's review changes the mapping, the test asserts the new outcome.
  3. Task 5 adds `test_a_native_identity_answer_maps_with_an_empty_reason` (in `test_absorb_identity.py`): an `existing` answer on an offered id is accepted with `reason == ""`.
- **`was_read` and `offerable` are G's, and H keeps them true** (N5). Two obligations:
  1. **`offerable` carries `NONE_KEY`.** See Task 1. G's builders drop a `<none>` id with no change of their own.
  2. **A native `refused` or `abstained` on a `decision` question is not read.** G's mappings leave that row `unchecked` or that candidate without a proposal, which agrees with ruling 4 (a refusal is final and unanswered). A native tie or `NONE_KEY` on a `decision` question is `abstained` (it never allows none, so a `NONE_KEY` answer is `unreadable` with no detail, which is read and becomes `uncertain`, as G's unknown-word rule does). H adds no new `Answer` reason or detail without updating `was_read` and its test in the same commit: a reason `was_read` does not know would be treated as read and stored as `uncertain`. Task 1's `test_native_answer_*` set gains one assertion over `was_read` for each outcome `native_answer` can give (`refused`, `abstained` and `unreadable` with no detail), and Task 5's native continuity tests assert that a refused item leaves its row `unchecked` and its candidate unproposed.
- **The gate is untouched by H.** H changes no structured output, so `$PY evals/run.py --gate` prints G's two lines and F's three as PASS after Tasks 1, 4 and 5.
- **Absorb's budget.** G's identity `around` is `budget.run`, which is stateless per call, so up to `NATIVE_CONCURRENCY` concurrent native items under it are safe. An item it refuses (`BudgetRefused`) moves on, and the next stage's `around` refuses it too, unsent.
- **If G stalls**, the controller may reverse the order (H first through Task 5). In that case, Task 5's grep must include `test_absorb_identity.py` and the continuity route suites, and G must not add assertions on `skipped` or `skip_text`.

**With slice E** (pricing):
- If E is not in H's base, the E/H integration step adds `test_a_pricing_wildcard_does_not_price_the_decision_probe` and makes `estimate_from_rates` honour `Probe.priceable` (I6). It also checks that `decide_native` still installs no `Estimate` once E's facade carries one, with `test_a_native_row_without_reported_usage_is_unpriced_not_estimated` run against the real facade and a `count_tokens` counter (I7).

**With slice I** (adapter registry):
- I moves `decide_native` into the per-adapter `decide` operation and deletes the lowering, `FALLBACK_KEY` and `STRUCTURED_KEY`.
- `stages` is the seam I keeps: a stage then names an adapter operation instead of a conn.
- I inherits two dependencies on `FALLBACK_KEY` from H, and row I's retirement must re-express both as stage data: `stages` stripping the key from a stage's conn, and ruling 6's attach rule (the fallback rides the facade only when it is structured and the primary generates) (I11).
- Strict mode's string budgets are H's (ruling 14), so I need not add them.

## Rulings (Task 0 folds each into the spec)

1. **Native only where the model cannot generate** (C1, §16). An attempt is `"native"` only when `generate` is a known non-guess `no` and `decide_native` is not a known `no` (unknown is allowed, §5.3). It is `"structured"` when it can generate, whatever `decide_native` says. "Native first for a model that also generates" is a later user decision, made only after Task 10's same-model measurement, and it is not this slice's.
2. **F's skip is deleted.** A model that cannot generate is served natively, so `skipped`, `skip_text` and the skip notice go. The Decision card resolves as `decide`, and card and row read one refusal (§5.3, §12).
3. **`OPERATION_CAPABILITY["decide"]` is "decide_native or generate".** It is missing only when both are known `no`, and the sentence then says "cannot generate text or make native decisions", composed from `CANNOT`'s two entries.
4. **What moves an item to the next stage** is a failed call: an `LLMError`, including a 2xx body that is not the documented envelope, or an envelope that answers none of the item's questions (`bad_response`, I2), and an item refused unsent (code `native_unrepresentable`). An answer never moves an item, including a native refusal (`refused`) and a `None` from a well-formed body.
5. **The fallback gets one stage**, native or structured by ruling 1 applied to its own capabilities (§5.5's "one attempt").
6. **The fallback rides the facade** (`FALLBACK_KEY`) only when it is structured and the primary generates. Otherwise it is its own stage. A same-provider fallback is kept when the primary cannot generate, because the next step is a different model on another backend, not a retry.
7. **A `PresetRefusalError` stops the chain**, as it stops the facade. That includes a native fallback stage that would take no sampling: the user should fix the preset, as the facade's message says (M8).
8. **Native normalisation:**
   - the provider's explicit answer wins (a choice's `choice`; a predicate's bool, which neither provider sends today);
   - otherwise the argmax of its distribution, and a tie is `abstained`;
   - a predicate's probability is P(true), and exactly 0.5 is `abstained`;
   - a provider's weighted `score` is never an answer (ruling 24);
   - distributions are keyed by option id or level index, with `NONE_KEY` (`"<none>"`, reserved) for the reserved none;
   - an invalid report is dropped, never repaired.
9. **`confidence` is not carried, from either provider.** The contract has no field for it, and §7.4 forbids a synthetic confidence.
10. **A native row is priced only from what the provider reports** (I7). `cost_usd` is filed when the provider reports a cost (OpenRouter's `usage.cost`). Reported tokens are filed as reported. `modelled_usd` is never computed for a native row: neither provider is documented as billing decisions per token on both sides like chat (OpenAI bills input only), so chat rates would mis-model it. That is enforced at read time (Task 3, controller ruling): `store.usage.Rates.estimate` returns None for a row whose `decision_mode` is `native`, which every modelled figure (the rollups, the per-turn rows, the rail's aggregate at `usage_rollup.VERSION = 6`) passes through, and `unpriced_models` never lists one. Without a reported cost the row is unpriced, and it never carries a local token estimate. An unpriced native row is counted in its own `unpriced_native_calls` (version 6 is the bump that added it; 5 was the guard alone), so the cost card does not ask for per-token rates that could price nothing, and Housekeeping's `in_use.unpriced` skips every generative use of a native-only model.
11. **A native 4xx in `NATIVE_REJECTED_STATUSES`** (`REJECTED_STATUSES` plus 403, M6) **does not mark the connection failing.** The connection answered; the decisions endpoint refused that model, key or request (F's CODE-M5 reasoning).
12. **Retries** (I3): a primary stage takes the facade's budget; a fallback stage takes 0, native or structured (§5.4). `LLMClient.complete` takes an optional `retries=`, which overrides the primary route's count only when given.
13. **The decide capture runs after each call settles**, as `(messages, outcome, conn)` (I4, M4, M9).
    - The messages are the request as sent: a structured chunk's messages, or a native item's normalised body as one JSON message.
    - They are built only when a capture is given, and off the loop.
    - The outcome is `decisions.outcome`, recorded as a zero-token `decision` section.
    - The capture runs outside the meter, guarded, so it can never fail an answered decision or file an error row.
    - A native stage's conn is captured without `sampling`.
    - One structured call is one capture, its schema-refusal retry included.
    - A failed or timed-out call is captured with its error. A cancelled call is not, because a cancel is the player's own act and a write during cancellation would be cancelled or delay it.
    - Scene-break, voice drift and G's continuity decisions still capture nothing.
14. **Strict mode's string budgets** (15,000 characters of enum strings above 250 values; 120,000 schema characters) are enforced in `decisions.validate` and `chunks` whatever the backend, because the chain can fall to a structured fallback. They are re-checked against OpenAI's docs in Task 1.
15. **Voice drift on a native verdict:** a drift with no note is usable, stages nothing, keeps any standing flag, and is listed in `noteless`. The over-cap and unknown checks still apply.
16. **`NATIVE_CONCURRENCY = 4`**, argued structurally and to be tuned later. The native items of one stage run in one `asyncio.TaskGroup`, so an unexpected exception or a cancel leaves no request running (I5).
17. **The `decide_native` probe** is one fixed predicate (I6, M5).
    - It is never priced: `Probe.priceable=False`, read by every estimator, because a catalog row does not say how a decision is billed and a 0-token probe must not read as $0.00.
    - The Decision picker offers it only for a model known unable to generate. The provider page's explicit Test… may list it for any model.
18. **A native attempt reports every control `n/a`.** The controls API takes `operation`.
19. **`Decision.backend`, `provider` and `model` are empty when answers came from more than one backend or route.** One backend and one route answering name themselves (G's rule for `provider` and `model`, extended to `backend`); a fallback stage that took the items the primary failed leaves all three empty, because naming the first stage would credit it with answers it never gave. The per-item truth is `ItemResult.backend`; `served` and `errors` span every stage. (Supersedes "the first stage that answered".)
20. **`--live` runs decide cases through the chain** on the whole resolution by default, and grades `decisions.render` of the result (I10). `--decide-backend native|structured` forces one backend on the resolved primary, and `--provider/--model` selects through `override_inference`, writing no settings. Every `--live` run needs the user's approval.
21. **OpenRouter `provider.require_parameters`** (F's final review) stays out of H: the schema is always in the prompt, so routing to a provider that ignores `response_format` is harmless. It is left to I.
22. **The native wire shapes** (I1; from OpenRouter's Jev tutorial and OpenAI's Decisions guide, read 2026-10-08):
    - OpenRouter: `{model, state, questions}`, with `questions` an object keyed by question id; answers an object keyed by question id, each tagged with `type`.
    - OpenAI: `{model, input, questions}`, with `questions` an array, each carrying a unique `name`; answers an array matched by `name`.
    - Grimoire ids are sent verbatim unless a documented charset forbids one, and then positionally, reversibly, inside the adapter.
23. **`allow_none` adds one reserved option** (I1). Neither provider has an explicit "none" (OpenRouter's tutorial recommends adding one; OpenAI's guide, a fallback option). Its wire key is `none`, or `none_2`, `none_3`, … when an option already uses it, and its description is `NATIVE_NONE_TEXT`. Choosing it, or a distribution whose argmax is on it, is `abstained`. A one-option nullable choice (G's ruling 2) is therefore a two-option native choice.
24. **Question kinds map one to one** (I1):
    - predicate → OpenRouter `noul` (no criteria) / OpenAI `predicate`; the answer is from P(true);
    - choice → `choice`, with OpenRouter criteria `{key: description}` / OpenAI `choices [{value, description}]`; the answer is the explicit `choice`, else the argmax of `probabilities`;
    - score → `score`, with OpenRouter criteria `[descriptions]` / OpenAI `levels [{label: str(i), description}]`; the answer is the **argmax of the per-level probabilities**, a tie is `abstained`, and with no usable probabilities it is `unreadable`. The weighted `score` (fractional on both) is never rounded into an answer.
25. **An item a decisions endpoint cannot represent cannot go native** (I1). Today that is a nullable choice whose options plus the reserved none pass 255; Tasks 2 and 3 add any further documented limit. `decisions.native_gap` names it, and `decide_native` refuses it unsent with `LLMError("bad_response", <the sentence>, code="native_unrepresentable")`. It moves on to a structured fallback stage when there is one. On a model that cannot generate, with no such stage, it fails with that reason, which reaches the caller and the error store.
26. **Refusals:** OpenAI's per-answer `"type": "refusal"` is `refused` for that question. OpenRouter documents none, so none is mapped until a reference does. An answer of the wrong `type` is `unreadable`.
27. **Usage:** OpenRouter's `{input_tokens, output_tokens, cost}` and OpenAI's `{input_tokens, output_tokens, input_tokens_details}` are mapped explicitly, never through the chat parser. OpenAI's guide documents no usage, but its API reference does (re-read in Task 3, 2026-10-08), so its rows carry the reported counts, an absent field filing nothing. Neither provider's native row is ever modelled; that is enforced at read time (ruling 10).
28. **The docs are re-read at implementation time** (Tasks 1–3, Step 1). A contradiction of rulings 22–27 stops the task and is reported; an open point is recorded in Appendix B.
29. **The Models page's decide warning reads the resolution's `decision_mode`** (I9, §10): native, structured on a dual-capable model, structured with no native API, or the refusal's own sentence. The frontend keeps no capability rule of its own.

### Plan review answers (2026-10-08), one line each

- **C1** upheld by the controller: rulings 1 and 18; §5.5's sentence and §16 amended in Task 0; Task 5's `test_a_dual_capable_primary_stays_structured`; Task 4 drops the unreachable structured-after-native stage (the controller's "drops the unreachable path") and its end-to-end test; Task 8 offers the probe only to models that cannot generate; Task 10's `--decide-backend`.
- **I1** ruled now (controller): rulings 22–28, Appendix B corrected in Task 0, and a re-read step that stops on a contradiction.
- **I2** applied: an envelope answering no question is `bad_response`; a partly answered one is `unreadable` per missing question, with a warning.
- **I3** applied: every fallback stage takes 0 retries, through `complete(retries=)` (ruling 12).
- **I4** applied: the capture is outside the meter, guarded, built off the loop only when asked for, and records the native request body; a cancelled pick loses its entry, for the reason in ruling 13.
- **I5** applied: `asyncio.TaskGroup`, `LLMError` caught per item, and a cancellation test.
- **I6** applied: `Probe.priceable=False`, read by both estimators, with the wildcard test placed by whether E is in the base.
- **I7** applied: ruling 10 rewritten; `decide_native` installs no `Estimate`, and it is tested.
- **I8** applied: `inference_fixtures.neither`; F's CODE-I1 tests and G's no-fallback tests ported with every assertion; the example sentence corrected; the unknown-allowed test added.
- **I9** applied: `decision_mode` on the settings view (Task 5), and the warning keyed on it (Task 9, ruling 29).
- **I10** applied: `--decide-backend`, `--provider/--model` with no settings write, a throwaway `GRIMOIRE_HOME`, and approval still required.
- **I11** re-derived against G's actual plan, and the controller rules **G first**: shared files, merge rules and the rationale obligation are under **Parallelism and coordination**.
- **G's review I4** (controller): a native answer carries no rationale, and Task 5 tests that it reaches G's continuity mapping as a read item (coordination section).
- **G's re-review N5** applied: H consumes `was_read` and `offerable`; `NONE_KEY` is refused inside `offerable` (Task 1, with a test that G's builders drop it), and a native `refused`/`abstained` is unread (coordination section).
- **M1** applied: Base names `llm.SchemaRefusalError` and `inference._ask`; two retry tests added.
- **M2** applied: `stages` copies, never pops, and is tested.
- **M3** applied: the re-raised error names the fallback's failure.
- **M4** applied: a native capture's conn has no `sampling`.
- **M5** applied with C1 (Task 8).
- **M6** applied: 403 joins `NATIVE_REJECTED_STATUSES`.
- **M7** applied: the phrase is composed in `incapable_text`, and `CANNOT` gains no operation key.
- **M8** applied: ruling 7 says so.
- **M9** applied: one structured call is one capture.
- **M10** applied: Task 11 Step 4 waits for the go-ahead.
- **M11** applied: `test_native_answer_explicit_predicate_wins_over_its_probability`.
- **Execution rules unchanged:** targeted tests only, never the full suite; `--live` and `--record` need the user's explicit approval; Claude stand-in review gates.

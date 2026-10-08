# Inference slice H — native decisions: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `decide()` be answered by a provider's own decisions endpoint (OpenRouter, OpenAI) wherever the selected model is known to decide natively. The chain is native, then structured generation on the same selection, then the role fallback. It replaces slice F's decide skip, and the capture, the voice-drift check, the model test call and the Models page say what happened.

**Architecture:**

- **The wire lives in the adapters.** Each adapter is a gateway module (#239) and normalises its provider's body into `grimoire.decisions`:
  - `OpenRouterClient.decide` and `OpenAICompatibleClient.decide` send one item;
  - `decision_body` / `decision_result` beside each are pure.
- **The facade sends one native attempt.** `LLMClient.decide_native(item, conn, usage, *, retries)` dispatches by kind. It carries the per-attempt bookkeeping `_resilient` carries for a stream: stamp, capture sink, retry on `RETRYABLE_KINDS`, health observer. It never falls back.
- **The chain sits in `inference.decide`.** A pure `stages(resolved)` turns a decide resolution into an ordered list of `(mode, conn)` stages. `decide` runs the pending items through each stage's backend in turn (`_BACKENDS["native"]` is new, `_BACKENDS["structured"]` is F's). An item moves on only when its stage *failed* to answer it.
- **The resolver says which backend serves an attempt.** `decision_mode` returns `"native"` where `decide_native` is `yes`. `OPERATION_CAPABILITY["decide"]` becomes "decide_native or generate", and F's skip is deleted: the attempt it passed over is now served natively.
- **Every call site stays as F left it**, except the speaker's capture lambda (Task 6) and voice drift's native branch (Task 7, spec ruling 12).

**Tech Stack:** Python ≥3.11, FastAPI, httpx, Jinja2, pytest; React + vitest for three small frontend changes; the offline eval suite under `evals/`.

**Spec:** `docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md`.

- Slice H is §14 row H.
- It draws on §5.3–§5.5, §6.1, §6.2, §6.4, §7.2, §7.4, §8, §9.3, §9.4, §10, §12, §14.1, §15 "Decide" and Appendix B.
- Read slice F's plan (`docs/superpowers/plans/2026-10-08-inference-slice-f-decide.md`), and its section "How G and H extend this without reshaping it" first.
- Task 0 amends the spec for this plan's rulings.

**Base:** `claude/inference-slice-h`, which sits at slice F's `b2c85c6`. F's final fix round is still landing on `claude/inference-slice-f`. Task 0 rebases onto it, because this plan consumes four of its pieces:

- `llm._schema_refusal(exc, conn) -> bool`, and the same-attempt retry without structured mode that `inference._structured` makes on a schema refusal with nowhere to fall;
- `resolve._skippable` and the same-provider fallback it keeps for the skip (`6cfff01`), which Task 5 replaces;
- the Decision card's routes list (`ModelsView.DecisionRoutes`, F's CODE-M3);
- the gate's `ruling` field (F's SPEC-M-5).

## Global Constraints

**The user's rules:**

- **Never run the full test suite, and never `make check` or `make check-py`.**
  - Each task runs only the test files it names, plus `make check-lint`, `make check-mypy` and `make check-templates`.
  - A task that touches `frontend/` also runs `npm run typecheck`, `npx vitest run <its files>` and `npx eslint <its files>`, all from `frontend/`. Run `npm ci` in the worktree's own `frontend/` once first, and never symlink `node_modules`.
  - CI runs the full suite. **Merging waits for CI to be green.**
- **Never spend money without asking.**
  - No task makes a live LLM call. Every test uses `backend/tests/llm_fakes.py`, `httpx.MockTransport` and hand-authored canned bodies.
  - **Every `evals/run.py --live` and `--record` step is marked _requires the user's explicit approval_.** It is skipped unless the controller relays that approval. It is not part of any task's completion: the slice is complete and testable offline without it.
- **Reading provider docs is not a call.** Tasks 1–3 begin by reading current provider documentation (no API request, no key). If the docs cannot be reached, or contradict a shape §7.4 or Appendix B pins, **stop and report to the controller** before coding. Never guess an API shape.
- **Review gates:**
  - Each task gets a Claude stand-in spec and quality review, which serves as CLAUDE.md's Codex gates until the PR.
  - The final review pair (code review of the branch, then an adversarial review against spec row H) is also Claude stand-ins.
  - Codex reviews the PR.

**Rule 3 (behaviour-neutral where nothing chose native):**

- `backend/tests/fixtures/inference_baseline.json`, `inference_baseline_c.json` and `test_lore_golden`'s golden are **never regenerated**.
- A store whose models are not known to decide natively resolves every decide task exactly as F does: mode `structured`, the same attempts, the same `FALLBACK_KEY` attach. Tasks 4 and 5 run both equivalence suites.
- A generate resolution's dicts stay byte-identical.

**Spec rules that bind here:**

- §12: an unanswerable question is `answer: None` plus a `reason`, never a guessed default. That holds for a native body too: a tie, a value outside the options, or a missing answer is `None`.
- Rule 5: a price nobody reported is never rendered as zero. A native row with no reported price and no rates is unpriced, and the probe's estimate is `None`.
- §5.3: `unknown` is allowed and only a known `no` refuses.
- §5.5: the decide chain, and one attempt for the fallback.

**CLAUDE.md rules this slice touches:**

- **Imports:** all at module scope; the graph stays acyclic (`test_import_guard.py`).
  - `decisions.py` stays a stdlib-only leaf.
  - `openrouter.py`, `openai_compatible.py` and `llm.py` may import `decisions`.
  - `store/inference/probes.py` may import `decisions` (a gateway leaf, as it imports `content_parts`).
- **Faking the LLM:** only `llm_fakes.py`, injected at `routes.get_llm`. Canned native bodies live under `backend/tests/fixtures/llm/native/` and are hand-authored from the documented shape, never recorded.
- **Instrument LLM failures at `usage.Meter.done`:** every native call runs under a meter that `inference.decide` (or the probe route) opens. `test_usage_guard.py` learns `decide_native`.
- **Observability:** the native adapters record the decoded response body through `llm_capture.emit` (event `decision_body`) before reading fields, and `http_error_body` on a refusal. Never the request, URL or headers.
- **pydantic:** plain `BaseModel` fields only, so the controls body's new field is `operation: str = ""`.
- **Privacy:** invented names only (Seraphine, Mara, Winifred, Rowan, Tobin, Saltmarch). No constant is justified by measuring a store. `NATIVE_CONCURRENCY` is argued structurally.
- **Lint gates are ratcheted:** when a count shrinks, run `make baseline PY=$PY` and commit the smaller file with the fix.
- **Linear history:** rebase and `--ff-only`, never a merge commit.

**Commands** (the worktree has no venv of its own):

- `PY=/home/user/grimoire/backend/.venv/bin/python`
- Backend tests: `cd backend && PYTHONPATH=src $PY -m pytest tests/<file> -q`
- Gates: `make check-lint PY=$PY`, `make check-mypy PY=$PY`, `make check-templates PY=$PY`
- Offline evals: `tests/test_evals.py`, `tests/test_eval_graders.py`, `tests/test_decide_gate.py`.

## Review Focus

1. **A native-only Decision model on a single OpenRouter provider, whose native call fails** (a retired model answers 404, or a 5xx), with a generating fallback on the same provider.
   - The fallback answers through structured generation.
   - The failed native call is its own ledger row: `error`, `decision_mode: "native"`.
   - A 404 from the decisions endpoint does not turn the connection's health dot red while it serves every generate call.
   - Tests: Task 5 `test_a_native_failure_falls_to_a_same_provider_generating_fallback`; Task 2 `test_a_refused_native_request_does_not_mark_the_connection_failing`.
2. **A well-formed native body that does not answer cleanly:** an option the item never offered, a question missing from the body, a probability of exactly 0.5, or a distribution whose values are not probabilities.
   - Each is `None` with a reason (`unreadable`, with `not_an_option` where it applies; `abstained` for a tie), never a default.
   - The item does **not** fall through to the next stage, because an answer is final.
   - The caller's safe direction holds: no break, a failed voice check, control back to the player.
   - Tests: Task 1 `test_native_answer_*`; Tasks 2 and 3 `test_decision_result_reads_the_canned_bodies`.
3. **A dual-capable model** (`decide_native` and `generate` both `yes`) on the Decision role whose decisions endpoint is down.
   - The same items are answered by structured generation on the same selection, then by the fallback.
   - The rows say `native` (error), then `structured`.
   - Test: Task 4 `test_native_failure_moves_to_structured_on_the_same_selection`.
4. **Many items natively** (slice G's continuity sweep sends one item per row), one of which fails.
   - At most `NATIVE_CONCURRENCY` requests are in flight.
   - Only the failed item moves to the next stage.
   - Results keep input order, and nothing answered at all re-raises the first `LLMError`.
   - Tests: Task 4 `test_native_concurrency_is_bounded` and `test_native_items_fall_through_alone_and_keep_their_order`.
5. **A standing voice-drift corrective judged natively.**
   - A native `drift` with no note keeps the standing flag: nothing staged, nothing cleared. The character is listed in `noteless`.
   - A native `in_voice` still proposes the clear.
   - A structured `drift` with no note still fails the check.
   - Tests: Task 7 `test_native_drift_without_a_note_keeps_the_standing_flag` and `test_structured_drift_without_a_note_still_fails`.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| the spec | the rulings below, and Appendix B re-checked | 0, 1–3 |
| `backend/src/grimoire/decisions.py` | `ItemResult.backend`; `NONE_KEY`; `native_answer`; strict-mode string limits in `validate` and `chunks`; `outcome`; `render` | 1 |
| `openrouter.py`, `openai_compatible.py` | `decision_body`, `decision_result`, `*Client.decide` | 2, 3 |
| `llm.py` | `LLMClient.decide_native` | 2, 3 |
| `backend/tests/llm_fakes.py`, `backend/tests/fixtures/llm/native/**`, `fixtures/llm/README.md` | `FakeLLM(decisions=)`, `decide_native`; canned bodies | 2, 3 |
| `backend/src/grimoire/inference.py` | `Stage`, `stages`, `_native`, the chain in `decide`, `_Call.conn`/`retries`, generic `Around`; the capture after the call settles | 4, 6 |
| `store/inference/resolve.py`, `resolved.py`, `settings.py`, `capabilities.py`, `routes/common.py` | `generates`; native `decision_mode`; the widened `OPERATION_CAPABILITY`; skip removed; fallback attach rule; `incapable` wording; Decision card resolved as decide | 4, 5 |
| `routes/character_turns.py` | the speaker's capture lambda; `_capture(outcome=)` | 6 |
| `store/voice_drift.py`, `routes/scenes.py`, `frontend/src/api/types.ts`, `frontend/src/components/review/ReviewPanel.tsx` | the native "verdict without a note" branch | 7 |
| `store/inference/probes.py`, `routes/config.py`, `frontend/src/api/types.ts`, `routes/ProvidersView.tsx`, `components/inference/ProviderModelPicker.tsx` | the `decide_native` probe | 8 |
| `llm_sampling.py`, `store/inference/controls.py`, `routes/config.py`, `components/inference/ControlsReadout.tsx`, `routes/ModelsView.tsx`, `api/client.ts` | `n/a` controls for a native-only attempt | 9 |
| `evals/runner.py`, `evals/README.md` | `--live` runs decide cases through `inference.decide` | 10 |
| `CLAUDE.md`, `CONTRIBUTING.md`, `docs/incoming-llm-capture.md`, `evals/README.md` | docs | 5, 11 |

---

### Task 0: Rebase and settle the spec

**Files:** the spec (§5.3, §5.4, §5.5, §6.4, §7.4, §8, §9.3, §9.4, §10, §12, §14 row H).

- [ ] **Step 1: Rebase.**
  1. Wait for F's final fix round to be committed on `claude/inference-slice-f`.
  2. `git rebase claude/inference-slice-f` (or onto `main`, if F has merged).
  3. Confirm the four pieces named under **Base** exist. If any is named or shaped differently, update this plan's Interfaces blocks before starting Task 1, and say so in the task report.
  4. If D and E have been integrated, also confirm `llm_usage.with_account`, `ACCOUNT_FIELDS` (with `decision_mode`) and `usage.record(decision_mode=)`.
- [ ] **Step 2:** Fold the rulings at the end of this plan into the spec, one line each, in the section each names. §14 row H becomes "settled". Remove §5.3's "until then the Models page can disagree with itself" paragraph and §12's skip-notice sentence, replacing each with one line on what H does.
- [ ] **Step 3:** Commit `docs(spec): settle slice H's native chain, capture and probe`.

---

### Task 1: The contract's native half

**Files:**
- Modify: `backend/src/grimoire/decisions.py`
- Test: `backend/tests/test_decisions.py`, `backend/tests/test_character_turns.py` (one test)

**Interfaces:**
- Consumes: F's `decisions` as it stands.
- Produces:
  - **`ItemResult.backend: str = ""`.** `"structured"` or `"native"` (a member of `BACKENDS`) on an answered item; `""` on one built by `unanswered`. `parse` leaves it `""`, and the backend stamps it (Task 4).
  - **`NONE_KEY = "<none>"`.** The distribution key for an `allow_none` choice's explicit none. `_check_choice` refuses an option id or alias whose `normalise` equals `NONE_KEY`.
  - **Strict-mode string budgets.** The values are checked in Step 1 before they are coded:
    - `MAX_ENUM_STRING_CHARS = 15_000`;
    - `ENUM_STRING_CHARS_ABOVE = 250`;
    - `MAX_SCHEMA_STRING_CHARS = 120_000`.
  - **`schema_chars(items: Sequence[Item]) -> int`.** The characters of every key of every `properties` mapping, plus every string `enum` value, in `schema(items, explain=True)`. That is the worst case: `rationale` counts.
  - **`validate`** also refuses:
    - a `Choice` with more than `ENUM_STRING_CHARS_ABOVE` options whose ids total more than `MAX_ENUM_STRING_CHARS` characters;
    - an item with `schema_chars([item]) > MAX_SCHEMA_STRING_CHARS`.
    - Checked whatever the backend, because the chain can fall to structured (ruling 14).
  - **`chunks(items, size=MAX_ITEMS_PER_CALL, budget=MAX_ENUM_VALUES, chars=MAX_SCHEMA_STRING_CHARS)`.** Also closes a chunk before `schema_chars(held + [item]) > chars`. An item that alone exceeds `chars` is a chunk of its own; `validate` refuses it first.
  - **`UNSTATED: Final = object()`**, the sentinel for "the body named no answer".
  - **`native_answer(q: Question, *, chosen: object = UNSTATED, probability: object = None, distribution: object = None, refused: bool = False) -> Answer`.** Adapters hand it ids already mapped back to Grimoire's (option ids, level indices). In order:
    1. `refused` gives `Answer(None, "refused")`.
    2. Validate the report:
       - `probability` is kept only when it is a finite non-bool number in [0, 1];
       - `distribution` is kept only when it is a mapping whose keys are all legal (a choice: its option ids, plus `NONE_KEY` when `allow_none`; a score: `str(i)` for each level) and whose values are all finite numbers in [0, 1];
       - an invalid report is dropped whole, never repaired.
    3. **Predicate:**
       - `chosen` a `bool` is the answer;
       - else the probability decides: `> 0.5` is `True`, `< 0.5` is `False`, `== 0.5` is `Answer(None, "abstained")`;
       - with no usable probability, `unreadable`;
       - `probability` is carried as P(true).
    4. **Choice:**
       - `chosen` an exact option id is that option (aliases are not consulted: §7.4);
       - `chosen` `None` or `NONE_KEY` with `allow_none` is `abstained`; without it, `unreadable`;
       - any other present value is `unreadable` with `detail=NOT_AN_OPTION`;
       - `chosen` UNSTATED with a distribution takes its argmax, and a tie at the maximum is `abstained`. The argmax landing on `NONE_KEY` is `abstained`.
    5. **Score:** an `int` (not `bool`) in `range(len(levels))`, else the distribution's argmax under the same tie rule, else `unreadable`.
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
  - `test_native_answer_choice`:
    - an exact id;
    - `"Characters:Mara"` (not exact) gives `unreadable`/`not_an_option`;
    - `None` with `allow_none` gives `abstained`, and without it `unreadable`;
    - `NONE_KEY` likewise;
    - a distribution `{"characters:mara": 0.6, "grimoire": 0.4}` with no `chosen` gives `characters:mara`, carrying the distribution;
    - a tie gives `abstained`;
    - a distribution with a key `"characters:rowan"` (not offered) is dropped, and the answer comes from `chosen` alone.
  - `test_native_answer_score`: index 2 of 3; `3` gives `unreadable`; `True` gives `unreadable`; a distribution `{"0": .1, "1": .2, "2": .7}` gives `2`.
  - `test_native_answer_refused`
  - `test_outcome_omits_empty_fields` and `test_outcome_of_a_failure`
  - `test_render_round_trips_answered_values`: for answered results of each type, `parse(render(r, items, explain=True), items, explain=True)` gives the same `answer` values and rationale.
  - In `tests/test_character_turns.py`, `test_a_roster_past_the_schema_string_budget_raises_an_issue`: 254 eligible refs whose ids push the choice past `MAX_ENUM_STRING_CHARS` give `INVALID_HANDOFF` through `_select`, and nothing is sent.
- [ ] **Step 3:** Run them: FAIL.
- [ ] **Step 4:** Implement in `decisions.py`. It stays stdlib-only.
- [ ] **Step 5:** Run `tests/test_decisions.py tests/test_character_turns.py tests/test_inference_decide.py tests/test_decide_gate.py`, then `make check-lint check-mypy PY=$PY`. PASS.
- [ ] **Step 6:** Commit `feat(decisions): the native half of the contract, and strict mode's string budgets`.

---

### Task 2: OpenRouter's native adapter, and the facade's native attempt

**Files:**
- Modify: `backend/src/grimoire/openrouter.py`, `backend/src/grimoire/llm.py`, `backend/tests/llm_fakes.py`, `backend/tests/fixtures/llm/README.md`, the spec's Appendix B
- Create: `backend/tests/fixtures/llm/native/openrouter/{answered,none,refused,malformed,error_400}.json`, `backend/tests/test_native_decisions.py`

**Interfaces:**
- Consumes: Task 1 (`native_answer`, `ItemResult.backend`, `NONE_KEY`); the facade's `_stamp`, `_observe`, `_backoff_delay`, `RETRYABLE_KINDS`, `RETRY_AFTER_CAP`, `REJECTED_STATUSES`, `_without_fallback`, `effective_model`, `llm_capture.Capture`/`emit`; `llm_usage.from_openai_chunk`.
- What §7.4 and Appendix B pin, and what this task must not contradict:
  - `POST https://openrouter.ai/api/alpha/decisions`;
  - body `{model, state: <item.context>, questions}`;
  - Grimoire `predicate` → `noul` (true/false probability);
  - `choice` → `choice`, with a criteria map from option descriptions and `allow_none` as its explicit `none`;
  - `score` → `score`, ordered criteria (2–10 levels);
  - answers keyed by question id, with distributions.
- **What the spec does not pin, and Step 1 must establish from current docs:**
  - the auth header (expected to be the chat endpoint's bearer key; confirm);
  - each question object's field names (its id key, its instructions key);
  - how the criteria map is keyed, and how `none` is declared;
  - the response envelope: where answers sit, and the field names of the chosen value, the probability and the distribution;
  - how a refusal is signalled;
  - the usage/cost block;
  - the error body;
  - any limit (questions per request, context length, label charset).
- **Produces:**
  - `openrouter.DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"`.
  - `openrouter.decision_body(item: decisions.Item, model: str) -> dict`:
    - pure; ignores aliases and `explain`.
    - When the docs constrain question or option labels in a way Grimoire ids can break (for example `characters:mara`), map them positionally (`q0…`, `o0…`) with the description carrying the meaning. The mapping must be reversible and is used only inside this module.
  - `openrouter.decision_result(body: object, item: decisions.Item) -> decisions.ItemResult`:
    - pure;
    - raises `OpenRouterError("bad_response", …)` when `body` is not the documented envelope (the item then falls through, ruling 4);
    - otherwise answers every question through `decisions.native_answer`, with `backend="native"` and `rationale=""`;
    - a question missing from a well-formed envelope is `unreadable`.
  - `OpenRouterClient.decide(item, model: str, key: str, *, usage: dict | None = None, timeout: float | None = None) -> decisions.ItemResult`:
    - one POST and no retry; status errors are mapped by `_status_kind`/`_extract_error`, with `Retry-After` read exactly as `stream` reads it;
    - `llm_capture.emit(usage, "http_error_body", scrub(text))` on an error status, and `llm_capture.emit(usage, "decision_body", scrub(text))` on success, before any field is read;
    - the usage block goes through `llm_usage.from_openai_chunk` when the body carries one in that shape; otherwise as Step 1 documents, never inventing a zero.
  - `LLMClient.decide_native(item: decisions.Item, conn: dict, usage: dict | None = None, *, retries: int | None = None) -> decisions.ItemResult`:
    - `conn = _without_fallback(conn)`. A kind with no native adapter (here, anything but `openrouter`; Task 3 adds `openai_compatible`) raises `LLMError("bad_response", "<kind> connections have no native decisions endpoint")` before any request or stamp.
    - Attempts `1 + (self._retry_count() if retries is None else max(0, retries))`. Each attempt:
      - `_stamp(usage, conn, tries)`;
      - installs `llm_capture.Capture(sink, call_id, tries, effective_model(conn), kind)` when the client has a capture sink, as `_resilient` does, with the `start`/`end` events;
      - awaits the adapter with `timeout=self._timeout_seconds()`.
    - Retries only `RETRYABLE_KINDS` whose `retry_after` is not over `RETRY_AFTER_CAP`, after `max(_backoff_delay(n - 1), retry_after)`.
    - Observer: success gives `_observe(conn, None)`; a failure gives `_observe(conn, exc)`, **except** a status in `REJECTED_STATUSES` (ruling 11).
    - Sends no sampling: a native decision takes none (§8).
    - `_resilient` is not changed. A retry-delay helper may be shared only if `_resilient`'s suites still pass unmodified.
  - **`llm_fakes`:**
    - `FakeLLM(..., decisions: list[decisions.ItemResult | LLMError] | None = None)`;
    - `async decide_native(item, conn, usage=None, *, retries=None)` takes the next entry (the last repeats) and records `(item, conn, retries)` in `self.native_requests`;
    - it stamps the holder as the fake's `stream` stamps (model, provider, `ATTEMPTED`, `llm_usage.account`) so the meter files a row;
    - it raises an `LLMError` entry, and stamps `backend="native"` on a returned result that lacks it;
    - `decisions=None` with a `decide_native` call raises `AssertionError("FakeLLM has no native decisions scripted")`;
    - the module docstring's surface list is updated.

- [ ] **Step 1: Read OpenRouter's current docs (no money).** Read the Appendix B link and the API reference for `/api/alpha/decisions`.
  - Record every "not pinned" item above in spec Appendix B under "OpenRouter Decisions (checked <date>)", with the URLs.
  - Author the five canned bodies from the documented examples:
    - `answered` holds one `noul`, one `choice` with `none` allowed and one `score`, with distributions;
    - `none` holds an explicit none;
    - `refused` holds a refusal;
    - `malformed` is a 200 with no answers envelope;
    - `error_400` is the error body.
  - Name their provenance in `fixtures/llm/README.md` ("hand-authored from <URL>, checked <date>; never recorded").
  - **If the docs contradict §7.4's mapping** (no per-question answers, no explicit none, a different endpoint), stop and report; do not code around it.
- [ ] **Step 2: Write the failing tests** (`tests/test_native_decisions.py`, `httpx.MockTransport` replaying the canned bodies; refs `characters:mara` and `characters:winifred`):
  - `test_openrouter_decision_body_maps_each_question_type`: the exact body for a three-question item, with values from Step 1's docs.
  - `test_openrouter_decision_body_ignores_aliases_and_explain`
  - `test_decision_result_reads_the_canned_bodies`:
    - `answered` gives three answers with distributions and `backend == "native"`;
    - `none` gives `abstained`;
    - `refused` gives `refused` on every question;
    - an answered body edited to name `characters:rowan` gives `unreadable`/`not_an_option`;
    - one with a question removed gives that question `unreadable`.
  - `test_a_malformed_body_is_a_bad_response`: `malformed` raises `LLMError` kind `bad_response`.
  - `test_openrouter_decide_posts_once_and_captures_the_body`: one request to `DECISIONS_URL` with the bearer key; a capture sink sees `decision_body` (scrubbed); the key appears in no captured event.
  - `test_facade_decide_native_retries_rate_limits_only`:
    - a 429 then `answered` gives 2 requests and `attempts == 2` in the holder;
    - a 400 gives 1 request and raises;
    - `retries=0` makes one request on a 429.
  - `test_a_refused_native_request_does_not_mark_the_connection_failing`: an observer sees nothing for a 404/400, and sees the error for a 500 and for a network failure.
  - `test_facade_decide_native_strips_the_fallback_and_sends_no_sampling`: a conn carrying `FALLBACK_KEY` and a preset; one request, its body holds no sampler field, and the fallback is never called.
  - `test_facade_decide_native_refuses_a_kind_without_an_endpoint`: `anthropic` and `claude` raise before any request, and the holder stays empty.
  - `test_fake_decide_native_scripts_and_stamps`
- [ ] **Step 3:** Run it: FAIL.
- [ ] **Step 4:** Implement.
- [ ] **Step 5:** Run `tests/test_native_decisions.py tests/test_openrouter.py tests/test_llm.py tests/test_llm_structured.py tests/test_inference_fallback.py tests/test_llm_fakes.py tests/test_import_guard.py`, then `make check-lint check-mypy PY=$PY`. PASS, and every pre-existing suite is unmodified.
- [ ] **Step 6:** Commit `feat(openrouter): native decisions, and the facade's single native attempt`.

---

### Task 3: OpenAI's native adapter

**Files:**
- Modify: `backend/src/grimoire/openai_compatible.py`, `backend/src/grimoire/llm.py` (`decide_native` dispatch), `fixtures/llm/README.md`, spec Appendix B
- Create: `backend/tests/fixtures/llm/native/openai/{answered,none,refused,malformed,error_400}.json`
- Test: `backend/tests/test_native_decisions.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- What §7.4 and Appendix B pin:
  - `POST {base_url}/decisions` (the OpenAI preset's base URL is `https://api.openai.com/v1`);
  - body `{model, input: <item.context>, questions}`;
  - each question carries `name` and `instructions`;
  - `predicate` returns `probability`;
  - `choice` takes `choices` and returns `choice`, `probabilities`, `confidence`;
  - `score` takes `levels` and returns `score`, `probabilities`, `confidence`;
  - public beta.
- **What Step 1 must establish:**
  - whether a beta header is required;
  - the shape of a `choices` entry (a bare string, or an object with a description) and of a `levels` entry;
  - whether `score` answers by index or by label;
  - whether `none` is expressible (if not, `allow_none` is an extra option the adapter adds under a reserved name and maps back to `None`/`abstained`);
  - the charset limits of `name` and of choice labels;
  - the response envelope;
  - the refusal signal;
  - the usage block;
  - the error body.
- **Produces:**
  - `openai_compatible.decision_body(item, model) -> dict`;
  - `openai_compatible.decision_result(body, item) -> decisions.ItemResult`, with the same rules as Task 2. `confidence` is not carried (ruling 9).
  - `OpenAICompatibleClient.decide(item, model, key, base_url, *, usage=None, timeout=None) -> decisions.ItemResult`, with the same capture and error rules as Task 2.
  - `LLMClient.decide_native` dispatches `openai_compatible` to it with `conn["base_url"]`. Only the OpenAI preset reaches it: every other `openai_compatible` preset carries `decide_native` in `never`, so the resolver never makes it native, and `_test_plan` refuses its probe.

- [ ] **Step 1: Read OpenAI's current Decisions guide and API reference (no money).** Record the "must establish" items in Appendix B under "OpenAI Decisions (checked <date>)". Author the five canned bodies and their provenance. Stop and report on a contradiction, as in Task 2.
- [ ] **Step 2: Write the failing tests** (in the same file, parametrized over the provider where the assertion is shared):
  - `test_openai_decision_body_maps_each_question_type`
  - `test_decision_result_reads_the_canned_bodies[openai]`
  - `test_a_malformed_body_is_a_bad_response[openai]`
  - `test_openai_decide_posts_to_the_connections_base_url`: the request URL is `<base_url>/decisions`, and the beta header is present if Step 1 found one is required.
  - `test_openai_confidence_is_not_carried`
  - `test_facade_decide_native_dispatches_openai_compatible`
- [ ] **Step 3:** Run them: FAIL.
- [ ] **Step 4:** Implement.
- [ ] **Step 5:** Run `tests/test_native_decisions.py tests/test_openai_compatible.py tests/test_llm.py tests/test_import_guard.py`, then `make check-lint check-mypy PY=$PY`. PASS.
- [ ] **Step 6:** Commit `feat(openai): native decisions on the OpenAI preset`.

---

### Task 4: The chain in `decide`

No resolution produces `"native"` until Task 5. This task's tests build one with `dataclasses.replace` over a real resolution, so the chain lands and is proven before anything routes to it. Nothing changes behaviour.

**Files:**
- Modify: `backend/src/grimoire/inference.py`, `store/inference/resolve.py` (`generates` only), `backend/tests/test_usage_guard.py`
- Create: `backend/tests/test_inference_decide_native.py`

**Interfaces:**
- Consumes: Tasks 1–3; F's `_structured`, `_with_mode`, `_served_by`, `_BACKENDS`, `_Call`, and the schema-refusal retry.
- **`resolve.generates(attempt: Attempt) -> bool`**: `"generate"` is not in `_missing(attempt, …)` (a known non-guess `no`). F's `decision_mode` uses it in place of its inline check.
- **`inference.NATIVE_CONCURRENCY = 4`.** One request per item; at most this many in flight per stage. Argued structurally: a continuity sweep sends one item per row, and an unbounded fan-out would meet the provider's rate limit as a burst of 429s that the retry budget then pays for. To be tuned against real prompts later.
- **`class Stage(NamedTuple)`**: `mode: str`, `conn: dict`, `retries: int | None` (None means the facade's budget).
- **`stages(resolved: ResolvedInference) -> tuple[Stage, ...]`**, pure. Let `A0 = resolved.attempts[0]`, and `A1 = resolved.attempts[1]` when it exists and `resolved.fallback_missing` is empty, else None. In order:
  1. If `A0.decision_mode == "native"`: `Stage("native", without FALLBACK_KEY(A0.conn), None)`.
  2. If `resolve.generates(A0)`: `Stage("structured", A0.conn, None)`. `A0.conn` carries `FALLBACK_KEY` exactly when the resolver attached a structured fallback (Task 5), and then the facade sends it.
  3. If `A1` is not None, and `A1.decision_mode == "native"` or `not resolve.generates(A0)`: `Stage(A1.decision_mode, without FALLBACK_KEY(A1.conn), 0 if A1.decision_mode == "native" else None)`. One attempt for the fallback, by its own capabilities (§5.5, ruling 5). A structured fallback sent on its own takes the facade's budget, as F's skip sent it (ruling 12).
  - **F's skip still exists in this task.** For a skipped resolution, `A0.decision_mode == ""` and it does not generate, so `stages` yields the fallback's structured stage alone, which is exactly what F sends. For every resolution F can build, `stages` is `(Stage("structured", resolved.conn, None),)` or that one fallback stage.
- **`_Call`** gains `conn: dict` and `retries: int | None`. `_structured` sends `call.conn` (not `call.resolved.conn`), and returns its parsed results with `backend="structured"`. Unanswered and errored items keep `""`.
- **`Around = Callable[[Awaitable[Any], dict], Awaitable[Any]]`.** A native call's awaitable yields an `ItemResult`; F's callers' `around` (`budget.run`) is already generic.
- **`async def _native(items, call) -> decisions.Decision`:**
  - `conn = llm_usage.with_account(call.conn, decision_mode="native")`.
  - Per item, under an `asyncio.Semaphore(NATIVE_CONCURRENCY)`:
    1. open `store.usage.meter(call.task, campaign=…, scene=…, post=…, round_id=…)`;
    2. `pending = call.client.decide_native(item, conn, m.usage, retries=call.retries)`;
    3. `result = await (call.around(pending, m.usage) if call.around else pending)`.
  - An `LLMError` makes that item `unanswered((item,), "error")[0]`.
  - When no item answered, re-raise the first `LLMError`.
  - Returns `Decision(items=<input order>, backend="native", provider/model=_served_by(the first answering holder), usage=<rows>)`.
  - Named `client`, the receiver `test_usage_guard.py` recognises.
- **`decide`** (signature unchanged) replaces the single dispatch with the chain:
  1. Validate as F does, then `chain = stages(resolved)`. An empty chain is a `ValueError`; the seam's refusal makes that unreachable.
  2. `pending = every index`. For each stage, while `pending`:
     - run `_BACKENDS[stage.mode](pending items, replace(call, conn=stage.conn, retries=stage.retries))`;
     - a `PresetRefusalError` propagates at once (the facade's rule, ruling 7);
     - another `LLMError` keeps every pending item pending and remembers the first error;
     - otherwise, an item whose every answer has reason `error` stays pending, and every other item is final.
  3. When no item was ever answered, re-raise the first `LLMError` (F's ruling 8). Otherwise each item still pending becomes `unanswered(…, "error")`.
  4. `Decision.backend`, `provider` and `model` come from the first stage that answered any item (ruling 19); `usage` holds every stage's rows, in order.
  5. The capture keeps F's behaviour here (before each structured send). Task 6 moves it.
- **`test_usage_guard.py`:** `_GENERATORS` gains `"decide_native"`, so every `client.decide_native(...)` under `routes/` or in `EXTRA_SOURCES` must pass `<meter>.usage`.

- [ ] **Step 1: Write the failing tests** (`tests/test_inference_decide_native.py`).
  - The helper `_native_resolution(client, *, fallback, generates)` builds a real decide resolution on an isolated format-2 store (F's `inference_fixtures.format2`/`decide_only`), then `dataclasses.replace`s `decision_mode="native"` onto the primary (and the fallback where asked). The fake is `FakeLLM(turns=[[decision_reply(...)]], decisions=[...])`.
  - `test_stages_for_every_F_resolution_are_Fs`: a structured primary with a structured fallback attached gives one structured stage on `resolved.conn`. F's skipped resolution gives one structured stage on the fallback's conn.
  - `test_stages_for_a_native_only_primary`: native(A0), then the fallback's stage. A0's conn carries no `FALLBACK_KEY` in its stage.
  - `test_stages_for_a_dual_capable_primary`: native(A0), structured(A0, carrying the fallback).
  - `test_stages_for_a_native_fallback`: structured(A0 without `FALLBACK_KEY`), native(A1, `retries=0`).
  - `test_a_native_answer_is_final`: the fake's `refused` result is returned and no structured call is made (`fake.calls == 0`).
  - `test_native_failure_moves_to_structured_on_the_same_selection` (Review Focus 3): native raises `LLMError("network")`, the structured reply answers, `items[0].backend == "structured"`, and the ledger rows are `[("error", "native"), ("ok", "structured")]` (`status`, `decision_mode`).
  - `test_native_items_fall_through_alone_and_keep_their_order` (Review Focus 4): five items; the third's native call fails; one structured call carries only the third; the results are in input order with backends `n, n, s, n, n`.
  - `test_native_concurrency_is_bounded`: ten items through a fake whose `decide_native` records the in-flight high-water mark, which is `<= NATIVE_CONCURRENCY`.
  - `test_nothing_answered_reraises_the_first_error`: native raises A and the structured stage raises B; `decide` raises A.
  - `test_a_preset_refusal_stops_the_chain`: the structured stage raises `PresetRefusalError`; the native fallback stage is never called.
  - `test_around_runs_inside_each_native_meter`: an `around` raising `LLMError("timeout")` files an `error/timeout` row with `decision_mode == "native"`, then the chain moves on.
  - `test_native_rows_carry_operation_and_mode`, and the resolution's own account blocks are unchanged (E's no-mutation rule).
  - `test_decision_names_the_first_answering_stage`
  - Also run F's `tests/test_inference_decide.py` unmodified: every F path is a one-stage chain.
  - `test_usage_guard.py`: a planted `client.decide_native(item, conn)` with no meter is caught.
- [ ] **Step 2:** Run them: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `tests/test_inference_decide_native.py tests/test_inference_decide.py tests/test_usage_guard.py tests/test_operation_guard.py tests/test_scene_break_routes.py tests/test_character_turns.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_import_guard.py`, then `make check-lint check-mypy check-templates PY=$PY`. PASS.
- [ ] **Step 5:** Commit `feat(inference): decide runs native, structured and fallback stages in order`.

---

### Task 5: The resolver serves native, and the skip goes

**Files:**
- Modify:
  - `store/inference/resolve.py`
  - `store/inference/resolved.py`
  - `store/inference/settings.py`
  - `store/inference/capabilities.py` (`CANNOT["decide"]`)
  - `routes/common.py` (`UsableInference.conn`)
  - `CLAUDE.md` (the "Until slice H, a decide-only Decision model is skipped…" sentence, so `test_docs_guard.py` holds while `skip_text` disappears)
- Tests: `test_inference_decide.py` and `test_inference_decide_native.py`, plus the decide-only cases F's fix round put in `test_scene_break_routes.py`, `test_routes.py` (voice drift), `test_character_turns.py` and `inference_fixtures.py` (grep `decide_only` and `skip_text`)

**Interfaces:**
- Consumes: Task 4's `generates` and `stages`.
- **`OPERATION_CAPABILITY: dict[str, tuple[str, ...]] = {"generate": ("generate",), "embed": ("embed",), "decide": ("decide_native", "generate")}`.** Each entry is a set of alternatives.
- **`_needs(route, operation) -> tuple[frozenset[str], ...]`.** The operation's alternatives as one group, and each `route.requires` capability as a group of its own.
- **`_missing(attempt, needs) -> tuple[str, ...]`.** Every member of every group whose members are **all** known `no` (non-guess), in `capabilities.NAMES` order. Callers pass groups; `generates` passes `(frozenset({"generate"}),)`.
- **`decision_mode(attempt) -> str`** (ruling 1):
  - `"native"` when `decide_native` is `yes`;
  - else `"structured"` when `generates(attempt)`;
  - else `"native"` when `decide_native` is not known `no` (unknown is allowed, §5.3);
  - else `""`.
- **The skip is deleted:**
  - `ResolvedInference.skipped`, `_skipped`, `skip_text`, and `_sent`'s skip branch: `conn` is `attempts[0].conn` again, and `fallback` loses its skip branch;
  - `settings._problem`'s skip fallback;
  - `UsableInference.conn`'s skip branch (`return self._sent.conn` after the narrowing assert, per F's review note 9, or `attempts[0].conn`);
  - every reference in code and tests.
- **The same-provider rule** replaces `_skippable` with `_apart(primary: Attempt, operation: str) -> bool` (`operation == "decide" and not generates(primary)`). A same-provider fallback is built when `_apart` holds, and kept as an ordinary fallback: it is its own stage and never rides the facade. F's post-chain trim is deleted; a fallback that cannot serve either is reported in `fallback_missing` like any other.
- **The fallback attach on a decide resolution** (`_chain`): modes are computed **before** the attach. `FALLBACK_KEY` is attached when `not fallback_missing`, the fallback's `decision_mode == "structured"`, **and** `generates(primary)`. Otherwise the fallback stays in `attempts`, unattached, as its own stage. Generate resolutions are unchanged.
- **`incapable`:** on a decide resolution whose `missing` holds both `generate` and `decide_native`, the sentence is `incapable_text(resolved, "decide")`, with `capabilities.CANNOT["decide"] = "generate text or make native decisions"`. For example: "The Scene-break checks route runs on the Decision role (vendor/embedder on OpenRouter), which cannot generate text or make native decisions — choose another Decision model or pin this route."
- **`settings._role_card`** resolves with `operation="decide" if role == "decision" else "generate"`. The Decision card and every decide route row then read the one decision (§12). A native-only Decision model shows no problem on either (ruling 2).
- `STRUCTURED_KEY` is still set by `_flag_structured` on every decide attempt whose `structured_output` is `yes`. A native-only attempt has no structured stage, so the flag is harmless there.

- [ ] **Step 1: Write and rewrite the failing tests.**
  - F's skip tests become native tests (keep the names' subjects, with the verbs changed):
    - `test_resolve_serves_a_decide_only_primary_natively`: OpenRouter, Decision on a model whose catalog `outputs` is `["decisions"]`:
      - `decision_mode == "native"`, `missing == ()`, `refusal is None`;
      - a generating fallback on another provider is attached to nothing and is its own stage;
      - the route row's and the Decision card's `problem` are both None.
    - `test_a_decide_only_model_with_a_same_provider_fallback_keeps_it`: the reviewer's single-provider case. `stages` is native(A0), structured(A1).
    - `test_a_native_failure_falls_to_a_same_provider_generating_fallback` (Review Focus 1), end to end through `decide` with `FakeLLM(decisions=[LLMError(status=404)])`.
    - `test_a_dual_capable_primary_is_native_then_structured_with_its_fallback_attached`
    - `test_a_model_that_neither_generates_nor_decides_is_refused`: 409 `incapable` with the exact sentence above, on the card and on the row alike.
    - `test_an_unknown_native_capability_is_not_refused`: `generate` known `no` and `decide_native` unknown give mode `"native"` and no refusal.
    - `test_a_structured_primary_is_unchanged`: the F resolution, byte for byte (`conn`, `FALLBACK_KEY`, `STRUCTURED_KEY`).
  - The route suites' decide-only cases (scene-break, voice drift, speaker) now script `FakeLLM(decisions=[…])` and assert the native answer is used and nothing goes to `complete`. Their "no fallback" twins no longer 409: a native-only model answers natively. Keep a twin where both capabilities are `no`, which does 409.
  - `test_inference_settings.py`: `test_the_decision_card_resolves_as_a_decision`.
- [ ] **Step 2:** Run them: FAIL.
- [ ] **Step 3:** Implement, and edit CLAUDE.md's sentence to: "A Decision model that decides natively is served by its provider's decisions endpoint first (`decision_mode == "native"`), then by structured generation on the same selection where it can generate, then by the role fallback (`inference.stages`)."
- [ ] **Step 4:** Run:
  - `tests/test_inference_decide.py tests/test_inference_decide_native.py tests/test_inference_resolve.py tests/test_inference_settings.py tests/test_inference_cascade.py tests/test_inference_capabilities.py tests/test_inference_fallback.py`
  - `tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_inference_migrate.py tests/test_scene_break_routes.py tests/test_routes.py tests/test_character_turns.py tests/test_operation_guard.py tests/test_routing_guard.py tests/test_docs_guard.py tests/test_import_guard.py`
  - `make check-lint check-mypy PY=$PY`.
  - PASS. **The equivalence suites pass with no new allowance.**
- [ ] **Step 5:** Commit `feat(inference): a decide-only model is answered natively, and the skip is gone`.

---

### Task 6: Capture — mode, normalised answers and distributions (§9.4)

**Files:**
- Modify: `backend/src/grimoire/inference.py`, `routes/character_turns.py` (`_select`'s lambda, `_capture`)
- Tests: `test_inference_decide.py` (rewrite F's `test_capture_sees_each_chunks_messages_before_it_is_sent`), `test_inference_decide_native.py`, `test_character_turns.py`

**Interfaces:**
- **`Capture = Callable[[list[dict], dict, dict], Awaitable[None]]`**: `(messages, outcome, conn)`.
- **When:** called once per backend call **after it settles**: answered, or failed with an `LLMError`. Never on cancellation (ruling 13).
  - `messages` is the request as sent: the structured chunk's `structured_messages`, or for a native item `native_messages(item)`.
  - `outcome` is `decisions.outcome(mode, provider, model, results)`, or `decisions.outcome(mode, provider, model, error=f"{exc.kind}: {exc.detail}")` on failure.
  - `conn` is the stage's conn (the account-stamped copy), so the capture names the attempt that ran.
  - Each structured chunk and each native item is one call.
- **`native_messages(item: decisions.Item) -> list[dict]`**: `[{"role": "user", "content": prompts.render("decide/user.j2", items=(item,), explain="")}]`. That is the context and questions the native body carries, in the prompt log's vocabulary. No new template.
- **Speaker call site:** `capture=lambda msgs, outcome, conn: run_in_threadpool(_capture, cid, sid, "response-selector", msgs, conn, outcome)`.
- **`_capture(cid, sid, task, messages, conn, outcome: dict | None = None)`**: with `outcome`, the breakdown built from plain messages gains a last section:

  ```python
  {"id": "decision", "label": "decision", "text": json.dumps(outcome, indent=2, ensure_ascii=False),
   "tier": "lock-in", "dropped": False, "pinned": False, "trimmed": 0, "tokens": 0}
  ```

  `total_tokens` excludes it (it was not sent). Nothing else changes, and scene-break and voice drift still pass no capture.
- `verify_templates.py`: `native_messages` equals a direct render of `decide/user.j2`.

- [ ] **Step 1: Write the failing tests:**
  - `test_capture_records_each_structured_call_once_it_settles`: messages, `outcome["mode"] == "structured"`, the normalised answers, and the conn the call was sent on.
  - `test_capture_records_a_native_call_with_its_distribution`
  - `test_capture_records_a_failed_call_with_its_error`
  - `test_a_cancelled_call_is_not_captured`
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
  - `PROBES["decide_native"] = Probe("decide_native", "decide", 0, 0)`
  - `describe("decide_native", capped)` = `"One native decision request: the statement “{DECIDE_CONTEXT}” and the yes/no question “{DECIDE_QUESTION}”."`
  - `estimate_usd` returns `None` whenever `decide_native` is among `caps` (ruling 17).
- **`_probe`:** when `PROBES[cap].operation == "decide"`:

  ```python
  with store.usage.meter("model-test") as m:
      await _bounded_call(client.decide_native(
          probes.PROBE_ITEM,
          llm_usage.with_account(conn, operation="decide", decision_mode="native"),
          m.usage, retries=0))
  ```

  - Success is the request accepted and a body normalised (any `ItemResult`).
  - `_records` and `_halts` apply unchanged; a `bad_response` with no status is not recorded.
  - `_test_plan` already refuses the probe on presets whose `never` holds `decide_native`.
- **Frontend:**
  - `TestableCapability` adds `"decide_native"`, and `TESTABLE` appends it.
  - `probesFor` maps the `decide` need to `["generate", "decide_native"]`. It drops `decide_native` when the row's `decide_native.value === "no"`, then keeps the probes not already `yes`, as today.

- [ ] **Step 1: Write the failing tests:**
  - Backend:
    - `test_the_decide_native_probe_sends_one_native_request`: `FakeLLM(decisions=[…])`; one `native_requests` entry; `retries == 0`; the ledger row is under `model-test` with `operation == "decide"` and `decision_mode == "native"`; the facts record `decide_native: ok`.
    - `test_a_refused_decide_native_probe_is_recorded_unverified`: a 400 is recorded as a failed test, and the capability reads `unknown` with its error.
    - `test_the_decide_native_probe_is_refused_on_presets_that_cannot`: Anthropic answers 400 and nothing is sent.
    - `test_the_preview_says_cost_unknown_for_a_decision_probe`
  - Frontend:
    - `ProviderModelPicker`: an unverified Decision row offers both probes; a row whose `decide_native` is `no` offers `generate` only.
    - `ProvidersView`: Test… lists "decide_native".
    - `TestCallDialog`: with an estimate of null, "cost unknown" is shown.
- [ ] **Step 2:** Run them: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `tests/test_model_test_call.py tests/test_provider_api.py tests/test_usage_guard.py tests/test_routing_guard.py`. From `frontend/`: `npm run typecheck`, `npx vitest run` on the three files, and `npx eslint` on the touched files. Then `make check-lint check-mypy PY=$PY`. PASS.
- [ ] **Step 5:** Commit `feat(test-call): a decide_native probe, one predicate, cost unknown`.

---

### Task 9: `n/a` controls for a native-only attempt (§8)

**Files:**
- Modify: `llm_sampling.py`, `store/inference/resolve.py` (`_attempt`'s controls on a decide resolution), `store/inference/controls.py`, `routes/config.py` (`InferenceControlsBody`), `frontend/src/api/client.ts` (`previewControls`), `frontend/src/components/inference/ControlsReadout.tsx`, `frontend/src/routes/ModelsView.tsx`
- Tests: `backend/tests/test_inference_controls.py`, `test_inference_resolve.py`, `frontend/src/components/inference/ControlsReadout.test.tsx`, `frontend/src/routes/ModelsView.test.tsx`

**Interfaces:**
- `llm_sampling.WHY_NATIVE = "a native decision takes no sampling"`.
- `llm_sampling.not_applicable(conn: dict, why: str) -> dict` has `effective`'s shape:
  - `requested` is the stored values, as `effective` reports them;
  - `effective` is `{}`;
  - every control in `CONTROLS` is `{"state": "n/a", "wire": "", "why": why, "source": "adapter"}`.
- In `resolve._chain`, on a decide resolution, an attempt for which `native_only(attempt.capabilities)` holds gets `controls=not_applicable(conn, WHY_NATIVE)`, through `dataclasses.replace` like the mode. A native attempt that also generates keeps its real controls, because its structured stage sends them.
- `InferenceControlsBody.operation: str = ""`. `controls.preview(preset_id, conn, model, operation="")`: when `operation == "decide"` and the model is native-only by Task 5's rule, it answers `not_applicable`. Native-only means that in `capabilities.caps_for(lowered)`, `generate` is a known `no` from a source other than `name`, and `decide_native` is not a known `no`. Factor that test into `resolve.native_only(caps: dict[str, Cap]) -> bool`, which `_chain` uses too, so the two never disagree.
- Frontend:
  - `previewControls` takes optional `operation`;
  - `ControlsReadout` takes `operation?: "decide"`, and when every control is `n/a` renders "Not sent: a native decision takes no sampling.";
  - `GenerativeSummary` passes `operation="decide"` on the Decision card.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_native_only_attempt_reports_na_controls`
  - `test_a_dual_capable_attempt_keeps_its_controls`
  - `test_controls_preview_for_decide_on_a_decide_only_model`
  - a generate preview is byte-identical to today's;
  - `ControlsReadout` renders the n/a line;
  - `ModelsView` shows it on a Decision card whose model is native-only.
- [ ] **Step 2:** Run them: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `tests/test_inference_controls.py tests/test_inference_resolve.py tests/test_llm_sampling.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py`; the frontend checks on the touched files; `make check-lint check-mypy PY=$PY`. PASS.
- [ ] **Step 5:** Commit `feat(controls): a native decision reports its sampling as n/a`.

---

### Task 10: `--live` measures what production sends, native included

**Files:**
- Modify: `evals/runner.py` (`resolve_connections`, `live`), `evals/README.md`
- Test: `backend/tests/test_evals.py`

**Interfaces:**
- `resolve_connections` keeps the whole `ResolvedInference` for a decide case (`require_inference(...)`), and the `.conn` for a generate case. Its return type is `dict[str, dict | ResolvedInference]`.
- `live(case, target, record=False, *, client=None)`. For a decide case (`case.schema is not None`):

  ```python
  decision = await inference.decide(case.task, ctx["items"], client=c, resolved=target,
                                    explain=ctx.get("explain", ""))
  output = decisions.render(decision.items, ctx["items"], explain=bool(ctx.get("explain")))
  ```

  So the structured stage sends what F's path sent, and a native Decision model is measured natively. The grade is unchanged. `Result` gains `note: str = ""`, which carries `f"backend: {decision.backend}"`, and `report` prints it.
- README: what `--live` now measures for a decide case (the full chain on the Decision role); how to measure a native model (point the Decision role at it, then run the three `decide-*` cases); and that it costs money and is never run without the user's approval.

- [ ] **Step 1: Write the failing tests:**
  - `test_live_runs_a_decide_case_through_decide`: `runner.live` with a `FakeLLM(decisions=[…])` client and a native resolution of `scene-break` grades PASS, with `note == "backend: native"`, and no `complete` is called.
  - `test_live_structured_decide_case_sends_the_schema`: F's assertion, kept through the new path.
  - Replay is unaffected.
- [ ] **Step 2:** Run them: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `tests/test_evals.py tests/test_eval_graders.py tests/test_decide_gate.py`, then `make check-lint check-mypy PY=$PY`. PASS.
- [ ] **Step 5:** Commit `feat(evals): --live runs decide cases through decide(), native included`.
- [ ] **Step 6 — REQUIRES THE USER'S EXPLICIT APPROVAL (spends money). Skip unless the controller relays that approval.**
  1. With the Decision role pointed at a native model the user chooses, run `$PY evals/run.py --live --case decide-scene-break --case decide-voice-drift --case decide-speaker`.
  2. Then the same with the Decision role on a structured model.
  3. Report both outputs in the PR description. Never add `--record` without separate, explicit approval.
  4. The slice does not wait on this step.

---

### Task 11: Docs, gates, PR

**Files:** `CLAUDE.md`, `CONTRIBUTING.md` (the `test_usage_guard.py` row names `decide_native`), `docs/incoming-llm-capture.md` (the `decision_body` event), `evals/README.md`

- [ ] **Step 1: Docs.**
  - `CLAUDE.md` "Adding an LLM call site?" (decide paragraph):
    - the chain (`inference.stages`) and what moves an item on: a failed call, never an answer;
    - `decide_native` metered per item;
    - one attempt for the fallback;
    - the capture after the call settles with its outcome;
    - voice drift's native branch.
  - Observability: native adapters record `decision_body`.
  - Run `tests/test_docs_guard.py tests/test_evals.py tests/test_decide_gate.py`, then `make check-lint check-mypy check-templates PY=$PY`.
- [ ] **Step 2:** Commit `docs: native decisions, the decide chain and its capture`.
- [ ] **Step 3: Gates.** Claude stand-ins for the Codex gates:
  1. A code review of the branch diff.
  2. An adversarial review against this spec. Does H implement §14 row H, §5.3–§5.5 as amended, §7.4's native backends, §8's `n/a`, §9.4's capture and §6.4's probe? Are the strict-mode limits bounded?
  3. Address every finding, or record why not.
- [ ] **Step 4:** Push, open the PR (Codex reviews it), wait for **CI green**, and merge with rebase and `--ff-only`.

---

## Parallelism and coordination

| Wave | Tasks | Notes |
|---|---|---|
| 0 | 0 | after F's final fix round lands |
| 1 | 1 | `decisions.py` only |
| 2 | 2, then 3 | both touch `llm.py`'s `decide_native` and `llm_fakes` |
| 3 | 4, then 5 | 5 deletes what 4's tests build on for F's resolutions; run in order |
| 4 | 6, 7, 8, 9 | disjoint files except `frontend/src/api/types.ts` (7 and 8: different types) and `routes/config.py` (8 `_probe`, 9 the controls body). Parallel worktrees with a hand merge of those two files, or in sequence. 6 must follow 5 (speaker tests). |
| 5 | 10 | needs 1 and 4 |
| 6 | 11 | last |

**With slice G** (continuity on `decide()`):
- G's items reach the native chain automatically. Review Focus 4 (concurrency, per-item fall-through) is written with G's one-item-per-row batches in mind.
- G touches neither `inference.py`'s chain nor the adapters. If G lands first, Task 5's route-suite rewrite also covers G's `decide_only` cases. If G's continuity tests assert "skipped", they move to native exactly as F's do.

**With slice I** (adapter registry):
- I moves `decide_native` into the per-adapter `decide` operation and deletes the lowering, `FALLBACK_KEY` and `STRUCTURED_KEY`.
- `stages` is the seam I keeps: a stage then names an adapter operation instead of a conn.
- Strict mode's string budgets are H's (ruling 14), so I need not add them.

## Rulings (Task 0 folds each into the spec)

1. **Native is opt-in by capability.** An attempt is native first only when its `decide_native` is `yes` (catalog, passed test or user override). It is structured when it can generate. It is tried natively on `unknown` only when it is known unable to generate, because unknown is allowed (§5.3).
2. **F's skip is deleted.** A decide-only model is served natively, so `skipped`, `skip_text` and the skip notice go. The Decision card resolves as `decide`, and card and row read one refusal (§5.3, §12).
3. **`OPERATION_CAPABILITY["decide"]` is "decide_native or generate".** It is missing only when both are known `no`, and the sentence then says "cannot generate text or make native decisions".
4. **What moves an item to the next stage** is a failed call: an `LLMError`, including a 2xx body that is not the documented envelope (`bad_response`). An answer never moves an item, including a native refusal (`refused`) and a `None` from a well-formed body.
5. **The fallback gets one stage**, native or structured by its own capabilities (§5.5's "one attempt").
6. **The fallback rides the facade** (`FALLBACK_KEY`) only when it is structured and the primary generates. Otherwise it is its own stage. A same-provider fallback is kept when the primary cannot generate, because the next step is a different model on another backend, not a retry.
7. **A `PresetRefusalError` stops the chain**, as it stops the facade.
8. **Native normalisation:**
   - the provider's explicit answer wins;
   - otherwise the argmax of its distribution, and a tie is `abstained`;
   - a predicate's probability is P(true), and exactly 0.5 is `abstained`;
   - distributions are keyed by option id or level index, with `NONE_KEY` (`"<none>"`, reserved) for an explicit none;
   - an invalid report is dropped, never repaired.
9. **OpenAI's `confidence` is not carried.** The contract has no field for it, and §7.4 forbids a synthetic confidence.
10. **A native row is priced from what the provider reports** (cost, or reported tokens × rates). It gets no local token estimate (its billing unit is the decision, not a chat prompt); without either it is unpriced.
11. **A native 4xx in `REJECTED_STATUSES` does not mark the connection failing.** The connection answered; the decisions endpoint refused that model or request (F's CODE-M5 reasoning).
12. **Retries:** a native primary stage takes the facade's budget, and a native fallback stage takes 0 (§5.4). A structured fallback sent as its own stage takes the facade's budget, as F's skip sent it.
13. **The decide capture runs after each call settles**, as `(messages, outcome, conn)`. The outcome is `decisions.outcome` (mode, provider, model, normalised answers and distributions, or the error), recorded as a zero-token `decision` section. A cancelled call is not captured. Scene-break and voice drift still capture nothing.
14. **Strict mode's string budgets** (15,000 characters of enum strings above 250 values; 120,000 schema characters) are enforced in `decisions.validate` and `chunks` whatever the backend, because the chain can fall to structured. They are re-checked against OpenAI's docs in Task 1.
15. **Voice drift on a native verdict:** a drift with no note is usable, stages nothing, keeps any standing flag, and is listed in `noteless`. The over-cap and unknown checks still apply.
16. **`NATIVE_CONCURRENCY = 4`**, argued structurally and to be tuned later.
17. **The `decide_native` probe** is one fixed predicate. Its estimate is always `None` ("cost unknown"), because a catalog row does not say how a decision is billed.
18. **A native-only attempt reports every control `n/a`.** A native attempt that also generates keeps its controls, because its structured stage sends them. The controls API takes `operation`.
19. **`Decision.backend`, `provider` and `model` name the first stage that answered.** The per-item truth is `ItemResult.backend`.
20. **`--live` runs decide cases through `inference.decide`** on the whole resolution and grades `decisions.render` of the result, so it measures the chain production runs.
21. **OpenRouter `provider.require_parameters`** (F's final review) stays out of H: the schema is always in the prompt, so routing to a provider that ignores `response_format` is harmless. It is left to I.

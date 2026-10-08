# Inference slice F — `decide()`: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give grimoire its second operation, `decide()`, answered by structured generation, and move the scene-break check, the voice-drift check and the next-speaker pick onto it. Each one moves only after an offline eval gate shows the new parse equals or beats today's on recorded replies taken from today's parser tests. The three routes then default to the Decision role.

**Architecture:**

- **The contract is a gateway leaf.** `grimoire/decisions.py` holds the request and result types, validation, the JSON Schema of a batch, and the parser. It imports nothing from the package. Slice H's native decision adapters (gateway modules, #239) can then normalise into it without touching the store.
- **The operation sits above the facade.** `grimoire/inference.py` (spec §7.1) holds `structured_messages` and `decide`. `decide` renders `templates/decide/`, makes one `LLMClient.complete(..., schema=)` per chunk under its own `store.usage.meter`, and parses with `decisions.parse`. It is disjoint from slice D's embed operation, which lives store-side in `store/inference/embed.py` (Open (a)).
- **`generate(schema=)` is the facade's `schema=` keyword.** For each attempt, the facade sends the provider's structured mode when the resolver flagged that attempt structured-capable (`STRUCTURED_KEY`, set on decide resolutions only). The schema is always in the prompt as well (ruling 3).
- **Call sites still resolve through the seam.** They call `require_inference(task, cid, operation="decide")` and hand the resolution to `decide`. The resolver answers *which backend can serve* each attempt (`Attempt.decision_mode`, always `"structured"` in F). The backend stamps how a call *was* served per call, on a copied account block (slice E's `llm_usage.with_account`), so H's native → structured-on-the-same-selection chain files each call honestly.
- **A decide-only Decision model is skipped, not refused, when the role fallback can generate** (§5.5, I5). With no generating fallback, §5.3's 409 stands.
- **Each conversion takes two tasks:**
  1. *Prepare.* Add the item builder, the outcome mapping, the gate corpus (built from today's parser tests) and a permanent `decide-*` eval case. Nothing changes behaviour.
  2. *Switch.* Change the call site, flip the route to `operation="decide"` and `default_role="decision"`, delete the legacy prompt and parser, and update the tests.

**Tech Stack:** Python ≥3.11, FastAPI, httpx, Jinja2 templates, pytest; the offline eval suite under `evals/`. No frontend change: `/models` already lists the routes a role serves and shows a row's `problem` text (`ModelsView.tsx`), which is where the decide skip is reported.

**Spec:** `docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md`

- Slice F is §14 row F, together with the "Safety rule across slices" under it.
- It draws on §5.3, §5.4 (`decision_mode`), §5.5 (the decide chain), §6.2, §7.1, §7.2, §7.4, §9.3, §9.4, §12 ("Decide question unanswerable"), §14.1 and §15 "Decide".
- Task 0 amends the spec for the rulings at the end of this plan.
- Base: `claude/inference-slice-f` at `6a87cd4` (slice C, nearly finished, not merged).
- Plan review: `.superpowers/sdd/plan-reviews/slice-f-plan-review.md` (verdict "With fixes"); every finding is answered in "Plan review rulings" below.

## Global Constraints

**The user's rules:**

- **Never run the full test suite, and never `make check` or `make check-py`.** Each task runs only the test files it names, plus `make check-lint`, `make check-mypy` and `make check-templates` (and `make check-eslint` if it touches the frontend, which no task here does). CI runs the full suite. **Merging waits for CI to be green.**
- **Never spend money without asking.** No live LLM call anywhere. `evals/run.py --live` and `--record` are never run without the user's explicit approval, in this slice or after it, because they spend money. The gate (`--gate`) is offline only and refuses `--live`, `--record` and `--case`.

**Ordering with D and E** (details in "Parallelism and coordination"):

- **F's Task 3 lands after E's Task 2.** E stamps the account block (`operation`, `role`, `billing`) in `resolve` and `embed._stamp`, and `llm._stamp` copies it into the holder. D owns `usage.record(operation=)` (E adds it exactly as D specifies if E lands first).
- F sets `decision_mode` **per facade call, on a copied account block** (`llm_usage.with_account`). An account block is never mutated in place; that rule is in E's plan and pinned by E's `test_account_blocks_are_never_mutated_in_place`.
- F adds no `Meter` parameter and does not touch `store/usage.py` (unless F's Task 3 is implemented before E's Task 2; then the "If F is implemented first" list applies).
- F's Tasks 0, 1, 2 and 4 can run in parallel with D and E.

**Spec rules that bind here:**

- **Safety rule (§14):** a route's `default_role` and `operation` flip only in the task whose call site calls `decide()`. That is Tasks 6, 8 and 10, and never earlier. Task 3's guard enforces it.
- **Rule 3, no behaviour change:**
  - `backend/tests/fixtures/inference_baseline.json`, `inference_baseline_c.json` and `test_lore_golden`'s golden are **never regenerated**.
  - The only allowed difference is the new task `scene-break-title` (Task 6, `NEW_TASKS`). Its cells must equal those of its route sibling, `rolling-summary`.
  - A generate resolution's connection dicts stay byte-identical: `STRUCTURED_KEY` is set only on decide resolutions (Minor 4).
- **Rule 5:** a price nobody reported is never rendered as zero. `decide` puts no number in a holder that no provider stamped.
- **§12:** an unanswerable question is `answer: None` plus a `reason`, never a guessed default. Each call site keeps today's safe direction:
  - scene-break: no break;
  - voice-drift: a failed check;
  - speaker: control returns to the player, with today's issue string.

**CLAUDE.md rules (all of them; these are the ones this slice touches):**

- **Imports:** all at module scope, and the module graph stays acyclic (`test_import_guard.py`).
  - `grimoire/decisions.py` imports only the standard library.
  - `grimoire/inference.py` imports `llm`, `llm_usage`, `prompts`, `decisions` and `store`. Nothing in `store/` or the gateway imports it.
  - Route modules bind it as `from .. import inference as operations`. The name `inference` is taken in `routes/common.py:36` and `routes/scenes.py:33` (`store.inference.resolve`), and `routes/inference.py` exists (Minor 9).
- **Adding an LLM call site?** Resolve it with `require_inference(<literal task>, cid, operation=<the route's>)`. The task must be claimed by a route in `store/routing.py`.
- **Instrument failures at `usage.Meter.done`, never at call sites.** `decide` opens the meter, and a caller's time budget runs *inside* it through the `around` hook (I2), so a budget overrun is filed as an `error/timeout` row exactly as today.
- **Faking the LLM:** only through `backend/tests/llm_fakes.py`, injected at `app.dependency_overrides[routes.get_llm]`. Never write a new inline fake. Existing inline fakes gain `schema=None` on `complete`, and that is all they gain (Task 6).
- **Stored text is raw.** LLM readers read `store.regex.view.view(..., phase="prompt")`. The renamed selector reader stays pinned in `test_regex_prompt_guard.py`.
- **Prompt text lives in `templates/`.** `scripts/verify_templates.py` checks builders against templates byte for byte, and `templates/README.md` names real builders (`test_docs_guard.py`).
- **pydantic stays v1/v2-agnostic.** Use plain dataclasses for the contract and add no pydantic models.
- **Privacy:**
  - Invented names only: Seraphine, Mara, Winifred, Realm, Saltmarch, Rowan, Tobin.
  - No measurement of a real store informs any constant. `MAX_ITEMS_PER_CALL` is argued structurally (ruling 16).
- **Lint gates are ratcheted.** When a count shrinks, run `make baseline PY=$PY` and commit the smaller file with the fix.
- **Linear history:** rebase and `--ff-only`, never a merge commit.
- **Codex gates:** `/codex:review` and the final `/codex:adversarial-review` (Task 11). If Codex is unavailable, stop: the controller asks the user before substituting anything.

**Commands** (the worktree has no venv of its own; pass the main checkout's):

- `PY=/home/user/grimoire/backend/.venv/bin/python`
- Backend tests: `cd backend && PYTHONPATH=src $PY -m pytest tests/<file> -q`
- Gates: `make check-lint PY=$PY`, `make check-mypy PY=$PY`, `make check-templates PY=$PY`
- Eval replay runs through pytest: `tests/test_evals.py`, `tests/test_eval_graders.py`, and Task 4's `tests/test_decide_gate.py`. The gate CLI is `$PY evals/run.py --gate` (offline).

**Parallel worktrees:** reset to the slice branch first, because the harness bases worktrees on `main`. Report in the final message; agents cannot write the shared checkout.

## Review Focus

1. **A Decision role pointed at a decide-only model** (an OpenRouter model whose catalog `outputs` is `["decisions"]`, picked during C–E).
   - With a role fallback that can generate: each converted route **answers on the fallback**. Nothing is sent to the decide-only model. The settings row's `problem` names the skip.
   - With no generating fallback: §5.3's 409 `incapable` naming the Decision role, and nothing is sent. The voice-drift phase reports `failed` with that reason, and absorb still lands.
   - Tests: Task 6 `test_a_decide_only_decision_model_falls_to_the_role_fallback` and `test_a_decide_only_decision_model_without_a_fallback_is_refused`; Task 8 `test_voice_drift_on_a_decide_only_model_answers_on_the_fallback` and `test_voice_drift_on_a_decide_only_model_without_a_fallback_fails_the_phase_not_the_absorb`; Task 10 `test_the_speaker_on_a_decide_only_model_answers_on_the_fallback`; Task 3 `test_resolve_skips_a_decide_only_primary_for_a_generating_fallback`.
2. **A structured-capable primary with a fallback that is not.**
   - The fallback attempt gets no `response_format`/`output_config.format`, still answers from the schema in the prompt, and `decide` parses it.
   - A provider 400 that names the structured field is **not** a sampler-preset refusal, so the fallback is still tried (I3).
   - Tests: Task 2 `test_structured_mode_is_decided_per_attempt` and `test_a_refused_schema_is_not_a_preset_refusal`; Task 3 `test_a_fallback_without_structured_mode_still_answers`.
3. **Valid JSON in the wrong shape:** an unwrapped item, a flattened item, a missing question id, an option outside the vocabulary, the string `"true"` for a predicate, a bool for a score, `null` on a choice that disallows none.
   - Each is `None` with reason `unreadable` (and `detail: "not_an_option"` for an answer outside the options) and never a default. The unwrapped and flattened single-item shapes are read (decided deliberately, I8).
   - The call site's safe direction holds: Task 1's `test_unreadable_answers_carry_a_reason`, and each conversion's gate corpus (Tasks 5, 7, 9).
4. **The scene-break title.** The verdict is committed first; the title is drafted only when a YES landed, and written by a second guarded write.
   - A title call that fails keeps the verdict with an empty title; its own meter files the failure.
   - A verdict that did not land makes no title call; a title whose verdict moved on is dropped.
   - Tests: Task 6 `test_a_failed_title_keeps_the_verdict`, `test_a_verdict_that_did_not_land_makes_no_title_call`, `test_a_title_is_dropped_when_the_verdict_moved_on`.
5. **A legacy (format 1) store after F.** The three tasks resolve exactly as before, because the Decision role is unset and inherits Fast → Primary. Both equivalence sweeps stay green with only the `scene-break-title` allowance.
   - Tests: Tasks 6, 8 and 10 each run `tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py`.
6. **Voice drift's time budget** (I2). An overrun is filed by `decide`'s meter as `error/timeout`, reaches the error store with `module == "voice-drift"`, and is noted against the connection that was running (the fallback when it had taken over).
   - Test: Task 8 `test_a_voice_drift_overrun_on_the_fallback_is_filed_and_noted_against_it`.
7. **The ledger says how a decision was served** (I6, I7). A decide row carries `operation: "decide"` and `decision_mode: "structured"`, the fallback's row included, and no account block of the resolution is touched.
   - Tests: Task 3 `test_a_decide_row_files_its_operation_and_mode` and `test_the_mode_is_stamped_per_call_on_copied_blocks`.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| spec | rulings below folded in | 0 |
| `backend/src/grimoire/decisions.py` (new, gateway leaf) | request/result types, `validate`, `schema`, `parse`, `chunks`, `unanswered`, `normalise` | 1 |
| `llm.py`, `openrouter.py`, `openai_compatible.py`, `anthropic.py` | `schema=` through the facade; per-attempt structured mode; each adapter's wire spelling; a structured refusal is not a preset refusal | 2 |
| `store/inference/capabilities.py` | `response_format` alone no longer means structured output (Minor 5) | 2 |
| `store/inference/resolve.py`, `resolved.py`, `store/inference/settings.py` | `STRUCTURED_KEY` on a decide resolution's capable attempts; `Attempt.decision_mode`; the decide skip (`skipped`, `skip_text`) | 2, 3 |
| `backend/tests/llm_fakes.py` | `schema=` on `FakeLLM.complete/single/stream` and `StallingGateway.complete`, recorded per `complete`/`single`; `decision_reply(...)` | 2, 3 |
| `backend/src/grimoire/inference.py` (new) | `structured_messages`, `decide` (with `around` and `capture` hooks), the backend dispatch H extends | 3 |
| `templates/decide/system.j2`, `templates/decide/user.j2` (new) | the structured backend's prompt | 3 |
| `backend/tests/test_operation_guard.py` (D creates it; F adds the decide half), `test_usage_guard.py` | §14.1 guards | 3 |
| `evals/gate.py`, `evals/legacy.py` (new), `evals/run.py`, `evals/runner.py`, `evals/cases.py` (`Case.task`, `Case.schema`), `evals/README.md`, `backend/tests/test_decide_gate.py` (new) | the eval gate, and a faithful `--live` path for decide cases | 4 |
| `store/scene_break.py`, `templates/scene_break/*`, `templates/scene_break_title/*` (new), `evals/gate/scene-break.json`, `evals/cases.py`, `evals/recordings/decide-scene-break.*` | scene-break prepared | 5 |
| `routes/scenes.py`, `store/routing.py`, tests (and every inline fake's `complete`) | scene-break switched; `scene-break-title` registered | 6 |
| `store/voice_drift.py`, `templates/voice_drift/*`, `evals/gate/voice-drift.json`, `evals/cases.py`, `evals/recordings/decide-voice-drift.*` | voice-drift prepared | 7 |
| `routes/scenes.py`, `routes/common.py` (`_soft_resolved`), `store/routing.py`, `fixtures/llm/campaign_flow.json`, tests | voice-drift switched | 8 |
| `store/response_protocol.py`, `templates/scene/response_selector_*.j2` (new), `evals/gate/speaker.json`, `evals/legacy.py`, `evals/cases.py`, `evals/recordings/decide-speaker.*` | speaker prepared | 9 |
| `routes/character_turns.py`, `store/routing.py`, `test_regex_prompt_guard.py`, tests | speaker switched | 10 |
| `CLAUDE.md`, `CONTRIBUTING.md`, `templates/README.md`, `evals/README.md` | docs | 11 |

---

### Task 0: Settle the spec

**Files:** Modify `docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md`, in §5.3, §5.4, §5.5, §6.2, §7.1, §7.2, §7.4, §9.3, §9.4, §12 (one line: the skip notice beside the refusal), §14 rows F, H and I, the safety rule, and §14.1.

- [ ] **Step 1:** Fold rulings 1–16 and the plan-review rulings into the sections they touch, one line each:
  - §5.3 / §5.5 (I5): for `operation == "decide"` with no native backend (all of F–G), a primary known unable to `generate` is **skipped**, not refused, when the role fallback can generate; the fallback is then the attempt sent, and the settings row's `problem` says so (`skip_text`). With no generating fallback, the 409 stands. H replaces the skip: a `decide_native` primary is served natively, and `OPERATION_CAPABILITY["decide"]` widens to "decide_native or generate". §12's "one refusal decision" still holds: a route row's `problem` is the refusal, else the skip notice, both read off the one resolution.
  - §5.4 (I7): `Attempt.decision_mode` is the resolver's *capability* answer (which backend will serve that attempt first). How a call *was* served is stamped per call by the backend, on a copied account block. `ResolvedInference.skipped` names the primary's gaps when the skip applied.
  - §6.2 (Minor 5): a catalog's `supported_parameters` naming `structured_outputs` is a `structured_output` yes; `response_format` alone says nothing (on OpenRouter it usually means `json_object`).
  - §7.1 (Open (a)): only if D's Task 0 has not landed, write slice D's text verbatim (D's plan, Task 0, at `3e0ce87`): "`embed` / `embed_sync` live in `store/inference/embed.py`, because every caller is a store module and the store never imports `llm.py` (#239). `decide` stays in slice F's top-level `grimoire/inference.py`. The two modules are disjoint, and neither merges into the other." If D's has landed, change nothing in §7.1. Before writing, re-read D's plan in `.claude/worktrees/slice-d`; if D's wording has moved, copy the new wording.
  - §7.2: rulings 3, 4 (amended, I4), 11 and 14; schema features restricted to the intersection of OpenAI strict mode's and Anthropic's documented subsets; `STRUCTURED_KEY` on decide resolutions only (Minor 4); a 400 naming the structured field is not a preset refusal (I3); Anthropic compiles each new schema once and caches it, so the speaker's roster-dependent schema pays compile latency once per roster composition, to be tuned against real prompts later (Minor 14).
  - §7.4: rulings 1, 2, 5, 6 (with Open (c)'s collision rule), 7 (with I8's corpus source and full outcomes), 8, 9, 12, 13 and 16; the `around` and `capture` hooks; the unwrapped and flattened single-item shapes; the `detail` sub-reason; the scene-break row (Minor 6): the verdict commits first, the title is drafted only when a YES landed, is cleaned and capped, comes from the `summary` route's model, and a YES costs two calls and two copies of the transcript; the speaker keeps today's two issue strings.
  - §9.3 (I6, I7): `decision_mode` reaches a row through the account block, stamped per call on a copy.
  - §9.4 (ruling 10 overturned): F owns the speaker's request capture (the decide prompt, contexts and questions). Scene-break and voice drift never had a capture and gain none: per-check entries would compete in the prompt log's Turn-history rail with the turns the reader inspects, and `taskLabel` has no label for them. H owns mode, normalised answers and distributions (in F the mode is always `structured`, which the ledger already records, and distributions exist only natively).
  - §14 row F: settled. Row H: the native backend stamps `"native"`, replaces the skip, widens `OPERATION_CAPABILITY`, owns §9.4's mode/answers/distributions capture and ruling 12's voice-drift branch. Row I (Minor 12): `inference.generate` (ruling 14) and the retirement of `STRUCTURED_KEY` with the lowering and `FALLBACK_KEY`. The safety rule: after F, a decide-only Decision model is skipped for a generating fallback.
  - §14.1 (I1): the decide guard resolves import bindings (D's alias resolution, generalised to a target module) and never matches a method by its name alone.
- [ ] **Step 2:** Commit `docs(spec): settle slice F's decide contract, structured mode and gate`.

---

### Task 1: The decision contract

**Files:**
- Create: `backend/src/grimoire/decisions.py`
- Test: `backend/tests/test_decisions.py` (new)

**Interfaces:**
- Consumes: nothing. The module imports only the standard library.
- Produces (all frozen dataclasses):
  - `Option(id: str, description: str, aliases: tuple[str, ...] = ())`
  - `Predicate(id: str, instructions: str)`
  - `Choice(id: str, instructions: str, options: tuple[Option, ...], allow_none: bool = False)`
  - `Score(id: str, instructions: str, levels: tuple[str, ...])`
  - `Question = Predicate | Choice | Score`
  - `Item(context: str, questions: tuple[Question, ...])`
  - `Answer(answer: bool | str | int | None, reason: str = "", probability: float | None = None, distribution: dict[str, float] | None = None, detail: str = "")`. `reason` is set if and only if `answer is None`. `detail` is set only with `reason == "unreadable"`, and its one value in F is `"not_an_option"` (a choice answered with a value that is present, not null, and matches no option). It is a sub-reason, not a member of `REASONS`.
  - `ItemResult(answers: dict[str, Answer], rationale: str = "")`
  - `Decision(items: tuple[ItemResult, ...], backend: str, provider: str = "", model: str = "", usage: tuple[dict, ...] = ())`
  - `class DecideRequestError(ValueError)`
- Constants:
  - `REASONS = ("unreadable", "refused", "abstained", "error")`
  - `BACKENDS = ("structured", "native")`
  - `NOT_AN_OPTION = "not_an_option"`
  - `MIN_OPTIONS, MAX_OPTIONS = 2, 255`
  - `MIN_LEVELS, MAX_LEVELS = 2, 10`
  - `MAX_ITEMS_PER_CALL = 8` (ruling 16)
  - `RESERVED_IDS = ("answers", "rationale")`
- `validate(items: Sequence[Item]) -> None` raises `DecideRequestError` on any of:
  - no items, or an item with no questions;
  - a question id that is empty, all digits, in `RESERVED_IDS`, or repeated within an item (the reserved and digit ids keep the flattened and wrapped shapes unambiguous);
  - option count outside 2–255, or level count outside 2–10;
  - an empty option id;
  - two options whose ids collide **after `normalise`**, or an alias whose `normalise` collides with any option's normalised id or alias (Open (c), Minor 15).
- `normalise(word: str) -> str`: casefold, strip, then every run of spaces or hyphens becomes `_`.
- `schema(items: Sequence[Item], *, explain: bool) -> dict`: the JSON Schema of one batch, restricted to features both OpenAI strict mode and Anthropic `output_config.format` document (I4): `type` object/string/boolean/integer/null, `enum`, `anyOf`, `required`, `additionalProperties: false`. Every object has `additionalProperties: False` and lists every property in `required`. No numeric bounds (Anthropic refuses `minimum`/`maximum`), so a score is an integer `enum`. A choice without `allow_none` is `{"type": "string", "enum": [ids]}`; with it, `{"anyOf": [{"type": "string", "enum": [ids]}, {"type": "null"}]}`. Type arrays are never emitted.
- `parse(text: str, items: Sequence[Item], *, explain: bool) -> tuple[ItemResult, ...]`: never raises, and returns one `ItemResult` per item in order. The algorithm, because the tests do not fix every branch:
  1. Find the object.
     - `json.loads(text.strip())` first.
     - Then the body of a leading ```` ``` ```` / ```` ```json ```` fence.
     - Then the substring from the first `{` to the last `}`.
     - Anything that is not a `dict` after all three leaves every answer `None`/`unreadable`.
  2. Find each item's object.
     - Item `i` is `obj[str(i)]`.
     - **Unwrapped** (one item only, no `"0"` key): an object that itself has `"answers"` is item 0.
     - **Flattened** (one item only, no `"0"` key, no `"answers"` key): an object holding at least one of the item's question ids is taken as item 0's answers, and its `"rationale"` as the rationale. This is the shape an unstructured fallback answering in today's style produces; the gate holds it (I8).
  3. `answers = item_obj["answers"]` (or the flattened object). If that is not a dict, every answer of that item is unreadable.
  4. Per question:
     - **predicate:** a JSON `bool` only. The string `"true"` is unreadable.
     - **choice:**
       - absent: unreadable.
       - `null` gives `None`/`abstained` when `allow_none`, otherwise unreadable.
       - A string matching an option id exactly is that option.
       - Otherwise `normalise(s)` is compared against `normalise` of each id and each alias; a match is that option's **id** (`validate` makes a match unique).
       - Any other present, non-null value (no match, or not a string) is unreadable with `detail="not_an_option"`.
     - **score:** an `int` that is not a `bool`, in `range(len(levels))`. Anything else is unreadable.
  5. `rationale`: when `explain` and the value is a `str`, its `.strip()`; otherwise `""`.
- `unanswered(items: Sequence[Item], reason: str) -> tuple[ItemResult, ...]`: every answer `None` with `reason`.
- `chunks(items: Sequence[Item], size: int = MAX_ITEMS_PER_CALL) -> list[tuple[int, tuple[Item, ...]]]`: `(offset, chunk)` pairs in order.

- [ ] **Step 1: Write the failing tests** (`tests/test_decisions.py`). Use invented refs (`characters:mara`, `characters:winifred`).
  - `test_validate_bounds`:
    - a Choice with 1 option raises; with 256 options raises; with 2 and with 255 passes;
    - a Score with 1 level raises; with 11 levels raises;
    - a duplicate question id raises; a question id `"rationale"`, `"answers"` or `"0"` raises;
    - `validate([])` raises.
  - `test_validate_rejects_collisions_after_normalising`: `Option("not_enough", "", ("drift",))` beside `Option("drift", "")` raises; `Option("in voice", "")` beside `Option("in_voice", "")` raises; `Option("Characters:Mara", "")` beside `Option("characters:mara", "")` raises.
  - `test_schema_shape`: for `Item("ctx", (Predicate("over", "i"), Choice("who", "i", (Option("characters:mara", "Mara"), Option("grimoire", "g")), allow_none=True), Score("tone", "i", ("low", "mid", "high"))))`:
    ```python
    assert decisions.schema([item], explain=True) == {
        "type": "object", "additionalProperties": False, "required": ["0"],
        "properties": {"0": {
            "type": "object", "additionalProperties": False,
            "required": ["answers", "rationale"],
            "properties": {
                "answers": {"type": "object", "additionalProperties": False,
                            "required": ["over", "who", "tone"],
                            "properties": {
                                "over": {"type": "boolean"},
                                "who": {"anyOf": [
                                    {"type": "string", "enum": ["characters:mara", "grimoire"]},
                                    {"type": "null"}]},
                                "tone": {"type": "integer", "enum": [0, 1, 2]}}},
                "rationale": {"type": "string"}}}}}
    ```
    With `explain=False`, `rationale` is in neither `required` nor `properties`. Without `allow_none`, `who` is `{"type": "string", "enum": [...]}`.
  - `test_schema_uses_only_the_shared_subset`: walking the schema of every question type, no key outside `{"type", "enum", "anyOf", "required", "properties", "additionalProperties"}` appears, and no `type` value is a list.
  - `test_parse_reads_each_type`: `'{"0": {"answers": {"over": true, "who": "grimoire", "tone": 2}, "rationale": " r "}}'` gives answers `True`, `"grimoire"`, `2`, and rationale `"r"`.
  - `test_parse_tolerates_fences_and_prose`: the same object inside ```` ```json ```` and inside "Here you go: … Thanks." parses identically.
  - `test_parse_unwrapped_single_item`: `'{"answers": {...}}'` parses for one item. With two items it is unreadable.
  - `test_parse_flattened_single_item`: `'{"over": true, "rationale": "r"}'` for one item with explain gives `True` and `"r"`. With two items it is unreadable. `'{"unrelated": 1}'` is unreadable.
  - `test_parse_normalises_spelling_and_aliases`: options `drift`, `in_voice`, `not_enough(aliases=("insufficient", "unclear"))`. `"In Voice"` gives `in_voice`; `"not-enough"` gives `not_enough`; `"insufficient"` gives `not_enough`; `"ok"` is `None`/`unreadable`/`not_an_option`.
  - `test_unreadable_answers_carry_a_reason`: each of these is `None`/`unreadable`:
    - `"true"` for a predicate;
    - `"characters:rowan"` (not offered), with `detail == "not_an_option"`;
    - `5` for a choice, with `detail == "not_an_option"`;
    - `null` on a choice without `allow_none` (`detail == ""`);
    - `3` for a 3-level score;
    - `true` for a score;
    - a missing question id (`detail == ""`);
    - `"[]"`;
    - `"no json here"`.
  - `test_explicit_none_is_abstained`: `null` on `allow_none` gives `Answer(None, "abstained")`.
  - `test_parse_never_raises`: `""`, `"{"`, `"null"`, `'{"0": 5}'`, `'{"0": {"answers": []}}'` each return `len(items)` results.
  - `test_rationale_only_when_explained_and_a_string`.
  - `test_chunks`: 17 items at size 8 give offsets `[0, 8, 16]` and sizes `[8, 8, 1]`.
  - `test_unanswered`.
  - `test_decisions_is_a_leaf`: `test_llm._sibling_imports("decisions") == set()`. Import the helper; do not copy it.
- [ ] **Step 2:** Run `tests/test_decisions.py`: FAIL (module missing).
- [ ] **Step 3:** Implement `decisions.py`.
- [ ] **Step 4:** Run these three commands separately (Minor 1), each PASS:
  - `tests/test_decisions.py`
  - `tests/test_import_guard.py`
  - `tests/test_llm.py -k "acyclic or leaf"` (the `-k` applies to this file alone: `test_llm_gateway_imports_are_acyclic`, `test_llm_errors_stays_a_leaf`)

  Then `make check-lint check-mypy PY=$PY`.
- [ ] **Step 5:** Commit `feat(inference): the decide() contract, its schema and its parser`.

---

### Task 2: `generate(schema=)`, decided per attempt

**Files:**
- Modify:
  - `backend/src/grimoire/llm.py`
  - `openrouter.py`
  - `openai_compatible.py`
  - `anthropic.py`
  - `store/inference/resolve.py`
  - `store/inference/capabilities.py` (`_STRUCTURED_PARAMS`, Minor 5)
  - `backend/tests/llm_fakes.py`
  - `backend/tests/test_inference_capabilities.py` (`test_catalog_params_say_structured_output_and_absence_says_nothing`: `response_format` alone now says nothing)
- Test: `backend/tests/test_llm_structured.py` (new)
- Also run, to see them unchanged: `tests/test_openrouter.py tests/test_openai_compatible.py tests/test_anthropic.py tests/test_llm.py tests/test_inference_resolve.py`

**Interfaces:**
- **`llm.STRUCTURED_KEY = "_structured"`.** `resolve.STRUCTURED_KEY` restates it, the way `FALLBACK_KEY` is restated, because `resolve` never imports `llm`. A test holds the two equal.
- **`resolve.resolve`**, at its tail and before the fallback attach (beside E's account-block stamp; after it when E has landed): **only when `operation == "decide"`**, each attempt whose `capabilities["structured_output"].value == "yes"` gets `conn[STRUCTURED_KEY] = True` on its own lowered dict (the resolution's own copy, as the `FALLBACK_KEY` attach already writes). Otherwise the key is absent. A generate resolution's dicts are byte-identical to today's (Minor 4), so `test_inference_resolve.py:277` and every frozen cell stay green.
- **`capabilities._STRUCTURED_PARAMS = frozenset({"structured_outputs"})`** (Minor 5). Anthropic's `features.structured_output`, a user override and a passed test still answer yes.
- **`LLMClient`:**
  - `stream(messages, conn, usage=None, *, schema: dict | None = None)` and `complete(messages, conn, usage=None, *, schema: dict | None = None)`.
  - `stream` builds its route lambda as `lambda route, holder: self._dispatch(messages, route, holder, schema)`. `_dispatch` and `_lowered` take `schema` through to `_provider`. `single` and `_resilient` are unchanged.
- **In `_provider`:** `structured = schema if schema is not None and conn.get(STRUCTURED_KEY) is True else None`. It is passed to the three HTTP adapters as `schema=structured` **only when it is not None**, the way `sampling` is passed only when there is something to send. Every existing provider stub (`ScriptedProvider`, `RefusingProvider`, the stubs in `test_llm.py`) therefore keeps its signature. The `claude` kind never takes it.
- **Adapters:** each owns its wire spelling. Each `stream(..., schema: dict | None = None)` adds to the body only when `schema` is not None:
  - `openrouter._payload(..., schema=None)` and `openai_compatible.stream`'s `payload`: `"response_format": {"type": "json_schema", "json_schema": {"name": "reply", "strict": True, "schema": schema}}`.
  - `anthropic._payload(..., schema=None)`: `body["output_config"] = {**body.get("output_config", {}), "format": {"type": "json_schema", "schema": schema}}`. It **merges**, so an effort control in `output_config` survives.
- **A structured refusal is not a preset refusal (I3).**
  - `llm._structured_share(conn) -> dict` returns the structured mode's envelope for the attempt's kind, with the schema emptied so no question id can enter the spellings: `{"output_config": {"format": {"type": None, "schema": None}}}` for `anthropic`; `{"response_format": {"type": None, "json_schema": None}}` for `openrouter` and `openai_compatible`; `{}` for `claude`, and `{}` whenever `conn.get(STRUCTURED_KEY) is not True`.
  - `_preset_refusal(exc, conn)` subtracts `_wire_spellings(_structured_share(conn))` from every sent field's spellings before matching. An effort refusal still matches (`output_config.effort`, `effort`); a refusal naming `output_config.format…` no longer matches the bare parent `output_config`, so the fallback is tried. `_resilient`'s call is unchanged. Reading the flag rather than whether a schema was sent costs nothing, because only decide resolutions carry the flag and `decide` always sends a schema.
  - Declined: the structured-degrade sibling the review suggested (see the Plan review rulings, I3).
- **`llm_fakes`:**
  - `FakeLLM.complete(messages, conn, usage=None, *, schema=None)` and `single(...)` append `schema` to `self.schemas: list[dict | None]` and then consume `self.stream(messages, conn, usage)` **without** forwarding `schema`. So `ModelessHolder`, `HeldOpenRouter` and `HeldCassette`, which override `stream(messages, conn, usage=None)`, need no change (Minor 3).
  - `FakeLLM.stream(messages, conn, usage=None, *, schema=None)` accepts the keyword for signature parity and records nothing (the facade's decide path only calls `complete`).
  - `StallingGateway.complete` (~line 408) gains the same keyword and recording.
  - Update the module docstring's surface listing.

- [ ] **Step 1: Write the failing tests** (`tests/test_llm_structured.py`). Use `httpx.MockTransport` for the adapters, as `test_anthropic.py` does.
  - `test_openrouter_sends_response_format_only_with_a_schema`
  - `test_openai_compatible_sends_response_format_only_with_a_schema`
  - `test_anthropic_merges_format_into_output_config`: a body that already has `output_config.effort` keeps it beside `format`.
  - `test_the_facade_sends_structured_mode_only_to_a_flagged_attempt`: a conn without `STRUCTURED_KEY` and a `schema` produces a body with no `response_format`, and the adapter is called without a `schema` keyword.
  - `test_structured_mode_is_decided_per_attempt`: the primary is flagged and fails retryably with zero retries; the fallback under `FALLBACK_KEY` is not flagged. The first body carries `response_format`, the second does not (Review Focus 2).
  - `test_a_refused_schema_is_not_a_preset_refusal` (I3): an Anthropic primary on an adaptive-thinking model with an effort preset, flagged, answers 400 whose detail names `output_config.format.schema`. The fallback is still tried and answers; no `PresetRefusalError` is raised. Its twin: the same primary answering 400 naming `output_config.effort` still raises `PresetRefusalError`.
  - `test_the_resolver_flags_only_a_decide_resolutions_capable_attempts`: a catalog row whose `params` contain `structured_outputs` gives a decide resolution a conn with `STRUCTURED_KEY`, and a generate resolution of the same task none; an unknown capability leaves it absent; a row naming only `response_format` leaves it absent.
  - `test_structured_key_is_restated_equal`: `resolve.STRUCTURED_KEY == llm.STRUCTURED_KEY`.
  - `test_fakes_record_the_schema`: `FakeLLM.complete(..., schema=s)` records `s`; a `HeldCassette` driven through `complete(..., schema=s)` does not raise.
- [ ] **Step 2:** Run the new file: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the new file plus the five suites listed under **Files** (all PASS and unmodified), `tests/test_inference_capabilities.py tests/test_model_catalog.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_llm_fakes.py tests/test_import_guard.py`, then `make check-lint check-mypy PY=$PY`.
- [ ] **Step 5:** Commit `feat(llm): generate(schema=) sends structured mode where the attempt can take it`.

---

### Task 3: `inference.decide` and its guards

**Depends on:** Tasks 1 and 2, and **E's Task 2** (the account block, `llm_usage.with_account`, `_stamp`'s `account` line, and `record(operation=, decision_mode=)`). If E's Task 2 has not landed, see "If F is implemented first" under coordination before starting.

**Files:**
- Create:
  - `backend/src/grimoire/inference.py`
  - `templates/decide/system.j2`
  - `templates/decide/user.j2`
  - `backend/tests/test_inference_decide.py`
- Modify:
  - `store/inference/resolved.py`
  - `store/inference/resolve.py`
  - `store/inference/settings.py` (`_problem`)
  - `routes/common.py` (`UsableInference.conn`)
  - `backend/tests/llm_fakes.py` (`decision_reply`)
  - `backend/tests/test_operation_guard.py`: slice D creates it as **the** §14.1 guard file with a binding walker for `grimoire.store.inference.embed`. F generalises that walker to take the target module and appends the decide half. If F lands first, F creates the file with the generalised walker and its half, and adds the `CONTRIBUTING.md` row (text under coordination); D then passes its own target module and appends its half.
  - `backend/tests/test_usage_guard.py`
  - `scripts/verify_templates.py`

**Interfaces:**

*Consumes:* everything in Task 1, `LLMClient.complete(..., schema=)` from Task 2, and E's `llm_usage.ACCOUNT_KEY` / `with_account`.

*Resolver:*
- `Attempt.decision_mode: str = ""` is the resolver's capability answer: which backend will serve the attempt first.
  - `resolve.decision_mode(attempt: Attempt) -> str` returns `"structured"` when the attempt is not known `no` on `generate`, else `""`. H changes this body to return `"native"` where `decide_native` is `yes`.
  - At the tail of `resolve.resolve`, when `operation == "decide"`, each attempt is `dataclasses.replace(a, decision_mode=decision_mode(a))`. Its `conn` is the same dict object.
- **The decide skip (§5.5, I5).**
  - `ResolvedInference.skipped: tuple[str, ...] = ()`: when `operation == "decide"`, `"generate"` is in the primary's `_missing(...)`, and a fallback attempt exists whose `_missing(...)` is empty, then `skipped` holds the primary's missing needs, `missing` is `()` (so `incapable` does not refuse), and the fallback is **not** attached to the primary's dict (nothing after it to fall back to).
  - `ResolvedInference.conn` returns `attempts[1].conn` when `skipped`, otherwise `attempts[0].conn`; `UsableInference.conn` (`routes/common.py:1121`) does the same. The docstrings say "the connection dict the facade is sent". `ResolvedInference.fallback` returns None when `skipped` (the attempt it would name is the one sent). `ResolvedInference.decision_mode` (property) is the decision mode of that same attempt, or `None` when it is `""` or nothing resolved.
  - With no fallback, or one that cannot generate either, nothing changes: `missing` keeps `generate` and §5.3's 409 stands.
  - `resolve.skip_text(resolved) -> str | None`: the sentence for a skipped resolution, in `incapable_text`'s register: "The <route label> route runs on the <role> role (<model> on <provider>), which cannot generate; until native decisions arrive it is answered by the fallback (<model> on <provider>)." For a pin, "is pinned to …".
  - `settings._problem` returns the refusal sentence, else `skip_text`, so the route row shows why its model is not the one answering.
  - `OPERATION_CAPABILITY["decide"]` stays `"generate"`. H widens it and replaces the skip.
- The resolver writes nothing into any account block. E's tail stamp (`operation`, `role`) runs before F's tail code.

*`inference.py`:*
- `structured_messages(items: Sequence[decisions.Item], *, explain: str = "") -> list[dict]`:
  - system: `prompts.render("decide/system.j2", schema=decisions.schema(items, explain=bool(explain)), explain=bool(explain))`;
  - user: `prompts.render("decide/user.j2", items=items, explain=explain)`.
  - The template renders the schema with `tojson`, and each question's type, instructions and options (id, description) or levels (index, description).
- `Around = Callable[[Awaitable[str], dict], Awaitable[str]]`: given the facade call and the meter's live holder, return what to await instead.
- `async def decide(task: str, items: Sequence[decisions.Item], *, client: LLMClient, resolved: ResolvedInference, explain: str = "", campaign: str = "", scene: str = "", post: int | None = None, round_id: str = "", capture: Callable[[list[dict]], Awaitable[None]] | None = None, around: Around | None = None) -> decisions.Decision`.

*Behaviour of `decide`, in order:*
1. Raise `ValueError` when `resolved.task != task`, when `resolved.operation != "decide"`, or when `resolved.conn` is None.
2. `decisions.validate(items)`. Both checks raise before any meter opens, so no ledger row is written.
3. `backend = resolved.decision_mode`, dispatched through `_BACKENDS: dict[str, Backend]`, where `Backend = Callable[[tuple[decisions.Item, ...], _Call], Awaitable[decisions.Decision]]` (a backend returns the whole `Decision`, since it alone knows which selection answered and what each chunk's meter filed) and `_Call` is a frozen dataclass of the keyword arguments `decide` received. F registers only `"structured"`. A mode with no backend is a `ValueError`. **H adds `"native"` and the §5.5 chain here; the signature of `decide` does not change.**
4. **Structured backend, per chunk** (`decisions.chunks`):
   1. `conn = _with_mode(resolved.conn, "structured")` (I7). `_with_mode(conn, mode)` returns `llm_usage.with_account(conn, decision_mode=mode)`, and when `llm.FALLBACK_KEY` is in it, that copy with `FALLBACK_KEY` replaced by `llm_usage.with_account(conn[FALLBACK_KEY], decision_mode=mode)`. No block of the resolution is mutated (E's rule).
   2. `msgs = structured_messages(chunk, explain=explain)`;
   3. `await capture(msgs)` when given;
   4. `with store.usage.meter(task, campaign=campaign, scene=scene, post=post, round_id=round_id) as m:` let `call = client.complete(msgs, conn, m.usage, schema=decisions.schema(chunk, explain=bool(explain)))`, then `text = await (around(call, m.usage) if around else call)`. The budget (when any) therefore runs inside the meter, as today (I2). Nothing is written into `m.usage`: operation and decision mode arrive through the account block;
   5. `decisions.parse(text, chunk, explain=bool(explain))`.
5. **Failures:**
   - An `LLMError` from a chunk, when **no** chunk has answered and no later chunk answers, is re-raised: the first such error, after the remaining chunks have been tried.
   - Once any chunk has answered, a failed chunk's items become `decisions.unanswered(chunk, "error")`.
   - `CancelledError` and other `BaseException`s propagate untouched; the meter files `aborted`.
6. **Result:** `Decision(items=<in input order>, backend="structured", provider=<id of the conn under llm.ATTEMPTED in the last answering chunk's holder>, model=llm.effective_model(that conn), usage=<each chunk's Meter.row, None skipped>)`.
7. Rendering runs on the event loop inside `decide` (a short Jinja render per call). Accepted, and said in the speaker's commit (Minor 10).

*Shared test helper:*
- `llm_fakes.decision_reply(*answers: dict, rationales: Sequence[str] = ()) -> str`.
- `answers[i]` is item `i`'s `{question_id: value}`. It returns the JSON string `{"<i>": {"answers": ..., "rationale"?: ...}}`.
- Tests write decide replies only through this helper.

*`test_operation_guard.py`, the decide half (spec §14.1: "every `inference.decide` names a task on a decide route"), I1:*
- **Bindings, resolved, never spellings.** Reuse D's alias resolution (the step that records names bound by an `ImportFrom` resolving to `grimoire.store.inference.embed`), generalised to take a target module: `bindings(tree, modname, target) -> (module aliases, name aliases)`. D keeps its own spelling rules for `embed_sync`, which is a unique name; F adds none, because `decide` is a common method name. For `grimoire.inference` the walk covers every module under `backend/src/grimoire/` and resolves each `ImportFrom` to an absolute module with `test_import_guard._resolve` (imported, not copied):
  - a **module binding** is `from <grimoire> import inference [as x]` or `import grimoire.inference as x`;
  - a **name binding** is `from <grimoire.inference> import decide [as y]`, relative or absolute.
- An **operation call** is `x.decide(...)` for a module binding `x`, or `y(...)` for a name binding `y`. Nothing else is: `exam.decide(decisions)` (`routes/scenes.py:2066`, `continuity_identity.Examination.decide`) and `self.decide(...)` (`store/context/activation.py:536`) are never matched.
- Every operation call must:
  - pass a first positional **string literal** whose `routing.route(task).operation == "decide"`;
  - pass `resolved=`.
- An operation binding loaded as a value anywhere other than a call's `func` fails (a `decide` handed to `run_in_threadpool` would hide its literal).
- The safety rule, both directions:
  - every task of every route with `operation == "decide"` is the literal of some operation call;
  - every route with `default_role == "decision"` has `operation == "decide"`.
- No exemption markers.
- `test_the_guard_flags_planted_cases`:
  - caught: `from .. import inference as operations` then `operations.decide("chat", items, client=c, resolved=r)` (not a decide route); `operations.decide(task, …)` (not a literal); `operations.decide("scene-break", items, client=c)` once `scene-break` is a decide route (no `resolved=`); `from ..inference import decide as pick` then `pick("chat", …)`; `run_in_threadpool(operations.decide, …)`.
  - **not** caught (true negatives): `exam.decide(rows)`; `self.decide(p, level, pullers)`; a local `def decide(...)` called bare in a module that never imports `grimoire.inference`.
- `test_the_walk_finds_the_call_sites`: at least N operation calls, where N is 0 in this task, 1 after Task 6, 2 after Task 8 and 3 after Task 10. Each switch task bumps it.

*`test_usage_guard.py`:* add a module-level `EXTRA_SOURCES = (<path of grimoire/inference.py>,)`, scanned by the existing `_offenders` and marker loops beside `routes/`. Everything else is untouched; D's appended embed test is a separate function. This makes "decide is metered" (§14.1) a static fact: `decide`'s `client.complete` must pass `<x>.usage`.

*`verify_templates.py`:* `structured_messages` equals direct renders of `decide/system.j2` and `decide/user.j2`. Check every branch: explain on and off; predicate, choice with and without `allow_none`, and score; one item and two.

- [ ] **Step 1: Write the failing tests** (`tests/test_inference_decide.py`). Each uses a `FakeLLM` scripted with `decision_reply` and a real resolution on an isolated store (`GRIMOIRE_HOME`). Until Task 6 no route is a decide route, so the tests resolve with `resolve.resolve("scene-break", operation="decide")` directly.
  - `test_decide_returns_one_result_per_item_in_order`
  - `test_decide_sends_the_schema_and_the_prompt_carries_it`: `fake.schemas[-1] == decisions.schema(items, explain=False)`, and the schema's `tojson` render appears in the system message.
  - `test_decide_refuses_a_generate_resolution_and_a_mismatched_task`
  - `test_an_invalid_request_files_no_row`: an empty ledger after `DecideRequestError`.
  - `test_each_chunk_is_its_own_metered_row`: 9 items give 2 ledger rows under the task, and `fake.calls == 2`.
  - `test_decide_writes_nothing_into_the_holder`: after a decide call the holder's keys are exactly what `llm._stamp` (via the fake's stamp) wrote (ruling 9).
  - `test_a_decide_row_files_its_operation_and_mode` (I6, unconditional): the filed row has `operation == "decide"` and `decision_mode == "structured"`. With a failing primary, the fallback's row says the same.
  - `test_the_mode_is_stamped_per_call_on_copied_blocks` (I7): the conn the fake received carries `decision_mode: "structured"` in its account block and in its `FALLBACK_KEY` dict's block; `resolved.conn[ACCOUNT_KEY]` and the fallback attempt's block are unchanged and carry no `decision_mode`.
  - `test_a_failed_only_chunk_raises_the_llm_error`
  - `test_a_failed_chunk_beside_an_answered_one_is_marked_error`
  - `test_capture_sees_each_chunks_messages_before_it_is_sent`
  - `test_around_runs_inside_the_meter`: an `around` that raises `LLMError("timeout")` files one `error/timeout` row (I2).
  - `test_decision_mode_is_structured_on_every_decide_attempt`, and `None` on a generate resolution.
  - `test_a_fallback_without_structured_mode_still_answers` (Review Focus 2): the primary is flagged and fails; the fallback replies with the JSON in prose; the answers parse.
  - `test_resolve_skips_a_decide_only_primary_for_a_generating_fallback` (I5): at format 2, a Decision role on an OpenRouter model whose cached catalog row has `outputs: ["decisions"]`, with a role fallback that generates. `resolve(..., operation="decide")` has `skipped == ("generate",)`, `missing == ()`, `refusal(...) is None`, `.conn` is the fallback's dict, and `settings`' route `problem` is `skip_text`. Without a fallback, `missing == ("generate",)` and the refusal is 409 `incapable`. A generate resolution of the same role is unchanged.
  - `test_operation_guard.py`: the checks above plus their planted cases. At this task no route is `decide`, so the safety half is vacuously true. Pin that with `test_no_route_is_decide_before_a_call_site_decides`, and **delete** that pin in Task 6.
- [ ] **Step 2:** Run both files: FAIL.
- [ ] **Step 3:** Implement. Write `decide/system.j2` in the house register:
  - you answer questions about material you are given;
  - JSON only;
  - the schema;
  - how each question type is answered (`true`/`false`; an option id, or `null` where the schema allows it; a level number);
  - "do not guess: when the material does not settle a choice that allows none, answer null";
  - when `explain` is on, that `rationale` follows the user message's instruction.
- [ ] **Step 4:** Run `tests/test_inference_decide.py tests/test_operation_guard.py tests/test_usage_guard.py tests/test_routing_guard.py tests/test_inference_resolve.py tests/test_inference_settings.py tests/test_inference_cascade.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_import_guard.py tests/test_docs_guard.py tests/test_llm_fakes.py tests/test_usage_fields.py` (E's file), then `make check-lint check-mypy check-templates PY=$PY`.
- [ ] **Step 5:** Commit `feat(inference): decide() over structured generation, metered per call`.

---

### Task 4: The eval gate

**Files:**
- Create:
  - `evals/gate.py`
  - `evals/legacy.py`
  - `evals/gate/` (directory; empty until Task 5)
  - `backend/tests/test_decide_gate.py`
- Modify: `evals/run.py` (`--gate`), `evals/runner.py` and `evals/cases.py` (`Case.task`, `Case.schema`, I9), `evals/README.md` ("The decide gate" section, and what each mode measures)

**What the gate decides** (ruling 7, strengthened by I8): spec §7.4 says "switches its call site only when the structured backend equals or beats today's parse on [recorded cases], offline".

- Per conversion, a corpus of reply pairs. Each pair is a reply *of the same shape* to today's prompt and to the decide prompt, together with what the reply means (`intended`).
- **The legacy replies come from today's parser tests**, not from the new parser's author: every input string of the legacy parse tests a switch task deletes is an entry's `legacy` reply, and `test_every_legacy_parse_case_is_a_gate_entry` holds that. Each prepare task adds a few more shapes (decide-only deviations).
- **Each side is scored by the full call-site outcome:** what the app would store or raise, through the production mapping, compared by value. Never booleans alone.
- **Pass:** on every entry, `legacy == intended` implies `decide == intended`. The structured backend may beat today's parse on an entry, and may never lose one.
- `evals/legacy.py` holds **verbatim frozen copies** of today's parsers, so the gate still runs after Tasks 6, 8 and 10 delete them from production. Nothing in it is imported from production parsers:
  - `scene_break.parse_output` and its `_extract_json`;
  - `voice_drift.parse_output` with `_extract_object` and `_VERDICTS`;
  - the selector's `json.loads` and `response_protocol.validate_handoff`, copied verbatim (production keeps its own for handoff fences; a later edit there must not move the legacy side).
  - The module docstring says the copies are never edited.

**Interfaces:**
- `Entry(shape: str, intended: object, legacy: str, decide: str, aux: Mapping[str, str] = {})`. `aux` carries a conversion's extra replies (scene-break: `aux["title"]`, the reply to the title prompt).
- `Conversion(id: str, items: Callable[[], tuple[decisions.Item, ...]], explain: bool, legacy: Callable[[str], object], decide: Callable[[decisions.ItemResult, Entry], object], corpus: Path, legacy_cases: tuple[str, ...])`.
  - `items` builds the same items the production builder makes, from fixed fixture inputs.
  - `decide` maps the parsed `ItemResult` through the production mapping (Tasks 5, 7, 9).
  - `legacy_cases` are the input strings of today's parse tests for this conversion, copied verbatim when the prepare task is written.
- `load(conv) -> tuple[Entry, ...]` reads `evals/gate/<id>.json`: `[{"shape", "intended", "legacy", "decide", "aux"?}, ...]`. Outcomes are JSON values (lists for tuples).
- `judge(conv) -> GateResult(conversion: str, entries: int, legacy_right: int, decide_right: int, regressions: tuple[str, ...])`; `passed` is the property `not regressions`.
- `GATES: tuple[Conversion, ...]` is empty here; Tasks 5, 7 and 9 each append one.
- `evals/run.py --gate`: prints one line per conversion, e.g. `scene-break: legacy 19/24, decide 24/24 — PASS`, and exits non-zero on a regression. It never makes a call. Combined with `--live`, `--record` or `--case` it is an argparse error (Minor 13).
- **`--live` measures what production sends (I9).** `Case` gains `task: str = ""` and `schema: Callable[[dict], dict] | None = None`. In live mode, a case with `task` is resolved through `store.inference.resolve(task, operation="decide")` before `GRIMOIRE_HOME` is repointed (as `resolve_connection` is), refused with `RuntimeError(<refusal detail>)` when `resolve.refusal` refuses it, and sent with `client.complete(messages, resolved.conn, schema=case.schema(ctx))`. Replay ignores both fields. Nothing in this slice runs it.

- [ ] **Step 1: Write the failing tests** (`backend/tests/test_decide_gate.py`), with a planted conversion built inline from Task 1 types and a two-entry tmp corpus.
  - `test_judge_counts_and_passes_when_decide_never_loses`
  - `test_judge_names_a_regression`
  - `test_judge_compares_whole_outcomes`: a planted entry whose decide outcome matches `intended` on its first element only is a regression.
  - `test_every_gate_passes`: parametrized over `gate.GATES`. It has no cases until Task 5, which is fine.
  - `test_every_legacy_parse_case_is_a_gate_entry` (I8): parametrized over `gate.GATES`; each string in `conv.legacy_cases` is the `legacy` of some entry in its corpus.
  - `test_every_corpus_file_belongs_to_a_gate`: no orphan files in `evals/gate/`.
  - `test_run_gate_flag_exits_zero_when_all_pass`
  - `test_gate_refuses_live_record_and_case`
  - `test_live_resolves_a_decide_case_on_its_task` (in `tests/test_evals.py`): with `runner.live`'s client monkeypatched to a recorder, a case with `task` and `schema` is sent on the Decision resolution's conn with `schema=`; replay of the same case passes no schema.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Implement `gate.py`, the `legacy.py` copies, `--gate`, `Case.task`/`Case.schema` and the README section. The README says exactly what each mode can and cannot show: the gate compares *parsers* on recorded shapes; replay grades recorded output; `--live` measures whether a model *follows* the decide prompt on the Decision role's own connection with structured mode, costs money, and is never run without the user's explicit approval.
- [ ] **Step 4:** Run `tests/test_decide_gate.py tests/test_evals.py tests/test_eval_graders.py`, then `make check-lint check-mypy PY=$PY`.
- [ ] **Step 5:** Commit `feat(evals): the decide gate — the structured parse may beat today's, never lose to it`.

---

### Task 5: Scene-break, prepared (no behaviour change)

**Files:**
- Modify: `store/scene_break.py`
- Create:
  - `templates/scene_break/question.j2`
  - `templates/scene_break/explain.j2`
  - `templates/scene_break_title/system.j2`
  - `templates/scene_break_title/user.j2`
  - `evals/gate/scene-break.json`
  - `evals/recordings/decide-scene-break.{compliant,undecodable,wrong,no-reason}.json`
- Modify: `evals/gate.py` (one `Conversion`), `evals/cases.py` (one `Case`), `scripts/verify_templates.py`, `templates/README.md`
- Test: `backend/tests/test_scene_break_store.py` (additions)

**Interfaces** (all in `store/scene_break.py`, pure):
- `QUESTION_ID = "over"`.
- `build_item(transcript: str, signals: list[dict], facts: dict | None = None, title: str = "") -> decisions.Item`:
  - `context` is today's `scene_break/user.j2` render, unchanged;
  - one `Predicate(QUESTION_ID, render("scene_break/question.j2"))`.
- `explain() -> str` renders `scene_break/explain.j2`.
- `verdict_of(result: decisions.ItemResult) -> dict` returns `{"break": answer is True, "reason": " ".join(rationale.split()), "title": ""}`. `None` (unreadable) is `break: False`, which is today's safe direction. The collapse is today's `parse_output` expression.
- `build_title_prompt(transcript: str, facts: dict | None, title: str, reason: str) -> list[dict]` renders `scene_break_title/{system,user}.j2`.
- `TITLE_MAX = 80`.
- `parse_title(text: str) -> str`: collapse all whitespace (newlines included) to single spaces, as today's parser does; strip surrounding `"`, `'` and backticks, then trailing `.!?:;`; cut at `TITLE_MAX`; `""` for nothing. The cap is structural: a title is one frontmatter line shown in a chip, and the template asks for about six words. The stored title is therefore cleaned and capped where today's was only collapsed (recorded in the spec, Minor 6).

**Templates** (move text out of `scene_break/system.j2`; invent no new criteria). The carried fragments below are checked by `verify_templates.py` (I8), compared after collapsing whitespace runs to one space:

| Fragment of `scene_break/system.j2` | Goes to |
|---|---|
| "You are watching a role-play scene that is still being played, and answering one question about it: has the scene reached a natural place to stop?" | `question.j2` |
| the "A scene ends when the beat it was about has resolved …" paragraph, whole | `question.j2` |
| the "You will be told which mechanical signals prompted the question. …" paragraph, whole | `question.j2` |
| "one sentence saying what resolved", "when the scene is still mid-beat", "one sentence saying what is still unresolved" | `explain.j2` |
| "a title for the scene that would start next"; "a short phrase — no more than about six words, no quotation marks, no trailing punctuation"; "Do not invent events the transcript does not show, and do not describe what happens next beyond naming the scene it would be." | `scene_break_title/system.j2` |

Excluded as format (owned by `decide/system.j2`): "Reply with ONLY a JSON object, no prose around it and no code fence:", the `{"break": true` example, `Set "break" to false`, `"title" is an empty string`, `Keep "reason" to a single sentence.`, `Keep "title" to`.

- `scene_break_title/system.j2` adds "Reply with the title alone." `scene_break_title/user.j2` is the head of `user.j2` (title, facts), then "Why it ended: {{ reason }}" when given, then the transcript.
- `scene_break/system.j2` stays until Task 6 (the legacy path still uses it).

**`verify_templates.py`:**
- `build_item`'s context and instructions against direct renders, over today's head/signal grid; `build_title_prompt` against both templates, with and without a reason.
- **Carried fragments (I8):** each fragment in the table is in its target template's source.
- **Coverage, while the legacy template exists:** collapse whitespace, split `scene_break/system.j2` into paragraphs on blank lines and sentences on `(?<=[.?!])\s+`; every sentence either contains a carried or excluded fragment or is contained in one. Task 6 drops this coverage half with the template and keeps the carried-fragment half.

**Gate corpus** (`evals/gate/scene-break.json`). The outcome is `[break, reason, title]`, what `_break_commit` stores (title only on a break).
- Legacy side: `legacy.scene_break_parse(text)` → `[a["break"], a["reason"], a["title"] if a["break"] else ""]`.
- Decide side: `v = verdict_of(result)`; `[v["break"], v["reason"], parse_title(entry.aux.get("title", "")) if v["break"] else ""]`.
- `legacy_cases`, verbatim from `test_scene_break_store.py`'s parse tests:
  - `{"break": true, "reason": "The ledger changed hands.", "title": "The Long Walk Back"}`
  - the prose-then-json-fence reply of `test_prose_around_the_object_is_tolerated` (copy the literal from the test source)
  - `""`, `"no idea"`, `"[1, 2, 3]"`, `"null"`
  - `{"break": true, "reason": "They parted.\n---\ndone: false", "title": "A\nB"}` (the JSON escapes as written in the test)
  - `{"break": "yes", "reason": "r"}`
- Each gets a decide twin of the same shape (`{"0": {"answers": {"over": …}, "rationale": …}}`, the same fence or prose, the same garbage), with `aux.title` equal to the legacy title where there is one.
- Added shapes: `string-boolean` (`"over": "true"`, both sides False), `truncated`, `unwrapped-item` and `flattened` (`{"over": true, "rationale": "…"}`; decide-only deviations whose legacy side is the clean legacy reply), `no-reason-yes`, and `quoted-title` (`aux.title` `"\"The Long Walk Back.\""`: decide strips the quotes and the period, which is the intended title).

**Eval case** `decide-scene-break` (`task="scene-break"`, `schema` from `decisions.schema`):
- **build:** a Saltmarch scene (invented: Seraphine settles Mara's debt and the ledger changes hands), one signal and facts.
- **prompt:** `inference.structured_messages([build_item(...)], explain=explain())`.
- **grade:**
  - `decide.json`: the reply decodes to an object. When this fails the output checks short-circuit, as `fence.present` does;
  - `decide.answer`: `over` is `True`;
  - `decide.rationale`: non-empty;
  - `prompt.question`: `question.j2`'s render appears verbatim;
  - `prompt.context`: the transcript appears;
  - `prompt.schema`: `schema` rendered with `tojson` appears in the system message;
  - `prompt.explain`.
- **Recordings:**
  - `compliant`;
  - `undecodable` → `("decide.json",)`;
  - `wrong` (says false) → `("decide.answer",)`;
  - `no-reason` → `("decide.rationale",)`.

- [ ] **Step 1: Write the failing tests.**
  - `test_scene_break_store.py`:
    - `test_build_item_carries_the_old_user_message_as_context`
    - `test_verdict_of_an_unreadable_answer_is_no_break`
    - `test_a_multi_line_rationale_is_collapsed`
    - `test_parse_title_strips_quotes_and_punctuation_collapses_lines_and_caps_length`
    - `test_parse_title_of_nothing_is_empty`
  - The gate entry and the eval case, through `tests/test_decide_gate.py` and `tests/test_evals.py`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Implement, with the `verify_templates.py` checks above and the `templates/README.md` entries.
- [ ] **Step 4:** Run `tests/test_scene_break_store.py tests/test_decide_gate.py tests/test_evals.py tests/test_eval_graders.py tests/test_docs_guard.py`, then `$PY evals/run.py --gate` (expect `scene-break … PASS`), then `make check-lint check-mypy check-templates PY=$PY`. **Do not start Task 6 unless the gate passes.**
- [ ] **Step 5:** Commit `feat(scene-break): the decision item, its title prompt, and its gate`.

---

### Task 6: Scene-break, switched

**Files:**
- Modify:
  - `routes/scenes.py` (`_break_once`, `_break_ask`, new `_break_title`, new `_break_title_commit`)
  - `store/routing.py`
  - `store/scene_break.py` (delete `build_prompt`, `parse_output`, `_extract_json`)
  - `templates/scene_break/system.j2` (delete)
  - `scripts/verify_templates.py` (drop the legacy checks and the coverage half; keep the carried fragments)
  - `templates/README.md`
- **Every inline fake's `complete` (Minor 3).** List them with `grep -n "def complete(self" tests/*.py` (outside `llm_fakes.py`). At plan time that is `test_routes.py` (~7378, 7923, 7981, 8242, 8432, 8638, 8659, 8687, 8752, 8804, 8846, 8963, 9321, 9472, 9595, 14131, 14777), `test_rolling_summary_routes.py` (ten), `test_observability_routes.py:207`, `test_ingest_scene.py:150`, `test_review_detach.py:393`, plus `test_locks_http.py`, `test_runs_routes.py` and `test_inference_fallback.py`. Each gains `*, schema=None` and nothing else. A decide path reaching one without it raises a `TypeError` that absorb or a follow-up swallows into a "failed" phase, so the test would stay green while testing something else. Tasks 8 and 10 re-run the grep for any new one.
- Tests:
  - `tests/test_scene_break_routes.py`
  - `tests/test_turn_follow_ups.py`
  - `tests/test_scene_break_store.py`: delete the `parse_output` tests. Each input is in `legacy_cases` (Task 5), which `test_every_legacy_parse_case_is_a_gate_entry` holds; say so in the commit.
  - `tests/test_routing.py`
  - `tests/test_inference_equivalence.py`, `tests/test_inference_equivalence_c.py` (`NEW_TASKS`)
  - `tests/test_operation_guard.py` (delete the Task 3 vacuous pin; the walk minimum becomes 1)

**Interfaces:**
- **`store/routing.py`:**
  - `scene_break`: `operation="decide", default_role="decision"`.
  - `summary`: tasks `("rolling-summary", "scene-break-title")`; label `"Summaries & scene titles"`; hint "The live rolling summary, and the title a proposed scene break suggests."
  - Update the `Route` docstring ("Both are generate in this slice").
  - `LEGACY_ROUTES` then lists `summary` with `scene-break-title`. Update `test_routing.py::_ORIGINAL_TASKS` and `test_every_route_declares_operation_and_default_role` (the expected map: `scene_break` decide/decision).
- **`routes/scenes.py`** imports `from .. import inference as operations`.
- **`_break_once`:** `resolved = require_inference("scene-break", cid, operation="decide")`, still **before** the due check (today's ordering). It passes `resolved` to `_break_ask`.
- **`_break_ask`** (Minor 6: commit the verdict first):
  - It builds `item = store.scene_break.build_item(...)` from the same `shown` transcript, signals, facts and title as today.
  - `decision = await operations.decide("scene-break", [item], client=client, resolved=resolved, explain=store.scene_break.explain(), campaign=cid, scene=sid)`.
  - `except LLMError as exc: raise _llm_http_error(exc) from exc`, exactly as today.
  - `answer = store.scene_break.verdict_of(decision.items[0])` (title `""`).
  - Today's digest and `_break_commit`, unchanged.
  - **Only when `result["landed"]` and `answer["break"]`:** `title = await _break_title(cid, sid, transcript_text, facts, scene_title, answer["reason"], client)`; when `title` is non-empty, `result = await run_in_threadpool(_break_title_commit, cid, sid, view["watermark"], digest, answer["reason"], title)`.
  - The response is built from `result["scene"]` as today.
- **`async def _break_title(cid, sid, transcript, facts, title, reason, client) -> str`:**
  - `resolved, _why = _soft_resolved(lambda: require_inference("scene-break-title", cid))`. `_soft_resolved` is added in this task: `routes/common._soft_resolved(thunk) -> tuple[UsableInference | None, str]`, with `_soft_inference` reimplemented as its `.conn`.
  - It returns `""` when unresolved.
  - `with store.usage.meter("scene-break-title", campaign=cid, scene=sid) as m: text = await client.complete(store.scene_break.build_title_prompt(...), resolved.conn, m.usage)`.
  - Any `LLMError` returns `""`; the meter has filed it.
  - It returns `parse_title(text)`.
- **`_break_title_commit(cid, sid, watermark, digest, reason, title) -> dict`:** under `store.locks.campaign_lock(cid)`, reads the scene's `scene_break_fields`; only when they still equal `(at=watermark["at"], digest=digest, verdict="yes", reason=reason, title="")` does it call `store.scenes.set_scene_break(...)` with the same watermark fields and the title, then `store.revision.bump(cid)` (#409, as `_break_commit` does). Otherwise it writes nothing and the paid title is dropped. Returns `{"scene": <the scene read back>}`.
- **Equivalence:**
  - `NEW_TASKS = {"scene-break-title": "rolling-summary"}`, in `test_inference_equivalence.py`; the C suite imports it.
  - Before comparing, assert each new task's observed `tasks` cell (and, in C, its `c.lowered` cell) equals its sibling's, at both scopes. Then drop it.
  - The JSON fixtures are untouched.

- [ ] **Step 1: Write and convert the failing tests.**
  - In `test_scene_break_routes.py`, `YES`/`NO` become `decision_reply({"over": True}, rationales=["The ledger changed hands."])` and the matching `False` reply.
  - `_judge(*verdicts, title="The Long Walk Back")` scripts a title turn after each `YES` verdict that lands. Prompt assertions read the decide call's messages (`fake.requests`), not `fake.messages`, when a title call follows.
  - New tests:
    - `test_a_yes_drafts_the_title_in_a_second_call_on_the_summary_route`: two ledger rows, `scene-break` and `scene-break-title`; the stored title is the cleaned one.
    - `test_a_no_makes_no_title_call`
    - `test_a_verdict_that_did_not_land_makes_no_title_call`: the transcript is edited inside the prefix while the decide call is out; `fake.calls == 1`.
    - `test_a_title_is_dropped_when_the_verdict_moved_on`: a dismissal lands while the title call is out; the stored proposal is the dismissal's, with no title.
    - `test_a_failed_title_keeps_the_verdict` (Review Focus 4): a title script that raises `LLMError` → stored `yes` with title `""`; a `summary` route pinned at a keyless provider → the same.
    - `test_a_decide_only_decision_model_falls_to_the_role_fallback` (Review Focus 1): at format 2, the Decision role on an OpenRouter model whose cached catalog row has `outputs: ["decisions"]`, with a generating role fallback. The POST answers; the decide call's conn is the fallback's; nothing is sent to the decide-only model.
    - `test_a_decide_only_decision_model_without_a_fallback_is_refused`: the same with no fallback. The POST is 409 `incapable` naming the Decision role, and `fake.calls == 0`.
    - `test_the_decision_role_now_serves_scene_break`: with Decision set to provider B, the decide call's conn is B.
    - `test_scene_break_on_a_legacy_store_resolves_as_before`.
  - `test_turn_follow_ups.py`: its `NO_BREAK` and verdict fixtures become decide replies.
- [ ] **Step 2:** Run those files: FAIL.
- [ ] **Step 3:** Implement and delete the legacy code. `routing.py`'s flip lands in this same commit (safety rule).
- [ ] **Step 4:** Run:
  ```
  tests/test_scene_break_routes.py tests/test_scene_break_store.py tests/test_turn_follow_ups.py \
  tests/test_runs_detach.py tests/test_routing.py tests/test_routing_guard.py tests/test_operation_guard.py \
  tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py tests/test_inference_resolve.py \
  tests/test_inference_settings.py tests/test_inference_cascade.py tests/test_decide_gate.py tests/test_evals.py \
  tests/test_regex_prompt_guard.py tests/test_docs_guard.py tests/test_usage_guard.py \
  tests/test_rolling_summary_routes.py tests/test_observability_routes.py tests/test_ingest_scene.py \
  tests/test_review_detach.py tests/test_locks_http.py tests/test_runs_routes.py tests/test_inference_fallback.py
  ```
  plus `tests/test_routes.py -k "absorb or drift or break or summary"`. Then `make check-lint check-mypy check-templates PY=$PY`.
- [ ] **Step 5:** Commit `feat(scene-break): ask decide(), draft the title apart, and default to the Decision role`. The body records: the verdict commits first; the stored title is cleaned and capped; it comes from the `summary` route's model; a YES costs two calls and two copies of the transcript.

---

### Task 7: Voice drift, prepared (no behaviour change)

**Files:**
- Modify: `store/voice_drift.py`
- Create:
  - `templates/voice_drift/question.j2`
  - `templates/voice_drift/option.j2`
  - `templates/voice_drift/explain.j2`
  - `evals/gate/voice-drift.json`
  - `evals/recordings/decide-voice-drift.{compliant,undecodable,wrong,no-note}.json`
- Modify: `evals/gate.py`, `evals/cases.py`, `scripts/verify_templates.py`, `templates/README.md`
- Test: `tests/test_voice_drift_store.py` (additions)

**Interfaces** (`store/voice_drift.py`, pure):
- `QUESTION_ID = "verdict"`.
- `ALIASES = {NOT_ENOUGH: ("insufficient", "unclear")}`: today's two word-synonyms, kept (ruling 6, Open (c)). `validate` rejects any collision after normalising.
- `build_item(name: str, anchor: str, transcript: str, correction: str = "") -> decisions.Item`:
  - `context` is today's `voice_drift/user.j2` render, unchanged;
  - one `Choice(QUESTION_ID, render("voice_drift/question.j2"), options=(Option(DRIFT, render("voice_drift/option.j2", verdict=DRIFT)), Option(IN_VOICE, ...), Option(NOT_ENOUGH, ..., ALIASES[NOT_ENOUGH])))`;
  - no `allow_none`.
- `explain() -> str` renders `voice_drift/explain.j2`.
- `finding_of(result: decisions.ItemResult) -> dict` returns `{"verdict": answer or UNKNOWN, "note": rationale}`. `None` is `UNKNOWN`, which is today's failed check.
- `check_failure(finding: dict) -> str | None`: the three per-finding failures moved verbatim out of `_stage_voice_drift`. It returns the same reason strings: "unreadable verdict from the voice judge", "drift reported with no corrective", and the over-`MAX_NOTE` text. Otherwise `None`.

**Templates** (move text out of `voice_drift/system.j2`). Carried fragments, checked as in Task 5:

| Fragment of `voice_drift/system.j2` | Goes to |
|---|---|
| the opening paragraph ("You are checking one character's dialogue …"), whole | `question.j2` |
| the "Judge ONLY how the character sounds. …" paragraph, whole | `question.j2` |
| the "Where a correction is shown, it SUPERSEDES …" paragraph, whole | `question.j2` |
| the "Be conservative. …" paragraph, whole | `question.j2` |
| each bullet's text after "— " (drift, in_voice, not_enough) | `option.j2`, selected by `verdict` |
| "the corrective the model will be given on its next turn: one or two sentences, addressed to the writer, naming the specific way the voice slipped and what to do instead."; "Quote a short offending line if it helps."; "empty unless the verdict is `drift`." | `explain.j2` |

Excluded as format: "Reply with ONE JSON object and nothing else:", the `{"verdict":` example, `` `note` is ``, `` Leave `note` ``. `voice_drift/system.j2` stays until Task 8.

**Gate corpus** (`evals/gate/voice-drift.json`). The outcome is what `_stage_voice_drift` stores or reports: `["failed", <check_failure reason>]` when a check fails, otherwise `[verdict, note]`. Both sides map through the production `check_failure` (the call-site mapping, not a parser).
- Legacy side: `legacy.voice_drift_parse(text)`. Decide side: `finding_of(result)`.
- `legacy_cases`, verbatim from `test_voice_drift_store.py`'s parse tests (23 strings):
  - `{"verdict": "in_voice", "note": "a little terse"}`;
  - the prose-then-json-fence reply of `test_parse_output_tolerates_a_fenced_reply` (copy the literal from the test source);
  - `{"verdict": "<w>"}` for each of `in voice`, `in-voice`, `not enough`, `insufficient`, `unclear`, `  DRIFT  `;
  - `{"verdict": "<w>"}` for each of `none`, `ok`, `fine`, `n/a`, `yes`;
  - `I'm sorry, I can't do that.`, `{"note": "no verdict"}`, `{"verdict": null}`, `{"verdict": "maybe?"}`, `{"verdict": true}`;
  - `{"verdict": "drift", "note": null}`;
  - `{"verdict": "drift", "note": <x>}` for each of `{"tone": "terse"}`, `["terse", "clipped"]`, `42`, `true`.
- Each gets a decide twin of the same shape. Added shapes: `flattened` (`{"verdict": "drift", "rationale": "…"}`), `unwrapped-item`, `in  voice` with a double space (decide reads `in_voice`; record it as a win if legacy loses it), and `long-note` (over `MAX_NOTE`, `failed` both sides).

**Eval case** `decide-voice-drift` (`task="voice-drift"`, `schema`):
- **build:** Seraphine's anchor ("Clipped. Never uses contractions…") and a transcript where she speaks in loose, contracted chatter.
- **grade:**
  - `decide.json`;
  - `decide.answer` (`drift`);
  - `decide.rationale` (non-empty and ≤ `MAX_NOTE`);
  - `prompt.question`;
  - `prompt.options`: every option id and its `option.j2` render appear;
  - `prompt.context`;
  - `prompt.correction`: build with a correction and require it in the prompt;
  - `prompt.schema`.
- **Recordings:**
  - `compliant`;
  - `undecodable` → `("decide.json",)`;
  - `wrong` (`in_voice`) → `("decide.answer",)`;
  - `no-note` → `("decide.rationale",)`.

- [ ] **Step 1: Write the failing tests.**
  - `test_voice_drift_store.py`:
    - `test_build_item_keeps_the_user_message_and_offers_three_verdicts`
    - `test_the_two_synonyms_still_mean_not_enough`
    - `test_an_unreadable_answer_is_unknown_not_in_voice`
    - `test_check_failure_reasons_match_the_route`
  - The gate entry and the eval case.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Implement, with the `verify_templates` checks (both correction branches, all three options, the carried fragments and the coverage half) and the README entries.
- [ ] **Step 4:** Run `tests/test_voice_drift_store.py tests/test_decide_gate.py tests/test_evals.py tests/test_eval_graders.py tests/test_docs_guard.py`, `$PY evals/run.py --gate`, then `make check-lint check-mypy check-templates PY=$PY`. **The gate must pass before Task 8.**
- [ ] **Step 5:** Commit `feat(voice-drift): the decision item and its gate`.

---

### Task 8: Voice drift, switched

**Files:**
- Modify:
  - `routes/scenes.py` (`_stage_voice_drift` and the absorb fan-out that resolves it)
  - `store/routing.py`
  - `store/voice_drift.py` (delete `build_prompt`, `parse_output`, `_extract_object`, `_VERDICTS`)
  - `templates/voice_drift/system.j2` (delete)
  - `backend/tests/fixtures/llm/campaign_flow.json`
  - `scripts/verify_templates.py`
  - `templates/README.md`
- Tests:
  - `tests/test_routes.py`: the voice-drift cases. The `build_prompt` monkeypatches at ~7055 and ~15377 patch `build_item` instead.
  - `tests/test_voice_drift_store.py`: delete the `parse_output` tests; their inputs are `legacy_cases`.
  - `tests/test_llm_fakes.py`
  - `tests/test_routing.py`, `tests/test_operation_guard.py` (walk minimum 2)
  - Re-run Task 6's inline-fake grep for any new `complete`.

**Interfaces:**
- **`routing.py`:** `voice_drift` becomes `operation="decide", default_role="decision"`.
- **The absorb fan-out:**
  - `voice_resolved, voice_why = _soft_resolved(lambda: require_inference("voice-drift", cid, operation="decide"))`.
  - It passes `voice_resolved` to `_stage_voice_drift(cid, sid, transcript, client, resolved: UsableInference | None, budget, unroutable="")`.
- **Per NPC** (I2: today's meter/budget nesting, through `around`):
  - `decision = await operations.decide("voice-drift", [store.voice_drift.build_item(...)], client=client, resolved=resolved, explain=store.voice_drift.explain(), campaign=cid, scene=sid, around=lambda call, holder: budget.run(call, lambda: out.__setitem__("attempted", True), on_timeout=_noting(client, resolved.conn, holder)))`.
  - The overrun's `LLMError("timeout")` is raised inside `decide`'s meter, which files `error/timeout` and records the error; `_noting` reads the live holder's `llm.ATTEMPTED`, so a fallback overrun is noted against the fallback.
  - `finding = store.voice_drift.finding_of(decision.items[0])`.
  - `reason = store.voice_drift.check_failure(finding)`: when set, append `{"id", "reason"}` to `failed` and continue.
  - The rest (`stage_edit`, `checked`/`flagged`/`unjudged`, the budget bookkeeping) is unchanged.
- **Cassette (Minor 7):**
  - The voice-drift entry becomes `{"when": {"system_contains": <a phrase from decide/system.j2>, "user_contains": "Voice anchor:"}, "reply": decision_reply-shaped JSON with verdict "in_voice"}`, placed before any other entry keyed on the decide system phrase.
  - Fix its old `"consistent"` verdict, which never parsed.
- **`test_llm_fakes.py` (Minor 7):**
  - `_rendered_prompts()` returns `(system, user)` pairs. The generate prompts keep `user=""`. Each conversion `campaign_flow` drives adds its decide pair, built through `inference.structured_messages` (voice drift here).
  - `NOT_IN_CASSETTE = {"scene-break": "…", "response-selector": "…"}` names the decide conversions `campaign_flow` never drives, each with its reason (their route tests script replies); a test asserts every decide route task is either rendered or listed there.
  - The matcher test reads whichever of `system_contains` / `user_contains` an entry carries and requires each needle in the rendered text of its role. The coverage test requires, for every rendered pair, an entry whose every matcher matches that pair.

- [ ] **Step 1: Write and convert the failing tests.**
  - `test_voice_drift_runs_on_decide_and_meters_under_its_task`
  - `test_an_unreadable_decision_is_a_failed_check_not_a_clear` (a standing flag survives)
  - `test_voice_drift_on_a_decide_only_model_answers_on_the_fallback` (Review Focus 1)
  - `test_voice_drift_on_a_decide_only_model_without_a_fallback_fails_the_phase_not_the_absorb`
  - `test_a_voice_drift_overrun_on_the_fallback_is_filed_and_noted_against_it` (I2): a budget that expires while the fallback is answering files one `error/timeout` row, writes one ERROR log row with `module == "voice-drift"`, and `client.note_outcome` receives the fallback's conn.
  - `test_the_decision_role_now_serves_voice_drift`
  - Convert today's voice-drift tests in `test_routes.py` to `decision_reply` replies.
  - The cassette tests.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Implement, flip the route, and delete the legacy code.
- [ ] **Step 4:** Run:
  ```
  tests/test_routes.py -k "voice or drift or absorb" tests/test_voice_drift_store.py tests/test_review_detach.py \
  tests/test_tracker_absorb.py tests/test_llm_fakes.py tests/test_routing.py tests/test_routing_guard.py \
  tests/test_operation_guard.py tests/test_inference_equivalence.py tests/test_inference_equivalence_c.py \
  tests/test_inference_resolve.py tests/test_inference_settings.py tests/test_inference_cascade.py \
  tests/test_observability_routes.py tests/test_decide_gate.py tests/test_evals.py tests/test_docs_guard.py
  ```
  Then `make check-lint check-mypy check-templates PY=$PY`.
- [ ] **Step 5:** Commit `feat(voice-drift): judge through decide() on the Decision role`.

---

### Task 9: Speaker, prepared (no behaviour change)

**Files:**
- Modify: `store/response_protocol.py`
- Create:
  - `templates/scene/response_selector_question.j2`
  - `templates/scene/response_selector_context.j2`
  - `templates/scene/response_selector_grimoire.j2`
  - `evals/gate/speaker.json`
  - `evals/recordings/decide-speaker.{compliant,undecodable,off-roster,abstained}.json`
- Modify: `evals/gate.py`, `evals/cases.py`, `scripts/verify_templates.py` (a new section; the selector had none), `templates/README.md`
- Test: `tests/test_responses.py` (additions)

**Interfaces** (`store/response_protocol.py`, pure; it imports `grimoire.decisions` and `prompts`):
- `SELECTOR_QUESTION = "next"`, `GRIMOIRE_REF = "grimoire"`, and the two issue strings as constants: `INVALID_HANDOFF = "missing or invalid handoff"` and `INELIGIBLE = "ineligible or repeated speaker"` (the values `validate_handoff` and `_HANDOFF_ISSUES` use today).
- `selector_item(roster: list[dict], conversation: list[dict], note: str = "") -> decisions.Item`:
  - `context` renders `scene/response_selector_context.j2` (the observable transcript as `tojson`, plus the user direction when `note`);
  - one `Choice(SELECTOR_QUESTION, render("scene/response_selector_question.j2"), options=(*(Option(e["ref"], e["name"]) for e in roster), Option(GRIMOIRE_REF, render("scene/response_selector_grimoire.j2"))), allow_none=True)`.
- `selection_of(result: decisions.ItemResult) -> tuple[str | None, str | None]` keeps today's issue strings (I8):
  - an answer gives `(ref, None)`;
  - `None`/`abstained` gives `(None, None)`, which returns control to the player;
  - `None`/`unreadable` with `detail == "not_an_option"` gives `(None, INELIGIBLE)`, as an off-roster ref does today;
  - any other `None` gives `(None, INVALID_HANDOFF)`.

**Templates** (from `scene/response_selector.j2`). Carried fragments, checked as in Task 5:

| Fragment | Goes to |
|---|---|
| "Choose at most one initial speaker for the observable conversation below." | `response_selector_question.j2` |
| "Choose a listed NPC reference"; "or null to return control to the player." | question |
| "general scene information, an independent scene event or a genuinely new character's entrance" | `response_selector_grimoire.j2` |
| each sentence of lines 5–10 ("An established NPC's actions …" through "Never invent private knowledge or script reactions."), whole | question |
| "Observable transcript:"; "User direction:" | `response_selector_context.j2` |

Excluded: the "Return only a JSON object …" line (format), and "Available NPCs:" (the options replace the roster listing). `scene/response_selector.j2` stays until Task 10.

**Gate corpus** (`evals/gate/speaker.json`). The outcome is `[next, issue]`, the strings `_round_state` stores.
- Legacy side: `legacy.selector_parse(text, eligible)` → the frozen `json.loads` + frozen `validate_handoff(payload, eligible + ["grimoire"], [])`. Decide side: `selection_of(result)`.
- `legacy_cases`, verbatim from today's selector tests: `not json`, `{}`, `{"next":"pcs:seraphine"}`, `{"next":"absent"}` (`test_response_controls_routes.py:83`), and `{"next":null}`, `{"next":"characters:mara"}`, `{"next":"characters:winifred"}` (`test_character_turns.py`, `test_group_play_turns.py`).
- Each gets a decide twin of the same shape. Added shapes: `grimoire`, `fenced` and `prose-wrapped` (legacy `json.loads` fails, so decide beats it), `extra-key` (`{"next": …, "why": …}`: legacy rejects; decide reads `answers`), `flattened` (`{"next": "characters:mara"}` as the decide reply: read), `non-string` (`{"next": 5}`: `INELIGIBLE` both sides), `truncated`.

**Eval case** `decide-speaker` (`task="response-selector"`, `schema`):
- **build:** roster `[{ref: "characters:mara", name: "Mara"}, {ref: "characters:winifred", name: "Winifred"}]`, and a conversation where the player asks Winifred directly.
- **grade:**
  - `decide.json`;
  - `decide.answer` (`characters:winifred`);
  - `prompt.options`: every ref and name, and the grimoire description;
  - `prompt.question`, `prompt.context`, `prompt.schema`.
- **Recordings:**
  - `compliant`;
  - `undecodable` → `("decide.json",)`;
  - `off-roster` → `("decide.answer",)`;
  - `abstained` → `("decide.answer",)`.

- [ ] **Step 1: Write the failing tests.**
  - `test_selector_item_offers_the_roster_and_grimoire_and_allows_none`
  - `test_selection_of_null_returns_control_without_an_issue`
  - `test_selection_of_an_off_roster_answer_raises_the_ineligible_issue`
  - `test_selection_of_an_unreadable_answer_raises_the_invalid_handoff_issue`
  - The gate entry and the eval case.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Implement, with the `verify_templates` checks (note on and off, carried fragments, coverage) and the README entries.
- [ ] **Step 4:** Run `tests/test_responses.py tests/test_decide_gate.py tests/test_evals.py tests/test_eval_graders.py tests/test_docs_guard.py tests/test_import_guard.py`, `$PY evals/run.py --gate`, then `make check-lint check-mypy check-templates PY=$PY`. **The gate must pass before Task 10.**
- [ ] **Step 5:** Commit `feat(speaker): the decision item and its gate`.

---

### Task 10: Speaker, switched

**Files:**
- Modify:
  - `routes/character_turns.py` (`_selector_messages` → `_selector_item`, `_select`; `from .. import inference as operations`)
  - `store/routing.py`
  - `templates/scene/response_selector.j2` (delete)
  - `scripts/verify_templates.py` (drop the coverage half)
  - `backend/tests/test_regex_prompt_guard.py` (`PINNED` names `_selector_item`)
- Tests: every scripted **selector** reply (bare `{"next": …}` JSON, not inside a ```` ```handoff ```` fence) becomes `decision_reply({"next": ...})`. Find them with `grep -n "'{\"next\"" tests/*.py | grep -v handoff` and convert every selector hit (Minor 2). At plan time the hosts are:
  - `test_character_turns.py:60, 72, 409, 546`;
  - `test_group_play_turns.py:268, 281, 586` (one-line `FakeLLM([['{"next":…}'], …])`);
  - `test_response_controls_routes.py:83` (parametrized: `"not json"`, `"{}"`, `'{"next":"pcs:seraphine"}'`, `'{"next":"absent"}'`). Each becomes its decide twin (garbage stays garbage), and the test still asserts no actor call.
  - Not selector replies, and left alone: `test_prompt_log_store.py:243, 543` (prompt-log index bytes) and `test_stream_watchers_incremental.py:207` (watcher input).
  - Also: `test_regex_prompt.py:141` calls `_selector_messages` and becomes `_selector_item` (reading the item's context); `test_character_turns.py:555` reads the selector prompt's phrase from the decide **user** message.

  Handoff fences stay as they are; `validate_handoff` still serves them.
  - `tests/test_routing.py`, `tests/test_operation_guard.py` (walk minimum 3). Re-run Task 6's inline-fake grep.

**Interfaces:**
- **`routing.py`:** `speaker` becomes `operation="decide", default_role="decision"`.
- **`_selector_item(cid, sid, round_record) -> decisions.Item`:** today's `_selector_messages` body up to the `public` list, through `regex.view.view(..., phase="prompt")` (still pinned), then `store.response_protocol.selector_item(round_record["eligible"], public, round_record.get("note", ""))`.
- **`_select`:**
  - When there are at most 1 eligible, it returns early as today.
  - `resolved = await run_in_threadpool(lambda: require_inference("response-selector", cid, operation="decide"))`.
  - `item = await run_in_threadpool(_selector_item, cid, sid, round_record)`.
  - `decision = await operations.decide("response-selector", [item], client=client, resolved=resolved, campaign=cid, scene=sid, post=round_record["post"], round_id=round_record["id"], capture=lambda msgs: run_in_threadpool(_capture, cid, sid, "response-selector", msgs, resolved.conn))`.
  - `except decisions.DecideRequestError` (a roster whose refs collide after normalising, Minor 15): log a warning naming the campaign and scene, and return `(None, INVALID_HANDOFF)`, so the round raises an issue instead of a 500 mid-turn.
  - `return store.response_protocol.selection_of(decision.items[0])`.
  - `LLMError` propagates as today; the meter inside `decide` files it.
  - The decide prompt renders on the event loop inside `decide` where `_selector_messages` rendered in the threadpool. A short Jinja render; accepted and said in the commit (Minor 10).

- [ ] **Step 1: Convert the selector replies and add the failing tests.**
  - `test_the_selector_meters_one_row_with_its_round`: the `post` and `round_id` survive.
  - `test_the_selector_capture_is_the_decide_prompt`
  - `test_the_speaker_on_a_decide_only_model_answers_on_the_fallback` (Review Focus 1)
  - `test_the_decision_role_now_serves_the_speaker`
  - `test_a_fenced_selector_reply_now_reads` (a gate "beats" case, end to end)
  - `test_an_off_roster_selection_keeps_the_ineligible_issue`
  - `test_a_colliding_roster_raises_an_issue_not_a_500`
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Implement, flip, and delete the legacy template.
- [ ] **Step 4:** Run:
  ```
  tests/test_character_turns.py tests/test_group_play_turns.py tests/test_response_controls_routes.py \
  tests/test_authors_note_turns.py tests/test_hide_from_context.py tests/test_regex_prompt.py \
  tests/test_regex_prompt_guard.py tests/test_prompt_log_store.py tests/test_responses.py tests/test_routing.py \
  tests/test_routing_guard.py tests/test_operation_guard.py tests/test_inference_equivalence.py \
  tests/test_inference_equivalence_c.py tests/test_inference_resolve.py tests/test_inference_settings.py \
  tests/test_inference_cascade.py tests/test_decide_gate.py tests/test_evals.py tests/test_docs_guard.py
  ```
  Then `make check-lint check-mypy check-templates PY=$PY`.
- [ ] **Step 5:** Commit `feat(speaker): pick the next speaker through decide() on the Decision role`.

---

### Task 11: Docs and the gates

**Files:** `CLAUDE.md`, `templates/README.md`, `evals/README.md`, and `CONTRIBUTING.md` (the `test_operation_guard.py` row ends as the combined text under coordination).

**`CLAUDE.md`, "Adding an LLM call site?":** one paragraph saying:
- a yes/no, pick-one or graded question is a `decide` call. Resolve with `operation="decide"` on a decide route, and call `operations.decide(<task>, items, client=…, resolved=…)` with `inference` bound as `operations`;
- `decide` meters each call itself, and a caller's budget goes through `around` so it runs inside the meter; `test_operation_guard.py` holds the task literal (by import binding) and the safety rule;
- prose is never a decision. Scene-break's title is a separate `generate` on `summary`;
- the structured mode is per attempt (`STRUCTURED_KEY`, decide resolutions only), the schema is always in the prompt, and the mode is filed per call on a copied account block;
- a decide-only Decision model is skipped for a generating role fallback until slice H;
- a conversion lands only behind `evals/run.py --gate`, whose corpus starts from the legacy parser tests.

**`evals/README.md`:** the three `decide-*` rows in the case table, the "decide gate" section from Task 4, and what `--live` measures for a decide case (never run without the user's approval).

- [ ] **Step 1:** Write the docs. Run `tests/test_docs_guard.py tests/test_evals.py tests/test_decide_gate.py`, then `make check-lint check-mypy check-templates PY=$PY`. **Not** `make check`: CI runs the full suite.
- [ ] **Step 2:** Commit `docs: decide() call sites, the gate, and the Decision role in use`.
- [ ] **Step 3: Gates (CLAUDE.md).**
  1. `/codex:review` of the branch diff.
  2. `/codex:adversarial-review` against the diff and this spec: does F implement §14 row F, the safety rule, §5.5's skip, §9.3/§9.4 as amended, and §14.1?
  3. Address every finding, or record why not, before moving on.

  If Codex is unavailable, stop and report; the controller asks the user before any substitute runs. No stand-in is pre-approved here.
- [ ] **Step 4:** Push, open the PR, and **wait for CI green** (its `make check` is the §14 gate) before merging, with rebase and `--ff-only`.

---

## Parallelism and coordination

| Wave | Tasks | Notes |
|---|---|---|
| 0 | 0 | Docs only |
| 1 | 1, 2, 4 | In parallel with each other and with D and E. 1 is a new leaf; 2 touches the facade, adapters, `resolve.py`, `capabilities.py` and `llm_fakes`; 4 touches only `evals/` and a new test. They share no file. |
| 2 | 3 | Needs 1, 2 **and E's Task 2** (I6). Touches `resolve.py`/`resolved.py`/`settings.py` (after 2 and after E), `routes/common.py`, `llm_fakes` and the two guard files. |
| 3 | 5, 7, 9 | Each needs 3 and 4. They touch different store modules, but all three append to `evals/gate.py`, `evals/cases.py`, `scripts/verify_templates.py` and `templates/README.md`. Run them in parallel worktrees and merge those four files by hand, or run them in sequence. |
| 4 | 6, then 8, then 10 | **In sequence** (Minor 11). 6 and 8 share `routes/scenes.py`; all three rewrite `test_routing.py`'s expected map and bump `test_operation_guard.py`'s walk minimum, and 6 deletes the vacuous pin. Each switch also needs its own prepare task. |
| 5 | 11 | Last |

**The order with D and E (I6):**

1. D owns `usage.record(operation=)`, first after `images`, passed through by `Meter.done`. If E lands first, E adds it exactly as D specifies and D drops it.
2. E's Task 2 stamps the account block: `operation` (and `role`) at the tail of `resolve()` before the fallback attach, and `billing` in `_lowered`. (The embed block in `resolve.embedding()` and the `account` line in `embed._stamp` are added by whichever of D and E lands second.) `llm._stamp` copies the block into the holder with `llm_usage.account`, and `record` files `decision_mode` from it.
3. **F's Task 3 lands after E's Task 2.** F stamps `decision_mode` per facade call on a **copied** block (`llm_usage.with_account`), on the primary's dict and on its `FALLBACK_KEY` dict. No account block is mutated in place; E's `test_account_blocks_are_never_mutated_in_place` pins it, and F's `test_the_mode_is_stamped_per_call_on_copied_blocks` checks the decide path.

**If F is implemented first** (F's Task 3 must start before E's Task 2 has landed), F adds exactly these, each verbatim from E's Task 2 Interfaces:
- in `llm_usage.py`: `ACCOUNT_KEY`, `ACCOUNT_FIELDS`, `account()` and `with_account()`;
- `resolve.ACCOUNT_KEY` restated, and `test_account_key_is_one_spelling`;
- `llm._stamp`'s `llm_usage.account(usage, conn)` line, and the same line in `FakeLLM.stream`'s stamp;
- in `store/usage.py`: `record(..., operation: str = "")` exactly as D specifies (if D has not landed), then `decision_mode: str = ""`, each written only when non-empty, and `Meter.done`'s guarded reads of both;
- at the tail of `resolve()`, before the fallback attach, a fresh block carrying `operation` only on each attempt (no `role`, no `billing`).

E's Task 2 then drops those pieces from its diff and keeps the rest: `role`, `billing` in `_lowered`, `provider_id`, `preset`, `requested_model`, `tokens_estimated`, the probe meters and its own tests. D drops `record(operation=)` if F added it.

**Files that two or more of D, E and F touch:**

| File | D | E | F | Merge rule |
|---|---|---|---|---|
| `store/inference/resolve.py` | `embedding()`, `_embed_endpoint`, a `ValueError` at the top of `resolve()` | account block in `_lowered`, and at the tail of `resolve()` before the fallback attach | at the tail, after E's stamp: `STRUCTURED_KEY`, `decision_mode`, the decide skip; `skip_text` | Keep all; F's tail code runs after E's block stamp. |
| `store/inference/resolved.py` | `space_id` | — | `Attempt.decision_mode`; `skipped`; the `conn` and `decision_mode` properties | Separate fields. |
| `store/inference/settings.py` | `used_by`'s Embedding line | rewrites `used_by` over `in_use.selections()` | `_problem` (skip text) | Different functions. |
| `store/inference/capabilities.py` | — | — | `_STRUCTURED_PARAMS` | F only. |
| `llm.py` | — | `_stamp`, `_dispatch`/`_lowered` (one line each), `_resilient`, `LLMClient(count_tokens=)` | `stream`, `complete`, `_dispatch`, `_lowered`, `_provider`, `STRUCTURED_KEY`, `_structured_share`, `_preset_refusal` | Conflicts in `_dispatch`/`_lowered` are mechanical: keep E's lines and F's `schema` pass-through. |
| `llm_usage.py` | uses `account` in `embed._stamp` | creates the names | uses `with_account` in `inference._with_mode` | E's. |
| `backend/tests/llm_fakes.py` | `FakeEmbeddings.embed` | `FakeLLM.stream`'s stamp; `ScriptedProvider(usage=)` | `FakeLLM.stream/complete/single` signatures, `self.schemas`, `StallingGateway.complete`, `decision_reply` | Keep E's stamp inside F's new `stream` signature. |
| `store/usage.py` | `record(operation=)` | its ledger block after `operation`; `Meter.done` reads | untouched (unless F first, above) | — |
| `backend/tests/test_usage_guard.py` | appends `test_every_embeddings_request_is_metered` | not touched | `EXTRA_SOURCES` in the scan loops | Disjoint hunks. |
| `backend/tests/test_operation_guard.py` | creates it: embed half and the binding walker | — | generalises the walker to a target module; appends the decide half | The second to land rebases onto the first. |
| `store/routing.py`, `tests/test_routing.py` | `EMBED_TASKS` | — | three flips; `scene-break-title` on `summary` | Disjoint lines. |
| `backend/tests/test_routing_guard.py` | `_unclassified` | — | none | — |
| `routes/common.py` | — | — | `_soft_resolved`, `UsableInference.conn` | F only. |
| `CLAUDE.md` | "Adding an LLM call site?" (embed) | Costs | "Adding an LLM call site?" (decide) | Separate sentences, merged by hand. |
| `CONTRIBUTING.md` | the canonical `test_operation_guard.py` row, and the `test_usage_guard.py` row amended | — | appends `; every decide names a task on a decide route` to D's row's middle cell; no row of its own | If F lands first, F writes D's canonical row text plus that suffix, so D changes nothing in it. Final text: "`test_operation_guard.py` \| every embed names a registered embed task, and only the operation and the model test reach the embeddings client; every decide names a task on a decide route \| —". |
| the spec | §5.4, §6.4, §7.1, §7.3, §9.3/§9.4, §14 D, §14.1 | §9, §14 E | §5.3–§5.5, §6.2, §7.1 (only if D's has not landed), §7.2, §7.4, §9.3, §9.4, §14 F/H/I, §14.1 | Different paragraphs; §7.1's text is identical in both. |
| `backend/tests/test_inference_resolve.py` | — | ~277 excludes `ACCOUNT_KEY` | must stay green: a generate resolution carries no `STRUCTURED_KEY` | — |

There is no add/add on `grimoire/inference.py`: D's embed lives in `store/inference/embed.py` (Open (a)).

## How G and H extend this without reshaping it

- **G (continuity on `decide()`)** adds only:
  - item builders in `store/continuity/`, one item per row or candidate, with per-item option lists;
  - its two gate corpora (from the legacy continuity parser tests) and eval cases -- which need the gate's one-item limit lifted, or per-entry items (below);
  - the switch, passing its `budget.run` through `around` (continuity-identity has today's meter/budget shape, I2);
  - the `continuity` route's flip.

  **One gap G must close first: the gate judges one item per conversion.** `gate.judge` raises `ValueError` for a conversion whose builder makes more than one item, because every F call site sends one. G's legacy parsers (`continuity/identity.parse_output`, `reconcile.parse_output`) answer whole batches while its decide call sites send one item per row or candidate, so G either extends `judge` to score a multi-item conversion (each entry's decide reply parsed against several items, the outcome the batch's) or writes its corpora as per-entry items (one row or candidate per entry, each scored alone). `evals/README.md` says the same.

  Batching, chunking (`MAX_ITEMS_PER_CALL`), one metered row per chunk, `reason: "error"` for a failed chunk beside answered ones, and per-item options are all in F's contract already. The decide guard matches bindings, so `exam.decide(...)` beside G's call site is never mistaken for an operation (I1). `test_operation_guard.py`'s safety check applies to `continuity` the moment it flips.
- **H (native decisions)** adds:
  - the native adapters, which normalise into `decisions.Answer` with `probability`/`distribution`/`reason: "refused"`. Those fields already exist; they are unset in F;
  - `resolve.decision_mode()` returning `"native"` where `decide_native` is `yes`;
  - a `"native"` entry in `inference._BACKENDS`, which stamps `decision_mode: "native"` on its own copied block, and the §5.5 chain (native → structured on the same selection, a second call that stamps `"structured"` → the role fallback) inside `decide`;
  - the replacement of F's decide skip: a decide-only primary is then served natively, and `OPERATION_CAPABILITY["decide"]` widens to "decide_native or generate";
  - §9.4's mode, normalised answers and distributions in the capture;
  - its probe;
  - `--live` evals, run only with the user's approval.

  The `decide(...)` signature and every call site stay as F leaves them. One call-site branch is H's, by ruling 12: voice drift's native "verdict without a note".
- **I** retires `STRUCTURED_KEY` together with the lowering and `FALLBACK_KEY`, and adds `inference.generate` (ruling 14).

## Rulings (Task 0 folds each into the spec)

1. **`decide` takes a resolution, not a `cid`.** The signature is `decide(task, items, *, client, resolved, explain, campaign, scene, post, round_id, capture, around)`.
   - Call sites resolve with `require_inference(task, cid, operation="decide")`. That keeps scene-break's 409 before its due check, absorb's soft resolution (a phase that cannot resolve reports itself failed), and the speaker's threadpool hop.
   - The client is passed because the app-scoped client is the seam the fakes replace.
   - The task stays a literal for the guard, and `decide` refuses a resolution for another task or another operation.
2. **`explain` is the rationale *instruction* (a string), not a bool.** Scene-break's rationale is one sentence of reason; voice drift's is a corrective addressed to the writer. "Empty" means no rationale is requested.
3. **The schema is always in the prompt.** Native structured mode is added for each attempt whose `structured_output` is `yes`. §7.2 said the schema is appended only otherwise. With it always present, there is one prompt per decision: one capture, one prompt for the evals to score, and no per-attempt prompt variant for a fallback that lacks the mode.
4. **Anthropic's structured mode is `output_config.format` with `json_schema`, not a forced tool** (amended, I4). §6.1's row already names it; the review found current models that answer a forced `tool_choice` with a 400. Schemas use only the intersection of OpenAI strict mode's and Anthropic's documented subsets: no numeric bounds (a score is an integer `enum`), and a nullable choice is `anyOf` with a `{"type": "null"}` branch, never a type array. Structured output is incompatible with prefill, which decide prompts never use. Each adapter re-checks Appendix B's reference before coding.
5. **An explicit none on an `allow_none` choice is `answer: None, reason: "abstained"`.** A `null` on a choice without `allow_none` is `unreadable`. This keeps §7.4's closed reason vocabulary; `detail` is a sub-reason, not a new reason.
6. **Options may carry `aliases`, which the structured parser accepts, after one generic spelling normalisation.** Voice drift's two legacy synonyms survive that way, so the gate can hold on them honestly. `validate` rejects any id or alias that collides with another after normalising. Native backends ignore aliases.
7. **The gate compares parsers on a recorded corpus taken from today's parser tests,** scored by the full call-site outcome. Each entry pairs a legacy reply with a decide reply of the same shape. The permanent `decide-*` eval cases hold the prompt contract, and `verify_templates.py` holds that no criterion of the old prompt was dropped.
   - "Equals or beats" means "never loses an entry". Today's parsers are frozen verbatim in `evals/legacy.py` (the selector's `validate_handoff` included) so the gate stays runnable after the switch.
   - The legacy parse tests are deleted only after `test_every_legacy_parse_case_is_a_gate_entry` holds their inputs.
8. **Provider errors propagate as `LLMError` when no chunk answered.** `reason: "error"` marks only a failed chunk's items when another chunk answered.
9. **One meter per chunk, opened inside `decide`,** with a caller's budget inside it through `around`. `test_usage_guard.py` scans `inference.py`. Operation reaches the ledger through E's account block and D's `record(operation=)`; decision mode through the block too, stamped per call on a copy. F adds no Meter parameter and writes nothing into the holder.
10. **Decide capture (§9.4) is split** (overturned from "partly deferred"). F owns the speaker's request capture, which now records the decide prompt with its contexts and questions. Scene-break and voice drift captured nothing before and capture nothing now. H owns mode, normalised answers and distributions.
11. **The structured-capability flag rides the lowered conn** as `STRUCTURED_KEY`, present only on a decide resolution's `yes` attempts. The facade decides per attempt from the dict it already receives, so the fallback is covered without the facade importing the store.
12. **Voice drift on a native backend with no rationale** (§7.4: "the verdict is shown without a note") is H's. F keeps "drift needs a corrective" for the structured backend, which always has a rationale field.
13. **The scene-break title.**
    - `scene-break-title` is registered on `summary` when the switch lands (Task 6), not in the prepare task: the routing guard requires every claimed task to be named at a call site.
    - The verdict commits first; the title is drafted only for a YES that landed and written by a second guarded write. A title that fails (resolution or provider) leaves the verdict with an empty title.
    - The `summary` route's label becomes "Summaries & scene titles".
14. **No separate `inference.generate` wrapper.** `LLMClient.complete(..., schema=)` *is* §7.2's `generate(schema=)` until slice I's adapter registry, which adds `inference.generate`. Its only F caller is `decide`.
15. **Module placement.** The contract is in the gateway leaf `grimoire/decisions.py`, so H's adapters (gateway, #239) can normalise into it. The operation is in `grimoire/inference.py`, as spec §7.1 names it; D's embed is store-side and disjoint.
16. **`MAX_ITEMS_PER_CALL = 8`, justified structurally.** Each item carries its own transcript context, so one call's prompt grows linearly with the batch, and one unreadable reply loses every item in its chunk. Eight bounds both. It is to be tuned against real prompts later, never against a measured library. No F caller sends more than one item.

## Plan review rulings

Answers to `.superpowers/sdd/plan-reviews/slice-f-plan-review.md`, one line each.

- **I1** applied: the decide guard resolves import bindings (D's alias resolution, generalised to a target module) and never matches by name alone; `exam.decide` and `self.decide` are planted true negatives, five planted true positives (Task 3).
- **I2** applied: `decide` takes `around`, so the budget runs inside its meter and `_noting` reads the live holder; Task 8 tests the fallback overrun.
- **I3** applied: `_preset_refusal` subtracts the structured envelope's spellings, tested both ways (Task 2). The structured-degrade sibling is declined: with I3 fixed the fallback answers from the in-prompt schema, and a third route kind in `_resilient` is outside F's scope (H's §5.5 chain owns same-selection retries).
- **I4** applied: a nullable choice is `anyOf` with null; schemas stay within the shared subset (Task 1, ruling 4).
- **I5** applied as the review's option (b), which follows §5.3/§5.5: a decide-only primary is skipped for a generating role fallback; with none, §5.3's 409 stands. H replaces the skip with the native backend and widens `OPERATION_CAPABILITY` (Task 3, Review Focus 1).
- **I6** applied: F's Task 3 lands after E's Task 2; the row test is unconditional and F's; the "F first" list says what F adds and E drops; the shared-file table is corrected.
- **I7** applied: `Attempt.decision_mode` is the capability answer; the backend stamps the mode per call on copied blocks (`_with_mode`).
- **I8** applied: corpora start from the legacy parser tests (enumerated), outcomes are full call-site values, `validate_handoff` is frozen in `legacy.py`, flattened shapes are read, the speaker keeps both issue strings, and `verify_templates.py` checks carried fragments and coverage.
- **I9** applied: `Case.task`/`Case.schema` make `--live` resolve the Decision role with `schema=`; the README says what each mode measures; nothing runs it.
- **I10** applied per controller ruling 10: F owns the speaker's request capture, H owns mode, answers and distributions; Task 0 writes it into §9.4 and row H.
- **Minor 1** applied: Task 1 Step 4 runs three separate commands.
- **Minor 2** applied: the grep and the verified host list (Task 10), with the non-selector hits named.
- **Minor 3** applied: `FakeLLM.complete` records the schema without forwarding it to `stream`; every inline fake's `complete` gains `schema=None` in Task 6, re-checked in 8 and 10.
- **Minor 4** applied: `STRUCTURED_KEY` only on decide resolutions; generate dicts stay byte-identical.
- **Minor 5** applied: `_STRUCTURED_PARAMS` is `structured_outputs` only, and §6.2 is amended.
- **Minor 6** applied: verdict first, title only for a landed YES, a second guarded write; the title's cleaning, source model and cost are recorded in the spec and the commit.
- **Minor 7** applied: `(system, user)` pairs per conversion, with `NOT_IN_CASSETTE` naming the two `campaign_flow` never drives.
- **Minor 8** applied: Tasks 8 and 10 run the settings, resolve and cascade suites.
- **Minor 9** applied: routes bind `from .. import inference as operations`.
- **Minor 10** applied: the render moving onto the loop is accepted and said in Task 10's commit.
- **Minor 11** applied: Tasks 6, 8 and 10 run in sequence.
- **Minor 12** applied: `inference.generate` and `STRUCTURED_KEY`'s retirement are named in row I.
- **Minor 13** applied: `--gate` refuses `--live`, `--record` and `--case`.
- **Minor 14** applied: the schema compile cache is noted in §7.2, to be tuned later against real prompts.
- **Minor 15** applied: `validate` rejects normalised id collisions, and `_select` maps `DecideRequestError` to the invalid-handoff issue; §7.1 is Open (a).
- **Ruling 4** amended as the controller ruled: `output_config.format`, nullable via `anyOf`.
- **Ruling 10** overturned as the controller ruled (I10).
- **Open (a):** D's embed stays in `store/inference/embed.py`; F's `decide` is in top-level `inference.py`; §7.1's text is identical in D's and F's Task 0, and F's Task 0 writes it only if D's has not landed.
- **Open (b):** answered by ruling 10.
- **Open (c):** aliases kept, with collision validation after normalising (ruling 6).
- **Task 11:** the Codex gates stay; if Codex is unavailable the controller asks the user, and no stand-in is pre-approved.
- **No live runs:** `evals/run.py --live` and `--record` are never run without the user's explicit approval (Global Constraints, Task 4).
- **Test runs:** each task runs only its named files plus the lint, mypy and templates checks, never `make check` or the full suite; merging waits for CI green.

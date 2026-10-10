# 01g-S3: Run attribution on the ledger and the capture — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a ledger row can say which caller's run it belongs to and which
turn of that run it was. `store.usage.record`, `Meter` and `meter` gain
`run_id`, `loop_turn` and `tool_calls`, each written only when set, so a row
without them is byte-for-byte today's. `inference.decide` and `run_stages`
gain `run_id=`, `loop_turn=` and `response_id=` and hand them to every meter
they open: each structured chunk, each prompt-only re-send, each native item
and each fallback stage. The incoming-response capture (`llm_capture`)
carries `run_id` in its meta whenever the holder belongs to a run, on every
attempt of the call, retries included. Delivers 01g-C3 (part). No caller
passes any of it yet. 01g-S4 (the loop), 01g-S7 (the decide tool), 01h-S5 and
01h-S6 (embeds under a run) and 02 (`decide(response_id=)`) are the first.

**Architecture:** the run id lives on the **meter**. The holder only gets a
copy, for the capture. `Meter(run_id=...)` keeps `run_id`, `loop_turn` and
`tool_calls` as attributes and files them from there. It also seeds its own
holder with `usage[RUN_KEY] = run_id`. `llm._stamp` keeps that key across its
per-attempt `usage.clear()`, as it keeps the reasoning buffer
(`llm.py:598-602`), and each `llm_capture.Capture` reads it into its `meta`.
So any facade call made under a run id's meter gets the capture attribution
with no second step at the call site: decide's stages and S4's turns. Embeds
get only the ledger field: `embed._capture` writes its own `logs.record`
from its arguments and never reads the holder, so 01h-S5/S6 pass `run_id` to
`_capture` themselves if they want it on that line. A seeded holder is no
longer empty before anything is sent, so the one test "was a request sent?"
becomes `store.usage.sent(holder)`: any key outside `PRE_SEND_KEYS`
(`RUN_KEY` and the reasoning buffer's key, restated; `tool_calls.KEY` joins
the tuple when 01g-S1 lands). `Meter.done` asks it, and
so do the two `inference.py` sites that ask today with `bool(m.usage)`
(`inference.py:398` `_once`, `inference.py:647` `_native`'s capture). Inside
`inference.py`, decide's two meter openings
(`inference.py:379-380`, `620-621`) become one helper, `_meter(call)`, so a
third meter cannot forget the new keywords.

**Tech Stack:** Python 3.11, pytest, `httpx.MockTransport`, the shared
fakes (`FakeLLM`, `SequencedProvider`, `LLMClient` over a real adapter).

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01g-tool-calling-design.md`
§3.10 ("Metering, capture and the trace": the `run_id`, `loop_turn` and
`tool_calls` bullets, and the incoming-response capture bullet), §3.12
(`decide` gains `run_id=`, `loop_turn=`, `response_id=`), §4 (C3), Slices
(01g-S3). Cross-checked against
`docs/superpowers/specs/2026-10-09-roadmap-01h-embedding-options-design.md`
§6.2, §7 and Slices 01h-S5/S6 (their `run_id` is "filed on every row in 01g-C3's
field": they pass `run_id=` to `usage.meter(...)` in `embed_sync` /
`embed`). Also against
`docs/superpowers/specs/2026-10-09-roadmap-02-decision-integration-design.md`
§3.5 (`inference.decide`, `run_stages` and `_Call` gain `response_id: str = ""`,
a shared structure in `ROADMAP-CHECKLIST.md`; `responses.mint_id()` stays 02's).

## Global Constraints

- **A row with none of the new fields set is exactly today's row.** The
  fields are omitted rather than written empty, zero or null, like
  `round_id`. Every existing ledger, rollup and golden test passes untouched:
  `test_usage_*.py`, `test_decide_chain_golden.py`, `test_decision_capture.py`,
  and the frozen-campaign sweep.
- **`usage_rollup.VERSION` stays 6.** No rollup and no per-turn projection
  reads the new fields. `_add` sums only known keys, and `_turn`
  (`usage.py:1215`) projects a fixed set, so nothing reaches the frontend and
  no API type changes.
- **`record` never raises** (its contract, `usage.py:287-292`). The new
  values are type-tested, never coerced, the way `post` is
  (`usage.py:386-387`). A `run_id` that is not a non-empty `str`, or a
  `loop_turn` / `tool_calls` that is not an `int` (and not a `bool`) of at
  least 1, costs that field, never the row.
- **Unsent stays unfiled.** A meter whose holder holds only `RUN_KEY` files
  no row: the no-row rule of `Meter.done` (`usage.py:580`) still holds.
  But a failure is still recorded in the error store, as today (`missing_key`).
  A decide call refused unsent is still captured with `messages=[]`.
- **The store does not import the gateway** (#239). `RUN_KEY` is restated in
  `store.usage` as `ESTIMATE_KEY` and `ENDED_KEY` are (`usage.py:157-166`),
  and a test holds the two spellings equal (`test_usage_estimate.py:602`'s
  pattern).
- **No call site passes the new keywords.** No route, eval or script
  changes. `decide` and `run_stages` take them keyword-only with defaults, so
  `evals/runner.py:547` and the three `run_stages` tests are unaffected.
- **Privacy / observability:** the run id is an opaque id (a detached run's
  `uuid4().hex`), so ledger rows and Debug capture rows may carry it
  (CLAUDE.md, "Observability": logs may carry ids). Nothing else is added to
  a log line, and no handler changes.
- Imports stay at module scope. `store/usage.py` imports nothing new.
  `llm.py` already imports `llm_capture`.

## Review Focus

- **The seeded holder.** Every place that treated "the holder is non-empty"
  as "a request went out" must now ask `usage.sent`. The inventory, by grep
  over `backend/src/grimoire` for `bool(m.usage)`, `if m.usage`,
  `not self.usage` and holder iteration, is three sites: `Meter.done`,
  `inference._once` and `inference._native`. `embed.record_failure`'s
  `meter.usage.clear()` (`store/inference/embed.py:131`) already empties a
  seeded holder, which is correct. Tasks 1 and 3 prove each site with a
  holder that carries a run id and sent nothing.
- **`_stamp` preserves `RUN_KEY` and nothing else new.** Every other key is
  still cleared per attempt.
- **Every meter decide opens carries the keywords.** That covers the
  structured chunk, a re-send after a schema refusal, the native item and
  the fallback stage. `_meter(call)` is the only `store.usage.meter(` spelled
  in `inference.py` after this slice.
- **`tool_calls` is a meter attribute that can be set after construction.**
  S4 learns the count only after the facade returns, inside the meter. It
  sets `m.tool_calls = n` before the meter exits. `done` reads the attribute
  when it files.
- The capture's `meta` gains `run_id` only when one is set, so the existing
  capture tests that read `meta` keys see no new key.

---

### Task 1: the ledger fields and the "sent" test

**Files:**
- Modify: `backend/src/grimoire/store/usage.py`:
  - `RUN_KEY` and `REASONING_KEY` (restated `llm_reasoning.KEY`) beside
    `ENDED_KEY`, and `PRE_SEND_KEYS = (RUN_KEY, REASONING_KEY)`;
  - `sent(holder)`;
  - `_run_fields(run_id, loop_turn, tool_calls) -> dict`, beside `_estimated`;
  - `record(..., run_id: str = "", loop_turn: int | None = None,
    tool_calls: int = 0)`, with its docstring;
  - `Meter.__init__(..., run_id="", loop_turn=None, tool_calls=0)`, which
    seeds `self.usage[RUN_KEY]` when `run_id` is a non-empty `str`;
  - `Meter.done`: `if not sent(self.usage)`, and the three fields passed to
    `record`;
  - `meter(...)`: the same three keywords, and its docstring.
- Test: `backend/tests/test_usage_fields.py`

**Interfaces:**
- Produces:
  - `usage.RUN_KEY = "_run_id"`;
  - `usage.sent(holder: dict) -> bool`, which answers whether the facade
    stamped the holder: any key outside `PRE_SEND_KEYS`. It is guarded, so an
    unreadable holder counts as sent, the same as today's truthiness;
  - `record(run_id=, loop_turn=, tool_calls=)`;
  - `Meter.run_id`, `Meter.loop_turn` and `Meter.tool_calls`, the last of
    which a caller may set;
  - `meter(run_id=, loop_turn=, tool_calls=)`.
- `record` is at exactly C901 = 10, so the fields enter through one
  unconditional `row.update(_run_fields(...))` with no new branch.

- [ ] **Step 1: Failing tests** (`test_usage_fields.py`, `home` fixture):
  - `test_record_writes_the_run_fields_only_when_set`. A row written without
    them has none of `run_id`, `loop_turn` or `tool_calls`, in the returned
    row and on disk. A row written with `run_id="run-1"`, `loop_turn=2` and
    `tool_calls=3` round-trips through `usage.calls(days=1)` with those
    values.
  - `test_run_fields_are_type_tested_not_coerced`, parametrised. Each of these
    is dropped while the row is still written: `run_id=""`, a `run_id` of
    `object()`, `loop_turn=0`, `loop_turn=True`, `loop_turn="2"`,
    `tool_calls=0`, `tool_calls=-1`, `tool_calls=False`.
  - `test_an_older_row_reads_unchanged`. A ledger line written by hand with
    no run fields reads back from `usage.calls` as the same dict. Two rows
    alike except for the run fields give equal `usage.summary(...)["totals"]`
    and equal `_turn` projections, the projection having no run key.
  - `test_a_meter_files_its_run_fields`. A `usage.meter("chat",
    run_id="run-1", loop_turn=1)` whose holder is stamped (`model`,
    `attempts`) has `m.tool_calls = 2` set inside the `with`. Its row
    carries all three fields. The holder carried `RUN_KEY` from
    construction.
  - `test_a_run_meter_that_sent_nothing_files_no_row`. A
    `Meter("chat", run_id="run-1")` is exited cleanly, and another is exited
    by an `LLMError("missing_key", ...)`. Neither files a ledger row. The
    second still files its error-store record (`store.errors`), as an
    unseeded one does.
  - `test_sent_ignores_only_the_pre_send_keys`. `sent({})`,
    `sent({RUN_KEY: "x"})` and `sent({RUN_KEY: "x", "_reasoning_display":
    Buffer()})` are False. `sent({RUN_KEY: "x", "model": "m"})` is True.
  - `test_meter_takes_tool_calls_as_a_keyword` and
    `test_a_non_string_run_id_seeds_nothing` (`Meter(run_id=object())`
    writes no row key and no holder key).
  - In `test_usage_estimate.py`, beside line 602:
    `assert usage.RUN_KEY == llm_capture.RUN_KEY == "_run_id"` and
    `usage.REASONING_KEY == llm_reasoning.KEY`, in
    `test_estimate_keys_are_one_spelling`. Added in Task 2's Step 1, since
    `llm_capture.RUN_KEY` arrives there.
- [ ] **Step 2: Run** `cd /home/user/wt/01g-s3/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_usage_fields.py` → fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same file, plus `tests/test_usage_store.py tests/test_usage_estimate.py tests/test_usage_pricing.py tests/test_usage_rollup_store.py tests/test_usage_routes.py tests/test_usage_prefilter.py` → PASS.

### Task 2: the run id on the incoming-response capture

**Files:**
- Modify: `backend/src/grimoire/llm_capture.py`:
  - `RUN_KEY = "_run_id"`;
  - `run_id(usage) -> str`, which returns the holder's run id or `""`;
  - `Capture.__init__(..., run_id: str = "")`, which adds `meta["run_id"]`
    only when the run id is set;
  - the module docstring.
- Modify: `backend/src/grimoire/llm.py`:
  - `_stamp` (`llm.py:562-609`) keeps `RUN_KEY` across `usage.clear()`, the
    same way the reasoning buffer is kept, and its docstring says so;
  - both `llm_capture.Capture(...)` constructions pass
    `run_id=llm_capture.run_id(usage)`. They are `_resilient` at
    `llm.py:1086-1088` and `decide_native` at `llm.py:1575-1577`. Each is
    built *after* `_stamp`, so it reads the key `_stamp` kept.
- Modify: `docs/incoming-llm-capture.md`. The fields sentence
  (lines 26-30) says a row also carries `run_id` when the call was made
  under a caller's run, the same value on every attempt.
- Test: `backend/tests/test_incoming_capture.py`,
  `backend/tests/test_native_decisions.py`,
  `backend/tests/test_usage_estimate.py`

- [ ] **Step 1: Failing tests**
  - `test_incoming_capture.py::test_stamp_keeps_the_run_key_and_clears_the_rest`.
    A holder with `RUN_KEY` and a stale `prompt_tokens`, after `llm._stamp`
    with `CONN`, keeps the run id and loses the count.
  - `test_incoming_capture.py::test_the_run_id_rides_every_attempt_of_a_call`.
    `monkeypatch.setattr(llm, "RETRY_BASE", 0.0)`. `GRIMOIRE_HOME` is set to
    `tmp_path`. An `LLMClient` is built directly, as `client_for` does but
    with `retries=1`, over a handler that answers 429 and then a content
    frame. The call is `client.complete([], CONN, m.usage)` inside
    `usage.meter("chat", run_id="run-1")`. The checks:
    - every captured event has `run_id == "run-1"`;
    - the events come from attempts `{1, 2}`;
    - `m.row["run_id"] == "run-1"` and `m.row["attempts"] == 2`.
  - `test_incoming_capture.py::test_no_run_id_without_a_run`. The first
    existing test's events carry no `run_id` key. Add one assertion to
    `test_all_lines_survive_before_content_and_usage_filtering`.
  - `test_native_decisions.py::test_a_native_capture_carries_the_run_id_across_a_retry`.
    The call is
    `Wire((429, {"error": {"code": 429, "message": "slow down"}}), (200, body("answered")))`,
    with `facade(wire, capture=lambda: events.append, retries=1)` and
    `decide_native(ITEM, CONN, {llm_capture.RUN_KEY: "run-1"})`. Every
    event carries `run_id == "run-1"`, from attempts `{1, 2}`.
  - `test_usage_estimate.py`: the spelling-equality assertion from Task 1.
- [ ] **Step 2: Run** `... -m pytest -q -p no:cacheprovider tests/test_incoming_capture.py tests/test_native_decisions.py tests/test_usage_estimate.py` → fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same set, plus `tests/test_llm*.py tests/test_anthropic.py tests/test_claude_agent.py tests/test_docs_guard.py` → PASS.

### Task 3: `decide` and `run_stages` thread the keywords to every meter

**Files:**
- Modify: `backend/src/grimoire/inference.py`:
  - `_Call` (`inference.py:213-237`) gains `response_id: str = ""`,
    `run_id: str = ""` and `loop_turn: int | None = None`. They go after
    `positions`, with defaults, so the dataclass stays valid;
  - new `_meter(call) -> store.usage.Meter`, which opens
    `store.usage.meter(call.task, campaign=, scene=, post=, round_id=,
    response_id=, run_id=, loop_turn=)`;
  - `_once` and `_native` open their meters through it;
  - `_once` sets `sent = store.usage.sent(m.usage)`;
  - `_native` checks `store.usage.sent(m.usage)` before building the
    capture's request;
  - `run_stages(..., response_id="", run_id="", loop_turn=None)` builds the
    `_Call` with them;
  - `decide(..., response_id="", run_id="", loop_turn=None)` passes them on,
    and its docstring names them.
- Test: `backend/tests/test_inference_decide.py`,
  `backend/tests/test_inference_decide_native.py`

- [ ] **Step 1: Failing tests**
  - `test_inference_decide.py::test_every_row_a_decide_files_carries_the_run`.
    Build the setup of
    `test_a_generating_primary_with_a_decide_only_fallback_falls_to_a_native_stage`
    (line 1094): a structured stage that fails, then a native stage that
    answers. Call
    `_decide(..., run_id="run-1", loop_turn=2, response_id="response-mara")`.
    Both rows (`error`/structured and `ok`/native) carry `run_id`,
    `loop_turn` and `response_id`, and `got.usage == tuple(_rows())`.
  - `test_inference_decide.py::test_a_schema_resend_files_under_the_run`.
    Use the setup of `test_a_refused_fallback_is_retried_without_the_mode`
    (line 741). Every row, the re-send's included, carries the three fields.
  - `test_inference_decide.py::test_a_decide_without_a_run_files_no_run_fields`.
    A plain `_decide` row has none of the three keys. The golden test already
    covers this too.
  - `test_inference_decide_native.py::test_a_clock_refusal_under_a_run_files_no_row_and_captures_no_messages`.
    Use the setup of
    `test_a_call_the_clock_refused_unsent_is_captured_with_no_messages`
    (line 1175), plus `run_id="run-1"`. There is no row (`_rows() == []`),
    and the capture's `messages == []`.
  - `test_inference_decide_native.py::test_a_native_item_refused_unsent_under_a_run_captures_no_messages`.
    Use the setup of
    `test_an_item_refused_unsent_is_captured_with_no_messages` (line 1150),
    plus `run_id="run-1"`. The refused capture's messages are `[]`. The
    native item filed no row: the only row is the structured fallback's, and
    it carries `run_id`.
  - `test_inference_decide_native.py::test_native_rows_carry_the_run`.
    Use `_native_resolution(client, fallback=True)` and two items, both
    answered natively. Both rows carry `run_id`, `loop_turn` and
    `response_id`.
  - `test_inference_decide_native.py::test_run_stages_files_the_run`:
    `run_stages` with a hand-built chain (as at line 1001) and `run_id=`
    stamps every row.
  - `test_inference_decide.py::test_a_decide_under_a_run_captures_its_run_id`:
    `inference.decide(run_id=)` through a real `LLMClient` over a
    `SequencedProvider`; every capture event carries the run id.
- [ ] **Step 2: Run** `... -m pytest -q -p no:cacheprovider tests/test_inference_decide.py tests/test_inference_decide_native.py` → fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_inference_decide.py tests/test_inference_decide_native.py tests/test_decide_chain_golden.py tests/test_decision_capture.py tests/test_decisions.py tests/test_decide_gate.py tests/test_native_decisions.py tests/test_usage_guard.py tests/test_operation_guard.py tests/test_routing_guard.py tests/test_import_guard.py tests/test_evals.py` → PASS.

### Task 4: docstrings, the gate, and the commit

**Files:**
- Modify: `backend/src/grimoire/store/inference/embed.py`. One docstring
  sentence in `_stamp` (`embed.py:56-58`): "an empty one means never sent"
  becomes "an unstamped one (`usage.sent`)". There is no code change.
- Modify: `backend/src/grimoire/inference.py`. The `_Reply` docstring
  (`inference.py:339-345`) names `store.usage.sent` as the test.
- Modify: `store/usage.py` `Meter.done` docstring and
  `routes/config.py:1184` ("an empty holder") name `usage.sent`.
- Modify: `backend/tests/test_usage_guard.py`: a guard refusing
  `bool(<x>.usage)`, `if <x>.usage` and `not <x>.usage` anywhere in the
  package outside `store/usage.py`, with planted cases.

- [ ] **Step 1:** `make check-lint check-mypy PY=/home/user/grimoire/backend/.venv/bin/python`
  from `/home/user/wt/01g-s3`. Both must be at baseline. A resolved finding
  means `make baseline PY=...` and committing the smaller file.
- [ ] **Step 2:** `make check-templates PY=...`, which is unaffected, as a
  sanity check. Then run the whole suite once:
  `make check-py PY=/home/user/grimoire/backend/.venv/bin/python`.
- [ ] **Step 3: Commit** `01g-S3: run attribution on the ledger and the capture`.
  The body names `run_id` / `loop_turn` / `tool_calls`, `decide(run_id=,
  loop_turn=, response_id=)`, the capture meta, and `usage.sent`. It ends
  with the two attribution lines from the brief.

## Decisions taken where the spec is silent

- **The meter seeds `RUN_KEY`; the caller does not.** Spec §3.10 says "the
  loop puts it in each turn's holder". Putting it in `Meter.__init__` gives
  the same holder content for S4. It also gives every other metered call
  under a run the same capture attribution: decide's stages and 01h's embeds.
  Without it, each of those call sites would repeat one line. The alternative
  leaves the same non-empty-holder hazard, so `usage.sent` is needed either
  way.
- **`tool_calls` is on all three of `record`, `Meter` and `meter`**, as the
  slice entry lists. It is also a settable `Meter` attribute, because only
  the loop knows the count, and only after the facade call.
- **`loop_turn` and `tool_calls` are each written independently of `run_id`.**
  This is the "only when set" rule `round_id` follows. Requiring `run_id` for
  `loop_turn` would be a second validation rule in a function whose contract
  is to drop what it cannot file.
- **`response_id` on `decide`** is the shared structure `ROADMAP-CHECKLIST.md`
  lists as 02's. Whichever spec lands first adds it, and this slice's entry
  names it. 02's `responses.mint_id()` is not added.
- **The error store is unchanged.** `errors.record` in `Meter.done` keeps
  its fields, and no `run_id` is added there. The spec does not ask for it.
  This leaves a gap: a failure refused before sending (`native_unrepresentable`,
  `missing_key` before a stamp) files an error record and no ledger row, so
  it cannot be tied to a run.
- No spec open question bears on this slice. Q1 (the caller's run id) and
  Q7 (embeds under a run, through 01h-C5) are settled, and this slice is
  their field.

## Plan gate (substitute review, 2026-10-10)

An independent reviewer agent stood in for `/codex:adversarial-review`
(no Codex CLI). Verdict: sound and complete; the holder inventory
(`usage.py:580`, `inference.py:398`, `inference.py:647`) confirmed; seeding
at `Meter.__init__` accepted on findings 1 and 2. Folded:

- **S1 `sent` against S4's pre-send keys.** `sent` is defined against
  `PRE_SEND_KEYS` (the run key and the restated reasoning-buffer key, pinned
  equal by `test_estimate_keys_are_one_spelling`); `tool_calls.KEY` joins it
  in one line when 01g-S1 integrates. Tested with a reasoning buffer.
- **S2 an emptiness test cannot come back.** `test_usage_guard.py` refuses
  `bool(<x>.usage)`, `if <x>.usage` and `not <x>.usage` outside
  `store/usage.py`.
- **S3 embeds.** The false claim that embeds get capture attribution is
  corrected: they get the ledger field only; the handoff note for 01h-S5/S6
  is in the Architecture paragraph.
- **N4** the `Meter.done` and `routes/config.py` docstrings; **N5** the
  error-store reason reworded; **N6** the four tests named above; **N7**
  `record` gains no branch; **N8** `ROADMAP-CHECKLIST.md` untouched.

Implementation notes: `store/usage.py` already has a `_count` (the rollup's),
so the run-field helper is `_positive`. The end-to-end `decide(run_id=)`
capture test sits in `test_inference_decide.py` beside the decide store
fixtures. The emptiness guard's predicate is one function
(`_tests_emptiness`), which keeps ruff's SIM114 at baseline.

## Review and final gate (substitute review, 2026-10-10)

An independent reviewer agent stood in for `/codex:review` and the final
spec gate (no Codex CLI). Verdict: no blockers; every spec item of the slice
(01g-C3 part: the three ledger fields, `decide(run_id=, loop_turn=,
response_id=)` on every meter, `RUN_KEY` kept by `_stamp` and named in the
capture meta) is delivered; a re-sweep found no other emptiness reader and
no holder serialisation. Recorded drift from spec §3.10: the run id is
seeded by `Meter.__init__`, not placed by the loop (accepted at the plan
gate). Folded before integration, after a rebase onto `b3204cc` (01d-S1,
01h-S1):

- **The emptiness guard widened.** It now catches a holder's truth value in
  `if`/`while`/ternary/`assert`/a comprehension's `if`/`not`/`and`-`or`
  operands/`bool()`, its `len()`, and `== {}`/`!= dict()`, on attributes,
  bare names and subscripts called `usage`, `holder` or `holders`, each
  shape planted. `x or {}` (a None default) is allowed, and
  `NOT_HOLDER_RECEIVERS` exempts `store.usage` and `Decision.usage` (bound
  as `decision`/`decided`/`got`) by name. The docstring says what it cannot
  see: a holder bound under another name.
- **Invariant:** `test_the_keys_stamp_keeps_are_the_pre_send_keys` stamps a
  holder carrying every holder key the gateway declares (`KEY`, `*_KEY`,
  `ATTEMPTED`) and requires the kept set to equal `usage.PRE_SEND_KEYS`; a
  declared key with no candidate value fails first, so 01g-S1's
  `tool_calls.KEY` must be added to both.
- **Nits:** `sent(None)` is False and the docstring says so; `Meter.done`'s
  `missing_key` sentence names `sent`; a row never carries `_run_id`
  (asserted on the row and on disk); the run id rides a fallback's capture;
  a run row folds into `usage_rollup.campaign_totals` as any row does.

Rebased onto `36d78eb` (01d-S2, 01c-S1/S2, 01d-S3). Every meter `decide`
opens still goes through `_meter`, the isolated native-first stage's
included; 01d-S3's escalation hop builds its `_Call` with the base's
`response_id`, `run_id` and `loop_turn`, so its rows (marked `hop`) file the
run too (`test_decide_escalation.py::test_hop_rows_carry_the_run`).
`usage.record` takes both `hop` and the run fields.

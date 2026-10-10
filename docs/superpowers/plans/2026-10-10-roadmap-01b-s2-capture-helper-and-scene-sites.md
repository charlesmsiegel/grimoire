# 01b-S2: The capture helper, the decision pool and the four scene-level sites — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The speaker pick, the scene-break check, the voice-drift phase and
the continuity identity check each file ONE prompt-log entry per decision
scope -- the requests as sent, every call's sanitised outcome with its stage
and batch indices, and the caller's notes -- into a scene-decision retention
pool of their own, fenced on the scene's strict identity, and listed apart
from turns in the inspector.

**Architecture:** A new `routes/decision_capture.py` owns `OUTCOME_SECTION_ID`,
`Scope` (`hook(part)`, `note(part, key, value)`) and the
`capturing(cid, sid, task, *, fence=None, abandoned=None)` async context
manager. The hook only appends to memory; on exit the scope builds the §3.4
envelope in a worker thread and files it through `common._record_prompt`,
which gains `operation=` (passed to `prompt_log.record`) and `still=` (a check
run inside its non-blocking hold, after the scene read and before the write).
`prompt_log.record` gains `operation` and evicts per pool. The scenes
diff route refuses a decision entry against the live side with 409.
Frontend: labels, `isDecision`, a Decisions section, a restricted compare
picker, `ConfigView.lastPrompt` skipping decisions, and the Settings caption.

**Tech Stack:** Python 3.11 (FastAPI, starlette `run_in_threadpool`), pytest;
React + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01b-decision-capture-design.md` §3.1, §3.3-§3.8 (scene level), §4, §5, §6 tests 1-5, 6 (not reconcile), 7 (scene pools), 8, 9 (diff), 10, 11, 12, frontend 1-3.

## Global Constraints

- The hook does no I/O and never blocks: it appends `(part, messages|None,
  sanitised outcome, target)`; past `MAX_CALLS_IN_FULL` (4) calls it keeps
  `None` for the messages at append time (test 8: no fifth call's messages
  held).
- **Error sanitising needs the `LLMError`, which the outcome dict does not
  carry** (it has only `"kind: detail"`). `inference._outcome` returns an
  `inference.Outcome` -- a `dict` subclass, equal to and JSON-encoded as the
  plain dict, so the golden and every `==` test stay put -- whose `failure`
  attribute carries the call's `LLMError` (None when it answered). The hook
  replaces `error` with `error_kind`, `error_status` and `code` (when the
  error has one), and never keeps `detail`. Without a `failure` (a foreign
  capture), the kind is the text before the first `": "` and status is null.
- Filing happens once, after `decide()` returned or raised an `LLMError` /
  `DecideRequestError`, in a worker thread, under the non-blocking lock, and
  never raises: every failure is one `warning` line naming the task and the
  exception type. Nothing is filed on cancel, `Abandoned`, any other exception,
  or when `abandoned()` answers True. The original exception is re-raised.
- Fence: on entry the scope reads `prompt_log.capturing()` and
  `scenes.scene_identity_strict(cid, sid)` off the loop; off, `None` or a raise
  means `hook()` returns None and nothing is filed. At filing the strict read is
  repeated inside the hold (`still=`) along with the site's `fence`; a
  mismatch, `None` or a raise skips the write, and a skipped write bumps no
  revision. `ensure_identity` is never called (no scene file written).
- `prompt_log.record(..., operation="decide")` writes `operation` to the row
  and payload; eviction counts within the new entry's pool (generations;
  scene decisions; campaign decisions, which S3 first fills). `_well_formed*`
  accept an optional `str` `operation`.
- `character_turns.OUTCOME_SECTION_ID` stays, re-exported from
  `decision_capture`; `character_turns._capture` stays the generation path
  (the golden calls it) and shares the message-section builder.
- No `decisions.py` change; the only `inference.py` change is `Outcome`.
- Privacy: no key/URL/provider text in a payload; logs carry the task and
  exception type only.

## Review Focus

- `capturing` as an `asynccontextmanager`: an exception thrown in at `yield`
  that is not `LLMError`/`DecideRequestError` must propagate untouched (no
  filing), `CancelledError` included (it is a `BaseException`).
- Voice drift catches every `Exception` per NPC, so an `Abandoned` from a
  call would be swallowed; the phase gains `abandoned=` (passed from
  `_absorb_work`) and the scope skips filing when it answers True.
- Scene-break wraps only the `decide` call (not the title `generate`).
- `_record_prompt`'s existing revision bump stays after the write, inside the
  hold; `still=` runs before the write so a fenced-off capture bumps nothing.
- The diff route: a decision base against `live` is 409
  `{"kind": "not_comparable"}`; a legacy `response-selector` row with no
  `operation` is a decision.

---

### Task 1: `prompt_log` pools and `operation`

**Files:** Modify `backend/src/grimoire/store/prompt_log.py`; Test `backend/tests/test_prompt_log.py` (or the existing prompt-log test file).

- [x] Failing tests: `record(..., operation="decide")` writes it to row and payload; a row/payload with a non-str `operation` is ill-formed; with depth 3, four decide records for a scene leave three decide rows and every generation row, and four generations leave every decide row.
- [x] Implement `_pool(row)`, per-pool eviction, `operation` in `_well_formed_row`/`_well_formed`.

### Task 2: `inference.Outcome`

**Files:** Modify `backend/src/grimoire/inference.py` (`Outcome`, `_outcome`); Test `backend/tests/test_inference_decide.py`.

- [x] Failing test: a failed chunk's captured outcome `isinstance(o, inference.Outcome)`, `o.failure` is the `LLMError`, and `o == {plain dict}`; an answered one has `failure is None`.
- [x] Implement: `class Outcome(dict)` with `failure` set by `_outcome`.

### Task 3: `routes/decision_capture.py` and `_record_prompt(still=, operation=)`

**Files:** Create `backend/src/grimoire/routes/decision_capture.py`; Modify `backend/src/grimoire/routes/common.py`, `backend/src/grimoire/routes/character_turns.py` (re-export, shared section builder); Test `backend/tests/test_decision_capture.py`.

**Interfaces — Produces:** `decision_capture.OUTCOME_SECTION_ID`, `DECIDE`, `MAX_CALLS_IN_FULL = 4`, `MAX_OUTCOME_CHARS = 64_000`, `Scope.hook(part="") -> inference.Capture | None`, `Scope.note(part, key, value)`, `capturing(cid, sid, task, *, fence=None, abandoned=None)`, `is_decision(row) -> bool`, `message_sections(messages, *, call=None, calls=1)`.

- [x] Failing helper tests (driving `capturing` + `inference.decide` with `FakeLLM` over a real scene): one entry, `operation == "decide"`, outcome last; a two-chunk decide → `c0_*`/`c1_*` sections and two records with batch `at`; capture off → `hook() is None`, nothing filed, `_native_request` not called; a raising `prompt_log.record` → decision unchanged, no error row, no ERROR log, one warning naming task and type; cancel and `Abandoned` file nothing; `abandoned()` True files nothing; an `LLMError` files every call's kind/status and re-raises; a `DecideRequestError` files `calls: []` and `"error": "invalid_request: ..."`; fence (scene deleted+recreated mid-scope, identity-less scene not written, identity read raising at filing, contended lock does not wait); six calls → four kept, `elided_calls == 2`, fifth call's messages not held; notes count against `MAX_OUTCOME_CHARS`; over the cap drops whole records from the end with `truncated`; revision bumped once after the write, not on a skip; privacy (no key / base URL / masked-key detail in payload bytes; warning carries no content).
- [x] Implement the module and the two `_record_prompt` keywords.

### Task 4: the four scene sites

**Files:** Modify `routes/character_turns.py` (`_select`), `routes/scenes.py` (`_break_ask`, `_stage_voice_drift` + `abandoned=` from `_absorb_work`, `_resolve_identity`, diff route); Test `test_decision_capture.py`, `test_character_turns.py` (test 11).

- [x] Failing site tests: speaker, scene-break, voice drift (one entry per phase, `part` = `aid`), identity (two chunks) each file exactly one entry with the task, `operation == "decide"`, outcome last; scene-break with a scene deleted and recreated during the call files nothing; four scene-break checks at depth 3 leave three decision entries and every turn entry; diff `against=live` on a decision entry (and a legacy `response-selector` one) is 409 `not_comparable`, while two same-task frozen entries compare.
- [x] Rewrite `test_the_selector_capture_records_the_decision` to the envelope (one record = old outcome + `part`, `stage`, `at`; row `operation: "decide"`).
- [x] Implement each site's `async with decision_capture.capturing(...) as scope:` around its `decide`.

### Task 5: frontend

**Files:** Modify `frontend/src/api/types.ts`, `components/turnLabels.ts`, `components/SceneInspector.tsx`, `routes/ConfigView.tsx`; Tests `components/SceneInspector.test.tsx`, `routes/ConfigView.test.tsx`.

- [x] Failing vitest: Turn history hides decision rows and the collapsed Decisions section lists them with the new labels; opening one shows "outcome · not sent", and the compare picker has no live option and only same-task entries; `ConfigView`'s bar uses the newest non-decision entry.
- [x] Implement: `PromptEntry.operation?: "decide"`, widened `task`, labels, `isDecision`, the section, the picker filter, `lastPrompt`, caption "per campaign; decisions are kept separately, as many again".

### Task 6: verify and commit

- [x] Backend: new and touched tests, guards (import, atomic, paths, lock-domain, lock-order, usage, operation, routing), golden, frozen-campaign; ratchets; `make check-web` equivalents (typecheck, vitest, eslint ratchet).
- [x] Commit `01b-S2: one decision capture per scope at the four scene-level decide sites`.

## Plan gate (substitute review, 2026-10-10)

No Codex CLI and no subagent tool in this session: an adversarial
self-review of the plan against the spec and the code stood in. Findings:

1. **Blocking, folded (Global Constraints).** §3.3 has the hook rewrite a
   failed call's record to kind, status and code, but the hook receives only
   `decisions.outcome`'s dict, whose `error` is the string `"kind: detail"`:
   the status and code are not recoverable from it. Folded as
   `inference.Outcome`, a `dict` subclass carrying the `LLMError` as an
   attribute, so `decisions.outcome`, the golden and every equality test stay
   as they are (the spec's own constraint) while the capture can sanitise.
2. **Should-fix, folded.** A scope that made no call and exited normally
   (a voice-drift phase whose every NPC failed before its item was built, or
   whose anchored NPCs all fell to the budget) would file an empty entry.
   Rule: a normal exit with no call and no note files nothing; only a
   `DecideRequestError` files a call-less entry (§3.3's stated shape).
3. **Should-fix, folded.** The spec's voice-drift `abandoned` rationale
   ("its loop swallows `Abandoned`") does not hold in today's code -- the
   phase is never handed `abandoned`, and absorb's `_watched` cancels the
   fan-out, which crosses the scope as `CancelledError` and files nothing.
   The phase still gains `abandoned=` (passed from `_absorb_work`) so the
   scope skips filing for a review the person closed while the phase was
   finishing, and test 5's clause is driven by a decide that raises
   `Abandoned` inside the per-NPC `except Exception`.
4. **Minor, folded.** A turn's compare picker also leaves decisions out:
   comparing a chat prompt with a decide prompt is the noise §3.7 refuses.
5. **Minor, folded.** `still=` runs inside `_record_prompt`'s hold after the
   scene read and before `record`, so a fence that fails skips the write and
   the revision bump together (test 10's "a skipped capture bumps nothing");
   a `still` that raises leaves `_record_prompt` (it is not one of the
   swallowed kinds) and the scope's own guard turns it into the one warning.
6. **Minor, noted.** `prompt_log`'s module docstring states retention per
   campaign; it is rewritten to name the pools.

## Review and final gate (substitute review, 2026-10-10)

An adversarial self-review of the slice diff against §3, §4 and the slice's
acceptance stood in for `/codex:review` and the final spec gate (no Codex CLI,
no subagent tool). Each §6 clause the slice owns was traced to a test in
`test_decision_capture.py` (tests 1-5, 6 bar reconcile, 7's scene pools, 8,
9's diff clause, 10, 12), `test_prompt_log_store.py` (the pools, `operation`
validation), `test_character_turns.py` (test 11) and the vitest suites
(frontend 1-3). Findings folded:

- **A live comparison outlived a move onto a decision.** Comparing a turn with
  the live preview survives clicking down the rail by design; clicking a
  decision then asked the server for a decision-vs-live diff it refuses (409).
  The inspector now ends a live comparison when the entry shown is a decision.
- **A turn's compare picker offered decisions** (a chat prompt against a
  decide prompt); it now lists turns only, and a decision's lists only its own
  task (frontend test 2).
- **`ContextBreakdown`'s comment named the outcome id's old home**
  (`character_turns`); it names `decision_capture` now.
- **An `OSError` from the strict identity read at filing is swallowed by
  `_record_prompt`** (it is one of that function's "unreadable: capture
  nothing" kinds), so it costs the capture silently rather than with a
  warning. Kept: it is the existing filer's rule for the same failure, and the
  warning path is still exercised by a non-storage failure (test 4).
- **A new eslint finding** (`no-misused-promises` on the Decisions row's
  `onClick`) was avoided with `void`, leaving the ratchet at baseline.

Drift from the spec, kept on purpose:

- `inference.Outcome` (a `dict` subclass carrying the `LLMError`) is new:
  without it the hook cannot read a failed call's status or code (plan gate,
  finding 1). `decisions.outcome` and the golden do not move for it.
- A normal exit with no call and no note files nothing (plan gate, finding 2).
- The identity/voice-drift "warning on failure" covers non-storage failures;
  storage failures are silent, as `_record_prompt` already makes them.
- CLAUDE.md's "capture nothing" sentence is rewritten in S3, as the spec's
  Slices section places it, although after this slice only the reconcile
  sweep still captures nothing.

Results: `test_decision_capture.py` (31), the prompt-log, character-turn,
identity, scene-break, routes and scene-freeze suites, every guard, the golden
and the frozen campaign pass; ruff, mypy and eslint ratchets all at baseline;
`tsc -b` clean; vitest 4551 passed; the full backend suite (`-n 4`): 16394
passed, 5 skipped, 1 failed -- `test_atomic.py::test_a_read_only_record_is_not_silently_replaced`,
which fails on main too because the container runs as root.

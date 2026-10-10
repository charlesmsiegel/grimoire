# 01d-S3: The escalation hop in `decide` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `inference.decide` gains `escalation=`, a thunk the call site
hands in exactly when the task's code policy escalates
(`routing.policy(task).escalate_to`). After the unchanged chain has answered,
`decide` reads the base items' triggers (`decisions.triggers`, 01d-S2), and
hands the triggered items, once, to the declared next resolver: the
escalation role's primary alone (`retries=0`, one structured chunk at most,
never the model that answered the item) or a caller-supplied `Resolver`
returning a `decisions.ResolverReply`. A hop answer replaces the item's
deciding answer under the per-question merge (a decline only on a
`reads_declines` task); anything else leaves the original standing. The
record is `Decision.escalations`; hop calls are metered under the same task
and attribution, file `hop: "escalation"` on their ledger rows (a new
`wire.Account` / `llm_usage.ACCOUNT_FIELDS` / `usage.record` field), are
`Decision.calls` records marked `hop="escalation"`, and reach the capture
with `"hop": "escalation"` on their outcome. A call site and its policy that
disagree raise `ValueError` before any meter opens. No call site passes
`escalation=` and `TASK_POLICY` is empty, so nothing escalates in production.
Delivers 01d-C2b (part) and 01d-C1 (part: the call-site/policy `ValueError`).

**Architecture:**

1. *The base is untouched.* `decide` still calls
   `run_stages(task, items, stages(resolved), ...)` exactly as today, and
   returns its `Decision` as it is when the task's policy does not escalate
   (`escalation is None`), and when nothing triggered. Neither `stages` nor
   `run_stages` is edited (01c-S2 owns those two, below).
2. *The hop is one backend call, not a second `run_stages`.* The spec's flow
   (§5.3) writes "`run_stages` again on the escalated subset with a one-stage
   chain". This plan sends the hop through the same backend dispatch
   `run_stages` uses (`_BACKENDS[stage.mode](items, call)`), with a `_Call`
   built for the hop. For a one-stage chain the two are the same calls,
   meters, retries, captures and settle, with three differences, each of
   which the spec asks for and `run_stages` cannot give without being
   edited: (a) `run_stages` *raises* when no item answered, losing the hop's
   ledger rows and `CallRecord`s, which §5.5 says join `Decision.usage` and
   `Decision.calls` — a backend returns them in `_Answered` whatever
   happened; (b) the hop's `_Call` carries `positions` = the batch indices it
   took, so every `CallRecord.items` and capture `at` the backends write is a
   BATCH index without any rewriting (a nested `run_stages` would number the
   subset from 0); (c) its `stage` is `len(stages(resolved))` (the hop sits
   after the chain), set once on the `_Call`. Decision 1 records this.
3. *The hop mark rides the `_Call`.* `_Call` gains `hop: str = ""`; the two
   readers that write what a call was, `_record` (the `CallRecord`) and
   `_outcome` (the capture record), copy it when it is set and add nothing
   when it is "". The ledger mark rides the hop target's account
   (`target.with_account(hop="escalation")`), which `llm._stamp` /
   `llm_usage.account` already file, exactly as `decision_mode` does. So the
   base chain's rows, records and captures are byte-identical, and
   `test_decide_chain_golden.py` does not move.
4. *`decisions` stays a leaf.* `Escalation`, `ResolverReply`, the outcome and
   detail vocabularies and `HOP_ESCALATION` are plain frozen dataclasses and
   constants over `ItemResult`; `Decision` gains `escalations` (default `()`,
   so every existing equality holds). The `Resolver` and `Escalator` type
   aliases live in `inference` (they name `ResolvedInference`).
5. *Small functions.* `inference.py` has no C901 baseline entry, so every new
   function stays at mccabe ≤ 10: `_policy_for` (pre-meter checks),
   `_hop_stage`, `_take`, `_role_hop`, `_caller_hop`, `_checked_reply`,
   `_merge`, `_escalated` (the `Decision`), and `decide` itself delegating to
   `_escalate`.

**Tech Stack:** Python 3.11 (`dataclasses.replace`, frozen dataclasses,
`collections.abc`), pytest, the shared fakes (`FakeLLM` with `turns=` and
`decisions=`, its `stall=`, `error=`/`fail_after=`), the inference fixtures
(`tests/inference_fixtures.py`: `format2`, `decide_only`, `put_settings`,
`primary`).

**Spec refs:** `docs/superpowers/specs/2026-10-09-roadmap-01d-decision-escalation-policy-design.md`
§3 (failure chain vs escalation), §5.2 (C2b contract: `escalation=`,
`Resolver`, `ResolverReply`, the `ValueError`/`TypeError`), §5.3 (the flow,
the per-chunk cap, the per-question merge, why declines are opt-in), §5.4
(same model), §5.5 (`Escalation`, `Decision.escalations`, `errors`
unchanged, `Decision.calls`), §5.7 (metering, capture, budgets,
cancellation, errors), §7 (01d-C1, C2b), §8 (usage guard, ledger, import
guard, docs), §9 (`test_decide_escalation.py`), §11 (open questions),
Slices (01d-S3). Landed plans read: `2026-10-10-roadmap-01d-s1-task-policy.md`
(`routing.TaskPolicy`, `policy`, `CALLER`, `ESCALATION_ROLES`; the §4.3
rules in `test_task_policy.py`) and
`2026-10-10-roadmap-01d-s2-item-provenance-and-triggers.md` (its "Handoff to
01d-S3", and the `MASS_TIE` erratum in `ROADMAP-CHECKLIST.md`). 01a's `hop`
consumers: `evals/costs.py` (`hop_of` reads `row["hop"]`, `NO_HOP = "-"`),
`evals/runner.py` (`item_records` reads `CallRecord.hop == "escalation"`),
`evals/runfile.py` (writes `record.hop`), and `decisions.CallRecord.hop`
(reserved by 01a-S1, `decisions.py:535-548`).

**Open questions adopted (spec §11):**

- Q1 (raw or grouped margins): **raw**, as 01d-S2 built `decisions.margin`.
  Nothing here regroups.
- Q2 (a hop's `abstained` replacing an answer): **only with
  `reads_declines=True`** (§5.3). On any other task a hop decline is
  `failed`, detail `declined`, and the original stands.
- Q3 (Settings readout): not this slice (01s / a task's switching change).
- Q4 (`retries=0` on the hop): **yes**. The hop `_Call` has `retries=0`, so
  a structured hop passes `retries=0` to `complete` and a native one to
  `decide_native`.
- Q5 (speaker escalation): **off**. `TASK_POLICY` stays empty; nothing here
  touches `routes/character_turns.py`.
- Q6 (the `hop` ledger field): **added** (Task 2), as 01a already reads it.

## Global Constraints

- **No behaviour change at landing.** `TASK_POLICY` stays `{}` and no call
  site passes `escalation=`. `test_decide_chain_golden.py`,
  `test_inference_decide.py`, `test_inference_decide_native.py`,
  `test_decision_capture.py`, `test_adapter_wire_golden.py` and the frozen
  campaign's `snapshot.json` pass untouched; no golden is regenerated.
- **`run_stages` and `stages` are not edited.** The hop calls
  `_BACKENDS[mode]` directly (Architecture 2). Native-first (01c) can
  therefore never apply to the hop: the hop's stage is built from the
  escalation primary's `decision_mode` alone (§5.3).
- **Every refusal of the call site is before any meter.** In order, after the
  three existing `resolved` checks: `escalation` given iff
  `policy.escalate_to` (`ValueError`); when escalating, `decisions.validate`
  then every item asks `policy.question` (`ValueError`, so S2's
  `triggers` can never raise after a paid call — S2 handoff); and
  `decisions.triggers((), question=..., escalate_on=..., margins=..., answers=...)`
  run once on no results, which raises for an unknown trigger, a bare
  `str`, or a threshold that is not a positive finite number, before any
  call.
- **After the base, nothing raises on a provider's account.** The hop's
  backend catches `LLMError` per unit; a caller `Resolver`'s `LLMError` is
  caught. What may still raise after the base: `CancelledError` (passes
  through), a non-`LLMError` exception from a backend or a resolver
  (propagates, as from `run_stages`), and the caller-bug errors §5.2 names —
  `TypeError` (a `Resolver` for a role policy or a resolution for `CALLER`),
  `ValueError` (a resolution of another task or operation; a malformed
  `ResolverReply`). These cannot be checked before the base: the thunk is
  only called once something triggered (§5.2, Decision 3).
- **`Decision.errors` is the base's, always** (§5.5). A failed hop is never a
  unit there, so `routes.common._decide_error` reads what it read before.
- **A role hop is the escalation primary alone**: `wire.Chain(primary)` with
  no fallback, `retries=0`, the target's own sampling (whatever the
  escalation resolution built: S4's seam makes that the role's own preset),
  `account.hop="escalation"`; a native stage strips sampling as `_native`
  always does.
- **Attribution is the base's.** The hop `_Call` is built from the same
  `task`, `client`, `explain`, `campaign`, `scene`, `post`, `round_id`,
  `capture` and `around` the base got — and, once 01g-S3 lands, its
  `response_id`, `run_id` and `loop_turn` (see "Where this meets 01g-S3").
- **`decisions` stays a leaf**: standard library and `schemas` only
  (`test_decisions.py`'s `_sibling_imports`). `inference` imports nothing
  new (`store.routing` through the `store` binding it already has, as
  `for_task` does).
- **The ledger field is additive.** `hop` is written only when non-empty, so
  every existing row, rollup and golden is unchanged; no money column moves;
  `usage_rollup.VERSION` is not bumped (§5.7).
- **Imports and guards.** No new meter site (the hop's meters are the
  backends' own, in `inference.py`, which `test_usage_guard.py` scans); no
  `client.complete`/`stream` spelled anywhere new; no new `inference.resolve`
  call in `routes/` (that is S4's seam).
- **Privacy.** Fixtures use the placeholder names (Mara, Winifred,
  Seraphine, Saltmarch) and invented model ids (`vendor/active`,
  `vendor/decider`, `vendor/second`, `vendor/spare`). No store data is read
  or described.
- Ratchets (`check-lint`, `check-mypy`, `check-eslint`) stay at baseline; no
  baseline file changes (mccabe ≤ 10 per new function, Architecture 5). No
  frontend file changes.

## Review Focus

- **The base path is identical.** With `escalation=None` (and so an empty
  policy), `decide` returns `run_stages`' `Decision` object itself; with a
  policy whose triggers find nothing, it returns that object too, and the
  thunk is never awaited.
- **The pre-meter checks** (Global Constraints) run before `stages` /
  `run_stages`, and a test asserts no row and no fake call for each.
- **The cap order.** Triggers in S2's priority order; `same_model` (role hop)
  removed first, so a skipped item never uses the cap; then
  `escalate_max`; then, on a structured hop, only the prefix that
  `decisions.chunks` (default budgets, the call `_structured` makes) puts in
  its FIRST chunk. The rest are `skipped`, detail `cap`.
- **Same model** compares `before.served[1:]` with the hop target's
  `(provider_id, model)`; an unstamped item (`served == ()`) never matches.
  Another model on the same provider is allowed.
- **The merge** (`_merge`): replaced only when the hop's `question` answer is
  non-None, or a decline on a `reads_declines` task; then the deciding answer
  and rationale, `backend` and `served` are the hop's, and every other
  question keeps the base's answer unless the hop gave it a value (a
  non-None answer; Decision 7 on why not `was_read`). Anything else is
  `failed` and the item is the base's object, unchanged.
- **The `Decision`** (`_escalated`): `items` merged; `usage` base rows then
  hop rows; `calls` base records then hop records (`hop="escalation"`,
  `stage=len(chain)`, batch indices); `errors` the base's; `served` the
  union, in first-answered order, of the base's and of every server that
  answered a hop call -- whether or not the merge kept that answer (plan
  gate 3; spec §5.5 and the `Decision` docstring); `provider` and `model`
  stand only when that union is one route, and `backend` only when every
  answering call had one backend (`run_stages`' own rule).
- **Decision 1** (backend dispatch instead of a nested `run_stages`) is the
  one place this plan reads the spec's flow differently; it is argued above
  and below, and it changes no call, row or retry the spec describes.
- **No second hop**: `triggers` runs once, on `base.items`; the hop's
  results are never fed back.

---

### Task 1: the records — `Escalation`, `ResolverReply`, `Decision.escalations`

**Files:**
- Modify: `backend/src/grimoire/decisions.py` (a new section after "trigger
  evaluation": the constants, `Escalation`, `ResolverReply`;
  `Decision.escalations`; `CallRecord`'s docstring names `HOP_ESCALATION`)
- Test: `backend/tests/test_decisions.py`

**Interfaces:**
- Produces, in `decisions.py`:
  ```python
  #: `CallRecord.hop` and a ledger row's `hop` for a call an escalation hop
  #: sent (01d §5.5, §5.7); "" for a call of the chain itself.
  HOP_ESCALATION = "escalation"
  #: What became of one triggered item (01d §5.5).
  ESCALATION_OUTCOMES: tuple[str, ...] = ("answered", "failed", "skipped")
  #: `Escalation.detail` words this module names; a failed call's detail is
  #: its error's "kind: detail", and an unresolved hop's is the escalator's
  #: own sentence.
  SKIPPED_CAP = "cap"
  SKIPPED_SAME_MODEL = "same_model"
  SKIPPED_INCAPABLE = "incapable"
  FAILED_DECLINED = "declined"
  FAILED_NO_RESULT = "no_result"

  @dataclass(frozen=True)
  class Escalation:
      index: int
      trigger: str                    # one of TRIGGERS
      margin: float | None            # set exactly for low_margin, as Trigger
      before: ItemResult
      outcome: str                    # one of ESCALATION_OUTCOMES
      detail: str = ""
      served: tuple[()] | tuple[str, str, str] = ()
      # __post_init__: trigger in TRIGGERS, outcome in ESCALATION_OUTCOMES,
      # margin set iff low_margin, served () or three strings (ValueError).

  @dataclass(frozen=True)
  class ResolverReply:
      results: tuple[ItemResult | None, ...]
      rows: tuple[dict[str, Any], ...] = ()

  @dataclass(frozen=True)
  class Decision:
      ...                             # unchanged fields
      calls: tuple[CallRecord, ...] = ()
      #: One per triggered item, in trigger priority order (01d §5.5): what
      #: the hop did with it. Empty when nothing escalated.
      escalations: tuple[Escalation, ...] = ()
  ```
  `Escalation`'s docstring states the detail vocabulary: `cap`,
  `same_model`, `incapable`, `dead_connection`, `unresolved: <kind>`, the
  escalator's sentence (skipped); `declined`,
  `no_result`, `unreadable` or `unreadable: <detail>`, `"kind: detail"`
  (failed); `""` (answered) -- and that `detail` can carry a provider's own
  text (a failed call's `"kind: detail"`), so it must never be persisted
  (gate 9). `ResolverReply`'s states the §5.2 contract (one
  result per item handed over, `backend` in `BACKENDS`, `served` the final
  target's; `rows` each carrying `hop: "escalation"`).
- Consumed by: Tasks 4-6, 01d-S4 (the seam returns what the thunk awaits),
  01a's runner (`Decision.calls`), 02 (`Decision.escalations[*].outcome`,
  spec §5.5 / M7).

- [ ] **Step 1: Failing tests** (`test_decisions.py`, after the `Decision`
  tests):
  - `test_a_decision_records_no_escalation_by_default`:
    `Decision(items=(), backend="").escalations == ()`, and two `Decision`s
    equal before stay equal (escalations default on both).
  - `test_an_escalation_checks_its_words`: a valid `Escalation` for each
    outcome builds; an unknown `trigger`, an unknown `outcome`, a
    `low_margin` without a margin, an `abstained` with one, and a two-part
    `served` each raise `ValueError`.
  - `test_a_resolver_reply_defaults_to_no_rows`:
    `ResolverReply((None,)).rows == ()`.
  - `test_the_hop_word_is_the_one_the_eval_reads`: `decisions.HOP_ESCALATION
    == "escalation"` (the value `evals/runner.item_records` compares and
    `evals/costs.hop_of` groups by).
- [ ] **Step 2: Run** →
  `cd /home/user/wt/01d-s3/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_decisions.py`
  → the new tests fail.
- [ ] **Step 3: Implement** the constants, the two dataclasses (with
  `__post_init__`, reusing `TRIGGERS` and the `ItemResult` served check's
  shape), and the `Decision` field and its `#:` comment. Extend the module
  docstring's last list sentence ("what hands an answered item to a second
  opinion: ...") with "and what one did with it (`Escalation`,
  `ResolverReply`)".
- [ ] **Step 4: Run** the same command → all pass.

### Task 2: the ledger's `hop` field

**Files:**
- Modify: `backend/src/grimoire/wire.py` (`Account.hop`)
- Modify: `backend/src/grimoire/llm_usage.py` (`ACCOUNT_FIELDS` gains
  `"hop"`)
- Modify: `backend/src/grimoire/store/usage.py` (`record(..., hop="")`
  writes it when set, beside `decision_mode`; `Meter.SERVED` gains `"hop"`;
  `record`'s "What served the call" docstring names it)
- Test: `backend/tests/test_usage_fields.py`

**Interfaces:**
- `wire.Account` gains `hop: str = ""` LAST (after `decision_mode`, so the
  fields stay `ACCOUNT_FIELDS` in order); its docstring: "`hop` is
  `escalation` on an attempt an escalation hop sends (01d §5.7), "" on every
  other."
- `llm_usage.ACCOUNT_FIELDS = ("operation", "role", "billing",
  "decision_mode", "hop")`.
- `usage.record(..., decision_mode="", hop="", tokens_estimated=False)`:
  `("hop", hop)` joins the "written only when it has a value" loop.
- `usage.Meter.SERVED = (..., "decision_mode", "hop")`.
- Consumed by: Task 4 (the hop target's account), 01a's `evals/costs.hop_of`.

- [ ] **Step 1: Failing tests** (`test_usage_fields.py`):
  - Update `test_the_account_fields_are_the_ledgers` to the five-field tuple
    (`test_wire.py`'s twin compares the dataclass to `ACCOUNT_FIELDS` and
    needs no edit).
  - `test_a_hop_is_filed_only_when_set`: `usage.record(task="scene-break",
    hop="escalation", ...)` returns a row with `hop == "escalation"`;
    without it, no `hop` key.
  - `test_a_meter_files_the_hop_its_target_carried`: a holder stamped
    through `llm._stamp(holder, target.with_account(hop="escalation",
    operation="decide"), 1)` inside `store.usage.meter("scene-break")` files a
    row with `hop == "escalation"`; the same without the hop files none.
  - `test_every_account_field_reaches_the_row`:
    `set(llm_usage.ACCOUNT_FIELDS) <= set(store.usage.Meter.SERVED)` and each
    is a parameter of `usage.record` (so the next account field cannot be
    stamped and silently dropped at `Meter.done`).
- [ ] **Step 2: Run** →
  `cd /home/user/wt/01d-s3/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_usage_fields.py tests/test_wire.py`
  → the new and updated tests fail.
- [ ] **Step 3: Implement** the four edits.
- [ ] **Step 4: Run** the same command plus `tests/test_adapter_registry.py
  tests/test_adapter_wire_golden.py tests/test_decide_chain_golden.py` → all
  pass (an empty `hop` files nothing, so no golden moves).

### Task 3: the hop mark on `_Call`, its records and its captures

**Files:**
- Modify: `backend/src/grimoire/inference.py` (`_Call.hop`, `_record`,
  `_outcome`)
- Test: `backend/tests/test_decide_escalation.py` (new; the first tests)

**Interfaces:**
- `_Call` gains, after `positions`: `#: "escalation" on a call an escalation
  hop makes (01d §5.5), "" on the chain's own.` `hop: str = ""`.
- `_record(...)` passes `hop=call.hop` to `decisions.CallRecord`.
- `_outcome(...)`: when `call.hop`, the record gains `"hop": call.hop`
  after `stage` and `at`; when "", nothing (the golden's captures do not
  move).
- Consumed by: Task 4.

- [ ] **Step 1: Failing tests** (`test_decide_escalation.py`, module
  docstring naming 01d-S3 and the placeholder rule; fixtures `client` (a
  `TestClient` over `tmp_path`, `GRIMOIRE_HOME` set, as
  `test_inference_decide_native.py` builds it) and `_instant_backoff`):
  - `test_a_hop_call_records_and_captures_its_mark`: drive
    `inference._BACKENDS[inference.STRUCTURED]` with a hand-built
    `inference._Call(..., chain=<resolved.chain alone>, retries=0, stage=1,
    positions=(3,), hop="escalation", capture=<recording capture>)` over one
    item and a `FakeLLM` answering it; the one `CallRecord` has `hop ==
    "escalation"`, `stage == 1`, `items == (3,)`; the capture's outcome has
    `"hop": "escalation"`, `"stage": 1`, `"at": [3]`.
  - `test_a_chain_call_carries_no_hop`: the same with `hop=""` → the record's
    `hop == ""` and the outcome has no `"hop"` key.
- [ ] **Step 2: Run** →
  `cd /home/user/wt/01d-s3/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_decide_escalation.py`
  → fail (`_Call` has no `hop`).
- [ ] **Step 3: Implement** the field and the two readers.
- [ ] **Step 4: Run** the same plus `tests/test_decide_chain_golden.py
  tests/test_decision_capture.py` → all pass.

### Task 4: `decide(escalation=)` — the checks, the role hop, the merge

**Files:**
- Modify: `backend/src/grimoire/inference.py` (the `Resolver` / `Escalator`
  aliases beside `Capture`; `decide`'s signature, docstring and body; the
  new helpers after `decide`; a short paragraph in the module docstring
  after the "Four rules" list)
- Test: `backend/tests/test_decide_escalation.py`

**Interfaces:**
- Produces, in `inference.py`:
  ```python
  #: A caller-supplied next resolver (01d §5.2, `routing.CALLER`): the items
  #: handed over and their triggers, in the order taken; its reply.
  Resolver = Callable[[tuple[decisions.Item, ...], tuple[decisions.Trigger, ...]],
                      Awaitable[decisions.ResolverReply]]
  #: What `decide(escalation=)` awaits once something triggered: the
  #: escalation role's resolution (or a `Resolver`, for a CALLER policy), or
  #: None and the sentence why there is none. Soft: it never raises a 409
  #: (01d-S4's `routes.common.escalation_inference` is the seam behind it).
  Escalator = Callable[[], Awaitable[tuple[ResolvedInference | Resolver | None, str]]]

  async def decide(task, items, *, client, resolved, explain="", campaign="",
                   scene="", post=None, round_id="", capture=None, around=None,
                   escalation: Escalator | None = None) -> decisions.Decision
  ```
  Private helpers (each mccabe ≤ 10):
  - `_policy_for(task, items, escalation) -> routing.TaskPolicy | None` —
    the pre-meter checks of Global Constraints; None when the task does not
    escalate. Reads `store.routing.policy(task)`.
  - `_hop_stage(target: ResolvedInference) -> Stage | None` — None when
    `target.chain is None` or `target.attempts[0].decision_mode` is not in
    `decisions.BACKENDS`; else `Stage(mode, wire.Chain(target.chain.primary
    .with_account(hop=decisions.HOP_ESCALATION)), 0)`.
  - `_take(found, items, most, mode) -> tuple[tuple[Trigger, ...],
    tuple[Trigger, ...]]` — `(taken, capped)`: the first `most`; on
    `STRUCTURED`, only the prefix `decisions.chunks` puts in its first chunk.
  - `_merge(before, hop, question, reads_declines) -> tuple[ItemResult |
    None, str]` — the merged result, or None and the failed detail
    (`declined`; `unreadable` or `unreadable: <detail>`). Merged:
    `replace(hop, answers={qid: hop.answers[qid] if qid == question or
    hop.answers[qid].answer is not None else base_answer for qid,
    base_answer in before.answers.items()})`, in the base's question order.
  - `_role_hop(call, items, base, found, policy, target)` and, in Task 5,
    `_caller_hop(...)` — each returns
    `(final_items, escalations, rows, records)`.
  - `_escalated(base, final_items, escalations, rows, records) ->
    decisions.Decision`.
  - `_escalate(call, items, base, found, policy, escalation, chain)` —
    awaits the thunk once. A thunk that raises any `Exception` (never a
    `CancelledError`) is every trigger `skipped` with detail
    `unresolved: <kind>` (the `LLMError` kind, else the exception's type
    name; never its text), so the paid base answers are not lost (gate 8).
    `(None, why)` is then handled FIRST, for both policy kinds: every
    trigger `skipped` with `why` (gate 1). Only a non-None value meets
    §5.2's `TypeError`/`ValueError`. Then it dispatches.
- The flow in `decide` (after today's checks):
  ```python
  policy = _policy_for(task, items, escalation)        # ValueError: no meter yet
  chain = stages(resolved)
  ...                                                  # today's empty-chain check
  base = await run_stages(task, items, chain, ...)     # unchanged; may raise
  if policy is None or escalation is None:
      return base
  found = decisions.triggers(base.items, question=policy.question,
                             escalate_on=policy.escalate_on,
                             margins=dict(policy.margins),
                             answers=policy.escalate_answers)
  if not found:
      return base
  call = _Call(task=task, client=client, explain=explain, campaign=campaign,
               scene=scene, post=post, round_id=round_id, capture=capture,
               around=around, chain=chain[0].chain, retries=0,
               stage=len(chain), hop=decisions.HOP_ESCALATION)
  return await _escalate(call, tuple(items), base, found, policy, escalation)
  ```
  Role hop (`_role_hop`), per §5.3: `_hop_stage` None → every trigger
  `skipped`, `incapable`; the hop primary's `provider_id` on a base stage's
  connection (`_same_connection` against each base stage's attempts) while
  `base.errors` holds a `_connection_wide` error → every trigger `skipped`,
  `dead_connection` (gate 7: the base already met a failure that call would
  meet); else each trigger whose `before.served[1:] ==
  (primary.provider_id, primary.model)` is `skipped`, `same_model`; the rest
  go through `_take`, the capped ones are `skipped`, `cap`; the taken ones
  are sent as `_BACKENDS[stage.mode](tuple(items[t.index] for t in taken),
  replace(call, chain=stage.chain, positions=tuple(t.index for t in
  taken)))`. Each taken item: no result (its unit in `got.failed`) →
  `failed`, `"kind: detail"` of that unit's error; else `_merge` → answered
  (served = the merged result's) or failed (served = `_server(primary)`).
  `got.results` and `got.failed` are by SUBSET position `p` (the hop's own
  item order), mapped back to the batch through `taken[p].index`; only
  `got.calls`, built through `call.positions`, are already in batch
  indices (gate 6; said in a comment at the mapping). The hop's `got.rows`,
  `got.calls` and `got.served` are returned as they are.
- Consumed by: Task 5, Task 6, 01d-S4 (passes the thunk), 02/10/11/12 (the
  switching changes).

- [ ] **Step 1: Failing tests** (`test_decide_escalation.py`). Helpers, in
  the suite (none is an LLM fake): `_policy(**fields)` →
  `routing.TaskPolicy(escalate_to="primary", question="over", **fields)`;
  `_with(monkeypatch, policy)` → `monkeypatch.setitem(store.routing
  .TASK_POLICY, "scene-break", policy)`; `_escalator(role="primary")` → an
  async thunk returning `(inf.resolve("scene-break", operation="decide",
  role=role), "")` and counting its calls; `_native_base(client)` →
  `fx.decide_only(client, fallback=False)` (Decision natively on
  `openrouter`/`vendor/decider`, Primary structured on
  `openrouter`/`vendor/active`), resolved; `_p(prob)` →
  `ItemResult({"over": Answer(True, probability=prob)})`. Thresholds use the
  kind read off the resolution (`resolved.chain.primary.kind`), never a
  literal adapter name. Tests:
  - *Call site and policy* — `test_no_policy_returns_the_base_decision`:
    `escalation=None`, a native base with a low-margin item → the result
    `is` the `Decision` `run_stages` returned (monkeypatch `run_stages` with a
    wrapper that records its return), `escalations == ()`, and
    `fake.calls == 0`.
    `test_a_policy_without_an_escalator_is_refused_before_any_meter` and
    `test_an_escalator_without_a_policy_is_refused_before_any_meter`:
    `ValueError`; `fake.native_requests == []`, `fake.calls == 0`, no ledger
    row. `test_an_item_without_the_deciding_question_is_refused_first`:
    an item asking only `"tone"` → `ValueError`, nothing sent.
    `test_a_policy_with_an_unknown_trigger_is_refused_first`: a policy
    patched with `escalate_on=("bored",)` → `ValueError`, nothing sent.
  - *Nothing to hand over* — `test_no_trigger_returns_the_base_and_never_resolves`:
    a policy, an escalator, every item at P = 0.95 → result `is` the base
    decision; the escalator was never awaited.
    `test_a_failed_base_raises_and_never_resolves`: the base's one item
    fails (`LLMError("bad_response")` scripted) → that error is raised and
    the escalator was never awaited.
  - *The role hop answers* — `test_a_low_margin_item_is_handed_to_the_primary`:
    two items, P = 0.55 and P = 0.95, `escalate_on=("low_margin",)`,
    `margins=((kind, 0.2),)`; the structured hop (`FakeLLM` turn
    `decision_reply({"over": False})`) answers item 0 → item 0's `over` is
    `Answer(False)`, `backend == "structured"`, `served == (primary kind,
    "openrouter", "vendor/active")`; item 1 is the base's object;
    `escalations == (Escalation(0, "low_margin", ~0.1, before=base item 0,
    outcome="answered", served=...),)`; `Decision.served` holds the native
    pair then `("openrouter", "vendor/active")`; `provider == model ==
    backend == ""`; `errors == ()`; `fake.retries == [0]` and the hop
    request's chain has no fallback.
    `test_a_hop_sends_the_escalation_resolution_s_own_target`: the hop
    request's target equals the escalation resolution's
    `chain.primary` with `hop="escalation"` and `decision_mode="structured"`
    laid over its account (its sampling included — 01d-S4 makes that the
    role's own preset), and differs from the base's.
    `test_a_native_hop_answers_on_its_own_endpoint`: the Primary role put on
    a second decisions-only model (`vendor/second`, catalog row `outputs:
    ["decisions"]`) → the hop is a native request with `retries == 0`, the
    item's `served` names `vendor/second`.
  - *Skips* — `test_the_same_model_is_skipped_and_costs_no_call`: Decision
    role and Primary role on the same `openrouter`/`vendor/active`
    (structured), a choice `allow_none` question answered null (abstained),
    `escalate_on=("abstained",)` → `skipped`, `same_model`; `fake.calls ==
    1` (the base only); items unchanged. `test_another_model_on_the_same_provider_is_allowed`
    is the first answered test's shape (both `openrouter`), asserted
    explicitly. `test_an_unresolved_hop_skips_every_trigger_with_its_sentence`:
    the thunk returns `(None, "Saltmarch has no key.")` → every trigger
    `skipped` with that detail, no hop call, no new row.
    `test_a_hop_that_can_do_neither_is_skipped_incapable`: the Primary role
    put on `fx.NEITHER`'s model (`decision_mode == ""`) → `skipped`,
    `incapable`.
  - *The cap* — `test_the_cap_takes_refusals_then_abstentions_then_the_lowest_margins`:
    ten native items scripted as two `refused`, three `abstained`, five low
    margins (0.52, 0.51, 0.58, 0.55, 0.53), `escalate_max=8`, all three
    triggers → the hop's one structured call carries the two refusals, the
    three abstentions and the three lowest margins (by batch index, read off
    its `CallRecord.items` in that order); the two highest margins are
    `skipped`, `cap`. `test_a_structured_hop_takes_only_its_first_chunk`:
    eight native items each a `Choice` of 200 options (`allow_none`),
    scripted abstained → the hop is exactly one `complete` call carrying
    `len(decisions.chunks(those items)[0][1])` items (computed in the test),
    the rest `skipped`, `cap`.
  - *The merge* — `test_a_hop_decline_on_a_task_that_does_not_read_one_fails`:
    the hop answers the low-margin item null on an `allow_none` choice
    (`"pick"` as the policy's question) → `failed`, `declined`; the item is
    the base's object. `test_a_hop_decline_replaces_on_a_reads_declines_task`:
    the same with `reads_declines=True` → `answered`, the item's answer is
    the abstention. `test_a_garbled_hop_fails_and_the_original_stands`: the
    hop's reply is not JSON → `failed`, `unreadable: no_object`.
    `test_the_merge_keeps_a_question_the_hop_garbled`: items asking
    `decision` (a choice) and `evidence_scene` (a choice); the base answers
    both, the hop answers `decision` and, parametrised, leaves
    `evidence_scene` out of its reply object or names an option the item
    does not offer (each `unreadable`, and each `was_read`) → the merged
    item's `decision` is the hop's and `evidence_scene` is the base's
    `Answer` object. A third case where the hop gives `evidence_scene` a
    value → the hop's.
    `test_a_hop_decline_never_turns_a_read_batch_into_a_failure` (the
    review's S1 counterexample): item A read at low margin, item B failed
    (`LLMError` scripted); the hop abstains on A on a task without
    `reads_declines` → A stands, and `routes.common._decide_error(got,
    "pick")` is None, as it is for the base decision alone.
  - *No second hop* — `test_an_escalated_answer_is_never_escalated_again`:
    a native hop (`vendor/second`) answering at P = 0.51 → one hop request,
    one `Escalation`, outcome `answered`.
- [ ] **Step 2: Run** →
  `cd /home/user/wt/01d-s3/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_decide_escalation.py`
  → fail (`decide` takes no `escalation`).
- [ ] **Step 3: Implement** the aliases, the checks, the role hop, the merge
  and the `Decision`, as above. `decide`'s docstring gains: "`escalation`
  is handed in exactly when the task's policy escalates
  (`routing.policy(task).escalate_to`; a mismatch is a `ValueError` before
  any meter opens), and is awaited at most once, after the chain, only when
  an item triggered (`decisions.triggers`): the items it hands over are
  answered once more by the escalation role's primary alone, or by the
  `Resolver` it returns, and recorded in `Decision.escalations`; see
  `_escalate`." The module docstring gains one paragraph after the "Four
  rules": the hop is not a stage (§3's table, in two sentences).
- [ ] **Step 4: Run** the same → all pass; then
  `tests/test_inference_decide.py tests/test_inference_decide_native.py
  tests/test_decide_chain_golden.py` → unchanged and passing.

### Task 5: the caller `Resolver`

**Files:**
- Modify: `backend/src/grimoire/inference.py` (`_caller_hop`,
  `_checked_reply`; `_escalate`'s dispatch)
- Test: `backend/tests/test_decide_escalation.py`

**Interfaces:**
- `_escalate`: for `policy.escalate_to == routing.CALLER`, what the thunk
  returned must be callable and not a `ResolvedInference` (else
  `TypeError`); for a role policy, a `ResolvedInference` or None (else
  `TypeError`), and a resolution's `task`/`operation` must be `call.task` /
  `"decide"` (else `ValueError`, as `decide` checks `resolved`).
- `_caller_hop`: no `same_model` check (§5.2); `taken, capped =
  found[:policy.escalate_max], found[policy.escalate_max:]`; `reply = await
  resolver(tuple(items[t.index] for t in taken), taken)`; an `LLMError` →
  every taken item `failed` with its `"kind: detail"`, no rows;
  `CancelledError` and anything else pass through.
- `_checked_reply(reply, taken, question) -> decisions.ResolverReply`
  (`ValueError` unless): a `ResolverReply`; one result per taken item; each
  result None or an `ItemResult` with `backend in decisions.BACKENDS` whose
  question ids are exactly the item's (gate 2: so `_merge` can never
  `KeyError`); each row a `dict` with `row.get("hop") ==
  HOP_ESCALATION`.
- Its rows join `Decision.usage`; each becomes a `CallRecord(stage=call.stage,
  mode=row["decision_mode"] if that is in BACKENDS else STRUCTURED,
  items=tuple(t.index for t in taken), row=row, error_kind=row["error"] when
  `row.get("status") == "error"` else "", hop=HOP_ESCALATION)`. A None
  result is `failed`, `no_result`; a result goes through `_merge`, and the
  `served[1:]` of every non-None result (stamped) joins `Decision.served`,
  as every answering hop call's server does.

- [ ] **Step 1: Failing tests** (a `routing.TaskPolicy(escalate_to=
  routing.CALLER, question="over", escalate_on=("low_margin",), margins=...)`
  patched in; the resolver is an async function in the test, not an LLM
  fake):
  - `test_a_caller_resolver_answers_and_its_rows_and_server_join_the_decision`:
    it returns `ResolverReply((ItemResult({"over": Answer(False)},
    backend="structured", served=("openrouter", "openrouter",
    "vendor/tool")),), rows=({"task": "scene-break", "hop": "escalation",
    "decision_mode": "structured"},))` → item replaced; the row is the last
    of `Decision.usage`; `Decision.calls[-1].hop == "escalation"`;
    `("openrouter", "vendor/tool")` in `Decision.served`; the resolver got
    the item and its `Trigger`.
  - `test_a_caller_result_outside_the_backends_is_refused`: `ItemResult`
    with `backend=""` → `ValueError`. `test_a_caller_row_without_the_hop_is_refused`
    → `ValueError`. `test_a_caller_reply_of_the_wrong_length_is_refused` →
    `ValueError`.
  - `test_a_caller_resolver_that_raises_leaves_every_original`: it raises
    `LLMError("timeout", "slow")` → each taken item `failed`, `"timeout:
    slow"`; items, `usage`, `calls` and `errors` are the base's.
  - `test_a_caller_none_result_leaves_the_original`: `ResolverReply((None,))`
    → `failed`, `no_result`.
  - `test_a_resolver_for_a_role_policy_is_a_type_error` and
    `test_a_resolution_for_a_caller_policy_is_a_type_error`; and
    `test_a_resolution_of_another_task_is_refused` (`inf.resolve("voice-drift",
    operation="decide", role="primary")` handed back for `scene-break`) →
    `ValueError`, and no hop call was made.
- [ ] **Step 2: Run** the suite → the new tests fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the suite → all pass.

### Task 6: failures, budgets, cancellation, the ledger and the capture

**Files:**
- Test: `backend/tests/test_decide_escalation.py` (no production change is
  expected; a failure here is a Task 3-5 bug, fixed there)

- [ ] **Step 1: Tests**:
  - `test_a_rate_limited_hop_leaves_the_original_and_the_errors`: the hop
    `FakeLLM(..., error=LLMError("rate_limit", "slow down", status=429))`
    (raised on `stream` only, so the native base answers) → `failed`,
    detail starting `"rate_limit: "`; `got.errors == base errors` (empty);
    `_decide_error(got, "over")` is None; the hop's `error` row is in
    `Decision.usage` and its `CallRecord` has `error_kind == "rate_limit"`
    and `hop == "escalation"`.
  - `test_a_hop_the_budget_refuses_is_failed_and_files_no_error_row`: an
    `around` (a plain coroutine helper in the test) that awaits a native
    call and, for a structured one, closes it and raises
    `routes.scenes.BudgetRefused("timeout", "budget spent")` → `failed`,
    `"timeout: budget spent"`; no row was filed for the hop (nothing was
    sent) and no `errors` entry was recorded for it (`store.errors` has no
    `scene-break` error row); base items stand.
  - `test_hop_rows_carry_the_hop_and_the_role`: after an answered role hop,
    the ledger (`store.usage.calls(days=1)`) has the base row(s) with no
    `hop` key and one hop row with `hop == "escalation"`, `role ==
    "primary"`, `operation == "decide"`, `decision_mode == "structured"`,
    task `scene-break`, and the campaign / scene / post / round the call
    passed.
  - `test_hop_calls_reach_the_capture_marked`: a recording capture → the
    base's captures carry no `"hop"`; the hop's carries `"hop":
    "escalation"`, `"stage": 1` (one base stage) and `"at"` the batch
    indices it took. `test_a_capture_that_raises_fails_nothing`: a capture
    that raises on the hop's call → the decision is as with no capture.
  - `test_a_cancel_during_the_hop_propagates_and_files_aborted`: the hop
    `FakeLLM(..., stall=True)`; run `decide` as a task, wait until
    `fake.calls == 1` (the hop has stamped its holder), cancel → awaiting
    the task raises `asyncio.CancelledError`, and the hop's ledger row is
    `status == "aborted"`.
  - Gate additions: `test_a_none_escalator_under_a_caller_policy_skips_every_trigger`
    (gate 1); `test_a_caller_result_missing_a_question_is_refused` (gate 2);
    `test_served_names_a_hop_whose_answer_the_merge_rejected` (gate 3: a
    declined hop on a non-`reads_declines` task still puts its server in
    `Decision.served`, and `provider == ""`);
    `test_a_hop_on_a_dead_connection_is_skipped` (gate 7: a base whose second
    item failed `auth` on the openrouter Decision model, the Primary on
    openrouter too → every trigger `skipped`, `dead_connection`, no hop
    call); `test_an_escalator_that_raises_skips_every_trigger_by_kind` (gate
    8: a thunk raising `RuntimeError("secret words")` → `skipped`, detail
    `unresolved: RuntimeError`, the text nowhere in it; the base items
    stand).
- [ ] **Step 2: Run** the suite → all pass (if one fails, fix the cause in
  the task that owns it, never the test's expectation without saying so).

### Task 6b: `evals/runner.item_records` names the call that answered (gate 4)

**Files:**
- Modify: `evals/runner.py` (`item_records`, and a new `_answering_call`)
- Test: `backend/tests/test_evals.py` (which already imports `evals.runner`)

`item_records` picks an item's call as the last non-failed call carrying it,
so a hop whose answer the merge rejected (a decline, a garble) would be
named as the item's call, and its `stage` reported. A hop call is now
preferred only when that item's `Decision.escalations` outcome is
`answered`; otherwise the last non-failed CHAIN call (`hop == ""`) carrying
it, then any carrying it, as before. `escalated` is unchanged (any hop call
carried it).

- [ ] **Step 1: Failing test**
  `test_item_records_name_the_chain_call_when_the_hop_was_rejected`: a
  hand-built `Decision` with a chain call and a hop call over item 0, the
  hop's escalation `failed` → `call` is the chain call's index and `stage`
  its stage; with the escalation `answered` → the hop call's.
- [ ] **Step 2: Implement**, keeping `item_records` at mccabe ≤ 10 (a small
  `_answering_call(decision, index)` helper).
- [ ] **Step 3: Run** `tests/test_evals.py tests/test_eval_runfile.py` → pass.

### Task 7: CLAUDE.md, the gates, and the commit

**Files:**
- Modify: `CLAUDE.md` (two sentences, below)

- [ ] **Step 1: CLAUDE.md.**
  - In the decide paragraph, after "re-asking them elsewhere would be asking
    until something agreed.", add: "A policy-declared escalation is not a
    stage (`routing.TaskPolicy.escalate_to`, `inference.decide(escalation=)`):
    after the unchanged chain it hands an ANSWERED item, once, to a stronger
    resolver only on what the backend itself reported (`decisions.triggers`:
    a refusal, an abstention, a low margin), never to the model that
    answered it, and a hop that fails, garbles or declines leaves the
    original standing (`Decision.escalations`; `Decision.errors` is the
    chain's). Its calls file `hop: escalation`. No task enables it yet."
  - In the Costs paragraph "A row says what served it", after "`preset` and
    `billing`", add "(and `hop`, on a call an escalation hop sent)" — keeping
    the sentence's existing fields as they are.
  - Run the prose guards:
    `cd /home/user/wt/01d-s3/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_docs_guard.py tests/test_module_references.py`
    → PASS.
- [ ] **Step 2: Ratchets.** From `/home/user/wt/01d-s3`:
  `make check-lint check-mypy PY=/home/user/grimoire/backend/.venv/bin/python`
  and `/home/user/grimoire/backend/.venv/bin/python scripts/ratchet.py eslint`
  → at baseline, no baseline change (never `make baseline` or
  `check-eslint` here: both run `npm ci`).
- [ ] **Step 3: Targeted suites.**
  `cd backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_decide_escalation.py tests/test_decisions.py tests/test_decision_triggers.py tests/test_task_policy.py tests/test_inference_decide.py tests/test_inference_decide_native.py tests/test_decide_chain_golden.py tests/test_decision_capture.py tests/test_usage_fields.py tests/test_wire.py tests/test_adapter_registry.py tests/test_adapter_wire_golden.py tests/test_import_guard.py tests/test_usage_guard.py tests/test_routing_guard.py tests/test_operation_guard.py tests/test_eval_costs.py tests/test_docs_guard.py tests/test_module_references.py`
  → PASS.
- [ ] **Step 4: The whole backend once**:
  `make check-py WORKERS=2 PY=/home/user/grimoire/backend/.venv/bin/python`
  (the known root-only `test_atomic.py::test_a_read_only_record_is_not_silently_replaced`
  failure excepted; a timing-sensitive failure is re-run alone), then
  `make check-templates PY=/home/user/grimoire/backend/.venv/bin/python`.
  `git status` shows no change under `backend/tests/fixtures/`, `evals/` or
  `frontend/`.
- [ ] **Step 5: Commit** (one commit, never `frontend/node_modules`):

  ```text
  01d-S3: the escalation hop in decide

  decide(escalation=) hands an answered item whose backend reported a
  refusal, an abstention or a low margin, once, to the escalation role's
  primary alone (retries 0, one structured chunk, never the same model) or
  to a caller Resolver, merges per question, and records
  Decision.escalations. Hop calls file hop: escalation on their rows,
  records and captures. A call site and its policy that disagree raise
  before any meter; no task escalates yet.

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01DA4KKMFiW9cAipBiSS74NN
  ```

## Decisions taken in this plan (for the plan gate)

1. **The hop dispatches to the backend, not to a nested `run_stages`**
   (Architecture 2). Equivalent for a one-stage chain in every call, meter,
   retry, capture and settle; it keeps a wholly failed hop's rows and
   records (which §5.5 says join the decision, and which a raising
   `run_stages` would lose), writes batch indices into `CallRecord.items`
   and the capture's `at` without rewriting, and lets `run_stages` stay
   unedited (§3, §10). The spec's "except `LLMError` → each item failed"
   becomes "each item in a failed unit → failed with that unit's error",
   which is the same outcome per item.
2. ~~`Decision.served` gains only the servers of REPLACED items.~~
   **Rejected at the plan gate (finding 3).** `served` is the union, in
   first-answered order, of every server that answered a call, the hop's
   included, even when the merge rejected its answer (§5.5, the `Decision`
   docstring); `provider`/`model` stand only when one route answered every
   call.
3. **The thunk-shape errors are raised after the base, before any hop
   call.** §5.2 says "before any meter opens", but the thunk is only awaited
   once something triggered (also §5.2), so the base's meters have run by
   then; the check is first thing after the await. Only the
   `escalation`-given-iff-policy `ValueError` and the item/question and
   trigger-argument checks can be, and are, before every meter.
4. **Failed-detail words beyond §5.5's list**: `no_result` (a caller result
   of None) and `unreadable[: <detail>]` (a garbled hop answer), so every
   `failed` record says why. Skipped details are §5.5's.
5. **A caller `Resolver`'s rows must carry `hop: escalation`**, refused with
   `ValueError` otherwise, as a result outside `BACKENDS` is (§5.2's own
   rule). Both are caller bugs of the same kind: a row that does not say it
   was a hop would be counted as a base call by 01a's breakdown.
6. **The CLAUDE.md escalation sentence lands here**, with the hop (§8: "in
   the change that adds the hop"). The slice list puts "the sentence that an
   escalation is not a stage" under 01d-S4; S4 then only names its seam.
   Flagged for the coordinator.
7. **An item's other questions take the hop's answer only when it is a
   value**, not whenever it `was_read`. §5.3 says "keeps the base's answer
   unless the hop's answer to it `was_read`" and, in the same bullet, that
   "a hop that answers `decision` but garbles an evidence question therefore
   keeps the base's evidence scene" (also §9's per-question merge case). The
   two disagree in the code: `decisions.was_read` is True for an
   `unreadable` with no detail or `NOT_AN_OPTION` -- exactly what a garbled
   or missing question key parses to (`decisions._read`) -- so the literal
   rule would put the hop's garble over the base's evidence. This plan keeps
   the spec's stated outcome and its reason ("each question of an item
   stands alone"): a hop value replaces, a hop non-answer of any kind does
   not. Flagged for the coordinator as a candidate spec erratum.

## Where this meets 01c-S2 (in flight, `/home/user/wt/01c-s2`)

01c-S2 edits `Stage` (adds `isolated: bool = False`), `stages` (the
native-first stage), `run_stages` (`aside`, `_all_dead`), and the first
paragraph of the `inference.py` module docstring. This slice edits `_Call`,
`_record`, `_outcome`, `decide`, and adds helpers after `decide` and a
paragraph after the "Four rules" list. No hunk is shared; the merge is
textual at most. Semantically: the hop builds `Stage(mode, chain, 0)` (so
`isolated` takes its default and is never read: the hop does not pass
through `run_stages`), and it never calls `stages`, so native-first can
never apply to a hop (§5.3). 01c's `reports_distribution` forecasts the
BASE chain only; `samples` with `low_margin` is refused by
`test_task_policy.py` (01c-S2 lands that rule if second), so a sampled
distribution is never replaced by a hop's.

## Where this meets 01g-S3 (in flight, `/home/user/wt/01g-s3`)

01g-S3 adds `response_id`, `run_id`, `loop_turn` to `_Call` (after
`positions`), to `run_stages` and to `decide`, and opens every decide meter
through `_meter(call)`. Whichever of the two lands second: the hop's `_Call`
(built once in `decide`, Task 4) passes `response_id=response_id,
run_id=run_id, loop_turn=loop_turn`, so hop rows carry the same run
attribution through `_meter`, and a test asserts a hop row's `run_id`.
`usage.record` gains both slices' parameters (`hop` here, `run_id`,
`loop_turn`, `tool_calls` there): a textual merge in the signature and the
docstring. `hop` reaches the row through `Meter.SERVED`, so `Meter.done`'s
`record(...)` call is not edited here.

## Handoff to 01d-S4

- The thunk the seam backs is `inference.Escalator`: `lambda:
  run_in_threadpool(lambda: common.escalation_inference(<task>, cid))`,
  returning `(UsableInference | None, sentence)`. `decide` accepts any
  `ResolvedInference` subclass.
- The preset and `requires` checks are the seam's: `decide` sends whatever
  `chain.primary` the resolution carries, and skips `incapable` only for a
  primary with no decide backend (`decision_mode == ""`) or no chain.
- **A skipped hop makes no call, so it reaches no capture by itself.**
  §8 says a skipped hop is visible in the capture: S4's call sites note
  skips (and every escalation's outcome) through their capture scope
  (`decision_capture.Scope.note`) after `decide` returns. S3 exposes what
  that needs: `Decision.escalations`, one per triggered item, with
  `index`, `trigger`, `margin`, `outcome`, `detail` and `served`. The note
  must not persist a failed detail's provider text (keep only the part
  before the first `": "`).
- `test_operation_guard.py`'s new rule can read `routing.policy(task)
  .escalate_to` and the presence of `escalation=` at each
  `operations.decide` call; `decide` already enforces the same pairing at
  run time.

## Errata for the checklist (the coordinator logs these)

- **§5.3 merge contradiction** (Decision 7): "keeps the base's answer unless
  the hop's `was_read`" contradicts "a hop that ... garbles an evidence
  question keeps the base's evidence scene", since a garbled or missing key
  parses to an `unreadable` that `was_read`. Built: another question takes
  the hop's answer only when it is a value.
- **§5.5 detail words** (Decision 4, gate 7 and 8): beyond `cap`,
  `same_model`, `incapable`, `declined`, the soft sentence and `"kind:
  detail"`, this slice adds `no_result`, `unreadable[: <detail>]`,
  `dead_connection` and `unresolved: <kind>`.
- **CLAUDE.md placement** (Decision 6): the "an escalation is not a stage"
  sentence lands with the hop in S3 (§8), not S4 (slice list); S4 names its
  seam.
- **§5.2 `TypeError` timing** (Decision 3): it can only precede the hop's
  meters, never the base's.

## Handoff to other slices

- **01d-S5** (`evals/runner.escalator`) returns an `inference.Escalator`;
  `evals/runner.item_records` already marks an item `escalated` from
  `CallRecord.hop`, and `evals/costs.hop_of` groups the hop's rows.
- **02 / 10 / 11 / 12** read `Decision.escalations[*].outcome` for "did the
  second opinion answer" (spec §5.5, review M7). A task's switching change
  adds its `TASK_POLICY` entry, its call site's `escalation=`, its eval
  evidence, and — for a continuity task — the added time bound to
  CLAUDE.md's "up to twelve" (§6.2).

## Plan gate (substitute review, 2026-10-10)

An independent reviewer agent (substitute for `/codex:adversarial-review`
against the plan) found the plan sound in structure. Decisions 1, 3, 4 and
6 were accepted; Decision 7 was ruled the safe reading of a genuine spec
contradiction; Decision 2 was rejected. Each finding, and how it was folded:

| # | Finding | Folded |
|---|---|---|
| 1 | An escalator returning `(None, why)` under a `CALLER` policy would `TypeError` after the base was paid | `(None, why)` is handled first for both policy kinds: every trigger `skipped` with `why` (Task 4 `_escalate`; test under `CALLER`) |
| 2 | A caller result omitting a question would `KeyError` in `_merge` | `_checked_reply` requires each result's question ids to equal the item's (`ValueError`; test) |
| 3 | Decision 2 contradicts §5.5 and the `Decision` docstring | Rejected: `served` is the union of every answering call's server, hop included; `provider`/`model` only when one route answered (Review Focus, Task 4/5; test) |
| 4 | `evals/runner.item_records` would name a rejected hop as an item's answering call | Task 6b: a hop call is preferred only when the item's escalation is `answered` (test) |
| 5 | §8 says a skipped hop is visible in the capture, but a skip makes no call | Handoff to 01d-S4: call sites note escalations through the capture scope; S3 exposes `Decision.escalations` with outcome and detail |
| 6 | Say that `got.results`/`got.failed` are subset positions | Task 4, and a comment at the mapping |
| 7 | A role hop on a connection the base found dead would only meet the same failure | Skipped `dead_connection` when `base.errors` holds a `_connection_wide` error and the hop's provider is a base stage's (test) |
| 8 | A thunk that raises would lose the paid base answers | Converted to every trigger `skipped`, `unresolved: <kind>`, never the text (test) |
| 9 | Record the errata; `Escalation.detail` carries provider text | "Errata for the checklist" section; `Escalation`'s docstring says `detail` is never persisted |

Coordination: 01g-S3 is expected to integrate first; the hop's `_Call` is
built in one place in `decide` so its `response_id`/`run_id`/`loop_turn`
pass straight through on the rebase.

## Implementation and verification (speed mode)

Review gates after the plan gate: not run (speed mode); the Codex gates are
owed, and CI on the pushed branch is the full gate. Run instead: the
targeted suites of Task 7 Step 3 (plus `test_usage*.py`, `test_evals.py`,
`test_eval_runfile.py`), the speed-mode guards at `-n 3`, `make check-lint
check-mypy` and `make check-templates`; the whole backend suite was not
run. The new suite's key rules (the per-question merge, the dead-connection
skip, the same-model skip, the first-chunk cap and the served union) were
each mutated once and each mutation failed a test.

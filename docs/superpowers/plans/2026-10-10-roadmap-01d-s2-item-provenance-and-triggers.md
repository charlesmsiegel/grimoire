# 01d-S2: Per-item provenance and trigger evaluation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** every `ItemResult` a decide backend answers says which server
answered it, as `served = (kind, provider_id, model)`: stamped by
`inference._structured` from the answering holder's `llm.ATTEMPTED` target and
by `inference._native` from the stage's `named` target. `decisions` gains the
pure trigger evaluation that 01d-S3's hop, 01c's sampler record and 12's
investigation trigger read: `answer_key` (an answer's key, spelled as a
record spells it), `margin` (measured from the answered key, with
`math.fsum`, negative when the report ranks a rival above the answer), the
`TRIGGERS` vocabulary, the `Trigger` record and `triggers(...)` (refused,
abstained and low margin, with the exact and `:`-prefix answer filter and the
priority order). Nothing in production calls `triggers` yet, and no decision,
row, capture or golden moves. Delivers 01d-C2a (full).

**Architecture:** two halves that touch nothing in common.

1. *Provenance* is a new `ItemResult` field declared `compare=False`, so
   every existing equality assertion on an `ItemResult` (and on a `Decision`,
   whose `items` compare item by item) is unchanged. The backend that
   answered stamps it, at the same place it stamps `backend` today: the
   structured backend's `replace(result, backend=STRUCTURED)`
   (`inference.py:507-508`) and, for a native item, right after
   `decide_native` returns (`inference.py:627-628`). `native_lift` carries
   the lowered result's `served` through, as it carries `backend`. Nothing
   else reads or writes it: `decisions.outcome` (the capture's record),
   `decisions.render`, `Decision.served` (still `(provider, model)` pairs,
   `decisions.py:559`) and `run_stages` are untouched. That is what keeps
   `test_decide_chain_golden.py`, which records captures and
   `Decision.served`, byte-identical.
2. *Trigger evaluation* is pure and lives in `decisions`, which stays a
   gateway leaf (it imports nothing from the package but `schemas`;
   `test_decisions.py` holds that through `_sibling_imports`). It takes the
   policy's values as plain arguments (`question`, `escalate_on`, `margins`,
   `answers`), so it does not depend on 01d-S1's `routing.TaskPolicy`: 01d-S3
   reads `routing.policy(task)` and hands its fields in
   (`margins=dict(policy.margins)`). The one vocabulary both slices name,
   `TRIGGERS`, is defined here under the same name and with the same value
   as S1's `routing.TRIGGERS`, because `decisions` may not import `routing`;
   whichever of S1 and S2 lands second pins the two equal (Task 5, Step 4).

**Tech Stack:** Python 3.11 (`dataclasses.field(compare=False)`,
`math.fsum`, `collections.abc.Collection`), pytest, the shared fakes
(`FakeLLM`, `SequencedProvider`) and the inference fixtures
(`tests/inference_baseline.py`, `tests/inference_fixtures.py`).

**Spec refs:** `docs/superpowers/specs/2026-10-09-roadmap-01d-decision-escalation-policy-design.md`
§5.1 (C2a: `margin`, `Trigger`, `triggers`, the filter, `unreadable` and
`error`), §5.6 (per-item provenance), §6.1 (the margin formula), §7 (01d-C2a),
§8 (import guard: `decisions` stays a leaf), §9 (the trigger and margin
cases), Slices (01d-S2). Shared shape: `docs/superpowers/specs/2026-10-09-roadmap-01c-decision-distributions-sampling-design.md`
§5.1 and §6 (`draw` and the record read `ItemResult.served` as
`(kind, provider_id, model)`; 01c-S1 soft-needs this slice). Coordination:
`/home/user/wt/01d-s1/docs/superpowers/plans/2026-10-10-roadmap-01d-s1-task-policy.md`
(S1 defines `routing.TRIGGERS`, `MAX_MARGIN`, `DEFAULT_MARGIN`, `CALLER`,
`TaskPolicy`; none of them is defined here except `TRIGGERS`, see above).

**Open questions adopted:** §11 Q1 (raw or grouped margin for folded
options): the spec's recommendation, **raw**. `margin` reads the reported
keys as they are; two `existing:<id>` options splitting the mass, or the two
directions of a duplicate, is real ambiguity and lowers the margin. No
`group` argument is added. The other §11 questions are S3's, S4's or 01s's.

## Global Constraints

- **No behaviour change.** `served` is `compare=False`, nothing reads it, and
  nothing calls `triggers`. `test_decide_chain_golden.py` (which records
  `Decision.served`, the items' backends and answers, the rows and the
  captures, never `ItemResult.served`), `test_inference_decide.py`,
  `test_inference_decide_native.py`, `test_decision_capture.py` and the
  frozen campaign's `snapshot.json` pass untouched. No golden is
  regenerated.
- **`decisions` stays a leaf.** New imports are standard library only
  (`collections.abc.Collection`; `math` is already imported). No import of
  `routing`, `adapters` or `wire`: the adapter kind reaches `triggers` as the
  string in `served[0]`, and the thresholds as a plain mapping.
- **`served` is `()` or exactly three strings.** It is typed
  `tuple[()] | tuple[str, str, str]` with a `__post_init__` check. `()`
  means nothing stamped one: an `unanswered` item, what `parse` returns, an
  item an inline fake answered without stamping its holder, and any
  `ItemResult` built by hand.
- **The model in `served` is the target's `model`**, as `_served_by` reads it
  for `Decision.served` today (`inference.py:351-357`), never the holder's
  provider-reported `model` (a dated snapshot). So `served[1:]` and a
  `Decision.served` pair agree for the same call, which 01d-S3's same-model
  skip (§5.4) relies on.
- **The capture is unchanged.** `decisions.outcome` gains nothing: the
  golden records captures, and 01b's outcome already names each call's
  provider and model.
- **The margin never invents a report.** A structured answer (no
  `probability`, no `distribution`), a `Rank` or `MultiSelect` answer
  (`marginals` only, which are never a distribution, 01e-C4), a choice or
  score with no `distribution`, and a predicate with no `probability` all
  have margin `None`, and so never trigger `low_margin`.
- **A threshold is met within `MASS_TIE`.** `low_margin` fires when
  `margin < threshold - MASS_TIE` (`decisions.MASS_TIE = 1e-9`,
  `decisions.py:1558`). The spec writes `margin < margins[kind]`; this plan
  reads "does not fire at the threshold" (§9) as holding against float error
  too. Without it a predicate at P = 0.6 against the starting threshold 0.2
  computes `2 * 0.6 - 1 = 0.19999999999999996` and fires, though it sits
  exactly on the threshold the spec argues (§6.2). `MASS_TIE` exists for the
  same reason ("an error must not decide"), and its comment gains the
  margin. This is the one reading of the spec this plan adds; it is in the
  Review Focus.
- **Imports follow the import guard.** `inference.py` already binds
  `decisions` and `wire` as modules; the new helper is spelled
  `decisions.ItemResult` / `wire.Target` like its neighbours.
- **No privacy exposure.** Fixtures use the existing placeholder names
  (Mara, Winifred, Seraphine, `spare`, `vendor/...`), and no store data is
  read or described.
- Ratchets (`check-lint`, `check-mypy`, `check-eslint`) stay at baseline and
  no baseline file changes. No frontend file changes.

## Review Focus

- `served` is `compare=False` and every existing `ItemResult` / `Decision`
  equality in the suite still holds: run the decide suites unchanged.
- The structured stamp reads the holder of the call that ANSWERED the chunk
  (`llm.ATTEMPTED`): a fallback that answered, and a schema-refusal re-send,
  each name themselves. A failed chunk's items stay unstamped (they are
  `None` until a later stage or `unanswered` fills them).
- The native stamp overwrites whatever the adapter (or a fake) returned, so
  a scripted `ItemResult` cannot claim a server it was not answered by.
- `margin` is measured from the ANSWERED key (review B1): `choice: known`
  over `{known: 0.1, narrator_only: 0.9}` is `-0.8`, never `+0.8`.
- The `M < 1` branch subtracts `max(rival, 1 - M)`, and a partial single
  `0.6` gives `0.2` (§9). `math.fsum`, never `sum`, for `M`.
- `NONE_KEY` is a rival (an allow-none choice's reserved none is mass the
  answer did not get). A score's adjacent levels are rivals (§6.1, M6).
- The answer filter narrows `low_margin` only, never `refused` or
  `abstained`, and an entry ending in `:` matches by prefix.
- The `MASS_TIE` reading of "at the threshold" (Global Constraints).
- `triggers` raises `ValueError` for an unknown trigger, a threshold that is
  not a positive finite number, and a result that lacks `question`. These
  are caller bugs, and 01d-S3 must check `question` against the items before
  the chain sends anything (Task 5's handoff), so the raise never costs a
  paid call.

---

### Task 1: `ItemResult.served`, and `native_lift` keeps it

**Files:**
- Modify: `backend/src/grimoire/decisions.py` (`ItemResult`: the field, its
  docstring and its `__post_init__` check; `native_lift` passes
  `served=lowered.served`)
- Test: `backend/tests/test_decisions.py`

**Interfaces:**
- Produces, in `decisions.py`:
  ```python
  @dataclass(frozen=True)
  class ItemResult:
      answers: dict[str, Answer]
      rationale: str = ""
      backend: str = ""
      #: `(kind, provider_id, model)` of the server that answered this item
      #: -- its adapter kind, its provider id and the model it was sent --
      #: stamped by the backend that answered it, as `backend` is; `()` on
      #: an item nothing stamped (`unanswered`, `parse`, a result built by
      #: hand). Not compared, so two results that read alike are equal
      #: whoever answered them (01d §5.6). 01d's triggers read `served[0]`
      #: for the threshold and `served[1:]` for the same-model skip; 01c's
      #: draw record reads all three.
      served: tuple[()] | tuple[str, str, str] = field(default=(), compare=False)
  ```
  `__post_init__` adds: `served` is `()` or a tuple of exactly three `str`,
  else `ValueError("served is () or (kind, provider_id, model)")`.
- Consumed by: Task 2 (the stamps), Task 4 (`triggers` reads `served[0]`),
  01d-S3 (same-model skip, merge), 01c-S1 (`draw`).

- [ ] **Step 1: Failing tests** (`test_decisions.py`, beside
  `test_item_result_backend_defaults_empty_and_parse_leaves_it`):
  - `test_item_result_served_defaults_empty_and_parse_and_unanswered_leave_it`:
    `ItemResult({}).served == ()`; every result of `decisions.parse(REPLY,
    [_item()], explain=True)` and of `decisions.unanswered([_item()],
    "error")` has `served == ()`.
  - `test_item_result_served_is_three_strings_or_nothing`:
    `ItemResult({}, served=("openrouter", "openrouter", "vendor/active"))`
    keeps it; `("openrouter", "vendor/active")`, a four-tuple, and
    `("openrouter", "spare", None)` each raise `ValueError`.
  - `test_item_result_served_is_not_compared`:
    `ItemResult({"over": Answer(True)}, backend="native", served=("openrouter",
    "openrouter", "vendor/decider")) == ItemResult({"over": Answer(True)},
    backend="native")`, and two `decisions.Decision`s whose items differ
    only in `served` are equal.
  - `test_native_lift_keeps_the_lowered_results_server`: lower an item with
    a `Rank` (with `pointwise`) and a `Choice` through `native_form`, build
    the lowered `ItemResult` with `backend="native", served=("openai_compatible",
    "local", "local-model")`, and assert `native_lift(...).served` is that
    tuple (and `backend` still `"native"`).
- [ ] **Step 2: Run** →
  `cd /home/user/wt/01d-s2/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_decisions.py`
  → the four new tests fail.
- [ ] **Step 3: Implement** the field, the check and the `native_lift`
  keyword. Extend the class docstring's `backend` sentence with one on
  `served`.
- [ ] **Step 4: Run** the same command → all pass (the existing 119 included).

### Task 2: the backends stamp `served`

**Files:**
- Modify: `backend/src/grimoire/inference.py` (a helper `_server`; `_structured`
  and `_native` stamp it)
- Test: `backend/tests/test_inference_decide.py`,
  `backend/tests/test_inference_decide_native.py`

**Interfaces:**
- Produces, in `inference.py` (beside `_served_by`, `:351`):
  ```python
  def _server(target: object) -> tuple[()] | tuple[str, str, str]:
      """`(kind, provider id, model)` of `target`, the `ItemResult.served` of
      an item it answered; `()` for anything that is not a `wire.Target`
      (a holder an inline fake never stamped) and for a target any of whose
      three fields is not a `str`, so provenance can never raise inside
      `decide` (`ItemResult.__post_init__` would refuse it)."""
  ```
  - `_structured`: `answered = [replace(result, backend=STRUCTURED,
    served=_server(holder.get(llm.ATTEMPTED))) for result in
    decisions.parse(...)]` (only inside `if holder is not None`, as today).
  - `_native`: the awaited result is bound to a local, then
    `results[index] = replace(answered, served=_server(named))`, inside the
    `with m:` block where `results[index]` is set today
    (`inference.py:627-628`). `named` is the stage's primary as sent, the
    same target the capture names; `decide_native` stamps that same target
    (`llm._native_route` → `without_sampling`), so it is the server.
- Consumed by: Task 5's integration test, 01d-S3, 01c-S1.

- [ ] **Step 1: Failing tests** in `test_inference_decide.py` (reusing its
  `_store`, `_item`, `_decide`, `_resolved`, `SequencedProvider`, and
  `decision_reply`):
  - `test_each_structured_item_names_the_server_that_answered_it`: a
    `FakeLLM` answers one chunk of two items; each item's `served ==
    (resolved.chain.primary.kind, "openrouter", "vendor/active")`.
  - `test_a_chunk_the_fallback_answered_names_the_fallback`: the
    `test_chunks_answered_by_different_routes_name_every_one` script (nine
    items; primary answers chunk one, fails chunk two, `spare` answers it)
    → items 0-7 name `(kind, "openrouter", "vendor/active")`, item 8 names
    `(fallback.kind, "spare", "vendor/spare")`, read off
    `resolved.chain.primary` and `resolved.chain.fallback`.
  - `test_a_re_sent_chunk_names_the_attempt_that_answered`: the
    `test_a_refused_schema_with_nowhere_to_fall_is_retried_once_without_the_mode`
    script → the item names the primary (`kind`, `"openrouter"`,
    `"vendor/active"`).
  - `test_an_item_left_unanswered_names_no_server`: the
    `test_a_failed_chunk_beside_an_answered_one_is_marked_error` script →
    the `error` items have `served == ()`, the answered ones are stamped.
  - `test_a_server_is_three_strings_or_nothing` (pure, on `_server`): a
    `wire.Target` gives its three fields; `None`, `{}` and a target whose
    `model` is not a `str` (built with `dataclasses.replace`) give `()`.
  - `test_an_unstamped_holder_names_no_server`: an inline-free check with a
    `FakeLLM` subclass whose `_stamp` is a no-op (`staticmethod(lambda *a:
    None)`) → the answered item's `served == ()` and the decision still
    answers. (It must stay a subclass of the shared fake, per CLAUDE.md's
    "never write another inline fake".)
- [ ] **Step 2: Failing tests** in `test_inference_decide_native.py` (its
  `_native_resolution`, `_Endpoint`, `_yes`, `_decide`, `DECIDER`, `SPARE`):
  - `test_each_native_item_names_its_server`: `_native_resolution(client,
    fallback=False)`, two items answered natively → each `served ==
    (resolved.chain.primary.kind, *DECIDER)`.
  - `test_a_native_items_scripted_server_is_overwritten`: the `FakeLLM`
    decision entry is `ItemResult({"over": Answer(True)}, served=("anthropic",
    "elsewhere", "vendor/other"))` → the item names `(kind, *DECIDER)`.
  - `test_items_a_mixed_chain_answered_name_each_stage`: the `mixed` script
    of `test_decision_names_the_one_backend_and_route_that_answered` → item 0
    `(kind, *DECIDER)`, item 1 `(spare's kind, *SPARE)`, and
    `got.served == (DECIDER, SPARE)` still (the two-part decision field is
    untouched).
- [ ] **Step 3: Run** →
  `cd /home/user/wt/01d-s2/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_inference_decide.py tests/test_inference_decide_native.py`
  → the new tests fail.
- [ ] **Step 4: Implement** `_server` and the two stamps.
- [ ] **Step 5: Run** the same command, plus
  `tests/test_decide_chain_golden.py tests/test_decision_capture.py tests/test_usage_guard.py tests/test_routing_guard.py tests/test_operation_guard.py`
  → all pass, and the golden file is untouched (`git status` shows no
  change under `backend/tests/fixtures/`).

### Task 3: `answer_key` and `margin`

**Files:**
- Modify: `backend/src/grimoire/decisions.py` (a new section, `# ---
  trigger evaluation (01d-C2a) ---`, after `head_first`; `MASS_TIE`'s comment
  names the margin too; the module docstring's list of pure things gains
  "an answer's margin and the triggers that hand an item to a second
  opinion (`margin`, `triggers`)")
- Create: `backend/tests/test_decision_triggers.py`

**Interfaces:**
- Produces, in `decisions.py`:
  ```python
  def answer_key(answer: Answer) -> str | None:
      """The answer as a record spells its key (01c §6, 01d §5.1): a
      predicate's `"true"`/`"false"`, a score level's `str(level)`, a
      choice's option id, a joint pair's `key`; None for an answer of None,
      a `Ranking` and a selection, which no single key names. A bool is
      read before an int (`True` is an `int`)."""

  def margin(answer: Answer) -> float | None:
      """How far the answer's own report puts the ANSWERED key above its
      best rival (01d §6.1): negative when the report ranks another key
      higher, and always a `float`. None when the answer is None, or the
      report it needs is absent or empty -- a predicate's `probability`, a
      choice's, a score's or a joint's `distribution` (an empty mapping is
      no report: the `M < 1` branch would otherwise call it `-1`). `marginals` are never read: they are
      independent per-key probabilities, not a distribution (01e-C4).

      A predicate answered `a` with P(true) `p`: `2 * p(a) - 1`, with
      `p(a) = p` for True and `1 - p` for False. A distribution `w` with
      mass `M = math.fsum(w.values())` (correctly rounded, so the same on
      every supported interpreter), `w(a)` the answered key's weight (0
      when the report omits it) and `r` the largest other weight (0 when
      there is none; `NONE_KEY` is a rival): `(w(a) - r) / M` when
      `M >= 1`, and `w(a) - max(r, 1 - M)` when `M < 1` -- unreported mass
      could all be a rival's, so it may not be assumed otherwise. The keys
      are read raw, never regrouped (§11 Q1): mass split between two
      spellings of one meaning lowers the margin."""
  ```
- Consumed by: Task 4, 01d-S5 (`--escalation-sweep`), 01c-S1 (may reuse
  `answer_key` for the record's `answer` key).

- [ ] **Step 1: Failing tests** (`test_decision_triggers.py`, pure: no store,
  no client; a module docstring saying so and naming the spec sections):
  - `test_answer_key_spells_each_answer_as_a_record_does`: `Answer(True)` →
    `"true"`, `Answer(False)` → `"false"`, `Answer(2)` → `"2"`,
    `Answer("characters:mara")` → itself, `Answer(Pair("strike",
    "characters:mara"))` → `joint_key("strike", "characters:mara")`,
    `Answer(None, "abstained")`, `Answer(Ranking(tiers=(("a",),)))` and
    `Answer(("a",))` → None.
  - `test_margin_is_a_float`: `Answer(True, probability=1)` (a hand-built
    int) → `1.0` with `type(...) is float`; the same for a distribution of
    ints, `{"a": 1}` → `1.0`.
  - `test_margin_of_a_full_report_is_the_answers_lead_over_its_rival`:
    choice `"a"` over `{"a": 0.625, "b": 0.25, "c": 0.125}` → `0.375`
    (dyadic values, so `==` is exact).
  - `test_margin_over_a_mass_above_one_is_normalised`: `{"a": 0.75, "b":
    0.5}` → `0.25 / 1.25 == 0.2`.
  - `test_margin_of_a_partial_report_assumes_the_missing_mass_is_a_rival`:
    a single `{"a": 0.6}` → `pytest.approx(0.2)` (§9); `{"a": 0.5, "b":
    0.25}` → `0.5 - max(0.25, 0.25) == 0.25`; `{"a": 0.375, "b": 0.5}` →
    `0.375 - 0.5 == -0.125`.
  - `test_margin_is_negative_when_the_report_ranks_a_rival_first` (review
    B1): choice `"known"` built with `native_answer(Choice(... options known,
    narrator_only), chosen="known", distribution={"known": 0.1,
    "narrator_only": 0.9})` → `pytest.approx(-0.8)`.
  - `test_margin_of_an_answer_absent_from_its_report_is_negative`: chosen
    `"a"`, distribution `{"b": 1.0}` → `-1.0`; `{"b": 0.5}` (partial) →
    `-0.5`.
  - `test_margin_of_a_predicate_both_ways`: `native_answer(Predicate,
    probability=0.75)` (True) → `0.5`; `probability=0.25` (False) → `0.5`;
    `Answer(True, probability=0.25)` (a chosen True its report doubts) →
    `-0.5`.
  - `test_margin_counts_the_reserved_none_as_a_rival`: allow-none choice
    `"a"` over `{"a": 0.5, NONE_KEY: 0.5}` → abstained by tie, so build the
    answer directly: `Answer("a", distribution={"a": 0.5, NONE_KEY: 0.5})`
    → `0.0`.
  - `test_margin_of_a_score_counts_adjacent_levels_as_rivals` (M6): level 2
    over `{"1": 0.25, "2": 0.375, "3": 0.375}` → `0.0`.
  - `test_margin_of_a_joint_reads_the_pairs_key`: a `Joint` answer
    `Answer(Pair("strike", "characters:mara"), distribution={key: 0.75,
    other_key: 0.25})` → `0.5`.
  - `test_margin_reads_raw_keys_never_regrouped` (§11 Q1):
    `existing:a1` over `{"existing:a1": 0.5, "existing:b2": 0.5}` → `0.0`,
    though both spell "existing".
  - `test_no_margin_without_a_report`: `Answer(True)`, `Answer("a")`,
    `Answer(2)` (structured), `Answer("a", distribution={})` (an empty
    report is no report, never `-1`), `Answer(True, probability=None)`, `Answer(None, "abstained",
    distribution={"a": 0.5, "b": 0.5})`, `Answer(Ranking(...),
    marginals={...})`, `Answer(("a",), marginals={...})` → None.
  - No test pins `math.fsum` over `sum`: the two branches meet at `M = 1`
    (`(w - r) / 1 == w - max(r, 0)`), so the last-ulp difference between
    them cannot move a margin by more than float error, and a test could
    only re-derive the implementation. `fsum` is held by review (Review
    Focus) for the reason the spec gives: one rule on every interpreter.
- [ ] **Step 2: Run** →
  `cd /home/user/wt/01d-s2/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_decision_triggers.py`
  → fails (no `answer_key`, no `margin`).
- [ ] **Step 3: Implement** both functions as specified.
- [ ] **Step 4: Run** the same command → pass.

### Task 4: `TRIGGERS`, `Trigger` and `triggers`

**Files:**
- Modify: `backend/src/grimoire/decisions.py` (the same section;
  `from collections.abc import Callable, Collection, Mapping, Sequence`)
- Test: `backend/tests/test_decision_triggers.py`

**Interfaces:**
- Produces, in `decisions.py`:
  ```python
  #: What hands an answered item to a second opinion (01d §5.1). The same
  #: name and value as `routing.TRIGGERS` (01d-S1), which this leaf may not
  #: import; a test pins the two equal.
  TRIGGERS: tuple[str, ...] = ("low_margin", "abstained", "refused")

  #: `triggers`' order: a refusal first, then an abstention, then low
  #: margins by margin ascending.
  _PRIORITY = {"refused": 0, "abstained": 1, "low_margin": 2}

  @dataclass(frozen=True)
  class Trigger:
      """One item `triggers` found: its index in the batch, which trigger it
      met, and -- for `low_margin` only -- its margin."""
      index: int
      trigger: str
      margin: float | None = None
      # __post_init__: trigger in TRIGGERS; margin is a finite float exactly
      # when trigger == "low_margin"; else ValueError.

  def triggers(results: Sequence[ItemResult], *, question: str,
               escalate_on: Collection[str], margins: Mapping[str, float],
               answers: Collection[str] = ()) -> tuple[Trigger, ...]:
      """Each item whose `question` answer meets a trigger in `escalate_on`,
      in priority order: refused, then abstained, then low_margin by margin
      ascending; ties by index (01d §5.1, C2a). At most one per item, since
      an answer has one reason.

      - `refused` / `abstained`: the answer's `reason`.
      - `low_margin`: an answer that is not None, whose `margin` is not
        None, from an item whose server kind (`served[0]`) has an entry in
        `margins`, with `margin < margins[kind] - MASS_TIE`. A kind with no
        entry -- an unstamped item included -- never triggers: an unlisted
        endpoint keeps today's behaviour rather than a default threshold.
      - `answers`, when non-empty, narrows `low_margin` only: the answer's
        `answer_key` must equal an entry, or start with an entry that ends
        in ":". `refused` and `abstained` carry no answer to filter.
      - `unreadable` and `error` never trigger.

      Pure. A trigger not in `TRIGGERS`, a threshold that is not a positive
      finite number, or a result that has no `question` answer is a
      `ValueError`: each is a caller's mistake that would otherwise read as
      "nothing was unsure"."""
  ```
- Consumed by: 01d-S3 (`decide(escalation=)`), 01d-S5 (the sweep), 02, 10,
  11, 12.

- [ ] **Step 1: Failing tests** (`test_decision_triggers.py`; a helper
  `_result(answer, kind="openrouter")` builds `ItemResult({"q": answer},
  backend="native", served=(kind, "p", "m"))`, and `_ALL = TRIGGERS`):
  - `test_a_refusal_triggers` (§9: an OpenAI-shaped refusal): the answer is
    read from `{"answers": [{"type": "refusal", "name": "q"}]}` through
    `openai_compatible.decision_result` over a one-predicate item →
    `(Trigger(0, "refused"),)` with `escalate_on=("refused",)`; not with
    `escalate_on=("abstained", "low_margin")`.
  - `test_an_abstention_triggers_native_and_structured`: a native tie
    (`native_answer(Choice, distribution={"a": 0.5, "b": 0.5})`) and a
    structured null on an allow-none choice (`decisions.parse` of
    `{"0": {"answers": {"q": null}}}` over that item, stamped
    `backend="structured"`, `served=("openrouter", "p", "m")`) each give
    `Trigger(i, "abstained")`.
  - `test_low_margin_fires_just_under_the_threshold_and_not_at_it`:
    predicate P = 0.625 (margin 0.25) with `{"openrouter": 0.25}` → none;
    with `{"openrouter": 0.25 + 1e-6}` → `Trigger(0, "low_margin", 0.25)`.
  - `test_low_margin_at_a_threshold_float_error_missed_does_not_fire`:
    predicate P = 0.6 and P = 0.4 (margins `0.19999999999999996`) with
    `{"openrouter": 0.2}` → none; P = 0.59 → fires.
  - `test_low_margin_needs_a_threshold_for_the_items_kind`: the same low
    answer with `{"openai_compatible": 0.5}` → none; an unstamped item
    (`served=()`) with `{"openrouter": 0.5}` → none.
  - `test_unreadable_and_error_never_trigger`: `Answer(None, "unreadable")`,
    one with `NO_ITEM`, one with `NOT_AN_OPTION` and a `distribution`, and
    `Answer(None, "error")` → none, with every trigger listed and a 0.5
    threshold.
  - `test_a_structured_answer_never_triggers_low_margin`: `Answer("a")`
    stamped `structured` → none.
  - `test_the_answer_filter_narrows_low_margin` (review B1): the
    `known`/`narrator_only` answer (margin -0.8) with
    `answers=("known",)` → fires; `("narrator_only",)` → none; `()` →
    fires.
  - `test_an_answer_absent_from_its_report_triggers` (§9 B1): chosen `"a"`
    over `{"b": 1.0}` (margin -1.0) fires `low_margin` with a 0.2
    threshold, and with `answers=("a",)`.
  - Every `Trigger` carrying a margin is compared by its fields --
    `(t.index, t.trigger, t.margin) == (0, "low_margin", approx(0.1))` --
    since `Trigger(..., pytest.approx(...))` fails its own
    `__post_init__`.
  - `test_a_prefix_entry_matches_per_row_options`: `answers=("existing:",)`
    → a low-margin `existing:a1` fires, a low-margin `new` does not.
  - `test_the_filter_never_narrows_a_refusal_or_an_abstention`:
    `answers=("known",)` beside a refused and an abstained item → both fire.
  - `test_triggers_come_in_priority_order`: ten items -- low margins 0.1,
    -0.3 and 0.05 at indices 0, 1, 2, an abstention at 3, a refusal at 4,
    another refusal at 7, another abstention at 5, a confident answer at 6,
    a low margin -0.3 at 8 and an `error` at 9 -- with every trigger → order
    `[4, 7, 3, 5, 1, 8, 2, 0]` (refusals by index, abstentions by index,
    then margins ascending, the -0.3 tie by index).
  - `test_each_trigger_is_a_trigger_record`: every returned `Trigger` has
    `margin` set exactly for `low_margin`; `Trigger(0, "low_margin")`
    (no margin), `Trigger(0, "refused", 0.1)` and `Trigger(0, "doubt")`
    raise `ValueError`.
  - `test_triggers_refuses_a_callers_mistake`: an unknown trigger
    (`("low-margin",)`), a threshold of `0`, `-0.1`, `float("nan")`,
    `float("inf")` or `True`, and a result with no `question` answer each
    raise `ValueError`.
  - `test_no_trigger_listed_finds_nothing`: `escalate_on=()` → `()` over a
    batch holding every kind of unsure answer.
  - `test_triggers_is_pure`: the results passed in are unchanged (`==` and
    the same `served`) after the call.
  - `test_the_trigger_vocabulary_is_routings`: `assert routing.TRIGGERS ==
    decisions.TRIGGERS` (direct since the rebase onto 01d-S1; written with a
    `getattr` default while S1 had not landed) (`routing` is
    `grimoire.store.routing`). It holds in either landing order: before
    01d-S1 it is vacuous, and once S1 lands it pins the two spellings, with
    nothing for S1 to remember.
  - The leaf rule needs no new test: `test_decisions.py:664-669` already
    asserts `_sibling_imports("decisions") == {"schemas"}` over the whole
    module, so an import of `routing`, `adapters` or `wire` fails there.
- [ ] **Step 2: Run** the Task 3 command → the new tests fail.
- [ ] **Step 3: Implement** `TRIGGERS`, `_PRIORITY`, `Trigger` and
  `triggers`.
- [ ] **Step 4: Run** the Task 3 command plus `tests/test_decisions.py
  tests/test_import_guard.py` → pass.

### Task 5: end to end through `decide`, the docs line, and the gate

**Files:**
- Test: `backend/tests/test_inference_decide_native.py` (one integration
  test)
- Modify: `CLAUDE.md` (one sentence in the decide paragraph's "Who answered
  is only named when one did")

- [ ] **Step 1: Failing test** `test_triggers_read_the_kind_decide_stamped`
  (in `test_inference_decide_native.py`): `_native_resolution(client,
  fallback=False)`; a `FakeLLM([["unused"]], decisions=[ItemResult({"over":
  Answer(True, probability=0.55)}), ItemResult({"over": Answer(True,
  probability=0.95)})]` answers two items; then
  `decisions.triggers(got.items, question="over", escalate_on=("low_margin",),
  margins={resolved.chain.primary.kind: 0.2})` is
  one trigger whose `(index, trigger, margin)` is `(0, "low_margin",
  pytest.approx(0.1))`, and with `margins={"openai_compatible": 0.2}` it is `()`. No
  call beyond the two native ones is made (`len(fake.native_requests) ==
  2`, `fake.calls == 0`).
- [ ] **Step 2: Run** →
  `cd /home/user/wt/01d-s2/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_inference_decide_native.py -k triggers`
  → passes once Tasks 1-4 are in (it is a wiring check, so it may pass at
  first run; if so, confirm it fails with Task 2's native stamp reverted).
- [ ] **Step 3: CLAUDE.md.** After "`ItemResult.backend` is the per-item
  truth," insert: "`ItemResult.served` names the `(kind, provider id,
  model)` that answered each item (stamped by its backend, never compared),".
  Keep the rest of the sentence as it is. Run
  `PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_docs_guard.py`.
- [ ] **Step 4:** (folded into Task 4: the `TRIGGERS` pin lands here in
  either order.)
- [ ] **Step 5: Gate.** From `/home/user/wt/01d-s2`:
  `make check-lint check-mypy PY=/home/user/grimoire/backend/.venv/bin/python`
  (no new finding; if a finding in `decisions.py` or `inference.py` was
  resolved, `make baseline PY=...` and commit the smaller file), then the
  guard and decide suites:
  `cd backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_decisions.py tests/test_decision_triggers.py tests/test_native_decisions.py tests/test_inference_decide.py tests/test_inference_decide_native.py tests/test_decide_chain_golden.py tests/test_decision_capture.py tests/test_decide_gate.py tests/test_import_guard.py tests/test_usage_guard.py tests/test_routing_guard.py tests/test_operation_guard.py tests/test_docs_guard.py`,
  then the whole backend suite once:
  `make check-py PY=/home/user/grimoire/backend/.venv/bin/python`,
  `make check-templates PY=/home/user/grimoire/backend/.venv/bin/python`, and
  `make check-pydantic1 PY=/home/user/grimoire/backend/.venv/bin/python`
  (plain dataclasses, but the gate is cheap to confirm). `git status`
  shows no change under `backend/tests/fixtures/` or `evals/`.
- [ ] **Step 6: Commit** (one commit; the brief's message form):

  ```text
  01d-S2: per-item provenance and trigger evaluation

  ItemResult.served names the (kind, provider id, model) that answered each
  item, stamped by the structured and native backends and never compared.
  decisions gains answer_key, margin (from the answered key, fsum), TRIGGERS,
  Trigger and triggers with the exact and prefix answer filter. Pure; nothing
  calls triggers yet, and no decision, row, capture or golden moves.

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01DA4KKMFiW9cAipBiSS74NN
  ```

## Handoff to 01d-S3 (not built here)

- Call `triggers(base.items, question=p.question, escalate_on=p.escalate_on,
  margins=dict(p.margins), answers=p.escalate_answers)`; S1's "no repeated
  margin kind" rule makes the `dict` lossless.
- Check, before any meter opens, that every item asks `p.question`, so
  `triggers`' `ValueError` for a missing question can never follow a paid
  base call.
- The same-model skip compares `item.served[1:]` with the hop target's
  `(provider_id, model)`; an unstamped item (`served == ()`) matches no
  target, so it is never skipped as `same_model`.
- `Decision.served` stays two-part; S3's union of base and hop servers
  extends it with `served[1:]`.

## Handoff to other slices

- **01d-S5** (`--escalation-sweep`) counts which items would escalate
  through `triggers`, never through a bare `margin(a) < m`, so the sweep and
  production agree at the `MASS_TIE` boundary.
- **The `MASS_TIE` reading is a deliberate spec erratum**: §5.1 and §9 say
  `margin < margins[kind]`; this slice fires on `margin < margins[kind] -
  MASS_TIE`, so a margin float error put a hair under its threshold does not
  fire. A later edit of the spec (not this slice, which may not touch
  `docs/superpowers/specs/`) should say so.
- **01c-S1**: `answer_key` returns `None` for an answer of None, an
  abstention included, while 01c's record spells an abstained-none selection
  as `NONE_KEY`. A record that reuses `answer_key` maps `None` to `NONE_KEY`
  itself where the question allows none; `answer_key` does not, because a
  `None` there means "no key" for every other answer of None too
  (refused, unreadable, error).

## Decisions taken in this plan (for the plan gate)

1. **A threshold is met within `MASS_TIE`** (Global Constraints). The spec's
   bare `<` would fire on float error exactly at a threshold. Recorded as a
   spec erratum (Handoff to other slices).
2. **`decisions.TRIGGERS` duplicates `routing.TRIGGERS` by name and value**,
   because the leaf may not import `routing`; the equality pin lands here,
   written to hold in either landing order (Task 4).
3. **`served: tuple[()] | tuple[str, str, str]` plus a runtime check** (as
   the plan gate's N5c suggested; mypy accepts the union), so the `()`
   default type-checks and a hand-built result still cannot carry two parts.
4. **Joint answers have a key and a margin** (their `Pair.key` over the
   flattened distribution, which is how `native_lift` leaves it), though the
   spec, written before 01e, names only choice, score and predicate. Rank
   and MultiSelect answers have neither.
5. **`answer_key` is public**, so 01c-S1's record (`answer`, `selected` as
   keys) can share one spelling rather than writing a second.
6. **`triggers` raises on a caller's mistake** (unknown trigger, bad
   threshold, missing question) rather than finding nothing.
7. **`NONE_KEY` is a rival** in a margin.
8. **CLAUDE.md gains one clause**, because its decide paragraph says
   `ItemResult.backend` is the per-item truth and would otherwise imply
   provenance stops there.

## Plan gate (substitute review, 2026-10-10)

An independent reviewer agent (substitute for `/codex:adversarial-review`,
which this environment does not have) read the plan against the spec and the
code. Verdict: sound and complete; decisions 1-6 accepted, and the margin
formula matches §6.1. Each finding, and how it was folded:

| # | Finding | Folded |
|---|---|---|
| S1 | The `TRIGGERS` pin was left to S1, whose plan never mentions `decisions.TRIGGERS` | The pin lands in this slice as `getattr(routing, "TRIGGERS", decisions.TRIGGERS) == decisions.TRIGGERS`, which holds in either landing order (Task 4; Task 5 Step 4 removed) |
| S2 | An empty distribution passed the "has a report" check and gave margin `-1` | `margin` returns None for an empty distribution and for a missing probability, with tests (Task 3) |
| S3 | §9 B1 says an answered key absent from the report gives a negative margin AND triggers | `test_an_answer_absent_from_its_report_triggers` (Task 4) |
| N4a | `Trigger(..., pytest.approx(...))` fails its own `__post_init__` | Margin triggers are compared by their fields (Tasks 4, 5) |
| N4b | `FakeLLM` needs `turns=` or `cassette=` beside `decisions=` | `FakeLLM([["unused"]], decisions=...)` (Task 5) |
| N4c | §9's refusal case should be OpenAI-shaped | Read through `openai_compatible.decision_result` (Task 4) |
| N5a | `margin` could return an int from a hand-built `probability=1` | Coerced with `float(...)`, with a test (Task 3) |
| N5b | `_server` could hand `ItemResult` a non-`str` field, which would raise inside `decide` | `_server` returns `()` unless all three are `str`, with a test (Task 2) |
| N5c | Optional: type `served` as `tuple[()] \| tuple[str, str, str]` | Adopted: mypy accepts the union, and the runtime check stays (decision 3) |
| N6a | 01d-S5's sweep must count through `triggers`; the `MASS_TIE` reading is a spec erratum | Handoff to other slices |
| N6b | 01c's record spells an abstained none as `NONE_KEY`, `answer_key` gives None | Handoff to other slices |

## Review and final gate (substitute review, 2026-10-10)

An independent reviewer agent (substitute for `/codex:review` and the final
`/codex:adversarial-review` against the spec) read the committed diff against
this plan and spec 01d. Verdict: mergeable, no blocking findings; the margin
arithmetic, the triggers, every `served` stamp path and the invariants
(nothing compared, no golden or capture moved) were verified. The slice was
rebased onto the integration tip that carries 01d-S1 (`b3204cc`), with no
conflict. Each finding, and how it was folded:

| # | Finding | Folded |
|---|---|---|
| 1 | Rebase onto the tip with 01d-S1 | Rebased onto `b3204cc`; no conflict |
| 2 | With `routing.TRIGGERS` present, the `getattr` default can only go vacuous | The pin is the direct `routing.TRIGGERS == decisions.TRIGGERS` |
| 3 | A hand-built non-finite or out-of-range probability or weight gave a non-finite margin, so `Trigger` could raise inside a caller's `triggers` | `margin` reads every value through `_probability` and is None for any that is not a probability; parametrised test over -inf, inf, nan, -0.25, 1.5 and True, through `triggers` too |
| 4 | A bare `str` is a `Collection[str]` and was silently read as its characters | `triggers` raises `TypeError` for a `str` `escalate_on` or `answers`; test |
| 5a | The re-send test could not tell the answering attempt from `chain.primary` | Replaced by a refusing fallback re-sent without the mode, which names the fallback (and differs from the primary); a mutation stamping `chain.primary` fails it |
| 5b | No refusing-fallback re-send case | The same test; plus a refusing primary re-sent after its fallback failed, which names the primary |
| 5c | No native fallback stage case | `test_an_item_a_native_fallback_stage_answered_names_the_fallback` (structured primary fails, native fallback stage answers on its own target) |
| 6 | `_Unstamped(FakeLLM)` was a fake inline in a suite | Moved to `tests/llm_fakes.py` as `UnstampedHolder`, beside `ModelessHolder`; its docstring says the real facade cannot produce that state |
| 7 | `served` names the attempt's server, not a verdict that the item was answered | `ItemResult`'s docstring says so, naming the `NO_ITEM` / `NO_OBJECT` items of a chunk, which carry the chunk's server |
| 8 | The `MASS_TIE` reading drifts from the spec's bare `<` (§5.1, C2a, §9) | Kept, with this plan's record of it (Decisions 1, Handoff); the spec is not edited here, and the coordinator logs the erratum in `ROADMAP-CHECKLIST.md` |

# 01d-S1: The task policy and `fallback="none"` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `store/routing.py` gains the shared per-task policy
(`TaskPolicy`, `TASK_POLICY` empty, `policy(task)`) with every field of spec
§4.1, plus the vocabulary constants (`TRIGGERS`, `FALLBACKS`,
`ESCALATION_ROLES`, `CALLER`, `MAX_MARGIN`, `DEFAULT_MARGIN`).
`resolve.resolve` reads `routing.policy(task).fallback`, and for `"none"`
attaches no fallback attempt, reporting `NO_FALLBACK_POLICY` in
`fallback_problem` only when the role had a fallback to drop. The Models
page's dropped-fallback line gets a sentence of its own for that reason.
`backend/tests/test_task_policy.py` holds every §4.3 rule, each with a
planted violation, and that no task escalates or drops its fallback at
landing. No task gets a policy, so no behaviour changes. Delivers 01d-C1
(part) and 01d-C3 (part: `MAX_MARGIN`, `DEFAULT_MARGIN`, the `margins` and
`escalate_max` validation).

**Architecture:** the policy is code, not settings. `routing` stays a pure
leaf with literals only: `TaskPolicy` is a `NamedTuple` whose defaults are
today's behaviour, and `policy(task)` returns `TASK_POLICY.get(task,
_DEFAULT)`, so `""` (a role card's resolution) and any unlisted task read the
default. 01c-S2 appends `samples` and `native_first` to the same tuple, after
01d's fields, with defaults. The only production reader in this slice is
`resolve.resolve`, at the one place it attaches a fallback (the
`fallback = choice.fallback` block, `store/inference/resolve.py:822-842`).
There, a `"none"` policy short-circuits the fallback block before
`_apart`/`_fallback_problem` are asked. Everything downstream already handles
a one-attempt resolution: `_chain` (no `fallback_missing`, `rides=False`),
`inference.stages` (no fallback stage), the facade (`chain.fallback is
None`) and the settings view (`fallback_problem` passes through
`_role_card`/`_route_row` as it does today). The rules of §4.3 live in the
test as a pure checker, `violations(table, routes)`, that reads the store
constants the call sites build their items with. It takes the routes as an
argument so a rule about a route shape that does not exist today (a decide
route whose `default_role` is `primary`) can still be planted. 01c-S2 adds
its rules to that checker as further entries of one rule list.

**Tech Stack:** Python 3.11 (`typing.NamedTuple`), pytest (`monkeypatch.setitem`
on `routing.TASK_POLICY`), the inference baseline states
(`tests/inference_baseline.py`) and fixtures (`tests/inference_fixtures.py`),
vitest for the one frontend sentence.

**Spec refs:** `docs/superpowers/specs/2026-10-09-roadmap-01d-decision-escalation-policy-design.md`
§4.1 (the structure), §4.2 (the fallback field), §4.3 (the rules), §6.2
(`MAX_MARGIN`, `DEFAULT_MARGIN`, `escalate_max`), §7 (C1, C3), §8 (01s),
§9 (`test_task_policy.py`, `test_inference_resolve*.py`), Slices (01d-S1).
Shared structure: `docs/superpowers/specs/2026-10-09-roadmap-01c-decision-distributions-sampling-design.md`
§4.1 (`samples`, `native_first`, and the rules 01c adds to the same test).

**Open questions adopted:** none of §11's questions is about S1. Q3 (show
escalation in Settings) stays open, and nothing here shows it.

## Global Constraints

- **No behaviour change.** `TASK_POLICY` is `{}`, so `policy(t)` is the
  default for every task and `resolve` takes the existing path for each
  one. The frozen resolution baselines (`test_inference_equivalence.py`,
  `test_inference_resolve.py`'s `BASELINE`), `test_decide_chain_golden.py`
  and the frozen campaign's `snapshot.json` pass untouched. None of them is
  regenerated.
- **`routing` stays a pure leaf** (`store/routing.py` docstring; `config.py`
  imports it). It imports nothing from the store or the package, and every
  value is a literal: `escalate_max` defaults to the literal `8`, and the
  test pins it to `decisions.MAX_ITEMS_PER_CALL` rather than importing it.
- **`margins` is a tuple of pairs, not a dict**, so the default stays
  immutable and hashable (§4.1).
- **Field order is 01d's §4.1 order**, with nothing after
  `reads_declines`. 01c-S2 appends `samples` and `native_first` after it.
  Nothing constructs a `TaskPolicy` positionally, and the tests use keywords
  only, so the append is safe.
- **Imports follow the import guard.** `resolve.py` already binds `routing`
  as a submodule (`from .. import ... routing`) and calls
  `routing.policy(task)`. It never does `from ..routing import policy`.
- **`NO_FALLBACK_POLICY` is reported only for a fallback that existed**
  (review M1): the cascade chose a fallback selection *and* its connection
  reads (`fb_raw is not None`). A fallback slot naming a provider that does
  not exist never reaches `resolve`: `cascade._slot` drops it through
  `exists`, and `cascade.role_fallback` walks on to the inherited role's
  fallback (Decision → Fast → Primary). So a Decision role whose own
  fallback dangles, under a Primary with a working fallback, *has* a
  fallback (Primary's), and a `"none"` task reports `NO_FALLBACK_POLICY` for
  it. That is correct: that fallback would have been sent. `fb_raw is None`
  with a selection set happens only when a connection vanishes between the
  cascade's `exists` check and `lookup` (a read race). The guard stays, so
  that race reports nothing, as `_fallback_problem(..., None)` does today.
- **The policy reason outranks the others.** On a `"none"` task the fallback
  is never asked whether it is on the primary's provider (`SAME_PROVIDER`),
  or whether it can send (`problem`). The policy is why it is not sent, and
  saying `SAME_PROVIDER` there would point the user at a setting that would
  change nothing.
- **A route's tasks agree on `fallback`** (§4.2), because `inference.for_task`
  (`inference.py:1139-1157`) hands one sibling's resolution to another by
  `replace(resolved, task=task)`. The rule is held statically by
  `test_task_policy.py` over the code table. `for_task` gains no runtime
  check, since the table is a code literal and the static rule covers every
  value it can hold.
- **No privacy exposure.** Fixtures use the existing placeholder names
  (Saltmarch, Realm, `spare`, `vendor/...`), and no store data is read.
- Ratchets (`check-lint`, `check-mypy`, `check-eslint`) stay at baseline,
  and no baseline file changes.

## Review Focus

- `resolve`: the `"none"` branch sits *inside* `if selection is not None and
  raw is not None`, so a resolution with no primary is unchanged (no
  `fallback_problem`, as today). A role card (`task=""`) and any task not in
  `TASK_POLICY` take the old branch byte for byte.
- `fallback_problem` is `NO_FALLBACK_POLICY` exactly when the role's
  fallback selection exists and reads, and `None` otherwise. That includes
  the case where it would have been `SAME_PROVIDER` or a credential problem.
- The checker in `test_task_policy.py` refuses each planted violation for
  the rule it plants, and accepts a planted *valid* full policy (an identity
  policy with every trigger, a margin, and an `"existing:"` prefix filter;
  a speaker `CALLER` policy with `reads_declines`). A checker that refused
  everything would otherwise pass every planted case.
- The question pins (`_QUESTION`) and the answer vocabularies (`_answers()`)
  cover *every* task on a decide route. A new decide route fails by name
  until it adds both, so the pins cannot go stale silently.
- The frontend sentence branches on the server's reason string, held equal
  to `resolve.NO_FALLBACK_POLICY` by a backend test that reads
  `selection.ts`. This has precedent (`test_llm_error_status.py` reads
  `errors.ts`, `ProvidersView.tsx` and `types.ts` the same way).

---

### Task 1: the structure in `routing`, and its rules

**Files:**
- Modify: `backend/src/grimoire/store/routing.py` (the constants,
  `TaskPolicy`, `TASK_POLICY`, `_DEFAULT`, `policy`; the module docstring
  gains a paragraph saying the registry now also holds each task's policy,
  and that 01c shares the structure)
- Create: `backend/tests/test_task_policy.py`

**Interfaces:**
- Produces, in `store/routing.py`:
  ```python
  TRIGGERS: tuple[str, ...] = ("low_margin", "abstained", "refused")
  FALLBACKS: tuple[str, ...] = ("role", "none")
  ESCALATION_ROLES: tuple[str, ...] = ("primary", "fast")
  CALLER = "caller"
  #: §6.2: above this, most predicate answers escalate (P < 0.75), and the
  #: task should move its route to the stronger role instead.
  MAX_MARGIN = 0.5
  #: §6.2: the value a task's first eval starts from; tuned there.
  DEFAULT_MARGIN = 0.2

  class TaskPolicy(NamedTuple):
      fallback: str = "role"
      escalate_to: str = ""
      escalate_on: tuple[str, ...] = ()
      question: str = ""
      margins: tuple[tuple[str, float], ...] = ()
      escalate_max: int = 8
      escalate_answers: tuple[str, ...] = ()
      reads_declines: bool = False

  TASK_POLICY: dict[str, TaskPolicy] = {}
  _DEFAULT = TaskPolicy()

  def policy(task: str) -> TaskPolicy: ...
  ```
  Each field carries the `#:` comment §4.1 gives it. `TASK_POLICY`'s comment
  says that it is empty at landing, that a task's escalation is switched on
  only by the change carrying its §6.3 evidence (`evals/README.md`,
  "Decision escalation"), and that `fallback="none"` must agree across a
  route's tasks.
- Consumed by: Task 2 (`resolve`), and later 01d-S3 (`decide`), 01c-S2
  (`inference.stages`), 01g, 02, 10, 11.

**The checker** (`test_task_policy.py`, module level, pure):

```python
Rule = Callable[[str, routing.TaskPolicy, routing.Route], list[str]]

def violations(table: Mapping[str, routing.TaskPolicy],
               routes: Sequence[routing.Route] = routing.ROUTES) -> list[str]:
    """Every §4.3 rule broken by `table` over `routes`, as sentences naming
    the task. [] for a table the rules accept."""
```

It runs, in order:

1. **Known task.** Each key is a task some route in `routes` claims.
   Otherwise it is "<task>: not a task any route claims", and the per-task
   rules are skipped for that key.
2. **Per-task rules** (`_RULES: tuple[Rule, ...]`, each reading one policy
   and its route). 01c-S2 appends its rules here:
   - `_fallback_known`: `fallback in routing.FALLBACKS`.
   - `_escalation_on_decide_only`: `escalate_to`, `escalate_on`, `question`,
     `margins`, `escalate_answers` and `reads_declines` are default unless
     `route.operation == "decide"`.
   - `_escalation_complete`: a non-empty `escalate_to` needs a non-empty
     `escalate_on` and a `question`. Without `escalate_to`, `escalate_on`,
     `margins` and `escalate_answers` are empty, `escalate_max` is the
     default, and `reads_declines` is False. Each of these is a stale
     setting nothing reads. `question` alone is allowed, because 01c's
     `samples` requires it (§4.3). A comment on the rule says that 01c-S2
     tightens it to "`question` needs `escalate_to` or `samples`".
   - `_triggers_known`: `escalate_on` is a subset of `routing.TRIGGERS`
     with no repeats.
   - `_escalation_target`: `escalate_to` is `routing.CALLER`, or is in
     `routing.ESCALATION_ROLES` and is not `route.default_role`.
   - `_question_pinned`: a non-empty `question` equals `_QUESTION[task]`. A
     decide task with no `_QUESTION` entry is its own violation ("pin its
     question in test_task_policy._QUESTION").
   - `_margins_valid`: `low_margin` in `escalate_on` needs at least one
     `margins` entry. `margins` is empty without `low_margin`. No kind
     appears twice, each kind satisfies `adapters.decides_natively(kind)`,
     and each value satisfies `0 < m <= routing.MAX_MARGIN`.
   - `_answers_valid`: `escalate_answers` is empty without `low_margin`.
     Each entry ending in `":"` is in `_answers()[task].prefixes`. Each other
     entry is in `_answers()[task].exact`, or (for `continuity-reconcile`) is
     a folded option, meaning `reconcile.unfolded(e) != (e, "", "")`. A
     directed word of a pair vocabulary (`duplicate`, `continuation`,
     `subthread`, `pays_off`) is not in the exact set, because
     `reconcile._offered` offers it only folded.
   - `_declines_allowed`: `reads_declines` only for a task in
     `_READS_DECLINES = frozenset({"response-selector"})`. The message for
     the continuity tasks names `decisions.was_read`.
   - `_cap_valid`: `1 <= escalate_max <= decisions.MAX_ITEMS_PER_CALL`.
3. **Per-route rule** (`_route_agrees`): for each route in `routes`, the
   set of `policy_of(t).fallback` over its tasks has one element, where
   `policy_of` reads `table` with the default. A route that disagrees is
   "<route>: its tasks disagree on fallback (for_task hands one's resolution
   to another)".

**The pins** (from the store constants each call site builds its item with;
the test imports the store modules):

```python
_QUESTION = {
    "response-selector": response_protocol.SELECTOR_QUESTION,  # "next"
    "continuity-identity": identity.DECISION_ID,               # "decision"
    "continuity-reconcile": reconcile.DECISION_ID,             # "decision"
    "scene-break": scene_break.QUESTION_ID,                    # "over"
    "voice-drift": voice_drift.QUESTION_ID,                    # "verdict"
}

class _Vocab(NamedTuple):
    exact: frozenset[str]
    prefixes: frozenset[str] = frozenset()

# What `_answers()` returns, as first planned (the final gate reads these off the builders):
{
    # A Choice over per-row refs plus the one static option.
    "response-selector": _Vocab(frozenset({response_protocol.GRIMOIRE_REF})),
    # `existing:<id>` is per row, so it is named by its prefix.
    "continuity-identity": _Vocab(frozenset(identity.DECISIONS) - {"existing"},
                                  frozenset({identity.EXISTING_PREFIX})),
    # Every word offered bare: a pair vocabulary's directed words are only
    # ever offered folded (`reconcile._offered`), so they are left out here
    # and their folded ids pass via `unfolded`.
    "continuity-reconcile": _Vocab(frozenset(
        w for vocab, ws in reconcile.DECISIONS.items() for w in ws
        if vocab not in reconcile.PAIR_VOCABULARIES or not _directed(w))),
    # A Predicate: "true"/"false" (§5.1, keys as the record spells them).
    "scene-break": _Vocab(frozenset({"true", "false"})),
    # Read off the item builder, so the options cannot drift from the pin.
    "voice-drift": _Vocab(frozenset(o.id for o in voice_drift.build_item(
        "Seraphine", "anchor", "transcript").questions[0].options)),
}
```

`_directed(word)` is True when `reconcile.folded(word, "A", "B")` succeeds,
i.e. when the word has a join. That is the public function, so the test
needs no private `_DIRECTED`.

The voice-drift builder renders templates, so the vocabularies are built lazily in
a function (`_answers()`, `functools.cache`), not at import time. The speaker
and identity builders need per-row input (a roster, candidate rows), so
their static keys come from the constants, as §4.3 allows ("checked against
the store constant's question builder where one exists").

- [ ] **Step 1: Write the failing tests** (`backend/tests/test_task_policy.py`):
  - `test_policy_defaults_to_todays_behaviour`: `policy("")`,
    `policy("no-such-task")` and `policy("chat")` are each `== TaskPolicy()`.
    The defaults are pinned: `fallback == "role"`, `escalate_to == ""`,
    `escalate_on == ()`, `margins == ()`, `escalate_answers == ()`,
    `reads_declines is False`, and `escalate_max ==
    decisions.MAX_ITEMS_PER_CALL`. Also `hash(TaskPolicy())` works.
  - `test_policy_reads_the_table` (`monkeypatch.setitem`): a planted
    `TASK_POLICY["absorb"]` is what `policy("absorb")` returns, and
    `policy("audit")` is still the default.
  - `test_the_margin_constants`: `MAX_MARGIN == 0.5`, `DEFAULT_MARGIN ==
    0.2`, and `0 < DEFAULT_MARGIN <= MAX_MARGIN`.
  - `test_the_code_table_breaks_no_rule`: `violations(routing.TASK_POLICY)
    == []`.
  - `test_no_task_escalates_or_drops_its_fallback_at_landing` (§9): every
    entry has `escalate_to == ""` and `fallback == "role"`. Its docstring
    says that the change switching a task on edits this test together with
    its §6.3 evidence.
  - `test_every_decide_task_is_pinned`: `set(_QUESTION) == set(_answers())
    == {t for r in routing.ROUTES if r.operation == "decide" for t in
    r.tasks}`, and each `_QUESTION` value is spelled as §1 lists
    (`"next"`, `"decision"`, `"decision"`, `"over"`, `"verdict"`).
  - `test_a_valid_policy_is_accepted` (parametrised): an identity policy
    `TaskPolicy(escalate_to="primary", escalate_on=("refused", "abstained",
    "low_margin"), question="decision", margins=(("openrouter", 0.2),
    ("openai_compatible", 0.5)), escalate_answers=("existing:", "new"))`; a
    reconcile policy whose answer filter names a folded option
    (`reconcile.folded("duplicate", "A", "B")`); a speaker policy
    `TaskPolicy(escalate_to=routing.CALLER, escalate_on=("refused",),
    question="next", reads_declines=True)`; a scene-break `question`-only
    policy; a whole route set to `fallback="none"` (every `scene` task, and
    separately both `continuity` tasks). Each gives `violations(...) == []`.
  - `test_each_rule_refuses_its_planted_violation` (parametrised, `id`
    per case; each asserts that the expected fragment is in exactly one
    returned sentence):
    - unknown key `"no-such-task"`;
    - `fallback="never"`;
    - `{"chat": TaskPolicy(fallback="none")}` alone (route disagreement);
    - escalation fields on a generate task (`absorb` with a full
      escalation), and `question="decision"` alone on `absorb`;
    - `escalate_to` without `escalate_on`; `escalate_to` without
      `question`; `escalate_on` without `escalate_to`; `margins` without
      `escalate_to`; `escalate_max=4` without `escalate_to`;
      `reads_declines=True` on `response-selector` without `escalate_to`;
    - `escalate_on=("unsure",)`; `escalate_on=("refused", "refused")`;
    - `escalate_to="decision"` (not an escalation role); `escalate_to=
      "primary"` on a planted decide route whose `default_role` is
      `"primary"` (a `routing.Route(...)` handed in `routes=`, with a new
      task that is also pinned for the test via a patched `_QUESTION`, so
      only the target rule fires);
    - `question="verdict"` on `scene-break`; a decide task on a planted
      route with no pin;
    - `low_margin` with no `margins`; a margin kind `"anthropic"` and one of
      `"claude"` (no native endpoint); `0.0`; `0.6`; a kind listed twice;
      `margins` set beside `escalate_on=("refused",)`;
    - `escalate_answers` beside `("refused",)`; `"maybe"` on `voice-drift`;
      `"other:"` on `continuity-identity`; `"existing:"` on
      `continuity-reconcile`; `"rumoured"` on `continuity-reconcile`;
      `"duplicate"` on `continuity-reconcile` (a directed word, offered only
      folded);
    - `reads_declines=True` on `continuity-identity`, on
      `continuity-reconcile` (both messages name `was_read`), and on
      `scene-break`;
    - `escalate_max=0`; `escalate_max=9`.
- [ ] **Step 2: Run** `cd /home/user/wt/01d-s1/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_task_policy.py` → fails on import (no `routing.TaskPolicy`).
- [ ] **Step 3: Implement** the `routing.py` additions, then the checker
  and pins in the test.
- [ ] **Step 4: Run** the same command → PASS. Then `tests/test_routing_guard.py
  tests/test_import_guard.py tests/test_operation_guard.py` → PASS
  (`_task_literals` sees no new task string: the table is empty).

### Task 2: `fallback="none"` in `resolve`

**Files:**
- Modify: `backend/src/grimoire/store/inference/resolve.py`
  (`NO_FALLBACK_POLICY` beside `SAME_PROVIDER` at `:100-107`; the fallback
  block of `resolve` at `:818-842`; `resolve`'s docstring paragraph on what
  drops a fallback; `_fallback_problem`'s docstring)
- Modify: `backend/src/grimoire/store/inference/resolved.py`
  (`fallback_problem`'s `#:` comment, `:126-132`, names the third reason)
- Modify: `backend/src/grimoire/store/inference/settings.py` (module
  docstring, the `fallback_problem` sentence at `:17-19`: a route row also
  says when its task's policy sends none, and a role card never does)
- Test: `backend/tests/test_inference_resolve.py`,
  `backend/tests/test_inference_settings.py`

**Interfaces:**
- Produces: `resolve.NO_FALLBACK_POLICY = "this task's policy sends no
  fallback"`.
- The fallback block becomes, in shape:
  ```python
  fallback = choice.fallback
  fb_raw = lookup(fallback.provider) if fallback is not None else None
  if routing.policy(task).fallback == "none":
      # The task's code policy (`routing.TaskPolicy`) sends no fallback
      # whatever its role says; said only for a fallback that exists.
      fallback_problem = NO_FALLBACK_POLICY if fb_raw is not None else None
  else:
      fallback_problem = (...as today...)
      if fallback is not None and fb_raw is not None and fallback_problem is None:
          ...as today...
  ```
  `routing.policy(task)` is read even when `role=` is passed with a task.
  01d-S4's escalation seam (`resolve(task, cid, operation="decide",
  role=...)`) then reads the task's policy, and §5.3 sends that hop's primary
  alone anyway. A role card passes `task=""` and reads the default (§4.2).

- [ ] **Step 1: Failing tests** (`test_inference_resolve.py`, a new section
  `# ---- 01d: a task's policy may send no fallback ----`; each patches
  with `monkeypatch.setitem(routing.TASK_POLICY, t,
  routing.TaskPolicy(fallback="none"))` for every task of the route, so the
  patched table also passes `violations`):
  - `test_a_no_fallback_policy_drops_a_generate_tasks_fallback`
    (`at_state("routed")`): with no patch, `inf.resolve("tracker-update")`
    has a second attempt on `spare` (the precondition, as
    `test_a_fallback_naming_the_primary_is_no_fallback` asserts). After
    patching `tracker-update`, it has one attempt, `chain.fallback is None`,
    `rides is False`, `fallback_missing == ()`, `fallback_problem ==
    inf.NO_FALLBACK_POLICY`, and the primary target is `==` the unpatched
    one. `inf.resolve("dossier")` (another route) still has its `spare`
    fallback.
  - `test_a_no_fallback_policy_drops_a_decide_tasks_fallback_stage`
    (`inference_fixtures.decide_only(client, fallback=True)` on
    `baseline.client_at(tmp_path)`): unpatched, `inf.resolve("scene-break",
    operation="decide")` has stages `["native", "structured"]`
    (`grimoire.inference.stages`). Patched, it has one attempt,
    `fallback_problem == NO_FALLBACK_POLICY`, and stages `["native"]`.
  - `test_the_policy_reason_outranks_same_provider`
    (`decide_only(client, fallback=True, on=("openrouter",
    "vendor/decider"))`): unpatched, the reason is `SAME_PROVIDER` (as
    `test_a_decide_only_models_fallback_on_itself_is_dropped_and_said`
    pins). Patched, it is `NO_FALLBACK_POLICY`.
  - `test_a_no_fallback_policy_on_a_role_with_no_fallback_says_nothing`
    (§9 M1, `decide_only(client, fallback=False)`): unpatched, one attempt
    and `fallback_problem is None` (precondition). Patched, the same.
  - `test_a_no_fallback_policy_names_an_inherited_fallback` (format 2):
    the Decision role's own fallback slot names a provider that does not
    exist (written straight into `config.md` as
    `role_decision_fallback_provider`, since the settings PUT would refuse
    it), and Primary's fallback is `spare`. Unpatched,
    `inf.resolve("scene-break", operation="decide")` falls back to `spare`
    (the cascade walked past the dangling slot). Patched, it has one attempt
    and `fallback_problem == NO_FALLBACK_POLICY`. That fallback would have
    been sent, so naming the policy is correct.
  - `test_a_role_resolution_reads_the_default_policy`: with every task of
    every route patched to `"none"` (a table that passes `violations`),
    `inf.resolve("", role="primary")` in `routed` keeps its `spare` fallback,
    and `inf.resolve("", role="decision", operation="decide")` on
    `decide_only(..., fallback=True)` keeps its structured fallback.
    `fallback_problem is None` on both.
  - `test_for_task_between_siblings_keeps_the_policys_fallback`: both
    `continuity` tasks patched, on `decide_only(..., fallback=True)`. Then
    `ops.for_task(inf.resolve("continuity-identity", operation="decide"),
    "continuity-reconcile")` has `attempts` equal (target by target) to
    `inf.resolve("continuity-reconcile", operation="decide").attempts`, and
    the same `fallback_problem`. The `absorb`/`audit` pair checks the same
    thing on a generate route in `routed`.
  - `test_inference_settings.py`,
    `test_a_no_fallback_policy_says_why_on_its_route_rows_only` (the
    `_fresh` + `spare` fallback setup of the credential-problem test at
    `:488-506`, with every `scene` task patched): `_row(_global(client),
    "scene")["fallback_problem"] == inference.NO_FALLBACK_POLICY`, the same
    on `_campaign(client, cid)`; `roles.primary.fallback_problem is None`;
    `_row(got, "opener")["fallback_problem"] is None` (another
    primary-role route).
- [ ] **Step 2: Run** `cd /home/user/wt/01d-s1/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_inference_resolve.py tests/test_inference_settings.py -k "policy or for_task"` → fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_inference_resolve.py tests/test_inference_settings.py
  tests/test_inference_settings_limits.py tests/test_inference_settings_rates.py
  tests/test_inference_equivalence.py tests/test_inference_decide*.py
  tests/test_decide_chain_golden.py tests/test_format2_play.py
  tests/test_task_policy.py tests/test_import_guard.py
  tests/test_routing_guard.py` → PASS, with no golden or baseline
  regenerated.

### Task 3: the Models page's sentence for the policy reason

**Files:**
- Modify: `frontend/src/components/inference/selection.ts` (export
  `NO_FALLBACK_POLICY`, the server's sentence, mirrored; branch in
  `droppedFallbackWords`)
- Test: `frontend/src/routes/ModelsView.test.tsx`;
  `backend/tests/test_task_policy.py` (the mirror guard)

**Interfaces:**
- `export const NO_FALLBACK_POLICY = "this task's policy sends no fallback";`
  carries a comment saying it is `resolve.NO_FALLBACK_POLICY`, held equal by
  `test_task_policy.py`.
- `droppedFallbackWords(missing, fits, fallback, problem)` keeps its
  signature. Before the generic `problem` branch, it gains:
  `if (problem === NO_FALLBACK_POLICY) return \`${which} is never sent:
  ${fits} runs without a fallback.\`;`. The generic branch's "cannot be
  sent" would be false here, since the fallback could be sent and the
  policy chooses not to. All five callers are unchanged. Only route rows can
  receive this reason (role cards resolve `task=""`), and both route-row
  callers (`TaskOverrides.tsx:128`, `CampaignModels.tsx:426`) pass
  `row.label` as `fits`.

- [ ] **Step 1: Failing tests:**
  - `ModelsView.test.tsx`, beside "each task keeps what its detail page
    said": a route row `{ ...ROUTES[1], fallback_problem:
    NO_FALLBACK_POLICY }` renders `"The fallback is never sent: Rolling
    summary runs without a fallback."` under `task("Rolling summary")`, and
    no text matching `/cannot be sent/` in that row.
  - `SceneInspector.test.tsx` (the campaign Models panel,
    `CampaignModels.tsx:426`), beside "a fallback that cannot send says why
    on its role and its route": a campaign route row `{ ...base.routes[0],
    fallback_problem: NO_FALLBACK_POLICY }` renders `"The fallback is never
    sent: Scene turns runs without a fallback."` once its route is opened.
  - `test_task_policy.py`,
    `test_the_models_page_knows_the_policy_reason`: reads
    `frontend/src/components/inference/selection.ts`, finds
    `export const NO_FALLBACK_POLICY = "(.*?)";` (a missing match fails
    with "this guard reads it as text", as `test_llm_error_status.py` does),
    and asserts it equals `resolve.NO_FALLBACK_POLICY`.
- [ ] **Step 2: Run** `cd /home/user/wt/01d-s1/frontend && npx vitest run src/routes/ModelsView.test.tsx` and the backend test → fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `cd /home/user/wt/01d-s1/frontend && npx vitest run src/routes/ModelsView.test.tsx src/components/SceneInspector.test.tsx && npx tsc --noEmit -p .` → PASS.

### Task 4: CLAUDE.md, the gates, and the commit

**Files:**
- Modify: `CLAUDE.md`. In the paragraph that lists what never rides ("The
  fallback rides on the resolved chain: ..."), after "nor one that cannot
  carry the call's images.", add one sentence: "A task whose
  `routing.TaskPolicy` says `fallback="none"` is sent no fallback at all,
  and its route row says so (`resolve.NO_FALLBACK_POLICY`) when its role had
  one. A route's tasks agree on it, because `for_task` hands one sibling's
  resolution to another (`test_task_policy.py`)." The escalation sentence
  §8 asks for lands with 01d-S3/S4, not here.

- [ ] **Step 1:** `cd /home/user/wt/01d-s1/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_docs_guard.py` → PASS.
- [ ] **Step 2:** `cd /home/user/wt/01d-s1 && make check-lint check-mypy check-eslint check-templates PY=/home/user/grimoire/backend/.venv/bin/python` → at baseline, with no baseline change.
- [ ] **Step 3:** `make check-py PY=/home/user/grimoire/backend/.venv/bin/python` (whole backend suite, once). Then, from `frontend/`, the touched vitest files (`npx vitest run src/routes/ModelsView.test.tsx src/components/SceneInspector.test.tsx`) and `npx tsc --noEmit -p .` → PASS. The coordinator runs `check-pydantic1` and `check-web` when it integrates, so they are not run here (shared CPU).
- [ ] **Step 4: Commit** `01d-S1: the task policy and fallback="none"`, with
  the body and trailers the slice brief gives.

## Decisions taken in this plan (for the plan gate)

1. **The policy reason outranks `SAME_PROVIDER` and credential problems.**
   On a `"none"` task, those are not why the fallback is unsent.
2. **A dangling fallback slot is not a fallback.** The cascade drops it and
   walks on to the inherited role's fallback, which a `"none"` task then
   reports as `NO_FALLBACK_POLICY`, correctly. Only a connection that
   vanishes between the cascade and `lookup` reports nothing (see Global
   Constraints).
3. **`resolve` reads `policy(task)` whether or not `role=` is passed.** Only
   `task=""` (the role cards) reads the default. That matches §4.2's
   sentence, and gives 01d-S4's hop resolution the task's own policy.
4. **The §4.3 checker lives in the test**, parameterised by `(table,
   routes)`. `routing` stays a leaf of literals and cannot import the store
   constants the rules check against. 01c-S2 adds its rules to `_RULES`,
   including `samples` with `low_margin`, which lands with whichever slice
   is second. The rule that pins a sampling task's `question` reuses
   `_question_pinned`.
5. **Rules beyond §4.3's literal text**, each implied by it:
   - `margins` only beside `low_margin` (a threshold nothing reads is a
     stale setting);
   - no repeated trigger or margin kind (`margins` becomes a mapping in
     `decisions.triggers`, 01d-S2, where a repeat would silently pick one);
   - `reads_declines` restricted to an allowlist
     (`_READS_DECLINES = {"response-selector"}`), which is stricter than
     §4.3's "refused on the continuity tasks";
   - the decide-only rule covering `escalate_answers` and
     `reads_declines` too, not only §4.3's four escalation fields;
   - no stale escalation settings without `escalate_to`: a non-default
     `escalate_max`, or `reads_declines=True`.

   `question` alone stays allowed for 01c's `samples`. 01c-S2 tightens that
   to "`question` needs `escalate_to` or `samples`".
6. **The frontend branches on the server's sentence**, mirrored and held by
   a text-reading test. Adding a `fallback_policy` field to the route row
   instead would touch the `RouteRow` type and every fixture that builds
   one, for a value that only selects this one sentence.

## Plan gate (substitute review, 2026-10-10)

There is no Codex CLI here, so an independent reviewer agent stood in for
`/codex:adversarial-review`. It read the plan against spec 01d (§4, §6.2,
§9, the S1 slice), the 01c §4.1 shared structure, and the code. Nothing was
blocking. All six "Decisions taken" were judged sound. The findings, each
folded as described:

- **[SHOULD] The reconcile answer vocabulary accepted the directed words.**
  `reconcile._offered` never offers `duplicate`, `continuation`,
  `subthread` or `pays_off` bare on a pair vocabulary, only folded
  (`duplicate_a_into_b`). The exact set is now built without a pair
  vocabulary's directed words. A word is directed when
  `reconcile.folded(w, "A", "B")` succeeds. Folded ids still pass through
  `unfolded`. A planted `escalate_answers=("duplicate",)` on
  `continuity-reconcile` is a violation.
- **[SHOULD] The dangling-fallback rationale was wrong.** `cascade._slot`
  drops a nonexistent fallback provider through `exists`, and
  `role_fallback` walks on to the inherited role's fallback. So
  `fb_raw is None` with a selection set happens only in a read race, and
  the planned test would have passed with or without the guard. The Global
  Constraint now says this. The test is replaced by a format-2 case: the
  Decision role's own fallback dangles, Primary's is `spare`, and a
  `"none"` policy reports `NO_FALLBACK_POLICY` for the inherited fallback.
  That is correct, because that fallback would have been sent. The guard
  stays, for the race.
- **[NIT] "Two extra rules" undercounted.** Decision 5 now lists all of
  them: the margins-only-beside-low-margin rule, no repeats, the
  `reads_declines` allowlist, and decide-only covering
  `escalate_answers`/`reads_declines`. It also lists the stale-field
  refusals this finding asked for (a non-default `escalate_max`, or
  `reads_declines=True`, without `escalate_to`), each with a planted
  violation. The `question`-only allowance carries a note that 01c-S2
  tightens it to "`escalate_to` or `samples`".
- **[NIT] The campaign route row was untested.** Task 3 adds a
  `SceneInspector.test.tsx` case for the sentence on `CampaignModels.tsx`'s
  route row.
- **Verification.** Task 4 runs the whole backend suite, the three
  ratchets, `check-templates`, the touched vitest files and `tsc`.
  `check-pydantic1` and `check-web` are left to the coordinator's
  integration run.

## Review and final gate (substitute review, 2026-10-10)

There is no Codex CLI here, so an independent reviewer agent stood in for
both `/codex:review` (against the diff) and the final spec gate (against
the diff and spec 01d's S1 slice, C1 and C3 parts).

Verdict: **PASS**, with no blocking or should-fix findings. The slice
delivers §4.1's full structure, §4.2's `fallback="none"`, and every §4.3
rule this slice owns, each with a planted violation, plus §6.2's constants
and validation. The one §4.3 rule not here is the refusal of `samples`
beside `low_margin`. It needs 01c's `samples` field, so it belongs to
01c-S2, which appends it to `_RULES` together with the field (spec 01c
§4.1: "lands with whichever is second").

The nits, each folded in the amended commit:

1. **The landing test.** It now asserts the literal `routing.TASK_POLICY
   == {}` ("empty at landing"), and keeps the per-entry check for the
   change that first adds an entry.
2. **`override_inference`'s docstring** (`routes/common.py`) says the
   resolution carries the fallback (#144). It now adds that a task whose
   policy is `fallback="none"` carries none, overridden or not.
3. **The policy sentence was drawn as a fault.** Both route-row sites
   (`TaskOverrides.tsx`, `CampaignModels.tsx`) passed it to `<Problem>`.
   The `NO_FALLBACK_POLICY` case now renders as a plain
   `<p className="field-hint">`, because it is a deliberate code choice.
   Every other dropped-fallback reason keeps the problem styling. Both
   vitest cases assert the sentence, the `field-hint` class and the absence
   of `problem`.
4. **Vocabularies read off the builders** (spec §4.3, "checked against the
   store constant's question builder where one exists"). `_answers()` now
   builds each item and reads the deciding question's keys through
   `_keys(item, question)`: option ids, `str(level)` for a score, and
   "true"/"false" for a predicate.
   - Speaker: `selector_item([], [])`, which gives only `grimoire`.
   - Identity: `build_items` over a row with no candidates (`_BARE_ROW`),
     which gives `new` and `uncertain`. The per-row `existing:<id>`
     options keep the `EXISTING_PREFIX` prefix.
   - Scene-break: `build_item("", [])`, a predicate.
   - Voice-drift: as before.
   - Reconcile stays restated, with a comment saying why: its builder
     needs a whole sweep payload.

   `test_every_decide_task_is_pinned` pins each builder-read set.
5. **Plan wording.** References to the vocabularies now say `_answers()`,
   as the code spells it.
6. This section.

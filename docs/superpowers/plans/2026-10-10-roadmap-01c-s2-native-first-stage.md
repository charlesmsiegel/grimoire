# 01c-S2: Native-first in the decide chain (mechanism, all tasks off) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `routing.TaskPolicy` gains 01c's two fields, `samples` and
`native_first`, held to spec §4.1's rules in `test_task_policy.py`. When a
task's policy lists the primary's adapter kind and that primary generates
and is `native_capable`, `inference.stages` puts an *isolated* native stage
on the same model, with `retries=0`, before the structured stage. `native_capable`
means the kind has a decisions endpoint and `decide_native` is a known
`yes`. `run_stages` never marks a provider dead for an isolated stage. It
skips a stage as dead only when every route the stage sends is dead.
`inference.reports_distribution(resolved)` says whether the first stage is
native. `TASK_POLICY` lists no task, so no chain changes at landing.
Delivers 01c-C1 (part: everything except the §4.3 evidence and the
`decide.distribution` grader, which are 01c-S3).

**Architecture:** the policy stays code. `routing` stays a pure leaf, and
the two new fields are literals with today's defaults. The production rule
is two functions in `store/inference/resolve.py`, beside `native_only`, so
that the resolver, the chain and a later settings readout ask one rule:

- `native_capable(attempt)` is the per-attempt half: `decide_native` is a
  known `yes`. The kind half of §4.2's definition (the kind has a decisions
  endpoint) is already implied. Every preset whose kind has no endpoint
  lists `decide_native` in `never` (`providers.py:73`, `:79`), so it
  arrives as an adapter-source `no` that no override lifts
  (`capabilities.resolve_caps`). A test pins that invariant (Task 2), so
  `resolve` needs no gateway import (plan gate S1).
- `native_first(resolved)` is the per-resolution half: it is a decide
  resolution, the primary's mode is `structured`, the primary's kind is in
  `routing.policy(resolved.task).native_first`, and the primary is
  `native_capable`.

`inference.stages` asks `native_first` and, when it holds, prepends
`Stage(NATIVE, <primary alone, unflagged>, 0, isolated=True)` to the stages
it builds today. Nothing else about the chain changes.
`ResolvedInference.decision_mode` stays the primary's per-attempt mode
(`structured`), so the settings view, the seam's `incapable` refusal,
`refuse_an_unanswerable_pick` and the rate readout are untouched.

`run_stages` changes in three places, and each is inert for every chain
that can be built today:

1. An isolated stage never adds its provider to `dead`.
2. The dead skip asks whether every route the stage sends is dead: the
   primary, and the riding fallback when there is one.
3. An isolated stage's failures are set aside. They become an item's final
   error only when no later stage reached that item.

The evals runner (`evals/runner.chain`) is not changed. Its forced
`native` backend still allows `unknown`, so 01c-S3 can gather the evidence
on a model whose `decide_native` is unknown.

**Tech Stack:** Python 3.11 (`typing.NamedTuple`, `dataclasses.replace`),
pytest (`monkeypatch.setitem` on `routing.TASK_POLICY`), the shared fakes
(`FakeLLM`, `decision_reply`), a real `LLMClient` over the `_Wire` provider
double in `test_inference_decide_native.py`, and the inference fixtures
(`tests/inference_fixtures.py`).

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01c-decision-distributions-sampling-design.md`
§3.2 (the conditions), §4.1 (the fields and rules), §4.2 (the chain, with
4.2.1 isolation, 4.2.2 retries and 4.2.3 health), §8 (C1), §9 (repo rules
and docs), §10 (the `test_inference_decide*.py` and `test_task_policy.py`
parts), §12 (open questions), and Slices (01c-S2).
Shared structure: `docs/superpowers/plans/2026-10-10-roadmap-01d-s1-task-policy.md`
(`TaskPolicy`, the `violations` checker, `_RULES`, and the note that this
slice tightens the `question`-only allowance).

**Open questions (§12) adopted:**

- **Q2, adopted.** Production requires a known `yes` for `decide_native`, as
  §3.2 recommends. An `unknown` gets today's chain. The user makes a model
  `yes` with a passing test call, a model-facts override (`overrides:
  {decide_native: yes}`) or a catalog that lists `decisions`. The eval
  runner still allows `unknown`.
- **Q3 (settings phrase), deferred from this slice and recorded.** The
  recommendation is a read-only "native first" phrase on a decide route row.
  It is not in S2's scope list (Slices, 01c-S2), and with every task off it
  could never render. So S2 builds the rule the phrase will read,
  `resolve.native_first(resolved)`. It lives on the store side because
  `store/inference/settings.py` cannot import the gateway's `inference`.
  The phrase itself lands with the change that first lists a kind (02-C2a,
  per §4.3). No settings field and no frontend change here.
- **Q4, adopted for the record's location only.** The evidence lives in
  `evals/README.md`, section "Decision distributions". That section is
  01c-S3's. Here, only `TASK_POLICY`'s comment names it as the place a
  `native_first` entry must cite.
- **Q1 (`MASS_SLACK`) and Q5 (`Eligibility`)** are 01c-S1's (the sampler),
  not this slice's.

## Global Constraints

- **No behaviour change at landing.** `TASK_POLICY` stays `{}`, so
  `native_first(resolved)` is False for every resolution and `stages`
  returns exactly what it returns today:
  - `test_decide_chain_golden.py` passes and is not regenerated. It records
    `[s.mode, s.retries]` per stage (`test_decide_chain_golden.py:234`),
    so the new `isolated` field cannot move it.
  - The frozen resolution baselines pass untouched:
    `test_inference_equivalence.py`, and `BASELINE` in
    `test_inference_resolve.py`.
  - `test_a_dual_capable_primary_stays_structured` passes unchanged
    (`test_inference_decide.py:1166`): a known-`yes` model with no policy
    sends no native request. So does
    `test_live_forces_the_native_backend_on_a_dual_capable_model`
    (`test_evals.py:294`): `runner.chain(target) == inference.stages(target)`
    stays one structured stage.
- **`Stage` keeps its positional shape.** `isolated: bool = False` is appended
  after `retries`, so every existing `Stage(mode, chain, retries)` (the
  tests, `evals/runner.chain`) still builds the same stage, and still
  compares equal to it.
- **`TaskPolicy` field order:** 01d's fields, then `samples`, then
  `native_first`, appended after `reads_declines` as 01d-S1's plan says.
  Nothing builds a `TaskPolicy` positionally.
- **`routing` stays a pure leaf.** The new defaults are literals (`False` and
  `()`). The rules that need other modules (`adapters`, the store
  constants) live in the test.
- **No new imports anywhere.** `resolve.py` does not import `adapters`. The
  store may not import the gateway (`adapters.py:19-26`), and doing so would
  pull the provider clients and the SDK import attempt into every `store`
  import. `native_capable` reads only the attempt's capabilities, and the
  kind half rests on the preset invariant that Task 2's test pins.
  `inference.py` already binds `resolve`. `test_task_policy.py` and the
  tests may import `adapters`, as they already do.
- **The resolution is never mutated.** The native-first stage's chain is a
  new `wire.Chain` around `dataclasses.replace(primary.target,
  structured=False)`, or around the target itself when it is unflagged.
- **Every task is off.** A test pins that no `TASK_POLICY` entry sets
  `samples` or `native_first`. The change that lists a kind edits that test,
  and carries §4.3's evidence (01c-S3's record, 02-C2a's flip).
- **No privacy exposure.** Fixtures use invented ids (`vendor/both`, `spare`,
  `Realm OpenAI`, `Saltmarch Local`) and the placeholder cast (Mara,
  Winifred). Nothing reads or describes a real store.
- **Ratchets stay at baseline** (`check-lint`, `check-mypy`, eslint via
  `scripts/ratchet.py eslint`), and no baseline file changes. `run_stages`
  is at mccabe 9 today (`ruff check --select C901` with max-complexity 1
  prints it). The dead test moves into a helper, `_all_dead(stage, dead)`,
  so `run_stages` stays at 10 or below. That threshold comes from
  `ruff.toml:86`.

## Decisions this plan makes beyond the spec text

- **D1: two functions in `resolve`, not one.** The spec names
  `native_capable(attempt)`. This plan also adds
  `native_first(resolved)`, which combines the policy, the mode and
  `native_capable`, and `stages` asks it. That keeps one rule for the chain
  and for the later settings phrase (Q3). It is the same precedent as
  `native_only`, which the resolver and the controls preview both ask.
  `inference.reports_distribution` is then `bool(s) and s[0].mode ==
  NATIVE` over `s = stages(resolved)`, as §4.2 defines it. The `bool(s)`
  guard matters: a resolution with no chain builds no stage, and indexing
  it would raise `IndexError` (plan gate N7). `native_capable` reads only
  capabilities. The kind half is the preset invariant (Architecture; plan
  gate S1).
- **D2: the native-first stage sends the primary unflagged.** §4.2's sketch
  writes `whole.alone()`. A decide resolution's generating primary is
  usually flagged `structured=True` (`resolve._flag_structured`). 01f-S2
  made "flagged" mean "was sent the structured envelope" on every path
  (`llm._unflagged`; `_structured_share` reads the flag). A decisions
  endpoint is never sent that envelope, so the stage carries the target
  unflagged. On the wire this is inert, because `decide_native` reads no
  flag. It keeps the capture's `target` and the invariant truthful. A test
  pins both the unflagged chain and the resolution's untouched target.
- **D3: an isolated stage's failure is not composed into an item's error
  once a later stage took the item.** §4.2.1 isolates the dead list but says
  nothing about error composition. Today `_final` composes an item's
  failures in stage order. `llm.routes_failed` keeps the *first* word's
  kind and window (`llm.py:891-921`), so a native-first 403 (`auth`)
  followed by a structured timeout would report the turn's failure as
  `auth`. That is a refused key, on a chat endpoint that never refused one,
  and `routes.common._decide_error` reports a batch's failure from it.
  `run_stages` therefore keeps an isolated stage's failures in a separate
  `aside` map. They become an item's words only when no later stage reached
  it, which today means a clock refusal (`_refused_unsent`) ended the chain
  right after the isolated stage. The native failure is not lost: its meter
  already filed the ledger row and the error-store line (`Meter.done`), and
  the capture records it.
- **The cost of that error-store line, stated (plan gate S5).**
  `Meter.done` calls `errors.record` for every `error` row
  (`store/usage.py:567-571`). Under native-first, a key whose decisions
  endpoint answers 403 therefore files one error-store entry under the
  task on every native-first call (one per wave of `NATIVE_CONCURRENCY`
  items, since `auth` stops the stage), even when the structured stage
  then answers. The health dot is spared, because 403 is in
  `NATIVE_REJECTED_STATUSES`. This slice changes nothing here, because no
  task is on. CLAUDE.md states it beside the health note (Task 5), as a
  cost that the change which first lists a kind (02-C2a) accepts or
  addresses.
- **D4: a `samples` allow-list in the checker.** §7 says factual
  classifications (continuity, scene-break, voice drift) never sample. §4.1
  lists no rule for that, but the checker can hold it statically, the same
  way `_declines_allowed` holds `_READS_DECLINES`. `_SAMPLES =
  frozenset({"response-selector"})`, and 02 and 13 add their own tasks when
  they add them.
- **D5: the clock's refusal still ends the chain at an isolated stage.**
  `run_stages` breaks on `_refused_unsent` (absorb's `BudgetRefused`)
  whatever the stage is. The clock refuses every later call too, so the
  structured stage could only add another refusal. Isolation is about
  `dead` (a statement about one endpoint), not about the caller's clock.
  A native stage sends no sampling, so a `PresetRefusalError` cannot come
  from it.
- **D6: siblings need no agreement rule.** `inference.for_task` hands one
  sibling's resolution to another (`replace(resolved, task=task)`), and
  `stages` reads `resolved.task` when `decide` runs. Each task therefore
  gets its own `native_first`, unlike `fallback`, which the resolver
  applies. No per-route rule is added.

## Review Focus

- `native_first(resolved)` is False for every resolution `TASK_POLICY = {}`
  can produce, including a role card (`task=""` reads the default policy)
  and a generate resolution (its attempts carry `decision_mode == ""`).
- `native_capable` is `yes` only. `unknown`, a name-rule guess and a preset
  `never` are all False. A preset `never` arrives as an adapter-source `no`
  (`capabilities.resolve_caps`). A kind with no endpoint is False through
  that same `never`, which the preset invariant test pins, with no
  `adapters` import in `resolve`.
- The native-first stage is `retries=0` and `isolated=True`. The structured
  stage after it is exactly today's: `whole` when its fallback rides,
  `whole.alone()` when the fallback is a stage apart.
- In `run_stages`, isolation changes only `dead` and the composition of
  `aside`. `got.stopped` still stops the native stage's own later items
  (`_native`'s stop), and those items reach the structured stage with the
  failure that stopped them.
- With rule 2, a structured stage that carries a riding fallback is not
  skipped when only its primary's provider is dead.
- The checker refuses each planted violation for the rule it plants, and
  still accepts the planted valid policies, which now include sampling and
  native-first ones.

---

### Task 1: the policy fields, and their rules

**Files:**
- Modify: `backend/src/grimoire/store/routing.py`. Changes:
  - append to `TaskPolicy` (`routing.py:92-122`), each with a `#:`
    comment from §4.1;
  - the class docstring no longer says "01c appends";
  - `TASK_POLICY`'s comment (`:125-129`) adds that a `native_first` kind
    is listed only with its §4.3 evidence in `evals/README.md`, "Decision
    distributions", and that the entry's comment names that section, the
    kind and the run's date;
  - the module docstring's "01c shares the structure" sentence says the
    fields are here.
- Modify: `backend/tests/test_task_policy.py`.

**Interfaces:**
- Produces, in `store/routing.py`:
  ```python
  class TaskPolicy(NamedTuple):
      ...                      # 01d's fields, unchanged
      reads_declines: bool = False
      #: The task's caller draws from the decision's distribution (01c-C2,
      #: `draws.draw`); only then may it call `draw` (spec 01c §7).
      samples: bool = False
      #: The adapter kinds whose decisions endpoint is asked first for this
      #: task (01c-C1, spec 01c §4.2): ("openai_compatible",), ("openrouter",),
      #: both, or () for none. Per kind, because the evidence is per kind.
      native_first: tuple[str, ...] = ()
  ```
- Consumed by: Task 2 (`resolve.native_first`), and later by 01c-S1's
  callers (02, 13), which read `samples`.

**The rules** (appended to `_RULES`; each reads one policy and its route):

1. `_escalation_on_decide_only` (extended): its field list gains
   `"samples"` and `"native_first"`. The message is unchanged ("set on a
   route whose operation is not decide").
2. `_escalation_complete` (tightened, as 01d-S1's comment promises).
   Without `escalate_to`, a `question` needs `samples`: "question set
   without escalate_to or samples (stale)". The comment that said "`question`
   alone is allowed" is replaced by the rule.
3. `_samples_valid` (new). Three checks:
   - `samples` needs a `question` ("samples without a question");
   - `samples` is refused beside `"low_margin"` in `escalate_on` ("samples
     beside low_margin"). The comment gives §4.1's reason: escalating a
     low-margin answer replaces a reported distribution with a hop answer
     that may carry none;
   - `samples` is allowed only for a task in `_SAMPLES` (D4). The message
     for a continuity task, `scene-break` or `voice-drift` says that a
     verdict about the fiction never samples (§7).
4. `_native_first_valid` (new). Three checks:
   - every kind satisfies `adapters.decides_natively(kind)` ("native_first
     kind 'anthropic' has no native decisions endpoint");
   - no kind is listed twice;
   - **per kind** (plan gate S3): when `samples` is False, every
     `native_first` kind needs its own `margins` entry ("native_first kind
     'openrouter' on a task that consumes no distribution from it"). A
     sampling task consumes the distribution of every kind. A non-sampling
     task consumes one only through `low_margin`, and 01d says that a kind
     with no `margins` entry never triggers. So `native_first=("openrouter",)`
     beside an `openai_compatible`-only margin would give up the rationale
     for nothing. The comment gives §3.2's reason. A `margins` entry already
     requires `low_margin` (`_margins_valid`), so this one check also covers
     "`samples` or `low_margin`".

`_QUESTION` already pins `response-selector` to
`response_protocol.SELECTOR_QUESTION`, so `_question_pinned` holds a
sampling task's question too. No new pin is needed.

- [ ] **Step 1: Failing tests** (`test_task_policy.py`)
  - `test_policy_defaults_to_todays_behaviour` asserts `default.samples is
    False` and `default.native_first == ()`, and that the default still
    hashes.
  - `test_no_task_escalates_or_drops_its_fallback_at_landing` becomes
    `test_every_task_is_off_at_landing`, and adds `not p.samples` and
    `p.native_first == ()`. Its docstring names both specs' evidence
    sections.
  - `test_a_valid_policy_is_accepted` gains these cases:
    - `speaker-samples`: `{"response-selector": TaskPolicy(samples=True,
      question="next", native_first=("openai_compatible", "openrouter"))}`;
    - `speaker-samples-escalates-declines`: `samples=True`,
      `question="next"`, `escalate_to=routing.CALLER`,
      `escalate_on=("refused", "abstained")` and `reads_declines=True`. A
      sampling task may still escalate where there is nothing to sample;
    - `identity-native-first`: `_FULL_IDENTITY._replace(
      native_first=("openrouter", "openai_compatible"))`. That is a
      low-margin consumer whose margins name both kinds;
    - `question-only` leaves this list, because it is refused now.
  - `PLANTED` gains these cases (the fragment in brackets):
    - `question-only`: `{"scene-break": TaskPolicy(question="over")}`
      ["without escalate_to or samples"];
    - `samples-on-generate`: `{"absorb": TaskPolicy(samples=True)}`
      ["operation is not decide"];
    - `native-first-on-generate`: `{"absorb":
      TaskPolicy(native_first=("openrouter",))}` ["operation is not
      decide"];
    - `samples-without-question`: `{"response-selector":
      TaskPolicy(samples=True)}` ["samples without a question"];
    - `samples-with-low-margin`: `{"response-selector":
      TaskPolicy(samples=True, question="next", escalate_to=routing.CALLER,
      escalate_on=("low_margin",), margins=(("openrouter", 0.2),))}`
      ["samples beside low_margin"];
    - `samples-on-scene-break`: `{"scene-break": TaskPolicy(samples=True,
      question="over")}` ["never samples"];
    - `samples-on-identity`: the same planted on `continuity-identity`
      with `question="decision"` ["never samples"];
    - `native-first-kind-anthropic` and `native-first-kind-claude`: on
      `response-selector`, with `samples=True` and `question="next"` ["no
      native decisions endpoint"];
    - `native-first-kind-unknown`: kind `"vendor"` ["no native decisions
      endpoint"];
    - `native-first-kind-twice`: `("openrouter", "openrouter")` ["listed
      twice"];
    - `native-first-without-consumer`: `{"scene-break":
      TaskPolicy(native_first=("openrouter",))}` ["consumes no
      distribution"];
    - `native-first-kind-without-margin`: `{"continuity-identity":
      _FULL_IDENTITY._replace(margins=(("openai_compatible", 0.2),),
      native_first=("openrouter",))}` ["consumes no distribution"]. This
      is plan gate S3's case: `low_margin` is set, but no margin for the
      listed kind.

    Each planted case asserts that its fragment appears exactly once, as
    `test_each_rule_refuses_its_planted_violation` already does.
  - The module docstring no longer says 01c-S2 "adds its rules". It says
    the rules are here, and names §4.1 of both specs.
- [ ] **Step 2: Run** `cd backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_task_policy.py`.
  Expect a failure (no fields, no rules).
- [ ] **Step 3: Implement** the fields, then the rules. Run again. Expect
  PASS.

### Task 2: `native_capable` and `native_first` in `resolve`

**Files:**
- Modify: `backend/src/grimoire/store/inference/resolve.py`. Changes:
  - no import change: `resolve` stays off the gateway (plan gate S1);
  - add `native_capable` and `native_first` after `native_only`
    (`:945-951`);
  - the `decision_mode` docstring (`:954-963`) says that a generating model
    is still `structured` under native-first. Its native stage comes from
    `inference.stages` (via `native_first`), not from its mode.
- Modify: `backend/tests/inference_fixtures.py`: add `BOTH` and
  `generates_and_decides(client, *, fallback, on=SPARE)`.
- Test: `backend/tests/test_inference_decide.py`, next to the C1 tests
  (`:1150-1210`).

**Interfaces:**
- Produces:
  ```python
  def native_capable(attempt: Attempt) -> bool:
      """Whether `attempt` may be asked natively FIRST (01c §3.2, §4.2): its
      `decide_native` is a known `yes` -- never `unknown`, which
      `native_only` allows because a native-only model has no other way to
      answer, and a native-first one does. That a kind with no decisions
      endpoint never reads `yes` is the presets' business: each lists
      `decide_native` in `never`, an adapter-source `no` no override lifts
      (`capabilities.resolve_caps`), and a test holds every preset to it --
      so the store needs no gateway import (`adapters.py`: the store never
      imports the gateway). Production only: `evals/runner.chain` keeps
      `decides_natively`, so evidence can be gathered on an `unknown`."""

  def native_first(resolved: ResolvedInference) -> bool:
      """Whether `resolved`'s decide chain asks its primary's decisions
      endpoint before its structured stage (01c-C1): a decide resolution whose
      primary is `structured`, whose adapter kind its task's policy lists
      (`routing.policy(resolved.task).native_first`), and which is
      `native_capable`. The one rule `inference.stages` (and a later settings
      readout) asks. Pure; reads only the resolution and the code policy."""
  ```
- `inference_fixtures.generates_and_decides`: the Decision role on
  `openrouter` at `BOTH = ("openrouter", "vendor/both")`. Its catalog row
  is `{"id": "vendor/both", "outputs": ["text", "decisions"], "params":
  ["temperature", "structured_outputs"]}` (`generate` yes and
  `decide_native` yes, both from the catalog, and `structured_output` yes),
  next to `vendor/active`'s `["text"]` row. The fallback is `on` (`spare` by
  default) or none. It mirrors `decide_only`.

- [ ] **Step 1: Failing tests** (`test_inference_decide.py`). Each test
  plants a policy with `monkeypatch.setitem(routing.TASK_POLICY,
  "scene-break", TaskPolicy(native_first=(kind,)))`. That is a runtime
  plant: the static rules hold the code table, and `scene-break` is the
  task the decide helpers ask.
  - `test_native_first_needs_the_policy_the_kind_and_a_known_yes`
    (parametrised). Under `generates_and_decides`:
    - listed `openrouter` gives True;
    - an empty policy gives False;
    - a policy listing only `openai_compatible` gives False;
    - a `scene-break` policy read by a role-card resolution (`task=""`)
      gives False.
  - `test_native_capable_is_yes_only`:
    - `vendor/active` with `outputs: ["text"]` reads `decide_native`
      unknown, and gives False with the policy listing `openrouter`;
    - after `_dual_capable(how)` (catalog, or a passed probe) it gives
      True;
    - a name-rule guess is not a `yes`: no name rule ever yields
      `decide_native`, and the test asserts it for the decider id.
  - `test_every_preset_without_a_decisions_endpoint_never_decides_natively`
    (plan gate S1). For every preset in `providers.PRESETS`: if
    `adapters.decides_natively(preset.kind)` is False, then
    `"decide_native" in preset.never`. This is the invariant that lets
    `native_capable` drop the kind check. A new preset that broke it would
    let a user's `decide_native: yes` make a kind with no endpoint
    native-first, and that kind's call would be refused unsent every time.
  - `test_a_never_preset_is_never_native_first`. A custom
    OpenAI-compatible endpoint (`fx.endpoint(client, "Saltmarch Local")`),
    with the user's override `decide_native: yes` (`PUT
    /llm-connections/{id}/facts`) and the policy listing
    `openai_compatible`. Its `decide_native` reads `no` from `adapter`, and
    `native_first` is False.
  - `test_an_openai_preset_model_can_be_native_first`. The Realm OpenAI
    provider (as `fx.openai_decides_only` creates it), a model the preset
    says generates, and the user's `decide_native: yes`. With the policy
    listing `openai_compatible` it gives True. It is False with the policy
    listing only `openrouter`.
  - `test_native_first_leaves_the_resolution_structured`. With the policy
    planted:
    - `resolved.decision_mode == "structured"`;
    - every attempt's `controls` is what it is without the policy;
    - the seam's `refusal(resolved)` is None.
- [ ] **Step 2: Run.** Expect a failure. **Step 3: Implement.** Run again.
  Expect PASS.

### Task 3: the isolated stage in `stages`, and `reports_distribution`

**Files:**
- Modify: `backend/src/grimoire/inference.py`. Changes:
  - `Stage` (`:164-173`) gains `isolated` and its docstring;
  - `stages` (`:176-211`) gains the branch and a rewritten docstring;
  - `reports_distribution` is new, after `stages`;
  - the module docstring's chain sentence (`:17-27`).
- Test: `backend/tests/test_inference_decide_native.py`, in the "stages"
  section (`:218-305`). Also `test_stages_put_no_structured_stage_after_a_native_one`'s
  docstring.

**Interfaces:**
- Produces:
  ```python
  class Stage(NamedTuple):
      mode: str
      chain: wire.Chain
      retries: int | None
      #: A native-first stage (01c §4.2.1): whatever stops it is a statement
      #: about its decisions endpoint, so `run_stages` never marks its
      #: provider dead, and its failures are not composed into the error of
      #: an item a later stage took. False for every other stage.
      isolated: bool = False

  def reports_distribution(resolved: ResolvedInference) -> bool:
      """Whether `resolved`'s first decide stage is native (01c §4.2): a
      native-only primary, or a native-first one (`resolve.native_first`).
      Pure, sends nothing. A forecast: an item the native stage fails is
      answered by a structured stage with no distribution, so a sampling
      caller still goes through `draws.draw` per item."""
      s = stages(resolved)
      return bool(s) and s[0].mode == NATIVE      # no chain: no stage, no IndexError
  ```
- The branch, inside `stages`' existing `elif resolve.generates(primary):`:
  ```python
  elif resolve.generates(primary):
      if resolve.native_first(resolved):
          target = primary.target
          alone = replace(target, structured=False) if target.structured else target
          chain.append(Stage(NATIVE, wire.Chain(alone), 0, isolated=True))
      chain.append(Stage(STRUCTURED, whole.alone() if apart else whole, None))
  ```
  The structured and fallback stages are built exactly as today.

- [ ] **Step 1: Failing tests** (`test_inference_decide_native.py`, with a
  `_native_first(monkeypatch, *kinds)` helper that plants the runtime
  policy on `scene-break`):
  - `test_stages_for_a_native_first_primary` (parametrised: the fallback
    rides; no fallback):
    - `[(s.mode, s.retries, s.isolated) for s in stages] == [(NATIVE, 0,
      True), (STRUCTURED, None, False)]`;
    - the first chain is `wire.Chain(<primary target, unflagged>)`, with no
      fallback;
    - the second chain `is resolved.chain` (its fallback rides when it is
      set);
    - `resolved.attempts[0].target.structured is True` is unchanged (D2),
      and `resolved.chain` is equal to what it was before.
  - `test_stages_for_a_native_first_primary_with_a_native_fallback`. A
    `decide_only`-style decider as the fallback on `spare` (catalog
    `outputs: ["decisions"]`) gives `[(NATIVE, 0, True), (STRUCTURED, None,
    False), (NATIVE, 0, False)]`. The structured chain is `whole.alone()`.
  - `test_a_planted_policy_leaves_every_other_shape_as_today` (plan gate
    S2). This replaces a "same as with the table empty" comparison, which
    would be tautological because the table is empty at landing. It plants
    `native_first=("openrouter",)` on `scene-break`, then asserts explicit
    expected shapes as `[(mode, retries, isolated)]` plus each stage's
    chain:
    - `decide_only`, no fallback: `[(NATIVE, None, False)]` on
      `whole.alone()`;
    - `decide_only`, fallback on `spare`: `[(NATIVE, None, False),
      (STRUCTURED, 0, False)]`;
    - `_structured_store` on `vendor/active` (`decide_native` unknown), with
      the `spare` fallback riding: `[(STRUCTURED, None, False)]` on
      `resolved.chain`, its fallback included;
    - `_structured_resolution(fallback_mode=NATIVE)`: `[(STRUCTURED, None,
      False), (NATIVE, 0, False)]`.

    The `isolated` default is also checked: `Stage(m, c, r) == Stage(m, c,
    r, False)`.
  - `test_an_unknown_or_unlisted_primary_is_not_native_first`. With the
    policy planted, `vendor/active` (`decide_native` unknown) gets
    `[(STRUCTURED, None, False)]`. With `generates_and_decides` and a policy
    listing only `openai_compatible`, it gets `[(STRUCTURED, None, False)]`
    too.
  - `test_a_kind_with_no_decisions_endpoint_gets_todays_stages` (plan gate
    N9, at the `stages()` level). Setup:
    - an Anthropic provider ("Realm Anthropic", kind `anthropic`) on the
      Decision role;
    - the user's override `decide_native: yes`;
    - a runtime policy listing `"anthropic"`, which the static rule would
      refuse.

    Expected:
    - `decide_native` reads `("no", "adapter")`;
    - `resolve.native_first` is False;
    - `stages` is `[(STRUCTURED, None, False)]`;
    - `reports_distribution` is False.

    The same check runs for a custom OpenAI-compatible endpoint (a preset
    `never`), which is the kind-has-an-endpoint, preset-says-never case.
  - `test_reports_distribution` (parametrised): True for `decide_only`
    (native-only), and for `generates_and_decides` with the policy listing
    `openrouter`. False for these:
    - `generates_and_decides` with no policy;
    - `vendor/active` with the policy;
    - `_structured_resolution`;
    - a generate resolution of `chat`;
    - a resolution with no chain (`fx.neither` resolves to `incapable`, so
      the test uses a `dataclasses.replace(resolved, attempts=())`).
  - `test_stages_put_no_structured_stage_after_a_native_one`: docstring
    only. It says that native-first is the separate branch on a
    *structured* primary (`resolve.native_first`), and that the hand-built
    native-and-generating attempt here is still not given a structured
    stage. The assertion is unchanged.
- [ ] **Step 2: Run.** Expect a failure. **Step 3: Implement.** Run again.
  Expect PASS, and also run `tests/test_decide_chain_golden.py
  tests/test_evals.py`. Both pass untouched.

### Task 4: `run_stages`: isolation, the all-routes dead skip, and the set-aside failure

**Files:**
- Modify: `backend/src/grimoire/inference.py`. Changes:
  - `_settle` (`:704-719`) takes the map it adds failures to;
  - `_all_dead` is a new helper;
  - `run_stages` (`:728-818`) changes at `:779-803`, and its docstring.
- Test: `backend/tests/test_inference_decide_native.py`, in a new section,
  "native first (01c §4.2.1)", after the "a stop carried to a later stage"
  section (`:952-1006`).

**Interfaces:**
- Produces:
  ```python
  def _all_dead(stage: Stage, dead: Sequence[str]) -> bool:
      """Whether every route `stage` sends -- its primary, and on a
      structured stage the fallback riding it -- is on a connection a stage
      before it stopped dead (01c §4.2.1, rule 2). A native stage sends its
      primary alone (`_native` reads only `chain.primary`), so only that
      counts there. Today only a first stage carries a riding fallback, so
      this is `primary is dead` for every chain built."""
      sent = ((stage.chain.primary,) if stage.mode == NATIVE
              else stage.chain.attempts)
      return all(any(_same_connection(target.provider_id, d) for d in dead)
                 for target in sent)
  ```
- `run_stages` body, as changed:
  ```python
  words: dict[int, list[LLMError]] = {}
  #: An isolated stage's failures, per item: an item's words only when no
  #: later stage reached it (D3).
  aside: dict[int, list[LLMError]] = {}
  ...
  for number, stage in enumerate(chain):
      if not pending:
          break
      if _all_dead(stage, dead):
          continue
      got = await _BACKENDS[stage.mode](...)
      ...
      failed = _settle(got, pending, results, aside if stage.isolated else words)
      pending = [...]
      if any(PresetRefusalError or _refused_unsent ...):
          break                      # D5: the clock ends the chain, isolated or not
      if got.stopped is not None and _connection_wide(got.stopped) and not stage.isolated:
          dead.append(stage.chain.primary.provider_id)
  errors = tuple(_final(words.get(unit[0]) or aside[unit[0]]) for unit, _error in failed)
  ```
  A unit in `failed` comes from the last stage its items reached. When
  that stage is not isolated, `words` holds at least its failure. When it
  is the isolated stage (the chain broke after it), `words` is empty for
  those items and `aside` holds their failure. So the expression always
  names a non-empty list.

- [ ] **Step 1: Failing tests.** These use a real `LLMClient` over `_Wire`
  (one OpenRouter double serves `openrouter` and `spare`, both of kind
  `openrouter`), `generates_and_decides`, and the policy planted. Items are
  `_items(n)`, and replies are `decision_reply`.
  - `test_a_403_from_the_native_first_stage_falls_to_the_structured_stage`:
    - setup: `generates_and_decides(fallback=False)`. No fallback, so the
      structured stage is the only route after the native one, and only
      rule 1 (isolation) lets it run (plan gate N8). Also
      `_Wire(streams=[decision_reply(...)], decides=[LLMError("auth",
      "forbidden", status=403)])`, and an observer that records each call;
    - `wire.decided == ["vendor/both"]`;
    - the structured stage answers every item (`backend == STRUCTURED`,
      `errors == ()`);
    - the observer saw no failure (403 is in `NATIVE_REJECTED_STATUSES`).
  - `test_a_rate_limit_from_the_native_first_stage_is_sent_once`:
    - setup: `decides=[LLMError("rate_limit", "slow down", status=429,
      retry_after=1.0)]`, under `_real(wire, retries=2)`;
    - `len(wire.decided) == 1`. That is `retries=0`, and the
      connection-wide stop starts no further item. Two items, so the second
      was never sent;
    - the structured stage answers both, and `len(wire.streamed) == 1`.
  - `test_an_auth_from_native_first_leaves_the_riding_fallback`:
    - setup: `generates_and_decides(fallback=True)` (`spare` riding),
      `decides=[LLMError("auth", "invalid key", status=401)]`,
      `streams=[LLMError("server", "boom", status=500),
      decision_reply(...)]` and `_real(wire, retries=0)`;
    - the streamed models are `["vendor/both", "vendor/spare"]`, and the
      fallback answers;
    - the test runs twice: as built, and with `inference._all_dead` patched
      back to today's primary-only rule (`any(_same_connection(
      stage.chain.primary.provider_id, d) for d in dead)`). Rule 2 alone
      would let this stage run, since `spare` is alive. With rule 2
      neutralised, only isolation (rule 1) can, so the second run proves
      isolation and is not masked by rule 2 (plan gate N8).

    Before this slice, the dead rule would have skipped the structured
    stage and its fallback. The docstring says so.
  - `test_a_native_failure_is_answered_by_the_structured_stage_and_a_refusal_is_not_re_asked`.
    `FakeLLM`-based (`_Endpoint`), with the native script keyed by context:
    - item 0 is `ItemResult({"over": Answer(None, reason="refused")})`;
    - item 1 is `LLMError("server", ...)`;
    - item 2 is `_yes()`;
    - the structured chunk is sent item 1 only (`fake.calls == 1`, one
      item in the user message), and item 0 keeps its refusal with backend
      `native`;
    - `Decision.backend == ""` (two backends answered), and
      `Decision.served` names one route;
    - `Decision.calls` names stage 0 for the native calls and stage 1 for
      the chunk.
  - `test_a_both_failed_item_reports_the_structured_failure`:
    - setup: native 403 (`auth`), then structured `LLMError("timeout",
      ...)` with no fallback;
    - the raised error's kind is `timeout`, and its `words` do not contain
      the 403 (D3);
    - the 403's ledger row is still filed (`_rows()`, `decision_mode:
      native`, `status: error`).
  - `test_a_clock_refusal_at_the_native_first_stage_ends_the_chain` (D5):
    - the native call is refused unsent (`_budget_refused()`, `:778`);
    - the raised error `is` that refusal, so it keeps its type;
    - nothing is streamed.
  - `test_a_structured_stage_stop_still_skips_a_later_stage_on_its_connection`.
    A hand-built `run_stages` chain on one provider id `openrouter`:
    - stages: `Stage(NATIVE, c, 0, isolated=True)`, `Stage(STRUCTURED, c,
      None)`, `Stage(NATIVE, c2, 0)`;
    - the native-first stage fails `auth`, the structured stage fails
      `auth`, and the third stage is never run (`fake.native_requests`
      counts only the first stage's items);
    - the same chain with the first stage `isolated=False` skips the
      structured stage too. That is the contrast which pins rule 1.
  - `test_a_stage_with_a_live_riding_fallback_is_not_skipped_as_dead`
    (rule 2). A hand-built chain:
    - stage A is `Stage(STRUCTURED, Chain(x), None)` on provider `x`, and
      stops with `auth`;
    - stage B is `Stage(STRUCTURED, Chain(x2, y), 0)`: a primary on `x`
      with a fallback on `y` riding it;
    - B runs, and its fallback on `y` answers;
    - `_all_dead` is unit-tested on its own. It is True only when every
      target the stage sends has its provider in `dead`, and False for an
      id-less target, whose id matches nothing. On a NATIVE stage whose
      chain carries a fallback on a live provider, it counts only the
      primary (plan gate N6): a dead primary means True.
  - Existing stop tests pass unchanged:
    `test_a_connection_wide_stop_skips_a_later_stage_on_the_same_connection`
    (a non-isolated native stage still kills), and
    `test_a_native_timeout_stop_does_not_skip_a_generating_stage_on_its_connection`.
- [ ] **Step 2: Run.** Expect a failure. **Step 3: Implement.** Run again.
  Expect PASS.
- [ ] **Step 4: Complexity check.** Run `/home/user/grimoire/backend/.venv/bin/python -m ruff check --select C901 backend/src/grimoire/inference.py`
  and expect no new finding, then run the ratchet (Task 5).

### Task 5: docs, and the gate

**Files:**
- Modify: `CLAUDE.md`, the decide-chain paragraph (`CLAUDE.md:912-928`).
  - Replace "A model that can generate stays on structured generation
    whatever its `decide_native` says: trying native first for one is a
    later decision ... and the chain does not do it today." The new text
    says these things:
    - a model that can generate stays structured unless its task's
      `routing.TaskPolicy.native_first` lists its adapter kind and it is
      `resolve.native_capable` (a decisions endpoint and a known `yes`,
      never `unknown`);
    - in that case the chain asks the decisions endpoint first, in an
      isolated stage on the same model with no retries, and the structured
      stage takes the items it failed;
    - an isolated stage never marks its connection dead, and its failure is
      not composed into an item's error once a later stage took the item;
    - a stage is skipped as dead only when every route it sends is dead;
    - a kind is listed only with the same-model comparison `evals/run.py
      --live --decide-backend` gives. Keep the parenthetical about rationale
      n/a and the pre-send refusals. The comparison is recorded in
      `evals/README.md`, and no task lists a kind today;
    - `inference.reports_distribution` says whether a resolution's first
      stage is native.
  - The sentence "What moves an item on to the next stage is a failed
    call, never an answer" stays as it is. It covers the native-first stage
    too, and the text says so.
  - "Who answered is only named when one did" (plan gate S4). The current
    text says `Decision.provider`/`model` are empty when answers came from
    "more than one backend or route". Under native-first, both stages run
    the same model, so `served` holds one route. `provider`/`model` are
    then named while `backend == ""`. The reworded text: `backend` is
    empty when more than one backend answered, and `provider`/`model` are
    empty when more than one route did.
  - Beside the health note (the `NATIVE_REJECTED_STATUSES` sentence), state
    the cost (plan gate S5). `Meter.done` files an error-store entry for
    every failed native-first call, so a key whose decisions endpoint
    answers 403 logs an error under the task on every native-first call,
    even when the structured stage then answers. The health dot is spared.
    The change that first lists a kind accepts that cost.
- `backend/src/grimoire/inference.py` module docstring, under "Provider
  errors propagate": "composed with the later stages' failures" says that
  an isolated (native-first) stage's failure is not composed into an item a
  later stage took (plan gate S4).
- `backend/src/grimoire/store/inference/resolve.py` module docstring: the
  passage "structured generation for one that can generate, whatever its
  `decide_native` says" gains "(a native-first task asks its decisions
  endpoint first: `native_first`, a stage of `inference.stages`, not a
  mode)" (plan gate S4).
- Docstrings, as listed in Tasks 2-4. Also these:
  - `resolve.decision_mode` ("until native wins on evals (spec 16)" becomes
    "unless its task lists its kind in `native_first`; that stage is
    `inference.stages`', not a mode");
  - `test_a_dual_capable_primary_stays_structured`'s docstring, in the same
    terms;
  - `stages`' last paragraph ("Trying native first ... would add it back
    here") describes the branch.
- [ ] **Step 1:** `cd backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_task_policy.py tests/test_inference_decide.py tests/test_inference_decide_native.py tests/test_decide_chain_golden.py tests/test_inference_resolve.py tests/test_inference_equivalence.py tests/test_inference_settings.py tests/test_evals.py tests/test_docs_guard.py tests/test_import_guard.py tests/test_llm.py tests/test_usage_guard.py tests/test_routing_guard.py tests/test_operation_guard.py tests/test_lowering_retired_guard.py tests/test_format2_play.py`
  Expect PASS.
- [ ] **Step 2:** `make check-lint check-mypy check-templates PY=/home/user/grimoire/backend/.venv/bin/python`
  and `/home/user/grimoire/backend/.venv/bin/python scripts/ratchet.py eslint`
  (never `make check-eslint`, which runs `npm ci`). Expect everything at
  baseline.
- [ ] **Step 3:** Run the whole backend suite once: `make check-py
  PY=/home/user/grimoire/backend/.venv/bin/python`. The only accepted
  failure is the known root-only
  `test_atomic.py::test_a_read_only_record_is_not_silently_replaced`.
- [ ] **Step 4: Commit** `01c-S2: native-first in the decide chain, all tasks off`,
  with the attribution lines the brief gives.

## Handoff notes

- **The settings "native first" phrase (spec §12 Q3) is deferred** out of
  this slice, and the coordinator carries it in the checklist. It should
  land with the change that first lists a kind (02-C2a). It is a read-only
  phrase on a decide route row, driven by `resolve.native_first`, which is
  on the store side so `store/inference/settings.py` can read it without
  importing `inference`. There is no toggle.
- **The error-store cost of a failing decisions endpoint under native-first**
  (D3's note, plan gate S5) is stated in CLAUDE.md. Accepting or addressing
  it is 02-C2a's call.
- **The §4.3 evidence, the `decide.distribution` grader and the
  `evals/README.md` "Decision distributions" section are 01c-S3's.**
- **Departures from spec C1's text** (the coordinator logs them in the
  checklist):
  - D3. An isolated stage's failure is not composed into the error of an
    item a later stage took. C1 says only "anything that stops the native
    stage hands its items to the structured stage". Composing would report
    the decisions endpoint's 403 as the turn's `auth`.
  - D5. A clock refusal (`_refused_unsent`, absorb's `BudgetRefused`) at the
    isolated stage ends the chain, as at any stage. C1's "anything that
    stops the native stage hands its items" does not hold for the caller's
    own clock, because that clock refuses every later call too.
- **The health caveat (spec §4.2.3) is stated in CLAUDE.md.** It is not
  fixed here. Under native first, a 5xx or timeout from the decisions
  endpoint counts against the connection's health, so a working chat
  endpoint can show "failing".

## Plan gate (substitute review, 2026-10-10)

No Codex CLI exists in this environment. An independent reviewer agent,
dispatched by the coordinator, reviewed this plan adversarially against
spec §3.2, §4, §8 (C1), §10 and §12, and against the code at 01d-S1's
commit. It found nothing blocking. It judged D2 through D6 sound (D6
leaves today's chains unchanged) and accepted deferring the Q3 phrase.
Each finding was folded as follows:

- **S1: `resolve` importing `adapters` breaks layering**
  (`adapters.py:19-26`: the store never imports the gateway). It would also
  pull the provider clients into every `store` import. **Folded.** The
  import is dropped, and `native_capable` now means `decide_native` is a
  known `yes`. The kind half rests on the presets: every kind with no
  endpoint lists `decide_native` in `never` (`providers.py:73`, `:79`), an
  adapter-source `no` that no override lifts. A new test holds every
  preset in `providers.PRESETS` to that (Task 2).
- **S2: the "chain is today's without the policy" test was
  tautological.** The table is empty at landing. **Folded.** The test now
  plants `native_first=("openrouter",)` on `scene-break` and asserts
  explicit stage shapes for `decide_only`, an `unknown` model and the
  structured resolutions (Task 3).
- **S3: the distribution rule must be per kind.** A kind with no margin
  never triggers `low_margin`. **Folded.** When `samples` is unset, each
  `native_first` kind needs its own `margins` entry. The new planted case
  is `native-first-kind-without-margin`, and the valid identity case lists
  margins for both kinds (Task 1).
- **S4: docs drift.** **Folded** into Task 5: "Who answered" in CLAUDE.md,
  "not composed into" in place of "heads", the error paragraph in the
  `inference.py` docstring, and the `decide_native` passage in the
  `resolve.py` docstring.
- **S5: the error-store cost is unstated.** **Folded.** It is in D3's note,
  in CLAUDE.md beside the health note, and in the handoff notes.
- **N6: `_all_dead` on a native stage.** **Folded.** On a native stage it
  counts only the primary, with a unit test.
- **N7: `reports_distribution` with no chain.** **Folded.** It is written
  as `bool(s) and s[0].mode == NATIVE`.
- **N8: the fall-through tests did not prove isolation.** **Folded.** The
  403 test uses `fallback=False`. The riding-fallback test also runs with
  `_all_dead` patched back to today's rule, so it passes only through
  isolation.
- **N9: no `stages()`-level test for a kind with no endpoint.** **Folded.**
  Task 3 now tests an Anthropic Decision primary with the user's
  `decide_native: yes` and the kind planted, and a custom OpenAI-compatible
  endpoint whose preset says `never`.
- **N10: the deferred settings phrase.** **Folded.** It is recorded in the
  handoff notes.

## Implementation notes (2026-10-10)

Rebased onto `6364d6c`, which is 01d-S1 (final, `9e2251d`) plus 01h-S1.
Every line reference to `routing.py`, `inference.py` and `resolve.py` in
this plan still holds on that tree. 01d-S1's final `test_task_policy.py`
reads answer vocabularies through `_answers()` and `_keys(item, question)`,
and its landing test asserts `TASK_POLICY == {}`. The rules and planted
cases above were added to that file, and the landing test was extended in
place (`test_every_task_is_off_at_landing`).

Small departures from the task text:

- **The rate-limit test sends one item, not two.** `_Wire.decide` never
  yields, so whether a second item starts before the first fails depends
  on scheduling. One item pins `retries=0` exactly. The stop on a
  connection-wide failure is already pinned by
  `test_a_connection_wide_failure_starts_no_further_native_items`.
- **`test_native_capable_is_yes_only` asserts that the `unknown` is not a
  name-rule guess.** It does this in place of a separate name-rule case.
  The adapter-kind half is held by
  `test_every_preset_without_a_decisions_endpoint_never_decides_natively`
  and by `test_a_kind_with_no_decisions_endpoint_gets_todays_stages`.
- **Mutation checks, run by hand and not committed:**
  - With `and not stage.isolated` removed, four native-first tests fail:
    the 403 test, the rate-limit test, the riding-fallback test in its
    `isolation-only` run, and the structured-stop test with
    `isolated=True`.
  - With the set-aside map removed,
    `test_a_both_failed_item_reports_the_structured_failure` fails.

## Review and final gate (substitute review, 2026-10-10)

No Codex CLI exists in this environment. An independent reviewer agent,
dispatched by the coordinator, reviewed the slice diff against spec §3.2,
§4, §8 (C1) and §10 and against this plan. It ran in place of
`/codex:review` and the final spec gate.

**Verdict: ready to land.** Today's chains were verified unchanged. The
isolated stage, `native_capable` and the policy rules check out. Each
finding was folded as follows, on the commit rebased onto `706ffa3`
(01d-S2):

- **Rebase onto 01d-S2.** The conflict in CLAUDE.md's "Who answered" is
  resolved: this slice's opening on `backend`, then 01d-S2's sentence on
  `ItemResult.served`, then `Decision.served`. `inference.py` and the tests
  merged cleanly.
- **How the two slices interact.**
  `test_a_native_failure_is_answered_by_the_structured_stage_and_a_refusal_is_not_re_asked`
  now asserts that every item's `ItemResult.served` is `("openrouter",
  "openrouter", "vendor/both")`. Both stages stamp the same model.
- **CLAUDE.md's dead-skip sentence** now reads "A non-isolated stage that
  stopped that way skips a later stage every route of which is on that
  connection."
- **The §4.2.3 health caveat** is stated in CLAUDE.md, beside the
  `NATIVE_REJECTED_STATUSES` sentence.
- **Nits:**
  - The error-store cost is reworded to "roughly one per wave of
    `NATIVE_CONCURRENCY` items".
  - The #144 sentence says the same-model structured stage behind a
    native-first stage is a different endpoint, not a resend.
  - A sampling task with a wrong `question` is planted
    (`samples-question-wrong`).
  - D3 and D5 are recorded in the handoff notes as departures from C1's
    text.

Verification after folding:

- the targeted tests and guards, plus `test_decision_triggers.py`,
  `test_decisions.py`, `test_module_references.py` and
  `test_docs_guard.py`;
- the ruff and mypy ratchets.

Results are in the slice's report.

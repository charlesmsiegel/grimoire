# 01d. Decision escalation and per-task fallback policy

**Status:** Draft — spec gate (substitute review) folded in; Codex gate pending.
**Date:** 2026-10-09
**Roadmap:** 01d in `ROADMAP-CHECKLIST.md`. Lane: decision.
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** the bundle draft `02-decision-integration.md` §8
("Escalation"), and the escalation positions in `10-retrieval-query-planning.md`
§2/§6 and `12-bounded-agentic-investigation.md` ("Relationship to the
Decision and planner layers", §10). It extends the landed 01 spec
(`2026-10-07-inference-backend-refactor-design.md`) at §5.5, which keeps the
failure-driven chain, and at §7.4, which says "task-specific escalation uses
what a backend actually reports". It leaves both rules unchanged.

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning.

## Depends on

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| `routing.ROUTES`, `resolve` (fallback, `SAME_PROVIDER`, `fallback_missing`, `role=`), `inference.stages` / `run_stages`, `around` | 01 (landed) | The fallback this policy can switch off, the resolution of the escalation target, and the chain the hop reuses | Hard, and met |
| 01a-C1 per-case and per-call wall time, tokens and money, aggregated per route, backend and `hop` | 01a | Measuring what escalation costs and how long it takes, apart from the base call (section 6.3) | Hard to enable escalation on any task; not needed to land the mechanism |
| 01a-C3 repeatable `--decide-backend`, `--out`, `--compare` | 01a | Comparing "no escalation" with "escalation at margin m" on one task | Hard to enable |
| 01b-C1 capture at every decide site | 01b | Hop calls are captured wherever the site captures. Today only the speaker pick does | Soft |
| `routing.TaskPolicy` (shared structure) | this spec, or 01c if it lands first | 01c adds `samples` and `native_first` to the same structure | Shared structure |
| `decisions.CallRecord`, `Decision.calls` (shared structure) | 01a | Each hop call appears in `Decision.calls` as any call does, marked with its `hop` | Shared structure. Until it lands, `Decision.usage` and `Decision.escalations` carry the hop |

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 01d-C1 `routing.TaskPolicy` | 02 (H for 02-C5a), 01g (S), 10 (S), 11 (S) | Policies for 02's play and kit tasks; the `tool-decision` route's policy (01g-C5); `fallback="none"` and escalation on 10's `history_check` and `history_plan` tasks; 11's capped Decision pass |
| 01d-C1 `routing.TaskPolicy` (shared structure) | 01c (coordination) | 01c adds `samples` and `native_first` to the same structure; whichever spec lands first creates it |
| 01d-C2a trigger evaluation | 02 (H for 02-C5a), 10 (S), 11 (S), 12 (S) | Deciding which items are unsure, with the answer filter (`escalate_answers`) 02 asked for; 10's sufficiency verdict; 11's epistemic classes; 12 deciding, with no decide hop, that cheap judgments were unsure enough to start an investigation |
| 01d-C2b one escalation hop | 02 (H for 02-C5a), 10 (S), 11 (S), 12 (H for RP mode) | A second opinion from a stronger role (02's continuity and epistemic kit); 10's repair hop escalation; 12 supplies a tool-loop resolver as the next hop after 10's repair hop |
| 01d-C3 thresholds | 02 (H for 02-C5a) | Where the margin lies, per task and per native endpoint kind |

09 and 10 also reach this spec through 02-C5b (history relevance), whose
policy 02 states. 13 has no edge to 01d: an NPC action decision may set a
policy here, but nothing in 13-C3 requires one.

## 1. Current state (reconciled against main)

- **The chain moves on failures, never on answers.** `run_stages`
  (`backend/src/grimoire/inference.py:595-681`) runs each stage over the items
  still pending. An item moves on only when its stage *failed* it. The
  docstring says: "an answer, a `refused` or a `None` from a well-formed reply
  included, is final". CLAUDE.md gives the reason: "re-asking them elsewhere
  would be asking until something agreed".
- **The chain's stages come from the resolution, not the task.**
  `inference.stages` (:147-182) gives the selection's one backend, and then
  the role fallback as a stage of its own, but only when either attempt is
  native. A structured fallback rides the facade (`resolve._rides`,
  `store/inference/resolve.py:893-903`). The fallback stage gets one attempt
  (`retries=0`).
- **The fallback is per role, and nothing turns it off per task.**
  `resolve.resolve` (:700-848) attaches the role's fallback. It drops the
  fallback when it is unreadable, when it cannot send, or when it is on the
  primary's own provider (`SAME_PROVIDER`, :107), except behind a decide
  primary that cannot generate (`_apart`, :943-953). A fallback known
  incapable is kept but reported (`fallback_missing`). The reason for any
  drop is in `fallback_problem`, which the settings view shows (01s,
  `droppedFallbackWords`).
- **The resolver answers per route, and `for_task` relies on that.**
  `inference.for_task` (:791-809) hands a resolution of one task to a sibling
  task on the same route, because "two tasks of one route resolve to the same
  attempts".
- **A route can be resolved through a named role.** `resolve(..., role=)`
  resolves that role via `cascade.choose_role`, including a campaign's own
  override of it. Today only the settings view uses it.
- **A margin exists only where a backend reported one.** `Answer.probability`
  and `Answer.distribution` come only from native endpoints (`decisions.py:220-254`,
  `native_answer` :866-901). The structured parser sets neither. 01 §7.4
  forbids a synthetic confidence, so a structured answer has no margin.
  01c-C1 records the same rule for generating models.
- **The non-answers are already typed.** `abstained` covers a tie at the
  top, an argmax on `NONE_KEY`, a predicate at exactly 0.5, and an explicit
  null on an `allow_none` choice (`decisions.py:908-940`, `_read_choice`
  :660-671). `refused` is OpenAI's per-question refusal
  (`openai_compatible.py:216-223`). OpenRouter documents no refusal.
- **Budgets run inside each call's meter.** `around` (:103) is how absorb's
  `_Budget.run` (`routes/scenes.py:1939-1990`) and `routes.common._bounded_call`
  (:544-610) bound a decide call. `BudgetRefused` (`routes/scenes.py:1899-1912`)
  is a call refused unsent, and it ends the chain (`_refused_unsent`).
- **Per-item provenance stops at the backend.** `ItemResult.backend` says
  `native` or `structured` (`decisions.py:257-273`). Which provider and
  model answered is known only for the whole `Decision` (`served`, :284-304).
- **The five decide tasks** are `response-selector` (route `speaker`),
  `continuity-identity` and `continuity-reconcile` (`continuity`),
  `scene-break` (`scene_break`) and `voice-drift` (`voice_drift`)
  (`store/routing.py:82-139`). Their deciding questions are `next`
  (`response_protocol.SELECTOR_QUESTION`), `decision` (identity and reconcile
  `DECISION_ID`, `continuity/identity.py:121`, `continuity/reconcile.py:1435`),
  `over` (`scene_break.QUESTION_ID`, :237) and `verdict`
  (`voice_drift.QUESTION_ID`, :242).

## 2. Goal (and what is explicitly not the goal)

Goal:

1. A per-task policy, in `routing`, that says whether the task may fall back
   and whether, how and to which role it escalates (C1).
2. A one-hop escalation that runs after the chain has answered. When an
   answer's own report says it is unsure (a low margin), or the backend
   declined (abstained) or refused, the item is handed once to a declared
   stronger resolver. The hop is metered and captured. It does not change
   the failure-driven chain at all (C2).
3. Thresholds per task and per reporting endpoint, argued from structure and
   tuned with 01a's evals (C3).

Not the goal:

- Enabling escalation on any task. Every task ships with escalation off, and
  the policy reproduces today's behaviour exactly. Each task is switched on
  only by the change that carries its eval (section 6.3). A route likewise
  flips to `decide` only in the change whose call site decides (CLAUDE.md).
- A confidence score that is comparable across backends, or escalation on a
  structured answer's "confidence". There is none, and none is invented.
- Voting, ensembles, or more than one hop.
- Specifying an agent. 12 may supply a tool loop as a `CALLER` resolver
  (C2b), or use the trigger evaluation (C2a) alone to decide to start an
  investigation; this spec specifies only the hop's contract.

## 3. Failure-driven fallback and answer-driven escalation are different things

| | Failure chain (01, unchanged) | Escalation (this spec) |
|---|---|---|
| Moves an item when | its call failed (`LLMError`, refused unsent) | its call succeeded and the answer says it is unsure, declined, or refused |
| Next resolver | the role's fallback | the task policy's `escalate_to` role |
| Depth | one stage per attempt | exactly one hop, whose answer is final |
| On the next resolver failing | items keep their failures; `Decision.errors` | the original answer stands; nothing in `Decision.errors` |
| Decided by | the resolution (role settings) | the code policy (`routing.TaskPolicy`) |

Escalation re-asks an *answered* item, which the chain's rule forbids. It
does so only under three constraints, so that it does not become "asking
until something agreed":

1. **The policy declares it per task.** The default is no escalation.
2. **One hop.** The hop's result never re-enters trigger evaluation.
3. **The hop goes to a different model.** A hop that would land on the model
   that already answered is skipped (`same_model`, section 5.4).

It is implemented *after* `run_stages` returns, by calling `run_stages` again
on the escalated subset with a one-stage chain. `run_stages` and `stages`
are not modified by this spec. (01c's `native_first` modifies `stages`; the
two are independent.)

## 4. The task policy (C1)

### 4.1 The structure

`store/routing.py` gains a `NamedTuple` and a table. 01c adds two fields of
its own (`samples`, `native_first`). Whichever spec lands first creates the
structure.

```python
TRIGGERS = ("low_margin", "abstained", "refused")
FALLBACKS = ("role", "none")
ESCALATION_ROLES = ("primary", "fast")
CALLER = "caller"   # a caller-supplied resolver (section 5.2)

class TaskPolicy(NamedTuple):
    #: "role": the role's fallback, as today. "none": the resolver attaches
    #: none, and says so in `fallback_problem` (`NO_FALLBACK_POLICY`).
    fallback: str = "role"
    #: The role escalation hands an item to; "" = never escalates.
    escalate_to: str = ""
    #: What hands an item over (subset of TRIGGERS).
    escalate_on: tuple[str, ...] = ()
    #: The deciding question id the triggers read (one per task).
    question: str = ""
    #: low_margin thresholds, per native endpoint kind: (("openrouter", 0.2), ...).
    margins: tuple[tuple[str, float], ...] = ()
    #: Items one decide call may escalate (section 6.2).
    escalate_max: int = 8
    #: Optional answer filter (02 named it): when non-empty, `low_margin`
    #: fires only on an item whose deciding answer, as a key (option id,
    #: `str(level)`, "true"/"false"), matches an entry. An entry ending in
    #: ":" matches by prefix ("existing:"), any other entry exactly.
    #: Empty = every answer.
    escalate_answers: tuple[str, ...] = ()
    #: Whether the task's caller reads `abstained` and `refused` on its
    #: deciding question as answers (section 5.3). Only then may a hop's
    #: decline replace a base answer.
    reads_declines: bool = False

TASK_POLICY: dict[str, TaskPolicy] = {}   # empty at landing: today's behaviour

def policy(task: str) -> TaskPolicy:
    return TASK_POLICY.get(task, _DEFAULT)
```

`routing` stays a pure leaf (`store/routing.py:15-16`). Every value is a
literal. `margins` is a tuple of pairs, not a dict, so the default stays
immutable and hashable.

### 4.2 The fallback field

`resolve.resolve` reads `routing.policy(task).fallback` and, for `"none"`,
attaches no fallback attempt. When the role had a fallback configured, it
sets `fallback_problem = NO_FALLBACK_POLICY` ("this task's policy sends no
fallback"), next to `SAME_PROVIDER` (:107). When the role has none, it sets
nothing, so the readout never reports dropping a fallback that did not
exist.
Nothing refuses on it, as with every other `fallback_problem`. The settings
view's dropped-fallback line (01s, `droppedFallbackWords`) gets a matching
sentence. A call made with `role=` and no task (the settings view's role
cards) reads the default policy, so the role card is unchanged.

**A route's tasks must agree on `fallback`.** `for_task` hands one task's
resolution to a sibling on the same route (`inference.py:791-809`). If the
siblings' fallback policies differed, the sibling would run with the wrong
fallback. `test_task_policy.py` fails a route whose tasks disagree. The
escalation fields are read by `decide` per task, so siblings may differ there.

`fallback = "none"` is for a task where a second model can only spend: a
planner whose failure already falls back to cheaper retrieval (10), or a
tool loop with a spend ceiling (12). It applies to generate tasks as well as
decide tasks. No existing task changes.

### 4.3 Rules held by `backend/tests/test_task_policy.py`

- every key is a task in `TASK_ROUTE`;
- `fallback` is in `FALLBACKS`, and agrees across a route's tasks;
- the escalation fields (`escalate_to`, `escalate_on`, `question`, `margins`)
  are set only on a task whose route's `operation` is `decide`. A non-empty
  `escalate_to` needs a non-empty `escalate_on` and a `question`, and
  `escalate_on` and `margins` are empty without it. `question` is shared
  with 01c: it is also required by `samples`, so a sampling task that does
  not escalate sets `question` alone;
- `samples=True` (01c) is refused together with `low_margin` in
  `escalate_on`. A low-margin answer is the one sampling exists for, and a
  hop that replaced its distribution with one that may carry none would turn
  the draws that most needed a distribution into `basis: answer` (02 asked
  for this rule);
- `escalate_answers` is set only beside `low_margin`. Each exact entry is a
  key the deciding question can answer (checked against the store
  constant's question builder where one exists). A prefix entry (ending in
  ":") is checked against the store's prefix constant (for example
  `identity.EXISTING_PREFIX`, `continuity/identity.py:123-126`), because
  per-row options such as `existing:<id>` cannot be listed statically;
- `reads_declines=True` only where the deciding question's caller maps
  `abstained`/`refused` to an outcome of its own (the speaker pick's
  `selection_of` maps `abstained` to "hand control back"). It is refused on
  the continuity tasks, whose `decision` reads a decline as not read
  (`decisions.was_read`, `decisions.py:634-647`);
- `escalate_to` is `CALLER`, or is in `ESCALATION_ROLES` and not the route's
  `default_role`. Otherwise the hop would resolve to the same selection and
  always be skipped as `same_model`. This catches only the static case:
  `escalate_to="fast"` on a decide route whose Decision role is unset
  (Decision inherits Fast) also resolves to the base's own model, and every
  hop is then skipped `same_model` per item. That is correct, and costs no
  call, but it is a no-op the settings readout should name (section 11);
- `question` equals the store constant the call site builds its item with.
  The test imports `SELECTOR_QUESTION`, both `DECISION_ID`s, and both
  `QUESTION_ID`s, and pins them;
- `low_margin` in `escalate_on` needs at least one `margins` entry. Each
  entry's kind is an adapter kind whose `decides_natively` is true
  (`adapters.decides_natively`, `adapters.py:305`), and each value satisfies
  `0 < m <= MAX_MARGIN` (section 6.1);
- `1 <= escalate_max <= decisions.MAX_ITEMS_PER_CALL`.

## 5. The escalation helper (C2)

### 5.1 C2a: trigger evaluation (pure)

In `decisions.py` (a leaf, so 12 can call it without the operation):

```python
def margin(answer: Answer) -> float | None:
    """How far the answer's own report puts the ANSWERED key above its best
    rival: negative when the report ranks another key higher. None when the
    answer is None or nothing was reported (section 6.1 has the formula)."""

@dataclass(frozen=True)
class Trigger:
    index: int            # item position in the batch
    trigger: str          # "low_margin" | "abstained" | "refused"
    margin: float | None  # set for low_margin; None otherwise

def triggers(results: Sequence[ItemResult], *, question: str,
             escalate_on: Sequence[str], margins: Mapping[str, float],
             answers: Collection[str] = ()) -> tuple[Trigger, ...]:
    """Each item whose `question` answer meets a listed trigger, in priority
    order: refused, then abstained, then low_margin by margin ascending; ties
    by index."""
```

- `refused`: `reason == "refused"`.
- `abstained`: `reason == "abstained"`.
- `low_margin`: the answer is not `None`, `margin(answer)` is not `None`, the
  item's server kind (`ItemResult.served[0]`, section 5.6) has a `margins`
  entry, and `margin < margins[kind]`. A kind with no entry never triggers.
  An unlisted endpoint therefore keeps today's behaviour; it is not given
  some default threshold.
- **The answer filter** (`answers`, the policy's `escalate_answers`) narrows
  `low_margin` only: when it is non-empty, an item whose deciding answer
  matches no entry does not trigger, however small its margin. An entry
  ending in ":" matches by prefix. 02-C5a uses exact entries to escalate only
  the permissive epistemic answers (`known`, `suspected`, `experienced`),
  where a wrong answer is a leak. Continuity could use `"existing:"` to
  escalate a low-margin merge but not a low-margin `new`. `refused` and
  `abstained` carry no answer, so the filter does not apply to them. Keys
  are compared as the record spells them: an option id, `str(i)` for a score
  level, `"true"`/`"false"` for a predicate.
- `unreadable` and `error` never trigger. The first is an answer the item
  got and read badly, and 01 says it is not re-asked. The second is the
  failure chain's business.

On a structured Decision model, only `abstained` can fire. Nothing else a
structured reply returns says it was unsure, and a verbalised confidence
would be the synthetic one 01 forbids (01c-C1). This is the main way 01c's
`native_first` and this spec interact.

### 5.2 C2b: the call-site contract

`inference.decide` gains one keyword:

```python
#: A caller-supplied next resolver: given the triggered items and their
#: triggers, its reply (below).
Resolver = Callable[[tuple[Item, ...], tuple[Trigger, ...]],
                    Awaitable["ResolverReply"]]

Escalator = Callable[[], Awaitable[tuple[ResolvedInference | Resolver | None, str]]]

@dataclass(frozen=True)
class ResolverReply:
    #: One per triggered item: an ItemResult whose `backend` is in
    #: `decisions.BACKENDS` (a tool loop that ends in a generated answer
    #: stamps "structured") and whose `served` is the final target's
    #: (kind, provider_id, model); None where the resolver produced nothing.
    results: tuple[ItemResult | None, ...]
    #: The ledger rows its calls filed, each carrying hop="escalation".
    rows: tuple[dict, ...] = ()

async def decide(task, items, *, client, resolved, explain="", campaign="",
                 scene="", post=None, round_id="", capture=None, around=None,
                 escalation: Escalator | None = None) -> decisions.Decision
```

- `escalation` is a thunk the call site builds over a new seam,
  `routes.common.escalation_inference(task, cid) -> (UsableInference | None,
  reason)`. It is soft: it never raises a 409. It resolves
  `inference.resolve(task, cid, operation="decide",
  role=policy.escalate_to)  # routing-ok: the escalation seam` and wraps the
  result in the existing soft pattern (`_soft_resolved`, `routes/common.py:1519`),
  so a refusal (missing key, incapable) comes back as `(None, sentence)`
  rather than a third way of failing softly.
- **What a `role=` resolution runs with** (review S3). `resolve(..., role=)`
  has no route (`resolve.py:766`). So: the hop sends the escalation role's
  **own** sampling preset, not the decide route's preset, because the route's
  preset was chosen for the route's model. A native hop sends none, as any
  native call does. `escalation_inference` itself checks the base route's
  `requires` against the hop's primary (`resolve._missing` over
  `_needs(route, "decide")`), and returns `(None, the incapable sentence
  naming the base route's label)` when one is known missing, so the hop
  never runs on a model the route could not. The refusal sentence names the
  base route (`routing.label_for`), not "This decision". It is a thunk so that the resolution, which
  reads files, happens only when some item triggered, and in the threadpool:
  `lambda: run_in_threadpool(lambda: escalation_inference("continuity-identity", cid))`.
  The task stays a literal at the call, where `test_routing_guard.py` reads
  it.
- `decide` raises `ValueError` before any meter opens when
  `bool(policy.escalate_to) != (escalation is not None)`. A call site and its
  policy cannot drift apart: turning a task's escalation on means changing
  the call site in the same change.
- A returned resolution must be for the same `task` and `operation="decide"`,
  checked as `decide` checks `resolved`.
- **The declared next resolver may be a caller-supplied `Resolver`**, when
  the policy's `escalate_to` is `CALLER`. 12 uses this: after 10's repair hop
  (10-C2) has run, its next hop is a bounded tool-loop resolver (01g-C2a
  under a 12-C2a budget) rather than a stronger role. The rules that do not
  change: it is one hop, run once, on the triggered subset only, after the
  unchanged chain; its results are merged by section 5.3's replacement rule;
  a resolver that raises `LLMError` (or returns None for an item) leaves the
  original answer; `CancelledError` passes through. What moves to the
  caller: the resolver opens its own meters and must stamp
  `hop="escalation"` on the rows it files (01g-C3's rows carry it through
  their targets' account), captures its own calls, and runs under its own
  budget, since `decide`'s `around` cannot bound a loop. Its `rows` join
  `Decision.usage` and, through 01a's `CallRecord`, `Decision.calls`; its
  results' `served` join `Decision.served`, under the rules of section 5.5. `same_model` is not
  checked for it: the resolver is not one model. `decide` raises `TypeError`
  before any meter opens if a `Resolver` comes back for a policy that names
  a role, or a resolution for a `CALLER` policy.

### 5.3 The flow

```text
base = await run_stages(task, items, stages(resolved), ...)   # unchanged; may raise
found = triggers(base.items, question=..., escalate_on=..., margins=..., answers=...)
if not found: return base
esc, why = await escalation()                                  # None -> all skipped (why)
role hop: stage = the escalation primary's one stage: mode by decision_mode
          (native or structured), Chain(primary) ALONE, retries=0,
          account hop="escalation"
          (no stage, e.g. it can neither generate nor decide -> skipped "incapable")
          per item: same (provider_id, model) as item.served -> skipped "same_model"
take = found, in priority order, up to escalate_max items AND, for a
       structured stage, only as many as one `decisions.chunks` chunk holds;
       the rest -> skipped "cap"
got = role hop:   await run_stages(task, take, [stage], same campaign/scene/
                  post/round_id, capture=hop-marked capture, around=around)
      caller hop: await resolver(take_items, take_triggers)
      except LLMError (BudgetRefused, PresetRefusalError included) -> each
      item in `take` "failed", the original stands
merge, per item (below)
```

- **If the base raises, there is no escalation.** When no item answered,
  `run_stages` raises (:671-673). There is nothing to escalate, and the
  caller's error handling is untouched.
- **A role hop is one stage on one attempt.** It has no fallback and no
  retry budget (`retries=0`, as a fallback stage has), because a lost
  escalation costs only the second opinion: the original answer stands. Its
  backend follows `resolve.decision_mode` on the escalation primary. A
  native-only `escalate_to` role is a native stage. Native-first (01c) never
  applies to the hop.
- **The cap is per chunk, not per item alone** (review S6).
  `decisions.chunks` splits on the enum, character and property budgets as
  well as on `MAX_ITEMS_PER_CALL` (`decisions.py:724-753`), and an identity
  item carries one `existing:<id>` option per candidate. So the hop takes
  triggered items in priority order only while they fit the **first** chunk
  `decisions.chunks` would make of them. A structured hop is then always one
  call, plus at most one prompt-only re-send. A native hop is at most
  `escalate_max` (at most 8) items, which is two waves of
  `NATIVE_CONCURRENCY`.
- **Merge, per question** (review S2). An item is replaced only when the
  hop's deciding `question` answer is one of:
  - a non-`None` answer;
  - `abstained` or `refused`, and only when the task's policy has
    `reads_declines=True`.

  Anything else counts as `failed`, and the original item stands whole.
  This covers an `unreadable` (a garbled reply, `NO_OBJECT`, `NO_ITEM`, an
  off-roster value), an `error`, or a decline on a task that does not read
  declines.

  When the item is replaced, the hop's deciding answer and its rationale are
  taken. Each **other** question keeps the base's answer unless the hop's
  answer to it `was_read`. A hop that answers `decision` but garbles an
  evidence question therefore keeps the base's evidence scene. This is
  consistent with 01's rule that each question of an item stands alone. The
  merged `ItemResult.backend` and `served` are the hop's, because the
  deciding answer is.
- **Why declines are opt-in** (review S1). Continuity's mappings read an
  `abstained` or `refused` `decision` as not read (`decisions.was_read`):
  the row stays `unchecked`, and the candidate gets no proposal and is asked
  again on every sweep. If a hop's decline replaced a read answer, the batch
  could lose its only read answer. `_decide_error` would then report a
  sibling's `error` as the phase's failure. A task opts in only when its
  caller maps a decline to an outcome. For the speaker pick, an abstention
  hands control back.
- **One hop, by construction.** `triggers` runs once, on `base` only.
- **The caller's `explain` is reused**, so a hop to a generating role
  returns a rationale exactly when the call asked for one.

### 5.4 Same model, and why it is skipped

A hop to the `(provider_id, model)` that already answered the item would
send the same question to the same model again. That is the "second send of
the call that failed" which 01 §5.5 drops with `SAME_PROVIDER`, applied to
an answer. It is skipped with reason `same_model`. A hop to another model on
the same provider is allowed, as 01 allows a native-only primary's
same-provider fallback on another model (`_apart`). An example is an
OpenRouter decisions model escalating to an OpenRouter generating model. The
comparison is per item, because the chain's fallback stage may have answered
some items and the primary others.

### 5.5 The result

`decisions.Decision` gains `escalations: tuple[Escalation, ...] = ()`:

```python
@dataclass(frozen=True)
class Escalation:
    index: int
    trigger: str                 # "low_margin" | "abstained" | "refused"
    margin: float | None
    before: ItemResult           # the base answer
    outcome: str                 # "answered" | "failed" | "skipped"
    detail: str = ""             # "cap" | "same_model" | "incapable" |
                                 # "declined" | the soft resolution's
                                 # sentence | "kind: detail"
    served: tuple[str, str, str] = ()  # (kind, provider_id, model) of the hop
```

- `items` holds the final results: escalated ones replaced, and the rest as
  they were. `usage` is base rows plus hop rows. `served` is the union in
  first-answered order. `backend`, `provider` and `model` follow the
  existing rule (`decisions.py:292-297`): when a hop answered with another
  model, more than one route answered, so they are `""`.
- **`errors` is the base's, unchanged.** A failed hop is not an unanswered
  unit.
- **The guarantee, with its condition.** On a task with
  `reads_declines=False`, a merged deciding answer is always non-`None`, so
  it `was_read` whenever the base's was. `routes.common._decide_error`
  (:1565), which reads `errors` and `was_read`, therefore reports exactly
  what it reported before, and a hop cannot turn an absorb phase from `ok`
  to `failed`. A task with `reads_declines=True` declares that it does not
  rely on `was_read` for its deciding question, and `test_task_policy.py`
  refuses the flag on the tasks that do.
- **`Decision.calls`** (01a's `CallRecord`, a shared structure): each hop
  call is one record, marked `hop="escalation"`, after the base's calls in
  the order made, and it points back to the item indices it carried. A
  caller resolver's rows become records the same way. Until 01a lands,
  `usage` and `escalations` carry the same information.
- The per-item "did the escalation answer" signal is
  `Decision.escalations[*].outcome`, not per-item provenance.

### 5.6 Per-item provenance

`ItemResult` gains `served: tuple[str, str, str] = field(default=(),
compare=False)`, holding `(kind, provider_id, model)`. The backend that
answered stamps it, as it stamps `backend`. `_structured` reads it from the
answering holder's `llm.ATTEMPTED` target (`inference.py:250-256`), and
`_native` from `named`. It is `compare=False` so that every existing equality
assertion on `ItemResult` stays as it is. C2a reads `served[0]` for the
threshold, and section 5.4 reads `served[1:]`. 01c's replay record reads the
same field.

### 5.7 Metering, capture, budgets, cancellation

- **Metering.** Each hop call opens its meter inside `run_stages`, as every
  decide call does, under the same `task`, campaign, scene, post and round.
  The hop's target carries a new account field, `hop="escalation"`.
  `wire.Account` gains `hop: str = ""` and `llm_usage.ACCOUNT_FIELDS` gains
  `"hop"`, and the existing test holds the two equal. The ledger row then
  files `hop: "escalation"`. An absent field ("") files nothing, so every
  existing row is unchanged. No money column moves and no rollup reads the
  field, so `usage_rollup.VERSION` is not bumped. Its `role` is the
  escalation role's (`_account`), so a Costs breakdown by role already shows
  where the hop's spend went.
- **Capture.** Each hop call is handed to the call site's `capture`, as any
  call is, with `outcome` extended by `"hop": "escalation"`. Today that is
  the speaker pick only (`routes/character_turns.py:752-756`). With 01b,
  every site captures. The capture stays off the decide path (`_captured`,
  `inference.py:333-346`).
- **Budgets.** The hop runs under the same `around`. A spent absorb budget
  refuses the hop unsent (`BudgetRefused`): the hop is `failed`, the original
  stands, and the phase reports what it would have reported. Absorb's
  `block["attempted"]` is already true by then, since the base sent first.
  Under `_bounded_call` (reconcile), each hop call gets the full ceiling. The
  added wall time is bounded in section 6.2. One display-only gap remains:
  `_noting(client, resolved, holder)` charges an overrun to the *base*
  resolution when the holder was not yet stamped, so a hop that overruns
  before its first byte can mark the base's connection in the health
  registry. That is the same blind spot `_bounded_call` has today
  (`routes/common.py:544-610`), and it is accepted, not fixed here.
- **Cancellation** passes through untouched. Open meters file `aborted`, as
  in `_native` and `_once`.
- **Errors and health.** A hop call that fails files its `error` row at
  `Meter.done` and tells the health registry, like any call. It did fail. A
  clock refusal is `NOT_A_FAILURE`, as today.

## 6. Thresholds (C3)

### 6.1 The margin

The margin is measured **from the answered key `a`**, not from the top of
the report (review B1). Both adapters pass an endpoint's explicit `choice`
as `chosen`, and `native_answer` keeps it "whatever the probability beside
it says" (`decisions.py:866-901`, `:928-936`; `openrouter.py:166-167`;
`openai_compatible.py:238-239`). So a reply of `choice: known` with
`{known: 0.1, narrator_only: 0.9}` is the answer `known`. Measuring the top
two would give it a margin of 0.8, the most confident reading of the least
sure answer the report can describe.

For a reported distribution `w` (choice or score) with mass
`M = math.fsum(w)` (correctly rounded, so the same on every supported
interpreter; builtin `sum()` over floats changed in 3.12, the finding 01c
fixes for draws):

```text
w(a) = the answered key's reported weight (0 when the report omits it)
r    = max over k != a of w(k)          (0 when no other key was reported)
M >= 1:  margin = (w(a) - r) / M
M <  1:  margin = w(a) - max(r, 1 - M)
```

For a predicate with probability `p = P(true)`, take `p(a) = p` when the
answer is True and `1 - p` when it is False: `margin = 2 p(a) - 1`.

- **The margin is negative when the report ranks a rival above the
  answer**, and so is always below any threshold. An answer the report
  itself puts below another always triggers `low_margin` (subject to the
  answer filter and a threshold for its kind). That is the case 02-C5a's
  filter exists to catch.
- **The `M < 1` branch is the conservative one.** Mass the endpoint left
  unreported could all belong to a rival, so the margin may not assume
  otherwise. Normalising a partial report would turn a single 0.6 into a
  certainty. 01c refuses to draw from a partial report for the same reason.
- **The margin is computed over the raw keys, not regrouped.** For
  continuity's folded options, two `existing:<id>` options splitting the
  mass is an ambiguity about *which* record, and the same holds for a
  duplicate's two directions. That is the kind of ambiguity a second opinion
  is for. Section 11 has the grouped alternative as an open question.
- **A score's adjacent levels are rivals, deliberately** (review M6). A
  2-versus-3 split on a 5-level scale reads as unsure. The margin asks only
  whether the answered level is clearly the report's choice, and a task
  that cares only about distance on the scale should not list `low_margin`
  for a score.

### 6.2 Structural bounds and starting values

- **Per task and per reporting endpoint, never global.** 01 §7.4: "task-
  specific escalation uses what a backend actually reports". OpenAI's and
  OpenRouter's decisions endpoints are different estimators, and nothing
  makes 0.2 from one mean what 0.2 from the other means. The finest axis
  code can name is the adapter kind. A per-model threshold would need the
  user's model list in code (section 10).
- **The structured backend has no threshold**, because it reports no
  margin (section 5.1).
- **`MAX_MARGIN = 0.5`.** For a predicate, a threshold above 0.5 escalates
  every answer whose P is below 0.75. At that point most answers escalate,
  and the task should move its route to the stronger role rather than
  disguise that as an escalation. `test_task_policy.py` refuses anything
  higher.
- **Starting value `DEFAULT_MARGIN = 0.2`** (a predicate's P between 0.4 and
  0.6, or a choice's top two within 20 points). Structurally, this is the
  band where moving 0.1 of mass from the leader to the runner-up, a shift
  smaller than the disagreement two uncalibrated endpoints may show, would
  flip the answer. It is the value a task's first eval starts from. It is
  tuned there and is never taken from a library.
- **`escalate_max = 8`** (`decisions.MAX_ITEMS_PER_CALL`), and a
  structured hop takes only what fits one chunk (section 5.3). A structured
  hop is then one call, plus at most one prompt-only re-send on a schema
  refusal (`inference._ask`, :289-318). A native hop is at most two waves of
  `NATIVE_CONCURRENCY` (4, :86). So a role hop adds at most two of the
  caller's per-call ceilings to a sweep's time, whatever the chunk budgets
  do. A caller resolver is bounded by its own budget (12-C2a), not by this
  rule.
  The change that enables escalation on a continuity task must add this to
  01's stated bounds (CLAUDE.md's "up to twelve").

### 6.3 Tuning through 01a, and the bar for switching a task on

1. **Offline sweep** (no calls). A new `evals/run.py --escalation-sweep
   --case <decide case>` reads the case's recorded native replies
   (`evals/recordings/decide-*.native*.json`), computes each item's margin,
   and prints, for each candidate threshold in a fixed grid (0.05 to 0.5),
   which items would escalate. It is deterministic, and reads only the
   synthetic recordings.
2. **Live comparison.** `evals/run.py --live --case <decide case>` runs once
   with escalation off and once with the candidate policy (a new
   `--escalation POLICY_JSON` flag overrides the task's `TaskPolicy` for the
   run only). The run needs an `escalation=` for `decide`'s consistency
   check, so the runner gains `evals/runner.escalator(task, policy)`. It
   resolves the escalation role inside 01a-C2's throwaway home, through the
   same `store.inference.resolve` path the runner already uses for a case's
   resolution (with `role=`), and returns the `Escalator` thunk. It never
   goes through `routes.common`, and never reaches the real home (01a-C2's
   tripwire). A test covers it (section 9). The output goes through 01a-C3's table: correctness per item,
   escalation rate, and, from 01a-C1 broken down by `hop`, the added wall
   time, tokens and the three money columns. Escalation costs are kept
   apart from the base call's.
3. **The bar.** On the case corpus, escalation corrects at least one item
   the base answered wrongly. It turns no correctly answered item wrong
   (damage = 0). The escalation rate and the added cost and latency are
   reported. The last three are the user's call, not a gate.
4. **The record.** The table goes into `evals/README.md` under
   "Decision escalation", and the policy line carries a comment naming that
   section and the run date. The corpus is synthetic, so no store data
   enters a committed file.

A threshold's eval corpus needs items the base gets wrong at low margin.
Writing those cases (one or two per task, in the existing `decide-*` case
style) is part of the change that switches the task on, not of this spec.

### 6.4 Candidate policies for the five existing decide tasks

All of these ship **off** (`TASK_POLICY` empty at landing). The table is the
starting point for each task's own switching change.

| Task | `question` | Proposed `escalate_on` | `escalate_to` | Why, or why not yet |
|---|---|---|---|---|
| `continuity-identity` | `decision` | refused, abstained, low_margin | primary | A wrong merge or a missed duplicate is costly, and absorb already has a budget the hop runs under. 02's first candidate |
| `continuity-reconcile` | `decision` | refused, abstained, low_margin | primary | A background sweep; latency is cheap and a wrong proposal costs review time. The evidence questions are never triggers |
| `response-selector` | `next` | refused (with `reads_declines=True`) | primary | Not `abstained`: a null hands control back, which is an answer. Not `low_margin` once 02-C2a samples the pick (`samples` with `low_margin` is refused, 4.3). The hop adds a call before the turn's first token, so recommended off until 02-C2 measures it |
| `scene-break` | `over` | refused, low_margin | primary | Low leverage (02 draft §11). Recommended off |
| `voice-drift` | `verdict` | refused, low_margin | primary | `not_enough` is a real option, not an abstention. Recommended off until a drift eval shows a gain |

## Slices

Landing order within this spec: S1 → S2 → S3 → S4 → S5. S1 and S2 can go in
parallel. S5 needs S4.

### 01d-S1: The task policy and `fallback="none"`

- **Delivers:** 01d-C1 (part: `routing.TaskPolicy`, `routing.policy`, `TASK_POLICY` empty, `fallback="none"` in `resolve` with `NO_FALLBACK_POLICY`, and every section 4.3 rule in `test_task_policy.py`); 01d-C3 (part: `MAX_MARGIN`, `DEFAULT_MARGIN` and the `margins` and `escalate_max` validation)
- **Needs (this spec):** none
- **Needs (other specs):** 01c-C1 (S: the `TaskPolicy.samples` field. Until it lands, the rule refusing `samples` with `low_margin` is added by whichever of 01c-S2 and this slice lands second)
- **Scope:** Adds the shared structure to `store/routing.py` (pure leaf, literals only) with all of section 4.1's fields, including `escalate_answers` and `reads_declines`. If 01c-S2 has already landed, this slice extends the structure rather than creating it. `resolve.resolve` reads `policy(task).fallback`: `"none"` attaches no fallback, and sets `NO_FALLBACK_POLICY` only when the role had one configured. The settings view's dropped-fallback line gets its sentence (01s's `droppedFallbackWords`). No task gets a policy, so behaviour is unchanged.
- **Acceptance:** `test_task_policy.py`: every section 4.3 rule, each with a planted violation, plus "empty at landing". `test_inference_resolve.py` (extended): `fallback="none"` on a generate task and a decide task; on a role with no fallback, no `fallback_problem`; the role card unchanged; `for_task` between siblings stays correct. `make check` is green.
- **Size:** S

### 01d-S2: Per-item provenance and trigger evaluation

- **Delivers:** 01d-C2a (full)
- **Needs (this spec):** none
- **Needs (other specs):** none
- **Scope:** Adds `ItemResult.served` (`compare=False`), stamped by `_structured` from the answering holder's `llm.ATTEMPTED` and by `_native` from `named`. Adds `decisions.margin` (measured from the answered key, with `math.fsum`), `Trigger` and `decisions.triggers`, with the exact and `:`-prefix answer filter and the priority order. These are pure, and nothing calls `triggers` in production yet.
- **Acceptance:** the trigger and margin cases of section 9: each trigger; at the threshold and just under it; no entry for a kind; `unreadable` and `error` never escalated; the margin formula for `M >= 1`, `M < 1` and a predicate both ways; the answer-disagrees cases from review B1; the prefix filter; priority order. Existing `ItemResult` equality tests stay green, and `test_decide_chain_golden.py` is unchanged.
- **Size:** S

### 01d-S3: The escalation hop in `decide`

- **Delivers:** 01d-C2b (part: `decide(escalation=)` with the role hop and the caller `Resolver` / `ResolverReply`, the per-chunk cap, the same-model skip, the per-question merge with `reads_declines`, `Decision.escalations`, the `hop` account field on ledger rows and the hop-marked capture); 01d-C1 (part: `decide`'s `ValueError` when the call site and the policy disagree)
- **Needs (this spec):** 01d-S1 (H); 01d-S2 (H)
- **Needs (other specs):** 01a-C1 (S: `decisions.CallRecord` / `Decision.calls`. Until it lands, `Decision.usage` and `Decision.escalations` carry the hop, and this slice adds the `calls` records when 01a lands, or 01a adds them if it lands second); 01b-C1 (S: the capture helper at every decide site. Until it lands, hop calls are captured only where the site passes `capture`, which today is the speaker pick)
- **Scope:** `inference.decide` gains `escalation=`, and runs the section 5.3 flow after the unchanged `run_stages`. `wire.Account` and `llm_usage.ACCOUNT_FIELDS` gain `hop`. `decisions` gains `Escalation`, `Decision.escalations` and `ResolverReply`. No call site passes `escalation=`, and `TASK_POLICY` is empty, so nothing escalates in production. The tests drive it with a patched synthetic policy and `llm_fakes`.
- **Acceptance:** `backend/tests/test_decide_escalation.py` as in section 9, apart from the seam and evals items: no policy means an identical `Decision`; cap and chunk; `same_model`; replacement; the declines and per-question merge cases, including the review's `_decide_error` counterexample; `rate_limit` and `BudgetRefused` on the hop; no second hop; the caller-`Resolver` cases; the policy/call-site `ValueError`; ledger `hop` rows; capture; cancellation. `test_usage_guard.py` and `test_decide_chain_golden.py` stay green.
- **Size:** L

### 01d-S4: The escalation seam in `routes/`

- **Delivers:** 01d-C2b (full: `routes.common.escalation_inference` over `_soft_resolved`, the role's own preset, the route's `requires` check and the route-named sentence); 01d-C1 (full)
- **Needs (this spec):** 01d-S3 (H)
- **Needs (other specs):** none
- **Scope:** Adds `escalation_inference(task, cid)` with its `# routing-ok:` marker. `test_routing_guard.py` treats it as a seam (only called, with a literal task whose policy escalates), and `RESOLVER_CALL_CAP` goes from 5 to 6. `test_operation_guard.py` checks that `operations.decide` passes `escalation=` exactly when the task's policy escalates. CLAUDE.md gains the sentence that an escalation is not a stage. No call site uses the seam yet: each task's switching change (02, 10, 11, 12) adds its own.
- **Acceptance:** the seam cases of section 9: a soft refusal skips every candidate with the sentence; a hop primary lacking the route's `requires` is skipped as `incapable`, with the route named; the role's own preset is sent. The routing guard and the operation guard each fail a planted violation. `test_docs_guard.py` passes.
- **Size:** M

### 01d-S5: Threshold tooling in evals

- **Delivers:** 01d-C3 (full: `--escalation-sweep`, `--escalation POLICY_JSON`, `evals/runner.escalator` and the `evals/README.md` "Decision escalation" section with the bar of section 6.3)
- **Needs (this spec):** 01d-S4 (H)
- **Needs (other specs):** 01a-C1 (H: aggregation per route, backend and `hop`); 01a-C2 (H: live evals metered inside a throwaway home, with the tripwire against the real home); 01a-C3 (H: `--out` and `--compare FILE...`)
- **Scope:** Adds the offline margin sweep over `evals/recordings/decide-*.native*.json`, the per-run policy override, and the eval-side escalator that resolves inside the throwaway home. Adds the README section that states the procedure and the bar. It enables no task. Each task's thresholds and its eval cases land with that task's switching change.
- **Acceptance:** `--escalation-sweep` output is golden over a fixed recording. `--escalation` is refused for a task that is not on a decide route. `evals/runner.escalator` resolves inside the throwaway home and is refused by 01a-C2's tripwire against the real one. `make check` is green.
- **Size:** M

## 7. Contract

- **01d-C1. `routing.TaskPolicy`** (shared with 01c) and
  `routing.policy(task)`, whose default is today's behaviour.
  `fallback="none"` makes `resolve` attach no fallback, with
  `fallback_problem = NO_FALLBACK_POLICY`, and must agree across a route's
  tasks. The escalation fields exist only on decide tasks, are validated by
  `test_task_policy.py` (including the refusal of `samples=True` with
  `low_margin`), and decide whether `decide` may, and must, be handed an
  `escalation`. Failure behaviour: a call site and its policy that disagree
  raise `ValueError` before any meter opens.
- **01d-C2a. Trigger evaluation.** `decisions.margin(answer)` and
  `decisions.triggers(results, question=, escalate_on=, margins=,
  answers=)`: low margin, abstention, native `refused`, with an optional
  answer filter (`escalate_answers`, exact or `:`-prefix entries) that
  narrows `low_margin`. The margin is measured from the answered key and is
  negative when the report ranks a rival above it (section 6.1). Pure; reads
  only what a backend reported; never triggers on `unreadable` or `error`;
  never triggers `low_margin` for an endpoint kind with no threshold.
  Priority: refused, abstained, then low margin ascending.
- **01d-C2b. One escalation hop** after the unchanged chain.
  `decide(..., escalation=)` sends at most `escalate_max` triggered items,
  once, to the declared next resolver: the policy's role (primary alone,
  `retries=0`, the role's own preset, the base route's `requires` checked,
  a different model from the item's server, at most one structured chunk,
  under the same `around`, meters and capture), or a caller-supplied
  `Resolver` (`escalate_to=CALLER`) returning a `ResolverReply`, which
  meters, captures and budgets itself. Rows carry `hop: escalation`, and
  each hop call is a `Decision.calls` record. A non-`None` hop answer (or a
  decline, on a `reads_declines` task) replaces the deciding answer, and the
  item's other questions keep the base's answers unless the hop's were
  read. A failed, garbled, declined (elsewhere) or skipped hop leaves the
  original. The record is `Decision.escalations`, and `Decision.errors` is
  untouched. Guarantees: never more than one hop; never the same model on a
  role hop; on a task without `reads_declines`, never a read deciding answer
  turned unread, so never a new failure reported for a batch that had one.
- **01d-C3. Thresholds.** `TaskPolicy.margins` per task and per native
  endpoint kind, each in `(0, MAX_MARGIN]` with `MAX_MARGIN = 0.5`, starting
  at `DEFAULT_MARGIN = 0.2`, and `escalate_max <= 8`, argued in section 6.2.
  All existing tasks are off. A task's values are set only with the section
  6.3 evidence in `evals/README.md`.

## 8. Interaction with repo rules

- **Routing guard** (`test_routing_guard.py`). `escalation_inference` is a
  seam. It is only ever called, its first argument is a literal task that a
  route claims, and that task's policy has `escalate_to`. Its one
  `inference.resolve` call carries `# routing-ok:`. `RESOLVER_CALL_CAP`
  goes from 5 to 6 (:340-345), with the reason in the comment.
- **Operation guard** (`test_operation_guard.py`). An `operations.decide`
  call passes `escalation=` exactly when its task's policy escalates. This
  is checked statically, by the same import-binding walk.
- **Usage guard.** No new meter site in `routes/`. The hop's meters open
  inside `inference.py`, which the guard already scans.
- **Ledger and costs.** The `hop` account field is additive. All three money
  columns stay as they are, and a native hop row is still never modelled
  (`decision_mode` native). The Costs page needs no change to stay correct.
  A per-hop breakdown is 01a's.
- **Logs and errors** (CLAUDE.md, "Observability"). Failures are recorded at
  `Meter.done` only, and the hop adds no log line of its own. A skipped hop
  is visible in `Decision.escalations` and in the capture, not in `logs/`.
- **Locks and detached runs.** `decide` takes no lock. The hop runs inside
  the caller's existing run: a turn, absorb's review run, or reconcile's
  background run. There is no new run class and no exclusion key.
  `escalation_inference` reads `config.md` and campaign frontmatter, so it
  runs in the threadpool, as `require_inference` does.
- **Import guard.** `decisions` stays a leaf (`Trigger`, `Escalation`,
  `margin` and `triggers` need nothing from the package). `inference`
  already imports `store`.
- **Android / pydantic v1.** Plain dataclasses and NamedTuples, and no new
  dependency.
- **Privacy.** Thresholds are justified structurally and tuned on the
  synthetic corpus. No count or proportion from any store appears in a
  committed file.
- **Docs.** In the change that adds the hop, CLAUDE.md's decide paragraph
  gains one sentence after "What moves an item on to the next stage is a
  failed call, never an answer": a policy-declared escalation is not a
  stage, and its rules are as in section 3. `test_docs_guard.py` is run.
- **01s (settings group, in flight).** `fallback_problem =
  NO_FALLBACK_POLICY` reaches the existing dropped-fallback line. Showing a
  task's escalation on its route row is an open question.

## 9. Tests and acceptance

`backend/tests/test_task_policy.py`: every rule in section 4.3, each with a
planted violation. It also checks that `TASK_POLICY` is empty at landing, or
that every entry has `escalate_to=""` and `fallback="role"`.

`backend/tests/test_decide_escalation.py`, with `llm_fakes`, a scripted
native fake and a structured fake, and a synthetic policy patched in:

- **No policy:** `decide` returns exactly the base `Decision`, including
  `usage`, `served` and `errors`, and makes no extra call.
  `test_decide_chain_golden.py` stays green without regenerating.
- Each trigger fires: refused (an OpenAI-shaped refusal), abstained (a native
  tie, and a structured null on an `allow_none` choice), and low margin just
  under the threshold. Low margin does not fire just at it, or for a kind
  with no entry.
- `unreadable` and `error` items are never escalated.
- The margin formula: `M >= 1`, `M < 1` (a partial report of a single 0.6
  gives margin 0.2), and a predicate answered True and False.
- **The answer disagrees with the report (review B1):** `choice: known`
  with `{known: 0.1, narrator_only: 0.9}` gives margin -0.8 and triggers. An
  answered key absent from the report gives a negative margin and triggers.
  With `escalate_answers=("known",)` it triggers; with `("narrator_only",)`
  it does not.
- The prefix filter: `("existing:",)` triggers on a low-margin
  `existing:a1` and not on a low-margin `new`.
- Cap and priority: 10 triggers with `escalate_max=8` escalate the two
  refusals, then the abstentions, then the lowest margins. The rest are
  `skipped: cap`. Eight identity items whose options overflow one chunk's
  enum budget: only those fitting the first chunk are sent, and the hop is
  one structured call.
- `same_model` skip. Same provider with another model is allowed.
- The hop answers: the item is replaced, `served` holds both, and
  `Decision.provider` is `""`.
- The hop returns `abstained`: on a `reads_declines` task the item is
  replaced; otherwise the outcome is `failed` with `detail: declined` and the
  original stands. The review's counterexample (item A read, low margin;
  item B `error`; the hop abstains on A) leaves `_decide_error` returning
  None, as before the hop. The hop returns a garbled reply: the original
  stands and the outcome is `failed`.
- Per-question merge: a reconcile item whose hop answers `decision` but
  garbles `evidence_scene` keeps the base's evidence scene.
- A role hop sends the escalation role's own preset, not the route's. A hop
  primary known to lack the route's `requires` is skipped `incapable`, with
  a sentence naming the route.
- A caller `Resolver`: its `ResolverReply.rows` reach `Decision.usage`; its
  results' `served` reach `Decision.served`; a result with a `backend`
  outside `BACKENDS` is a `ValueError`; a resolver that raises `LLMError`
  leaves every original.
- The hop raises `rate_limit`: the original stands, `errors` is unchanged,
  and `_decide_error` gives the same answer as without the hop.
- The hop is refused by `around` (`BudgetRefused`): the original stands, no
  row files as failed, and `NOT_A_FAILURE` holds.
- An escalated answer that is itself low margin is not escalated again.
- A soft resolution that fails (missing key): every candidate is `skipped`
  with the refusal sentence, and no call is made.
- The policy and the call site disagree: `ValueError`, and no meter opened.
- Ledger: hop rows carry `hop: "escalation"` and `role: "primary"`. Base rows
  carry no `hop`.
- Capture: hop calls reach `capture` with `"hop": "escalation"`, and a
  capture that raises fails nothing.
- Cancellation during the hop: `CancelledError` propagates, and the hop's
  meter files `aborted`.

`backend/tests/test_inference_resolve*.py` (extended): `fallback="none"`
drops the fallback with `NO_FALLBACK_POLICY` on a generate task and a decide
task. The settings view's role card (no task) is unchanged. `for_task`
between siblings stays correct.

Evals: `--escalation-sweep` output is golden over a fixed recording.
`--escalation POLICY_JSON` is refused for a task whose route is not
`decide`. `evals/runner.escalator` resolves inside the throwaway home and is
refused by 01a-C2's tripwire against the real one.

`backend/tests/test_inference_resolve.py` (extended) also covers
`fallback="none"` on a role with no fallback configured, which sets no
`fallback_problem` (review M1).

Acceptance: `make check` passes, `check-pydantic1` included, and no task's
behaviour changes at landing.

## 10. Non-goals

- Enabling escalation on any task (each one's own change, section 6.3).
- Escalation on a structured answer's confidence, or any synthetic
  confidence.
- Grouped (regrouped) margins for folded options (section 11).
- Per-model thresholds, or thresholds the user edits in Settings.
- More than one hop, voting, or asking several models and taking a majority.
- Building an agent. A `CALLER` resolver may be one (12's tool loop), and
  12 may read C2a's triggers on their own. Neither is specified here.
- Changing `run_stages`, `stages` (01c owns the `native_first` branch and
  the isolated-stage rule), `SAME_PROVIDER`, or the failure-driven rule.

## 11. Open questions

1. **Raw or grouped margin for folded options** (continuity's
   `existing:<id>` and directed words)? Recommendation: raw (section 6.1).
   Splits between candidates or directions are real ambiguity. If the eval
   shows spurious hops, add an optional per-task `group` that the call site
   passes, mirroring `regrouped`.
2. **Should a hop's `abstained` replace a low-margin answer?** Decided,
   per task: only with `reads_declines=True` (section 5.3). The earlier
   draft said that continuity reads a decline as `uncertain`. That was
   wrong: it reads one as not read (`was_read`), so continuity keeps
   `reads_declines=False`. If continuity later wants a hop's "cannot tell"
   to stand, the change is to map a hop decline to its `uncertain` word in
   the call site, not to flip the flag.
3. **Show escalation in Settings (01s)?** Recommendation: once any task
   enables it, the settings view adds a read-only `escalates_to` to each
   affected route row ("Unsure answers go to Primary"), because the hop
   spends on another role. It should also say "has no effect" when the
   escalation role resolves to the route's own model (a `fast` hop while
   Decision inherits Fast). No toggle: the policy is code, switched on
   evidence.
4. **`retries=0` on the hop?** Recommendation: yes. A lost second opinion
   costs nothing that was not already answered, and on absorb's budget a
   retry would spend the clock the later phases need.
5. **Speaker escalation?** Recommendation: off until 02-C2's eval measures
   the latency the hop adds before the first token.
6. **The `hop` ledger field.** Recommendation: add it, since without it
   01a cannot report escalation cost apart from the base calls. The checklist
   should record the edge 01a-C1 ← "breakdown by `hop`".

## 12. Review record

Substitute adversarial review, 2026-10-10 (`reviews/01d.md`: 1 blocking,
7 should-fix, 7 minor). Each finding was checked against the code at
`35c1fb7`.

| # | Finding | Disposition |
|---|---|---|
| B1 | The margin ignores the answered key, so an answer its own report ranks low reads as confident | **Fixed.** Verified (`decisions.py:866-901, 928-936`; both adapters pass `choice` as `chosen`). The margin is measured from the answered key and is negative when a rival leads (6.1, C2a, tests) |
| S1 | A hop's decline breaks continuity's `was_read` and the `_decide_error` guarantee | **Fixed.** `reads_declines` (opt-in, refused on continuity), the guarantee restated with its condition, open question 2 corrected (5.3, 5.5) |
| S2 | Whole-item replacement loses the base's other answers | **Fixed.** Per-question merge (5.3) |
| S3 | A `role=` resolution has no route: unspecified preset, `requires` and wording; `fast` can be a no-op | **Fixed.** Role's own preset, the route's `requires` checked, the route named; the `fast` no-op stated (4.3, 5.2, open question 3) |
| S4 | A caller resolver's `backend` and `served` are unspecified | **Fixed.** `ResolverReply`, with `backend` in `BACKENDS`, three-part `served`, and rows into `usage` and `calls` (5.2) |
| S5 | `escalate_answers` cannot express per-row options | **Fixed.** `:`-prefix entries (4.1, 4.3, 5.1) |
| S6 | The time bound assumes one chunk | **Fixed.** The hop takes only what fits the first chunk (5.3, 6.2) |
| S7 | Evals cannot meet the `ValueError` rule | **Fixed.** `evals/runner.escalator` inside 01a-C2's throwaway home (6.3, 9) |
| M1 | `NO_FALLBACK_POLICY` reported with no fallback configured | **Fixed** (4.2) |
| M2 | Wrong `adapters` citation | **Fixed** (`adapters.py:305`) |
| M3 | `Decision.calls` not specified | **Fixed** (5.5) |
| M4 | A third soft-resolution pattern | **Fixed.** Reuses `_soft_resolved` (5.2) |
| M5 | An early hop overrun is charged to the base connection's health | **Stated** as display-only and accepted (5.7) |
| M6 | A score's adjacent levels count as rivals | **Stated** as intended (6.1) |
| M7 | 02 §10 points at the wrong signal | **Not changed here** (02's file). 5.5 now names `Decision.escalations[*].outcome`, and 02 should cite it |

Slices added (5 slices).

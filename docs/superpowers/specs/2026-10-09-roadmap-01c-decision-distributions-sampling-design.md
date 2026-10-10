# 01c. Decision distributions and seeded sampling

**Status:** Draft — spec gate (substitute review) folded in; Codex gate pending.
**Date:** 2026-10-09
**Roadmap:** 01c in `ROADMAP-CHECKLIST.md`. Lane: decision.
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** the bundle draft `02-decision-integration.md` §4 ("Normalized
result"), §5 ("Sampling belongs to Grimoire") and §13 (logging), and
`13-mechanics-II-actions-effects.md` §27 ("Behavioral distributions",
"Replay"). It extends the landed 01 spec
(`2026-10-07-inference-backend-refactor-design.md`) at §5.5 (the decide chain)
and §7.4 (the result contract, "There is no synthetic confidence value"), and
it records the decision §16 of that spec deferred: whether native is tried
first for a model that also generates.

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning.

## Depends on

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| `decide`, `Answer.probability` / `Answer.distribution`, `inference.stages` / `run_stages`, `resolve.native_only` | 01 (landed) | The distributions sampled here, and the chain section 4 adds a stage to | Hard, and met |
| 01a-C1 per-case and per-call wall time, tokens and money, per route, backend and `hop` | 01a | The evidence section 4.3 requires before `native_first` is switched on for any task | Hard to switch a task on; not needed to land the mechanism |
| 01a-C3 repeatable `--decide-backend`, `--out`, `--compare` | 01a | The recorded comparison table for that evidence | Hard to switch a task on |
| 01b-C1 capture at every decide site | 01b | Lets a reader see, in the prompt log, the distribution a draw was made from | Soft. The replay record (C3) is persisted by the caller, not by capture |
| `routing.TaskPolicy` (shared structure) | 01d-C1 or this spec, whichever lands first | Holds `samples` and `native_first` | Shared structure (section 4.1) |
| `ItemResult.served` (shared structure) | 01d | The record's `kind`, `provider` and `model` per item (section 6) | Shared structure. Until it lands, the caller passes `served=` of the same shape |
| 01e-C4 `Answer.marginals` | 01e | Named so that C4 can say marginals are never sampled (section 5.4) | Soft |
| 01e-C4 `Answer.marginals`, kept apart from `distribution` | 01e | C4's rule that marginals from `Rank` and `MultiSelect` are never sampled (section 5) | Soft: until 01e lands there are no marginals to refuse |

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 01c-C1 recorded policy, `native_first`, `reports_distribution(resolved)` | 02 (H for 02-C2 sampling) | Whether a play decision can be sampled on the user's Decision model (`reports_distribution`), and how a task is switched to native-first |
| 01c-C1 | 13 (S) | Whether 13-C3's NPC action seam can expect a distribution, or will act on a plain answer |
| 01c-C2 `draws.py` | 02 (H for 02-C2a/C2b), 13 (H for 13-C3), 01g (S) | A sampled next speaker and turn intent; a sampled NPC Action over the legal set; a decide tool's sampled answer (01g-C5) |
| 01c-C3 replay record | 02 (H), 13 (H for 13-C3), 01g (S) | Stored on the round record (02), beside the Action resolution in the transaction ledger (13), and in a tool loop's outcome (01g) |
| 01c-C4 never sampled | 02 (H), 13 (H for 13-C3), 11 (S) | What each caller does when there is nothing to sample. 13 may act on a plain Choice answer recorded `sampled: false`. 11's Decision stage never samples an epistemic class |

09 and 10 reach this spec only through 02-C5b (history relevance), which
classifies and never samples.

## 1. Current state (reconciled against main)

- **An `Answer` carries what a backend reported, and nothing else.**
  `decisions.Answer` has `probability: float | None` and
  `distribution: dict[str, float] | None`
  (`backend/src/grimoire/decisions.py:220-254`). The docstring says: "a missing
  one stays missing, and nothing computes a stand-in" (:227-229).
- **Only the native backend fills them.** `native_answer` keeps a probability
  only when it is a finite number in [0, 1] (`_probability`, :824-832), and a
  distribution only when every key is legal for the question and every value
  is a probability (`_distribution`, :843-856). An invalid report "is dropped
  whole, never repaired". The structured parser never sets either field
  (`_read`, :674-684; `parse`, :687-715).
- **A reported distribution is not normalised and need not be complete.**
  `_distribution` checks each value on its own. Nothing checks that the values
  sum to 1, or that every option is present. Keys are option ids, `str(i)` for
  a score level, and `NONE_KEY` (`"<none>"`, :89) for the reserved none of an
  `allow_none` choice. A predicate has no distribution: its `probability` is
  P(true) (`openrouter.py:152-154`, `openai_compatible.py:226-228`).
- **The answer is the explicit choice, else the argmax.** A tie at the top, an
  argmax on `NONE_KEY`, or a predicate at exactly 0.5 is `abstained`
  (`_native_predicate`, :908-915; `_from_distribution`, :918-925). OpenAI's
  per-question `"type": "refusal"` is `refused` (`openai_compatible.py:216-223`).
  OpenRouter documents no refusal, so none is mapped.
- **Native serves only a model that cannot generate.** `resolve.native_only`
  (`store/inference/resolve.py:922-928`) is a known `no` for `generate` and not a
  known `no` for `decide_native`. `decision_mode` (:931-940) makes every model
  that can generate `structured`, "whatever its `decide_native` says".
  `inference.stages` (`inference.py:147-182`) builds that chain, and its
  docstring records native-first as "a later user decision (spec 16)". So
  today a user whose Decision role is a generating model never sees a
  distribution.
- **Nothing samples.** No call site reads `distribution`, except continuity's
  `decisions.regrouped` (:952-982), which reads it to re-weigh a folded choice
  and not to draw. The speaker pick maps its answer with
  `response_protocol.selection_of` (`store/response_protocol.py:125-145`):
  a named ref is the speaker, `abstained` hands control back, and anything
  else is an issue. "Nothing unreadable is ever guessed into a speaker."
- **There is house precedent for seeded, recorded randomness.**
  `store/dice.py:114-125` draws a 53-bit seed from `secrets.randbits(53)`,
  so the seed "survives the JSON/JS boundary", and records it with the roll.
  `store/group_play.py:7-11` takes all randomness through an injected
  `random.Random`, and its caller "stores the result on the round record so a
  retry, a recovery or a roll resume continues the same plan and never
  re-rolls". `routes/character_turns.py:45-48` is that seam (`_rng`).
- **The round record is where a pick's outcome is persisted.** `_first_actor`
  (`routes/character_turns.py:882-893`) writes `actor_ref` and `issue` through
  `_round_state` → `responses.update_round` (`store/responses.py:366-379`),
  under the campaign lock.
- **No adapter asks for logprobs** (no occurrence in `backend/src/grimoire`),
  and the capability vocabulary has no such flag.

## 2. Goal (and what is explicitly not the goal)

Goal:

1. Decide, and record in code, when a Decision call made through a
   *generating* model gets a distribution (C1).
2. Give every caller that wants a probabilistic outcome one helper, which
   draws from a reported distribution with a recorded seed. The same record
   always gives the same draw: on Python 3.11 and later, on Android's
   Chaquopy, and in a browser (C2).
3. Give the draw a self-contained record, persisted by the caller with the
   outcome it chose (C3).
4. Make it impossible to turn "the backend did not say" into a random
   answer (C4).

Not the goal:

- Wiring any caller. The sampled speaker and turn intent are 02-C2, and NPC
  Action choice is 13-C3. This spec ships the helper, the policy and the
  record, with no caller switched to sampling.
- Making any distribution calibrated, or comparable across backends. 01 §7.4
  forbids a synthetic confidence, and nothing here computes one.
- Reshaping a distribution beyond the one recorded pre-draw `Eligibility` step
  (mask and floor, section 5.3): no temperature, no sharpening. See section 11.

## 3. The policy for generating Decision models (C1)

There are three candidate answers to "a generating Decision model was asked;
where does a distribution come from?"

### 3.1 Structured verbalised probabilities: rejected, permanently

One could add a number per option to the structured schema ("how likely is
each option, 0 to 1") and read it back as a distribution. This spec rejects
that, for reasons that hold on every model:

- **It is text the model generated, not the model's predictive
  distribution.** The number is a sampled token sequence conditioned on the
  prompt. It is not the probability mass the model put on each answer.
  Verbalised confidences cluster on round numbers, shift with the wording,
  and are not consistent with the answer generated beside them. Sampling
  from them makes play's randomness a function of prompt phrasing.
- **It is the synthetic confidence 01 forbids.** 01 §7.4 says "There is no
  synthetic confidence value, and callers must not compute one across
  backends". A field the prompt asks the model to fill in is exactly that.
  Putting it on `Answer.distribution` would make it indistinguishable from a
  native endpoint's report, and `regrouped`, 01d's margins and this spec's
  sampler would all trust it.
- **The schema cannot hold it to the contract.** The batch schema uses only
  `type`, `enum`, `anyOf`, `required`, `properties` and
  `additionalProperties`, because Anthropic refuses numeric bounds
  (`decisions.py:32-38`). A probability field would be an unbounded
  `number`, which `_probability` would then silently drop when it fell out of
  range. Values that do not sum to 1 would need a repair, which 01 forbids
  ("dropped whole, never repaired").
- **It costs output tokens on every structured chunk** (one number per option
  per item, up to 255 options) for a value nothing can trust.

Logprobs over a constrained enum token would be a real model distribution.
They are not offered here: no adapter requests them, Anthropic's API offers
none, and an option id that spans several tokens gives a distribution over
first tokens, not over options. They are listed as a non-goal (section 11).

### 3.2 Native-first: allowed per task, opt-in, and gated on evidence

A model that both generates and decides natively (an OpenAI-preset model, or
an OpenRouter model with a decisions endpoint) can be asked through its
provider's decisions endpoint first. That gives a real reported
distribution. The structured stage on the same model remains the next stage
for any item the native stage *failed*. Section 4 specifies the chain.

It is opt-in per task and per adapter kind (`TaskPolicy.native_first`, section 4.1). It is allowed
only on a task that consumes a distribution: one that samples (`samples`), or
one whose 01d escalation triggers on `low_margin`, which needs a margin. It is
switched on for a task only by a change that carries the section 4.3
evidence. Each condition prevents a specific failure:

- *Only where a distribution is consumed.* A native answer carries no
  rationale (01 §7.4). Native-first on a task that does not use the
  distribution would trade away the rationale (scene-break's reason, voice
  drift's corrective) for nothing.
- *Only with a known `yes` for `decide_native`.* The native-only rule allows
  `unknown` (`native_only`, :922-928) because a native-only model has no
  other way to answer. A native-first model does have one. If an unknown
  endpoint were tried first, a model whose endpoint rejects it would fail
  every call before reaching the structured stage, on a path (the speaker
  pick) the player is waiting on.
- *Only on evidence.* 01 §16 made this a later user decision, measured with
  `evals/run.py --live --decide-backend` on one model. This spec keeps that
  gate and adds the cost and latency columns 01a provides.

### 3.3 Neither: the default

At landing, every task has `native_first = ()`. A generating Decision
model answers through the structured backend, as today, and its `Answer`s
carry no distribution. A sampling caller handed such an answer takes the
answer as given, and the record says so (`basis: "answer"`, section 6). It
does not sample from a one-hot or uniform stand-in (C4).

So the recorded policy is: **structured verbalised probabilities never;
native-first per task, opt-in, on evidence; otherwise neither.**

## 4. Native-first in the decide chain

### 4.1 The policy field

01d-C1 introduces `routing.TaskPolicy`, a `NamedTuple` keyed by task in
`routing.TASK_POLICY`, with `routing.policy(task)` returning the default
`TaskPolicy()` for a task not listed. This spec adds two fields. Whichever of
01c and 01d lands first creates the structure. Both specs' fields default to
today's behaviour.

```python
class TaskPolicy(NamedTuple):
    # ... 01d's fields (fallback, escalation, question) ...
    #: The task's caller draws from the decision's distribution (01c-C2).
    samples: bool = False
    #: The adapter kinds whose decisions endpoint is asked first for this task
    #: (01c-C1, section 4.2): ("openai_compatible",), ("openrouter",), both,
    #: or () for none. Per kind, because the evidence is per kind (4.3).
    native_first: tuple[str, ...] = ()
```

`routing` stays a pure leaf (`store/routing.py:15-16`). The fields are
literals.

`backend/tests/test_task_policy.py`, which 01d also creates, holds these
rules:

- every policy key is a task some route claims (`TASK_ROUTE`);
- `samples` and `native_first` appear only on a task whose route's
  `operation` is `decide`;
- each `native_first` kind is an adapter kind whose `decides_natively` is
  true (`adapters.decides_natively`, `adapters.py:305`);
- a non-empty `native_first` implies `samples` or a `low_margin` trigger
  (01d);
- `samples=True` is refused together with `low_margin` in `escalate_on`
  (02 asked for this). A low-margin answer is exactly the case sampling
  exists for: it is where the distribution has two live options. Escalating
  it would replace a reported distribution with a hop answer that may carry
  none (a structured role), so the draw would silently fall back to
  `basis: answer` on the items that most needed it. A sampling task may
  still escalate on `refused` or `abstained`, where there is nothing to
  sample;
- a sampling task names its distribution-bearing question, using the same
  `question` field 01d uses, and a test pins that field to the store constant
  (for example `response_protocol.SELECTOR_QUESTION`).

### 4.2 The chain

`inference.stages(resolved)` gains one branch. It reads the task's policy
(`store.routing.policy(resolved.task)`):

```text
primary.target.kind in policy.native_first
    and primary.decision_mode == "structured" and native_capable(primary)
  -> Stage(NATIVE, whole.alone(), retries=0, isolated=True)   # same model, decisions endpoint
     Stage(STRUCTURED, whole or whole.alone(), None)          # as today
     [fallback stage, as today, when apart]
otherwise -> today's stages, unchanged
```

- `native_capable(attempt)` is a new function in `resolve`, used only by
  the production chain. It holds when the attempt's adapter kind has a
  decisions endpoint (`adapters.decides_natively(kind)`) and
  `decide_native` is a known `yes` (`capabilities.YES`). A preset's `never`
  already arrives as an adapter-source `no` (`capabilities.py:206-209`).
  `evals/runner.chain` (`evals/runner.py:233-280`) is **not** changed: it
  keeps `resolve.decides_natively` (anything but a known `no`), as CLAUDE.md
  documents, so the section 4.3 evidence can be gathered on a model whose
  `decide_native` is still `unknown`. Only production requires `yes`.
- `inference.reports_distribution(resolved) -> bool` is the one predicate a
  caller asks before it plans to sample (02-C2 asks it to decide whether the
  sampled path is available at all). It is True when the first stage
  `stages(resolved)` builds is native: a native-only primary, or a
  native-first kind on a `native_capable` primary. It is pure, reads only
  the resolution and the task policy, and sends nothing. It is a forecast,
  not a guarantee: an item the native stage fails is answered by a later
  structured stage with no distribution, so the caller still goes through
  `draw`, and C4 still decides each item. The settings view may show the
  same predicate.
- **The failure-driven rule is unchanged.** The structured stage takes only
  the items the native stage *failed* (`run_stages`, `inference.py:595-681`).
  A native `refused`, an `abstained` or an `unreadable` from a well-formed
  body is final, exactly as on a native-only model.

#### 4.2.1 A native-first stage never kills the stages after it

`Stage` gains `isolated: bool = False`. A native-first stage is built with
`isolated=True`, and `run_stages` changes in two places, both inert for
every chain that exists today:

1. **An isolated stage never adds its provider to `dead`.** Whatever
   stopped it (`auth`, `rate_limit`, money, a timeout run) is a statement
   about the decisions endpoint. The failure this prevents is real:
   OpenRouter maps 403 to `auth` (`openrouter.py:53-58`), and `llm.py:305-312`
   says a 403 "is what a key without access to an alpha endpoint gets while
   it serves every chat call". Today's `dead` rule (`inference.py:640-663`)
   would then skip the structured stage on the same provider, and the riding
   fallback with it, and the speaker pick would fail a turn that answers
   through the chat endpoint today. Inside the native stage, `_native`'s own
   stop still applies (no further item is started), and the unsent items
   carry that failure to the structured stage, which takes them as it takes
   any failed item.
2. **A stage is skipped as dead only when every route it sends is dead**:
   its primary's provider and, when a fallback rides it, the fallback's.
   Today no later stage carries a riding fallback (only a first stage can),
   so this changes nothing that runs now. It closes the same hole for any
   later chain shape.

A connection-wide failure on the *structured* stage after it is unchanged:
it still skips later stages on that provider, as 01 §5.5 says.

#### 4.2.2 Retries and the added latency

The native-first stage gets `retries=0`, as a fallback stage does
(`Stage.retries`, `inference.py:135-144`). A rate limit or a network
failure on the decisions endpoint is not retried before the structured
stage, which can answer, gets its turn. For the speaker pick, which has no
`around` ceiling, the added worst case is therefore **one** decisions
request, bounded by the facade's own read timeout, before the structured
call is sent. A task that turns native-first on restates its bound in that
change. 01's continuity bounds assume two stages, and a continuity task that
adds a native-first stage adds one wave per `NATIVE_CONCURRENCY` items.

#### 4.2.3 Health

A decisions endpoint that answers 5xx or times out under native-first is
observed against the connection like any native call (`llm.py:1497`, which
already exempts `NATIVE_REJECTED_STATUSES`). The health dot is display
only, so a chat endpoint that still works can show "failing" while a
decisions endpoint is down. This is accepted and stated, not fixed here.

`ResolvedInference.decision_mode` stays the primary's per-attempt mode
(`structured`). The settings view's decide note (01s) may say "native
first" for a task whose policy names the primary's kind (section 12).

### 4.3 The evidence that switches a task on, per adapter kind

A change that adds an adapter kind to a task's `native_first` carries all
of the following, run on at least one model **of that kind** that both
generates and has a native endpoint. The evidence is per kind because
`native_first` moves every user whose Decision model is a known-`yes` model
of that kind, and the two decisions endpoints are different services:

1. `evals/run.py --live --provider ID --model NAME --decide-backend native`
   and then `--decide-backend structured`, over the task's decide cases
   (today `decide-speaker`, `evals/README.md:29`). The output goes through
   01a-C3's comparison table: per-item correctness, wall time, prompt and
   completion tokens, and the three money columns kept apart.
2. **The bar:**
   - every case the structured backend passes, the native backend passes on
     the same model;
   - every native answer in the run carries a usable distribution (a
     `decide.distribution` check, graded n/a on a structured answer, as
     `NATIVE_RATIONALE` is graded in the other direction,
     `evals/graders.py:707-746`), and none is `partial` or `inconsistent`
     (section 5.4);
   - the fall-through tests of section 10 (403, rate limit, auth with a
     riding fallback) pass on that kind's fake.

   Latency and cost are reported but are not gated. They are the user's
   call, made from the table.
3. **A cost note.** A native row whose provider reported no cost is unpriced
   (`unpriced_native_calls`), and no rate can model it. If the kind's
   decisions endpoint reports no cost, the change says that the task's rows
   will read "not reported" on the Costs card, and the user accepts that
   explicitly.
4. **A record.** The table lands in `evals/README.md` under a new section,
   "Decision distributions". It is allowed there because the corpus is the
   synthetic eval corpus, and no store data is involved. The policy line
   carries a comment naming that section, the kind and the run's date.

01c's own plan includes running items 1 and 2 for `response-selector`. Live
runs are opt-in, cost money and need the user's key, so the user runs them.
The plan records the result, whether or not it meets the bar. It does not set
`native_first` for `response-selector`: the rule in section 4.1 would refuse
that until 02-C2 sets `samples`. The flip belongs to 02-C2's change.

## 5. The sampling helper (C2)

A new gateway leaf, `backend/src/grimoire/draws.py`. It imports only
`decisions` (itself a leaf, `decisions.py:18-21`) and the standard library
(`hashlib`, `secrets`, `math`, `re`). Nothing in it reads the store, the
clock or the filesystem, so it needs no entry in `store/locks.py`. `store/`
modules may import it, as `response_protocol` already imports `decisions`.

**01c owns the sampler contract.** Every caller that draws (02, 13, 01g's
decide tool) uses `draw`, `new_seed` and the section 6 record as they are.
A caller's own reshaping of a distribution is expressed only as an
`Eligibility` (section 5.3), which this spec defines, applies and records.
02's draft already does such reshaping (it drops the none, floors options at
`1/(2n)`, and narrows to the addressed actors); it conforms to this section
rather than carrying a second sampler.

### 5.1 Functions

```python
ALGORITHM = "sha256-q32-icdf/1"   # recorded; any change of rule is a new name
SEED_BITS = 53                    # dice.py's reason: a JS-safe integer
QUANTUM = 2**32                   # weights are drawn as integers of 1/QUANTUM
MASS_SLACK = 0.02                 # section 5.4

@dataclass(frozen=True)
class Eligibility:
    only: tuple[str, ...] | None = None   # keys allowed; None = every offered key
    exclude: tuple[str, ...] = ()         # keys removed (e.g. decisions.NONE_KEY)
    floor: float = 0.0                    # minimum probability per eligible key

def new_seed() -> int
    """secrets.randbits(SEED_BITS). Test seam: module-level `_seed_source`,
    patched like `character_turns._rng`."""

def unit(seed: int, purpose: str) -> int
    """A 53-bit integer, uniform on [0, 2**53): the top 53 bits of
    sha256(b"grimoire/draw/1\\0" + str(seed).encode("ascii") + b"\\0"
    + purpose.encode("ascii"))."""

def quanta(w: float) -> int
    """math.floor(w * QUANTUM): exact, since w is in [0, 1] and QUANTUM is a
    power of two."""

def pick(weights: Sequence[tuple[str, int]], r53: int) -> str
    """Integer inverse CDF over positive integer weights in the given order:
    the first key whose cumulative bound exceeds (r53 * total) >> 53."""

def draw_from(weights: Sequence[tuple[str, float]], seed: int, purpose: str,
              eligibility: Eligibility = Eligibility()) -> str | None
    """(distribution, seed) -> selected: the reported weights, in canonical
    order, through `eligibility` (5.3), drawn with unit(seed, purpose).
    None when nothing eligible has weight."""

def draw(q: decisions.Question, result: decisions.ItemResult, *, seed: int,
         purpose: str = "", eligibility: Eligibility = Eligibility()) -> Draw
    """Section 5.4's preconditions (C4), then draw_from; returns the
    selection and its record (section 6)."""

def replay(record: Mapping) -> str | None
    """Recompute a record's selection from its own fields (section 6.2).
    Raises ReplayError on a record of another ALGORITHM or a malformed one."""
```

`Draw` is a frozen dataclass: `basis` (`"sampled" | "answer" | "none"`),
`key` (the selection as a support key, `NONE_KEY` included, or `None`),
`value` (the selection in the answer's own type: `bool` for a predicate,
`int` for a score, `str` or `None` for a choice), and `record` (the C3 dict).
`draw` reads the item's server from `result.served`, which is
`(kind, provider_id, model)` (01d section 5.6). Until 01d lands, `draw`
takes an optional `served=` of that same three-part shape.

### 5.2 Why this RNG, and why integers

- **SHA-256 for the uniform.** SHA-256 is a fixed standard. CPython on
  desktop (`requires-python = ">=3.11"`), Chaquopy's CPython 3.12
  (`android/app/build.gradle.kts:64`) and a browser's SubtleCrypto all give
  the same 53-bit integer. `random.Random` is guaranteed stable only for
  `random()`, not for `choice`, `choices`, `randrange` or `randint`
  (`store/dice.py:84,92` uses `randint`, which is the trap), and it gives
  one coupled stream per seed.
- **Integer weights for the cumulative sums.** Floating-point summation is
  not stable across the supported interpreters. CPython 3.12 switched
  `sum()` over floats to compensated summation, so `sum([0.1] * 10)` is
  `0.9999999999999999` on 3.11 and `1.0` on 3.12. A JavaScript `reduce`
  agrees with 3.11. With a mass that large, a draw near an interval
  boundary can select a different key on different interpreters. CI runs
  3.11 and 3.14. So the draw never adds floats. Each weight becomes
  `quanta(w)`, an exact integer in `[0, 2**32]`. Totals and cumulative bounds
  are Python integers: at most 255 keys of 2**32 each is under 2**40, and
  the product with a 53-bit `r53` is under 2**93. Python's integers are
  exact, and JavaScript reproduces this with `BigInt`. `math.fsum` was
  considered and rejected. It is correctly rounded and therefore stable,
  but a browser has no equivalent, and an integer rule needs none.
  Quantisation loses weight below 2**-32, which is far under any reported
  precision.
- **`purpose` decouples draws.** A batched turn plan (02-C3) may draw a
  speaker and an intent from one decision. With one RNG stream, the second
  draw would depend on how many came before it: the coupling
  `group_play.py:200-204` works around by making its draw count depend "on
  the cast alone". With `unit(seed, purpose)`, each draw is a pure function
  of its own label. 13's joint choice can draw `purpose="action"` and then
  `purpose=f"target:{action}"` from one seed, and replay either alone.
- **`purpose` is printable ASCII**, at most 200 characters (ids and fixed
  labels). `draw` raises `ValueError` otherwise. Python's `encode()` raises on
  a lone surrogate, while a browser's `TextEncoder` substitutes U+FFFD, so
  the two would disagree on any other text.
- **The seed is 53 bits**, for `dice.py`'s reason (:117-119): it stays exact
  as a JSON number in JavaScript. Every caller mints it with `new_seed()`.
  A 63-bit seed (02's draft uses `getrandbits(63)`) is not accepted:
  `draw` raises `ValueError` for a seed outside `[0, 2**53)`.

### 5.3 Support, eligibility and renormalisation

The draw is computed in five fixed steps. A caller cannot change these
steps. It can only supply an `Eligibility`, and the record carries what it
supplied.

1. **Offered keys, in canonical order.** This is the question's order,
   never the order of the report. For a choice, the options in order, then
   `NONE_KEY` (when `allow_none`). For a score, the level indices
   ascending, as `str(i)`. For a predicate, `("true", "false")`, with
   weights `(p, 1 - p)` from `probability`. Two providers can report the
   same distribution with keys in different orders, and both must give the
   same draw.
2. **Eligible keys.** The offered keys that are in `only` (when it is
   given) and not in `exclude`, still in canonical order. Examples:
   `exclude=(NONE_KEY,)` drops the reserved none; `only=addressed_refs`
   narrows the draw to the actors a post addressed. A key in `only` or
   `exclude` that is not offered raises `ValueError`. A caller's eligibility
   comes from its own deterministic state, never from the answer.
3. **Integer weights.** For each eligible key, `quanta(w)`, where `w` is its
   reported weight. A key the report left out has weight 0.
4. **Floor.** With `F = quanta(floor)`, each eligible key's weight becomes
   `max(q, F)`. A floor gives every eligible key a chance the report did not
   give it. That is the caller's recorded policy, not the backend's
   report, so it is allowed only through this field. `floor * len(eligible)`
   must not exceed 1, or `draw` raises `ValueError`, because a floor whose
   total would be more than all the probability there is is a mis-specified
   policy. 02's `1/(2n)` passes this check.
5. **Draw.** `total = sum` of the step-4 integers. When `total == 0`, there
   is no draw (section 5.4). Otherwise `pick(weights, unit(seed, purpose))`:
   each key with positive weight owns the half-open integer interval
   `[c, c + q)` of `[0, total)`, and the key whose interval holds
   `(r53 * total) >> 53` is selected. The arithmetic is exact, so no key
   can be "rounded past". Equal weights are equal intervals; ties need no
   rule. Renormalisation is exactly this: the draw is proportional to the
   step-4 weights, and nothing else.

### 5.4 When there is no draw

`draw` checks these preconditions, in order, on the question's `Answer`
and the *reported* weights. These are checked before any eligibility is
applied. The first check that holds decides:

| Condition | `basis` | `why` | `selected` |
|---|---|---|---|
| `answer is None` (abstained, refused, unreadable, error) | `none` | the answer's reason | `None` |
| no usable report (structured; dropped as invalid; a question type with no `distribution`, including `Rank` and `MultiSelect`) | `answer` | `no_report` | the answer's key, if eligible |
| the reported mass, `sum(quanta(w))` over offered keys, is under `quanta(1 - MASS_SLACK)` | `answer` | `partial` | the answer's key, if eligible |
| the answered key's reported weight is 0, or the answered key is absent from the report | `answer` | `inconsistent` | the answer's key, if eligible |
| otherwise, with total 0 after steps 2-4 | `answer` | `ineligible_mass` | the answer's key, if eligible |
| otherwise | `sampled` | `""` | the step-5 key |

In the `answer` rows, an answer whose key is not eligible gives
`basis: none`, `why: "ineligible"`, `selected: None`. The caller narrowed the
set, and a plain answer outside it is not returned as a selection.

- **Partial reports are not normalised** (review S1). Mass the report left
  out could belong to any option, so drawing in proportion to what was
  reported would invent the missing mass's shape. 01d §6.1 refuses the same
  move for the margin. `MASS_SLACK = 0.02` absorbs rounding in reported
  values. With two-decimal reports it covers up to four rounded weights, and
  more at finer precision. It will be tuned against the per-kind mass
  figures 01a's report shows. A mass *over* 1 is drawn in proportion and
  recorded (`mass_q`). This is the over-reporting case, where every option
  was priced.
- **An answer the report contradicts is not sampled** (review S6).
  `native_answer` keeps an explicit `chosen` "whatever the probability beside
  it says" (`decisions.py:866-901`). When that key has no reported weight, a
  draw could never return the backend's own answer, so the report and the
  answer disagree about what was meant. The answer stands, recorded
  `inconsistent`.
- **Rank and MultiSelect marginals are never sampled.** 01e-C4 adds
  `Answer.marginals`, per-candidate probabilities kept apart from
  `distribution`. Each marginal is its own event, and marginals do not sum
  to 1 over mutually exclusive outcomes, so a draw over them would be a draw
  from a distribution nobody reported. `draw` reads only `distribution` and
  `probability`. A `Joint` (01e-C3b) is a flattened Choice, so its
  `distribution` is drawn like any Choice's.
- **A predicate at exactly P(true) = 0.5 is `abstained`**
  (`_native_predicate`, :908-915), so it is never sampled. The backend's
  report is "no answer", and turning that into a coin flip is what C4
  forbids.

## 6. The replay record (C3)

One shape, version 1, for every caller:

```json
{
  "v": 1,
  "algorithm": "sha256-q32-icdf/1",
  "question": "next",
  "purpose": "next",
  "basis": "sampled",
  "sampled": true,
  "why": "",
  "offered": ["characters:mara", "characters:seraphine", "grimoire", "<none>"],
  "distribution": [["characters:mara", 0.55], ["characters:seraphine", 0.3],
                   ["grimoire", 0.1], ["<none>", 0.05]],
  "eligibility": {"only": null, "exclude": ["<none>"], "floor": 0.125},
  "mass_q": 4294967293,
  "drawn_q": 4187593112,
  "seed": 4503599627370495,
  "selected": "characters:mara",
  "answer": "characters:mara",
  "backend": "native",
  "kind": "openai_compatible",
  "provider": "realm-openai",
  "model": "decision-model-x"
}
```

- `offered` is every legal key in canonical order, including keys the
  report left out (review M2: 13's draft wants the eligible choices in the
  record).
- `distribution` is the **reported** weights as an ordered list of pairs in
  canonical order. A JSON object's key order is not a contract, and the draw
  depends on order. Python's `json` and JavaScript's `JSON.parse` round-trip
  binary64 exactly, and `quanta` is exact, so a stored record replays
  exactly.
- `eligibility` is what the caller supplied, as given. `{"only": null,
  "exclude": [], "floor": 0.0}` means none was supplied.
- `mass_q` (the reported mass in quanta) and `drawn_q` (the step-5 total)
  are informational. `replay` recomputes both and ignores the stored ones.
- `selected` and `answer` are both **keys**: an option id, `NONE_KEY`,
  `str(i)` for a score, `"true"`/`"false"` for a predicate (review M1). So
  `answer == selected` is a meaningful comparison. It tells 02's eval gate
  how often sampling departed from the backend's own answer, without
  re-asking anything.
- `sampled` is `basis == "sampled"`. It is kept as its own boolean because a
  consumer that only acts on the result (13) reads it rather than the basis
  vocabulary.
- `kind`, `provider` and `model` come from `ItemResult.served`. `kind` is
  kept because 01a and 01d tune per kind.
- **The record holds no prose.** It holds keys (option ids, which may be
  refs such as `characters:mara`, the same refs the round record's
  `eligible` already stores), floats, ints and the server's names. No
  context, instructions or rationale. It is never written to `logs/`.
- 02's draft names its fields `selection`, `policy` and `support`, and
  stores `distribution` and `support` as objects. Under this spec it stores
  this record unchanged, under the key it chooses on its own record (for
  the round record, `pick`).

### 6.1 Persistence is the caller's, with the outcome

The helper returns the record. It never stores it. Every caller follows
four rules:

1. **Same write, same lock.** The record goes into the write that persists
   the outcome it chose, under the lock that write holds. For the speaker
   pick, that is `_round_state(..., actor_ref=draw.value, pick=draw.record)`,
   one `responses.update_round` under `campaign_lock`. For 13, it is the
   transaction-ledger entry that commits the Action. It never goes into a
   side store, and never only into the prompt log. Capture can be switched
   off, and is not persistence.
2. **Never re-draw a persisted outcome.** "Already decided" means **the
   record is present** (`round.get("pick") is not None`), not that the
   outcome field is set (review M3). `_first_actor` today tests
   `actor_ref is None`, which cannot tell "not asked yet" from "picked:
   none". A retry, a recovery or a roll resume reads the stored record, as
   `group_play`'s caller does today. A reroll is a new decision with a new
   seed, and the superseded round keeps its record.
3. **Mint the seed once per decision occasion**, with `new_seed()`, at the
   point the caller asks. Nothing derives a seed from an id, which would make
   two rerolls of one round identical.
4. **Readers tolerate absence.** A round written before this spec has no
   `pick`, and the frozen campaign's rounds have none. Readers use
   `.get("pick")`.

### 6.2 Replay

`replay(record)` checks `v` and `algorithm`. For `basis: sampled`, it
recomputes steps 1 to 5 from `offered`, `distribution`, `eligibility`,
`seed` and `purpose` alone and returns the key. For any other basis, it
returns the stored `selected` without drawing: nothing was drawn, so there
is nothing to replay. A record that is internally inconsistent, for example
`sampled` with a null seed or a key in `distribution` that is not in
`offered`, raises `ReplayError`.

## 7. C4: nothing becomes a sampled answer

Section 5.4 is C4's table. In short:

- **A non-answer is never drawn.** Abstained, refused, unreadable and error
  give `basis: none`. A draw would overrule the abstention or the refusal,
  or guess an answer nobody gave.
- **No usable report is never drawn**, whether it is structured, dropped,
  partial, inconsistent or marginals only. The answer stands as `basis:
  answer`. A one-hot draw would only relabel it as sampled.
- **A plain answer can be acted on, and is not a draw.** This is a
  cross-spec decision (13's open question 4). When an NPC action decision
  comes back with no usable distribution, 13 may act on the plain Choice
  answer. The record says `sampled: false` (`basis: answer`, `seed: null`).
  It is never presented as a sample, and `replay` does not draw from it.
- **A sampled `NONE_KEY` is a selection, not an abstention**, unless the
  caller excluded it through `Eligibility`. When the draw lands there,
  `Draw.value` is `None` with `basis: sampled`. The caller maps that as it
  maps an explicit none (for the speaker pick, control goes back to the
  player).
- **The caller's issue mapping stays the caller's.** `selection_of`'s
  `INELIGIBLE` and `INVALID_HANDOFF` are decided from the `Answer` before
  any draw, as today.
- **Only a task whose policy has `samples = True` calls `draw`.** Callers'
  tests assert this; `draw` does not check it at runtime. Factual
  classifications (continuity, scene-break, voice drift, 02-C5's kits, 11's
  epistemic classes) never sample. A verdict about what happened in the
  fiction must not change between two runs of the same input.

## 8. Contract

- **01c-C1. Recorded policy.** Structured verbalised probabilities are
  rejected: never requested, never read. Native-first is opt-in per task
  and per adapter kind (`TaskPolicy.native_first`). For a primary of a
  listed kind that generates and is `native_capable` (a known `yes`),
  `inference.stages` puts an isolated native stage on the same model, with
  `retries=0`, before the structured stage. That stage never adds its
  provider to `dead`. The failure-driven chain is otherwise unchanged. A
  kind is added only with the section 4.3 evidence from 01a, recorded in
  `evals/README.md`. Every task is off at landing.
  `inference.reports_distribution(resolved)` says whether the first stage
  is native. Failure behaviour: anything that stops the native stage hands
  its items to the structured stage and to the riding fallback; a model that
  is not `native_capable` gets today's chain.
- **01c-C2. `draws.py`, which owns sampling.** A SHA-256 integer
  inverse-CDF draw (`ALGORITHM = "sha256-q32-icdf/1"`). Integer weights
  (`quanta`) and exact integer cumulative sums make it identical on Python
  3.11 to 3.14, on Android and in a browser. The caller's pre-draw step is
  one recorded `Eligibility` (`only`, `exclude`, `floor`), applied and
  renormalised as section 5.3 defines. Seeds come from `new_seed()` (53
  bits), and `purpose` is printable ASCII. `draw` never raises on a valid
  `ItemResult` with a valid eligibility and seed.
- **01c-C3. Replay record.** The section 6 shape, version 1, JSON-safe,
  holding no prose. It is the one record shape for every caller. The caller
  stores it with the outcome it chose, in the same write and under the same
  lock. The presence of the record means "decided", and a decided outcome is
  never re-drawn. Guarantee: `replay(record) == record["selected"]` for
  every record `draw` produced, on any supported interpreter.
- **01c-C4. Never sampled.** `basis: sampled` occurs only for an answered
  question whose report is usable, at least `1 - MASS_SLACK` in mass, and
  gives the answered key positive weight. Abstained, refused, unreadable,
  error, no report, a partial or inconsistent report, and Rank or
  MultiSelect marginals (01e-C4) never produce a draw. A plain answer may be
  acted on, recorded `sampled: false`; it is not a draw and is never
  replayed.

## 9. Interaction with repo rules

- **Import guard** (`test_import_guard.py`): `draws` imports at module scope,
  only `decisions` and the stdlib. The graph stays acyclic.
  `inference.stages` reading `store.routing.policy` uses the existing
  `store` import (`inference.py:70`).
- **Routing guard and operation guard:** unchanged. No new task, route or
  `require_inference` call. `native_first` changes a stage, not a route's
  operation.
- **Metering:** a native-first stage opens one meter per item through the
  existing `_native`. No new meter site. `test_usage_guard.py` already scans
  `inference.py`.
- **Costs:** native-first rows are native rows (`decision_mode: native`), so
  they are `cost_usd` or unpriced, never `modelled_usd`. Section 4.3 item 3
  makes that visible before a task switches. `usage_rollup.VERSION` needs no
  change, because no new kind of row is created.
- **Capture:** a native-first item is captured as a native item already is
  (`_captured`, `inference.py:333-346`). With 01b, every site files it.
- **Privacy:** the record carries no prose. The evidence in section 4.3 is
  the synthetic corpus's only. No store measurement appears in any committed
  file.
- **Android / pydantic v1:** pure stdlib. No model classes, no new
  dependency.
- **Health:** under native-first, a decisions endpoint's 5xx or timeout is
  observed against the connection, as any native call is (section 4.2.3).
- **Locks and detached runs:** the helper takes no lock. Persistence rides
  the caller's existing write (section 6.1). No new run class.
- **Docs:** CLAUDE.md's decide paragraph ("A model that can generate stays on
  structured generation whatever its `decide_native` says ... the chain does
  not do it today") is rewritten in the change that adds the branch, to say
  "unless its task's policy lists its kind in `native_first`", and states the
  isolated-stage rule (section 4.2.1). `test_docs_guard.py` is run.

## 10. Tests and acceptance

`backend/tests/test_draws.py`:

- `unit` golden vectors: fixed `(seed, purpose)` pairs give pinned 53-bit
  integers. They are checked in, so that a browser-side check can
  reproduce them.
- **Interpreter-independence golden.** Ten weights of 0.1 (where `sum()`
  differs between 3.11 and 3.12), and the weights `[0.6, 0.26, 0.76, 0.7]`
  with an `r53` at the boundary where a float draw flips between naive and
  compensated summation (the review's case), pin the expected `mass_q`,
  `drawn_q` and selection. CI's 3.11 and 3.14 legs both run this test.
- `pick`: half-open integer intervals, zero-weight keys skipped, the largest
  `r53` (2**53 - 1) selecting the last positive key, and two equal weights
  splitting at their boundary.
- Canonical order: one distribution reported in two key orders draws the
  same key for every seed in a sweep.
- Eligibility: `exclude=(NONE_KEY,)`, `only=` a subset, a floor that lifts
  an unreported key, a floor too large for the eligible set
  (`ValueError`), and an `only` key that is not offered (`ValueError`). The
  record carries each as supplied.
- Section 5.4: one case per row, including a predicate at 0.5, a refused
  answer (`native_answer(refused=True)`), a structured answer (`basis:
  answer`, `seed: null`, `why: no_report`), a partial report (`{mara: 0.4,
  seraphine: 0.1}`), an inconsistent one (an explicit choice with no
  weight), an answer outside the eligible set (`basis: none`, `why:
  ineligible`), an `Answer` that carries only marginals, and a sampled
  `NONE_KEY`.
- Seeds and purposes: a 63-bit seed and a non-ASCII purpose raise
  `ValueError`.
- `replay`: equals `selected` over a property sweep of random supports,
  eligibilities and seeds. A non-sampled record returns its stored
  `selected`. An unknown `algorithm`, or a sampled record with a null seed,
  raises `ReplayError`.
- JSON round trip: `replay(json.loads(json.dumps(record)))` equals
  `selected`.

`backend/tests/test_inference_decide.py` and `test_inference_decide_native.py`
(extended):

- A policy listing the primary's kind, on a primary that generates and is
  `native_capable`, gives `[native (isolated, retries=0), structured]`, plus
  the fallback stage when it is apart. A kind that is not listed, an
  `unknown` for `decide_native`, a kind with no endpoint, or a preset
  `never` gives today's stages.
- **Fall-through (review B1).**
  - A 403 (`auth`) from the native-first stage: the structured stage on the
    same provider answers.
  - A `rate_limit` from it: the structured stage answers, and the native
    stage made exactly one request.
  - An `auth` from it, with a structured fallback on another provider riding
    the structured stage: the fallback answers when the structured primary
    also fails.
  - A connection-wide failure on the *structured* stage still skips a later
    stage on that provider, as today.
- An item the native stage fails is answered by the structured stage. A
  native `refused` is not re-asked.
- `reports_distribution` is True for a native-only primary and for a listed
  kind on a `native_capable` primary, and False otherwise.
- With no task setting `native_first`, `stages` is identical to the baseline
  for every resolution in the existing equivalence fixtures, and
  `test_decide_chain_golden.py` stays green without regenerating its golden.

`backend/tests/test_task_policy.py`: the section 4.1 rules, each with a
planted violation.

Evals: the `decide.distribution` grader (n/a on structured), and the
`evals/README.md` section with the section 4.3 table for `decide-speaker`.
`evals/runner.chain` is unchanged and still allows `unknown`.

Acceptance: all of the above pass under `make check`, including
`check-pydantic1`. No task's behaviour changes at landing.

## 11. Non-goals

- Logprob-derived distributions on structured generation (section 3.1).
- Any reshaping of a reported distribution other than the recorded
  `Eligibility` (mask and floor). Temperature or sharpening would need a new
  `ALGORITHM`, specified by whoever needs it.
- Sampling a `Rank` (Plackett–Luce) or a multi-select (01e). The helper
  samples one keyed distribution. A joint choice is drawn as a flattened
  `Joint`, or as successive draws with distinct `purpose`s.
- Any calibration of, or comparison between, backends' probabilities.
- Fixing `store/dice.py`'s use of `randint` (section 5.2). It is recorded in
  the checklist's "Found on main" list.
- Keeping the health dot green while a decisions endpoint fails under
  native-first (section 4.2.3).

## 12. Open questions

1. **Is `MASS_SLACK = 0.02` right?** Recommendation: start there, show
   each kind's reported mass in 01a's table, and widen it only if a kind
   rounds coarsely. A mass over 1 stays drawn in proportion and recorded.
2. **Native-first on `decide_native: unknown`?** Recommendation: no.
   Production requires a known `yes` (section 3.2). The user can make it
   `yes` with a passing test call or a model-facts override. The eval runner
   still allows `unknown`, so the evidence can be gathered.
3. **Settings visibility.** Should 01s's decide note say "native first" on a
   route row whose task lists the primary's kind? Recommendation: yes, as a
   read-only phrase driven by `reports_distribution`. No toggle, because
   the policy is code and is switched on evidence.
4. **Where the evidence lives.** Recommendation: `evals/README.md`, section
   "Decision distributions", with the policy line's comment pointing to it.
5. **Does 02's floor belong in `Eligibility` or in 02?** Decided: in
   `Eligibility` (the coordinator ruled that 01c owns the sampler). 02's
   `1/(2n)` floor, its none exclusion and its addressed-actor narrowing are
   each a field of it.

## 13. Review record

Substitute adversarial review, 2026-10-10 (`reviews/01c.md`: 3 blocking,
6 should-fix, 6 minor). Each finding was checked against the code at
`35c1fb7`.

| # | Finding | Disposition |
|---|---|---|
| B1 | An `auth` or `rate_limit` from a native-first stage marks the provider dead and skips the same-model structured stage and its riding fallback | **Fixed.** Verified (`openrouter.py:53-58`, `llm.py:305-312`, `inference.py:640-663`). Isolated stage, a dead skip only when every route is dead, and fall-through tests (4.2.1, 10) |
| B2 | 01c forbids the reshaping 02 performs, and 02 uses a different record and seed | **Fixed, by the coordinator's ruling:** 01c owns the sampler. A recorded `Eligibility` (mask and floor) with renormalisation defined here (5.3), one record shape (6), and one seed source (5.2). 02 conforms |
| B3 | Unspecified float summation; `sum()` changed in 3.12 | **Fixed.** Integer quanta and exact integer cumulative sums (5.2, 5.3), with a golden that differs under `sum()` (10) |
| S1 | A partial report is normalised, unlike 01d's margin | **Fixed.** Below `1 - MASS_SLACK`, `basis: answer`, `why: partial` (5.4) |
| S2 | The native-first stage spends the retry budget first | **Fixed.** `retries=0`, and the added latency is stated (4.2.2) |
| S3 | The eval runner does not make the same check and must not | **Fixed.** `native_capable` is production-only, and the runner keeps `decides_natively` (4.2) |
| S4 | `served` has two shapes | **Fixed.** One three-part `(kind, provider_id, model)`; the record keeps `kind` (5.1, 6) |
| S5 | Evidence from one model switches every user | **Fixed.** `native_first` lists adapter kinds, and evidence is per kind (4.1, 4.3) |
| S6 | An explicit choice the distribution contradicts | **Fixed.** `basis: answer`, `why: inconsistent` (5.4) |
| M1 | `selected` and `answer` are typed differently | **Fixed.** Both are keys (6) |
| M2 | The record drops options the report omitted | **Fixed.** `offered` (6) |
| M3 | "Already picked" is ambiguous | **Fixed.** The presence of the record (6.1) |
| M4 | The checklist edge lacks 01e-C4 (S) | **Not changed here**, because only the two specs are edited. 01e-C4 is now in Depends on, and the checklist edge should add it |
| M5 | Decisions-endpoint failures mark chat health failing | **Stated** (4.2.3); fixing it is a non-goal |
| M6 | A non-ASCII `purpose` encodes differently in a browser | **Fixed.** Printable ASCII only, checked (5.2) |

# 01c. Decision distributions and seeded sampling

**Status:** Draft — cross-linked; spec gate pending.
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
| `ItemResult.served` (shared structure) | 01d | The record's `provider` and `model` per item (section 6) | Shared structure. Until it lands, the caller passes `served=` |
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
- Reshaping a distribution (temperature, sharpening, flooring). See section 11.

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

It is opt-in per task (`TaskPolicy.native_first`, section 4.1). It is allowed
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

At landing, every task has `native_first = False`. A generating Decision
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
    # ... 01d's fields (fallback, escalation) ...
    #: The task's caller draws from the decision's distribution (01c-C2).
    samples: bool = False
    #: Ask a primary that also generates through its decisions endpoint first
    #: (01c-C1, section 4.2). Only where a distribution is consumed.
    native_first: bool = False
```

`routing` stays a pure leaf (`store/routing.py:15-16`). The fields are
literals.

`backend/tests/test_task_policy.py`, which 01d also creates, holds these
rules:

- every policy key is a task some route claims (`TASK_ROUTE`);
- `samples` and `native_first` appear only on a task whose route's
  `operation` is `decide`;
- `native_first` implies `samples` or a `low_margin` trigger (01d);
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
policy.native_first and primary.decision_mode == "structured"
                    and native_capable(primary)
  -> Stage(NATIVE, whole.alone(), None)          # same model, decisions endpoint
     Stage(STRUCTURED, whole or whole.alone(), None)  # as today
     [fallback stage, as today, when apart]
otherwise -> today's stages, unchanged
```

- `native_capable(attempt)` is a new function in `resolve`. It holds when the
  attempt's adapter kind has a decisions endpoint
  (`adapters.decides_natively(kind)`) and `decide_native` is a known `yes`
  (`capabilities.YES`). A preset's `never` already arrives as an
  adapter-source `no` (`capabilities.py:206-209`). `evals/runner.chain`
  (`evals/runner.py:233-280`) makes the same checks for `--decide-backend
  native`, and the two share this function.
- `inference.reports_distribution(resolved) -> bool` is the one predicate
  a caller asks before it plans to sample (02-C2 asks it to decide whether
  the sampled path is available at all). It is True when the first stage
  `stages(resolved)` builds is native: a native-only primary, or a
  `native_first` task on a `native_capable` primary. It is pure, reads only
  the resolution and the task policy, and sends nothing. It is a forecast,
  not a guarantee: an item the native stage fails is answered by a later
  structured stage with no distribution, so the caller still goes through
  `draw`, and C4 still decides each item. The settings view may show the
  same predicate.
- **The failure-driven rule is unchanged.** The structured stage takes only
  the items the native stage *failed* (`run_stages`, `inference.py:595-681`).
  A native `refused`, an `abstained` or an `unreadable` from a well-formed
  body is final, exactly as on a native-only model.
- `run_stages`'s existing rules apply as they are. A connection-wide failure
  on the native stage (auth, `missing_key`, money) skips the structured stage
  on the same provider (`dead`, :645). A run of native timeouts
  (`NATIVE_TIMEOUT_STOP`, :97) stops the native stage only, and the
  structured stage still runs. 01 wrote this rule for "a hung decisions
  endpoint", which "says nothing about the chat endpoint".
- The native stage is metered and captured as any native stage is: one meter
  per item, `decision_mode: native` stamped, no sampler preset sent
  (`_native`, :452-540). Its ledger rows are native rows, so
  `modelled_usd` is never computed for them (CLAUDE.md, "`modelled_usd` is
  never computed for a native decision row").
- **The chain's worst case grows by one stage** for a native-first task.
  01's time bounds for the continuity sweep assume two stages. A task that
  turns native-first on restates its own bound in the change that turns it on.
- `ResolvedInference.decision_mode` stays the primary's per-attempt mode
  (`structured`). The settings view's decide note (01s, "the decide note")
  gains "native first" for a task whose policy has it. Section 12 has this
  as an open question.

### 4.3 The evidence that switches a task on

A change that sets `native_first = True` for a task carries all of the
following, run on at least one model that both generates and has a known
native endpoint:

1. `evals/run.py --live --provider ID --model NAME --decide-backend native`
   and then `--decide-backend structured`, over the task's decide cases
   (today `decide-speaker`, `evals/README.md:29`). The output goes through
   01a-C3's comparison table: per-item correctness, wall time, prompt and
   completion tokens, and the three money columns kept apart.
2. **The bar:**
   - every case the structured backend passes, the native backend passes on
     the same model;
   - every native answer in the run carries a usable distribution. A new
     check, `decide.distribution`, records this. It is graded n/a on a
     structured answer, the way `NATIVE_RATIONALE` is graded n/a in the other
     direction (`evals/graders.py:707-746`).

   Latency and cost are reported but are not gated. They are the user's
   call, made from the table.
3. **A cost note.** A native row whose provider reported no cost is unpriced
   (`unpriced_native_calls`), and no rate can model it. If the provider's
   decisions endpoint reports no cost, the change says that the task's rows
   will read "not reported" on the Costs card, and the user accepts that
   explicitly.
4. **A record.** The table lands in `evals/README.md` under a new section,
   "Decision distributions". It is allowed there because the corpus is the
   synthetic eval corpus, and no store data is involved. The policy line
   carries a comment naming that section and the run's date.

01c's own plan includes running items 1 and 2 for `response-selector`. Live
runs are opt-in, cost money and need the user's key, so the user runs them.
The plan records the result, whether or not it meets the bar. It does not set
`native_first` for `response-selector`: the rule in section 4.1 would refuse
that until 02-C2 sets `samples`. The flip belongs to 02-C2's change.

## 5. The sampling helper (C2)

A new gateway leaf, `backend/src/grimoire/draws.py`. It imports only
`decisions` (itself a leaf, `decisions.py:18-21`) and the standard library
(`hashlib`, `secrets`, `math`). Nothing in it reads the store, the clock or
the filesystem, so it needs no entry in `store/locks.py`. `store/` modules
may import it, as `response_protocol` already imports `decisions`.

### 5.1 Functions

```python
ALGORITHM = "sha256-icdf/1"     # recorded; a change of algorithm is a new name
SEED_BITS = 53                  # dice.py's reason: a JS-safe integer

def new_seed() -> int
    """secrets.randbits(SEED_BITS). Test seam: module-level `_seed_source`,
    patched like `character_turns._rng`."""

def uniform(seed: int, purpose: str) -> float
    """A float in [0, 1): the top 53 bits of
    sha256(b"grimoire/draw/1\\0" + str(seed).encode() + b"\\0" + purpose.encode())
    divided by 2**53. Exact in binary64."""

def support(q: decisions.Question, answer: decisions.Answer
            ) -> tuple[tuple[str, float], ...] | None
    """The reported weights in canonical order, or None when there is
    nothing usable (section 5.3)."""

def pick(weights: Sequence[tuple[str, float]], u: float) -> str
    """Inverse CDF: the key whose half-open interval holds u * mass."""

def draw_from(weights: Sequence[tuple[str, float]], seed: int, purpose: str) -> str
    """(distribution, seed) -> selected: pick(weights, uniform(seed, purpose))."""

def draw(q: decisions.Question, result: decisions.ItemResult, *, seed: int,
         purpose: str = "", served: tuple[str, str] = ()) -> Draw
    """C4 applied, then draw_from; returns the selection and its record."""

def replay(record: Mapping) -> str | None
    """Recompute a record's selection from its own fields. Raises
    ReplayError on a record of another ALGORITHM or a malformed one."""
```

`Draw` is a frozen dataclass: `basis` (`"sampled" | "answer" | "none"`),
`value` (the selection in the answer's own type: `bool` for a predicate,
`int` for a score, `str` or `None` for a choice), `key` (the selection as its
support key, `NONE_KEY` included), and `record` (the C3 dict).

### 5.2 Why this RNG

- **It is reproducible by definition.** SHA-256 and IEEE-754 binary64 are
  fixed standards. CPython on desktop (`requires-python = ">=3.11"`), Chaquopy's
  CPython 3.12 (`android/app/build.gradle.kts:64`) and a browser's
  SubtleCrypto all compute the same `u`. The record can therefore be replayed
  by a frontend inspector, or by a test on any interpreter.
- **`random.Random` is guaranteed only for `random()`.** Python's
  reproducibility promise covers `random()` given a compatible seeder. It
  does not cover `choice`, `choices`, `randrange` or `randint`, whose
  algorithms may change between versions. `store/dice.py:84,92` uses
  `randint`, which is the trap. `Random(seed).random()` alone would be stable,
  but it gives one stream per seed.
- **`purpose` decouples draws from each other.** A batched turn plan (02-C3)
  may draw a speaker and an intent from one decision. With one RNG stream,
  the second draw would depend on how many draws came before it, the coupling
  `group_play.py:200-204` works around by making its draw count depend "on
  the cast alone". With `uniform(seed, purpose)`, each draw is a pure
  function of its own label. 13's joint choice can then draw
  `purpose="action"` and then `purpose=f"target:{action}"` from one seed,
  and replay either draw alone.
- **The seed is 53 bits**, for `dice.py`'s reason (:117-119): it stays exact
  as a JSON number in JavaScript.

### 5.3 Support, normalisation and ties

- **Canonical order is the question's, never the report's.** For a choice,
  the order is the options in order and then `NONE_KEY` (when `allow_none`).
  For a score, it is level indices ascending, as `str(i)`. For a predicate,
  it is `("true", "false")` with weights `(p, 1 - p)` from `probability`. Two
  providers can report the same distribution with keys in different orders,
  and the same draw must result from both.
- **What is reported is what is used.** A legal key the report left out has
  no weight and cannot be selected. Nothing is added for it.
- **Normalisation is implicit.** `pick` scales `u` by the reported mass (the
  sum of weights), so values that sum to 0.98 or 1.02 because of rounding
  draw as their proportions. The mass is recorded (section 6), so an endpoint
  that reports independent per-option scores rather than a distribution shows
  up in 01a's reports instead of being hidden. A mass that is zero or not
  finite is no support (`None`).
- **Ties are not a special case.** Each key with positive weight owns a
  half-open interval `[c, c + w)` of `[0, mass)`, laid out in canonical order.
  `pick` returns the first key whose cumulative bound exceeds `u * mass`.
  Two equal weights are two equal-width intervals. Floating-point rounding
  can leave `u * mass` at or past the last bound; then the last
  positive-weight key is returned, so `pick` is total. This differs from the
  answer's own tie rule, where a tie at the top is `abstained`. That rule
  decides whether the backend *answered*; the draw happens only after it did
  (C4).
- **Zero-weight keys stay in the record** but own no interval.

## 6. The replay record (C3)

```json
{
  "v": 1,
  "algorithm": "sha256-icdf/1",
  "question": "next",
  "purpose": "next",
  "basis": "sampled",
  "sampled": true,
  "distribution": [["characters:mara", 0.55], ["characters:seraphine", 0.3],
                   ["grimoire", 0.1], ["<none>", 0.05]],
  "mass": 1.0,
  "seed": 4503599627370495,
  "selected": "characters:mara",
  "answer": "characters:mara",
  "backend": "native",
  "provider": "realm-openai",
  "model": "decision-model-x"
}
```

- `distribution` is an **ordered list of pairs** in canonical order, holding
  the *reported* weights. A JSON object's key order is not a contract, and
  the draw depends on order. Python's `json` and JavaScript's `JSON.parse`
  both round-trip binary64 exactly, so a persisted record replays exactly.
- `sampled` is `basis == "sampled"`, kept as its own boolean because a
  consumer that only acts (13) reads it rather than the basis vocabulary.
- `basis` is `sampled` (a draw was made; `seed` is set), `answer` (the
  backend answered with no usable distribution; `selected` is that answer,
  `seed` is null), or `none` (no answer; `selected` is null and `reason`
  carries the answer's reason). The checklist headline names
  `{distribution, seed, selected, backend}`. The other fields make the record
  self-contained: the question can be rebuilt differently later (a roster
  changes), and the record must still replay.
- `answer` is the backend's own answer (explicit choice or argmax), kept
  beside the draw. Comparing `answer` with `selected` over many records shows
  how often sampling departed from the argmax. 02's eval gate wants that
  comparison, and it can be made without re-asking anything.
- `provider` and `model` are the item's server, when the caller has it.
  `Decision.provider` and `.model` are empty when several routes answered
  (`decisions.py:292-297`); 01d adds a per-item `served`.
- **The record holds no prose.** It holds keys (option ids, which may be
  refs such as `characters:mara`, the same refs the round record's
  `eligible` already stores), floats, ints and the server's names. No
  context, instructions or rationale goes into it. It is never written to
  `logs/`.

### 6.1 Persistence is the caller's, with the outcome

The helper returns the record. It never stores it. Every caller follows
four rules:

1. **Same write, same lock.** The record goes into the write that persists
   the outcome it chose, under the lock that write holds. For the speaker
   pick, that is `_round_state(..., actor_ref=draw.value, pick=draw.record)`,
   one `responses.update_round` under `campaign_lock`. For 13, it is the
   transaction-ledger entry that commits the Action. It is never kept in a
   side store, and never only in the prompt log. Capture can be switched
   off, and is not persistence.
2. **Never re-draw a persisted outcome.** A retry, a recovery or a roll
   resume reads the stored selection, as `group_play`'s caller does today.
   A reroll is a new decision with a new seed, and the superseded round
   keeps its record.
3. **Mint the seed once per decision occasion**, with `new_seed()`, at the
   point the caller asks. Nothing derives a seed from an id, which would make
   two rerolls of one round identical.
4. **Readers tolerate absence.** A round written before this spec has no
   `pick`, and the frozen campaign's rounds have none. Readers use
   `.get("pick")`.

## 7. C4: nothing becomes a sampled answer

`draw` applies these rules, in this order, to the question's `Answer`:

| The answer | `basis` | `selected` | Why |
|---|---|---|---|
| `answer is None`, any reason: `abstained`, `refused`, `unreadable`, `error` | `none` | `None` | The backend did not answer. A draw would overrule an abstention or a refusal, or guess an answer nobody gave |
| answered, and `support` is `None` (structured, an invalid report dropped, zero mass, no probability) | `answer` | the answer | Nothing was reported to sample. A one-hot draw would only relabel the answer as sampled |
| answered, with a usable support | `sampled` | `draw_from(...)` | A real reported distribution |

Consequences:

- A predicate at exactly P(true) = 0.5 is `abstained` (`_native_predicate`,
  :908-915), so it is never sampled, even though that is where a coin flip
  looks most natural. The backend's report is "no answer". Turning that into
  a random answer is what C4 forbids.
- A sampled `NONE_KEY` is a *selection*, not an abstention. When a nullable
  choice's distribution puts mass on the reserved none, a draw may land
  there. `Draw.value` is then `None` with `basis: sampled`, and the caller
  maps it as it maps an explicit none (for the speaker pick: hand control
  back). The caller cannot exclude it. Doing so would reshape the reported
  distribution (section 11).
- The caller's issue mapping stays the caller's. `selection_of`'s
  `INELIGIBLE` and `INVALID_HANDOFF` are decided from the `Answer` before any
  draw, exactly as today.
- **A plain answer can be acted on, and is not a draw** (cross-spec
  decision, 13's open question 4). When an NPC action decision comes back
  with no usable distribution, 13 may act on the plain Choice answer. The
  record says `sampled: false` (`basis: answer`, `seed: null`), it is never
  presented as a sample, and nothing is replayed from it: `replay` returns
  the stored `selected` for such a record without drawing.
- **Rank and MultiSelect marginals are never sampled.** 01e-C4 adds
  `Answer.marginals`, per-candidate probabilities kept apart from
  `distribution`. Marginals do not sum to one over a set of mutually
  exclusive outcomes (each candidate's is its own event), so an inverse-CDF
  draw over them would be a draw from a distribution nobody reported.
  `support` reads `distribution` and `probability` only, and a `Rank`,
  `MultiSelect` or other question with no `distribution` gives
  `basis: answer` (or `none`). A `Joint` (01e-C3b) is a flattened Choice, so
  its `distribution` is sampled like any Choice's.
- Only a task whose policy has `samples = True` calls `draw` (an assertion
  in `draw`'s callers' tests, not at runtime). Factual classifications
  (continuity, scene-break, voice drift) never sample. A verdict about what
  happened in the fiction must not change between two runs of the same
  input.

## 8. Contract

- **01c-C1. Recorded policy.** Structured verbalised probabilities are
  rejected: never requested, never read. Native-first is opt-in per task
  (`TaskPolicy.native_first`): `inference.stages` then puts a native stage on
  the same model before the structured stage, for a primary that generates
  and is `native_capable` (a known `yes`), with the failure-driven chain
  unchanged. A task sets the flag only with the section 4.3 evidence from
  01a, recorded in `evals/README.md`. Every task is off at landing.
  `inference.reports_distribution(resolved)` says whether the first stage is
  native. Failure behaviour: a native stage that fails an item hands it to
  the structured stage, as any failed stage does; a model that is not
  `native_capable` gets today's chain.
- **01c-C2. `draws.py`.** A SHA-256 inverse-CDF draw:
  `draws.draw_from(weights, seed, purpose) -> key` and
  `draws.draw(question, result, *, seed, purpose, served) -> Draw`. The draw
  is a pure function of (canonical weights, seed, purpose) under `ALGORITHM`,
  stable across Python versions, Android and a browser, and never raises on a
  valid `ItemResult`. `replay(record)` recomputes it.
- **01c-C3. Replay record.** The section 6 dict, JSON-safe, holding no
  prose. The caller stores it with the outcome it chose, in the same write
  and under the same lock, and never re-draws a stored outcome. Guarantee:
  `replay(record) == record["selected"]` for every record `draw` produced.
- **01c-C4. Never sampled.** `basis: sampled` occurs only for an answered
  question with a usable reported `distribution` or `probability`.
  Abstained, refused, unreadable, error, or no usable distribution never
  produce a draw. Rank or MultiSelect marginals (01e-C4) are never sampled.
  A plain answer may be acted on, recorded `sampled: false`; it is not a
  draw and is never replayed.

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
- **Locks and detached runs:** the helper takes no lock. Persistence rides
  the caller's existing write (section 6.1). No new run class.
- **Docs:** CLAUDE.md's decide paragraph ("A model that can generate stays on
  structured generation whatever its `decide_native` says ... the chain does
  not do it today") is rewritten in the change that adds the branch, to say
  "unless its task's policy is `native_first`". `test_docs_guard.py` is run.

## 10. Tests and acceptance

`backend/tests/test_draws.py`:

- `uniform` golden vectors: fixed `(seed, purpose)` pairs give pinned floats.
  These are checked in, and a browser-side check can reproduce them.
- `pick`: half-open intervals, zero-weight keys skipped, `u` near 1 with a
  mass whose float sum overshoots returns the last positive key, and two
  equal weights split at their boundary.
- Canonical order: one distribution reported in two key orders draws the
  same key for every seed in a sweep.
- Mass: weights summing to 0.98 and 1.02 draw proportionally, and `mass` is
  recorded. Zero mass gives `basis: answer`.
- C4 table: one case per row of section 7, including a predicate at 0.5, a
  refused answer (built with `native_answer(refused=True)`), a structured
  answer (`basis: answer`, `seed: null`), and a sampled `NONE_KEY`.
- `replay`: equals `selected` over a property sweep of random supports and
  seeds. A record whose `algorithm` is unknown raises `ReplayError`.
- JSON round trip: `replay(json.loads(json.dumps(record)))` equals
  `selected`.

`backend/tests/test_inference_decide.py` and `test_inference_decide_native.py` (extended):

- A `native_first` policy on a primary that generates and is `native_capable`
  gives `[native, structured]` stages, plus the fallback stage when it is
  apart.
- The same policy with `decide_native` unknown, a kind with no endpoint, or a
  preset `never` gives today's stages.
- An item the native stage fails is answered by the structured stage. A
  native `refused` is not re-asked. An auth failure on the native stage skips
  the structured stage on the same provider. A run of timeouts stops only the
  native stage.
- With no task setting `native_first`, `stages` is identical to the baseline
  for every resolution in the existing equivalence fixtures, and
  `test_decide_chain_golden.py` stays green without regenerating its golden.

`backend/tests/test_task_policy.py`: the section 4.1 rules, each with a
planted violation.

Evals: the `decide.distribution` grader (n/a on structured), and the
`evals/README.md` section with the section 4.3 table for `decide-speaker`.

Acceptance: all of the above pass under `make check`, including
`check-pydantic1`. No task's behaviour changes at landing.

## 11. Non-goals

- Logprob-derived distributions on structured generation (section 3.1).
- Temperature, sharpening, flooring or excluding options. Any reshaping of a
  reported distribution is a transform of what the backend said. If 02 or 13
  needs one, it is a recorded parameter of a new `ALGORITHM`, specified
  there.
- Sampling a `Rank` (Plackett–Luce) or a multi-select (01e). The helper
  samples one keyed distribution. A joint choice is drawn as successive draws
  with distinct `purpose`s.
- Any calibration of, or comparison between, backends' probabilities.
- Fixing `store/dice.py`'s use of `randint` (section 5.2). It is noted here
  only as a reason to avoid that API.

## 12. Open questions

1. **Should a distribution whose mass is far from 1 be refused rather than
   drawn proportionally?** Recommendation: no, not now. Draw proportionally,
   record `mass`, and have 01a's report show each endpoint's mass. Revisit
   if an endpoint turns out to report independent scores.
2. **Native-first on `decide_native: unknown`?** Recommendation: no, require
   a known `yes` (section 3.2). The user can make it `yes` with a passing
   test call or a model-facts override.
3. **Settings visibility.** Should 01s's decide note say "native first" on a
   route row whose task has it? Recommendation: yes, as a read-only phrase
   from the settings view (`decides_first: "native"`). No toggle, because the
   policy is code, switched on evidence.
4. **Hash RNG versus `random.Random(seed).random()`.** Recommendation: the
   hash. It is stable by standard, can be replayed in a browser, and gives
   per-purpose independence. `Random(...).random()` would also be stable, but
   gives one coupled stream.
5. **Where the evidence lives.** Recommendation: `evals/README.md`, section
   "Decision distributions", with the policy line's comment pointing to it.
   The alternative is an appendix to this spec, which would then go stale.

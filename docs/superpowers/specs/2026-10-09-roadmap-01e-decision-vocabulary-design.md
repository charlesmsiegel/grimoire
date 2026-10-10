# 01e. Decision vocabulary: Rank, finer Score, multi-select and joint choice

**Status:** Draft — spec gate (substitute review) folded in; Codex gate pending.
**Date:** 2026-10-09
**Roadmap:** 01e in `ROADMAP-CHECKLIST.md`. Lane: decision.
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** the bundle drafts `01-inference-backend-refactor.md` (section 9,
"Rank", and its line "`rank` may exist as an emulated convenience built from
score/choice") and `02-decision-integration.md` ("Treat rank as a Grimoire
convenience, not a required native primitive"). It extends the landed 01 spec
(`2026-10-07-inference-backend-refactor-design.md`), section 7.4, whose table
ends "`rank` is not provided — nothing needs it" (line 1124). 09 and 13 now
need it, and this spec is that change.

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning. Re-check both providers' decisions
> references and both structured-output references before coding, and record
> what moved in 01's Appendix B, as slice H did.

## Depends on

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| `decide`, `decisions.py`, the native and structured stages | 01 (landed) | Everything here extends that contract; nothing replaces it | Hard, and met |
| 01a-C1 eval cost, latency and token reporting | 01a | Tuning `MAX_RANK_CANDIDATES` and `MAX_SELECT_OPTIONS`, and comparing native and structured Rank | Soft: the types ship without it, the constants are tuned with it |
| 01b-C1 decision capture at every decide site | 01b | Captures the new answer shapes through `decisions.outcome` (section 8) | Soft: `outcome` is extended here, 01b decides who calls it |

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 01e-C1 `Rank`, with 01e-C2 `tiers` | 09 (S) | Reranking a bounded candidate set (09 section 7.3). The caller's rule (section 4.4): on a native Decision model a `Rank` must carry `pointwise`, and it never abstains |
| 01e-C2 `expected` | 09 (S, optional) | A finer per-candidate signal on a native-only Decision model. 09 section 7.3 does not read it today; adopting it is 09's choice |
| 01e-C1..C3 | 02 (S) | The vocabulary 02's decide routes may ask (02-C5); 02 works without it |
| 01e-C3a `MultiSelect` | 13 (H for multi-target actions) | The target set of an Action with `targets.max > 1`, asked as the second step after the action is chosen (section 6.2). This is the only shape that covers multi-target |
| (nothing from 01e) | 13 (legal sets past 254 options) | A legal set over the choice cap is decomposed: a `Choice` of action, then a `Choice` (one target) or a `MultiSelect` (01e-C3a, several) of that action's targets. Only the multi-target branch needs 01e |
| 01e-C3b `Joint` | 13 (S) | One action plus one target in one question, for a legal set of at most 254 pairs (255 with no reserved none on the structured path): `Pair` splitting, `Pair.key`, and the head-level reading (`head_marginal`, `head_first`). A plain flat `Choice` already does the rest |
| 01e-C4 `Answer.marginals` apart from `distribution` | 01c (S) | 01c's sampler reads C4 to refuse sampling marginals (01c-C2/C4) |

## 1. Current state (reconciled against main)

**The vocabulary is three types.** `Question = Predicate | Choice | Score`
(`backend/src/grimoire/decisions.py:208`). A `Choice` offers 2 to 255
options, or 1 to 255 with `allow_none` (`decisions.py:91-93`). A `Score`
has 2 to 10 ordered levels (`decisions.py:94`). An `Answer.answer` is
`bool | str | int | None` (`decisions.py:239`). An answer that is `None`
carries a reason from the closed set `unreadable`, `refused`, `abstained`,
`error` (`decisions.py:57`, enforced at `decisions.py:246-254`). No call site
asks a `Score` today: every decide site asks a `Predicate` or a `Choice`
(`store/scene_break.py:273`, `store/voice_drift.py:279`,
`store/response_protocol.py:120`, `store/continuity/identity.py:750`,
`store/continuity/reconcile.py:1582`, `store/inference/probes.py:54`).

**The structured schema is a deliberate subset.** The module docstring
(`decisions.py:32-38`) limits a batch schema to `type` (object, string,
boolean, integer, null), `enum`, `anyOf`, `required`, `properties` and
`additionalProperties: false`, because OpenAI strict mode and Anthropic's
`output_config.format` accept different subsets. A score is an integer `enum`
because Anthropic refuses numeric bounds (`decisions.py:448-454`). The batch is
four objects deep (`decisions.py:126-133`). Chunks hold at most 8 items
(`MAX_ITEMS_PER_CALL`, `decisions.py:101`), at most 1000 enum values
(`MAX_ENUM_VALUES`, `decisions.py:109`) and strict mode's string and property
budgets (`decisions.py:122-133`), enforced by `chunks`
(`decisions.py:724-750`) and, per item, by `validate` (`decisions.py:397-436`).

**Structured parsing is tolerant and never raises** (`parse`,
`decisions.py:687-715`). A choice value is matched exactly, then through
`normalise` and aliases (`_read_choice`, `decisions.py:660-671`). An unknown
value is `unreadable` with detail `NOT_AN_OPTION`.

**Native decisions are one request per item, and each question is answered
alone.** Both endpoints know only three question types. OpenRouter maps
`Predicate` → `noul`, `Choice` → `choice`, `Score` → `score`
(`openrouter.py:115`, body at `openrouter.py:137-142`). OpenAI maps them to
`predicate`, `choice`, `score` (`openai_compatible.py:135-136`, body at
`openai_compatible.py:160-165`). 01's Appendix B records both references:
neither has a rank, a multi-select or a joint type. Neither documents a bound
on questions per request. The OpenRouter tutorial gives 2 to 10 levels for a
score, and OpenAI documents no bound on levels. Both cap a choice at 255
options, which `NATIVE_MAX_OPTIONS` holds counting the reserved none
(`decisions.py:140`). What an endpoint cannot carry is refused unsent by
`native_gap` (`decisions.py:812-821`), raised in `LLMClient.decide_native` as
`bad_response` with code `native_unrepresentable` (`llm.py:1470-1472`). The
chain then moves the item to the next stage.

The 01 spec records why a native question must stand alone (line 1356).
Continuity identity first asked `id` as a separate question, "null unless the
decision is existing". The native endpoint answered each question of an item
alone, so it answered `id` with none, and every native `existing` was
downgraded. The id was then folded into the decision's own options. **Any
conditioned answer must therefore be one native question, never two.** That
rule shapes `Joint` (section 6).

**Native distributions are read, never invented.** A choice or score with no
`chosen` takes the distribution's argmax, and a tie at the top is `abstained`
(`_argmax`, `decisions.py:859-863`; `_native_score`,
`decisions.py:939-943`). A provider's probability-weighted `score` field is
never the answer (`openrouter.py:145-150`, `openai_compatible.py:215-219`).
`regrouped` (`decisions.py:952-982`) sums a distribution's mass by meaning, for
a choice that spells one meaning several ways.

**The prompt branches on duck typing.** `templates/decide/user.j2:19` and `:24`
test `q.options is defined` and `q.levels is defined`. A new type with
`options` would render as a choice.

## 2. Goal (and what is explicitly not the goal)

Add three things to the decide vocabulary that the roadmap's consumers need,
without weakening any rule `decisions.py` keeps:

1. **`Rank`**: an order over given candidates, on the structured and native
   paths, with abstention (01e-C1).
2. **A finer Score**: a continuous `expected` level where a native endpoint
   reported a distribution, and a `tiers` helper that makes ties explicit
   instead of breaking them silently (01e-C2).
3. **`MultiSelect` and `Joint`**: any subset of options, and one action plus a
   target conditioned on it. Both work on the structured path, and both have
   a native lowering (01e-C3).

Not the goal:

- No new provider endpoint, and no change to which backend answers. The
  backend is still chosen by `resolve.native_only`.
- No synthetic probability. A structured answer to any new type carries no
  distribution, no marginals and no `expected`.
- No call-site conversion. 09, 11 and 13 adopt these types in their own specs,
  behind their own eval gates.

## 3. Shared design: one new answer field, value objects, and a native lowering

### 3.1 Answer values

`Answer.answer` becomes
`bool | str | int | Ranking | Pair | tuple[str, ...] | None`:

| Question | Answer value | Example |
|---|---|---|
| `Rank` | `Ranking` | `Ranking(tiers=(("scene:a",), ("scene:c", "scene:b")), rest=())` |
| `MultiSelect` | `tuple[str, ...]`, option ids in the **caller's option order**, possibly empty | `("characters:mara", "characters:winifred")` |
| `Joint` | `Pair` | `Pair(head="strike", tail="creatures:sentinel")` |

```python
@dataclass(frozen=True)
class Ranking:
    tiers: tuple[tuple[str, ...], ...]   # best first; members of a tier are tied
    rest: tuple[str, ...] = ()           # candidates the answer did not rank, input order

    def flat(self, tiebreak: Callable[[str], object] | None = None) -> tuple[str, ...]: ...
    def position(self, candidate: str) -> int | None: ...  # tier index; None for rest

@dataclass(frozen=True)
class Pair:
    head: str
    tail: str | None   # None for a head that takes no tail
```

**Why value objects rather than nested tuples.** A ranking with ties and a
flat order are different facts. A bare `tuple[tuple[str, ...], ...]` invites
a caller to flatten it by input order without noticing, and that is exactly
the silent tie-break 01e-C2 exists to prevent. `Ranking.flat` takes the
tie-break as an argument, so whoever flattens must choose one. With no
tie-break, `flat` raises `ValueError` on a tier of more than one.

**Why a multi-select answers in option order.** A set has no order the model
can be trusted to mean. Mechanics II fans a target effect out "in selection
order — the order of the accepted proposal's `targets` list"
(`2026-10-04-mechanics-ii-design.md:319`). That order must be deterministic
and replayable, so it is the caller's. A caller that needs an order the model
chooses asks a `Rank`.

### 3.2 Two new optional fields on `Answer` (01e-C4 and part of 01e-C2)

- `marginals: dict[str, float] | None`: per-key probabilities a native
  endpoint reported **independently**: each candidate's or option's P(true)
  under its lowered predicate (section 3.3). They do not sum to 1. Kept apart
  from `distribution` on purpose: `distribution` is categorical and 01c's
  sampler (01c-C2) samples it. A sampler that took marginals for a
  distribution would sample a meaningless value. 01c-C4 already forbids
  turning a missing distribution into a sampled answer, and a `Rank` or
  `MultiSelect` answer always has `distribution is None`.
- `expected: float | None`: a score's probability-weighted level (section 5).

Both follow the existing rule that a report rides on the answer, an answer of
`None` included (`decisions.py:871-875`). Both are `None` on every structured
answer. `__post_init__` checks that `marginals` values are probabilities, and
that `expected` is finite and is only set on an answer to a `Score`. The
second check lives in `native_answer`, since `Answer` does not know its
question.

### 3.3 The native lowering, in `decisions.py`

Neither endpoint has these types, so they are lowered to types the endpoints
do have. Both adapters share one lowering, as they already share
`native_choice_keys` and `native_result`:

```python
def native_form(item: Item) -> tuple[Item, Lift]: ...
def native_lift(item: Item, lowered: ItemResult, lift: Lift) -> ItemResult: ...
```

- `Predicate`, `Choice` and `Score` pass through unchanged, under their own
  ids.
- `Rank` with `pointwise` set becomes one `Predicate` per candidate (section
  4.3). Without `pointwise`, `native_gap` refuses it (section 4.3).
- `MultiSelect` becomes one `Predicate` per option (section 6.1).
- `Joint` becomes one flattened `Choice` (section 6.2).

A lowered predicate's id is `f"{q.id}#{index}"`. `native_gap` returns a
sentence when such an id collides with another question id in the item. This
is the same refusal unsent that `native_unrepresentable` already is. It is
not a new `validate` rule, because the collision is a native-only problem and
a structured stage can still answer the item.

Each adapter changes in two places. `decision_body` iterates
`decisions.native_form(item)[0].questions` instead of `item.questions`
(`openrouter.py:137-142`, `openai_compatible.py:160-165`). `decision_result` reads the
reply against the lowered item exactly as today, then returns
`native_lift(item, result, lift)`. `llm.native_body` (`llm.py:1124`) goes
through `decision_body`, so the prompt-log capture records the lowered body,
which is what was actually sent (`inference._native_request`,
`inference.py:543`). `native_gap` runs on the **original** item, so its
sentences name the caller's question ids. A lowered item of three plain types
has no gap of its own beyond the choice-size rule, which it checks on the
flattened `Choice`.

## 4. 01e-C1: `Rank`

### 4.1 Request

```python
@dataclass(frozen=True)
class Rank:
    id: str
    instructions: str                  # how to order, best first
    candidates: tuple[Option, ...]     # 2..MAX_RANK_CANDIDATES
    top: int | None = None             # rank at least this many; None = all
    allow_none: bool = False           # null = cannot order these (abstained)
    pointwise: str = ""                # native only: a yes/no asked of ONE candidate
```

`validate` adds these checks:

- the candidate count is in `2..MAX_RANK_CANDIDATES`;
- candidate ids and aliases pass `offerable`, and none collide once
  normalised (the same check as `_check_choice`);
- `1 <= top <= len(candidates)` when `top` is set.

`MAX_RANK_CANDIDATES = 32`. The bound is argued from structure alone. The
structured reply echoes each candidate id, so its length grows with the
count. The model has to produce a permutation, and a long one is where
duplicates and omissions appear. The native lowering puts one question per
candidate into a single request, and neither endpoint documents a
questions-per-request bound (01 Appendix B), so a 413 would be the only sign
of one. 09 reranks a pool it has already narrowed (09-C1), not a library. Tune
against real prompts later with 01a's reports, never against a measured
library.

### 4.2 Structured path

Schema (`_question_schema`): `{"type": "array", "items": {"type": "string",
"enum": [candidate ids]}}`, or `anyOf` with a null branch when `allow_none` is
set. No `minItems`, `maxItems` or `uniqueItems` is used. Anthropic's
structured outputs support array `minItems` of only 0 or 1, and refuse
"array constraints beyond `minItems` of 0 or 1". OpenAI documents
`minItems`/`maxItems` but not `uniqueItems` (both references checked
2026-10-09). The parser enforces count and uniqueness instead. The docstring
rule (`decisions.py:32-38`) gains `array` and `items`. The nesting note
(`decisions.py:126-133`) becomes five levels: batch, item, answers, array and
the item string. That is still inside strict mode's 10.

Prompt (`decide/user.j2`): every question class gets a `KIND` class constant
(`"predicate"`, `"choice"`, `"score"`, `"rank"`, `"select"`, `"joint"`), and
the template branches on `q.KIND` instead of `is defined`. A rank renders as
`- <id> (ranking, best first[, at least the top k][, or null if they cannot be
ordered]): <instructions>` followed by the candidates as `  - <id>:
<description>`. `decide/system.j2` gains one bullet per new kind ("a ranking
is answered with a list of candidate ids, best first, each at most once", and
the matching sentences for a selection and a joint choice). **Each bullet is
rendered only when the batch holds a question of that kind**, so the prompt of
every existing call site, which asks only predicates and choices, stays
byte-identical. `scripts/verify_templates.py` keeps its current
`_DECIDE_ITEMS` renders unchanged and gains a second set that includes the
new kinds. The `KIND` constants are `ClassVar[str]` (or unannotated class
attributes), so they are neither constructor nor equality fields of the
frozen dataclasses.

Parse (`_read_rank`):

- `null` gives `abstained` when `allow_none` is set, and `unreadable`
  otherwise.
- A value that is not a list is `unreadable`.
- Each entry is matched like a choice value: exact first, then `normalise`
  plus aliases.
- An entry that names no candidate is `unreadable` with detail
  `NOT_AN_OPTION`. As with a structured choice, no `stated` is set: the
  `Answer` docstring keeps `stated` for native replies, whose raw text is not
  at hand (`decisions.py:231-236`). The entry is not dropped:
  dropping it would shift every later candidate up a place. That is the
  misattribution `_foreign_index` refuses for items
  (`decisions.py:568-601`), and an answer left unread is better than one
  misread.
- An entry naming a candidate already listed is `unreadable`, with no detail.
  The reply gave a value, and which position was meant cannot be told.
- Fewer distinct candidates than `top` (or than all of them, when `top` is
  None) is `unreadable`, with no detail. The reply was read, and was
  incomplete.
- Otherwise the answer is `Ranking(tiers=((c,) for c in listed), rest=<the
  unlisted candidates in input order>)`. A structured ranking has only
  singleton tiers. `rest` is non-empty only when `top` let the model stop
  early.

No new `detail` value is added. `was_read` keeps its meaning: an incomplete or
duplicated ranking was read, and answered badly.

### 4.3 Native path: pointwise predicates

A native endpoint has no rank, and building one out of a tournament of
choices would take `n - 1` sequential requests. The lowering instead asks one
predicate per candidate in **one** request, which fits the endpoints' rule
that each question is answered alone (section 1):

```
instructions = f"{rank.pointwise}\n\nCandidate {opt.id}: {opt.description}"
```

`pointwise` is required for the native path. **There is no default
wording.** "Order these by relevance" does not turn into a per-candidate
yes/no question by any template that is right for every caller. Only the
caller knows that the per-candidate question is, for example, "Does this
scene hold something the current turn needs to stay consistent with?". A
`Rank` with an empty `pointwise` is refused unsent by `native_gap`
("Question <id> ranks its candidates and names no pointwise question; a
decisions endpoint cannot order them."). It then goes to the structured
fallback stage, or fails as `native_unrepresentable`, as any unrepresentable
item does today.

Lift (`native_lift`). It reads each lowered predicate's `Answer`, as
`native_answer` already produced it: its `answer`, `reason` and
`probability`. It never re-thresholds a raw probability, so a bool `chosen`
an adapter might pass one day (`native_answer`'s rule M11) cannot read two
ways.

- `marginals` = each candidate's reported P(true) (`Answer.probability`).
- If every lowered predicate is `refused`, the rank is `refused`.
- If any candidate has no usable probability (refused, unreadable or
  missing), the rank is `unreadable` with no detail. The probabilities that
  were reported still ride in `marginals`. A partial order is not an order
  the caller asked for, and the missing candidate's place cannot be guessed.
- Otherwise `answer = Ranking(tiers=tiers(marginals))` (section 5.2), with
  `rest=()`. The endpoint scored every candidate, so `top` is satisfied by
  construction.
- A P(true) of exactly 0.5 is a value here, not an abstention: it places the
  candidate, and only `tiers` decides whom it ties with. `allow_none` has no
  native trigger. Neither endpoint can say "cannot order these" in a
  predicate's answer, so a native rank never abstains, and that is stated
  rather than invented.

A native rank costs one metered request per item, as every native item does,
billed on input tokens. On OpenAI that means `n` copies of the pointwise text
plus `n` candidate descriptions (01 Appendix B: decisions bill input tokens
only). Its ledger row is a native decision row, so it is never modelled
(`usage.Rates.estimate`, CLAUDE.md "`modelled_usd` is never computed for a
native decision row").

## 5. 01e-C2: a finer Score, with ties explicit

`Score` keeps its 2 to 10 levels, its integer-`enum` schema and its
argmax-with-ties-abstain answer. Two additions make it usable for reranking.

### 5.1 `expected`, on native answers

`native_answer` sets `expected = Σ i·p_i / Σ p_i` over the validated
distribution (`_distribution`, `decisions.py:843-856`), keyed `str(i)`, and
`None` when there is no valid distribution or its mass is 0. Rules:

- It is computed from the reported distribution, not read from the provider's
  `score` field. Ruling 24 already keeps that field out of the answer
  (`openrouter.py:145-150`). Computing from the distribution keeps one rule
  for both providers. OpenAI defines its `score` as exactly this average, and
  OpenRouter's "probability-weighted" `score` is the same idea, but neither
  is needed when the distribution is present.
- It is normalised by the reported mass, because `_distribution` accepts a
  report that omits levels. An omitted level is unreported, not zero, so it
  contributes nothing to either sum.
- It rides on an `abstained` answer (a tie at the top) as the distribution
  does. A tie between levels 3 and 4 still yields 3.5, which is precisely the
  information a reranker needs and the argmax throws away.
- A structured score never has `expected`. The model said one level, and
  that is the answer.

### 5.2 `tiers`: one grouping rule for every ordered signal

```python
def tiers(values: Mapping[str, float | int], *, tolerance: float = MASS_TIE
          ) -> tuple[tuple[str, ...], ...]:
    """Keys grouped by value, highest first; keys whose values lie within
    `tolerance` of the group's first are tied, and a tier keeps the mapping's
    order. Never breaks a tie."""
```

The grouping is greedy from the top. A key joins the current tier when its
value lies within `tolerance` of that tier's **highest** value. Grouping on the
highest value, rather than chaining neighbours, means three values each just
inside tolerance of the next cannot drag a long run into one tier.
`MASS_TIE` (`decisions.py:949`) already exists for exactly this purpose:
float error in summed probabilities. A native rank's lift uses it (section
4.3). So does a caller that scores candidates one item each: a `Score` per
candidate (by `answer`, or by `expected` where present) or a `Predicate` per
candidate (by `probability`).

The tolerance applies equally to integer levels. Two candidates both
answered level 7 are one tier. So a structured per-candidate Score, which is
coarse, still yields an honest partial order and never a fabricated strict
one.

### 5.3 What "finer" means, per backend

| Decision model | Finest available signal | Ties |
|---|---|---|
| Native-only | `expected` per candidate, or `Rank` via P(true) | Only within `MASS_TIE`, so rare |
| Structured (generating) | `Rank`: a strict order | None within the ranked prefix. `rest` is one explicit bottom group |
| Structured, per-candidate `Score` | The level (2 to 10) | Frequent, and explicit through `tiers` |

`MAX_LEVELS` stays 10. Widening it for structured-only scores (with
`native_gap` refusing above 10) is Open question 2.

## 6. 01e-C3: `MultiSelect` and `Joint`

### 6.1 `MultiSelect` (01e-C3a)

```python
@dataclass(frozen=True)
class MultiSelect:
    id: str
    instructions: str
    options: tuple[Option, ...]      # 1..MAX_SELECT_OPTIONS
    min: int = 0
    max: int | None = None           # None = up to len(options)
    allow_none: bool = False         # null = cannot say (abstained); () is a real answer
```

`validate` adds these checks: option ids and aliases as for a choice;
`0 <= min <= hi <= n` with `hi = n if max is None else max` (so `max=0` is
zero, not "all"); and an option count in
`1..MAX_SELECT_OPTIONS`. `MAX_SELECT_OPTIONS = 32`, argued as for
`MAX_RANK_CANDIDATES`, since a native multi-select is one predicate per option
in one request. Tune it later.

**The empty selection is an answer, and it is not abstention.** `()` means
"none of these". `None` with reason `abstained` means "cannot say", and only
`allow_none` permits it. A consumer must never read one as the other.
Mechanics treats "no target" and "the model could not choose" differently.

Structured: the same array schema as `Rank`. Parsing rejects an unknown entry
(`NOT_AN_OPTION`), a duplicate entry (`unreadable`) and a count outside
`[min, max]` (`unreadable`), for the reasons given in section 4.2. Otherwise
the answer is the named ids in **option order**. The prompt says "answered
with a list of option ids, each at most once; an empty list means none
apply", plus the bounds when they are set.

Native: one `Predicate` per option, worded with the fixed template
`NATIVE_SELECT_TEXT = "{instructions}\n\nOption {id}: {description}\n\nIs this
option one of those selected?"`. Unlike `Rank`, a fixed template is honest
here, because "is this option selected" is exactly the per-option question a
multi-select asks. Lift:

- `marginals` = P(true) per option.
- Every option refused gives `refused`. Any option with no usable probability
  gives `unreadable`.
- Any option at exactly 0.5 means the selection cannot be stated without a
  stand-in for that option. With `allow_none` it is `abstained`, as a native
  predicate at 0.5 is (`decisions.py:913-914`). Without `allow_none` it is
  `unreadable` with no detail, and the marginals still ride. This is the rule
  `_native_choice` keeps for a none without `allow_none`
  (`decisions.py:928-936`), so a caller that declared the question not
  abstainable never sees `abstained`.
- Otherwise the selection is every option with P(true) > 0.5, in option
  order. A selection outside `[min, max]` is `unreadable`, and the marginals
  still ride. **It is never repaired** by taking the top `max` or padding to
  `min`. A caller that wants top-k-by-probability reads `marginals` and says
  so in its own code.

### 6.2 `Joint` (01e-C3b): an action and a target conditioned on it

```python
@dataclass(frozen=True)
class Joint:
    id: str
    instructions: str
    heads: tuple[Option, ...]                              # the actions
    tails: tuple[tuple[str, tuple[Option, ...]], ...]      # head id -> its legal targets
    allow_none: bool = False
```

A head missing from `tails`, or mapped to an empty tuple, takes no tail.
**The conditioning is the legal-pair set itself.** The caller lists only
pairs that are legal. 13's deterministic legality check (13-C1, "engine
validates actor/targets/costs") decides which pairs are legal, and the model
never sees an illegal pair.

**One flattened choice, on both paths.** A `Joint` is a `Choice` whose
options are the legal pairs:

- The option id is `head` for a tailless head, and `f"{head}{JOINT_SEP}{tail}"`
  otherwise, with `JOINT_SEP = "=>"`.
- The description is `f"{head.description} -> {tail.description}"`.
- `validate` refuses these cases:
  - a head or tail id containing `JOINT_SEP`;
  - aliases on a `Joint`'s options, since pairing aliases would multiply them;
  - a `tails` key that names no head, or a head listed twice in `tails`;
  - a flattened option count outside the choice bounds, 2 to 255, or 1 to 255
    with `allow_none`.

  Everything else reuses `_check_choice` on the flattened choice.

The flattened `Choice` gives the following:

- **Native**: the existing `choice` question, which also gives the joint
  **distribution** 01c samples (13-C3: "a distribution over legal Actions").
  A two-question shape (`action` and then `target`) is ruled out by the
  identity lesson in section 1: a native endpoint answers the second question
  without seeing the first answer.
- **Structured**: a string `enum`. The alternative is nested
  `anyOf`-of-objects with one branch per head. That needs single-value enums
  standing in for `const`, multiplies schema properties, and buys nothing:
  the flat enum already admits exactly the legal pairs.

Parse and lift both reduce to the choice reading. `_read_choice` on the
flattened choice, or `native_answer` on the lowered one, gives an option id,
which is split back into `Pair`. **The flattened key is the one spelling of a
pair everywhere a key is needed**: the distribution, 01c's draw and replay
record, 01d's answer filter, the capture and the eval render. `Pair` carries
it, so a pair and its key cannot disagree:

```python
def joint_key(head: str, tail: str | None) -> str: ...      # head, or head + JOINT_SEP + tail
def split_joint(key: str) -> Pair: ...                      # the inverse; ValueError on no head

@dataclass(frozen=True)
class Pair:
    head: str
    tail: str | None
    @property
    def key(self) -> str: return joint_key(self.head, self.tail)

def head_marginal(answer: Answer, q: Joint) -> dict[str, float] | None:
    """The distribution summed per head, NONE_KEY excluded; None without one."""
def head_first(answer: Answer, q: Joint) -> str | None:
    """`regrouped`'s rule applied to `answer.answer.key`, grouped by head,
    NONE_KEY excluded: the head the distribution puts most mass on when that is
    not the chosen pair's head, else None."""
```

`regrouped` itself returns None for any answer that is not a `str`
(`decisions.py:968-970`), so `head_first` does not call it on the `Pair`. It
applies the same summing and tie rule to the pair's key. For 01d's answer
filter (01d section 5.1), a `Joint` answer is spelled by its key. The filter
does not apply to a `Rank` or a `MultiSelect`: neither has one answer key, and
neither carries a distribution.

`head_first` exists for the reason `regrouped` exists (`decisions.py:952-968`).
A native endpoint scoring pairs one by one splits a head's mass across its
targets. With 0.3 on each of two sentinels and 0.4 on one heal, the argmax
pair is a heal, while the action most mass favours is a strike. Which reading
a caller trusts is its own policy. 13 records it beside 01c's replay record.

**Size.** The number of legal pairs is bounded by the choice limit of 255,
counting the reserved none natively. A caller with more legal pairs
decomposes the decision into a `Choice` of action with its distribution, then
a `Choice` or `MultiSelect` of that action's targets in a second `decide`. Any
target-level answer must then be read as conditioned on the first answer by
the caller. **Multi-target Actions** (`targets.max > 1`,
`mechanics-ii-design.md:315-324`) follow the same two-step shape: the action
first, then a `MultiSelect` over its legal targets with `max` set to the
action's `targets.max`. A single "joint multi-select" type is a non-goal
(Open question 3).

## 7. Chunking and cost

| Type | Enum values added (`enum_values`) | Structured reply grows by | Native request carries |
|---|---|---|---|
| `Rank`, n candidates | n | Up to n ids | n predicates (`pointwise` + description each) |
| `MultiSelect`, m options | m | Up to m ids | m predicates |
| `Joint`, P legal pairs | P | One id | One choice of P (+none) options |
| `Score` with `expected` | Unchanged | Unchanged | Unchanged |

`enum_values` (`decisions.py:388-394`) counts only `Choice` and `Score`
today, so it gains a branch per new type (a test pins each). `_tally` already walks
nested `items.enum`, so `schema_chars` and `schema_properties` need no
change. `chunks` therefore splits a batch of large ranks or joints by the
existing enum and string budgets, with no new rule. At the bounds above, 8
items of one 32-candidate rank add 256 enum values, well inside 1000. A
255-pair joint takes a quarter of the budget, so a chunk holds three such
items (765 values; a fourth would pass 1000), and `chunks` already closes the
chunk there. 01a-C1 is how the real per-item token and
latency cost is read before anyone tunes a bound.

## 8. Capture, rendering and evals

- `decisions.outcome` (`decisions.py:991-1011`) serialises the new values as
  follows:
  - a `Ranking` becomes `{"tiers": [[...], ...], "rest": [...]}`;
  - a `Pair` becomes its flattened key (`Pair.key`), never a bare
    two-element list, which a capture could not tell from a selection;
  - a selection becomes a list;
  - `marginals` and `expected` are added beside `probability` and
    `distribution`, under `_present`'s rule that an empty value is left out.

  01b-C1 captures whatever `outcome` returns.
- `decisions.render` (`decisions.py:1014-1027`, used by
  `evals/run.py --live` to hand a native result to structured graders):
  - a singleton-tier `Ranking` renders as its id list, and one with a tie
    renders as `null`;
  - a `Pair` renders as its flattened key;
  - a selection renders as its list.

  Graders already read the native result itself (`evals/graders.py:348-359`),
  so the null is never scored as a model's answer.
- `evals/cases.py` gains one offline case per new type, on synthetic material
  with placeholder names only:
  - a `Rank` of five Saltmarch scenes for a turn about Mara;
  - a `MultiSelect` of the characters present who witnessed an event;
  - a `Joint` over three actions and their legal targets.

  Each case:
  - renders the real templates and requires each question's line, and the
    schema, verbatim in the prompt;
  - grades hand-written replies through `decisions.parse`: a valid one, a
    duplicate, an unknown id, an under-length one and a null.

  Each case also gets a `--live` run under both `--decide-backend native` and
  `structured`. That comparison is 01a-C3's table once 01a lands.
- There is no `gate.Conversion`. The decide gate (`evals/README.md`, "The
  decide gate") compares a legacy parser with its decide twin per converted
  call site. These types convert no call site, and each consumer that adopts
  one owns its own gate.

## 9. Contract

**01e-C1 `Rank`.** Inputs: `Rank(id, instructions, candidates, top, allow_none,
pointwise)`, validated as in section 4.1. Output: `Answer.answer` is a
`Ranking`.

- **Structured:** singleton tiers, best first, with `rest` holding the
  candidates left unranked under `top`.
- **Native:** tiers by P(true) under `pointwise`, with `marginals` set.

Guarantees:

- No tie is ever broken silently.
- An answer with a duplicate, an unknown id or too few candidates is
  `unreadable`, never repaired.
- `null` is `abstained` only with `allow_none`, and only on the structured
  path.
- A native rank with any candidate unanswered is `unreadable`, and a fully
  refused one is `refused`.
- A rank with no `pointwise` is refused unsent on a native stage
  (`native_unrepresentable`) and moves on down the chain.

**01e-C2 finer Score.** `Answer.expected` is set on a native `Score` answer
whose distribution is valid: the mass-normalised probability-weighted level,
riding on an abstained answer too. It is `None` on every structured answer.
`decisions.tiers(values, tolerance=MASS_TIE)` groups any mapping of scores or
probabilities into tied tiers, highest first, and never breaks a tie.
`Ranking.flat(tiebreak)` is the only flattening, and it raises without a
tie-break when a tier holds more than one.

**01e-C3a `MultiSelect`.** `Answer.answer` is a tuple of option ids in option
order. `()` is a real answer, and `None` + `abstained` is "cannot say" (with
`allow_none`).

- **Structured:** an array of ids, with count and uniqueness enforced by the
  parser.
- **Native:** one predicate per option, thresholded at 0.5, with `marginals`
  set. An option at exactly 0.5 is `abstained` with `allow_none` and
  `unreadable` without it. A count outside bounds is `unreadable`, never
  repaired.

**01e-C3b `Joint`.** `Answer.answer` is a `Pair(head, tail)`. It is one
flattened `Choice` on both paths, so a native answer carries a joint
`distribution` over legal pairs that 01c can sample. `Pair.key`
(`joint_key`) and `split_joint` are the one spelling of a pair in every
distribution, filter, replay record, capture and render. `head_marginal` and
`head_first` give the head-level reading, with `NONE_KEY` excluded. The number
of legal pairs is bounded by the choice limit (255, or 254 natively with the
reserved none). Joint does not cover a set of targets, and it does not cover a
legal set past the cap: both are the two-step decomposition of section 6.2.

**01e-C4 `Answer.marginals`.** Per-key probabilities a native endpoint
reported independently (a lowered `Rank` or `MultiSelect`). It is never a
`distribution`, never sums to 1, and is never sampled: 01c's sampler refuses
it. Every structured answer has `marginals is None`.

**Shared.** Every refusal of a request is a `DecideRequestError` raised before
anything is sent. Every native-only incapacity is a `native_gap` sentence,
refused unsent.

## 10. Interaction with repo rules

- **Gateway leaf.** `decisions.py` still imports nothing from the package
  (`decisions.py:18-21`). The value objects, the lowering and `tiers` all live
  there. The adapters gain only the two-line change in section 3.3.
- **Metering and the chain.** These are unchanged: one meter per structured
  chunk and one per native item (CLAUDE.md, "A decide call is answered down a
  chain"). A `native_gap` refusal moves the item on like any failed call, and
  "an answer is never a reason to move on" still holds. A `refused` or
  `abstained` rank is an answer.
- **Capture.** No new capture site. Section 8 extends only the record that
  01b-C1 and the speaker capture already use.
- **`test_operation_guard.py`** is unaffected, since no task and no route
  changes. `scripts/verify_templates.py` and `test_llm_fakes.py` re-render
  `decide/user.j2` and `decide/system.j2`. Any cassette keyed on the decide
  system prompt's wording must be updated in the same change (CLAUDE.md,
  "Faking the LLM").
- **Android and pydantic.** `decisions.py` uses dataclasses, not pydantic, and
  adds no dependency.
- **Privacy.** Eval fixtures use invented names only, and no constant is
  justified by a measured library (sections 4.1, 6.1).

## 11. Tests and acceptance

`backend/tests/test_decisions.py`:

- **`validate`**: each new bound refused (candidate and option counts, `top`,
  `min`/`max`, `JOINT_SEP` in an id, aliases on a joint, a `tails` key naming
  no head, too many flattened pairs).
- **`schema`**: rank and select render as an array of an enum, nullable as
  `anyOf`; a joint renders as a flat enum. `schema_chars` and `enum_values`
  count the new enums.
- **`parse`**, per type:
  - exact, aliased and normalised entries;
  - an unknown entry gives `NOT_AN_OPTION` with `stated`;
  - a duplicate gives `unreadable` with no detail, and `was_read` is true;
  - too short a reply gives `unreadable`;
  - null with and without `allow_none`;
  - the empty selection is `()`, not None;
  - a joint reply splits into a `Pair`.
- **`tiers`**: exact ties, ties within `MASS_TIE`, the greedy anchor rule
  (three values each just inside tolerance of the next do not collapse into
  one tier), and integer levels.
- **`Ranking.flat`** raises on a tie without a tie-break.
- **`expected`**: computed over the reported mass; set on an abstained
  (tied) answer; absent on a structured answer and without a distribution;
  never taken from a provider `score` field.
- **`chunks`** splits on the enum budget with large joints.

`backend/tests/test_native_decisions.py`, for both providers:

- `decision_body` carries the lowered predicates for a rank (with
  `pointwise`) and a select, and the flattened choice for a joint.
- Lifting canned bodies: a rank with a candidate missing gives `unreadable`
  with `marginals`; all refused (OpenAI) gives `refused`; equal P(true) gives
  one tier; 0.5 on a select gives `abstained`; a select outside bounds gives
  `unreadable` and is not repaired; a joint's distribution is kept by
  flattened key and `head_first` regroups it.
- `native_gap` names a rank with no `pointwise`, and a lowered-id collision.

`backend/tests/test_inference_decide_native.py`:

- A rank with no `pointwise` on a native primary is refused unsent and is
  answered by a structured fallback stage.
- With no fallback, the `Decision.errors` entry is `native_unrepresentable`.

Evals: the three offline cases in section 8 pass under `pytest backend`.

**Acceptance.** Every test above is green, and `make check` is green with the
ratchet baselines updated if any finding moved. No existing decide call
site's prompt changes: the new system-prompt bullets render only beside a
question of their kind, and `verify_templates.py`'s existing decide renders
are byte-identical. The decide gate and `test_decide_chain_golden.py` are unchanged.

## 12. Non-goals

- A native rank, select or joint wire type. None exists, and this spec
  lowers to the three that do.
- A conditional multi-select in one question (an action plus a *set* of its
  targets). This is the two-step decomposition of section 6.2.
- Probabilities on a structured answer, a synthetic rationale on a native
  one, or any use of a provider's weighted `score` as an answer.
- Escalation on a low margin or a tie (01d), sampling (01c) and capture call
  sites (01b).
- Converting any existing call site.

## 13. Open questions

1. **Should `Rank` be allowed natively with no `pointwise`, using a default
   wording?** Recommendation: **no**. A default would be a sentence the
   caller never wrote, answered per candidate, and silently not what was
   asked. Refusing unsent sends the item to the structured stage, which can
   answer it.
2. **Widen `MAX_LEVELS` above 10 for structured-only scores, with
   `native_gap` refusing above 10?** Recommendation: **not in 01e**. Whether
   a generating model gives a finer judgement on a 21-step scale than on a
   10-step one is an eval question 01a can answer. `Rank` already gives a
   structured model a strict order.
3. **A joint multi-select type (action plus up to k targets in one
   question)?** Recommendation: **no**. Natively it would need a choice over
   every legal subset, which grows combinatorially and hits 255 at once. The
   two-step shape in section 6.2 is exact, and 13 can measure its cost with
   01a.
4. **`MAX_RANK_CANDIDATES` and `MAX_SELECT_OPTIONS` at 32.** Recommendation:
   ship 32, which is structural (section 4.1), and tune it with 01a-C1
   against 09's real candidate pools. If 09 needs more, it reranks in two
   rounds rather than raising the bound blind.
5. **01c and marginals.** 01c-C2's sampler must refuse `marginals` (01e-C4)
   and any answer whose `distribution` is None (01c-C4). Recommendation: 01c
   cites 01e-C4 and refuses any answer to a `Rank` or `MultiSelect`.

# 01c-S1: The sampler, the record and replay — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new gateway leaf, `backend/src/grimoire/draws.py`, is the one
sampler a decision's distribution is ever drawn through. It provides
`ALGORITHM`, `SEED_BITS`, `QUANTUM`, `MASS_SLACK`, `Eligibility`, `Draw`,
`ReplayError`, `new_seed`, `unit`, `quanta`, `pick`, `draw_from`, `draw` and
`replay`. It implements the six steps of spec §5.3, the no-draw table of §5.4
and the version-1 record of §6. It stores nothing, takes no lock, wires no
caller, and no task samples. Delivers 01c-C2 (full), 01c-C3 (full: the shape,
`replay`, and the persistence rules stated for callers) and 01c-C4 (full).

**Architecture:** the draw is pure integer arithmetic over a SHA-256
uniform. `unit(seed, purpose)` is the top 53 bits of one SHA-256 digest of
ASCII bytes. No float is ever hashed or formatted, so float printing cannot
differ between platforms. Each reported weight becomes `quanta(w) =
math.floor(w * 2**32)`, which is exact in binary64 on every platform because
`2**32` is a power of two. From there, totals and cumulative bounds are Python
`int`s (a browser uses `BigInt`), and `pick` selects with `(r53 * total) >>
53`. One private function, `_weigh`, runs steps 2 to 5 (masks, quanta, cutoff,
floor). `draw_from`, `draw` and `replay` all call it, so the draw and its
replay cannot drift apart. `draw` checks the §5.4 table in order and then
builds the record. The two things `draws` needs from 01d-S2 are each item's
server (`ItemResult.served`, `(kind, provider_id, model)`) and the record's
spelling of an answer (`decisions.answer_key`). `draws` imports only
`decisions` (bound as a module) and the standard library.

**Tech Stack:** Python 3.11 floor (`hashlib`, `secrets`, `math`, `re`,
`dataclasses`), pytest. The goldens below were computed with the stdlib on
CPython 3.11.17, 3.12 and 3.13.16 on this machine, and all three agree.

**Spec refs:** `docs/superpowers/specs/2026-10-09-roadmap-01c-decision-distributions-sampling-design.md`
§1 (current state), §5 (C2: §5.1 functions, §5.2 RNG and integers, §5.3 the
six steps, §5.4 the no-draw table), §6 (C3: the record, §6.1 persistence,
§6.2 replay), §7 (C4), §8 (contract), §9 (repo rules), §10 (`test_draws.py`),
§11 (non-goals), §12 (open questions), Slices (01c-S1).

**Needs:** 01d-S2 (S) for `ItemResult.served` and `decisions.answer_key`
(commit `a9ab1b4` on `wt/01d-s2`, not yet integrated; see Task 0). 01e-S1
(S) for `Answer.marginals`, which is already on this tip
(`decisions.Answer.marginals`, `backend/src/grimoire/decisions.py:468`).

**Open questions adopted (§12):**
- Q1: `MASS_SLACK = 0.02`, a literal with the §5.4 comment. It is tuned
  later against 01a's per-kind mass figures, never against a measured
  library.
- Q5 (decided): reshaping is `Eligibility` alone. 02's `1/(2n)` is a
  `cutoff`, its none exclusion is `exclude=(NONE_KEY,)`, and its
  addressed-actor narrowing is `only=`. `floor` is for a caller that wants to
  raise low keys. 02 does not use it.
- Q4 (the evidence lives in `evals/README.md`, "Decision distributions"):
  adopted, but nothing in S1 writes it. It is S3's.
- Q2 and Q3 are S2's (`native_capable`, the settings phrase). S1 adds no
  `TaskPolicy` field (`samples` and `native_first` are 01c-S2's, which is
  being built in parallel).

## Current code this plan stands on (re-read at `706ffa3`)

- `decisions.Answer` (`decisions.py:435-487`): `answer`, `reason` (one of
  `REASONS = ("unreadable", "refused", "abstained", "error")`, set exactly
  when `answer is None`), `probability`, `distribution`, `detail`, `stated`,
  `marginals` (independent per-key P(true) values, never a distribution) and
  `expected`.
- `decisions.ItemResult` (`:490-525`): `answers` keyed by question id,
  `rationale`, `backend`. 01d-S2 adds
  `served: tuple[()] | tuple[str, str, str] = field(default=(), compare=False)`.
- `decisions.NONE_KEY = "<none>"` (`:102`). A native choice reports it under
  `allow_none` (`_legal_keys`, `:1447-1452`). An explicit `NONE_KEY`, or an
  argmax on it, is `abstained` (`_native_choice`, `_from_distribution`,
  `:1549-1567`).
- Native validation drops an invalid report whole (`_probability`,
  `_distribution`, `:1424-1468`). A report need not sum to 1 or cover every
  option. A predicate's report is `probability` (P(true)), and exactly 0.5 is
  `abstained` (`_native_predicate`, `:1539-1546`). An explicit `chosen` is
  kept "whatever the probability beside it says" (`native_answer`,
  `:1478-1517`), which is how an inconsistent answer arises.
- Question types (`:219-379`): `Predicate`, `Choice` (`options`,
  `allow_none`), `Score` (`levels`), `Rank` (answer a `Ranking`),
  `MultiSelect` (answer a tuple of ids), and `Joint`, asked as its flattened
  `joint_choice(q)`. A joint's answer is a `Pair`, and its native
  distribution stays keyed by the flattened keys (`_lift_joint`,
  `:1062-1069`). `split_joint` (`:318`) turns a key back into a `Pair`.
- `decisions.answer_key` (01d-S2): `"true"`/`"false"` for a bool, `str(i)`
  for a score level, the option id for a choice, `Pair.key` for a joint, and
  None for an answer of None, a `Ranking` or a selection. Its docstring says
  a record that spells an abstention as `NONE_KEY` "does so itself".
- House seed precedent: `store/dice.py:115-121` mints
  `secrets.randbits(53)`. The test seam precedent is
  `routes/character_turns.py:49`
  (`_rng: Callable[[], random.Random] = random.Random`).
- **Where a pick's outcome is stored today (for §6.1, not wired here).** The
  speaker pick's outcome is written by `_first_actor`
  (`routes/character_turns.py:873-885`) through `_round_state`
  (`:706-715`). That calls `store.responses.update_round(cid, sid,
  round_id, **fields)` (`store/responses.py:366-379`), which holds
  `locks.campaign_lock(cid)`, merges `fields` into the round record with
  `record.update(fields)`, and writes the whole ledger through
  `atomic.write_text` (`_write`, `:48-61`) as compact `json.dumps`.
  `store.responses` is already in `locks.DOMAIN_MODULES`
  (`store/locks.py:228`). So 02's `pick=draw.record` rides an existing
  atomic write under the existing lock, and **no new store module, writer,
  lock-domain entry or guard marker is needed for C3**. `new_round`
  (`:289-346`) writes no `pick`, so readers must use `.get("pick")`.
  `_cloned_round` (`:846-851`) deep-copies a round onto a branch, so a
  branch keeps its round's record and does not re-draw. 13's transaction
  ledger and action proposal do not exist yet. They follow the same rules in
  13's slices.
- `ruff.toml`: `C90` with `max-complexity = 10`, and `B` (so `B008`: no call
  in a default argument), `E7` (so `E731`: no assigned lambda). mypy runs at
  `python_version = 3.11` over `backend/src/grimoire`.
- The leaf tests use `tests.test_llm._sibling_imports(name)`
  (`tests/test_llm.py:1099`), as `test_decisions_is_a_leaf`
  (`tests/test_decisions.py:664-669`) does.
- CI runs the backend suite on `["3.11", "3.14"]`
  (`.github/workflows/ci.yml:44`). Android packages `backend/src` verbatim
  (`android/app/build.gradle.kts:98`) on Chaquopy 3.12 (`:64`).

## Global Constraints

- **Nothing in production imports `draws`** at landing, and no task
  samples. Every existing test, golden and snapshot passes untouched
  (`test_decide_chain_golden.py`, the frozen campaign's `snapshot.json`), and
  none is regenerated.
- **`draws` is a gateway leaf.** It is `backend/src/grimoire/draws.py` and
  imports exactly `from . import decisions` (a module binding, never a
  name) plus `hashlib`, `secrets`, `math`, `re`, `dataclasses`,
  `collections.abc` and `typing`, all at module scope. It reads no store, no
  clock, no environment and no file. So no entry in `store/locks.py` is
  needed, and no atomic, paths or lock guard is touched.
- **No float enters the hash, and no float is summed.** `unit` hashes
  `b"grimoire/draw/1\0" + str(seed).encode("ascii") + b"\0" +
  purpose.encode("ascii")`, where `seed` is an `int` and `purpose` is
  checked ASCII. Every comparison after `quanta` is between `int`s.
  `builtins.sum` is only ever applied to `int`s. The mass and floor checks
  are integer comparisons (`mass_q < MASS_FLOOR_Q`, `F * n > QUANTUM`),
  never `floor * n > 1` in floats. The module docstring spells out the
  browser recipe (gate finding 9): `r53 * total` reaches 2**93, so it is a
  `BigInt` product, and JS's `>>` is int32, so the shift is `BigInt`'s `>>
  53n`. `unit` is `new DataView(digest).getBigUint64(0) >> 11n`.
  `Math.floor(w * 2**32)` is exact, as it is here. `String(seed)` equals
  `str(seed)` for every integer below 2**53. SubtleCrypto's `digest` is
  async, so a browser draw is a promise.
- **The goldens are literals and are never regenerated to make a change
  pass.** They are what a browser-side check will reproduce. A change to the
  rule is a new `ALGORITHM` name, with new goldens beside it.
- **`draw` never raises on a valid `ItemResult` with a valid eligibility
  and seed** (C2). It raises `ValueError` only for caller misuse: a seed
  outside `[0, 2**53)` or a `bool`; a purpose that is not printable ASCII of
  at most 200 characters; an `only` or `exclude` key that is not offered; a
  `result` with no answer to `q.id`; and a floor too large for the
  **eligible** keys (`quanta(floor) * len(eligible) > QUANTUM`). All of
  these are checked at step 1, before the answer is read, so `draw` never
  raises on a report, mid-turn or otherwise (coordinator decision 2). The
  kept keys are a subset of the eligible ones, so a floor that passes there
  passes §5.3 step 5's count over the kept keys too. A report that a
  backend would never produce (a hand-built `Answer` with an illegal key or
  a weight outside [0, 1]) is read as **no usable report** (`why:
  no_report`), mirroring `_distribution`'s "dropped whole, never repaired".
  It is never a raise.
- **The record holds no prose** (§6): keys, floats, ints, bools, null and
  the server's names. `draw` never reads `result.rationale`, an option's
  `description` or `Answer.stated`. The record is never written to
  `logs/`, and `draws` has no logger.
- **Pydantic, Android:** no model class and no dependency.
  `check-pydantic1` runs `test_draws.py` like any other test.
- Ratchets (`check-lint`, `check-mypy`, eslint via `scripts/ratchet.py
  eslint`) stay at baseline. No baseline file changes. Never run `npm ci` or
  `make baseline` in this worktree.
- Privacy: fixtures use the placeholder names (`characters:mara`,
  `characters:seraphine`, `characters:winifred`, `grimoire`, Realm,
  Saltmarch, `realm-openai`, `decision-model-x`). No store is read and no
  count of anything real appears.

## Decisions taken in this plan (where the spec is silent)

Each of these is a reading the plan gate should check.

1. **Rank and MultiSelect** (§5.4 row 2 and C4). They are accepted and never
   drawn. If the answer is None, the result is `basis: none` with the
   answer's reason. Otherwise it is `basis: answer`, `why: no_report`,
   `distribution: null`, `mass_q: null`, `seed: null`. `offered` is the
   candidate ids (Rank) or option ids (MultiSelect) in question order. These
   questions have no reserved none. No single key names such an answer
   (`answer_key` returns None), so the record's `answer` and `selected` are
   null, `Draw.key` is None, and `Draw.value` is the `Ranking` or tuple as
   given. Under any narrowing (`only is not None` or a non-empty `exclude`),
   such an answer is `basis: none`, `why: ineligible`. A plain answer cannot
   be shown to sit inside a narrowed set, so it is not returned as a
   selection (§5.4's rule for an answer outside the eligible set).
   `marginals` are never read.
2. **The record's `answer` is `decisions.answer_key(answer)`, unchanged**
   (gate finding 3). An abstention therefore records `answer: null`, and
   `why: abstained` says what happened. A tie such as `{mara 0.45,
   seraphine 0.45, <none> 0.1}` is abstained, but no none was chosen, so
   spelling it `NONE_KEY` would misreport it. This plan does not tell an
   explicit none apart from a tie either: the `Answer` cannot tell them
   apart, and `why` is the same.
3. **Which answer rows apply the cutoff to the answered key** (coordinator
   decision 1, a deliberate reading of §5.4). §5.4 says "where a usable
   report exists". This plan reads that as **the `ineligible_mass` row**,
   which is the only answer row the draw's steps 2-5 have run for. It is
   not `partial` or `inconsistent`. In those two rows only `only` and
   `exclude` decide eligibility. Otherwise 02's always-on `1/(2n)` cutoff
   would turn every inconsistent answer (weight 0, always under the
   cutoff) into `none`/`ineligible`. That would erase its `why` and make the
   row unreachable for 02. The same would hold for every partial answer
   under the cutoff. The answers stand as `answer` with their own `why`, and
   the caller sees exactly what the report did wrong.
4. **The `ineligible_mass` row always resolves to `none`/`ineligible`.** By
   the time that row is reached, row 4 has ruled out a zero weight on the
   answered key. So if that key passed the masks and the cutoff, the total
   would be positive. The row therefore needs no branch of its own. It goes
   through the same answer-row helper, whose eligibility test (masks plus
   cutoff, here) turns it into `none`/`ineligible`. The test asserts that
   outcome, and no unreachable code is written.
5. **Null fields.** `seed` is the given seed only for `basis: sampled`.
   Every other row records `seed: null`, the cutoff row included ("no seed
   used"). The seed and purpose are still validated on every call, so
   misuse fails the same way whatever the answer was. `mass_q` is an int
   whenever a usable report was read (every row after `no_report`) and null
   otherwise. `drawn_q` is the step-6 total where one was computed
   (`sampled`, and `ineligible_mass`, which is 0) and null otherwise.
   `distribution` is the usable report's pairs in canonical order, keys the
   report omitted left out, or null.
6. **A predicate's `distribution`** is `[["true", p], ["false", 1.0 - p]]`.
   `1 - p` is one IEEE subtraction, which is exact the same way on every
   platform, and recording it lets `replay` stay type-agnostic. Its
   `offered` is `["true", "false"]`.
7. **An unstamped server** (`served == ()`) records `kind`, `provider` and
   `model` as `""`, as `ItemResult.backend` spells its own unstamped value.
   `draw` takes no `served=` parameter. That parameter was the spec's
   stopgap until 01d lands, and Task 0 lands 01d-S2 first.
8. **`Eligibility` validates its own shape and range at construction** (a
   `ValueError` from `__post_init__`). `only` must be None or a tuple of
   `str`, and `exclude` a tuple of `str`. `cutoff` and `floor` must each be
   an `int` or a `float`, not a `bool`, finite and in `[0, 1]`. They are
   coerced to `float` (`object.__setattr__`), so a `Fraction` or `Decimal`
   is refused and the record's `json.dumps` cannot break (gate finding 11).
   The docstring states the sizing rule: `floor` may be at most
   `1 / len(eligible)`. `draw` validates the question-dependent parts at
   step 1: masks against `offered`, and the floor against the eligible
   count (decision 11). The default instance is the module constant
   `UNNARROWED = Eligibility()`, used as `draw`'s and `draw_from`'s default
   (ruff `B008`).
9. **`_seed_source` is a zero-argument callable** (`_os_seed`, which
   returns `secrets.randbits(SEED_BITS)`), patched like
   `character_turns._rng`. `new_seed()` checks what it returns, so a patched
   source cannot mint an out-of-range seed.
10. **`replay` checks only the record's own fields** (§6.2). It returns the
    key it recomputed, and does not compare it with `selected`, so a caller
    can detect a record that was edited or drawn under another rule. It
    does not re-run the §5.4 preconditions, which are about the answer, not
    the record.
11. **The floor is validated against the eligible keys, up front, in
    integers** (coordinator decision 2, which is how C2's "never raises on a
    valid `ItemResult`" is reconciled with §5.3 step 5). With `F =
    quanta(floor)`, `F * len(eligible) > QUANTUM` is caller misuse. It is
    raised at step 1 by `draw` and `draw_from`, and by `_weigh` right after
    step 2 (so `replay` checks the same thing), before any reported weight
    is read. §5.3 counts the kept keys, which are a subset of the eligible
    ones, so any floor accepted here also satisfies §5.3, and no report can
    make it fail later. This is stricter than §5.3 only for a floor that
    fits the kept keys but not the eligible set, and that floor's validity
    would have depended on the report. The integer check passes a floor too
    large by less than `len(eligible) / 2**32`, the same everywhere.

## Review Focus

- The order of the §5.4 checks, the cutoff row before the
  `ineligible_mass` row, and the answer-row eligibility (masks always; the
  cutoff only at the `ineligible_mass` row, never at `partial` or
  `inconsistent`; the key must be in `offered`).
- Every `ValueError` is raised at step 1, before the answer is read. No
  report can make `draw` raise.
- Canonical order everywhere: `offered` comes from the question and never
  from the report's key order, so the steps iterate `offered` and look
  weights up by key.
- `pick` on half-open integer intervals: `t = (r53 * total) >> 53`, and the
  first positive key with `t < c + q` is selected. Zero weights are skipped,
  and `r53 = 2**53 - 1` lands on the last positive key.
- The cutoff compares before the floor (a floor never lifts a cut key), and
  the floor count is taken over the eligible keys, up front.
- `replay` and `draw` share `_weigh` and `pick`, so no second
  implementation of steps 2 to 6 exists.
- The record has exactly the 19 keys of §6, in that order, with every value
  JSON-native. `json.dumps(record, allow_nan=False)` succeeds, and a
  round trip replays.

---

### Task 0: precondition — 01d-S2 on this branch

- [ ] **Step 1:** Wait for the coordinator's word, then rebase `wt/01c-s1`
  onto the tip it names, which must contain 01d-S2 (`a9ab1b4`'s content).
- [ ] **Step 2: Verify:** `grep -n "served: tuple\[()\]" backend/src/grimoire/decisions.py`
  and `grep -n "^def answer_key" backend/src/grimoire/decisions.py` both
  hit. Then run `cd backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_decisions.py tests/test_decision_triggers.py`, which should pass.
- If the coordinator instead says to land before 01d-S2: `draw` gains a
  keyword `served: tuple[str, str, str] | tuple[()] = ()` that is read when
  `result` has no `served` attribute, and a private `_answer_key` with
  `answer_key`'s rule. Both are deleted in 01d-S2's integration. Do not take
  this branch without that instruction.

### Task 1: the primitives — constants, `Eligibility`, seeds, `unit`, `quanta`, `pick`, `_weigh`, `draw_from`

**Files:**
- Create: `backend/src/grimoire/draws.py`
- Create: `backend/tests/test_draws.py`

**Interfaces (produced):**

```python
ALGORITHM: Final = "sha256-q32-icdf/1"   # recorded; any change of rule is a new name
SEED_BITS: Final = 53                    # store/dice.py's reason: exact as a JS number
QUANTUM: Final = 2**32                   # weights are drawn as integers of 1/QUANTUM
MASS_SLACK: Final = 0.02                 # §5.4; §12 Q1: tuned per kind on 01a's figures
PURPOSE_MAX: Final = 200
RECORD_VERSION: Final = 1
_DOMAIN: Final = b"grimoire/draw/1\0"

@dataclass(frozen=True)
class Eligibility:
    only: tuple[str, ...] | None = None
    exclude: tuple[str, ...] = ()
    cutoff: float = 0.0
    floor: float = 0.0
    def __post_init__(self) -> None: ...      # decision 8
    def as_record(self) -> dict[str, Any]: ... # {"only": list|None, "exclude": list,
                                               #  "cutoff": float, "floor": float}

UNNARROWED: Final = Eligibility()

def _os_seed() -> int: return secrets.randbits(SEED_BITS)
_seed_source: Callable[[], int] = _os_seed
def new_seed() -> int
def unit(seed: int, purpose: str) -> int
def quanta(w: float) -> int
MASS_FLOOR_Q: Final = quanta(1 - MASS_SLACK)   # defined after quanta; 4209067950, pinned
def pick(weights: Sequence[tuple[str, int]], r53: int) -> str
def draw_from(weights: Sequence[tuple[str, float]], seed: int, purpose: str,
              eligibility: Eligibility = UNNARROWED) -> str | None

class _Weighed(NamedTuple):
    eligible: tuple[str, ...]          # step 2, canonical order
    kept: tuple[tuple[str, int], ...]  # after steps 3-5
    total: int                         # sum of kept weights (ints)
    cut_empty: bool                    # step 4 emptied a non-empty eligible set

def _weigh(pairs: Sequence[tuple[str, float]], eligibility: Eligibility) -> _Weighed
```

Each function's rules:

- `_check_seed(seed)`: an `int` that is not a `bool`, with `0 <= seed <
  2**SEED_BITS`. Otherwise `ValueError` naming §5.2 (a 63-bit seed is
  refused).
- `_check_purpose(purpose)`: a `str` that fully matches
  `[\x20-\x7e]{0,200}` (`re.fullmatch`). Otherwise `ValueError` that says a
  browser's `TextEncoder` would encode other text differently.
- `unit`: both checks run, then the result is
  `int.from_bytes(hashlib.sha256(_DOMAIN + str(seed).encode("ascii") + b"\0"
  + purpose.encode("ascii")).digest()[:8], "big") >> 11`, i.e. the top 53
  bits.
- `quanta(w)`: `math.floor(w * QUANTUM)`. The docstring says the result is
  exact because QUANTUM is a power of two. Its callers only pass checked
  probabilities.
- `pick(weights, r53)`: every weight must be an `int`, not a `bool`, and
  `>= 0`. Keys are unique `str`s. `r53` must be in `[0, 2**53)`. `total`
  must be greater than 0. Any of these failing is a `ValueError`. Then `t =
  (r53 * total) >> 53`, and the walk adds up the positive weights only,
  returning the first key with `t < c + q`.
- `_weigh(pairs, e)`: `pairs` is the offered keys in canonical order, each
  with its reported weight (0.0 for an omitted key). An `only` or `exclude`
  key that is not among the pairs' keys is a `ValueError`. Step 2 gives
  `eligible`. Right after it, `_check_floor(e, len(eligible))` raises
  `ValueError` if `quanta(e.floor) * len(eligible) > QUANTUM` (decision 11).
  This is report-independent, and it is the same function `draw` calls at
  its step 1. Step 3 takes `quanta` of each eligible weight. Step 4 sets `C
  = quanta(e.cutoff)` and drops each weight `< C`, and if eligible was
  non-empty and nothing is left, the result is `cut_empty=True` with nothing
  kept. Step 5 sets `F = quanta(e.floor)` and replaces each kept weight with
  `max(q, F)`. It cannot overflow, because kept ⊆ eligible.
- `draw_from`: checks the seed and purpose. Checks that the pairs have
  unique `str` keys and weights that pass `decisions`' probability rule (a
  finite non-`bool` number in [0, 1]). `draws` reimplements this three-line
  rule rather than import a private name. Then `_weigh`. Returns None when
  `cut_empty` or `total == 0`, and otherwise `pick(kept, unit(seed,
  purpose))`. Its docstring says it is **not for callers; use `draw`** (gate
  finding 10). It takes raw weights and so bypasses C4's no-draw table. It
  is public only so that a browser port and the goldens can test steps 2-6
  alone.

- [ ] **Step 1: Failing tests** (`backend/tests/test_draws.py`; every literal
  below was computed on CPython 3.11, 3.12 and 3.13 and agrees on all three):
  - `test_the_constants`: `ALGORITHM == "sha256-q32-icdf/1"`, `SEED_BITS ==
    53`, `QUANTUM == 2**32`, `MASS_SLACK == 0.02`, `MASS_FLOOR_Q ==
    4209067950`, `PURPOSE_MAX == 200`.
  - `test_unit_golden_vectors` (parametrised, checked in for a browser to
    reproduce): `(0, "") -> 554538817032997`; `(1, "next") ->
    5375726895549859`; `(2**53 - 1, "next") -> 4554579951240114`;
    `(4503599627370495, "next") -> 1951174975979578`; `(42, "action") ->
    5281169727463273`; `(42, "target:strike") -> 5553503322267527`;
    `(123456789, "intent") -> 8731260151485154`. Each is `< 2**53`.
  - `test_purpose_decouples_draws`: `unit(42, "action") != unit(42,
    "target:strike")`, and each equals its golden however many other units
    were computed before it.
  - `test_quanta_is_exact`: `quanta(0.1) == 429496729`, `quanta(0.125) ==
    536870912`, `quanta(0.0) == 0`, `quanta(1.0) == 2**32`, and
    `quanta(2**-33) == 0`.
  - `test_ten_tenths_are_interpreter_independent`: ten keys `o0`..`o9` at
    0.1 each. The integer mass is `4294967290`. The docstring records that
    `sum([0.1] * 10)` is `0.9999999999999999` on 3.11 and `1.0` on 3.12+,
    which is the trap. `draw_from(ten, 7, "next") == "o0"` (`unit ==
    323243774254641`), and `draw_from(ten, 2**53 - 1, "next") == "o5"`
    (`unit == 4554579951240114`). The boundary golden is `pick(ten_q,
    2702159776422298) == "o3"` (gate finding 4). A float inverse CDF over
    3.11's naive total gives `o2` there, so this golden fails a float
    implementation. It was re-derived on 3.11. CI's 3.11 and 3.14 legs both
    run this.
  - `test_the_reviews_boundary_case`: the weights `a 0.6, b 0.26, c 0.76, d
    0.7` quantise to `[2576980377, 1116691496, 3264175144, 3006477107]`,
    total `9964324124` (a mass over 1, drawn in proportion). At `r53 =
    6289509824431210`, a float inverse CDF picks `d` with naive left-to-right
    summation (`2.3200000000000003`) and `c` with `math.fsum` (`2.32`). The
    integer rule's answer is pinned: `pick(q, 6289509824431210) == "d"`. The
    integer boundary is pinned on both sides: `pick(q, 6289509823870140) ==
    "c"` and `pick(q, 6289509823870141) == "d"`.
  - `test_pick`: half-open intervals. With `[("a", 1), ("b", 0), ("c",
    1)]`, `r53 = 0` gives `a`, `r53 = 2**52` gives `c`, and `b` is never
    picked. `r53 = 2**53 - 1` gives the last positive key. Two equal weights
    split exactly at `2**52` (`2**52 - 1` gives the first, `2**52` the
    second). A total of 0, a negative weight, a `bool` weight, a repeated
    key, and `r53` of `-1` or `2**53` each raise `ValueError`.
  - `test_eligibility_validates_itself` (parametrised): `only=["a"]` (a
    list), `exclude=("a", 1)`, `cutoff=-0.1`, `cutoff=1.5`, `floor=nan`,
    `cutoff=True`, `cutoff=Fraction(1, 4)` and `floor=Decimal("0.1")` each
    raise `ValueError`. `Eligibility(cutoff=0, floor=1)` stores `0.0` and
    `1.0` as `float`s. `Eligibility()` round-trips through `as_record()` as
    `{"only": None, "exclude": [], "cutoff": 0.0, "floor": 0.0}`.
  - `test_masks` (on `draw_from`): `exclude=(NONE_KEY,)` never returns
    `NONE_KEY` over a sweep of 200 seeds, even when it carries most of the
    weight. `only=("a", "b")` returns only `a` or `b`. An `only` or
    `exclude` key that is not offered raises `ValueError`. `only=()` returns
    None.
  - `test_cutoff`: a key whose weight quantises one quantum under the
    cutoff's quanta is never drawn, and one exactly at it can be (find a
    seed that draws it in the sweep). A cutoff that removes every key
    returns None. A cutoff of 0.0 removes nothing.
  - `test_cutoff_then_floor`: with five eligible keys, three under the
    cutoff, and `floor=0.2`, no seed in a 300-seed sweep returns a cut key
    (a floor never lifts a cut key). A floor lifts an unreported (0.0) key:
    with `[("a", 1.0), ("b", 0.0)]` and `floor=0.5`, the sweep draws `b` at
    least once.
  - `test_the_floor_is_sized_to_the_eligible_set` (decision 11): with five
    eligible keys, `floor=0.4` raises `ValueError`, even with a cutoff that
    would keep only two of them (0.4 x 2 <= 1, but 0.4 x 5 > 1).
    `floor=0.6` over two eligible keys raises. `floor=0.5` over two does
    not.
  - `test_a_sized_floor_never_raises_on_any_report` (property; coordinator
    decision 2): a `random.Random(1010)` makes 1000 cases. Each has 1 to 12
    offered keys, random masks, a cutoff in `[0, 1]`, a floor of exactly
    `1 / len(eligible)` or anything below it, and random reports: omitted
    keys, zeros, masses from 0 to 2, and all weight under the cutoff.
    `draw_from` and `draw` (on a native answer of the same report) never
    raise.
  - `test_seed_and_purpose_checks` (parametrised over `unit`, `draw_from`):
    a seed of `2**53`, `2**63 - 1`, `-1` or `True`, and a purpose `"é"`,
    `"\x7f"`, `"a\nb"` or 201 `"x"`s each raise `ValueError`. A purpose of
    200 printable characters is accepted.
  - `test_new_seed`: `0 <= new_seed() < 2**53` over 50 calls. Patching
    `draws._seed_source` with `lambda: 5` gives 5, and patching it to return
    `2**53` raises `ValueError`.
  - `test_draws_is_a_leaf`: `_sibling_imports("draws") == {"decisions"}`.
    Every other import is in `sys.stdlib_module_names` (the AST walk
    `test_schemas.py:408-418` uses). The only relative import is `from .
    import decisions`, which binds the module (as
    `test_decisions_binds_the_module_not_its_names` checks for `decisions`).
- [ ] **Step 2: Run** `cd /home/user/wt/01c-s1/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_draws.py`, which should fail.
- [ ] **Step 3: Implement** Task 1's part of `draws.py`. The module
  docstring states the four rules: one sampler, integers after `quanta`, no
  draw from a non-report, and that the helper stores nothing. It also gives
  the browser recipe from Global Constraints (`BigInt` product and shift,
  `getBigUint64(0) >> 11n`, the exact `Math.floor(w * 2**32)`,
  `String(seed)`, an async SubtleCrypto).
- [ ] **Step 4: Run** it again, and it should pass.

### Task 2: `draw`, `Draw` and the version-1 record (§5.4, §6)

**Files:**
- Modify: `backend/src/grimoire/draws.py`
- Modify: `backend/tests/test_draws.py`

**Interfaces (produced):**

```python
BASES: Final = ("sampled", "answer", "none")

@dataclass(frozen=True)
class Draw:
    basis: str          # one of BASES
    key: str | None     # the selection as a support key, NONE_KEY included
    value: bool | int | str | decisions.Pair | decisions.Ranking | tuple[str, ...] | None
    record: dict[str, Any]   # §6, a fresh dict per call

def draw(q: decisions.Question, result: decisions.ItemResult, *, seed: int,
         purpose: str = "", eligibility: Eligibility = UNNARROWED) -> Draw
```

The private helpers keep each function at complexity 10 or under:

- `_offered(q) -> tuple[str, ...]` (§5.3 step 1). A Predicate gives
  `("true", "false")`. A Choice gives its option ids, then `NONE_KEY` if
  `allow_none`. A Score gives `str(i)` for each level. A Joint gives its
  `joint_choice(q)` option ids, then `NONE_KEY` if `allow_none`. Rank and
  MultiSelect give their candidate or option ids (decision 1).
- `_reported(q, answer, offered) -> tuple[tuple[str, float], ...] | None`. A
  Predicate gives `(("true", p), ("false", 1.0 - p))` when `probability`
  passes the probability rule. A Choice, Score or Joint gives the
  `distribution`'s entries in `offered` order when the distribution is a
  non-empty mapping with every key in `offered` and every value passing the
  rule. Rank and MultiSelect always give None. Anything else is None, i.e.
  no usable report.
- The answered key is `decisions.answer_key(answer)`, called directly with
  no wrapper (decision 2).
- `_value(q, key)`. `NONE_KEY` gives None. A Predicate gives `key ==
  "true"`, a Score `int(key)`, a Joint `decisions.split_joint(key)`, and a
  Choice `key`.
- `_eligible_answer(key, e, offered, cut_weight_q: int | None) -> bool`.
  The key must not be None, must be in `offered` (gate finding 6: a
  hand-built answer naming no offered key is `none`/`ineligible`, never
  returned), and must pass `only` and `exclude`. Only the
  `ineligible_mass` row passes `cut_weight_q`, the answered key's quanta;
  there it must also be `>= quanta(e.cutoff)` (decision 3). The
  `no_report`, `partial` and `inconsistent` rows pass None, and no cutoff
  applies. A keyless Rank or MultiSelect answer passes only when the
  eligibility narrows nothing (decision 1).
- `_record(...) -> dict[str, Any]` builds the 19 keys in §6's order: `v`,
  `algorithm`, `question` (`q.id`), `purpose`, `basis`, `sampled`
  (`basis == "sampled"`), `why`, `offered` (a list), `distribution` (a
  list of `[key, weight]` lists, or None), `eligibility`
  (`e.as_record()`), `mass_q`, `drawn_q`, `seed`, `selected`, `answer`,
  `backend` (`result.backend`), and then `kind`, `provider` and `model`
  (`result.served`, or `""` x 3 when it is empty).

`draw`'s order:
1. `_check_seed`, `_check_purpose`. If `q.id` is not in `result.answers`,
   raise `ValueError`. Compute `offered`. If an `only` or `exclude` key is
   not in `offered`, raise `ValueError`. Compute the eligible keys, and call
   `_check_floor(e, len(eligible))`. That is every raise `draw` has, and all
   of them come before the answer is read.
2. If `answer.answer is None`, the result is `none` with `why =
   answer.reason`.
3. If `_reported` gives None, the result is `answer`/`no_report`.
4. Otherwise compute `mass_q` as the int sum of `quanta(w)` over the report.
   If `mass_q < MASS_FLOOR_Q`, the result is `answer`/`partial`.
5. If the answered key is absent from the report or `quanta(weight) == 0`,
   the result is `answer`/`inconsistent`.
6. Otherwise run `w = _weigh([(k, report.get(k, 0.0)) for k in offered], e)`.
   If `w.cut_empty`, the result is `none`/`cutoff`.
7. If `w.total == 0`, the result is `answer`/`ineligible_mass`. Its
   eligibility test includes the cutoff (decision 3), and decision 4 shows it
   always becomes `none`/`ineligible`.
8. Otherwise the result is `sampled`, with key `pick(w.kept, unit(seed,
   purpose))`, `seed` recorded, and `drawn_q = w.total`.

In every `answer` row: if `_eligible_answer` holds, then `key` is the
answered key, `value = answer.answer` and `selected` is the key. If it does
not hold, the row becomes `none`/`ineligible` with `selected`, `key` and
`value` all None. The `answer` field is filed either way.

- [ ] **Step 1: Failing tests.** The fixtures are a `Choice("next", ...,
  (Option("characters:mara", ...), Option("characters:seraphine", ...),
  Option("grimoire", ...)), allow_none=True)` (the speaker pick's shape);
  answers built with `decisions.native_answer` (never by hand, except where
  a case says so); and `ItemResult(..., backend="native",
  served=("openai_compatible", "realm-openai", "decision-model-x"))`.
  - `test_the_specs_example_record_is_reproduced_exactly`: §6's example is
    the golden. Its report is `mara 0.55, seraphine 0.3, grimoire 0.1,
    NONE_KEY 0.05`. The call is `Eligibility(exclude=(NONE_KEY,),
    floor=0.125)`, `seed=4503599627370495`, `purpose="next"`. `draw(...).record`
    must equal the spec's dict exactly, key order included
    (`list(record) == [...]`): `mass_q 4294967293`, `drawn_q 4187593112`,
    `selected "characters:mara"`, `answer "characters:mara"`. Also
    `basis == "sampled"`, `key == value == "characters:mara"`.
  - `test_canonical_order`: one distribution handed to `native_answer` in
    two key orders gives identical records, `distribution` order included,
    for every seed in `range(300)`.
  - `test_the_no_draw_table` (parametrised, one id per §5.4 row and branch):
    - `answer is None`, giving `none` with `why` set to its reason: a
      predicate at exactly 0.5 (`abstained`), `native_answer(q,
      refused=True)` (`refused`), `Answer(None, "unreadable")`, and
      `decisions.unanswered(...)` with `"error"`. `selected`, `seed`,
      `mass_q` and `drawn_q` are null. The `answer` field is null in every
      case (decision 2): for an `allow_none` choice's explicit
      `chosen=NONE_KEY`, and for the tie `{mara 0.45, seraphine 0.45,
      NONE_KEY 0.1}`, which is abstained. That record must not hold
      `"<none>"` anywhere except in `offered` and `distribution`.
    - A structured answer (`ItemResult({"next": Answer("characters:mara")},
      backend="structured")`) gives `answer`/`no_report`, `seed: null`,
      `distribution: null`, `selected == answer == "characters:mara"`.
    - A predicate with an explicit `chosen=True` and no probability gives
      `no_report`.
    - An `Answer` carrying only `marginals` (hand-built on the choice:
      `Answer("characters:mara", marginals={"characters:mara": 0.9})`)
      gives `no_report`. A `Rank` answered with a `Ranking`, and a
      `MultiSelect` answered with a tuple, each give `no_report` with
      `selected: null`, `answer: null`, `key is None`, and `value` being the
      answer as given. Under `exclude=` they give `none`/`ineligible`
      (decision 1).
    - A hand-built report with a key that is not offered, or a weight of
      1.5, gives `no_report` (decision: never a raise).
    - The partial report `{mara: 0.4, seraphine: 0.1}` gives
      `answer`/`partial` with `mass_q == 2147483647`. With `cutoff=0.5`,
      above mara's 0.4, it is still `answer`/`partial` with `selected ==
      "characters:mara"` (decision 3: no cutoff at `partial`). With
      `exclude=("characters:mara",)` it is `none`/`ineligible`. A report of
      `{mara: 0.98}` alone sums to `MASS_FLOOR_Q` exactly and is not
      partial. A mass over 1, `{mara: 0.9, seraphine: 0.8}` (not a tie, so
      the argmax answers mara; gate finding 5), is sampled with `mass_q`
      recorded.
    - An inconsistent answer (`chosen="characters:seraphine"` beside `{mara:
      0.98, grimoire: 0.02}`, which omits seraphine; and again with
      `seraphine: 0.0` present) gives `answer`/`inconsistent` with
      `selected == "characters:seraphine"`. With `cutoff=0.1` (02's
      always-on shape) the same answer is **still** `answer`/`inconsistent`,
      with the same `selected` (coordinator decision 1). With
      `exclude=("characters:seraphine",)` it is `none`/`ineligible`.
    - A hand-built `Answer("characters:winifred", distribution=...)` whose
      key is not offered, beside a usable report, gives `none`/`ineligible`
      and never returns winifred (gate finding 6). (Its report holds no
      winifred key, so it is otherwise read as `inconsistent`.)
    - A cutoff that empties the set (every weight under it) gives
      `none`/`cutoff`, `seed: null`, and `drawn_q: null`.
    - The `ineligible_mass` row: `only=("grimoire",)` with `grimoire`
      reported at 0.0 (beside `{mara: 0.98, seraphine: 0.02}`) and the
      answer on mara gives `none`/`ineligible`, with `drawn_q == 0`
      (decision 4).
    - An answer outside `only` whose others carry weight is sampled from
      the others. An `answer` row whose key is excluded gives
      `none`/`ineligible`.
  - `test_a_sampled_none_is_a_selection`: with the reserved none carrying
    most of the mass, there is a seed in `range(200)` whose draw is `basis
    == "sampled"`, `key == NONE_KEY`, `value is None`. With
    `exclude=(NONE_KEY,)` no seed in that range ever gives it.
  - `test_values_take_the_answers_type`: a sampled predicate gives a
    `bool`, a score an `int` (with `key == str(value)`), a joint a
    `decisions.Pair` whose `.key == key`, and a choice a `str`.
  - `test_a_joint_draws_its_flattened_choice`: a `Joint` with a head that
    takes no tail and a head with two tails, answered natively through
    `native_lift` (as the native backend builds it), gives `offered` equal
    to `[o.id for o in joint_choice(q).options]`, and the sampled pair is
    one of them.
  - `test_the_record_is_json_and_holds_no_prose`: for a record of every
    basis, `set(record) == RECORD_KEYS` (the 19), `json.dumps(record,
    allow_nan=False)` succeeds, and every value is a str, int, float, bool,
    None or a list of those. With `result.rationale` set to "Seraphine
    hesitates at the door" and options described in prose, neither string
    appears in the dump. A result with `served == ()` records `kind`,
    `provider` and `model` as `""`.
  - `test_the_eligibility_is_recorded_as_supplied` (gate finding 11):
    `Eligibility(only=("characters:mara", "characters:seraphine"),
    cutoff=0.25)` is recorded as `{"only": ["characters:mara",
    "characters:seraphine"], "exclude": [], "cutoff": 0.25, "floor": 0.0}`.
    That holds on a sampled record and on an `answer` and a `none` record,
    and `Eligibility(cutoff=0)` records `cutoff: 0.0`, a float.
  - `test_draw_raises_only_for_misuse` (parametrised): a 63-bit seed, a
    non-ASCII purpose, `only=("characters:winifred",)` (not offered), a
    `result` lacking `"next"`, and `floor=0.3` over the four eligible keys
    of the speaker choice (0.3 x 4 > 1). Each raises `ValueError` even when
    the answer is `refused` or structured, because every check is at step 1
    (coordinator decision 2).
  - `test_draw_does_not_mutate_its_inputs`: the `Answer` and its
    distribution compare equal before and after, and two calls return
    distinct record dicts.
- [ ] **Step 2: Run**, which should fail. **Step 3: Implement.**
  **Step 4: Run**, which should pass.

### Task 3: `replay`, `ReplayError`, and the record through the round store

**Files:**
- Modify: `backend/src/grimoire/draws.py`
- Modify: `backend/tests/test_draws.py`

**Interfaces (produced):**

```python
class ReplayError(ValueError):
    """A record `replay` cannot recompute: another version or ALGORITHM,
    or a malformed or internally inconsistent record (§6.2)."""

def replay(record: Mapping[str, Any]) -> str | None
```

Rules (decision 10):
- The record must be a `Mapping`, with `v == RECORD_VERSION` and `algorithm
  == ALGORITHM`. `basis` must be in `BASES`, `sampled` a `bool` equal to
  `basis == "sampled"`, and `selected` None or a `str`. Otherwise
  `ReplayError`.
- If the basis is not `sampled`, return `selected` and draw nothing.
- If it is `sampled`: `seed` must be an int (a null seed is a
  `ReplayError`), `purpose` must pass `_check_purpose`, and `offered` must
  be a list or tuple of unique `str`s. `distribution` must be a list of
  two-element lists or tuples `[str, probability]` with unique keys, every
  key in `offered`. `eligibility` must be a mapping with exactly the keys
  `only`, `exclude`, `cutoff` and `floor`, rebuilt as `Eligibility(only=None
  if only is None else tuple(only), exclude=tuple(exclude), cutoff=cutoff,
  floor=floor)`. The pairs are rebuilt exactly as `draw` builds them, `[(k,
  dist.get(k, 0.0)) for k in offered]` (gate finding 7), so a key the
  report omitted weighs 0.0 and a floor can lift it on replay as it did in
  the draw. Then `_weigh`. Any `ValueError` raised on the way is
  re-raised as `ReplayError` from it. `cut_empty` or a total of 0 is also a
  `ReplayError` ("a sampled record whose support draws nothing"). Return
  `pick(kept, unit(seed, purpose))`.

- [ ] **Step 1: Failing tests:**
  - `test_replay_sweep`: a `random.Random(20261010)` drives 500 cases (test
    randomness only; the seeds it makes are inputs, not the thing under
    test). Each case has 2 to 12 options, some omitted from the report,
    sometimes `allow_none`, with masses from 0.5 to 1.4. Eligibilities come
    from `only` subsets, `exclude` subsets, cutoffs in `[0, 0.3]` and floors
    within `1 / len(offered)`, plus a seed in `[0, 2**53)` and a purpose
    from a fixed list. For every case, `replay(d.record) ==
    d.record["selected"]`, and `replay(json.loads(json.dumps(d.record))) ==
    d.record["selected"]`. The sweep asserts that it produced at least one
    record of each basis, at least one sampled record with a positive
    cutoff, one with a positive floor, and one whose `selected` is a key the
    report omitted (only a floor can have lifted it; gate finding 7), so
    the sweep cannot go vacuous.
  - `test_a_cutoff_record_replays`: a fixed sampled record with
    `cutoff=0.2` is pinned as a literal, and `replay` gives its literal
    `selected`. This is the interpreter-independence check §10 asks for
    under a cutoff, run on CI's 3.11 and 3.14.
  - `test_a_non_sampled_record_returns_its_selected`: for a `basis: answer`
    record and a `none` record, `replay` returns the stored `selected` even
    after `seed`, `offered` and `distribution` are corrupted, since nothing
    is drawn.
  - `test_replay_refuses` (parametrised, each a `ReplayError`):
    `algorithm: "sha256-q32-icdf/2"`; `v: 2`; a sampled record with `seed:
    None`; a `distribution` key not in `offered`; a duplicate `offered`
    key; a weight of `1.5`; `basis: "drawn"`; `sampled: True` with `basis:
    "answer"`; an `eligibility` missing `floor`; an `exclude` key that is
    not offered; a sampled record whose cutoff empties the support; and a
    non-mapping. `issubclass(ReplayError, ValueError)` holds.
  - `test_the_record_rides_the_round_record` (C3 §6.1 rule 1 shown on the
    write the speaker pick will use; nothing is wired). This uses an
    isolated `GRIMOIRE_HOME` with `store.worlds.create_world("Realm")`,
    `store.campaigns.create_campaign("Saltmarch", wid)` and
    `store.scenes.create_scene(cid, "Mara")`. It opens a round with
    `store.responses.new_round(cid, sid, eligible=[{"ref":
    "characters:mara", "name": "Mara"}], automatic=True, post=0,
    run_id="run-draw")` and asserts `round.get("pick") is None` (rule 4:
    readers tolerate absence). It then makes `d = draw(...)` and calls
    `store.responses.update_round(cid, sid, round["id"],
    actor_ref=d.value, pick=d.record)`. It re-reads `responses.json` from
    disk with `json.loads` and finds the round. `stored["pick"] ==
    d.record` and `replay(stored["pick"]) == stored["pick"]["selected"]`
    (gate finding 8: `selected` is a key and `actor_ref` a value, which
    differ for a sampled none). The
    docstring says this is the existing `campaign_lock` + `atomic` write, so
    C3 adds no store module, writer or guard marker.
- [ ] **Step 2: Run**, which should fail. **Step 3: Implement.**
  **Step 4: Run** `tests/test_draws.py`, which should pass.

### Task 4: CLAUDE.md, cross-interpreter evidence, the gates, the commit

**Files:**
- Modify: `CLAUDE.md`. Add one new Working-notes bullet directly before
  "**Adding an embedding call site?**", so that it does not edit the
  decide bullet, which 01c-S2 rewrites in parallel.

The bullet's content: **sampling a decision goes through `draws.py`, and
only there**. `draws.draw(q, result, seed=draws.new_seed(), purpose=…,
eligibility=draws.Eligibility(...))`, and the only reshaping is a recorded
`Eligibility` (`only`, `exclude`, `cutoff`, `floor`). The `floor` is sized
to the eligible set (`floor <= 1 / len(eligible)`) and is refused up front
otherwise, so no report can make a draw raise mid-turn. `draws.draw_from`
is not for callers: it takes raw weights and so skips the no-draw rules
below. The draw is SHA-256
and integer arithmetic (`quanta`), never `random` and never a float sum, so
a record replays to the same key on 3.11 to 3.14, on Android and in a
browser, and a change of rule is a new `ALGORITHM`. Nothing that reported no
distribution is sampled: an answer of None is `basis: none`. A structured
answer, a report under `1 - MASS_SLACK` in mass, a report that contradicts
its own answer, and a rank's or selection's `marginals` are each `basis:
answer`, `sampled: false`, acted on and never replayed. The helper stores
nothing: the caller files `Draw.record` in the same write and under the same
lock as the outcome it chose (for the speaker pick, the round record through
`responses.update_round`). A present record means decided, a decided
outcome is never re-drawn, and a reroll mints a new seed. No task samples
yet (`TaskPolicy.samples` is 01c-S2's).

- [ ] **Step 1: Cross-interpreter check (evidence, not committed).** From
  `backend/`, run a scratchpad script under `python3.11`, `python3.12` and
  the venv's 3.13. The script inserts `src` on `sys.path`; `draws`,
  `decisions` and `schemas` are stdlib-only and `grimoire/__init__.py` is
  empty. It prints every Task 1 to 3 golden (`unit` vectors, ten tenths,
  the boundary case, the spec's example record, the cutoff record), and all
  three outputs must be byte-identical. Record the result in the plan's
  review section, not in any committed file other than this plan.
- [ ] **Step 2: Gates.**
  `cd backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_draws.py tests/test_decisions.py tests/test_decision_triggers.py tests/test_import_guard.py tests/test_lock_domain_guard.py tests/test_atomic_guard.py tests/test_paths_guard.py tests/test_docs_guard.py tests/test_responses.py`
  should pass. Then from the worktree root,
  `make check-lint check-mypy check-templates PY=/home/user/grimoire/backend/.venv/bin/python`
  and `/home/user/grimoire/backend/.venv/bin/python scripts/ratchet.py
  eslint` should all be at baseline. Then run the whole suite once:
  `make check-py WORKERS=2 PY=/home/user/grimoire/backend/.venv/bin/python`.
  The only accepted failure is the known root-permission one,
  `tests/test_atomic.py::test_a_read_only_record_is_not_silently_replaced`.
  Also run `make check-pydantic1 PY=...`.
- [ ] **Step 3: Commit** `01c-S1: the sampler, the record and replay`, with
  a short body and the two attribution lines from the brief. Never push.

## Plan gate (substitute review, 2026-10-10)

No Codex CLI is available here. An independent reviewer agent stood in for
`/codex:adversarial-review` against this plan, spec 01c and the code at
`b3204cc` (references re-checked at `706ffa3`). It re-derived every golden independently. Verdict: sound, with
nothing blocking. The coordinator ruled on the two spec ambiguities it
raised. Every finding is folded above, as follows:

| # | Finding | Disposition |
|---|---|---|
| 1 | **[SHOULD], coordinator decision.** Applying the cutoff to the answered key at `partial`/`inconsistent` makes `inconsistent` unreachable under 02's always-on `1/(2n)` cutoff and erases its `why` | **Folded.** Decision 3 is rewritten: the cutoff applies to the answered key only at the `ineligible_mass` row. That is recorded as a deliberate reading of §5.4's "where a usable report exists". `_eligible_answer` takes the cut weight only from that row. Tests: an inconsistent answer under `cutoff=0.1` stays `answer`/`inconsistent`, and a partial answer under `cutoff=0.5` stays `answer`/`partial` |
| 2 | **[SHOULD], coordinator decision.** A floor checked over the kept keys makes `draw` raise depending on the report | **Folded.** Decision 11 and Global Constraints: `quanta(floor) * len(eligible) > QUANTUM` is refused at step 1, before the answer is read. `_weigh` checks it right after step 2, so `replay` agrees. Kept ⊆ eligible, so §5.3's count over the kept keys always passes. This is recorded as the resolution of C2's "never raises" against §5.3. Added a property test (`test_a_sized_floor_never_raises_on_any_report`) and `test_the_floor_is_sized_to_the_eligible_set`. The sizing rule is in `Eligibility`'s docstring and the CLAUDE.md bullet. `test_draw_raises_only_for_misuse` now raises the floor on a refused and on a structured answer |
| 3 | [SHOULD] Decision 2's `NONE_KEY` spelling misreports a tie as a chosen none | **Folded.** The record's `answer` is `answer_key(answer)` unchanged, so an abstention is `null` and `why` says what happened. The `_answered_key` helper is deleted. Test: the tie `{mara .45, seraphine .45, <none> .1}` holds no `"<none>"` outside `offered` and `distribution` |
| 4 | [SHOULD] The ten-tenths golden does not catch a float implementation | **Folded.** `pick(ten_q, 2702159776422298) == "o3"`. A float draw over 3.11's naive total gives `o2` there (re-derived on 3.11) |
| 5 | [SHOULD] `{mara: .9, seraphine: .9}` is a tie, so abstained, not sampled | **Folded.** Changed to `{mara: .9, seraphine: .8}` |
| 6 | [NIT] `_eligible_answer` should require the key in `offered` | **Folded.** It is required, and a test uses a hand-built answer naming winifred |
| 7 | [NIT] `replay`'s pairs must weigh an omitted key at 0.0 | **Folded.** It rebuilds them as `[(k, dist.get(k, 0.0)) for k in offered]`. The sweep asserts a sampled record whose selected key the report omitted |
| 8 | [NIT] The C3 test compares the replay with `actor_ref` | **Folded.** It compares with `stored["pick"]["selected"]` |
| 9 | [NIT] Spell out the JS recipe | **Folded** into Global Constraints and the module docstring (Task 1 step 3): `BigInt` for `r53 * total` (up to 2**93; JS `>>` is int32), `getBigUint64(0) >> 11n`, exact `Math.floor(w * 2**32)`, `String(seed)` below 2**53, async SubtleCrypto |
| 10 | [NIT] `draw_from` bypasses C4 | **Folded.** Its docstring and the CLAUDE.md bullet say "not for callers; use `draw`" |
| 11 | [NIT] A `Fraction` cutoff would break `json.dumps`, `MASS_FLOOR_Q` must be defined after `quanta`, and the record should be asserted to hold the eligibility as supplied | **Folded.** `cutoff` and `floor` accept only `int`/`float` (not `bool`), coerced to `float`, with `Fraction`/`Decimal` refused in the test. `MASS_FLOOR_Q` moved below `quanta`. Added `test_the_eligibility_is_recorded_as_supplied` |

Implementation waits on Task 0, the coordinator's word that a tip
containing the final 01d-S2 is ready to rebase onto.

## Implementation record (2026-10-10)

The branch was rebased onto `706ffa3` (01d-S2 integrated), and the line
references above were re-checked there. The implementation follows Tasks 1-4
with these small differences from the text:

- `draw`'s step-1 checks live in one helper, `_prepared`, and the record's
  fields are spread over `_record`, `_none` and `_as_answer`. That keeps every
  function within ruff's complexity limit of 10.
- `replay` also turns a `TypeError` into `ReplayError`, so an unhashable key
  in a hand-edited record is a refusal, not a crash. `test_replay_refuses`
  covers it.
- `test_a_cutoff_record_replays` pins its literal record and checks it in
  both directions: `draw` reproduces it, and `replay` gives `d`. Its numbers
  were derived separately by a stdlib-only script before the module existed.

Cross-interpreter evidence (Task 4 step 1): a scratchpad script printed
every golden through `grimoire.draws` itself. That covered the `unit`
vectors, ten tenths with the `o3` boundary, the review's boundary case, the
spec's example record and the cutoff record. Its output had the same SHA-256
on CPython 3.11.17, 3.12 and 3.13.16.

Gates run, at the coordinator's speed-mode instruction: the whole-suite
`make check-py` was stopped part-way. The slice's own tests and the guard
set ran green with `-n 3`, and `make check-lint check-mypy` and the
`check-templates` and eslint ratchets were all at baseline. CI is the full
gate. Review gates: the plan gate was run; the implementation review and the
final spec gate were not run (speed mode), so those Codex gates are still
owed.

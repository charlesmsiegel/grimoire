"""The one sampler a decision's distribution is ever drawn through (roadmap 01c).

A caller that wants a probabilistic outcome -- a sampled next speaker, an
NPC's action over its legal set -- hands `draw` the question, the item's
`decisions.ItemResult`, a seed from `new_seed()` and a `purpose`, and gets
back a `Draw`: what was selected, and a self-contained record of how. Four
rules hold everywhere:

- **One sampler.** Nothing else in the package draws from a distribution. A
  caller's own reshaping is expressed only as an `Eligibility` (`only`,
  `exclude`, `cutoff`, `floor`), which this module applies and records.
- **Integers after `quanta`.** The uniform is the top 53 bits of one SHA-256
  digest of ASCII bytes (`unit`); no float is ever hashed or formatted. Each
  reported weight becomes `quanta(w)`, an exact integer, and every total,
  bound and comparison after that is integer arithmetic. Float summation is
  not stable across the supported interpreters (`sum([0.1] * 10)` is
  0.9999999999999999 on 3.11 and 1.0 on 3.12), and a draw near a boundary
  would otherwise pick a different key on different ones. So a record replays
  to the same key on CPython 3.11 to 3.14, on Android's Chaquopy and in a
  browser, and any change to the rule is a new `ALGORITHM`.
- **No draw from a non-report** (01c-C4). An answer of None is never drawn;
  a structured answer, a report that is invalid, partial (`MASS_SLACK`) or
  contradicts its own answer, and a rank's or selection's `marginals` are
  acted on as the plain answer, recorded `sampled: false`. Turning "the
  backend did not say" into a random answer is exactly what this forbids.
- **The helper stores nothing.** The caller files `Draw.record` in the same
  write, under the same lock, as the outcome it chose; a present record means
  decided, and a decided outcome is never re-drawn -- `replay` recomputes it.

The browser recipe, for a port that must agree with this module: SHA-256 via
SubtleCrypto (`crypto.subtle.digest`, which is async, so a draw is a
promise) over the same bytes; `unit` is `new DataView(digest).getBigUint64(0)
>> 11n`; `String(seed)` spells every integer below 2**53 as `str(seed)` does;
`Math.floor(w * 2**32)` is exact, as `quanta` is, because 2**32 is a power of
two; and `r53 * total` reaches 2**93, so it and its `>> 53n` are `BigInt`
arithmetic (JavaScript's `>>` on a number is int32). The goldens in
`tests/test_draws.py` are what such a port reproduces.

A gateway leaf: it imports `decisions` and the standard library, and reads no
store, clock, environment or file.
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, NamedTuple, TypeAlias

from . import decisions

#: The rule's name, recorded with every draw. Any change of rule is a new name.
ALGORITHM: Final = "sha256-q32-icdf/1"
#: A seed's width: `store/dice.py`'s reason, a seed must stay exact as a
#: JavaScript number.
SEED_BITS: Final = 53
#: Weights are drawn as integers of 1/QUANTUM.
QUANTUM: Final = 2**32
#: How far under 1 a report's mass may fall and still be drawn from: it
#: absorbs rounding in reported values (two-decimal reports, up to four
#: rounded weights). Mass the report left out could belong to any option, so a
#: report below this is not normalised (§5.4). To be tuned per adapter kind
#: against the reported masses 01a's reports show -- never against a measured
#: library.
MASS_SLACK: Final = 0.02
#: The longest `purpose`: ids and fixed labels.
PURPOSE_MAX: Final = 200
#: The record's shape version.
RECORD_VERSION: Final = 1
#: What a draw stood on: a sample, the backend's plain answer, or nothing.
BASES: Final = ("sampled", "answer", "none")

_DOMAIN: Final = b"grimoire/draw/1\0"
_R53: Final = 2**53
_PURPOSE: Final = re.compile(rf"[\x20-\x7e]{{0,{PURPOSE_MAX}}}")


def quanta(w: float) -> int:
    """`w` in integer units of 1/QUANTUM, rounded down: exact for any
    binary64 `w`, since QUANTUM is a power of two."""
    return math.floor(w * QUANTUM)


#: `1 - MASS_SLACK` in quanta: a usable report's mass is at least this.
MASS_FLOOR_Q: Final = quanta(1 - MASS_SLACK)


def _probability(value: object) -> float | None:
    """`value` as a probability -- a finite number, never a bool, in [0, 1] --
    or None: `decisions`' own rule, restated rather than imported private."""
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0 <= value <= 1 and math.isfinite(value)):
        return float(value)
    return None


def _fraction(name: str, value: object) -> float:
    """`value` as a float in [0, 1]: an int or a float only (never a bool, a
    Fraction or a Decimal, which a record's `json.dumps` could not carry)."""
    p = _probability(value)
    if p is None:
        raise ValueError(f"{name} must be an int or float in [0, 1], not {value!r}")
    return p


def _keys(name: str, value: object) -> None:
    if not isinstance(value, tuple) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be a tuple of keys, not {value!r}")


@dataclass(frozen=True)
class Eligibility:
    """A caller's one recorded reshaping of a distribution before the draw
    (§5.3): `only` the keys allowed (None: every offered key), `exclude` keys
    removed (e.g. `decisions.NONE_KEY`), `cutoff` the weight a key must reach
    to stay, and `floor` the minimum weight each kept key is raised to.
    `cutoff` runs before `floor`, so a floor never lifts a key the cutoff
    removed.

    Sizing rule: `floor` may be at most ``1 / len(eligible)``, the keys left
    after `only` and `exclude`. A larger floor would give more probability
    than there is, and `draw` refuses it before reading any answer, so no
    report can make a draw raise. A caller's eligibility comes from its own
    deterministic state, never from the answer."""

    only: tuple[str, ...] | None = None
    exclude: tuple[str, ...] = ()
    cutoff: float = 0.0
    floor: float = 0.0

    def __post_init__(self) -> None:
        if self.only is not None:
            _keys("only", self.only)
        _keys("exclude", self.exclude)
        object.__setattr__(self, "cutoff", _fraction("cutoff", self.cutoff))
        object.__setattr__(self, "floor", _fraction("floor", self.floor))

    def as_record(self) -> dict[str, Any]:
        return {"only": None if self.only is None else list(self.only),
                "exclude": list(self.exclude), "cutoff": self.cutoff, "floor": self.floor}


#: No narrowing: every offered key, no cutoff, no floor.
UNNARROWED: Final = Eligibility()


def _os_seed() -> int:
    return secrets.randbits(SEED_BITS)


#: Where `new_seed` gets its bits; a test seam, patched like
#: `routes.character_turns._rng`.
_seed_source: Callable[[], int] = _os_seed


def _check_seed(seed: object) -> int:
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**SEED_BITS:
        raise ValueError(f"a seed is an int in [0, 2**{SEED_BITS}) (a JS-safe integer), "
                         f"not {seed!r}")
    return seed


def _check_purpose(purpose: object) -> str:
    if not isinstance(purpose, str) or _PURPOSE.fullmatch(purpose) is None:
        raise ValueError(f"a purpose is printable ASCII of at most {PURPOSE_MAX} characters "
                         f"(a browser's TextEncoder would encode other text differently), "
                         f"not {purpose!r}")
    return purpose


def new_seed() -> int:
    """A fresh seed, minted once per decision occasion: `SEED_BITS` from the
    OS CSPRNG, as `store/dice.py` mints one. Nothing derives a seed from an
    id, which would make two rerolls of one round identical."""
    return _check_seed(_seed_source())


def unit(seed: int, purpose: str) -> int:
    """A 53-bit integer, uniform on [0, 2**53): the top 53 bits of
    sha256(b"grimoire/draw/1\\0" + str(seed) + b"\\0" + purpose), all ASCII.
    Each `purpose` is its own stream, so two draws from one seed never depend
    on how many came before either."""
    _check_seed(seed)
    _check_purpose(purpose)
    digest = hashlib.sha256(_DOMAIN + str(seed).encode("ascii") + b"\0"
                            + purpose.encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big") >> 11


def _total(weights: Sequence[tuple[str, int]]) -> int:
    seen: set[str] = set()
    total = 0
    for key, q in weights:
        if not isinstance(key, str) or key in seen:
            raise ValueError(f"weights need unique string keys; {key!r} is not one")
        if isinstance(q, bool) or not isinstance(q, int) or q < 0:
            raise ValueError(f"a weight is a non-negative int, not {q!r}")
        seen.add(key)
        total += q
    return total


def pick(weights: Sequence[tuple[str, int]], r53: int) -> str:
    """The integer inverse CDF: each key with a positive weight owns the
    half-open interval ``[c, c + q)`` of ``[0, total)`` in the given order,
    and the key whose interval holds ``(r53 * total) >> 53`` is selected.
    Exact, so no key is ever rounded past; equal weights are equal intervals."""
    if isinstance(r53, bool) or not isinstance(r53, int) or not 0 <= r53 < _R53:
        raise ValueError(f"r53 is an int in [0, 2**53), not {r53!r}")
    total = _total(weights)
    if total <= 0:
        raise ValueError("no weight to pick from")
    target = (r53 * total) >> 53
    positive = [(key, q) for key, q in weights if q > 0]
    bound = 0
    for key, q in positive[:-1]:
        bound += q
        if target < bound:
            return key
    return positive[-1][0]


class _Weighed(NamedTuple):
    eligible: tuple[str, ...]
    kept: tuple[tuple[str, int], ...]
    total: int
    cut_empty: bool


def _check_floor(e: Eligibility, eligible: int) -> None:
    if quanta(e.floor) * eligible > QUANTUM:
        raise ValueError(f"floor {e.floor!r} over {eligible} eligible keys is more than all "
                         f"the probability there is; size it to at most 1/{eligible}")


def _eligible(keys: Sequence[str], e: Eligibility) -> tuple[str, ...]:
    """Step 2: the offered keys `only` and `exclude` leave, in canonical
    order; then the floor's sizing check against them."""
    offered = set(keys)
    unknown = [key for key in (*(e.only or ()), *e.exclude) if key not in offered]
    if unknown:
        raise ValueError(f"eligibility names keys that are not offered: {unknown!r}")
    allowed = None if e.only is None else set(e.only)
    eligible = tuple(key for key in keys
                     if (allowed is None or key in allowed) and key not in e.exclude)
    _check_floor(e, len(eligible))
    return eligible


def _weigh(pairs: Sequence[tuple[str, float]], e: Eligibility) -> _Weighed:
    """§5.3 steps 2 to 5 over `pairs` -- every offered key in canonical order
    with its reported weight (0.0 for one the report left out): the masks,
    integer weights, the cutoff and the floor."""
    weights = dict(pairs)
    eligible = _eligible([key for key, _ in pairs], e)
    cut = quanta(e.cutoff)
    survivors = [(key, q) for key in eligible if (q := quanta(weights[key])) >= cut]
    if eligible and not survivors:
        return _Weighed(eligible, (), 0, True)
    lift = quanta(e.floor)
    kept = tuple((key, max(q, lift)) for key, q in survivors)
    return _Weighed(eligible, kept, sum(q for _, q in kept), False)


def _checked_pairs(weights: Sequence[tuple[str, float]]) -> list[tuple[str, float]]:
    seen: set[str] = set()
    out: list[tuple[str, float]] = []
    for key, w in weights:
        p = _probability(w)
        if not isinstance(key, str) or key in seen or p is None:
            raise ValueError(f"weights are unique keys with probabilities, not {(key, w)!r}")
        seen.add(key)
        out.append((key, p))
    return out


def draw_from(weights: Sequence[tuple[str, float]], seed: int, purpose: str,
              eligibility: Eligibility = UNNARROWED) -> str | None:
    """Not for callers; use `draw`. This takes raw weights -- every offered
    key in canonical order, 0.0 for one unreported -- and so skips C4's
    no-draw rules. It is public for the goldens and a browser port, which
    test steps 2 to 6 alone. None when the cutoff left nothing, or nothing
    eligible has weight."""
    _check_seed(seed)
    _check_purpose(purpose)
    weighed = _weigh(_checked_pairs(weights), eligibility)
    if weighed.cut_empty or weighed.total == 0:
        return None
    return pick(weighed.kept, unit(seed, purpose))


# --- draw: the no-draw table and the record (§5.4, §6) -----------------------

Value: TypeAlias = (bool | int | str | decisions.Pair | decisions.Ranking
                    | tuple[str, ...] | None)


@dataclass(frozen=True)
class Draw:
    """What `draw` decided. `basis` is one of `BASES`; `key` is the selection
    as a support key (`NONE_KEY` included) or None; `value` is the selection
    in the answer's own type -- a bool, an int level, an option id, a
    `decisions.Pair`, or for a rank or selection the answer as given -- and
    None for a sampled `NONE_KEY` as for nothing selected (read `basis`);
    `record` is the §6 record, a fresh dict the caller stores with its
    outcome."""

    basis: str
    key: str | None
    value: Value
    record: dict[str, Any]


class _Case(NamedTuple):
    q: decisions.Question
    result: decisions.ItemResult
    answer: decisions.Answer
    offered: tuple[str, ...]
    eligibility: Eligibility
    purpose: str


def _offered(q: decisions.Question) -> tuple[str, ...]:
    """§5.3 step 1: the legal keys in the QUESTION's order, never the
    report's."""
    if isinstance(q, decisions.Predicate):
        return ("true", "false")
    if isinstance(q, decisions.Score):
        return tuple(str(level) for level in range(len(q.levels)))
    if isinstance(q, decisions.Rank):
        return tuple(opt.id for opt in q.candidates)
    if isinstance(q, decisions.MultiSelect):
        return tuple(opt.id for opt in q.options)
    choice = q.choice if isinstance(q, decisions.Joint) else q
    ids = tuple(opt.id for opt in choice.options)
    return (*ids, decisions.NONE_KEY) if choice.allow_none else ids


def _reported(q: decisions.Question, answer: decisions.Answer,
              offered: tuple[str, ...]) -> tuple[tuple[str, float], ...] | None:
    """The answer's usable report as pairs in canonical order, or None: a
    predicate's P(true) as ``("true", p), ("false", 1 - p)``; a choice's,
    score's or joint's distribution when every key is offered and every
    value a probability -- an invalid one is dropped whole, as
    `decisions._distribution` drops it. A rank or selection has none: its
    `marginals` are independent events, never a distribution."""
    if isinstance(q, (decisions.Rank, decisions.MultiSelect)):
        return None
    if isinstance(q, decisions.Predicate):
        p = _probability(answer.probability)
        return None if p is None else (("true", p), ("false", 1.0 - p))
    dist = answer.distribution
    if not isinstance(dist, Mapping) or not dist:
        return None
    checked: dict[str, float] = {}
    for key, w in dist.items():
        p = _probability(w)
        if key not in offered or p is None:
            return None
        checked[key] = p
    return tuple((key, checked[key]) for key in offered if key in checked)


def _value(q: decisions.Question, key: str) -> Value:
    if key == decisions.NONE_KEY:
        return None
    if isinstance(q, decisions.Predicate):
        return key == "true"
    if isinstance(q, decisions.Score):
        return int(key)
    if isinstance(q, decisions.Joint):
        return decisions.split_joint(key)
    return key


def _eligible_answer(case: _Case, key: str | None, cut_q: int | None) -> bool:
    """Whether a plain answer may be returned as the selection: offered, and
    through `only` and `exclude`; and through the cutoff where `cut_q` (its
    quanta) is given, which only the `ineligible_mass` row does. A keyless
    rank or selection answer only when nothing is narrowed."""
    e = case.eligibility
    if key is None:
        narrowed = e.only is not None or bool(e.exclude)
        return isinstance(case.q, (decisions.Rank, decisions.MultiSelect)) and not narrowed
    if key not in case.offered or key in e.exclude:
        return False
    if e.only is not None and key not in e.only:
        return False
    return cut_q is None or cut_q >= quanta(e.cutoff)


def _record(case: _Case, basis: str, why: str, *,
            report: tuple[tuple[str, float], ...] | None = None, mass_q: int | None = None,
            drawn_q: int | None = None, seed: int | None = None,
            selected: str | None = None) -> dict[str, Any]:
    served: tuple[str, ...] = case.result.served or ("", "", "")
    return {
        "v": RECORD_VERSION,
        "algorithm": ALGORITHM,
        "question": case.q.id,
        "purpose": case.purpose,
        "basis": basis,
        "sampled": basis == "sampled",
        "why": why,
        "offered": list(case.offered),
        "distribution": None if report is None else [[key, w] for key, w in report],
        "eligibility": case.eligibility.as_record(),
        "mass_q": mass_q,
        "drawn_q": drawn_q,
        "seed": seed,
        "selected": selected,
        "answer": decisions.answer_key(case.answer),
        "backend": case.result.backend,
        "kind": served[0],
        "provider": served[1],
        "model": served[2],
    }


def _none(case: _Case, why: str, **fields: Any) -> Draw:
    return Draw("none", None, None, _record(case, "none", why, **fields))


def _as_answer(case: _Case, why: str, *, report: tuple[tuple[str, float], ...] | None = None,
               mass_q: int | None = None, drawn_q: int | None = None,
               cut: bool = False) -> Draw:
    """An `answer` row: the backend's plain answer, recorded `sampled: false`
    -- or `none`/`ineligible` when the caller's narrowing excludes it."""
    key = decisions.answer_key(case.answer)
    cut_q = quanta(dict(report or ()).get(key, 0.0)) if cut and key is not None else None
    if not _eligible_answer(case, key, cut_q):
        return _none(case, "ineligible", report=report, mass_q=mass_q, drawn_q=drawn_q)
    return Draw("answer", key, case.answer.answer,
                _record(case, "answer", why, report=report, mass_q=mass_q, drawn_q=drawn_q,
                        selected=key))


def _prepared(q: decisions.Question, result: decisions.ItemResult, seed: int, purpose: str,
              eligibility: Eligibility) -> _Case:
    """Every check `draw` raises for, all before the answer is read."""
    _check_seed(seed)
    _check_purpose(purpose)
    if q.id not in result.answers:
        raise ValueError(f"the result has no answer to {q.id!r}")
    offered = _offered(q)
    _eligible(offered, eligibility)
    return _Case(q, result, result.answers[q.id], offered, eligibility, purpose)


def draw(q: decisions.Question, result: decisions.ItemResult, *, seed: int,
         purpose: str = "", eligibility: Eligibility = UNNARROWED) -> Draw:
    """Draw `q`'s selection from `result`'s report (§5.4, in order):

    - an answer of None (abstained, refused, unreadable, error) is `none`;
    - no usable report (structured, invalid, a rank or selection) is the
      plain `answer`, `no_report`;
    - a report under `MASS_FLOOR_Q` in mass is the `answer`, `partial`;
    - a report giving the answered key no weight is the `answer`,
      `inconsistent`;
    - a cutoff that leaves no key is `none`, `cutoff`;
    - nothing eligible with weight is the `answer`, `ineligible_mass` (the
      answered key cannot then be eligible, so it always reads `none`,
      `ineligible`);
    - otherwise `sampled`.

    An answer row whose key the caller's `only`/`exclude` (and, at
    `ineligible_mass` only, the cutoff) rules out is `none`, `ineligible`.
    Raises `ValueError` only for misuse -- the seed, the purpose, a mask key
    not offered, a floor too large for the eligible keys, a result without
    `q` -- all checked before the answer is read, so no report makes it
    raise. Only a task whose policy samples calls this."""
    case = _prepared(q, result, seed, purpose, eligibility)
    answer = case.answer
    if answer.answer is None:
        return _none(case, answer.reason)
    report = _reported(q, answer, case.offered)
    if report is None:
        return _as_answer(case, "no_report")
    mass_q = sum(quanta(w) for _, w in report)
    if mass_q < MASS_FLOOR_Q:
        return _as_answer(case, "partial", report=report, mass_q=mass_q)
    weights = dict(report)
    key = decisions.answer_key(answer)
    if key is None or quanta(weights.get(key, 0.0)) == 0:
        return _as_answer(case, "inconsistent", report=report, mass_q=mass_q)
    weighed = _weigh([(k, weights.get(k, 0.0)) for k in case.offered], eligibility)
    if weighed.cut_empty:
        return _none(case, "cutoff", report=report, mass_q=mass_q)
    if weighed.total == 0:
        return _as_answer(case, "ineligible_mass", report=report, mass_q=mass_q, drawn_q=0,
                          cut=True)
    chosen = pick(weighed.kept, unit(seed, purpose))
    return Draw("sampled", chosen, _value(q, chosen),
                _record(case, "sampled", "", report=report, mass_q=mass_q,
                        drawn_q=weighed.total, seed=seed, selected=chosen))


# --- replay (§6.2) ------------------------------------------------------------

class ReplayError(ValueError):
    """A record `replay` cannot recompute: another version or `ALGORITHM`, or
    a malformed or internally inconsistent record (§6.2)."""


def _sequence(value: object, what: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise ReplayError(f"{what} is not a list")
    return value


def _replay_offered(value: object) -> tuple[str, ...]:
    keys = _sequence(value, "offered")
    if not all(isinstance(key, str) for key in keys) or len(set(keys)) != len(keys):
        raise ReplayError("offered is not a list of unique keys")
    return tuple(keys)


def _replay_distribution(value: object, offered: tuple[str, ...]) -> dict[str, float]:
    weights: dict[str, float] = {}
    for pair in _sequence(value, "distribution"):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ReplayError(f"a distribution entry is not a pair: {pair!r}")
        key, w = pair
        p = _probability(w)
        if key not in offered or key in weights or p is None:
            raise ReplayError(f"a distribution entry is not an offered key's weight: {pair!r}")
        weights[key] = p
    return weights


def _replay_eligibility(value: object) -> Eligibility:
    if not isinstance(value, Mapping) or set(value) != {"only", "exclude", "cutoff", "floor"}:
        raise ReplayError(f"eligibility is malformed: {value!r}")
    only = value["only"]
    return Eligibility(only=None if only is None else tuple(_sequence(only, "only")),
                       exclude=tuple(_sequence(value["exclude"], "exclude")),
                       cutoff=value["cutoff"], floor=value["floor"])


def _replayed(record: object) -> str | None:
    if not isinstance(record, Mapping):
        raise ReplayError("a record is a mapping")
    if record.get("v") != RECORD_VERSION or record.get("algorithm") != ALGORITHM:
        raise ReplayError(f"not a version-{RECORD_VERSION} {ALGORITHM} record")
    basis, sampled, selected = record.get("basis"), record.get("sampled"), record.get("selected")
    if basis not in BASES or not isinstance(sampled, bool) or sampled != (basis == "sampled"):
        raise ReplayError(f"basis {basis!r} and sampled {sampled!r} disagree")
    if selected is not None and not isinstance(selected, str):
        raise ReplayError(f"selected is a key or null, not {selected!r}")
    if basis != "sampled":
        return selected
    seed = _check_seed(record.get("seed"))
    purpose = _check_purpose(record.get("purpose"))
    offered = _replay_offered(record.get("offered"))
    weights = _replay_distribution(record.get("distribution"), offered)
    weighed = _weigh([(key, weights.get(key, 0.0)) for key in offered],
                     _replay_eligibility(record.get("eligibility")))
    if weighed.cut_empty or weighed.total == 0:
        raise ReplayError("a sampled record whose support draws nothing")
    return pick(weighed.kept, unit(seed, purpose))


def replay(record: Mapping[str, Any]) -> str | None:
    """A record's selection, recomputed from its own fields (§6.2): for
    `basis: sampled`, steps 1 to 6 from `offered`, `distribution`,
    `eligibility`, `seed` and `purpose` alone; for any other basis, its
    stored `selected`, since nothing was drawn. It returns what it computed
    and does not compare it with `selected`, so a caller can tell a record
    that was edited or drawn under another rule. Raises `ReplayError` for a
    record of another version or `ALGORITHM`, or a malformed one."""
    try:
        return _replayed(record)
    except ReplayError:
        raise
    except (ValueError, TypeError) as exc:
        raise ReplayError(str(exc)) from exc

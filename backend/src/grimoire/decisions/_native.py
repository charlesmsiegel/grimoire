"""Lower closed questions to native endpoints and lift only what they reported."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from . import _models, _reports, _structured
from ._models import Answer as Answer
from ._models import Choice as Choice
from ._models import Item as Item
from ._models import ItemResult as ItemResult
from ._models import MultiSelect as MultiSelect
from ._models import NativeQuestion as NativeQuestion
from ._models import Option as Option
from ._models import Question as Question
from ._models import Rank as Rank
from ._models import Score as Score

# --- native -----------------------------------------------------------------
#
# Slice H's native backends send one item per request to a provider's
# decisions endpoint. What both adapters share lives here, so the reserved
# none, the endpoint's limits and the reading of a report are one rule for
# both providers.

def native_choice_keys(q: Choice) -> tuple[tuple[str, str], ...]:
    """`(wire key, Grimoire key)` per option a native `choice` offers: each
    option as itself, in order, and with `allow_none` one more, the reserved
    none -- `NATIVE_NONE`, or the first of `none_2`, `none_3`, ... that no
    option id equals -- mapped to `NONE_KEY`. A one-option nullable choice is
    therefore a two-option native choice."""
    keys = tuple((opt.id, opt.id) for opt in q.options)
    if not q.allow_none:
        return keys
    taken = {opt.id for opt in q.options}
    wire, n = _models.NATIVE_NONE, 1
    while wire in taken:
        n += 1
        wire = f"{_models.NATIVE_NONE}_{n}"
    return (*keys, (wire, _models.NONE_KEY))


@dataclass(frozen=True)
class Lift:
    """How `native_lift` reads a lowered item back (`native_form`): per
    question of the ORIGINAL item, in order, its id and the ids of the
    lowered questions that answer it -- its own id for a question asked as
    itself or lowered to one question (a joint), and `f"{id}#{i}"` per
    candidate or option for one lowered pointwise (a rank, a multi-select)."""

    lowered: tuple[tuple[str, tuple[str, ...]], ...]


def _lowered_ids(q: Question) -> tuple[str, ...]:
    """The ids `q` is asked under natively: one predicate per candidate or
    option for a rank or a multi-select, else its own."""
    if isinstance(q, _models.Rank):
        return tuple(f"{q.id}#{i}" for i in range(len(q.candidates)))
    if isinstance(q, _models.MultiSelect):
        return tuple(f"{q.id}#{i}" for i in range(len(q.options)))
    return (q.id,)


def _asked_as(q: Question) -> tuple[Question, ...]:
    if isinstance(q, _models.Rank):
        if not q.pointwise:
            raise ValueError(f"rank {q.id!r} names no pointwise question")
        return tuple(_models.Predicate(qid, f"{q.pointwise}\n\nCandidate {opt.id}: {opt.description}")
                     for qid, opt in zip(_lowered_ids(q), q.candidates, strict=True))
    if isinstance(q, _models.MultiSelect):
        return tuple(_models.Predicate(qid, _models.NATIVE_SELECT_TEXT.format(
                         instructions=q.instructions, id=opt.id, description=opt.description))
                     for qid, opt in zip(_lowered_ids(q), q.options, strict=True))
    if isinstance(q, _models.Joint):
        return (q.choice,)
    return (q,)


def native_form(item: Item) -> tuple[Item, Lift]:
    """`item` as a decisions endpoint is asked it, and how to read the reply
    back (01e §3.3). Neither endpoint has the 01e kinds, so they are lowered
    to the three types both have; both adapters share this one lowering, as
    they share `native_choice_keys` and `native_result`:

    - a `Predicate`, `Choice` or `Score` passes through under its own id, so
      an item of those alone lowers to ITSELF and its body is unchanged;
    - a `Joint` becomes its one flattened `Choice` (`Joint.choice`), under
      its own id;
    - a `Rank` becomes one `Predicate` per candidate, worded with its
      `pointwise` question and the candidate, and a `MultiSelect` one per
      option, worded `NATIVE_SELECT_TEXT` -- each under `f"{id}#{i}"`, all in
      the ONE request: an endpoint answers each question alone, which is
      exactly a pointwise question.

    A rank with no `pointwise` has no lowering: `native_gap` refuses it
    unsent, before any adapter calls this, so meeting one here raises
    `ValueError`."""
    asked: list[Question] = []
    lift: list[tuple[str, tuple[str, ...]]] = []
    for q in item.questions:
        lowered = _asked_as(q)
        asked.extend(lowered)
        lift.append((q.id, tuple(low.id for low in lowered)))
    return _models.Item(item.context, tuple(asked)), Lift(tuple(lift))


def native_lift(item: Item, lowered: ItemResult, lift: Lift) -> ItemResult:
    """`lowered`, a decisions reply read against `native_form(item)`'s item,
    as `item`'s result: each original question's answer from the lowered
    answers `lift` names -- as it is for a question asked as itself; split
    back into a `Pair` for a joint (its distribution stays keyed by the
    flattened keys, the pair's one spelling); and built from its predicates'
    answers for a rank (`_lift_rank`) or a multi-select (`_lift_select`).
    The backend, the server and the (empty) rationale are the lowered
    result's."""
    by_id = dict(lift.lowered)
    answers: dict[str, Answer] = {}
    for q in item.questions:
        got = [lowered.answers[qid] for qid in by_id[q.id]]
        if isinstance(q, _models.Rank):
            answers[q.id] = _lift_rank(q, got)
        elif isinstance(q, _models.MultiSelect):
            answers[q.id] = _lift_select(q, got)
        else:
            (answered,) = got
            answers[q.id] = _structured._lift_joint(answered) if isinstance(q, _models.Joint) else answered
    return _models.ItemResult(answers, rationale=lowered.rationale, backend=lowered.backend,
                      served=lowered.served)


def _pointwise_read(options: Sequence[Option], got: Sequence[Answer]
                    ) -> tuple[dict[str, float] | None, Answer | None]:
    """What a pointwise lowering's predicates reported: the P(true) each
    candidate or option was given (`marginals`, by the caller's id; None when
    none was), and the answer the whole question reads as when they cannot be
    one -- `refused` when every predicate was refused, `unreadable` (the
    reported marginals riding on it) when any has no usable probability. Each
    predicate's `Answer` is read as `native_answer` produced it, and nothing
    here re-thresholds a raw probability."""
    marginals = {opt.id: a.probability for opt, a in zip(options, got, strict=True)
                 if a.probability is not None}
    reported = marginals or None
    if all(a.reason == "refused" for a in got):
        return reported, _models.Answer(None, "refused", marginals=reported)
    if len(marginals) < len(options):
        return reported, _models.Answer(None, "unreadable", marginals=reported)
    return reported, None


def _lift_rank(q: Rank, got: Sequence[Answer]) -> Answer:
    """A rank from its candidates' P(true): their `tiers` (`MASS_TIE`), so a
    tie is a tier and never broken, with every candidate placed (`rest` is
    empty, so `top` holds by construction). A P(true) of exactly 0.5 places
    its candidate like any other: a native rank never abstains, since no
    endpoint can say "cannot order these" in a predicate's answer. A
    candidate with no usable probability makes the rank `unreadable` -- a
    partial order is not the order asked for, and the missing place cannot
    be guessed."""
    marginals, failed = _pointwise_read(q.candidates, got)
    if failed is not None or marginals is None:
        return failed or _models.Answer(None, "unreadable")
    return _models.Answer(_models.Ranking(tiers=_reports.tiers(marginals), rest=()), marginals=marginals)


def _lift_select(q: MultiSelect, got: Sequence[Answer]) -> Answer:
    """A selection from its options' predicates: every option whose
    predicate answered True, in option order. An option at exactly 0.5 (its
    predicate `abstained`) cannot be stated without a stand-in: `abstained`
    with `allow_none`, else `unreadable` -- the rule a native choice's none
    keeps without `allow_none`. A selection outside `[min, max]` is
    `unreadable`, never repaired by taking the top `max` or padding to `min`
    (a caller that wants top-k by probability reads `marginals`)."""
    marginals, failed = _pointwise_read(q.options, got)
    if failed is not None or marginals is None:
        return failed or _models.Answer(None, "unreadable")
    if any(a.reason == "abstained" for a in got):
        reason = "abstained" if q.allow_none else "unreadable"
        return _models.Answer(None, reason, marginals=marginals)
    if any(not isinstance(a.answer, bool) for a in got):
        return _models.Answer(None, "unreadable", marginals=marginals)
    chosen = tuple(opt.id for opt, a in zip(q.options, got, strict=True) if a.answer)
    hi = len(q.options) if q.max is None else q.max
    if not q.min <= len(chosen) <= hi:
        return _models.Answer(None, "unreadable", marginals=marginals)
    return _models.Answer(chosen, marginals=marginals)


def native_questions(item: Item) -> tuple[NativeQuestion, ...]:
    """`item`'s questions as a decisions endpoint is asked them, each one a
    `NativeQuestion`: what both adapters iterate, so the type system holds
    that no other kind reaches a wire mapping. A question an endpoint cannot
    be asked raises `ValueError` -- `native_gap` refuses such an item unsent,
    before any adapter sees it, so this is a broken invariant, not a reply."""
    out: list[NativeQuestion] = []
    for q in item.questions:
        if not isinstance(q, (_models.Predicate, _models.Choice, _models.Score)):
            raise ValueError(f"question {q.id!r} is a {q.KIND}, which a decisions "
                             f"endpoint is never asked")
        out.append(q)
    return tuple(out)


#: What `native_key` hands over for an unoffered wire key spelled `NONE_KEY`.
_UNOFFERED: Final = object()


def native_key(keys: Mapping[str, str], wire: object) -> object:
    """A choice's wire key read back as Grimoire's, through `keys` (a
    `dict(native_choice_keys(q))`): an offered key as its option id (the
    reserved none as `NONE_KEY`), anything else as itself, so `native_answer`
    reads it as `not_an_option` and a distribution keyed by it as invalid.
    The one exception is an unoffered key spelled `NONE_KEY`, which would read
    as the reserved none: it goes as a value no option id and no distribution
    key can equal."""
    if isinstance(wire, str) and wire in keys:
        return keys[wire]
    return _UNOFFERED if wire == _models.NONE_KEY else wire


def native_result(item: Item, answers: Mapping[str, Answer], provider: str) -> ItemResult | None:
    """`item`'s result from one decisions reply: `answers` holds an `Answer`
    for each of its questions the reply answered, keyed by question id, and a
    question it left out is `unreadable` (one warning per call, naming
    `provider`). None when the reply answered none of them: that is not an
    answer but a failed call (ruling 4, I2), which the adapter raises as its
    own `bad_response` so the item falls through to the next stage."""
    missing = [q.id for q in item.questions if q.id not in answers]
    if len(missing) == len(item.questions):
        return None
    if missing:
        _models.log.warning("%s's decisions reply left %d of %d questions unanswered",
                    provider, len(missing), len(item.questions))
    return _models.ItemResult({q.id: answers.get(q.id, _models.Answer(None, "unreadable"))
                       for q in item.questions},
                      rationale="", backend=_models.NATIVE_BACKEND)


def native_gap(item: Item) -> str:
    """`""` when a decisions endpoint can carry `item`; otherwise one sentence
    naming what it cannot (ruling 25), which the native call refuses unsent.
    That is a choice -- or a joint's flattened choice -- whose options, with
    the reserved none, pass `NATIVE_MAX_OPTIONS`; a rank that names no
    `pointwise` question (there is no default wording: only the caller knows
    what per-candidate question its order stands for); and a lowered
    predicate's id (`f"{id}#{i}"`, `native_form`) that another question of
    the item already uses, which the endpoint would answer once for both. It
    reads the ORIGINAL item, so a sentence names the caller's question ids.
    These are native-only: a structured stage can still answer the item, so
    none of them is a `validate` rule. A limit is named here, never met by
    truncating."""
    asked: dict[str, str] = {}      # lowered id -> the question asked under it
    for q in item.questions:
        flat = q.choice if isinstance(q, _models.Joint) else q
        if isinstance(flat, _models.Choice) and (n := len(native_choice_keys(flat))) > _models.NATIVE_MAX_OPTIONS:
            return (f"Question {q.id} offers {n} options including none; "
                    f"a decisions endpoint takes at most {_models.NATIVE_MAX_OPTIONS}.")
        if isinstance(q, _models.Rank) and not q.pointwise:
            return (f"Question {q.id} ranks its candidates and names no pointwise "
                    f"question; a decisions endpoint cannot order them.")
        for qid in _lowered_ids(q):
            if qid in asked:
                # Question ids are unique (`validate`), so one side of the
                # collision is a lowered id: name the question it lowers.
                lowered = q.id if qid != q.id else asked[qid]
                return (f"Question {lowered} is asked natively as {qid}, which another "
                        f"question of the item already uses; a decisions endpoint "
                        f"would answer one of them for both.")
            asked[qid] = q.id
    return ""


def _legal_keys(q: NativeQuestion) -> set[str]:
    if isinstance(q, _models.Choice):
        return {opt.id for opt in q.options} | ({_models.NONE_KEY} if q.allow_none else set())
    if isinstance(q, _models.Score):
        return {str(i) for i in range(len(q.levels))}
    return set()


def _distribution(q: NativeQuestion, value: object) -> dict[str, float] | None:
    """`value` as `q`'s distribution, or None: a non-empty mapping whose every
    key is legal for `q` and whose every value is a probability. An invalid
    report is dropped whole, never repaired."""
    if not isinstance(value, Mapping) or not value:
        return None
    legal = _legal_keys(q)
    out: dict[str, float] = {}
    for key, weight in value.items():
        p = _models._probability(weight)
        if not isinstance(key, str) or key not in legal or p is None:
            return None
        out[key] = p
    return out


def _argmax(distribution: dict[str, float]) -> str | None:
    """The distribution's one most probable key; None on a tie at the top."""
    top = max(distribution.values())
    winners = [key for key, weight in distribution.items() if weight == top]
    return winners[0] if len(winners) == 1 else None


def native_answer(q: NativeQuestion, *, chosen: object = _models.UNSTATED, probability: object = None,
                  distribution: object = None, refused: bool = False) -> Answer:
    """A native endpoint's report for `q` as an `Answer`.

    The adapter hands over keys already mapped back to Grimoire's (option ids
    or `NONE_KEY`, level indices as `str(i)`), and never a provider's
    probability-weighted `score` as `chosen` (ruling 24). `refused` is
    `refused`. Otherwise `probability` is kept only when it is a probability
    and `distribution` only when it is valid for `q` (`_distribution`), and
    both ride on the answer, an answer of None included.

    - A predicate: a `bool` `chosen` is the answer, whatever the probability
      beside it says (M11); else P(true) above 0.5 is True, below is False,
      exactly 0.5 is `abstained`; with no usable probability, `unreadable`.
    - A choice: `chosen` an exact option id is that option (aliases are the
      structured parser's, §7.4); `NONE_KEY` or None is `abstained` with
      `allow_none` and `unreadable` without; any other value is `unreadable`
      with `NOT_AN_OPTION`, a string kept as its `stated`. With nothing chosen, the distribution's argmax --
      `abstained` on a tie or on `NONE_KEY` -- and with no usable
      distribution, `unreadable`.
    - A score: `chosen` an `int` index (never a bool or a float); else the
      distribution's argmax under the same tie rule; else `unreadable`. A
      valid distribution also gives the score its `expected` level
      (`_expected`), whatever the answer -- an abstained tie included.
    """
    if refused:
        return _models.Answer(None, "refused")
    p = _models._probability(probability)
    dist = _distribution(q, distribution)
    expected = None
    if isinstance(q, _models.Predicate):
        value, reason, detail = _native_predicate(chosen, p)
    elif isinstance(q, _models.Choice):
        value, reason, detail = _native_choice(q, chosen, dist)
    else:
        value, reason, detail = _native_score(q, chosen, dist)
        expected = _expected(dist)
    stated = chosen if detail == _models.NOT_AN_OPTION and isinstance(chosen, str) else ""
    return _models.Answer(value, reason, probability=p, distribution=dist, detail=detail,
                  stated=stated, expected=expected)


def _expected(dist: dict[str, float] | None) -> float | None:
    """A score distribution's probability-weighted level, `Σ i·p_i / Σ p_i`
    over the levels it reported (keyed `str(i)`, which `_distribution` has
    already checked): an omitted level is unreported rather than zero, so it
    adds to neither sum. None with no distribution or no mass. Computed here
    from the report rather than read from a provider's weighted `score`,
    which is never an answer (ruling 24), so one rule serves both providers."""
    if dist is None:
        return None
    mass = sum(dist.values())
    if mass <= 0:
        return None
    return sum(int(level) * p for level, p in dist.items()) / mass


#: `(answer, reason, detail)`: an `Answer` before its report rides on it.
_Read = tuple[bool | str | int | None, str, str]


def _native_predicate(chosen: object, p: float | None) -> _Read:
    if isinstance(chosen, bool):
        return chosen, "", ""
    if p is None:
        return None, "unreadable", ""
    if p == 0.5:
        return None, "abstained", ""
    return p > 0.5, "", ""


def _from_distribution(dist: dict[str, float] | None) -> tuple[str | None, str]:
    """`(key, "")` for the distribution's argmax; `(None, reason)` when there
    is no usable distribution (`unreadable`), or its top is a tie or the
    reserved none (`abstained`)."""
    if dist is None:
        return None, "unreadable"
    top = _argmax(dist)
    return (None, "abstained") if top in (None, _models.NONE_KEY) else (top, "")


def _native_choice(q: Choice, chosen: object, dist: dict[str, float] | None) -> _Read:
    if chosen is _models.UNSTATED:
        key, reason = _from_distribution(dist)
        return key, reason, ""
    if isinstance(chosen, str) and chosen in {opt.id for opt in q.options}:
        return chosen, "", ""
    if chosen is None or chosen == _models.NONE_KEY:
        return None, "abstained" if q.allow_none else "unreadable", ""
    return None, "unreadable", _models.NOT_AN_OPTION


def _native_score(q: Score, chosen: object, dist: dict[str, float] | None) -> _Read:
    if isinstance(chosen, int) and not isinstance(chosen, bool) and 0 <= chosen < len(q.levels):
        return chosen, "", ""
    key, reason = _from_distribution(dist)
    return (None if key is None else int(key)), reason, ""

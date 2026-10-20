"""Interpret reported probabilities and render decision outcomes without another call."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import replace
from typing import Any

from . import _models
from ._models import Answer as Answer
from ._models import Item as Item
from ._models import ItemResult as ItemResult
from ._models import Joint as Joint
from ._models import Trigger as Trigger


def tiers(values: Mapping[str, float | int], *,
          tolerance: float = _models.MASS_TIE) -> tuple[tuple[str, ...], ...]:
    """Keys grouped by value, highest first; keys whose values lie within
    `tolerance` of the group's first are tied, and a tier keeps the mapping's
    order. Never breaks a tie.

    Greedy from the top: a key joins the current tier when its value is
    within `tolerance` of that tier's HIGHEST value (inclusive, as
    `regrouped`'s tie is), so three values each just inside tolerance of the
    next cannot chain into one tier. Integer levels group the same way, so
    two candidates both answered level 7 are one tier: a coarse signal gives
    an honest partial order, never a fabricated strict one. A value that is
    not a finite real number cannot be placed, and raises `ValueError`
    rather than being placed somewhere silently."""
    order = {key: n for n, key in enumerate(values)}
    placed: list[tuple[float, str]] = []
    for key, value in values.items():
        number = _models._finite(value)
        if number is None:
            raise ValueError(f"{key!r} has no finite value to rank by: {value!r}")
        placed.append((number, key))
    placed.sort(key=lambda pair: (-pair[0], order[pair[1]]))
    out: list[list[str]] = []
    anchor = 0.0
    for number, key in placed:
        if out and anchor - number <= tolerance:
            out[-1].append(key)
        else:
            out.append([key])
            anchor = number
    return tuple(tuple(sorted(tier, key=order.__getitem__)) for tier in out)


def regrouped(answer: Answer, group: Callable[[str], str],
              order: Sequence[str]) -> str | None:
    """The group a choice's reported distribution puts the most mass on, when
    that is not the group of the option `answer` chose; None when it is (a
    tie at the top that includes it counts as it), and when there is no
    chosen option or no distribution -- a structured answer, which carries
    none, is read as chosen.

    For a choice that spells one meaning several ways (continuity's folded
    options: ``duplicate_a_into_b`` and ``duplicate_b_into_a``, or one
    ``existing:<id>`` per candidate), where an endpoint that scores options
    one by one splits that meaning's probability across its spellings: 0.3 on
    each of two and 0.4 on a rival is a 0.6 verdict that the per-option
    argmax, and an endpoint's own `choice`, both lose. `group` maps an option
    id to the meaning it spells; the reserved none is no group. A tie at the
    top that leaves the chosen group out goes to the group first in `order`
    (the caller's option order), then to the first reported."""
    if not isinstance(answer.answer, str) or not answer.distribution:
        return None
    mass: dict[str, float] = {}
    for key, weight in answer.distribution.items():
        if key != _models.NONE_KEY:
            mass[group(key)] = mass.get(group(key), 0.0) + weight
    if not mass:
        return None
    top = max(mass.values())
    winners = [g for g, weight in mass.items() if weight >= top - _models.MASS_TIE]
    if group(answer.answer) in winners:
        return None
    rank = {g: n for n, g in enumerate(order)}
    return min(winners, key=lambda g: rank.get(g, len(rank)))


def head_marginal(answer: Answer, q: Joint) -> dict[str, float] | None:
    """A joint answer's reported distribution summed per head, `NONE_KEY`
    excluded, in `q`'s head order (a head the report gave no pair of is
    absent, not 0: unreported is not zero); None without a distribution --
    a structured answer has none."""
    if not answer.distribution:
        return None
    mass: dict[str, float] = {}
    for key, weight in answer.distribution.items():
        if key != _models.NONE_KEY:
            head = _models.split_joint(key).head
            mass[head] = mass.get(head, 0.0) + weight
    rank = {head.id: n for n, head in enumerate(q.heads)}
    return dict(sorted(mass.items(), key=lambda kv: rank.get(kv[0], len(rank))))


def head_first(answer: Answer, q: Joint) -> str | None:
    """The head the reported distribution puts the most mass on, when that is
    not the chosen pair's head; None when it is, and when nothing was chosen
    or nothing was reported. `regrouped`'s rule -- sum by meaning, `NONE_KEY`
    excluded, a tie that includes the chosen head counting as it, another
    tie going to `q`'s head order -- applied to the pair's key, grouped by
    head. It exists for the reason `regrouped` does: an endpoint scoring
    pairs one by one splits an action's mass across its targets, so 0.3 on
    each of two strikes and 0.4 on one heal is a heal by the argmax and a
    strike by the action. Which reading to trust is the caller's policy."""
    if not isinstance(answer.answer, _models.Pair):
        return None
    return regrouped(replace(answer, answer=answer.answer.key),
                     lambda key: _models.split_joint(key).head, [head.id for head in q.heads])


def answer_key(answer: Answer) -> str | None:
    """The answer as a record spells its key (01c §6, 01d §5.1): a
    predicate's ``"true"``/``"false"``, a score level's ``str(level)``, a
    choice's option id, a joint pair's `key`; None for an answer of None (an
    abstention included: a record that spells one as `NONE_KEY` does so
    itself), a `Ranking` and a selection, which no single key names. A bool
    is read before an int, since `True` is an `int`."""
    value = answer.answer
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, _models.Pair):
        return value.key
    return None


def margin(answer: Answer) -> float | None:
    """How far the answer's own report puts the ANSWERED key above its best
    rival (01d §6.1), as a float: negative when the report ranks another key
    higher. None when the answer is None, or the report it needs is absent
    or empty -- a predicate's `probability`, a choice's, a score's or a
    joint's `distribution` -- and when any value in it is not a probability
    (a finite number in [0, 1], never a bool): `native_answer` never keeps
    such a report, and a hand-built one gets no margin rather than a
    non-finite one that `Trigger` would refuse. `marginals` are never read: they are independent
    per-key probabilities, not a distribution (01e-C4), so a rank and a
    selection have no margin.

    A predicate answered `a` with P(true) `p`: ``2 * p(a) - 1``, with
    ``p(a) = p`` for True and ``1 - p`` for False. A distribution `w` with
    mass ``M = math.fsum(w.values())`` (correctly rounded, so the same on
    every supported interpreter, which builtin `sum` over floats is not),
    ``w(a)`` the answered key's weight (0 when the report omits it) and `r`
    the largest other weight (0 when there is none; `NONE_KEY` is a rival):
    ``(w(a) - r) / M`` when ``M >= 1``, and ``w(a) - max(r, 1 - M)`` when
    ``M < 1`` -- mass the report left out could all be a rival's, so it may
    not be assumed otherwise. The keys are read raw, never regrouped (01d
    §11 Q1): mass split between two spellings of one meaning (two
    ``existing:<id>`` candidates) lowers the margin, as an ambiguity about
    which one."""
    value = answer.answer
    if value is None:
        return None
    if isinstance(value, bool):
        p = _models._probability(answer.probability)
        if p is None:
            return None
        return 2.0 * (p if value else 1.0 - p) - 1.0
    key = answer_key(answer)
    if key is None or not answer.distribution:
        return None
    weights: dict[str, float] = {}
    for k, w in answer.distribution.items():
        if (checked := _models._probability(w)) is None:
            return None
        weights[k] = checked
    mass = math.fsum(weights.values())
    own = weights.get(key, 0.0)
    rival = max((w for k, w in weights.items() if k != key), default=0.0)
    if mass >= 1.0:
        return (own - rival) / mass
    return own - max(rival, 1.0 - mass)


def _threshold(kind: str, value: object) -> float:
    if isinstance(value, bool) or (number := _models._finite(value)) is None or number <= 0:
        raise ValueError(f"the {kind!r} threshold is not a positive number: {value!r}")
    return number


def _filtered(key: str | None, answers: Collection[str]) -> bool:
    """Whether the answer filter lets `key` through: always when it is empty;
    else `key` equals an entry, or starts with one ending in ":"."""
    if not answers:
        return True
    if key is None:
        return False
    return any(key.startswith(entry) if entry.endswith(":") else key == entry
               for entry in answers)


def triggers(results: Sequence[ItemResult], *, question: str,
             escalate_on: Collection[str], margins: Mapping[str, float],
             answers: Collection[str] = ()) -> tuple[Trigger, ...]:
    """Each item whose `question` answer meets a trigger in `escalate_on`, in
    priority order: refused, then abstained, then low_margin by margin
    ascending; ties by index (01d §5.1, C2a). At most one per item, since an
    answer has one reason.

    - `refused` and `abstained` are the answer's `reason`.
    - `low_margin` is an answer that is not None, whose `margin` is not None,
      from an item whose server kind (`ItemResult.served[0]`) has an entry in
      `margins`, with ``margin < margins[kind] - MASS_TIE`` -- a margin float
      error put a hair under its threshold is on it, not under it. A kind
      with no entry, an unstamped item's included, never triggers: an
      unlisted endpoint keeps today's behaviour rather than a default
      threshold. A structured answer reports no margin, so on a structured
      model only an abstention can fire.
    - `answers`, when non-empty, narrows `low_margin` only: the answer's
      `answer_key` must equal an entry, or start with an entry that ends in
      ":" (a per-row option such as ``existing:<id>``). `refused` and
      `abstained` carry no answer to filter.
    - `unreadable` and `error` never trigger: the first is an answer read
      badly, which is never re-asked, the second the failure chain's.

    Pure. A trigger not in `TRIGGERS`, a threshold that is not a positive
    finite number, and a result with no answer to `question` are each a
    `ValueError`, and a bare `str` for `escalate_on` or `answers` (a
    collection of its characters) a `TypeError`: each a caller's mistake
    that would otherwise read as "nothing was unsure"."""
    for name, given in (("escalate_on", escalate_on), ("answers", answers)):
        if isinstance(given, str):
            raise TypeError(f"{name} is a collection of strings, not the string {given!r}")
    unknown = [name for name in escalate_on if name not in _models.TRIGGERS]
    if unknown:
        raise ValueError(f"unknown triggers {unknown!r}; known: {list(_models.TRIGGERS)!r}")
    limits = {kind: _threshold(kind, value) for kind, value in margins.items()}
    found: list[Trigger] = []
    for index, result in enumerate(results):
        if question not in result.answers:
            raise ValueError(f"item {index} has no answer to {question!r}")
        answer = result.answers[question]
        if answer.reason in ("refused", "abstained"):
            if answer.reason in escalate_on:
                found.append(_models.Trigger(index, answer.reason))
            continue
        if "low_margin" not in escalate_on or answer.answer is None:
            continue
        limit = limits.get(result.served[0]) if result.served else None
        lead = margin(answer)
        if (limit is not None and lead is not None and lead < limit - _models.MASS_TIE
                and _filtered(answer_key(answer), answers)):
            found.append(_models.Trigger(index, "low_margin", lead))
    return tuple(sorted(found, key=lambda t: (_models._PRIORITY[t.trigger],
                                              0.0 if t.margin is None else t.margin,
                                              t.index)))


def _present(record: dict[str, Any]) -> dict[str, Any]:
    """`record` without its keys whose value is empty or None."""
    return {key: value for key, value in record.items()
            if value is not None and value != "" and value != {} and value != []}


def _recorded(value: object) -> object:
    """An answer value as the capture writes it: JSON, and never ambiguous
    between two answer shapes."""
    if isinstance(value, _models.Ranking):
        return {"tiers": [list(tier) for tier in value.tiers], "rest": list(value.rest)}
    if isinstance(value, _models.Pair):
        return value.key                    # never a two-element list
    if isinstance(value, tuple):
        return list(value)                  # a selection; () as [], never null
    return value


def _rendered(value: object) -> object:
    """An answer value as a structured reply would have spelled it, or None
    where no reply could."""
    if isinstance(value, _models.Ranking):
        if any(len(tier) != 1 for tier in value.tiers):
            return None
        return [tier[0] for tier in value.tiers]
    if isinstance(value, _models.Pair):
        return value.key
    if isinstance(value, tuple):
        return list(value)                  # a selection; () as [], never null
    return value


def outcome(mode: str, provider: str, model: str,
            results: Sequence[ItemResult] | None = None, error: str = "") -> dict[str, Any]:
    """The capture's record of one call (spec §9.4): its mode and what served
    it, then each item's backend, normalised answers -- `answer`, always
    present (None as null; a `Ranking` as `{"tiers": [[...]], "rest": [...]}`;
    a `Pair` as its key; a selection as a list, `[]` included),
    with its `reason`, `detail`, `probability`, `distribution`, `marginals`
    and `expected` -- and rationale; or, on a failure, the `error`. A key
    whose value is empty or None is left out (an answer's own `answer`
    excepted)."""
    head = _present({"mode": mode, "provider": provider, "model": model})
    if error:
        return {**head, "error": error}
    items = []
    for result in results or ():
        answers = {
            qid: {"answer": _recorded(a.answer), **_present({
                "reason": a.reason, "detail": a.detail, "probability": a.probability,
                "distribution": dict(a.distribution) if a.distribution else None,
                "marginals": dict(a.marginals) if a.marginals else None,
                "expected": a.expected})}
            for qid, a in result.answers.items()}
        items.append(_present({"backend": result.backend, "answers": answers,
                               "rationale": result.rationale}))
    return {**head, "items": items}


def render(results: Sequence[ItemResult], items: Sequence[Item], *, explain: bool) -> str:
    """`results` written as the structured backend's reply to `items` --
    `{"0": {"answers": {qid: value}, "rationale": ...}}`, a None answer as
    JSON null and `rationale` only when `explain` -- so the structured
    graders can read a native `Decision` (`evals/run.py --live`). A selection
    is its list of ids (`[]` for the empty one, never null), a pair its key.
    A ranking
    of singleton tiers is its list of ids, which `parse` reads back to it; one
    with a tie is null, since no structured reply can say a tie (graders read
    the native result itself, so that null is never scored as an answer)."""
    out: dict[str, Any] = {}
    for index, (result, item) in enumerate(zip(results, items, strict=True)):
        entry: dict[str, Any] = {"answers": {
            q.id: (_rendered(result.answers[q.id].answer) if q.id in result.answers
                   else None)
            for q in item.questions}}
        if explain:
            entry["rationale"] = result.rationale
        out[str(index)] = entry
    return json.dumps(out)

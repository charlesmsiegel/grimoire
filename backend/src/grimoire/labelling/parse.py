"""A labeller's decoded reply, bounded into 029 factors and stakes, or refused (035 milestone 2).

The reply is model output: it names nothing, grants nothing and is trusted for nothing. It may cite only
the facts it was offered and choose their bearing; each factor's text, sequence and source are the fact's,
never the reply's. Stakes are the reply's own words, bounded, and only ever shown to people and recorded
as what a failure could bring. Anything else in the reply is refused whole, never repaired.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

from grimoire.adjudication import BEARINGS, MAX_FACTORS, MAX_STAKES, AdjudicationError, Factor, Stake

from .facts import MAX_STAKE_CHARS, NOVEL, Fact

#: Why a reply could not be read as labels.
LABEL_REFUSALS = ("unparseable", "unknown_fact", "repeated_fact", "invalid_bearing", "too_many_factors",
                  "too_many_stakes", "invalid_stake", "unknown_approach", "suggested_stakes")


class LabelRefused(ValueError):
    """A reply that is not a frame's labels. ``reason`` is one of ``LABEL_REFUSALS``."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(f"{reason}: {message}" if message else reason)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason,))


def parse_attempt(decoded: object, offered: Sequence[Fact], approaches: Collection[str]
                  ) -> tuple[str, tuple[Factor, ...], tuple[Stake, ...]]:
    """A decoded reply against a 040 obstacle: the approach it chose (a suggestion's id from ``approaches``, or
    ``NOVEL``), and its factors and stakes as ``parse_labels`` reads them. A reply that names no offered approach
    is ``unknown_approach``; one that chose a suggestion and still lists stakes is ``suggested_stakes`` (a
    suggestion risks exactly the author's stakes, which the runner adds)."""
    if not isinstance(decoded, Mapping) or set(decoded) - {"approach", "factors", "stakes"}:
        raise LabelRefused("unparseable", "the reply is an object with approach, factors and stakes only")
    approach = decoded.get("approach")
    if type(approach) is not str or (approach != NOVEL and approach not in approaches):
        raise LabelRefused("unknown_approach", f"the approach is a suggested one's id, or {NOVEL!r}")
    factors, stakes = parse_labels({key: value for key, value in decoded.items() if key != "approach"}, offered)
    if approach != NOVEL and stakes:
        raise LabelRefused("suggested_stakes", "a suggested approach risks the obstacle's own stakes, listed for it")
    return approach, factors, stakes


def parse_labels(decoded: object, offered: Sequence[Fact]) -> tuple[tuple[Factor, ...], tuple[Stake, ...]]:
    """The factors (sorted by id) and stakes (``s1``, ``s2``... in reply order) a decoded reply labels."""
    if not isinstance(decoded, Mapping) or set(decoded) - {"factors", "stakes"}:
        raise LabelRefused("unparseable", "the reply is an object with factors and stakes only")
    raw_factors = decoded.get("factors", [])
    raw_stakes = decoded.get("stakes", [])
    if type(raw_factors) is not list or type(raw_stakes) is not list:
        raise LabelRefused("unparseable", "factors and stakes are lists")
    if len(raw_factors) > MAX_FACTORS:
        raise LabelRefused("too_many_factors", f"at most {MAX_FACTORS} factors")
    if len(raw_stakes) > MAX_STAKES:
        raise LabelRefused("too_many_stakes", f"at most {MAX_STAKES} stakes")
    by_id = {fact.fact_id: fact for fact in offered}
    factors: dict[str, Factor] = {}
    for entry in raw_factors:
        if not isinstance(entry, Mapping) or set(entry) != {"fact", "bearing"}:
            raise LabelRefused("unparseable", "a factor is {\"fact\", \"bearing\"}")
        fact_id, bearing = entry["fact"], entry["bearing"]
        if type(fact_id) is not str or fact_id not in by_id:
            raise LabelRefused("unknown_fact", "a factor cites a fact that was not offered")
        if type(bearing) is not str or bearing not in BEARINGS:
            raise LabelRefused("invalid_bearing", f"a bearing is one of {sorted(BEARINGS)}")
        if fact_id in factors:
            raise LabelRefused("repeated_fact", "a fact is cited once")
        fact = by_id[fact_id]
        try:
            factors[fact_id] = Factor(fact_id, bearing, fact.text, fact.established, fact.source)
        except AdjudicationError:  # facts() offers only what 029 records; refuse rather than escape regardless
            raise LabelRefused("unknown_fact", "a factor cites a fact 029 cannot record") from None
    stakes: list[Stake] = []
    for entry in raw_stakes:
        text = entry.strip() if type(entry) is str else None
        if not text or len(text) > MAX_STAKE_CHARS:
            raise LabelRefused("invalid_stake", f"a stake is nonblank text of at most {MAX_STAKE_CHARS} characters")
        try:
            stakes.append(Stake(f"s{len(stakes) + 1}", text))
        except AdjudicationError:  # e.g. a lone surrogate: text that could never be stored
            raise LabelRefused("invalid_stake", "a stake is storable text") from None
    return tuple(factors[key] for key in sorted(factors)), tuple(stakes)


__all__ = ["LABEL_REFUSALS", "LabelRefused", "parse_attempt", "parse_labels"]

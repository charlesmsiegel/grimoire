"""The budgeted packer (031): ranked candidates in, a pack that fits the budget out.

It knows nothing about actors or sources; ``actor.py`` builds the candidates and decides who may
see them. Packing is greedy and deterministic: lanes in priority order, each lane in rank order,
first fit. A candidate is included when its counted line plus the per-item overhead fits both its
lane's remaining cap and the pack's remaining total; otherwise it is reported as omitted, and a
smaller candidate later in the same lane may still fit. The total never exceeds the budget.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .model import LANES, Budget, ClaimReading, ContextError, Omission, Origin, PackedItem, TokenCounter


@dataclass(frozen=True, slots=True)
class Candidate:
    """Something that may go into a pack. ``rank`` sorts within the lane, lowest first, and must be unique."""

    lane: str
    rank: tuple[object, ...]
    line: str
    origin: Origin
    stance: str | None = None
    sequence: int | None = None
    claims: tuple[ClaimReading, ...] = ()


def _one_line(text: str) -> str:
    """``text`` as one line: every line break (``\\n``, ``\\r``, U+2028 ...) becomes a space, so an item
    can never forge another item's line in a render."""
    if "\n" not in text and text.isprintable():
        return text
    return " ".join(part.strip() for part in text.splitlines() if part.strip())


def _cost(count: TokenCounter, line: str, overhead: int) -> int:
    tokens = count(line)
    if type(tokens) is not int or tokens < 0:
        raise ContextError("the token counter must return a non-negative int", "invalid_count")
    return tokens + overhead


def pack(candidates: Iterable[Candidate], budget: Budget,
         count: TokenCounter) -> tuple[tuple[PackedItem, ...], tuple[Omission, ...]]:
    """Fit ``candidates`` into ``budget``; returns what was included and what was shed, both in order."""
    by_lane: dict[str, list[Candidate]] = {lane: [] for lane in LANES}
    for candidate in candidates:
        if not isinstance(candidate, Candidate) or type(candidate.lane) is not str or candidate.lane not in by_lane:
            raise ContextError(f"a candidate needs one of the lanes {LANES}", "invalid_lane")
        by_lane[candidate.lane].append(candidate)
    remaining = budget.max_tokens
    items: list[PackedItem] = []
    omitted: list[Omission] = []
    for lane in LANES:
        lane_left = budget.cap(lane)
        for candidate in sorted(by_lane[lane], key=lambda c: c.rank):
            line = _one_line(candidate.line)
            tokens = _cost(count, line, budget.item_overhead)
            if tokens <= lane_left and tokens <= remaining:
                items.append(PackedItem(lane, line, tokens, candidate.origin, candidate.stance,
                                        candidate.sequence, candidate.claims))
                lane_left -= tokens
                remaining -= tokens
            else:
                reason = "lane_cap" if tokens <= remaining else "over_budget"
                omitted.append(Omission(lane, candidate.origin, reason, tokens))
    return tuple(items), tuple(omitted)


__all__ = ["Candidate", "pack"]

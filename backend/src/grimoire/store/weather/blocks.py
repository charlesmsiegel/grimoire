"""Blocks: which slice of a day a moment falls in, and its index in the field.

Two coordinates, deliberately separate. A moment picks its block by wall-clock
minute, which is what keeps `night` contiguous across midnight. The noise field
is indexed by a *consecutive ordinal*, because `persistence` is defined as the
correlation between indices one apart — and consecutive blocks are 240-480
minutes apart, so indexing by minute would leave every block independent while
every distribution still looked correct.
"""

from __future__ import annotations

POSITIONS = ("dawn", "morning", "afternoon", "evening", "night")

# (start minute, position index), ascending; `night` wraps past midnight and is
# handled separately below.
_DAY_BLOCKS = ((4 * 60, 0), (8 * 60, 1), (12 * 60, 2), (17 * 60, 3), (21 * 60, 4))

_NIGHT = POSITIONS.index("night")
_DEFAULT = POSITIONS.index("afternoon")  # the block containing midday


def block_of(fixed_day: int, minutes: int | None) -> tuple[int, int]:
    """(owning fixed day, position index) for a moment.

    A scene with a date but no clock resolves to `afternoon` — stable and
    unsurprising, rather than whatever the zero minute would give.
    """
    if minutes is None:
        return fixed_day, _DEFAULT
    if minutes < _DAY_BLOCKS[0][0]:
        return fixed_day - 1, _NIGHT  # 00:00-03:59 is the previous date's night
    position = _DAY_BLOCKS[0][1]
    for start, index in _DAY_BLOCKS:
        if minutes >= start:
            position = index
    return fixed_day, position


def ordinal(fixed_day: int, minutes: int | None) -> int:
    """The block's index in the noise field. Consecutive blocks differ by 1."""
    day, position = block_of(fixed_day, minutes)
    return 5 * day + position

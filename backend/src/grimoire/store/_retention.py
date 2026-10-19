"""Bound accumulated history without discarding the batch just written."""

from __future__ import annotations

import json


def trim(entries: list[dict], keep: int, *, rows: int, max_bytes: int) -> list[dict]:
    """Drop the oldest entries until both caps hold, never below `keep`.

    `keep` is what this append just wrote. A single row can exceed `max_bytes`
    on its own -- one absorb rewriting a very long lore body -- and trimming to
    satisfy the cap would then delete the entry for the write that just
    happened, leaving that change with no history at all. The cap is a bound on
    accumulation, not a licence to discard the present, so it yields.

    Sizes are measured once per entry rather than by re-serializing the list on
    each pop, which would be quadratic in exactly the case that reaches here.
    """
    # The floor applies to the row cap too, not only to the byte cap below. A
    # commit with more edits than `rows` would otherwise have this trim
    # away rows of the write it is in the middle of recording -- the same thing
    # the byte cap is careful not to do, so the two must not disagree.
    room = max(rows, keep)
    entries = entries[-room:] if len(entries) > room else entries
    sizes = [len(json.dumps(e, default=str)) for e in entries]
    total, first = sum(sizes), 0
    while total > max_bytes and len(entries) - first > keep:
        total -= sizes[first]
        first += 1
    return entries[first:]

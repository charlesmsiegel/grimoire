"""Dice notation parser + seeded roll engine (Mechanics & Dice Phase 2, #824).

Grammar (case-insensitive; spaces allowed between clauses):

    [N]dM [khn|kln|dhn|dln] [!] [+k|-k] [tN | vs N]

Sum rolls: total = kept dice + modifier; `vs N` grades success/failure.
Pool rolls (`tN`): count kept dice >= N as successes; modifiers are rejected.
Exploding (`!`): a max-face die rolls again — sum rolls chain the extra onto
the same die's value, pool rolls append a new die to the pool.

Pure module: no filesystem access. Every roll records its integer seed and
`roll(notation, seed)` replays it exactly (rolls.py builds replay on this).
"""

from __future__ import annotations

import random
import re
import secrets

MAX_DICE = 100
MAX_SIDES = 1000
MAX_EXPLOSIONS = 100  # per roll — bounds hostile notations without changing honest ones


class DiceError(ValueError):
    """Unparseable or out-of-range dice notation."""


_GRAMMAR = re.compile(
    r"^(?P<count>\d*)d(?P<sides>\d+)"
    r"(?:\s*(?P<keep>kh|kl|dh|dl)(?P<keep_n>\d+))?"
    r"(?:\s*(?P<explode>!))?"
    r"(?:\s*(?P<mod_sign>[+-])\s*(?P<mod>\d+))?"
    r"(?:\s*(?:t(?P<pool>\d+)|vs\s*(?P<vs>\d+)))?$"
)


def parse(notation: str) -> dict:
    m = _GRAMMAR.match(notation.strip().lower())
    if not m:
        raise DiceError(
            f"can't read dice notation {notation.strip()!r} — try 2d6+3, 4d6kh3, 7d10t6, or 5d6!")
    count = int(m["count"] or 1)
    sides = int(m["sides"])
    if not 1 <= count <= MAX_DICE:
        raise DiceError(f"dice count must be 1-{MAX_DICE}")
    if not 2 <= sides <= MAX_SIDES:
        raise DiceError(f"dice must have 2-{MAX_SIDES} sides")
    keep = (m["keep"], int(m["keep_n"])) if m["keep"] else None
    if keep is not None:
        op, n = keep
        if n < 1:
            raise DiceError("keep/drop count must be at least 1")
        if op in ("kh", "kl") and n > count:
            raise DiceError(f"can't keep {n} of {count} dice")
        if op in ("dh", "dl") and n >= count:
            raise DiceError("can't drop every die")
    modifier = int(m["mod"]) * (-1 if m["mod_sign"] == "-" else 1) if m["mod"] else 0
    pool = int(m["pool"]) if m["pool"] else None
    if pool is not None and modifier:
        raise DiceError("pool rolls (tN) don't take a +/- modifier")
    return {"count": count, "sides": sides, "keep": keep, "explode": bool(m["explode"]),
            "modifier": modifier, "pool": pool, "vs": int(m["vs"]) if m["vs"] else None}

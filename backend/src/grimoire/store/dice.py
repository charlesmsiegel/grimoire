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


def _roll_dice(rng, spec: dict) -> list[dict]:
    """Roll the dice for a parsed spec. Sum explosions chain onto the die that
    exploded (its value is the chain sum); pool explosions append a new die.
    A shared per-roll budget bounds explosion count."""
    sides, explode = spec["sides"], spec["explode"]
    is_pool = spec["pool"] is not None
    budget = MAX_EXPLOSIONS
    dice_out: list[dict] = []
    to_roll = spec["count"]
    while to_roll:
        to_roll -= 1
        chain = [rng.randint(1, sides)]
        if is_pool:
            if explode and chain[-1] == sides and budget:
                budget -= 1
                to_roll += 1
        else:
            while explode and chain[-1] == sides and budget:
                budget -= 1
                chain.append(rng.randint(1, sides))
        dice_out.append({"value": sum(chain), "rolls": chain, "kept": True})
    return dice_out


def _apply_keep(dice_out: list[dict], keep: tuple[str, int] | None) -> None:
    if keep is None:
        return
    op, n = keep
    order = sorted(range(len(dice_out)), key=lambda i: dice_out[i]["value"])
    if op == "kh":
        dropped = order[:-n]
    elif op == "kl":
        dropped = order[n:]
    elif op == "dh":
        dropped = order[len(dice_out) - n:]
    else:  # dl
        dropped = order[:n]
    for i in dropped:
        dice_out[i]["kept"] = False


def roll(notation: str, seed: int | None = None) -> dict:
    """Resolve `notation`. Omitted seed is drawn from the OS CSPRNG (secrets),
    then all dice come from one random.Random(seed) — reproducible by design."""
    spec = parse(notation)
    if seed is None:
        seed = secrets.randbits(64)
    rng = random.Random(seed)
    dice_out = _roll_dice(rng, spec)
    _apply_keep(dice_out, spec["keep"])
    result = {"notation": notation.strip(), "seed": seed, "dice": dice_out,
              "modifier": spec["modifier"], "pool_target": spec["pool"], "vs": spec["vs"],
              "total": None, "successes": None, "outcome": None}
    if spec["pool"] is not None:
        result["successes"] = sum(
            1 for d in dice_out if d["kept"] and d["value"] >= spec["pool"])
    else:
        result["total"] = sum(d["value"] for d in dice_out if d["kept"]) + spec["modifier"]
        if spec["vs"] is not None:
            result["outcome"] = "success" if result["total"] >= spec["vs"] else "failure"
    return result


def _face(d: dict) -> str:
    marker = "!" if len(d["rolls"]) > 1 else ""
    face = f"{d['value']}{marker}"
    return face if d["kept"] else f"~~{face}~~"


def format_roll(result: dict, label: str | None = None) -> str:
    """One markdown transcript line: dropped dice struck through, sum-exploded
    dice marked `!`, outcome bolded."""
    faces = ", ".join(_face(d) for d in result["dice"])
    head = (f"\U0001F3B2 **{label}** · `{result['notation']}`" if label
            else f"\U0001F3B2 `{result['notation']}`")
    if result["successes"] is not None:
        n = result["successes"]
        return f"{head} → [{faces}] — **{n} success{'' if n == 1 else 'es'}**"
    line = f"{head} → [{faces}]"
    if result["modifier"]:
        line += f" {'+' if result['modifier'] > 0 else '−'} {abs(result['modifier'])}"
    line += f" = **{result['total']}**"
    if result["vs"] is not None:
        line += f" vs {result['vs']} — **{result['outcome']}**"
    return line

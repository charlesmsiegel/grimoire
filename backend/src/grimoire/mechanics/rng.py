"""Deterministic per-campaign RNG seed derivation.

A campaign carries one ``rng_seed``. Mechanics modules need a stable
per-roll seed so a replay produces the same dice; mixing the campaign
seed with the roll's own ``seed`` (set by the Orchestrator when the roll
is created) gives forks that preserve outcomes while still producing
independent results across rolls within a campaign.
"""

from __future__ import annotations

import hashlib

INT64_MASK = 0x7FFFFFFFFFFFFFFF


def derive_roll_seed(campaign_seed: int, roll_seed: int, roll_id: str | None = None) -> int:
    """Return a stable seed for a single roll in a campaign.

    Combines ``campaign_seed`` with the roll's own seed (and, optionally, its
    id) via SHA-256 so changing any input changes the result, but the same
    inputs always yield the same output. Returns a non-negative 63-bit int.
    """
    h = hashlib.sha256()
    h.update(b"grimoire.mechanics.rng/v1\n")
    h.update(f"{int(campaign_seed)}\n".encode())
    h.update(f"{int(roll_seed)}\n".encode())
    if roll_id is not None:
        h.update(roll_id.encode("utf-8"))
    digest = h.digest()
    return int.from_bytes(digest[:8], "big", signed=False) & INT64_MASK


__all__ = ["INT64_MASK", "derive_roll_seed"]

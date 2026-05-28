"""Speaker selection for per_character_multi_call mode."""

from __future__ import annotations

import random


def parse_speaker_ref(raw: str, present_refs: list[str]) -> str | None:
    cleaned = raw.strip()
    if cleaned in present_refs:
        return cleaned
    return None


def select_fallback_speaker(
    present_refs: list[str],
    recent_speakers: list[str],
    rng: random.Random,
) -> str:
    if len(present_refs) == 1:
        return present_refs[0]

    spoken = set(recent_speakers)
    unspoken = [r for r in present_refs if r not in spoken]
    if unspoken:
        return rng.choice(unspoken)

    # All have spoken — pick whoever spoke least recently.
    last_index: dict[str, int] = {}
    for i, ref in enumerate(reversed(recent_speakers)):
        if ref in set(present_refs):
            last_index.setdefault(ref, i)
    if last_index:
        return max(last_index, key=lambda r: last_index[r])

    return rng.choice(present_refs)

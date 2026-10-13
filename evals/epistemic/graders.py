"""The offline graders (spec 11 §8.2): a forbidden needle in no message of
the reader's prompt, an allowed one somewhere in it, and a placed one in its
section and no other history section."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def leaks(messages: Sequence[dict], forbidden: Sequence[str]) -> list[str]:
    """Every forbidden needle that appears in any message."""
    return [n for n in forbidden if any(n in str(m.get("content", "")) for m in messages)]


def missing(messages: Sequence[dict], allowed: Sequence[str]) -> list[str]:
    """Every allowed needle that appears in no message."""
    return [n for n in allowed if not any(n in str(m.get("content", "")) for m in messages)]


def misplaced(sections: Mapping[str, str], placed: Mapping[str, str]) -> list[str]:
    """Every placed needle not in its section, or also in another one."""
    out = []
    for needle, home in placed.items():
        holders = {sid for sid, text in sections.items() if needle in text}
        if holders != {home}:
            out.append(f"{needle} in {sorted(holders) or 'nothing'}, wanted {home}")
    return out

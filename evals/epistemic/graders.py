"""The offline graders (spec 11 §8.2): a forbidden needle in no message of
the reader's prompt, an allowed one somewhere in it, and a placed one in its
section and no other history section."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

#: A planted needle: an invented token, so a reply carrying one used the
#: planted text. The live graders read nothing else -- a title or a heading
#: phrase is something a reply can say honestly.
TOKEN = re.compile(r"\b[A-Z]+NEEDLE\d+\b")


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


def tokens(text: str) -> set[str]:
    return set(TOKEN.findall(text))


def needles(values: Iterable[str]) -> tuple[str, ...]:
    """The planted tokens among a check's needles, in order."""
    return tuple(v for v in values if TOKEN.fullmatch(v))


def reply_leaks(reply: str, exposed: Iterable[str]) -> list[str]:
    """The live-forbidden tokens the prompt held that the reply repeats."""
    used = tokens(reply)
    return [n for n in exposed if n in used]


def contamination(reply: str, unseen: Iterable[str]) -> list[str]:
    """Forbidden tokens in a reply whose prompt never held them: the
    harness's fault (a question or fixture carrying one), not the model's."""
    used = tokens(reply)
    return [n for n in unseen if n in used]


def recall(reply: str, allowed: Sequence[str]) -> float | None:
    """The share of `allowed` tokens the reply uses; None with none to use."""
    if not allowed:
        return None
    used = tokens(reply)
    return sum(n in used for n in allowed) / len(allowed)

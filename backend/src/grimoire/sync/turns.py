"""What a 035 played turn needs on a device before sync lands it there (028's turn merge, docs/SYNC.md).

A turn is a mutable record changed by compare-and-swap, so it follows the ordinary decision rows. It is one
turn of one chat's tree (``grimoire.scene.branches``), so beside what any campaign record needs (the actors it
names that the origin holds, the revisions its lines pin), a turn never lands without its chat record and the
campaign's play scope (a turn is history of one chat, and a later chat given that id would inherit it), the
turn it was played after (``follows``), and the turn a set-aside mark names as going on instead (``chosen``):
without them the receiver could not place it, and would read a gap or a choice that names nothing. These are
needed on the receiver whether or not the origin still holds them. Read structurally from the payload,
without decoding the turn (an unreadable payload is never copied: the scan blocks it).
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.store.replica import parse_exact

from .documents import syncable


def turn_needs(path: str, data: bytes | None) -> list[str]:
    """The paths a turn at ``path`` needs on its receiver (nothing for any other record)."""
    if data is None or syncable(path) != "turn":
        return []
    parts = path.split("/")
    campaign = parts[1]
    payload = parse_exact(tuple(parts), data).payload  # type: ignore[union-attr]
    needs = [f"campaigns/{campaign}/scope.json"]
    chat = payload.get("chat") if isinstance(payload, Mapping) else None
    if isinstance(chat, str):
        needs.append(f"campaigns/{campaign}/chats/{chat}.json")
    mark = payload.get("set_aside") if isinstance(payload, Mapping) else None
    for turn in (payload.get("follows") if isinstance(payload, Mapping) else None,
                 mark.get("chosen") if isinstance(mark, Mapping) else None):
        if isinstance(turn, str):
            needs.append(f"campaigns/{campaign}/turns/{turn}.json")
    return [need for need in needs if need != path]


__all__ = ["turn_needs"]

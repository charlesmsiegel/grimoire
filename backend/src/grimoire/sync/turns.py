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

from grimoire.core.identity import ChatRef, ScopeRef, TurnRef
from grimoire.store import UnstorableIdentifier
from grimoire.store.layout import chat_parts, scope_parts, turn_parts
from grimoire.store.replica import parse_exact

from .documents import syncable


def turn_needs(path: str, data: bytes | None) -> list[str]:
    """The paths a turn at ``path`` needs on its receiver (nothing for any other record)."""
    if data is None or syncable(path) != "turn":
        return []
    parts = path.split("/")
    campaign = parts[1]
    envelope = parse_exact(tuple(parts), data)
    payload = getattr(envelope, "payload", None)
    if not isinstance(payload, Mapping):
        return []
    needs = [_spell(lambda: scope_parts(ScopeRef(campaign)))]
    chat = payload.get("chat")
    if isinstance(chat, str):
        needs.append(_spell(lambda: chat_parts(ChatRef(campaign, chat))))
    mark = payload.get("set_aside")
    for turn in (payload.get("follows"), mark.get("chosen") if isinstance(mark, Mapping) else None):
        if isinstance(turn, str):
            needs.append(_spell(lambda turn=turn: turn_parts(TurnRef(campaign, turn))))
    # text that spells no path a library can store names nothing (as for 107's named actors): the turn itself is
    # then one this version cannot decode, which the receiver reports as a damaged turn of that chat
    return [need for need in needs if need is not None and need != path]


def _spell(parts) -> str | None:  # type: ignore[no-untyped-def]
    try:
        return "/".join(parts())
    except (ValueError, TypeError, UnstorableIdentifier):
        return None


__all__ = ["turn_needs"]

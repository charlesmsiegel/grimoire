"""What a 035 played turn needs on a device before sync lands it there (028's turn merge, docs/SYNC.md).

A turn is a mutable record changed by compare-and-swap, so it follows the ordinary decision rows. It is one
turn of one chat's tree (``grimoire.scene.branches``), so beside what any campaign record needs (the actors it
names that the origin holds, the revisions its lines pin), a turn never lands without its chat record and the
campaign's play scope (a turn is history of one chat, and a later chat given that id would inherit it), the
turn it was played after (``follows``), and the turn a set-aside mark names as going on instead (``chosen``):
without them the receiver could not place it, and would read a gap or a choice that names nothing. These are
needed on the receiver whether or not the origin still holds them. A turn written before ``follows`` existed
is placed by the turn before it of its own device, else the untagged one (``branches.parent``): those, when the
origin holds them, are needed too (``turn_named``), so it never lands after another device's turn of that
number. Read structurally from the payload, without decoding the turn (an unreadable payload is never copied:
the scan blocks it).
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from grimoire.core.identity import ChatRef, ScopeRef, TurnRef
from grimoire.store import UnstorableIdentifier
from grimoire.store.layout import chat_parts, scope_parts, turn_parts
from grimoire.store.replica import parse_exact

from .documents import syncable

_TURN_NAME = re.compile(r"(?P<chat>.+)\.(?P<number>[0-9]{6})(?:-(?P<device>[0-9a-f]{8}))?\.json")


def _payload(path: str, data: bytes | None) -> Mapping | None:  # type: ignore[type-arg]
    if data is None or syncable(path) != "turn":
        return None
    payload = getattr(parse_exact(tuple(path.split("/")), data), "payload", None)
    return payload if isinstance(payload, Mapping) else None


def turn_needs(path: str, data: bytes | None) -> list[str]:
    """The paths a turn at ``path`` needs on its receiver (nothing for any other record)."""
    payload = _payload(path, data)
    if payload is None:
        return []
    campaign = path.split("/")[1]
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


def turn_named(path: str, data: bytes | None) -> list[str]:
    """For a turn written before ``follows`` existed, the turns it would be placed after on the receiver: the
    one before it of its own device tag and the untagged one, needed there when the origin holds them."""
    payload = _payload(path, data)
    found = _TURN_NAME.fullmatch(path.rsplit("/", 1)[-1])
    if payload is None or found is None or payload.get("follows") is not None or int(found["number"]) < 2:
        return []
    folder = path.rsplit("/", 1)[0]
    before = f"{folder}/{found['chat']}.{int(found['number']) - 1:06d}"
    return sorted({f"{before}.json", f"{before}{'' if found['device'] is None else '-' + found['device']}.json"})


def _spell(parts) -> str | None:  # type: ignore[no-untyped-def]
    try:
        return "/".join(parts())
    except (ValueError, TypeError, UnstorableIdentifier):
        return None


__all__ = ["turn_named", "turn_needs"]

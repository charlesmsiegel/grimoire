"""Who the player last played with a card, and the lorebooks bound then: the ``last`` of ``play.options``.

Derived on every call from the chats the selection is already in, as the ``chats`` op lists them (107's navigation
index and each chat's own record): nothing is stored, so there is nothing to sync or migrate, and a chat a sync
brought from another device counts as soon as it is here. No model runs and no transcript is read. A source is
matched by its qualified reference, never by its name.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from grimoire.core.identity import SourceRef


def _usable(store, entry: Mapping[str, object], kind: str) -> dict | None:  # type: ignore[no-untyped-def]
    """``entry`` (one of a listed chat's ``sources``) as Start Chat could bind it now, or ``None``: 052 pins a
    persona or lorebook with this same check, so only what a start would accept is offered."""
    name = entry.get("name")
    try:
        ref = SourceRef.parse(str(entry.get("ref")))
    except (ValueError, TypeError):
        return None
    if ref.kind != kind or type(name) is not str or isinstance(store.check_definition(ref), str):
        return None
    return {"ref": str(ref.unpinned()), "name": name}


def last_played(store, launched: SourceRef, related: Iterable[Mapping[str, object]]) -> dict | None:  # type: ignore[no-untyped-def]
    """The newest of ``related`` (chat entries, newest first) that began from ``launched`` and says who was played.

    A chat with a problem (undecodable, or its scope gone) is passed over, as is one started from something else
    that this card was added to, and one with no persona bound: none of them says who the player played with
    this card. The first chat that does say decides alone: if its persona cannot be bound now (its card was
    deleted or excluded) the answer is ``None``, never an older chat's persona. Its lorebooks are the ones still
    bound in it that can be bound now."""
    wanted = str(launched.unpinned())
    for chat in related:
        launch = chat.get("launch")
        if chat.get("problem") is not None or not isinstance(launch, Mapping) or launch.get("ref") is None:
            continue
        try:
            began = str(SourceRef.parse(str(launch["ref"])).unpinned())
        except (ValueError, TypeError):
            continue
        bound = chat.get("sources")
        sources = [entry for entry in (bound if isinstance(bound, list) else []) if isinstance(entry, Mapping)]
        personas = [entry for entry in sources if entry.get("role") == "persona"]
        if began != wanted or not personas:
            continue
        persona = _usable(store, personas[0], "character")
        if persona is None:
            return None
        lorebooks = [found for entry in sources if entry.get("role") == "lorebook"
                     for found in [_usable(store, entry, "lorebook")] if found is not None]
        return {"chat": chat.get("chat"), "persona": persona, "lorebooks": lorebooks,
                "when": chat.get("last_activity")}
    return None


__all__ = ["last_played"]

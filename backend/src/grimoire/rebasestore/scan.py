"""Raw scans of stored payloads for an actor's id (042).

History is reported, never interpreted: a rebase or a canon proposal lists what names the actor so a
human sees what it keeps, and writes none of it. The scan is raw, like 107's id reservation, so a
record shape this version does not know still counts when it names the actor.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.identity import InstanceRef

#: nesting this scan follows; stored payloads are far shallower, and a deeper one is not searched
MAX_DEPTH = 64


def names_instance(payload: object, actor: InstanceRef) -> bool:
    """Whether any string in ``payload`` (keys included) is an instance ref of ``actor``'s campaign
    with ``actor``'s instance id, whatever kind it spells (a kind change keeps the id taken)."""
    stack: list[tuple[object, int]] = [(payload, 0)]
    while stack:
        value, depth = stack.pop()
        if isinstance(value, str):
            try:
                ref = InstanceRef.parse(value)
            except ValueError:
                continue
            if ref.campaign_id == actor.campaign_id and ref.instance_id == actor.instance_id:
                return True
        elif depth < MAX_DEPTH and isinstance(value, Mapping):
            stack.extend((item, depth + 1) for item in (*value.keys(), *value.values()))
        elif depth < MAX_DEPTH and isinstance(value, (list, tuple)):
            stack.extend((item, depth + 1) for item in value)
    return False


__all__ = ["names_instance"]

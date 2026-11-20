"""The instance ids a campaign's 077 commitment ledger names, for ``grimoire.chatstore.campaign_taken_ids``.

A commitment names its obligors and the parties owed by reference, and every decision snapshots the obligors'
actor ids. A new individual given a hand-deleted character's id would inherit that character's promises, so
these ids stay reserved. The ledger is scanned raw, with no decode, so a ledger this build cannot read still
protects its ids. A scope record the store cannot read raises ``TakenIdsUnlisted``: allocation fails closed.
"""
from __future__ import annotations

from collections.abc import Mapping

from grimoire.commitments.codec import COMMITMENTS_KEY
from grimoire.core.identity import InstanceRef, ScopeRef, validate_segment
from grimoire.store import LibraryStore, RecordNotFound, StoreError, TakenIdsUnlisted
from grimoire.store.layout import address_parts

_ACTOR_KEYS = ("actor_id",)
_REF_KEYS = ("owed_by", "owed_to")


def commitment_party_ids(store: LibraryStore, campaign_id: str) -> frozenset[str]:
    validate_segment(campaign_id, "campaign")
    scope = ScopeRef(campaign_id)
    try:
        stored = store.read(scope)
    except RecordNotFound:
        return frozenset()
    except StoreError:
        path = "/".join(address_parts(scope))
        raise TakenIdsUnlisted(f"the scope record of {campaign_id} cannot be read, so the ids its commitments "
                               "name cannot be reserved", (path,)) from None
    taken: set[str] = set()
    stack: list[object] = [stored.record.payload.get(COMMITMENTS_KEY)]
    while stack:
        value = stack.pop()
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key in _REF_KEYS and isinstance(item, (list, tuple)):
                    for text in item:
                        if isinstance(text, str):
                            try:
                                ref = InstanceRef.parse(text)
                            except ValueError:
                                continue
                            if ref.campaign_id == campaign_id:
                                taken.add(ref.instance_id)
                elif key in _ACTOR_KEYS and isinstance(item, str):
                    taken.add(item)
                elif key == "controls" and isinstance(item, (list, tuple)):
                    taken |= {pair[0] for pair in item
                              if isinstance(pair, (list, tuple)) and pair and isinstance(pair[0], str)}
                stack.append(item)
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
    return frozenset(taken)

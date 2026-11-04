"""A synthetic campaign and a deterministic relation library for Sprint 017 tests (no real library)."""

from __future__ import annotations

from grimoire.core.entity import EntityInstance
from grimoire.core.entity_codec import ENTITY_SCHEMA, instance_to_payload
from grimoire.core.identity import InstanceRef
from grimoire.graphstore import RelationLibrary
from grimoire.store import LibraryStore, RecordEnvelope

from chat_fixtures import Clock, Ids
from store_helpers import OWNER, owner_handle

C = "c"


def actor(name: str, kind: str = "character") -> InstanceRef:
    return InstanceRef(C, kind, name)


WREN, TOMAS, INES, ORRIN = (actor(n) for n in ("wren", "tomas", "ines", "orrin"))
GUILD, HARBOR, LANTERN = actor("ashen-guild", "organization"), actor("harbor", "place"), actor("lantern", "item")
EVERYONE = (WREN, TOMAS, INES, ORRIN, GUILD, HARBOR, LANTERN)


def instance_record(ref: InstanceRef) -> RecordEnvelope:
    entity = EntityInstance(ref, local_name=ref.instance_id.replace("-", " ").title())
    return RecordEnvelope(ref, instance_to_payload(entity), owner=OWNER, extra={"schema": ENTITY_SCHEMA})


def seed_campaign(store: LibraryStore, refs=EVERYONE) -> None:
    txn = store.transaction()
    for ref in refs:
        txn.put(instance_record(ref), None)
    txn.commit(owner_handle())


def relation_library(store: LibraryStore, handle=None) -> RelationLibrary:
    return RelationLibrary(store, handle or owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)

"""Synthetic world authoring fixtures (087): Realm's Saltmarch Coast, its Lamplighters Guild and a lighthouse."""

from __future__ import annotations

from grimoire.core.entity_codec import ENTITY_SCHEMA
from grimoire.core.identity import SourceRef
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest
from grimoire.topology import Place, Topology
from grimoire.topology.codec import encode_topology

from store_helpers import OWNER, owner_handle

REALM = "world:realm"
COAST = SourceRef(REALM, "location", "saltmarch-coast", "r1")
GUILD = SourceRef(REALM, "organization", "lamplighters-guild", "r1")
LIGHT = SourceRef(REALM, "location", "old-light", "r1")
MAP = SourceRef(REALM, "topology", "coast-map", "r1")
MANIFEST = "sources/world/realm/_source/manifest.json"


def entity(ref: SourceRef, name: str, *, spatial: bool, nature: str = "individual") -> RecordEnvelope:
    traits = [{"facet": "spatial"}] if spatial else []
    return RecordEnvelope(ref, {"name": name, "nature": nature, "traits": traits}, name=name, owner=OWNER,
                          extra={"schema": ENTITY_SCHEMA})


def seed(store: LibraryStore, *, with_map: bool = False) -> None:
    """Realm: the Saltmarch Coast (a region), the Lamplighters Guild and, optionally, a map of the coast."""
    heads = {"location/saltmarch-coast": "r1", "organization/lamplighters-guild": "r1", "location/old-light": "r1"}
    txn = store.transaction()
    txn.put(entity(COAST, "Saltmarch Coast", spatial=True), None)
    txn.put(entity(GUILD, "Lamplighters Guild", spatial=False), None)
    txn.put(entity(LIGHT, "Old Light", spatial=True), None)
    if with_map:
        heads["topology/coast-map"] = "r1"
        graph = Topology(MAP, (Place(COAST), Place(LIGHT, parent=COAST)), (), "drawn by hand")
        txn.put(RecordEnvelope(MAP, encode_topology(graph), name="Coast map", owner=OWNER), None)
    txn.put(SourceManifest(REALM, OWNER, "Realm", heads), None)
    txn.commit(owner_handle())

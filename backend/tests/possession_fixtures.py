"""Synthetic people, places and swords for Sprint 034 unique-possession tests (no real library)."""

from __future__ import annotations

from grimoire.core.entity import EntityDefinition, EntityInstance
from grimoire.core.entity_codec import ENTITY_SCHEMA, definition_to_payload, instance_to_payload
from grimoire.core.facets import FacetSet, Physical, Portable
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.possessions import Creation, Operation, Origin, Possession, State, Step
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest

from store_helpers import OWNER, owner_handle

C = "c1"
MARA = InstanceRef(C, "character", "mara-1")
TOMA = InstanceRef(C, "character", "toma-1")
CHEST = InstanceRef(C, "item", "chest-1")
HALL = InstanceRef(C, "location", "hall-1")
OTHER = InstanceRef("c2", "character", "ivo-1")
ARMS = "catalog:arms"
LONGSWORD = SourceRef(ARMS, "item", "longsword", "r1")  # a template: any number of longswords
STARFALL = SourceRef(ARMS, "item", "starfall", "r1")  # one particular blade
AT = "2026-11-05T10:00:00Z"
HANDS = ("main-hand", "off-hand")


class Clock:
    """Deterministic UTC timestamps, one second apart."""

    def __init__(self) -> None:
        self.ticks = 0

    def __call__(self) -> str:
        self.ticks += 1
        minutes, seconds = divmod(self.ticks, 60)
        return f"2026-11-05T10:{minutes:02d}:{seconds:02d}Z"


def everyone(_ref: InstanceRef) -> bool:
    return True


def sword(item_id: str = "sword-1", *, holder=MARA, owner=MARA, equipped=None, fits=HANDS, rev=1,
          op="c-" + "sword") -> Possession:
    """A record as ``originate`` would make it, at revision ``rev`` with no log."""
    origin = Origin(op, AT, holder, owner, "Sword", None, fits)
    return Possession(InstanceRef(C, "item", item_id), State(holder, owner, equipped), origin, rev, fits,
                      evicted_through=rev)


def op(op_id: str, *steps: Step, **kwargs) -> Operation:
    return Operation(op_id, tuple(steps), **kwargs)


def make(op_id: str = "make-1", *, holder=MARA, owner=MARA, name="Sword", fits=HANDS, **kwargs) -> Creation:
    return Creation(op_id, holder, owner, name, fits=fits, **kwargs)


def _entity(ref: InstanceRef) -> RecordEnvelope:
    entity = EntityInstance(ref, local_name=ref.instance_id.title())
    return RecordEnvelope(ref, instance_to_payload(entity), owner=OWNER, extra={"schema": ENTITY_SCHEMA})


def seed(store: LibraryStore, parties=(MARA, TOMA, CHEST, HALL)) -> None:
    """Party instance files and an arms catalog with a template and an individual blade."""
    txn = store.transaction()
    for party in parties:
        txn.put(_entity(party), None)
    txn.put(SourceManifest(ARMS, OWNER, "Arms"), None)
    traits = FacetSet((Physical("steel"), Portable()))
    for ref, name, nature in ((LONGSWORD, "Longsword", "template"), (STARFALL, "Starfall", "individual")):
        definition = EntityDefinition(ref, name, nature, traits)
        txn.put(RecordEnvelope(ref, definition_to_payload(definition), name=name, extra={"schema": ENTITY_SCHEMA}),
                None)
    txn.commit(owner_handle())


def possession_library(store: LibraryStore, handle=None):
    from grimoire.possessionstore import PossessionLibrary
    return PossessionLibrary(store, handle or owner_handle(), clock=Clock(), owner=OWNER)

"""A synthetic campaign for Sprint 033 tests: a trainer, two individualized goblins and bystanders (no real library)."""

from __future__ import annotations

from grimoire.core import DIRECTOR, CampaignRoster, DefinitionIndex, InstanceRef, SourceDescriptor
from grimoire.core.entity import EntityInstance
from grimoire.core.entity_codec import ENTITY_SCHEMA, definition_to_payload, instance_to_payload
from grimoire.core.individual import IndividualizationDecision, individualize
from grimoire.partystore import PartyLibrary
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest

from chat_fixtures import Clock, Ids
from entity_fixtures import EMBER, goblin
from graph_fixtures import instance_record
from store_helpers import OWNER, owner_handle

C = "c"
SPECIES = goblin()
MARA = InstanceRef(C, "character", "mara")            # the trainer, a player character
TOMA = InstanceRef(C, "character", "toma")            # another player character
INNKEEPER = InstanceRef(C, "character", "innkeeper")  # an NPC of an unrelated scene
CREW = InstanceRef(C, "group", "crew")                # a party anchor for troupe play
SNIK, GRUB = InstanceRef(C, "creature", "goblin-1"), InstanceRef(C, "creature", "goblin-2")
PEOPLE = (MARA, TOMA, INNKEEPER, CREW)


def _individual(ref: InstanceRef, name: str) -> EntityInstance:
    """Instantiate a generic goblin and individualize it in place with 024 (a campaign-unique creature)."""
    generic = EntityInstance(ref, SPECIES.ref.unpinned())
    index = DefinitionIndex([SourceDescriptor(EMBER, owner=OWNER)], [SPECIES], {SPECIES.ref.unpinned(): "r1"})
    decision = IndividualizationDecision(C, f"d-{name.lower()}", ref, name, DIRECTOR, "s1")
    return individualize(CampaignRoster(C, (generic,)), decision, index, ()).instance


def seed(store: LibraryStore) -> None:
    txn = store.transaction()
    txn.put(SourceManifest(EMBER, OWNER, "Emberfall", {"species/goblin": "r1"}), None)
    txn.put(RecordEnvelope(SPECIES.ref, definition_to_payload(SPECIES), name=SPECIES.name,
                           extra={"schema": ENTITY_SCHEMA}), None)
    for ref in PEOPLE:
        txn.put(instance_record(ref), None)
    for ref, name in ((SNIK, "Snik"), (GRUB, "Grub")):
        txn.put(RecordEnvelope(ref, instance_to_payload(_individual(ref, name)), owner=OWNER,
                               extra={"schema": ENTITY_SCHEMA}), None)
    txn.commit(owner_handle())


def party_library(store: LibraryStore, handle=None) -> PartyLibrary:
    return PartyLibrary(store, handle or owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)

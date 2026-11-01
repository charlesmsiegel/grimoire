"""An individualized goblin survives the native store (024 x 010), via a test-local join.

There is deliberately no production 009<->010 adapter here: this test encodes
entity values with the provisional codec into opaque payloads, and decodes them
back into a fresh ``DefinitionIndex``. The schema id is a test constant
because ``RecordEnvelope`` has no schema field. Instance envelopes carry only
address and payload; mapping ``local_name``/``source`` onto the envelope's
``name``/``derived_from``/``base`` is the join's decision, not 024's, and
``Individuality.template`` is provenance, distinct from ``base``.
"""

import pytest
from entity_fixtures import EMBER, goblin
from store_helpers import OWNER, owner_handle

from grimoire.core import (
    DIRECTOR, CampaignRoster, DefinitionIndex, InstanceRef, SourceDescriptor, effective_entity,
)
from grimoire.core.entity import EntityInstance
from grimoire.core.entity_codec import (
    ENTITY_SCHEMA, definition_from_payload, definition_to_payload, instance_from_payload,
    instance_to_payload,
)
from grimoire.core.individual import IndividualizationDecision, individualize, is_generic
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest, StaleWrite

C = "c1"
G1, G2, G3 = (InstanceRef(C, "creature", f"goblin-{n}") for n in (1, 2, 3))
SPECIES = goblin()


def decode_index(view) -> DefinitionIndex:
    """Test-local: decode every head revision of every loaded source into entity definitions."""
    descriptors, definitions, heads = [], [], {}
    for source_id, stored in view.sources.items():
        descriptors.append(SourceDescriptor(source_id, owner=stored.manifest.owner))
        for ref, revision in stored.manifest.head_refs().items():
            record = view.index.resolve(ref.at(revision))
            definitions.append(definition_from_payload(record.ref, ENTITY_SCHEMA, record.envelope.payload))
            heads[ref] = revision
    return DefinitionIndex(descriptors, definitions, heads)


def decode_roster(view) -> CampaignRoster:
    return CampaignRoster(C, tuple(
        instance_from_payload(ref, ENTITY_SCHEMA, stored.envelope.payload)
        for ref, stored in view.instances.items() if ref.campaign_id == C))


def envelope(instance: EntityInstance) -> RecordEnvelope:
    return RecordEnvelope(instance.ref, instance_to_payload(instance))


@pytest.fixture
def library(tmp_path):
    store = LibraryStore.create(tmp_path / "lib")
    txn = store.transaction()
    txn.put(SourceManifest(EMBER, OWNER, "Emberfall", {"species/goblin": "r1"}), None)
    txn.put(RecordEnvelope(SPECIES.ref, definition_to_payload(SPECIES), name=SPECIES.name), None)
    for ref in (G1, G2, G3):
        txn.put(envelope(EntityInstance(ref, SPECIES.ref.unpinned())), None)
    txn.commit(owner_handle())
    yield store
    store.close()


def snik_decision():
    return IndividualizationDecision(C, "d-snik", G1, "Snik", DIRECTOR, "s3", ("evt-talk",))


def test_an_individualized_goblin_round_trips_and_nothing_else_is_rewritten(library):
    before = library.load()
    etags = {ref: stored.etag for ref, stored in before.instances.items()}
    species_etag = before.index.resolve(SPECIES.ref).etag
    index, roster = decode_index(before), decode_roster(before)
    assert all(is_generic(instance, index) for instance in roster.instances)

    result = individualize(roster, snik_decision(), index)
    txn = library.transaction()
    txn.put(envelope(result.instance), etags[G1])
    txn.commit(owner_handle())

    after = library.load()
    assert after.problems == ()
    reloaded_index, reloaded = decode_index(after), decode_roster(after)
    snik = reloaded.instance(G1)
    assert snik == result.instance and snik.individuality.template == SPECIES.ref
    assert effective_entity(snik, reloaded_index).name == "Snik"
    assert after.instances[G1].etag != etags[G1]
    assert {ref: after.instances[ref].etag for ref in (G2, G3)} == {G2: etags[G2], G3: etags[G3]}
    assert after.index.resolve(SPECIES.ref).etag == species_etag
    assert reloaded_index.resolve(SPECIES.ref) == SPECIES
    assert all(is_generic(reloaded.instance(ref), reloaded_index) for ref in (G2, G3))


def test_a_stale_write_of_the_individual_is_refused(library):
    view = library.load()
    stale_etag = view.instances[G1].etag
    first = individualize(decode_roster(view), snik_decision(), decode_index(view))
    txn = library.transaction()
    txn.put(envelope(first.instance), stale_etag)
    txn.commit(owner_handle())

    other = IndividualizationDecision(C, "d-grub", G1, "Grub", DIRECTOR, "s5")
    rival = individualize(decode_roster(view), other, decode_index(view))  # from the old read
    txn = library.transaction()
    txn.put(envelope(rival.instance), stale_etag)
    with pytest.raises(StaleWrite):
        txn.commit(owner_handle())
    assert decode_roster(library.load()).instance(G1).local_name == "Snik"

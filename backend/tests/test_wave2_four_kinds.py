"""Wave 2 exit item: four entity kinds through ``LibraryStore`` with instances in two campaigns.

A person, a species, a creature and a sentient weapon round-trip through the native store with
unknown fields kept, each with an instance in two campaigns; after a reopen everything decodes as
written, and a stale write in one campaign is refused without touching the other.
"""

import pytest
from entity_fixtures import EMBER, grakka, mara, ogre, vessa
from store_helpers import OWNER, owner_handle

from grimoire.core import InstanceRef
from grimoire.core.entity import EntityInstance
from grimoire.core.entity_codec import (
    ENTITY_SCHEMA, definition_from_payload, definition_to_payload, instance_from_payload, instance_to_payload,
)
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest, StaleWrite

DEFINITIONS = (mara(), ogre(), grakka(), vessa())
INSTANCES = tuple(EntityInstance(InstanceRef(campaign, d.ref.kind, f"{d.ref.entity_id}-{campaign}"), d.ref.unpinned())
                  for campaign in ("c1", "c2") for d in DEFINITIONS)


def test_four_kinds_in_two_campaigns_round_trip_and_refuse_a_stale_write(tmp_path):
    assert len({d.ref.kind for d in DEFINITIONS}) == 4
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        txn = store.transaction()
        heads = {f"{d.ref.kind}/{d.ref.entity_id}": "r1" for d in DEFINITIONS}
        txn.put(SourceManifest(EMBER, OWNER, "Emberfall", heads, extra={"x-unknown": [1]}), None)
        for definition in DEFINITIONS:
            txn.put(RecordEnvelope(definition.ref, definition_to_payload(definition), name=definition.name,
                                   extra={"x-keep": {"a": 1}}), None)
        for instance in INSTANCES:
            txn.put(RecordEnvelope(instance.ref, instance_to_payload(instance), derived_from=str(instance.source)),
                    None)
        txn.commit(owner_handle())

    with LibraryStore.open(root) as store:
        view = store.load()
        assert view.problems == () and not view.broken
        assert dict(view.sources[EMBER].manifest.extra) == {"x-unknown": (1,)}  # frozen on load
        for definition in DEFINITIONS:
            record = view.index.resolve(definition.ref)
            assert definition_from_payload(record.ref, ENTITY_SCHEMA, record.envelope.payload) == definition
            assert dict(record.envelope.extra) == {"x-keep": {"a": 1}}
        assert {ref.campaign_id for ref in view.instances} == {"c1", "c2"}
        for instance in INSTANCES:
            assert instance_from_payload(instance.ref, ENTITY_SCHEMA, view.instances[instance.ref].envelope.payload) \
                == instance

        weapon_c1, weapon_c2 = (view.instances[INSTANCES[i].ref] for i in (3, 7))
        txn = store.transaction()
        txn.put(RecordEnvelope(weapon_c1.envelope.address, {"x": 1}), weapon_c1.etag)
        txn.commit(owner_handle())
        txn = store.transaction()
        txn.put(RecordEnvelope(weapon_c1.envelope.address, {"x": 2}), weapon_c1.etag)
        with pytest.raises(StaleWrite):
            txn.commit(owner_handle())
        after = store.load()
        assert after.instances[weapon_c1.envelope.address].envelope.payload == {"x": 1}
        assert after.instances[weapon_c2.envelope.address].etag == weapon_c2.etag

"""Configuration instances do not become chat actors or change an actor's effective state."""
from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.core import DIRECTOR
from grimoire.effective import chat_snapshot
from grimoire.store import LibraryStore
from grimoire.topologystore import TopologyLibrary
from chat_fixtures import Clock, Ids
from portrayal_fixtures import VAMPIRE_MARA, seed_elderfen
from store_helpers import OWNER, owner_handle
from topology_fixtures import GRAPH, topology, seed


def test_topology_configuration_leaves_chat_cast_and_source_unchanged(tmp_path):
    with LibraryStore.create(tmp_path / 'lib') as store:
        seed_elderfen(store, owner_handle())
        seed(store)
        chats = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)
        started = chats.start_chat(Launch('character', VAMPIRE_MARA)).commit()
        before = chat_snapshot(store.load(), started.chat)
        lib = TopologyLibrary(store, owner_handle())
        lib.publish(topology(), requested_by=DIRECTOR).commit()
        lib.change_path(GRAPH, started.chat.campaign_id, topology().paths[0], operation_id='path-edit',
                        provenance=OWNER, expected=None, requested_by=DIRECTOR).commit()
        after = chat_snapshot(store.load(), started.chat)
        assert after.problems == ()
        assert after.snapshot.sources() == before.snapshot.sources()
        for actor in started.actors:
            assert after.snapshot.entity(actor) == before.snapshot.entity(actor)
            assert after.snapshot.instance_etag(actor) == before.snapshot.instance_etag(actor)

"""An instance id is unique within a campaign across kinds (IDENTITY.md): the store refuses a pair.

``campaign:c1/creature/wren-a`` beside ``campaign:c1/character/wren-a`` would make the segment that
authority and intent use (``actor_id``) ambiguous. The commit refuses to create one; a pair already
on disk (hand-made, or formed across devices) is reported on both paths and ``load()`` leaves both
out, never picking one; edits of a pair's members stay allowed so a human can repair it.
"""

from __future__ import annotations

import pickle

import pytest

from grimoire.chats import Launch
from grimoire.chatstore import InvalidBinding
from grimoire.core import Event, Moment, Perception
from grimoire.core.entity import EntityInstance
from grimoire.core.identity import InstanceRef, ScopeRef
from grimoire.core.roster import CampaignRoster
from grimoire.evidencestore import EvidenceLibrary, EvidenceWriteError
from grimoire.store import InstanceIdConflict, LibraryStore, StoreError
from grimoire.testing.fileops import snapshot

from chat_fixtures import MARA, chat_library, seed_sources
from store_helpers import OWNER, WREN, WREN_A, WREN_R1, instance, new_library, owner_handle, seed

CREATURE_A = InstanceRef("c1", "creature", "wren-a")


def _commit(store: LibraryStore, *refs: InstanceRef, expected: str | None = None):
    txn = store.transaction()
    for ref in refs:
        txn.put(instance(ref, WREN_R1), expected)
    return txn.commit(owner_handle())


def _hand_write(store: LibraryStore, ref: InstanceRef) -> None:
    """The exact bytes the store would write, placed by hand (as another program or a sync tool could)."""
    txn = store.transaction()
    txn.put(instance(ref, WREN_R1), None)
    (data,) = txn.preview().values()
    path = store.root.joinpath("campaigns", ref.campaign_id, "instances", ref.kind, f"{ref.instance_id}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_a_new_instance_never_takes_an_id_another_kind_holds(tmp_path):
    with new_library(tmp_path / "lib") as store:
        seed(store)  # campaign:c1/character/wren-a
        before = snapshot(store.root)
        with pytest.raises(InstanceIdConflict) as caught:
            _commit(store, CREATURE_A)
        assert caught.value.reason == "instance_id_conflict"
        assert caught.value.paths == ("campaigns/c1/instances/creature/wren-a.json",)
        assert isinstance(caught.value, StoreError)
        assert pickle.loads(pickle.dumps(caught.value)).paths == caught.value.paths
        assert snapshot(store.root) == before  # nothing written
        with pytest.raises(InstanceIdConflict):  # check() refuses it too, before any approval is spent
            txn = store.transaction()
            txn.put(instance(CREATURE_A, WREN_R1), None)
            txn.check()


def test_two_kinds_created_in_one_transaction_are_refused(tmp_path):
    with new_library(tmp_path / "lib") as store:
        seed(store)
        with pytest.raises(InstanceIdConflict) as caught:
            _commit(store, InstanceRef("c1", "character", "tam"), InstanceRef("c1", "creature", "tam"))
        assert set(caught.value.paths) == {"campaigns/c1/instances/character/tam.json",
                                           "campaigns/c1/instances/creature/tam.json"}
        assert store.campaign_instances("c1") == (WREN_A,)


def test_the_same_id_in_another_campaign_or_kind_folder_is_fine(tmp_path):
    with new_library(tmp_path / "lib") as store:
        seed(store)
        _commit(store, InstanceRef("c9", "creature", "wren-a"))  # another campaign
        _commit(store, InstanceRef("c1", "character", "tam"))  # same kind folder, new id
        assert InstanceRef("c9", "creature", "wren-a") in store.load().instances


def test_a_look_alike_file_of_another_kind_counts(tmp_path):
    """``Wren-A.json`` may be ``wren-a.json`` on a case-insensitive filesystem or after a rename."""
    with new_library(tmp_path / "lib") as store:
        seed(store)
        folder = store.root / "campaigns" / "c1" / "instances" / "item"
        folder.mkdir(parents=True)
        (folder / "Tam.json").write_bytes(b"{}")
        with pytest.raises(InstanceIdConflict):
            _commit(store, InstanceRef("c1", "character", "tam"))


def test_a_pair_on_disk_is_reported_and_both_are_left_out_of_load(tmp_path):
    with new_library(tmp_path / "lib") as store:
        seed(store)
        _hand_write(store, CREATURE_A)
        loaded = store.load()
        conflicts = sorted(p.path for p in loaded.problems if p.code == "instance_id_conflict")
        assert conflicts == ["campaigns/c1/instances/character/wren-a.json",
                             "campaigns/c1/instances/creature/wren-a.json"]
        assert WREN_A not in loaded.instances and CREATURE_A not in loaded.instances
        roster = CampaignRoster("c1", tuple(EntityInstance(ref, source=WREN) for ref in loaded.instances
                                            if ref.campaign_id == "c1"))
        assert roster.instances == ()  # no IdentityConflict: neither member is picked
        # the listing still names both, so an allocator keeps both ids taken
        assert set(store.campaign_instances("c1")) == {WREN_A, CREATURE_A}


def test_a_pairs_members_stay_editable_so_it_can_be_repaired(tmp_path):
    with new_library(tmp_path / "lib") as store:
        seed(store)
        _hand_write(store, CREATURE_A)
        txn = store.transaction()
        txn.put(instance(WREN_A, WREN_R1, hp=9), store.read(WREN_A).etag)
        txn.commit(owner_handle())
        assert store.read(WREN_A).envelope.payload == {"hp": 9}
        store.root.joinpath("campaigns", "c1", "instances", "creature", "wren-a.json").unlink()  # the repair
        loaded = store.load()
        assert WREN_A in loaded.instances
        assert not [p for p in loaded.problems if p.code == "instance_id_conflict"]


def test_services_refuse_to_act_through_a_paired_id(tmp_path):
    """107 never reuses, and 020 never records knowledge for, an actor whose id another kind shares."""
    with new_library(tmp_path / "lib") as store:
        seed_sources(store)
        chats = chat_library(store)
        (mara,) = chats.start_chat(Launch("character", MARA)).commit().actors
        _hand_write(store, InstanceRef(mara.campaign_id, "creature", mara.instance_id))
        with pytest.raises(InvalidBinding) as refused:
            chats.start_chat(Launch("character", MARA), scope=ScopeRef(mara.campaign_id),
                             reuse_actors={("character", MARA.entity_key): mara})
        assert refused.value.reason == "instance_id_conflict"
        evidence = EvidenceLibrary(store, owner_handle(), owner=OWNER)
        with pytest.raises(EvidenceWriteError) as caught:
            evidence.append(mara.campaign_id, [
                Event(mara.campaign_id, "e1", "a door opens", Moment(1), "director"),
                Perception(mara.campaign_id, "v1", mara, "e1", "witnessed", "a door", Moment(1), "director",
                           confirmed=True)])
        assert caught.value.reason == "instance_id_conflict"

"""033: public persistent actor bindings, with synthetic individualized creatures only."""

import json
from dataclasses import replace

import pytest

from grimoire.chats import CAMPAIGN_CONTINUITY, ChatModelError, Launch
from grimoire.chatstore import InvalidBinding
from grimoire.core import DIRECTOR, Event, Moment, Perception
from grimoire.core.entity import EntityInstance
from grimoire.core.entity_codec import ENTITY_SCHEMA, instance_to_payload
from grimoire.core.identity import InstanceRef, ScopeRef, SourceRef
from grimoire.effective import chat_snapshot
from grimoire.evidencestore import EvidenceLibrary
from grimoire.ledgers import ControlChange
from grimoire.quickstart import QuickStart, QuickStartRefused
from grimoire.rebasestore import SourceRebase
from grimoire.scene import SceneRefused
from grimoire.store import LibraryStore, RecordEnvelope, StaleWrite, WriteDenied
from grimoire.testing.fileops import snapshot

from chat_fixtures import Clock, chat_library
from party_fixtures import C, GRUB, MARA, SNIK, SPECIES, party_library, seed
from scene_fixtures import ANA, Table, secure
from store_helpers import OWNER, owner_handle


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "v2"
    with LibraryStore.create(path) as store:
        seed(store)
    return path


def put(store, envelope, expected=None):
    transaction = store.transaction()
    transaction.put(envelope, expected)
    transaction.commit(owner_handle())


def start(chats, actors=(SNIK, GRUB)):
    return chats.start_chat(Launch("premise", premise="A quiet camp"), scope=ScopeRef(C),
                            actors=[(SPECIES.ref, actor) for actor in actors]).commit().chat


def test_same_species_public_create_update_reopen(root):
    with LibraryStore.open(root) as store:
        chats = chat_library(store)
        chat = start(chats, (SNIK,))
        chats.attach_source(chat, SPECIES.ref, "actor", actor=GRUB, expected=chats.open_chat(chat).etag).commit()
        original = chats.open_chat(chat).record
        assert {e.actor for e in original.roster} == {SNIK, GRUB}
        assert all(e.prior_history == CAMPAIGN_CONTINUITY for e in original.roster)
        before = snapshot(root)
        for actor in (None, InstanceRef(C, "creature", "absent")):
            with pytest.raises((ChatModelError, InvalidBinding)):
                chats.detach_source(chat, SPECIES.ref, "actor", actor=actor, expected=chats.open_chat(chat).etag)
            assert snapshot(root) == before
        with pytest.raises(ChatModelError, match="already bound|bound twice"):
            chats.attach_source(chat, SPECIES.ref, "actor", actor=SNIK, expected=chats.open_chat(chat).etag)
        assert snapshot(root) == before
        chats.detach_source(chat, SPECIES.ref, "actor", actor=SNIK, expected=chats.open_chat(chat).etag).commit()
        assert [b.actor for b in chats.open_chat(chat).record.active_bindings()] == [GRUB]
        chats.attach_source(chat, SPECIES.ref, "actor", actor=SNIK, expected=chats.open_chat(chat).etag).commit()
    with LibraryStore.open(root) as store:
        chats = chat_library(store)
        view = chats.open_chat(chat)
        assert view.broken == () and view.record.roster == original.roster
        assert len(view.record.bindings) == 3 and not view.record.bindings[0].active
        assert chats.navigation().by_source(SPECIES.ref) == (chat,)
        captured = chat_snapshot(store.load(), chat)
        assert captured.problems == ()
        assert captured.snapshot.entity(SNIK).name == "Snik"
        assert captured.snapshot.entity(GRUB).name == "Grub"


def test_two_same_species_actors_can_start_together(root):
    with LibraryStore.open(root) as store:
        chat = start(chat_library(store))
        assert {b.actor for b in chat_snapshot(store.load(), chat).chat.active_bindings()} == {SNIK, GRUB}


def test_reserve_control_and_private_memory_survive_chat_and_release(root):
    with LibraryStore.open(root) as store:
        party = party_library(store)
        for actor in (SNIK, GRUB):
            party.join(MARA, actor, at=Moment(1), provenance="director").commit()
        party.store_in_reserve(GRUB, at=Moment(2), provenance="director").commit()
        EvidenceLibrary(store, owner_handle(), owner=OWNER).append(C, [
            Event(C, "e-secret", "A hidden bell rang", Moment(3), "director", (GRUB,)),
            Perception(C, "v-secret", GRUB, "e-secret", "heard", "A quiet chime", Moment(3), "director", confirmed=True)
        ]).commit()
        remembered = EvidenceLibrary(store).actor_knowledge(C, GRUB, as_of=9)
        before = snapshot(root)
        chat = start(chat_library(store))
        # Binding only writes chat/scope: neither private memory nor the instances change.
        assert all(snapshot(root).get(k) == v for k, v in before.items())
        assert EvidenceLibrary(store).actor_knowledge(C, SNIK, as_of=9).evidence == ()
    secure(root)
    with Table(root) as table:
        table.runner.sovereignty.record(C, ControlChange(C, SNIK.instance_id, ANA), table.owner)
        table.runner.seat(chat, OWNER)
        scene = table.runner.open(chat)
        assert scene.cast == (SNIK,) and scene.players == (SNIK,)
        assert scene.controller(GRUB) == DIRECTOR
        with pytest.raises(SceneRefused) as error:
            table.runner.generate(chat, table.owner, speakers=[GRUB])
        assert error.value.reason == "not_present"
        party_library(table.store, table.owner).withdraw(GRUB, at=Moment(4), provenance="director").commit()
        scene = table.runner.open(chat)
        assert set(scene.cast) == {SNIK, GRUB} and scene.players == (SNIK,) and scene.npcs == (GRUB,)
        party_library(table.store, table.owner).release(GRUB, at=Moment(5), provenance="director").commit()
        chats = chat_library(table.store, table.owner)
        chats.detach_source(chat, SPECIES.ref, "actor", actor=GRUB, expected=chats.open_chat(chat).etag).commit()
        assert table.runner.open(chat).cast == (SNIK,)
    with Table(root) as table:
        assert table.runner.open(chat).controller(SNIK) == ANA
        assert EvidenceLibrary(table.store).actor_knowledge(C, GRUB, as_of=9) == remembered
        assert len(party_library(table.store).history(GRUB)) == 3
        assert {e.actor for e in chat_library(table.store).open_chat(chat).record.roster} == {SNIK, GRUB}


@pytest.mark.parametrize("actor,ref,reason", [
    (None, SPECIES.ref, "actor_required"),
    (InstanceRef("elsewhere", "creature", "goblin-1"), SPECIES.ref, "actor_mismatch"),
    (InstanceRef(C, "creature", "missing"), SPECIES.ref, "missing_actor"),
    (SNIK, SourceRef("world:other", "species", "goblin", "r1"), "actor_mismatch"),
])
def test_invalid_actor_bindings_write_nothing(root, actor, ref, reason):
    with LibraryStore.open(root) as store:
        chats = chat_library(store)
        chat = start(chats, ())
        before = snapshot(root)
        with pytest.raises(InvalidBinding) as error:
            chats.attach_source(chat, ref, "actor", actor=actor, expected=chats.open_chat(chat).etag)
        assert error.value.reason == reason
        assert snapshot(root) == before


@pytest.mark.parametrize("fault", ["newer_schema", "malformed", "conflicting_source", "missing_source"])
def test_native_actor_provenance_fails_closed(root, fault):
    with LibraryStore.open(root) as store:
        stored = store.read(SNIK)
        envelope = stored.envelope
        if fault == "newer_schema":
            envelope = replace(envelope, extra={"schema": "grimoire.entity/999"})
        elif fault == "malformed":
            envelope = replace(envelope, payload={"source": str(SPECIES.ref), "added": "invalid"})
        elif fault == "conflicting_source":
            envelope = replace(envelope, derived_from="world:other/species/goblin@r1")
        else:
            envelope = replace(envelope, payload=instance_to_payload(EntityInstance(SNIK, local_name="Snik")))
        put(store, envelope, stored.etag)
        before = snapshot(root)
        with pytest.raises(InvalidBinding) as error:
            start(chat_library(store), (SNIK,))
        assert error.value.reason in {"invalid_actor", "actor_mismatch"}
        assert snapshot(root) == before


@pytest.mark.parametrize("when", ["before", "after"])
def test_generic_actor_reuse_preserves_one_id_rule(root, when):
    with LibraryStore.open(root) as store:
        chats = chat_library(store)
        chat = start(chats, ())
        def conflict():
            ref = InstanceRef(C, "item", SNIK.instance_id)
            # Simulate an external edit, bypassing the writer which correctly refuses this.
            path = root / "campaigns" / C / "instances" / "item" / f"{ref.instance_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            document = json.loads((root / "campaigns" / C / "instances" / "creature" / f"{ref.instance_id}.json").read_text())
            document["address"] = str(ref)
            path.write_text(json.dumps(document))
        if when == "before":
            conflict()
            with pytest.raises(InvalidBinding) as error:
                chats.attach_source(chat, SPECIES.ref, "actor", actor=SNIK, expected=chats.open_chat(chat).etag)
            assert error.value.reason == "instance_id_conflict"
        else:
            change = chats.attach_source(chat, SPECIES.ref, "actor", actor=SNIK, expected=chats.open_chat(chat).etag)
            conflict()
            before = snapshot(root)
            with pytest.raises(StaleWrite):
                change.commit()
            assert snapshot(root) == before
        assert chats.open_chat(chat).record.roster == ()


def test_stale_actor_and_denied_binding_do_not_write_chat(root):
    with LibraryStore.open(root) as store:
        chats = chat_library(store)
        chat = start(chats, ())
        change = chats.attach_source(chat, SPECIES.ref, "actor", actor=SNIK, expected=chats.open_chat(chat).etag)
        actor = store.read(SNIK)
        put(store, replace(actor.envelope, name="Changed"), actor.etag)
        before = snapshot(root)
        with pytest.raises(StaleWrite):
            change.commit()
        denied = chat_library(store, owner_handle("sources"))
        change = denied.attach_source(chat, SPECIES.ref, "actor", actor=SNIK, expected=chats.open_chat(chat).etag)
        with pytest.raises(WriteDenied):
            change.commit()
        assert snapshot(root) == before and chats.open_chat(chat).record.roster == ()


def test_generic_role_reuses_other_kinds_without_rekey(root):
    with LibraryStore.open(root) as store:
        ref = InstanceRef(C, "group", "companions")
        put(store, RecordEnvelope(ref, instance_to_payload(EntityInstance(ref, SPECIES.ref)),
                                               extra={"schema": ENTITY_SCHEMA}), None)
        chats = chat_library(store)
        chat = start(chats, (ref,))
        assert chats.open_chat(chat).record.roster[0].actor == ref
        assert store.read(ref).envelope.payload["source"] == str(SPECIES.ref)


def test_actor_specific_pinned_revisions_survive_shared_species(root):
    with LibraryStore.open(root) as store:
        old = store.read(SPECIES.ref)
        newer = SPECIES.ref.at("r2")
        put(store, replace(old.envelope, address=newer, name="New species"), None)
        grub = store.read(GRUB)
        payload = dict(grub.envelope.payload)
        payload["source"] = str(newer)
        put(store, replace(grub.envelope, payload=payload), grub.etag)
        # Binding an unpinned source keeps each native instance's own pinned revision.
        snik = store.read(SNIK)
        payload = dict(snik.envelope.payload)
        payload["source"] = str(SPECIES.ref)
        put(store, replace(snik.envelope, payload=payload), snik.etag)
        chat = chat_library(store).start_chat(Launch("premise", premise="A camp"), scope=ScopeRef(C),
            actors=[(SPECIES.ref.unpinned(), SNIK), (SPECIES.ref.unpinned(), GRUB)]).commit().chat
        captured = chat_snapshot(store.load(), chat)
        assert {b.actor: b.ref for b in captured.chat.active_bindings()} == {SNIK: SPECIES.ref, GRUB: newer}
        assert captured.snapshot.entity(SNIK).name == "Snik"
        assert captured.snapshot.entity(GRUB).name == "Grub"


def test_missing_species_stays_broken_and_can_be_detached(root):
    with LibraryStore.open(root) as store:
        chat = start(chat_library(store))
    # External removal cannot redirect these actors to a similarly named definition.
    (root / "sources" / "world" / "emberfall" / "_source" / "records" / "species" / "goblin" / "r1.json").unlink()
    with LibraryStore.open(root) as store:
        chats = chat_library(store)
        view = chats.open_chat(chat)
        assert any(b.field == "bindings.actor" for b in view.broken)
        assert {e.actor for e in view.record.roster} == {SNIK, GRUB}
        chats.detach_source(chat, SPECIES.ref, "actor", actor=SNIK, expected=view.etag).commit()
        assert [b.actor for b in chats.open_chat(chat).record.active_bindings()] == [GRUB]


def test_reviewed_rebase_repins_only_the_selected_same_species_actor(root):
    with LibraryStore.open(root) as store:
        stored = store.read(SNIK)
        put(store, replace(stored.envelope, payload={**stored.envelope.payload, "source": str(SPECIES.ref)}), stored.etag)
        chats = chat_library(store)
        chat = start(chats)
        old = store.read(SPECIES.ref)
        newer = SPECIES.ref.at("r2")
        put(store, replace(old.envelope, address=newer), None)
        rebase = SourceRebase(store, owner_handle(), clock=Clock())
        review = rebase.review(SNIK, newer)
        rebase.apply(review, {}).commit()
        captured = chat_snapshot(store.load(), chat)
        assert {b.actor: b.ref for b in captured.chat.active_bindings()} == {SNIK: newer, GRUB: SPECIES.ref}
        assert captured.snapshot.entity(SNIK).name == "Snik"
        assert captured.snapshot.entity(GRUB).name == "Grub"


def test_source_only_quickstart_repair_does_not_offer_ambiguous_actor_detach(root):
    with LibraryStore.open(root) as store:
        chat = start(chat_library(store))
    (root / "sources" / "world" / "emberfall" / "_source" / "records" / "species" / "goblin" / "r1.json").unlink()
    secure(root)
    with Table(root) as table:
        chats = chat_library(table.store, table.owner)
        quick = QuickStart(chats, table.runner)
        repairs = quick.repairs(chat)
        assert repairs and all(repair.role == "actor" and repair.choices == () for repair in repairs)
        before = snapshot(root)
        with pytest.raises(QuickStartRefused) as error:
            quick.detach_broken(chat, repairs[0], table.owner)
        assert error.value.reason == "invalid_field" and snapshot(root) == before
        # The public exact-instance repair remains available, including with the source gone.
        chats.detach_source(chat, SPECIES.ref, "actor", actor=SNIK, expected=chats.open_chat(chat).etag).commit()
        assert [b.actor for b in chats.open_chat(chat).record.active_bindings()] == [GRUB]

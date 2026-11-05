"""Sprint 032 proving scenario end to end on the real store: a spoken telepathic line reaches Emma only.

The player speaks into a microphone "I tell Emma telepathically the guards are listening". The
routed intent becomes a telepathic message; ``transmit`` makes the records; 020's
``EvidenceLibrary`` stores them. After a reopen, Emma holds the message, the guards and the
off-stage spy hold nothing, and Ines recalls what she sent. A spoken UI command is never dialogue.
Every fixture is synthetic under ``tmp_path``; no model is called.
"""

from __future__ import annotations

import pytest

from grimoire.comms import Message, Unread, read_message, transmit
from grimoire.core import DIRECTOR, InstanceRef, actor_knowledge
from grimoire.evidencestore import EvidenceLibrary, EvidenceWriteError
from grimoire.intent import RoutingContext, Utterance, route
from grimoire.store import LibraryStore, RecordEnvelope
from grimoire.testing.fileops import snapshot

from comms_fixtures import ACTORS, BYSTANDERS, C, EMMA, GUARD_1, GUARD_2, INES, NAMES, SPY, hall
from store_helpers import OWNER, owner_handle

PLAYER_CONTEXT = RoutingContext(active_pc="ines", can_direct=False)
LINE = "I tell Emma telepathically the guards are listening"


def seed(store: LibraryStore) -> None:
    txn = store.transaction()
    for ref in ACTORS:
        txn.put(RecordEnvelope(InstanceRef(C, ref.kind, ref.instance_id), {}), None)
    txn.commit(owner_handle())


def spoken(text: str):
    return route(Utterance(text, origin="voice", confidence=0.94), PLAYER_CONTEXT)


def deliver(evidence: EvidenceLibrary, text: str, message_id: str, sequence: int):
    message = read_message(spoken(text), INES, NAMES, message_id=message_id)
    if isinstance(message, Unread):
        return message
    sent = transmit(message, hall(), sequence=sequence, provenance="scene:great-hall")
    evidence.append(C, sent.records()).commit()
    return sent


def knowledge_files(root) -> dict:
    return {path: data for path, data in snapshot(root).items()
            if data is not None and ("/events/" in path or "/evidence/" in path)}


def test_the_spoken_telepathic_line_reaches_emma_only_and_survives_reopen(tmp_path):
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        seed(store)
        evidence = EvidenceLibrary(store, owner_handle(), owner=OWNER)
        sent = deliver(evidence, LINE, "m-1", 1)
        assert sent.message == Message("m-1", INES, "telepathy", "the guards are listening", (EMMA,))
        before = knowledge_files(root)
        # a spoken UI command is not dialogue: nothing is transmitted or written
        assert deliver(evidence, "Grimoire, open settings", "m-2", 2) == Unread("not_dialogue")
        assert knowledge_files(root) == before
    with LibraryStore.open(root, writable=False) as store:
        evidence = EvidenceLibrary(store)
        stored = evidence.read(C, requested_by=DIRECTOR)
        assert stored.quarantined == () and stored.problems == ()
        for as_of in range(0, 4):
            for ref in ACTORS:
                assert evidence.actor_knowledge(C, ref, as_of=as_of) == actor_knowledge(
                    stored.knowledge, ref, as_of=as_of)
        emma = evidence.actor_knowledge(C, EMMA, as_of=3)
        assert [(e.content, e.channel, e.attributed_to) for e in emma.evidence] == [
            ("the guards are listening", "telepathic", INES)]
        ines = evidence.actor_knowledge(C, INES, as_of=3)
        assert [(s.recipient, s.text, s.channel) for s in ines.sent] == [
            (EMMA, "the guards are listening", "telepathic")]
        for ref in BYSTANDERS + (SPY,):
            view = evidence.actor_knowledge(C, ref, as_of=3)
            assert view.evidence == () and view.sent == () and view.current == ()
        assert evidence.standing(C, "m-1", as_of=3) == "reported"  # received, never established
    # one proposition and one report on disk; no file names a guard
    files = knowledge_files(root)
    assert sorted(path.rsplit("/", 2)[-2] for path in files if "/batch/" not in path) == ["evidence", "proposition"]
    assert not any(guard.instance_id in data.decode() for data in files.values() for guard in BYSTANDERS)


def test_ordinary_speech_reaches_the_room_and_a_whisper_after_it_does_not(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        seed(store)
        evidence = EvidenceLibrary(store, owner_handle(), owner=OWNER)
        deliver(evidence, "\"Good evening.\"", "m-1", 1)
        deliver(evidence, "I whisper to Emma that we leave at midnight", "m-2", 2)
        guard = evidence.actor_knowledge(C, GUARD_2, as_of=5)
        assert [(e.content, e.channel) for e in guard.evidence] == [("Good evening.", "told")]
        emma = evidence.actor_knowledge(C, EMMA, as_of=5)
        assert [(e.content, e.channel) for e in emma.evidence] == [
            ("Good evening.", "told"), ("we leave at midnight", "whispered")]
        assert {s.recipient for s in evidence.actor_knowledge(C, INES, as_of=5).sent} == {EMMA, GUARD_1, GUARD_2}


def test_reusing_a_message_id_is_refused_by_the_store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        seed(store)
        evidence = EvidenceLibrary(store, owner_handle(), owner=OWNER)
        deliver(evidence, LINE, "m-1", 1)
        with pytest.raises(EvidenceWriteError) as caught:
            deliver(evidence, "I whisper to Emma that we leave at midnight", "m-1", 2)
        assert caught.value.reason == "duplicate_id"


def test_an_unstored_actor_cannot_receive(tmp_path):
    """Presence is the caller's claim; 020 still refuses a receiver that is not a stored actor."""
    with LibraryStore.create(tmp_path / "lib") as store:
        seed(store)
        evidence = EvidenceLibrary(store, owner_handle(), owner=OWNER)
        ghost = InstanceRef(C, "character", "ghost")
        presence = hall(present=(INES, EMMA, ghost))
        sent = transmit(Message("m-1", INES, "whisper", "hello", (ghost,)), presence, sequence=1,
                        provenance="director")
        with pytest.raises(EvidenceWriteError) as caught:
            evidence.append(C, sent.records())
        assert caught.value.reason == "unknown_actor"


def test_the_longest_message_id_is_stored(tmp_path):
    """Every id ``transmit`` accepts fits 020's files: the evidence id plus ``.json`` stays a slot."""
    import sys

    from grimoire.comms import MESSAGE_ID_MAX
    from grimoire.evidence.codec import record_ref
    from grimoire.store.layout import knowledge_parts
    longest = "m" * MESSAGE_ID_MAX
    sent = transmit(Message(longest, INES, "speech", "hello"), hall(), sequence=1, provenance="director")
    for record in sent.records():  # every file name is a storable slot on any system
        assert len(knowledge_parts(record_ref(record))[-1]) <= 128
    if sys.platform == "win32":  # a whole path's limit there depends on where the library lives
        return
    with LibraryStore.create(tmp_path / "lib") as store:
        seed(store)
        evidence = EvidenceLibrary(store, owner_handle(), owner=OWNER)
        evidence.append(C, sent.records()).commit()
        assert evidence.standing(C, longest, as_of=1) == "reported"

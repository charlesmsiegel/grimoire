"""Sprint 041 proving scenario on the real store: who left the room, and what they can still reach, see
and hear.

Mara and Toma stand in the study; the door to the hall is locked and Mara holds its key; a letter lies on
the desk. Mara lets Toma out and locks the door behind him. The next post cannot have Toma take the letter;
he sees nothing of what Mara does with it; a whisper through the shut door does not reach him, while
telepathy does. Once he leaves the house he can reach nothing at all. 020 stores the evidence, and after a
reopen each actor holds exactly what it could perceive. Synthetic fixtures under ``tmp_path``; no model.
"""

from __future__ import annotations

import pytest

from grimoire.comms import Message, TransmissionError
from grimoire.core import DIRECTOR, Event, InstanceRef, Moment
from grimoire.evidencestore import EvidenceLibrary
from grimoire.staging import (
    Answer, StagingRefused, apply, close_way, deliver, leave, lock_way, move, open_way, perceptions,
    reach, sees, take, unlock_way, where,
)
from grimoire.store import LibraryStore, RecordEnvelope

from staging_fixtures import ACTORS, C, EMMA, FRONT_DOOR, LETTER, MARA, ODD, SCENE, STUDY_DOOR, TOMA, stage
from store_helpers import OWNER, owner_handle

P = "scene:the-study"


def seed(store: LibraryStore) -> None:
    txn = store.transaction()
    for ref in ACTORS:
        txn.put(RecordEnvelope(InstanceRef(C, ref.kind, ref.instance_id), {}), None)
    txn.commit(owner_handle())


def refused(reason, s, change, sequence):
    with pytest.raises(StagingRefused) as caught:
        apply(s, change, sequence=sequence, provenance=P)
    assert caught.value.reason == reason


def test_who_left_the_room_and_what_they_can_still_reach_see_and_hear(tmp_path):
    root = tmp_path / "lib"
    s = stage()
    assert reach(s, TOMA, LETTER) == Answer(True, "within_reach")
    # Mara lets Toma out and locks the door behind him
    for sequence, change in ((2, unlock_way(MARA, STUDY_DOOR)), (3, open_way(MARA, STUDY_DOOR)),
                             (4, move(TOMA, STUDY_DOOR)), (5, close_way(MARA, STUDY_DOOR)),
                             (6, lock_way(MARA, STUDY_DOOR))):
        s = apply(s, change, sequence=sequence, provenance=P)
    assert where(s, TOMA).area == "hall"
    # the next post cannot have Toma casually take the letter from the desk
    refused("elsewhere", s, take(TOMA, LETTER), 7)
    assert reach(s, TOMA, LETTER) == Answer(False, "elsewhere")
    assert sees(s, TOMA, LETTER) == Answer(False, "door_shut")
    # Mara takes it; only she sees it happen
    s = apply(s, take(MARA, LETTER), sequence=7, provenance=P)
    seen = perceptions(s, Event(C, "e-1", "Mara pockets the letter", Moment(7, SCENE), "director"),
                       area="study", percept="a hand closes over the letter on the desk", provenance=P)
    assert [p.holder for p in seen] == [MARA]
    # a whisper through the shut door reaches no one; telepathy does
    s = s.at(8)
    whisper = Message("m-1", MARA, "whisper", "meet me at the gate", (TOMA,))
    with pytest.raises(TransmissionError) as caught:
        deliver(s, whisper, sequence=8, provenance=P)
    assert caught.value.reason == "addressee_absent"
    tele = Message("m-2", MARA, "telepathy", "meet me at the gate", (TOMA,))
    told = deliver(s, tele, sequence=8, provenance=P, telepaths=(MARA,))
    assert [r.holder for r in told.reports] == [TOMA]
    # the door opens again: Mara's call to the room is overheard in the hall
    s = apply(apply(s, unlock_way(MARA, STUDY_DOOR), sequence=9, provenance=P),
              open_way(MARA, STUDY_DOOR), sequence=10, provenance=P)
    call = deliver(s.at(11), Message("m-3", MARA, "speech", "the gate, at dusk"), sequence=11, provenance=P)
    assert {(r.holder, r.channel) for r in call.reports} == {(EMMA, "overheard"), (TOMA, "overheard")}
    # Toma leaves the house: off stage, he reaches and sees nothing
    s = apply(s.at(11), leave(TOMA, FRONT_DOOR), sequence=12, provenance=P)
    refused("not_on_stage", s, take(TOMA, LETTER), 13)
    assert where(s, TOMA).status == "off_stage" and where(s, TOMA).absent == TOMA
    assert sees(s, TOMA, MARA) == Answer(False, "viewer_off_stage")
    s = s.at(13)
    lamp = perceptions(s, Event(C, "e-2", "a lamp falls in the hall", Moment(13, SCENE), "director"),
                       area="hall", percept="a crash of glass", provenance=P)
    assert [p.holder for p in lamp] == [EMMA, MARA]
    # stored by 020, reopened
    with LibraryStore.create(root) as store:
        seed(store)
        evidence = EvidenceLibrary(store, owner_handle(), owner=OWNER)
        evidence.append(C, (Event(C, "e-1", "Mara pockets the letter", Moment(7, SCENE), "director"), *seen)).commit()
        evidence.append(C, told.records()).commit()
        evidence.append(C, call.records()).commit()
        evidence.append(C, (Event(C, "e-2", "a lamp falls in the hall", Moment(13, SCENE), "director"), *lamp)).commit()
    with LibraryStore.open(root, writable=False) as store:
        evidence = EvidenceLibrary(store)
        assert evidence.read(C, requested_by=DIRECTOR).quarantined == ()
        toma = evidence.actor_knowledge(C, TOMA, as_of=13)
        assert [(e.content, e.channel) for e in toma.evidence] == [
            ("meet me at the gate", "telepathic"), ("the gate, at dusk", "overheard")]
        mara = evidence.actor_knowledge(C, MARA, as_of=13)
        assert [e.channel for e in mara.evidence] == ["witnessed", "witnessed"]
        assert [s.recipient for s in mara.sent] == [TOMA]  # the overheard call is in no one's sent
        emma = evidence.actor_knowledge(C, EMMA, as_of=13)
        assert [(e.content, e.channel) for e in emma.evidence] == [("the gate, at dusk", "overheard"),
                                                                   ("a crash of glass", "witnessed")]
        odd = evidence.actor_knowledge(C, ODD, as_of=13)
        assert odd.evidence == () and odd.sent == ()

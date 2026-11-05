"""032 first slice: who an in-scene message reaches (pure ``transmit``)."""

from __future__ import annotations

import pickle

import pytest

from grimoire.comms import (
    MAX_RECEIVERS, MESSAGE_ID_MAX, Message, Presence, Transmission, TransmissionError, evidence_id, transmit,
)
from grimoire.core import DIRECTOR, Knowledge, Moment, Subject, actor_knowledge, director_knowledge
from grimoire.evidence import BATCH_LIMIT

from comms_fixtures import BYSTANDERS, C, EMMA, GUARD_1, GUARD_2, INES, SECRET, SPY, actor, hall


def send(channel: str, addressees=(EMMA,), *, sender=INES, presence=None, text=SECRET, message_id="m-1",
         sequence=3, about=()) -> Transmission:
    message = Message(message_id, sender, channel, text, addressees, about)
    return transmit(message, presence or hall(), sequence=sequence, provenance="scene:great-hall/t3",
                    turn_id="t3")


def ledger(transmission: Transmission) -> Knowledge:
    return Knowledge(C, propositions=[transmission.proposition], evidence=transmission.reports)


def refused(reason: str, call) -> None:
    with pytest.raises(TransmissionError) as caught:
        call()
    assert caught.value.reason == reason and str(caught.value) == reason
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert SECRET not in repr(caught.value)


# -- the private channels reach the addressee only ------------------------------------------------

@pytest.mark.parametrize("channel, received", [("whisper", "whispered"), ("telepathy", "telepathic")])
def test_a_private_message_reaches_only_its_addressee(channel, received):
    sent = send(channel)
    assert sent.receivers == (EMMA,)
    (report,) = sent.reports
    assert (report.holder, report.channel, report.confirmed, report.addressed) == (EMMA, received, True, True)
    assert (report.sender, report.attributed_to, report.sent_at, report.moment) == (
        INES, INES, Moment(3, "great-hall", "t3"), Moment(3, "great-hall", "t3"))
    assert sent.proposition.proposition_id == "m-1" and sent.proposition.text == SECRET
    knowledge = ledger(sent)
    emma = actor_knowledge(knowledge, EMMA, as_of=3)
    assert [(e.content, e.channel, e.attributed_to) for e in emma.evidence] == [(SECRET, received, INES)]
    ines = actor_knowledge(knowledge, INES, as_of=3)
    assert ines.evidence == () and [(s.recipient, s.text) for s in ines.sent] == [(EMMA, SECRET)]
    for bystander in BYSTANDERS + (SPY,):
        view = actor_knowledge(knowledge, bystander, as_of=3)
        assert view.evidence == () and view.sent == () and SECRET not in repr(view)
    assert actor_knowledge(knowledge, EMMA, as_of=2).evidence == ()  # nothing before it was sent


def test_a_private_message_to_several_addressees_reaches_each_and_no_one_else():
    sent = send("whisper", (GUARD_2, EMMA))
    assert sent.receivers == (EMMA, GUARD_2) and sent.message.addressees == (EMMA, GUARD_2)
    assert all(r.addressed and r.channel == "whispered" for r in sent.reports)
    assert actor_knowledge(ledger(sent), GUARD_1, as_of=9).evidence == ()


# -- speech is heard by the room ----------------------------------------------------------------

def test_unmarked_speech_reaches_everyone_present_but_the_speaker():
    sent = send("speech", ())
    assert sent.receivers == (EMMA, GUARD_1, GUARD_2)
    assert all((r.channel, r.addressed, r.confirmed) == ("told", True, True) for r in sent.reports)
    assert {s.recipient for s in actor_knowledge(ledger(sent), INES, as_of=3).sent} == {EMMA, GUARD_1, GUARD_2}
    assert actor_knowledge(ledger(sent), SPY, as_of=3).evidence == ()  # off stage hears nothing


def test_speech_to_one_actor_is_overheard_by_the_rest_of_the_room():
    sent = send("speech", (EMMA,))
    by_holder = {r.holder: r for r in sent.reports}
    assert set(by_holder) == {EMMA, GUARD_1, GUARD_2}
    assert (by_holder[EMMA].channel, by_holder[EMMA].addressed) == ("told", True)
    for guard in BYSTANDERS:
        report = by_holder[guard]
        assert (report.channel, report.addressed, report.sent_at, report.confirmed) == ("overheard", False, None, True)
    # the speaker knowingly told Emma only; overhearing is not something she sent
    assert [s.recipient for s in actor_knowledge(ledger(sent), INES, as_of=3).sent] == [EMMA]
    assert [e.channel for e in actor_knowledge(ledger(sent), GUARD_1, as_of=3).evidence] == ["overheard"]


# -- receipt is evidence, never truth ------------------------------------------------------------

def test_delivery_creates_no_ruling_event_or_belief_and_standing_is_only_reported():
    knowledge = ledger(send("telepathy"))
    assert knowledge.events == () and knowledge.rulings == () and knowledge.beliefs == ()
    view = director_knowledge(knowledge, as_of=3, requested_by=DIRECTOR)
    assert [p.standing for p in view.propositions] == ["reported"]


def test_about_is_carried_onto_the_proposition():
    subject = Subject("instance", str(GUARD_1))
    assert send("whisper", about=(subject,)).proposition.about == (subject,)


# -- evidence ids --------------------------------------------------------------------------------

def test_evidence_ids_are_neutral_deterministic_and_silent_about_other_receivers():
    alone, with_others = send("whisper", (EMMA,)), send("whisper", (EMMA, GUARD_1, GUARD_2))
    emma_alone = alone.reports[0].evidence_id
    assert emma_alone == evidence_id("m-1", EMMA) == {r.holder: r.evidence_id for r in with_others.reports}[EMMA]
    assert emma_alone.startswith("m-1.r-") and "emma" not in emma_alone
    longest = Message("m" * MESSAGE_ID_MAX, INES, "whisper", SECRET, (EMMA,))
    transmit(longest, hall(), sequence=1, provenance="director")  # still valid segments
    with pytest.raises(TransmissionError):
        Message("m" * (MESSAGE_ID_MAX + 1), INES, "whisper", SECRET, (EMMA,))


# -- refusals deliver nothing and carry no text -------------------------------------------------------

@pytest.mark.parametrize("reason, call", [
    ("sender_absent", lambda: send("whisper", sender=SPY)),
    ("addressee_absent", lambda: send("whisper", (SPY,))),
    ("addressee_absent", lambda: send("speech", (EMMA, SPY))),
    ("self_addressed", lambda: send("whisper", (INES,))),
    ("self_addressed", lambda: send("speech", (INES, EMMA))),
    ("no_addressee", lambda: send("whisper", ())),
    ("no_addressee", lambda: send("telepathy", ())),
    ("not_telepathic", lambda: send("telepathy", presence=hall(telepaths=()))),
    ("not_telepathic", lambda: send("telepathy", sender=EMMA, addressees=(INES,))),
    ("no_audience", lambda: send("speech", (), presence=hall(present=(INES,)))),
    ("campaign_mismatch", lambda: send("whisper", (actor("emma", campaign="c2"),))),
    ("campaign_mismatch", lambda: send("whisper", sender=actor("ines", campaign="c2"))),
    ("unknown_channel", lambda: send("shout")),
    ("unknown_channel", lambda: send("letter")),
    ("too_long", lambda: send("whisper", text="x" * 20_001)),
    ("invalid_record", lambda: send("whisper", text="hi \ud800")),
    ("invalid_record", lambda: transmit(Message("m-1", INES, "whisper", SECRET, (EMMA,)), hall(), sequence=1,
                                        provenance="scene \udc00")),
    ("invalid_record", lambda: send("whisper", text="   ")),
    ("invalid_record", lambda: send("whisper", addressees=(EMMA, EMMA))),
    ("invalid_record", lambda: send("whisper", addressees=str(EMMA))),
    ("invalid_record", lambda: send("whisper", addressees=("campaign:c1/character/emma",))),
    ("invalid_record", lambda: send("whisper", sequence=-1)),
    ("invalid_record", lambda: send("whisper", sequence=True)),
    ("invalid_record", lambda: send("whisper", message_id="Bad Id")),
    ("invalid_record", lambda: send("whisper", about=("event:e1",))),
    ("invalid_record", lambda: transmit("not a message", hall(), sequence=1, provenance="director")),
    ("invalid_record", lambda: transmit(Message("m-1", INES, "whisper", SECRET, (EMMA,)), hall(), sequence=1,
                                        provenance="")),
    ("invalid_record", lambda: transmit(Message("m-1", INES, "whisper", SECRET, (EMMA,)), hall(), sequence=1,
                                        provenance="director", turn_id="x" * 201)),
    ("campaign_mismatch", lambda: send("whisper", about=(Subject("instance", "campaign:c2/character/x"),))),
])
def test_refusals(reason, call):
    refused(reason, call)


@pytest.mark.parametrize("reason, kwargs", [
    ("invalid_record", {"present": ()}),
    ("invalid_record", {"present": (INES, INES)}),
    ("invalid_record", {"present": "campaign:c1/character/ines"}),
    ("invalid_record", {"present": {INES: 1, EMMA: 2}}),
    ("campaign_mismatch", {"present": (INES, actor("emma", campaign="c2"))}),
    ("telepath_absent", {"telepaths": (SPY,)}),
])
def test_presence_refusals(reason, kwargs):
    refused(reason, lambda: hall(**kwargs))


def test_one_transmission_always_fits_one_020_append():
    crowd = tuple(actor(f"guest-{n}") for n in range(MAX_RECEIVERS))
    sent = send("speech", (), presence=hall(present=(INES, *crowd)))
    assert len(sent.records()) == MAX_RECEIVERS + 1 == BATCH_LIMIT
    refused("too_many_receivers", lambda: send("speech", (), presence=hall(present=(INES, EMMA, *crowd))))


def test_presence_is_canonical_and_rejects_bad_scene_labels():
    assert hall(present=(GUARD_2, INES, EMMA)) == hall(present=[EMMA, INES, GUARD_2])
    refused("invalid_record", lambda: Presence(C, "", (INES,)))
    refused("invalid_record", lambda: Presence("Bad", "hall", (INES,)))
    refused("invalid_record", lambda: Presence(C, "hall \ud800", (INES,)))


def test_errors_pickle_with_their_reason():
    error = pickle.loads(pickle.dumps(TransmissionError("addressee_absent")))
    assert error.reason == "addressee_absent"

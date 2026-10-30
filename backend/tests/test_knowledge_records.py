"""012 knowledge records: strict, immutable, fail-closed values."""

import pickle

import pytest

from grimoire.core import (
    Belief, Disclosure, Event, InstanceRef, KnowledgeError, Moment, Perception, Proposition, Report,
    Ruling, Subject,
)
from knowledge_fixtures import C, INES, ORRIN, TAVI, at, betrayal_event, told, witness

SECRET = "SECRET-TEXT"


def reason_of(build) -> KnowledgeError:
    with pytest.raises(KnowledgeError) as caught:
        build()
    return caught.value


def test_every_record_family_constructs():
    Event(C, "e1", "a door opened", at(0), "director")
    Proposition(C, "p1", "the door is open", at(0), "director", about=(Subject("event", "e1"),))
    Ruling(C, "r1", "p1", "established", at(1), "director", grounds=(Subject("event", "e1"),))
    Ruling(C, "r2", "p1", "unknown", at(2), "director")
    witness(INES, "ev1", "a creak of hinges")
    told("ev2", TAVI, "p1", 3, sender=ORRIN, attributed_to=ORRIN, addressed=True, sent_at=2)
    Belief(C, "b1", TAVI, "p1", "suspected", at(3), "director", ("ev2",))
    Disclosure(C, "d1", "e1", at(4), "director")


@pytest.mark.parametrize("sequence", [True, -1, 1.0, "1"])
def test_moment_sequence_is_a_non_negative_int(sequence):
    assert reason_of(lambda: Moment(sequence)).reason == "invalid_record"


def test_moment_orders_only_by_sequence_and_labels_are_optional_text():
    assert Moment(2, "scene-1", "turn-3").sequence == 2
    for bad in ("", "  "):
        assert reason_of(lambda: Moment(1, bad)).reason == "invalid_record"


@pytest.mark.parametrize("kind, ref", [
    ("place", "x"), ("event", "Not A Segment"), ("instance", "nope"),
    ("instance", "world:a/character/ines"), ("definition", "campaign:c/character/x"),
    ("relation", ""),
])
def test_subjects_are_typed_and_parsed_exactly(kind, ref):
    assert reason_of(lambda: Subject(kind, ref)).reason == "invalid_record"


def test_subjects_accept_each_kind():
    assert str(Subject("instance", str(INES))) == f"instance:{INES}"
    Subject("definition", "world:a/character/ines@r1")
    Subject("relation", "member-of-17")


@pytest.mark.parametrize("confirmed", ["false", 0, None, 1])
def test_confirmed_must_be_a_real_bool(confirmed):
    assert reason_of(lambda: Perception(C, "e", INES, "evt", "witnessed", "a sound", at(1),
                                        "director", confirmed)).reason == "invalid_record"


def test_confirmed_has_no_default():
    with pytest.raises(TypeError):
        Perception(C, "e", INES, "evt", "witnessed", "a sound", at(1), "director")


def test_holders_and_refs_must_be_in_the_record_campaign():
    other = InstanceRef("campaign-b", "character", "ines")
    assert reason_of(lambda: witness(other, "e1", "x")).reason == "campaign_mismatch"
    assert reason_of(lambda: told("e1", TAVI, "p", 1, sender=other)).reason == "campaign_mismatch"
    assert reason_of(lambda: Event(C, "e", "x", at(0), "d", participants=(other,))).reason \
        == "campaign_mismatch"
    assert reason_of(lambda: Proposition(C, "p", "x", at(0), "d",
                                         about=(Subject("instance", str(other)),))).reason \
        == "campaign_mismatch"


@pytest.mark.parametrize("build", [
    lambda: Belief(C, "b", TAVI, "p", "certain", at(1), "director", ("e",)),
    lambda: Ruling(C, "r", "p", "reported", at(1), "director"),
    lambda: Belief(C, "b", TAVI, "p", "believed", at(1), "", ("e",)),
    lambda: Belief(C, "b", TAVI, "p", "believed", at(1), "director", ("e", "e")),
    lambda: Belief(C, "b", TAVI, "p", "believed", at(1), "director", ("e",), supersedes="b"),
    lambda: Belief(C, "b", TAVI, "p", "believed", at(1), "director", "rx"),  # one string, not ids
    lambda: Event(C, "E1", "x", at(0), "director"),
    lambda: Event(C, "e1", "", at(0), "director"),
    lambda: Event(C, "e1", "x", 0, "director"),
    lambda: Event(C, "e1", "x", at(0), "director", participants=("ines",)),
    lambda: Report(C, "r", TAVI, "p", "told", at(1), "director", True, addressed="yes"),
])
def test_field_validation_fails_closed(build):
    assert reason_of(build).reason == "invalid_record"


def test_an_established_ruling_needs_grounds():
    error = reason_of(lambda: Ruling(C, "r", "p", "established", at(1), "director"))
    assert (error.reason, error.records) == ("ungrounded_ruling", ("ruling:r",))


@pytest.mark.parametrize("build", [
    lambda: told("r", TAVI, "p", 3, sender=ORRIN, addressed=True),          # no sent_at
    lambda: told("r", TAVI, "p", 3, addressed=True, sent_at=1),             # no sender
    lambda: told("r", TAVI, "p", 3, sender=ORRIN, addressed=True, sent_at=4),  # sent after receipt
    lambda: told("r", TAVI, "p", 3, sender=ORRIN, sent_at=1),               # not addressed
])
def test_sent_at_is_exactly_for_addressed_reports(build):
    error = reason_of(build)
    assert (error.reason, error.records) == ("invalid_sent_at", ("evidence:r",))


def test_tuple_fields_are_copied_from_any_iterable():
    participants = [INES]
    event = Event(C, "e1", "x", at(0), "director", participants=participants)
    participants.append(TAVI)
    assert event.participants == (INES,)
    evidence = (e for e in ["a", "b"])
    assert Belief(C, "b", TAVI, "p", "believed", at(1), "director", evidence).evidence_ids == ("a", "b")


def test_records_are_frozen():
    event = betrayal_event()
    with pytest.raises(AttributeError):
        event.summary = "rewritten"  # type: ignore[misc]


@pytest.mark.parametrize("build", [
    lambda: Event(C, "e1", SECRET, SECRET, "director"),
    lambda: Event(C, SECRET, SECRET, at(0), "director"),
    lambda: Belief(C, "b", TAVI, "p", SECRET, at(1), SECRET, (SECRET,)),
    lambda: Subject("instance", SECRET),
    lambda: Ruling(C, "r", "p", SECRET, at(1), SECRET),
])
def test_errors_never_carry_record_text(build):
    error = reason_of(build)
    for text in (str(error), repr(error), repr(error.args), repr(error.records)):
        assert SECRET not in text
    assert error.__cause__ is None and error.__context__ is None


def test_knowledge_error_pickles_with_reason_and_records():
    error = KnowledgeError("unknown_event", ("perception:x",))
    copy = pickle.loads(pickle.dumps(error))
    assert (copy.reason, copy.records, str(copy)) == (error.reason, error.records, str(error))
    assert isinstance(copy, ValueError)

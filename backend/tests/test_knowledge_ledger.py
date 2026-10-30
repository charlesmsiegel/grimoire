"""012 ledger: one validated, immutable knowledge record set per campaign."""

import random

import pytest

from grimoire.core import (
    Belief, Event, InstanceRef, Knowledge, KnowledgeError, Perception, Proposition, Ruling, Subject,
)
from knowledge_fixtures import (
    ABOUT, BETRAYAL, C, INES, ORRIN, SUMMARY, TAVI, TRUTH, at, believes, betrayal_knowledge,
    betrayal_records, disclosure, told, witness,
)

RAIN = Event(C, "rain", "rain on the roofs", at(0), "director")
TAVI_RAIN = Perception(C, "p-tavi-rain", TAVI, "rain", "witnessed", "a drumming overhead", at(0),
                       "director", confirmed=True)
TOKENS = (SUMMARY, TRUTH, "SECRET")


def failure(**changes) -> KnowledgeError:
    with pytest.raises(KnowledgeError) as caught:
        betrayal_knowledge(**changes)
    error = caught.value
    for text in (str(error), repr(error), repr(error.args)):
        assert not [token for token in TOKENS if token in text]
    assert error.__cause__ is None and error.__context__ is None
    return error


def replaced(family: str, record_id: str, new) -> dict:
    records = betrayal_records()
    records[family] = [new if getattr(r, _id_field(family)) == record_id else r for r in records[family]]
    return records


def _id_field(family: str) -> str:
    return {"events": "event_id", "propositions": "proposition_id", "rulings": "ruling_id",
            "evidence": "evidence_id", "beliefs": "belief_id", "disclosures": "disclosure_id"}[family]


def build_failure(records: dict) -> KnowledgeError:
    with pytest.raises(KnowledgeError) as caught:
        Knowledge(C, **records)
    return caught.value


def test_the_betrayal_timeline_is_a_valid_ledger():
    knowledge = betrayal_knowledge()
    assert [e.evidence_id for e in knowledge.evidence] == sorted(e.evidence_id for e in knowledge.evidence)


def test_inputs_are_copied_and_order_independent():
    records = betrayal_records()
    shuffled = {family: random.Random(7).sample(items, len(items)) for family, items in records.items()}
    generators = {family: (item for item in items) for family, items in records.items()}
    one, two, three = Knowledge(C, **records), Knowledge(C, **shuffled), Knowledge(C, **generators)
    assert one == two == three
    records["evidence"].clear()
    assert one.evidence and one == two


@pytest.mark.parametrize("changes, reason, records", [
    (dict(events=[Event("campaign-b", "x", "elsewhere", at(0), "director")]),
     "campaign_mismatch", ("event:x",)),
    (dict(events=[Event(C, "evt-q7", "again", at(1), "director")]), "duplicate_id", ("event:evt-q7",)),
    (dict(evidence=[witness(INES, "p-orrin", "same id as Orrin's")]), "duplicate_id",
     ("evidence:p-orrin",)),
    (dict(propositions=[Proposition(C, "px", "x", at(0), "d", about=(Subject("event", "ghost"),))]),
     "unknown_event", ("proposition:px",)),
    (dict(events=[Event(C, "later", "later", at(9), "d")],
          propositions=[Proposition(C, "px", "x", at(3), "d", about=(Subject("event", "later"),))]),
     "about_future_event", ("proposition:px",)),
    (dict(rulings=[Ruling(C, "r9", "ghost", "unknown", at(3), "director")]),
     "unknown_proposition", ("ruling:r9",)),
    (dict(rulings=[Ruling(C, "r9", "duke-innocent", "unknown", at(1), "director")]),
     "before_proposition", ("ruling:r9",)),
    (dict(rulings=[Ruling(C, "r9", "duke-betrayed", "established", at(3), "d",
                          grounds=(Subject("event", "ghost"),))]), "unknown_event", ("ruling:r9",)),
    (dict(events=[Event(C, "later", "later", at(9), "d")],
          rulings=[Ruling(C, "r9", "duke-betrayed", "established", at(3), "d",
                          grounds=(Subject("event", "later"),))]),
     "ruling_cites_future", ("ruling:r9",)),
    (dict(rulings=[Ruling(C, "r9", "duke-innocent", "unknown", at(2), "director")]),
     "conflicting_rulings", ("ruling:r9", "ruling:rule-lie")),
    (dict(evidence=[witness(INES, "p-early", "too soon", sequence=0)]),
     "before_event", ("evidence:p-early",)),
    (dict(evidence=[Perception(C, "p-x", INES, "ghost", "witnessed", "x", at(1), "d", True)]),
     "unknown_event", ("evidence:p-x",)),
    (dict(evidence=[told("r-x", TAVI, "ghost", 3)]), "unknown_proposition", ("evidence:r-x",)),
    (dict(evidence=[told("r-x", TAVI, "duke-innocent", 1)]), "before_proposition", ("evidence:r-x",)),
    (dict(evidence=[told("r-x", TAVI, "duke-innocent", 3, sender=ORRIN, addressed=True, sent_at=1)]),
     "before_proposition", ("evidence:r-x",)),
    (dict(disclosures=[disclosure("d1", 0)]), "before_event", ("disclosure:d1",)),
    (dict(disclosures=[disclosure("d1", 3, "ghost")]), "unknown_event", ("disclosure:d1",)),
    (dict(beliefs=[believes("b-x", TAVI, "ghost", "believed", 5, ["r-orrin-tavi"])]),
     "unknown_proposition", ("belief:b-x",)),
    (dict(beliefs=[believes("b-x", ORRIN, "duke-innocent", "believed", 1, ["p-orrin"])]),
     "before_proposition", ("belief:b-x",)),
    (dict(beliefs=[believes("b-x", ORRIN, "duke-betrayed", "believed", 2, [])]),
     "unsupported_belief", ("belief:b-x",)),
    (dict(beliefs=[believes("b-x", ORRIN, "duke-betrayed", "believed", 2, ["ghost"])]),
     "unknown_evidence", ("belief:b-x", "evidence:ghost")),
    (dict(beliefs=[believes("b-x", TAVI, "duke-betrayed", "suspected", 2, ["p-ines"])]),
     "foreign_evidence", ("belief:b-x", "evidence:p-ines")),
    (dict(evidence=[told("r-maybe", TAVI, "duke-betrayed", 3, confirmed=False)],
          beliefs=[believes("b-x", TAVI, "duke-betrayed", "suspected", 3, ["r-maybe"])]),
     "unconfirmed_support", ("belief:b-x", "evidence:r-maybe")),
    (dict(beliefs=[believes("b-x", INES, "duke-betrayed", "believed", 1, ["p-ines"]),
                   believes("b-y", TAVI, "duke-betrayed", "suspected", 3, ["r-ines-tavi"])]),
     "future_evidence", ("belief:b-y", "evidence:r-ines-tavi")),
    (dict(events=[RAIN], evidence=[TAVI_RAIN],
          beliefs=[believes("b-x", TAVI, "duke-betrayed", "suspected", 1, ["p-tavi-rain"])]),
     "unrelated_support", ("belief:b-x",)),
    (dict(beliefs=[believes("b-x", ORRIN, "duke-betrayed", "believed", 2, ["p-orrin"],
                            supersedes="ghost")]),
     "unknown_belief", ("belief:b-x", "belief:ghost")),
    (dict(beliefs=[believes("b-x", TAVI, "duke-betrayed", "believed", 5, ["r-ines-tavi"],
                            supersedes="b-tavi-1")]),
     "invalid_supersession", ("belief:b-x", "belief:b-tavi-1")),
    (dict(beliefs=[believes("b-x", TAVI, "duke-innocent", "suspected", 2, ["r-orrin-tavi"],
                            supersedes="b-tavi-1")]),
     "invalid_supersession", ("belief:b-x", "belief:b-tavi-1")),
    (dict(beliefs=[believes("b-x", TAVI, "duke-innocent", "suspected", 5, ["r-ines-tavi"],
                            supersedes="b-tavi-1")]),
     "forked_supersession", ("belief:b-tavi-2", "belief:b-x")),
    (dict(beliefs=[believes("b-x", TAVI, "duke-betrayed", "suspected", 5, ["r-ines-tavi"])]),
     "unsuperseded_conflict", ("belief:b-tavi-3", "belief:b-x")),
])
def test_every_ledger_rule_fails_closed_with_named_records(changes, reason, records):
    error = failure(**changes)
    assert (error.reason, error.records) == (reason, records)


def test_a_contribution_backs_at_most_one_belief_of_a_holder():
    records = betrayal_records()
    records["beliefs"] = [
        Belief(C, "b-1", INES, "duke-betrayed", "believed", at(1, "s1", "t1"), "player-one",
               ("p-ines",), contribution_id="c-1"),
        Belief(C, "b-2", INES, "duke-innocent", "rejected", at(2, "s1", "t1"), "player-one",
               ("p-ines",), contribution_id="c-1"),
    ]
    error = build_failure(records)
    assert (error.reason, error.records) == ("reused_contribution", ("belief:b-2",))


def test_support_needs_only_one_related_item_and_contradiction_by_shared_subject_is_related():
    knowledge = betrayal_knowledge(events=[RAIN], evidence=[TAVI_RAIN], beliefs=[
        believes("b-orrin", ORRIN, "duke-innocent", "rejected", 2, ["p-orrin"]),
    ])
    assert knowledge.beliefs  # p-orrin perceived the betrayal, which duke-innocent is about
    mixed = believes("b-tavi-9", TAVI, "duke-betrayed", "suspected", 5, ["p-tavi-rain", "r-ines-tavi"],
                     supersedes="b-tavi-3")
    assert mixed in betrayal_knowledge(events=[RAIN], evidence=[TAVI_RAIN], beliefs=[mixed]).beliefs


def test_removing_cited_evidence_names_both_records_and_never_drops_the_belief():
    records = replaced("evidence", "r-orrin-tavi", None)
    records["evidence"] = [e for e in records["evidence"] if e is not None]
    error = build_failure(records)
    assert (error.reason, error.records) == ("unknown_evidence", ("belief:b-tavi-1", "evidence:r-orrin-tavi"))


def test_first_error_is_independent_of_input_order():
    records = betrayal_records()
    records["rulings"].append(Ruling(C, "r9", "ghost", "unknown", at(3), "director"))
    records["rulings"].append(Ruling(C, "r-aa", "duke-innocent", "unknown", at(1), "director"))
    records["beliefs"].append(believes("b-a", TAVI, "duke-betrayed", "believed", 5, ["ghost"]))
    records["beliefs"].append(Belief("campaign-b", "b-z", InstanceRef("campaign-b", "character", "x"),
                                     "p", "believed", at(1), "director", ("e",)))
    records["evidence"].append(told("r-b", TAVI, "ghost", 3))
    errors = set()
    for seed in range(8):
        shuffled = {f: random.Random(seed).sample(items, len(items)) for f, items in records.items()}
        error = build_failure(shuffled)
        errors.add((error.reason, error.records))
    assert errors == {("before_proposition", ("ruling:r-aa",))}  # same family: lowest id wins


def test_non_records_are_refused():
    with pytest.raises(KnowledgeError) as caught:
        Knowledge(C, events=["evt-q7"])
    assert caught.value.reason == "invalid_record"
    with pytest.raises(KnowledgeError):
        Knowledge("Campaign A")


def test_ledger_is_immutable():
    knowledge = betrayal_knowledge()
    with pytest.raises(AttributeError):
        knowledge.events = ()  # type: ignore[misc]


def test_propositions_and_rulings_may_rest_on_non_event_subjects():
    member = Subject("relation", "orrin-member-of-cult")
    knowledge = betrayal_knowledge(
        propositions=[Proposition(C, "orrin-in-cult", "Orrin serves the cult", at(0), "director",
                                  about=(member,))],
        rulings=[Ruling(C, "rule-cult", "orrin-in-cult", "established", at(0), "director",
                        grounds=(member,))],
    )
    assert len(knowledge.rulings) == 3


def test_about_and_grounds_events_are_kept_but_never_imply_access():
    knowledge = betrayal_knowledge()
    assert BETRAYAL in knowledge.propositions[0].about and ABOUT == knowledge.propositions[0].about


@pytest.mark.parametrize("supplied", [None, 3, "evt-q7", RAIN])
def test_a_family_must_be_an_iterable_of_records(supplied):
    with pytest.raises(KnowledgeError) as caught:
        Knowledge(C, events=supplied)
    assert caught.value.reason == "invalid_record"


def test_lookups_fail_as_knowledge_errors_without_echoing_ids():
    knowledge = betrayal_knowledge()
    for call, reason in ((lambda: knowledge.event("ghost"), "unknown_event"),
                         (lambda: knowledge.proposition(TRUTH), "unknown_proposition"),
                         (lambda: knowledge.proposition(["x"]), "unknown_proposition"),
                         (lambda: knowledge.subjects_of(TAVI_RAIN), "unknown_evidence")):
        with pytest.raises(KnowledgeError) as caught:
            call()
        assert caught.value.reason == reason and TRUTH not in str(caught.value)


def test_a_ledger_pickles_and_copies_to_an_equal_working_ledger():
    import copy
    import pickle
    knowledge = betrayal_knowledge()
    for clone in (pickle.loads(pickle.dumps(knowledge)), copy.deepcopy(knowledge)):
        assert clone == knowledge
        assert clone.event("evt-q7") == knowledge.event("evt-q7")

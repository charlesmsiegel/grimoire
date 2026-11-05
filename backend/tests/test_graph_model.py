"""Typed relations: predicates, validity in campaign time, ending and retraction (Sprint 017)."""

from __future__ import annotations

import pickle

import pytest

from grimoire.core.authority import PROTECTED_DOMAINS
from grimoire.core.identity import InstanceRef, RelationRef
from grimoire.core.json_value import JsonObject
from grimoire.core.knowledge import Moment
from grimoire.graph import PREDICATES, Ending, GraphError, Relation, Retraction

C = "c"
A, B, D = (InstanceRef(C, "character", name) for name in ("a", "b", "d"))
GUILD = InstanceRef(C, "organization", "guild")


def rel(rid="r-1", predicate="member_of", subject=A, obj=GUILD, since=3, **kwargs) -> Relation:
    moment = Moment(since) if type(since) is int and since >= 0 else since  # pass bad values through
    return Relation(RelationRef(C, rid), predicate, subject, obj, moment, kwargs.pop("provenance", "director"),
                    kwargs.pop("recorded_at", "2026-11-03T10:00:00Z"), **kwargs)


def ending(until=12) -> Ending:
    return Ending(Moment(until), "director", "2026-11-03T11:00:00Z")


RETRACTION = Retraction("director: recorded in error", "2026-11-03T12:00:00Z")


def test_predicate_registry_is_exact():
    assert {name: p.symmetric for name, p in PREDICATES.items()} == {
        "member_of": False, "located_in": False, "owns": False, "carries": False, "knows": False,
        "has_met": True, "kin_of": True, "allied_with": True,
        "in_party": False, "in_reserve": False,  # 033's membership (docs/PARTY.md)
    }
    assert {p.domain for p in PREDICATES.values()} == {"world_fact"}
    assert not {p.domain for p in PREDICATES.values()} & PROTECTED_DOMAINS
    assert all(p.name == name for name, p in PREDICATES.items())
    with pytest.raises(TypeError):
        PREDICATES["loves"] = PREDICATES["knows"]  # type: ignore[index]


@pytest.mark.parametrize("kwargs, reason", [
    ({"predicate": "loves"}, "unknown_predicate"),
    ({"predicate": 5}, "invalid_field"),
    ({"obj": A}, "self_relation"),
    ({"obj": InstanceRef("other", "organization", "guild")}, "campaign_mismatch"),
    ({"subject": "campaign:c/character/a"}, "invalid_field"),
    ({"since": -1}, "invalid_field"),
    ({"since": 3.0}, "invalid_field"),
    ({"provenance": " "}, "invalid_field"),
    ({"recorded_at": ""}, "invalid_field"),
    ({"ending": Ending(Moment(3), "director", "t")}, "invalid_until"),
    ({"ending": Ending(Moment(2), "director", "t")}, "invalid_until"),
    ({"ending": "12"}, "invalid_field"),
    ({"retraction": "oops"}, "invalid_field"),
    ({"extras": {"note": 1}}, "invalid_field"),
])
def test_relation_validates_fields(kwargs, reason):
    with pytest.raises(GraphError) as caught:
        rel(**kwargs)
    assert caught.value.reason == reason


@pytest.mark.parametrize("bad", [lambda: Ending("12", "d", "t"), lambda: Ending(Moment(1), "", "t"),
                                 lambda: Retraction("d", " ")])
def test_ending_and_retraction_validate(bad):
    with pytest.raises(GraphError) as caught:
        bad()
    assert caught.value.reason == "invalid_field"


def test_extras_are_kept_as_json_objects():
    relation = rel(extras=JsonObject({"x-note": [1, True]}))
    assert relation.extras == {"x-note": [1, True]}


def test_holds_at_interval():
    relation = rel(since=3, ending=ending(12))
    assert [relation.holds_at(s) for s in (2, 3, 11, 12)] == [False, True, True, False]
    assert relation.interval() == (3, 12)
    assert rel().interval() == (3, None) and rel().holds_at(10**6)
    retracted = rel(retraction=RETRACTION)
    assert not any(retracted.holds_at(s) for s in range(0, 20))


def test_symmetric_endpoints_unordered():
    met = rel(predicate="has_met", subject=A, obj=B)
    assert met.symmetric and met.endpoints() == frozenset({A, B})
    assert met.overlaps(rel("r-2", predicate="has_met", subject=B, obj=A))
    member = rel(predicate="member_of", subject=A, obj=B)
    assert not member.symmetric
    assert not member.overlaps(rel("r-2", predicate="member_of", subject=B, obj=A))
    assert member.overlaps(rel("r-2", predicate="member_of", subject=A, obj=B))
    assert not member.overlaps(rel("r-2", predicate="knows", subject=A, obj=B))


def test_overlap_needs_intersecting_intervals():
    past = rel("r-1", since=1, ending=ending(3))
    assert not past.overlaps(rel("r-2", since=5))
    assert not past.overlaps(rel("r-2", since=3))  # [1, 3) and [3, ...) only touch
    assert rel("r-1", since=1, ending=ending(6)).overlaps(rel("r-2", since=5))
    assert not rel("r-1", retraction=RETRACTION).overlaps(rel("r-2"))
    assert not rel("r-1").overlaps(rel("r-2", retraction=RETRACTION))


def test_ending_and_retraction_order():
    ended = rel().ended(ending(12))
    assert ended.ending == ending(12) and ended.provenance == "director"
    with pytest.raises(GraphError) as caught:
        ended.ended(ending(14))
    assert caught.value.reason == "already_ended"
    retracted = ended.retracted(RETRACTION)  # retracting an ended relation is allowed
    assert retracted.retraction == RETRACTION and retracted.ending == ending(12)
    for again in (lambda: retracted.ended(ending(14)), lambda: retracted.retracted(RETRACTION)):
        with pytest.raises(GraphError) as caught:
            again()
        assert caught.value.reason == "already_retracted"
    with pytest.raises(GraphError) as caught:
        rel(since=5).ended(ending(5))
    assert caught.value.reason == "invalid_until"


def test_graph_error_message_has_no_record_text_and_pickles():
    with pytest.raises(GraphError) as caught:
        rel(provenance="SECRET-TEXT", obj=A)
    assert "SECRET-TEXT" not in str(caught.value)
    error = GraphError("duplicate", ["r-1", "r-2"])
    assert str(error) == "duplicate: r-1, r-2"
    copy = pickle.loads(pickle.dumps(error))
    assert (copy.reason, copy.records) == ("duplicate", ("r-1", "r-2"))

"""Planning against the 017 graph (Sprint 021, pure): preconditions decide, names never do."""

from __future__ import annotations

import pytest

from grimoire.core import Moment
from grimoire.core.identity import RelationRef
from grimoire.effects import BeginStep, EffectError, EffectProposal, EndStep, Op, plan
from grimoire.graph import Ending, GraphIndex, Relation, Reopening, Retraction

from effects_fixtures import C, CAST, CHAT, GUILD, INES, KEY1, KEY2, TAM, WREN, give, moment


def rel(rid, predicate, subject, obj, since=1, until=None, retracted=False) -> Relation:
    return Relation(RelationRef(C, rid), predicate, subject, obj, Moment(since), "director", "t",
                    None if until is None else Ending(Moment(until), "director", "t"),
                    Retraction("d", "t") if retracted else None)


def index(*relations, instances=CAST) -> GraphIndex:
    return GraphIndex.build(C, relations, instances)


def reason(call) -> str:
    with pytest.raises(EffectError) as caught:
        call()
    return caught.value.reason


BASE = (rel("r-1", "carries", WREN, KEY1), rel("r-2", "carries", WREN, KEY2))


def test_give_plans_an_end_and_a_begin():
    assert plan(give(), index(*BASE)) == (EndStep(0, RelationRef(C, "r-1")), BeginStep(1))


def test_second_application_fails_the_precondition():
    after = (rel("r-1", "carries", WREN, KEY1, until=7), rel("r-2", "carries", WREN, KEY2),
             rel("r-3", "carries", TAM, KEY1, since=7))
    assert reason(lambda: plan(give(sequence=9), index(*after))) == "precondition_failed"
    assert reason(lambda: plan(give(), index(*after))) == "precondition_failed"


def test_never_matches_another_key_by_name():
    only_key2 = (rel("r-2", "carries", WREN, KEY2),)
    assert reason(lambda: plan(give(KEY1), index(*only_key2))) == "precondition_failed"


@pytest.mark.parametrize("relations, expected", [
    ((rel("r-1", "carries", WREN, KEY1, since=7),), "precondition_failed"),  # since == as_of
    ((rel("r-1", "carries", WREN, KEY1, until=9),), "precondition_failed"),  # a bounded stint
    ((rel("r-1", "carries", WREN, KEY1, retracted=True),), "precondition_failed"),
    ((rel("r-1", "carries", WREN, KEY1), rel("r-9", "carries", WREN, KEY1, since=3)), "ambiguous_relation"),
    ((rel("r-1", "carries", WREN, KEY1, since=8),), "precondition_failed"),  # not yet holding
])
def test_end_preconditions(relations, expected):
    assert reason(lambda: plan(give(), index(*relations))) == expected


def test_end_on_a_missing_endpoint_relation_is_not_walkable():
    relations = (rel("r-1", "carries", WREN, KEY1),)
    assert reason(lambda: plan(give(), index(*relations, instances=(WREN, TAM)))) == "precondition_failed"


def test_begin_preconditions():
    join = EffectProposal(C, CHAT, moment(), "Wren joins.", (Op("begin", "member_of", WREN, GUILD),), "director")
    assert plan(join, index()) == (BeginStep(0),)
    assert reason(lambda: plan(join, index(rel("r-5", "member_of", WREN, GUILD, since=9)))) == "precondition_failed"
    assert plan(join, index(rel("r-5", "member_of", WREN, GUILD, since=2, until=5))) == (BeginStep(0),)
    assert plan(join, index(rel("r-5", "member_of", WREN, GUILD, since=9, retracted=True))) == (BeginStep(0),)
    assert reason(lambda: plan(join, index(instances=(WREN,)))) == "missing_endpoint"
    met = EffectProposal(C, CHAT, moment(), "They meet.", (Op("begin", "has_met", TAM, INES),), "director")
    assert reason(lambda: plan(met, index(rel("r-6", "has_met", INES, TAM)))) == "precondition_failed"


def test_a_reopened_relation_can_be_ended_again_at_the_same_sequence():
    end7 = Ending(Moment(7), "effect:accepted/e-1", "t")
    reopened = rel("r-1", "carries", WREN, KEY1, until=7)
    reopened = Relation(reopened.ref, "carries", WREN, KEY1, Moment(1), "director", "t", end7)
    reopened = reopened.reopened(Reopening(end7, "effect:revoked/e-1", "t2"))
    assert plan(give(to=INES), index(reopened)) == (EndStep(0, RelationRef(C, "r-1")), BeginStep(1))


def test_index_of_another_campaign_is_refused():
    assert reason(lambda: plan(give(), GraphIndex.build("other", (), ()))) == "campaign_mismatch"

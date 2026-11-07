"""040 with 035 milestone 2: the structured changes an obstacle's costs and stakes make, as pure values.

Over the synthetic guarded archive (``obstacle_fixtures``): the bribe's purse is a 023 line from the acting
character to the guard, and the guard's report is a 017 relation. ``ACTOR`` stands for whoever attempts it.
"""

from __future__ import annotations

import pytest

from grimoire.adjudication import Decision, Outcome
from grimoire.holdings import Line
from grimoire.obstacles import (
    ACTOR, MAX_CHANGES, Approach, Attempt, Cost, HoldingsChange, ObstacleError, ObstacleStake, RelationChange,
    approach_from_payload, approach_to_payload, attempt_from_payload, attempt_to_payload, change_from_payload,
    change_to_payload, derive, obstacle_from_payload, obstacle_to_payload, resolve_approach,
)

from adjudication_fixtures import IRIS, MARA, Q, SOLA
from obstacle_fixtures import ARMED, BRIBE, FORCE, GREEDY, LOYAL, OWED, PURSE, REPORTED, WATCH, archive, attempt
from obstacle_fixtures import stakes_of

PAID = HoldingsChange("move", ACTOR, "crown", 12, IRIS, ACTOR)
TOLD = RelationChange("begin", "knows", IRIS, ACTOR)


def _reason(call, *args, **kwargs) -> str:
    with pytest.raises(ObstacleError) as caught:
        call(*args, **kwargs)
    return caught.value.reason


def priced():
    """The archive with the bribe's purse and the report as structured changes."""
    purse = Cost(PURSE.cost_id, PURSE.text, (PAID,))
    bribe = Approach(BRIBE.approach_id, BRIBE.text, BRIBE.risks, (purse, OWED))
    reported = ObstacleStake(REPORTED.stake_id, REPORTED.text, REPORTED.escalates, (TOLD,))
    obstacle = archive()
    return archive(approaches=tuple(bribe if a.approach_id == "bribe" else a for a in obstacle.approaches),
                   stakes=tuple(reported if s.stake_id == "reported" else s for s in obstacle.stakes))


def test_a_change_is_a_017_relation_or_a_023_line_naming_the_actor_or_a_reference():
    assert PAID.line(MARA) == Line.move(MARA, IRIS, "crown", 12)
    assert HoldingsChange("add", ACTOR, "crown", 3).line(MARA) == Line.add(MARA, "crown", 3)
    assert TOLD.refs() == (IRIS,) and PAID.refs() == (IRIS,)
    assert _reason(RelationChange, "begin", "adores", IRIS, ACTOR) == "unknown_predicate"
    assert _reason(RelationChange, "begin", "knows", ACTOR, ACTOR) == "self_relation"
    assert _reason(RelationChange, "renew", "knows", IRIS, ACTOR) == "invalid_field"
    assert _reason(RelationChange, "begin", "knows", "someone", ACTOR) == "invalid_field"  # only the bare word
    assert _reason(HoldingsChange, "adjust", ACTOR, "crown", 3) == "invalid_field"  # needs the count seen
    assert _reason(HoldingsChange, "move", ACTOR, "crown", 3) == "invalid_field"  # a move has a target
    assert _reason(HoldingsChange, "add", ACTOR, "crown", 3, IRIS) == "invalid_field"
    assert _reason(HoldingsChange, "move", ACTOR, "crown", 3, ACTOR) == "same_holder"
    assert _reason(HoldingsChange, "add", ACTOR, "crown", 0) == "invalid_field"  # 023's own rules
    assert _reason(HoldingsChange, "add", ACTOR, "two words", 1) == "invalid_field"
    assert _reason(Cost, "c", "A cost.", (TOLD,) * (MAX_CHANGES + 1)) == "invalid_field"
    assert _reason(Cost, "c", "A cost.", ("knows",)) == "invalid_field"


def test_a_change_names_only_the_actor_and_the_obstacles_own_actors():
    stranger = Cost("tip", "A tip for the porter.", (HoldingsChange("move", ACTOR, "crown", 1, SOLA, ACTOR),))
    tipped = Approach("tip", "Tip the porter.", frozenset(), (stranger,))
    assert _reason(archive, approaches=(*archive().approaches, tipped)) == "unlisted_actor"
    told = ObstacleStake("told", "Sola hears of it.", (), (RelationChange("begin", "knows", SOLA, ACTOR),))
    assert _reason(archive, stakes=(*archive().stakes, told)) == "unlisted_actor"
    assert _reason(resolve_approach, archive(), tipped) == "unlisted_actor"  # a novel approach's price too
    assert archive(actors=frozenset({IRIS, SOLA}), stakes=(*archive().stakes, told)).stake("told").changes


def test_changes_round_trip_and_an_obstacle_without_them_keeps_its_old_form():
    plain = obstacle_to_payload(archive())
    assert all("changes" not in stake for stake in plain["stakes"])  # entries recorded before read back unchanged
    assert all("changes" not in cost for a in plain["approaches"] for cost in a["costs"])
    payload = obstacle_to_payload(priced())
    assert obstacle_from_payload(Q, payload) == priced()
    (stake,) = [s for s in payload["stakes"] if s["stake_id"] == "reported"]
    assert stake["changes"] == [{"change": "relation", "kind": "begin", "predicate": "knows", "subject": str(IRIS),
                                 "object": "actor", "agent": None}]
    assert change_from_payload(change_to_payload(PAID)) == PAID
    empty = dict(payload, stakes=[dict(s, changes=[]) if s["stake_id"] == "reported" else s
                                  for s in payload["stakes"]])
    assert _reason(obstacle_from_payload, Q, empty) == "undecodable_entry"  # never written empty
    for bad in ({**change_to_payload(TOLD), "change": "spell"}, {**change_to_payload(TOLD), "extra": 1},
                {**change_to_payload(TOLD), "object": "campaign:q-1/character"},
                {**change_to_payload(PAID), "amount": "12"}):
        assert _reason(change_from_payload, bad) == "undecodable_entry"
    novel = Approach("forge", "Forge a requisition.", frozenset(), (Cost("ink", "Ink spent.", (TOLD,)),))
    assert approach_from_payload(approach_to_payload(novel)) == novel
    assert attempt_from_payload(attempt_to_payload("archive", "c10", novel, False))[3] == novel


def test_what_comes_due_carries_the_authors_changes():
    obstacle = priced()
    frame = attempt((GREEDY.factor("favours"), LOYAL.factor("hinders"), WATCH.factor("hinders")), stakes_of(BRIBE))
    won = derive(obstacle, 1, [Attempt("c10", obstacle.approach("bribe"), True)], {},
                 {"c10": Outcome(frame, "uncertain", Decision("partial", "adjudicated", {"greedy", "loyal"},
                                                              {"reported"}), "app:x")})
    assert [(c.kind, c.consequence_id, c.changes) for c in won.due()] == [
        ("stake", "reported", (TOLD,)), ("cost", "owed", ()), ("cost", "purse", (PAID,))]
    force = attempt((ARMED.factor("hinders"), WATCH.factor("hinders")), stakes_of(FORCE))
    lost = derive(obstacle, 1, [Attempt("c10", FORCE, True)], {},
                  {"c10": Outcome(force, "uncertain", Decision("failure", "adjudicated", set(), {"wounded"}), "app:x")})
    assert [(c.consequence_id, c.changes) for c in lost.due()] == [("wounded", ())]

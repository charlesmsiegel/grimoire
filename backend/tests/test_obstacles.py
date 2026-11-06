"""Open obstacles as pure values (Sprint 040): authoring checks, the frame rules, codecs and derivation."""

from __future__ import annotations

import pytest

from grimoire.adjudication import Decision, Outcome
from grimoire.core.identity import InstanceRef
from grimoire.obstacles import (
    Approach, Attempt, ObstacleError, ObstacleFact, ObstacleStake, approach_from_payload, approach_to_payload,
    attempt_from_payload, attempt_to_payload, check_attempt, derive, obstacle_from_payload, obstacle_to_payload,
)

from adjudication_fixtures import Q, moment
from obstacle_fixtures import (
    ALARM, BRIBE, FORCE, FORGE, GREEDY, LANTERN, LOYAL, REPORTED, SNEAK, SPENT, WATCH, WINDOWLESS, WOUNDED, archive,
    attempt, stakes_of,
)


def _reason(call, *args, **kwargs) -> str:
    with pytest.raises(ObstacleError) as caught:
        call(*args, **kwargs)
    return caught.value.reason


# -- authoring --------------------------------------------------------------------------------

def test_the_archive_offers_three_viable_suggestions_and_one_red_herring():
    assert archive().viable() == ("bribe", "force", "sneak")


def test_an_obstacle_with_one_answer_is_a_railroad():
    bricked = ObstacleFact("bricked", "constraint", "The door is bricked up.", 1, "event:e-bricks",
                           {"force": "prevents", "sneak": "prevents"})
    assert _reason(archive, facts=(GREEDY, LOYAL, WATCH, WINDOWLESS, bricked)) == "railroaded"
    honest = ObstacleFact("honest", "motive", "He cannot be bought or beaten.", 1, "event:e-honest",
                          {"bribe": "prevents", "force": "prevents"})
    assert _reason(archive, facts=(WATCH, WINDOWLESS, honest)) == "railroaded"  # only sneaking is left


@pytest.mark.parametrize("changes, reason", [
    (dict(facts=(GREEDY, ObstacleFact("wall", "constraint", "A wall.", 1, "event:e", {"*": "prevents"}))),
     "blanket_prevention"),
    (dict(stakes=(REPORTED, ObstacleStake("sealed", "Sealed.", {"*": "prevents"}))), "blanket_prevention"),
    (dict(facts=(GREEDY, ObstacleFact("dig", "clue", "Soft ground.", 1, "event:e", {"dig": "favours"}))),
     "unknown_approach"),
    (dict(stakes=(ALARM,)), "unknown_stake"),
    (dict(facts=(GREEDY, ObstacleFact("alarm", "clue", "A bell.", 1, "event:e", {}))), "duplicate_id"),
    (dict(approaches=(BRIBE, FORCE, Approach("bribe", "Again.", frozenset()))), "duplicate_id"),
    (dict(actors=frozenset({InstanceRef("q-9", "character", "x")})), "campaign_mismatch"),
], ids=["fact-wall", "stake-wall", "unsuggested", "undeclared-risk", "fact-stake-id", "approach-id", "campaign"])
def test_authoring_refusals(changes, reason):
    assert _reason(archive, **changes) == reason


def test_failure_may_close_a_suggestion_but_never_all_but_one():
    wall = ObstacleStake("walled", "The archivist bricks up the door.", {"force": "prevents", "sneak": "prevents"})
    assert _reason(archive, stakes=(REPORTED, SPENT, WOUNDED, ALARM, wall), approaches=(
        BRIBE, FORCE, SNEAK, Approach("climb", "Climb in by a window.", frozenset({"walled"})))) == "railroaded"
    assert archive().viable() == ("bribe", "force", "sneak")  # the guard's report closes only bribery


def test_an_escalation_is_never_authored():
    with pytest.raises(ObstacleError, match="escalation"):
        archive(facts=(GREEDY, ObstacleFact("x", "escalation", "x", 1, "event:e", {})))


# -- codecs -----------------------------------------------------------------------------------

def test_the_codecs_round_trip_exactly_and_refuse_a_non_canonical_form():
    payload = obstacle_to_payload(archive())
    assert obstacle_from_payload(Q, payload) == archive()
    shuffled = dict(payload, actors=payload["actors"], facts=list(reversed(payload["facts"])))
    assert _reason(obstacle_from_payload, Q, shuffled) == "undecodable_entry"
    assert _reason(obstacle_from_payload, "q-9", payload) == "undecodable_entry"
    assert approach_from_payload(approach_to_payload(FORGE)) == FORGE
    assert attempt_from_payload(attempt_to_payload("archive", "c10", FORGE, False)) == ("archive", "c10", "forge",
                                                                                      FORGE)
    assert attempt_from_payload(attempt_to_payload("archive", "c10", BRIBE, True)) == ("archive", "c10", "bribe",
                                                                                     None)


# -- the frame rules --------------------------------------------------------------------------

def test_an_attempt_cannot_come_before_the_obstacle_stands():
    # The labeller chooses the moment: one before the watch is established would leave the watch out.
    early = attempt((GREEDY.factor("favours"), LOYAL.factor("hinders")), stakes_of(BRIBE), at=moment(6, "t6"))
    assert archive().opens == 6
    assert _reason(check_attempt, archive(), archive().facts, Attempt("c10", BRIBE, True), early) == "premature"


def test_a_novel_approach_may_read_any_fact_its_own_way_but_must_carry_the_blanket_ones():
    obstacle, novel = archive(), Attempt("c10", FORGE, False)
    check_attempt(obstacle, obstacle.facts, novel,
                  attempt((LANTERN.factor("favours"), WATCH.factor("hinders")), (REPORTED.stake,)))
    assert _reason(check_attempt, obstacle, obstacle.facts, novel, attempt((), (REPORTED.stake,))) == "missing_fact"
    assert _reason(check_attempt, obstacle, obstacle.facts, novel,
                   attempt((WATCH.factor("favours"),), (REPORTED.stake,))) == "fact_changed"


# -- derivation -------------------------------------------------------------------------------

def _outcome(frame, result, cites, consequences=()):
    return Outcome(frame, "uncertain", Decision(result, "adjudicated", set(cites), set(consequences)), "app:x")


def _bribe_frame(cid="c10", at=None, extra=()):
    return attempt((GREEDY.factor("favours"), LOYAL.factor("hinders"), WATCH.factor("hinders")) + extra,
                   stakes_of(BRIBE), cid=cid, at=at)


def test_a_stake_escalates_once_and_records_out_of_turn_diverge():
    obstacle = archive()
    first = _bribe_frame()
    reported = derive(obstacle, 1, [Attempt("c10", BRIBE, True)], {}, {"c10": _outcome(first, "failure", {"loyal"},
                                                                                      {"reported"})})
    assert [f.fact_id for f in reported.facts].count("reported") == 1 and reported.status == "open"
    # A link whose frame was never written waits for it as the latest attempt, and is void once superseded.
    unframed = derive(obstacle, 1, [Attempt("c10", BRIBE, True)], {}, {})
    assert (unframed.status, unframed.pending.attempt.attempt_id) == ("open", "c10")
    superseded = derive(obstacle, 1, [Attempt("c10", BRIBE, True), Attempt("c11", BRIBE, True)],
                        {"c11": _bribe_frame("c11", moment(11, "t11"))}, {})
    assert [a.status for a in superseded.attempts] == ["void", "pending"]
    # A link after a pending attempt is not the order the service keeps.
    pending = derive(obstacle, 1, [Attempt("c10", BRIBE, True), Attempt("c11", SNEAK, True)], {"c10": first}, {})
    assert (pending.status, pending.problem) == ("diverged", "attempt_out_of_turn")

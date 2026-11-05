"""Sprint 033 pure values: membership state from relations, scene casts and presence (no files)."""

from __future__ import annotations

import pickle

import pytest

from grimoire.core import InstanceRef, Moment
from grimoire.core.identity import RelationRef
from grimoire.graph import PREDICATES, Ending, Relation, Retraction
from grimoire.party import (
    MEMBERSHIP, PRESENCES, PartyRefused, PartyState, SceneCast, cast_report, is_membership, presence, stage,
)

C = "c"
MARA, TOMA, INN = (InstanceRef(C, "character", n) for n in ("mara", "toma", "innkeeper"))
SNIK, GRUB = (InstanceRef(C, "creature", n) for n in ("goblin-1", "goblin-2"))
CREW = InstanceRef(C, "group", "crew")
EVERYONE = (MARA, TOMA, INN, SNIK, GRUB, CREW)


def rel(n, predicate, subject, obj, since, until=None, retracted=False) -> Relation:
    ending = None if until is None else Ending(Moment(until), "d", "t")
    return Relation(RelationRef(C, f"r-{n}"), predicate, subject, obj, Moment(since), "d", "t", ending,
                    Retraction("d", "t") if retracted else None)


def refused(call) -> PartyRefused:
    with pytest.raises(PartyRefused) as caught:
        call()
    return caught.value


def test_the_predicates_are_directed_world_facts():
    assert set(MEMBERSHIP) == {"in_party", "in_reserve"}
    assert all(not PREDICATES[name].symmetric and PREDICATES[name].domain == "world_fact" for name in MEMBERSHIP)


def test_state_at_a_sequence():
    relations = [rel(1, "in_party", SNIK, MARA, 1), rel(2, "in_party", GRUB, MARA, 1, until=4),
                 rel(3, "in_reserve", GRUB, MARA, 4), rel(4, "owns", MARA, GRUB, 1), rel(5, "member_of", INN, CREW, 0)]
    state = PartyState.build(C, relations, as_of=4)
    assert state.party(MARA) == ((SNIK,), (GRUB,)) and state.party(TOMA) == ((), ())
    assert state.membership(GRUB).relation == RelationRef(C, "r-3") and state.membership(INN) is None
    assert PartyState.build(C, relations, as_of=3).party(MARA) == ((SNIK, GRUB), ())
    assert PartyState.build(C, relations, as_of=0).memberships == {}
    assert state.complete and state.problems == ()
    assert not is_membership(relations[3]) and is_membership(relations[0]) and not is_membership("in_party")


def test_retracted_and_ended_memberships_do_not_hold():
    state = PartyState.build(C, [rel(1, "in_reserve", GRUB, MARA, 1, retracted=True),
                                 rel(2, "in_party", SNIK, MARA, 1, until=2)], as_of=2)
    assert state.memberships == {}


def test_two_memberships_at_once_are_a_problem_not_a_guess():
    state = PartyState.build(C, [rel(1, "in_party", SNIK, MARA, 1), rel(2, "in_reserve", SNIK, TOMA, 1)], as_of=1)
    assert state.conflicted == {SNIK} and state.memberships == {}
    assert [(p.code, p.member, p.relation_ids) for p in state.problems] == [
        ("conflicting_membership", SNIK, ("r-1", "r-2"))]
    cast = stage(state, "s", [MARA], instances=EVERYONE)
    assert presence(state, cast, SNIK) == "uncertain"
    assert cast_report(state, cast, [SNIK])[0].membership is None


def test_build_refuses_bad_input():
    assert refused(lambda: PartyState.build(C, [], as_of=-1)).reason == "invalid_field"
    assert refused(lambda: PartyState.build(C, [], as_of=True)).reason == "invalid_field"
    other = Relation(RelationRef("x", "r-1"), "in_party", InstanceRef("x", "creature", "a"),
                     InstanceRef("x", "character", "b"), Moment(0), "d", "t")
    assert refused(lambda: PartyState.build(C, [other], as_of=0)).reason == "cross_campaign"


def test_unreadable_files_make_the_state_incomplete():
    state = PartyState.build(C, [], as_of=0, unreadable=True)
    assert not state.complete and state.problems[0].code == "relations_unreadable"
    assert refused(lambda: stage(state, "s", [MARA], instances=EVERYONE)).reason == "relations_unreadable"


def test_stage_refusals():
    state = PartyState.build(C, [rel(1, "in_reserve", GRUB, MARA, 1)], as_of=1)
    ok = lambda chosen, known=EVERYONE: stage(state, "s", chosen, instances=known)
    assert ok([SNIK, MARA]).present == (MARA, SNIK)
    assert refused(lambda: ok([MARA, GRUB])).reason == "in_reserve"
    assert refused(lambda: ok([MARA, MARA])).reason == "duplicate_actor"
    assert refused(lambda: ok([str(MARA)])).reason == "invalid_field"
    assert refused(lambda: ok([InstanceRef("x", "character", "mara")])).reason == "cross_campaign"
    ghost = InstanceRef(C, "creature", "ghost")
    found = refused(lambda: ok([ghost, MARA]))
    assert found.reason == "missing_actor" and found.members == (ghost,)
    paired = InstanceRef(C, "character", "goblin-1")
    assert refused(lambda: ok([SNIK], (*EVERYONE, paired))).reason == "instance_id_conflict"
    many = [InstanceRef(C, "creature", f"g-{n}") for n in range(257)]
    assert refused(lambda: ok(many, many)).reason == "too_large"
    assert refused(lambda: stage("state", "s", [MARA], instances=EVERYONE)).reason == "invalid_field"
    with pytest.raises(ValueError):
        ok([MARA]).__class__(C, "Bad Scene", 1, (MARA,))


def test_scene_cast_is_validated_on_its_own():
    assert SceneCast(C, "s", 0, (SNIK, MARA)).present == (MARA, SNIK)
    assert SceneCast(C, "s", 0, ()).present == ()
    assert refused(lambda: SceneCast(C, "s", 0, [MARA])).reason == "invalid_field"
    assert refused(lambda: SceneCast(C, "s", 0, (MARA, MARA))).reason == "duplicate_actor"
    assert refused(lambda: SceneCast(C, "s", -1, ())).reason == "invalid_field"
    assert refused(lambda: SceneCast(C, "s", 0, (InstanceRef("x", "character", "a"),))).reason == "cross_campaign"


def test_presence_and_the_report_keep_control_membership_and_presence_apart():
    state = PartyState.build(C, [rel(1, "in_party", SNIK, MARA, 1), rel(2, "in_reserve", GRUB, MARA, 1),
                                 rel(3, "in_party", TOMA, CREW, 1)], as_of=2)
    cast = stage(state, "s", [MARA, INN], instances=EVERYONE)
    assert {ref: presence(state, cast, ref) for ref in EVERYONE} == {
        MARA: "present", INN: "present", SNIK: "accompanying", GRUB: "reserved", TOMA: "absent", CREW: "absent"}
    assert set(PRESENCES) >= {presence(state, cast, ref) for ref in EVERYONE}
    rows = cast_report(state, cast, [GRUB, MARA, GRUB, TOMA], controlled={TOMA})
    assert [row.instance for row in rows] == [MARA, TOMA, GRUB]
    assert [row.controlled for row in rows] == [False, True, False]
    assert rows[1].presence == "absent" and rows[1].membership.party == CREW  # controlled, in a party, not on stage
    assert refused(lambda: cast_report(state, cast, ["x"])).reason == "invalid_field"
    later = PartyState.build(C, [], as_of=3)
    assert refused(lambda: presence(later, cast, MARA)).reason == "stale_cast"


def test_anchors_above_and_a_recorded_cycle():
    state = PartyState.build(C, [rel(1, "in_party", MARA, CREW, 1), rel(2, "in_party", SNIK, MARA, 1)], as_of=1)
    assert state.anchors_above(SNIK) == (MARA, CREW) and state.anchors_above(CREW) == ()
    looped = PartyState.build(C, [rel(1, "in_party", MARA, CREW, 1), rel(2, "in_party", CREW, MARA, 1)], as_of=1)
    assert looped.anchors_above(MARA) == (CREW,)


def test_refusals_pickle():
    error = PartyRefused("m", "in_reserve", (GRUB,))
    copy = pickle.loads(pickle.dumps(error))
    assert (copy.reason, copy.members, str(copy)) == ("in_reserve", (GRUB,), "m")

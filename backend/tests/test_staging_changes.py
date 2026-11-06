"""Sprint 041: physical changes checked against the staging, and corrections (pure, synthetic)."""

from __future__ import annotations

import random

import pytest

from grimoire.core import InstanceRef
from grimoire.staging import (
    Absence, Change, actor_refusal, reach_refusal, Placement, Stage, StagingRefused, apply, close_way, drop, enter, give, leave, lock_way,
    move, open_way, place, remove, set_way, take, unlock_way,
)

from staging_fixtures import (
    C, COIN, EMMA, FRONT_DOOR, KEY, LETTER, MARA, ODD, SATCHEL, STUDY_DOOR, TOMA, ref, stage,
)

P = "scene:the-study/t1"


def do(s, *changes, start=None):
    sequence = start if start is not None else s.as_of
    for change in changes:
        sequence += 1
        s = apply(s, change, sequence=sequence, provenance=P)
    return s


def refused(reason, s, change, sequence=None):
    with pytest.raises(StagingRefused) as caught:
        apply(s, change, sequence=s.as_of + 1 if sequence is None else sequence, provenance=P)
    assert caught.value.reason == reason


def test_unlock_open_move_and_close():
    s = do(stage(), unlock_way(MARA, STUDY_DOOR), open_way(MARA, STUDY_DOOR), move(TOMA, STUDY_DOOR),
           close_way(TOMA, STUDY_DOOR), lock_way(MARA, STUDY_DOOR))
    assert s.placement(TOMA) == Placement(TOMA, area="hall", since=4)
    assert s.way(STUDY_DOOR).state == "locked" and s.way(STUDY_DOOR).since == 6 and s.as_of == 6


def test_door_rules():
    s = stage()
    refused("way_locked", s, move(TOMA, STUDY_DOOR))
    refused("way_locked", s, open_way(TOMA, STUDY_DOOR))
    refused("no_key", s, unlock_way(TOMA, STUDY_DOOR))
    refused("already_locked", s, lock_way(MARA, STUDY_DOOR))
    refused("not_locked", do(s, unlock_way(MARA, STUDY_DOOR)), unlock_way(MARA, STUDY_DOOR))
    opened = do(s, unlock_way(MARA, STUDY_DOOR), open_way(MARA, STUDY_DOOR))
    refused("still_open", opened, lock_way(MARA, STUDY_DOOR))
    refused("already_open", opened, open_way(MARA, STUDY_DOOR))
    refused("already_closed", do(s, unlock_way(MARA, STUDY_DOOR)), close_way(MARA, STUDY_DOOR))
    refused("no_lock", s, lock_way(EMMA, FRONT_DOOR))
    refused("way_elsewhere", s, close_way(MARA, FRONT_DOOR))
    refused("unknown_way", s, open_way(MARA, "window"))
    refused("way_closed", do(s, unlock_way(MARA, STUDY_DOOR)), move(MARA, STUDY_DOOR))


def test_a_key_in_a_bag_still_unlocks():
    s = stage(placements=(Placement(MARA, area="study"), Placement(TOMA, area="study"), Placement(EMMA, area="hall"),
                          Placement(SATCHEL, holder=MARA), Placement(KEY, holder=SATCHEL)))
    assert do(s, unlock_way(MARA, STUDY_DOOR)).way(STUDY_DOOR).state == "closed"


def test_leaving_takes_what_you_carry_and_entering_brings_it_back():
    s = do(stage(), unlock_way(MARA, STUDY_DOOR), open_way(MARA, STUDY_DOOR), move(MARA, STUDY_DOOR),
           leave(MARA, FRONT_DOOR))
    assert s.absence(MARA) == Absence(MARA, FRONT_DOOR, 5) and s.placement(MARA) is None
    assert s.root(COIN).absent == MARA and s.root(KEY).absent == MARA
    back = do(s, enter(MARA, FRONT_DOOR))
    assert back.placement(MARA).area == "hall" and back.root(COIN).area == "hall"
    refused("already_on_stage", back, enter(MARA, FRONT_DOOR))
    refused("leads_to_area", back, leave(MARA, STUDY_DOOR))
    refused("leads_off_stage", back, move(MARA, FRONT_DOOR))
    refused("not_on_stage", s, move(MARA, STUDY_DOOR))
    refused("not_on_stage", s, take(MARA, LETTER))


def test_an_absent_actor_works_the_door_to_off_stage_from_outside():
    s = do(stage(), close_way(EMMA, FRONT_DOOR))
    refused("way_closed", s, enter(ODD, FRONT_DOOR))
    s = do(s, open_way(ODD, FRONT_DOOR), enter(ODD, FRONT_DOOR))
    assert s.placement(ODD).area == "hall"
    refused("not_on_stage", do(stage(), leave(EMMA, FRONT_DOOR)), open_way(EMMA, STUDY_DOOR))


def test_take_drop_give():
    s = do(stage(), take(TOMA, LETTER))
    assert s.placement(LETTER) == Placement(LETTER, holder=TOMA, since=2)
    refused("already_held", s, take(TOMA, LETTER))
    refused("held_by_other", s, take(MARA, LETTER))
    refused("held_by_other", s, take(TOMA, COIN))
    s = do(s, give(TOMA, LETTER, MARA))
    assert s.placement(LETTER).holder == MARA
    refused("not_holding", s, drop(TOMA, LETTER))
    refused("not_holding", s, drop(MARA, COIN))
    s = do(s, drop(MARA, LETTER))
    assert s.placement(LETTER) == Placement(LETTER, area="study", since=4)
    assert do(s, take(MARA, COIN)).placement(COIN).holder == MARA  # out of her own satchel
    refused("recipient_elsewhere", s, give(MARA, KEY, EMMA))
    refused("is_self", s, give(MARA, KEY, MARA))
    refused("not_actor", s, give(MARA, SATCHEL, LETTER))
    refused("custody_cycle", s, give(MARA, KEY, KEY))
    refused("is_actor", s, take(MARA, TOMA))
    refused("is_self", s, take(MARA, MARA))
    refused("untracked", s, take(MARA, ref("cup", "item")))
    refused("elsewhere", do(s, place(LETTER, area="hall")), take(MARA, LETTER))
    refused("not_actor", s, take(SATCHEL, LETTER))
    refused("recipient_off_stage", s, give(MARA, KEY, ODD))


def test_a_restrained_actor_cannot_act_or_receive():
    s = do(stage(), place(TOMA, holder=MARA))
    refused("restrained", s, take(TOMA, LETTER))
    refused("recipient_restrained", s, give(MARA, KEY, TOMA))


def test_people_are_never_carried_off_or_handed_around():
    # Emma, set in Toma's arms by a correction, never leaves with him
    s = do(stage(door="open"), place(EMMA, holder=TOMA))
    s = do(s, move(TOMA, STUDY_DOOR))
    refused("holds_actor", s, leave(TOMA, FRONT_DOOR))
    refused("holds_actor", s, remove(TOMA))
    refused("carried_off_stage", s, place(TOMA, holder=ODD))
    refused("is_actor", s, drop(TOMA, EMMA))
    refused("is_actor", s, give(TOMA, EMMA, EMMA))
    # nor shut in a box that someone then picks up or hands over
    box = ref("box", "item")
    boxed = do(stage(), place(box, area="study"), place(EMMA, holder=box))
    refused("holds_actor", boxed, take(TOMA, box))
    held = do(stage(), place(box, holder=MARA), place(EMMA, holder=box))
    refused("holds_actor", held, give(MARA, box, TOMA))
    assert do(held, drop(MARA, box)).placement(box).area == "study"


def test_a_key_in_someone_elses_pocket_does_not_unlock():
    s = do(stage(), place(EMMA, holder=TOMA), place(KEY, holder=EMMA))
    refused("no_key", s, unlock_way(TOMA, STUDY_DOOR))


def test_refusal_helpers_refuse_malformed_input():
    for call in (lambda: actor_refusal(stage(), []), lambda: reach_refusal(stage(), MARA, []),
                 lambda: actor_refusal("stage", MARA)):
        with pytest.raises(StagingRefused) as caught:
            call()
        assert caught.value.reason == "invalid_field"


def test_corrections_need_no_physical_check_but_keep_the_invariants():
    s = do(stage(), place(TOMA, area="hall"))  # through a locked door: a correction
    assert s.placement(TOMA).area == "hall"
    s = do(s, set_way(STUDY_DOOR, "open", basis="assumed"))
    assert s.way(STUDY_DOOR).state == "open" and s.way(STUDY_DOOR).basis == "assumed"
    s = do(s, place(ODD, area="study", basis="assumed"))
    assert s.absence(ODD) is None and s.placement(ODD).basis == "assumed"
    s = do(s, remove(ODD))
    assert s.absence(ODD) == Absence(ODD, None, s.as_of)
    refused("already_absent", s, remove(ODD))
    s = do(s, remove(SATCHEL))
    assert s.placement(SATCHEL) is None and s.placement(COIN) is None
    refused("untracked", s, remove(SATCHEL))
    refused("custody_cycle", s, place(MARA, holder=KEY))
    refused("unknown_area", s, place(LETTER, area="attic"))
    refused("orphan_holder", s, place(LETTER, holder=ref("cup", "item")))
    sack = ref("sack", "item")
    s = do(s, place(sack, area="hall"), place(TOMA, holder=sack))
    refused("holds_actor", s, remove(sack))


def test_change_shapes_and_order():
    s = stage(as_of=4)
    refused("out_of_order", s, take(TOMA, LETTER), sequence=3)
    refused("out_of_order", s, take(TOMA, LETTER), sequence=4)  # one change per sequence
    assert apply(s, take(TOMA, LETTER), sequence=5, provenance=P).as_of == 5
    refused("campaign_mismatch", s, take(TOMA, InstanceRef("other", "item", "x")))
    for build in (lambda: Change("fly", who=MARA), lambda: Change("move", who=MARA),
                  lambda: Change("move", who=MARA, way=STUDY_DOOR, thing=KEY),
                  lambda: Change("take", who=MARA, thing=KEY, basis="assumed"),
                  lambda: place(MARA), lambda: place(MARA, area="hall", holder=KEY),
                  lambda: set_way(STUDY_DOOR, "ajar"), lambda: Change("take", who="mara", thing=KEY)):
        with pytest.raises(StagingRefused) as caught:
            build()
        assert caught.value.reason == "invalid_field"
    with pytest.raises(StagingRefused) as caught:
        apply(s, take(TOMA, LETTER), sequence=5, provenance="")
    assert caught.value.reason == "invalid_field"


def test_random_changes_never_break_a_stage_and_a_refusal_changes_nothing():
    rng = random.Random(41)
    individuals = (MARA, TOMA, EMMA, ODD, KEY, LETTER, SATCHEL, COIN)
    ways = (STUDY_DOOR, FRONT_DOOR, "window")
    builders = (
        lambda: move(rng.choice(individuals), rng.choice(ways)),
        lambda: leave(rng.choice(individuals), rng.choice(ways)),
        lambda: enter(rng.choice(individuals), rng.choice(ways)),
        lambda: take(rng.choice(individuals), rng.choice(individuals)),
        lambda: drop(rng.choice(individuals), rng.choice(individuals)),
        lambda: give(rng.choice(individuals), rng.choice(individuals), rng.choice(individuals)),
        lambda: rng.choice((open_way, close_way, lock_way, unlock_way))(rng.choice(individuals), rng.choice(ways)),
        lambda: place(rng.choice(individuals), area=rng.choice(("study", "hall"))),
        lambda: place(rng.choice(individuals), holder=rng.choice(individuals)),
        lambda: remove(rng.choice(individuals)),
        lambda: set_way(rng.choice(ways), rng.choice(("open", "closed", "locked"))),
    )
    applied = 0
    for _ in range(300):
        s = stage()
        for step in range(40):
            try:
                change = rng.choice(builders)()
            except StagingRefused:
                continue
            before = s
            try:
                s = apply(s, change, sequence=s.as_of + rng.choice((0, 1)), provenance=P)
                applied += 1
            except StagingRefused:
                assert s is before and s == before
                continue
            assert Stage(s.campaign_id, s.scene_id, s.as_of, s.areas, s.ways, s.actors, s.placements,
                         s.absences) == s
            assert set(s.actors) == {p.who for p in s.placements if p.who in s.actors} | {a.actor for a in s.absences}
    assert applied > 1000

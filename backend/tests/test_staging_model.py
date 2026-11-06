"""Sprint 041: stage values and their invariants (pure, synthetic)."""

from __future__ import annotations

import dataclasses
import pickle

import pytest

from grimoire.core import InstanceRef
from grimoire.staging import (
    MAX_ACTORS, MAX_AREAS, MAX_PLACEMENTS, MAX_WAYS, Absence, Area, Placement, Stage, StagingRefused, Way,
)

from staging_fixtures import (
    ACTORS, C, COIN, EMMA, FRONT_DOOR, KEY, LETTER, MARA, ODD, SATCHEL, SCENE, STUDY_DOOR, TOMA, ref, stage,
)


def refused(reason, call, *args, **kwargs):
    with pytest.raises(StagingRefused) as caught:
        call(*args, **kwargs)
    assert caught.value.reason == reason
    return caught.value


def test_a_stage_is_canonical_and_equal_stages_compare_equal():
    first = stage()
    second = Stage(C, SCENE, 1, tuple(reversed(first.areas)), tuple(reversed(first.ways)),
                   tuple(reversed(first.actors)), tuple(reversed(first.placements)), first.absences)
    assert first == second and hash(first.areas) == hash(second.areas)
    assert [a.area_id for a in first.areas] == ["hall", "study"]
    assert Way("w", ("study", "hall")).sides == ("hall", "study")
    assert Way("w", (None, "hall")).sides == ("hall", None)


def test_lookups_roots_and_custody():
    s = stage()
    assert s.root(COIN).area == "study" and s.root(COIN).holders == (SATCHEL, MARA)
    assert s.custody(MARA) == (KEY, SATCHEL, COIN)
    assert s.held_by(MARA) == (KEY, SATCHEL)
    assert s.root(ODD).absent == ODD and s.root(ref("nobody")).area is None
    assert s.on_stage() == (EMMA, MARA, TOMA)
    assert s.ways_between("study", "hall")[0].way_id == STUDY_DOOR
    assert s.way(FRONT_DOOR).to_off_stage and s.way(FRONT_DOOR).other("hall") is None


@pytest.mark.parametrize("reason, kwargs", [
    ("actor_unplaced", {"absences": ()}),
    ("placed_twice", {"absences": (Absence(ODD), Absence(MARA))}),
    ("not_cast", {"absences": (Absence(ODD), Absence(KEY))}),
    ("unknown_way", {"absences": (Absence(ODD, via="trapdoor"),)}),
    ("leads_to_area", {"absences": (Absence(ODD, via=STUDY_DOOR),)}),
    ("future_fact", {"absences": (Absence(ODD, since=2),)}),
    ("unknown_area", {"placements": (Placement(MARA, area="attic"), Placement(TOMA, area="study"),
                                     Placement(EMMA, area="hall"))}),
    ("orphan_holder", {"placements": (Placement(MARA, area="study"), Placement(TOMA, area="study"),
                                      Placement(EMMA, area="hall"), Placement(COIN, holder=SATCHEL))}),
    ("custody_cycle", {"placements": (Placement(MARA, area="study"), Placement(TOMA, area="study"),
                                      Placement(EMMA, area="hall"), Placement(COIN, holder=SATCHEL),
                                      Placement(SATCHEL, holder=COIN))}),
    ("placed_twice", {"placements": (Placement(MARA, area="study"), Placement(MARA, area="hall"),
                                     Placement(TOMA, area="study"), Placement(EMMA, area="hall"))}),
    ("carried_off_stage", {"placements": (Placement(MARA, area="study"), Placement(TOMA, area="study"),
                                          Placement(EMMA, holder=ODD))}),
    ("unknown_area", {"ways": (Way(STUDY_DOOR, ("study", "cellar")),)}),
    ("duplicate_id", {"ways": (Way("w", ("study", "hall")), Way("w", ("hall", None)))}),
])
def test_invariants_are_refused(reason, kwargs):
    refused(reason, stage, **kwargs)


def test_shapes_are_refused_without_text():
    for call in (lambda: Area("Bad Id"), lambda: Area("a", ""), lambda: Way("w", ("a", "a")),
                 lambda: Way("w", (None, None)), lambda: Way("w", ("a", "b"), "ajar"),
                 lambda: Way("w", "ab"), lambda: Placement(MARA), lambda: Placement(MARA, area="a", holder=KEY),
                 lambda: Placement(MARA, area="a", basis="guessed"), lambda: Placement(MARA, area="a", since=-1),
                 lambda: Placement(MARA, area="a", since=True), lambda: Absence("campaign:c1/character/x"),
                 lambda: Absence(MARA, basis="guessed"), lambda: Absence(MARA, basis=1),
                 lambda: stage(as_of=-1), lambda: Stage(C, "", 0, (Area("a"),)),
                 lambda: Stage(C, "s\ud800", 0, (Area("a"),)), lambda: Stage(C, "s", 0, ())):
        error = refused("invalid_field", call)
        assert str(error) == "invalid_field" and error.__cause__ is None and error.__context__ is None
    assert refused("custody_cycle", Placement, KEY, holder=KEY).reason == "custody_cycle"


def test_errors_pickle_with_their_reason_only():
    error = refused("campaign_mismatch", stage, placements=(
        Placement(MARA, area="study"), Placement(TOMA, area="study"), Placement(EMMA, area="hall"),
        Placement(InstanceRef("other", "item", "x"), area="study")))
    again = pickle.loads(pickle.dumps(error))
    assert type(again) is StagingRefused and again.reason == "campaign_mismatch" and again.args == ("campaign_mismatch",)


def test_a_stage_pickles_copies_and_converts():
    import copy
    s = stage()
    assert pickle.loads(pickle.dumps(s)) == s and copy.deepcopy(s) == s
    assert pickle.loads(pickle.dumps(s)).placement(COIN).holder == SATCHEL
    assert dataclasses.asdict(s)["scene_id"] == SCENE


def test_replace_is_checked_too():
    s = stage()
    refused("actor_unplaced", dataclasses.replace, s, absences=())
    refused("future_fact", dataclasses.replace, s, as_of=0, placements=s.placements + (Placement(ref("cup", "item"), area="hall", since=1),))


def test_bounds_at_and_over_the_limit():
    areas = tuple(Area(f"a{i}") for i in range(MAX_AREAS))
    assert len(Stage(C, SCENE, 0, areas).areas) == MAX_AREAS
    refused("too_large", Stage, C, SCENE, 0, areas + (Area("extra"),))
    ways = tuple(Way(f"w{i}", ("a0", None)) for i in range(MAX_WAYS))
    assert len(Stage(C, SCENE, 0, areas, ways).ways) == MAX_WAYS
    refused("too_large", Stage, C, SCENE, 0, areas, ways + (Way("extra", ("a0", None)),))
    actors = tuple(ref(f"p{i}") for i in range(MAX_ACTORS))
    absences = tuple(Absence(actor) for actor in actors)
    assert len(Stage(C, SCENE, 0, areas, (), actors, (), absences).actors) == MAX_ACTORS
    refused("too_large", Stage, C, SCENE, 0, areas, (), actors + (ref("extra"),), (), absences + (Absence(ref("extra")),))
    things = tuple(Placement(ref(f"t{i}", "item"), area="a0") for i in range(MAX_PLACEMENTS))
    assert len(Stage(C, SCENE, 0, areas, (), (), things).placements) == MAX_PLACEMENTS
    refused("too_large", Stage, C, SCENE, 0, areas, (), (), things + (Placement(ref("extra", "item"), area="a0"),))


def test_at_moves_a_stage_forward_only():
    s = stage(as_of=3)
    assert s.at(5).as_of == 5 and s.at(5).placements == s.placements
    refused("out_of_order", s.at, 2)
    refused("invalid_field", s.at, True)


def test_things_carried_by_an_absent_actor_stay_with_them():
    s = stage(placements=(Placement(MARA, area="study"), Placement(TOMA, area="study"), Placement(EMMA, area="hall"),
                          Placement(LETTER, holder=ODD)))
    assert s.root(LETTER).absent == ODD and s.root(LETTER).area is None
    assert KEY not in {p.who for p in s.placements}

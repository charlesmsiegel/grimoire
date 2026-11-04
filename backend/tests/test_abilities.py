"""Ability values and the ``abilities`` facet (025): authored constraints with invariants."""

import pickle

import pytest
from ability_fixtures import index_of, light_bending, person, speak_with_dead, thruster_pack

from grimoire.core import AuthorityError, CampaignRoster, InstanceRef
from grimoire.core.abilities import (
    MAX_ABILITIES, MAX_PARTS, MAX_TAGS, PROSE_MAX, Abilities, Ability, Cost, Effect, Limit, Prerequisite,
)
from grimoire.core.entity import EntityDefinition, EntityInstance
from grimoire.core.facet_errors import FacetError
from grimoire.core.facets import KNOWN_FACETS, FacetBundle, FacetSet, Physical, UnknownFacet
from grimoire.core.identity import SourceRef


def reason(build):
    with pytest.raises(FacetError) as caught:
        build()
    assert isinstance(caught.value, ValueError)
    return caught.value.reason


def effect(effect_id="veil", tags=("light",), **kwargs):
    return Effect(effect_id, "text", tags, **kwargs)


def ability(effects=None, **kwargs):
    return Ability("a", "A", effects if effects is not None else [effect()], **kwargs)


def test_abilities_is_a_known_facet_that_requires_nothing():
    assert KNOWN_FACETS["abilities"] is Abilities and Abilities.requires == frozenset()
    assert FacetSet([Abilities()]).names == ("abilities",)
    # An empty facet is valid: the entity has the facet and no powers.
    assert Abilities() == Abilities([]) and not Abilities().abilities


def test_three_constrained_abilities_are_valid_canonical_values():
    facet = Abilities([thruster_pack(), speak_with_dead(), light_bending()])
    assert [each.id for each in facet.abilities] == ["light_bending", "speak_with_dead", "thruster_pack"]
    bending = facet.by_id("light_bending")
    assert [e.id for e in bending.effects] == ["dazzle", "mirage", "veil"]
    assert bending.effect("veil").certainty == "reliable" and bending.effect("mirage").certainty == "uncertain"
    assert bending.effect("nope") is None and facet.by_id("nope") is None
    assert facet == Abilities([light_bending(), speak_with_dead(), thruster_pack()])
    assert hash(facet) == hash(Abilities([light_bending(), speak_with_dead(), thruster_pack()]))


def test_order_and_set_duplicates_never_matter():
    a = Ability("a", "A", [effect("e2", ("y",)), effect("e1", ("x", "x"))],
                limits=[Limit("l2", "t", ["q"]), Limit("l1", "t", ["p"])])
    b = Ability("a", "A", [effect("e1", ("x",)), effect("e2", ("y",))],
                limits=[Limit("l1", "t", {"p"}), Limit("l2", "t", {"q"})])
    assert a == b and a.effects[0].tags == frozenset({"x"})


def test_an_authored_contradiction_is_refused():
    # F1: a limit may not exclude what one of the same ability's effects declares.
    assert reason(lambda: ability(limits=[Limit("no_light", "t", {"light"})])) == "contradictory_ability"
    # Across abilities it is not a contradiction.
    Abilities([ability(), Ability("b", "B", [effect("e", ("dark",))], limits=[Limit("l", "t", {"light"})])])


@pytest.mark.parametrize("build, expected", [
    (lambda: Abilities([ability(), ability()]), "duplicate_ability"),
    (lambda: ability([effect("x"), effect("x", ("other",))]), "duplicate_ability_part"),
    (lambda: ability(limits=[Limit("veil", "t", {"force"})]), "duplicate_ability_part"),
    (lambda: ability(costs=[Cost("c", "t")], prerequisites=[Prerequisite("c", "t")]), "duplicate_ability_part"),
    (lambda: ability(costs=[Cost("c", "t", {"missing"})]), "unknown_applies_to"),
    (lambda: ability(prerequisites=[Prerequisite("p", "t", {"missing"})]), "unknown_applies_to"),
    (lambda: ability([]), "empty_ability"),
    (lambda: effect(tags=()), "invalid_field"),
    (lambda: Limit("l", "t", ()), "invalid_field"),
    (lambda: effect("Bad Id"), "invalid_field"),
    (lambda: effect(tags=("Not A Tag",)), "invalid_field"),
    (lambda: effect(tags="light"), "invalid_field"),
    (lambda: effect(tags={"light": 1}), "invalid_field"),
    (lambda: effect(tags=[["light"]]), "invalid_field"),
    (lambda: effect(tags=[f"t{n}" for n in range(MAX_TAGS + 1)]), "invalid_field"),
    (lambda: Effect("e", "  ", {"x"}), "invalid_field"),
    (lambda: Effect("e", 3, {"x"}), "invalid_field"),
    (lambda: Effect("e", "x" * (PROSE_MAX + 1), {"x"}), "invalid_field"),
    (lambda: effect(range=""), "invalid_field"),
    (lambda: effect(certainty="sometimes"), "invalid_field"),
    (lambda: effect(certainty=None), "invalid_field"),
    (lambda: Ability("a", "", [effect()]), "invalid_field"),
    (lambda: Ability("a", "A", [effect()], description=None), "invalid_field"),
    (lambda: Ability("a", "A", effect()), "invalid_field"),
    (lambda: Ability("a", "A", [Limit("l", "t", {"x"})]), "invalid_field"),
    (lambda: Abilities([effect()]), "invalid_field"),
    (lambda: Abilities(ability()), "invalid_field"),
    (lambda: Abilities([Ability(f"a{n}", "A", [effect()]) for n in range(MAX_ABILITIES + 1)]), "invalid_field"),
    (lambda: effect(extras={"tags": ["x"]}), "invalid_field"),
    (lambda: ability(extras={"effects": []}), "invalid_field"),
    (lambda: Abilities(extras={"facet": "agent"}), "invalid_field"),
    (lambda: Abilities(extras={"abilities": []}), "invalid_field"),
    (lambda: effect(extras={"x": float("nan")}), "invalid_field"),
    (lambda: Cost("c", "t", "e"), "invalid_field"),
    (lambda: effect(certainty=type("Loose", (str,), {})("uncertain")), "invalid_field"),
    (lambda: ability([effect(f"e{n}", (f"t{n}",)) for n in range(MAX_PARTS + 1)]), "invalid_field"),
    (lambda: ability(limits=[Limit(f"l{n}", "t", {f"x{n}"}) for n in range(MAX_PARTS + 1)]), "invalid_field"),
    (lambda: Ability("a", "A", [effect()], description="x" * (PROSE_MAX + 1)), "invalid_field"),
    (lambda: effect(range="x" * (PROSE_MAX + 1)), "invalid_field"),
    (lambda: Limit("l", "t", {f"x{n}" for n in range(MAX_TAGS + 1)}), "invalid_field"),
    (lambda: Cost("c", "t", {f"e{n}" for n in range(MAX_PARTS + 1)}), "invalid_field"),
    (lambda: effect(type("Loose", (str,), {})("veil")), "invalid_field"),
    (lambda: Effect("e", type("Loose", (str,), {})("t"), {"x"}), "invalid_field"),
])
def test_invalid_values_are_refused_with_a_reason(build, expected):
    assert reason(build) == expected


def test_subclassed_values_are_refused():
    class Sneaky(Effect):
        __slots__ = ()

    sneaky = Sneaky("veil", "t", frozenset({"light"}))
    assert reason(lambda: Ability("a", "A", [sneaky])) == "invalid_field"

    class SneakyAbility(Ability):
        __slots__ = ()

    assert reason(lambda: Abilities([SneakyAbility("a", "A", [effect()])])) == "invalid_field"


def test_extras_are_kept_verbatim_and_take_part_in_equality():
    plain, noted = effect(), effect(extras={"x-flavor": "shimmer", "extras": 1})
    assert noted.extras["x-flavor"] == "shimmer" and noted.extras["extras"] == 1
    assert plain != noted and effect(extras={"v": 1}) != effect(extras={"v": True})


def test_facet_error_moved_to_a_leaf_and_still_pickles():
    error = pickle.loads(pickle.dumps(FacetError("bad", "contradictory_ability")))
    assert error.reason == "contradictory_ability" and str(error) == "bad"


def test_an_unknown_facet_may_no_longer_be_called_abilities():
    # F11: older data that kept an `abilities` facet opaque must now decode as the real facet.
    assert reason(lambda: UnknownFacet("abilities", {"abilities": []})) == "known_name_as_unknown"
    assert len(FacetBundle([Abilities(), Physical()])) == 2


def test_abilities_never_confer_control():
    # F12: an object that describes powers is still not an agent and cannot be controlled.
    amulet = EntityDefinition(SourceRef("world:emberfall", "item", "amulet", "r1"), "Amulet", "individual",
                              FacetSet([Physical(), Abilities([light_bending()])]))
    ref = InstanceRef("c1", "item", "amulet-1")
    roster = CampaignRoster("c1", [EntityInstance(ref, amulet.ref)])
    with pytest.raises(AuthorityError):
        roster.assign_control(ref, "player:p1", index_of(amulet))
    # A powered person is controllable because of `agent`, not because of abilities.
    iris = person("iris", "Iris", light_bending())
    other = InstanceRef("c1", "character", "iris-1")
    assert CampaignRoster("c1", [EntityInstance(other, iris.ref)]).assign_control(
        other, "player:p1", index_of(iris)).actor_id == "iris-1"

"""Entity definitions, campaign instances and effective capabilities (sprint 009)."""

from dataclasses import replace

import pytest
from entity_fixtures import EMBER, grakka, index_of, mara, ogre, plain_sword, ref, vessa

from grimoire.core import CharacterDefinition, InstanceRef, MissingDefinition, SourceRef
from grimoire.core.entity import (
    EntityDefinition, EntityError, EntityInstance, Integrity, can_be_addressed, can_be_carried,
    can_be_damaged, can_be_heard, effective_entity, integrity, is_agent, propose_damage,
)
from grimoire.core.facets import (
    Agent, Biological, Communicative, Durable, FacetBundle, FacetSet, Physical, Portable,
    UnknownFacet,
)
from grimoire.core.json_value import JsonObject


def entity_reason(build):
    with pytest.raises(EntityError) as caught:
        build()
    assert isinstance(caught.value, ValueError)
    return caught.value.reason


def instance(kind, instance_id, source=None, campaign="c1", **fields):
    return EntityInstance(InstanceRef(campaign, kind, instance_id), source, **fields)


VESSA_R1 = EntityDefinition(  # built independently of the fixture builder
    SourceRef(EMBER, "item", "vessa", "r1"), "Vessa", "individual",
    FacetSet([Physical(material="steel", size="small"), Portable("light"), Durable(12), Agent(),
              Communicative(speaks={"speech"}, understands={"speech"})]),
    "A sentient sword")
LONGSWORD_R1 = EntityDefinition(
    SourceRef(EMBER, "item", "longsword", "r1"), "Longsword", "individual",
    FacetSet([Physical(material="steel"), Portable(), Durable(10)]))


def test_four_kinds_share_one_entity_model():
    definitions = (mara(), ogre(), grakka(), vessa())
    index = index_of(*definitions)
    assert all(type(index.resolve(d.ref)) is EntityDefinition for d in definitions)
    assert EntityDefinition.__subclasses__() == []
    assert {d.ref.kind for d in definitions} == {"character", "species", "creature", "item"}
    assert ogre().nature == "template" and grakka().nature == "individual"
    sword = effective_entity(instance("item", "vessa", vessa().ref), index)
    assert set(sword.facets.names) == {"agent", "communicative", "durable", "physical", "portable"}


def test_sentient_sword_capabilities():
    index = index_of(vessa())
    sword = instance("item", "vessa", vessa().ref.unpinned(), extras={"x-notes": [{"a": 1}]})
    view = effective_entity(sword, index)
    assert (is_agent(view), can_be_carried(view), can_be_damaged(view),
            can_be_heard(view), can_be_addressed(view)) == (True, True, True, True, True)
    assert integrity(view) == 12 and view.name == "Vessa" and view.resolved_source == VESSA_R1.ref
    damaged = propose_damage(sword, 5, index)
    assert integrity(effective_entity(damaged, index)) == 7
    assert damaged.extras == sword.extras and damaged.ref == sword.ref
    assert integrity(effective_entity(propose_damage(damaged, 50, index), index)) == 0
    assert integrity(effective_entity(sword, index)) == 12  # the proposal is a new value
    assert index.resolve(VESSA_R1.ref) == VESSA_R1


@pytest.mark.parametrize("amount", [0, -1, True, 2.0])
def test_damage_amount_is_a_positive_whole_number(amount):
    index = index_of(vessa())
    assert entity_reason(lambda: propose_damage(
        instance("item", "vessa", vessa().ref), amount, index)) == "invalid_field"


def test_a_template_is_not_an_actor_and_its_instances_are_independent():
    index = index_of(ogre())
    named = instance("creature", "ogre-1", ogre().ref, local_name="Grakka's cousin")
    unnamed = instance("creature", "ogre-2", ogre().ref)
    assert effective_entity(named, index).name == "Grakka's cousin"
    assert effective_entity(unnamed, index).name == "Ogre"
    hurt = propose_damage(named, 10, index)
    assert integrity(effective_entity(hurt, index)) == 20
    assert integrity(effective_entity(unnamed, index)) == 30
    # A template instance chooses its own kind; individual ones must not.
    assert effective_entity(instance("species", "odd", ogre().ref), index).ref.kind == "species"


def test_awakening_never_mutates_the_definition():
    index = index_of(plain_sword())
    awakened = instance("item", "blade", plain_sword().ref, campaign="c1",
                        added=FacetBundle([Agent(), Communicative(understands={"speech"})]))
    elsewhere = instance("item", "blade", plain_sword().ref, campaign="c2")
    assert can_be_addressed(effective_entity(awakened, index)) and is_agent(effective_entity(awakened, index))
    view = effective_entity(elsewhere, index)
    assert not can_be_addressed(view) and not is_agent(view)
    assert index.resolve(LONGSWORD_R1.ref) == LONGSWORD_R1
    assert dict(effective_entity(awakened, index).origins) == {
        "agent": "added", "communicative": "added", "durable": "trait", "physical": "trait",
        "portable": "trait"}


def test_pinned_and_unpinned_instances_diverge_independently():
    r1, r2 = vessa("r1"), vessa("r2", max_integrity=20)
    index = index_of(r1, r2, heads={r1.ref.unpinned(): "r2"})
    pinned = instance("item", "vessa-a", r1.ref, state=(Integrity(4),))
    following = instance("item", "vessa-b", r1.ref.unpinned())
    assert effective_entity(pinned, index).resolved_source == r1.ref
    assert effective_entity(following, index).resolved_source == r2.ref
    assert (integrity(effective_entity(pinned, index)), integrity(effective_entity(following, index))) == (4, 20)


def test_campaign_original_entities_need_a_name_and_have_no_source():
    original = instance("item", "pebble", None, local_name="A lucky pebble",
                        added=FacetBundle([Physical(), Portable("light")]))
    view = effective_entity(original, index_of())
    assert view.resolved_source is None and view.name == "A lucky pebble" and can_be_carried(view)
    assert entity_reason(lambda: instance("item", "nameless")) == "campaign_original_needs_name"


def test_a_namesake_from_another_world_is_never_used():
    elsewhere = vessa(source="world:b")
    stray = instance("item", "vessa", SourceRef("world:a", "item", "vessa", "r1"))
    with pytest.raises(MissingDefinition) as caught:
        effective_entity(stray, index_of(elsewhere))
    assert caught.value.reason == "missing_source"


def test_additions_must_satisfy_requirements():
    index = index_of(plain_sword())
    talking = instance("item", "blade", plain_sword().ref,
                       added=FacetBundle([Communicative(speaks={"speech"})]))
    assert entity_reason(lambda: effective_entity(talking, index)) == "missing_requirement"
    glowing = instance("item", "blade", plain_sword().ref, added=FacetBundle([UnknownFacet("x-glow")]))
    assert effective_entity(glowing, index).facets.by_name("x-glow") == UnknownFacet("x-glow")


def test_state_needs_its_facet_and_range():
    index = index_of(vessa(), mara())
    assert entity_reason(lambda: effective_entity(
        instance("character", "mara", mara().ref, state=(Integrity(3),)), index)) == "state_without_facet"
    assert entity_reason(lambda: effective_entity(
        instance("item", "vessa", vessa().ref, state=(Integrity(13),)), index)) == "invalid_state"
    assert entity_reason(lambda: propose_damage(
        instance("character", "mara", mara().ref), 1, index)) == "state_without_facet"
    assert entity_reason(lambda: instance("item", "v", vessa().ref,
                                          state=(Integrity(1), Integrity(2)))) == "duplicate_state"
    for bad in (-1, True, 1.0):
        assert entity_reason(lambda: Integrity(bad)) == "invalid_field"
    assert entity_reason(lambda: instance("item", "v", vessa().ref, state=("broken",))) == "invalid_field"


def test_replacements_and_additions_are_explicit():
    index = index_of(vessa())
    reforged = instance("item", "vessa", vessa().ref, replaced=FacetBundle([Physical(material="bronze")]))
    view = effective_entity(reforged, index)
    assert view.facets.get(Physical) == Physical(material="bronze")
    assert dict(view.origins)["physical"] == "replaced"
    assert entity_reason(lambda: effective_entity(instance(
        "item", "vessa", vessa().ref, added=FacetBundle([Physical()])), index)) == "added_shadows_trait"
    assert entity_reason(lambda: effective_entity(instance(
        "item", "vessa", vessa().ref, replaced=FacetBundle([Biological()])), index)) == "replaced_without_trait"
    assert entity_reason(lambda: instance("item", "vessa", vessa().ref, added=FacetBundle([Agent()]),
                                          replaced=FacetBundle([Agent()]))) == "added_shadows_trait"


def test_a_replacement_must_name_an_existing_trait_even_when_unknown():
    index = index_of(vessa())
    assert entity_reason(lambda: effective_entity(instance(
        "item", "vessa", vessa().ref, replaced=FacetBundle([UnknownFacet("agent-v2")])), index)
    ) == "replaced_without_trait"


def test_kind_and_definition_type_rules():
    index = index_of(vessa())
    assert entity_reason(lambda: effective_entity(
        instance("character", "vessa", vessa().ref), index)) == "kind_mismatch"
    legacy = CharacterDefinition(ref("character", "wren"), "Wren")
    assert entity_reason(lambda: effective_entity(
        instance("character", "wren", legacy.ref), index_of(legacy))) == "not_entity_definition"


def test_source_drift_breaks_unpinned_instances_with_named_reasons_and_pinning_repairs():
    r1 = plain_sword("r1")
    gains_agent = replace(r1, ref=r1.ref.at("r2"), traits=FacetSet([*r1.traits, Agent()]))
    weaker = replace(r1, ref=r1.ref.at("r2"), traits=FacetSet([Physical(material="steel"), Portable(), Durable(3)]))
    intangible = replace(r1, ref=r1.ref.at("r2"), traits=FacetSet([Agent()]))
    cases = [
        (gains_agent, {"added": FacetBundle([Agent()])}, "added_shadows_trait"),
        (weaker, {"state": (Integrity(8),)}, "invalid_state"),
        # Dropping Physical strands an added facet that requires it.
        (intangible, {"added": FacetBundle([Biological()])}, "missing_requirement"),
    ]
    for r2, fields, expected in cases:
        before = index_of(r1)
        after = index_of(r1, r2, heads={r1.ref.unpinned(): "r2"})
        following = instance("item", "blade", r1.ref.unpinned(), **fields)
        effective_entity(following, before)
        assert entity_reason(lambda: effective_entity(following, after)) == expected
        pinned = replace(following, source=r1.ref)
        assert effective_entity(pinned, after).resolved_source == r1.ref


def test_definition_validation():
    traits = FacetSet([Agent()])
    assert entity_reason(lambda: EntityDefinition(ref("item", "x").unpinned(), "X", "individual", traits)
                         ) == "invalid_field"
    assert entity_reason(lambda: EntityDefinition(ref("item", "x"), "X", "species", traits)) == "invalid_field"
    assert entity_reason(lambda: EntityDefinition(ref("item", "x"), "X", ["individual"], traits)
                         ) == "invalid_field"
    assert entity_reason(lambda: EntityDefinition(ref("item", "x"), " ", "individual", traits)) == "invalid_field"
    assert entity_reason(lambda: EntityDefinition(ref("item", "x"), "X", "individual",
                                                  FacetBundle([Agent()]))) == "invalid_field"
    assert entity_reason(lambda: EntityDefinition(ref("item", "x"), "X", "individual", traits,
                                                  extras={"v": float("nan")})) == "invalid_field"
    defined = EntityDefinition(ref("item", "x"), "X", "individual", traits, extras={"x-art": [1]})
    assert defined.extras == JsonObject({"x-art": (1,)}) and hash(defined)


ADDRESS = ["ref", "address", "source_id", "campaign_id", "schema", "kind", "entity_id", "instance_id",
           "revision"]


@pytest.mark.parametrize("key", ["name", "nature", "traits", "description", *ADDRESS])
def test_definition_extras_cannot_shadow_fields_or_the_address(key):
    assert entity_reason(lambda: EntityDefinition(ref("item", "x"), "X", "individual",
                                                  FacetSet([Agent()]), extras={key: 1})) == "invalid_field"


@pytest.mark.parametrize("key", ["source", "local_name", "added", "replaced", "state", *ADDRESS])
def test_instance_extras_cannot_shadow_fields_or_the_address(key):
    assert entity_reason(lambda: instance("item", "x", vessa().ref, extras={key: 1})) == "invalid_field"


def test_instances_normalize_bundles_and_are_hashable():
    as_set = instance("item", "x", vessa().ref, added=FacetSet([Agent()]))
    as_bundle = instance("item", "x", vessa().ref, added=FacetBundle([Agent()]))
    assert as_set == as_bundle and type(as_set.added) is FacetBundle and hash(as_set) == hash(as_bundle)
    assert entity_reason(lambda: instance("item", "x", vessa().ref, added=[Agent()])) == "invalid_field"
    assert entity_reason(lambda: instance("item", "x", "world:a/item/x@r1")) == "invalid_field"


def test_absent_facets_answer_no():
    view = effective_entity(instance("character", "mara", mara().ref), index_of(mara()))
    assert (can_be_carried(view), can_be_damaged(view), integrity(view)) == (False, False, None)
    stone = instance("item", "stone", None, local_name="Stone", added=FacetBundle([Physical()]))
    view = effective_entity(stone, index_of())
    assert (is_agent(view), can_be_heard(view), can_be_addressed(view)) == (False, False, False)
    listener = effective_entity(instance("item", "ear", None, local_name="Ear", added=FacetBundle(
        [Agent(), Communicative(understands={"sign"})])), index_of())
    assert can_be_addressed(listener) and not can_be_heard(listener)


def test_extras_equality_keeps_json_types_apart():
    flagged = [EntityDefinition(ref("item", "x"), "X", "individual", FacetSet([Agent()]), extras={"x-flag": v})
               for v in (True, 1, 1.0)]
    assert len(set(flagged)) == 3


def test_state_subclasses_are_not_state():
    class Shadow(Integrity):
        pass

    assert entity_reason(lambda: instance("item", "v", vessa().ref, state=(Integrity(1), Shadow(2)))
                         ) == "invalid_field"

"""The Wave 1 character types bridge explicitly into entities (sprint 009)."""

import pytest
from entity_fixtures import index_of, plain_sword, ref, vessa

from grimoire.core import (
    CampaignActor, CharacterDefinition, ControlAssignment, Event, IdentityConflict, InstanceRef,
    Knowledge, KnowledgeError, Moment, Perception, PlayerContribution, actor_knowledge, actor_view,
    submit_contribution,
)
from grimoire.core.entity import (
    EntityDefinition, EntityError, EntityInstance, effective_entity, entity_from_character,
    instance_from_actor,
)
from grimoire.core.facets import Agent, FacetBundle, FacetSet, Physical
from grimoire.core.roster import CampaignRoster

WREN = CharacterDefinition(ref("character", "wren"), "Wren", "A courier")
ACTOR = CampaignActor("c1", "pc-1", source=WREN.ref)
VESSA = InstanceRef("c1", "item", "vessa")


def seen(evidence_id, holder, percept):
    return Perception("c1", evidence_id, holder, "e1", "sight", percept, Moment(1), "director",
                      confirmed=True)


LEDGER = Knowledge("c1", events=[Event("c1", "e1", "The door was forced", Moment(1), "director")],
                   evidence=[seen("seen-1", ACTOR.ref, "A door opened"),
                             seen("seen-2", InstanceRef("c1", "character", "pc-2"), "Someone else saw it"),
                             seen("felt-1", VESSA, "Gripped")])
KNOWS = actor_knowledge(LEDGER, ACTOR.ref, as_of=1)


def test_conversion_keeps_identity_and_asserts_only_agency():
    entity = entity_from_character(WREN)
    assert entity == EntityDefinition(WREN.ref, "Wren", "individual", FacetSet([Agent()]), "A courier")
    converted = instance_from_actor(CampaignActor("c1", "pc-1", local_name="Wren"))
    assert converted == EntityInstance(InstanceRef("c1", "character", "pc-1"), None, "Wren",
                                       added=FacetBundle([Agent()]))
    assert instance_from_actor(ACTOR) == EntityInstance(ACTOR.ref, WREN.ref)


def test_a_converted_campaign_original_actor_stays_an_agent():
    original = CampaignActor("c1", "pc-1", local_name="Wren")
    instance, empty = instance_from_actor(original), index_of()
    assert actor_view(instance, empty, KNOWS) == actor_view(original, empty, KNOWS)
    roster = CampaignRoster("c1", [instance])
    control = roster.assign_control(instance.ref, "human:alice", empty)
    assert control == ControlAssignment("c1", "pc-1", "human:alice")
    assert roster.check_controls([control], empty) == ()


def test_legacy_actor_end_to_end_keeps_its_view_and_its_player():
    legacy_view = actor_view(ACTOR, index_of(WREN), KNOWS)
    converted_index = index_of(entity_from_character(WREN))
    instance = instance_from_actor(ACTOR)
    assert actor_view(instance, converted_index, KNOWS) == legacy_view
    assert actor_view(ACTOR, converted_index, KNOWS) == legacy_view
    assert effective_entity(instance, converted_index).name == "Wren"
    old_control = (ControlAssignment("c1", "pc-1", "human:alice"),)
    line = PlayerContribution("c1", "k1", "s1", "t1", instance.ref.instance_id, "human:alice",
                              "speech", "I'll carry it.")
    assert submit_contribution(line, old_control) == line


def test_legacy_and_converted_definitions_cannot_share_an_index():
    with pytest.raises(IdentityConflict):
        index_of(WREN, entity_from_character(WREN))


def test_actor_views_need_an_agent():
    index = index_of(plain_sword(), vessa())
    sword = EntityInstance(InstanceRef("c1", "item", "blade"), plain_sword().ref)
    with pytest.raises(EntityError) as caught:
        actor_view(sword, index)
    assert caught.value.reason == "not_agent"
    talking = EntityInstance(VESSA, vessa().ref)
    view = actor_view(talking, index, actor_knowledge(LEDGER, VESSA, as_of=1))
    assert (view.actor_id, view.name, len(view.knowledge.evidence)) == ("vessa", "Vessa", 1)
    assert "The door was forced" not in repr(view)  # the sword's own percept, not director truth
    with pytest.raises(KnowledgeError) as mismatch:  # another actor's projection is refused
        actor_view(talking, index, KNOWS)
    assert mismatch.value.reason == "actor_mismatch"


def test_the_legacy_type_cannot_bypass_the_agent_rule():
    statue = EntityDefinition(ref("character", "statue"), "Statue", "individual", FacetSet([Physical()]))
    with pytest.raises(EntityError) as caught:
        actor_view(CampaignActor("c1", "statue", source=statue.ref), index_of(statue))
    assert caught.value.reason == "not_agent"

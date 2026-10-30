"""V2 first-slice scenarios. No user library or provider access is needed."""

from dataclasses import replace

import pytest

from grimoire.core import (
    Belief, CampaignActor, CharacterDefinition, ControlAssignment, Event, InstanceRef,
    MissingDefinition, Observation, SourceRef, actor_view,
)


def test_same_definition_has_independent_campaign_instances_and_knowledge():
    root = SourceRef("catalog:fiction", "character", "mara", "revision-1")
    definition = CharacterDefinition(root, "Mara", "An observant negotiator")
    a = CampaignActor("campaign-a", "mara-a", source=root)
    b = CampaignActor("campaign-b", "mara-b", source=root)
    event = Event("campaign-a", "secret-1", "The duke secretly betrayed the guild")
    observation = Observation("campaign-a", a.actor_id, event.event_id,
                              "overheard", "The duke was named in a private accusation")
    mistaken = Belief("campaign-a", a.actor_id,
                     "The accused duke must be guilty", "suspected", (event.event_id,))
    lookup = {root: definition}
    a_view = actor_view(a, lookup, [observation], [mistaken])
    b_view = actor_view(b, lookup, [observation], [mistaken])
    assert a_view.name == b_view.name == "Mara"
    assert len(a_view.observations) == len(a_view.beliefs) == 1
    assert b_view.observations == b_view.beliefs == ()
    assert event.description not in repr(a_view)  # subjective evidence, not director truth


def test_world_variants_cannot_be_matched_by_name():
    a_ref = SourceRef("world:a", "character", "mara", "r1")
    b_ref = SourceRef("world:b", "character", "mara", "r1")
    b_definition = CharacterDefinition(b_ref, "Mara", "A different portrayal")
    actor = CampaignActor("campaign-a", "one", source=a_ref)
    with pytest.raises(MissingDefinition):
        actor_view(actor, {b_ref: b_definition})


def test_control_changes_without_changing_actor_identity_or_history():
    original = CampaignActor("campaign-a", "mara", local_name="Mara")
    npc = ControlAssignment("campaign-a", "mara", "director")
    pc = replace(npc, controller_id="human:player-one")
    assert npc != pc
    assert original == CampaignActor("campaign-a", "mara", local_name="Mara")
    assert actor_view(original, {}).name == "Mara"


def test_actor_view_is_an_immutable_snapshot():
    actor = CampaignActor("campaign-a", "mara", local_name="Mara")
    observations = [Observation("campaign-a", "mara", "e1", "sight", "The door opened")]
    view = actor_view(actor, {}, observations)
    observations.append(Observation("campaign-a", "mara", "e2", "sight", "Someone entered"))
    assert len(view.observations) == 1
    assert len(actor_view(actor, {}, observations).observations) == 2


def test_source_and_actor_validation():
    with pytest.raises(ValueError):
        SourceRef("", "character", "x")
    with pytest.raises(ValueError):
        CampaignActor("campaign-a", "anonymous")
    with pytest.raises(ValueError):
        CharacterDefinition(SourceRef("world:a", "item", "ring"), "Ring")


def test_definitions_are_always_pinned():
    with pytest.raises(ValueError):
        CharacterDefinition(SourceRef("world:emberfall", "character", "vessa"), "Vessa")


@pytest.mark.parametrize("build", [
    lambda: CampaignActor("Campaign A", "vessa", local_name="Vessa"),
    lambda: CampaignActor("campaign-a", "Vessa", local_name="Vessa"),
    lambda: ControlAssignment("campaign a", "vessa", "director"),
    lambda: Observation("campaign-a", "Vessa", "e1", "sight", "A door opened"),
    lambda: Belief("campaign-a", "vessa ", "The door is open", "believed"),
    lambda: Event("", "e1", "A door opened"),
])
def test_campaign_and_actor_ids_obey_identity_grammar(build):
    with pytest.raises(ValueError):
        build()


def test_campaign_actor_has_an_instance_ref():
    actor = CampaignActor("campaign-a", "vessa-a", local_name="Vessa")
    assert actor.ref == InstanceRef("campaign-a", "character", "vessa-a")
    assert str(actor.ref) == "campaign:campaign-a/character/vessa-a"


def test_model_reexports_the_identity_source_ref():
    from grimoire.core import identity, model
    assert model.SourceRef is identity.SourceRef

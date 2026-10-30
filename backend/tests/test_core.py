"""V2 first-slice scenarios. No user library or provider access is needed."""

from dataclasses import replace

import pytest

from grimoire.core import (
    DIRECTOR, Belief, CampaignActor, CharacterDefinition, ControlAssignment, DefinitionIndex, Event,
    InstanceRef, Knowledge, KnowledgeError, MissingDefinition, Moment, Perception, SourceDescriptor,
    SourceRef, actor_knowledge, actor_view, director_knowledge,
)


def index_of(*definitions, heads=None):
    sources = sorted({definition.ref.source for definition in definitions})
    return DefinitionIndex(
        [SourceDescriptor(source, owner="user") for source in sources], definitions, heads
    )


EMPTY = DefinitionIndex([], [])


def _witnessed(campaign_id, actor_id, evidence_id, percept, sequence=1):
    holder = InstanceRef(campaign_id, "character", actor_id)
    return Perception(campaign_id, evidence_id, holder, "secret-1", "overheard", percept,
                      Moment(sequence), "director", confirmed=True)


def test_same_definition_has_independent_campaign_instances_and_knowledge():
    root = SourceRef("catalog:fiction", "character", "mara", "revision-1")
    definition = CharacterDefinition(root, "Mara", "An observant negotiator")
    a = CampaignActor("campaign-a", "mara-a", source=root)
    b = CampaignActor("campaign-b", "mara-b", source=root)
    event = Event("campaign-a", "secret-1", "The duke secretly betrayed the guild", Moment(1), "director")
    knowledge = Knowledge("campaign-a", events=[event], evidence=[
        _witnessed("campaign-a", a.actor_id, "heard", "The duke was named in a private accusation")])
    lookup = index_of(definition)
    a_view = actor_view(a, lookup, actor_knowledge(knowledge, a.ref, as_of=1))
    assert a_view.name == actor_view(b, lookup).name == "Mara"
    assert len(a_view.knowledge.evidence) == 1
    with pytest.raises(KnowledgeError) as caught:  # campaign A's ledger has nothing for campaign B
        actor_knowledge(knowledge, b.ref, as_of=1)
    assert caught.value.reason == "campaign_mismatch"
    assert event.summary not in repr(a_view)  # subjective evidence, not director truth


def test_world_variants_cannot_be_matched_by_name():
    a_ref = SourceRef("world:a", "character", "mara", "r1")
    b_ref = SourceRef("world:b", "character", "mara", "r1")
    b_definition = CharacterDefinition(b_ref, "Mara", "A different portrayal")
    actor = CampaignActor("campaign-a", "one", source=a_ref)
    with pytest.raises(MissingDefinition) as caught:
        actor_view(actor, index_of(b_definition))
    assert caught.value.reason == "missing_source"


def test_control_changes_without_changing_actor_identity_or_history():
    original = CampaignActor("campaign-a", "mara", local_name="Mara")
    npc = ControlAssignment("campaign-a", "mara", "director")
    pc = replace(npc, controller_id="human:player-one")
    assert npc != pc
    assert original == CampaignActor("campaign-a", "mara", local_name="Mara")
    assert actor_view(original, EMPTY).name == "Mara"


def test_actor_view_is_an_immutable_snapshot():
    actor = CampaignActor("campaign-a", "mara", local_name="Mara")
    event = Event("campaign-a", "secret-1", "A door opened", Moment(0), "director")
    evidence = [_witnessed("campaign-a", "mara", "e1", "The door opened")]
    knowledge = Knowledge("campaign-a", events=[event], evidence=evidence)
    view = actor_view(actor, EMPTY, actor_knowledge(knowledge, actor.ref, as_of=1))
    evidence.append(_witnessed("campaign-a", "mara", "e2", "Someone entered"))
    assert len(view.knowledge.evidence) == 1
    grown = Knowledge("campaign-a", events=[event], evidence=evidence)
    assert len(actor_view(actor, EMPTY, actor_knowledge(grown, actor.ref, as_of=1)).knowledge.evidence) == 2


def test_actor_view_accepts_only_this_actors_projection():
    mara = CampaignActor("campaign-a", "mara", local_name="Mara")
    wren = CampaignActor("campaign-a", "wren", local_name="Wren")
    knowledge = Knowledge("campaign-a")
    for wrong, reason in (
        (director_knowledge(knowledge, as_of=0, requested_by=DIRECTOR), "not_actor_knowledge"),
        ({"evidence": ()}, "not_actor_knowledge"),
        (actor_knowledge(knowledge, wren.ref, as_of=0), "actor_mismatch"),
    ):
        with pytest.raises(KnowledgeError) as caught:
            actor_view(mara, EMPTY, wrong)
        assert caught.value.reason == reason
    assert actor_view(mara, EMPTY).knowledge is None  # none supplied, never "unfiltered"


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
    lambda: Belief("campaign-a", "b1", InstanceRef("campaign-a", "character", "vessa"), "p", "believed",
                   Moment(0), "director", ("e",), supersedes="Vessa"),
    lambda: Event("", "e1", "A door opened", Moment(0), "director"),
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


EMBER = "world:emberfall"
LANTERN = "catalog:lanternworks"


def vessa(source, revision, description):
    return CharacterDefinition(SourceRef(source, "character", "vessa", revision), "Vessa", description)


def test_vessa_namesakes_select_one_portrayal_without_replacing_other():
    ember, lantern = vessa(EMBER, "r1", "Emberfall's Vessa"), vessa(LANTERN, "v1", "Lanternworks' Vessa")
    index = index_of(ember, lantern)
    candidates = index.find_by_name("Vessa")
    assert candidates == (lantern.ref.unpinned(), ember.ref.unpinned())
    chosen = CampaignActor("campaign-a", "vessa", source=candidates[1])
    view = actor_view(chosen, index)
    assert (view.description, view.resolved_source) == ("Emberfall's Vessa", ember.ref)
    third = vessa("catalog:fans", "a", "A fan's Vessa")
    grown = index_of(ember, lantern, third)
    assert len(grown.find_by_name("Vessa")) == 3
    assert actor_view(chosen, grown) == view
    assert grown.resolve(lantern.ref.unpinned()) == lantern


def test_unpinned_actor_follows_head_and_pinned_actor_stays():
    r1, r2, r3 = (vessa(EMBER, rev, f"Vessa {rev}") for rev in ("r1", "r2", "r3"))
    following = CampaignActor("campaign-a", "vessa", source=r1.ref.unpinned())
    pinned = CampaignActor("campaign-b", "vessa", source=r1.ref)
    before = index_of(r1, r2, heads={r1.ref.unpinned(): "r2"})
    after = index_of(r1, r2, r3, heads={r1.ref.unpinned(): "r3"})
    assert actor_view(following, before).resolved_source == r2.ref
    assert actor_view(following, after).resolved_source == r3.ref
    assert actor_view(pinned, before).resolved_source == actor_view(pinned, after).resolved_source == r1.ref
    assert actor_view(pinned, after).description == "Vessa r1"


@pytest.mark.parametrize("source, reason", [
    (SourceRef("catalog:ghost", "character", "vessa"), "missing_source"),
    (SourceRef(EMBER, "character", "nobody"), "missing_entity"),
    (SourceRef(EMBER, "character", "vessa", "r9"), "missing_revision"),
])
def test_actor_view_missing_reasons(source, reason):
    actor = CampaignActor("campaign-a", "vessa", source=source, local_name="Vessa")
    with pytest.raises(MissingDefinition) as caught:
        actor_view(actor, index_of(vessa(EMBER, "r1", "")))
    assert (caught.value.ref, caught.value.reason) == (source, reason)


def test_campaign_original_actor_has_no_resolved_source():
    assert actor_view(CampaignActor("campaign-a", "wren", local_name="Wren"), EMPTY).resolved_source is None

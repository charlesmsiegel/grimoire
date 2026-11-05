"""The 009 index path and derived portrayals (022): a delta is never a complete card."""

import pytest
from entity_fixtures import EMBER, index_of, mara, vessa

from grimoire.core import (
    CharacterDefinition, ControlAssignment, InstanceRef, SourceRef, actor_view, compose_instance, effective_entity,
)
from grimoire.core.entity import EntityDefinition, EntityError, EntityInstance, Integrity
from grimoire.core.facets import Agent, Communicative, FacetBundle, FacetSet, Portable
from grimoire.core.roster import CampaignRoster

BASE = "catalog:elderfen-cast/character/mara@r1"
DERIVED = EntityDefinition(SourceRef(EMBER, "character", "mara-vampire", "r1"), "Mara", "individual",
                           FacetSet([Agent()]), extras={"derivation": {"base": BASE}})
ACTOR = EntityInstance(InstanceRef("c1", "character", "mara-vampire-1"), DERIVED.ref)


def reason(build):
    with pytest.raises(EntityError) as caught:
        build()
    return caught.value.reason


def test_effective_entity_refuses_a_derived_definition():
    assert reason(lambda: effective_entity(ACTOR, index_of(DERIVED))) == "derived_definition"


def test_compose_instance_refuses_a_raw_derived_definition():
    assert reason(lambda: compose_instance(ACTOR, DERIVED)) == "derived_definition"


def test_roster_control_of_a_derived_actor_is_a_named_limitation():
    """Owned by 035: control of derived-portrayal actors needs a snapshot path; the index-only actor_view
    keeps refusing (``snapshot_actor_view`` is the perspective path)."""
    index = index_of(DERIVED)
    roster = CampaignRoster("c1", [ACTOR])
    assert reason(lambda: roster.assign_control(ACTOR.ref, "human:p", index)) == "derived_definition"
    problems = roster.check_controls([ControlAssignment("c1", ACTOR.ref.instance_id, "human:p")], index)
    assert [(p.reason, p.detail) for p in problems] == [("unresolved", "derived_definition")]
    assert reason(lambda: actor_view(ACTOR, index)) == "derived_definition"


@pytest.mark.parametrize("build", [
    lambda: EntityInstance(InstanceRef("c1", "character", "m"), mara().ref),
    lambda: EntityInstance(InstanceRef("c1", "item", "v"), vessa().ref, replaced=FacetBundle([Portable("heavy")]),
                           state=(Integrity(4),)),
    lambda: EntityInstance(InstanceRef("c1", "item", "v"), vessa(agent=False).ref,
                           added=FacetBundle([Agent(), Communicative(speaks={"sign"})])),
    lambda: EntityInstance(InstanceRef("c1", "item", "stone"), None, local_name="Stone"),
])
def test_compose_instance_matches_effective_entity(build):
    instance = build()
    index = index_of(mara(), vessa()) if instance.source != vessa(agent=False).ref else index_of(vessa(agent=False))
    base = index.resolve(instance.source) if instance.source is not None else None
    assert compose_instance(instance, base) == effective_entity(instance, index)


def test_compose_instance_keeps_the_kind_check():
    wrong = EntityInstance(InstanceRef("c1", "item", "m"), mara().ref)
    assert reason(lambda: compose_instance(wrong, mara())) == "kind_mismatch"


@pytest.mark.parametrize("base", [CharacterDefinition(SourceRef(EMBER, "character", "mara", "r1"), "Mara"),
                                  "catalog:x/character/y@r1"])
def test_compose_instance_refuses_a_value_that_is_not_an_authored_base(base):
    assert reason(lambda: compose_instance(ACTOR, base)) == "not_entity_definition"


def test_damage_proposals_for_a_derived_actor_are_the_same_named_limitation():
    from grimoire.core.entity import propose_damage
    assert reason(lambda: propose_damage(ACTOR, 1, index_of(DERIVED))) == "derived_definition"


def test_compose_instance_refuses_a_base_that_is_not_the_instances_source():
    """Names alone confer no identity: the public composition step keeps 009's correspondence."""
    from grimoire.core.facets import Physical
    namesake = EntityDefinition(SourceRef("world:elsewhere", "character", "mara", "r1"), "Mara", "individual",
                                FacetSet([Agent()]))
    sourced = EntityInstance(InstanceRef("c1", "character", "m"), mara().ref)
    original = EntityInstance(InstanceRef("c1", "item", "stone"), None, "Stone", FacetBundle([Physical()]))
    assert reason(lambda: compose_instance(sourced, None)) == "source_mismatch"
    assert reason(lambda: compose_instance(sourced, namesake)) == "source_mismatch"
    assert reason(lambda: compose_instance(original, mara())) == "source_mismatch"
    newer = EntityDefinition(mara().ref.at("r2"), "Mara", "individual", FacetSet([Agent()]))
    assert compose_instance(sourced, newer).resolved_source == newer.ref  # another revision is fine


def test_the_roster_path_and_a_snapshot_may_disagree_on_a_rebound_actors_revision():
    """Named 035 limitation: the index path follows the instance's source; a snapshot follows its binding."""
    from grimoire.core.snapshot import EffectiveSnapshot, SelectedSource, SourceSelection
    newer = EntityDefinition(mara().ref.at("r2"), "Mara", "individual", FacetSet([Agent()]), "Older now.")
    index = index_of(mara(), newer, heads={mara().ref.unpinned(): "r1"})
    actor = EntityInstance(InstanceRef("c1", "character", "m"), mara().ref.unpinned())
    snap = EffectiveSnapshot(index, SourceSelection(None, (SelectedSource(newer.ref, "character", actor=actor.ref),)),
                             instances=[(actor, None)])
    assert snap.entity(actor.ref).resolved_source == newer.ref
    assert effective_entity(actor, index).resolved_source == mara().ref

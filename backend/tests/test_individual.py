"""Creature individualization (sprint 024): one goblin of three becomes "Snik".

Synthetic fixtures only. Individualization promotes a generic template
instance in place: the ``InstanceRef`` (and so everything keyed by it, such as
the evidence it holds) is unchanged, the template keeps supplying inherited
traits, and nothing personal is shared with the species or its other instances.
"""

from dataclasses import replace

import pytest
from entity_fixtures import EMBER, goblin, goblin_r2, grakka, index_of, plain_sword, ref

from grimoire.core import (
    DIRECTOR, AuthorityError, CampaignRoster, ControlAssignment, Event, InstanceRef, Knowledge,
    MissingDefinition, Moment, Perception, SourceRef, UnknownInstance,
)
from grimoire.core.entity import (
    EntityError, EntityInstance, Individuality, effective_entity, integrity, propose_damage,
)
from grimoire.core.facets import Communicative
from grimoire.core.individual import (
    IndividualizationDecision, Individualization, individualize, is_generic,
)

C = "c1"
GOBLIN = ref("species", "goblin").unpinned()
G1, G2, G3 = (InstanceRef(C, "creature", f"goblin-{n}") for n in (1, 2, 3))


def reason(build, error=EntityError):
    with pytest.raises(error) as caught:
        build()
    return caught.value.reason


def generic(ref_, source=GOBLIN, **fields):
    return EntityInstance(ref_, source, **fields)


def three_goblins():
    return CampaignRoster(C, (generic(G1), generic(G2), generic(G3)))


def snik_individuality(**fields):
    values = dict(decision_id="d-snik", given_name="Snik", decided_by=DIRECTOR, scene_id="s3",
                  template=ref("species", "goblin"))
    return Individuality(**{**values, **fields})


def decide(instance=G1, name="Snik", decision_id="d-snik", decided_by=DIRECTOR, scene_id="s3",
           event_ids=(), campaign_id=C):
    return IndividualizationDecision(campaign_id, decision_id, instance, name, decided_by, scene_id,
                                     event_ids)


INDEX = index_of(goblin())


# --- Individuality on an instance -------------------------------------------------

def test_individuality_invariants():
    snik = snik_individuality(event_ids=("evt-b", "evt-a"))
    assert snik.event_ids == ("evt-a", "evt-b")
    assert EntityInstance(G1, GOBLIN, "Snik", individuality=snik).individuality is snik
    assert reason(lambda: EntityInstance(G1, None, "Snik", individuality=snik)) \
        == "individuality_without_template"
    assert reason(lambda: EntityInstance(G1, GOBLIN, None, individuality=snik)) == "individual_needs_name"
    assert reason(lambda: EntityInstance(G1, ref("species", "orc"), "Snik", individuality=snik)) \
        == "template_mismatch"
    assert reason(lambda: EntityInstance(G1, GOBLIN, "Snik", individuality="Snik")) == "invalid_field"
    assert reason(lambda: EntityInstance(G1, GOBLIN, "Snik", extras={"individuality": {}})) == "invalid_field"


@pytest.mark.parametrize("fields", [
    {"template": GOBLIN},                      # unpinned
    {"template": "world:emberfall/species/goblin@r1"},
    {"decision_id": "Not A Segment"},
    {"given_name": " "},
    {"decided_by": ""},
    {"scene_id": ""},
    {"event_ids": ("evt-a", "evt-a")},
    {"event_ids": ("Bad",)},
    {"event_ids": "evt-a"},
])
def test_individuality_field_errors_carry_a_reason(fields):
    assert reason(lambda: snik_individuality(**fields)) == "invalid_field"


def test_an_individual_resolves_with_its_own_name_and_inherited_traits():
    snik = EntityInstance(G1, GOBLIN, "Snik", individuality=snik_individuality())
    view = effective_entity(snik, INDEX)
    assert view.name == "Snik" and view.ref == G1
    assert view.facets == goblin().traits and view.description == goblin().description
    assert view.resolved_source == ref("species", "goblin")


def test_a_template_turning_individual_is_named_drift_for_the_individual_only():
    # Instances of the template's own kind pass kind_mismatch and reach the new check.
    s1, s2 = InstanceRef(C, "species", "goblin-a"), InstanceRef(C, "species", "goblin-b")
    snik = EntityInstance(s1, GOBLIN, "Snik", individuality=snik_individuality())
    sibling = generic(s2)
    drifted = index_of(goblin(), goblin("r2", nature="individual"),
                       heads={GOBLIN: "r2"})
    assert reason(lambda: effective_entity(snik, drifted)) == "not_template"
    assert effective_entity(sibling, drifted).name == "Goblin"
    assert not is_generic(sibling, drifted)  # its source is now an individual
    pinned = replace(snik, source=ref("species", "goblin"))
    assert effective_entity(pinned, drifted).name == "Snik"  # pinning is the remedy
    # kind_mismatch keeps precedence for instances of another kind.
    creature = EntityInstance(G1, GOBLIN, "Snik", individuality=snik_individuality())
    assert reason(lambda: effective_entity(creature, drifted)) == "kind_mismatch"


# --- the proving scenario ------------------------------------------------------------

def conversation_knowledge():
    """Snik-to-be (goblin-1) witnessed a conversation; nobody else holds anything."""
    talk = Event(C, "evt-talk", "the party bargained with a goblin for safe passage", Moment(1, "s2"),
                 "director", participants=(G1,))
    heard = Perception(C, "ev-g1-talk", G1, "evt-talk", "witnessed", "the humans offered bread",
                       Moment(1, "s2"), "director", confirmed=True)
    return Knowledge(C, events=(talk,), evidence=(heard,))


def test_proving_scenario_one_goblin_named_and_remembering_others_generic():
    roster, knowledge = three_goblins(), conversation_knowledge()
    species_before = goblin()
    result = individualize(roster, decide(event_ids=("evt-talk",)), INDEX)
    assert isinstance(result, Individualization) and result.applied
    snik = result.roster.instance(G1)
    assert snik is result.instance and snik.ref == G1
    assert snik.local_name == "Snik"
    assert snik.individuality == snik_individuality(event_ids=("evt-talk",))
    assert effective_entity(snik, INDEX).name == "Snik"
    assert not is_generic(snik, INDEX)
    # The memory stays with the unchanged ref; individualize never saw Knowledge.
    assert [item.evidence_id for item in knowledge.evidence_held_by(G1)] == ["ev-g1-talk"]
    for other in (G2, G3):
        assert result.roster.instance(other) == roster.instance(other) == generic(other)
        assert is_generic(result.roster.instance(other), INDEX)
        assert effective_entity(result.roster.instance(other), INDEX).name == "Goblin"
        assert knowledge.evidence_held_by(other) == () and knowledge.beliefs_held_by(other) == ()
    assert INDEX.resolve(GOBLIN) == species_before
    assert roster.instance(G1) == generic(G1)  # the input roster is a value, untouched


def test_two_individuals_keep_separate_names_and_wounds():
    roster = individualize(three_goblins(), decide(), INDEX).roster
    roster = individualize(roster, decide(G2, "Mog", "d-mog", scene_id="s4"), INDEX).roster
    snik, mog = roster.instance(G1), roster.instance(G2)
    assert (snik.local_name, mog.local_name) == ("Snik", "Mog")
    assert snik.individuality.decision_id != mog.individuality.decision_id
    wounded = propose_damage(snik, 5, INDEX)
    assert integrity(effective_entity(wounded, INDEX)) == 2
    assert integrity(effective_entity(mog, INDEX)) == 7
    assert integrity(effective_entity(roster.instance(G3), INDEX)) == 7
    assert INDEX.resolve(GOBLIN) == goblin()
    assert is_generic(roster.instance(G3), INDEX)


def test_a_head_update_flows_into_an_unpinned_individual_without_losing_identity():
    pinned_ref = InstanceRef(C, "creature", "goblin-pinned")
    roster = CampaignRoster(C, (generic(G1), generic(pinned_ref, ref("species", "goblin"))))
    roster = individualize(roster, decide(), INDEX).roster
    roster = individualize(roster, decide(pinned_ref, "Grub", "d-grub"), INDEX).roster
    roster = replace(roster)  # values only
    moved = index_of(goblin(), goblin_r2(), heads={GOBLIN: "r2"})
    snik = propose_damage(roster.instance(G1), 1, INDEX)
    view = effective_entity(snik, moved)
    assert view.ref == G1 and view.name == "Snik"
    assert view.facets.has(Communicative) and view.description == goblin_r2().description
    assert view.resolved_source == ref("species", "goblin", "r2")
    assert snik.individuality.template == ref("species", "goblin")  # provenance stays r1
    assert integrity(view) == 6
    grub = effective_entity(roster.instance(pinned_ref), moved)
    assert grub.resolved_source == ref("species", "goblin") and not grub.facets.has(Communicative)


# --- idempotency and conflicts -----------------------------------------------------------

def test_replaying_a_decision_is_a_no_op_even_reordered_or_after_a_rename():
    first = individualize(three_goblins(), decide(event_ids=("evt-b", "evt-a")), INDEX)
    again = individualize(first.roster, decide(event_ids=("evt-a", "evt-b")), INDEX)
    assert not again.applied and again.roster == first.roster and again.instance == first.instance
    renamed = replace(first.instance, local_name="Snik the Bold")
    roster = CampaignRoster(C, (renamed, generic(G2), generic(G3)))
    replay = individualize(roster, decide(event_ids=("evt-a", "evt-b")), INDEX)
    assert not replay.applied and replay.instance.local_name == "Snik the Bold"
    assert replay.instance.individuality.given_name == "Snik"


def test_replay_needs_no_authority_but_a_new_decision_does():
    roster = individualize(three_goblins(), decide(), INDEX).roster
    p1 = (ControlAssignment(C, "goblin-1", "p1"),)  # control changed after promotion
    assert not individualize(roster, decide(), INDEX, p1).applied


def test_conflicting_decisions_are_refused():
    roster = individualize(three_goblins(), decide(), INDEX).roster
    assert reason(lambda: individualize(roster, decide(name="Snak"), INDEX)) == "decision_conflict"
    assert reason(lambda: individualize(roster, decide(scene_id="s9"), INDEX)) == "decision_conflict"
    assert reason(lambda: individualize(roster, decide(name="Snak", decision_id="d-2"), INDEX)) \
        == "already_individualized"
    assert reason(lambda: individualize(roster, decide(G2, name="Snik"), INDEX)) == "decision_conflict"


def test_a_duplicated_decision_id_fails_closed_even_for_an_exact_replay():
    snik = EntityInstance(G1, GOBLIN, "Snik", individuality=snik_individuality())
    twin = EntityInstance(G2, GOBLIN, "Snik", individuality=snik_individuality())
    merged = CampaignRoster(C, (snik, twin, generic(G3)))  # e.g. after a sync merge: still loads
    assert reason(lambda: individualize(merged, decide(), INDEX)) == "decision_conflict"


# --- authority ----------------------------------------------------------------------------

def test_only_the_director_individualizes_an_uncontrolled_creature():
    with pytest.raises(AuthorityError):
        individualize(three_goblins(), decide(decided_by="p1"), INDEX)
    other = (ControlAssignment(C, "goblin-2", "p1"),)  # control of another actor is irrelevant
    assert individualize(three_goblins(), decide(), INDEX, other).applied


def test_only_the_controlling_player_individualizes_their_actor():
    controls = [ControlAssignment(C, "goblin-1", "p1")]
    with pytest.raises(AuthorityError):
        individualize(three_goblins(), decide(), INDEX, controls)
    with pytest.raises(AuthorityError):
        individualize(three_goblins(), decide(decided_by="p2"), INDEX, controls)
    result = individualize(three_goblins(), decide(decided_by="p1"), INDEX, iter(controls))
    assert result.instance.individuality.decided_by == "p1"
    assert result.roster.check_controls(controls, INDEX) == ()
    assert result.roster.assign_control(G1, "p1", INDEX) == controls[0]


def test_ambiguous_control_fails_closed():
    controls = (ControlAssignment(C, "goblin-1", "p1"), ControlAssignment(C, "goblin-1", "p2"))
    with pytest.raises(AuthorityError):
        individualize(three_goblins(), decide(decided_by="p1"), INDEX, controls)


def test_a_non_agent_template_instance_is_director_only():
    from grimoire.core.entity import EntityDefinition
    blade = EntityDefinition(ref("item", "goblin-blade"), "Goblin blade", "template", plain_sword().traits)
    knife = InstanceRef(C, "item", "knife-1")
    roster = CampaignRoster(C, (EntityInstance(knife, blade.ref.unpinned()),))
    index = index_of(blade)
    with pytest.raises(AuthorityError):
        individualize(roster, decide(knife, "Biter", decided_by="p1"), index)
    assert individualize(roster, decide(knife, "Biter"), index).instance.local_name == "Biter"


# --- refusals --------------------------------------------------------------------------------

def test_individuals_and_originals_are_not_templates():
    chief = InstanceRef(C, "creature", "chief")
    hero = InstanceRef(C, "character", "hero")
    odd = InstanceRef(C, "species", "odd")  # an individual source of another kind
    roster = CampaignRoster(C, (EntityInstance(chief, grakka().ref), EntityInstance(hero, None, "Hero"),
                                EntityInstance(odd, grakka().ref)))
    index = index_of(goblin(), grakka())
    for target in (chief, hero, odd):
        assert reason(lambda: individualize(roster, decide(target), index)) == "not_template"


def test_unknown_targets_campaigns_and_sources():
    roster = three_goblins()
    with pytest.raises(UnknownInstance):
        individualize(roster, decide(InstanceRef(C, "creature", "goblin-9")), INDEX)
    with pytest.raises(UnknownInstance):
        individualize(roster, decide(InstanceRef(C, "species", "goblin-1")), INDEX)
    with pytest.raises(ValueError) as caught:
        individualize(roster, decide(InstanceRef("c2", "creature", "goblin-1"), campaign_id="c2"), INDEX)
    assert not isinstance(caught.value, EntityError)
    gone = CampaignRoster(C, (generic(G1, ref("species", "goblin", "r9")),))
    with pytest.raises(MissingDefinition):
        individualize(gone, decide(), INDEX)


def test_argument_types_are_checked():
    roster = three_goblins()
    with pytest.raises(TypeError):
        individualize(list(roster.instances), decide(), INDEX)
    with pytest.raises(TypeError):
        individualize(roster, "d-snik", INDEX)
    with pytest.raises(TypeError):
        individualize(roster, decide(), {})
    with pytest.raises(TypeError):
        individualize(roster, decide(), INDEX, [("c1", "goblin-1", "p1")])
    with pytest.raises(TypeError):
        is_generic(G1, INDEX)


@pytest.mark.parametrize("fields", [
    {"instance": InstanceRef("c2", "creature", "goblin-1")},
    {"instance": "campaign:c1/creature/goblin-1"},
    {"campaign_id": "C1"},
    {"decision_id": ""},
    {"name": "  "},
    {"decided_by": ""},
    {"scene_id": ""},
    {"event_ids": ("evt-a", "evt-a")},
    {"event_ids": ("Evt",)},
    {"event_ids": "evt-a"},
])
def test_decision_field_errors_carry_a_reason(fields):
    assert reason(lambda: decide(**fields)) == "invalid_field"


def test_is_generic_cases():
    labelled = generic(G1, local_name="the tall goblin")  # a label is not individuality
    assert is_generic(labelled, INDEX)
    index = index_of(goblin(), grakka())
    assert not is_generic(EntityInstance(G2, grakka().ref), index)
    assert not is_generic(EntityInstance(G3, None, "Original"), index)
    labelled_snik = individualize(CampaignRoster(C, (labelled,)), decide(), INDEX).instance
    assert labelled_snik.local_name == "Snik" and not is_generic(labelled_snik, INDEX)


def test_promotion_preserves_pin_facets_state_and_extras():
    from grimoire.core.facets import FacetBundle, UnknownFacet
    rich = EntityInstance(G1, ref("species", "goblin"), added=FacetBundle([UnknownFacet("x-mark")]),
                          extras={"x-note": "has a limp"})
    rich = propose_damage(rich, 2, INDEX)
    snik = individualize(CampaignRoster(C, (rich,)), decide(), INDEX).instance
    assert replace(snik, local_name=None, individuality=None) == rich


def test_existing_drift_still_blocks_promotion():
    from grimoire.core.facets import FacetBundle
    voiced = EntityInstance(G1, GOBLIN, added=FacetBundle([Communicative(speaks={"speech"})]))
    moved = index_of(goblin(), goblin_r2(), heads={GOBLIN: "r2"})
    assert reason(lambda: individualize(CampaignRoster(C, (voiced,)), decide(), moved)) \
        == "added_shadows_trait"

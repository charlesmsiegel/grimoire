"""A perspective view of a derived-portrayal actor, composed through the effective snapshot (022).

``actor_view(instance, index)`` refuses a derived portrayal (``derived_definition``): the index path
would read the delta as a complete card. ``snapshot_actor_view(instance, snapshot)`` composes the
actor from the snapshot instead. The perspective rules that hold for a base actor hold for it, and
it exposes exactly what the index path exposes for a standalone actor with the same composed
content: no sections, no ancestry, no director truth, no other actor's knowledge. Synthetic only.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from portrayal_fixtures import (
    BASE, BASE_SECTIONS, VAMPIRE_MARA, base, derived, elderfen_index, modern, namesake, vampire,
)

from grimoire.core import (
    DIRECTOR, CampaignActor, Event, InstanceRef, Knowledge, KnowledgeError, Moment, Perception, actor_knowledge,
    actor_view, director_knowledge, snapshot_actor_view,
)
from grimoire.core.entity import EntityDefinition, EntityError, EntityInstance
from grimoire.core.facets import Agent, FacetBundle
from grimoire.core.portrayal import PortrayalError
from grimoire.core.snapshot import EffectiveSnapshot, SelectedSource, SourceSelection

C = "c1"
MARA = InstanceRef(C, "character", "mara-1")
WREN = InstanceRef(C, "character", "wren-1")
SECRET = "The duke secretly betrayed the guild"


def seen(evidence_id, holder, percept):
    return Perception(C, evidence_id, holder, "e1", "sight", percept, Moment(1), "director", confirmed=True)


LEDGER = Knowledge(C, events=[Event(C, "e1", SECRET, Moment(1), "director")],
                   evidence=[seen("seen-1", MARA, "A sealed letter changed hands"),
                             seen("seen-2", WREN, "Someone slipped out the back")])


def vampire_npc(**fields):
    return EntityInstance(MARA, VAMPIRE_MARA, **fields)


def snap(*instances, source=VAMPIRE_MARA, actor=MARA, index=None):
    instances = instances or (vampire_npc(),)
    selection = SourceSelection(None, (SelectedSource(source, "character", actor=actor),))
    return EffectiveSnapshot(index or elderfen_index(), selection, instances=[(i, None) for i in instances])


def standalone_index(snapshot, ref):
    """The composed portrayal flattened into a complete card at the same ref: what a non-derived actor is."""
    effective = snapshot.effective(ref)
    flat = EntityDefinition(effective.ref, effective.name, effective.nature, effective.traits, effective.description)
    return elderfen_index(flat, links=[])


# -- the audit repro (w3c_027), inverted ------------------------------------------------------------

def test_a_derived_portrayal_npc_has_an_actor_view_through_the_snapshot():
    npc = vampire_npc()
    view = snapshot_actor_view(npc, snap(npc))
    assert (view.campaign_id, view.actor_id, view.name) == (C, "mara-1", "Mara")
    assert view.description == base().description  # inherited through the chain
    assert view.resolved_source == VAMPIRE_MARA  # the leaf pinned revision, never the base
    with pytest.raises(EntityError) as caught:  # the index-only call keeps its contract
        actor_view(npc, elderfen_index())
    assert caught.value.reason == "derived_definition"


@pytest.mark.parametrize("portrayal, description", [
    (derived(VAMPIRE_MARA, description="A pale archivist who keeps night hours.", clear_facets=["biological"]),
     "A pale archivist who keeps night hours."),
    (derived(VAMPIRE_MARA, clear_description=True, clear_facets=["biological"]), ""),
    (vampire(), base().description),
])
def test_the_view_shows_the_composed_fields(portrayal, description):
    index = elderfen_index(base(), portrayal, namesake())
    view = snapshot_actor_view(vampire_npc(), snap(index=index))
    assert view.description == description
    if description == "":
        assert base().description not in repr(view)  # a cleared value never leaks back
    renamed = vampire_npc(local_name="Countess Mara")
    assert snapshot_actor_view(renamed, snap(renamed, index=index)).name == "Countess Mara"


# -- perspective safety: nothing beyond what a base actor's view exposes ------------------------------

@pytest.mark.parametrize("index", [
    elderfen_index(),
    elderfen_index(base(), derived(VAMPIRE_MARA, description="Night archivist.", clear_description=False,
                                   clear_facets=["biological"]), namesake()),
    elderfen_index(base(), derived(VAMPIRE_MARA, clear_description=True), namesake()),
])
def test_the_derived_view_equals_the_index_view_of_the_same_standalone_actor(index):
    npc = vampire_npc()
    snapshot = snap(npc, index=index)
    knows = actor_knowledge(LEDGER, MARA, as_of=1)
    flat = standalone_index(snapshot, VAMPIRE_MARA)
    assert snapshot_actor_view(npc, snapshot, knows) == actor_view(npc, flat, knows)
    assert snapshot_actor_view(npc, snapshot) == actor_view(npc, flat)


def test_no_section_ancestry_or_director_truth_reaches_the_view():
    npc = vampire_npc()
    view = snapshot_actor_view(npc, snap(npc), actor_knowledge(LEDGER, MARA, as_of=1))
    shown = repr(view)
    for text in (*BASE_SECTIONS.values(), *vampire().extras["sections"].values()):
        assert text not in shown  # sections are prompt assembly's (027), not the perspective's
    assert BASE.source not in shown  # ancestry stays in the snapshot
    assert SECRET not in shown  # subjective evidence, not director truth
    assert [e.evidence_id for e in view.knowledge.evidence] == ["seen-1"]  # never Wren's percept


def test_the_derived_view_accepts_only_this_actors_projection():
    npc, wren = vampire_npc(), EntityInstance(WREN, None, "Wren", FacetBundle([Agent()]))
    snapshot = snap(npc, wren)
    for wrong, reason in (
        (director_knowledge(LEDGER, as_of=1, requested_by=DIRECTOR), "not_actor_knowledge"),
        ({"evidence": ()}, "not_actor_knowledge"),
        (actor_knowledge(LEDGER, WREN, as_of=1), "actor_mismatch"),
    ):
        with pytest.raises(KnowledgeError) as caught:
            snapshot_actor_view(npc, snapshot, wrong)
        assert caught.value.reason == reason
    assert snapshot_actor_view(npc, snapshot).knowledge is None  # none supplied, never "unfiltered"


def test_the_derived_view_is_an_immutable_snapshot():
    npc = vampire_npc()
    evidence = [seen("seen-1", MARA, "A sealed letter changed hands")]
    events = [Event(C, "e1", SECRET, Moment(1), "director")]
    knows = actor_knowledge(Knowledge(C, events=events, evidence=evidence), MARA, as_of=1)
    view = snapshot_actor_view(npc, snap(npc), knows)
    evidence.append(seen("seen-3", MARA, "The letter burned"))
    assert len(view.knowledge.evidence) == 1
    grown = actor_knowledge(Knowledge(C, events=events, evidence=evidence), MARA, as_of=1)
    assert len(snapshot_actor_view(npc, snap(npc), grown).knowledge.evidence) == 2


def test_a_derived_portrayal_that_clears_agency_has_no_perspective():
    statue = derived(VAMPIRE_MARA, clear_facets=["agent", "biological", "communicative"])
    npc = vampire_npc()
    with pytest.raises(EntityError) as caught:
        snapshot_actor_view(npc, snap(npc, index=elderfen_index(base(), statue, namesake())))
    assert caught.value.reason == "not_agent"


# -- the snapshot path for a base actor matches the index path ---------------------------------------

@pytest.mark.parametrize("instance", [
    EntityInstance(MARA, BASE),
    EntityInstance(MARA, BASE.unpinned()),
    EntityInstance(MARA, BASE, local_name="Archivist Mara"),
])
def test_a_base_actor_has_the_same_view_on_both_paths(instance):
    index = elderfen_index()
    knows = actor_knowledge(LEDGER, MARA, as_of=1)
    snapshot = snap(instance, source=BASE, index=index)
    assert snapshot_actor_view(instance, snapshot, knows) == actor_view(instance, index, knows)


def test_a_campaign_original_has_the_same_view_on_both_paths():
    wren = EntityInstance(WREN, None, "Wren", FacetBundle([Agent()]))
    snapshot = EffectiveSnapshot(elderfen_index(), SourceSelection(), instances=[(wren, None)])
    knows = actor_knowledge(LEDGER, WREN, as_of=1)
    assert snapshot_actor_view(wren, snapshot, knows) == actor_view(wren, elderfen_index(), knows)


# -- the captured instance is the instance -----------------------------------------------------------

def portrayal_reason(call):
    with pytest.raises(PortrayalError) as caught:
        call()
    return caught.value.reason


def test_the_passed_instance_must_be_the_captured_value():
    captured = vampire_npc(local_name="Mara of the crypt")
    snapshot = snap(captured)
    assert portrayal_reason(lambda: snapshot_actor_view(vampire_npc(), snapshot)) == "instance_mismatch"
    assert portrayal_reason(lambda: snapshot_actor_view(replace(captured, local_name="Mara"), snapshot)) \
        == "instance_mismatch"
    assert snapshot_actor_view(captured, snapshot).name == "Mara of the crypt"


def test_an_uncaptured_or_detached_actor_has_no_view():
    other = InstanceRef(C, "character", "mara-2")
    assert portrayal_reason(lambda: snapshot_actor_view(vampire_npc(), snap(EntityInstance(other, VAMPIRE_MARA),
                                                                            actor=other))) == "unknown_instance"
    detached = snap(vampire_npc(), EntityInstance(other, VAMPIRE_MARA), actor=other)
    assert portrayal_reason(lambda: snapshot_actor_view(vampire_npc(), detached)) == "not_selected"


def test_a_modern_portrayal_selected_for_a_vampire_actor_is_a_mismatch():
    npc = EntityInstance(MARA, modern().ref)
    assert portrayal_reason(lambda: snapshot_actor_view(npc, snap(npc))) == "actor_source_mismatch"


def test_argument_types_are_checked():
    npc = vampire_npc()
    with pytest.raises(TypeError):
        snapshot_actor_view(CampaignActor(C, "mara-1", source=VAMPIRE_MARA), snap(npc))
    with pytest.raises(TypeError):
        snapshot_actor_view(npc, elderfen_index())

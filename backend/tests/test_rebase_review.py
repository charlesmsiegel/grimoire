"""Sprint 042: the pure three-way field review and the decided rebase of one campaign instance.

Synthetic Wren of a synthetic harbor world; no user content.
"""

from __future__ import annotations

import pickle

import pytest

from grimoire.core import DefinitionIndex, SourceDescriptor
from grimoire.core.entity import (
    EntityDefinition, EntityError, EntityInstance, Individuality, Integrity, compose_instance,
)
from grimoire.core.facets import Agent, Communicative, Durable, FacetBundle, FacetSet, Physical, Portable
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.core.portrayal import compose
from grimoire.rebase import RebaseError, canon_definition, rebase_instance, review_fields

WORLD = "world:harbor"
WREN = SourceRef(WORLD, "character", "wren")
R1, R2 = WREN.at("r1"), WREN.at("r2")
ACTOR = InstanceRef("c2", "character", "wren-b")
VOICE = Communicative(speaks={"speech"}, understands={"speech"})
SECTIONS = {"biography": "Born at sea.", "voice-evidence": "Counts tides aloud."}


def wren(ref=R1, *, name="Wren", nature="individual", facets=(Physical(), Agent(), VOICE),
         description="A harbor pilot.", sections=None) -> EntityDefinition:
    return EntityDefinition(ref, name, nature, FacetSet(list(facets)), description,
                            {"sections": dict(SECTIONS if sections is None else sections)})


def effective(*definitions):
    sources = sorted({definition.ref.source for definition in definitions})
    heads = {definition.ref.unpinned(): definition.ref.revision for definition in definitions}  # the last one
    index = DefinitionIndex([SourceDescriptor(source, owner="human:owner") for source in sources], definitions, heads)

    def read(ref):
        return index.resolve(ref), None
    return tuple(compose(definition.ref, index, read) for definition in definitions)


def instance(**fields) -> EntityInstance:
    return EntityInstance(ACTOR, fields.pop("source", R1), **fields)


def rows_of(old, new, inst):
    return {row.field: row for row in review_fields(old, new, inst)}


# -- rows ------------------------------------------------------------------------------------------

def test_a_corrected_section_is_one_upstream_row():
    old, new = effective(wren(), wren(R2, sections={**SECTIONS, "biography": "Born in the harbor town."}))
    rows = review_fields(old, new, instance())
    assert [(row.field, row.status, row.ancestor, row.upstream) for row in rows] == [
        ("section:biography", "upstream", "Born at sea.", "Born in the harbor town.")]
    rebased, entity = rebase_instance(instance(), rows, {"section:biography": "take"}, new)
    assert rebased.source == R2 and entity.name == "Wren"


def test_a_section_cannot_be_kept_by_the_campaign():
    old, new = effective(wren(), wren(R2, sections={**SECTIONS, "biography": "Born in the harbor town."}))
    rows = review_fields(old, new, instance())
    with pytest.raises(RebaseError) as caught:
        rebase_instance(instance(), rows, {"section:biography": "keep"}, new)
    assert caught.value.reason == "not_overridable" and caught.value.fields == ("section:biography",)


def test_a_renamed_actor_against_a_renamed_definition_is_a_conflict():
    old, new = effective(wren(), wren(R2, name="Wren Tidewater"))
    local = instance(local_name="Captain Wren")
    rows = rows_of(old, new, local)
    assert (rows["name"].status, rows["name"].local) == ("conflict", "Captain Wren")
    kept, entity = rebase_instance(local, tuple(rows.values()), {"name": "keep"}, new)
    assert kept.local_name == "Captain Wren" and entity.name == "Captain Wren"
    taken, entity = rebase_instance(local, tuple(rows.values()), {"name": "take"}, new)
    assert taken.local_name is None and entity.name == "Wren Tidewater"


def test_keeping_an_upstream_rename_keeps_the_old_name_locally():
    old, new = effective(wren(), wren(R2, name="Wren Tidewater"))
    rows = review_fields(old, new, instance())
    kept, entity = rebase_instance(instance(), rows, {"name": "keep"}, new)
    assert kept.local_name == "Wren" and entity.name == "Wren"


def test_the_same_rename_on_both_sides_converges():
    old, new = effective(wren(), wren(R2, name="Wren Tidewater"))
    local = instance(local_name="Wren Tidewater")
    [row] = review_fields(old, new, local)
    assert row.status == "converged" and not row.needs_decision
    rebased, _ = rebase_instance(local, (row,), {}, new)
    assert rebased.local_name == "Wren Tidewater"


def test_an_individual_keeps_its_given_name_when_the_template_is_renamed():
    template = SourceRef(WORLD, "character", "guard")
    old, new = effective(wren(template.at("r1"), name="Guard", nature="template"),
                         wren(template.at("r2"), name="Watchman", nature="template"))
    individual = EntityInstance(ACTOR, template.at("r1"), "Mara", individuality=Individuality(
        "d-1", "Mara", "human:owner", "s-1", template.at("r1")))
    [row] = review_fields(old, new, individual)
    assert (row.status, row.needs_decision) == ("individual", False)
    rebased, entity = rebase_instance(individual, (row,), {}, new)
    assert rebased.local_name == "Mara" and entity.name == "Mara" and rebased.individuality is not None


def test_a_facet_the_instance_replaced_conflicts_with_an_upstream_change():
    old, new = effective(wren(facets=(Physical(), Agent(), VOICE, Durable(5))),
                         wren(R2, facets=(Physical(), Agent(), VOICE, Durable(7))))
    local = instance(replaced=FacetBundle((Durable(9),)))
    [row] = review_fields(old, new, local)
    assert (row.field, row.status) == ("facet:durable", "conflict")
    kept, entity = rebase_instance(local, (row,), {"facet:durable": "keep"}, new)
    assert kept.replaced == FacetBundle((Durable(9),)) and entity.facets.by_name("durable") == Durable(9)
    taken, entity = rebase_instance(local, (row,), {"facet:durable": "take"}, new)
    assert taken.replaced == FacetBundle() and entity.facets.by_name("durable") == Durable(7)


def test_keeping_a_facet_upstream_removed_moves_it_to_added():
    old, new = effective(wren(facets=(Physical(), Agent(), VOICE, Portable())),
                         wren(R2, facets=(Physical(), Agent(), VOICE)))
    [row] = review_fields(old, new, instance())
    assert (row.status, row.upstream, row.keepable) == ("upstream", None, True)
    kept, entity = rebase_instance(instance(), (row,), {"facet:portable": "keep"}, new)
    assert kept.added == FacetBundle((Portable(),)) and "portable" in entity.facets


def test_a_trait_upstream_adds_cannot_be_kept_out():
    old, new = effective(wren(), wren(R2, facets=(Physical(), Agent(), VOICE, Portable())))
    [row] = review_fields(old, new, instance())
    assert (row.status, row.keepable) == ("upstream", False)
    with pytest.raises(RebaseError) as caught:
        rebase_instance(instance(), (row,), {"facet:portable": "keep"}, new)
    assert caught.value.reason == "not_overridable"


def test_an_added_facet_that_upstream_now_has_moves_to_replaced():
    """``added`` may not shadow a trait (009): a converged or kept added facet becomes ``replaced``."""
    old, new = effective(wren(), wren(R2, facets=(Physical(), Agent(), VOICE, Portable("heavy"))))
    for local_value, decisions in ((Portable("heavy"), {}), (Portable("light"), {"facet:portable": "keep"})):
        local = instance(added=FacetBundle((local_value,)))
        [row] = review_fields(old, new, local)
        rebased, entity = rebase_instance(local, (row,), decisions, new)
        assert rebased.added == FacetBundle() and rebased.replaced == FacetBundle((local_value,))
        assert entity.facets.by_name("portable") == local_value


def test_an_untouched_override_is_a_local_row_and_stays():
    old, new = effective(wren(), wren(R2, description="A harbor pilot and ferry captain."))
    local = instance(added=FacetBundle((Portable(),)))
    rows = rows_of(old, new, local)
    assert rows["facet:portable"].status == "local" and rows["description"].status == "upstream"
    rebased, _ = rebase_instance(local, tuple(rows.values()), {"description": "take"}, new)
    assert rebased.added == FacetBundle((Portable(),))


def test_taking_a_removal_that_leaves_state_behind_is_refused_by_composition():
    old, new = effective(wren(facets=(Physical(), Agent(), VOICE, Durable(5))),
                         wren(R2, facets=(Physical(), Agent(), VOICE)))
    local = instance(state=(Integrity(4),))
    [row] = review_fields(old, new, local)
    with pytest.raises(EntityError) as caught:
        rebase_instance(local, (row,), {"facet:durable": "take"}, new)
    assert caught.value.reason == "state_without_facet"
    kept, entity = rebase_instance(local, (row,), {"facet:durable": "keep"}, new)
    assert kept.added == FacetBundle((Durable(5),)) and entity.state == (Integrity(4),)


# -- refusals --------------------------------------------------------------------------------------

def refused(call):
    with pytest.raises(RebaseError) as caught:
        call()
    return caught.value.reason


def test_review_refusals():
    namesake = SourceRef("world:elsewhere", "character", "wren", "r1")
    old, new, other = effective(wren(), wren(R2, name="Wren Tidewater"), wren(namesake))
    assert refused(lambda: review_fields(old, other, instance())) == "other_entity"
    assert refused(lambda: review_fields(old, old, instance())) == "up_to_date"
    assert refused(lambda: review_fields(old, new, EntityInstance(ACTOR, None, "Wren"))) == "campaign_original"
    _, template = effective(wren(), wren(R2, nature="template"))
    assert refused(lambda: review_fields(old, template, instance())) == "nature_changed"


def test_decision_refusals():
    old, new = effective(wren(), wren(R2, name="Wren Tidewater", description="A ferry captain."))
    rows = review_fields(old, new, instance())
    assert refused(lambda: rebase_instance(instance(), rows, {"name": "take"}, new)) == "undecided"
    assert refused(lambda: rebase_instance(instance(), rows, {"name": "take", "description": "maybe"},
                                           new)) == "invalid_decision"
    assert refused(lambda: rebase_instance(instance(), rows, {"name": "take", "description": "take",
                                                              "facet:agent": "take"}, new)) == "unknown_decision"
    error = RebaseError("x", "undecided", ("name",))
    assert pickle.loads(pickle.dumps(error)).fields == ("name",)


# -- canon -----------------------------------------------------------------------------------------

def test_a_canon_portrayal_restates_only_what_the_campaign_overrode():
    [base] = effective(wren())
    local = instance(local_name="Captain Wren", added=FacetBundle((Durable(6),)))
    entity = compose_instance(local, base)
    new = SourceRef("world:saltmarch", "character", "wren", "r1")
    definition = canon_definition(new, local, entity, R1, "individual", {"chat": "c", "actor": "a"})
    assert definition.name == "Captain Wren" and definition.description == ""
    assert [facet.name for facet in definition.traits] == ["durable", "physical"]  # durable needs physical
    assert definition.extras["derivation"] == {"base": str(R1)}
    assert definition.extras["promoted_from"] == {"chat": "c", "actor": "a"}


def test_a_campaign_original_becomes_a_standalone_individual():
    local = EntityInstance(ACTOR, None, "Gull", added=FacetBundle((Physical(), Agent())))
    entity = compose_instance(local, None)
    new = SourceRef("world:saltmarch", "character", "gull", "r1")
    definition = canon_definition(new, local, entity, None, "individual", {"chat": "c", "actor": "a"})
    assert (definition.name, definition.nature) == ("Gull", "individual")
    assert {facet.name for facet in definition.traits} == {"physical", "agent"}
    assert "derivation" not in definition.extras

"""Frozen world-optional snapshots of selected sources and captured instances (Sprint 022)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from portrayal_fixtures import (
    BASE, BASE_SECTIONS, CAST, MODERN_MARA, NAMESAKE, VAMPIRE, VAMPIRE_MARA, base, elderfen_index, modern,
    namesake, vampire,
)

from grimoire.core import InstanceRef, MissingDefinition, SourceRef
from grimoire.core.entity import EntityDefinition, EntityError, EntityInstance
from grimoire.core.facets import Agent, Biological, FacetBundle, FacetSet, Physical
from grimoire.core.portrayal import PortrayalError
from grimoire.core.snapshot import EffectiveSnapshot, SelectedSource, SourceSelection


@dataclass(frozen=True)
class Lore:
    """A non-entity source (a lorebook): resolvable raw, never composed."""

    ref: SourceRef
    name: str


NOTES = SourceRef(CAST, "lorebook", "notes", "r1")
DOCKS = SourceRef(VAMPIRE, "location", "crypt", "r1")
ACTOR = InstanceRef("quick-1", "character", "mara-1")


def library(*extra, heads=None):
    return elderfen_index(base(), modern(), vampire(), namesake(), Lore(NOTES, "Notes"),
                          EntityDefinition(DOCKS, "Crypt", "individual", FacetSet([])), *extra, heads=heads)


def snapshot(*sources, world=None, index=None, instances=()):
    return EffectiveSnapshot(index or library(), SourceSelection(world, tuple(sources)), instances=instances)


def reason(call):
    with pytest.raises(PortrayalError) as caught:
        call()
    return caught.value.reason


def test_world_less_selection_resolves_a_character_and_a_lorebook():
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character"), SelectedSource(NOTES, "lorebook"))
    assert view.world is None
    assert view.effective(VAMPIRE_MARA.unpinned()).section("species") == "Vampire, turned in 1891."
    assert view.resolve(NOTES.unpinned()) == Lore(NOTES, "Notes")
    assert reason(lambda: view.effective(NOTES)) == "not_entity_definition"
    assert [(r.source.ref, r.resolved) for r in view.sources()] == [(VAMPIRE_MARA, VAMPIRE_MARA), (NOTES, NOTES)]


def test_a_world_does_not_expose_its_unselected_catalog():
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character"), world=VAMPIRE)
    assert view.world == VAMPIRE
    assert reason(lambda: view.resolve(DOCKS)) == "not_selected"
    assert reason(lambda: view.resolve(MODERN_MARA)) == "not_selected"


def test_two_roles_at_two_revisions_are_legal_and_an_unnamed_role_is_ambiguous():
    newer = EntityDefinition(VAMPIRE_MARA.at("r2"), "Mara", "individual", FacetSet([Agent()]))
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character"), SelectedSource(VAMPIRE_MARA.at("r2"), "persona"),
                    index=library(newer, heads={VAMPIRE_MARA.unpinned(): "r2"}))
    assert view.effective(VAMPIRE_MARA.unpinned(), role="character").ref == VAMPIRE_MARA
    assert view.effective(VAMPIRE_MARA.unpinned(), role="persona").ref == VAMPIRE_MARA.at("r2")
    assert view.effective(VAMPIRE_MARA.at("r2")).ref == VAMPIRE_MARA.at("r2")
    assert reason(lambda: view.effective(VAMPIRE_MARA.unpinned())) == "ambiguous_selection"


def test_conflicting_selections_are_refused():
    assert reason(lambda: SourceSelection(None, (SelectedSource(VAMPIRE_MARA, "character"),
                                                 SelectedSource(VAMPIRE_MARA.unpinned(), "character")))
                  ) == "conflicting_selection"
    assert reason(lambda: SourceSelection(None, (SelectedSource(VAMPIRE_MARA, "character", actor=ACTOR),
                                                 SelectedSource(MODERN_MARA, "character", actor=ACTOR)))
                  ) == "conflicting_selection"
    assert reason(lambda: SelectedSource(VAMPIRE_MARA, "character", "r2")) == "invalid_selection"
    assert reason(lambda: SelectedSource(VAMPIRE_MARA, "Bad Role")) == "invalid_selection"
    assert reason(lambda: SourceSelection("Not A World")) == "invalid_selection"


def test_a_pinned_request_must_match_the_selection():
    newer = EntityDefinition(VAMPIRE_MARA.at("r2"), "Mara", "individual", FacetSet([Agent()]))
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character"), index=library(newer, heads={
        VAMPIRE_MARA.unpinned(): "r2"}))
    assert reason(lambda: view.resolve(VAMPIRE_MARA.at("r2"))) == "revision_not_selected"


def test_chain_members_are_readable_but_their_siblings_are_not():
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character"))
    raw = view.resolve(BASE)  # raw ancestry, for merge owners
    assert raw.extras["sections"]["mortal-family"] == BASE_SECTIONS["mortal-family"]
    assert view.effective(VAMPIRE_MARA).section("mortal-family") is None
    assert reason(lambda: view.resolve(BASE.unpinned())) == "not_selected"
    assert reason(lambda: view.resolve(MODERN_MARA)) == "not_selected"


def test_a_broken_selection_raises_missing_definition_and_is_listed():
    gone = SourceRef(CAST, "character", "nobody", "r1")
    view = snapshot(SelectedSource(gone, "character"))
    assert view.sources()[0].broken.reason == "missing_entity"
    with pytest.raises(MissingDefinition):
        view.resolve(gone)


def test_an_unpinned_selection_that_moved_reports_drift():
    newer = EntityDefinition(VAMPIRE_MARA.at("r2"), "Mara", "individual", FacetSet([Agent()]))
    view = snapshot(SelectedSource(VAMPIRE_MARA.unpinned(), "character", "r1"),
                    index=library(newer, heads={VAMPIRE_MARA.unpinned(): "r2"}))
    [resolution] = view.sources()
    assert resolution.resolved == VAMPIRE_MARA.at("r2")
    assert [(n.code, n.detail) for n in resolution.notices] == [("binding_drift", "r1 -> r2")]


def test_the_snapshot_is_frozen_against_a_newer_index():
    selection = SourceSelection(None, (SelectedSource(VAMPIRE_MARA.unpinned(), "character", "r1"),))
    old = EffectiveSnapshot(library(), selection)
    before = old.effective(VAMPIRE_MARA.unpinned())
    newer = EntityDefinition(VAMPIRE_MARA.at("r2"), "Mara", "individual", FacetSet([Agent()]))
    fresh = EffectiveSnapshot(library(newer, heads={VAMPIRE_MARA.unpinned(): "r2"}), selection)
    assert fresh.effective(VAMPIRE_MARA.unpinned()).ref == VAMPIRE_MARA.at("r2")
    assert old.effective(VAMPIRE_MARA.unpinned()) == before and before.ref == VAMPIRE_MARA


def test_two_snapshots_over_one_index_agree():
    index = library()
    selection = SourceSelection(None, (SelectedSource(VAMPIRE_MARA, "character"),
                                       SelectedSource(MODERN_MARA, "persona")))
    one, two = EffectiveSnapshot(index, selection), EffectiveSnapshot(index, selection)
    for ref in (VAMPIRE_MARA, MODERN_MARA):
        assert one.effective(ref) == two.effective(ref)
    assert one.sources() == two.sources()


def vampire_actor(**fields):
    return EntityInstance(ACTOR, VAMPIRE_MARA, **fields)


def test_an_instance_composes_over_the_effective_portrayal():
    awakened = vampire_actor(replaced=FacetBundle([Physical(size="small")]))
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character", actor=ACTOR), instances=[(awakened, "sha256:x")])
    entity = view.entity(ACTOR)
    assert entity.resolved_source == VAMPIRE_MARA and entity.name == "Mara"
    assert entity.facets.get(Physical) == Physical(size="small") and "biological" not in entity.facets
    assert dict(entity.origins)["physical"] == "replaced" and view.instance_etag(ACTOR) == "sha256:x"


def test_an_instance_adds_a_facet_over_the_effective_portrayal():
    from grimoire.core.facets import Spatial
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character", actor=ACTOR),
                    instances=[(vampire_actor(added=FacetBundle([Spatial()])), None)])
    assert dict(view.entity(ACTOR).origins)["spatial"] == "added"


def test_replacing_a_cleared_facet_is_replaced_without_trait():
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character", actor=ACTOR),
                    instances=[(vampire_actor(replaced=FacetBundle([Biological()])), None)])
    with pytest.raises(EntityError) as caught:
        view.entity(ACTOR)
    assert caught.value.reason == "replaced_without_trait"


def test_an_actor_is_matched_by_its_selection_not_by_source_text():
    newer = EntityDefinition(VAMPIRE_MARA.at("r2"), "Mara", "individual", FacetSet([Agent()]))
    reused = EntityInstance(ACTOR, VAMPIRE_MARA.unpinned())  # payload source never rewritten on reuse
    view = snapshot(SelectedSource(VAMPIRE_MARA.at("r2"), "character", actor=ACTOR),
                    index=library(newer, heads={VAMPIRE_MARA.unpinned(): "r2"}), instances=[(reused, None)])
    assert view.entity(ACTOR).resolved_source == VAMPIRE_MARA.at("r2")


def test_an_actor_whose_source_names_another_entity_is_a_mismatch():
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character", actor=ACTOR),
                    instances=[(EntityInstance(ACTOR, NAMESAKE), None)])
    assert reason(lambda: view.entity(ACTOR)) == "actor_source_mismatch"


def test_a_detached_actor_is_not_selected_even_if_its_ref_text_is():
    other = InstanceRef("quick-1", "character", "mara-2")
    view = snapshot(SelectedSource(VAMPIRE_MARA, "persona", actor=other),
                    instances=[(vampire_actor(), None), (EntityInstance(other, VAMPIRE_MARA), None)])
    assert reason(lambda: view.entity(ACTOR)) == "not_selected"
    assert view.entity(other).resolved_source == VAMPIRE_MARA


def test_a_campaign_original_composes_without_a_base():
    stone = EntityInstance(InstanceRef("quick-1", "item", "stone"), None, "Stone", FacetBundle([Physical()]))
    view = snapshot(instances=[(stone, None)])
    assert view.entity(stone.ref).resolved_source is None and view.entity(stone.ref).name == "Stone"


def test_uncaptured_and_duplicate_instances():
    view = snapshot()
    assert reason(lambda: view.entity(ACTOR)) == "unknown_instance"
    assert reason(lambda: snapshot(instances=[(vampire_actor(), None), (vampire_actor(), None)])
                  ) == "conflicting_instance"


def test_find_by_name_is_limited_to_selected_entities():
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character"))
    assert view.find_by_name("mara") == (VAMPIRE_MARA.unpinned(),)  # neither the base nor the namesake
    assert snapshot().find_by_name("mara") == ()


def test_compositions_and_their_errors_are_memoized():
    from portrayal_fixtures import derived
    reads = []

    def decode(definition):
        reads.append(definition.ref)
        return definition

    broken = derived(MODERN_MARA, clear_sections=["wings"])
    view = EffectiveSnapshot(elderfen_index(base(), broken, vampire()), SourceSelection(None, (
        SelectedSource(MODERN_MARA, "character"), SelectedSource(VAMPIRE_MARA, "persona"))), decode=decode)
    assert reason(lambda: view.effective(MODERN_MARA)) == "stale_clear"
    assert reason(lambda: view.effective(MODERN_MARA)) == "stale_clear"
    assert view.effective(VAMPIRE_MARA) is view.effective(VAMPIRE_MARA)
    assert sorted(map(str, reads)) == sorted(map(str, [MODERN_MARA, BASE, VAMPIRE_MARA, BASE]))


def test_a_pinned_request_respects_its_role():
    newer = EntityDefinition(VAMPIRE_MARA.at("r2"), "Mara", "individual", FacetSet([Agent()]))
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character"), SelectedSource(VAMPIRE_MARA.at("r2"), "persona"),
                    index=library(newer, heads={VAMPIRE_MARA.unpinned(): "r2"}))
    assert reason(lambda: view.effective(VAMPIRE_MARA.at("r2"), role="character")) == "revision_not_selected"
    assert reason(lambda: view.resolve(VAMPIRE_MARA, role="persona")) == "revision_not_selected"
    assert reason(lambda: view.resolve(VAMPIRE_MARA, role="lorebook")) == "role_not_selected"
    assert reason(lambda: view.effective(VAMPIRE_MARA.unpinned(), role="lorebook")) == "role_not_selected"
    assert view.resolve(BASE, role="character").ref == BASE  # an ancestor of that role's selection
    assert reason(lambda: view.resolve(BASE, role="persona")) == "not_selected"  # r2 has no base


def test_effective_keeps_the_ref_as_asked():
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character"))
    assert view.effective(VAMPIRE_MARA.unpinned()).requested == VAMPIRE_MARA.unpinned()
    assert view.effective(VAMPIRE_MARA).requested == VAMPIRE_MARA


def test_find_by_name_uses_the_selected_revisions_name():
    renamed = EntityDefinition(VAMPIRE_MARA.at("r2"), "Lady Mara", "individual", FacetSet([Agent()]))
    view = snapshot(SelectedSource(VAMPIRE_MARA.at("r2"), "character"),
                    index=library(renamed, heads={VAMPIRE_MARA.unpinned(): "r2"}))
    assert view.find_by_name("mara") == () and view.find_by_name("LADY  mara") == (VAMPIRE_MARA.unpinned(),)
    assert view.find_by_name("lady mara", kind="lorebook") == ()


def test_ancestors_stay_readable_when_the_leaf_fails_a_field_rule():
    from portrayal_fixtures import derived
    broken = derived(VAMPIRE_MARA, clear_sections=["no-such-section"])
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character"), index=elderfen_index(base(), broken))
    assert reason(lambda: view.effective(VAMPIRE_MARA)) == "stale_clear"
    assert view.resolve(BASE).ref == BASE  # the merge owner can still reach the pinned base


def test_effective_never_serves_an_unselected_ancestor():
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character"))
    assert view.resolve(BASE).ref == BASE
    assert reason(lambda: view.effective(BASE)) == "not_selected"  # its cleared facts never reach a prompt


def test_find_by_name_uses_the_decoded_entity_name():
    """A stored envelope's display name may differ from (or omit) the authored name."""
    from dataclasses import replace

    def decode(raw):
        return replace(raw, name="Mira") if raw.ref == VAMPIRE_MARA else raw

    view = EffectiveSnapshot(library(), SourceSelection(None, (SelectedSource(VAMPIRE_MARA, "character"),
                                                               SelectedSource(NOTES, "lorebook"))), decode=decode)
    assert view.find_by_name("mira") == (VAMPIRE_MARA.unpinned(),) and view.find_by_name("mara") == ()
    assert view.find_by_name("notes") == (NOTES.unpinned(),)  # a non-entity record keeps its raw name


def test_an_instance_pinned_to_another_revision_is_reported():
    newer = EntityDefinition(VAMPIRE_MARA.at("r2"), "Mara", "individual", FacetSet([Agent()]))
    index = library(newer, heads={VAMPIRE_MARA.unpinned(): "r2"})
    torn = EntityInstance(ACTOR, VAMPIRE_MARA.at("r3"))  # written against a revision this index lacks
    view = snapshot(SelectedSource(VAMPIRE_MARA.unpinned(), "character", "r1", actor=ACTOR), index=index,
                    instances=[(torn, None)])
    [notice] = view.instance_notices(ACTOR)
    assert (notice.code, notice.detail) == ("instance_source_drift", "r3 -> r2 (missing_revision)")
    assert view.entity(ACTOR).resolved_source == VAMPIRE_MARA.at("r2")
    steady = snapshot(SelectedSource(VAMPIRE_MARA, "character", actor=ACTOR), instances=[(vampire_actor(), None)])
    assert steady.instance_notices(ACTOR) == ()


@pytest.mark.parametrize("where", ["leaf", "base"])
def test_ancestors_stay_readable_when_a_section_is_malformed(where):
    from portrayal_fixtures import derived
    bad = {"species": "x" * 8001}
    leaf = derived(VAMPIRE_MARA, sections=bad) if where == "leaf" else vampire()
    root = base(sections=bad) if where == "base" else base()
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character"), index=elderfen_index(root, leaf))
    assert reason(lambda: view.effective(VAMPIRE_MARA)) == "invalid_sections"
    assert view.resolve(BASE).ref == BASE


def test_an_unwalkable_ancestry_says_so_instead_of_not_selected():
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character"), index=elderfen_index(base(), vampire(), links=[]))
    with pytest.raises(PortrayalError) as caught:
        view.resolve(BASE)
    assert (caught.value.reason, caught.value.detail) == ("ancestry_unavailable", "unlinked_derivation")


def test_a_rebound_actor_is_not_a_torn_load():
    newer = EntityDefinition(VAMPIRE_MARA.at("r2"), "Mara", "individual", FacetSet([Agent()]))
    index = library(newer, heads={VAMPIRE_MARA.unpinned(): "r2"})
    view = snapshot(SelectedSource(VAMPIRE_MARA.at("r2"), "character", actor=ACTOR), index=index,
                    instances=[(vampire_actor(), None)])  # pins r1, which exists: an ordinary 107 rebind
    [notice] = view.instance_notices(ACTOR)
    assert (notice.code, notice.detail) == ("actor_rebound", "r1 -> r2")


def test_find_by_name_skips_an_undecodable_entity():
    def decode(raw):
        if raw.ref == VAMPIRE_MARA:
            raise PortrayalError("bad", "undecodable", raw.ref, "invalid_field")
        return raw

    view = EffectiveSnapshot(library(), SourceSelection(None, (SelectedSource(VAMPIRE_MARA, "character"),)),
                             decode=decode)
    assert view.find_by_name("mara") == () and view.find_by_name("") == () and view.find_by_name("   ") == ()


def test_a_selected_lorebook_never_masks_an_honest_refusal():
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character"), SelectedSource(NOTES, "lorebook"))
    assert reason(lambda: view.resolve(NAMESAKE)) == "not_selected"
    assert reason(lambda: view.resolve(MODERN_MARA)) == "not_selected"
    assert reason(lambda: view.resolve(VAMPIRE_MARA.at("r9"))) == "revision_not_selected"
    assert reason(lambda: view.resolve(NOTES.at("r9"))) == "revision_not_selected"
    assert view.resolve(BASE).ref == BASE


def test_a_roles_ancestor_is_reachable_when_its_entity_is_also_selected_under_another_role():
    newer = EntityDefinition(BASE.at("r2"), "Mara", "individual", FacetSet([Agent()]))
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character"), SelectedSource(BASE.unpinned(), "persona"),
                    index=library(newer, heads={BASE.unpinned(): "r2"}))
    assert view.resolve(BASE, role="character").ref == BASE  # the character's pinned base
    assert reason(lambda: view.resolve(BASE.unpinned(), role="character")) == "role_not_selected"
    assert reason(lambda: view.effective(BASE, role="character")) == "role_not_selected"


def test_a_pinned_request_for_a_broken_head_following_selection_is_missing_not_another_revision():
    """A head-following selection of an absent entity selected no revision: every revision of it is missing."""
    gone = SourceRef(CAST, "character", "seraphine")
    view = snapshot(SelectedSource(gone, "character"))
    for call in (lambda: view.resolve(gone.at("r1")), lambda: view.effective(gone.at("r1"))):
        with pytest.raises(MissingDefinition) as caught:
            call()
        assert (caught.value.ref, caught.value.reason) == (gone, "missing_entity")


def test_ancestors_stay_readable_when_the_composed_portrayal_is_over_the_section_cap():
    from portrayal_fixtures import derived
    crowded = derived(VAMPIRE_MARA, sections={f"rite{n}": f"Rite {n}." for n in range(60)})  # 9 + 60 composed
    view = snapshot(SelectedSource(VAMPIRE_MARA, "character"), index=elderfen_index(base(), crowded))
    assert reason(lambda: view.effective(VAMPIRE_MARA)) == "too_many_sections"
    assert view.resolve(BASE).ref == BASE  # the merge owner can still reach the pinned base


@pytest.mark.parametrize("sources", [None, SelectedSource(VAMPIRE_MARA, "character"), 7])
def test_a_selection_whose_sources_are_not_a_collection_is_invalid(sources):
    assert reason(lambda: SourceSelection(None, sources)) == "invalid_selection"

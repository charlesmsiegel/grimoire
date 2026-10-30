"""Definition index: name-safe resolution, attributed conflicts, links, legacy IDs."""

from dataclasses import dataclass

import pytest

from grimoire.core.identity import IdentityConflict, LegacyId, SourceRef
from grimoire.core.index import (
    BrokenReference, DefinitionIndex, DefinitionLink, LegacyIdMap,
    MissingDefinition, SourceDescriptor, UnmappedLegacyId,
)

EMBER = "world:emberfall"
LANTERN = "catalog:lanternworks"
FANS = "catalog:fans"
GHOST = "catalog:ghost"


@dataclass(frozen=True)
class _Def:
    """Duck-typed definition: the index is not character-specific."""

    ref: SourceRef
    name: str
    description: str = ""


def ref(source, entity_id, revision=None, kind="character"):
    return SourceRef(source, kind, entity_id, revision)


def define(source, entity_id, revision, name, kind="character", description=""):
    return _Def(ref(source, entity_id, revision, kind), name, description)


def descriptors(*source_ids):
    return [SourceDescriptor(source_id, owner="user") for source_id in source_ids]


def innocent():
    """The user's own world, which must keep building whatever an add-on does."""
    return dict(
        sources=descriptors(EMBER),
        definitions=[
            define(EMBER, "vessa", "r1", "Vessa", description="emberfall, young"),
            define(EMBER, "vessa", "r2", "Vessa", description="emberfall, grown"),
        ],
        heads={ref(EMBER, "vessa"): "r2"},
        links=[],
    )


def with_parts(sources=(), definitions=(), heads=None, links=()):
    parts = innocent()
    parts["sources"] += list(sources)
    parts["definitions"] += list(definitions)
    parts["heads"].update(heads or {})
    parts["links"] += list(links)
    return parts


def _rebuild_without(excluded, parts):
    return DefinitionIndex(
        sources=[s for s in parts["sources"] if s.source_id not in excluded],
        definitions=[d for d in parts["definitions"] if d.ref.source not in excluded],
        heads={k: v for k, v in parts["heads"].items() if k.source not in excluded},
        links=[link for link in parts["links"] if link.declared_by not in excluded],
    )


# --- Scenario A/B: same-named definitions stay distinct --------------------

def two_vessas():
    return with_parts(
        sources=descriptors(LANTERN),
        definitions=[define(LANTERN, "vessa", "v1", "Vessa", description="lanternworks portrayal")],
    )


def test_namesakes_are_candidates_never_a_choice():
    index = DefinitionIndex(**two_vessas())
    assert index.find_by_name("vessa") == (ref(LANTERN, "vessa"), ref(EMBER, "vessa"))
    assert index.resolve(ref(EMBER, "vessa")).description == "emberfall, grown"
    assert index.resolve(ref(LANTERN, "vessa")).description == "lanternworks portrayal"


def test_third_namesake_changes_candidates_not_resolution():
    before = DefinitionIndex(**two_vessas())
    parts = two_vessas()
    parts["sources"] += descriptors(FANS)
    parts["definitions"].append(define(FANS, "vessa", "a", "Vessa"))
    after = DefinitionIndex(**parts)
    assert len(after.find_by_name("Vessa")) == 3
    for chosen in (ref(EMBER, "vessa"), ref(LANTERN, "vessa"), ref(EMBER, "vessa", "r1")):
        assert after.resolve(chosen) == before.resolve(chosen)


@pytest.mark.parametrize("query", ["Vessa", "  VESSA ", "Ｖｅｓｓａ"])
def test_name_lookup_normalizes_case_space_and_compatibility_forms(query):
    index = DefinitionIndex(**two_vessas())
    assert len(index.find_by_name(query)) == 2


def test_name_lookup_dedups_revisions_filters_kind_and_remembers_old_names():
    parts = with_parts(
        sources=descriptors(LANTERN),
        definitions=[
            define(LANTERN, "lamp", "v1", "Vessa", kind="item"),
            define(LANTERN, "wren", "v1", "Wren"),
            define(LANTERN, "wren", "v2", "Wren the Elder"),
        ],
        heads={ref(LANTERN, "wren"): "v2"},
    )
    index = DefinitionIndex(**parts)
    assert index.find_by_name("vessa") == (ref(LANTERN, "lamp", kind="item"), ref(EMBER, "vessa"))
    assert index.find_by_name("vessa", kind="character") == (ref(EMBER, "vessa"),)
    assert index.find_by_name("wren") == (ref(LANTERN, "wren"),)
    assert index.find_by_name("nobody") == ()


# --- Scenario C: unpinned follows head, pinned stays -----------------------

def test_unpinned_follows_declared_head_and_pinned_stays():
    index = DefinitionIndex(**innocent())
    assert index.resolve(ref(EMBER, "vessa")).ref == ref(EMBER, "vessa", "r2")
    assert index.resolve(ref(EMBER, "vessa", "r1")).ref == ref(EMBER, "vessa", "r1")
    parts = innocent()
    parts["definitions"].append(define(EMBER, "vessa", "r3", "Vessa"))
    parts["heads"][ref(EMBER, "vessa")] = "r3"
    moved = DefinitionIndex(**parts)
    assert moved.resolve(ref(EMBER, "vessa")).ref.revision == "r3"
    assert moved.resolve(ref(EMBER, "vessa", "r1")).ref.revision == "r1"


def test_single_revision_is_its_own_head():
    index = DefinitionIndex(**two_vessas())
    assert index.resolve(ref(LANTERN, "vessa")).ref == ref(LANTERN, "vessa", "v1")


def test_index_is_immutable_after_build():
    parts = innocent()
    index = DefinitionIndex(**parts)
    parts["heads"][ref(EMBER, "vessa")] = "r1"
    parts["definitions"].clear()
    assert index.resolve(ref(EMBER, "vessa")).ref.revision == "r2"


# --- Scenario E: broken references are reported with a reason --------------

@pytest.mark.parametrize("missing, reason", [
    (ref(GHOST, "vessa"), "missing_source"),
    (ref(EMBER, "nobody"), "missing_entity"),
    (ref(EMBER, "vessa", kind="item"), "missing_entity"),
    (ref(EMBER, "vessa", "r9"), "missing_revision"),
])
def test_broken_reference_reasons(missing, reason):
    index = DefinitionIndex(**innocent())
    assert index.check(missing) == BrokenReference(missing, reason)
    with pytest.raises(MissingDefinition) as caught:
        index.resolve(missing)
    assert (caught.value.ref, caught.value.reason) == (missing, reason)
    assert index.check(ref(EMBER, "vessa")) is None


def test_check_links_reports_links_into_absent_sources():
    absent = ref(GHOST, "vessa")
    link = DefinitionLink(ref(EMBER, "vessa"), "same_individual", absent, EMBER)
    index = DefinitionIndex(**with_parts(links=[link]))
    assert index.check_links() == ((link, BrokenReference(absent, "missing_source")),)


# --- Scenario F: conflicts are attributed so a bad add-on can be excluded --

CONFLICTS = {
    "duplicate exact ref": (with_parts(
        sources=descriptors(LANTERN),
        definitions=[define(LANTERN, "vessa", "v1", "Vessa"), define(LANTERN, "vessa", "v1", "Vessa")],
    ), (LANTERN,)),
    "entity id reused across kinds": (with_parts(
        sources=descriptors(LANTERN),
        definitions=[define(LANTERN, "lamp", "v1", "Lamp"), define(LANTERN, "lamp", "v1", "Lamp", kind="item")],
    ), (LANTERN,)),
    "definition from unregistered source": (with_parts(
        definitions=[define(GHOST, "vessa", "v1", "Vessa")],
    ), (GHOST,)),
    "unpinned duck-typed definition": (with_parts(
        sources=descriptors(LANTERN),
        definitions=[_Def(ref(LANTERN, "vessa"), "Vessa")],
    ), (LANTERN,)),
    "link declared by unregistered source": (with_parts(
        links=[DefinitionLink(ref(EMBER, "vessa"), "same_individual", ref(LANTERN, "vessa"), GHOST)],
    ), (GHOST,)),
    "several revisions without head": (with_parts(
        sources=descriptors(LANTERN),
        definitions=[define(LANTERN, "vessa", "v1", "Vessa"), define(LANTERN, "vessa", "v2", "Vessa")],
    ), (LANTERN,)),
    "head names unknown revision": (with_parts(
        sources=descriptors(LANTERN),
        definitions=[define(LANTERN, "vessa", "v1", "Vessa")],
        heads={ref(LANTERN, "vessa"): "v9"},
    ), (LANTERN,)),
    "head keyed by pinned ref": (with_parts(
        sources=descriptors(LANTERN),
        definitions=[define(LANTERN, "vessa", "v1", "Vessa")],
        heads={ref(LANTERN, "vessa", "v1"): "v1"},
    ), (LANTERN,)),
    "head for unknown entity": (with_parts(
        sources=descriptors(LANTERN),
        heads={ref(LANTERN, "nobody"): "v1"},
    ), (LANTERN,)),
    "self link": (with_parts(
        sources=descriptors(LANTERN),
        definitions=[define(LANTERN, "vessa", "v1", "Vessa")],
        links=[DefinitionLink(ref(LANTERN, "vessa", "v1"), "alternate_portrayal", ref(LANTERN, "vessa"), LANTERN)],
    ), (LANTERN,)),
    "duplicate link by one source": (with_parts(
        sources=descriptors(LANTERN),
        links=[
            DefinitionLink(ref(LANTERN, "vessa"), "same_individual", ref(EMBER, "vessa"), LANTERN),
            DefinitionLink(ref(LANTERN, "vessa"), "same_individual", ref(EMBER, "vessa", "r1"), LANTERN),
        ],
    ), (LANTERN,)),
    "reversed symmetric duplicate by two sources": (with_parts(
        sources=descriptors(LANTERN, FANS),
        links=[
            DefinitionLink(ref(LANTERN, "vessa"), "alternate_portrayal", ref(EMBER, "vessa"), LANTERN),
            DefinitionLink(ref(EMBER, "vessa"), "alternate_portrayal", ref(LANTERN, "vessa"), FANS),
        ],
    ), (FANS, LANTERN)),
    "derivation cycle": (with_parts(
        sources=descriptors(LANTERN),
        definitions=[define(LANTERN, "a", "v1", "A"), define(LANTERN, "b", "v1", "B")],
        links=[
            DefinitionLink(ref(LANTERN, "a"), "derived_variant", ref(LANTERN, "b", "v1"), LANTERN),
            DefinitionLink(ref(LANTERN, "b"), "overrides", ref(LANTERN, "a", "v1"), LANTERN),
        ],
    ), (LANTERN,)),
}


@pytest.mark.parametrize("case", sorted(CONFLICTS))
def test_conflicts_name_the_offender_and_excluding_it_builds(case):
    parts, offenders = CONFLICTS[case]
    with pytest.raises(IdentityConflict) as caught:
        DefinitionIndex(**parts)
    assert caught.value.sources == offenders
    rebuilt = _rebuild_without(caught.value.sources, parts)
    assert rebuilt.resolve(ref(EMBER, "vessa")).ref.revision == "r2"


def test_duplicate_source_id_names_both_owners():
    parts = innocent()
    parts["sources"].append(SourceDescriptor(EMBER, owner="creator:lanternworks"))
    with pytest.raises(IdentityConflict) as caught:
        DefinitionIndex(**parts)
    assert caught.value.sources == (EMBER,)
    assert "user" in str(caught.value) and "creator:lanternworks" in str(caught.value)


def test_unpinned_derivation_base_is_rejected_at_the_link():
    with pytest.raises(IdentityConflict) as caught:
        DefinitionLink(ref(LANTERN, "vessa"), "derived_variant", ref(EMBER, "vessa"), LANTERN)
    assert caught.value.sources == (LANTERN,)
    assert DefinitionIndex(**with_parts(sources=descriptors(LANTERN))).check(ref(EMBER, "vessa")) is None


@pytest.mark.parametrize("args", [
    (ref(EMBER, "a"), "related", ref(EMBER, "b"), EMBER),
    (ref(EMBER, "a"), "same_individual", ref(EMBER, "b"), "Not A Source"),
    ("world:emberfall/character/a", "same_individual", ref(EMBER, "b"), EMBER),
])
def test_malformed_links_rejected(args):
    with pytest.raises(ValueError):
        DefinitionLink(*args)


def test_non_source_ref_definition_rejected():
    with pytest.raises(ValueError):
        DefinitionIndex(**with_parts(definitions=[_Def("world:emberfall/character/x@r1", "X")]))


def test_source_descriptor_requires_owner():
    with pytest.raises(ValueError):
        SourceDescriptor(LANTERN, owner=" ")
    with pytest.raises(ValueError):
        SourceDescriptor("v1:arch", owner="user")


# --- Scenario G: explicit links are the only relation, and never resolve ---

def test_links_are_explicit_listed_from_both_ends_and_do_not_affect_resolution():
    portrayal = DefinitionLink(ref(LANTERN, "vessa"), "alternate_portrayal", ref(EMBER, "vessa"), LANTERN)
    parts = two_vessas()
    parts["sources"] += descriptors(FANS)
    parts["definitions"].append(define(FANS, "vessa", "a", "Vessa"))
    parts["links"].append(portrayal)
    index = DefinitionIndex(**parts)
    assert index.links_for(ref(EMBER, "vessa", "r1")) == (portrayal,)
    assert index.links_for(ref(LANTERN, "vessa")) == (portrayal,)
    assert index.links_for(ref(FANS, "vessa")) == ()  # same name, no relation
    unlinked = DefinitionIndex(**two_vessas())
    for chosen in (ref(EMBER, "vessa"), ref(LANTERN, "vessa")):
        assert index.resolve(chosen) == unlinked.resolve(chosen)


def test_rederivation_from_a_new_base_uses_pinned_subject_revisions():
    links = [
        DefinitionLink(ref(LANTERN, "vessa", "v1"), "derived_variant", ref(EMBER, "vessa", "r1"), LANTERN),
        DefinitionLink(ref(LANTERN, "vessa", "v2"), "derived_variant", ref(EMBER, "vessa", "r2"), LANTERN),
    ]
    index = DefinitionIndex(**with_parts(
        sources=descriptors(LANTERN),
        definitions=[define(LANTERN, "vessa", "v1", "Vessa"), define(LANTERN, "vessa", "v2", "Vessa")],
        heads={ref(LANTERN, "vessa"): "v2"},
        links=links,
    ))
    assert index.links_for(ref(LANTERN, "vessa")) == tuple(links)


# --- Scenario H: legacy V1 IDs map by archive + path, never by slug --------

def test_legacy_ids_with_equal_paths_in_two_archives_stay_distinct():
    path = "worlds/emberfall/characters/vessa/character.md"
    mapping = LegacyIdMap([
        (LegacyId("arch-a", path), ref(EMBER, "vessa", "r1")),
        (LegacyId("arch-b", path), ref(LANTERN, "vessa", "v1")),
        (LegacyId("arch-a", path), ref(EMBER, "vessa", "r1")),  # idempotent repeat
        (LegacyId("arch-a", "pcs/vessa/pc.md"), ref(EMBER, "vessa", "r1")),  # many-to-one
    ])
    assert mapping.resolve(LegacyId("arch-a", path)) == ref(EMBER, "vessa", "r1")
    assert mapping.resolve(LegacyId("arch-b", path)) == ref(LANTERN, "vessa", "v1")
    with pytest.raises(UnmappedLegacyId):
        mapping.resolve(LegacyId("arch-c", path))


def test_legacy_map_rejects_unpinned_targets_and_one_to_many():
    with pytest.raises(IdentityConflict) as caught:
        LegacyIdMap([(LegacyId("arch-a", "a.md"), ref(EMBER, "vessa"))])
    assert caught.value.sources == (EMBER,)
    with pytest.raises(IdentityConflict) as caught:
        LegacyIdMap([
            (LegacyId("arch-a", "a.md"), ref(EMBER, "vessa", "r1")),
            (LegacyId("arch-a", "a.md"), ref(LANTERN, "vessa", "v1")),
        ])
    assert caught.value.sources == (LANTERN, EMBER)

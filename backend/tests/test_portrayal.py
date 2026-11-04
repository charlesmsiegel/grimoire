"""Effective authored definitions over a pinned derivation chain (Sprint 022)."""

from __future__ import annotations

import pickle

import pytest
from portrayal_fixtures import (
    BASE, BASE_SECTIONS, CAST, MODERN, MODERN_MARA, NAMESAKE, VAMPIRE, VAMPIRE_MARA, VOICE, base, derived,
    derives, elderfen_index, modern, namesake, vampire,
)

from grimoire.core import DefinitionLink, MissingDefinition, SourceRef
from grimoire.core.entity import EntityDefinition
from grimoire.core.facets import Agent, Biological, Durable, FacetSet, Physical, UnknownFacet
from grimoire.core.portrayal import (
    MAX_CHAIN, ChainNode, FieldOrigin, PortrayalError, compose, read_entities,
)


def effective(ref, index=None):
    index = index or elderfen_index()
    return compose(ref, index, read_entities(index))


def refusal(ref, index=None):
    with pytest.raises(PortrayalError) as caught:
        effective(ref, index)
    return caught.value


def origins(view):
    return {entry.field: (entry.origin, entry.source.ref) for entry in view.provenance}


# -- the proving scenario ------------------------------------------------------------------------

def test_modern_inherits_and_replaces():
    view = effective(MODERN_MARA)
    assert view.ref == MODERN_MARA and [node.ref for node in view.chain] == [MODERN_MARA, BASE]
    assert view.section("occupation") == "Data librarian." and view.section("age") == "Twenty-nine."
    assert view.section("personality") == BASE_SECTIONS["personality"]
    assert view.section("mortal-family") == BASE_SECTIONS["mortal-family"]
    assert view.section("period-customs") is None  # incompatible with a modern setting: cleared
    assert view.traits == base().traits and view.description == base().description
    assert origins(view) == {
        "name": ("restated", MODERN_MARA),
        "description": ("inherited", BASE),
        "section:period-customs": ("cleared", MODERN_MARA),
        **{f"section:{name}": ("replaced", MODERN_MARA) for name in ("age", "occupation", "background")},
        **{f"section:{name}": ("inherited", BASE)
           for name in ("personality", "voice-evidence", "appearance", "mortal-family", "species")},
        **{f"facet:{name}": ("inherited", BASE) for name in ("agent", "biological", "communicative", "physical")},
    }


def test_vampire_replaces_species_and_clears_mortal_facts():
    view = effective(VAMPIRE_MARA)
    assert view.section("species") == "Vampire, turned in 1891."
    assert view.section("mortal-family") is None and "biological" not in view.traits
    assert set(view.traits.names) == {"agent", "communicative", "physical"}
    assert origins(view) == {
        "name": ("restated", VAMPIRE_MARA),
        "description": ("inherited", BASE),
        "section:species": ("replaced", VAMPIRE_MARA),
        "section:mortal-family": ("cleared", VAMPIRE_MARA),
        "facet:biological": ("cleared", VAMPIRE_MARA),
        **{f"section:{name}": ("inherited", BASE) for name in (
            "personality", "voice-evidence", "appearance", "age", "occupation", "background", "period-customs")},
        **{f"facet:{name}": ("inherited", BASE) for name in ("agent", "communicative", "physical")},
    }
    assert "Twenty-nine." not in str(view)  # another world's portrayal never leaks in


def test_root_values_are_local():
    view = effective(BASE)
    assert [node.ref for node in view.chain] == [BASE]
    assert {origin for origin, _ in origins(view).values()} == {"local"}
    assert view.sections == tuple(sorted(BASE_SECTIONS.items()))


def test_chain_nodes_carry_the_etag_the_reader_returns():
    index = elderfen_index()
    plain = read_entities(index)
    view = compose(VAMPIRE_MARA, index, lambda ref: (plain(ref)[0], f"sha256:{ref.source}"))
    assert view.chain == (ChainNode(VAMPIRE_MARA, f"sha256:{VAMPIRE}"), ChainNode(BASE, f"sha256:{CAST}"))
    assert view.origin("section:personality") == FieldOrigin(
        "section:personality", "inherited", ChainNode(BASE, f"sha256:{CAST}"))


def test_an_unpinned_request_composes_the_head():
    view = effective(VAMPIRE_MARA.unpinned())
    assert view.requested == VAMPIRE_MARA.unpinned() and view.ref == VAMPIRE_MARA


def test_middle_layer_clear_stays_cleared_and_inheritance_keeps_its_author():
    middle = SourceRef(MODERN, "character", "mara-night", "r1")
    leaf = SourceRef(VAMPIRE, "character", "mara-night", "r1")
    index = elderfen_index(base(), derived(middle, clear_sections=["mortal-family"],
                                           sections={"age": "Ageless."}),
                           derived(leaf, middle, sections={"occupation": "Night archivist."}))
    found = origins(effective(leaf, index))
    assert found["section:mortal-family"] == ("cleared", middle)
    assert found["section:age"] == ("inherited", middle)
    assert found["section:personality"] == ("inherited", BASE)
    assert found["section:occupation"] == ("replaced", leaf)


def test_a_restated_equal_value_is_attributed_to_the_child():
    leaf = derived(MODERN_MARA, traits=[Physical(), Biological()], description=base().description,
                   sections={"age": BASE_SECTIONS["age"]})
    found = origins(effective(MODERN_MARA, elderfen_index(base(), leaf)))
    assert found["facet:physical"] == ("restated", MODERN_MARA)
    assert found["description"] == ("restated", MODERN_MARA)
    assert found["section:age"] == ("restated", MODERN_MARA)
    assert found["facet:agent"] == ("inherited", BASE)


def test_a_renamed_portrayal_replaces_its_name():
    found = origins(effective(MODERN_MARA, elderfen_index(base(), derived(MODERN_MARA, name="Mara Vale"))))
    assert found["name"] == ("replaced", MODERN_MARA)


def test_a_replaced_facet_and_an_unknown_facet_compose():
    leaf = derived(MODERN_MARA, traits=[Physical(size="small"), UnknownFacet("aura", {"hue": "blue"})])
    view = effective(MODERN_MARA, elderfen_index(base(), leaf))
    assert view.traits.get(Physical) == Physical(size="small")
    assert origins(view)["facet:physical"] == ("replaced", MODERN_MARA)
    assert origins(view)["facet:aura"] == ("local", MODERN_MARA)


def test_unregistered_extras_are_listed_not_composed():
    leaf = derived(MODERN_MARA)
    leaf = EntityDefinition(leaf.ref, leaf.name, leaf.nature, leaf.traits, "", {**leaf.extras, "x-mood": "dry"})
    view = effective(MODERN_MARA, elderfen_index(base(**{"x-palette": ["grey"]}), leaf))
    assert view.uncomposed == ((BASE, "x-palette"), (MODERN_MARA, "x-mood"))


# -- what is not inheritance ---------------------------------------------------------------------

def test_a_namesake_is_standalone():
    view = effective(NAMESAKE)
    assert [node.ref for node in view.chain] == [NAMESAKE] and view.sections == ()
    assert view.notices == () and view.ignored_links == ()


@pytest.mark.parametrize("relation", ["alternate_portrayal", "same_individual", "overrides"])
def test_other_relations_are_not_inheritance(relation):
    link = DefinitionLink(NAMESAKE, relation, BASE, NAMESAKE.source)
    view = effective(NAMESAKE, elderfen_index(base(), namesake(), links=[link]))
    assert [node.ref for node in view.chain] == [NAMESAKE] and view.notices == ()
    assert view.traits == namesake().traits


def test_a_link_without_a_payload_derivation_is_ancestry_only():
    view = effective(NAMESAKE, elderfen_index(base(), namesake(), links=[derives(NAMESAKE)]))
    assert [node.ref for node in view.chain] == [NAMESAKE] and view.sections == ()
    assert [(n.code, n.ref, n.detail) for n in view.notices] == [("ancestry_only", NAMESAKE, str(BASE))]


def test_a_third_party_derivation_link_is_ignored_and_reported():
    foreign = derives(NAMESAKE, declared_by="addon:fan")
    view = effective(NAMESAKE, elderfen_index(base(), namesake(), links=[foreign]))
    assert [node.ref for node in view.chain] == [NAMESAKE] and view.ignored_links == (foreign,)


def test_a_third_party_link_cannot_satisfy_a_payload_derivation():
    index = elderfen_index(base(), modern(), links=[derives(MODERN_MARA, declared_by="addon:fan")])
    error = refusal(MODERN_MARA, index)
    assert (error.reason, error.ref) == ("unlinked_derivation", MODERN_MARA)


# -- refusals ------------------------------------------------------------------------------------

def test_a_removed_link_never_falls_back_to_standalone():
    error = refusal(MODERN_MARA, elderfen_index(base(), modern(), links=[]))
    assert (error.reason, error.ref) == ("unlinked_derivation", MODERN_MARA)


def test_a_link_to_another_base_is_a_mismatch():
    other = SourceRef(CAST, "character", "mara", "r2")
    index = elderfen_index(base(), EntityDefinition(other, "Mara", "individual", base().traits),
                           modern(), heads={BASE.unpinned(): "r1"}, links=[derives(MODERN_MARA, other)])
    assert refusal(MODERN_MARA, index).reason == "link_mismatch"


@pytest.mark.parametrize("missing, why", [
    (SourceRef("catalog:gone", "character", "mara", "r1"), "missing_source"),
    (SourceRef(CAST, "character", "nobody", "r1"), "missing_entity"),
    (SourceRef(CAST, "character", "mara", "r9"), "missing_revision"),
])
def test_a_missing_base_is_reported_with_its_reason(missing, why):
    error = refusal(MODERN_MARA, elderfen_index(base(), derived(MODERN_MARA, missing)))
    assert (error.reason, error.detail, error.ref) == ("missing_base", why, MODERN_MARA)


def test_a_missing_leaf_is_missing_definition():
    with pytest.raises(MissingDefinition):
        effective(SourceRef(MODERN, "character", "nobody", "r1"))


def test_a_base_behind_its_head_is_used_pinned_with_a_notice():
    newer = EntityDefinition(BASE.at("r2"), "Mara", "individual", FacetSet([Agent()]), "Rewritten.",
                             {"sections": {"age": "Forty."}})
    view = effective(MODERN_MARA, elderfen_index(base(), newer, modern(), heads={BASE.unpinned(): "r2"}))
    assert [node.ref for node in view.chain] == [MODERN_MARA, BASE]
    assert view.section("personality") == BASE_SECTIONS["personality"] and view.description == base().description
    assert [(n.code, n.ref, n.detail) for n in view.notices] == [("base_not_head", BASE, "r2")]


def test_a_base_that_is_not_an_entity_definition_is_refused():
    from grimoire.core import CharacterDefinition
    legacy = CharacterDefinition(BASE, "Mara")
    error = refusal(MODERN_MARA, elderfen_index(legacy, modern()))
    assert (error.reason, error.ref) == ("not_entity_definition", BASE)


def test_an_undecodable_base_surfaces_the_readers_refusal():
    index = elderfen_index()
    plain = read_entities(index)

    def read(ref):
        if ref == BASE:
            raise PortrayalError("cannot decode", "undecodable", ref, "unknown_facet_field")
        return plain(ref)

    with pytest.raises(PortrayalError) as caught:
        compose(VAMPIRE_MARA, index, read)
    assert (caught.value.reason, caught.value.ref, caught.value.detail) == ("undecodable", BASE, "unknown_facet_field")


@pytest.mark.parametrize("change, reason", [
    (dict(nature="template"), "nature_mismatch"),
    (dict(clear_sections=["wings"]), "stale_clear"),
    (dict(clear_facets=["durable"]), "stale_clear"),
    (dict(clear_sections=["age"], sections={"age": "Ten."}), "clear_and_set"),
    (dict(clear_facets=["physical"], traits=[Physical()]), "clear_and_set"),
    (dict(clear_description=True, description="New."), "clear_and_set"),
    (dict(clear_facets=["physical"]), "missing_requirement"),  # biological still needs physical
])
def test_layer_refusals(change, reason):
    error = refusal(MODERN_MARA, elderfen_index(base(), derived(MODERN_MARA, **change)))
    assert (error.reason, error.ref) == (reason, MODERN_MARA)


def test_clearing_an_absent_description_is_stale():
    bare = EntityDefinition(BASE, "Mara", "individual", FacetSet([Agent()]))
    error = refusal(MODERN_MARA, elderfen_index(bare, derived(MODERN_MARA, clear_description=True)))
    assert error.reason == "stale_clear"


def test_clearing_the_description_is_recorded():
    view = effective(MODERN_MARA, elderfen_index(base(), derived(MODERN_MARA, clear_description=True)))
    assert view.description == "" and origins(view)["description"] == ("cleared", MODERN_MARA)


def test_a_base_of_another_kind_is_refused():
    other = SourceRef(CAST, "creature", "mara", "r1")
    index = elderfen_index(EntityDefinition(other, "Mara", "individual", FacetSet([Agent()])),
                           derived(MODERN_MARA, other))
    assert refusal(MODERN_MARA, index).reason == "kind_mismatch"


def chain(length):
    refs = [SourceRef(f"world:layer{n}", "character", "mara", "r1") for n in range(length)]
    definitions = [EntityDefinition(refs[0], "Mara", "individual", FacetSet([Agent()]))]
    definitions += [derived(refs[n], refs[n - 1]) for n in range(1, length)]
    return refs[-1], elderfen_index(*definitions)


def test_the_chain_bound():
    leaf, index = chain(MAX_CHAIN)
    assert len(effective(leaf, index).chain) == MAX_CHAIN
    leaf, index = chain(MAX_CHAIN + 1)
    assert refusal(leaf, index).reason == "chain_too_deep"


@pytest.mark.parametrize("sections", [
    ["age"], {"Age": "x"}, {"age": "   "}, {"age": 7}, {"age": "x" * 8001}, {"age": "a\x0bb"}, {"age": "a\x00"},
    {"age": "a\x85b"}, {"age": "a\u2028b"}, {f"s{n}": "x" for n in range(65)},
])
def test_malformed_sections_are_refused(sections):
    leaf = derived(MODERN_MARA, sections=None)
    leaf = EntityDefinition(MODERN_MARA, "Mara", "individual", FacetSet([Agent()]), "",
                            {**leaf.extras, "sections": sections})
    assert refusal(MODERN_MARA, elderfen_index(base(), leaf)).reason == "invalid_sections"
    bad_base = base(sections=sections)
    error = refusal(MODERN_MARA, elderfen_index(bad_base, modern()))
    assert (error.reason, error.ref) == ("invalid_sections", BASE)


@pytest.mark.parametrize("sections", [["age"], {"age": 7}, {"age": "a\x0bb"}])
def test_a_standalone_card_with_unusable_sections_stays_usable(sections):
    """It derives nothing, so 009 accepts it and composition must too: sections are left out, visibly."""
    card = EntityDefinition(NAMESAKE, "Mara", "individual", FacetSet([Agent()]), "Someone.", {"sections": sections})
    view = effective(NAMESAKE, elderfen_index(card))
    assert view.sections == () and view.description == "Someone." and (NAMESAKE, "sections") in view.uncomposed
    assert [n.code for n in view.notices] == ["invalid_sections"]


def test_section_limits_are_inclusive():
    leaf = EntityDefinition(MODERN_MARA, "Mara", "individual", FacetSet([Agent()]), "",
                            {"sections": {**{f"s{n}": "x" for n in range(63)}, "long": "a\n\tb" + "x" * 7996}})
    view = effective(MODERN_MARA, elderfen_index(leaf))
    assert len(view.sections) == 64 and view.notices == ()


@pytest.mark.parametrize("derivation", [
    "catalog:elderfen-cast/character/mara@r1", {"base": str(BASE), "clear": []}, {},
    {"base": str(BASE.unpinned())}, {"base": "not a ref"}, {"base": str(BASE), "clear_sections": "age"},
    {"base": str(BASE), "clear_sections": ["age", "age"]}, {"base": str(BASE), "clear_facets": ["Bad"]},
    {"base": str(BASE), "clear_description": 1},
])
def test_malformed_derivations_are_refused(derivation):
    leaf = EntityDefinition(MODERN_MARA, "Mara", "individual", FacetSet([Agent()]), "", {"derivation": derivation})
    assert refusal(MODERN_MARA, elderfen_index(base(), leaf, links=[derives(MODERN_MARA)])).reason == \
        "invalid_derivation"


def test_composition_changes_no_input_and_errors_pickle():
    definitions = (base(), modern(), vampire(), namesake())
    before = [pickle.dumps(d) for d in definitions]
    effective(VAMPIRE_MARA, elderfen_index(*definitions))
    assert [pickle.dumps(d) for d in definitions] == before
    error = PortrayalError("gone", "missing_base", MODERN_MARA, "missing_entity")
    copy = pickle.loads(pickle.dumps(error))
    assert (str(copy), copy.reason, copy.ref, copy.detail) == ("gone", "missing_base", MODERN_MARA, "missing_entity")


def test_a_durable_replacement_must_restate_its_requirement():
    """First-slice limit: a derived revision's own traits are a complete FacetSet (009)."""
    from grimoire.core.facets import FacetError
    with pytest.raises(FacetError):
        FacetSet([Durable(5)])
    view = effective(MODERN_MARA, elderfen_index(base(), derived(MODERN_MARA, traits=[Physical(), Durable(5)])))
    assert origins(view)["facet:durable"] == ("local", MODERN_MARA)
    assert origins(view)["facet:physical"] == ("restated", MODERN_MARA)
    assert VOICE in view.traits


@pytest.mark.parametrize("key, reason", [("derivation", "invalid_derivation"), ("sections", "invalid_sections")])
def test_a_null_registered_key_is_refused_not_ignored(key, reason):
    extras = {"derivation": {"base": str(BASE)}, key: None} if key == "sections" else {key: None}
    leaf = EntityDefinition(MODERN_MARA, "Mara", "individual", FacetSet([Agent()]), "", extras)
    assert refusal(MODERN_MARA, elderfen_index(base(), leaf, links=[derives(MODERN_MARA)])).reason == reason


def test_a_blank_description_inherits():
    view = effective(MODERN_MARA, elderfen_index(base(), derived(MODERN_MARA, description="   ")))
    assert view.description == base().description and origins(view)["description"] == ("inherited", BASE)
    blank_root = EntityDefinition(BASE, "Mara", "individual", base().traits, "  ", base().extras)
    assert effective(BASE, elderfen_index(blank_root)).origin("description") is None


def test_two_own_links_to_different_bases_are_a_mismatch():
    """CHAT_MODEL's "ambiguous simultaneous bases": one payload base, so any disagreeing link is refused."""
    links = [derives(MODERN_MARA), derives(MODERN_MARA, BASE.at("r2"))]
    error = refusal(MODERN_MARA, elderfen_index(base(), modern(), links=links))
    assert (error.reason, error.ref) == ("link_mismatch", MODERN_MARA)


def test_windows_line_endings_are_read_as_newlines():
    """CRLF text from Windows editors or pasted cards is normal user-owned JSON, never a failure."""
    crlf = base(**{"sections": {**BASE_SECTIONS, "personality": "Wry.\r\nPatient.\rKind."}})
    crlf = EntityDefinition(crlf.ref, crlf.name, crlf.nature, crlf.traits, "Line one.\r\nLine two.", crlf.extras)
    view = effective(VAMPIRE_MARA, elderfen_index(crlf, vampire()))
    assert view.section("personality") == "Wry.\nPatient.\nKind."
    assert view.description == "Line one.\r\nLine two."  # 009's field, kept verbatim


@pytest.mark.parametrize("text", ["a\x1b[2Jb", "a\u2028b", "a\x0bb", "a\r\nb"])
def test_a_description_is_009s_text_verbatim(text):
    """009 owns the description: composition never refuses or rewrites what 009 accepted."""
    view = effective(MODERN_MARA, elderfen_index(base(), derived(MODERN_MARA, description=text)))
    assert view.description == text and origins(view)["description"] == ("replaced", MODERN_MARA)
    standalone = EntityDefinition(NAMESAKE, "Mara", "individual", FacetSet([Agent()]), text)
    assert effective(NAMESAKE, elderfen_index(standalone)).description == text


def test_a_missing_base_can_carry_the_stores_reason():
    index = elderfen_index(base(), derived(MODERN_MARA, SourceRef("catalog:gone", "character", "mara", "r1")))
    with pytest.raises(PortrayalError) as caught:
        compose(MODERN_MARA, index, read_entities(index), lambda ref: "excluded_source")
    assert (caught.value.reason, caught.value.detail) == ("missing_base", "excluded_source")


def test_a_whitespace_description_is_kept_verbatim_and_agrees_with_the_index_path():
    from grimoire.core import InstanceRef, compose_instance, effective_entity
    from grimoire.core.entity import EntityInstance
    card = EntityDefinition(NAMESAKE, "Mara", "individual", FacetSet([Agent()]), "   \n")
    index = elderfen_index(card)
    view = effective(NAMESAKE, index)
    assert view.description == "   \n" and view.origin("description") is None  # blank: present, not authored
    actor = EntityInstance(InstanceRef("c1", "character", "m"), NAMESAKE)
    assert compose_instance(actor, view).description == effective_entity(actor, index).description == "   \n"

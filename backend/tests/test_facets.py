"""Typed composable facets: capabilities are values with invariants, not subclasses."""

import pytest

from grimoire.core.facets import (
    BULKS, CHANNELS, KNOWN_FACETS, RESERVED_FACET_NAMES, SIZES, Agent, Biological, Communicative,
    Durable, FacetBundle, FacetError, FacetSet, Physical, Portable, Spatial, UnknownFacet,
)
from grimoire.core.json_value import JsonObject


def reason(build):
    with pytest.raises(FacetError) as caught:
        build()
    assert isinstance(caught.value, ValueError)
    return caught.value.reason


def test_vocabulary_is_the_specified_first_slice():
    assert set(KNOWN_FACETS) == {
        "physical", "portable", "durable", "biological", "agent", "communicative", "spatial"}
    assert RESERVED_FACET_NAMES == {"control", "controller", "pc", "npc", "player", "director"}
    assert SIZES == {"tiny", "small", "medium", "large", "huge"}
    assert BULKS == {"light", "normal", "heavy"}
    assert CHANNELS == {"speech", "telepathy", "sign", "writing"}
    assert {name: cls.requires for name, cls in KNOWN_FACETS.items()} == {
        "physical": frozenset(), "portable": {"physical"}, "durable": {"physical"},
        "biological": {"physical"}, "agent": frozenset(), "communicative": {"agent"},
        "spatial": frozenset(),
    }


def test_a_facet_set_composes_capabilities_without_subclasses():
    sword = FacetSet([Durable(12), Communicative(speaks={"speech"}, understands={"speech"}),
                      Agent(), Portable("light"), Physical(material="steel", size="small")])
    assert sword.names == ("agent", "communicative", "durable", "physical", "portable")
    assert sword.has(Agent) and sword.get(Durable).max_integrity == 12
    assert sword.get(Biological) is None and not sword.has(Spatial)
    assert sword.by_name("portable") == Portable("light") and sword.by_name("spatial") is None
    assert "agent" in sword and Agent() in sword and "spatial" not in sword
    assert [type(facet) for facet in sword] == [Agent, Communicative, Durable, Physical, Portable]


def test_requirements_are_checked_in_sets_but_not_in_bundles():
    assert reason(lambda: FacetSet([Portable()])) == "missing_requirement"
    assert reason(lambda: FacetSet([Communicative(speaks={"speech"})])) == "missing_requirement"
    # An unknown facet never satisfies a known requirement, whatever it is called.
    assert reason(lambda: FacetSet([Portable(), UnknownFacet("physical-ish")])) == "missing_requirement"
    assert len(FacetBundle([Portable()])) == 1
    assert FacetSet([Portable(), Physical()]).has(Portable)


def test_order_never_matters_for_equality_or_hash():
    a, b = FacetBundle([Agent(), Spatial()]), FacetBundle([Spatial(), Agent()])
    assert a == b and hash(a) == hash(b)
    assert FacetSet([Physical(), Portable()]) == FacetSet([Portable(), Physical()])
    assert FacetBundle() == FacetBundle([]) and not FacetBundle()


@pytest.mark.parametrize("build, expected", [
    (lambda: FacetBundle([Agent(), Agent()]), "duplicate_facet"),
    (lambda: FacetBundle([UnknownFacet("x-a"), UnknownFacet("x-a", {"v": 1})]), "duplicate_facet"),
    (lambda: UnknownFacet("pc"), "reserved_name"),
    (lambda: UnknownFacet("controller"), "reserved_name"),
    (lambda: UnknownFacet("physical"), "known_name_as_unknown"),
    (lambda: UnknownFacet("Bad Name"), "invalid_field"),
    (lambda: UnknownFacet("x-glow", {"facet": "agent"}), "invalid_field"),
    (lambda: UnknownFacet("x-glow", [1, 2]), "invalid_field"),
    (lambda: UnknownFacet("x-glow", {"v": float("nan")}), "invalid_field"),
    (lambda: Durable(0), "invalid_field"),
    (lambda: Durable(True), "invalid_field"),
    (lambda: Durable(1.0), "invalid_field"),
    (lambda: Physical(size="gigantic"), "invalid_field"),
    (lambda: Physical(material="  "), "invalid_field"),
    (lambda: Physical(material=3), "invalid_field"),
    (lambda: Portable(bulk="weightless"), "invalid_field"),
    (lambda: Communicative(), "invalid_field"),
    (lambda: Communicative(speaks={"smoke"}), "invalid_field"),
    (lambda: Communicative(speaks="speech"), "invalid_field"),
    (lambda: FacetBundle(["agent"]), "invalid_field"),
])
def test_invalid_facets_are_refused_with_a_reason(build, expected):
    assert reason(build) == expected


def test_reserved_names_are_refused_even_for_known_style_bundles():
    for name in RESERVED_FACET_NAMES:
        assert reason(lambda: UnknownFacet(name)) == "reserved_name"


def test_unknown_facets_are_preserved_frozen_and_grant_nothing():
    glow = UnknownFacet("x-glow", {"lumens": 3, "colors": ["blue"]})
    assert glow.data == JsonObject({"lumens": 3, "colors": ("blue",)})
    assert hash(glow) == hash(UnknownFacet("x-glow", {"colors": ["blue"], "lumens": 3}))
    bundle = FacetBundle([glow, Agent()])
    assert bundle.by_name("x-glow") is glow and not bundle.has(Physical)


def test_communicative_channels_are_frozensets():
    voice = Communicative(speaks=["speech", "speech"], understands=("speech", "writing"))
    assert voice.speaks == frozenset({"speech"}) and voice.understands == {"speech", "writing"}
    assert Communicative(understands={"telepathy"}).speaks == frozenset()

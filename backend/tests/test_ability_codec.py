"""Payload codec for the ``abilities`` facet (025): canonical, strict on known fields, keeps unknowns."""

import pytest
from ability_fixtures import actor, light_bending, person, speak_with_dead, thruster_pack
from envelope_fixture import NeutralEnvelope

from grimoire.core.abilities import Abilities, Ability, Effect, Limit
from grimoire.core.entity import EntityInstance
from grimoire.core.entity_codec import (
    ENTITY_SCHEMA, CodecError, definition_from_payload, definition_to_payload, instance_from_payload,
    instance_to_payload,
)
from grimoire.core.facet_errors import FacetError
from grimoire.core.facets import FacetBundle
from grimoire.core.json_value import freeze_object


def three():
    return person("vanta", "Vanta", light_bending(), speak_with_dead(), thruster_pack())


def abilities_entry(payload):
    return next(entry for entry in payload["traits"] if entry["facet"] == "abilities")


def decode_traits(*entries):
    payload = {"name": "X", "nature": "individual",
               "traits": [{"facet": "agent"}, *entries]}
    return definition_from_payload(three().ref, ENTITY_SCHEMA, payload)


def test_three_abilities_round_trip_canonically_through_a_neutral_envelope():
    definition = three()
    payload = definition_to_payload(definition)
    envelope = NeutralEnvelope.wrap(definition.ref, "user", ENTITY_SCHEMA, "rev-1", payload)
    decoded = definition_from_payload(envelope.address, envelope.schema, envelope.payload)
    assert decoded == definition
    assert definition_to_payload(decoded) == payload
    entry = abilities_entry(payload)
    bending = entry["abilities"][0]
    assert list(bending) == ["id", "name", "effects", "limits", "costs", "prerequisites", "description"]
    veil = next(e for e in bending["effects"] if e["id"] == "veil")
    # Defaults are omitted; sets are sorted lists.
    assert veil == {"id": "veil", "text": veil["text"], "tags": ["concealment", "light"],
                    "range": "her body and an arm's length"}
    assert next(e for e in bending["effects"] if e["id"] == "dazzle")["certainty"] == "uncertain"
    assert bending["costs"][1] == {"id": "migraine", "text": bending["costs"][1]["text"],
                                   "applies_to": ["mirage"]}


def test_an_instance_with_added_and_replaced_abilities_round_trips():
    definition = person("iris", "Iris", light_bending())
    instance = EntityInstance(actor("iris"), definition.ref,
                              replaced=FacetBundle([Abilities([light_bending(force_limit=False)])]))
    original = EntityInstance(actor("orin"), None, "Orin", added=FacetBundle([Abilities([thruster_pack()])]))
    for value in (instance, original):
        payload = instance_to_payload(value)
        assert instance_from_payload(value.ref, ENTITY_SCHEMA, payload) == value
        assert instance_to_payload(instance_from_payload(value.ref, ENTITY_SCHEMA, payload)) == payload


def test_explicit_defaults_decode_equal_and_reencode_without_them():
    verbose = {"facet": "abilities", "abilities": [{
        "id": "a", "name": "A", "description": "", "limits": [], "costs": [], "prerequisites": [],
        "effects": [{"id": "e", "text": "t", "tags": ["x"], "range": None, "certainty": "reliable"}]}]}
    decoded = decode_traits(verbose)
    assert definition_to_payload(decoded)["traits"][0] == {
        "facet": "abilities", "abilities": [{"id": "a", "name": "A",
                                             "effects": [{"id": "e", "text": "t", "tags": ["x"]}]}]}


def test_an_empty_facet_round_trips():
    definition = person("blank", "Blank", facet_extras={})
    payload = definition_to_payload(definition)
    assert abilities_entry(payload) == {"facet": "abilities"}
    assert definition_from_payload(definition.ref, ENTITY_SCHEMA, payload) == definition


def test_unknown_keys_are_kept_as_extras_at_every_level():
    entry = {"facet": "abilities", "x-facet-note": 1, "abilities": [{
        "id": "a", "name": "A", "x-era": "modern", "extras": {"kept": True},
        "effects": [{"id": "e", "text": "t", "tags": ["x"], "x-glow": "blue"}],
        "limits": [{"id": "l", "text": "t", "excludes": ["y"], "when": "dawn"}],
        "costs": [{"id": "c", "text": "t", "x-unit": "breath"}],
        "prerequisites": [{"id": "p", "text": "t", "x-ritual": ["salt"]}]}]}
    decoded = decode_traits(entry)
    facet = decoded.traits.get(Abilities)
    ability = facet.abilities[0]
    assert facet.extras["x-facet-note"] == 1
    assert ability.extras["x-era"] == "modern" and ability.extras["extras"] == {"kept": True}
    assert ability.effects[0].extras["x-glow"] == "blue"
    assert ability.limits[0].extras["when"] == "dawn"
    assert ability.costs[0].extras["x-unit"] == "breath"
    assert ability.prerequisites[0].extras["x-ritual"] == ("salt",)
    assert definition_to_payload(decoded)["traits"][0] == entry


def test_a_deeply_frozen_payload_decodes():
    # A stored payload is read-only: mappings and tuples at every depth.
    definition = three()
    frozen = freeze_object(definition_to_payload(definition))
    assert definition_from_payload(definition.ref, ENTITY_SCHEMA, frozen) == definition


@pytest.mark.parametrize("entry", [
    {"facet": "abilities", "abilities": {"id": "a"}},
    {"facet": "abilities", "abilities": ["a"]},
    {"facet": "abilities", "abilities": [{"id": "a", "name": "A"}]},
    {"facet": "abilities", "abilities": [{"id": "a", "name": "A", "effects": {"e": 1}}]},
    {"facet": "abilities", "abilities": [{"id": "a", "name": "A", "effects": [{"id": "e", "text": "t"}]}]},
    {"facet": "abilities", "abilities": [{"id": "a", "name": "A",
                                          "effects": [{"id": "e", "text": "t", "tags": ["x", "x"]}]}]},
    {"facet": "abilities", "abilities": [{"id": "a", "name": "A",
                                          "effects": [{"id": "e", "text": "t", "tags": "x"}]}]},
    {"facet": "abilities", "abilities": [{"id": "a", "name": "A",
                                          "effects": [{"id": "e", "text": "t", "tags": [1]}]}]},
    {"facet": "abilities", "abilities": [{"id": "a", "name": "A", "effects": [
        {"id": "e", "text": "t", "tags": ["x"]}], "costs": [{"id": "c", "text": "t", "applies_to": ["e", "e"]}]}]},
])
def test_malformed_entries_are_refused_with_invalid_field(entry):
    with pytest.raises(CodecError) as caught:
        decode_traits(entry)
    assert caught.value.reason == "invalid_field"


@pytest.mark.parametrize("entry, expected", [
    ({"facet": "abilities", "abilities": [{"id": "a", "name": "A", "effects": [
        {"id": "e", "text": "t", "tags": ["x"]}], "limits": [{"id": "l", "text": "t", "excludes": ["x"]}]}]},
     "contradictory_ability"),
    ({"facet": "abilities", "abilities": [{"id": "a", "name": "A", "effects": []}]}, "empty_ability"),
    ({"facet": "abilities", "abilities": [{"id": "a", "name": "A", "effects": [
        {"id": "e", "text": "t", "tags": ["x"], "certainty": "maybe"}]}]}, "invalid_field"),
])
def test_domain_refusals_propagate_with_their_reason(entry, expected):
    with pytest.raises(FacetError) as caught:
        decode_traits(entry)
    assert caught.value.reason == expected


def test_other_facets_still_refuse_unknown_fields():
    with pytest.raises(CodecError) as caught:
        decode_traits({"facet": "physical", "colour": "red"})
    assert caught.value.reason == "unknown_facet_field"


def test_values_built_directly_and_decoded_agree():
    built = Abilities([Ability("a", "A", [Effect("e", "t", {"x"})], [Limit("l", "t", {"y"})])])
    entry = {"facet": "abilities", "abilities": [{"id": "a", "name": "A",
                                                  "effects": [{"id": "e", "text": "t", "tags": ["x"]}],
                                                  "limits": [{"id": "l", "text": "t", "excludes": ["y"]}]}]}
    assert decode_traits(entry).traits.get(Abilities) == built

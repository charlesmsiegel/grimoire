"""Provisional entity payload codec: lossless for unknowns, strict for known types."""

import ast
import copy
from pathlib import Path

import pytest
from entity_fixtures import VOICE, ref, vessa
from envelope_fixture import NeutralEnvelope

import grimoire.core
from grimoire.core import InstanceRef
from grimoire.core.entity import EntityDefinition, EntityInstance, Integrity
from grimoire.core.entity_codec import (
    ENTITY_SCHEMA, CodecError, definition_from_payload, definition_to_payload,
    instance_from_payload, instance_to_payload,
)
from grimoire.core.facets import Agent, FacetBundle, FacetSet, Physical, Portable, UnknownFacet

HERE = Path(__file__).resolve().parent
SWORD = InstanceRef("c1", "item", "vessa")


def strict_equal(a, b):
    """Equal values of identical types at every level (1, 1.0 and True differ)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(strict_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(strict_equal(x, y) for x, y in zip(a, b))
    return a == b


def glowing_vessa():
    plain = vessa()
    return EntityDefinition(plain.ref, plain.name, plain.nature,
                            FacetSet([*plain.traits, UnknownFacet("x-glow", {"lumens": 3, "hue": [0.5, 1]})]),
                            plain.description, extras={"x-portrait": {"crop": [0, 0, 1, 1], "ok": True}})


def awakened_instance():
    return EntityInstance(SWORD, vessa().ref.unpinned(), "Vessa the Wakeful",
                          added=FacetBundle([UnknownFacet("x-bond", {"to": "mara"})]),
                          replaced=FacetBundle([Physical(material="bronze")]),
                          state=(Integrity(7),), extras={"x-notes": [{"seen": 2}]})


def test_codec_round_trip_preserves_unknowns():
    definition, instance = glowing_vessa(), awakened_instance()
    for value, address, encode, decode in (
        (definition, definition.ref, definition_to_payload, definition_from_payload),
        (instance, SWORD, instance_to_payload, instance_from_payload),
    ):
        payload = encode(value)
        envelope = NeutralEnvelope.wrap(address, "user", ENTITY_SCHEMA, "rev-1", payload)
        assert strict_equal(envelope.payload, payload)
        assert decode(envelope.address, envelope.schema, envelope.payload) == value
        assert strict_equal(encode(decode(address, ENTITY_SCHEMA, envelope.payload)), payload)


def test_payload_shape_is_canonical():
    assert definition_to_payload(vessa()) == {
        "name": "Vessa", "nature": "individual", "description": "A sentient sword",
        "traits": [
            {"facet": "agent"},
            {"facet": "communicative", "speaks": ["speech"], "understands": ["speech"]},
            {"facet": "durable", "max_integrity": 12},
            {"facet": "physical", "material": "steel", "size": "small"},
            {"facet": "portable", "bulk": "light"},
        ],
    }
    assert instance_to_payload(EntityInstance(SWORD, vessa().ref)) == {"source": "world:emberfall/item/vessa@r1"}
    assert instance_to_payload(awakened_instance())["state"] == [{"state": "integrity", "current": 7}]
    assert "ENTITY_SCHEMA" not in grimoire.core.__all__ and "definition_to_payload" not in grimoire.core.__all__


def valid_definition():
    return definition_to_payload(vessa())


def valid_instance():
    return instance_to_payload(awakened_instance())


def mutated(base, change):
    payload = copy.deepcopy(base())
    change(payload)
    return payload


def facet(payload, name):
    return next(entry for entry in payload["traits"] if entry["facet"] == name)


DEFINITION_DEFECTS = [
    ("durable max as text", lambda p: facet(p, "durable").update(max_integrity="ten"), "invalid_field"),
    ("durable max as bool", lambda p: facet(p, "durable").update(max_integrity=True), "invalid_field"),
    ("unknown field in known facet", lambda p: facet(p, "physical").update(weight=3), "unknown_facet_field"),
    ("duplicate facet entry", lambda p: p["traits"].append({"facet": "agent"}), "duplicate_facet"),
    ("duplicate channel", lambda p: facet(p, "communicative").update(speaks=["speech", "speech"]),
     "invalid_field"),
    ("channels not a list", lambda p: facet(p, "communicative").update(speaks="speech"), "invalid_field"),
    ("channel not text", lambda p: facet(p, "communicative").update(speaks=[{"a": 1}]), "invalid_field"),
    ("bulk not text", lambda p: facet(p, "portable").update(bulk=["light"]), "invalid_field"),
    ("facet entry without name", lambda p: p["traits"].append({"lumens": 3}), "invalid_field"),
    ("facet entry not an object", lambda p: p["traits"].append("agent"), "invalid_field"),
    ("traits not a list", lambda p: p.update(traits={"facet": "agent"}), "invalid_field"),
    ("reserved facet", lambda p: p["traits"].append({"facet": "pc"}), "reserved_name"),
    ("missing name", lambda p: p.pop("name"), "invalid_field"),
    ("bad nature", lambda p: p.update(nature="species"), "invalid_field"),
    ("non-finite extra", lambda p: p.update({"x-v": float("nan")}), "invalid_field"),
    ("non-text key", lambda p: p.update({1: "x"}), "invalid_field"),
    *[(f"address key {key}", lambda p, key=key: p.update({key: "x"}), "address_in_payload")
      for key in ("ref", "address", "source_id", "campaign_id", "schema")],
]


@pytest.mark.parametrize("label, change, expected", DEFINITION_DEFECTS, ids=[d[0] for d in DEFINITION_DEFECTS])
def test_definition_payload_defects_are_refused_with_a_reason(label, change, expected):
    with pytest.raises(ValueError) as caught:
        definition_from_payload(vessa().ref, ENTITY_SCHEMA, mutated(valid_definition, change))
    assert caught.value.reason == expected


INSTANCE_DEFECTS = [
    ("unknown state type", lambda p: p["state"].append({"state": "mood", "value": "grim"}), "unknown_state"),
    ("unknown field in known state", lambda p: p["state"][0].update(max=9), "unknown_state_field"),
    ("state entry without type", lambda p: p["state"].append({"current": 1}), "invalid_field"),
    ("negative integrity", lambda p: p["state"][0].update(current=-1), "invalid_field"),
    ("bad source text", lambda p: p.update(source="Emberfall/Vessa"), "invalid_field"),
    ("source not text", lambda p: p.update(source=3), "invalid_field"),
    ("added not a list", lambda p: p.update(added={}), "invalid_field"),
    *[(f"address key {key}", lambda p, key=key: p.update({key: "x"}), "address_in_payload")
      for key in ("ref", "address", "source_id", "campaign_id", "schema")],
]


@pytest.mark.parametrize("label, change, expected", INSTANCE_DEFECTS, ids=[d[0] for d in INSTANCE_DEFECTS])
def test_instance_payload_defects_are_refused_with_a_reason(label, change, expected):
    with pytest.raises(ValueError) as caught:
        instance_from_payload(SWORD, ENTITY_SCHEMA, mutated(valid_instance, change))
    assert caught.value.reason == expected


def test_schema_and_address_are_checked():
    for decode, address, payload in ((definition_from_payload, vessa().ref, valid_definition()),
                                     (instance_from_payload, SWORD, valid_instance())):
        with pytest.raises(CodecError) as caught:
            decode(address, "grimoire.entity/9", payload)
        assert caught.value.reason == "unsupported_schema"
        with pytest.raises(CodecError) as caught:
            decode(str(address), ENTITY_SCHEMA, payload)
        assert caught.value.reason == "invalid_field"
        with pytest.raises(CodecError):
            decode(address, ENTITY_SCHEMA, [payload])


def test_decoding_never_mutates_the_payload():
    payload = valid_instance()
    before = copy.deepcopy(payload)
    instance_from_payload(SWORD, ENTITY_SCHEMA, payload)
    assert strict_equal(payload, before)


def test_unknown_facet_names_are_kept_but_reserved_names_are_not():
    payload = mutated(valid_definition, lambda p: p["traits"].append({"facet": "x-aura", "radius": 2}))
    decoded = definition_from_payload(vessa().ref, ENTITY_SCHEMA, payload)
    assert decoded.traits.by_name("x-aura") == UnknownFacet("x-aura", {"radius": 2})


def test_instances_without_facets_or_state_round_trip():
    bare = EntityInstance(InstanceRef("c1", "item", "pebble"), None, "Pebble",
                          added=FacetBundle([Physical(), Portable()]))
    assert instance_from_payload(bare.ref, ENTITY_SCHEMA, instance_to_payload(bare)) == bare
    minimal = EntityDefinition(ref("item", "x"), "X", "template", FacetSet([Agent(), VOICE]))
    assert definition_from_payload(minimal.ref, ENTITY_SCHEMA, definition_to_payload(minimal)) == minimal


def test_the_envelope_fixture_depends_on_no_grimoire_module():
    tree = ast.parse((HERE / "envelope_fixture.py").read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any(name.startswith("grimoire") for name in imported)

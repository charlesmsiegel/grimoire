"""Relation payloads to and from plain JSON (Sprint 017): strict inside, extensible at the top."""

from __future__ import annotations

import json
from types import MappingProxyType

import pytest

from grimoire.core.identity import InstanceRef, RelationRef
from grimoire.core.json_value import JsonObject
from grimoire.core.knowledge import Moment
from grimoire.graph import Ending, Relation, Retraction, UnsupportedRelation
from grimoire.graph.codec import RELATION_SCHEMA, RelationCodecError, relation_from_payload, relation_to_payload

C = "c"
REF = RelationRef(C, "r-1")
WREN = InstanceRef(C, "character", "wren")
GUILD = InstanceRef(C, "organization", "ashen-guild")


def full() -> Relation:
    return Relation(REF, "member_of", WREN, GUILD, Moment(3, "s1", "t2"), "director", "2026-11-03T10:00:00Z",
                    Ending(Moment(12), "director: left", "2026-11-03T11:00:00Z"),
                    Retraction("director: error", "2026-11-03T12:00:00Z"), JsonObject({"x-note": ["kept", 1]}))


def minimal_payload(**changes) -> dict:
    payload = {"predicate": "member_of", "subject": str(WREN), "object": str(GUILD), "since": {"sequence": 3},
               "provenance": "director", "recorded_at": "2026-11-03T10:00:00Z"}
    payload.update(changes)
    return payload


def decode(payload, schema=RELATION_SCHEMA):
    return relation_from_payload(REF, schema, payload)


def reason_of(payload, schema=RELATION_SCHEMA) -> str:
    with pytest.raises(RelationCodecError) as caught:
        decode(payload, schema)
    return caught.value.reason


def test_schema_id():
    assert RELATION_SCHEMA == "grimoire.relation/0"


def test_round_trip_canonical():
    relation = full()
    payload = relation_to_payload(relation)
    assert json.loads(json.dumps(payload)) == payload  # plain JSON
    assert decode(payload) == relation
    assert decode(MappingProxyType(payload)) == relation  # the store hands payloads over frozen
    assert payload["since"] == {"sequence": 3, "scene_id": "s1", "turn_id": "t2"}
    simple = decode(minimal_payload())
    assert relation_to_payload(simple) == minimal_payload()  # nulls and defaults are omitted
    assert simple.ending is None and simple.retraction is None and simple.extras == {}


def test_unknown_top_level_keys_preserved_in_extras():
    relation = decode(minimal_payload(**{"x-weight": 0.5, "notes": {"a": [True]}}))
    assert relation.extras == {"x-weight": 0.5, "notes": {"a": [True]}}
    assert relation_to_payload(relation) == minimal_payload(**{"x-weight": 0.5, "notes": {"a": [True]}})


@pytest.mark.parametrize("changes", [
    {"since": {"sequence": 3, "extra": 1}},
    {"ending": {"until": {"sequence": 9}, "provenance": "d", "at": "t", "why": "x"}},
    {"ending": {"until": {"sequence": 9, "hour": 1}, "provenance": "d", "at": "t"}},
    {"retraction": {"provenance": "d", "at": "t", "who": "x"}},
])
def test_unknown_nested_key_is_invalid_field(changes):
    assert reason_of(minimal_payload(**changes)) == "invalid_field"


@pytest.mark.parametrize("key", ["ref", "address", "campaign_id", "relation_id", "schema"])
def test_address_keys_refused(key):
    assert reason_of(minimal_payload(**{key: "anything"})) == "address_in_payload"


def test_address_keys_are_checked_before_the_schema_and_predicate():
    assert reason_of(minimal_payload(predicate="loves", ref="x"), "other/1") == "address_in_payload"


def test_wrong_schema_unsupported():
    assert reason_of(minimal_payload(), "grimoire.relation/1") == "unsupported_schema"


def test_endpoint_other_campaign_is_campaign_mismatch():
    assert reason_of(minimal_payload(object="campaign:other/organization/ashen-guild")) == "campaign_mismatch"


def test_self_relation_and_invalid_until_are_codec_errors():
    assert reason_of(minimal_payload(object=str(WREN))) == "self_relation"
    ending = {"until": {"sequence": 3}, "provenance": "d", "at": "t"}
    assert reason_of(minimal_payload(ending=ending)) == "invalid_until"


@pytest.mark.parametrize("sequence", ["3", True, -1, 3.0, None])
def test_mistyped_sequence_is_invalid_field(sequence):
    assert reason_of(minimal_payload(since={"sequence": sequence})) == "invalid_field"


@pytest.mark.parametrize("changes", [
    {"subject": "wren"}, {"subject": 5}, {"provenance": ""}, {"recorded_at": None}, {"predicate": 7},
    {"since": [3]}, {"since": {"sequence": 3, "scene_id": ""}}, {"ending": "12"}, {"retraction": {"at": "t"}},
])
def test_malformed_fields_are_invalid_field(changes):
    assert reason_of(minimal_payload(**changes)) == "invalid_field"


@pytest.mark.parametrize("missing", ["predicate", "subject", "object", "since", "provenance", "recorded_at"])
def test_missing_fields_are_invalid_field(missing):
    payload = minimal_payload()
    del payload[missing]
    assert reason_of(payload) == "invalid_field"


@pytest.mark.parametrize("payload", [None, [], "x", {1: "a"}])
def test_payload_not_object_is_invalid_field(payload):
    assert reason_of(payload) == "invalid_field"


def test_unknown_predicate_decodes_unsupported():
    payload = minimal_payload(predicate="mentors", object="campaign:other/character/x", since="whenever",
                              **{"x-new": {"k": 1}})
    found = decode(payload)
    assert isinstance(found, UnsupportedRelation)
    assert found.ref == REF and found.predicate == "mentors"
    assert found.endpoints == (WREN,)  # the other-campaign endpoint is dropped; nothing else is validated
    assert found.payload == payload  # preserved verbatim
    garbled = decode(minimal_payload(predicate="mentors", subject="nope", object=str(GUILD)))
    assert garbled.endpoints == (GUILD,)


def test_extras_cannot_smuggle_an_address_key():
    relation = Relation(REF, "member_of", WREN, GUILD, Moment(1), "d", "t", extras=JsonObject({"schema": "x"}))
    with pytest.raises(RelationCodecError) as caught:
        relation_to_payload(relation)
    assert caught.value.reason == "address_in_payload"

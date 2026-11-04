"""Payload codecs of Sprint 021: canonical round trips, strict decoding and structural re-validation."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from grimoire.core import Moment
from grimoire.core.identity import AcceptedRef, WrapUpRef
from grimoire.core.json_value import JsonObject
from grimoire.effects import (
    ACCEPTED_SCHEMA, WRAPUP_SCHEMA, EffectCodecError, EndorsementRecord, Revocation, SceneWrapUp, Skipped,
    accepted_from_payload, accepted_to_payload, wrapup_from_payload, wrapup_id, wrapup_to_payload,
)

from effects_fixtures import ANA, C, CHAT
from test_effects_model import event

REF = AcceptedRef(C, "e-1")
WREF = WrapUpRef(C, wrapup_id(CHAT, "s1"))


def wrapup() -> SceneWrapUp:
    return SceneWrapUp(WREF, CHAT, "s1", Moment(8, "s1", "t8"), "Wren gave the key away.", ("e-1",), "director", "t")


def decode_reason(payload, schema=ACCEPTED_SCHEMA, decoder=accepted_from_payload, ref=REF) -> str:
    with pytest.raises(EffectCodecError) as caught:
        decoder(ref, schema, payload)
    return caught.value.reason


def test_accepted_round_trip_is_canonical():
    item = event(extras=JsonObject({"x-note": {"kept": True}}))
    payload = accepted_to_payload(item)
    assert payload["x-note"] == {"kept": True}
    assert accepted_from_payload(REF, ACCEPTED_SCHEMA, payload) == item
    assert accepted_to_payload(accepted_from_payload(REF, ACCEPTED_SCHEMA, payload)) == payload
    assert "revocation" not in payload


def test_revoked_round_trip():
    item = event()
    revoked = replace(item, revocation=Revocation("edited", "t2", "human:owner",
                                                  (EndorsementRecord("c7", ANA, item.fingerprint),),
                                                  (Skipped(1, "receipt changed"),)))
    assert accepted_from_payload(REF, ACCEPTED_SCHEMA, accepted_to_payload(revoked)) == revoked


def test_stored_payload_with_tuples_decodes():
    payload = accepted_to_payload(event())
    frozen = JsonObject(payload)  # how a stored payload arrives: lists become tuples
    assert accepted_from_payload(REF, ACCEPTED_SCHEMA, frozen) == event()


@pytest.mark.parametrize("mutate, expected", [
    (lambda p: p.update(event_id="e-1"), "identity_in_payload"),
    (lambda p: p.update(schema="x"), "identity_in_payload"),
    (lambda p: p.pop("receipts"), "missing_field"),
    (lambda p: p["operations"][0].update(note="x"), "unknown_field"),
    (lambda p: p["moment"].update(scene_id="S1"), "invalid_moment"),
    (lambda p: p.update(ordinal="1"), "invalid_field"),
    (lambda p: p.update(ordinal=True), "invalid_field"),
    (lambda p: p["operations"][0].update(domain="director"), "invalid_domain"),
    (lambda p: p["receipts"].pop(), "invalid_receipts"),
    (lambda p: p["endorsements"][0].update(fingerprint="sha256:" + "1" * 64), "invalid_endorsement"),
    (lambda p: p["operations"][1].update(object="campaign:c/item/key-2"), "invalid_endorsement"),  # fingerprint moved
    (lambda p: p["controls"][0].update(controller_id="human:bob"), "invalid_expression"),
    (lambda p: p.update(revocation={"reason": "r", "at": "t", "committed_by": "p", "endorsements": [],
                                    "skipped": []}), "invalid_endorsement"),
    (lambda p: p.update(chat="campaign:c#chat/T"), "invalid_field"),
])
def test_accepted_decoding_is_strict(mutate, expected):
    payload = copy.deepcopy(accepted_to_payload(event()))
    mutate(payload)
    assert decode_reason(payload) == expected


def test_unknown_schema_and_identity():
    payload = accepted_to_payload(event())
    assert decode_reason(payload, schema="grimoire.accepted-event/1") == "unsupported_schema"
    with pytest.raises(EffectCodecError) as caught:
        accepted_to_payload(event(extras=JsonObject({"receipts": 1})))
    assert caught.value.reason == "invalid_field"
    with pytest.raises(EffectCodecError) as caught:
        accepted_to_payload(event(extras=JsonObject({"address": 1})))
    assert caught.value.reason == "identity_in_payload"


def test_wrapup_round_trip_and_strictness():
    item = wrapup()
    payload = wrapup_to_payload(item)
    assert wrapup_from_payload(WREF, WRAPUP_SCHEMA, payload) == item
    for key in ("operations", "receipts", "x-note"):  # nothing outside the schema, so nothing can act
        assert decode_reason({**payload, key: []}, WRAPUP_SCHEMA, wrapup_from_payload, WREF) == "unknown_field"
    assert decode_reason({**payload, "wrapup_id": "w"}, WRAPUP_SCHEMA, wrapup_from_payload, WREF) == \
        "identity_in_payload"
    assert decode_reason({**payload, "scene_id": "s2"}, WRAPUP_SCHEMA, wrapup_from_payload, WREF) == "wrapup_mismatch"
    assert decode_reason(payload, "grimoire.scene-wrapup/9", wrapup_from_payload, WREF) == "unsupported_schema"
    missing = dict(payload)
    del missing["cites"]
    assert decode_reason(missing, WRAPUP_SCHEMA, wrapup_from_payload, WREF) == "missing_field"

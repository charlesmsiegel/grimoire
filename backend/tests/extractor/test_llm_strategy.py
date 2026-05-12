"""Structured-LLM strategy tests."""

from __future__ import annotations

import pytest

from grimoire.extractor.config import ExtractorConfig
from grimoire.extractor.llm_strategy import (
    _extract_json_payload,
    extract_with_llm,
    parse_llm_payload,
)
from grimoire.types.scene import Scene
from grimoire.types.state import DeltaKind, StateSnapshot

from .conftest import FakeGateway


def test_extract_json_payload_handles_fenced_block():
    text = 'Here you go:\n```json\n{"facts": []}\n```'
    payload = _extract_json_payload(text)
    assert payload == {"facts": []}


def test_extract_json_payload_handles_bare_object():
    text = '{"facts": [{"text": "Hi", "confidence": 0.9}]}'
    payload = _extract_json_payload(text)
    assert payload is not None and payload["facts"][0]["text"] == "Hi"


def test_extract_json_payload_returns_none_for_garbage():
    assert _extract_json_payload("totally not json") is None


def test_parse_payload_emits_typed_deltas():
    payload = {
        "facts": [
            {"text": "winifred wrote to Sion", "confidence": 0.9, "evidence": "..."},
        ],
        "time_advances": [{"delta": "PT2H", "confidence": 1.0, "evidence": "two hours"}],
        "commitments": [
            {
                "kind": "promise",
                "text": "winifred will teach julian",
                "from": "winifred",
                "to": "julian",
                "confidence": 0.85,
            }
        ],
        "new_characters": [{"proposed_name": "Margaux", "role": "maid", "confidence": 0.8}],
        "inventory_changes": [
            {
                "character_id": "julian",
                "item": "silver ring",
                "delta": "+1",
                "confidence": 0.95,
            }
        ],
        "mechanical_events": [
            {"kind": "wound", "character_id": "julian", "amount": "light", "confidence": 0.7}
        ],
        "relationship_changes": [
            {"from": "julian", "to": "winifred", "field": "trust", "delta": "+1", "confidence": 0.6}
        ],
        "commitment_resolutions": [
            {"commitment_id": "c_4521", "outcome": "paid", "confidence": 0.9}
        ],
        "scene_changes": [
            {"kind": "location_change", "to_location": "the orchard", "confidence": 0.95}
        ],
        "character_updates": [
            {
                "character_id": "vivienne",
                "field": "emotional_state",
                "before": "calm",
                "after": "guarded",
                "confidence": 0.7,
            }
        ],
    }
    out = parse_llm_payload(payload, campaign_id="c1", source="extractor", max_new_entities=5)
    kinds = sorted({d.kind.value for d in out.deltas})
    expected = {
        DeltaKind.FACT_ADD.value,
        DeltaKind.TIME_ADVANCE.value,
        DeltaKind.COMMITMENT_ADD.value,
        DeltaKind.INVENTORY_CHANGE.value,
        DeltaKind.MECHANICAL_EVENT.value,
        DeltaKind.RELATIONSHIP_UPDATE.value,
        DeltaKind.COMMITMENT_RESOLVE.value,
        DeltaKind.SCENE_CHANGE.value,
        DeltaKind.CHARACTER_STATE_UPDATE.value,
    }
    assert expected <= set(kinds)
    assert len(out.candidates) == 1
    assert out.candidates[0].proposed_name == "Margaux"
    assert 0.0 < out.confidence_avg <= 1.0


def test_parse_payload_truncates_new_characters_to_budget():
    payload = {
        "new_characters": [{"proposed_name": f"NPC{i}", "confidence": 0.8} for i in range(10)]
    }
    out = parse_llm_payload(payload, campaign_id="c1", source="extractor", max_new_entities=3)
    assert len(out.candidates) == 3


@pytest.mark.asyncio
async def test_extract_with_llm_uses_routes_task_name(scene: Scene, snapshot: StateSnapshot):
    gateway = FakeGateway(queue=[{"facts": [{"text": "x", "confidence": 0.9}]}])
    config = ExtractorConfig(task_name="extractor-test")
    out = await extract_with_llm(
        response_text="some prose",
        scene=scene,
        snapshot=snapshot,
        campaign_id="c1",
        gateway=gateway,
        config=config,
        source="extractor",
    )
    assert gateway.seen[0][0] == "extractor-test"
    assert out.deltas and out.deltas[0].kind == DeltaKind.FACT_ADD


@pytest.mark.asyncio
async def test_extract_with_llm_flags_when_payload_unparseable(
    scene: Scene, snapshot: StateSnapshot
):
    gateway = FakeGateway(queue=["not even close to JSON"])
    out = await extract_with_llm(
        response_text="...",
        scene=scene,
        snapshot=snapshot,
        campaign_id="c1",
        gateway=gateway,
        config=ExtractorConfig(),
        source="extractor",
    )
    assert any(f.code == "llm_json_unparseable" for f in out.flags)
    assert not out.deltas


@pytest.mark.asyncio
async def test_extract_with_llm_flags_when_gateway_raises(scene: Scene, snapshot: StateSnapshot):
    gateway = FakeGateway(queue=[], raise_on_next=RuntimeError("boom"))
    out = await extract_with_llm(
        response_text="...",
        scene=scene,
        snapshot=snapshot,
        campaign_id="c1",
        gateway=gateway,
        config=ExtractorConfig(),
        source="extractor",
    )
    assert any(f.code == "llm_call_failed" for f in out.flags)

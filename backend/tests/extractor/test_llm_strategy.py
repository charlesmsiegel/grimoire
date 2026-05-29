"""Structured-LLM strategy tests."""

from __future__ import annotations

import pytest

from grimoire.extractor.config import ExtractorConfig
from grimoire.extractor.llm_strategy import (
    _extract_json_payload,
    extract_with_llm,
    parse_llm_payload,
)
from grimoire.extractor.schema import output_schema
from grimoire.types.common import EntityKind
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
                "action": "acquire",
                "item": "silver ring",
                "holder": "julian",
                "quantity": 1,
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


def test_parse_payload_emits_candidates_for_all_kinds():
    payload = {
        "new_characters": [
            {"proposed_name": "Margaux", "confidence": 0.8},
        ],
        "new_locations": [
            {"proposed_name": "The Orchard", "confidence": 0.7},
        ],
        "new_factions": [
            {"proposed_name": "Florentine Society", "confidence": 0.75},
        ],
        "new_items": [
            {"proposed_name": "Silver Ring", "confidence": 0.65},
        ],
    }
    out = parse_llm_payload(payload, campaign_id="c1", source="extractor", max_new_entities=10)
    by_name = {c.proposed_name: c for c in out.candidates}
    assert by_name["Margaux"].kind == EntityKind.CHARACTER
    assert by_name["The Orchard"].kind == EntityKind.LOCATION
    assert by_name["Florentine Society"].kind == EntityKind.FACTION
    assert by_name["Silver Ring"].kind == EntityKind.ITEM


def test_parse_payload_caps_combined_candidates_across_kinds():
    payload = {
        "new_characters": [{"proposed_name": f"NPC{i}", "confidence": 0.8} for i in range(3)],
        "new_locations": [{"proposed_name": f"Loc{i}", "confidence": 0.7} for i in range(3)],
        "new_factions": [{"proposed_name": f"Fac{i}", "confidence": 0.7} for i in range(3)],
    }
    out = parse_llm_payload(payload, campaign_id="c1", source="extractor", max_new_entities=5)
    assert len(out.candidates) == 5


def test_schema_advertises_all_new_entity_arrays():
    schema = output_schema()
    for key in ("new_characters", "new_locations", "new_factions", "new_items"):
        assert key in schema["properties"], f"missing schema array: {key}"
        assert schema["properties"][key]["type"] == "array"


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
        config=ExtractorConfig(retry_on_parse_failure=0),
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


@pytest.mark.asyncio
async def test_extract_with_llm_retries_once_on_parse_failure(
    scene: Scene, snapshot: StateSnapshot
):
    # First response is garbage; second response is valid JSON. With the
    # default retry budget of 1, the second call should succeed and yield deltas.
    gateway = FakeGateway(
        queue=[
            "totally not json",
            {"facts": [{"text": "after retry", "confidence": 0.9}]},
        ]
    )
    out = await extract_with_llm(
        response_text="...",
        scene=scene,
        snapshot=snapshot,
        campaign_id="c1",
        gateway=gateway,
        config=ExtractorConfig(),
        source="extractor",
    )
    assert len(gateway.seen) == 2
    assert out.deltas and out.deltas[0].kind == DeltaKind.FACT_ADD
    assert not any(f.code == "llm_json_unparseable" for f in out.flags)


@pytest.mark.asyncio
async def test_extract_with_llm_no_retry_when_budget_zero(scene: Scene, snapshot: StateSnapshot):
    gateway = FakeGateway(queue=["totally not json"])
    out = await extract_with_llm(
        response_text="...",
        scene=scene,
        snapshot=snapshot,
        campaign_id="c1",
        gateway=gateway,
        config=ExtractorConfig(retry_on_parse_failure=0),
        source="extractor",
    )
    assert len(gateway.seen) == 1
    assert any(f.code == "llm_json_unparseable" for f in out.flags)
    assert not out.deltas


@pytest.mark.asyncio
async def test_extract_with_llm_gives_up_after_exhausting_retries(
    scene: Scene, snapshot: StateSnapshot
):
    # Both attempts produce garbage; the strategy must surface the
    # unparseable flag rather than looping forever.
    gateway = FakeGateway(queue=["garbage one", "garbage two"])
    out = await extract_with_llm(
        response_text="...",
        scene=scene,
        snapshot=snapshot,
        campaign_id="c1",
        gateway=gateway,
        config=ExtractorConfig(retry_on_parse_failure=1),
        source="extractor",
    )
    assert len(gateway.seen) == 2
    assert any(f.code == "llm_json_unparseable" for f in out.flags)
    assert not out.deltas


@pytest.mark.asyncio
async def test_extract_with_llm_retry_appends_repair_message(scene: Scene, snapshot: StateSnapshot):
    gateway = FakeGateway(
        queue=[
            "totally not json",
            {"facts": []},
        ]
    )
    await extract_with_llm(
        response_text="...",
        scene=scene,
        snapshot=snapshot,
        campaign_id="c1",
        gateway=gateway,
        config=ExtractorConfig(),
        source="extractor",
    )
    # The retry request should include an additional user message asking
    # the model to return valid JSON.
    retry_request = gateway.seen[1][1]
    assert len(retry_request.messages) > len(gateway.seen[0][1].messages)
    assert any("json" in m.content.lower() for m in retry_request.messages[1:])

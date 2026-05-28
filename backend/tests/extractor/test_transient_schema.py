"""LLM extractor parses transient_updates from the structured payload."""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.extractor.llm_strategy import parse_llm_payload
from grimoire.types.transient import EntityKind


def test_parse_llm_payload_extracts_transient_updates():
    payload = {
        "transient_updates": [
            {
                "entity_kind": "character",
                "entity_id": "char_x",
                "field": "mood",
                "value": "guarded",
                "confidence": 0.9,
                "evidence": "She tensed at the question.",
            },
            {
                "entity_kind": "location",
                "entity_id": "loc_y",
                "field": "ambient_mood",
                "value": "tense",
                "confidence": 0.7,
                "evidence": "Lanterns sputtered.",
            },
        ],
    }
    out = parse_llm_payload(payload, campaign_id="c1", source="structured_llm", max_new_entities=10)
    assert len(out.transient_updates) == 2
    first = out.transient_updates[0]
    assert first.entity_kind == EntityKind.CHARACTER
    assert first.entity_id == "char_x"
    assert first.field == "mood"
    assert first.value == "guarded"
    assert first.confidence == 0.9


def test_parse_llm_payload_skips_invalid_transient_updates():
    payload = {
        "transient_updates": [
            {
                "entity_kind": "character",
                "field": "mood",
                "value": "ok",
                "confidence": 0.5,
            },  # missing entity_id
            {
                "entity_kind": "unknown_kind",
                "entity_id": "x",
                "field": "mood",
                "value": "ok",
                "confidence": 0.5,
            },
        ],
    }
    out = parse_llm_payload(payload, campaign_id="c1", source="structured_llm", max_new_entities=10)
    assert out.transient_updates == []


@dataclass
class _StubCompletion:
    text: str


class _StubGateway:
    def __init__(self, payload_text: str) -> None:
        self._text = payload_text

    async def complete(self, task, request, campaign_id=None, *, turn_id=None):
        return _StubCompletion(self._text)


async def test_extractor_service_surfaces_transient_updates_from_llm():
    from grimoire.extractor.config import ExtractorConfig
    from grimoire.extractor.service import ExtractorService
    from grimoire.types.scene import Scene
    from grimoire.types.state import StateSnapshot

    payload = (
        '{"transient_updates": [{"entity_kind": "character", '
        '"entity_id": "char_x", "field": "mood", "value": "guarded", '
        '"confidence": 0.9}]}'
    )
    config = ExtractorConfig(parallel_strategies=("structured_llm",))
    extractor = ExtractorService(gateway=_StubGateway(payload), config=config)
    scene = Scene(
        id="s1",
        campaign_id="c1",
        ordinal=1,
        slug="s1",
        file_path="scenes/s1.md",
        title="t",
    )
    snapshot = StateSnapshot(campaign_id="c1", scene_id="s1")
    result = await extractor.extract(
        response_text="...",
        scene=scene,
        campaign_id="c1",
        prior_state_snapshot=snapshot,
        pre_roll_resolved=False,
        turn_id="t1",
    )
    assert len(result.transient_updates) == 1
    assert result.transient_updates[0].field == "mood"

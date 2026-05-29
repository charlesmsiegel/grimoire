"""Tests for cast-change detection across the extractor (#464)."""

from __future__ import annotations

from grimoire.extractor.schema import empty_payload, output_schema
from grimoire.types.extraction import ExtractionResult
from grimoire.types.scene import CastChange, CastChangeProposal, PendingCastChange


def test_cast_change_proposal_defaults():
    p = CastChangeProposal(character_ref="reyes", change=CastChange.ENTER)
    assert p.change == "enter"
    assert p.confidence == 0.0
    assert p.evidence == ""


def test_extraction_result_carries_cast_changes():
    r = ExtractionResult(
        cast_changes=[CastChangeProposal(character_ref="x", change=CastChange.LEAVE)]
    )
    assert r.cast_changes[0].change == "leave"


def test_pending_cast_change_roundtrip():
    rec = PendingCastChange(
        id="cc-1",
        campaign_id="c",
        scene_id="s",
        character_ref="library:worlds/w/characters/reyes",
        change=CastChange.ENTER,
        is_pc=False,
        evidence="strides in",
        confidence=0.8,
        turn_id="t1",
        status="pending",
        created_at="2026-05-28T00:00:00+00:00",
    )
    assert rec.model_dump()["change"] == "enter"


def test_schema_includes_cast_changes():
    props = output_schema()["properties"]
    assert "cast_changes" in props
    item = props["cast_changes"]["items"]
    assert item["properties"]["change"]["enum"] == ["enter", "leave"]
    assert set(item["required"]) == {"character_id", "change", "confidence"}


def test_empty_payload_includes_cast_changes():
    assert empty_payload()["cast_changes"] == []


def test_parse_llm_payload_extracts_cast_changes():
    from grimoire.extractor.llm_strategy import parse_llm_payload

    payload = {
        "cast_changes": [
            {
                "character_id": "reyes",
                "change": "enter",
                "evidence": "strides in",
                "confidence": 0.9,
            },
            {"character_id": "bad", "change": "teleport", "confidence": 0.5},  # invalid -> dropped
            {"character_id": "", "change": "enter", "confidence": 0.5},  # empty ref -> dropped
        ]
    }
    out = parse_llm_payload(
        payload, campaign_id="c", source="structured_llm", max_new_entities=5
    )
    assert len(out.cast_changes) == 1
    assert out.cast_changes[0].character_ref == "reyes"
    assert out.cast_changes[0].change == "enter"

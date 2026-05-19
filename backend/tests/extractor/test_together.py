"""Tests for the Together-mode tracker parser + projector."""

from __future__ import annotations

import json

import pytest

from grimoire.extractor.together import (
    DELIMITER_CLOSE,
    DELIMITER_OPEN,
    TrackerMalformedError,
    extract_tracker_block,
    parse_tracker_text,
    project_tracker_to_candidates,
    project_tracker_to_deltas,
)
from grimoire.types.common import EntityKind
from grimoire.types.state import DeltaKind


def test_parses_valid_json():
    raw = json.dumps(
        {
            "facts": [{"text": "winifred has a scar"}],
            "character_updates": [],
        }
    )
    parsed = parse_tracker_text(raw)
    assert parsed.facts[0]["text"] == "winifred has a scar"
    assert parsed.character_updates == []


def test_raises_on_invalid_json():
    with pytest.raises(TrackerMalformedError):
        parse_tracker_text("{this is not JSON")


def test_raises_on_missing_required_key():
    raw = json.dumps({"facts": []})
    with pytest.raises(TrackerMalformedError, match="character_updates"):
        parse_tracker_text(raw)


def test_raises_on_empty_text():
    with pytest.raises(TrackerMalformedError):
        parse_tracker_text("")


def test_raises_on_non_object_top_level():
    with pytest.raises(TrackerMalformedError):
        parse_tracker_text("[1, 2, 3]")


def test_unknown_keys_are_ignored_for_forward_compat():
    raw = json.dumps(
        {
            "facts": [],
            "character_updates": [],
            "future_section_we_dont_know": [{"x": 1}],
        }
    )
    parsed = parse_tracker_text(raw)
    assert parsed.facts == []
    assert parsed.character_updates == []


def test_extract_tracker_block_returns_inner_text():
    text = f'prose here {DELIMITER_OPEN}\n{{"facts":[]}}\n{DELIMITER_CLOSE} more prose'
    assert extract_tracker_block(text) == '{"facts":[]}'


def test_extract_tracker_block_returns_none_without_markers():
    assert extract_tracker_block("just prose, no tracker") is None
    assert extract_tracker_block(f"{DELIMITER_OPEN} unterminated") is None


def test_projects_fact_to_fact_add_delta():
    raw = json.dumps(
        {
            "facts": [
                {
                    "text": "winifred has a scar",
                    "confidence": 0.85,
                    "about": {"character_ids": ["winifred"]},
                }
            ],
            "character_updates": [],
        }
    )
    parsed = parse_tracker_text(raw)
    deltas = project_tracker_to_deltas(parsed, campaign_id="camp-1")
    assert len(deltas) == 1
    d = deltas[0]
    assert d.kind == DeltaKind.FACT_ADD
    assert d.after["text"] == "winifred has a scar"
    assert d.confidence == 0.85


def test_projects_character_update():
    raw = json.dumps(
        {
            "facts": [],
            "character_updates": [
                {
                    "character_id": "winifred",
                    "field": "mood",
                    "after": "sad",
                    "confidence": 0.9,
                }
            ],
        }
    )
    parsed = parse_tracker_text(raw)
    deltas = project_tracker_to_deltas(parsed, campaign_id="camp-1")
    assert any(
        d.kind == DeltaKind.CHARACTER_STATE_UPDATE
        and d.target_id == "winifred"
        and d.after["after"] == "sad"
        for d in deltas
    )


def test_skips_character_update_without_required_fields():
    raw = json.dumps(
        {
            "facts": [],
            "character_updates": [{"field": "mood", "after": "sad"}],  # no character_id
        }
    )
    parsed = parse_tracker_text(raw)
    deltas = project_tracker_to_deltas(parsed, campaign_id="camp-1")
    assert all(d.kind != DeltaKind.CHARACTER_STATE_UPDATE for d in deltas)


def test_advance_time_projects_to_time_advance_delta():
    raw = json.dumps(
        {
            "facts": [],
            "character_updates": [],
            "advance_time": {"delta": "PT2H", "confidence": 0.95},
        }
    )
    parsed = parse_tracker_text(raw)
    deltas = project_tracker_to_deltas(parsed, campaign_id="camp-1")
    times = [d for d in deltas if d.kind == DeltaKind.TIME_ADVANCE]
    assert len(times) == 1
    assert times[0].after["delta"] == "PT2H"


def test_change_location_projects_to_scene_change():
    raw = json.dumps(
        {
            "facts": [],
            "character_updates": [],
            "change_location": {"to_location": "campaign:locations/the-docks"},
        }
    )
    parsed = parse_tracker_text(raw)
    deltas = project_tracker_to_deltas(parsed, campaign_id="camp-1", scene_branch_id="main")
    scene_changes = [d for d in deltas if d.kind == DeltaKind.SCENE_CHANGE]
    assert len(scene_changes) == 1
    assert scene_changes[0].after["to_location"] == "campaign:locations/the-docks"


def test_default_confidence_when_missing():
    raw = json.dumps({"facts": [{"text": "Hello"}], "character_updates": []})
    parsed = parse_tracker_text(raw)
    deltas = project_tracker_to_deltas(parsed, campaign_id="camp-1")
    assert deltas[0].confidence == pytest.approx(0.9)


def test_projects_new_entity_to_candidate():
    raw = json.dumps(
        {
            "facts": [],
            "character_updates": [],
            "new_entities": [
                {
                    "kind": "character",
                    "proposed_id": "campaign:characters/margaux",
                    "proposed_name": "Margaux",
                    "role_hint": "bartender",
                    "confidence": 0.8,
                }
            ],
        }
    )
    parsed = parse_tracker_text(raw)
    candidates = project_tracker_to_candidates(parsed)
    assert len(candidates) == 1
    assert candidates[0].kind == EntityKind.CHARACTER
    assert candidates[0].proposed_name == "Margaux"

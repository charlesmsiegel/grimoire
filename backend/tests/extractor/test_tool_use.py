"""Tests for tool-use mode declarations + projector."""

from __future__ import annotations

from grimoire.extractor.tool_use import (
    ALL_TOOLS,
    RECORD_FACT_TOOL,
    ToolCall,
    project_tool_calls,
)
from grimoire.types.state import DeltaKind


def test_all_tools_cover_expected_delta_kinds():
    names = {tool.name for tool in ALL_TOOLS}
    assert {
        "record_fact",
        "update_character_state",
        "advance_time",
        "change_location",
        "propose_new_entity",
        "create_commitment",
        "update_commitment",
        "close_thread",
    } <= names


def test_record_fact_schema_requires_text():
    schema = RECORD_FACT_TOOL.schema
    assert "text" in schema["properties"]
    assert "text" in schema["required"]


def test_record_fact_call_projects_to_fact_add_delta():
    deltas, candidates = project_tool_calls(
        [ToolCall(name="record_fact", args={"text": "winifred has scar"})],
        campaign_id="camp-1",
    )
    assert candidates == []
    assert len(deltas) == 1
    assert deltas[0].kind == DeltaKind.FACT_ADD
    assert deltas[0].after["text"] == "winifred has scar"


def test_update_character_state_call_projects_to_character_state_update():
    deltas, _ = project_tool_calls(
        [
            ToolCall(
                name="update_character_state",
                args={"character_id": "winifred", "field": "mood", "after": "sad"},
            )
        ],
        campaign_id="camp-1",
    )
    assert len(deltas) == 1
    assert deltas[0].kind == DeltaKind.CHARACTER_STATE_UPDATE
    assert deltas[0].target_id == "winifred"
    assert deltas[0].after["after"] == "sad"


def test_advance_time_call_projects_to_time_advance():
    deltas, _ = project_tool_calls(
        [ToolCall(name="advance_time", args={"delta": "PT30M"})],
        campaign_id="camp-1",
    )
    assert deltas[0].kind == DeltaKind.TIME_ADVANCE
    assert deltas[0].after["delta"] == "PT30M"


def test_propose_new_entity_projects_to_candidate_not_delta():
    deltas, candidates = project_tool_calls(
        [
            ToolCall(
                name="propose_new_entity",
                args={
                    "kind": "character",
                    "proposed_id": "campaign:characters/margaux",
                    "proposed_name": "Margaux",
                    "role_hint": "bartender",
                },
            )
        ],
        campaign_id="camp-1",
    )
    assert deltas == []
    assert len(candidates) == 1
    assert candidates[0].proposed_name == "Margaux"


def test_close_thread_projects_to_commitment_resolve():
    deltas, _ = project_tool_calls(
        [ToolCall(name="close_thread", args={"commitment_id": "cmt-42", "outcome": "kept"})],
        campaign_id="camp-1",
    )
    assert deltas[0].kind == DeltaKind.COMMITMENT_RESOLVE
    assert deltas[0].target_id == "cmt-42"


def test_unknown_tool_call_silently_skipped():
    deltas, candidates = project_tool_calls(
        [ToolCall(name="totally_unknown_tool", args={})],
        campaign_id="camp-1",
    )
    assert deltas == []
    assert candidates == []


def test_malformed_call_dropped_without_raising():
    # update_character_state without character_id is a no-op.
    deltas, _ = project_tool_calls(
        [ToolCall(name="update_character_state", args={"field": "mood", "after": "sad"})],
        campaign_id="camp-1",
    )
    assert deltas == []


def test_mixed_calls_partial_success():
    deltas, candidates = project_tool_calls(
        [
            ToolCall(name="record_fact", args={"text": "A fact"}),
            ToolCall(name="bogus", args={}),
            ToolCall(name="advance_time", args={"delta": "PT1H"}),
        ],
        campaign_id="camp-1",
    )
    kinds = {d.kind for d in deltas}
    assert DeltaKind.FACT_ADD in kinds
    assert DeltaKind.TIME_ADVANCE in kinds
    assert candidates == []

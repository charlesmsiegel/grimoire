"""Integration tests for `ExtractorService.extract(..., mode=...)`."""

from __future__ import annotations

import json

import pytest

from grimoire.extractor import ExtractorConfig, ExtractorService
from grimoire.extractor.together import DELIMITER_CLOSE, DELIMITER_OPEN
from grimoire.extractor.tool_use import ToolCall
from grimoire.types.extraction_modes import ExtractionMode
from grimoire.types.scene import Scene
from grimoire.types.state import DeltaKind, StateSnapshot


@pytest.mark.asyncio
async def test_none_mode_returns_empty_result(scene: Scene, snapshot: StateSnapshot):
    service = ExtractorService()
    result = await service.extract(
        "anything at all", scene, "camp-1", snapshot, mode=ExtractionMode.NONE
    )
    assert result.deltas == []
    assert result.candidates == []
    assert result.flags == []


@pytest.mark.asyncio
async def test_separate_mode_runs_default_pipeline(scene: Scene, snapshot: StateSnapshot):
    service = ExtractorService(
        config=ExtractorConfig(parallel_strategies=("rule_based", "heuristic_flags"))
    )
    result = await service.extract(
        "Two hours passed. Margaux brought the tea.",
        scene,
        "camp-1",
        snapshot,
        mode=ExtractionMode.SEPARATE,
    )
    assert set(result.extraction_strategies_run) == {"rule_based", "heuristic_flags"}
    times = [d for d in result.deltas if d.kind == DeltaKind.TIME_ADVANCE]
    assert len(times) == 1


@pytest.mark.asyncio
async def test_together_mode_with_explicit_tracker_text(
    scene: Scene, snapshot: StateSnapshot
):
    tracker = json.dumps(
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
    service = ExtractorService(
        config=ExtractorConfig(parallel_strategies=("rule_based",))
    )
    result = await service.extract(
        "winifred sighs by the window.",
        scene,
        "camp-1",
        snapshot,
        mode=ExtractionMode.TOGETHER,
        together_tracker_text=tracker,
    )
    assert any(
        d.kind == DeltaKind.CHARACTER_STATE_UPDATE and d.target_id == "winifred"
        for d in result.deltas
    )
    assert "together" in result.extraction_strategies_run


@pytest.mark.asyncio
async def test_together_mode_extracts_block_from_response(
    scene: Scene, snapshot: StateSnapshot
):
    tracker = json.dumps({"facts": [{"text": "It rained."}], "character_updates": []})
    response = f"It rained heavily.\n{DELIMITER_OPEN}\n{tracker}\n{DELIMITER_CLOSE}"
    service = ExtractorService(
        config=ExtractorConfig(parallel_strategies=("rule_based",))
    )
    result = await service.extract(
        response,
        scene,
        "camp-1",
        snapshot,
        mode=ExtractionMode.TOGETHER,
    )
    assert any(
        d.kind == DeltaKind.FACT_ADD and d.after.get("text") == "It rained."
        for d in result.deltas
    )


@pytest.mark.asyncio
async def test_together_mode_malformed_falls_back_to_separate(
    scene: Scene, snapshot: StateSnapshot
):
    service = ExtractorService(
        config=ExtractorConfig(parallel_strategies=("rule_based",))
    )
    result = await service.extract(
        "Two hours passed.",
        scene,
        "camp-1",
        snapshot,
        mode=ExtractionMode.TOGETHER,
        together_tracker_text="{not valid json",
    )
    # Falls back: rule-based picks up "two hours" as a time advance.
    assert any(d.kind == DeltaKind.TIME_ADVANCE for d in result.deltas)
    assert any(f.code == "together_malformed" for f in result.flags)


@pytest.mark.asyncio
async def test_together_mode_records_failure_on_auto_disable(
    scene: Scene, snapshot: StateSnapshot
):
    class _Recorder:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str, bool]] = []

        async def record_call(self, provider_id, model, mode, *, success):
            self.calls.append((provider_id, model, mode, success))

    recorder = _Recorder()
    service = ExtractorService(
        config=ExtractorConfig(parallel_strategies=("rule_based",)),
        auto_disable=recorder,
        provider_id="anthropic",
        model="opus",
    )
    await service.extract(
        "Prose.",
        scene,
        "camp-1",
        snapshot,
        mode=ExtractionMode.TOGETHER,
        together_tracker_text="not json",
    )
    assert ("anthropic", "opus", "together", False) in recorder.calls


@pytest.mark.asyncio
async def test_tool_use_mode_projects_calls(scene: Scene, snapshot: StateSnapshot):
    service = ExtractorService(
        config=ExtractorConfig(parallel_strategies=("rule_based",))
    )
    result = await service.extract(
        "winifred walks out.",
        scene,
        "camp-1",
        snapshot,
        mode=ExtractionMode.TOOL_USE,
        tool_calls=[
            ToolCall(
                name="update_character_state",
                args={"character_id": "winifred", "field": "location", "after": "outside"},
            )
        ],
    )
    assert any(
        d.kind == DeltaKind.CHARACTER_STATE_UPDATE and d.target_id == "winifred"
        for d in result.deltas
    )


@pytest.mark.asyncio
async def test_tool_use_mode_no_calls_falls_back_to_separate(
    scene: Scene, snapshot: StateSnapshot
):
    service = ExtractorService(
        config=ExtractorConfig(parallel_strategies=("rule_based",))
    )
    result = await service.extract(
        "Two hours passed.",
        scene,
        "camp-1",
        snapshot,
        mode=ExtractionMode.TOOL_USE,
        tool_calls=[],
    )
    assert any(d.kind == DeltaKind.TIME_ADVANCE for d in result.deltas)
    assert any(f.code == "tool_use_no_calls" for f in result.flags)

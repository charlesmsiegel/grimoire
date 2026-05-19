"""Tests for the Context Builder's `extractor_mode` parameter."""

from __future__ import annotations

from typing import Any

from grimoire.context import ContextBuilderService
from grimoire.types.extraction_modes import ExtractionMode

from .test_builder import (
    StubCharacters,
    StubContinuity,
    StubLibrary,
    StubScenes,
    StubWorld,
)


def _builder(**overrides: Any) -> ContextBuilderService:
    defaults: dict[str, Any] = {
        "library": StubLibrary(),
        "characters": StubCharacters(),
        "world": StubWorld(),
        "scenes": StubScenes(),
        "continuity": StubContinuity(),
        "state_store": None,
        "gateway": None,
    }
    defaults.update(overrides)
    return ContextBuilderService(**defaults)


async def test_separate_default_attaches_no_tools_and_no_tracker():
    builder = _builder()
    prompt = await builder.build("hello", "camp")
    assert prompt.tools == []
    body = "\n".join(m.content for m in prompt.messages)
    assert "<!-- TRACKER -->" not in body


async def test_together_appends_tracker_instructions():
    builder = _builder()
    prompt = await builder.build("hello", "camp", extractor_mode=ExtractionMode.TOGETHER)
    body = "\n".join(m.content for m in prompt.messages)
    assert "<!-- TRACKER -->" in body
    assert "<!-- /TRACKER -->" in body
    assert prompt.tools == []


async def test_tool_use_attaches_tool_declarations():
    builder = _builder()
    prompt = await builder.build("hello", "camp", extractor_mode=ExtractionMode.TOOL_USE)
    tool_names = {t.name for t in prompt.tools}
    assert "record_fact" in tool_names
    assert "update_character_state" in tool_names
    # Tool-use mode does NOT also inject tracker instructions.
    body = "\n".join(m.content for m in prompt.messages)
    assert "<!-- TRACKER -->" not in body


async def test_none_omits_tracker_and_tools():
    builder = _builder()
    prompt = await builder.build("hello", "camp", extractor_mode=ExtractionMode.NONE)
    assert prompt.tools == []
    body = "\n".join(m.content for m in prompt.messages)
    assert "<!-- TRACKER -->" not in body


async def test_auxiliary_task_overrides_mode_and_suppresses_everything():
    # Even though mode is TOOL_USE, an aux task forces no tools / no tracker.
    builder = _builder()
    prompt = await builder.build(
        "hello",
        "camp",
        extractor_mode=ExtractionMode.TOOL_USE,
        auxiliary_task=object(),
    )
    assert prompt.tools == []
    body = "\n".join(m.content for m in prompt.messages)
    assert "<!-- TRACKER -->" not in body


async def test_tools_have_parameters_schema():
    builder = _builder()
    prompt = await builder.build("hello", "camp", extractor_mode=ExtractionMode.TOOL_USE)
    record_fact = next(t for t in prompt.tools if t.name == "record_fact")
    assert "text" in record_fact.parameters["properties"]

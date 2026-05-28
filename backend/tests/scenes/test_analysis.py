"""Tests for the bundled scene analysis module."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from grimoire.extractor.llm_strategy import parse_llm_payload
from grimoire.extractor.schema import output_schema
from grimoire.scenes.analysis import (
    _extract_json,
    _parse_analysis_response,
    _parse_threads,
    analysis_schema,
    make_adaptive_scene_analyzer,
    make_scene_analyzer,
)
from grimoire.scenes.types import AuthorKind, Post, Scene


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeGateway:
    def __init__(self, response_text: str) -> None:
        self._text = response_text
        self.calls: list[tuple[str, Any]] = []
        self.raise_on_call = False

    async def complete(self, task, request, campaign_id=None, *, turn_id=None):
        self.calls.append((task, request))
        if self.raise_on_call:
            raise RuntimeError("provider unavailable")
        return _FakeResponse(self._text)


class _FakeAdaptiveGateway(_FakeGateway):
    def __init__(self, response_text: str, context_window: int = 200_000) -> None:
        super().__init__(response_text)
        self._context_window = context_window

    def resolve_route(self, task, campaign_id=None):
        class _Route:
            provider_id = "test"
            model = "test-model"

        return _Route()

    async def get_model_info(self, provider_id, model):
        class _Info:
            context_window = self._context_window

        _Info.context_window = self._context_window
        return _Info()


def _post(order: int, body: str) -> Post:
    return Post(
        id=f"p{order}",
        scene_id="s1",
        order_in_scene=order,
        author_kind=AuthorKind.NARRATOR,
        body=body,
        is_player=False,
        created_at=datetime(2024, 10, 31, 22, order, 0),
        turn_id=f"t{order}",
    )


def _scene() -> Scene:
    return Scene(
        id="s1",
        campaign_id="camp1",
        branch_id="main",
        ordinal=1,
        slug="scene",
        title="The Dark Tower",
        running_summary="Things have happened.",
    )


# -- Schema ---------------------------------------------------------------


def test_analysis_schema_extends_extraction():
    schema = analysis_schema(output_schema)
    props = schema["properties"]
    assert "summary" in props
    assert "key_beats" in props
    assert "threads" in props
    assert "facts" in props
    assert "commitments" in props
    assert "new_characters" in props


# -- JSON extraction -------------------------------------------------------


def test_extract_json_plain():
    raw = '{"summary": "hello"}'
    assert _extract_json(raw) == {"summary": "hello"}


def test_extract_json_fenced():
    raw = '```json\n{"summary": "hello"}\n```'
    assert _extract_json(raw) == {"summary": "hello"}


def test_extract_json_garbage():
    assert _extract_json("not json at all") is None


# -- Thread parsing --------------------------------------------------------


def test_parse_threads_mixed():
    raw = [
        {"text": "The prophecy", "status": "introduced", "at_post": 3},
        {"text": "Debt repaid", "status": "paid_off", "at_post": 7},
        {"text": "", "status": "introduced"},
    ]
    introduced, paid_off = _parse_threads(raw)
    assert len(introduced) == 1
    assert introduced[0].text == "The prophecy"
    assert introduced[0].introduced_at_post == 3
    assert len(paid_off) == 1
    assert paid_off[0].text == "Debt repaid"
    assert paid_off[0].paid_off_at_post == 7


def test_parse_threads_empty():
    introduced, paid_off = _parse_threads([])
    assert introduced == []
    assert paid_off == []


# -- Response parsing ------------------------------------------------------


def test_parse_analysis_response_full():
    payload = {
        "summary": "The party explored the ruins.",
        "key_beats": ["Found the artifact", "Met the guardian"],
        "threads": [{"text": "Ancient curse", "status": "introduced"}],
        "facts": [
            {
                "text": "The artifact is cursed",
                "confidence": 0.9,
                "about": {"item_ids": ["artifact-1"]},
            }
        ],
        "commitments": [{"kind": "promise", "text": "Return the artifact", "confidence": 0.85}],
        "new_characters": [
            {"proposed_name": "Guardian Kael", "confidence": 0.7, "role": "major_npc"}
        ],
    }
    result = _parse_analysis_response(
        payload, campaign_id="camp1", payload_parser=parse_llm_payload
    )
    assert result.summary == "The party explored the ruins."
    assert len(result.key_beats) == 2
    assert len(result.threads_introduced) == 1
    assert result.threads_introduced[0].text == "Ancient curse"
    assert len(result.extraction.deltas) == 2  # 1 fact + 1 commitment
    assert len(result.extraction.candidates) == 1
    assert result.extraction.candidates[0].proposed_name == "Guardian Kael"


def test_parse_analysis_response_empty():
    result = _parse_analysis_response({}, campaign_id="camp1", payload_parser=parse_llm_payload)
    assert result.summary == ""
    assert result.key_beats == []
    assert result.extraction.deltas == []


def test_parse_analysis_caps_key_beats():
    payload = {
        "summary": "s",
        "key_beats": ["a", "b", "c", "d", "e", "f", "g"],
    }
    result = _parse_analysis_response(
        payload, campaign_id="c", payload_parser=parse_llm_payload, max_key_beats=3
    )
    assert len(result.key_beats) == 3


# -- Single-pass analyzer -------------------------------------------------


async def test_single_pass_analyzer_parses_response():
    payload = {
        "summary": "Adventure ensues.",
        "key_beats": ["Found the key"],
        "threads": [],
        "facts": [{"text": "Door is locked", "confidence": 0.8}],
    }
    gateway = _FakeGateway(json.dumps(payload))
    analyze = make_scene_analyzer(
        gateway, extraction_schema_fn=output_schema, payload_parser=parse_llm_payload
    )
    result = await analyze(_scene(), [_post(1, "They found the door.")], "camp1")
    assert result.summary == "Adventure ensues."
    assert len(result.key_beats) == 1
    assert len(result.extraction.deltas) == 1
    assert gateway.calls[0][0] == "scene_analysis"


async def test_single_pass_analyzer_empty_posts():
    gateway = _FakeGateway("{}")
    analyze = make_scene_analyzer(
        gateway, extraction_schema_fn=output_schema, payload_parser=parse_llm_payload
    )
    result = await analyze(_scene(), [], "camp1")
    assert result.summary == "Things have happened."
    assert gateway.calls == []


async def test_single_pass_analyzer_llm_failure():
    gateway = _FakeGateway("")
    gateway.raise_on_call = True
    analyze = make_scene_analyzer(
        gateway, extraction_schema_fn=output_schema, payload_parser=parse_llm_payload
    )
    result = await analyze(_scene(), [_post(1, "x")], "camp1")
    assert result.summary == ""
    assert result.extraction.deltas == []


async def test_single_pass_analyzer_bad_json():
    gateway = _FakeGateway("not json")
    analyze = make_scene_analyzer(
        gateway, extraction_schema_fn=output_schema, payload_parser=parse_llm_payload
    )
    result = await analyze(_scene(), [_post(1, "x")], "camp1")
    assert result.summary == ""


# -- Adaptive analyzer -----------------------------------------------------


async def test_adaptive_analyzer_single_pass_for_short_scene():
    payload = {
        "summary": "Short scene summary.",
        "key_beats": ["One thing happened"],
        "threads": [{"text": "Mystery", "status": "introduced"}],
        "facts": [],
    }
    gateway = _FakeAdaptiveGateway(json.dumps(payload), context_window=200_000)
    analyze = make_adaptive_scene_analyzer(
        gateway, extraction_schema_fn=output_schema, payload_parser=parse_llm_payload
    )
    result = await analyze(_scene(), [_post(1, "Short post.")], "camp1")
    assert result.summary == "Short scene summary."
    assert len(result.threads_introduced) == 1
    assert len(gateway.calls) == 1


async def test_adaptive_analyzer_windows_for_long_scene():
    window_response = json.dumps(
        {
            "summary": "Window summary.",
            "facts": [{"text": "Found something", "confidence": 0.8}],
        }
    )
    final_response = json.dumps(
        {
            "summary": "Final consolidated summary.",
            "key_beats": ["A", "B"],
            "threads": [{"text": "Big thread", "status": "introduced"}],
        }
    )

    call_count = 0

    class _SequentialGateway(_FakeAdaptiveGateway):
        async def complete(self, task, request, campaign_id=None, *, turn_id=None):
            nonlocal call_count
            self.calls.append((task, request))
            call_count += 1
            if call_count <= 2:
                return _FakeResponse(window_response)
            return _FakeResponse(final_response)

    # context_window=10 forces windowing — each post exceeds the window budget
    gateway = _SequentialGateway("", context_window=10)
    analyze = make_adaptive_scene_analyzer(
        gateway, extraction_schema_fn=output_schema, payload_parser=parse_llm_payload
    )
    posts = [_post(i, f"Post body {i} with enough text.") for i in range(1, 4)]
    result = await analyze(_scene(), posts, "camp1")
    assert result.summary == "Final consolidated summary."
    assert len(result.key_beats) == 2
    assert len(result.threads_introduced) == 1
    # 3 window calls + 1 final consolidation = 4 calls minimum
    assert len(gateway.calls) >= 3


async def test_adaptive_analyzer_empty_posts():
    gateway = _FakeAdaptiveGateway("{}")
    analyze = make_adaptive_scene_analyzer(
        gateway, extraction_schema_fn=output_schema, payload_parser=parse_llm_payload
    )
    result = await analyze(_scene(), [], "camp1")
    assert result.summary == "Things have happened."
    assert gateway.calls == []


# -- SceneManager.analyze_scene() -----------------------------------------


@pytest.mark.integration
async def test_manager_analyze_scene_writes_summary(tmp_path: Path):
    from grimoire.scenes import (
        InMemoryEventBus,
        SceneInit,
        SceneManager,
        SceneManagerConfig,
        new_post,
    )

    payload = {
        "summary": "Bundled analysis summary.",
        "key_beats": ["Beat one"],
        "threads": [{"text": "Plot thread", "status": "introduced"}],
        "facts": [{"text": "A fact", "confidence": 0.9}],
    }

    async def fake_analyzer(scene, posts, campaign_id):
        return _parse_analysis_response(
            payload, campaign_id=campaign_id, payload_parser=parse_llm_payload
        )

    bus = InMemoryEventBus()
    config = SceneManagerConfig(running_summary_every_n_posts=0)
    manager = SceneManager(tmp_path, config=config, event_bus=bus, scene_analyzer=fake_analyzer)

    scene = await manager.start_scene(
        SceneInit(campaign_id="c1", title="Test Scene", present_pc_refs=["alice"])
    )
    post = new_post(
        author_kind=AuthorKind.NARRATOR,
        body="The story begins.",
        turn_id="t1",
        is_player=False,
    )
    await manager.append_post(scene.id, post)

    result = await manager.analyze_scene(scene.id)

    assert result.summary == "Bundled analysis summary."
    assert result.key_beats == ["Beat one"]
    assert len(result.threads_introduced) == 1
    assert len(result.extraction.deltas) == 1

    refreshed = await manager.get_scene(scene.id)
    assert refreshed.running_summary == "Bundled analysis summary."
    assert refreshed.key_beats == ["Beat one"]
    assert len(refreshed.threads_introduced) == 1


@pytest.mark.integration
async def test_manager_analyze_scene_skips_when_summary_exists(tmp_path: Path):
    from grimoire.scenes import (
        InMemoryEventBus,
        SceneInit,
        SceneManager,
        SceneManagerConfig,
        new_post,
    )

    call_count = 0

    async def counting_analyzer(scene, posts, campaign_id):
        nonlocal call_count
        call_count += 1
        return _parse_analysis_response(
            {"summary": "Fresh analysis."},
            campaign_id=campaign_id,
            payload_parser=parse_llm_payload,
        )

    bus = InMemoryEventBus()
    config = SceneManagerConfig(running_summary_every_n_posts=0)
    manager = SceneManager(tmp_path, config=config, event_bus=bus, scene_analyzer=counting_analyzer)

    scene = await manager.start_scene(
        SceneInit(campaign_id="c1", title="Test", present_pc_refs=["alice"])
    )
    post = new_post(
        author_kind=AuthorKind.NARRATOR,
        body="text",
        turn_id="t1",
        is_player=False,
    )
    await manager.append_post(scene.id, post)

    result1 = await manager.analyze_scene(scene.id, force=True)
    assert call_count == 1
    assert result1.summary == "Fresh analysis."

    result2 = await manager.analyze_scene(scene.id, force=False)
    assert call_count == 1  # no new LLM call
    assert result2.summary == "Fresh analysis."

    await manager.analyze_scene(scene.id, force=True)
    assert call_count == 2  # forced re-analysis


@pytest.mark.integration
async def test_manager_analyze_scene_without_analyzer_raises(tmp_path: Path):
    from grimoire.scenes import (
        SceneInit,
        SceneManager,
        SceneManagerConfig,
        new_post,
    )

    manager = SceneManager(tmp_path, config=SceneManagerConfig(running_summary_every_n_posts=0))
    scene = await manager.start_scene(
        SceneInit(campaign_id="c1", title="Test", present_pc_refs=["alice"])
    )
    post = new_post(
        author_kind=AuthorKind.NARRATOR,
        body="text",
        turn_id="t1",
        is_player=False,
    )
    await manager.append_post(scene.id, post)

    with pytest.raises(RuntimeError, match="scene analyzer not configured"):
        await manager.analyze_scene(scene.id)

"""§5 — default LLM-driven summarizers for ``SceneManager``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from grimoire.scenes.default_summarizers import (
    make_default_final_summarizer,
    make_default_running_summarizer,
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
        campaign_id="c",
        branch_id="main",
        ordinal=1,
        slug="scene",
        title="Scene",
        running_summary="Things have happened.",
    )


async def test_running_summarizer_returns_model_text() -> None:
    gateway = _FakeGateway("Updated scene summary.")
    summarize = make_default_running_summarizer(gateway)
    result = await summarize("prior", [_post(1, "Opening line.")])
    assert result == "Updated scene summary."
    assert gateway.calls and gateway.calls[0][0] == "scene_summary"


async def test_running_summarizer_falls_back_on_error() -> None:
    gateway = _FakeGateway("")
    gateway.raise_on_call = True
    summarize = make_default_running_summarizer(gateway)
    result = await summarize("prior summary", [_post(1, "x")])
    assert result == "prior summary"


async def test_final_summarizer_parses_json_payload() -> None:
    gateway = _FakeGateway(
        '{"summary": "All resolved.", "key_beats": ["Met the Prince", "Made a vow"]}'
    )
    finalize = make_default_final_summarizer(gateway)
    summary, beats = await finalize(_scene(), [_post(1, "x"), _post(2, "y")])
    assert summary == "All resolved."
    assert beats == ["Met the Prince", "Made a vow"]


async def test_final_summarizer_handles_fenced_json() -> None:
    gateway = _FakeGateway('```json\n{"summary": "Wrap.", "key_beats": []}\n```')
    finalize = make_default_final_summarizer(gateway)
    summary, beats = await finalize(_scene(), [_post(1, "x")])
    assert summary == "Wrap."
    assert beats == []


async def test_final_summarizer_falls_back_on_garbage() -> None:
    gateway = _FakeGateway("totally not json")
    finalize = make_default_final_summarizer(gateway)
    summary, beats = await finalize(_scene(), [_post(1, "first"), _post(2, "last")])
    assert summary == "totally not json"
    assert beats == []


async def test_final_summarizer_falls_back_on_exception() -> None:
    gateway = _FakeGateway("ignored")
    gateway.raise_on_call = True
    finalize = make_default_final_summarizer(gateway)
    summary, beats = await finalize(_scene(), [_post(1, "first"), _post(2, "last")])
    # Scene has a running_summary already; that's the trivial fallback.
    assert summary == "Things have happened."
    assert beats == []

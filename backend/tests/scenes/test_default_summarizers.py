"""§5 — default LLM-driven summarizers for ``SceneManager``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from grimoire.scenes.default_summarizers import (
    make_adaptive_summarizer,
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


# -- Adaptive summarizer tests ------------------------------------------------


class _AdaptiveGateway:
    """Fake gateway that also supports resolve_route and get_model_info."""

    def __init__(self, response_text: str, context_window: int = 200_000) -> None:
        self._text = response_text
        self._context_window = context_window
        self.calls: list[tuple[str, Any]] = []
        self.raise_on_call = False

    async def complete(self, task, request, campaign_id=None, *, turn_id=None):
        self.calls.append((task, request))
        if self.raise_on_call:
            raise RuntimeError("provider unavailable")
        return _FakeResponse(self._text)

    def resolve_route(self, task, campaign_id=None):
        class _Route:
            provider_id = "fake"
            model = "fake-model"

        return _Route()

    async def get_model_info(self, provider_id, model):
        from grimoire.types.llm import ModelInfo

        return ModelInfo(
            id=model,
            name=model,
            context_window=self._context_window,
        )


async def test_adaptive_summarizer_single_pass() -> None:
    gateway = _AdaptiveGateway(
        '{"summary": "All resolved.", "key_beats": ["Beat one"]}',
        context_window=200_000,
    )
    summarize = make_adaptive_summarizer(gateway)
    scene = _scene()
    posts = [_post(1, "short post"), _post(2, "another post")]
    summary, beats = await summarize(scene, posts)
    assert summary == "All resolved."
    assert beats == ["Beat one"]
    assert len(gateway.calls) == 1  # single LLM call


async def test_adaptive_summarizer_windowed() -> None:
    call_count = 0
    responses = [
        "Rolling summary of window 1.",
        '{"summary": "Final summary.", "key_beats": ["Beat A", "Beat B"]}',
    ]

    class _MultiGateway(_AdaptiveGateway):
        async def complete(self, task, request, campaign_id=None, *, turn_id=None):
            nonlocal call_count
            idx = min(call_count, len(responses) - 1)
            call_count += 1
            self.calls.append((task, request))
            return _FakeResponse(responses[idx])

    # context_window=100 tokens -> budget=50 tokens -> 200 chars per window.
    # Each post is 100 chars -> 2 posts per window. 4 posts -> 2 windows.
    gateway = _MultiGateway("ignored", context_window=100)
    summarize = make_adaptive_summarizer(gateway)
    scene = _scene()
    long_body = "x" * 100
    posts = [_post(i, long_body) for i in range(1, 5)]
    summary, beats = await summarize(scene, posts)
    assert summary == "Final summary."
    assert beats == ["Beat A", "Beat B"]
    assert call_count >= 2  # at least one rolling + one final


async def test_adaptive_summarizer_no_posts() -> None:
    gateway = _AdaptiveGateway('{"summary": "ignored", "key_beats": []}')
    summarize = make_adaptive_summarizer(gateway)
    scene = _scene()
    summary, beats = await summarize(scene, [])
    assert summary == "Things have happened."  # falls back to running_summary
    assert beats == []
    assert len(gateway.calls) == 0  # no LLM calls


async def test_adaptive_summarizer_fallback_context_window() -> None:
    gateway = _AdaptiveGateway(
        '{"summary": "Summarized.", "key_beats": []}',
        context_window=0,
    )
    summarize = make_adaptive_summarizer(gateway)
    scene = _scene()
    posts = [_post(1, "hello")]
    summary, _beats = await summarize(scene, posts)
    assert summary == "Summarized."
    assert len(gateway.calls) == 1  # fell back to 100k, small post fits in single pass

"""Unit tests for MockLLMGateway."""

from __future__ import annotations

import pytest

from grimoire.testing import MockLLMGateway, QueueExhaustedError
from grimoire.types.llm import CompletionRequest, Message, MessageRole


def _request(text: str = "hello") -> CompletionRequest:
    return CompletionRequest(
        model="m",
        messages=[Message(role=MessageRole.USER, content=text)],
        max_tokens=8,
        temperature=0.0,
    )


@pytest.mark.asyncio
async def test_complete_pops_queued_string() -> None:
    gw = MockLLMGateway()
    gw.queue_response("primary", "She nods.")
    response = await gw.complete("primary", _request())
    assert response.text == "She nods."
    assert response.finish_reason == "stop"
    assert gw.remaining("primary") == 0


@pytest.mark.asyncio
async def test_complete_serializes_structured_payloads() -> None:
    gw = MockLLMGateway()
    gw.queue_response("extractor", {"facts": [{"text": "Camden"}]})
    response = await gw.complete("extractor", _request())
    assert '"facts"' in response.text
    assert "Camden" in response.text


@pytest.mark.asyncio
async def test_complete_exhausted_queue_fails_loud() -> None:
    gw = MockLLMGateway()
    with pytest.raises(QueueExhaustedError) as exc_info:
        await gw.complete("primary", _request())
    assert exc_info.value.task == "primary"


@pytest.mark.asyncio
async def test_complete_separate_queues_per_task() -> None:
    gw = MockLLMGateway()
    gw.queue_response("primary", "A")
    gw.queue_response("drift_check", "B")
    a = await gw.complete("primary", _request())
    b = await gw.complete("drift_check", _request())
    assert a.text == "A"
    assert b.text == "B"


@pytest.mark.asyncio
async def test_stream_yields_chunks_then_terminator() -> None:
    gw = MockLLMGateway()
    gw.queue_stream("primary", ["he", "ll", "o"])
    collected: list[str] = []
    saw_final = False
    async for chunk in gw.stream("primary", _request()):
        collected.append(chunk.delta)
        if chunk.is_final:
            saw_final = True
    assert "".join(collected) == "hello"
    assert saw_final


@pytest.mark.asyncio
async def test_queue_error_raises_on_complete() -> None:
    gw = MockLLMGateway()
    gw.queue_error("primary", RuntimeError("kaboom"))
    with pytest.raises(RuntimeError, match="kaboom"):
        await gw.complete("primary", _request())


@pytest.mark.asyncio
async def test_embed_returns_deterministic_vectors() -> None:
    gw = MockLLMGateway()
    a = await gw.embed("emb", ["hello", "world"])
    b = await gw.embed("emb", ["hello", "world"])
    assert a == b
    assert len(a) == 2
    assert len(a[0]) == gw.embedding_dim


@pytest.mark.asyncio
async def test_assert_all_consumed_complains_about_leftovers() -> None:
    gw = MockLLMGateway()
    gw.queue_response("primary", "unused")
    with pytest.raises(AssertionError, match="unused queued responses"):
        gw.assert_all_consumed()


@pytest.mark.asyncio
async def test_routes_track_per_campaign() -> None:
    gw = MockLLMGateway()
    await gw.set_route("primary", "anthropic:opus")
    await gw.set_route("primary", "local:llama", campaign_id="camp")
    assert (await gw.list_routes())["primary"] == "anthropic:opus"
    assert (await gw.list_routes(campaign_id="camp"))["primary"] == "local:llama"

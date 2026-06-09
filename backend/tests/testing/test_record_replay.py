"""Unit tests for RecordReplayLLM."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from grimoire.testing.record_replay import (
    FixtureMissingError,
    RecordReplayLLM,
    ReplayMode,
    request_hash,
)
from grimoire.types.llm import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
    Message,
    MessageRole,
    TokenUsage,
)


def _request(text: str = "ping") -> CompletionRequest:
    return CompletionRequest(
        model="probe",
        messages=[Message(role=MessageRole.USER, content=text)],
        max_tokens=8,
        temperature=0.0,
    )


@dataclass
class _RealGateway:
    response_text: str = "She nods."
    response_cost: float | None = None
    calls: list[CompletionRequest] = field(default_factory=list)

    async def complete(
        self, task: str, request: CompletionRequest, campaign_id: Any = None
    ) -> CompletionResponse:
        self.calls.append(request)
        return CompletionResponse(
            text=self.response_text,
            model=request.model,
            finish_reason="stop",
            usage=TokenUsage(input_tokens=3, output_tokens=5),
            cost_estimate_usd=self.response_cost,
            latency_ms=12,
        )

    async def stream(self, task: str, request: CompletionRequest, campaign_id: Any = None):
        self.calls.append(request)
        yield CompletionChunk(delta=self.response_text, is_final=False)
        yield CompletionChunk(
            delta="",
            is_final=True,
            usage=TokenUsage(input_tokens=3, output_tokens=5),
            cost_estimate_usd=self.response_cost,
        )


def test_request_hash_is_stable() -> None:
    a = request_hash(_request())
    b = request_hash(_request())
    assert a == b
    assert request_hash(_request("different")) != a


@pytest.mark.asyncio
async def test_record_then_replay_roundtrip(tmp_path: Path) -> None:
    real = _RealGateway()
    recorder = RecordReplayLLM(tmp_path, mode=ReplayMode.RECORD, real_gateway=real)
    await recorder.complete("primary", _request())
    assert len(real.calls) == 1

    replayer = RecordReplayLLM(tmp_path, mode=ReplayMode.REPLAY)
    response = await replayer.complete("primary", _request())
    assert response.text == "She nods."


@pytest.mark.asyncio
async def test_roundtrip_preserves_provider_cost(tmp_path: Path) -> None:
    """A provider-reported actual charge survives the fixture round-trip so
    replays reproduce the cost data instead of dropping it."""
    real = _RealGateway(response_cost=0.064919)
    recorder = RecordReplayLLM(tmp_path, mode=ReplayMode.RECORD, real_gateway=real)
    await recorder.complete("primary", _request())

    replayer = RecordReplayLLM(tmp_path, mode=ReplayMode.REPLAY)
    response = await replayer.complete("primary", _request())
    assert response.cost_estimate_usd == pytest.approx(0.064919)


@pytest.mark.asyncio
async def test_stream_roundtrip_preserves_provider_cost(tmp_path: Path) -> None:
    """The final-chunk actual charge is captured into the recorded fixture and
    restored on the replayed final chunk."""
    real = _RealGateway(response_cost=0.0123)
    recorder = RecordReplayLLM(tmp_path, mode=ReplayMode.RECORD, real_gateway=real)
    async for _ in recorder.stream("primary", _request()):
        pass

    replayer = RecordReplayLLM(tmp_path, mode=ReplayMode.REPLAY)
    chunks = [c async for c in replayer.stream("primary", _request())]
    final = chunks[-1]
    assert final.is_final
    assert final.cost_estimate_usd == pytest.approx(0.0123)


@pytest.mark.asyncio
async def test_replay_missing_fixture_raises(tmp_path: Path) -> None:
    replayer = RecordReplayLLM(tmp_path, mode=ReplayMode.REPLAY)
    with pytest.raises(FixtureMissingError):
        await replayer.complete("primary", _request())


@pytest.mark.asyncio
async def test_passthrough_delegates_without_writing(tmp_path: Path) -> None:
    real = _RealGateway(response_text="raw")
    passthrough = RecordReplayLLM(tmp_path, mode=ReplayMode.PASSTHROUGH, real_gateway=real)
    response = await passthrough.complete("primary", _request())
    assert response.text == "raw"
    # No fixture written.
    assert not (tmp_path / "llm").exists()


@pytest.mark.asyncio
async def test_record_mode_requires_real_gateway(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        RecordReplayLLM(tmp_path, mode=ReplayMode.RECORD)

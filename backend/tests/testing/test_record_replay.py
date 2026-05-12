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
            latency_ms=12,
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

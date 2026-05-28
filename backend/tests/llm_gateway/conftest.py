"""Fakes and fixtures for the LLM Gateway tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db
from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.llm import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
    ModelInfo,
    ProviderCapabilities,
    TokenUsage,
)


@dataclass
class FakeLLMProvider:
    id: str
    name: str = "fake"
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)
    response_text: str = "hello"
    response_usage: TokenUsage = field(
        default_factory=lambda: TokenUsage(input_tokens=10, output_tokens=5)
    )
    response_cost: float | None = 0.0001
    stream_chunks: list[str] = field(default_factory=lambda: ["he", "ll", "o"])
    raise_sequence: list[BaseException] = field(default_factory=list)
    models: list[ModelInfo] = field(default_factory=list)
    health: HealthStatus | None = None
    call_count: int = 0
    seen_requests: list[CompletionRequest] = field(default_factory=list)
    stream_delays: list[float] = field(default_factory=list)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.call_count += 1
        self.seen_requests.append(request)
        if self.raise_sequence:
            err = self.raise_sequence.pop(0)
            if err is not None:
                raise err
        return CompletionResponse(
            text=self.response_text,
            model=request.model,
            finish_reason="stop",
            usage=self.response_usage,
            cost_estimate_usd=self.response_cost,
            latency_ms=12,
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        self.call_count += 1
        self.seen_requests.append(request)
        if self.raise_sequence:
            err = self.raise_sequence.pop(0)
            if err is not None:
                raise err
        for i, part in enumerate(self.stream_chunks):
            if i < len(self.stream_delays):
                import asyncio

                await asyncio.sleep(self.stream_delays[i])
            yield CompletionChunk(delta=part, is_final=False)
        yield CompletionChunk(delta="", is_final=True, usage=self.response_usage)

    async def list_models(self) -> list[ModelInfo]:
        return list(self.models)

    async def estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    async def health_check(self) -> HealthStatus:
        return self.health or HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)


@dataclass
class FakeEmbeddingProvider:
    id: str = "embed-fake"
    name: str = "fake-embeddings"
    model_id: str = "fake-model"
    dimensions: int = 4
    call_count: int = 0
    seen_inputs: list[list[str]] = field(default_factory=list)
    raise_sequence: list[BaseException] = field(default_factory=list)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.call_count += 1
        self.seen_inputs.append(list(texts))
        if self.raise_sequence:
            err = self.raise_sequence.pop(0)
            if err is not None:
                raise err
        out: list[list[float]] = []
        for text in texts:
            base = float(sum(ord(c) for c in text) % 100)
            out.append([base, base + 1.0, base + 2.0, base + 3.0])
        return out

    async def health_check(self) -> HealthStatus:
        return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)


class FakePlugins:
    """Minimal `Plugins`-shaped object the gateway looks up against."""

    def __init__(
        self,
        llm: dict[str, FakeLLMProvider] | None = None,
        embed: dict[str, FakeEmbeddingProvider] | None = None,
    ) -> None:
        self._llm = dict(llm or {})
        self._embed = dict(embed or {})

    def add_llm(self, provider: FakeLLMProvider) -> None:
        self._llm[provider.id] = provider

    def add_embedding(self, provider: FakeEmbeddingProvider) -> None:
        self._embed[provider.id] = provider

    # The four lookup methods the gateway uses
    def llm_providers(self) -> list[Any]:
        return list(self._llm.values())

    def embedding_providers(self) -> list[Any]:
        return list(self._embed.values())

    def get_llm_provider(self, id: str) -> Any | None:
        return self._llm.get(id)

    def get_embedding_provider(self, id: str) -> Any | None:
        return self._embed.get(id)


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(stamp_migrated_db(tmp_path / "gateway.sqlite"), pool_size=2)
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def plugins() -> FakePlugins:
    return FakePlugins()

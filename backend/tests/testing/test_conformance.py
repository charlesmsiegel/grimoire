"""Tests for the plugin conformance suites."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from grimoire.mechanics.null import NullMechanicsModule
from grimoire.testing import (
    EmbeddingProviderConformance,
    ExportAdapterConformance,
    ImageGenBackendConformance,
    LLMProviderConformance,
    MechanicsConformance,
    MockEmbeddingProvider,
)
from grimoire.testing.conformance.types import ConformanceReport
from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.export import ExportOptions, ExportResult, ExportSelection
from grimoire.types.imagegen import GenerationRequest, GenerationResult
from grimoire.types.llm import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
    ModelInfo,
    ProviderCapabilities,
    TokenUsage,
)

pytestmark = pytest.mark.conformance


# --- Mechanics ------------------------------------------------------- #


@pytest.mark.asyncio
async def test_null_mechanics_passes_conformance() -> None:
    report = await MechanicsConformance().run(NullMechanicsModule())
    assert isinstance(report, ConformanceReport)
    assert report.ok, report.failed
    # The null module declines a schema, so the validate-rejects test is skipped.
    assert any(name == "test_sheet_schema_valid_json_schema" for name, _ in report.skipped)


# --- LLM Provider ---------------------------------------------------- #


@dataclass
class _FakeProvider:
    id: str = "fake-llm"
    name: str = "fake"
    capabilities: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(streaming=True)
    )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        return CompletionResponse(
            text="ok",
            model=request.model,
            finish_reason="stop",
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        yield CompletionChunk(delta="o", is_final=False)
        yield CompletionChunk(
            delta="k",
            is_final=True,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="m1", name="m1", input_cost_per_1k=0.0, output_cost_per_1k=0.0)]

    async def estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    async def health_check(self) -> HealthStatus:
        return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)


@pytest.mark.asyncio
async def test_llm_provider_conformance_passes_for_fake() -> None:
    report = await LLMProviderConformance().run(_FakeProvider())
    assert report.ok, report.failed


# --- Embedding provider --------------------------------------------- #


@pytest.mark.asyncio
async def test_embedding_conformance_passes_for_mock() -> None:
    report = await EmbeddingProviderConformance().run(MockEmbeddingProvider())
    assert report.ok, report.failed


@pytest.mark.asyncio
async def test_embedding_conformance_flags_wrong_dimensions() -> None:
    class WrongProvider:
        id = "bad"
        name = "bad"
        model_id = "bad"
        dimensions = 999  # lies

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 2.0] for _ in texts]

        async def health_check(self) -> HealthStatus:
            return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)

    report = await EmbeddingProviderConformance().run(WrongProvider())
    assert not report.ok
    names = {name for name, _ in report.failed}
    assert "test_embed_returns_correct_vector_dimensions" in names


# --- ImageGen backend ------------------------------------------------ #


@dataclass
class _FakeImageBackend:
    id: str = "fake-img"
    name: str = "fake"
    deterministic_seed: bool = True

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        return GenerationResult(
            image_bytes=b"abc",
            thumbnail_bytes=b"thumb",
            backend=self.id,
            model="m",
            seed=request.seed or 0,
        )

    async def list_models(self) -> list[ModelInfo]:
        return []

    async def list_samplers(self) -> list[str]:
        return ["euler"]

    async def health_check(self) -> HealthStatus:
        return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)


@pytest.mark.asyncio
async def test_imagegen_conformance_passes_for_fake() -> None:
    report = await ImageGenBackendConformance().run(_FakeImageBackend())
    assert report.ok, report.failed


# --- Export adapter -------------------------------------------------- #


@dataclass
class _FakeExportAdapter:
    id: str = "fake-export"
    name: str = "fake"
    extensions: list[str] = field(default_factory=lambda: ["txt"])
    mime_type: str = "text/plain"

    async def export(
        self,
        campaign_id: str,
        selection: ExportSelection,
        options: ExportOptions,
        output_path: Path,
    ) -> ExportResult:
        output_path.write_bytes(b"hello")
        return ExportResult(
            format="txt",
            size_bytes=output_path.stat().st_size,
            scene_count=0 if selection.scene_ids == [] else 1,
        )

    def default_options(self) -> ExportOptions:
        return ExportOptions(title="Default")

    def option_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}


@pytest.mark.asyncio
async def test_export_conformance_passes_for_fake() -> None:
    report = await ExportAdapterConformance().run(_FakeExportAdapter())
    assert report.ok, report.failed

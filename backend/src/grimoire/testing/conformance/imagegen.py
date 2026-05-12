"""ImageGen backend conformance suite (spec 17 §ImageGen Backend)."""

from __future__ import annotations

from typing import Any

from grimoire.testing.conformance.types import (
    ConformanceReport,
    elapsed_ms,
    run_check,
    skip,
    started,
)
from grimoire.types.imagegen import GenerationRequest


def _probe_request(seed: int = 1) -> GenerationRequest:
    return GenerationRequest(
        prompt="a small test image",
        width=64,
        height=64,
        steps=1,
        cfg_scale=1.0,
        seed=seed,
    )


class ImageGenBackendConformance:
    kind = "imagegen_backend"

    async def run(self, adapter: Any) -> ConformanceReport:
        report = ConformanceReport(kind=self.kind, target_id=getattr(adapter, "id", "<unknown>"))
        t0 = started()

        await run_check(
            report,
            "test_generate_returns_image_bytes",
            lambda: self._generate(adapter),
        )
        await run_check(
            report,
            "test_generate_preserves_seed",
            lambda: self._preserves_seed(adapter),
        )
        await run_check(
            report,
            "test_generate_same_seed_same_image",
            lambda: self._deterministic(adapter),
        )
        await run_check(
            report,
            "test_list_samplers_returns_list",
            lambda: self._samplers(adapter),
        )

        report.duration_ms = elapsed_ms(t0)
        return report

    async def _generate(self, adapter: Any) -> None:
        result = await adapter.generate(_probe_request())
        if not result.image_bytes:
            raise AssertionError("generate produced no image bytes")

    async def _preserves_seed(self, adapter: Any) -> None:
        result = await adapter.generate(_probe_request(seed=7))
        if result.seed != 7:
            raise AssertionError(f"backend dropped the seed: requested 7, got {result.seed}")

    async def _deterministic(self, adapter: Any) -> None:
        # Spec says "where backend guarantees this"; declared via a class
        # attribute on the backend instance.
        if not getattr(adapter, "deterministic_seed", False):
            raise skip("backend does not guarantee deterministic seed")
        a = await adapter.generate(_probe_request(seed=11))
        b = await adapter.generate(_probe_request(seed=11))
        if a.image_bytes != b.image_bytes:
            raise AssertionError("same seed produced different image bytes")

    async def _samplers(self, adapter: Any) -> None:
        samplers = await adapter.list_samplers()
        if not isinstance(samplers, list):
            raise AssertionError("list_samplers must return a list")


__all__ = ["ImageGenBackendConformance"]

"""Embedding provider conformance suite."""

from __future__ import annotations

from typing import Any

from grimoire.testing.conformance.types import (
    ConformanceReport,
    elapsed_ms,
    run_check,
    started,
)


class EmbeddingProviderConformance:
    kind = "embedding_provider"

    async def run(self, adapter: Any) -> ConformanceReport:
        report = ConformanceReport(kind=self.kind, target_id=getattr(adapter, "id", "<unknown>"))
        t0 = started()

        await run_check(
            report,
            "test_embed_returns_correct_vector_dimensions",
            lambda: self._dimensions(adapter),
        )
        await run_check(
            report,
            "test_embed_consistent_across_calls",
            lambda: self._consistent(adapter),
        )
        await run_check(
            report,
            "test_embed_empty_input_returns_empty",
            lambda: self._empty(adapter),
        )

        report.duration_ms = elapsed_ms(t0)
        return report

    async def _dimensions(self, adapter: Any) -> None:
        declared = getattr(adapter, "dimensions", 0)
        vectors = await adapter.embed(["hello"])
        if not vectors or len(vectors[0]) != declared:
            raise AssertionError(
                f"declared dimensions={declared} but got {len(vectors[0]) if vectors else 0}"
            )

    async def _consistent(self, adapter: Any) -> None:
        a = await adapter.embed(["the same text"])
        b = await adapter.embed(["the same text"])
        if a != b:
            # Some providers add tiny noise; tolerate component-wise <1e-6.
            for ai, bi in zip(a[0], b[0], strict=True):
                if abs(ai - bi) > 1e-6:
                    raise AssertionError("embed produced different vectors for identical text")

    async def _empty(self, adapter: Any) -> None:
        vectors = await adapter.embed([])
        if vectors != []:
            raise AssertionError("embed([]) must return []")


__all__ = ["EmbeddingProviderConformance"]

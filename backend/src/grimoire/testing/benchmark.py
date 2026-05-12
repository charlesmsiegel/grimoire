"""Performance regression benchmarks (spec 17 §Performance regression tests).

The runner takes a list of :class:`BenchmarkSpec`, each with a budget,
runs them N times, and reports any whose mean exceeds the budget by
more than the configured threshold (default 20%).

This module is deliberately framework-agnostic — it neither imports
pytest nor writes files unless asked. Tests that wire it into pytest
should pull baselines from a JSON file and call :meth:`save_baseline`
to refresh them when the budget moves.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class BenchmarkSpec:
    """One benchmark.

    ``setup`` runs once before the timed loop (e.g. open a DB, seed
    embeddings); its return value is passed positionally to ``fn`` on
    each iteration. ``teardown`` runs once after the loop.
    """

    name: str
    fn: Callable[..., Any]
    budget_ms: float
    iterations: int = 5
    setup: Callable[[], Any] | None = None
    teardown: Callable[[Any], Any] | None = None


@dataclass(slots=True)
class BenchmarkResult:
    name: str
    budget_ms: float
    iterations: int
    samples_ms: list[float] = field(default_factory=list)
    error: str | None = None

    @property
    def mean_ms(self) -> float:
        return statistics.fmean(self.samples_ms) if self.samples_ms else float("nan")

    @property
    def median_ms(self) -> float:
        return statistics.median(self.samples_ms) if self.samples_ms else float("nan")

    @property
    def stdev_ms(self) -> float:
        return statistics.stdev(self.samples_ms) if len(self.samples_ms) > 1 else 0.0

    def regressed(self, threshold_pct: float) -> bool:
        if self.error is not None:
            return True
        if not self.samples_ms:
            return False
        allowed = self.budget_ms * (1.0 + threshold_pct / 100.0)
        return self.mean_ms > allowed


@dataclass(slots=True)
class RegressionReport:
    """Aggregate report across a benchmark run."""

    threshold_pct: float
    results: list[BenchmarkResult] = field(default_factory=list)

    @property
    def regressions(self) -> list[BenchmarkResult]:
        return [r for r in self.results if r.regressed(self.threshold_pct)]

    @property
    def ok(self) -> bool:
        return not self.regressions

    def summary(self) -> str:
        lines = [f"Performance benchmarks (threshold {self.threshold_pct:.1f}%):"]
        for result in self.results:
            mark = "FAIL" if result.regressed(self.threshold_pct) else "ok  "
            lines.append(
                f"  [{mark}] {result.name}: "
                f"mean {result.mean_ms:.1f}ms "
                f"(budget {result.budget_ms:.1f}ms, "
                f"n={result.iterations})"
            )
            if result.error:
                lines.append(f"        error: {result.error}")
        return "\n".join(lines)


class BenchmarkRunner:
    def __init__(self, threshold_pct: float = 20.0) -> None:
        self.threshold_pct = threshold_pct

    async def run(self, specs: list[BenchmarkSpec]) -> RegressionReport:
        results: list[BenchmarkResult] = []
        for spec in specs:
            results.append(await self._run_one(spec))
        return RegressionReport(threshold_pct=self.threshold_pct, results=results)

    async def _run_one(self, spec: BenchmarkSpec) -> BenchmarkResult:
        result = BenchmarkResult(
            name=spec.name,
            budget_ms=spec.budget_ms,
            iterations=spec.iterations,
        )
        ctx: Any = None
        try:
            if spec.setup is not None:
                ctx = await _maybe_await(spec.setup())
            for _ in range(spec.iterations):
                started = time.perf_counter()
                if ctx is None:
                    await _maybe_await(spec.fn())
                else:
                    await _maybe_await(spec.fn(ctx))
                result.samples_ms.append((time.perf_counter() - started) * 1000.0)
            if spec.teardown is not None:
                await _maybe_await(spec.teardown(ctx))
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
        return result

    # ------------------------------------------------------------------ #
    # Baselines on disk
    # ------------------------------------------------------------------ #

    @staticmethod
    def save_baseline(path: Path, report: RegressionReport) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "threshold_pct": report.threshold_pct,
            "results": [
                {
                    "name": r.name,
                    "budget_ms": r.budget_ms,
                    "mean_ms": r.mean_ms,
                    "median_ms": r.median_ms,
                    "stdev_ms": r.stdev_ms,
                    "iterations": r.iterations,
                }
                for r in report.results
            ],
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)

    @staticmethod
    def load_baseline(path: Path) -> dict[str, float]:
        """Return ``{name: budget_ms}`` from a saved baseline file."""
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return {r["name"]: r["budget_ms"] for r in data["results"]}


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _looks_async(fn: Callable[..., Any]) -> bool:
    return asyncio.iscoroutinefunction(fn)


__all__ = [
    "BenchmarkResult",
    "BenchmarkRunner",
    "BenchmarkSpec",
    "RegressionReport",
]

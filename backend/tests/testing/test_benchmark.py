"""Tests for the benchmark runner."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from grimoire.testing import BenchmarkRunner, BenchmarkSpec

pytestmark = pytest.mark.perf


@pytest.mark.asyncio
async def test_runner_collects_samples() -> None:
    async def fast() -> None:
        await asyncio.sleep(0)

    runner = BenchmarkRunner(threshold_pct=20.0)
    report = await runner.run([BenchmarkSpec(name="fast", fn=fast, budget_ms=50.0, iterations=3)])
    assert report.ok
    [result] = report.results
    assert len(result.samples_ms) == 3
    assert result.mean_ms < 50.0


@pytest.mark.asyncio
async def test_runner_flags_regression() -> None:
    async def slow() -> None:
        await asyncio.sleep(0.05)

    runner = BenchmarkRunner(threshold_pct=10.0)
    report = await runner.run([BenchmarkSpec(name="slow", fn=slow, budget_ms=1.0, iterations=2)])
    assert not report.ok
    assert "FAIL" in report.summary()


@pytest.mark.asyncio
async def test_runner_records_error_when_fn_raises() -> None:
    async def boom() -> None:
        raise ValueError("nope")

    runner = BenchmarkRunner()
    report = await runner.run([BenchmarkSpec(name="boom", fn=boom, budget_ms=10.0, iterations=2)])
    [result] = report.results
    assert result.error is not None
    assert "ValueError" in result.error


@pytest.mark.asyncio
async def test_save_and_load_baseline(tmp_path: Path) -> None:
    async def quick() -> None:
        await asyncio.sleep(0)

    runner = BenchmarkRunner()
    report = await runner.run([BenchmarkSpec(name="quick", fn=quick, budget_ms=5.0, iterations=2)])
    path = tmp_path / "baseline.json"
    BenchmarkRunner.save_baseline(path, report)
    loaded = BenchmarkRunner.load_baseline(path)
    assert loaded == {"quick": 5.0}

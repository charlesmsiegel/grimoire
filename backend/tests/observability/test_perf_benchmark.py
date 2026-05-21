"""Tests for the observability per-turn write benchmark harness.

These tests confirm the harness can be invoked, produces sensible
numbers against the standard test database, and does not regress past a
loose ceiling. The ceilings here are 4x the absolute budgets in
``perf_benchmark`` so CI noise does not flake — the harness exists to
*measure*, not to gate.
"""

from __future__ import annotations

import math

from grimoire.observability import perf_benchmark


async def test_run_benchmark_produces_samples_for_every_spec(db) -> None:
    report = await perf_benchmark.run_benchmark(db, iterations=2)

    names = [r.name for r in report.results]
    assert names == [
        "audit_write_minimal",
        "audit_write_realistic",
        "log_event_burst",
        "metric_sample_burst",
        "combined_turn_writes",
    ]
    for result in report.results:
        assert result.error is None, f"{result.name}: {result.error}"
        assert len(result.samples_ms) == 2
        assert all(not math.isnan(s) for s in result.samples_ms)


async def test_realistic_audit_payload_is_larger_than_minimal() -> None:
    """The two audit specs must measure different workloads.

    The realistic builder packs ~3 KB of messages + a 30-source context
    summary + extraction metadata; the minimal builder packs only the
    required fields. If they ever silently collapse to the same payload
    the benchmark stops being informative.
    """
    minimal = perf_benchmark.build_minimal_audit("t_min")
    realistic = perf_benchmark.build_realistic_audit("t_real")

    assert len(realistic.assembled_messages) > 0
    assert minimal.assembled_messages == []
    assert len(realistic.response_text) > len(minimal.response_text)
    assert realistic.context_summary is not None
    assert minimal.context_summary is None


async def test_each_spec_uses_a_unique_turn_id(db) -> None:
    """The harness must not upsert the same row repeatedly.

    If every iteration wrote to ``turn_id='bench'`` the second write
    would be an UPDATE rather than INSERT and the benchmark would
    under-report the real cost of new-row inserts. We exercise the
    realistic-audit spec at N=5 and confirm five distinct rows landed.
    """
    specs = perf_benchmark.build_specs(db, iterations=5)
    realistic = next(s for s in specs if s.name == "audit_write_realistic")
    ctx = await realistic.setup()
    for _ in range(5):
        await realistic.fn(ctx)

    rows = await db.fetchall("SELECT turn_id FROM turn_audits ORDER BY turn_id")
    assert len(rows) == 5
    assert len({r["turn_id"] for r in rows}) == 5


async def test_combined_turn_overhead_within_loose_ceiling(db) -> None:
    """End-to-end per-turn write cost stays well under one second.

    Spec 16 §18 asked whether the synchronous write path is "non-trivial"
    enough to justify a batched writer. The combined spec writes 50 log
    events + 30 metric samples + 1 realistic audit per iteration — a
    workload modelled on a chatty real turn. If this ever balloons past
    a second, we need to revisit batching.
    """
    report = await perf_benchmark.run_benchmark(db, iterations=3)
    combined = next(r for r in report.results if r.name == "combined_turn_writes")
    assert combined.samples_ms, "no samples recorded"
    # 1000 ms is ~25x the budget — flake-free CI guard, not a tuning
    # target. The harness exists to measure precise numbers; this test
    # only catches catastrophic regressions.
    assert combined.mean_ms < 1000.0, (
        f"combined per-turn write mean {combined.mean_ms:.1f}ms exceeded "
        f"1000ms — re-evaluate spec 16 §18 (batched writer)"
    )


async def test_runner_reports_regression_when_budget_too_tight(db) -> None:
    """The runner must surface a regression when budgets are exceeded.

    Force every budget to 0.001 ms so even an empty-row INSERT trips the
    threshold. This protects against the harness silently passing if a
    future refactor breaks budget evaluation.
    """
    impossible_budgets = {name: 0.001 for name in perf_benchmark.DEFAULT_BUDGETS_MS}
    report = await perf_benchmark.run_benchmark(
        db,
        iterations=2,
        budgets_ms=impossible_budgets,
    )
    assert not report.ok
    assert {r.name for r in report.regressions} == set(perf_benchmark.DEFAULT_BUDGETS_MS)

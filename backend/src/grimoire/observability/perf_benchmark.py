"""Per-turn observability write-overhead benchmark (spec 16 §Open questions).

Today each ``TurnAudit`` row, log event and metric sample is committed
synchronously. Spec 16 flagged the open question: "Capturing full audits
per turn has a non-trivial write cost. Async batching is the
implementation; benchmark before committing." This module measures the
actual overhead so the decision is grounded.

The harness uses :class:`grimoire.testing.benchmark.BenchmarkRunner` and
exercises the three write paths flagged by spec §18:

* ``turn_audits`` via :class:`AuditStore.record`
* ``log_events`` via :class:`LogStore.log`
* ``metric_samples`` via :class:`MetricsRegistry.record`

Each spec uses fresh turn ids so the workload models the real path
(``INSERT`` followed by ``ON CONFLICT`` no-op) rather than repeated
upsert of the same row.

Run from the command line::

    uv run python -m grimoire.observability.perf_benchmark

The CLI writes the report to stdout and exits non-zero if any spec
overshoots its budget by more than ``--threshold-pct`` (default 20%).
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from grimoire.observability.audit import AuditStore
from grimoire.observability.log import LogStore
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database, apply_migrations
from grimoire.testing.benchmark import (
    BenchmarkRunner,
    BenchmarkSpec,
    RegressionReport,
)
from grimoire.types.observability import (
    CompositionSnapshot,
    ContextSummary,
    LogEvent,
    LogLevel,
    TurnAudit,
)
from grimoire.types.state import ContextTier

# Realistic payload defaults — see ``build_realistic_audit`` for the
# shape. These are chosen to model a mid-sized turn (a 4-message prompt
# of ~3 KB total, 30 context sources, a handful of extracted deltas) so
# the numbers reflect "what does the audit cost on a real turn" rather
# than a degenerate one-byte row.
DEFAULT_LOG_EVENTS_PER_TURN = 50
DEFAULT_METRIC_SAMPLES_PER_TURN = 30
DEFAULT_ITERATIONS = 20

# Budgets are wall-clock per-iteration ceilings, in milliseconds. They
# are intentionally generous: the harness is a regression guard, not a
# tuning target. Refresh with ``save_baseline`` after a deliberate
# change to the write path.
DEFAULT_BUDGETS_MS: dict[str, float] = {
    "audit_write_minimal": 25.0,
    "audit_write_realistic": 50.0,
    "log_event_burst": 200.0,
    "metric_sample_burst": 150.0,
    "combined_turn_writes": 400.0,
}


@dataclass(slots=True)
class _Ctx:
    """Per-spec setup context: the stores plus a monotonic id counter."""

    audit_store: AuditStore
    log_store: LogStore
    metrics: MetricsRegistry
    counter: int = 0

    def next_turn_id(self) -> str:
        self.counter += 1
        return f"bench_t_{self.counter:08d}"


def build_realistic_audit(turn_id: str) -> TurnAudit:
    """Build a ``TurnAudit`` that models a mid-sized live turn.

    Sized to be larger than a default-constructed audit but smaller than
    a worst-case turn so the benchmark reflects steady-state usage. The
    exact field values do not matter — only the on-disk byte count does.
    """
    messages = [
        {"role": "system", "content": "You are running a tabletop RPG. " * 20},
        {"role": "user", "content": "I open the door and look inside. " * 15},
        {"role": "assistant", "content": "The door creaks open. " * 25},
        {"role": "user", "content": "What do I see? " * 10},
    ]
    return TurnAudit(
        turn_id=turn_id,
        campaign_id="bench_campaign",
        branch_id="bench_campaign:main",
        started_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
        completed_at=datetime(2026, 5, 20, 12, 0, 5, tzinfo=UTC),
        duration_ms=5000,
        player_input="I open the door and look inside.",
        scene_id="bench_scene_001",
        composition_snapshot=CompositionSnapshot(mechanics_module="vampires"),
        context_summary=ContextSummary(
            total_tokens=3500,
            per_tier={ContextTier.SPOTLIGHT: 1800, ContextTier.BACKGROUND: 1700},
            source_count=30,
        ),
        context_messages_hash="0" * 64,
        assembled_messages=messages,
        llm_provider="anthropic",
        llm_model="claude-3-haiku",
        llm_prompt_tokens=2800,
        llm_completion_tokens=420,
        llm_cost_usd=0.0042,
        llm_latency_ms=1200,
        llm_finish_reason="end_turn",
        llm_retries=0,
        response_text="The door creaks open and you see " + ("dust motes. " * 40),
    )


def build_minimal_audit(turn_id: str) -> TurnAudit:
    """Smallest plausible ``TurnAudit``: required fields only."""
    return TurnAudit(
        turn_id=turn_id,
        campaign_id="bench_campaign",
        branch_id="bench_campaign:main",
        started_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
    )


def build_log_event(turn_id: str, idx: int) -> LogEvent:
    return LogEvent(
        timestamp=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
        level=LogLevel.INFO,
        module="orchestrator",
        operation="turn_step",
        turn_id=turn_id,
        payload={
            "message": f"step {idx} processed",
            "step": idx,
            "branch_id": "bench_campaign:main",
        },
        duration_ms=12,
    )


def build_specs(
    db: Database,
    *,
    iterations: int = DEFAULT_ITERATIONS,
    log_events_per_turn: int = DEFAULT_LOG_EVENTS_PER_TURN,
    metric_samples_per_turn: int = DEFAULT_METRIC_SAMPLES_PER_TURN,
    budgets_ms: dict[str, float] | None = None,
) -> list[BenchmarkSpec]:
    """Build the spec list. Pass an open, migrated :class:`Database`."""
    budgets = {**DEFAULT_BUDGETS_MS, **(budgets_ms or {})}

    async def setup() -> _Ctx:
        # Force ``MetricsRegistry`` to record every sample so the
        # benchmark measures the storage cost, not the sampler's RNG.
        from grimoire.observability.config import MetricsConfig

        return _Ctx(
            audit_store=AuditStore(db),
            log_store=LogStore(db),
            metrics=MetricsRegistry(
                db,
                config=MetricsConfig(sample_rate_hot_path=1.0, sample_rate_cold_path=1.0),
            ),
        )

    async def audit_minimal(ctx: _Ctx) -> None:
        await ctx.audit_store.record(build_minimal_audit(ctx.next_turn_id()))

    async def audit_realistic(ctx: _Ctx) -> None:
        await ctx.audit_store.record(build_realistic_audit(ctx.next_turn_id()))

    async def log_burst(ctx: _Ctx) -> None:
        turn_id = ctx.next_turn_id()
        for i in range(log_events_per_turn):
            await ctx.log_store.log(build_log_event(turn_id, i))

    async def metric_burst(ctx: _Ctx) -> None:
        turn_id = ctx.next_turn_id()
        for i in range(metric_samples_per_turn):
            await ctx.metrics.record(
                module="orchestrator",
                operation="turn",
                duration_ms=12.5,
                labels={"turn_id": turn_id, "step": i},
                force=True,
            )

    async def combined(ctx: _Ctx) -> None:
        turn_id = ctx.next_turn_id()
        for i in range(log_events_per_turn):
            await ctx.log_store.log(build_log_event(turn_id, i))
        for i in range(metric_samples_per_turn):
            await ctx.metrics.record(
                module="orchestrator",
                operation="turn",
                duration_ms=12.5,
                labels={"turn_id": turn_id, "step": i},
                force=True,
            )
        await ctx.audit_store.record(build_realistic_audit(turn_id))

    return [
        _spec("audit_write_minimal", audit_minimal, budgets, iterations, setup),
        _spec("audit_write_realistic", audit_realistic, budgets, iterations, setup),
        _spec("log_event_burst", log_burst, budgets, iterations, setup),
        _spec("metric_sample_burst", metric_burst, budgets, iterations, setup),
        _spec("combined_turn_writes", combined, budgets, iterations, setup),
    ]


def _spec(
    name: str,
    fn: Callable[[_Ctx], Awaitable[None]],
    budgets: dict[str, float],
    iterations: int,
    setup: Callable[[], Awaitable[_Ctx]],
) -> BenchmarkSpec:
    return BenchmarkSpec(
        name=name,
        fn=fn,
        budget_ms=budgets[name],
        iterations=iterations,
        setup=setup,
    )


async def run_benchmark(
    db: Database,
    *,
    iterations: int = DEFAULT_ITERATIONS,
    log_events_per_turn: int = DEFAULT_LOG_EVENTS_PER_TURN,
    metric_samples_per_turn: int = DEFAULT_METRIC_SAMPLES_PER_TURN,
    threshold_pct: float = 20.0,
    budgets_ms: dict[str, float] | None = None,
) -> RegressionReport:
    """Run the per-turn write benchmarks against an open database.

    Caller owns the ``Database`` lifecycle. The function does not close
    or migrate the connection — set those up with
    :func:`grimoire.storage.apply_migrations` before calling.
    """
    runner = BenchmarkRunner(threshold_pct=threshold_pct)
    specs = build_specs(
        db,
        iterations=iterations,
        log_events_per_turn=log_events_per_turn,
        metric_samples_per_turn=metric_samples_per_turn,
        budgets_ms=budgets_ms,
    )
    return await runner.run(specs)


async def _cli(args: argparse.Namespace) -> int:
    tmp_root = Path(tempfile.mkdtemp(prefix="grimoire-bench-"))
    try:
        db = Database(tmp_root / "bench.sqlite", pool_size=2)
        await db.connect()
        try:
            await apply_migrations(db)
            report = await run_benchmark(
                db,
                iterations=args.iterations,
                log_events_per_turn=args.log_events,
                metric_samples_per_turn=args.metric_samples,
                threshold_pct=args.threshold_pct,
            )
        finally:
            await db.close()
    finally:
        # ``ignore_errors`` because the SQLite WAL/SHM files may still
        # be mapped by the process briefly on Windows.
        shutil.rmtree(tmp_root, ignore_errors=True)

    print(report.summary())
    # Per-iteration p50 is a more useful headline than mean for write
    # latency; the runner already exposes samples_ms.
    for result in report.results:
        if not result.samples_ms:
            continue
        print(
            f"  {result.name}: "
            f"median {result.median_ms:.2f}ms, "
            f"stdev {result.stdev_ms:.2f}ms"
        )
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument(
        "--log-events",
        dest="log_events",
        type=int,
        default=DEFAULT_LOG_EVENTS_PER_TURN,
        help="log events written per simulated turn",
    )
    parser.add_argument(
        "--metric-samples",
        dest="metric_samples",
        type=int,
        default=DEFAULT_METRIC_SAMPLES_PER_TURN,
        help="metric samples written per simulated turn",
    )
    parser.add_argument(
        "--threshold-pct",
        dest="threshold_pct",
        type=float,
        default=20.0,
        help="regression threshold over budget (percent)",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_cli(args))


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "DEFAULT_BUDGETS_MS",
    "DEFAULT_ITERATIONS",
    "DEFAULT_LOG_EVENTS_PER_TURN",
    "DEFAULT_METRIC_SAMPLES_PER_TURN",
    "build_minimal_audit",
    "build_realistic_audit",
    "build_specs",
    "main",
    "run_benchmark",
]

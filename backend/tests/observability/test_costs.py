"""Tests for ``CostTrackerService``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from grimoire.observability.costs import CostTrackerService
from grimoire.types.llm import LLMCallRecord


def _call(
    *,
    cost: float | None = 0.01,
    task: str = "primary",
    model: str = "claude-3-haiku",
    provider: str = "anthropic",
    campaign: str | None = "c1",
    turn: str | None = None,
) -> LLMCallRecord:
    return LLMCallRecord(
        id=f"r_{task}_{model}_{cost}",
        task=task,
        provider_id=provider,
        model=model,
        input_tokens=100,
        output_tokens=50,
        cost_usd=cost,
        latency_ms=200,
        finish_reason="stop",
        campaign_id=campaign,
        turn_id=turn,
    )


async def test_record_then_total_aggregates_cost(db) -> None:
    tracker = CostTrackerService(db)
    await tracker.record(_call(cost=0.01))
    await tracker.record(_call(cost=0.02))
    total = await tracker.total(campaign_id="c1")
    assert total.total_usd == 0.03
    assert total.call_count == 2


async def test_record_skips_none_cost(db) -> None:
    tracker = CostTrackerService(db)
    await tracker.record(_call(cost=None))
    total = await tracker.total(campaign_id="c1")
    assert total.total_usd == 0.0
    assert total.call_count == 0


async def test_total_filters_by_task_and_model(db) -> None:
    tracker = CostTrackerService(db)
    await tracker.record(_call(cost=0.10, task="primary", model="m-1"))
    await tracker.record(_call(cost=0.05, task="extraction", model="m-2"))
    await tracker.record(_call(cost=0.03, task="primary", model="m-2"))

    assert (await tracker.total(task="primary")).total_usd == 0.13
    assert (await tracker.total(model="m-2")).total_usd == 0.08


async def test_total_filters_by_time_window(db) -> None:
    tracker = CostTrackerService(db)
    await tracker.record(_call(cost=0.02))
    since = datetime.now(UTC) + timedelta(seconds=1)
    total_future = await tracker.total(campaign_id="c1", since=since)
    assert total_future.total_usd == 0.0
    total_recent = await tracker.total(
        campaign_id="c1", since=datetime.now(UTC) - timedelta(hours=1)
    )
    assert total_recent.total_usd == 0.02


async def test_by_task_and_by_model(db) -> None:
    tracker = CostTrackerService(db)
    await tracker.record(_call(cost=0.10, task="primary", model="m-1"))
    await tracker.record(_call(cost=0.05, task="extraction", model="m-1"))
    await tracker.record(_call(cost=0.03, task="extraction", model="m-2"))

    by_task = await tracker.by_task("c1")
    assert set(by_task) == {"primary", "extraction"}
    assert by_task["primary"] == 0.10
    assert abs(by_task["extraction"] - 0.08) < 1e-9

    by_model = await tracker.by_model("c1")
    assert set(by_model) == {"m-1", "m-2"}
    assert abs(by_model["m-1"] - 0.15) < 1e-9
    assert by_model["m-2"] == 0.03


async def test_by_day_returns_one_row_per_calendar_day(db) -> None:
    tracker = CostTrackerService(db)
    await tracker.record(_call(cost=0.01))
    await tracker.record(_call(cost=0.04))
    by_day = await tracker.by_day("c1", days=2)
    assert len(by_day) == 1
    assert by_day[0].total_usd == 0.05
    assert by_day[0].call_count == 2

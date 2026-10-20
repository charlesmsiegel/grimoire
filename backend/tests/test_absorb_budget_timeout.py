"""An absorb timeout owns its cancellation even before the next clock tick."""

import asyncio

import pytest

from grimoire.llm_errors import LLMError
from grimoire.routes import scenes
from grimoire.routes.scenes import _review_budget as scenes_review_budget


async def test_budget_timer_is_not_mistaken_for_an_upstream_timeout(monkeypatch):
    monkeypatch.setattr(scenes_review_budget, "_clock", lambda: 0.0)
    budget = scenes._Budget(0.01)
    events, noticed = [], []

    async def call():
        events.append("entered")
        try:
            await asyncio.Event().wait()
        finally:
            events.append("cancelled")

    with pytest.raises(LLMError) as failure:
        await budget.run(call(), lambda: events.append("marked"), noticed.append)
    assert events == ["marked", "entered", "cancelled"]
    assert budget.spent() is False  # the timer fired before the accounting clock advanced
    assert failure.value.detail == scenes.BUDGET_EXHAUSTED
    assert noticed == [failure.value]


async def test_upstream_timeout_with_time_remaining_keeps_its_reason(monkeypatch):
    monkeypatch.setattr(scenes_review_budget, "_clock", lambda: 0.0)
    budget = scenes._Budget(1.0)

    async def call():
        raise TimeoutError("provider stalled")

    with pytest.raises(LLMError) as failure:
        await budget.run(call())
    assert failure.value.detail == "provider stalled"
    assert not scenes._budget_overrun(failure.value)


async def test_caller_cancellation_is_not_reported_as_a_budget_timeout(monkeypatch):
    monkeypatch.setattr(scenes_review_budget, "_clock", lambda: 0.0)
    budget = scenes._Budget(1.0)
    entered = asyncio.Event()
    noticed = []

    async def call():
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(budget.run(call(), on_timeout=noticed.append))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert noticed == []

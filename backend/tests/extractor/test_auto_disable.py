"""Tests for `AutoDisableState` (SQLite-backed mode-health tracker)."""

from __future__ import annotations

import pytest

from grimoire.extractor.auto_disable import AutoDisableState
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


async def test_unknown_row_is_not_disabled(db: Database):
    state = AutoDisableState(db, min_samples=5)
    assert await state.together_disabled("anthropic", "opus") is False
    assert await state.tool_use_disabled("anthropic", "opus") is False


async def test_below_min_samples_is_not_disabled(db: Database):
    state = AutoDisableState(db, min_samples=20, together_threshold=0.15)
    # 5 failures out of 5 calls — failure rate 100% but below min_samples.
    for _ in range(5):
        await state.record_call("anthropic", "opus", "together", success=False)
    assert await state.together_disabled("anthropic", "opus") is False


async def test_threshold_crossed_disables_together(db: Database):
    state = AutoDisableState(db, min_samples=10, together_threshold=0.15)
    # 20 successes + 5 failures = 25 total, 20% failure rate > 15%.
    for _ in range(20):
        await state.record_call("anthropic", "opus", "together", success=True)
    for _ in range(5):
        await state.record_call("anthropic", "opus", "together", success=False)
    assert await state.together_disabled("anthropic", "opus") is True


async def test_threshold_not_crossed_stays_enabled(db: Database):
    state = AutoDisableState(db, min_samples=10, together_threshold=0.15)
    # 23 successes + 2 failures = 8% failure rate.
    for _ in range(23):
        await state.record_call("anthropic", "opus", "together", success=True)
    for _ in range(2):
        await state.record_call("anthropic", "opus", "together", success=False)
    assert await state.together_disabled("anthropic", "opus") is False


async def test_tool_use_uses_stricter_threshold(db: Database):
    state = AutoDisableState(
        db, min_samples=10, together_threshold=0.15, tool_use_threshold=0.10
    )
    # 9 successes + 2 failures: ~18% — disables tool_use (>10%) but
    # would not disable together (<15%? actually 18% > 15% too). Use a
    # clearer asymmetric case.
    for _ in range(45):
        await state.record_call("openai", "gpt", "tool_use", success=True)
    for _ in range(6):
        await state.record_call("openai", "gpt", "tool_use", success=False)
    # 6/51 ≈ 11.7% — over tool-use threshold (10%), under together (15%).
    assert await state.tool_use_disabled("openai", "gpt") is True


async def test_re_enable_resets_counters(db: Database):
    state = AutoDisableState(db, min_samples=10, together_threshold=0.15)
    for _ in range(20):
        await state.record_call("anthropic", "opus", "together", success=True)
    for _ in range(5):
        await state.record_call("anthropic", "opus", "together", success=False)
    assert await state.together_disabled("anthropic", "opus") is True
    await state.re_enable("anthropic", "opus", "together")
    assert await state.together_disabled("anthropic", "opus") is False


async def test_modes_tracked_independently(db: Database):
    state = AutoDisableState(db, min_samples=10, together_threshold=0.15)
    for _ in range(25):
        await state.record_call("anthropic", "opus", "together", success=False)
    # tool_use untouched
    assert await state.together_disabled("anthropic", "opus") is True
    assert await state.tool_use_disabled("anthropic", "opus") is False

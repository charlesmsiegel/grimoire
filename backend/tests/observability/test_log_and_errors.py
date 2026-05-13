"""Tests for ``LogStore`` and ``ErrorStore``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from grimoire.observability.config import DebugLogConfig
from grimoire.observability.errors_store import ErrorStore
from grimoire.observability.log import LogStore
from grimoire.types.observability import ErrorRecord, LogEvent, LogLevel, LogQuery


def _event(
    *,
    module: str = "extractor",
    level: LogLevel = LogLevel.INFO,
    operation: str = "extract",
    payload: dict | None = None,
    turn_id: str | None = None,
    when: datetime | None = None,
) -> LogEvent:
    return LogEvent(
        timestamp=when or datetime.now(UTC),
        level=level,
        module=module,
        operation=operation,
        payload=payload or {},
        turn_id=turn_id,
    )


async def test_log_and_query_roundtrip(db) -> None:
    store = LogStore(db)
    await store.log(_event(payload={"message": "extracted", "count": 3}, turn_id="t_1"))
    results = await store.query(LogQuery(modules=["extractor"]))
    assert len(results) == 1
    assert results[0].module == "extractor"
    assert results[0].operation == "extract"
    assert results[0].payload["count"] == 3
    assert results[0].payload["message"] == "extracted"
    assert results[0].turn_id == "t_1"


async def test_level_threshold_drops_quiet_levels(db) -> None:
    store = LogStore(
        db,
        config=DebugLogConfig(
            default_level=LogLevel.INFO,
            levels_per_module={"extractor": LogLevel.WARNING},
        ),
    )
    await store.log(_event(level=LogLevel.INFO))
    await store.log(_event(level=LogLevel.WARNING))
    results = await store.query(LogQuery())
    assert len(results) == 1
    assert results[0].level == LogLevel.WARNING


async def test_level_threshold_uses_default_for_unknown_module(db) -> None:
    store = LogStore(db, config=DebugLogConfig(default_level=LogLevel.WARNING))
    await store.log(_event(module="orchestrator", level=LogLevel.DEBUG))
    assert await store.query(LogQuery()) == []


async def test_query_filters_by_turn_id(db) -> None:
    store = LogStore(db)
    await store.log(_event(turn_id="t_1"))
    await store.log(_event(turn_id="t_2"))
    res = await store.query(LogQuery(turn_id="t_2"))
    assert len(res) == 1
    assert res[0].turn_id == "t_2"


async def test_query_filters_by_time_range(db) -> None:
    store = LogStore(db)
    now = datetime.now(UTC)
    await store.log(_event(when=now - timedelta(hours=2)))
    await store.log(_event(when=now))
    fresh = await store.query(LogQuery(since=now - timedelta(minutes=10)))
    assert len(fresh) == 1


async def test_query_filters_by_free_text(db) -> None:
    store = LogStore(db)
    await store.log(_event(payload={"message": "rolled 8 successes"}))
    await store.log(_event(payload={"message": "no match"}))
    matched = await store.query(LogQuery(free_text="successes"))
    assert len(matched) == 1


async def test_error_record_and_recent(db) -> None:
    store = ErrorStore(db)
    err = ErrorRecord(
        timestamp=datetime.now(UTC),
        module="llm_gateway",
        operation="complete",
        error_kind="llm_timeout",
        message="provider timed out",
        turn_id="t_42",
        traceback="...",
        context={"provider": "anthropic"},
        user_visible=True,
        user_action_taken="retried",
    )
    await store.record(err)
    recent = await store.recent()
    assert len(recent) == 1
    out = recent[0]
    assert out.module == "llm_gateway"
    assert out.operation == "complete"
    assert out.error_kind == "llm_timeout"
    assert out.context == {"provider": "anthropic"}
    assert out.user_action_taken == "retried"


async def test_error_aggregate_by_module(db) -> None:
    store = ErrorStore(db)
    for kind in ("a", "a", "b"):
        await store.record(
            ErrorRecord(
                timestamp=datetime.now(UTC),
                module="extractor",
                operation="parse",
                error_kind=kind,
                message="oops",
            )
        )
    grouped = await store.aggregate_by_module()
    assert grouped["extractor"]["a"] == 2
    assert grouped["extractor"]["b"] == 1

"""Tests for the retention maintainer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from grimoire.observability.audit import AuditStore
from grimoire.observability.config import DebugLogConfig, RetentionConfig
from grimoire.observability.costs import CostTrackerService
from grimoire.observability.errors_store import ErrorStore
from grimoire.observability.log import LogStore
from grimoire.observability.maintenance import RetentionMaintainer
from grimoire.observability.metrics import MetricsRegistry
from grimoire.types.llm import LLMCallRecord
from grimoire.types.observability import ErrorRecord, LogEvent, LogLevel, TurnAudit


async def _seed_log(store: LogStore, level: LogLevel, when: datetime) -> None:
    await store.log(
        LogEvent(
            timestamp=when,
            level=level,
            module="extractor",
            operation="extract",
            payload={"k": "v"},
        )
    )


async def test_purges_old_log_events_per_level(db) -> None:
    log = LogStore(db, config=DebugLogConfig(default_level=LogLevel.DEBUG))
    now = datetime.now(UTC)
    await _seed_log(log, LogLevel.DEBUG, now - timedelta(days=30))
    await _seed_log(log, LogLevel.DEBUG, now)
    await _seed_log(log, LogLevel.INFO, now - timedelta(days=100))
    await _seed_log(log, LogLevel.INFO, now)
    await _seed_log(log, LogLevel.ERROR, now - timedelta(days=999))

    maint = RetentionMaintainer(
        db,
        config=RetentionConfig(
            log_debug_days=7,
            log_info_days=30,
            log_warning_days=180,
            log_error_days=None,
            turn_audits_days=None,
            turn_audits_compress_after_days=None,
            cost_records_days=None,
            metric_samples_days=None,
            error_records_days=None,
            health_status_days=None,
        ),
    )
    report = await maint.run_once()
    assert report.deleted_log_events == 2

    rows = await db.fetchall("SELECT level, recorded_at FROM log_events")
    assert len(rows) == 3


async def test_purges_old_metric_samples(db) -> None:
    metrics = MetricsRegistry(db)
    now = datetime.now(UTC)
    await metrics.record(
        module="a",
        operation="x",
        duration_ms=1.0,
        timestamp=now - timedelta(days=200),
        force=True,
    )
    await metrics.record(module="a", operation="x", duration_ms=2.0, timestamp=now, force=True)
    maint = RetentionMaintainer(
        db,
        config=RetentionConfig(
            log_debug_days=None,
            log_info_days=None,
            log_warning_days=None,
            log_error_days=None,
            metric_samples_days=90,
            turn_audits_days=None,
            turn_audits_compress_after_days=None,
            cost_records_days=None,
            error_records_days=None,
            health_status_days=None,
        ),
    )
    report = await maint.run_once()
    assert report.deleted_metric_samples == 1


async def test_compresses_old_turn_audits(db) -> None:
    audits = AuditStore(db)
    await audits.record(
        TurnAudit(
            turn_id="t_old",
            campaign_id="c",
            started_at=datetime.now(UTC) - timedelta(days=1000),
            response_text="old response",
        )
    )
    await audits.record(
        TurnAudit(
            turn_id="t_new",
            campaign_id="c",
            started_at=datetime.now(UTC),
            response_text="new response",
        )
    )
    maint = RetentionMaintainer(
        db,
        config=RetentionConfig(
            log_debug_days=None,
            log_info_days=None,
            log_warning_days=None,
            log_error_days=None,
            metric_samples_days=None,
            turn_audits_days=None,
            turn_audits_compress_after_days=365,
            cost_records_days=None,
            error_records_days=None,
            health_status_days=None,
        ),
    )
    report = await maint.run_once()
    assert report.compressed_turn_audits == 1
    fresh = await audits.get("t_new")
    old = await audits.get("t_old")
    assert fresh is not None and fresh.response_text == "new response"
    assert old is not None and old.response_text == ""


async def test_run_once_no_op_when_disabled(db) -> None:
    log = LogStore(db, config=DebugLogConfig(default_level=LogLevel.DEBUG))
    await _seed_log(log, LogLevel.DEBUG, datetime.now(UTC) - timedelta(days=999))
    maint = RetentionMaintainer(db, config=RetentionConfig(enabled=False))
    report = await maint.run_once()
    assert report.deleted_log_events == 0


async def test_purges_old_cost_records(db) -> None:
    tracker = CostTrackerService(db)
    await tracker.record(
        LLMCallRecord(
            id="r",
            task="t",
            provider_id="p",
            model="m",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.01,
            latency_ms=1,
            finish_reason="stop",
            campaign_id="c",
        )
    )
    await db.execute(
        "UPDATE cost_records SET recorded_at = ?",
        ((datetime.now(UTC) - timedelta(days=400)).isoformat(),),
    )
    maint = RetentionMaintainer(
        db,
        config=RetentionConfig(
            log_debug_days=None,
            log_info_days=None,
            log_warning_days=None,
            log_error_days=None,
            metric_samples_days=None,
            turn_audits_days=None,
            turn_audits_compress_after_days=None,
            cost_records_days=365,
            error_records_days=None,
            health_status_days=None,
        ),
    )
    report = await maint.run_once()
    assert report.deleted_cost_records == 1


async def test_purges_old_error_records(db) -> None:
    errors = ErrorStore(db)
    await errors.record(
        ErrorRecord(
            timestamp=datetime.now(UTC) - timedelta(days=400),
            module="m",
            operation="o",
            error_kind="k",
            message="msg",
        )
    )
    maint = RetentionMaintainer(
        db,
        config=RetentionConfig(
            log_debug_days=None,
            log_info_days=None,
            log_warning_days=None,
            log_error_days=None,
            metric_samples_days=None,
            turn_audits_days=None,
            turn_audits_compress_after_days=None,
            cost_records_days=None,
            error_records_days=365,
            health_status_days=None,
        ),
    )
    report = await maint.run_once()
    assert report.deleted_error_records == 1

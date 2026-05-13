"""Configuration dataclasses for the Observability module (spec 16)."""

from __future__ import annotations

from dataclasses import dataclass, field

from grimoire.types.observability import LogLevel


@dataclass(frozen=True)
class AuditConfig:
    enabled: bool = True
    capture_full_prompt: bool = True
    capture_response: bool = True
    capture_extracted_deltas: bool = True


@dataclass(frozen=True)
class MetricsConfig:
    enabled: bool = True
    sample_rate_hot_path: float = 0.1
    sample_rate_cold_path: float = 1.0
    rolling_window_seconds: int = 30 * 24 * 60 * 60  # 30d


@dataclass(frozen=True)
class HealthCheckConfig:
    probe_interval_seconds: int = 300
    targets: str = "auto"


@dataclass(frozen=True)
class DebugLogConfig:
    default_level: LogLevel = LogLevel.INFO
    levels_per_module: dict[str, LogLevel] = field(default_factory=dict)


@dataclass(frozen=True)
class CostConfig:
    surface_in_status_bar: bool = True
    daily_budget_warn_usd: float = 5.00
    daily_budget_alert_usd: float = 20.00


@dataclass(frozen=True)
class RetentionConfig:
    """Per-table retention in days (None = forever)."""

    turn_audits_days: int | None = 365
    turn_audits_compress_after_days: int | None = 365
    cost_records_days: int | None = None
    metric_samples_days: int | None = 90
    log_debug_days: int | None = 7
    log_info_days: int | None = 30
    log_warning_days: int | None = 180
    log_error_days: int | None = None
    error_records_days: int | None = None
    health_status_days: int | None = 30
    enabled: bool = True


@dataclass(frozen=True)
class ObservabilityConfig:
    audit: AuditConfig = field(default_factory=AuditConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    health: HealthCheckConfig = field(default_factory=HealthCheckConfig)
    debug_log: DebugLogConfig = field(default_factory=DebugLogConfig)
    cost: CostConfig = field(default_factory=CostConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)


__all__ = [
    "AuditConfig",
    "CostConfig",
    "DebugLogConfig",
    "HealthCheckConfig",
    "MetricsConfig",
    "ObservabilityConfig",
    "RetentionConfig",
]

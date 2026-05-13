"""Observability module (spec 16).

Captures per-turn audit records, tracks costs and module-level metrics,
probes connected providers for health, exposes a queryable debug log and
error store, and replays past turns. Read-mostly: it consumes events
emitted by the Orchestrator (and direct calls from any module that wants
to log) and indexes them. It owns no domain state.
"""

from __future__ import annotations

from grimoire.observability.audit import AuditStore
from grimoire.observability.config import (
    AuditConfig,
    CostConfig,
    DebugLogConfig,
    HealthCheckConfig,
    MetricsConfig,
    ObservabilityConfig,
    RetentionConfig,
)
from grimoire.observability.costs import CostTrackerService
from grimoire.observability.errors_store import ErrorStore
from grimoire.observability.health import HealthMonitorService
from grimoire.observability.log import LogStore
from grimoire.observability.maintenance import RetentionMaintainer
from grimoire.observability.metrics import MetricsRegistry
from grimoire.observability.replayer import TurnReplayerService
from grimoire.observability.service import ObservabilityService
from grimoire.observability.turn_auditor import TurnAuditor

__all__ = [
    "AuditConfig",
    "AuditStore",
    "CostConfig",
    "CostTrackerService",
    "DebugLogConfig",
    "ErrorStore",
    "HealthCheckConfig",
    "HealthMonitorService",
    "LogStore",
    "MetricsConfig",
    "MetricsRegistry",
    "ObservabilityConfig",
    "ObservabilityService",
    "RetentionConfig",
    "RetentionMaintainer",
    "TurnAuditor",
    "TurnReplayerService",
]

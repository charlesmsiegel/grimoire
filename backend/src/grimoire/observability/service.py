"""Top-level ``ObservabilityService`` (spec 16 §interface).

Glues the constituent stores (audit, costs, metrics, health, logs,
errors) and the turn auditor + replayer behind a single Observability
protocol. Construction takes the SQLite ``Database`` and optionally an
event bus (so the turn auditor can subscribe) and a state-store +
gateway pair (so replay can fork branches and re-run prompts).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from grimoire.event_bus import Event, EventBus, Subscription
from grimoire.observability.audit import AuditStore
from grimoire.observability.config import ObservabilityConfig
from grimoire.observability.costs import CostTrackerService
from grimoire.observability.errors_store import ErrorStore
from grimoire.observability.health import HealthMonitorService
from grimoire.observability.log import LogStore
from grimoire.observability.maintenance import MaintenanceReport, RetentionMaintainer
from grimoire.observability.metrics import MetricsRegistry
from grimoire.observability.replayer import TurnReplayerService
from grimoire.observability.turn_auditor import TurnAuditor
from grimoire.storage.db import Database
from grimoire.types.common import CampaignId, HealthStatus, SubscriptionId, TurnId
from grimoire.types.llm import LLMCallRecord
from grimoire.types.observability import (
    ErrorRecord,
    LogEvent,
    LogQuery,
    ReplayOptions,
    ReplayResult,
    TurnAudit,
)

logger = logging.getLogger(__name__)


class ObservabilityService:
    """Public Observability façade — see spec 16 §interface."""

    def __init__(
        self,
        *,
        db: Database,
        config: ObservabilityConfig | None = None,
        event_bus: EventBus | None = None,
        state_store: object | None = None,
        llm_gateway: object | None = None,
    ) -> None:
        self._db = db
        self._config = config or ObservabilityConfig()
        self._event_bus = event_bus

        self.audit_store = AuditStore(db)
        self.costs_tracker = CostTrackerService(db)
        self.metrics_registry = MetricsRegistry(db, config=self._config.metrics)
        self.health_monitor = HealthMonitorService(db, config=self._config.health)
        self.log_store = LogStore(db, config=self._config.debug_log)
        self.errors_store = ErrorStore(db)
        self.retention = RetentionMaintainer(db, config=self._config.retention)

        self.turn_auditor: TurnAuditor | None = None
        if event_bus is not None:
            self.turn_auditor = TurnAuditor(
                event_bus=event_bus,
                audit_store=self.audit_store,
                config=self._config.audit,
            )

        self.replayer: TurnReplayerService | None = None
        if llm_gateway is not None:
            self.replayer = TurnReplayerService(
                audit_store=self.audit_store,
                gateway=llm_gateway,  # type: ignore[arg-type]
                state_store=state_store,  # type: ignore[arg-type]
            )

        self._cost_subscription: Subscription | None = None
        self._health_subscription: SubscriptionId | None = None

    async def start(self) -> None:
        """Subscribe the turn auditor and start the health probe + retention
        loops. Safe to call once at app startup."""
        if self.turn_auditor is not None:
            self.turn_auditor.start()
        if self._event_bus is not None and self._cost_subscription is None:
            self._cost_subscription = self._event_bus.subscribe(
                "llm_response_received", self._on_llm_response
            )
        if self._event_bus is not None and self._health_subscription is None:
            # §12 Frontend Health panel: republish each probe result onto the
            # event bus as ``health_status_changed`` so StreamManager can fan
            # it out to live WebSocket subscribers.
            self._health_subscription = self.health_monitor.subscribe(
                self._on_health_status
            )
        await self.health_monitor.load_latest()

    async def shutdown(self) -> None:
        if self.turn_auditor is not None:
            self.turn_auditor.stop()
        if self._cost_subscription is not None:
            self._cost_subscription.unsubscribe()
            self._cost_subscription = None
        if self._health_subscription is not None:
            self.health_monitor.unsubscribe(self._health_subscription)
            self._health_subscription = None
        await self.health_monitor.stop()
        await self.retention.stop()

    async def _on_health_status(self, status: HealthStatus) -> None:
        if self._event_bus is None:
            return
        try:
            await self._event_bus.emit(
                Event(
                    type="health_status_changed",
                    payload={
                        "target_id": status.target_id,
                        "level": status.level.value,
                        "message": status.message,
                        "checked_at": status.checked_at,
                        "details": status.details or {},
                    },
                )
            )
        except Exception:
            logger.exception("health_status_changed event emit failed")

    async def _on_llm_response(self, event: Event) -> None:
        try:
            payload = event.payload or {}
            usage = payload.get("usage") or {}
            call = LLMCallRecord(
                id=uuid.uuid4().hex,
                task=str(payload.get("task") or ""),
                provider_id=str(payload.get("provider") or ""),
                model=str(payload.get("model") or ""),
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cost_usd=payload.get("cost_estimate_usd"),
                latency_ms=int(payload.get("latency_ms") or 0),
                finish_reason=str(payload.get("finish_reason") or ""),
                campaign_id=payload.get("campaign_id"),
                turn_id=payload.get("turn_id"),
            )
            await self.costs_tracker.record(call)
        except Exception:
            logger.exception("failed to record cost from llm_response_received")

    # ------------------------------------------------------------------ #
    # Observability protocol
    # ------------------------------------------------------------------ #

    async def record_turn_audit(self, audit: TurnAudit) -> None:
        await self.audit_store.record(audit)

    async def get_turn_audit(self, turn_id: TurnId) -> TurnAudit:
        audit = await self.audit_store.get(turn_id)
        if audit is None:
            raise KeyError(f"unknown turn {turn_id!r}")
        return audit

    async def list_turn_audits(
        self,
        campaign_id: CampaignId,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[TurnAudit]:
        return await self.audit_store.list(campaign_id, since=since, limit=limit)

    async def replay_turn(self, turn_id: TurnId, opts: ReplayOptions) -> ReplayResult:
        if self.replayer is None:
            raise RuntimeError(
                "replay requires an LLM gateway; pass one to ObservabilityService(...)"
            )
        return await self.replayer.replay(turn_id, opts)

    def costs(self) -> CostTrackerService:
        return self.costs_tracker

    def health(self) -> HealthMonitorService:
        return self.health_monitor

    def metrics(self) -> MetricsRegistry:
        return self.metrics_registry

    async def log(self, event: LogEvent) -> None:
        await self.log_store.log(event)

    async def query_log(self, query: LogQuery) -> list[LogEvent]:
        return await self.log_store.query(query)

    async def record_error(self, err: ErrorRecord) -> None:
        await self.errors_store.record(err)
        # §16: fire an ``error_reported`` event so plugin hooks (Sentry,
        # external loggers) can subscribe via the event bus.
        if self._event_bus is not None:
            try:
                await self._event_bus.emit(
                    Event(
                        type="error_reported",
                        payload={
                            "module": err.module,
                            "operation": err.operation,
                            "error_kind": err.error_kind,
                            "message": err.message,
                            "turn_id": err.turn_id,
                            "user_visible": err.user_visible,
                            "context": err.context or {},
                        },
                    )
                )
            except Exception:
                logger.exception("error_reported event emit failed")

    async def recent_errors(self, limit: int = 50) -> list[ErrorRecord]:
        return await self.errors_store.recent(limit=limit)

    async def run_maintenance(self) -> MaintenanceReport:
        """Apply retention policy once; used by the nightly task."""
        return await self.retention.run_once()


__all__ = ["ObservabilityService"]

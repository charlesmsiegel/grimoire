"""``HealthMonitor`` (spec 16 §health checks).

Probes registered targets (LLM providers, embedding providers, ImageGen
backends, etc.) and stores their latest status in ``health_status``. A
periodic loop can be started via :meth:`start_periodic`; subscribers are
notified each time a probe result lands.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol

from grimoire.observability.config import HealthCheckConfig
from grimoire.storage.db import Database
from grimoire.types.common import HealthLevel, HealthStatus, SubscriptionId
from grimoire.types.observability import HealthTarget

logger = logging.getLogger(__name__)


class _Probeable(Protocol):
    """Anything we can probe — providers implement ``health_check`` directly."""

    id: str

    async def health_check(self) -> HealthStatus: ...


HealthHandler = Callable[[HealthStatus], Awaitable[None]]


class HealthMonitorService:
    """Concrete HealthMonitor.

    Targets are registered as ``(HealthTarget, async-callable)`` pairs. The
    callable returns a :class:`HealthStatus`. We don't import provider
    types here — duck-typing keeps the module reusable across the LLM
    Gateway, embedding plugins and ImageGen backends.
    """

    def __init__(
        self,
        db: Database,
        *,
        config: HealthCheckConfig | None = None,
    ) -> None:
        self._db = db
        self._config = config or HealthCheckConfig()
        self._targets: dict[str, tuple[HealthTarget, Callable[[], Awaitable[HealthStatus]]]] = {}
        self._subscribers: dict[SubscriptionId, HealthHandler] = {}
        self._latest: dict[str, HealthStatus] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #

    def register(
        self,
        target: HealthTarget,
        probe: Callable[[], Awaitable[HealthStatus]],
    ) -> None:
        self._targets[target.id] = (target, probe)

    def register_probeable(self, target: HealthTarget, obj: _Probeable) -> None:
        async def _probe() -> HealthStatus:
            return await obj.health_check()

        self.register(target, _probe)

    def unregister(self, target_id: str) -> None:
        self._targets.pop(target_id, None)
        self._latest.pop(target_id, None)

    def targets(self) -> list[HealthTarget]:
        return [t for (t, _) in self._targets.values()]

    # ------------------------------------------------------------------ #
    # Probes
    # ------------------------------------------------------------------ #

    async def probe(self, target: HealthTarget) -> HealthStatus:
        entry = self._targets.get(target.id)
        if entry is None:
            status = HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=target.id,
                message="no probe registered",
                checked_at=datetime.now(UTC).isoformat(),
            )
        else:
            _, fn = entry
            try:
                status = await fn()
            except Exception as exc:
                status = HealthStatus(
                    level=HealthLevel.UNHEALTHY,
                    target_id=target.id,
                    message=str(exc),
                    checked_at=datetime.now(UTC).isoformat(),
                )
            if not status.checked_at:
                status = status.model_copy(update={"checked_at": datetime.now(UTC).isoformat()})
            if not status.target_id:
                status = status.model_copy(update={"target_id": target.id})
        await self._persist(target, status)
        await self._notify(status)
        return status

    async def probe_all(self) -> list[HealthStatus]:
        results = []
        for target, _ in list(self._targets.values()):
            results.append(await self.probe(target))
        return results

    async def _persist(self, target: HealthTarget, status: HealthStatus) -> None:
        self._latest[target.id] = status
        await self._db.execute(
            """
            INSERT INTO health_status (target_id, target_kind, level, message, details, checked_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET
                target_kind=excluded.target_kind,
                level=excluded.level,
                message=excluded.message,
                details=excluded.details,
                checked_at=excluded.checked_at
            """,
            (
                target.id,
                target.kind,
                status.level.value,
                status.message,
                json.dumps(status.details or {}),
                status.checked_at or datetime.now(UTC).isoformat(),
            ),
        )

    async def _notify(self, status: HealthStatus) -> None:
        for handler in list(self._subscribers.values()):
            try:
                await handler(status)
            except Exception:
                logger.exception("health subscriber failed")

    # ------------------------------------------------------------------ #
    # Subscribers
    # ------------------------------------------------------------------ #

    def subscribe(self, handler: HealthHandler) -> SubscriptionId:
        sub_id = uuid.uuid4().hex
        self._subscribers[sub_id] = handler
        return sub_id

    def unsubscribe(self, sub_id: SubscriptionId) -> None:
        self._subscribers.pop(sub_id, None)

    def latest(self) -> dict[str, HealthStatus]:
        return dict(self._latest)

    async def load_latest(self) -> None:
        """Repopulate the in-memory ``latest`` map from the database.

        Useful at startup so the Frontend's health panel can render the
        last-known state before any new probe lands.
        """
        rows = await self._db.fetchall(
            "SELECT target_id, target_kind, level, message, details, checked_at FROM health_status"
        )
        for row in rows:
            try:
                details = json.loads(row["details"]) if row["details"] else {}
            except (TypeError, ValueError):
                details = {}
            self._latest[row["target_id"]] = HealthStatus(
                level=HealthLevel(row["level"]),
                target_id=row["target_id"],
                message=row["message"] or "",
                checked_at=row["checked_at"],
                details=details,
            )

    # ------------------------------------------------------------------ #
    # Periodic loop
    # ------------------------------------------------------------------ #

    async def start_periodic(self) -> None:
        """Start a background loop probing all targets at the configured
        interval. Idempotent."""
        if self._task is not None:
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="health-probe-loop")

    async def stop(self) -> None:
        if self._task is None:
            return
        assert self._stop_event is not None
        self._stop_event.set()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self._stop_event = None

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        interval = max(1, int(self._config.probe_interval_seconds))
        while not self._stop_event.is_set():
            try:
                await self.probe_all()
            except Exception:
                logger.exception("periodic probe_all failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except TimeoutError:
                continue


__all__ = ["HealthHandler", "HealthMonitorService"]

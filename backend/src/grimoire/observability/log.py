"""Structured debug log store (spec 16 §structured debug log).

Lightweight fine-grained events queryable by time range, module/operation,
turn id, level, and a free-text match against ``message``/``payload``.
Backed by the ``log_events`` table.

A subscriber hook (:meth:`LogStore.subscribe`) lets live consumers — the
``/ws/observability/log`` WebSocket route — receive each accepted event as
it's written, without polling. Per-module level thresholds are enforced
*before* fanout so subscribers only see events that also reached the table.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from grimoire.observability.config import DebugLogConfig
from grimoire.storage.db import Database
from grimoire.types.observability import LogEvent, LogLevel, LogQuery

LEVEL_ORDER: dict[LogLevel, int] = {
    LogLevel.DEBUG: 10,
    LogLevel.INFO: 20,
    LogLevel.WARNING: 30,
    LogLevel.ERROR: 40,
}


@dataclass(slots=True)
class LogSubscription:
    """Live tail of accepted log events for one consumer.

    Each subscriber owns an :class:`asyncio.Queue` it can ``await`` on. The
    queue is bounded — a slow consumer never blocks log writes; once it
    fills, additional events are dropped and counted on :attr:`dropped` so
    the consumer can surface an "events dropped" hint.
    """

    queue: asyncio.Queue[LogEvent]
    _store: LogStore
    _active: bool = True
    dropped: int = 0

    def unsubscribe(self) -> None:
        if not self._active:
            return
        self._active = False
        self._store._unsubscribe(self)


class LogStore:
    """Writes and queries structured log events.

    Per-module log level thresholds are honored on write so a noisy module
    can be configured at ``DEBUG`` while everyone else stays at ``INFO``.
    """

    def __init__(
        self,
        db: Database,
        *,
        config: DebugLogConfig | None = None,
    ) -> None:
        self._db = db
        self._config = config or DebugLogConfig()
        self._subscribers: list[LogSubscription] = []

    def _level_for(self, module: str) -> LogLevel:
        return self._config.levels_per_module.get(module, self._config.default_level)

    def _accept(self, module: str, level: LogLevel) -> bool:
        return LEVEL_ORDER[level] >= LEVEL_ORDER[self._level_for(module)]

    def subscribe(self, *, queue_size: int = 256) -> LogSubscription:
        """Register a live consumer. Call :meth:`LogSubscription.unsubscribe`
        when done — typically in a ``finally`` after the WebSocket closes."""
        sub = LogSubscription(queue=asyncio.Queue(maxsize=queue_size), _store=self)
        self._subscribers.append(sub)
        return sub

    def _unsubscribe(self, sub: LogSubscription) -> None:
        try:
            self._subscribers.remove(sub)
        except ValueError:
            return

    def _notify(self, event: LogEvent) -> None:
        # put_nowait is non-blocking so a slow socket can never stall the
        # producer. Inactive subs are pruned by ``_unsubscribe`` already; the
        # _active guard covers the rare race where unsubscribe() ran on
        # another task between this list iteration starting and now.
        for sub in self._subscribers:
            if not sub._active:
                continue
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                sub.dropped += 1

    async def log(self, event: LogEvent) -> None:
        if not self._accept(event.module, event.level):
            return
        message = ""
        payload: dict[str, Any] = {}
        if isinstance(event.payload, dict):
            payload = dict(event.payload)
            message = str(payload.pop("message", "")) if "message" in payload else ""
        body = {
            "payload": payload,
            "duration_ms": event.duration_ms,
            "error": event.error,
        }
        await self._db.execute(
            "INSERT INTO log_events "
            "(module, operation, turn_id, level, message, payload, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event.module,
                event.operation,
                event.turn_id,
                event.level.value,
                message,
                json.dumps(body),
                event.timestamp.isoformat() if event.timestamp else datetime.now(UTC).isoformat(),
            ),
        )
        self._notify(event)

    async def query(self, query: LogQuery) -> list[LogEvent]:
        clauses: list[str] = []
        params: list[Any] = []
        if query.since is not None:
            clauses.append("recorded_at >= ?")
            params.append(query.since.isoformat())
        if query.until is not None:
            clauses.append("recorded_at < ?")
            params.append(query.until.isoformat())
        if query.levels:
            placeholders = ", ".join("?" for _ in query.levels)
            clauses.append(f"level IN ({placeholders})")
            params.extend(level.value for level in query.levels)
        if query.modules:
            placeholders = ", ".join("?" for _ in query.modules)
            clauses.append(f"module IN ({placeholders})")
            params.extend(query.modules)
        if query.operations:
            placeholders = ", ".join("?" for _ in query.operations)
            clauses.append(f"operation IN ({placeholders})")
            params.extend(query.operations)
        if query.turn_id:
            clauses.append("turn_id = ?")
            params.append(query.turn_id)
        if query.free_text:
            clauses.append("(message LIKE ? OR payload LIKE ?)")
            needle = f"%{query.free_text}%"
            params.extend([needle, needle])

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = await self._db.fetchall(
            f"SELECT module, operation, turn_id, level, message, payload, recorded_at "
            f"FROM log_events{where} ORDER BY recorded_at DESC LIMIT ?",
            (*params, int(query.limit)),
        )
        events: list[LogEvent] = []
        for row in rows:
            try:
                body = json.loads(row["payload"]) if row["payload"] else {}
            except (TypeError, ValueError):
                body = {}
            payload = body.get("payload") or {}
            if row["message"]:
                payload = {"message": row["message"], **payload}
            events.append(
                LogEvent(
                    timestamp=datetime.fromisoformat(row["recorded_at"]),
                    level=LogLevel(row["level"].upper() if row["level"] else "INFO"),
                    module=row["module"],
                    operation=row["operation"] or "",
                    turn_id=row["turn_id"],
                    payload=payload,
                    duration_ms=body.get("duration_ms"),
                    error=body.get("error"),
                )
            )
        return events


__all__ = ["LEVEL_ORDER", "LogStore", "LogSubscription"]

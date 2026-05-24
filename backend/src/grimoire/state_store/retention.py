"""Periodic sweep that removes embeddings for retired facts past their retention window."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from grimoire import events
from grimoire.event_bus import Event, EventBus
from grimoire.state_store.config import RetentionConfig
from grimoire.storage.db import Database

logger = logging.getLogger(__name__)

_DELETE_SQL = """
DELETE FROM embeddings
WHERE source_kind = 'fact'
  AND ref IN (
    SELECT facts.id
    FROM facts
    JOIN posts ON posts.id = facts.retired_in_post
    WHERE facts.retired = 1
      AND posts.created_at IS NOT NULL
      AND posts.created_at <= ?
  )
"""


async def delete_expired_embeddings(
    db: Database,
    *,
    max_age_seconds: int,
    now: datetime | None = None,
) -> int:
    """Delete embeddings for facts retired more than *max_age_seconds* ago.

    Returns the number of rows deleted.
    """
    effective_now = now if now is not None else datetime.now(UTC)
    cutoff = (effective_now - timedelta(seconds=max_age_seconds)).isoformat()
    async with db.acquire() as conn:
        cur = await conn.execute(_DELETE_SQL, (cutoff,))
        return int(cur.rowcount or 0)


class RetentionSweeper:
    """Background task that periodically purges embeddings for retired facts.

    Parameters
    ----------
    db:
        Connected :class:`~grimoire.storage.db.Database` instance.
    config:
        Retention settings.  When
        ``config.embeddings_for_retired_facts_seconds`` is ``None`` the sweep
        is a no-op (forever retention).
    bus:
        Optional event bus; when supplied a ``retention_sweep_completed``
        event is emitted after each productive sweep.
    clock:
        Callable returning the current :class:`datetime`.  Defaults to
        ``datetime.now(UTC)``.  Inject a fixed value in tests.
    """

    def __init__(
        self,
        *,
        db: Database,
        config: RetentionConfig,
        bus: EventBus | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._db = db
        self._config = config
        self._bus = bus
        self._clock: Callable[[], datetime] = (
            clock if clock is not None else lambda: datetime.now(UTC)
        )
        self._task: asyncio.Task[Any] | None = None
        self._stop: asyncio.Event | None = None

    async def sweep_once(self) -> int:
        """Run a single sweep and return the number of deleted rows."""
        max_age = self._config.embeddings_for_retired_facts_seconds
        if max_age is None:
            return 0
        deleted = await delete_expired_embeddings(
            self._db,
            max_age_seconds=max_age,
            now=self._clock(),
        )
        if self._bus is not None:
            await self._bus.emit(
                Event(type=events.RETENTION_SWEEP_COMPLETED, payload={"deleted_embeddings": deleted})
            )
        return deleted

    async def start(self) -> None:
        """Start the background sweep loop. Idempotent."""
        if self._task is not None:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="retention-sweeper")

    async def stop(self) -> None:
        """Stop the background sweep loop and wait for it to finish."""
        if self._task is None:
            return
        assert self._stop is not None
        self._stop.set()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self._stop = None

    async def _loop(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            try:
                await self.sweep_once()
            except Exception:
                logger.exception("retention sweep failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._config.sweep_interval_seconds,
                )
            except TimeoutError:
                continue


__all__ = ["RetentionSweeper", "delete_expired_embeddings"]

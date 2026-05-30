"""Lifecycle management for background workers and services.

Provides ``LifecycleManager`` for orderly shutdown and ``QueueBundle``
for decoupling background workers from the file watcher.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from grimoire.watcher import EmbeddingQueue

logger = logging.getLogger(__name__)


@runtime_checkable
class Stoppable(Protocol):
    async def stop(self) -> None: ...


@runtime_checkable
class SyncStoppable(Protocol):
    def stop(self) -> None: ...


class LifecycleManager:
    """Tracks background workers/subscribers and stops them in reverse order."""

    def __init__(self) -> None:
        self._entries: list[tuple[str, object, bool]] = []  # (name, obj, is_async)

    def register_async(self, name: str, stoppable: Stoppable) -> None:
        self._entries.append((name, stoppable, True))

    def register_sync(self, name: str, stoppable: SyncStoppable) -> None:
        self._entries.append((name, stoppable, False))

    async def stop_all(self) -> None:
        for name, obj, is_async in reversed(self._entries):
            try:
                if is_async:
                    await obj.stop()  # type: ignore[union-attr]
                else:
                    obj.stop()  # type: ignore[union-attr]
            except Exception:
                logger.exception("%s stop failed during shutdown", name)
        self._entries.clear()


@dataclass
class QueueBundle:
    """Owns the embedding queue independently of the file watcher.

    Created unconditionally at startup so background workers always have
    a queue to drain, even when file watching is disabled.
    """

    embedding: EmbeddingQueue = field(default_factory=EmbeddingQueue)

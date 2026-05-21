"""In-process async pub/sub event bus.

Owned by the Orchestrator (spec 01). Other modules — Frontend WS relay,
Continuity, Time Engine, ImageGen, Characters drift scheduler, Observability —
subscribe to typed events and react asynchronously.

The wildcard event type ``"*"`` subscribes to every event; Observability uses it
to audit the full stream.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

WILDCARD = "*"

Handler = Callable[["Event"], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class Event:
    """A bus event. ``type`` routes; ``payload`` carries event-specific data."""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: float = field(default_factory=time.time)


@dataclass(slots=True)
class Subscription:
    """Handle returned by :meth:`EventBus.subscribe`. Call :meth:`unsubscribe`
    to remove the handler, or use as an async context manager."""

    event_type: str
    handler: Handler
    _bus: EventBus
    _active: bool = True

    def unsubscribe(self) -> None:
        if not self._active:
            return
        self._active = False
        self._bus._remove(self)

    async def __aenter__(self) -> Subscription:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.unsubscribe()


class EventBus:
    """Async pub/sub bus with per-type and wildcard subscribers.

    Handlers may be sync or async. ``emit`` schedules all matching handlers
    concurrently and awaits their completion; a failing handler is logged and
    does not prevent siblings from running.
    """

    def __init__(self) -> None:
        # subscribe / _remove / emit run without awaiting between their
        # internal reads and writes, so concurrent coroutines on a single
        # event loop see a coherent view of `_subs`. Multi-thread callers
        # must serialize externally — there's no asyncio.Lock here because
        # one would mean nothing in sync methods.
        self._subs: dict[str, list[Subscription]] = {}

    def subscribe(self, event_type: str, handler: Handler) -> Subscription:
        if not event_type:
            raise ValueError("event_type must be a non-empty string")
        sub = Subscription(event_type=event_type, handler=handler, _bus=self)
        self._subs.setdefault(event_type, []).append(sub)
        return sub

    def _remove(self, sub: Subscription) -> None:
        bucket = self._subs.get(sub.event_type)
        if not bucket:
            return
        try:
            bucket.remove(sub)
        except ValueError:
            return
        if not bucket:
            del self._subs[sub.event_type]

    def subscriber_count(self, event_type: str | None = None) -> int:
        if event_type is None:
            return sum(len(b) for b in self._subs.values())
        return len(self._subs.get(event_type, ()))

    async def emit(self, event: Event) -> None:
        handlers = [
            *self._subs.get(event.type, ()),
            *self._subs.get(WILDCARD, ()),
        ]
        if not handlers:
            return

        coros = [self._invoke(sub, event) for sub in handlers if sub._active]
        if coros:
            # _invoke already catches; return_exceptions=True keeps that
            # contract explicit so if _invoke ever stops catching, sibling
            # handlers still run (instead of one bad handler aborting the
            # rest of the dispatch via gather's default fail-fast).
            await asyncio.gather(*coros, return_exceptions=True)

    async def _invoke(self, sub: Subscription, event: Event) -> None:
        try:
            result = sub.handler(event)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception(
                "event handler raised for event_type=%s sub=%s",
                event.type,
                sub.event_type,
            )


__all__ = ["WILDCARD", "Event", "EventBus", "Handler", "Subscription"]

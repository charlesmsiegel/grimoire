"""Event types and a minimal in-process bus stub.

The real bus is task #3. This module defines the event payload classes the
Scene Manager emits and a small `EventBus` protocol that can be injected.
When the real bus lands, the Scene Manager just receives it via DI.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class SceneEvent:
    type: str
    campaign_id: str
    scene_id: str
    payload: dict[str, Any] = field(default_factory=dict)


SCENE_STARTED = "scene_started"
SCENE_ENDED = "scene_ended"
SCENE_DELETED = "scene_deleted"
POST_APPENDED = "post_appended"
PC_POST_APPENDED = "pc_post_appended"
ADVANCE_REQUESTED = "advance_requested"
ADVANCE_DISABLED = "advance_disabled"
ADVANCE_ENABLED = "advance_enabled"
RUNNING_SUMMARY_UPDATED = "running_summary_updated"
RUNNING_SUMMARY_DUE = "running_summary_due"
THREAD_INTRODUCED = "thread_introduced"
THREAD_PAID_OFF = "thread_paid_off"
SCENE_FILE_CHANGED = "scene_file_changed"
POST_EDITED = "post_edited"
POST_DELETED = "post_deleted"


Handler = Callable[[SceneEvent], Awaitable[None]]


class EventBus(Protocol):
    async def emit(self, event: SceneEvent) -> None: ...


WILDCARD = "*"


class _Subscription:
    """Handle returned by :meth:`InMemoryEventBus.subscribe`."""

    def __init__(self, bus: InMemoryEventBus, event_type: str, handler: Handler) -> None:
        self._bus = bus
        self._event_type = event_type
        self._handler = handler

    def unsubscribe(self) -> None:
        self._bus._remove(self._event_type, self._handler)


class InMemoryEventBus:
    """Trivial bus for tests and bootstrapping.

    Supports both typed subscription (``subscribe(event_type, handler)``,
    matching :class:`grimoire.event_bus.EventBus`) and the legacy
    ``subscribe(handler)`` form which receives every event.
    """

    def __init__(self) -> None:
        self.events: list[SceneEvent] = []
        self._by_type: dict[str, list[Handler]] = {}

    async def emit(self, event: SceneEvent) -> None:
        self.events.append(event)
        for handler in (*self._by_type.get(event.type, ()), *self._by_type.get(WILDCARD, ())):
            await handler(event)

    def subscribe(self, *args):
        """Subscribe to events.

        - ``subscribe(handler)`` — legacy: handler receives every event.
        - ``subscribe(event_type, handler)`` — typed: handler receives only
          events matching ``event_type`` (or every event when
          ``event_type == "*"``).
        """
        if len(args) == 1:
            handler = args[0]
            self._by_type.setdefault(WILDCARD, []).append(handler)
            return _Subscription(self, WILDCARD, handler)
        event_type, handler = args
        self._by_type.setdefault(event_type, []).append(handler)
        return _Subscription(self, event_type, handler)

    def _remove(self, event_type: str, handler: Handler) -> None:
        import contextlib

        bucket = self._by_type.get(event_type)
        if not bucket:
            return
        with contextlib.suppress(ValueError):
            bucket.remove(handler)

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
POST_APPENDED = "post_appended"
PC_POST_APPENDED = "pc_post_appended"
ADVANCE_REQUESTED = "advance_requested"
ADVANCE_DISABLED = "advance_disabled"
ADVANCE_ENABLED = "advance_enabled"
RUNNING_SUMMARY_UPDATED = "running_summary_updated"
THREAD_INTRODUCED = "thread_introduced"
THREAD_PAID_OFF = "thread_paid_off"
SCENE_FILE_CHANGED = "scene_file_changed"
POST_EDITED = "post_edited"
POST_DELETED = "post_deleted"


Handler = Callable[[SceneEvent], Awaitable[None]]


class EventBus(Protocol):
    async def emit(self, event: SceneEvent) -> None: ...


class InMemoryEventBus:
    """Trivial bus for tests and bootstrapping."""

    def __init__(self) -> None:
        self.events: list[SceneEvent] = []
        self._handlers: list[Handler] = []

    async def emit(self, event: SceneEvent) -> None:
        self.events.append(event)
        for handler in self._handlers:
            await handler(event)

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

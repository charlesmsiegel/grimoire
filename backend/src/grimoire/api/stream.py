"""Per-campaign WebSocket connection manager + event bus bridge.

The Orchestrator's ``ws_push`` callback (see spec 01) sends streaming token
chunks and turn-lifecycle messages here; this module fans them out to every
WebSocket subscribed to that campaign. We also subscribe to the in-process
event bus and forward selected event types as the protocol messages defined in
spec 14 §Backend contract.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from grimoire.event_bus import Event, EventBus, Subscription

logger = logging.getLogger(__name__)


# Event types that should be forwarded to subscribed WebSocket clients. The
# orchestrator already pushes ``token`` / ``turn_*`` directly via ``ws_push``;
# this list covers the side-channel events (drift, contradictions, images, ...).
_FORWARDED_EVENTS: tuple[str, ...] = (
    "drift_detected",
    "contradiction_detected",
    "review_item_added",
    "npc_tick_complete",
    "scene_started",
    "scene_ended",
    "library_file_changed",
    "campaign_file_changed",
    "scene_file_changed",
    "sheet_file_changed",
    "library_ref_upgraded",
    "image_ready",
    "imagegen_job_queued",
    "imagegen_job_started",
    "imagegen_progress",
    "imagegen_job_failed",
    "imagegen_backend_health_changed",
    "imagegen_download_progress",
    "imagegen_warning",
    "pc_post_appended",
    "post_appended",
    "advance_requested",
    "advance_disabled",
    "advance_enabled",
    "time_advanced",
    "fact_recorded",
    "commitment_created",
    "commitment_paid_off",
    "weather_changed",
    "deltas_extracted",
    "turn_complete",
    "mechanics_event",
    "thread_opened",
    "thread_closed",
)


@dataclass
class _CampaignChannel:
    sockets: set[WebSocket] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class StreamManager:
    """Per-campaign WebSocket fanout.

    Use :meth:`connect` from a WebSocket route handler to register a client and
    :meth:`disconnect` when it goes away. :meth:`push` is the callback the
    Orchestrator wires in as its ``ws_push``; it broadcasts a message to every
    socket currently subscribed to that campaign.
    """

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._channels: dict[str, _CampaignChannel] = {}
        self._bus = event_bus
        self._bus_subs: list[Subscription] = []
        if event_bus is not None:
            self._wire_event_bus(event_bus)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def _wire_event_bus(self, bus: EventBus) -> None:
        for event_type in _FORWARDED_EVENTS:
            self._bus_subs.append(bus.subscribe(event_type, self._on_event))

    async def aclose(self) -> None:
        for sub in self._bus_subs:
            sub.unsubscribe()
        self._bus_subs.clear()
        # Cooperative shutdown of any remaining sockets.
        channels = list(self._channels.values())
        self._channels.clear()
        for channel in channels:
            for ws in list(channel.sockets):
                with contextlib.suppress(Exception):
                    await ws.close()

    # ------------------------------------------------------------------ #
    # Connections
    # ------------------------------------------------------------------ #

    async def connect(self, campaign_id: str, websocket: WebSocket) -> None:
        """Accept the socket and register it for ``campaign_id``."""
        await websocket.accept()
        channel = self._channels.setdefault(campaign_id, _CampaignChannel())
        async with channel.lock:
            channel.sockets.add(websocket)

    async def disconnect(self, campaign_id: str, websocket: WebSocket) -> None:
        channel = self._channels.get(campaign_id)
        if channel is None:
            return
        async with channel.lock:
            channel.sockets.discard(websocket)
            if not channel.sockets:
                self._channels.pop(campaign_id, None)

    def subscriber_count(self, campaign_id: str) -> int:
        channel = self._channels.get(campaign_id)
        return 0 if channel is None else len(channel.sockets)

    def campaigns(self) -> Iterable[str]:
        return tuple(self._channels.keys())

    # ------------------------------------------------------------------ #
    # Push
    # ------------------------------------------------------------------ #

    async def push(self, campaign_id: str, message: dict[str, Any]) -> None:
        """Fanout a message to all sockets subscribed to ``campaign_id``."""
        channel = self._channels.get(campaign_id)
        if channel is None or not channel.sockets:
            return
        async with channel.lock:
            targets = list(channel.sockets)
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception as exc:
                logger.debug("ws send failed: %s", exc)
                dead.append(ws)
        if dead:
            async with channel.lock:
                for ws in dead:
                    channel.sockets.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Push a message to every connected campaign channel."""
        for campaign_id in tuple(self._channels.keys()):
            await self.push(campaign_id, message)

    # ------------------------------------------------------------------ #
    # Event bus bridge
    # ------------------------------------------------------------------ #

    async def _on_event(self, event: Event) -> None:
        payload = dict(event.payload or {})
        campaign_id = payload.get("campaign_id")
        message = {"type": event.type, **payload}
        if campaign_id:
            await self.push(str(campaign_id), message)
        else:
            await self.broadcast(message)


__all__ = ["StreamManager"]

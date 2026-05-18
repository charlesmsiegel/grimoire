"""Glue: subscribe to Orchestrator + Scene Manager events and ask the
ImageGen service to queue background image jobs based on the
per-campaign TriggerConfig (§1 of imagegen remaining-design).

This module is intentionally thin: every decision goes through the pure
``should_illustrate`` function so policy stays testable in isolation.
"""

from __future__ import annotations

import logging
from typing import Any

from grimoire.event_bus import Event, EventBus, Subscription
from grimoire.imagegen.service import ImageGenService, should_illustrate

logger = logging.getLogger(__name__)


class ImageGenIntegration:
    """Bridge from event bus → ImageGenService.queue_generation."""

    def __init__(self, service: ImageGenService, bus: EventBus) -> None:
        self._svc = service
        self._bus = bus
        self._subs: list[Subscription] = []
        # Scene-level latches: "fire on_scene_open / on_new_location / etc.
        # on the NEXT turn_complete for this scene id". Cleared after use.
        self._pending_flags: dict[str, set[str]] = {}

    def start(self) -> None:
        if self._subs:
            return
        self._subs = [
            self._bus.subscribe("turn_complete", self._on_turn_complete),
            self._bus.subscribe("scene_started", self._on_scene_started),
        ]

    def stop(self) -> None:
        for sub in self._subs:
            sub.unsubscribe()
        self._subs.clear()
        self._pending_flags.clear()

    async def _on_scene_started(self, event: Event) -> None:
        scene_id = event.payload.get("scene_id")
        if not scene_id:
            return
        flags = self._pending_flags.setdefault(str(scene_id), set())
        flags.add("on_scene_open")

    async def _on_turn_complete(self, event: Event) -> None:
        payload: dict[str, Any] = event.payload or {}
        campaign_id = payload.get("campaign_id")
        scene_id = payload.get("scene_id")
        if not campaign_id:
            return
        try:
            cfg = await self._svc.get_trigger_config(str(campaign_id))
        except Exception:
            logger.warning("imagegen fan-out failed loading trigger config", exc_info=True)
            return

        flags = self._pending_flags.pop(str(scene_id), set()) if scene_id else set()
        is_scene_open = "on_scene_open" in flags

        if not should_illustrate(
            cfg,
            is_scene_open=is_scene_open,
            # Scene Manager doesn't currently emit "new_location" /
            # "new_character_appearance" signals; producing them from
            # scene state is a follow-up.
            is_new_location=False,
            is_new_character=False,
            is_in_combat=False,
            post_index=None,
        ):
            return

        try:
            await self._svc.queue_generation(
                campaign_id=str(campaign_id),
                scene_id=str(scene_id) if scene_id else None,
                post_id=payload.get("post_id"),
            )
        except Exception:
            logger.warning("imagegen fan-out failed queuing job", exc_info=True)

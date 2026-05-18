"""Glue: subscribe to ``turn_complete`` and sample character drift checks.

Mirrors :mod:`grimoire.imagegen.integration` — a thin bridge that turns
event-bus signals into ``CharactersService.maybe_check_drift`` calls for
present characters, sampled at the per-campaign rate
``BackgroundWorkConfig.drift_check_sampling``. The cadence gate inside
``maybe_check_drift`` is the source of truth; this module just decides
*which* characters to ask about and never blocks the orchestrator's turn
loop.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from grimoire.event_bus import Event, EventBus, Subscription
from grimoire.orchestrator.config import BackgroundWorkConfig

logger = logging.getLogger(__name__)


class CharactersIntegration:
    """Bridge from event bus → CharactersService.maybe_check_drift."""

    def __init__(
        self,
        characters_service: Any,
        scene_manager: Any,
        bus: EventBus,
        config: BackgroundWorkConfig | None = None,
        *,
        rng: random.Random | None = None,
    ) -> None:
        self._chars = characters_service
        self._scenes = scene_manager
        self._bus = bus
        self._config = config or BackgroundWorkConfig()
        self._rng = rng or random.Random()
        self._subs: list[Subscription] = []
        self._tasks: set[asyncio.Task] = set()

    def start(self) -> None:
        if self._subs:
            return
        self._subs = [self._bus.subscribe("turn_complete", self._on_turn_complete)]

    def stop(self) -> None:
        for sub in self._subs:
            sub.unsubscribe()
        self._subs.clear()
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    async def _on_turn_complete(self, event: Event) -> None:
        payload: dict[str, Any] = event.payload or {}
        campaign_id = payload.get("campaign_id")
        scene_id = payload.get("scene_id")
        if not campaign_id or not scene_id:
            return
        try:
            scene = await self._scenes.get_scene(str(scene_id))
        except Exception:
            logger.debug("characters integration: get_scene failed", exc_info=True)
            return
        sampling = self._config.drift_check_sampling
        for ref in list(getattr(scene, "present_character_refs", []) or []):
            if sampling <= 0.0:
                continue
            if sampling < 1.0 and self._rng.random() >= sampling:
                continue
            task = asyncio.create_task(self._run_drift_check(ref, str(campaign_id)))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_drift_check(self, ref: str, campaign_id: str) -> None:
        try:
            await self._chars.maybe_check_drift(ref, campaign_id)
        except Exception:
            logger.warning("drift check failed for %s in %s", ref, campaign_id, exc_info=True)


__all__ = ["CharactersIntegration"]

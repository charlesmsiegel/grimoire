"""Scene-end and time-skip reset triggers for transient state.

Subscribes to the event bus for ``scene_ended`` and ``time_advanced``;
expires the relevant scene-scoped or time-skip-default fields by walking
the decay table.

The trigger module is independent of the service so it can be wired up
optionally — services without an event bus simply skip ``attach_triggers``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from grimoire import events
from grimoire.transient_state.decay import DecaySpec
from grimoire.transient_state.service import TransientStateService
from grimoire.types.transient import EntityKind

# Fields that default-reset on a >= 24h time skip per spec.
DEFAULT_TIME_SKIP_RESET_FIELDS: dict[EntityKind, frozenset[str]] = {
    EntityKind.CHARACTER: frozenset({"mood", "intent", "posture", "current_action"}),
}

# Seconds threshold for time-skip reset (default 24h).
DEFAULT_TIME_SKIP_THRESHOLD_SECONDS = 86_400


@dataclass
class TransientResetTriggers:
    """Reset handlers subscribed to the event bus.

    On ``scene_ended``: every (kind, field) whose DecaySpec has scene_scope=True
    is cleared for the closing scene's location and all present characters.

    On ``time_advanced``: when the elapsed in-game seconds reaches the
    configured threshold, default time-skip fields are cleared for all
    affected characters.
    """

    service: TransientStateService
    time_skip_threshold_seconds: int = DEFAULT_TIME_SKIP_THRESHOLD_SECONDS

    def _scene_scoped_fields(self, kind: EntityKind) -> list[str]:
        table = self.service.config.decay_table.get(kind, {})
        return [
            name for name, spec in table.items() if isinstance(spec, DecaySpec) and spec.scene_scope
        ]

    async def on_scene_ended(self, event: Any) -> None:
        payload = self._payload(event)
        campaign_id = payload.get("campaign_id")
        if not campaign_id:
            return
        location_ref = payload.get("location_ref")
        scene_id = payload.get("scene_id")
        character_refs = list(payload.get("present_character_refs") or [])

        char_fields = self._scene_scoped_fields(EntityKind.CHARACTER)
        for ref in character_refs:
            for field in char_fields:
                await self.service.clear(
                    campaign_id,
                    EntityKind.CHARACTER,
                    ref,
                    field=field,
                    reason="scene_ended",
                )

        if location_ref:
            for field in self._scene_scoped_fields(EntityKind.LOCATION):
                await self.service.clear(
                    campaign_id,
                    EntityKind.LOCATION,
                    location_ref,
                    field=field,
                    reason="scene_ended",
                )

        if scene_id:
            for field in self._scene_scoped_fields(EntityKind.SCENE):
                await self.service.clear(
                    campaign_id,
                    EntityKind.SCENE,
                    scene_id,
                    field=field,
                    reason="scene_ended",
                )

    async def on_time_advanced(self, event: Any) -> None:
        payload = self._payload(event)
        campaign_id = payload.get("campaign_id")
        if not campaign_id:
            return
        elapsed = int(payload.get("elapsed_seconds") or 0)
        if elapsed < self.time_skip_threshold_seconds:
            return
        character_refs = list(payload.get("character_refs") or [])
        reset_fields = DEFAULT_TIME_SKIP_RESET_FIELDS.get(EntityKind.CHARACTER, frozenset())
        for ref in character_refs:
            for field in reset_fields:
                await self.service.clear(
                    campaign_id,
                    EntityKind.CHARACTER,
                    ref,
                    field=field,
                    reason="time_skip",
                )

    @staticmethod
    def _payload(event: Any) -> dict[str, Any]:
        if event is None:
            return {}
        payload = getattr(event, "payload", None)
        if isinstance(payload, dict):
            return payload
        if isinstance(event, dict):
            return event
        return {}


def attach_triggers(
    service: TransientStateService,
    event_bus: Any,
    *,
    time_skip_threshold_seconds: int = DEFAULT_TIME_SKIP_THRESHOLD_SECONDS,
) -> TransientResetTriggers:
    """Wire scene_ended + time_advanced handlers onto the provided event bus.

    Returns the trigger object so callers can detach via the returned
    subscriptions later (the EventBus' subscribe returns a Subscription
    handle; we keep one per event for symmetry).
    """
    triggers = TransientResetTriggers(
        service=service,
        time_skip_threshold_seconds=time_skip_threshold_seconds,
    )
    event_bus.subscribe(events.SCENE_ENDED, triggers.on_scene_ended)
    event_bus.subscribe(events.TIME_ADVANCED, triggers.on_time_advanced)
    return triggers

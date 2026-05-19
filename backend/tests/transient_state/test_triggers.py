"""Scene-end and time-skip reset triggers."""

from __future__ import annotations

from grimoire.event_bus import Event, EventBus
from grimoire.transient_state import TransientStateService
from grimoire.transient_state.triggers import attach_triggers
from grimoire.types.transient import EntityKind, Provenance


async def test_scene_end_expires_scene_scoped_location_field(
    service: TransientStateService, seeded_campaign: str
):
    bus = EventBus()
    attach_triggers(service, bus)
    await service.set(
        seeded_campaign,
        EntityKind.LOCATION,
        "loc_pub",
        "ambient_mood",
        "tense",
        provenance=Provenance.EXTRACTOR_AUTO,
    )
    await bus.emit(
        Event(
            type="scene_ended",
            payload={
                "campaign_id": seeded_campaign,
                "scene_id": "s_1",
                "location_ref": "loc_pub",
                "present_character_refs": [],
            },
        )
    )
    v = await service.get(seeded_campaign, EntityKind.LOCATION, "loc_pub", "ambient_mood")
    assert v is None


async def test_scene_end_expires_present_character_intent(
    service: TransientStateService, seeded_campaign: str
):
    bus = EventBus()
    attach_triggers(service, bus)
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "char_x",
        "intent",
        "leave town",
        provenance=Provenance.EXTRACTOR_AUTO,
    )
    await bus.emit(
        Event(
            type="scene_ended",
            payload={
                "campaign_id": seeded_campaign,
                "scene_id": "s_1",
                "location_ref": None,
                "present_character_refs": ["char_x"],
            },
        )
    )
    v = await service.get(seeded_campaign, EntityKind.CHARACTER, "char_x", "intent")
    assert v is None


async def test_scene_end_preserves_persistent_faction_state(
    service: TransientStateService, seeded_campaign: str
):
    bus = EventBus()
    attach_triggers(service, bus)
    await service.set(
        seeded_campaign,
        EntityKind.FACTION,
        "f_camarilla",
        "alert_level",
        "raised",
        provenance=Provenance.USER_EDIT,
    )
    await bus.emit(
        Event(
            type="scene_ended",
            payload={
                "campaign_id": seeded_campaign,
                "scene_id": "s_1",
                "location_ref": None,
                "present_character_refs": [],
            },
        )
    )
    v = await service.get(seeded_campaign, EntityKind.FACTION, "f_camarilla", "alert_level")
    assert v is not None
    assert v.value == "raised"


async def test_scene_end_preserves_character_mood_outside_scope(
    service: TransientStateService, seeded_campaign: str
):
    """mood has decay (posts/in_game) but is NOT scene_scope, so scene-end keeps it."""
    bus = EventBus()
    attach_triggers(service, bus)
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "char_x",
        "mood",
        "guarded",
        provenance=Provenance.EXTRACTOR_AUTO,
    )
    await bus.emit(
        Event(
            type="scene_ended",
            payload={
                "campaign_id": seeded_campaign,
                "scene_id": "s_1",
                "present_character_refs": ["char_x"],
            },
        )
    )
    v = await service.get(seeded_campaign, EntityKind.CHARACTER, "char_x", "mood")
    assert v is not None
    assert v.value == "guarded"


async def test_time_skip_24h_resets_mood_and_intent(
    service: TransientStateService, seeded_campaign: str
):
    bus = EventBus()
    attach_triggers(service, bus)
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "char_x",
        "mood",
        "guarded",
        provenance=Provenance.EXTRACTOR_AUTO,
    )
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "char_x",
        "intent",
        "wait",
        provenance=Provenance.EXTRACTOR_AUTO,
    )
    await bus.emit(
        Event(
            type="time_advanced",
            payload={
                "campaign_id": seeded_campaign,
                "elapsed_seconds": 86_400,
                "character_refs": ["char_x"],
            },
        )
    )
    assert await service.get(seeded_campaign, EntityKind.CHARACTER, "char_x", "mood") is None
    assert await service.get(seeded_campaign, EntityKind.CHARACTER, "char_x", "intent") is None


async def test_time_skip_short_advance_does_not_reset(
    service: TransientStateService, seeded_campaign: str
):
    bus = EventBus()
    attach_triggers(service, bus)
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "char_x",
        "mood",
        "guarded",
        provenance=Provenance.EXTRACTOR_AUTO,
    )
    await bus.emit(
        Event(
            type="time_advanced",
            payload={
                "campaign_id": seeded_campaign,
                "elapsed_seconds": 3600,  # 1h, well below threshold
                "character_refs": ["char_x"],
            },
        )
    )
    v = await service.get(seeded_campaign, EntityKind.CHARACTER, "char_x", "mood")
    assert v is not None

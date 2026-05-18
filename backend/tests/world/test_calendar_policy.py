"""§6 multi-world calendar policy: pick | merge_warn | error."""

from __future__ import annotations

import logging

import pytest

from grimoire.world import CompositionPolicyConfig, WorldConfig
from grimoire.world.errors import CompositionError


async def _seed_two_worlds_with_different_calendars(store, library) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world(
        "w1",
        {
            "id": "w1",
            "name": "W1",
            "calendar": {
                "months": [{"name": "Frostmoon", "days": 30}],
                "days_per_week": 7,
                "week_day_names": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            },
        },
    )
    await library.create_world(
        "w2",
        {
            "id": "w2",
            "name": "W2",
            "calendar": {
                "months": [{"name": "Firewane", "days": 28}],
                "days_per_week": 10,
                "week_day_names": ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"],
            },
        },
    )
    await store.upsert_world_ref(
        campaign_id="camp-1",
        world_id="w1",
        priority=1,
        include=None,
        track_latest=True,
        bound_at_version=None,
    )
    await store.upsert_world_ref(
        campaign_id="camp-1",
        world_id="w2",
        priority=2,
        include=None,
        track_latest=True,
        bound_at_version=None,
    )


async def test_pick_policy_returns_highest_priority(store, library, world) -> None:
    await _seed_two_worlds_with_different_calendars(store, library)
    cal = await world.calendar_for_campaign("camp-1")
    assert cal.world_id == "w1"  # priority 1 wins


async def test_merge_warn_logs_warning(store, library, world, caplog) -> None:
    await _seed_two_worlds_with_different_calendars(store, library)
    world.config = WorldConfig(
        composition=CompositionPolicyConfig(multiple_calendars_policy="merge_warn")
    )
    with caplog.at_level(logging.WARNING, logger="grimoire.world.service"):
        cal = await world.calendar_for_campaign("camp-1")
    assert cal.world_id == "w1"
    assert any("multiple worlds declare calendars" in record.message for record in caplog.records)


async def test_error_policy_raises(store, library, world) -> None:
    await _seed_two_worlds_with_different_calendars(store, library)
    world.config = WorldConfig(
        composition=CompositionPolicyConfig(multiple_calendars_policy="error")
    )
    with pytest.raises(CompositionError, match="conflicting calendars"):
        await world.calendar_for_campaign("camp-1")


async def test_error_policy_silent_when_only_one_calendar(store, library, world) -> None:
    """When a campaign has multiple world refs but only one declares a calendar,
    the policy should not fire."""
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world(
        "w1",
        {
            "id": "w1",
            "name": "W1",
            "calendar": {"months": [{"name": "M1", "days": 30}]},
        },
    )
    await library.create_world("w2", {"id": "w2", "name": "W2"})
    await store.upsert_world_ref(
        campaign_id="camp-1",
        world_id="w1",
        priority=1,
        include=None,
        track_latest=True,
        bound_at_version=None,
    )
    await store.upsert_world_ref(
        campaign_id="camp-1",
        world_id="w2",
        priority=2,
        include=None,
        track_latest=True,
        bound_at_version=None,
    )
    world.config = WorldConfig(
        composition=CompositionPolicyConfig(multiple_calendars_policy="error")
    )
    cal = await world.calendar_for_campaign("camp-1")
    assert cal.world_id == "w1"

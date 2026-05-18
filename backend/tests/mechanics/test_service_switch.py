"""Tests for §6: mid-campaign mechanics module switching."""

from __future__ import annotations

from pathlib import Path

from grimoire.event_bus import Event, EventBus
from grimoire.mechanics import MechanicsConfig, MechanicsService

from .conftest import write_module


async def test_switch_module_records_history(
    service: MechanicsService, mechanics_root: Path
) -> None:
    write_module(mechanics_root, "alpha")
    write_module(mechanics_root, "beta")
    await service.rescan()
    await service._state_store.upsert_campaign(
        campaign_id="c-sw", name="C", mechanics_module="alpha"
    )

    result = await service.switch_module("c-sw", "beta")
    assert result.previous == "alpha"
    assert result.current == "beta"
    assert result.missing_sheets == []

    # History row was written.
    rows = await service._state_store.db.fetchall(
        "SELECT * FROM campaign_mechanics_history WHERE campaign_id = ?",
        ("c-sw",),
    )
    assert len(rows) == 1
    assert rows[0]["mechanics_module"] == "beta"
    assert rows[0]["switched_from"] == "alpha"

    # previous_mechanics_modules surfaces both
    prev = await service._state_store.previous_mechanics_modules("c-sw")
    assert set(prev) == {"alpha", "beta"}


async def test_switch_to_null_is_valid(service: MechanicsService, mechanics_root: Path) -> None:
    write_module(mechanics_root, "alpha")
    await service.rescan()
    await service._state_store.upsert_campaign(
        campaign_id="c-sw2", name="C", mechanics_module="alpha"
    )
    result = await service.switch_module("c-sw2", None)
    assert result.previous == "alpha"
    assert result.current is None
    assert result.missing_sheets == []


async def test_switch_detects_missing_sheets(store, mechanics_root: Path) -> None:
    write_module(mechanics_root, "alpha")
    write_module(mechanics_root, "beta")
    config = MechanicsConfig(root=mechanics_root)
    service = MechanicsService(config=config, state_store=store)
    await service.rescan()

    await store.upsert_campaign(campaign_id="c-sw3", name="C", mechanics_module="alpha")
    await store.add_pc(
        campaign_id="c-sw3",
        character_ref="character:hero",
        display_name="Hero",
        owner="local",
    )
    # Write a sheet under the old module.
    await store.write_sheet(
        campaign_id="c-sw3",
        kind="character",
        entity_id="hero",
        mechanics_id="alpha",
        sheet={"name": "Hero"},
        source="user",
    )

    result = await service.switch_module("c-sw3", "beta")
    assert result.previous == "alpha"
    assert result.current == "beta"
    assert len(result.missing_sheets) == 1
    assert result.missing_sheets[0].entity_id == "hero"
    assert result.missing_sheets[0].character_name == "Hero"


async def test_switch_emits_event(store, mechanics_root: Path) -> None:
    write_module(mechanics_root, "alpha")
    write_module(mechanics_root, "beta")
    bus = EventBus()
    received: list[Event] = []

    async def handle(event: Event) -> None:
        received.append(event)

    bus.subscribe("mechanics_switched", handle)

    config = MechanicsConfig(root=mechanics_root)
    service = MechanicsService(config=config, state_store=store, event_bus=bus)
    await service.rescan()
    await store.upsert_campaign(campaign_id="c-sw4", name="C", mechanics_module="alpha")

    await service.switch_module("c-sw4", "beta")
    # Allow the emit to settle.
    import asyncio

    await asyncio.sleep(0)
    assert any(e.payload.get("current") == "beta" for e in received)

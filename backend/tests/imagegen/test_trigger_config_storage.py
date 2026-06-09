"""Per-campaign trigger config persistence (§6)."""

from __future__ import annotations

import logging

import pytest

from grimoire.imagegen import TriggerConfig
from grimoire.state_store import StateStore


async def test_get_trigger_config_returns_defaults_for_unknown_campaign(service) -> None:
    svc, _ = service
    cfg = await svc.get_trigger_config("camp-1")
    assert cfg == TriggerConfig()


async def test_set_then_get_trigger_config_round_trip(service) -> None:
    svc, _ = service
    new = TriggerConfig(
        mode="every_n_posts",
        every_n=3,
        on_scene_open=False,
        on_new_location=False,
        on_new_character_appearance=False,
        auto_during_combat=True,
    )
    await svc.set_trigger_config("camp-1", new)
    got = await svc.get_trigger_config("camp-1")
    assert got == new


async def test_set_trigger_config_does_not_clobber_unrelated_imagegen_config(
    service,
) -> None:
    svc, _ = service
    await svc.set_active_backend("camp-1", "diffusers-memory")
    await svc.set_trigger_config("camp-1", TriggerConfig(mode="per_post"))
    info = await svc.active_backend("camp-1")
    assert info.id == "diffusers-memory"


async def test_corrupt_imagegen_config_logs_warning_and_returns_defaults(
    service,
    store: StateStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#587: a corrupt config row degrades to defaults, but must say so —
    a silent reset is indistinguishable from "never configured"."""
    svc, _ = service
    await store.db.execute(
        "UPDATE campaigns SET imagegen_config = ? WHERE id = ?",
        ("{not valid json", "camp-1"),
    )
    with caplog.at_level(logging.WARNING, logger="grimoire.imagegen.service"):
        cfg = await svc.get_trigger_config("camp-1")
    assert cfg == TriggerConfig()
    assert any(
        "corrupt imagegen_config" in r.message and "camp-1" in r.message for r in caplog.records
    )


async def test_non_object_imagegen_config_logs_warning_and_returns_defaults(
    service,
    store: StateStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    svc, _ = service
    await store.db.execute(
        "UPDATE campaigns SET imagegen_config = ? WHERE id = ?",
        ('["valid", "json", "wrong", "shape"]', "camp-1"),
    )
    with caplog.at_level(logging.WARNING, logger="grimoire.imagegen.service"):
        cfg = await svc.get_trigger_config("camp-1")
    assert cfg == TriggerConfig()
    assert any("not an object" in r.message and "camp-1" in r.message for r in caplog.records)

"""Per-campaign trigger config persistence (§6)."""

from __future__ import annotations

from grimoire.imagegen import TriggerConfig


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

"""Per-campaign active + fallback backend persistence (§6)."""

from __future__ import annotations

from grimoire.event_bus import EventBus
from grimoire.imagegen import (
    BackendRegistry,
    ImageGenService,
    InMemoryDiffusersBackend,
)


async def _new_service(store, *, default_backend_id="diffusers-memory"):
    reg = BackendRegistry()
    reg.register(InMemoryDiffusersBackend())
    return ImageGenService(
        store=store,
        registry=reg,
        default_backend_id=default_backend_id,
        event_bus=EventBus(),
    )


async def test_set_active_backend_survives_service_restart(store) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    svc = await _new_service(store)
    await svc.set_active_backend("camp-1", "diffusers-memory")
    await svc.aclose()

    svc2 = await _new_service(store, default_backend_id=None)
    try:
        info = await svc2.active_backend("camp-1")
        assert info.id == "diffusers-memory"
    finally:
        await svc2.aclose()


async def test_set_fallback_backend_round_trips(store) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    svc = await _new_service(store)
    try:
        await svc.set_fallback_backend("camp-1", "diffusers-memory")
        assert await svc.get_fallback_backend("camp-1") == "diffusers-memory"
    finally:
        await svc.aclose()


async def test_set_fallback_backend_rejects_unknown(store) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    svc = await _new_service(store)
    try:
        import pytest

        with pytest.raises(KeyError):
            await svc.set_fallback_backend("camp-1", "nope")
    finally:
        await svc.aclose()


async def test_set_fallback_backend_accepts_none_to_clear(store) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    svc = await _new_service(store)
    try:
        await svc.set_fallback_backend("camp-1", "diffusers-memory")
        await svc.set_fallback_backend("camp-1", None)
        assert await svc.get_fallback_backend("camp-1") is None
    finally:
        await svc.aclose()

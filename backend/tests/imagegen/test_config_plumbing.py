"""ImageGenConfig flows into runtime behavior."""

from __future__ import annotations

import pytest

from grimoire.event_bus import EventBus
from grimoire.imagegen import (
    BackendRegistry,
    ImageGenConfig,
    ImageGenService,
    InMemoryDiffusersBackend,
)
from grimoire.state_store import StateStore
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db
from grimoire.types.imagegen import GenerationRequest


@pytest.fixture
async def store_with_campaign(tmp_path):
    db = Database(stamp_migrated_db(tmp_path / "c.sqlite"), pool_size=1)
    await db.connect()
    data = tmp_path / "data"
    data.mkdir()
    s = StateStore(db, data)
    await s.upsert_campaign(campaign_id="camp-1", name="t")
    try:
        yield s
    finally:
        await db.close()


async def test_service_accepts_imagegen_config(store_with_campaign) -> None:
    reg = BackendRegistry()
    reg.register(InMemoryDiffusersBackend())
    cfg = ImageGenConfig(default_backend="diffusers-memory", caching_enabled=False)
    svc = ImageGenService(
        store=store_with_campaign,
        registry=reg,
        config=cfg,
        event_bus=EventBus(),
    )
    try:
        info = await svc.active_backend("camp-1")
        assert info.id == "diffusers-memory"
        assert svc.config.caching_enabled is False
    finally:
        await svc.aclose()


async def test_caching_disabled_skips_cache_store(store_with_campaign) -> None:
    reg = BackendRegistry()
    reg.register(InMemoryDiffusersBackend())
    cfg = ImageGenConfig(default_backend="diffusers-memory", caching_enabled=False)
    svc = ImageGenService(store=store_with_campaign, registry=reg, config=cfg, event_bus=EventBus())
    try:
        await svc.generate_sync("camp-1", GenerationRequest(prompt="x", width=8, height=8, seed=42))
        assert svc._cache == {}
    finally:
        await svc.aclose()

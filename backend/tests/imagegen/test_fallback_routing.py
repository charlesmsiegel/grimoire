"""§2 Health-aware fallback routing in queue_generation."""

from __future__ import annotations

from grimoire.event_bus import EventBus
from grimoire.imagegen import BackendRegistry, ImageGenService, InMemoryDiffusersBackend
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.imagegen import BackendCapabilities


class _UnhealthyBackend:
    id = "broken"
    name = "Broken"
    capabilities = BackendCapabilities()

    async def generate(self, request, *, progress=None, cancel_token=None):
        raise RuntimeError("backend is down")

    async def health_check(self):
        return HealthStatus(level=HealthLevel.UNHEALTHY, target_id=self.id, message="down")


async def test_queue_routes_to_fallback_when_active_is_unhealthy(tmp_path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    db = Database(tmp_path / "x.sqlite", pool_size=1)
    await db.connect()
    await apply_migrations(db)
    s = StateStore(db, data)
    await s.upsert_campaign(campaign_id="camp-1", name="t")
    reg = BackendRegistry()
    reg.register(_UnhealthyBackend())
    reg.register(InMemoryDiffusersBackend())
    svc = ImageGenService(store=s, registry=reg, default_backend_id="broken", event_bus=EventBus())
    try:
        await svc.set_fallback_backend("camp-1", "diffusers-memory")
        # Refresh health cache so the routing branch sees UNHEALTHY.
        await svc.health_check("broken")
        job_id = await svc.queue_generation(
            campaign_id="camp-1", scene_id=None, post_id=None, request=None
        )
        jobs = await svc.list_jobs("camp-1")
        target = next(j for j in jobs if j.id == job_id)
        assert target.backend == "diffusers-memory"
    finally:
        await svc.aclose()
        await db.close()


async def test_warning_emitted_when_no_fallback_configured(tmp_path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    db = Database(tmp_path / "x.sqlite", pool_size=1)
    await db.connect()
    await apply_migrations(db)
    s = StateStore(db, data)
    await s.upsert_campaign(campaign_id="camp-1", name="t")
    reg = BackendRegistry()
    reg.register(_UnhealthyBackend())
    bus = EventBus()
    warnings: list[dict] = []
    bus.subscribe("imagegen_warning", lambda ev: warnings.append(dict(ev.payload)))
    svc = ImageGenService(store=s, registry=reg, default_backend_id="broken", event_bus=bus)
    try:
        await svc.health_check("broken")
        await svc.queue_generation(campaign_id="camp-1", scene_id=None, post_id=None, request=None)
        # Drain pending tasks so the emit fires before we assert.
        import asyncio

        await asyncio.sleep(0.05)
        assert any("unhealthy" in w.get("reason", "") for w in warnings)
    finally:
        await svc.aclose()
        await db.close()

"""§3 Progress events from backends."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from grimoire.event_bus import EventBus
from grimoire.imagegen import BackendRegistry, ImageGenService
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.imagegen import (
    BackendCapabilities,
    GenerationRequest,
    GenerationResult,
    JobStatus,
)


class _ProgressEmittingBackend:
    id = "progress-test"
    name = "Progress Test"
    capabilities = BackendCapabilities()

    async def generate(
        self, request: GenerationRequest, *, progress=None, cancel_token=None
    ) -> GenerationResult:
        if progress is not None:
            await progress({"step": 1, "total_steps": 3, "eta_ms": 90})
            await progress({"step": 2, "total_steps": 3, "eta_ms": 60})
            await progress({"step": 3, "total_steps": 3, "eta_ms": 0})
        return GenerationResult(
            image_bytes=b"\x89PNG\r\n\x1a\n",
            thumbnail_bytes=b"\x89PNG\r\n\x1a\n",
            backend=self.id,
            model="x",
            seed=42,
            actual_params={},
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id, message="ok")


@pytest.fixture
async def progress_service(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db = Database(tmp_path / "x.sqlite", pool_size=1)
    await db.connect()
    await apply_migrations(db)
    s = StateStore(db, data)
    await s.upsert_campaign(campaign_id="camp-1", name="t")
    reg = BackendRegistry()
    reg.register(_ProgressEmittingBackend())
    bus = EventBus()
    svc = ImageGenService(store=s, registry=reg, default_backend_id="progress-test", event_bus=bus)
    try:
        yield svc, bus
    finally:
        await svc.aclose()
        await db.close()


async def test_progress_events_emit_on_each_step(progress_service) -> None:
    svc, bus = progress_service
    events: list[dict[str, Any]] = []
    bus.subscribe("imagegen_progress", lambda ev: events.append(dict(ev.payload)))
    job_id = await svc.queue_generation(
        campaign_id="camp-1",
        scene_id=None,
        post_id=None,
        request=GenerationRequest(prompt="x", width=8, height=8, seed=42),
    )
    # Wait for completion
    for _ in range(100):
        await asyncio.sleep(0.02)
        jobs = await svc.list_jobs("camp-1")
        if any(j.id == job_id and j.status == JobStatus.COMPLETE for j in jobs):
            break
    assert len(events) == 3
    assert events[0]["step"] == 1
    assert events[-1]["step"] == 3
    assert all(e["job_id"] == job_id for e in events)
    assert all(e["campaign_id"] == "camp-1" for e in events)

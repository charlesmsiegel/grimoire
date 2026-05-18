"""§4 Cooperative cancellation of running jobs."""

from __future__ import annotations

import asyncio

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


class _SlowBackend:
    id = "slow"
    name = "Slow"
    capabilities = BackendCapabilities()
    cancelled = False

    async def generate(self, request, *, progress=None, cancel_token=None):
        for _ in range(100):
            if cancel_token is not None and cancel_token.is_set():
                _SlowBackend.cancelled = True
                raise asyncio.CancelledError()
            await asyncio.sleep(0.02)
        return GenerationResult(
            image_bytes=b"x",
            thumbnail_bytes=b"x",
            backend=self.id,
            model="m",
            seed=1,
            actual_params={},
        )

    async def health_check(self):
        return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id, message="ok")


@pytest.fixture
async def slow_service(tmp_path):
    _SlowBackend.cancelled = False
    data = tmp_path / "data"
    data.mkdir()
    db = Database(tmp_path / "x.sqlite", pool_size=1)
    await db.connect()
    await apply_migrations(db)
    s = StateStore(db, data)
    await s.upsert_campaign(campaign_id="camp-1", name="t")
    reg = BackendRegistry()
    reg.register(_SlowBackend())
    svc = ImageGenService(store=s, registry=reg, default_backend_id="slow", event_bus=EventBus())
    try:
        yield svc
    finally:
        await svc.aclose()
        await db.close()


async def test_cancel_running_job_sets_token_and_marks_cancelled(slow_service) -> None:
    job_id = await slow_service.queue_generation(
        campaign_id="camp-1",
        scene_id=None,
        post_id=None,
        request=GenerationRequest(prompt="x", width=8, height=8, seed=1),
    )
    # Wait until the job moves to RUNNING
    for _ in range(100):
        await asyncio.sleep(0.02)
        jobs = await slow_service.list_jobs("camp-1")
        if any(j.id == job_id and j.status == JobStatus.RUNNING for j in jobs):
            break
    else:
        raise AssertionError("job never started")

    await slow_service.cancel_job(job_id)
    # Give worker a beat to process cancellation
    target = None
    for _ in range(100):
        await asyncio.sleep(0.02)
        jobs = await slow_service.list_jobs("camp-1")
        target = next(j for j in jobs if j.id == job_id)
        if target.status == JobStatus.CANCELLED and _SlowBackend.cancelled:
            break
    assert _SlowBackend.cancelled is True
    assert target is not None
    assert target.status == JobStatus.CANCELLED


async def test_worker_survives_in_flight_cancellation(slow_service) -> None:
    """After cancelling a running job, the worker should pick up the next one."""
    job1 = await slow_service.queue_generation(
        campaign_id="camp-1",
        scene_id=None,
        post_id=None,
        request=GenerationRequest(prompt="x", width=8, height=8, seed=1),
    )
    for _ in range(100):
        await asyncio.sleep(0.02)
        jobs = await slow_service.list_jobs("camp-1")
        if any(j.id == job1 and j.status == JobStatus.RUNNING for j in jobs):
            break
    await slow_service.cancel_job(job1)
    # Wait briefly so the worker can flush
    for _ in range(50):
        await asyncio.sleep(0.02)
        jobs = await slow_service.list_jobs("camp-1")
        if all(j.status != JobStatus.RUNNING for j in jobs):
            break
    # Queue a second job — should still process. We can't wait for it to
    # COMPLETE because the slow backend takes 2 seconds; just verify it
    # transitions to RUNNING (= worker is alive).
    job2 = await slow_service.queue_generation(
        campaign_id="camp-1",
        scene_id=None,
        post_id=None,
        request=GenerationRequest(prompt="y", width=8, height=8, seed=2),
    )
    saw_running = False
    for _ in range(100):
        await asyncio.sleep(0.02)
        jobs = await slow_service.list_jobs("camp-1")
        if any(j.id == job2 and j.status == JobStatus.RUNNING for j in jobs):
            saw_running = True
            break
    assert saw_running, "worker died after cancellation"
    await slow_service.cancel_job(job2)

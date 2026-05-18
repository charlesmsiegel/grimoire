"""§8 Persistent job queue."""

from __future__ import annotations

import asyncio

import pytest

from grimoire.event_bus import EventBus
from grimoire.imagegen import (
    BackendRegistry,
    ImageGenConfig,
    ImageGenService,
    InMemoryDiffusersBackend,
)
from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.imagegen import (
    BackendCapabilities,
    GenerationRequest,
    GenerationResult,
    JobStatus,
)


class _BlockingBackend:
    """Backend that never completes, so jobs stay queued/running."""

    id = "blocking"
    name = "Blocking"
    capabilities = BackendCapabilities()

    def __init__(self) -> None:
        self._stop = asyncio.Event()

    async def generate(self, request, *, progress=None, cancel_token=None):
        # Block until cancellation
        while True:
            if cancel_token is not None and cancel_token.is_set():
                raise asyncio.CancelledError()
            if self._stop.is_set():
                break
            await asyncio.sleep(0.01)
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


def _new_service(store, *, persist: bool):
    reg = BackendRegistry()
    reg.register(_BlockingBackend())
    cfg = ImageGenConfig(
        default_backend="blocking",
        queue_persist_pending=persist,
    )
    return ImageGenService(
        store=store,
        registry=reg,
        default_backend_id="blocking",
        event_bus=EventBus(),
        config=cfg,
    )


@pytest.fixture
async def persistent_store(store):
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    return store


async def test_queued_job_written_to_imagegen_jobs_table(persistent_store) -> None:
    svc = _new_service(persistent_store, persist=True)
    try:
        job_id = await svc.queue_generation(
            campaign_id="camp-1",
            scene_id=None,
            post_id=None,
            request=GenerationRequest(prompt="x", width=8, height=8, seed=1),
        )
        rows = await persistent_store.db.fetchall(
            "SELECT id, status FROM imagegen_jobs WHERE id = ?", (job_id,)
        )
        assert rows
        assert rows[0]["status"] in ("queued", "running")
    finally:
        await svc.aclose()


async def test_persistence_disabled_writes_nothing(persistent_store) -> None:
    svc = _new_service(persistent_store, persist=False)
    try:
        await svc.queue_generation(
            campaign_id="camp-1",
            scene_id=None,
            post_id=None,
            request=GenerationRequest(prompt="x", width=8, height=8, seed=1),
        )
        rows = await persistent_store.db.fetchall("SELECT COUNT(*) AS c FROM imagegen_jobs")
        assert rows[0]["c"] == 0
    finally:
        await svc.aclose()


async def test_reload_pending_jobs_reenqueues_queued_jobs(persistent_store) -> None:
    svc = _new_service(persistent_store, persist=True)
    job_id = None
    try:
        job_id = await svc.queue_generation(
            campaign_id="camp-1",
            scene_id=None,
            post_id=None,
            request=GenerationRequest(prompt="x", width=8, height=8, seed=1),
        )
    finally:
        await svc.aclose()

    # Force the DB row back to QUEUED so reload's "RUNNING → FAILED"
    # branch doesn't take it (the worker would have transitioned it
    # to RUNNING already).
    await persistent_store.db.execute(
        "UPDATE imagegen_jobs SET status = 'queued', started_at = NULL WHERE id = ?",
        (job_id,),
    )

    svc2 = _new_service(persistent_store, persist=True)
    try:
        await svc2.reload_pending_jobs()
        jobs = await svc2.list_jobs("camp-1")
        assert any(j.id == job_id for j in jobs)
    finally:
        await svc2.aclose()


async def test_reload_marks_running_jobs_as_failed(persistent_store) -> None:
    svc = _new_service(persistent_store, persist=True)
    try:
        job_id = await svc.queue_generation(
            campaign_id="camp-1",
            scene_id=None,
            post_id=None,
            request=GenerationRequest(prompt="x", width=8, height=8, seed=1),
        )
        # Wait until row is RUNNING
        for _ in range(100):
            await asyncio.sleep(0.02)
            rows = await persistent_store.db.fetchall(
                "SELECT status FROM imagegen_jobs WHERE id = ?", (job_id,)
            )
            if rows and rows[0]["status"] == "running":
                break
        await svc.cancel_job(job_id)  # so aclose doesn't hang
    finally:
        await svc.aclose()

    # Manually force the row back to RUNNING to test reload's recovery.
    await persistent_store.db.execute(
        "UPDATE imagegen_jobs SET status = 'running' WHERE id = ?", (job_id,)
    )

    svc2 = _new_service(persistent_store, persist=True)
    try:
        await svc2.reload_pending_jobs()
        rows = await persistent_store.db.fetchall(
            "SELECT status, error FROM imagegen_jobs WHERE id = ?", (job_id,)
        )
        assert rows[0]["status"] == "failed"
        assert "shutdown" in (rows[0]["error"] or "")
    finally:
        await svc2.aclose()


async def test_persistence_with_normal_completion(store) -> None:
    """Smoke test: persistence on, with a backend that actually completes."""
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    reg = BackendRegistry()
    reg.register(InMemoryDiffusersBackend())
    cfg = ImageGenConfig(default_backend="diffusers-memory", queue_persist_pending=True)
    svc = ImageGenService(
        store=store,
        registry=reg,
        default_backend_id="diffusers-memory",
        event_bus=EventBus(),
        config=cfg,
    )
    try:
        job_id = await svc.queue_generation(
            campaign_id="camp-1",
            scene_id=None,
            post_id=None,
            request=GenerationRequest(prompt="x", width=8, height=8, seed=42),
        )
        for _ in range(100):
            await asyncio.sleep(0.02)
            jobs = await svc.list_jobs("camp-1")
            if any(j.id == job_id and j.status == JobStatus.COMPLETE for j in jobs):
                break
        rows = await store.db.fetchall("SELECT status FROM imagegen_jobs WHERE id = ?", (job_id,))
        assert rows[0]["status"] == "complete"
    finally:
        await svc.aclose()

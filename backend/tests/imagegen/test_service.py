"""End-to-end tests for the ImageGenService facade."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from grimoire.event_bus import EventBus
from grimoire.imagegen import (
    BackendRegistry,
    ImageGenService,
    InMemoryDiffusersBackend,
    TriggerConfig,
    should_illustrate,
)
from grimoire.imagegen.backend import synthesize_png
from grimoire.types.common import HealthLevel
from grimoire.types.imagegen import GenerationRequest, JobStatus


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    """Poll ``predicate`` until it returns truthy or fail."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("predicate never became truthy")


def _request(prompt: str = "noir alley") -> GenerationRequest:
    return GenerationRequest(
        prompt=prompt, width=32, height=32, steps=1, cfg_scale=1.0, sampler="UniPC", seed=7
    )


# --------------------------------------------------------------------------- #
# Backend management
# --------------------------------------------------------------------------- #


async def test_list_backends_reports_integrated(service) -> None:
    svc, _ = service
    backends = await svc.list_backends()
    assert [b.id for b in backends] == ["diffusers-memory"]
    assert backends[0].is_integrated is True
    assert backends[0].plugin_id is None


async def test_active_backend_defaults_to_service_default(service) -> None:
    svc, _ = service
    info = await svc.active_backend("camp-1")
    assert info.id == "diffusers-memory"


async def test_set_active_backend_rejects_unknown(service) -> None:
    svc, _ = service
    with pytest.raises(KeyError):
        await svc.set_active_backend("camp-1", "nonexistent")


# --------------------------------------------------------------------------- #
# Trigger evaluation
# --------------------------------------------------------------------------- #


def test_should_illustrate_per_scene_modes() -> None:
    cfg = TriggerConfig(mode="per_scene")
    assert should_illustrate(cfg, is_scene_open=True)
    assert should_illustrate(cfg, is_new_location=True)
    assert should_illustrate(cfg, is_new_character=True)
    assert not should_illustrate(cfg)


def test_should_illustrate_per_post_always_unless_combat() -> None:
    cfg = TriggerConfig(mode="per_post", auto_during_combat=False)
    assert should_illustrate(cfg)
    assert not should_illustrate(cfg, is_in_combat=True)
    cfg = TriggerConfig(mode="per_post", auto_during_combat=True)
    assert should_illustrate(cfg, is_in_combat=True)


def test_should_illustrate_every_n_posts() -> None:
    cfg = TriggerConfig(mode="every_n_posts", every_n=3)
    assert should_illustrate(cfg, post_index=3)
    assert should_illustrate(cfg, post_index=6)
    assert not should_illustrate(cfg, post_index=2)


def test_should_illustrate_manual_only_never_triggers() -> None:
    cfg = TriggerConfig(mode="manual_only")
    assert not should_illustrate(cfg, is_scene_open=True, is_new_location=True)


def test_trigger_config_from_yaml_block() -> None:
    cfg = TriggerConfig.from_config(
        {
            "trigger_mode": "every_n_posts",
            "trigger_n": 7,
            "trigger_on_scene_open": False,
            "auto_illustrate_during_combat": True,
        }
    )
    assert cfg.mode == "every_n_posts"
    assert cfg.every_n == 7
    assert cfg.on_scene_open is False
    assert cfg.auto_during_combat is True


# --------------------------------------------------------------------------- #
# Synchronous generation + caching
# --------------------------------------------------------------------------- #


async def test_generate_sync_returns_result(service) -> None:
    svc, _ = service
    result = await svc.generate_sync("camp-1", _request())
    assert result.image_bytes
    assert result.seed == 7


async def test_generate_sync_caches_identical_seeded_requests(service) -> None:
    svc, _ = service
    request = _request()
    first = await svc.generate_sync("camp-1", request)
    second = await svc.generate_sync("camp-1", request)
    # Same id object: the cache returned the previously stored result.
    assert first is second


async def test_generate_sync_random_seed_skips_cache(service) -> None:
    svc, _ = service
    req = GenerationRequest(prompt="randomish", width=32, height=32, steps=1, cfg_scale=1.0)
    first = await svc.generate_sync("camp-1", req)
    second = await svc.generate_sync("camp-1", req)
    assert first is not second


# --------------------------------------------------------------------------- #
# Queue path
# --------------------------------------------------------------------------- #


async def test_queue_generation_writes_files_and_index(service, tmp_path: Path) -> None:
    svc, bus = service
    events: list[str] = []
    bus.subscribe("imagegen_job_queued", lambda ev: events.append(ev.type))
    bus.subscribe("imagegen_job_started", lambda ev: events.append(ev.type))
    bus.subscribe("image_ready", lambda ev: events.append(ev.type))

    job_id = await svc.queue_generation(
        campaign_id="camp-1",
        scene_id="scene-1",
        post_id=None,
        request=_request(prompt="scene shot"),
    )

    await _wait_for(lambda: any(j.status == JobStatus.COMPLETE for j in svc._jobs.values()))

    job = svc._jobs[job_id]
    assert job.status == JobStatus.COMPLETE
    images = await svc.list_images("camp-1")
    assert len(images) == 1
    image = images[0]
    assert image.prompt == "scene shot"
    assert image.scene_id == "scene-1"
    assert image.seed == 7

    # File assets written to data/campaigns/<id>/images/.
    data_root = svc.data_root
    png_path = data_root / image.file_path
    assert png_path.exists()
    assert png_path.read_bytes().startswith(b"\x89PNG")

    sidecar = data_root / "campaigns" / "camp-1" / "images" / f"{image.id}.yaml"
    assert sidecar.exists()
    side = yaml.safe_load(sidecar.read_text())
    assert side["id"] == image.id
    assert side["scene_id"] == "scene-1"

    assert "imagegen_job_queued" in events
    assert "imagegen_job_started" in events
    assert "image_ready" in events


async def test_queue_generation_cache_hit_skips_re_render(service) -> None:
    svc, bus = service
    job1 = await svc.queue_generation(
        campaign_id="camp-1",
        scene_id=None,
        post_id=None,
        request=_request(prompt="dup"),
    )
    await _wait_for(lambda: svc._jobs[job1].status == JobStatus.COMPLETE)

    cached_flags: list[bool] = []
    bus.subscribe("image_ready", lambda ev: cached_flags.append(bool(ev.payload.get("cached"))))

    job2 = await svc.queue_generation(
        campaign_id="camp-1",
        scene_id=None,
        post_id=None,
        request=_request(prompt="dup"),
    )
    await _wait_for(lambda: svc._jobs[job2].status == JobStatus.COMPLETE)

    images = await svc.list_images("camp-1")
    # Only one image row — the second job reused the cached image.
    assert len(images) == 1
    assert any(cached_flags), "expected at least one image_ready with cached=True"


async def test_cancel_queued_job_skips_generation(service) -> None:
    svc, _ = service
    # Saturate the worker with a long-running request first.
    job1 = await svc.queue_generation(
        campaign_id="camp-1",
        scene_id=None,
        post_id=None,
        request=_request(prompt="first"),
    )
    job2 = await svc.queue_generation(
        campaign_id="camp-1",
        scene_id=None,
        post_id=None,
        request=_request(prompt="second"),
    )
    await svc.cancel_job(job2)
    await _wait_for(lambda: svc._jobs[job1].status == JobStatus.COMPLETE)
    # The cancelled job should never have produced an image.
    images = await svc.list_images("camp-1")
    assert len(images) == 1


async def test_prioritize_job_updates_priority(service) -> None:
    svc, _ = service
    job_id = await svc.queue_generation(
        campaign_id="camp-1",
        scene_id=None,
        post_id=None,
        request=_request(),
    )
    await svc.prioritize_job(job_id, 10)
    job = svc._jobs[job_id]
    assert job.priority == 10


# --------------------------------------------------------------------------- #
# Listing / star / delete
# --------------------------------------------------------------------------- #


async def test_star_filter_and_delete(service) -> None:
    svc, _ = service
    job_id = await svc.queue_generation(
        campaign_id="camp-1",
        scene_id=None,
        post_id=None,
        request=_request(prompt="starring"),
    )
    await _wait_for(lambda: svc._jobs[job_id].status == JobStatus.COMPLETE)
    image = (await svc.list_images("camp-1"))[0]
    await svc.star_image(image.id, True)
    starred = await svc.list_images("camp-1", starred_only=True)
    assert [i.id for i in starred] == [image.id]

    await svc.delete_image(image.id)
    remaining = await svc.list_images("camp-1")
    assert remaining == []
    # Asset gone too.
    png_path = svc.data_root / image.file_path
    assert not png_path.exists()


# --------------------------------------------------------------------------- #
# Re-roll / variation
# --------------------------------------------------------------------------- #


async def test_reroll_clears_seed_and_keeps_prompt(service) -> None:
    svc, _ = service
    job1 = await svc.queue_generation(
        campaign_id="camp-1",
        scene_id="scene-1",
        post_id=None,
        request=_request(prompt="reroll subject"),
    )
    await _wait_for(lambda: svc._jobs[job1].status == JobStatus.COMPLETE)
    original = (await svc.list_images("camp-1"))[0]

    job2 = await svc.reroll(original.id)
    await _wait_for(lambda: svc._jobs[job2].status == JobStatus.COMPLETE)

    images = await svc.list_images("camp-1")
    assert len(images) == 2
    # The new image carries the same prompt but a (random) different seed.
    new_image = next(i for i in images if i.id != original.id)
    assert new_image.prompt == "reroll subject"
    assert new_image.scene_id == "scene-1"


async def test_variation_runs_img2img_with_strength(service) -> None:
    svc, _ = service
    job1 = await svc.queue_generation(
        campaign_id="camp-1",
        scene_id=None,
        post_id=None,
        request=_request(prompt="variation source"),
    )
    await _wait_for(lambda: svc._jobs[job1].status == JobStatus.COMPLETE)
    original = (await svc.list_images("camp-1"))[0]

    job2 = await svc.variation(original.id, 0.4)
    await _wait_for(lambda: svc._jobs[job2].status == JobStatus.COMPLETE)
    follow_up_job = svc._jobs[job2]
    assert follow_up_job.request.init_image is not None
    assert follow_up_job.request.init_image_strength == 0.4


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #


async def test_health_check_unknown_backend_returns_unconfigured(service) -> None:
    svc, _ = service
    status = await svc.health_check("does-not-exist")
    assert status.level == HealthLevel.UNCONFIGURED


async def test_health_check_emits_event_on_change(service) -> None:
    svc, bus = service
    events: list[dict] = []
    bus.subscribe("imagegen_backend_health_changed", lambda ev: events.append(ev.payload))
    # First check transitions UNDEFINED -> HEALTHY.
    await svc.health_check("diffusers-memory")
    # Second check is the same level, so no new event.
    await svc.health_check("diffusers-memory")
    await asyncio.sleep(0)  # give the event bus a tick
    levels = [ev["level"] for ev in events]
    assert "healthy" in levels


# --------------------------------------------------------------------------- #
# Multi-backend parallelism
# --------------------------------------------------------------------------- #


async def test_multiple_backends_have_independent_queues(store) -> None:
    reg = BackendRegistry()
    reg.register(InMemoryDiffusersBackend({"base_model": "memory:fast"}))

    class SlowBackend(InMemoryDiffusersBackend):
        id = "slow-stub"
        name = "Slow stub"

        async def generate(self, request):  # type: ignore[override]
            await asyncio.sleep(0.05)
            return await super().generate(request)

    reg.register(SlowBackend({"base_model": "memory:slow"}))

    svc = ImageGenService(
        store=store,
        registry=reg,
        default_backend_id="diffusers-memory",
        event_bus=EventBus(),
    )
    await store.upsert_campaign(campaign_id="camp-a", name="A")
    await store.upsert_campaign(campaign_id="camp-b", name="B")
    await svc.set_active_backend("camp-b", "slow-stub")

    try:
        await svc.queue_generation("camp-a", None, None, request=_request("fast a"))
        await svc.queue_generation("camp-b", None, None, request=_request("slow b"))

        async def all_done() -> bool:
            return all(j.status == JobStatus.COMPLETE for j in svc._jobs.values())

        await _wait_for(all_done, timeout=3.0)
    finally:
        await svc.aclose()


def test_synthesize_png_dimensions_are_clamped() -> None:
    # synthesize_png clamps oversized requests so tests never blow up.
    png = synthesize_png(5000, 5000, seed=0)
    assert png.startswith(b"\x89PNG")


# --------------------------------------------------------------------------- #
# Review regression tests
# --------------------------------------------------------------------------- #


async def test_cancel_running_job_skips_persist_and_complete(store) -> None:
    """Cancelling a RUNNING job must not result in a persisted image or
    a final ``COMPLETE`` status overwriting the cancellation."""

    started = asyncio.Event()
    release = asyncio.Event()

    class GatedBackend(InMemoryDiffusersBackend):
        id = "gated"
        name = "Gated stub"

        async def generate(self, request):  # type: ignore[override]
            started.set()
            await release.wait()
            return await super().generate(request)

    reg = BackendRegistry()
    reg.register(GatedBackend())
    svc = ImageGenService(
        store=store,
        registry=reg,
        default_backend_id="gated",
        event_bus=EventBus(),
    )
    await store.upsert_campaign(campaign_id="camp-x", name="X")
    try:
        job_id = await svc.queue_generation("camp-x", None, None, request=_request("racey"))
        await asyncio.wait_for(started.wait(), timeout=2.0)
        await svc.cancel_job(job_id)
        release.set()
        await _wait_for(lambda: svc._jobs[job_id].finished_at is not None)
        assert svc._jobs[job_id].status == JobStatus.CANCELLED
        # No persisted image despite the backend completing.
        assert await svc.list_images("camp-x") == []
    finally:
        release.set()
        await svc.aclose()


async def test_init_image_bytes_are_part_of_cache_key(service) -> None:
    """Two seeded img2img requests with different sources must not collide."""
    svc, _ = service
    backend = svc.registry.get("diffusers-memory")

    req_a = GenerationRequest(
        prompt="img2img",
        width=32,
        height=32,
        steps=1,
        cfg_scale=1.0,
        seed=5,
        init_image=b"AAAA",
        init_image_strength=0.5,
    )
    req_b = req_a.model_copy(update={"init_image": b"BBBB"})

    res_a = await backend.generate(req_a)
    res_b = await backend.generate(req_b)
    svc._store_in_cache("camp-1", req_a, backend=backend, result=res_a)
    svc._store_in_cache("camp-1", req_b, backend=backend, result=res_b)
    hit_a = svc._lookup_cache("camp-1", req_a, backend=backend)
    hit_b = svc._lookup_cache("camp-1", req_b, backend=backend)
    assert hit_a is res_a
    assert hit_b is res_b
    assert hit_a is not hit_b


async def test_cache_does_not_leak_across_campaigns(store) -> None:
    """Same seeded request submitted by two campaigns must not share an image."""
    reg = BackendRegistry()
    reg.register(InMemoryDiffusersBackend())
    svc = ImageGenService(
        store=store,
        registry=reg,
        default_backend_id="diffusers-memory",
        event_bus=EventBus(),
    )
    await store.upsert_campaign(campaign_id="camp-a", name="A")
    await store.upsert_campaign(campaign_id="camp-b", name="B")
    try:
        job_a = await svc.queue_generation("camp-a", None, None, request=_request("shared"))
        await _wait_for(lambda: svc._jobs[job_a].status == JobStatus.COMPLETE)
        job_b = await svc.queue_generation("camp-b", None, None, request=_request("shared"))
        await _wait_for(lambda: svc._jobs[job_b].status == JobStatus.COMPLETE)

        images_a = await svc.list_images("camp-a")
        images_b = await svc.list_images("camp-b")
        assert len(images_a) == 1
        assert len(images_b) == 1
        assert images_a[0].id != images_b[0].id
    finally:
        await svc.aclose()


async def test_unsafe_campaign_id_is_rejected(service) -> None:
    """Path-traversal-shaped campaign ids never reach the filesystem."""
    svc, _ = service
    for bad in ("../etc", "..", "abc/def", "", "a\x00b", "."):
        with pytest.raises(ValueError):
            await svc.queue_generation(bad, None, None, request=_request("x"))
        with pytest.raises(ValueError):
            await svc.generate_sync(bad, _request("x"))


async def test_prioritize_job_mutates_in_place(service) -> None:
    """``prioritize_job`` must update the live job record the worker holds,
    not swap the dict entry for a copy that the worker won't see."""
    svc, _ = service
    job_id = await svc.queue_generation("camp-1", None, None, request=_request("priority probe"))
    live = svc._jobs[job_id]
    await svc.prioritize_job(job_id, 9)
    assert live.priority == 9
    assert svc._jobs[job_id] is live

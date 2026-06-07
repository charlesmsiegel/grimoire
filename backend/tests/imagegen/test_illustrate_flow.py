"""Tests for the illustrate flow: prompt preview, recent-prose context, and
on-disk file serving added alongside the sidebar Illustrate button."""

from __future__ import annotations

import asyncio

import pytest

from grimoire.imagegen.prompt import PromptComposer
from grimoire.state_store import StateStore
from grimoire.types.imagegen import GenerationRequest, JobStatus


async def _wait_done(svc, campaign_id: str, job_id: str, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        job = next((j for j in await svc.list_jobs(campaign_id) if j.id == job_id), None)
        if job is not None and job.status == JobStatus.COMPLETE:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"job {job_id} did not complete in {timeout}s")


async def _insert_post(
    store: StateStore, *, scene_id: str, post_id: str, order: int, body: str
) -> None:
    await store.db.execute(
        """
        INSERT INTO posts (id, scene_id, campaign_id, order_in_scene, body_excerpt)
        VALUES (?, ?, 'camp-1', ?, ?)
        """,
        (post_id, scene_id, order, body),
    )


async def test_preview_prompt_does_not_queue_a_job(service) -> None:
    """compose-prompt previews without rendering — no job is enqueued."""
    svc, _bus = service
    request = await svc.preview_prompt("camp-1", "scene-1", None)
    assert isinstance(request, GenerationRequest)
    assert request.prompt  # never empty — falls back to a placeholder
    assert await svc.list_jobs("camp-1") == []


async def test_recent_scene_prose_uses_last_three_posts(service) -> None:
    """The composer is fed the last 3 posts, oldest-first, not just one line."""
    svc, _bus = service
    for i in range(1, 6):
        await _insert_post(
            svc.store, scene_id="scene-1", post_id=f"p{i}", order=i, body=f"body {i}"
        )

    prose = await svc._recent_scene_prose("scene-1", None)
    assert prose == "body 3\n\nbody 4\n\nbody 5"

    # Anchored at an earlier post: window ends there.
    anchored = await svc._recent_scene_prose("scene-1", "p4")
    assert anchored == "body 2\n\nbody 3\n\nbody 4"


async def test_preview_prompt_includes_extracted_visual_elements(service) -> None:
    """With a composer + stub extractor wired, the preview folds in the
    light-LLM's visual elements drawn from recent prose."""
    svc, _bus = service

    class _StubExtractor:
        async def extract_visual_elements(self, text: str) -> list[str]:
            assert "body 3" in text  # got the recent prose, not an empty string
            return ["torchlit cavern", "two figures facing off"]

    svc.composer = PromptComposer(visual_extractor=_StubExtractor())
    for i in range(1, 4):
        await _insert_post(
            svc.store, scene_id="scene-1", post_id=f"p{i}", order=i, body=f"body {i}"
        )

    request = await svc.preview_prompt("camp-1", "scene-1", "p3")
    assert "torchlit cavern" in request.prompt
    assert "two figures facing off" in request.prompt


async def test_campaign_preset_id_strips_library_prefix(service) -> None:
    """The composition stores the preset as a library ref; get_image_preset
    re-adds the folder, so the bare id must come back out (no double prefix)."""
    svc, _bus = service
    await svc.store.db.execute(
        "UPDATE campaigns SET image_preset_id = ? WHERE id = ?",
        ("image-presets/hentai", "camp-1"),
    )
    assert await svc._campaign_image_preset_id("camp-1") == "hentai"


async def test_compose_survives_unresolvable_preset(service) -> None:
    """A missing/misconfigured preset must degrade, not 404 the whole request."""
    svc, _bus = service

    class _BoomLibrary:
        async def get_image_preset(self, preset_id: str):
            raise KeyError(f"image preset {preset_id!r} not found")

    svc.composer = PromptComposer(library=_BoomLibrary())
    await svc.store.db.execute(
        "UPDATE campaigns SET image_preset_id = ? WHERE id = ?",
        ("image-presets/missing", "camp-1"),
    )
    request = await svc.preview_prompt("camp-1", "scene-1", None)
    assert request.prompt  # placeholder at worst — never raises


async def test_image_file_serves_png_and_thumbnail(service) -> None:
    svc, _bus = service
    job_id = await svc.queue_generation(
        campaign_id="camp-1",
        scene_id="scene-1",
        post_id=None,
        request=GenerationRequest(prompt="a quiet shot"),
    )
    await _wait_done(svc, "camp-1", job_id)
    image = (await svc.list_images("camp-1"))[0]

    png = await svc.image_file(image.id)
    assert png.is_file()
    assert png.read_bytes().startswith(b"\x89PNG")

    thumb = await svc.image_file(image.id, thumbnail=True)
    assert thumb.is_file()
    assert thumb.suffix == ".jpg"


async def test_image_file_unknown_image_raises(service) -> None:
    svc, _bus = service
    with pytest.raises(KeyError):
        await svc.image_file("does-not-exist")

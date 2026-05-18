"""§10 Image tag editing."""

from __future__ import annotations

import asyncio

import pytest

from grimoire.types.imagegen import GenerationRequest, JobStatus


async def _wait_complete(svc, job_id):
    for _ in range(100):
        await asyncio.sleep(0.02)
        for j in await svc.list_jobs("camp-1"):
            if j.id == job_id and j.status == JobStatus.COMPLETE:
                return


async def test_set_tags_updates_sql_and_sidecar(service) -> None:
    svc, _ = service
    job_id = await svc.queue_generation(
        campaign_id="camp-1",
        scene_id="scene-1",
        post_id=None,
        request=GenerationRequest(prompt="x", width=8, height=8, seed=1),
    )
    await _wait_complete(svc, job_id)
    image = (await svc.list_images("camp-1"))[0]
    await svc.set_tags(image.id, ["scene-establishing", "action"])
    updated = await svc.get_image(image.id)
    assert updated.tags == ["scene-establishing", "action"]


async def test_set_tags_unknown_image_raises(service) -> None:
    svc, _ = service
    with pytest.raises(KeyError):
        await svc.set_tags("nope", ["x"])


async def test_set_tags_strips_empty_and_whitespace(service) -> None:
    svc, _ = service
    job_id = await svc.queue_generation(
        campaign_id="camp-1",
        scene_id="scene-1",
        post_id=None,
        request=GenerationRequest(prompt="x", width=8, height=8, seed=1),
    )
    await _wait_complete(svc, job_id)
    image = (await svc.list_images("camp-1"))[0]
    await svc.set_tags(image.id, [" foo ", "", "bar", "   "])
    updated = await svc.get_image(image.id)
    assert updated.tags == ["foo", "bar"]

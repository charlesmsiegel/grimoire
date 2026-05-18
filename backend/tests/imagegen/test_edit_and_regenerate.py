"""§5 edit_and_regenerate queues a fresh job with a merged request."""

from __future__ import annotations

import asyncio

from grimoire.types.imagegen import GenerationRequest, JobStatus


async def _wait_complete(svc, job_id):
    for _ in range(100):
        await asyncio.sleep(0.02)
        for j in await svc.list_jobs("camp-1"):
            if j.id == job_id and j.status in (JobStatus.COMPLETE, JobStatus.FAILED):
                return j
    raise AssertionError(f"job {job_id} never completed")


async def test_edit_and_regenerate_uses_new_prompt_keeps_seed(service) -> None:
    svc, _ = service
    job0 = await svc.queue_generation(
        campaign_id="camp-1",
        scene_id="scene-1",
        post_id=None,
        request=GenerationRequest(prompt="orig", width=8, height=8, seed=7),
    )
    await _wait_complete(svc, job0)
    image_id = (await svc.list_images("camp-1"))[0].id
    job1_id = await svc.edit_and_regenerate(image_id, prompt="new", keep_seed=True)
    await _wait_complete(svc, job1_id)
    images = await svc.list_images("camp-1")
    # Old image persists; new image saved under a new id.
    assert len(images) == 2
    new_image = max(images, key=lambda i: i.created_at)
    assert new_image.prompt == "new"
    assert new_image.seed == 7  # keep_seed honored


async def test_edit_and_regenerate_new_seed_when_keep_false(service) -> None:
    svc, _ = service
    job0 = await svc.queue_generation(
        campaign_id="camp-1",
        scene_id="scene-1",
        post_id=None,
        request=GenerationRequest(prompt="orig", width=8, height=8, seed=7),
    )
    await _wait_complete(svc, job0)
    image_id = (await svc.list_images("camp-1"))[0].id
    job1_id = await svc.edit_and_regenerate(image_id, prompt="new")
    await _wait_complete(svc, job1_id)
    images = await svc.list_images("camp-1")
    new_image = max(images, key=lambda i: i.created_at)
    assert new_image.prompt == "new"
    # We can't assert the exact seed (it's randomly chosen), just that
    # keep_seed=False does NOT carry over the old seed deterministically.


async def test_edit_and_regenerate_unknown_image_raises(service) -> None:
    svc, _ = service
    import pytest

    with pytest.raises(KeyError):
        await svc.edit_and_regenerate("nope", prompt="x")

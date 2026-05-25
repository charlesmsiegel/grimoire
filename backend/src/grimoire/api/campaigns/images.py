"""Campaign image generation and management routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from grimoire.api.deps import ImageGenDep
from grimoire.api.util import map_lookup_errors, to_payload

from .schemas import (
    EditAndRegeneratePayload,
    FallbackBackendPayload,
    ImageGenPayload,
    PrioritizeJobPayload,
    SetActiveBackendPayload,
    SetTagsPayload,
    StarImagePayload,
    TriggerConfigPayload,
    VariationPayload,
)

router = APIRouter()


@router.post("/{campaign_id}/images/generate", status_code=202)
async def generate_image(
    campaign_id: str,
    payload: ImageGenPayload,
    imagegen: ImageGenDep,
) -> Any:
    from grimoire.types.imagegen import GenerationRequest

    try:
        request = (
            GenerationRequest.model_validate(payload.request)
            if payload.request is not None
            else None
        )
        job_id = await imagegen.queue_generation(
            campaign_id=campaign_id,
            scene_id=payload.scene_id,
            post_id=payload.post_id,
            request=request,
            priority=payload.priority,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"job_id": job_id}


@router.get("/{campaign_id}/images")
async def list_images(
    campaign_id: str,
    imagegen: ImageGenDep,
    scene_id: str | None = None,
    starred_only: bool = False,
) -> Any:
    try:
        return to_payload(
            await imagegen.list_images(campaign_id, scene_id=scene_id, starred_only=starred_only)
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/imagegen/active")
async def get_active_backend(campaign_id: str, imagegen: ImageGenDep) -> Any:
    try:
        return to_payload(await imagegen.active_backend(campaign_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.put("/{campaign_id}/imagegen/active")
async def set_active_backend(
    campaign_id: str,
    payload: SetActiveBackendPayload,
    imagegen: ImageGenDep,
) -> Any:
    try:
        await imagegen.set_active_backend(campaign_id, payload.backend_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.get("/{campaign_id}/imagegen/trigger")
async def get_imagegen_trigger(campaign_id: str, imagegen: ImageGenDep) -> Any:
    cfg = await imagegen.get_trigger_config(campaign_id)
    return {
        "mode": cfg.mode,
        "every_n": cfg.every_n,
        "on_scene_open": cfg.on_scene_open,
        "on_new_location": cfg.on_new_location,
        "on_new_character_appearance": cfg.on_new_character_appearance,
        "auto_during_combat": cfg.auto_during_combat,
    }


@router.put("/{campaign_id}/imagegen/trigger")
async def set_imagegen_trigger(
    campaign_id: str,
    payload: TriggerConfigPayload,
    imagegen: ImageGenDep,
) -> Any:
    from grimoire.imagegen import TriggerConfig

    await imagegen.set_trigger_config(
        campaign_id,
        TriggerConfig(
            mode=payload.mode,
            every_n=payload.every_n,
            on_scene_open=payload.on_scene_open,
            on_new_location=payload.on_new_location,
            on_new_character_appearance=payload.on_new_character_appearance,
            auto_during_combat=payload.auto_during_combat,
        ),
    )
    return {"ok": True}


@router.get("/{campaign_id}/imagegen/fallback")
async def get_imagegen_fallback(campaign_id: str, imagegen: ImageGenDep) -> Any:
    return {"backend_id": await imagegen.get_fallback_backend(campaign_id)}


@router.put("/{campaign_id}/imagegen/fallback")
async def set_imagegen_fallback(
    campaign_id: str,
    payload: FallbackBackendPayload,
    imagegen: ImageGenDep,
) -> Any:
    try:
        await imagegen.set_fallback_backend(campaign_id, payload.backend_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.get("/{campaign_id}/images/jobs")
async def list_image_jobs(
    campaign_id: str,
    imagegen: ImageGenDep,
    status: str | None = None,
) -> Any:
    from grimoire.types.imagegen import JobStatus

    try:
        status_enum = JobStatus(status) if status else None
        return to_payload(await imagegen.list_jobs(campaign_id, status=status_enum))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.delete("/{campaign_id}/images/jobs/{job_id}", status_code=204)
async def cancel_image_job(
    campaign_id: str,
    job_id: str,
    imagegen: ImageGenDep,
) -> None:
    try:
        await imagegen.cancel_job(job_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.patch("/{campaign_id}/images/jobs/{job_id}")
async def prioritize_image_job(
    campaign_id: str,
    job_id: str,
    payload: PrioritizeJobPayload,
    imagegen: ImageGenDep,
) -> Any:
    try:
        await imagegen.prioritize_job(job_id, payload.priority)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.get("/{campaign_id}/images/{image_id}")
async def get_image(
    campaign_id: str,
    image_id: str,
    imagegen: ImageGenDep,
) -> Any:
    try:
        return to_payload(await imagegen.get_image(image_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.put("/{campaign_id}/images/{image_id}/star")
async def star_image(
    campaign_id: str,
    image_id: str,
    payload: StarImagePayload,
    imagegen: ImageGenDep,
) -> Any:
    try:
        await imagegen.star_image(image_id, payload.starred)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.delete("/{campaign_id}/images/{image_id}", status_code=204)
async def delete_image(
    campaign_id: str,
    image_id: str,
    imagegen: ImageGenDep,
) -> None:
    try:
        await imagegen.delete_image(image_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/{campaign_id}/images/{image_id}/reroll", status_code=202)
async def reroll_image(
    campaign_id: str,
    image_id: str,
    imagegen: ImageGenDep,
) -> Any:
    try:
        job_id = await imagegen.reroll(image_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"job_id": job_id}


@router.post("/{campaign_id}/images/{image_id}/variation", status_code=202)
async def variation_image(
    campaign_id: str,
    image_id: str,
    payload: VariationPayload,
    imagegen: ImageGenDep,
) -> Any:
    try:
        job_id = await imagegen.variation(image_id, payload.strength)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"job_id": job_id}


@router.post("/{campaign_id}/images/{image_id}/edit", status_code=202)
async def edit_and_regenerate_image(
    campaign_id: str,
    image_id: str,
    payload: EditAndRegeneratePayload,
    imagegen: ImageGenDep,
) -> Any:
    del campaign_id
    try:
        job_id = await imagegen.edit_and_regenerate(
            image_id,
            prompt=payload.prompt,
            negative_prompt=payload.negative_prompt,
            params=payload.params,
            keep_seed=payload.keep_seed,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"job_id": job_id}


@router.put("/{campaign_id}/images/{image_id}/tags")
async def set_image_tags(
    campaign_id: str,
    image_id: str,
    payload: SetTagsPayload,
    imagegen: ImageGenDep,
) -> Any:
    del campaign_id
    try:
        await imagegen.set_tags(image_id, payload.tags)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}

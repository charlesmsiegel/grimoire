"""Non-campaign-scoped ImageGen routes (§13 of imagegen remaining-design).

Campaign-scoped routes live in :mod:`grimoire.api.campaigns`; this module
covers the routes that aren't keyed on a single campaign id (backend list,
backend health, prewarm).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from grimoire.api.deps import ImageGenDep
from grimoire.api.util import map_lookup_errors, to_payload

router = APIRouter(prefix="/imagegen", tags=["imagegen"])


@router.get("/backends")
async def list_backends(imagegen: ImageGenDep) -> Any:
    try:
        return to_payload(await imagegen.list_backends())
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/backends/{backend_id}/health")
async def backend_health(backend_id: str, imagegen: ImageGenDep) -> Any:
    try:
        return to_payload(await imagegen.health_check(backend_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/backends/{backend_id}/prewarm")
async def prewarm_backend(backend_id: str, imagegen: ImageGenDep) -> Any:
    try:
        await imagegen.prewarm(backend_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


__all__ = ["router"]

"""Scene import endpoints — preview and streaming import."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from grimoire.api.deps import ContainerDep, ScenesDep, StateStoreDep
from grimoire.scenes.importer import parse_import_source, run_import_pipeline

logger = logging.getLogger(__name__)

router = APIRouter()


class ImportPreviewRequest(BaseModel):
    path: str


class ImportPreviewResponse(BaseModel):
    post_count: int
    detected_characters: dict[str, list[str]]
    sidecar: dict[str, Any] | None


@router.post("/{campaign_id}/scenes/import/preview")
async def preview_import(
    campaign_id: str,
    body: ImportPreviewRequest,
) -> ImportPreviewResponse:
    try:
        md_path = Path(body.path).resolve()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid path: {exc}") from exc
    if not md_path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {body.path}")
    try:
        parsed = parse_import_source(md_path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if parsed.post_count == 0:
        raise HTTPException(
            status_code=400,
            detail="No posts found. File must use grimoire format: ## Post N — author",
        )
    return ImportPreviewResponse(
        post_count=parsed.post_count,
        detected_characters={
            "pc_refs": parsed.detected_pc_refs,
            "npc_refs": parsed.detected_npc_refs,
        },
        sidecar=parsed.sidecar_metadata,
    )


class ImportRequest(BaseModel):
    path: str
    title: str
    location_ref: str | None = None
    in_game_start: str | None = None
    in_game_end: str | None = None
    mood: str | None = None
    tags: list[str] = []
    present_character_refs: list[str] = []
    present_pc_refs: list[str] = []


@router.post("/{campaign_id}/scenes/import")
async def import_scene(
    campaign_id: str,
    body: ImportRequest,
    scenes: ScenesDep,
    state_store: StateStoreDep,
    container: ContainerDep,
) -> StreamingResponse:
    row = await state_store.db.fetchone("SELECT id FROM campaigns WHERE id = ?", (campaign_id,))
    if not row:
        raise HTTPException(status_code=404, detail=f"Campaign not found: {campaign_id}")
    try:
        md_path = Path(body.path).resolve()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid path: {exc}") from exc
    if not md_path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {body.path}")

    metadata = body.model_dump(exclude={"path", "title"})
    eq = getattr(getattr(container, "file_watcher", None), "embedding_queue", None)

    async def event_stream():
        scene_id = ""
        try:
            async for progress in run_import_pipeline(
                scene_manager=scenes,
                md_path=md_path,
                campaign_id=campaign_id,
                title=body.title,
                metadata=metadata,
                embedding_queue=eq,
            ):
                yield f"event: progress\ndata: {json.dumps(asdict(progress))}\n\n"
                if progress.step == "done":
                    scene_id = progress.detail
            yield f"event: result\ndata: {json.dumps({'scene_id': scene_id})}\n\n"
        except Exception as exc:
            logger.exception("import: pipeline failed")
            # ValueError signals bad input (no posts, source already in dest);
            # anything else is an unexpected server-side failure.
            status = 400 if isinstance(exc, ValueError) else 500
            yield f"event: error\ndata: {json.dumps({'detail': str(exc), 'status': status})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")

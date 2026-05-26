"""Scene import endpoints — preview and streaming import."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from grimoire.scenes.importer import parse_import_source

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
    md_path = Path(body.path).resolve()
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

"""worlds: tags responsibilities."""

from __future__ import annotations

from fastapi import HTTPException

from ... import store
from ..common import _world_root_or_404
from ..models import NameBody
from ._router import router as router


# ---- world tags ----
@router.get("/worlds/{wid}/tags")
def get_world_tags(wid: str):
    return store.tags.read_tags(_world_root_or_404(wid))


@router.post("/worlds/{wid}/tags")
def post_world_tag(wid: str, body: NameBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    return {"id": store.tags.add_tag(_world_root_or_404(wid), name)}


@router.put("/worlds/{wid}/tags/{tid}")
def put_world_tag(wid: str, tid: str, body: NameBody):
    try:
        store.tags.rename_tag(_world_root_or_404(wid), tid, body.name.strip())
    except store.tags.TagNotFound as exc:
        raise HTTPException(status_code=404, detail="tag not found") from exc
    return {"id": tid, "name": body.name.strip()}


@router.delete("/worlds/{wid}/tags/{tid}")
def delete_world_tag(wid: str, tid: str):
    try:
        store.tags.delete_tag(_world_root_or_404(wid), tid)
    except store.tags.TagNotFound as exc:
        raise HTTPException(status_code=404, detail="tag not found") from exc
    return {"ok": True}


# ---- world PCs ----
def _validate_tags(root, tags: list[str]) -> None:
    for t in tags:
        if not store.tags.has_tag(root, t):
            raise HTTPException(status_code=400, detail=f"unknown tag: {t}")

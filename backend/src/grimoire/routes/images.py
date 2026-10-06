"""The image object's own routes: what is true of a picture, whoever places it.

``/images/{image_id}/usage`` is the "Used in..." answer
(`store/image_usage.py`): derived on demand from the placements, so it reads
and stamps nothing. It sits under no campaign, so the activity middleware never
sees it, and it is a GET besides.

Included after `world_images`: no pattern here begins with a segment another
router captures, but the order keeps the picture routes together.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import store

router = APIRouter()


@router.get("/images/{image_id}/usage")
def get_image_usage(image_id: str) -> dict:
    if not store.image_hash.is_image_id(image_id):
        raise HTTPException(status_code=400, detail="not an image id")
    return store.image_usage.find(image_id)

"""Campaign review queue routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from grimoire.api.deps import StateStoreDep
from grimoire.api.util import map_lookup_errors

from .helpers import _require_review_owned
from .schemas import ReviewUpdatePayload

router = APIRouter()


@router.post("/{campaign_id}/reviews/{review_id}/approve")
async def approve_review(
    campaign_id: str,
    review_id: str,
    state_store: StateStoreDep,
) -> Any:
    await _require_review_owned(state_store, campaign_id, review_id)
    try:
        delta_id = await state_store.approve_review_item(review_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"delta_id": delta_id}


@router.post("/{campaign_id}/reviews/{review_id}/reject")
async def reject_review(
    campaign_id: str,
    review_id: str,
    state_store: StateStoreDep,
    payload: ReviewUpdatePayload | None = None,
) -> Any:
    await _require_review_owned(state_store, campaign_id, review_id)
    try:
        await state_store.reject_review_item(review_id, notes=payload.notes if payload else "")
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.patch("/{campaign_id}/reviews/{review_id}")
async def update_review(
    campaign_id: str,
    review_id: str,
    payload: ReviewUpdatePayload,
    state_store: StateStoreDep,
) -> Any:
    await _require_review_owned(state_store, campaign_id, review_id)
    try:
        await state_store.db.execute(
            "UPDATE review_queue SET reviewer_notes = ? WHERE id = ? AND campaign_id = ?",
            (payload.notes, review_id, campaign_id),
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}

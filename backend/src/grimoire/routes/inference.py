"""Roles and routes over HTTP (`store.inference.settings`).

* ``GET``/``PUT /inference/settings`` -- the library's roles (Primary, Fast,
  Decision, Embedding) and its route choices.
* ``GET``/``PUT /campaigns/{cid}/inference`` -- one campaign's overrides: the
  three generative roles and the campaign-scoped routes. 404 for a campaign
  that does not exist, before anything else is asked of the request.
* ``POST /inference/retired-notes/{note_id}/dismiss`` -- dismiss one of the
  views' ``retirement_notes``, for good (slice I).

A ``PUT`` takes the store's write body plus ``confirm_embedding`` (a bool,
false when absent) and answers with the fresh view. It is refused with 409
``newer_format`` on a store a newer build wrote and 409 ``not_migrated`` while
the store's layout is not the current one (`common.refuse_unmigrated`), and
with what `settings.RefusedError` names otherwise.

The body is a bare ``dict`` rather than a model in `routes.models`: a model
drops the fields it does not declare, so a legacy key (``active_connection_id``,
``route_scene``, ...) sent here would be answered 200 and change nothing. The
store validates every key, and refuses those by name.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import store
from ..store.inference import retired, settings
from .common import refuse_newer, refuse_unmigrated

router = APIRouter()


def _retirement_unreadable() -> HTTPException:
    """409 `retirement_unreadable` (R3-3): a write that would read the
    retirement record, which cannot be read just now. Never a 500, and
    nothing is written: D's `facts_unreadable` precedent."""
    return HTTPException(status_code=409, detail={
        "kind": "retirement_unreadable", "detail": settings.RETIREMENT_UNREADABLE})


def _write(scope: str, cid: str, body: dict) -> None:
    body = dict(body)
    confirm = body.pop("confirm_embedding", False)
    if not isinstance(confirm, bool):
        raise HTTPException(status_code=400, detail="confirm_embedding must be true or false")
    try:
        settings.write(scope, cid, body, confirm_embedding=confirm)
    except settings.RefusedError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    except retired.RecordUnreadableError as exc:
        # Migrating an unmarked campaign in the write's hold (spec 11.1)
        # reads a stripped connection's fields from the record (R2-2).
        raise _retirement_unreadable() from exc


def _campaign_view(cid: str) -> dict:
    try:
        return settings.view("campaign", cid)
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc


@router.get("/inference/settings")
def get_inference_settings():
    return settings.view("global")


@router.put("/inference/settings")
def put_inference_settings(body: dict):
    refuse_unmigrated()
    _write("global", "", body)
    return settings.view("global")


@router.get("/campaigns/{cid}/inference")
def get_campaign_inference(cid: str):
    return _campaign_view(cid)


@router.put("/campaigns/{cid}/inference")
def put_campaign_inference(cid: str, body: dict):
    # The campaign first: a refusal about what this scope may set is an answer
    # about a campaign, and giving it for one that does not exist says the
    # wrong thing about the request.
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")
    refuse_unmigrated()
    try:
        _write("campaign", cid, body)
    except store.campaigns.CampaignNotFound as exc:
        # Deleted in another tab between the check and the write.
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return _campaign_view(cid)


@router.post("/inference/retired-notes/{note_id}/dismiss")
def dismiss_retired_note(note_id: str):
    """Dismiss one "not carried over" note on `/models`, for good and on
    every device (the note lives in the library's retirement record). A
    settings write that spends nothing and touches no campaign: refused only
    on a store a newer build wrote. 404 for an id neither the record nor the
    planner knows."""
    refuse_newer()
    try:
        found = settings.dismiss_note(note_id)
    except retired.RecordUnreadableError as exc:
        raise _retirement_unreadable() from exc
    if not found:
        raise HTTPException(status_code=404, detail="no such note")
    return {"ok": True}

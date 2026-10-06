"""Quick replies' HTTP surface (store/quick_replies.py).

Three surfaces: a world's set (`/worlds/{wid}/quick-replies`), a campaign's own
set (`/campaigns/{cid}/quick-replies`, answered with the world replies it layers
on as `inherited`, so the editor can offer Override and Hide without a second
read), and the layered set the composer shows (`.../quick-replies/effective`).

A PUT replaces the whole set -- sets are small, and order is part of the
contract -- and carries `expect`, the digest of the set the client read. A set
that moved since is refused 409 `set_changed` rather than overwritten, because
override, hide and reorder are client read-modify-writes and two tabs doing them
would otherwise lose one. Every rule violation is a 400 with a `kind` the client
can branch on (`invalid_quick_reply`, `plugin_api_unavailable`).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import store
from .models import QuickReplySetBody

router = APIRouter()


def _refuse(exc: Exception) -> HTTPException:
    if isinstance(exc, store.quick_replies.QuickReplyError):
        return HTTPException(status_code=400, detail={"kind": exc.code, "detail": str(exc)})
    return HTTPException(status_code=409, detail={
        "kind": "set_changed", "detail": "the set changed since it was read"})


def _world_or_404(wid: str) -> None:
    if not store.worlds.world_exists(wid):
        raise HTTPException(status_code=404, detail="world not found")


def _campaign_world(cid: str) -> str:
    try:
        return store.campaigns.read_campaign(cid)["meta"].get("world") or ""
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc


def _campaign_body(cid: str) -> dict:
    wid = _campaign_world(cid)
    try:
        own = store.quick_replies.campaign_set(cid)
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    inherited = store.quick_replies.world_set(wid)["replies"] if wid else []
    return {**own, "inherited": inherited}


@router.get("/worlds/{wid}/quick-replies")
def get_world_quick_replies(wid: str):
    _world_or_404(wid)
    return store.quick_replies.world_set(wid)


@router.put("/worlds/{wid}/quick-replies")
def put_world_quick_replies(wid: str, body: QuickReplySetBody):
    _world_or_404(wid)
    try:
        return store.quick_replies.write_world(wid, body.replies, body.expect)
    except (store.quick_replies.QuickReplyError, store.quick_replies.SetChanged) as exc:
        raise _refuse(exc) from exc
    except store.worlds.WorldNotFound as exc:
        raise HTTPException(status_code=404, detail="world not found") from exc


@router.get("/campaigns/{cid}/quick-replies")
def get_campaign_quick_replies(cid: str):
    return _campaign_body(cid)


@router.put("/campaigns/{cid}/quick-replies")
def put_campaign_quick_replies(cid: str, body: QuickReplySetBody):
    _campaign_world(cid)
    try:
        store.quick_replies.write_campaign(cid, body.replies, body.expect)
    except (store.quick_replies.QuickReplyError, store.quick_replies.SetChanged) as exc:
        raise _refuse(exc) from exc
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return _campaign_body(cid)


@router.get("/campaigns/{cid}/quick-replies/effective")
def get_effective_quick_replies(cid: str):
    _campaign_world(cid)
    try:
        return {"replies": store.quick_replies.effective(cid)}
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc

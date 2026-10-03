"""The scene state tracker's HTTP surface (store/tracker/).

Only the on/off setting so far; the tracker's own reads and writes join it here
as they land."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import store
from .models import CampaignTracker

router = APIRouter()


def _setting_body(cid: str) -> dict:
    try:
        return {"setting": store.tracker.settings.campaign_setting(cid),
                "enabled": store.tracker.settings.enabled(cid)}
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc


@router.get("/campaigns/{cid}/tracker")
def get_campaign_tracker(cid: str):
    return _setting_body(cid)


@router.put("/campaigns/{cid}/tracker")
def put_campaign_tracker(cid: str, body: CampaignTracker):
    # The campaign first: a 400 about a value is an answer about a campaign, and
    # giving it for one that does not exist tells the caller the wrong thing.
    _setting_body(cid)
    try:
        store.campaigns.set_campaign_tracker(cid, body.setting)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except store.campaigns.CampaignNotFound as exc:
        # Deleted between the check and the write, in another tab.
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return _setting_body(cid)

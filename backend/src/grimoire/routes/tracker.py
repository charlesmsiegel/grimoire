"""The scene state tracker's HTTP surface (store/tracker/).

The on/off setting and the field-definition layers so far; the tracker's own
reads and writes join them here as they land."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import store
from .common import _dump, _require_scene
from .models import CampaignTracker, TrackerLayer

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


# --- field definition layers -------------------------------------------------
#
# Each layer's GET answers `inherited` (what the layer sits on) next to its own
# `layer`, so an editor can show what a change would be changing without a
# second request, and `effective` (the two combined).

def _layer_body(inherited: list[dict], layer: dict) -> dict:
    fields = store.tracker.fields
    return {"layer": layer, "effective": fields.apply_layer(inherited, layer),
            "inherited": inherited}


def _bad_layer(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _world_or_404(wid: str) -> None:
    if not store.worlds.world_exists(wid):
        raise HTTPException(status_code=404, detail="world not found")


def _inherited_for_world() -> list[dict]:
    return [dict(f) for f in store.tracker.fields.DEFAULT_FIELDS]


def _campaign_world(cid: str) -> str:
    try:
        return store.campaigns.read_campaign(cid)["meta"].get("world") or ""
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc


@router.get("/worlds/{wid}/tracker-fields")
def get_world_tracker_fields(wid: str):
    _world_or_404(wid)
    return _layer_body(_inherited_for_world(), store.tracker.fields.world_layer(wid))


@router.put("/worlds/{wid}/tracker-fields")
def put_world_tracker_fields(wid: str, body: TrackerLayer):
    _world_or_404(wid)
    try:
        store.tracker.fields.write_world_layer(wid, _dump(body))
    except store.tracker.fields.FieldLayerError as exc:
        raise _bad_layer(exc) from exc
    except store.worlds.WorldNotFound as exc:
        raise HTTPException(status_code=404, detail="world not found") from exc
    return get_world_tracker_fields(wid)


@router.get("/campaigns/{cid}/tracker-fields")
def get_campaign_tracker_fields(cid: str):
    wid = _campaign_world(cid)
    return _layer_body(store.tracker.fields.world_fields(wid),
                       store.tracker.fields.campaign_layer(cid))


@router.put("/campaigns/{cid}/tracker-fields")
def put_campaign_tracker_fields(cid: str, body: TrackerLayer):
    _campaign_world(cid)
    try:
        store.tracker.fields.write_campaign_layer(cid, _dump(body))
    except store.tracker.fields.FieldLayerError as exc:
        raise _bad_layer(exc) from exc
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return get_campaign_tracker_fields(cid)


@router.get("/campaigns/{cid}/scenes/{sid}/tracker-fields")
def get_scene_tracker_fields(cid: str, sid: str):
    _require_scene(cid, sid)
    return _layer_body(store.tracker.fields.campaign_fields(cid),
                       store.tracker.fields.scene_layer(cid, sid))


@router.put("/campaigns/{cid}/scenes/{sid}/tracker-fields")
def put_scene_tracker_fields(cid: str, sid: str, body: TrackerLayer):
    _require_scene(cid, sid)
    try:
        store.tracker.fields.write_scene_layer(cid, sid, _dump(body))
    except store.tracker.fields.FieldLayerError as exc:
        raise _bad_layer(exc) from exc
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound) as exc:
        # Deleted between the check and the write, in another tab.
        raise HTTPException(status_code=404, detail="scene not found") from exc
    return get_scene_tracker_fields(cid, sid)

"""Campaign HTTP endpoints: core domain."""


from __future__ import annotations

from fastapi import (
    HTTPException,
)
from fastapi.responses import Response

from ... import store
from ..models import (
    NewCampaign,
)
from ._router import router


@router.get("/campaigns/{cid}/calendar/months")
def get_calendar_months(cid: str, year: int):
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")
    cfg = store.calendars.read_calendar(store.campaigns.campaign_root(cid))
    try:
        return {"months": store.calendars.get_provider(cfg["primary"]).months(year)}
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/campaigns")
def post_campaign(body: NewCampaign):
    try:
        cid = store.campaigns.create_campaign(body.name, body.world,
                                              body.region, body.calendar,
                                              body.module, body.climate)
        store.config.mark_setup_done()   # see routes/worlds.post_world (#194)
        return {"id": cid}
    except store.worlds.WorldNotFound as exc:
        raise HTTPException(status_code=400, detail="world not found") from exc
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except store.climates.ClimateError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except store.modules.ModuleNotFound as exc:
        raise HTTPException(status_code=404, detail="module not found") from exc


@router.get("/campaigns/{cid}")
def get_campaign(cid: str):
    try:
        store.campaigns.ensure_campaign_slim(cid)
        out = store.campaigns.read_campaign(cid)
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    # embedded so the frontend needn't chain a world fetch after this one
    wid = out["meta"].get("world", "")
    out["meta"]["world_name"] = store.worlds.world_name(wid) or wid
    # Derived, like world_name above -- nothing about the cover is written into
    # campaign.md, whose unlocked read-modify-writers would race it.
    out["meta"]["cover"] = store.covers.cover_version(cid)
    return out


@router.get("/campaigns/{cid}/export.epub")
def export_campaign_epub(cid: str):
    try:
        blob, filename = store.epub.build_epub(cid)
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return Response(content=blob, media_type="application/epub+zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/campaigns/{cid}/export.md.zip")
def export_campaign_markdown(cid: str):
    try:
        blob, filename = store.export.build_markdown_bundle(cid)
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return Response(content=blob, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/campaigns/{cid}/export.html")
def export_campaign_html(cid: str):
    try:
        blob, filename = store.export.build_html(cid)
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return Response(content=blob, media_type="text/html",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/campaigns/{cid}/export.txt")
def export_campaign_text(cid: str):
    try:
        blob, filename = store.export.build_text(cid)
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return Response(content=blob, media_type="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/campaigns/{cid}/export.json")
def export_campaign_json(cid: str):
    try:
        blob, filename = store.export.build_json(cid)
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return Response(content=blob, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})



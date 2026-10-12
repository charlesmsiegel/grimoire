"""Scoped record art; Greetings retain their separate HTTP workflow."""

from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from .. import store
from .common import _upload_image_ext, field_provided, thumb_query
from .models import RecordArtLink

router = APIRouter()


def _response(body: dict) -> dict:
    for row in (*body["images"], *body["world_originals"]):
        row["thumb"] = row["url"].split("?", 1)[0] + thumb_query(quote(row["v"], safe=""))
    return body


@router.get("/worlds/{wid}/record-art/{kind}/{rid}")
def get_world_record_art(wid: str, kind: str, rid: str, version: str | None = None):
    try:
        return _response(store.record_art.read_world(wid, kind, rid, version))
    except store.record_art.ArtTargetNotFoundError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.get("/campaigns/{cid}/record-art/{kind}/{rid}")
def get_campaign_record_art(cid: str, kind: str, rid: str, version: str | None = None):
    try:
        return _response(store.record_art.read_campaign(cid, kind, rid, version))
    except store.record_art.ArtTargetNotFoundError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


async def _append(writer, sid: str, kind: str, rid: str, version: str | None, file: UploadFile):
    data = await file.read()
    ext = _upload_image_ext(data)
    try:
        return await run_in_threadpool(writer, sid, kind, rid, version, data, ext)
    except store.record_art.ArtTargetNotFoundError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/worlds/{wid}/record-art/{kind}/{rid}/images")
async def append_world_record_art(wid: str, kind: str, rid: str, version: str | None = None,
                                  file: UploadFile = File(...)):
    return await _append(store.record_art.append_world, wid, kind, rid, version, file)


@router.post("/campaigns/{cid}/record-art/{kind}/{rid}/images")
async def append_campaign_record_art(cid: str, kind: str, rid: str, version: str | None = None,
                                     file: UploadFile = File(...)):
    return await _append(store.record_art.append_campaign, cid, kind, rid, version, file)


def _reuse_request(writer, sid: str, kind: str, rid: str, version: str | None, body: RecordArtLink):
    try:
        return writer(sid, kind, rid, version, body.source, body.intent,
                      expected_primary_v=body.expected_primary_v,
                      check_primary=field_provided(body, "expected_primary_v"))
    except store.record_art.ArtConflictError as exc:
        raise HTTPException(409, detail={"kind": exc.kind, "detail": str(exc)}) from exc
    except (store.record_art.ArtTargetNotFoundError, FileNotFoundError) as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except store.image_descriptions.DescriptionUnavailableError as exc:
        raise HTTPException(503, detail={"kind": "description_unavailable", "detail": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/worlds/{wid}/record-art/{kind}/{rid}/images/link")
def reuse_world_record_art(wid: str, kind: str, rid: str, body: RecordArtLink,
                           version: str | None = None):
    return _reuse_request(store.record_art.reuse_world, wid, kind, rid, version, body)


@router.post("/campaigns/{cid}/record-art/{kind}/{rid}/images/link")
def reuse_campaign_record_art(cid: str, kind: str, rid: str, body: RecordArtLink,
                              version: str | None = None):
    return _reuse_request(store.record_art.reuse_campaign, cid, kind, rid, version, body)

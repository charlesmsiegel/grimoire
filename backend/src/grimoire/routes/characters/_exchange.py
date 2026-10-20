"""characters: exchange responsibilities."""

from __future__ import annotations

from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse

from ... import store
from ..common import _world_root_or_404
from ..models import ChubImportBody, ChubSourceBody
from . import _framing
from ._router import router as router


@router.get("/worlds/{wid}/characters/chub-unlinked")
def get_world_characters_chub_unlinked(wid: str):
    return {"versions": store.characters.find_unlinked_versions(_world_root_or_404(wid))}


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/chub-source")
def post_world_character_chub_source(wid: str, cid: str, vid: str, body: ChubSourceBody):
    root = _world_root_or_404(wid)
    url = store.chub.normalize_link(body.url)
    if url is None:
        raise HTTPException(status_code=400, detail="not a valid URL")
    try:
        store.characters.set_chub_source(root, cid, vid, url)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    except store.characters.VersionNotFound as exc:
        raise HTTPException(status_code=404, detail="version not found") from exc
    return {"chub_source": url}


@router.delete("/worlds/{wid}/characters/{cid}/versions/{vid}/chub-source")
def delete_world_character_chub_source(wid: str, cid: str, vid: str):
    root = _world_root_or_404(wid)
    try:
        store.characters.clear_chub_source(root, cid, vid)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    except store.characters.VersionNotFound as exc:
        raise HTTPException(status_code=404, detail="version not found") from exc
    return {"chub_source": ""}


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/chub-gallery")
def post_world_character_chub_gallery(wid: str, cid: str, vid: str):
    root = _world_root_or_404(wid)
    try:
        node = store.characters.resolve_chub_node(root, cid, vid)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    except store.characters.VersionNotFound as exc:
        raise HTTPException(status_code=404, detail="version not found") from exc
    except store.chub.ChubFetchError as exc:
        raise HTTPException(status_code=404, detail="could not fetch from chub.ai") from exc

    def event_stream():
        for ev in store.characters.download_chub_gallery_stream(root, cid, vid, node):
            yield _framing._sse(ev)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/chub-lorebooks")
def post_world_character_chub_lorebooks(wid: str, cid: str, vid: str):
    root = _world_root_or_404(wid)
    try:
        return store.characters.download_chub_lorebooks(root, cid, vid)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    except store.characters.VersionNotFound as exc:
        raise HTTPException(status_code=404, detail="version not found") from exc
    except store.chub.ChubFetchError as exc:
        raise HTTPException(status_code=404, detail="could not fetch from chub.ai") from exc


_EXPORT_MEDIA = {"json": "application/json", "png": "image/png", "charx": "application/zip"}


def _import_name_check(wid: str, into: str | None):
    def check(name: str) -> None:
        try:
            store.actor_names.require_unique(name, scope="world", scope_id=wid,
                                             actor_ref=f"characters:{into}" if into else None)
        except store.actor_names.ActorNameError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
    return check


@router.post("/worlds/{wid}/characters/import")
async def post_character_import(wid: str, file: UploadFile = File(...),
                                format: str = Form(...), into: str | None = Form(None),
                                name: str | None = Form(None),
                                version_name: str | None = Form(None)):
    """Import a card. `into` adds it as a version of an existing character;
    `name` names a new character and `version_name` names the version, which is
    otherwise derived from the card and so is the same for every version."""
    root = _world_root_or_404(wid)
    data = await file.read()
    try:
        cid, vid = store.characters.import_card(root, data, format, into_cid=into, name=name,
                                                version_name=version_name,
                                                validate_name=_import_name_check(wid, into))
    except store.cards.CardParseError as exc:
        raise HTTPException(status_code=400, detail=f"could not parse card: {exc}") from exc
    except store.characters.CharacterNotFound as err:
        raise HTTPException(status_code=404, detail="character not found") from err
    return {"character": cid, "version": vid}


@router.post("/worlds/{wid}/characters/import/chub")
def post_character_import_chub(wid: str, body: ChubImportBody):
    root = _world_root_or_404(wid)
    try:
        return store.characters.import_from_chub(root, body.url, into_cid=body.into,
                                                 into_vid=body.into_version,
                                                 version_name=body.version_name,
                                                 validate_name=_import_name_check(wid, body.into))
    except store.chub.ChubParseError as exc:
        raise HTTPException(status_code=400, detail="not a valid URL") from exc
    except store.chub.ChubFetchError as exc:
        raise HTTPException(status_code=404, detail="could not fetch a character card from that URL") from exc
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc


@router.get("/worlds/{wid}/characters/{cid}/versions/{vid}/export")
def get_character_export(wid: str, cid: str, vid: str, format: str = "json"):
    root = _world_root_or_404(wid)
    if format not in _EXPORT_MEDIA:
        raise HTTPException(status_code=400, detail="unknown format")
    try:
        blob, filename = store.characters.export_card(root, cid, vid, format)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    except store.characters.VersionNotFound as exc:
        raise HTTPException(status_code=404, detail="version not found") from exc
    # Named like the campaign exports: the frontend offers these as plain
    # download links, and without the header the browser saves them as "export".
    return Response(content=blob, media_type=_EXPORT_MEDIA[format],
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/localize")
def post_character_localize(wid: str, cid: str, vid: str):
    root = _world_root_or_404(wid)
    try:
        card = store.characters.read_card(root, cid, vid)
    except store.characters.CharacterNotFound as err:
        raise HTTPException(status_code=404, detail="character not found") from err
    except store.characters.VersionNotFound as err:
        raise HTTPException(status_code=404, detail="version not found") from err

    def event_stream():
        # localize_card rewrites `card` in place field-by-field; save whenever any
        # rewrite landed, including on a mid-stream error or client disconnect
        # (GeneratorExit skips the except but runs the finally).
        state = {"changed": False, "saved": False}

        def save():
            if state["changed"] and not state["saved"]:
                store.characters.update_version(root, cid, vid, card)
                state["saved"] = True

        try:
            for ev in store.localize.localize_card(card, root, cid, vid, wid):
                if ev.get("applied") or ("summary" in ev and ev["summary"].get("localized")):
                    state["changed"] = True
                yield _framing._sse(ev)
            save()  # normal completion: a failed save surfaces as an error frame
        except Exception as exc:  # noqa: BLE001 — surface a stream error like the chat routes
            yield _framing._sse({"error": {"detail": str(exc), "kind": "localize"}})
        finally:
            try:
                save()
            except Exception:  # noqa: BLE001 — a disconnected client can't be told
                pass

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/lorebook/import")
def post_character_lorebook_import(wid: str, cid: str, vid: str):
    root = _world_root_or_404(wid)
    try:
        card = store.characters.read_card(root, cid, vid)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    except store.characters.VersionNotFound as exc:
        raise HTTPException(status_code=404, detail="version not found") from exc
    book = card.get("data", {}).get("character_book") or {}
    created = store.lorebook.commit(root, store.lorebook.from_character_book(book))
    return {"created": created}

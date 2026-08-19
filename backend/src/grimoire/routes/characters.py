"""World characters: versions, taglines, voice anchors, Chub links, card
import/export, localization and per-version images."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse

from .. import store
from ..llm import LLMClient
from ..llm_errors import LLMError
from .common import (_bounded_call, _require_connection, _serve_image, _upload_image_ext,
                     _world_char_version_or_404, _world_root_or_404, get_llm)
from .models import (AvatarFocus, CharacterBirthdate, CharacterCreate, ChubImportBody,
                     ChubSourceBody, DefaultVersion, NameBody, TaglineSave, VersionCreate, VersionUpdate,
                     VoiceAnchorSave)

router = APIRouter()


@router.get("/worlds/{wid}/characters")
def get_world_characters(wid: str):
    return store.characters.list_characters(_world_root_or_404(wid))


@router.post("/worlds/{wid}/characters")
def post_world_character(wid: str, body: CharacterCreate):
    cid, vid = store.characters.create_character(
        _world_root_or_404(wid), body.name, body.version_name, body.card
    )
    return {"character": cid, "version": vid}


@router.get("/worlds/{wid}/characters/chub-unlinked")
def get_world_characters_chub_unlinked(wid: str):
    return {"versions": store.characters.find_unlinked_versions(_world_root_or_404(wid))}


@router.get("/worlds/{wid}/characters/{cid}")
def get_world_character(wid: str, cid: str):
    try:
        return store.characters.read_character(_world_root_or_404(wid), cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")


@router.put("/worlds/{wid}/characters/{cid}")
def put_world_character(wid: str, cid: str, body: DefaultVersion):
    try:
        store.characters.set_default_version(_world_root_or_404(wid), cid, body.default_version)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.put("/worlds/{wid}/characters/{cid}/name")
def put_world_character_name(wid: str, cid: str, body: NameBody):
    """Rename the container (#13). The card's own `data.name` is saved with the
    card; this is the name the grid, the cast panel and the `meta.name` prompt
    sections read, and the two used to be unable to agree."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        store.characters.set_name(_world_root_or_404(wid), cid, name)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"ok": True}


@router.put("/worlds/{wid}/characters/{cid}/birthdate")
def put_world_character_birthdate(wid: str, cid: str, body: CharacterBirthdate):
    try:
        store.characters.set_birthdate(_world_root_or_404(wid), cid, body.birthdate)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"ok": True}


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/chub-source")
def post_world_character_chub_source(wid: str, cid: str, vid: str, body: ChubSourceBody):
    root = _world_root_or_404(wid)
    url = store.chub.normalize_link(body.url)
    if url is None:
        raise HTTPException(status_code=400, detail="not a valid URL")
    try:
        store.characters.set_chub_source(root, cid, vid, url)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"chub_source": url}


@router.delete("/worlds/{wid}/characters/{cid}/versions/{vid}/chub-source")
def delete_world_character_chub_source(wid: str, cid: str, vid: str):
    root = _world_root_or_404(wid)
    try:
        store.characters.clear_chub_source(root, cid, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"chub_source": ""}


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/chub-gallery")
def post_world_character_chub_gallery(wid: str, cid: str, vid: str):
    root = _world_root_or_404(wid)
    try:
        node = store.characters.resolve_chub_node(root, cid, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except store.chub.ChubFetchError:
        raise HTTPException(status_code=404, detail="could not fetch from chub.ai")

    def event_stream():
        for ev in store.characters.download_chub_gallery_stream(root, cid, vid, node):
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/chub-lorebooks")
def post_world_character_chub_lorebooks(wid: str, cid: str, vid: str):
    root = _world_root_or_404(wid)
    try:
        return store.characters.download_chub_lorebooks(root, cid, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except store.chub.ChubFetchError:
        raise HTTPException(status_code=404, detail="could not fetch from chub.ai")


@router.delete("/worlds/{wid}/characters/{cid}")
def delete_world_character(wid: str, cid: str):
    root = _world_root_or_404(wid)
    try:
        store.characters.delete_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    store.overlay.forget_world_record(root, "characters", cid)   # #225
    return {"ok": True}


@router.post("/worlds/{wid}/characters/{cid}/versions")
def post_world_version(wid: str, cid: str, body: VersionCreate):
    try:
        vid = store.characters.create_version(_world_root_or_404(wid), cid, body.name, body.card)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"version": vid}


@router.put("/worlds/{wid}/characters/{cid}/versions/{vid}")
def put_world_version(wid: str, cid: str, vid: str, body: VersionUpdate):
    try:
        store.characters.update_version(_world_root_or_404(wid), cid, vid, body.card)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/worlds/{wid}/characters/{cid}/versions/{vid}")
def delete_world_version(wid: str, cid: str, vid: str):
    try:
        store.characters.delete_version(_world_root_or_404(wid), cid, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.get("/worlds/{wid}/characters/{cid}/tagline")
def get_character_tagline(wid: str, cid: str):
    root = _world_root_or_404(wid)
    try:
        store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"tagline": store.taglines.read(root, cid)}


@router.put("/worlds/{wid}/characters/{cid}/tagline")
def put_character_tagline(wid: str, cid: str, body: TaglineSave):
    root = _world_root_or_404(wid)
    try:
        store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    store.taglines.write(root, cid, body.tagline)
    return {"ok": True}


@router.post("/worlds/{wid}/characters/{cid}/tagline/generate")
async def post_character_tagline_generate(wid: str, cid: str,
                                          client: LLMClient = Depends(get_llm)):
    root = _world_root_or_404(wid)
    conn = _require_connection()
    try:
        ch = store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    card = store.characters.read_card(root, cid, ch["meta"]["default_version"])
    messages = store.taglines.build_prompt(card["data"])
    try:
        with store.usage.meter("tagline") as m:
            text = await _bounded_call(client.complete(messages, conn, m.usage))
    except LLMError as exc:
        raise HTTPException(status_code=502, detail={"detail": exc.detail, "kind": exc.kind})
    # Preview only — the caller persists via PUT on Save, so Generate-then-cancel
    # (e.g. the import popup's Skip) leaves nothing written.
    return {"tagline": store.taglines.parse_output(text)}


@router.get("/worlds/{wid}/characters/{cid}/voice-anchor")
def get_character_voice_anchor(wid: str, cid: str):
    root = _world_root_or_404(wid)
    try:
        store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"voice_anchor": store.voice_anchors.read(root, cid)}


@router.put("/worlds/{wid}/characters/{cid}/voice-anchor")
def put_character_voice_anchor(wid: str, cid: str, body: VoiceAnchorSave):
    """Set (or, with a blank body, remove) the anchor absorb judges drift against.

    Removing it is a real operation, not a no-op: an anchorless character is not
    judged at all, so clearing the text is how a user opts a character back out
    of drift detection."""
    root = _world_root_or_404(wid)
    try:
        store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    store.voice_anchors.write(root, cid, body.voice_anchor)
    return {"ok": True}


@router.post("/worlds/{wid}/characters/{cid}/voice-anchor/generate")
async def post_character_voice_anchor_generate(wid: str, cid: str,
                                               client: LLMClient = Depends(get_llm)):
    root = _world_root_or_404(wid)
    conn = _require_connection()
    try:
        ch = store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    card = store.characters.read_card(root, cid, ch["meta"]["default_version"])
    # Version PUT accepts ANY dict as a card and writes it unchanged, so `{}`
    # and `{"data": ["speech"]}` are both supported state. `.get` alone is not
    # enough -- a truthy non-object reaches the template, where `card.get(...)`
    # raises and 500s the request before the LLM is ever called. The template
    # already renders "(none)" for every missing field, which is a far better
    # answer -- the draft is a starting point the user edits anyway.
    data = card.get("data")
    messages = store.voice_anchors.build_prompt(data if isinstance(data, dict) else {})
    try:
        with store.usage.meter("voice-anchor") as m:
            text = await _bounded_call(client.complete(messages, conn, m.usage))
    except LLMError as exc:
        raise HTTPException(status_code=502, detail={"detail": exc.detail, "kind": exc.kind})
    # Preview only, like tagline/generate — the caller persists via PUT on Save,
    # so an anchor is never written without review (#59).
    return {"voice_anchor": store.voice_anchors.parse_output(text)}


_EXPORT_MEDIA = {"json": "application/json", "png": "image/png", "charx": "application/zip"}


@router.post("/worlds/{wid}/characters/import")
async def post_character_import(wid: str, file: UploadFile = File(...),
                                format: str = Form(...), into: str | None = Form(None),
                                name: str | None = Form(None)):
    root = _world_root_or_404(wid)
    data = await file.read()
    try:
        cid, vid = store.characters.import_card(root, data, format, into_cid=into, name=name)
    except store.cards.CardParseError as exc:
        raise HTTPException(status_code=400, detail=f"could not parse card: {exc}")
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"character": cid, "version": vid}


@router.post("/worlds/{wid}/characters/import/chub")
def post_character_import_chub(wid: str, body: ChubImportBody):
    root = _world_root_or_404(wid)
    try:
        return store.characters.import_from_chub(root, body.url, into_cid=body.into,
                                                 into_vid=body.into_version)
    except store.chub.ChubParseError:
        raise HTTPException(status_code=400, detail="not a valid URL")
    except store.chub.ChubFetchError:
        raise HTTPException(status_code=404, detail="could not fetch a character card from that URL")
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")


@router.get("/worlds/{wid}/characters/{cid}/versions/{vid}/export")
def get_character_export(wid: str, cid: str, vid: str, format: str = "json"):
    root = _world_root_or_404(wid)
    if format not in _EXPORT_MEDIA:
        raise HTTPException(status_code=400, detail="unknown format")
    try:
        blob, filename = store.characters.export_card(root, cid, vid, format)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    # Named like the campaign exports: the frontend offers these as plain
    # download links, and without the header the browser saves them as "export".
    return Response(content=blob, media_type=_EXPORT_MEDIA[format],
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/localize")
def post_character_localize(wid: str, cid: str, vid: str):
    root = _world_root_or_404(wid)
    try:
        card = store.characters.read_card(root, cid, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")

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
                yield f"data: {json.dumps(ev)}\n\n"
            save()  # normal completion: a failed save surfaces as an error frame
        except Exception as exc:  # noqa: BLE001 — surface a stream error like the chat routes
            yield f"data: {json.dumps({'error': {'detail': str(exc), 'kind': 'localize'}})}\n\n"
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
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    book = card.get("data", {}).get("character_book") or {}
    created = store.lorebook.commit(root, store.lorebook.from_character_book(book))
    return {"created": created}


@router.get("/worlds/{wid}/characters/{cid}/versions/{vid}/images")
def list_world_images(wid: str, cid: str, vid: str):
    return store.assets.list_images(_world_root_or_404(wid), cid, vid)


@router.get("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}")
def get_world_image(wid: str, cid: str, vid: str, name: str, request: Request):
    return _serve_image(_world_root_or_404(wid), cid, vid, name, request=request)


@router.put("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}")
async def put_world_image(wid: str, cid: str, vid: str, name: str, file: UploadFile = File(...)):
    root = _world_char_version_or_404(wid, cid, vid)
    data = await file.read()
    ext = _upload_image_ext(data)  # the bytes name the type, not `file.filename` (#321)
    try:
        stored = store.assets.put_image(root, cid, vid, name, data, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": name, "ext": stored}


@router.delete("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}")
def delete_world_image(wid: str, cid: str, vid: str, name: str):
    # Gated on the character and the version, not on the image: removing an
    # image that is already gone is the caller getting what they asked for, but
    # removing one from a character that does not exist is a typo worth reporting.
    store.assets.delete_image(_world_char_version_or_404(wid, cid, vid), cid, vid, name)
    return {"ok": True}


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}/promote")
def promote_world_image(wid: str, cid: str, vid: str, name: str):
    root = _world_char_version_or_404(wid, cid, vid)
    try:
        store.assets.promote_image(root, cid, vid, name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    except ValueError as exc:
        # an externally-placed file whose extension we never accepted for upload
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.put("/worlds/{wid}/characters/{cid}/versions/{vid}/images/avatar/focus")
def put_world_avatar_focus(wid: str, cid: str, vid: str, body: AvatarFocus):
    root = _world_char_version_or_404(wid, cid, vid)
    if store.assets.image_path(root, cid, vid, store.assets.AVATAR) is None:
        raise HTTPException(status_code=404, detail="image not found")
    store.assets.write_focus(root, cid, vid, body.focus)
    return {"ok": True}

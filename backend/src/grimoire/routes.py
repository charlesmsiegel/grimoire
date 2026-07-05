"""HTTP surface for grimoire."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from . import store
from .openrouter import OpenRouterClient, OpenRouterError

router = APIRouter()
_openrouter = OpenRouterClient()


def get_openrouter() -> OpenRouterClient:
    return _openrouter


# ---- models ----
class ConfigUpdate(BaseModel):
    model: str | None = None
    theme: str | None = None
    openrouter_key: str | None = None
    system_prompt: str | None = None
    quote_color: str | None = None
    user_label: str | None = None
    assistant_label: str | None = None


class DataDirUpdate(BaseModel):
    data_dir: str | None = None


class RegenerateBody(BaseModel):
    guidance: str | None = None


class NameBody(BaseModel):
    name: str


class NewCampaign(BaseModel):
    name: str
    world: str
    region: str | None = None


class PickBody(BaseModel):
    version: str


class MarkBody(BaseModel):
    status: str  # "completed" | "skipped" | "none" — validated in the store


class EntityCreate(BaseModel):
    name: str
    body: str = ""
    keys: str = ""
    owners: str = ""


class EntityUpdate(BaseModel):
    name: str | None = None
    body: str | None = None
    keys: str | None = None
    owners: str | None = None


class CharacterCreate(BaseModel):
    name: str
    version_name: str = "default"
    card: dict | None = None


class VersionCreate(BaseModel):
    name: str
    card: dict


class VersionUpdate(BaseModel):
    card: dict


class DefaultVersion(BaseModel):
    default_version: str


class CharacterBirthdate(BaseModel):
    birthdate: str = ""


class ChubImportBody(BaseModel):
    url: str
    into: str | None = None
    into_version: str | None = None


class ChubSourceBody(BaseModel):
    url: str


class TaglineSave(BaseModel):
    tagline: str = ""


class AvatarFocus(BaseModel):
    focus: int


class PCCreate(BaseModel):
    name: str
    tags: list[str] = []
    version_name: str = "default"
    persona: dict | None = None


class PCUpdate(BaseModel):
    default_version: str | None = None
    tags: list[str] | None = None


class PersonaVersionCreate(BaseModel):
    name: str
    persona: dict


class PersonaVersionUpdate(BaseModel):
    persona: dict


class Ref(BaseModel):
    kind: str
    id: str


class RefList(BaseModel):
    refs: list[Ref]


class NewScene(BaseModel):
    title: str | None = None
    suggested_date: str | None = None
    pcless: bool = False


class RenameScene(BaseModel):
    title: str


class ChronicleSave(BaseModel):
    one_line: str = ""
    summary: str = ""
    keywords: list[str] = []
    timeline_events: list[dict] = []
    edits: list[dict] = []


class ChatTurn(BaseModel):
    content: str


class Appear(BaseModel):
    kind: str = "characters"
    id: str
    version: str | None = None
    role: str | None = None


class SceneLocation(BaseModel):
    location: str


class SceneDatetime(BaseModel):
    datetime: str


class CalendarConfig(BaseModel):
    primary: dict
    secondary: dict | None = None
    confirmed: bool = False


class EditMessage(BaseModel):
    content: str


class Dismiss(BaseModel):
    character: str


class GreetingCreate(BaseModel):
    name: str
    character: str
    version: str
    body: str = ""
    requires_tags: list[str] = []
    predecessor_join: str = "all"
    present: list[str] | None = None


class SubjectsBody(BaseModel):
    subjects: list[str] = []


class CopyFromGreeting(BaseModel):
    gid: str
    name: str
    slot: str


class GreetingUpdate(BaseModel):
    name: str | None = None
    body: str | None = None
    requires_tags: list[str] | None = None
    predecessor_join: str | None = None
    present: list[str] | None = None


class Edges(BaseModel):
    leads_to: list[str] | None = None
    excludes: list[str] | None = None


class ImportGreetings(BaseModel):
    character: str
    version: str


class StartFromGreeting(BaseModel):
    greeting: str


class Opener(BaseModel):
    prompt: str


class FirstPost(BaseModel):
    text: str


class LoreEntry(BaseModel):
    name: str
    keys: list[str] = []
    body: str = ""
    category: str = "lore"


class LorebookCommit(BaseModel):
    entries: list[LoreEntry]


# ---- config ----
def _public_config(cfg: dict[str, str]) -> dict:
    return {"model": cfg["model"], "theme": cfg["theme"], "key_set": bool(cfg["openrouter_key"]),
            "system_prompt": cfg.get("system_prompt", ""), "quote_color": cfg.get("quote_color", "off"),
            "user_label": cfg.get("user_label", "You"),
            "assistant_label": cfg.get("assistant_label", "Grimoire")}


@router.get("/config")
def get_config():
    return _public_config(store.read_config())


@router.put("/config")
def put_config(update: ConfigUpdate):
    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    return _public_config(store.write_config(**fields))


@router.get("/config/data-dir")
def get_data_dir():
    return store.data_dir_info()


@router.put("/config/data-dir")
def put_data_dir(update: DataDirUpdate):
    try:
        store.set_data_dir(update.data_dir)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"detail": str(exc), "kind": "data_dir"})
    return store.data_dir_info()


# ---- worlds ----
@router.get("/worlds")
def get_worlds():
    return store.worlds.list_worlds()


@router.post("/worlds")
def post_world(body: NameBody):
    return {"id": store.worlds.create_world(body.name)}


@router.get("/worlds/{wid}")
def get_world(wid: str):
    try:
        return store.worlds.read_world(wid)
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=404, detail="world not found")


@router.put("/worlds/{wid}")
def put_world(wid: str, body: NameBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        store.worlds.rename_world(wid, name)
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=404, detail="world not found")
    return {"id": wid, "name": name}


@router.delete("/worlds/{wid}")
def delete_world(wid: str):
    try:
        store.worlds.delete_world(wid)
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=404, detail="world not found")
    return {"ok": True}


@router.get("/worlds/{wid}/campaigns")
def get_world_campaigns(wid: str):
    return store.sync.campaigns_for_world(wid)


# ---- world tags (declared before the generic /{kind} routes) ----
@router.get("/worlds/{wid}/tags")
def get_world_tags(wid: str):
    return store.tags.read_tags(_world_root_or_404(wid))


@router.post("/worlds/{wid}/tags")
def post_world_tag(wid: str, body: NameBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    return {"id": store.tags.add_tag(_world_root_or_404(wid), name)}


@router.put("/worlds/{wid}/tags/{tid}")
def put_world_tag(wid: str, tid: str, body: NameBody):
    try:
        store.tags.rename_tag(_world_root_or_404(wid), tid, body.name.strip())
    except store.tags.TagNotFound:
        raise HTTPException(status_code=404, detail="tag not found")
    return {"id": tid, "name": body.name.strip()}


@router.delete("/worlds/{wid}/tags/{tid}")
def delete_world_tag(wid: str, tid: str):
    try:
        store.tags.delete_tag(_world_root_or_404(wid), tid)
    except store.tags.TagNotFound:
        raise HTTPException(status_code=404, detail="tag not found")
    return {"ok": True}


# ---- world PCs (declared before the generic /{kind} routes) ----
def _validate_tags(root, tags: list[str]) -> None:
    for t in tags:
        if not store.tags.has_tag(root, t):
            raise HTTPException(status_code=400, detail=f"unknown tag: {t}")


@router.get("/worlds/{wid}/pcs")
def get_world_pcs(wid: str):
    return store.pcs.list_pcs(_world_root_or_404(wid))


@router.post("/worlds/{wid}/pcs")
def post_world_pc(wid: str, body: PCCreate):
    root = _world_root_or_404(wid)
    _validate_tags(root, body.tags)
    pid, vid = store.pcs.create_pc(root, body.name, body.tags, body.version_name, body.persona)
    return {"pc": pid, "version": vid}


@router.get("/worlds/{wid}/pcs/{pid}")
def get_world_pc(wid: str, pid: str):
    try:
        return store.pcs.read_pc(_world_root_or_404(wid), pid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")


@router.put("/worlds/{wid}/pcs/{pid}")
def put_world_pc(wid: str, pid: str, body: PCUpdate):
    root = _world_root_or_404(wid)
    try:
        if body.tags is not None:
            _validate_tags(root, body.tags)
            store.pcs.set_tags(root, pid, body.tags)
        if body.default_version is not None:
            store.pcs.set_default_version(root, pid, body.default_version)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/worlds/{wid}/pcs/{pid}")
def delete_world_pc(wid: str, pid: str):
    try:
        store.pcs.delete_pc(_world_root_or_404(wid), pid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    return {"ok": True}


@router.post("/worlds/{wid}/pcs/{pid}/versions")
def post_pc_version(wid: str, pid: str, body: PersonaVersionCreate):
    try:
        vid = store.pcs.create_version(_world_root_or_404(wid), pid, body.name, body.persona)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    return {"version": vid}


@router.put("/worlds/{wid}/pcs/{pid}/versions/{vid}")
def put_pc_version(wid: str, pid: str, vid: str, body: PersonaVersionUpdate):
    try:
        store.pcs.update_version(_world_root_or_404(wid), pid, vid, body.persona)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/worlds/{wid}/pcs/{pid}/versions/{vid}")
def delete_pc_version(wid: str, pid: str, vid: str):
    try:
        store.pcs.delete_version(_world_root_or_404(wid), pid, vid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


# ---- world characters (dedicated; declared before the generic /{kind} routes) ----
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
    try:
        store.characters.delete_character(_world_root_or_404(wid), cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
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
                                          client: OpenRouterClient = Depends(get_openrouter)):
    root = _world_root_or_404(wid)
    cfg = store.read_config()
    _require_key(cfg)
    try:
        ch = store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    card = store.characters.read_card(root, cid, ch["meta"]["default_version"])
    messages = store.taglines.build_prompt(card["data"])
    try:
        text = await client.complete(messages, cfg["model"], cfg["openrouter_key"])
    except OpenRouterError as exc:
        raise HTTPException(status_code=502, detail={"detail": exc.detail, "kind": exc.kind})
    # Preview only — the caller persists via PUT on Save, so Generate-then-cancel
    # (e.g. the import popup's Skip) leaves nothing written.
    return {"tagline": store.taglines.parse_output(text)}


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
        blob = store.characters.export_card(root, cid, vid, format)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return Response(content=blob, media_type=_EXPORT_MEDIA[format])


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


_IMAGE_MEDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif", "webp": "image/webp"}


def _serve_image(root, cid: str, vid: str, name: str, base: str = "characters",
                 request: Request | None = None):
    p = store.assets.image_path(root, cid, vid, name, base)
    if p is None:
        raise HTTPException(status_code=404, detail="image not found")
    # Bare URLs are no-cache: promotions swap file contents under stable URLs,
    # so the browser must revalidate — with an ETag that's a 304, not a re-download.
    # A `?v=` URL (built from list responses' version tokens) names one exact
    # content state, so it caches immutable: zero requests on later renders.
    st = p.stat()
    etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
    versioned = request is not None and "v" in request.query_params
    cache = "public, max-age=31536000, immutable" if versioned else "no-cache"
    headers = {"Cache-Control": cache, "ETag": etag}
    if request is not None and etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers=headers)
    # ?w= asks for a downscaled variant — tiles shouldn't pull multi-MB originals.
    # An undecodable source just serves the original bytes.
    if request is not None and (w := request.query_params.get("w", "")).isdigit():
        tp = store.thumbs.thumbnail(p, max(16, min(1024, int(w))))
        if tp is not None:
            return Response(content=tp.read_bytes(), media_type="image/webp", headers=headers)
    ext = p.suffix.lstrip(".").lower()
    return Response(content=p.read_bytes(),
                    media_type=_IMAGE_MEDIA.get(ext, "application/octet-stream"),
                    headers=headers)


@router.get("/worlds/{wid}/characters/{cid}/versions/{vid}/images")
def list_world_images(wid: str, cid: str, vid: str):
    return store.assets.list_images(_world_root_or_404(wid), cid, vid)


@router.get("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}")
def get_world_image(wid: str, cid: str, vid: str, name: str, request: Request):
    return _serve_image(_world_root_or_404(wid), cid, vid, name, request=request)


@router.put("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}")
async def put_world_image(wid: str, cid: str, vid: str, name: str, file: UploadFile = File(...)):
    root = _world_root_or_404(wid)
    data = await file.read()
    fn = file.filename or ""
    ext = fn.rsplit(".", 1)[-1] if "." in fn else ""
    try:
        stored = store.assets.put_image(root, cid, vid, name, data, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": name, "ext": stored}


@router.delete("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}")
def delete_world_image(wid: str, cid: str, vid: str, name: str):
    store.assets.delete_image(_world_root_or_404(wid), cid, vid, name)
    return {"ok": True}


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}/promote")
def promote_world_image(wid: str, cid: str, vid: str, name: str):
    try:
        store.assets.promote_image(_world_root_or_404(wid), cid, vid, name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    return {"ok": True}


@router.put("/worlds/{wid}/characters/{cid}/versions/{vid}/images/avatar/focus")
def put_world_avatar_focus(wid: str, cid: str, vid: str, body: AvatarFocus):
    root = _world_root_or_404(wid)
    if store.assets.image_path(root, cid, vid, store.assets.AVATAR) is None:
        raise HTTPException(status_code=404, detail="image not found")
    store.assets.write_focus(root, cid, vid, body.focus)
    return {"ok": True}


# ---- world greetings (declared before the generic /{kind} routes) ----
@router.get("/worlds/{wid}/greetings")
def get_world_greetings(wid: str):
    return store.greetings.list_greetings(_world_root_or_404(wid))


@router.post("/worlds/{wid}/greetings")
def post_world_greeting(wid: str, body: GreetingCreate):
    gid = store.greetings.create_greeting(_world_root_or_404(wid), body.name, body.character,
                                          body.version, body.body, body.requires_tags,
                                          body.predecessor_join, present=body.present)
    return {"id": gid}


@router.post("/worlds/{wid}/greetings/import")
def post_world_greetings_import(wid: str, body: ImportGreetings):
    root = _world_root_or_404(wid)
    try:
        gids = store.greetings.import_from_character(root, body.character, body.version)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"greetings": gids}


@router.get("/worlds/{wid}/greetings/{gid}")
def get_world_greeting(wid: str, gid: str):
    root = _world_root_or_404(wid)
    try:
        g = store.greetings.read_greeting(root, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    plotmap = store.greetings.read_plotmap(root)
    g["edges"] = store.greetings.edges_of(plotmap, gid)
    g["predecessors"] = store.greetings.predecessors_of(plotmap, gid)
    return g


@router.put("/worlds/{wid}/greetings/{gid}")
def put_world_greeting(wid: str, gid: str, body: GreetingUpdate):
    try:
        store.greetings.update_greeting(_world_root_or_404(wid), gid, name=body.name,
                                        body=body.body, requires_tags=body.requires_tags,
                                        predecessor_join=body.predecessor_join, present=body.present)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    return {"ok": True}


@router.put("/worlds/{wid}/greetings/{gid}/edges")
def put_world_greeting_edges(wid: str, gid: str, body: Edges):
    root = _world_root_or_404(wid)
    try:
        store.greetings.read_greeting(root, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    store.greetings.set_edges(root, gid, body.leads_to, body.excludes)
    return {"ok": True}


@router.delete("/worlds/{wid}/greetings/{gid}")
def delete_world_greeting(wid: str, gid: str):
    try:
        store.greetings.delete_greeting(_world_root_or_404(wid), gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    return {"ok": True}


# ---- greeting image subjects (who appears in each localized image) ----
def _greeting_or_404(root, gid: str) -> None:
    try:
        store.greetings.read_greeting(root, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")


@router.get("/worlds/{wid}/greetings/{gid}/subjects")
def get_world_greeting_subjects(wid: str, gid: str):
    root = _world_root_or_404(wid)
    _greeting_or_404(root, gid)
    return store.image_subjects.read_subjects(root, gid)


@router.get("/worlds/{wid}/greetings/{gid}/images/{name}/subjects")
def get_world_greeting_image_subjects(wid: str, gid: str, name: str):
    root = _world_root_or_404(wid)
    _greeting_or_404(root, gid)
    if store.assets.image_path(root, gid, "default", name, base="greetings") is None:
        raise HTTPException(status_code=404, detail="image not found")
    return {"subjects": store.image_subjects.read_subjects(root, gid).get(name, [])}


@router.put("/worlds/{wid}/greetings/{gid}/images/{name}/subjects")
def put_world_greeting_image_subjects(wid: str, gid: str, name: str, body: SubjectsBody):
    root = _world_root_or_404(wid)
    _greeting_or_404(root, gid)
    if store.assets.image_path(root, gid, "default", name, base="greetings") is None:
        raise HTTPException(status_code=404, detail="image not found")
    known = {c["id"] for c in store.characters.list_characters(root)}
    bad = [c for c in body.subjects if c not in known]
    if bad:
        raise HTTPException(status_code=400, detail=f"unknown characters: {bad}")
    store.image_subjects.set_image_subjects(root, gid, name, body.subjects)
    return {"ok": True}


_THUMB_W = 320  # tiles render at 96-154px; 320 covers retina


def _greeting_image_urls(root, wid: str, a: dict) -> dict:
    """Versioned full + thumbnail URLs for one greeting image: both cache
    immutable, and the thumb keeps a 70-tile gallery from pulling 100MB+
    of full-resolution art."""
    base = f"/api/worlds/{wid}/greetings/{a['gid']}/images/{a['name']}"
    p = store.assets.image_path(root, a["gid"], "default", a["name"], base="greetings")
    if p is None:  # vanished between sweep and stat: bare URLs, still renderable
        return {"url": base, "thumb": base}
    v = store.assets.image_version(p)
    return {"url": f"{base}?v={v}", "thumb": f"{base}?w={_THUMB_W}&v={v}"}


@router.get("/worlds/{wid}/subjects/untagged")
def get_world_untagged_images(wid: str):
    root = _world_root_or_404(wid)
    names = {g["id"]: g["name"] for g in store.greetings.list_greetings(root)}
    return [{**a, "greeting_name": names.get(a["gid"], a["gid"]),
             **_greeting_image_urls(root, wid, a)}
            for a in store.image_subjects.untagged(root)]


@router.get("/worlds/{wid}/characters/{cid}/appearances")
def get_world_character_appearances(wid: str, cid: str):
    root = _world_root_or_404(wid)
    names = {g["id"]: g["name"] for g in store.greetings.list_greetings(root)}
    return [{**a, "greeting_name": names.get(a["gid"], a["gid"]),
             **_greeting_image_urls(root, wid, a)}
            for a in store.image_subjects.appearances(root, cid)]


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/images/copy-from-greeting")
def post_copy_image_from_greeting(wid: str, cid: str, vid: str, body: CopyFromGreeting):
    root = _world_root_or_404(wid)
    try:
        stored = store.image_subjects.copy_to_character(root, body.gid, body.name, cid, vid, body.slot)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="source image not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    p = store.assets.image_path(root, cid, vid, stored)
    return {"name": stored, "ext": p.suffix.lstrip(".").lower() if p else ""}


# ---- world lorebook import (declared before the generic /{kind} routes) ----
@router.post("/worlds/{wid}/lorebook/parse")
async def post_lorebook_parse(wid: str, file: UploadFile = File(...), format: str = Form(...)):
    _world_root_or_404(wid)
    data = await file.read()
    try:
        return {"entries": store.lorebook.parse(data, format)}
    except (store.lorebook.LorebookError, store.cards.CardParseError) as exc:
        raise HTTPException(status_code=400, detail=f"could not parse: {exc}")


@router.post("/worlds/{wid}/lorebook/import")
def post_lorebook_import(wid: str, body: LorebookCommit):
    root = _world_root_or_404(wid)
    try:
        created = store.lorebook.commit(root, [e.model_dump() for e in body.entries])
    except store.lorebook.LorebookError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"created": created}


# ---- generic entity CRUD (shared by worlds and campaigns) ----
def _world_root_or_404(wid: str):
    if not store.worlds.world_meta_path(wid).exists():
        raise HTTPException(status_code=404, detail="world not found")
    return store.worlds.world_root(wid)


def _campaign_root_or_404(cid: str):
    try:
        store.campaigns.ensure_campaign_copy(cid)  # lazy backfill of legacy campaigns
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return store.campaigns.campaign_root(cid)


def _entity_list(root, kind: str):
    try:
        items = store.entities.list_entities(root, kind)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    for it in items:
        p = store.assets.image_path(root, it["id"], "default", store.assets.AVATAR, base=kind)
        it["has_image"] = p is not None
        it["image_v"] = store.assets.image_version(p) if p is not None else None
    return items


def _entity_create(root, kind: str, body: EntityCreate):
    try:
        return {"id": store.entities.create_entity(root, kind, body.name, body.body, body.keys, body.owners)}
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")


def _entity_read(root, kind: str, eid: str):
    try:
        return store.entities.read_entity(root, kind, eid)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")


def _entity_update(root, kind: str, eid: str, body: EntityUpdate):
    try:
        store.entities.update_entity(root, kind, eid, name=body.name, body=body.body,
                                     keys=body.keys, owners=body.owners)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")
    return {"ok": True}


def _entity_delete(root, kind: str, eid: str):
    try:
        store.entities.delete_entity(root, kind, eid)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")
    return {"ok": True}


@router.get("/worlds/{wid}/{kind}")
def get_world_entities(wid: str, kind: str):
    return _entity_list(_world_root_or_404(wid), kind)


@router.post("/worlds/{wid}/{kind}")
def post_world_entity(wid: str, kind: str, body: EntityCreate):
    return _entity_create(_world_root_or_404(wid), kind, body)


@router.get("/worlds/{wid}/{kind}/{eid}")
def get_world_entity(wid: str, kind: str, eid: str):
    return _entity_read(_world_root_or_404(wid), kind, eid)


@router.put("/worlds/{wid}/{kind}/{eid}")
def put_world_entity(wid: str, kind: str, eid: str, body: EntityUpdate):
    return _entity_update(_world_root_or_404(wid), kind, eid, body)


@router.delete("/worlds/{wid}/{kind}/{eid}")
def delete_world_entity(wid: str, kind: str, eid: str):
    return _entity_delete(_world_root_or_404(wid), kind, eid)


# ---- entity images (locations/lore) — assets keyed <kind>/<eid>/assets/default ----
def _entity_kind_or_404(kind: str) -> None:
    if kind not in store.entities.ENTITY_KINDS:
        raise HTTPException(status_code=404, detail="unknown kind")


_IMAGE_KINDS = store.entities.ENTITY_KINDS + ("greetings",)


def _image_kind_or_404(kind: str) -> None:
    # read side only: greeting images are stored by localize_greeting / scripts,
    # not uploaded over HTTP, so the write routes keep the strict entity check
    if kind not in _IMAGE_KINDS:
        raise HTTPException(status_code=404, detail="unknown kind")


def _entity_images_list(root, kind: str, eid: str):
    _entity_kind_or_404(kind)
    return store.assets.list_images(root, eid, "default", base=kind)


async def _entity_image_put(root, kind: str, eid: str, name: str, file: UploadFile):
    _entity_kind_or_404(kind)
    data = await file.read()
    fn = file.filename or ""
    ext = fn.rsplit(".", 1)[-1] if "." in fn else ""
    try:
        stored = store.assets.put_image(root, eid, "default", name, data, ext, base=kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": name, "ext": stored}


def _entity_image_promote(root, kind: str, eid: str, name: str):
    _entity_kind_or_404(kind)
    try:
        store.assets.promote_image(root, eid, "default", name, base=kind)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    return {"ok": True}


@router.get("/worlds/{wid}/{kind}/{eid}/images")
def list_world_entity_images(wid: str, kind: str, eid: str):
    _image_kind_or_404(kind)
    return store.assets.list_images(_world_root_or_404(wid), eid, "default", base=kind)


@router.get("/worlds/{wid}/{kind}/{eid}/images/{name}")
def get_world_entity_image(wid: str, kind: str, eid: str, name: str, request: Request):
    _image_kind_or_404(kind)
    return _serve_image(_world_root_or_404(wid), eid, "default", name, base=kind, request=request)


@router.put("/worlds/{wid}/{kind}/{eid}/images/{name}")
async def put_world_entity_image(wid: str, kind: str, eid: str, name: str, file: UploadFile = File(...)):
    return await _entity_image_put(_world_root_or_404(wid), kind, eid, name, file)


@router.delete("/worlds/{wid}/{kind}/{eid}/images/{name}")
def delete_world_entity_image(wid: str, kind: str, eid: str, name: str):
    _entity_kind_or_404(kind)
    store.assets.delete_image(_world_root_or_404(wid), eid, "default", name, base=kind)
    return {"ok": True}


@router.post("/worlds/{wid}/{kind}/{eid}/images/{name}/promote")
def promote_world_entity_image(wid: str, kind: str, eid: str, name: str):
    return _entity_image_promote(_world_root_or_404(wid), kind, eid, name)


# ---- campaigns ----
@router.get("/campaigns")
def get_campaigns():
    out = []
    for c in store.campaigns.list_campaigns():
        scene_list = store.scenes.list_scenes(c["id"])
        out.append({**c, "scenes": len(scene_list),
                    "last_scene": scene_list[0]["title"] if scene_list else ""})
    return out


@router.get("/campaigns/{cid}/calendar")
def get_calendar_config(cid: str):
    if not store.campaigns.campaign_meta_path(cid).exists():
        raise HTTPException(status_code=404, detail="campaign not found")
    return store.calendars.read_calendar(store.campaigns.campaign_root(cid))


@router.put("/campaigns/{cid}/calendar")
def put_calendar_config(cid: str, body: CalendarConfig):
    if not store.campaigns.campaign_meta_path(cid).exists():
        raise HTTPException(status_code=404, detail="campaign not found")
    cfg = {"primary": body.primary, "secondary": body.secondary, "confirmed": body.confirmed}
    try:
        store.calendars.validate_calendar(cfg)
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    store.calendars.write_calendar(store.campaigns.campaign_root(cid), cfg)
    return {"ok": True}


@router.post("/campaigns")
def post_campaign(body: NewCampaign):
    try:
        return {"id": store.campaigns.create_campaign(body.name, body.world, body.region)}
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=400, detail="world not found")


@router.get("/campaigns/{cid}")
def get_campaign(cid: str):
    try:
        store.campaigns.ensure_campaign_copy(cid)
        out = store.campaigns.read_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    # embedded so the frontend needn't chain a world fetch after this one
    wid = out["meta"].get("world", "")
    out["meta"]["world_name"] = store.worlds.world_name(wid) or wid
    return out


@router.put("/campaigns/{cid}")
def put_campaign(cid: str, body: NameBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        store.campaigns.rename_campaign(cid, name)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"id": cid, "name": name}


@router.delete("/campaigns/{cid}")
def delete_campaign(cid: str):
    try:
        store.campaigns.delete_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}


# ---- campaign sync ----
# Declared before the generic /campaigns/{cid}/{kind} routes so "incoming" is
# never captured as an entity kind.
@router.get("/campaigns/{cid}/incoming")
def get_incoming(cid: str):
    try:
        store.campaigns.ensure_campaign_copy(cid)
        return store.sync.incoming(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")


@router.post("/campaigns/{cid}/incoming/accept")
def post_accept(cid: str, body: RefList):
    try:
        store.campaigns.ensure_campaign_copy(cid)
        store.sync.accept(cid, [r.model_dump() for r in body.refs])
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/incoming/reject")
def post_reject(cid: str, body: RefList):
    try:
        store.campaigns.ensure_campaign_copy(cid)
        store.sync.reject(cid, [r.model_dump() for r in body.refs])
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}


# ---- scenes ----
# Declared before the generic /campaigns/{cid}/{kind} routes so "scenes" is
# never captured as an entity kind.
def _require_key(cfg: dict[str, str]) -> None:
    if not cfg["openrouter_key"]:
        raise HTTPException(
            status_code=409,
            detail={"detail": "OpenRouter key not set", "kind": "missing_key"},
        )


def _persist_reply(cid: str, sid: str, text: str) -> None:
    """Split one model reply into per-speaker posts and append them (#744)."""
    players = frozenset(store.appearances.player_names(cid, sid))
    for seg in store.scenes.split_reply(text, players):
        store.scenes.append_message(cid, sid, "assistant", seg["content"], speaker=seg["speaker"])


def _chat_stream(cid: str, sid: str, messages: list[dict], cfg: dict, client: OpenRouterClient):
    async def event_stream():
        parts: list[str] = []
        try:
            async for delta in client.stream(messages, cfg["model"], cfg["openrouter_key"]):
                parts.append(delta)
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            _persist_reply(cid, sid, "".join(parts))
            yield f"data: {json.dumps({'done': True})}\n\n"
        except OpenRouterError as exc:
            if parts:
                _persist_reply(cid, sid, "".join(parts))
            yield f"data: {json.dumps({'error': {'detail': exc.detail, 'kind': exc.kind}})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _ephemeral_stream(messages: list[dict], cfg: dict, client: OpenRouterClient):
    """Stream a generation without persisting it to any scene (used by the opener)."""
    async def event_stream():
        try:
            async for delta in client.stream(messages, cfg["model"], cfg["openrouter_key"]):
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except OpenRouterError as exc:
            yield f"data: {json.dumps({'error': {'detail': exc.detail, 'kind': exc.kind}})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/campaigns/{cid}/scenes")
def get_scenes(cid: str):
    try:
        return store.scenes.list_scenes(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")


@router.post("/campaigns/{cid}/scenes")
def post_scene(cid: str, body: NewScene):
    title = body.title or "New scene"
    try:
        return {"id": store.scenes.create_scene(cid, title, body.suggested_date,
                                                pcless=body.pcless)}
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")


def _resolve_cast(cid: str, tokens: list[str]) -> list[dict]:
    croot = store.campaigns.campaign_root(cid)
    wroot = store.worlds.world_root(store.campaigns.read_campaign(cid)["meta"].get("world", ""))
    out = []
    for tok in tokens:
        kind, _, aid = tok.partition(":")
        try:
            if kind == "pcs":
                name = store.pcs.read_pc(croot, aid)["meta"].get("name", aid)
            else:
                try:
                    name = store.characters.read_character(croot, aid)["meta"].get("name", aid)
                except store.characters.CharacterNotFound:
                    name = store.characters.read_character(wroot, aid)["meta"].get("name", aid)
        except (store.characters.CharacterNotFound, store.pcs.PCNotFound):
            name = aid
        out.append({"kind": kind, "id": aid, "name": name})
    return out


@router.post("/campaigns/{cid}/scene-suggestions")
async def post_scene_suggestions(cid: str, after: str | None = None,
                                 client: OpenRouterClient = Depends(get_openrouter)):
    try:
        store.campaigns.read_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    cfg = store.read_config()
    _require_key(cfg)
    # with >2 startable greetings the same call also ranks them for the chooser
    candidates = store.suggest.greeting_candidates(cid, after)
    messages = store.suggest.build_prompt(store.suggest.build_snapshot(cid), candidates)
    try:
        text = await client.complete(messages, cfg["model"], cfg["openrouter_key"])
    except OpenRouterError as exc:
        raise HTTPException(status_code=502, detail={"detail": exc.detail, "kind": exc.kind})
    croot = store.campaigns.campaign_root(cid)
    loc_names = {e["id"]: e.get("name", e["id"]) for e in store.entities.list_entities(croot, "locations")}
    out = []
    for s in store.suggest.parse_output(text, cid):
        loc = {"id": s["location"], "name": loc_names.get(s["location"], s["location"])} if s["location"] else None
        out.append({"title": s["title"], "premise": s["premise"], "date": s["date"],
                    "cast": _resolve_cast(cid, s["cast"]), "location": loc})
    picks = (store.suggest.parse_greeting_picks(text, {c["id"] for c in candidates})
             if candidates else [])
    return {"suggestions": out, "greeting_picks": picks,
            "next_date": store.suggest.parse_next_date(text, cid)}


@router.get("/campaigns/{cid}/scenes/{sid}")
def get_scene(cid: str, sid: str):
    try:
        return store.scenes.read_scene(cid, sid)
    except store.scenes.SceneNotFound:
        raise HTTPException(status_code=404, detail="scene not found")


@router.put("/campaigns/{cid}/scenes/{sid}")
def put_scene(cid: str, sid: str, body: RenameScene):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    try:
        new_sid = store.scenes.rename_scene(cid, sid, title)
    except store.scenes.SceneNotFound:
        raise HTTPException(status_code=404, detail="scene not found")
    return {"id": new_sid, "title": title}


@router.delete("/campaigns/{cid}/scenes/{sid}")
def delete_scene(cid: str, sid: str):
    try:
        store.scenes.delete_scene(cid, sid)
    except store.scenes.SceneNotFound:
        raise HTTPException(status_code=404, detail="scene not found")
    return {"ok": True}


def _require_scene(cid: str, sid: str) -> dict:
    try:
        return store.scenes.read_scene(cid, sid)
    except store.scenes.SceneNotFound:
        raise HTTPException(status_code=404, detail="scene not found")


@router.post("/campaigns/{cid}/scenes/{sid}/chat")
def post_chat(cid: str, sid: str, turn: ChatTurn, client: OpenRouterClient = Depends(get_openrouter)):
    _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    names = store.appearances.player_names(cid, sid)
    speaker = names[0] if len(names) == 1 else None
    if speaker:
        store.scenes.stamp_user_speaker(cid, sid, speaker)
    store.scenes.append_message(cid, sid, "user", turn.content, speaker=speaker)
    messages = store.context.build_messages(cid, sid)
    return _chat_stream(cid, sid, messages, cfg, client)


@router.post("/campaigns/{cid}/scenes/{sid}/retry")
def post_retry(cid: str, sid: str, client: OpenRouterClient = Depends(get_openrouter)):
    scene = _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    if not scene["messages"]:
        raise HTTPException(status_code=400, detail="nothing to retry")
    messages = store.context.build_messages(cid, sid)
    return _chat_stream(cid, sid, messages, cfg, client)


@router.post("/campaigns/{cid}/scenes/{sid}/regenerate")
def post_regenerate(cid: str, sid: str, body: RegenerateBody | None = None,
                    client: OpenRouterClient = Depends(get_openrouter)):
    """Redo the most recent post: drop a trailing assistant reply, stream a fresh one."""
    scene = _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    msgs = scene["messages"]
    if not msgs:
        raise HTTPException(status_code=400, detail="nothing to regenerate")
    if msgs[-1]["role"] == "assistant":
        if all(m["role"] == "assistant" for m in msgs):
            raise HTTPException(status_code=400, detail="cannot regenerate the opening post")
        store.scenes.remove_trailing_assistant_run(cid, sid)
    messages = store.context.build_messages(cid, sid)
    guidance = (body.guidance or "").strip() if body else ""
    if guidance:
        messages.append({
            "role": "system",
            "content": f"Regenerate your previous reply. Guidance from the player: {guidance}",
        })
    return _chat_stream(cid, sid, messages, cfg, client)


@router.get("/campaigns/{cid}/chronicle")
def get_chronicle(cid: str):
    _campaign_root_or_404(cid)
    return store.chronicle.recent(cid, 50)


@router.post("/campaigns/{cid}/scenes/{sid}/absorb")
async def post_absorb(cid: str, sid: str,
                      client: OpenRouterClient = Depends(get_openrouter)):
    scene = _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    if not scene["messages"]:
        raise HTTPException(status_code=400, detail="nothing to absorb")
    facts = store.chronicle.scene_facts(cid, sid)
    transcript = store.chronicle.transcript_text(scene["messages"])
    messages = store.absorb.build_prompt(
        transcript, facts,
        store.absorb.state_snapshot(cid, sid), store.absorb.relationships_snapshot(cid, sid),
        store.absorb.plot_snapshot(cid))
    try:
        text = await client.complete(messages, cfg["model"], cfg["openrouter_key"])
    except OpenRouterError as exc:
        raise HTTPException(status_code=502, detail={"detail": exc.detail, "kind": exc.kind})
    parsed = store.absorb.parse_output(text)
    edits = store.absorb.materialize(cid, sid, parsed)
    # Phase 2: refresh each present character's campaign dossier from this scene.
    croot = store.campaigns.campaign_root(cid)
    for a in store.appearances.scene_cast(cid, sid):
        if a["kind"] != "characters" or a["role"] != "npc":
            continue  # dossiers feed the npc-only "Active elsewhere" tier; skip player cards
        try:
            name = store.characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
            msgs = store.dossiers.build_prompt(name, store.dossiers.read(croot, a["id"]), transcript)
            d_text = await client.complete(msgs, cfg["model"], cfg["openrouter_key"])
            store.dossiers.write(croot, a["id"], store.dossiers.parse_output(d_text))
        except Exception:  # noqa: BLE001 — a dossier failure must not fail absorb
            continue
    return {"one_line": parsed["one_line"], "summary": parsed["summary"],
            "keywords": parsed["keywords"], "timeline_events": parsed["timeline_events"],
            **facts, "edits": edits}


@router.put("/campaigns/{cid}/scenes/{sid}/chronicle")
def put_chronicle(cid: str, sid: str, body: ChronicleSave):
    _require_scene(cid, sid)
    facts = store.chronicle.scene_facts(cid, sid)
    record = store.chronicle.absorb(cid, {
        "id": sid, "one_line": body.one_line, "summary": body.summary,
        "keywords": body.keywords, **facts})
    store.chronicle.append_timeline(cid, body.timeline_events)
    store.scenes.mark_absorbed(cid, sid, body.one_line, body.summary)
    applied = store.absorb.apply_edits(cid, body.edits, sid)
    return {**record, "applied": applied}


def _record_name(croot, kind: str, eid: str) -> str | None:
    """Display name for a campaign record, or None if it no longer exists."""
    try:
        if kind == "characters":
            return store.characters.read_character(croot, eid)["meta"].get("name", eid)
        if kind in store.entities.ENTITY_KINDS:
            return store.entities.read_entity(croot, kind, eid)["meta"].get("name", eid)
    except (store.characters.CharacterNotFound, store.entities.EntityNotFound):
        return None
    return None


@router.get("/campaigns/{cid}/changes")
def get_changes(cid: str):
    croot = _campaign_root_or_404(cid)
    data = store.changes.read(cid)
    scenes_by_id = {s["id"]: s for s in store.scenes.list_scenes(cid)}
    try:
        chron = store.chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 — garbled chronicle.json: labels degrade, no 500
        chron = {}
    out: list[dict] = []
    for ref, entry in data.items():
        kind, _, eid = ref.partition("/")
        name = _record_name(croot, kind, eid)
        if name is None:
            continue  # record deleted since the change was captured
        sid = entry.get("scene", "")
        s, c = scenes_by_id.get(sid, {}), chron.get(sid, {})
        fields = [{"field": f.get("field", ""), "label": f.get("label", ""),
                   "diff": store.changes.line_diff(f.get("before", ""), f.get("after", ""))}
                  for f in entry.get("fields", [])]
        out.append({"ref": {"kind": kind, "id": eid}, "name": name,
                    "scene": {"id": sid, "title": s.get("title", sid), "date": c.get("date", "")},
                    "fields": fields})
    out.sort(key=lambda r: (r["ref"]["kind"], r["name"]))
    return out


# ---- campaign cast & suggestions (declared before the generic /{kind} routes) ----
@router.get("/campaigns/{cid}/appearances")
def get_appearances(cid: str):
    _campaign_root_or_404(cid)
    return store.appearances.roster(cid)


@router.get("/campaigns/{cid}/pcs")
def get_campaign_pcs(cid: str):
    return store.pcs.list_pcs(_campaign_root_or_404(cid))


@router.post("/campaigns/{cid}/pcs")
def post_campaign_pc(cid: str, body: PCCreate):
    # Campaign-local PC overlay: tags are free strings (no world-vocabulary check).
    root = _campaign_root_or_404(cid)
    pid, vid = store.pcs.create_pc(root, body.name, body.tags, body.version_name, body.persona)
    return {"pc": pid, "version": vid}


@router.get("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
def get_campaign_image(cid: str, char: str, vid: str, name: str, request: Request):
    return _serve_image(_campaign_root_or_404(cid), char, vid, name, request=request)


def _campaign_wroot(cid: str):
    return store.worlds.world_root(store.campaigns.read_campaign(cid)["meta"].get("world", ""))


@router.get("/campaigns/{cid}/characters")
def get_campaign_characters(cid: str):
    return store.characters.list_characters(_campaign_root_or_404(cid))


@router.get("/campaigns/{cid}/characters/{char}")
def get_campaign_character(cid: str, char: str):
    try:
        return store.characters.read_character(_campaign_root_or_404(cid), char)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")


@router.put("/campaigns/{cid}/characters/{char}")
def put_campaign_character(cid: str, char: str, body: DefaultVersion):
    try:
        store.characters.set_default_version(_campaign_root_or_404(cid), char, body.default_version)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/versions")
def post_campaign_character_version(cid: str, char: str, body: VersionCreate):
    root = _campaign_root_or_404(cid)
    if store.appearances.locked_version(cid, "characters", char) is not None:
        raise HTTPException(status_code=409, detail="character is locked to one version")
    try:
        vid = store.characters.create_version(root, char, body.name, body.card)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"version": vid}


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}")
def put_campaign_character_version(cid: str, char: str, vid: str, body: VersionUpdate):
    try:
        store.characters.update_version(_campaign_root_or_404(cid), char, vid, body.card)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/campaigns/{cid}/characters/{char}/versions/{vid}")
def delete_campaign_character_version(cid: str, char: str, vid: str):
    try:
        store.characters.delete_version(_campaign_root_or_404(cid), char, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.get("/campaigns/{cid}/pcs/{pid}")
def get_campaign_pc(cid: str, pid: str):
    try:
        return store.pcs.read_pc(_campaign_root_or_404(cid), pid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")


@router.put("/campaigns/{cid}/pcs/{pid}")
def put_campaign_pc(cid: str, pid: str, body: PCUpdate):
    # Campaign tags are free strings: no world-vocabulary check on this side.
    root = _campaign_root_or_404(cid)
    try:
        if body.tags is not None:
            store.pcs.set_tags(root, pid, body.tags)
        if body.default_version is not None:
            store.pcs.set_default_version(root, pid, body.default_version)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/pcs/{pid}/versions")
def post_campaign_pc_version(cid: str, pid: str, body: PersonaVersionCreate):
    root = _campaign_root_or_404(cid)
    if store.appearances.locked_version(cid, "pcs", pid) is not None:
        raise HTTPException(status_code=409, detail="pc is locked to one version")
    try:
        vid = store.pcs.create_version(root, pid, body.name, body.persona)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    return {"version": vid}


@router.put("/campaigns/{cid}/pcs/{pid}/versions/{vid}")
def put_campaign_pc_version(cid: str, pid: str, vid: str, body: PersonaVersionUpdate):
    try:
        store.pcs.update_version(_campaign_root_or_404(cid), pid, vid, body.persona)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/campaigns/{cid}/pcs/{pid}/versions/{vid}")
def delete_campaign_pc_version(cid: str, pid: str, vid: str):
    try:
        store.pcs.delete_version(_campaign_root_or_404(cid), pid, vid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/campaigns/{cid}/{kind}/{aid}/pick-version")
def post_pick_version(cid: str, kind: str, aid: str, body: PickBody):
    root = _campaign_root_or_404(cid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    if store.appearances.locked_version(cid, kind, aid) is not None:
        # checked before existence: the sibling versions were purged by the pick
        raise HTTPException(status_code=409, detail=f"{kind}/{aid} is already locked")
    if store.appearances.actor_hash(root, kind, aid, body.version) is None:
        raise HTTPException(status_code=404, detail="actor or version not found in campaign")
    try:
        store.appearances.pick_version(cid, kind, aid, body.version)
    except store.appearances.AppearError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.post("/campaigns/{cid}/{kind}/{aid}/import-version")
def post_import_version(cid: str, kind: str, aid: str, body: PickBody):
    _campaign_root_or_404(cid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    if store.appearances.actor_hash(_campaign_wroot(cid), kind, aid, body.version) is None:
        raise HTTPException(status_code=404, detail="actor or version not found in world")
    try:
        store.appearances.import_version(cid, kind, aid, body.version)
    except store.appearances.AppearError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.get("/campaigns/{cid}/scenes/{sid}/cast")
def get_scene_cast(cid: str, sid: str):
    _require_scene(cid, sid)
    return store.appearances.scene_cast(cid, sid)


def _seat_cast_member(cid: str, sid: str, wroot, croot, body: Appear) -> None:
    """Validate + resolve one cast addition and record it. Raises HTTPException
    (404 unknown, 400 bad role) or store.appearances.AppearError (already cast)."""
    if body.kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    role = "player" if body.kind == "pcs" else (body.role or "npc")
    if role not in ("player", "npc"):
        raise HTTPException(status_code=400, detail="role must be player or npc")
    version = body.version
    try:
        if version is None:
            if body.kind == "characters":
                try:
                    version = store.characters.read_character(croot, body.id)["meta"]["default_version"]
                except store.characters.CharacterNotFound:
                    version = store.characters.read_character(wroot, body.id)["meta"]["default_version"]
            else:
                try:
                    version = store.pcs.read_pc(croot, body.id)["meta"]["default_version"]
                except store.pcs.PCNotFound:
                    version = store.pcs.read_pc(wroot, body.id)["meta"]["default_version"]
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound):
        raise HTTPException(status_code=404, detail="actor not found")
    store.appearances.appear(cid, sid, body.kind, body.id, version, role)


@router.post("/campaigns/{cid}/scenes/{sid}/cast")
def post_scene_cast(cid: str, sid: str, body: Appear):
    _require_scene(cid, sid)
    wroot = store.worlds.world_root(store.campaigns.read_campaign(cid)["meta"].get("world", ""))
    croot = store.campaigns.campaign_root(cid)
    try:
        _seat_cast_member(cid, sid, wroot, croot, body)
    except store.appearances.AppearError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


class AppearBatch(BaseModel):
    refs: list[Appear]


@router.post("/campaigns/{cid}/scenes/{sid}/cast/batch")
def post_scene_cast_batch(cid: str, sid: str, body: AppearBatch):
    """Seat a whole suggestion cast in one request. Already-cast members are
    skipped (the per-member 409), matching what the chooser's serial loop
    tolerated; unknown actors still 404 the request."""
    _require_scene(cid, sid)
    wroot = store.worlds.world_root(store.campaigns.read_campaign(cid)["meta"].get("world", ""))
    croot = store.campaigns.campaign_root(cid)
    added, skipped = 0, []
    for ref in body.refs:
        try:
            _seat_cast_member(cid, sid, wroot, croot, ref)
            added += 1
        except store.appearances.AppearError:
            skipped.append(f"{ref.kind}/{ref.id}")
    return {"ok": True, "added": added, "skipped": skipped}


@router.get("/campaigns/{cid}/scenes/{sid}/suggestions")
def get_scene_suggestions(cid: str, sid: str):
    _require_scene(cid, sid)
    return store.appearances.suggestions(cid, sid)


@router.post("/campaigns/{cid}/scenes/{sid}/suggestions/dismiss")
def post_dismiss(cid: str, sid: str, body: Dismiss):
    _require_scene(cid, sid)
    store.scenes.add_dismissed(cid, sid, body.character)
    return {"ok": True}


@router.get("/campaigns/{cid}/scenes/{sid}/location")
def get_scene_location(cid: str, sid: str):
    _require_scene(cid, sid)
    croot = store.campaigns.campaign_root(cid)
    history = store.scenes.get_location_history(cid, sid)

    def ref(eid: str) -> dict:
        try:
            name = store.entities.read_entity(croot, "locations", eid)["meta"].get("name", eid)
        except store.entities.EntityNotFound:
            name = eid
        return {"id": eid, "name": name}

    return {"current": ref(history[-1]) if history else None,
            "visited": [ref(e) for e in history[:-1]]}


@router.put("/campaigns/{cid}/scenes/{sid}/location")
def put_scene_location(cid: str, sid: str, body: SceneLocation):
    _require_scene(cid, sid)
    try:
        result = store.scenes.set_location(cid, sid, body.location)
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="location not found")
    return {"ok": True, **result}


@router.get("/campaigns/{cid}/scenes/{sid}/datetime")
def get_scene_datetime(cid: str, sid: str):
    _require_scene(cid, sid)
    history = store.scenes.get_time_history(cid, sid)
    current = None
    suggested = None
    if history:
        cfg = store.calendars.read_calendar(store.campaigns.campaign_root(cid))
        native = history[-1]
        try:
            current = {"native": native, **store.calendars.today_facts(cfg, native),
                       "cast": store.context.cast_datetime_facts(cid, sid, native)}
        except store.calendars.CalendarError:
            current = None  # misconfigured calendar — surface "no date" rather than 500
    else:
        # dateless: offer a pre-fill — the creation-time hint, else where the story left off
        hint = store.scenes.get_suggested_date(cid, sid)
        if not hint:
            try:
                recent = store.chronicle.recent(cid, 1)
                hint = recent[-1].get("date", "") if recent else ""
            except Exception:  # noqa: BLE001 — garbled chronicle.json
                hint = ""
        if hint:
            suggested = store.calendars.split_native(hint)[0]
    return {"current": current, "history": history, "suggested": suggested}


@router.put("/campaigns/{cid}/scenes/{sid}/datetime")
def put_scene_datetime(cid: str, sid: str, body: SceneDatetime):
    _require_scene(cid, sid)
    try:
        result = store.scenes.set_datetime(cid, sid, body.datetime)
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@router.get("/campaigns/{cid}/scenes/{sid}/context")
def get_scene_context(cid: str, sid: str):
    scene = _require_scene(cid, sid)
    sections = []
    total = 0
    for s in store.context.context_sections(cid, sid):
        tokens = store.context.count_tokens(s["text"])
        total += tokens
        sections.append({"label": s["label"], "text": s["text"], "tokens": tokens})
    return {"model": scene["meta"].get("model", ""), "total_tokens": total, "sections": sections}


@router.get("/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}")
def get_cast_detail(cid: str, sid: str, kind: str, id: str):
    _require_scene(cid, sid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    try:
        return store.appearances.cast_detail(cid, sid, kind, id)
    except (store.appearances.AppearError, store.characters.CharacterNotFound,
            store.characters.VersionNotFound, store.pcs.PCNotFound, store.pcs.PCVersionNotFound):
        raise HTTPException(status_code=404, detail="actor not found")


@router.put("/campaigns/{cid}/scenes/{sid}/messages/{index}")
def put_scene_message(cid: str, sid: str, index: int, body: EditMessage):
    _require_scene(cid, sid)
    try:
        store.scenes.edit_message(cid, sid, index, body.content)
    except IndexError:
        raise HTTPException(status_code=400, detail="message index out of range")
    return {"ok": True}


# ---- campaign greetings / play (declared before the generic /{kind} routes) ----
@router.get("/campaigns/{cid}/greetings/available")
def get_available_greetings(cid: str, after: str | None = None):
    _campaign_root_or_404(cid)
    try:
        return store.playing.available_greetings(cid, after=after)
    except store.scenes.SceneNotFound:
        raise HTTPException(status_code=404, detail="scene not found")


@router.get("/campaigns/{cid}/greetings")
def get_campaign_greetings(cid: str):
    root = _campaign_root_or_404(cid)
    marks = store.playing.read_marks(cid)
    mark_of = {g: "played" for g in marks["played"]}
    mark_of.update({g: "completed" for g in marks["completed"]})
    mark_of.update({g: "skipped" for g in marks["skipped"]})
    return [{**g, "mark": mark_of.get(g["id"])} for g in store.greetings.list_greetings(root)]


@router.post("/campaigns/{cid}/greetings")
def post_campaign_greeting(cid: str, body: GreetingCreate):
    root = _campaign_root_or_404(cid)
    gid = store.greetings.create_greeting(root, body.name, body.character, body.version,
                                          body.body, body.requires_tags,
                                          body.predecessor_join, present=body.present)
    return {"id": gid}


@router.get("/campaigns/{cid}/greetings/{gid}")
def get_campaign_greeting(cid: str, gid: str):
    root = _campaign_root_or_404(cid)
    try:
        g = store.greetings.read_greeting(root, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    plotmap = store.greetings.read_plotmap(root)
    g["edges"] = store.greetings.edges_of(plotmap, gid)
    g["predecessors"] = store.greetings.predecessors_of(plotmap, gid)
    return g


@router.put("/campaigns/{cid}/greetings/{gid}")
def put_campaign_greeting(cid: str, gid: str, body: GreetingUpdate):
    root = _campaign_root_or_404(cid)
    try:
        store.greetings.update_greeting(root, gid, name=body.name, body=body.body,
                                        requires_tags=body.requires_tags,
                                        predecessor_join=body.predecessor_join,
                                        present=body.present)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    return {"ok": True}


@router.put("/campaigns/{cid}/greetings/{gid}/edges")
def put_campaign_greeting_edges(cid: str, gid: str, body: Edges):
    root = _campaign_root_or_404(cid)
    try:
        store.greetings.read_greeting(root, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    store.greetings.set_edges(root, gid, body.leads_to, body.excludes)
    return {"ok": True}


@router.delete("/campaigns/{cid}/greetings/{gid}")
def delete_campaign_greeting(cid: str, gid: str):
    root = _campaign_root_or_404(cid)
    try:
        store.greetings.delete_greeting(root, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/greetings/{gid}/mark")
def post_campaign_greeting_mark(cid: str, gid: str, body: MarkBody):
    _campaign_root_or_404(cid)
    try:
        store.playing.mark_greeting(cid, gid, body.status)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    except store.playing.PlayError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.post("/campaigns/{cid}/scenes/{sid}/start-from-greeting")
def post_start_from_greeting(cid: str, sid: str, body: StartFromGreeting):
    _require_scene(cid, sid)
    try:
        store.playing.start_from_greeting(cid, sid, body.greeting)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    except (store.playing.PlayError, store.appearances.AppearError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.post("/campaigns/{cid}/scenes/{sid}/opener")
def post_opener(cid: str, sid: str, body: Opener, client: OpenRouterClient = Depends(get_openrouter)):
    _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    messages = store.context.build_opener_messages(cid, sid, body.prompt)
    return _ephemeral_stream(messages, cfg, client)


@router.post("/campaigns/{cid}/scenes/{sid}/first-post")
def post_first_post(cid: str, sid: str, body: FirstPost):
    """Adopt a generated opener as the scene's first (assistant) message. The cast is
    already set up in the panel, so this just persists the text onto an empty scene."""
    _require_scene(cid, sid)
    if store.scenes.read_scene(cid, sid)["messages"]:
        raise HTTPException(status_code=409, detail="scene already has messages")
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="empty first post")
    _persist_reply(cid, sid, body.text)
    return {"ok": True}


# ---- campaign entity CRUD (generic; declared last so literal sub-paths win) ----
@router.get("/campaigns/{cid}/{kind}")
def get_campaign_entities(cid: str, kind: str):
    return _entity_list(_campaign_root_or_404(cid), kind)


@router.post("/campaigns/{cid}/{kind}")
def post_campaign_entity(cid: str, kind: str, body: EntityCreate):
    return _entity_create(_campaign_root_or_404(cid), kind, body)


@router.get("/campaigns/{cid}/{kind}/{eid}")
def get_campaign_entity(cid: str, kind: str, eid: str):
    return _entity_read(_campaign_root_or_404(cid), kind, eid)


@router.put("/campaigns/{cid}/{kind}/{eid}")
def put_campaign_entity(cid: str, kind: str, eid: str, body: EntityUpdate):
    return _entity_update(_campaign_root_or_404(cid), kind, eid, body)


@router.delete("/campaigns/{cid}/{kind}/{eid}")
def delete_campaign_entity(cid: str, kind: str, eid: str):
    return _entity_delete(_campaign_root_or_404(cid), kind, eid)


@router.get("/campaigns/{cid}/{kind}/{eid}/images")
def list_campaign_entity_images(cid: str, kind: str, eid: str):
    return _entity_images_list(_campaign_root_or_404(cid), kind, eid)


@router.get("/campaigns/{cid}/{kind}/{eid}/images/{name}")
def get_campaign_entity_image(cid: str, kind: str, eid: str, name: str, request: Request):
    _image_kind_or_404(kind)
    return _serve_image(_campaign_root_or_404(cid), eid, "default", name, base=kind, request=request)


@router.put("/campaigns/{cid}/{kind}/{eid}/images/{name}")
async def put_campaign_entity_image(cid: str, kind: str, eid: str, name: str, file: UploadFile = File(...)):
    return await _entity_image_put(_campaign_root_or_404(cid), kind, eid, name, file)


@router.delete("/campaigns/{cid}/{kind}/{eid}/images/{name}")
def delete_campaign_entity_image(cid: str, kind: str, eid: str, name: str):
    _entity_kind_or_404(kind)
    store.assets.delete_image(_campaign_root_or_404(cid), eid, "default", name, base=kind)
    return {"ok": True}


@router.post("/campaigns/{cid}/{kind}/{eid}/images/{name}/promote")
def promote_campaign_entity_image(cid: str, kind: str, eid: str, name: str):
    return _entity_image_promote(_campaign_root_or_404(cid), kind, eid, name)

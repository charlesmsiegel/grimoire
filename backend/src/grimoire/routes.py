"""HTTP surface for grimoire."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
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


class NameBody(BaseModel):
    name: str


class NewCampaign(BaseModel):
    name: str
    world: str


class EntityCreate(BaseModel):
    name: str
    body: str = ""


class EntityUpdate(BaseModel):
    name: str | None = None
    body: str | None = None


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


class RenameScene(BaseModel):
    title: str


class ChatTurn(BaseModel):
    content: str


class Appear(BaseModel):
    kind: str = "characters"
    id: str
    version: str | None = None
    role: str | None = None


class Dismiss(BaseModel):
    character: str


# ---- config ----
def _public_config(cfg: dict[str, str]) -> dict:
    return {"model": cfg["model"], "theme": cfg["theme"], "key_set": bool(cfg["openrouter_key"])}


@router.get("/config")
def get_config():
    return _public_config(store.read_config())


@router.put("/config")
def put_config(update: ConfigUpdate):
    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    return _public_config(store.write_config(**fields))


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


# ---- generic entity CRUD (shared by worlds and campaigns) ----
def _world_root_or_404(wid: str):
    if not store.worlds.world_meta_path(wid).exists():
        raise HTTPException(status_code=404, detail="world not found")
    return store.worlds.world_root(wid)


def _campaign_root_or_404(cid: str):
    if not store.campaigns.campaign_meta_path(cid).exists():
        raise HTTPException(status_code=404, detail="campaign not found")
    return store.campaigns.campaign_root(cid)


def _entity_list(root, kind: str):
    try:
        return store.entities.list_entities(root, kind)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")


def _entity_create(root, kind: str, body: EntityCreate):
    try:
        return {"id": store.entities.create_entity(root, kind, body.name, body.body)}
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
        store.entities.update_entity(root, kind, eid, name=body.name, body=body.body)
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


# ---- campaigns ----
@router.get("/campaigns")
def get_campaigns():
    return store.campaigns.list_campaigns()


@router.post("/campaigns")
def post_campaign(body: NewCampaign):
    try:
        return {"id": store.campaigns.create_campaign(body.name, body.world)}
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=400, detail="world not found")


@router.get("/campaigns/{cid}")
def get_campaign(cid: str):
    try:
        return store.campaigns.read_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")


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
        return store.sync.incoming(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")


@router.post("/campaigns/{cid}/incoming/accept")
def post_accept(cid: str, body: RefList):
    try:
        store.sync.accept(cid, [r.model_dump() for r in body.refs])
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/incoming/reject")
def post_reject(cid: str, body: RefList):
    try:
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


def _chat_stream(cid: str, sid: str, messages: list[dict], cfg: dict, client: OpenRouterClient):
    async def event_stream():
        parts: list[str] = []
        try:
            async for delta in client.stream(messages, cfg["model"], cfg["openrouter_key"]):
                parts.append(delta)
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            store.scenes.append_message(cid, sid, "assistant", "".join(parts))
            yield f"data: {json.dumps({'done': True})}\n\n"
        except OpenRouterError as exc:
            if parts:
                store.scenes.append_message(cid, sid, "assistant", "".join(parts))
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
        return {"id": store.scenes.create_scene(cid, title)}
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")


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
    scene = _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    store.scenes.append_message(cid, sid, "user", turn.content)
    messages = [{"role": m["role"], "content": m["content"]} for m in scene["messages"]]
    messages.append({"role": "user", "content": turn.content})
    return _chat_stream(cid, sid, messages, cfg, client)


@router.post("/campaigns/{cid}/scenes/{sid}/retry")
def post_retry(cid: str, sid: str, client: OpenRouterClient = Depends(get_openrouter)):
    scene = _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    messages = [{"role": m["role"], "content": m["content"]} for m in scene["messages"]]
    if not messages:
        raise HTTPException(status_code=400, detail="nothing to retry")
    return _chat_stream(cid, sid, messages, cfg, client)


# ---- campaign cast & suggestions (declared before the generic /{kind} routes) ----
@router.get("/campaigns/{cid}/appearances")
def get_appearances(cid: str):
    _campaign_root_or_404(cid)
    return store.appearances.roster(cid)


@router.get("/campaigns/{cid}/scenes/{sid}/cast")
def get_scene_cast(cid: str, sid: str):
    _require_scene(cid, sid)
    return store.appearances.scene_cast(cid, sid)


@router.post("/campaigns/{cid}/scenes/{sid}/cast")
def post_scene_cast(cid: str, sid: str, body: Appear):
    _require_scene(cid, sid)
    if body.kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    wroot = store.worlds.world_root(store.campaigns.read_campaign(cid)["meta"].get("world", ""))
    role = "player" if body.kind == "pcs" else (body.role or "npc")
    if role not in ("player", "npc"):
        raise HTTPException(status_code=400, detail="role must be player or npc")
    version = body.version
    try:
        if version is None:
            if body.kind == "characters":
                version = store.characters.read_character(wroot, body.id)["meta"]["default_version"]
            else:
                version = store.pcs.read_pc(wroot, body.id)["meta"]["default_version"]
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound):
        raise HTTPException(status_code=404, detail="actor not found")
    try:
        store.appearances.appear(cid, sid, body.kind, body.id, version, role)
    except store.appearances.AppearError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.get("/campaigns/{cid}/scenes/{sid}/suggestions")
def get_scene_suggestions(cid: str, sid: str):
    _require_scene(cid, sid)
    return store.appearances.suggestions(cid, sid)


@router.post("/campaigns/{cid}/scenes/{sid}/suggestions/dismiss")
def post_dismiss(cid: str, sid: str, body: Dismiss):
    _require_scene(cid, sid)
    store.scenes.add_dismissed(cid, sid, body.character)
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

"""HTTP surface for grimoire."""

from __future__ import annotations

import contextlib
import json
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from . import prompts, store
from .llm import LLMClient, LLMError

router = APIRouter()
_llm = LLMClient()


def _dump(model: BaseModel) -> dict:
    """model_dump() on pydantic v2, dict() on v1. The Android build may pin the
    pure-python pydantic 1.x wheel (docs/android-architecture.md §7); this is
    the only v2-specific API the codebase uses."""
    dump = getattr(model, "model_dump", None)
    return dump() if dump is not None else model.dict()


def get_llm() -> LLMClient:
    return _llm


# ---- models ----
class ConfigUpdate(BaseModel):
    model: str | None = None
    theme: str | None = None
    openrouter_key: str | None = None
    system_prompt: str | None = None
    quote_color: str | None = None
    user_label: str | None = None
    assistant_label: str | None = None
    provider: Literal["openrouter", "claude"] | None = None
    claude_model: str | None = None
    default_style_id: str | None = None


class DataDirUpdate(BaseModel):
    data_dir: str | None = None


class StyleCreate(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = []
    body: str = ""


class StyleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    body: str | None = None


class StyleSelect(BaseModel):
    style_id: str = ""


class RegenerateBody(BaseModel):
    guidance: str | None = None


class NameBody(BaseModel):
    name: str


class RollBody(BaseModel):
    notation: str
    label: str | None = None


class ProposalAction(BaseModel):
    proposal: str
    action: str
    check: str | None = None
    actor: str | None = None
    difficulty: int | None = None
    modifier: int | None = None


class CheckBody(BaseModel):
    check: str
    actor: str
    difficulty: int | None = None
    modifier: int | None = None


class NewCampaign(BaseModel):
    name: str
    world: str
    region: str | None = None
    calendar: str | None = None
    module: str | None = None


class ModuleCreate(BaseModel):
    name: str


class ModuleSetting(BaseModel):
    module: str = ""


class SheetBody(BaseModel):
    sheet_type: str
    fields: dict | None = None
    expected: dict | None = None  # omitted == null == "assert no sheet exists"


class SheetCreationBody(BaseModel):
    sheet_type: str
    spends: dict[str, dict[str, int]] = {}
    expected: dict | None = None  # omitted == null == "assert no sheet exists"


class SheetAdvanceBody(BaseModel):
    field: str


class PickBody(BaseModel):
    version: str


class MarkBody(BaseModel):
    status: str  # "completed" | "skipped" | "none" — validated in the store


class EntityCreate(BaseModel):
    name: str
    body: str = ""
    keys: str = ""
    owners: str = ""
    fields: dict | None = None


class EntityUpdate(BaseModel):
    name: str | None = None
    body: str | None = None
    keys: str | None = None
    owners: str | None = None
    fields: dict | None = None


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


class GroupStateSave(BaseModel):
    goals: str = ""
    resources: str = ""
    focus: str = ""
    public_perception: str = ""
    secrets: str = ""


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
    content: str = ""


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
    pcless: bool = False


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
    pcless: bool | None = None


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
            "assistant_label": cfg.get("assistant_label", "Grimoire"),
            "provider": cfg.get("provider", "openrouter"),
            "claude_model": cfg.get("claude_model", "opus"),
            "default_style_id": cfg.get("default_style_id", "")}


@router.get("/config")
def get_config():
    return _public_config(store.read_config())


@router.put("/config")
def put_config(update: ConfigUpdate):
    fields = {k: v for k, v in _dump(update).items() if v is not None}
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


# ---- modules (#160) ----
@router.get("/modules")
def get_modules():
    return store.modules.list_modules()


@router.post("/modules")
def post_module(body: ModuleCreate):
    return {"id": store.modules.create_module(body.name)}


@router.get("/modules/{mid}")
def get_module(mid: str):
    try:
        return store.modules.load_pack(mid)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")


@router.delete("/modules/{mid}")
def delete_module(mid: str):
    try:
        store.modules.delete_module(mid)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.modules.ModuleError:
        raise HTTPException(status_code=400, detail="built-in modules cannot be deleted")
    return {"ok": True}


# ---- styles ----
@router.get("/styles")
def get_styles():
    return store.styles.list_styles()


@router.post("/styles")
def post_style(body: StyleCreate):
    return {"id": store.styles.create_style(body.name, body.description, body.tags, body.body)}


@router.get("/styles/{sid}")
def get_style(sid: str):
    try:
        return store.styles.read_style(sid)
    except store.styles.StyleNotFound:
        raise HTTPException(status_code=404, detail="style not found")


@router.put("/styles/{sid}")
def put_style(sid: str, body: StyleUpdate):
    try:
        store.styles.update_style(sid, name=body.name, description=body.description,
                                  tags=body.tags, body=body.body)
    except store.styles.StyleNotFound:
        raise HTTPException(status_code=404, detail="style not found")
    except store.styles.BuiltInStyleImmutable:
        raise HTTPException(status_code=400, detail="built-in styles can't be edited — duplicate it first")
    return {"ok": True}


@router.delete("/styles/{sid}")
def delete_style(sid: str):
    try:
        store.styles.delete_style(sid)
    except store.styles.StyleNotFound:
        raise HTTPException(status_code=404, detail="style not found")
    except store.styles.BuiltInStyleImmutable:
        raise HTTPException(status_code=400, detail="built-in styles can't be deleted")
    return {"ok": True}


@router.post("/styles/{sid}/duplicate")
def post_style_duplicate(sid: str):
    try:
        return {"id": store.styles.duplicate_style(sid)}
    except store.styles.StyleNotFound:
        raise HTTPException(status_code=404, detail="style not found")


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
    except store.worlds.WorldInUse as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.get("/worlds/{wid}/campaigns")
def get_world_campaigns(wid: str):
    return store.sync.campaigns_for_world(wid)


# registered before the generic /worlds/{wid}/{kind} entity routes below,
# which would otherwise swallow /worlds/x/module
@router.put("/worlds/{wid}/module")
def put_world_module(wid: str, body: ModuleSetting):
    try:
        # affected: non-overridden campaigns of this world -- their resolved
        # module (and thus every captured baseline) is about to change.
        affected = []
        for c in store.campaigns.list_campaigns():
            if c.get("world") != wid:
                continue
            try:
                meta = store.campaigns.read_campaign(c["id"])["meta"]
            except store.campaigns.CampaignNotFound:
                continue                         # deleted between list and read
            setting = (meta.get("module") or "").strip()
            if not setting:                      # no per-campaign override
                affected.append(c["id"])
        with contextlib.ExitStack() as stack:
            for c in sorted(affected):           # sole multi-lock holder; sorted order
                stack.enter_context(store.sheets.lock_for(c))
            store.modules.set_world_module(wid, body.module.strip())
            for c in affected:
                store.audit.clear_baselines(c)
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=404, detail="world not found")
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.modules.ModuleError:
        raise HTTPException(status_code=400, detail="'none' is reserved")
    return {"ok": True}


# registered before the generic /worlds/{wid}/{kind} entity routes below,
# same reasoning as /worlds/x/module above
@router.get("/worlds/{wid}/sheets")
def get_world_sheets_index(wid: str):
    _world_root_or_404(wid)
    meta = store.worlds.read_world(wid)["meta"]
    return {"modules": store.sheets.world_sheet_modules(wid),
            "default": (meta.get("module") or "").strip()}


@router.get("/worlds/{wid}/sheets/{mid}")
def get_world_sheets(wid: str, mid: str):
    _world_root_or_404(wid)
    try:
        store.modules.pack_root(mid)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    return {"coverage": store.sheets.world_coverage(wid, mid),
            "refs": store.sheets.world_list_refs(wid, mid)}


@router.get("/worlds/{wid}/sheets/{mid}/{kind}/{eid}")
def get_world_sheet(wid: str, mid: str, kind: str, eid: str):
    _world_root_or_404(wid)
    return {"sheet": store.sheets.read_world(wid, mid, kind, eid)}


@router.put("/worlds/{wid}/sheets/{mid}/{kind}/{eid}")
def put_world_sheet(wid: str, mid: str, kind: str, eid: str, body: SheetBody):
    _world_root_or_404(wid)
    try:
        store.sheets.write_world(wid, mid, kind, eid, body.sheet_type, body.fields)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.put("/worlds/{wid}/sheets/{mid}/{kind}/{eid}/creation")
def put_world_sheet_creation(wid: str, mid: str, kind: str, eid: str, body: SheetCreationBody):
    _world_root_or_404(wid)
    try:
        store.sheets.write_world_creation(wid, mid, kind, eid, body.sheet_type, body.spends)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound, store.entities.EntityNotFound):
        raise HTTPException(status_code=404, detail=f"{kind} not found")
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"sheet": store.sheets.read_world(wid, mid, kind, eid)}


@router.delete("/worlds/{wid}/sheets/{mid}/{kind}/{eid}")
def delete_world_sheet(wid: str, mid: str, kind: str, eid: str):
    _world_root_or_404(wid)
    return {"ok": store.sheets.delete_world(wid, mid, kind, eid)}


# registered before the generic /worlds/{wid}/{kind} entity routes below --
# distinct path shape (extra segments), so no collision either way, but
# grouped with the other module/sheet routes for readability
@router.get("/modules/{mid}/content/{kind}/{id}")
def get_module_content(mid: str, kind: str, id: str):
    try:
        return store.modules.read_content(mid, kind, id)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.modules.ContentNotFound:
        raise HTTPException(status_code=404, detail="content not found")


@router.post("/worlds/{wid}/{kind}/instantiate/{mid}/{content_id}")
def post_world_instantiate(wid: str, kind: str, mid: str, content_id: str):
    root = _world_root_or_404(wid)
    try:
        content = store.modules.read_content(mid, kind, content_id)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.modules.ContentNotFound:
        raise HTTPException(status_code=404, detail="content not found")
    try:
        eid = store.entities.create_entity(root, kind, content["name"], content["body"],
                                           content.get("keys", ""), "",
                                           fields=_content_fields(kind, content))
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    if content.get("sheet_type"):
        try:
            store.sheets.write_world(wid, mid, kind, eid, content["sheet_type"], content.get("fields"))
        except (store.modules.ModuleNotFound, store.sheets.SheetError) as e:
            # Sheet write failed after the entity was already created -- roll
            # it back so a failed instantiate leaves no sheetless orphan.
            store.entities.delete_entity(root, kind, eid)
            raise HTTPException(status_code=400, detail=str(e))
    return {"id": eid}


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
                                          client: LLMClient = Depends(get_llm)):
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
        text = await client.complete(messages, cfg)
    except LLMError as exc:
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
                                          body.predecessor_join, present=body.present,
                                          pcless=body.pcless)
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
                                        predecessor_join=body.predecessor_join, present=body.present,
                                        pcless=body.pcless)
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
        created = store.lorebook.commit(root, [_dump(e) for e in body.entries])
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
        store.campaigns.ensure_campaign_slim(cid)  # lazy slim of pre-overlay campaigns
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


def _check_fields(kind: str, fields: dict | None) -> None:
    if kind not in store.entities.ENTITY_KINDS:
        return  # let the store's unknown-kind handling produce the 404
    bad = store.entity_schema.invalid_keys(kind, fields or {})
    if bad:
        raise HTTPException(status_code=400, detail=f"unknown fields for {kind}: {', '.join(bad)}")


def _content_fields(kind: str, content: dict) -> dict:
    return {k: content[k] for k in store.entity_schema.field_keys(kind) if k in content}


def _entity_create(root, kind: str, body: EntityCreate):
    _check_fields(kind, body.fields)
    try:
        return {"id": store.entities.create_entity(root, kind, body.name, body.body, body.keys, body.owners,
                                                    fields=body.fields)}
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
    _check_fields(kind, body.fields)
    try:
        store.entities.update_entity(root, kind, eid, name=body.name, body=body.body,
                                     keys=body.keys, owners=body.owners, fields=body.fields)
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


# ---- campaign entity CRUD: same exception -> HTTP mapping, but reads/writes
# resolve through the overlay (campaign-over-world) instead of a bare root.
def _campaign_entity_list(cid: str, kind: str):
    try:
        items = store.overlay.list_entities(cid, kind)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    for it in items:
        root = store.overlay.image_root(cid, it["id"], "default", store.assets.AVATAR, base=kind)
        p = store.assets.image_path(root, it["id"], "default", store.assets.AVATAR, base=kind)
        it["has_image"] = p is not None
        it["image_v"] = store.assets.image_version(p) if p is not None else None
    return items


def _campaign_entity_create(cid: str, kind: str, body: EntityCreate):
    _check_fields(kind, body.fields)
    try:
        return {"id": store.overlay.create_entity(cid, kind, body.name, body.body, body.keys, body.owners,
                                                   fields=body.fields)}
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")


def _campaign_entity_read(cid: str, kind: str, eid: str):
    try:
        return store.overlay.read_entity(cid, kind, eid)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")


def _campaign_entity_update(cid: str, kind: str, eid: str, body: EntityUpdate):
    _check_fields(kind, body.fields)
    try:
        store.overlay.update_entity(cid, kind, eid, name=body.name, body=body.body,
                                    keys=body.keys, owners=body.owners, fields=body.fields)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")
    return {"ok": True}


def _campaign_entity_delete(cid: str, kind: str, eid: str):
    try:
        store.overlay.delete_entity(cid, kind, eid)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")
    return {"ok": True}


# ---- group state (#47): campaign-local, not covered by generic entity CRUD
# (path shape groups/{gid}/state cannot collide with /{kind}/{eid} or its
# /images sub-paths, so order relative to the generic routes doesn't matter)
@router.get("/campaigns/{cid}/groups/{gid}/state")
def get_group_state(cid: str, gid: str):
    if not store.campaigns.campaign_meta_path(cid).exists():
        raise HTTPException(status_code=404, detail="campaign not found")
    try:
        store.overlay.read_entity(cid, "groups", gid)
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="group not found")
    st = store.groupstate.read_state(store.campaigns.campaign_root(cid), gid)
    if st is None:
        return {**{k: "" for k in store.groupstate.FIELDS}, "updated": ""}
    return st


@router.put("/campaigns/{cid}/groups/{gid}/state")
def put_group_state(cid: str, gid: str, body: GroupStateSave):
    if not store.campaigns.campaign_meta_path(cid).exists():
        raise HTTPException(status_code=404, detail="campaign not found")
    try:
        store.overlay.read_entity(cid, "groups", gid)
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="group not found")
    values = {"goals": body.goals, "resources": body.resources, "focus": body.focus,
              "public_perception": body.public_perception, "secrets": body.secrets}
    store.groupstate.write_state(store.campaigns.campaign_root(cid), gid,
                                 store.groupstate.compose_body(values))
    return {"ok": True}


@router.get("/calendars/providers")
def get_calendar_providers():
    return {"providers": store.calendars.list_providers()}


@router.get("/worlds/{wid}/calendar/months")
def get_world_calendar_months(wid: str, year: int):
    if not store.worlds.world_meta_path(wid).exists():
        raise HTTPException(status_code=404, detail="world not found")
    cfg = store.calendars.read_calendar(store.worlds.world_root(wid))
    try:
        return {"months": store.calendars.get_provider(cfg["primary"]).months(year)}
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))


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


@router.get("/campaigns/{cid}/calendar/months")
def get_calendar_months(cid: str, year: int):
    if not store.campaigns.campaign_meta_path(cid).exists():
        raise HTTPException(status_code=404, detail="campaign not found")
    cfg = store.calendars.read_calendar(store.campaigns.campaign_root(cid))
    try:
        return {"months": store.calendars.get_provider(cfg["primary"]).months(year)}
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/campaigns")
def post_campaign(body: NewCampaign):
    try:
        return {"id": store.campaigns.create_campaign(body.name, body.world,
                                                      body.region, body.calendar,
                                                      body.module)}
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=400, detail="world not found")
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")


@router.get("/campaigns/{cid}")
def get_campaign(cid: str):
    try:
        store.campaigns.ensure_campaign_slim(cid)
        out = store.campaigns.read_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    # embedded so the frontend needn't chain a world fetch after this one
    wid = out["meta"].get("world", "")
    out["meta"]["world_name"] = store.worlds.world_name(wid) or wid
    return out


# Declared before the generic /campaigns/{cid}/{kind} routes so "export.epub" isn't captured as a kind.
@router.get("/campaigns/{cid}/export.epub")
def export_campaign_epub(cid: str):
    try:
        blob, filename = store.epub.build_epub(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return Response(content=blob, media_type="application/epub+zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


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


@router.get("/campaigns/{cid}/style")
def get_campaign_style(cid: str):
    try:
        meta = store.campaigns.read_campaign(cid)["meta"]
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"style_id": meta.get("style_id", "")}


@router.put("/campaigns/{cid}/style")
def put_campaign_style(cid: str, body: StyleSelect):
    try:
        store.campaigns.set_campaign_style(cid, body.style_id)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}


# ---- campaign sync ----
# Declared before the generic /campaigns/{cid}/{kind} routes so "incoming" is
# never captured as an entity kind.
@router.get("/campaigns/{cid}/incoming")
def get_incoming(cid: str):
    try:
        store.campaigns.ensure_campaign_slim(cid)
        return store.sync.incoming(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")


@router.post("/campaigns/{cid}/incoming/accept")
def post_accept(cid: str, body: RefList):
    try:
        store.campaigns.ensure_campaign_slim(cid)
        store.sync.accept(cid, [_dump(r) for r in body.refs])
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/incoming/reject")
def post_reject(cid: str, body: RefList):
    try:
        store.campaigns.ensure_campaign_slim(cid)
        store.sync.reject(cid, [_dump(r) for r in body.refs])
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}


# ---- scenes ----
# Declared before the generic /campaigns/{cid}/{kind} routes so "scenes" is
# never captured as an entity kind.
def _require_key(cfg: dict[str, str]) -> None:
    if cfg.get("provider", "openrouter") == "openrouter" and not cfg["openrouter_key"]:
        raise HTTPException(
            status_code=409,
            detail={"detail": "OpenRouter key not set", "kind": "missing_key"},
        )


def _persist_reply(cid: str, sid: str, text: str) -> None:
    """Split one model reply into per-speaker posts and append them (#744)."""
    players = frozenset(store.appearances.player_names(cid, sid))
    for seg in store.scenes.split_reply(text, players):
        store.scenes.append_message(cid, sid, "assistant", seg["content"], speaker=seg["speaker"])


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _sse_response(frames: list[str]):
    """A StreamingResponse that just replays already-computed SSE frames (used
    for the immediate-done / error-frame branches of the proposal route)."""
    async def event_stream():
        for f in frames:
            yield f
    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _fence_stream(cid: str, sid: str, messages: list[dict], cfg: dict,
                  client: LLMClient, finalize, on_error=None):
    """Stream one persisted turn while watching for a ```roll fence.

    Deltas are routed through a FenceWatcher, so an opener (even split across
    chunks) is never emitted and streaming stops once a fence closes. When the
    stream ends, `finalize(watcher)` (called with the lock/persist strategy of
    the caller — initial turn vs continuation) returns the trailing SSE frames
    (proposal / done). `on_error(watcher)` decides what to persist on an
    upstream LLM failure. Fence watching runs on persisted turns only;
    `_ephemeral_stream` is deliberately untouched.
    """
    async def event_stream():
        watcher = store.fence.FenceWatcher()
        try:
            async for delta in client.stream(messages, cfg):
                out = watcher.feed(delta)
                if out:
                    yield _sse({"delta": out})
                if watcher.complete:
                    break  # stop-after-fence: ignore anything past the close
            tail = watcher.finish()
            if tail:
                yield _sse({"delta": tail})
        except LLMError as exc:
            watcher.finish()
            if on_error is not None:
                on_error(watcher)
            yield _sse({"error": {"detail": exc.detail, "kind": exc.kind}})
            return
        for frame in finalize(watcher):
            yield frame

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _heal_current_proposal(cid: str, sid: str) -> None:
    """Complete the scene's current record's projection (roll + 🎲 line +
    metadata) before the record is retired or replaced. The record is the only
    recovery handle for a projection crash (roll tagged, line missing): the
    stale-retry heal in the POST roll-proposal route matches on the record's
    id and only projects superseded records that still carry a resolution, so
    once ``new`` overwrites ``data[sid]`` (or the frontend stops offering the
    superseded record) the roll would stand in rolls.json without its
    transcript line forever. Projection is idempotent pure file I/O, so
    healing an already-complete record is a cheap no-op. Only records whose
    resolution carries a roll ``result`` can project — declined records never
    store a resolution (and would have no roll to project if they somehow
    did), and pending/resolving records have nothing resolved yet; those are
    retired/replaced as before.

    INVARIANT (keep it when adding call sites): a record with a projectable
    resolution is never retired (``proposals.supersede``) nor replaced
    (``proposals.new``) before its projection completes — every retirement or
    replacement path calls this first. The heal is idempotent, and a crash
    during the heal leaves the record current, so the next attempt re-heals.
    The only remaining loss windows are the spec's two accepted fence-handoff
    ones."""
    rec = store.proposals.get(cid, sid)
    if (isinstance(rec, dict) and isinstance(rec.get("resolution"), dict)
            and "result" in rec["resolution"]):
        _project_resolution(cid, sid, rec["id"])


def _chat_stream(cid: str, sid: str, messages: list[dict], cfg: dict, client: LLMClient):
    """A normal persisted turn. A ```roll fence cuts the stream: the pending
    proposal record is written *before* the pre-fence narration persists, so a
    transcript that ends at a mechanical decision point always has a
    recoverable proposal (see the crash-window disclosure above)."""
    def finalize(watcher) -> list[str]:
        frames: list[str] = []
        if watcher.complete or watcher.truncated:
            payload = _make_proposal(cid, sid, watcher)
            _heal_current_proposal(cid, sid)  # new() erases the recovery handle
            rec = store.proposals.new(cid, sid, payload)
            _persist_reply(cid, sid, watcher.narration)
            frames.append(_sse({"proposal": {**payload, "id": rec["id"]}}))
        elif watcher.narration.strip():
            _persist_reply(cid, sid, watcher.narration)
        frames.append(_sse({"done": True}))
        return frames

    def on_error(watcher) -> None:
        if watcher.narration.strip():  # a normal turn keeps its partial reply
            _persist_reply(cid, sid, watcher.narration)

    return _fence_stream(cid, sid, messages, cfg, client, finalize, on_error)


def _continuation_stream(cid: str, sid: str, pid: str, messages: list[dict],
                         cfg: dict, client: LLMClient):
    """Stream a proposal's continuation and commit it atomically. A supersede
    that lands mid-stream makes ``commit_narration`` return False and the
    streamed text is dropped. A follow-up fence in the continuation hands off
    under one lock: commit the old record's narration, then mint the new
    pending record, then emit its proposal event."""
    def finalize(watcher) -> list[str]:
        frames: list[str] = []
        persist = lambda: _persist_reply(cid, sid, watcher.narration)  # noqa: E731
        if watcher.complete or watcher.truncated:
            with store.proposals.locked(cid):
                if store.proposals.commit_narration(cid, sid, pid, persist):
                    payload = _make_proposal(cid, sid, watcher)
                    # the lock is reentrant, so healing (projection) is safe
                    # here; new() below erases the recovery handle
                    _heal_current_proposal(cid, sid)
                    rec = store.proposals.new(cid, sid, payload)
                    frames.append(_sse({"proposal": {**payload, "id": rec["id"]}}))
        else:
            store.proposals.commit_narration(cid, sid, pid, persist)
        frames.append(_sse({"done": True}))
        return frames

    # No on_error: an upstream failure mid-continuation drops the partial
    # (nothing persisted) and leaves the record resolved/declined, so a retry
    # re-streams a fresh continuation cleanly.
    return _fence_stream(cid, sid, messages, cfg, client, finalize)


def _ephemeral_stream(messages: list[dict], cfg: dict, client: LLMClient):
    """Stream a generation without persisting it to any scene (used by the opener)."""
    async def event_stream():
        try:
            async for delta in client.stream(messages, cfg):
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except LLMError as exc:
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
    out = []
    for tok in tokens:
        kind, _, aid = tok.partition(":")
        try:
            if kind == "pcs":
                name = store.pcs.read_pc(store.overlay.pc_root(cid, aid), aid)["meta"].get("name", aid)
            else:
                name = store.characters.read_character(
                    store.overlay.char_root(cid, aid), aid)["meta"].get("name", aid)
        except (store.characters.CharacterNotFound, store.pcs.PCNotFound):
            name = aid
        out.append({"kind": kind, "id": aid, "name": name})
    return out


@router.post("/campaigns/{cid}/scene-suggestions")
async def post_scene_suggestions(cid: str, after: str | None = None, offscreen: bool = False,
                                 client: LLMClient = Depends(get_llm)):
    try:
        store.campaigns.read_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    cfg = store.read_config()
    _require_key(cfg)
    # with >2 startable greetings the same call also ranks them for the chooser
    candidates = store.suggest.greeting_candidates(cid, after, pcless=offscreen)
    messages = store.suggest.build_prompt(store.suggest.build_snapshot(cid, offscreen=offscreen),
                                          candidates, offscreen=offscreen)
    try:
        text = await client.complete(messages, cfg)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail={"detail": exc.detail, "kind": exc.kind})
    loc_names = {e["id"]: e.get("name", e["id"]) for e in store.overlay.list_entities(cid, "locations")}
    out = []
    for s in store.suggest.parse_output(text, cid, offscreen=offscreen):
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
def post_chat(cid: str, sid: str, turn: ChatTurn, client: LLMClient = Depends(get_llm)):
    _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    _heal_current_proposal(cid, sid)     # retirement paths heal first (invariant)
    store.proposals.supersede(cid, sid)  # a new send retires any pending decision
    if store.scenes.is_pcless(cid, sid) or not turn.content.strip():
        # ephemeral turn, never stored: a director note steering one generation
        # (pcless), or — in any scene — an empty send meaning "next NPC round"
        note = turn.content.strip() or prompts.render("scene/director_note.j2")
        messages = store.context.build_director_messages(cid, sid, note)
        return _chat_stream(cid, sid, messages, cfg, client)
    names = store.appearances.player_names(cid, sid)
    speaker = names[0] if len(names) == 1 else None
    if speaker:
        store.scenes.stamp_user_speaker(cid, sid, speaker)
    store.scenes.append_message(cid, sid, "user", turn.content, speaker=speaker)
    messages = store.context.build_messages(cid, sid)
    return _chat_stream(cid, sid, messages, cfg, client)


@router.post("/campaigns/{cid}/scenes/{sid}/retry")
def post_retry(cid: str, sid: str, client: LLMClient = Depends(get_llm)):
    scene = _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    _heal_current_proposal(cid, sid)     # retirement paths heal first (invariant)
    store.proposals.supersede(cid, sid)  # a fresh generation retires the old decision
    if not scene["messages"]:
        raise HTTPException(status_code=400, detail="nothing to retry")
    messages = store.context.build_messages(cid, sid)
    return _chat_stream(cid, sid, messages, cfg, client)


@router.post("/campaigns/{cid}/scenes/{sid}/regenerate")
def post_regenerate(cid: str, sid: str, body: RegenerateBody | None = None,
                    client: LLMClient = Depends(get_llm)):
    """Redo the most recent post: drop a trailing assistant reply, stream a fresh one."""
    scene = _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    _heal_current_proposal(cid, sid)     # retirement paths heal first (invariant)
    store.proposals.supersede(cid, sid)  # regenerating retires the old decision
    msgs = scene["messages"]
    if not msgs:
        raise HTTPException(status_code=400, detail="nothing to regenerate")
    if msgs[-1]["role"] == "assistant":
        if all(m["role"] == "assistant" for m in msgs):
            raise HTTPException(status_code=400, detail="cannot regenerate the opening post")
        if msgs[-1].get("speaker") == store.scenes.ROLL_SPEAKER:
            raise HTTPException(status_code=400, detail="cannot regenerate past a manual dice roll")
        store.scenes.remove_trailing_assistant_run(cid, sid)
    messages = store.context.build_messages(cid, sid)
    guidance = (body.guidance or "").strip() if body else ""
    if guidance:
        messages.append({
            "role": "system",
            "content": prompts.render("scene/regenerate_guidance.j2", guidance=guidance),
        })
    return _chat_stream(cid, sid, messages, cfg, client)


@router.get("/campaigns/{cid}/chronicle")
def get_chronicle(cid: str):
    _campaign_root_or_404(cid)
    return store.chronicle.recent(cid, 50)


@router.post("/campaigns/{cid}/scenes/{sid}/absorb")
async def post_absorb(cid: str, sid: str,
                      client: LLMClient = Depends(get_llm)):
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
        store.absorb.plot_snapshot(cid), store.absorb.group_snapshot(cid))
    try:
        text = await client.complete(messages, cfg)
    except LLMError as exc:
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
            d_text = await client.complete(msgs, cfg)
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


def _record_name(cid: str, kind: str, eid: str) -> str | None:
    """Display name for a campaign record (materialized or still inherited
    from the world), or None if it no longer exists anywhere."""
    try:
        if kind == "characters":
            return store.overlay.read_character(cid, eid)["meta"].get("name", eid)
        if kind in store.entities.ENTITY_KINDS:
            return store.overlay.read_entity(cid, kind, eid)["meta"].get("name", eid)
    except (store.characters.CharacterNotFound, store.entities.EntityNotFound):
        return None
    return None


@router.get("/campaigns/{cid}/changes")
def get_changes(cid: str):
    _campaign_root_or_404(cid)
    data = store.changes.read(cid)
    scenes_by_id = {s["id"]: s for s in store.scenes.list_scenes(cid)}
    try:
        chron = store.chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 — garbled chronicle.json: labels degrade, no 500
        chron = {}
    out: list[dict] = []
    for ref, entry in data.items():
        kind, _, eid = ref.partition("/")
        name = _record_name(cid, kind, eid)
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
    _campaign_root_or_404(cid)
    return store.overlay.list_pcs(cid)


@router.post("/campaigns/{cid}/pcs")
def post_campaign_pc(cid: str, body: PCCreate):
    # Campaign-local PC overlay: tags are free strings (no world-vocabulary check).
    _campaign_root_or_404(cid)
    pid, vid = store.overlay.create_pc(cid, body.name, body.tags, body.version_name, body.persona)
    return {"pc": pid, "version": vid}


@router.get("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
def get_campaign_image(cid: str, char: str, vid: str, name: str, request: Request):
    _campaign_root_or_404(cid)
    return _serve_image(store.overlay.image_root(cid, char, vid, name), char, vid, name, request=request)


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
async def put_campaign_image(cid: str, char: str, vid: str, name: str, file: UploadFile = File(...)):
    root = _campaign_root_or_404(cid)
    data = await file.read()
    fn = file.filename or ""
    ext = fn.rsplit(".", 1)[-1] if "." in fn else ""
    try:
        stored = store.assets.put_image(root, char, vid, name, data, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": name, "ext": stored}


@router.delete("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
def delete_campaign_image(cid: str, char: str, vid: str, name: str):
    _campaign_root_or_404(cid)
    # tombstone so a still-materialized world image doesn't show back through
    # the overlaid read the moment the campaign's own copy is gone (get_campaign_image).
    store.overlay.delete_image(cid, char, vid, name)
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}/promote")
def promote_campaign_image(cid: str, char: str, vid: str, name: str):
    _campaign_root_or_404(cid)
    try:
        store.overlay.promote_image(cid, char, vid, name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    return {"ok": True}


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}/images/avatar/focus")
def put_campaign_avatar_focus(cid: str, char: str, vid: str, body: AvatarFocus):
    root = _campaign_root_or_404(cid)
    # a thin campaign may only have this avatar through the inherited world
    # character, so the existence gate must check the overlay union, not croot alone
    names = {i["name"] for i in store.overlay.list_images(cid, char, vid)}
    if store.assets.AVATAR not in names:
        raise HTTPException(status_code=404, detail="image not found")
    # the write always lands campaign-side; overlay.read_focus then finds this
    # campaign focus.json and treats the campaign as authoritative going forward
    store.assets.write_focus(root, char, vid, body.focus)
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/versions/{vid}/images/copy-from-greeting")
def post_copy_campaign_image_from_greeting(cid: str, char: str, vid: str, body: CopyFromGreeting):
    root = _campaign_root_or_404(cid)
    # the source greeting image may still be inherited (unmaterialized) in a thin
    # campaign; resolve its root through the overlay so the copy still finds it,
    # while the destination character write always targets the campaign root
    src_root = store.overlay.image_root(cid, body.gid, "default", body.name, base="greetings")
    # the free gallery_N slot must account for inherited world gallery images too,
    # or a campaign-side copy can reuse a name that shadows one of them
    taken_names = {i["name"] for i in store.overlay.list_images(cid, char, vid)}
    try:
        stored = store.image_subjects.copy_to_character(root, body.gid, body.name, char, vid, body.slot,
                                                         src_root=src_root, taken_names=taken_names)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="source image not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    p = store.assets.image_path(root, char, vid, stored)
    return {"name": stored, "ext": p.suffix.lstrip(".").lower() if p else ""}


def _campaign_wroot(cid: str):
    return store.worlds.world_root(store.campaigns.read_campaign(cid)["meta"].get("world", ""))


@router.get("/campaigns/{cid}/characters")
def get_campaign_characters(cid: str):
    _campaign_root_or_404(cid)
    return store.overlay.list_characters(cid)


@router.get("/campaigns/{cid}/characters/{char}")
def get_campaign_character(cid: str, char: str):
    _campaign_root_or_404(cid)
    try:
        return store.overlay.read_character(cid, char)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")


@router.put("/campaigns/{cid}/characters/{char}")
def put_campaign_character(cid: str, char: str, body: DefaultVersion):
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "characters", char)
        store.characters.set_default_version(root, char, body.default_version)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/versions")
def post_campaign_character_version(cid: str, char: str, body: VersionCreate):
    _campaign_root_or_404(cid)
    if store.appearances.locked_version(cid, "characters", char) is not None:
        raise HTTPException(status_code=409, detail="character is locked to one version")
    try:
        root = store.overlay.ensure_actor_writable(cid, "characters", char)
        vid = store.characters.create_version(root, char, body.name, body.card)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"version": vid}


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}")
def put_campaign_character_version(cid: str, char: str, vid: str, body: VersionUpdate):
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "characters", char)
        store.characters.update_version(root, char, vid, body.card)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/campaigns/{cid}/characters/{char}/versions/{vid}")
def delete_campaign_character_version(cid: str, char: str, vid: str):
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "characters", char)
        store.characters.delete_version(root, char, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.get("/campaigns/{cid}/pcs/{pid}")
def get_campaign_pc(cid: str, pid: str):
    _campaign_root_or_404(cid)
    try:
        return store.pcs.read_pc(store.overlay.pc_root(cid, pid), pid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")


@router.put("/campaigns/{cid}/pcs/{pid}")
def put_campaign_pc(cid: str, pid: str, body: PCUpdate):
    # Campaign tags are free strings: no world-vocabulary check on this side.
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "pcs", pid)
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
    _campaign_root_or_404(cid)
    if store.appearances.locked_version(cid, "pcs", pid) is not None:
        raise HTTPException(status_code=409, detail="pc is locked to one version")
    try:
        root = store.overlay.ensure_actor_writable(cid, "pcs", pid)
        vid = store.pcs.create_version(root, pid, body.name, body.persona)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    return {"version": vid}


@router.put("/campaigns/{cid}/pcs/{pid}/versions/{vid}")
def put_campaign_pc_version(cid: str, pid: str, vid: str, body: PersonaVersionUpdate):
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "pcs", pid)
        store.pcs.update_version(root, pid, vid, body.persona)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/campaigns/{cid}/pcs/{pid}/versions/{vid}")
def delete_campaign_pc_version(cid: str, pid: str, vid: str):
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "pcs", pid)
        store.pcs.delete_version(root, pid, vid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/campaigns/{cid}/{kind}/{aid}/pick-version")
def post_pick_version(cid: str, kind: str, aid: str, body: PickBody):
    _campaign_root_or_404(cid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    if store.appearances.locked_version(cid, kind, aid) is not None:
        # checked before existence: the sibling versions were purged by the pick
        raise HTTPException(status_code=409, detail=f"{kind}/{aid} is already locked")
    if store.appearances.actor_hash(store.overlay.actor_root(cid, kind, aid), kind, aid, body.version) is None:
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


def _seat_cast_member(cid: str, sid: str, body: Appear) -> None:
    """Validate + resolve one cast addition and record it. Raises HTTPException
    (404 unknown, 400 bad role) or store.appearances.AppearError (already cast)."""
    if body.kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    role = "player" if body.kind == "pcs" else (body.role or "npc")
    if role not in ("player", "npc"):
        raise HTTPException(status_code=400, detail="role must be player or npc")
    if role == "player" and store.scenes.is_pcless(cid, sid):
        raise HTTPException(status_code=400, detail="cannot seat a player in an offscreen scene")
    version = body.version
    try:
        if version is None:
            if body.kind == "characters":
                version = store.characters.read_character(
                    store.overlay.char_root(cid, body.id), body.id)["meta"]["default_version"]
            else:
                version = store.pcs.read_pc(
                    store.overlay.pc_root(cid, body.id), body.id)["meta"]["default_version"]
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound):
        raise HTTPException(status_code=404, detail="actor not found")
    # A first appearance locks lazily by copying from the world when the campaign
    # lacks the version; validate a supplied version against the campaign-visible
    # actor first so a purged/tombstoned one can't be revived. An already-cast
    # actor skips this — appear() reports the lock conflict (409), no revival.
    if store.appearances.locked_version(cid, body.kind, body.id) is None and store.appearances.actor_hash(
            store.overlay.actor_root(cid, body.kind, body.id), body.kind, body.id, version) is None:
        raise HTTPException(status_code=404, detail="actor or version not found in campaign")
    store.appearances.appear(cid, sid, body.kind, body.id, version, role)


@router.post("/campaigns/{cid}/scenes/{sid}/cast")
def post_scene_cast(cid: str, sid: str, body: Appear):
    _require_scene(cid, sid)
    try:
        _seat_cast_member(cid, sid, body)
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
    added, skipped = 0, []
    for ref in body.refs:
        try:
            _seat_cast_member(cid, sid, ref)
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
    history = store.scenes.get_location_history(cid, sid)

    def ref(eid: str) -> dict:
        try:
            name = store.overlay.read_entity(cid, "locations", eid)["meta"].get("name", eid)
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


@router.get("/campaigns/{cid}/scenes/{sid}/style")
def get_scene_style(cid: str, sid: str):
    scene = _require_scene(cid, sid)
    return {"style_id": scene["meta"].get("style_id", "")}


@router.put("/campaigns/{cid}/scenes/{sid}/style")
def put_scene_style(cid: str, sid: str, body: StyleSelect):
    _require_scene(cid, sid)
    store.scenes.set_style(cid, sid, body.style_id)
    return {"ok": True}


@router.post("/campaigns/{cid}/scenes/{sid}/roll")
def post_scene_roll(cid: str, sid: str, body: RollBody):
    """Manual dice roll: resolve, log to <campaign>/rolls.json, and write the
    result into the scene transcript as a narrator line."""
    _require_scene(cid, sid)
    try:
        result = store.dice.roll(body.notation)
    except store.dice.DiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Collapse newlines so a hostile label can't fake a blank-line boundary
    # followed by a marker like "**You:**" and get split into a forged
    # message by scenes._markers on the next read.
    label = " ".join((body.label or "").split()) or None
    entry = store.rolls.append(cid, sid, label, result)
    line = store.dice.format_roll(result, label)
    store.scenes.append_message(cid, sid, "assistant", line, speaker=store.scenes.ROLL_SPEAKER)
    return {"ok": True, "roll": entry, "message": line}


# ---- mechanics roll proposals & manual checks (#162, Phase 4) --------------
# Declared before the generic /campaigns/{cid}/{kind} entity routes below.
#
# Crash-window disclosure (accepted risk, per the phase-4 spec). The fence
# handoffs are serialized against concurrent writers by the per-campaign
# proposals lock, but are NOT crash-atomic across files (proposals.json and
# the scene transcript are separate writes; grimoire is a local single-process
# app with no cross-file journal). Two microsecond-wide windows exist, both
# bounded and non-corrupting:
#   - initial fence: a crash between writing the pending record and persisting
#     the pre-fence narration leaves a recoverable chip whose last narration
#     beat is missing — the player can still adjudicate or decline;
#   - follow-up fence: a crash between the old record's `narrated` write and
#     the new pending record leaves the continuation fully persisted and the
#     follow-up check simply lost; play continues on the next send.
# The guaranteed invariant: no roll is ever duplicated or lost once logged, no
# narration is attributed to a superseded decision, and no crash leaves an
# unrecoverable or corrupted state. Full journaling was rejected as
# disproportionate for a local single-user store.


def _scene_messages(cid: str, sid: str) -> list[dict]:
    return store.scenes.read_scene(cid, sid)["messages"]


def _roll_label(res: dict) -> str:
    return f"{res['actor_label']} — {res['check_label']}"


def _make_proposal(cid: str, sid: str, watcher) -> dict:
    """Build the proposal payload from a closed/truncated fence: parse the
    body, resolve the actor against the scene's available checks (exact
    kind:id, then case-insensitive label), and collect `problems`. A proposal
    is never silently dropped — a bad one just opens the chip in Modify."""
    fields, problems = store.fence.parse_roll_body(watcher.body or "")
    if watcher.truncated:
        problems = [*problems, "roll fence truncated"]

    actors = store.checks.available_checks(cid, sid)
    available = {a["ref"]: a["checks"] for a in actors}

    actor_raw = fields.get("actor")
    actor, actor_label = None, actor_raw
    if actor_raw:
        for a in actors:
            if a["ref"] == actor_raw:
                actor, actor_label = a["ref"], a["label"]
                break
        if actor is None:
            for a in actors:
                if a["label"].lower() == str(actor_raw).strip().lower():
                    actor, actor_label = a["ref"], a["label"]
                    break
    if actor is None:
        problems = [*problems, "actor could not be resolved"]

    mid = store.modules.resolve(cid)
    check_labels: dict[str, str] = {}
    if mid is not None:
        pack = store.modules.load_pack(mid)
        cd = pack["checks"] if isinstance(pack["checks"], dict) else {}
        check_labels = {k: (v.get("label", k) if isinstance(v, dict) else k)
                        for k, v in cd.items() if k != "_defaults"}

    check = fields.get("check")
    if check is not None:
        if check not in check_labels:
            problems = [*problems, "unknown check id"]
        elif actor is not None and check not in dict(available.get(actor, [])):
            problems = [*problems, "check unavailable to this actor"]
    elif fields:
        # fields is non-empty (e.g. valid JSON with an actor but no `check`
        # key) yet carries no check id — parse_roll_body's own tolerant path
        # already flags this same case (see fence.py); a wholly unparseable
        # body (fields == {}) is left alone here since "roll request was
        # unparseable" already covers it and a second problem would be noise.
        problems = [*problems, "roll request had no check id"]

    return {"check": check, "check_label": check_labels.get(check, check),
            "actor": actor, "actor_label": actor_label,
            "difficulty": fields.get("difficulty"), "modifier": fields.get("modifier", 0),
            "reason": fields.get("reason", ""), "available": available,
            "problems": problems}


def _project_resolution(cid: str, sid: str, pid: str) -> dict | None:
    """Idempotent, crash-recoverable projection of a resolved proposal into the
    roll log and transcript. Runs entirely under the proposals per-campaign
    lock (pure file I/O, no LLM), so concurrent retries serialize. The updated
    resolution is carried forward across each CAS — never rebuilt from a stale
    local — so the roll_id survives the line_intent write.

    Defensive re-validation: the caller checks status *before* acquiring this
    lock, so a supersede + brand-new record for the scene can land in that
    narrow window. If the scene's current record no longer carries this
    proposal id, or has no stored resolution yet, another actor won — return
    None and do nothing (no roll append, no line). Deliberately NOT a status
    check: a record that still carries this id but was superseded after
    resolving (status "superseded") must still project — its roll stands in
    the transcript as history per spec; only the automatic continuation is
    cancelled (by commit_narration), not the roll projection itself. The
    roll_id/line_intent backfills persist via ``update_resolution``, which
    writes metadata without touching terminal status, so a same-id superseded
    record keeps them (a status CAS would silently lose and drop them)."""
    with store.proposals.locked(cid):
        rec = store.proposals.get(cid, sid)
        if (rec is None or rec.get("id") != pid
                or rec.get("status") not in ("resolved", "superseded")
                or not isinstance(rec.get("resolution"), dict)):
            return None
        res = dict(rec["resolution"])
        entry = store.rolls.find_or_append_by_proposal(
            cid, sid, _roll_label(res), res["result"], proposal=pid)
        res = {**res, "roll_id": entry["id"]}
        store.proposals.update_resolution(cid, sid, pid, res)
        if "line_intent" not in res:
            res = {**res, "line_intent": len(_scene_messages(cid, sid))}
            store.proposals.update_resolution(cid, sid, pid, res)
        line = store.checks.format_check_roll(res)
        if not any(m.get("speaker") == store.scenes.ROLL_SPEAKER and m["content"] == line
                   for m in _scene_messages(cid, sid)[res["line_intent"]:]):
            store.scenes.append_message(cid, sid, "assistant", line,
                                        speaker=store.scenes.ROLL_SPEAKER)
        return res


def _continuation_rule_bodies(cid: str, resolution: dict) -> tuple[list[str], list[str]]:
    """Bodies of every `on_roll` rules doc plus the check's linked `rules:`
    docs (the continuation's mechanical grounding)."""
    on_roll_docs: list[str] = []
    check_docs: list[str] = []
    mid = store.modules.resolve(cid)
    if mid is None:
        return on_roll_docs, check_docs
    pack = store.modules.load_pack(mid)
    for doc in pack.get("rules", []):
        if doc.get("on_roll"):
            rule = store.modules.read_rule(mid, doc["id"])
            if rule is not None:
                on_roll_docs.append(rule["body"].strip())
    cd = pack["checks"] if isinstance(pack["checks"], dict) else {}
    check = cd.get(resolution.get("check"))
    if isinstance(check, dict):
        for rid in (check.get("rules") or []):
            rule = store.modules.read_rule(mid, rid)
            if rule is not None:
                check_docs.append(rule["body"].strip())
    return on_roll_docs, check_docs


def _continuation_messages(cid: str, sid: str, resolution: dict) -> list[dict]:
    messages = store.context.build_messages(cid, sid)
    on_roll_docs, check_docs = _continuation_rule_bodies(cid, resolution)
    messages.append({"role": "system", "content": prompts.render(
        "scene/roll_result.j2", resolution=resolution,
        on_roll_docs=on_roll_docs, check_docs=check_docs)})
    return messages


def _declined_continuation_messages(cid: str, sid: str) -> list[dict]:
    messages = store.context.build_messages(cid, sid)
    messages.append({"role": "system", "content": prompts.render("scene/roll_declined.j2")})
    return messages


@router.get("/campaigns/{cid}/scenes/{sid}/roll-proposal")
def get_roll_proposal(cid: str, sid: str):
    """Recovery endpoint: the scene's current proposal record (or null)."""
    _require_scene(cid, sid)
    return {"record": store.proposals.get(cid, sid)}


@router.post("/campaigns/{cid}/scenes/{sid}/roll-proposal")
def post_roll_proposal(cid: str, sid: str, body: ProposalAction,
                       client: LLMClient = Depends(get_llm)):
    """Adjudicate a roll proposal (accept / decline). Idempotent by proposal
    id, keyed to the scene's current record. Every state change is a CAS; a
    lost transition means someone else moved the record (a new send
    superseded it, another accept won the claim) — we stop dead: no
    projection, no continuation, 409."""
    _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    pid = body.proposal
    rec = store.proposals.get(cid, sid)
    if rec is None or rec.get("id") != pid:
        raise HTTPException(status_code=409, detail="proposal is stale")
    if rec.get("status") == "superseded":
        # A same-id superseded record that already resolved still owes its roll
        # + 🎲 line to the transcript (the roll stands as history per spec). If
        # a crash landed between the roll append and the line write, no other
        # path heals it, so a stale client's retry becomes the recovery path:
        # project idempotently (pure file I/O), then still 409 — no
        # continuation is ever offered for a superseded record. A record
        # superseded while still pending/resolving has no resolution to
        # project; it stays a plain 409.
        if isinstance(rec.get("resolution"), dict):
            _project_resolution(cid, sid, pid)
        raise HTTPException(status_code=409, detail="proposal is stale")
    status = rec["status"]

    if status == "narrated":
        return _sse_response([_sse({"done": True})])
    if status == "resolving":
        raise HTTPException(status_code=409, detail="adjudication in progress")

    if status == "pending":
        if body.action == "decline":
            if not store.proposals.transition(cid, sid, pid, ("pending",), "declined"):
                raise HTTPException(status_code=409, detail="proposal is stale")
        else:  # accept
            if not store.proposals.claim(cid, sid, pid):
                raise HTTPException(status_code=409, detail="adjudication in progress")
            p = rec["payload"]
            try:
                resolution = store.checks.resolve_check(
                    cid, body.check or p.get("check"), body.actor or p.get("actor"),
                    body.difficulty if body.difficulty is not None else p.get("difficulty"),
                    body.modifier if body.modifier is not None else (p.get("modifier") or 0))
            except Exception as exc:  # noqa: BLE001 — any failure reverts cleanly
                store.proposals.transition(cid, sid, pid, ("resolving",), "pending")
                detail = (str(exc) if isinstance(exc, store.checks.CheckError)
                          else "the check could not be resolved")
                return _sse_response([_sse({"error": {"detail": detail, "kind": "check_error"}})])
            if not store.proposals.transition(cid, sid, pid, ("resolving",), "resolved", resolution):
                # superseded mid-resolve: the pure roll result is discarded unlogged
                raise HTTPException(status_code=409, detail="proposal was superseded")
        status = store.proposals.get(cid, sid)["status"]

    if status == "resolved":
        resolution = _project_resolution(cid, sid, pid)
        if resolution is None:
            # Another actor won the scene's record in the window between our
            # pre-stream status read and the projection lock (a supersede +
            # brand-new fence/send). Nothing was projected — stop dead, same
            # as any other lost-race case, with a clean done frame.
            return _sse_response([_sse({"done": True})])
        messages = _continuation_messages(cid, sid, resolution)
    elif status == "declined":
        messages = _declined_continuation_messages(cid, sid)
    else:  # defensive: a race moved the record out from under us
        raise HTTPException(status_code=409, detail="proposal is stale")
    return _continuation_stream(cid, sid, pid, messages, cfg, client)


@router.get("/campaigns/{cid}/scenes/{sid}/checks")
def get_scene_checks(cid: str, sid: str):
    _require_scene(cid, sid)
    return {"actors": store.checks.available_checks(cid, sid)}


@router.post("/campaigns/{cid}/scenes/{sid}/check")
def post_scene_check(cid: str, sid: str, body: CheckBody):
    """Manual check: run the pure resolver, log the roll (no proposal tag), and
    append the 🎲 line — the same resolution path an accepted proposal takes."""
    _require_scene(cid, sid)
    try:
        resolution = store.checks.resolve_check(
            cid, body.check, body.actor, body.difficulty, body.modifier or 0)
    except store.checks.CheckError as e:
        raise HTTPException(status_code=400, detail=str(e))
    entry = store.rolls.append(cid, sid, _roll_label(resolution), resolution["result"])
    resolution = {**resolution, "roll_id": entry["id"]}
    line = store.checks.format_check_roll(resolution)
    store.scenes.append_message(cid, sid, "assistant", line, speaker=store.scenes.ROLL_SPEAKER)
    return {"ok": True, "resolution": resolution, "roll": entry, "message": line}


# registered before the generic /campaigns/{cid}/{kind} entity routes below,
# which would otherwise swallow /campaigns/x/rolls
@router.get("/campaigns/{cid}/rolls")
def get_rolls(cid: str):
    if not store.campaigns.campaign_meta_path(cid).exists():
        raise HTTPException(status_code=404, detail="campaign not found")
    return list(reversed(store.rolls.read(cid)))


@router.post("/campaigns/{cid}/rolls/{rid}/replay")
def post_roll_replay(cid: str, rid: str):
    try:
        return {"ok": True, **store.rolls.replay(cid, rid)}
    except store.rolls.RollNotFound:
        raise HTTPException(status_code=404, detail="roll not found")


# also registered before the generic /campaigns/{cid}/{kind} entity routes,
# same reasoning as /campaigns/x/rolls above
@router.get("/campaigns/{cid}/module")
def get_campaign_module(cid: str):
    try:
        meta = store.campaigns.read_campaign(cid)["meta"]
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    setting = (meta.get("module") or "").strip()
    resolved = store.modules.resolve(cid)
    source = None
    if resolved is not None:
        source = "campaign" if setting and setting != "none" else "world"
    return {"setting": setting, "resolved": resolved, "source": source}


@router.put("/campaigns/{cid}/module")
def put_campaign_module(cid: str, body: ModuleSetting):
    try:
        with store.sheets.lock_for(cid):
            store.modules.set_campaign_module(cid, body.module.strip())
            store.audit.clear_baselines(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    return {"ok": True}


# also registered before the generic /campaigns/{cid}/{kind} entity routes,
# same reasoning as /campaigns/x/module above
@router.get("/campaigns/{cid}/sheets")
def get_campaign_sheets(cid: str):
    _campaign_root_or_404(cid)
    return {"coverage": store.sheets.coverage(cid),
            "refs": store.sheets.list_refs(cid)}


@router.get("/campaigns/{cid}/sheets/{kind}/{eid}")
def get_campaign_sheet(cid: str, kind: str, eid: str):
    _campaign_root_or_404(cid)
    return {"sheet": store.sheets.read(cid, kind, eid)}


@router.put("/campaigns/{cid}/sheets/{kind}/{eid}")
def put_campaign_sheet(cid: str, kind: str, eid: str, body: SheetBody):
    _campaign_root_or_404(cid)
    try:
        store.sheets.write(cid, kind, eid, body.sheet_type, body.fields, expected=body.expected)
    except store.sheets.SheetConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.delete("/campaigns/{cid}/sheets/{kind}/{eid}")
def delete_campaign_sheet(cid: str, kind: str, eid: str, gen: str | None = None):
    _campaign_root_or_404(cid)
    try:
        return {"ok": store.sheets.delete(cid, kind, eid, expected_gen=gen)}
    except store.sheets.SheetConflict as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put("/campaigns/{cid}/sheets/{kind}/{eid}/creation")
def put_campaign_sheet_creation(cid: str, kind: str, eid: str, body: SheetCreationBody):
    _campaign_root_or_404(cid)
    try:
        store.sheets.write_creation(cid, kind, eid, body.sheet_type, body.spends,
                                    expected=body.expected)
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound, store.entities.EntityNotFound):
        raise HTTPException(status_code=404, detail=f"{kind} not found")
    except store.sheets.SheetConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"sheet": store.sheets.read(cid, kind, eid)}


@router.post("/campaigns/{cid}/sheets/{kind}/{eid}/advance")
def post_sheet_advance(cid: str, kind: str, eid: str, body: SheetAdvanceBody):
    _campaign_root_or_404(cid)
    try:
        return {"sheet": store.sheets.advance(cid, kind, eid, body.field)}
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound, store.entities.EntityNotFound):
        raise HTTPException(status_code=404, detail=f"{kind} not found")
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/campaigns/{cid}/{kind}/instantiate/{mid}/{content_id}")
def post_campaign_instantiate(cid: str, kind: str, mid: str, content_id: str):
    _campaign_root_or_404(cid)
    try:
        content = store.modules.read_content(mid, kind, content_id)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.modules.ContentNotFound:
        raise HTTPException(status_code=404, detail="content not found")
    try:
        eid = store.overlay.create_entity(cid, kind, content["name"], content["body"],
                                          content.get("keys", ""), "",
                                          fields=_content_fields(kind, content))
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    if content.get("sheet_type"):
        try:
            store.sheets.write(cid, kind, eid, content["sheet_type"], content.get("fields"),
                              expected=None)
        except (store.modules.ModuleNotFound, store.sheets.SheetError) as e:
            # The entity was just created campaign-side via overlay.create_entity
            # with no world counterpart, so overlay.delete_entity removes the
            # campaign file cleanly (no tombstone) -- roll it back so a failed
            # instantiate leaves no sheetless orphan.
            store.overlay.delete_entity(cid, kind, eid)
            raise HTTPException(status_code=400, detail=str(e))
    return {"id": eid}


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
    except store.scenes.RollMessageImmutable:
        raise HTTPException(status_code=400, detail="a dice roll's transcript line can't be edited")
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
    _campaign_root_or_404(cid)
    marks = store.playing.read_marks(cid)
    mark_of = {g: "played" for g in marks["played"]}
    mark_of.update({g: "completed" for g in marks["completed"]})
    mark_of.update({g: "skipped" for g in marks["skipped"]})
    return [{**g, "mark": mark_of.get(g["id"])} for g in store.overlay.list_greetings(cid)]


@router.post("/campaigns/{cid}/greetings")
def post_campaign_greeting(cid: str, body: GreetingCreate):
    _campaign_root_or_404(cid)
    gid = store.overlay.create_greeting(cid, body.name, body.character, body.version,
                                       body.body, body.requires_tags,
                                       body.predecessor_join, present=body.present,
                                       pcless=body.pcless)
    return {"id": gid}


@router.get("/campaigns/{cid}/greetings/{gid}")
def get_campaign_greeting(cid: str, gid: str):
    _campaign_root_or_404(cid)
    try:
        g = store.overlay.read_greeting(cid, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    plotmap = store.overlay.read_plotmap(cid)
    g["edges"] = store.greetings.edges_of(plotmap, gid)
    g["predecessors"] = store.greetings.predecessors_of(plotmap, gid)
    return g


@router.put("/campaigns/{cid}/greetings/{gid}")
def put_campaign_greeting(cid: str, gid: str, body: GreetingUpdate):
    _campaign_root_or_404(cid)
    try:
        store.overlay.update_greeting(cid, gid, name=body.name, body=body.body,
                                     requires_tags=body.requires_tags,
                                     predecessor_join=body.predecessor_join,
                                     present=body.present, pcless=body.pcless)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    return {"ok": True}


@router.put("/campaigns/{cid}/greetings/{gid}/edges")
def put_campaign_greeting_edges(cid: str, gid: str, body: Edges):
    _campaign_root_or_404(cid)
    try:
        store.overlay.read_greeting(cid, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    store.overlay.set_edges(cid, gid, body.leads_to, body.excludes)
    return {"ok": True}


@router.delete("/campaigns/{cid}/greetings/{gid}")
def delete_campaign_greeting(cid: str, gid: str):
    _campaign_root_or_404(cid)
    try:
        store.overlay.delete_greeting(cid, gid)
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
        new_sid = store.playing.start_from_greeting(cid, sid, body.greeting)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    except (store.playing.PlayError, store.appearances.AppearError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True, "id": new_sid}


@router.post("/campaigns/{cid}/scenes/{sid}/opener")
def post_opener(cid: str, sid: str, body: Opener, client: LLMClient = Depends(get_llm)):
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
    _campaign_root_or_404(cid)
    return _campaign_entity_list(cid, kind)


@router.post("/campaigns/{cid}/{kind}")
def post_campaign_entity(cid: str, kind: str, body: EntityCreate):
    _campaign_root_or_404(cid)
    return _campaign_entity_create(cid, kind, body)


@router.get("/campaigns/{cid}/{kind}/{eid}")
def get_campaign_entity(cid: str, kind: str, eid: str):
    _campaign_root_or_404(cid)
    return _campaign_entity_read(cid, kind, eid)


@router.put("/campaigns/{cid}/{kind}/{eid}")
def put_campaign_entity(cid: str, kind: str, eid: str, body: EntityUpdate):
    _campaign_root_or_404(cid)
    return _campaign_entity_update(cid, kind, eid, body)


@router.delete("/campaigns/{cid}/{kind}/{eid}")
def delete_campaign_entity(cid: str, kind: str, eid: str):
    _campaign_root_or_404(cid)
    return _campaign_entity_delete(cid, kind, eid)


@router.get("/campaigns/{cid}/{kind}/{eid}/images")
def list_campaign_entity_images(cid: str, kind: str, eid: str):
    _campaign_root_or_404(cid)
    _entity_kind_or_404(kind)
    return store.overlay.list_images(cid, eid, "default", base=kind)


@router.get("/campaigns/{cid}/{kind}/{eid}/images/{name}")
def get_campaign_entity_image(cid: str, kind: str, eid: str, name: str, request: Request):
    _campaign_root_or_404(cid)
    _image_kind_or_404(kind)
    return _serve_image(store.overlay.image_root(cid, eid, "default", name, base=kind),
                        eid, "default", name, base=kind, request=request)


@router.put("/campaigns/{cid}/{kind}/{eid}/images/{name}")
async def put_campaign_entity_image(cid: str, kind: str, eid: str, name: str, file: UploadFile = File(...)):
    return await _entity_image_put(_campaign_root_or_404(cid), kind, eid, name, file)


@router.delete("/campaigns/{cid}/{kind}/{eid}/images/{name}")
def delete_campaign_entity_image(cid: str, kind: str, eid: str, name: str):
    _campaign_root_or_404(cid)
    _entity_kind_or_404(kind)
    store.overlay.delete_image(cid, eid, "default", name, base=kind)
    return {"ok": True}


@router.post("/campaigns/{cid}/{kind}/{eid}/images/{name}/promote")
def promote_campaign_entity_image(cid: str, kind: str, eid: str, name: str):
    _campaign_root_or_404(cid)
    _entity_kind_or_404(kind)
    try:
        store.overlay.promote_image(cid, eid, "default", name, base=kind)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    return {"ok": True}

"""World-scoped routes: the world record itself, its bound mechanics module
and sheets, tags, player characters, calendar and lorebook import.

Characters and greetings have their own modules; the generic
``/worlds/{wid}/{kind}`` entity surface lives in ``entities``.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import store
from .common import _content_fields, _dump, _world_root_or_404
from .models import (LorebookCommit, ModuleSetting, NameBody, PCCreate, PCUpdate,
                     PersonaVersionCreate, PersonaVersionUpdate, SheetBody, SheetCreationBody)

router = APIRouter()


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


@router.put("/worlds/{wid}/module")
def put_world_module(wid: str, body: ModuleSetting):
    try:
        # Lock every campaign of this world -- regardless of current override
        # -- BEFORE reading any per-campaign setting. Enumerating "affected"
        # (non-overridden) campaigns from metadata before locking would race
        # a concurrent campaign-module PUT: a campaign could flip from
        # overridden to inheriting after enumeration but before this route's
        # rebind, leaving it unlocked and holding stale baselines against the
        # new world default. Locking every campaign of the world -- then
        # re-reading each override under the lock -- closes that window.
        all_cids = sorted(
            c["id"] for c in store.campaigns.list_campaigns() if c.get("world") == wid
        )
        with store.locks.hold_all(all_cids):     # sorted order; see locks.hold_all
            store.modules.set_world_module(wid, body.module.strip())
            for c in all_cids:
                try:
                    meta = store.campaigns.read_campaign(c)["meta"]
                except store.campaigns.CampaignNotFound:
                    continue                     # deleted while we held the lock
                setting = (meta.get("module") or "").strip()
                if not setting:                  # no per-campaign override (fresh read)
                    store.audit.clear_baselines(c)
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=404, detail="world not found")
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.modules.ModuleError:
        raise HTTPException(status_code=400, detail="'none' is reserved")
    return {"ok": True}


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
        store.sheets.write_world(wid, mid, kind, eid, body.sheet_type, body.fields,
                                 expected=body.expected)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.sheets.SheetConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.put("/worlds/{wid}/sheets/{mid}/{kind}/{eid}/creation")
def put_world_sheet_creation(wid: str, mid: str, kind: str, eid: str, body: SheetCreationBody):
    _world_root_or_404(wid)
    try:
        store.sheets.write_world_creation(wid, mid, kind, eid, body.sheet_type, body.spends,
                                          expected=body.expected)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound, store.entities.EntityNotFound):
        raise HTTPException(status_code=404, detail=f"{kind} not found")
    except store.sheets.SheetConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"sheet": store.sheets.read_world(wid, mid, kind, eid)}


@router.delete("/worlds/{wid}/sheets/{mid}/{kind}/{eid}")
def delete_world_sheet(wid: str, mid: str, kind: str, eid: str, gen: str | None = None):
    _world_root_or_404(wid)
    try:
        return {"ok": store.sheets.delete_world(wid, mid, kind, eid, expected_gen=gen)}
    except store.sheets.SheetConflict as e:
        raise HTTPException(status_code=409, detail=str(e))


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
            store.sheets.write_world(wid, mid, kind, eid, content["sheet_type"], content.get("fields"),
                                     expected=None)
        except (store.modules.ModuleNotFound, store.sheets.SheetError) as e:
            # Sheet write failed after the entity was already created -- roll
            # it back so a failed instantiate leaves no sheetless orphan.
            store.entities.delete_entity(root, kind, eid)
            raise HTTPException(status_code=400, detail=str(e))
    return {"id": eid}


# ---- world tags ----
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


# ---- world PCs ----
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


# ---- world lorebook import ----
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


@router.get("/worlds/{wid}/calendar/months")
def get_world_calendar_months(wid: str, year: int):
    if not store.worlds.world_exists(wid):
        raise HTTPException(status_code=404, detail="world not found")
    cfg = store.calendars.read_calendar(store.worlds.world_root(wid))
    try:
        return {"months": store.calendars.get_provider(cfg["primary"]).months(year)}
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))

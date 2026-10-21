"""worlds: mechanics responsibilities."""

from __future__ import annotations

from fastapi import HTTPException

from ... import store
from ..common import _content_fields, _sheet_failure_status, _world_root_or_404
from ..models import ModuleSetting, SheetBody, SheetCreationBody
from ._router import router as router


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
                    # Inside the hold that covers the clear (#409). A world route
                    # writes these campaigns and nothing in `/api/campaigns/...`
                    # stamps them -- the same call `sync.demote` and
                    # `store.reclassify` make, for the same reason.
                    store.revision.bump(c)
    except store.worlds.WorldNotFound as exc:
        raise HTTPException(status_code=404, detail="world not found") from exc
    except store.modules.ModuleNotFound as exc:
        raise HTTPException(status_code=404, detail="module not found") from exc
    except store.modules.ModuleError as exc:
        raise HTTPException(status_code=400, detail="'none' is reserved") from exc
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
    except store.modules.ModuleNotFound as exc:
        raise HTTPException(status_code=404, detail="module not found") from exc
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
    except store.modules.ModuleNotFound as exc:
        raise HTTPException(status_code=404, detail="module not found") from exc
    except store.sheets.SheetConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@router.put("/worlds/{wid}/sheets/{mid}/{kind}/{eid}/creation")
def put_world_sheet_creation(wid: str, mid: str, kind: str, eid: str, body: SheetCreationBody):
    _world_root_or_404(wid)
    try:
        store.sheets.write_world_creation(wid, mid, kind, eid, body.sheet_type, body.spends,
                                          expected=body.expected)
    except store.modules.ModuleNotFound as exc:
        raise HTTPException(status_code=404, detail="module not found") from exc
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound, store.entities.EntityNotFound) as exc:
        raise HTTPException(status_code=404, detail=f"{kind} not found") from exc
    except store.sheets.SheetConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"sheet": store.sheets.read_world(wid, mid, kind, eid)}


@router.delete("/worlds/{wid}/sheets/{mid}/{kind}/{eid}")
def delete_world_sheet(wid: str, mid: str, kind: str, eid: str, gen: str | None = None):
    _world_root_or_404(wid)
    try:
        return {"ok": store.sheets.delete_world(wid, mid, kind, eid, expected_gen=gen)}
    except store.sheets.SheetConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/worlds/{wid}/{kind}/instantiate/{mid}/{content_id}")
def post_world_instantiate(wid: str, kind: str, mid: str, content_id: str):
    root = _world_root_or_404(wid)
    try:
        content = store.modules.read_content(mid, kind, content_id)
    except store.modules.ModuleNotFound as exc:
        raise HTTPException(status_code=404, detail="module not found") from exc
    except store.modules.ContentNotFound as exc:
        raise HTTPException(status_code=404, detail="content not found") from exc
    try:
        eid = store.entities.create_entity(root, kind, content["name"], content["body"],
                                           keys=content.get("keys", ""), owners="",
                                           fields=_content_fields(kind, content))
    except store.entities.UnknownKind as exc:
        raise HTTPException(status_code=404, detail="unknown kind") from exc
    if content.get("sheet_type"):
        try:
            store.sheets.write_world(wid, mid, kind, eid, content["sheet_type"], content.get("fields"),
                                     expected=None)
        except (store.modules.ModuleNotFound, store.sheets.SheetError) as e:
            # Sheet write failed after the entity was already created -- roll
            # it back so a failed instantiate leaves no sheetless orphan.
            # Deliberately no `forget_world_record` here, unlike the delete
            # routes: this restores the state before the create, and anything
            # sitting under `eid` was already sitting there then (#225).
            store.entities.delete_entity(root, kind, eid)
            raise HTTPException(status_code=_sheet_failure_status(e), detail=str(e)) from e
    return {"id": eid}

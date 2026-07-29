"""Mechanics-module authoring (#160): the ``/modules`` CRUD surface over
``store.module_edit``, plus import/export of module packs."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from .. import store
from .models import (ModuleCheckBody, ModuleContentBody, ModuleCreate, ModuleDefaultsBody,
                     ModuleGroupBody, ModuleLayoutBody, ModuleManifestBody, ModuleRenameBody,
                     ModuleRuleBody, ModuleSheetTypeBody, ModuleThemeBody)

router = APIRouter()


# ---- modules (#160) ----
@router.get("/modules")
def get_modules():
    return store.modules.list_modules()


@router.post("/modules")
def post_module(body: ModuleCreate):
    return {"id": store.module_edit.create_module(body.name)}


@router.get("/modules/{mid}")
def get_module(mid: str):
    try:
        with store.module_edit.locked():
            return store.modules.load_pack(mid)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")


@router.delete("/modules/{mid}")
def delete_module(mid: str):
    try:
        store.module_edit.delete_module(mid)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.modules.ModuleError:
        raise HTTPException(status_code=400, detail="built-in modules cannot be deleted")
    return {"ok": True}


def _module_edit_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.modules.ModuleError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/modules/{mid}/duplicate")
def post_module_duplicate(mid: str, body: ModuleCreate):
    return {"id": _module_edit_call(store.module_edit.duplicate_module, mid, body.name)}


@router.put("/modules/{mid}/manifest")
def put_module_manifest(mid: str, body: ModuleManifestBody):
    return _module_edit_call(store.module_edit.set_manifest, mid,
                             name=body.name, description=body.description,
                             version=body.version, dice=body.dice,
                             notes=body.notes, dry_run=body.dry_run)


@router.put("/modules/{mid}/groups/{gid}")
def put_module_group(mid: str, gid: str, body: ModuleGroupBody):
    return _module_edit_call(store.module_edit.upsert_group, mid, gid,
                             body.group, dry_run=body.dry_run)


@router.delete("/modules/{mid}/groups/{gid}")
def delete_module_group(mid: str, gid: str, dry_run: bool = False):
    return _module_edit_call(store.module_edit.delete_group, mid, gid, dry_run=dry_run)


@router.put("/modules/{mid}/sheet-types/{tid}")
def put_module_sheet_type(mid: str, tid: str, body: ModuleSheetTypeBody):
    return _module_edit_call(store.module_edit.upsert_sheet_type, mid, tid,
                             body.sheet_type, dry_run=body.dry_run)


@router.delete("/modules/{mid}/sheet-types/{tid}")
def delete_module_sheet_type(mid: str, tid: str, dry_run: bool = False):
    return _module_edit_call(store.module_edit.delete_sheet_type, mid, tid, dry_run=dry_run)


@router.put("/modules/{mid}/checks/{check_id}")
def put_module_check(mid: str, check_id: str, body: ModuleCheckBody):
    return _module_edit_call(store.module_edit.upsert_check, mid, check_id,
                             body.check, dry_run=body.dry_run)


@router.delete("/modules/{mid}/checks/{check_id}")
def delete_module_check(mid: str, check_id: str, dry_run: bool = False):
    return _module_edit_call(
        store.module_edit.delete_check, mid, check_id, dry_run=dry_run,
        pre_swap=store.module_edit.check_proposal_guard(mid, check_id))


@router.put("/modules/{mid}/check-defaults")
def put_module_check_defaults(mid: str, body: ModuleDefaultsBody):
    return _module_edit_call(store.module_edit.set_check_defaults, mid,
                             body.defaults, dry_run=body.dry_run)


@router.get("/modules/{mid}/rules/{slug}")
def get_module_rule(mid: str, slug: str):
    with store.module_edit.locked():
        try:
            rule = store.modules.read_rule(mid, slug)
        except store.modules.ModuleNotFound:
            raise HTTPException(status_code=404, detail="module not found")
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")
        return rule


@router.put("/modules/{mid}/rules/{slug}")
def put_module_rule(mid: str, slug: str, body: ModuleRuleBody):
    return _module_edit_call(store.module_edit.upsert_rule, mid, slug,
                             body.flags, body.body, dry_run=body.dry_run)


@router.delete("/modules/{mid}/rules/{slug}")
def delete_module_rule(mid: str, slug: str, dry_run: bool = False):
    return _module_edit_call(store.module_edit.delete_rule, mid, slug, dry_run=dry_run)


@router.put("/modules/{mid}/content/{kind}/{id}")
def put_module_content(mid: str, kind: str, id: str, body: ModuleContentBody):
    return _module_edit_call(store.module_edit.upsert_content, mid, kind, id,
                             name=body.name, body=body.body, keys=body.keys,
                             fields=body.fields, sheet=body.sheet,
                             dry_run=body.dry_run)


@router.delete("/modules/{mid}/content/{kind}/{id}")
def delete_module_content(mid: str, kind: str, id: str, dry_run: bool = False):
    return _module_edit_call(store.module_edit.delete_content, mid, kind, id, dry_run=dry_run)


@router.put("/modules/{mid}/layout")
def put_module_layout(mid: str, body: ModuleLayoutBody):
    return _module_edit_call(store.module_edit.set_layout, mid,
                             body.layout, dry_run=body.dry_run)


@router.put("/modules/{mid}/theme")
def put_module_theme(mid: str, body: ModuleThemeBody):
    return _module_edit_call(store.module_edit.set_theme, mid,
                             body.theme, dry_run=body.dry_run)


@router.post("/modules/{mid}/rename")
def post_module_rename(mid: str, body: ModuleRenameBody):
    return _module_edit_call(store.module_edit.rename, mid, body.kind,
                             body.address, body.to, dry_run=body.dry_run)


@router.get("/modules/{mid}/export")
def get_module_export(mid: str):
    data = _module_edit_call(store.module_edit.export_module, mid)
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{mid}.zip"'})


IMPORT_CAP = 16 * 1024 * 1024


@router.post("/modules/import")
async def post_module_import(request: Request):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > IMPORT_CAP:
        raise HTTPException(status_code=413, detail="zip too large")
    fd, tmp_name = tempfile.mkstemp(suffix=".zip")
    total = 0
    try:
        # atomic-ok: a system temp file for the uploaded zip, not a store
        # record; read by import_module and unlinked in the finally below
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                total += len(chunk)
                if total > IMPORT_CAP:
                    raise HTTPException(status_code=413, detail="zip too large")
                f.write(chunk)
        # In a worker thread (#234). import_module takes the module-edit lock,
        # which is now cross-process: when another backend holds it, acquire
        # sleeps in its retry loop for up to LOCK_TIMEOUT. This route is
        # `async def`, so an inline call would block the event loop for 30s and
        # freeze every unrelated request and live stream, not just this import.
        # (Sync `def` routes get FastAPI's threadpool for free; the `async`
        # ones have to ask.)
        return {"id": await run_in_threadpool(
            _module_edit_call, store.module_edit.import_module, Path(tmp_name))}
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


# Grouped with the other module-content routes for readability; its path
# shape is distinct from the generic entity routes either way.
@router.get("/modules/{mid}/content/{kind}/{id}")
def get_module_content(mid: str, kind: str, id: str):
    try:
        with store.module_edit.locked():
            return store.modules.read_content(mid, kind, id)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.modules.ContentNotFound:
        raise HTTPException(status_code=404, detail="content not found")

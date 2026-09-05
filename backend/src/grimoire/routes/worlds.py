"""World-scoped routes: the world record itself, its bound mechanics module
and sheets, tags, player characters (personas and their per-version images),
calendar, and the two import flows that populate a world wholesale — a
lorebook, and a scenario card (#217).

Characters and greetings have their own modules; the generic
``/worlds/{wid}/{kind}`` entity surface lives in ``entities``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from .. import store
from ..llm import LLMClient
from . import runs
from .common import (
    _content_fields,
    _dump,
    _require_connection,
    _serve_image,
    _sheet_failure_status,
    _spooled_upload,
    _upload_image_ext,
    _world_root_or_404,
    draft_completion,
    get_llm,
    image_draft_prompt,
)
from .models import (
    AvatarFocus,
    CalendarConfig,
    ImageDescription,
    LorebookCommit,
    ModuleSetting,
    NameBody,
    PCCreate,
    PCUpdate,
    PersonaVersionCreate,
    PersonaVersionUpdate,
    ScenarioProposal,
    ScenarioUrlBody,
    SheetBody,
    SheetCreationBody,
)

router = APIRouter()


# ---- worlds ----
@router.get("/worlds")
def get_worlds():
    # The cover token is derived HERE rather than in `store.worlds.list_worlds`,
    # exactly as the campaigns list derives its own: nothing about a cover is
    # written into `world.md`, and a store reader that resolved one would put a
    # `covers` import and a per-world `stat` in front of every caller --
    # `routes/todo.py`'s chore sweep and `routes/shell.py`'s badge included --
    # which none of them asked for.
    return [{**w, "cover": store.covers.world_cover_version(w["id"])}
            for w in store.worlds.list_worlds()]


@router.post("/worlds")
def post_world(body: NameBody):
    wid = store.worlds.create_world(body.name)
    # A store with a world in it has been set up, whatever happens to that world
    # afterwards -- recording it here rather than waiting for a config read is
    # what makes "deleting your content does not reopen the wizard" true even
    # when nothing read the config in between (#194).
    store.config.mark_setup_done()
    return {"id": wid}


@router.get("/worlds/{wid}")
def get_world(wid: str):
    try:
        out = store.worlds.read_world(wid)
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=404, detail="world not found")
    out["meta"]["cover"] = store.covers.world_cover_version(wid)
    return out


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
def delete_world(wid: str, request: Request):
    try:
        store.worlds.delete_world(wid)
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=404, detail="world not found")
    except store.worlds.WorldInUse as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    # AFTER the delete, and for the campaign route's reason: world ids are
    # slugs too, so a replacement of the same name would otherwise be handed
    # this world's taglines and scenario proposals.
    runs.forget_subject(request.app, runs.world_subject(wid))
    return {"ok": True}


# Declared here rather than in `entities`, which registers the
# `/worlds/{wid}/{kind}` catch-all -- `routes/__init__` includes that module
# last precisely so a literal second segment like `fork` is still reachable,
# and `tests/test_route_order.py` fails if that ever stops being true.
@router.post("/worlds/{wid}/fork")
def post_world_fork(wid: str, body: NameBody):
    """Copy `wid` into a brand-new world called `body.name`.

    A deep copy of the whole directory, not a reference: nothing the fork holds
    is shared with the world it came from, and nothing at all happens to that
    world (`store/worlds/lifecycle.py`). Returns the new world's id, the same
    shape `POST /worlds` and `POST /worlds/import` return -- the client
    navigates or refreshes with it.

    A `def`, so FastAPI runs it in a threadpool: copying a world with a full
    character gallery is a gigabyte of I/O, and doing that on the event loop
    would stall every other request for its duration.
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        return {"id": store.worlds.fork_world(wid, name)}
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=404, detail="world not found") from None
    except store.worlds.WorldIdConflictError as exc:
        # The copy is finished and fine; it just could not be given an id
        # before another writer took one. 409 rather than 500: retrying is the
        # right move, which is not what a 500 tells a client (matching the
        # answer `POST /worlds/import` gives for the same collision).
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/worlds/{wid}/campaigns")
def get_world_campaigns(wid: str):
    return store.sync.campaigns_for_world(wid)


# ---- world bundles: export / import (#54) ----
#
# `export.zip` is a literal third segment and `entities` registers the generic
# `/worlds/{wid}/{kind}` that would swallow it -- safe because `entities` is
# included last (see routes/__init__), and pinned by tests/test_route_order.py.
IMPORT_CAP = 4 * 1024 * 1024 * 1024


@router.get("/worlds/{wid}/export.zip")
def get_world_export(wid: str):
    """The whole world directory as a bundle.

    Built to a temp file and streamed rather than returned as bytes: a world
    with a full character gallery runs past a gigabyte, and buffering that into
    a response is how the Android build dies. A sync `def` route, so FastAPI
    runs the zipping in its threadpool and the event loop keeps serving.
    """
    _world_root_or_404(wid)
    fd, tmp_name = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        store.world_bundle.write_bundle(wid, tmp)
        size = tmp.stat().st_size
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    def stream():
        try:
            with open(tmp, "rb") as f:      # atomic guard: read-only, not a record write
                while chunk := f.read(1024 * 1024):
                    yield chunk
        finally:
            tmp.unlink(missing_ok=True)

    return StreamingResponse(
        stream(), media_type="application/zip",
        # `identity` is what makes GZipMiddleware stand aside (it skips any
        # response that already declares an encoding). Deflating a zip of
        # already-deflated records buys nothing and costs a full pass over a
        # gigabyte -- and standing aside is also what keeps Content-Length,
        # so the browser can show real download progress.
        headers={"Content-Disposition":
                 f'attachment; filename="{store.world_bundle.bundle_filename(wid)}"',
                 "Content-Encoding": "identity",
                 "Content-Length": str(size)})


@router.post("/worlds/import")
async def post_world_import(request: Request):
    """Import a bundle as a **new** world; the body is the raw zip.

    Raw rather than multipart, matching `POST /modules/import`: the two are the
    same operation, and a gigabyte of multipart framing buys nothing.
    """
    async with _spooled_upload(request, IMPORT_CAP, "bundle too large") as tmp:
        try:
            # In a worker thread: unzipping a large world would otherwise block
            # the event loop for the whole import (see post_module_import).
            wid = await run_in_threadpool(store.world_bundle.import_bundle, tmp)
        except store.world_bundle.BundleConflict as exc:
            # A good bundle that lost an id race, not a bad upload -- 409, so a
            # client can retry it rather than reading 400 as "this file is
            # broken" (Codex review). Must be caught before BundleError.
            raise HTTPException(status_code=409, detail=str(exc))
        except store.world_bundle.BundleError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    # Same reason as `post_world`: a store someone imported a world into has
    # been set up, so deleting that world later must not reopen the wizard
    # (#194). Past the rename the world exists, so a failure here must not be
    # reported as a failed import -- the caller would retry and import a second
    # copy (Codex review). The flag is backfilled by the next config read
    # anyway; this only saves that read a scan.
    try:
        store.config.mark_setup_done()
    except OSError:
        pass
    return {"id": wid}


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
            # Deliberately no `forget_world_record` here, unlike the delete
            # routes: this restores the state before the create, and anything
            # sitting under `eid` was already sitting there then (#225).
            store.entities.delete_entity(root, kind, eid)
            raise HTTPException(status_code=_sheet_failure_status(e), detail=str(e))
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
        # The VERSION first, for the reason the campaign-scoped twin of this
        # route states at length: `set_default_version` validates before it
        # writes, so an unknown one refuses before the tags are saved rather
        # than after. No write token is involved on this side -- a world route
        # stamps none -- but a 404 that has already changed the record is the
        # same defect either way.
        if body.default_version is not None:
            store.pcs.set_default_version(root, pid, body.default_version)
        if body.tags is not None:
            _validate_tags(root, body.tags)
            store.pcs.set_tags(root, pid, body.tags)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/worlds/{wid}/pcs/{pid}")
def delete_world_pc(wid: str, pid: str):
    root = _world_root_or_404(wid)
    try:
        store.pcs.delete_pc(root, pid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    store.overlay.forget_world_record(root, "pcs", pid)   # #225
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


# ---- world PC images (#219) — the same surface world characters have, keyed
# on `pcs.ASSET_BASE` so `<world>/pcs/<pid>/assets/<vid>/` holds the files.
# Deliberately under `/versions/{vid}/` rather than the entity kinds' flat
# `/pcs/{pid}/images`: a PC's art belongs to the version it depicts, exactly as
# a character's does, and the flat shape is already claimed by the generic
# `/worlds/{wid}/{kind}/{eid}/images` routes.
def _world_pc_version_or_404(wid: str, pid: str, vid: str):
    """The world root, once `pid`/`vid` are known to name a real PC version.

    Every image route below goes through this, and that is a deliberate
    departure from the character routes it otherwise mirrors: those hand
    `assets.put_image` whatever id the URL carried, and `put_image` creates the
    directory it writes into. A typo'd PC or version therefore *succeeds* with
    200 and leaves `pcs/<typo>/assets/<vid>/avatar.png` on disk -- a folder no
    listing shows (`list_pcs` needs `pc.md`, `read_pc` needs the version file)
    and nothing ever collects. The character surface has the same hole -- filed
    as #360, since closing it changes the status code of nine live handlers --
    but there is no reason to ship a second copy of it here.
    """
    root = _world_root_or_404(wid)
    try:
        store.pcs.require_version(root, pid, vid)   # two stats, no read
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return root


@router.get("/worlds/{wid}/pcs/{pid}/versions/{vid}/images")
def list_world_pc_images(wid: str, pid: str, vid: str):
    return store.assets.list_images(_world_pc_version_or_404(wid, pid, vid), pid, vid,
                                    base=store.pcs.ASSET_BASE)


@router.get("/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}")
def get_world_pc_image(wid: str, pid: str, vid: str, name: str, request: Request):
    return _serve_image(_world_pc_version_or_404(wid, pid, vid), pid, vid, name,
                        base=store.pcs.ASSET_BASE, request=request)


@router.put("/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}")
async def put_world_pc_image(wid: str, pid: str, vid: str, name: str,
                             file: UploadFile = File(...)):
    root = _world_pc_version_or_404(wid, pid, vid)
    data = await file.read()
    ext = _upload_image_ext(data)  # the bytes name the type, not `file.filename` (#321)
    try:
        stored = store.assets.put_image(root, pid, vid, name, data, ext,
                                        base=store.pcs.ASSET_BASE)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": name, "ext": stored}


@router.delete("/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}")
def delete_world_pc_image(wid: str, pid: str, vid: str, name: str):
    # Gated on the PC and version, not on the image: removing an image that is
    # already gone is the caller getting what they asked for, but removing one
    # from a PC that does not exist is a typo worth reporting.
    store.assets.delete_image(_world_pc_version_or_404(wid, pid, vid), pid, vid, name,
                              base=store.pcs.ASSET_BASE)
    return {"ok": True}


@router.post("/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}/promote")
def promote_world_pc_image(wid: str, pid: str, vid: str, name: str):
    root = _world_pc_version_or_404(wid, pid, vid)
    try:
        store.assets.promote_image(root, pid, vid, name, base=store.pcs.ASSET_BASE)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    except ValueError as exc:
        # an externally-placed file whose extension we never accepted for upload
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.put("/worlds/{wid}/pcs/{pid}/versions/{vid}/images/avatar/focus")
def put_world_pc_avatar_focus(wid: str, pid: str, vid: str, body: AvatarFocus):
    root = _world_pc_version_or_404(wid, pid, vid)
    if store.assets.image_path(root, pid, vid, store.assets.AVATAR,
                               base=store.pcs.ASSET_BASE) is None:
        raise HTTPException(status_code=404, detail="image not found")
    store.assets.write_focus(root, pid, vid, body.focus, base=store.pcs.ASSET_BASE)
    return {"ok": True}


@router.post("/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}/description/draft",
             status_code=202)
def post_world_pc_image_description_draft(
        wid: str, pid: str, vid: str, name: str, request: Request,
        client: LLMClient = Depends(get_llm),
        x_grimoire_attempt: str | None = Header(default=None)):
    """Start a model-drafted first pass at what this PC's picture shows."""
    root = _world_pc_version_or_404(wid, pid, vid)
    try:
        subject = store.pcs.read_pc(root, pid)["meta"]["name"]
    except store.pcs.PCNotFound:
        subject = ""
    conn, messages = image_draft_prompt(
        store.assets.image_path(root, pid, vid, name, base=store.pcs.ASSET_BASE), subject)

    async def work():
        return await draft_completion(
            client, conn, messages, "image-description",
            lambda text: {"description": store.image_drafts.parse_output(text)})

    return runs.run_draft(request.app, runs.world_subject(wid),
                          "image-description", x_grimoire_attempt, work)


@router.put("/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}/description")
def put_world_pc_image_description(wid: str, pid: str, vid: str, name: str,
                                   body: ImageDescription):
    root = _world_pc_version_or_404(wid, pid, vid)
    try:
        store.image_descriptions.set_description(root, pid, vid, name, body.description,
                                                 base=store.pcs.ASSET_BASE)
    except ValueError:
        # `from None`: the strict-write ValueError is this module's own
        # implementation detail, and chaining it onto the 404 says nothing a
        # caller can act on.
        raise HTTPException(status_code=404, detail="image not found") from None
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


# ---- scenario-card import (#217) ----
# Three routes, one flow: get the card (from an upload or a URL), extract a
# proposal from it, and — after the user has edited that proposal — write it.
# Only the third writes anything, which is what makes the review gate real
# rather than a confirmation dialog over work already done.
async def _scenario_proposal(card: dict, client: LLMClient, conn: dict, root) -> dict:
    """Extract a proposal from `card`, as a detached run's outcome.

    One bounded completion, exactly like the tagline and voice-anchor previews.
    A reply the extraction cannot use — prose, a refusal, a truncated object —
    is not an error: `parse_output` yields empty sections and the proposal falls
    back to what the card alone holds, which is its own world-info and its
    openers. A provider that *failed* is a different thing and surfaces as the
    status its failure maps to (#213), leaving the world untouched — reported
    on the run now rather than raised, so a client that was away while it
    failed still reads the reason.
    """
    # Metered like every other generation (#152): a world-level call, so the
    # row carries no campaign -- the same shape the tagline and voice-anchor
    # previews file.
    #
    # The world's roster is read in the SHAPE, after the call, so each proposed
    # row can say whether the import would REUSE a character of that name
    # rather than create one -- and so it describes the roster as it stands
    # when the proposal is built rather than as it stood minutes earlier, which
    # detachment now makes a real difference.
    return await draft_completion(
        client, conn, store.scenario.build_prompt(card), "scenario",
        lambda text: store.scenario.proposal(
            card, store.scenario.parse_output(text),
            [c["name"] for c in store.characters.list_characters(root)]))


@router.post("/worlds/{wid}/scenario/parse", status_code=202)
async def post_scenario_parse(wid: str, request: Request,
                              file: UploadFile = File(...), format: str = Form(...),
                              client: LLMClient = Depends(get_llm),
                              x_grimoire_attempt: str | None = Header(default=None)):
    """Start a scenario proposal from an uploaded card. 202 and a run to poll.

    **Reserved before the upload is read**, which is the ordering these two
    routes need and the other ten do not: the attempt has to be discoverable
    for as long as the request could still be accepted, and reading a card off
    a phone is long enough for a connection to die inside it. Reserved after,
    a client whose POST vanished mid-upload asks `?attempt=` and is told,
    truthfully, that there is no such run -- and then the handler goes on to
    reserve one and spend a provider call, so the retry pays twice.

    A card that will not parse is still a 400 while the reader is holding the
    file picker: `runs.reservation` releases the run on that path, so the
    refusal reaches them as a refusal and the record does not stay `running`.
    """
    root = _world_root_or_404(wid)
    # Before the upload is read, and before the download in the sibling route:
    # "you have no model configured" is a setup mistake, and reporting it only
    # after the user has fixed a card (or waited on a slow host) tells them the
    # wrong thing first. Before the RESERVATION too, so a keyless install does
    # not leave a failed run behind for every press.
    conn = _require_connection("scenario")
    run, fresh = await _reserve_scenario(request, wid, x_grimoire_attempt)
    if not fresh:
        return {"run": runs.run_payload(run)}
    with runs.reservation(request.app, run):
        data = await file.read()
        try:
            card = store.cards.loads(data, format)
        except store.cards.CardParseError as exc:
            raise HTTPException(status_code=400, detail=f"could not parse card: {exc}")
        return await run_in_threadpool(_detach_scenario, request, run, card,
                                       client, conn, root)


@router.post("/worlds/{wid}/scenario/parse-url", status_code=202)
async def post_scenario_parse_url(wid: str, body: ScenarioUrlBody, request: Request,
                                  client: LLMClient = Depends(get_llm),
                                  x_grimoire_attempt: str | None = Header(default=None)):
    """The same, from a URL, and the sharper case for reserving first: the
    download is a whole HTTP fetch against somebody else's host, and it is the
    longest thing either of these routes does before any of the work is
    addressable.

    The download itself stays on the request rather than in the run: a URL that
    fetches nothing is a 404 the reader retypes, and hiding it behind a run
    would make "not a valid URL" arrive as a failed generation."""
    root = _world_root_or_404(wid)
    conn = _require_connection("scenario")
    run, fresh = await _reserve_scenario(request, wid, x_grimoire_attempt)
    if not fresh:
        return {"run": runs.run_payload(run)}
    with runs.reservation(request.app, run):
        try:
            # The download is blocking and this route is async, so it goes to the
            # threadpool rather than stalling the event loop for a slow host --
            # the same treatment `post_world_import` gives its unpacking.
            data, fmt, _url, _node = await run_in_threadpool(
                store.characters.download_card, body.url)
            card = store.cards.loads(data, fmt)
        except store.chub.ChubParseError:
            raise HTTPException(status_code=400, detail="not a valid URL")
        except store.chub.ChubFetchError:
            raise HTTPException(status_code=404,
                                detail="could not fetch a card from that URL")
        except store.cards.CardParseError as exc:
            raise HTTPException(status_code=400, detail=f"could not parse card: {exc}")
        return await run_in_threadpool(_detach_scenario, request, run, card,
                                       client, conn, root)


async def _reserve_scenario(request: Request, wid: str, attempt_id: str | None):
    """Reserve the run both parse routes publish before doing anything slow.

    **Through `run_in_threadpool`, and it has to be.** These two routes are
    `async def` -- one awaits an upload, the other a download -- so their
    bodies run ON the lifespan loop, and reserving a run builds its handshake
    events through the portal, which raises when called from the loop thread.
    Every other draft route is a plain `def` and is already in a worker; these
    two get there explicitly.
    """
    return await run_in_threadpool(
        runs.reserve_draft, request.app, runs.world_subject(wid), "scenario",
        attempt_id)


def _detach_scenario(request: Request, run, card: dict, client: LLMClient,
                     conn: dict, root) -> dict:
    """Hand the proposal to the runner. Both parse routes end here.

    One function because the two differ only in where the card came from, and
    that difference is settled by the time either gets here.

    In a worker for `_reserve_scenario`'s reason: `runner.start` submits
    through the same portal, which refuses to be called from the loop.
    """
    async def work():
        return await _scenario_proposal(card, client, conn, root)

    runs.start_computing(request.app, run, work)
    return {"run": runs.run_payload(run)}


@router.post("/worlds/{wid}/scenario/import")
async def post_scenario_import(wid: str, body: ScenarioProposal):
    root = _world_root_or_404(wid)
    prop = _dump(body)
    # `art` is a request option, not part of the proposal — it rides on the same
    # body so the reviewer's "download the openers' images" checkbox needs no
    # second round trip, and is lifted back out here.
    prop.pop("art", None)
    try:
        # Localizing the openers' art downloads one image per reference, so the
        # whole write goes to the threadpool: without it a card with a dozen
        # illustrated openers holds the event loop for the length of a dozen
        # HTTP fetches.
        return await run_in_threadpool(store.scenario.apply, root, wid, prop, art=body.art)
    except store.lorebook.LorebookError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---- the world's calendar (#223) ----
#
# The same calendar.json the campaign routes edit, one level up. A world's copy
# is the DEFAULT its campaigns are created with: `campaigns.create_campaign`
# reads it out of the world root and writes the whole file -- `confirmed`
# included -- into the new campaign. A creation that names a calendar of its own
# is an explicit choice and confirms itself, so what the world's flag actually
# carries is every other path: a campaign created without one starts confirmed
# only because this world says it is settled, and its clock and scene inspector
# never ask again.
#
# Declared in `worlds`, which `routes.__init__` includes before `entities`, so
# `/worlds/{wid}/{kind}` cannot capture `calendar`; `test_route_order.py` holds
# that rather than this comment.
@router.get("/worlds/{wid}/calendar")
def get_world_calendar_config(wid: str):
    return store.calendars.read_calendar(_world_root_or_404(wid))


@router.put("/worlds/{wid}/calendar")
def put_world_calendar_config(wid: str, body: CalendarConfig):
    root = _world_root_or_404(wid)
    # Field-by-field rather than `_dump(body)` for the same reason the campaign
    # route does it: the store re-normalizes and coerces every field anyway
    # (`stale_after_days` included -- 0 or a missing field means "no opinion",
    # not a threshold of zero), so this is the shape of the request, not its
    # validation. `warn_days` is the one field where 0 is a real answer ("no
    # warnings"), which is why its no-opinion value is None instead.
    cfg = {"primary": body.primary, "secondary": body.secondary, "confirmed": body.confirmed,
           "stale_after_days": body.stale_after_days,
           "warn_days": body.warn_days}
    try:
        store.calendars.validate_calendar(cfg)
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    store.calendars.write_calendar(root, cfg)
    return {"ok": True}


@router.get("/worlds/{wid}/calendar/months")
def get_world_calendar_months(wid: str, year: int):
    if not store.worlds.world_exists(wid):
        raise HTTPException(status_code=404, detail="world not found")
    cfg = store.calendars.read_calendar(store.worlds.world_root(wid))
    try:
        return {"months": store.calendars.get_provider(cfg["primary"]).months(year)}
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))

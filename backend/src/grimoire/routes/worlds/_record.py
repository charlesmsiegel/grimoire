"""worlds: record responsibilities."""

from __future__ import annotations

from fastapi import HTTPException, Request

from ... import store
from ...store import compiled, overview
from ...store import paths as library_paths
from .. import runs
from ..models import NameBody, WorldCreate, WorldUpdate
from ..overview_log import overview_read
from ._router import router as router


# ---- worlds ----
@router.get("/worlds")
def get_worlds():
    # The cover token is derived HERE rather than in `store.worlds.list_worlds`,
    # exactly as the campaigns list derives its own: nothing about a cover is
    # written into `world.md`, and a store reader that resolved one would put a
    # `covers` import and a per-world `stat` in front of every caller --
    # `routes/todo/`'s chore sweep and `routes/shell.py`'s badge included --
    # which none of them asked for.
    #
    # Composed by `store.overview.world_rows` (spec 04 section 4.1): each
    # world's card row (remembered on `world.md`'s stamp), its nine counts
    # (live listings) and the cover token.
    with overview_read("worlds", "library"):
        return overview.world_rows()


@router.post("/worlds")
def post_world(body: WorldCreate):
    wid = store.worlds.create_world(body.name, genre=body.genre, tone=body.tone,
                                    themes=body.themes, description=body.description)
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
    except store.worlds.WorldNotFound as exc:
        raise HTTPException(status_code=404, detail="world not found") from exc
    out["meta"]["cover"] = store.covers.world_cover_version(wid)
    # How many campaigns are played in this world -- the one fact about a
    # world that is not a record inside it, and which the world page used to
    # learn by fetching every campaign's shelf row (a scene listing each) and
    # filtering. The same rows' `world`, off `list_campaigns`, whose heads are
    # stat-memoized: a stat or two per campaign, and no transcript is read.
    #
    # It is also the one field here that reads outside this world, and
    # `list_campaigns` raises on a campaign.md it cannot read. That must cost
    # the count, not the world: None is "unknown" to the page, the same dash it
    # drew when its own campaign-shelf read failed.
    try:
        out["campaigns"] = sum(1 for c in store.campaigns.list_campaigns()
                               if c["world"] == wid)
    except Exception:  # noqa: BLE001 — an unreadable campaign costs the count, no 500
        out["campaigns"] = None
    return out


@router.put("/worlds/{wid}")
def put_world(wid: str, body: WorldUpdate):
    """Rename the world and/or edit its profile (#38). A field left out is left
    alone; a name that IS sent must say something."""
    name = None if body.name is None else body.name.strip()
    if name is not None and not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        meta = store.worlds.update_world(wid, name=name, genre=body.genre, tone=body.tone,
                                         themes=body.themes, description=body.description)
    except store.worlds.WorldNotFound as err:
        raise HTTPException(status_code=404, detail="world not found") from err
    except store.worlds.WorldChangedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": wid, "name": meta.get("name", wid)}


@router.delete("/worlds/{wid}")
def delete_world(wid: str, request: Request):
    # Held against image-store maintenance for the whole delete: a migration
    # placing this world's legacy files, or a collection walking its
    # placements, must not have the tree removed beneath it.
    root, intent, prepared = None, None, False
    try:
        with runs.maintenance_excluded(request.app):
            with library_paths.library_guard():
                root = library_paths.home()
                with library_paths.pin_library(root):
                    try:
                        # Refuse a missing or in-use world BEFORE fencing the
                        # campaigns' inherited membership reviews against it.
                        store.worlds.lifecycle._preflight_delete(wid)
                        intent = compiled.prepare_delete(root)
                        prepared = True
                        with store.overlay.world_identity_scope(store.worlds.world_root(wid)) as lifetime:
                            store.overlay.retire_world_scope_reviews(store.worlds.world_root(wid), lifetime.captured)
                            store.worlds.delete_world(wid)
                    except store.worlds.WorldNotFound as err:
                        raise HTTPException(status_code=404, detail="world not found") from err
                    except store.worlds.WorldInUse as exc:
                        raise HTTPException(status_code=409, detail=str(exc)) from exc
            # AFTER the delete, and for the campaign route's reason: world ids
            # are slugs too, so a replacement of the same name would otherwise
            # be handed this world's taglines and scenario proposals. The
            # registry is entered after releasing the leaf root mutex.
            runs.forget_subject(request.app, runs.world_subject(wid))
    finally:
        # Optional SQL erasure never holds a run, root or campaign exclusion.
        if prepared and root is not None:
            compiled.purge_for_delete(root, intent=intent)
    return {"ok": True}


# Declared here rather than in `entities`, which registers the
# `/worlds/{wid}/{kind}` catch-all -- `routes/__init__` includes that module
# last precisely so a literal second segment like `fork` is still reachable,
# and `tests/test_route_order.py` fails if that ever stops being true.
@router.post("/worlds/{wid}/fork")
def post_world_fork(wid: str, body: NameBody, request: Request):
    """Copy `wid` into a brand-new world called `body.name`.

    A deep copy of the whole directory, not a reference: nothing the fork holds
    is shared with the world it came from, and nothing at all happens to that
    world (`store/worlds/lifecycle.py`). Returns the new world's id, the same
    shape `POST /worlds` and `POST /worlds/import` return -- the client
    navigates or refreshes with it.

    A `def`, so FastAPI runs it in a threadpool: copying a world with a full
    character gallery is a gigabyte of I/O, and doing that on the event loop
    would stall every other request for its duration.

    Held against image-store maintenance for the whole copy: a migration that
    unlinked a legacy file the copy had not reached yet would leave the fork
    without the picture, and a collection could take an object the copied
    placements are about to need.
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        with runs.maintenance_excluded(request.app):
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

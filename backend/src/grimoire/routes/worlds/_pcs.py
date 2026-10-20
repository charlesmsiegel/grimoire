"""worlds: pcs responsibilities."""

from __future__ import annotations

from fastapi import HTTPException

from ... import store
from ..common import PC_HISTORY_MISSES, _world_root_or_404, pc_history_404
from ..models import PCCreate, PCUpdate, PersonaVersionCreate, PersonaVersionUpdate
from . import _tags
from ._router import router as router


@router.get("/worlds/{wid}/pcs")
def get_world_pcs(wid: str):
    return store.pcs.list_pcs(_world_root_or_404(wid))


@router.get("/worlds/{wid}/campaign-pc-name")
def get_campaign_pc_name(wid: str, name: str):
    """Whether a campaign begun in this world could seat a new PC called
    `name`: `{"refusal": null}` when it could, and otherwise the sentence
    `POST /campaigns/{cid}/pcs` would refuse it with.

    The campaign wizard asks before it creates anything, because asking the PC
    route meant asking after the campaign existed (`actor_names.
    refusal_in_new_campaign`). A read, and unlocked: the answer can be stale by
    the time the wizard commits, so the PC route's own check, under the
    campaign lock, is still the one that binds."""
    _world_root_or_404(wid)
    return {"refusal": store.actor_names.refusal_in_new_campaign(name, wid)}


@router.post("/worlds/{wid}/pcs")
def post_world_pc(wid: str, body: PCCreate):
    root = _world_root_or_404(wid)
    _tags._validate_tags(root, body.tags)
    with store.locks.world_actor_lock(wid):
        try:
            store.actor_names.require_unique(body.name, scope="world", scope_id=wid)
            if body.persona:
                store.actor_names.require_unique(store.actor_names.persona_name(body.persona),
                                                 scope="world", scope_id=wid)
        except store.actor_names.ActorNameError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        pid, vid = store.pcs.create_pc(root, body.name, body.tags, body.version_name, body.persona)
    return {"pc": pid, "version": vid}


@router.get("/worlds/{wid}/pcs/{pid}")
def get_world_pc(wid: str, pid: str):
    try:
        return store.pcs.read_pc(_world_root_or_404(wid), pid)
    except store.pcs.PCNotFound as exc:
        raise HTTPException(status_code=404, detail="pc not found") from exc


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
        with store.locks.world_actor_lock(wid):
            if body.default_version is not None:
                selected = store.pcs.read_persona(root, pid, body.default_version)
                store.actor_names.require_unique(store.actor_names.persona_name(selected),
                                                 scope="world", scope_id=wid,
                                                 actor_ref=f"pcs:{pid}")
                store.pcs.set_default_version(root, pid, body.default_version)
            if body.tags is not None:
                _tags._validate_tags(root, body.tags)
                store.pcs.set_tags(root, pid, body.tags)
    except store.pcs.PCNotFound as err:
        raise HTTPException(status_code=404, detail="pc not found") from err
    except store.pcs.PCVersionNotFound as err:
        raise HTTPException(status_code=404, detail="version not found") from err
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    return {"ok": True}


@router.delete("/worlds/{wid}/pcs/{pid}")
def delete_world_pc(wid: str, pid: str):
    root = _world_root_or_404(wid)
    with store.overlay.world_identity_change(root, record=("pcs", pid)) as cids:
        try:
            store.pcs.delete_pc(root, pid)
        except store.pcs.PCNotFound as exc:
            raise HTTPException(status_code=404, detail="pc not found") from exc
        store.overlay.forget_world_record(root, "pcs", pid, cids=cids)   # #225
        return {"ok": True}


@router.post("/worlds/{wid}/pcs/{pid}/versions")
def post_pc_version(wid: str, pid: str, body: PersonaVersionCreate):
    try:
        store.actor_names.require_unique(store.actor_names.persona_name(body.persona),
                                         scope="world", scope_id=wid,
                                         actor_ref=f"pcs:{pid}")
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    try:
        vid = store.pcs.create_version(_world_root_or_404(wid), pid, body.name, body.persona)
    except store.pcs.PCNotFound as err:
        raise HTTPException(status_code=404, detail="pc not found") from err
    return {"version": vid}


@router.put("/worlds/{wid}/pcs/{pid}/versions/{vid}")
def put_pc_version(wid: str, pid: str, vid: str, body: PersonaVersionUpdate):
    root = _world_root_or_404(wid)
    try:
        # Under the world's actor lock since #67: a save now reads the text it
        # replaces into history, and two unserialized saves would each keep the
        # same old text and lose the first one's from the trail altogether.
        with store.locks.world_actor_lock(wid):
            store.actor_names.require_unique(store.actor_names.persona_name(body.persona),
                                             scope="world", scope_id=wid,
                                             actor_ref=f"pcs:{pid}")
            store.pcs.update_version(root, pid, vid, body.persona)
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except store.pcs.PCNotFound as err:
        raise HTTPException(status_code=404, detail="pc not found") from err
    except store.pcs.PCVersionNotFound as err:
        raise HTTPException(status_code=404, detail="version not found") from err
    return {"ok": True}


# ---- world PC revision history (#67) — the earlier texts of one version, as
# `store/pcs.py` keeps them on every editor save. Literal segments past
# `/versions/{vid}/`, so nothing in `entities`' catch-alls can claim them.
@router.get("/worlds/{wid}/pcs/{pid}/versions/{vid}/revisions")
def get_pc_revisions(wid: str, pid: str, vid: str):
    try:
        return store.pcs.list_revisions(_world_root_or_404(wid), pid, vid)
    except PC_HISTORY_MISSES as exc:
        raise pc_history_404(exc) from None


@router.get("/worlds/{wid}/pcs/{pid}/versions/{vid}/revisions/{rid}")
def get_pc_revision(wid: str, pid: str, vid: str, rid: str):
    try:
        return store.pcs.read_revision(_world_root_or_404(wid), pid, vid, rid)
    except PC_HISTORY_MISSES as exc:
        raise pc_history_404(exc) from None


@router.post("/worlds/{wid}/pcs/{pid}/versions/{vid}/revisions/{rid}/restore")
def post_pc_revision_restore(wid: str, pid: str, vid: str, rid: str):
    """Make an earlier text current again. Held to the same name rule as an
    ordinary save (`put_pc_version`): the restored name may since have been
    taken by another actor in this world."""
    root = _world_root_or_404(wid)
    try:
        with store.locks.world_actor_lock(wid):    # see put_pc_version
            persona = store.pcs.read_revision(root, pid, vid, rid)
            store.actor_names.require_unique(store.actor_names.persona_name(persona),
                                             scope="world", scope_id=wid,
                                             actor_ref=f"pcs:{pid}")
            store.pcs.restore_revision(root, pid, vid, rid)
    except PC_HISTORY_MISSES as exc:
        raise pc_history_404(exc) from None
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    return {"ok": True}


@router.delete("/worlds/{wid}/pcs/{pid}/versions/{vid}")
def delete_pc_version(wid: str, pid: str, vid: str):
    try:
        store.pcs.delete_version(_world_root_or_404(wid), pid, vid)
    except store.pcs.PCNotFound as err:
        raise HTTPException(status_code=404, detail="pc not found") from err
    except store.pcs.PCVersionNotFound as err:
        raise HTTPException(status_code=404, detail="version not found") from err
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}

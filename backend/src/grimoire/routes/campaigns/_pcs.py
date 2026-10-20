"""Campaign HTTP endpoints: pcs domain."""


from __future__ import annotations

from fastapi import (
    File,
    HTTPException,
    Request,
    UploadFile,
)

from ... import store
from ..common import (
    PC_HISTORY_MISSES,
    _campaign_root_or_404,
    _content_fields,
    _serve_image,
    _sheet_failure_status,
    _upload_image_ext,
    image_id_field,
    image_promote_guards,
    pc_history_404,
    record_art_mutation,
)
from ..models import (
    AvatarFocus,
    ImageDescription,
    ImagePromote,
    PCUpdate,
    PersonaVersionCreate,
    PersonaVersionUpdate,
    PickBody,
)
from ._actors import _campaign_wroot
from ._router import router


@router.get("/campaigns/{cid}/pcs/{pid}")
def get_campaign_pc(cid: str, pid: str):
    _campaign_root_or_404(cid)
    try:
        # overlay.read_pc, not pcs.read_pc under pc_root: the persona files
        # resolve whole-directory, but images overlay per file, so a
        # materialized PC can still be wearing an avatar only the world has (#219)
        return store.overlay.read_pc(cid, pid)
    except store.pcs.PCNotFound as exc:
        raise HTTPException(status_code=404, detail="pc not found") from exc


@router.put("/campaigns/{cid}/pcs/{pid}")
def put_campaign_pc(cid: str, pid: str, body: PCUpdate):
    # Campaign tags are free strings: no world-vocabulary check on this side.
    _campaign_root_or_404(cid)
    try:
        with store.locks.campaign_lock(cid):
            if body.default_version is not None:
                selected = store.pcs.read_persona(store.overlay.pc_root(cid, pid),
                                                  pid, body.default_version)
                store.actor_names.require_unique(store.actor_names.persona_name(selected),
                                                 scope="campaign", scope_id=cid,
                                                 actor_ref=f"pcs:{pid}")
            root = store.overlay.ensure_actor_writable(cid, "pcs", pid)
            # The VERSION first, and that order is the fix rather than a
            # preference: `set_default_version` validates before it writes
            # (`require_version`), so an unknown or concurrently deleted version
            # now refuses before anything has been saved. The other way round, the
            # tags were already committed when the 404 went out -- a durable change
            # answered non-2xx, which is the one shape a deliberate refusal shares
            # with a crash and the middleware's failure stamp cannot see (Codex
            # review). Not stamped but PREVENTED: with nothing written there is
            # nothing to record.
            #
            # The end state is unchanged when both are supplied: `set_tags` re-reads
            # the metadata, so it carries the new default forward.
            if body.default_version is not None:
                store.pcs.set_default_version(root, pid, body.default_version)
            if body.tags is not None:
                store.pcs.set_tags(root, pid, body.tags)
    except store.pcs.PCNotFound as err:
        raise HTTPException(status_code=404, detail="pc not found") from err
    except store.pcs.PCVersionNotFound as err:
        raise HTTPException(status_code=404, detail="version not found") from err
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/campaigns/{cid}/pcs/{pid}/versions")
def post_campaign_pc_version(cid: str, pid: str, body: PersonaVersionCreate):
    _campaign_root_or_404(cid)
    try:
        with store.locks.campaign_lock(cid):
            store.actor_names.require_unique(store.actor_names.persona_name(body.persona),
                                             scope="campaign", scope_id=cid,
                                             actor_ref=f"pcs:{pid}")
            if store.appearances.locked_version(cid, "pcs", pid) is not None:
                raise HTTPException(status_code=409, detail="pc is locked to one version")
            root = store.overlay.ensure_actor_writable(cid, "pcs", pid)
            vid = store.pcs.create_version(root, pid, body.name, body.persona)
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except store.pcs.PCNotFound as err:
        raise HTTPException(status_code=404, detail="pc not found") from err
    return {"version": vid}


@router.put("/campaigns/{cid}/pcs/{pid}/versions/{vid}")
def put_campaign_pc_version(cid: str, pid: str, vid: str, body: PersonaVersionUpdate):
    _campaign_root_or_404(cid)
    try:
        with store.locks.campaign_lock(cid):
            store.actor_names.require_unique(store.actor_names.persona_name(body.persona),
                                             scope="campaign", scope_id=cid,
                                             actor_ref=f"pcs:{pid}")
            root = store.overlay.ensure_actor_writable(cid, "pcs", pid)
            store.pcs.update_version(root, pid, vid, body.persona)
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except store.pcs.PCNotFound as err:
        raise HTTPException(status_code=404, detail="pc not found") from err
    except store.pcs.PCVersionNotFound as err:
        raise HTTPException(status_code=404, detail="version not found") from err
    return {"ok": True}


# ---- campaign PC revision history (#67). Read off the CAMPAIGN root, not
# `overlay.pc_root`: history belongs to the copy that was edited, so a PC this
# campaign still inherits lists none here rather than showing the world's (the
# world page is where that lives). Restore takes the path every campaign PC
# write takes -- lock, materialize, name check -- and so never writes the world.
@router.get("/campaigns/{cid}/pcs/{pid}/versions/{vid}/revisions")
def get_campaign_pc_revisions(cid: str, pid: str, vid: str):
    _campaign_root_or_404(cid)
    try:
        return store.overlay.pc_revisions(cid, pid, vid)
    except PC_HISTORY_MISSES as exc:
        raise pc_history_404(exc) from None


@router.get("/campaigns/{cid}/pcs/{pid}/versions/{vid}/revisions/{rid}")
def get_campaign_pc_revision(cid: str, pid: str, vid: str, rid: str):
    _campaign_root_or_404(cid)
    try:
        return store.overlay.pc_revision(cid, pid, vid, rid)
    except PC_HISTORY_MISSES as exc:
        raise pc_history_404(exc) from None


@router.post("/campaigns/{cid}/pcs/{pid}/versions/{vid}/revisions/{rid}/restore")
def post_campaign_pc_revision_restore(cid: str, pid: str, vid: str, rid: str):
    _campaign_root_or_404(cid)
    try:
        with store.locks.campaign_lock(cid):
            # Found BEFORE anything is made writable: only a materialized copy
            # has history, so a revision that exists means `ensure_actor_writable`
            # below is a no-op -- and one that does not 404s having written
            # nothing, rather than having materialized the PC on the way out.
            persona = store.overlay.pc_revision(cid, pid, vid, rid)
            store.actor_names.require_unique(store.actor_names.persona_name(persona),
                                             scope="campaign", scope_id=cid,
                                             actor_ref=f"pcs:{pid}")
            root = store.overlay.ensure_actor_writable(cid, "pcs", pid)
            store.pcs.restore_revision(root, pid, vid, rid)
    except PC_HISTORY_MISSES as exc:
        raise pc_history_404(exc) from None
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    return {"ok": True}


@router.delete("/campaigns/{cid}/pcs/{pid}/versions/{vid}")
def delete_campaign_pc_version(cid: str, pid: str, vid: str):
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "pcs", pid)
        store.pcs.delete_version(root, pid, vid)
    except store.pcs.PCNotFound as err:
        raise HTTPException(status_code=404, detail="pc not found") from err
    except store.pcs.PCVersionNotFound as err:
        raise HTTPException(status_code=404, detail="version not found") from err
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


# ---- campaign PC images (#219) — the campaign-side half of the world routes
# in `worlds.py`, resolved through the overlay exactly as the character image
# routes above are: reads take the union, deletes tombstone, promotion copies
# up first.
def _campaign_pc_version_or_404(cid: str, pid: str, vid: str):
    """The campaign root, once `pid`/`vid` are known to name a real PC version.

    Resolved through `overlay.pc_root`, not the campaign root: on a thin
    campaign the PC's persona files are still the world's, so a croot-only
    check would 404 every inherited PC. The root this *returns* is always the
    campaign's, because that is where a write has to land.

    Why gate at all: see `worlds._world_pc_version_or_404`. `put_image` creates
    the directory it writes into, so an unchecked id turns a typo into a
    permanent, unlisted folder of orphaned bytes.
    """
    root = _campaign_root_or_404(cid)
    try:
        store.pcs.require_version(store.overlay.pc_root(cid, pid), pid, vid)
    except store.pcs.PCNotFound as exc:
        raise HTTPException(status_code=404, detail="pc not found") from exc
    except store.pcs.PCVersionNotFound as exc:
        raise HTTPException(status_code=404, detail="version not found") from exc
    return root


@router.put("/campaigns/{cid}/pcs/{pid}/versions/{vid}/images/{name}/description")
def put_campaign_pc_image_description(cid: str, pid: str, vid: str, name: str,
                                      body: ImageDescription):
    _campaign_pc_version_or_404(cid, pid, vid)
    return record_art_mutation("campaign", cid, "pcs", pid, vid, name,
                               "description", description=body.description, expected_v=body.expected_v,
                               expected_description_rev=body.expected_description_rev,
                               cleanup_only=body.cleanup_only)


@router.get("/campaigns/{cid}/pcs/{pid}/versions/{vid}/images")
def list_campaign_pc_images(cid: str, pid: str, vid: str):
    _campaign_pc_version_or_404(cid, pid, vid)
    return store.overlay.list_images(cid, pid, vid, base=store.pcs.ASSET_BASE)


@router.get("/campaigns/{cid}/pcs/{pid}/versions/{vid}/images/{name}")
def get_campaign_pc_image(cid: str, pid: str, vid: str, name: str, request: Request):
    _campaign_pc_version_or_404(cid, pid, vid)
    return _serve_image(
        store.overlay.image_root(cid, pid, vid, name, base=store.pcs.ASSET_BASE),
        pid, vid, name, base=store.pcs.ASSET_BASE, request=request)


@router.put("/campaigns/{cid}/pcs/{pid}/versions/{vid}/images/{name}")
async def put_campaign_pc_image(cid: str, pid: str, vid: str, name: str,
                                file: UploadFile = File(...)):
    root = _campaign_pc_version_or_404(cid, pid, vid)
    data = await file.read()
    ext = _upload_image_ext(data)  # the bytes name the type, not `file.filename` (#321)
    try:
        stored = store.assets.put_image(root, pid, vid, name, data, ext,
                                        base=store.pcs.ASSET_BASE)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"name": name, "ext": stored,
            **image_id_field(store.assets.image_id(root, pid, vid, name,
                                                   base=store.pcs.ASSET_BASE))}


@router.delete("/campaigns/{cid}/pcs/{pid}/versions/{vid}/images/{name}")
def delete_campaign_pc_image(cid: str, pid: str, vid: str, name: str, expected_v: str | None = None):
    # tombstone so a still-materialized world image doesn't show back through
    # the overlaid read the moment the campaign's own copy is gone
    _campaign_pc_version_or_404(cid, pid, vid)
    return record_art_mutation("campaign", cid, "pcs", pid, vid, name,
                               "delete", expected_v=expected_v)


@router.post("/campaigns/{cid}/pcs/{pid}/versions/{vid}/images/{name}/promote")
def promote_campaign_pc_image(cid: str, pid: str, vid: str, name: str, body: ImagePromote | None = None):
    _campaign_pc_version_or_404(cid, pid, vid)
    return record_art_mutation("campaign", cid, "pcs", pid, vid, name,
                               "promote", **image_promote_guards(body))


@router.put("/campaigns/{cid}/pcs/{pid}/versions/{vid}/images/avatar/focus")
def put_campaign_pc_avatar_focus(cid: str, pid: str, vid: str, body: AvatarFocus):
    # a thin campaign may only have this avatar through the inherited world PC,
    # so the existence gate checks the overlay union, not croot alone
    # the write always lands campaign-side; overlay.read_focus then finds this
    # campaign crop record (an avatar placement -- image-less over an inherited
    # avatar -- or a focus.json beside a legacy campaign avatar) and treats the
    # campaign as authoritative going forward
    _campaign_pc_version_or_404(cid, pid, vid)
    return record_art_mutation("campaign", cid, "pcs", pid, vid, "avatar",
                               "focus", focus=body.focus, expected_v=body.expected_v)


@router.post("/campaigns/{cid}/{kind}/{aid}/pick-version")
def post_pick_version(cid: str, kind: str, aid: str, body: PickBody):
    _campaign_root_or_404(cid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    with store.locks.campaign_lock(cid):
        if store.appearances.locked_version(cid, kind, aid) is not None:
            # checked before existence: the sibling versions were purged by the pick
            raise HTTPException(status_code=409, detail=f"{kind}/{aid} is already locked")
        root = store.overlay.actor_root(cid, kind, aid)
        if store.appearances.actor_hash(root, kind, aid, body.version) is None:
            raise HTTPException(status_code=404, detail="actor or version not found in campaign")
        selected = (store.actor_names.card_name(store.characters.read_card(root, aid, body.version))
                    if kind == "characters" else
                    store.actor_names.persona_name(store.pcs.read_persona(root, aid, body.version)))
        try:
            store.actor_names.require_unique(selected, scope="campaign", scope_id=cid,
                                             actor_ref=f"{kind}:{aid}")
            store.appearances.pick_version(cid, kind, aid, body.version)
        except (store.actor_names.ActorNameError, store.appearances.AppearError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/campaigns/{cid}/{kind}/{aid}/import-version")
def post_import_version(cid: str, kind: str, aid: str, body: PickBody):
    _campaign_root_or_404(cid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    if store.appearances.actor_hash(_campaign_wroot(cid), kind, aid, body.version) is None:
        raise HTTPException(status_code=404, detail="actor or version not found in world")
    wroot = _campaign_wroot(cid)
    incoming = (store.actor_names.card_name(store.characters.read_card(wroot, aid, body.version))
                if kind == "characters" else
                store.actor_names.persona_name(store.pcs.read_persona(wroot, aid, body.version)))
    try:
        with store.locks.campaign_lock(cid):
            store.actor_names.require_unique(incoming, scope="campaign", scope_id=cid,
                                             actor_ref=f"{kind}:{aid}")
            store.appearances.import_version(cid, kind, aid, body.version)
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except store.appearances.AppearError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/campaigns/{cid}/{kind}/instantiate/{mid}/{content_id}")
def post_campaign_instantiate(cid: str, kind: str, mid: str, content_id: str):
    _campaign_root_or_404(cid)
    try:
        content = store.modules.read_content(mid, kind, content_id)
    except store.modules.ModuleNotFound as exc:
        raise HTTPException(status_code=404, detail="module not found") from exc
    except store.modules.ContentNotFound as exc:
        raise HTTPException(status_code=404, detail="content not found") from exc
    try:
        eid = store.overlay.create_entity(cid, kind, content["name"], content["body"],
                                          content.get("keys", ""), "",
                                          fields=_content_fields(kind, content))
    except store.entities.UnknownKind as exc:
        raise HTTPException(status_code=404, detail="unknown kind") from exc
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
            raise HTTPException(status_code=_sheet_failure_status(e), detail=str(e)) from e
    return {"id": eid}

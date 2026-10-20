"""Scene creation and shape changes hold the existing run and identity fences."""

from __future__ import annotations

import logging

from fastapi import File, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from ... import store
from .. import runs
from ..common import _campaign_root_or_404, _dump, _require_scene, computes_only
from ..models import BranchScene, NewScene, RenameScene, SceneImportCommit
from . import _stage, _targets
from ._router import router as router

log = logging.getLogger('grimoire.routes.scenes')


@router.post("/campaigns/{cid}/scenes")
def post_scene(cid: str, body: NewScene, request: Request):
    title = body.title or "New scene"
    # Only when this create would WIDEN the campaign. Crossing 999 -> 1000
    # scenes repads every scene in it -- renaming them all and repointing their
    # sidecars -- so every live turn loses the path it captured, and the
    # per-scene guard cannot express that. An ordinary create touches nothing
    # but the new file, so it stays allowed while a turn generates elsewhere:
    # refusing every create during any turn would be a much larger change to
    # how the app feels, for a case that is not dangerous.
    #
    # ONE HOLD over the boundary read, the guard and the create. The three were
    # separate steps, and both gaps are real: a turn can reserve after
    # `require_campaign_free` returns, and two concurrent creates can each
    # preflight below the boundary so that the second one repads anyway. Either
    # way `_create_scene` renames the live turn's transcript and its terminal
    # fence discards the reply. The campaign lock is reentrant, so
    # `_create_scene`'s own `@_serialized` acquisition costs nothing, and
    # `reserve_turn` takes the same lock across its identity capture and its
    # reservation -- which is what makes holding it here exclude a turn rather
    # than merely narrow the window before one.
    # The model chat would run on here, stamped on the scene. Resolved before
    # the hold: it only reads (config, connection files and campaign.md), and
    # a choice that changes in between is a stamp, not a transcript write.
    _resolved, model = _targets._chat_target(cid)
    try:
        with store.locks.campaign_lock(cid):
            if store.scenes.create_would_repad(cid):
                runs.require_campaign_free(request.app, cid)
            return {"id": store.scenes.create_scene(cid, title, body.suggested_date,
                                                    pcless=body.pcless, model=model,
                                                    focus=body.focus)}
    except store.campaigns.CampaignNotFound as err:
        raise HTTPException(status_code=404, detail="campaign not found") from err
    except OSError as exc:
        # The boundary read failed, so nobody knows whether this create would
        # repad -- and `create_would_repad` now says so rather than answering
        # "no". Reported as store contention, which the client retries, because
        # that is what it almost always is; going ahead would skip the guard and
        # then repad anyway on `_create_scene`'s own read, stranding a live turn
        # on a path that no longer exists.
        raise HTTPException(status_code=409, detail={
            "kind": "busy",
            "detail": f"the campaign could not be read: {exc}"}) from exc


# ---- importing an existing transcript (#92) ----
# Declared before the `{sid}` routes below and before `entities`' generic
# `/campaigns/{cid}/{kind}` catch-alls (which `routes/__init__` includes last),
# so "import" is read as the literal it is rather than as a scene id.
#
# Two routes, one flow, the same split `lorebook` makes: the first reads an
# upload into a draft and writes NOTHING, the second writes the draft the
# reviewer approved. Only the second creates anything, which is what makes the
# review step a gate rather than a confirmation dialog over work already done.
@router.post("/campaigns/{cid}/scenes/import/parse")
@computes_only
async def post_scene_import_parse(cid: str, file: UploadFile = File(...)):
    """Read a grimoire transcript into a reviewable draft. Writes nothing.

    Which is what `@computes_only` says, and it is load-bearing since #409:
    the marker keeps this off both the recents rail and the campaign's write
    token, so reviewing an import in another tab cannot refuse a clock
    confirmation over a campaign nothing has written. `POST .../scenes/import`,
    one route down, is the one that commits.
    """
    _campaign_root_or_404(cid)
    # Before the read, like every other upload route here: `read()` materializes
    # the whole body as one `bytes`, and that allocation is what the bound
    # exists for (see `scene_import.MAX_BYTES`). Starlette spools the body to
    # disk above 1 MB and fills in `size`, so this costs nothing -- and `parse`
    # re-checks the bytes, since `size` is Optional in the ASGI contract.
    if file.size is not None and file.size > store.scene_import.MAX_BYTES:
        raise HTTPException(status_code=413, detail=store.scene_import.TOO_LARGE)
    data = await file.read()
    try:
        # In a worker, not on the loop. This route is `async` only because the
        # upload has to be awaited; the parse itself is a megabyte of regex plus
        # a card read per actor in the campaign, and the lifespan loop it would
        # otherwise run on is the one every detached turn streams through.
        return await run_in_threadpool(store.scene_import.parse, cid, data)
    except store.scene_import.TranscriptTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except store.scene_import.SceneImportError as exc:
        raise HTTPException(status_code=400, detail=f"could not parse: {exc}") from exc


@router.post("/campaigns/{cid}/scenes/import")
def post_scene_import(cid: str, body: SceneImportCommit, request: Request):
    """Write a reviewed import draft as a new scene.

    Everything the draft can get wrong is settled BEFORE the create: an actor
    this campaign does not have, a role that fights the campaign's lock, a
    player seated in an offscreen scene. Those are the failures an import
    actually has -- the review step surfaces the speakers it could not match,
    and this is where a reviewer who confirmed one anyway finds out -- and
    raising them after the create would leave a stray half-scene behind for
    every one of them.

    What cannot be settled early still cleans up after itself: a date the
    calendar refuses, a location that was deleted between the parse and the
    commit, a store that goes busy mid-append all discard the scene rather than
    leave a fragment of the transcript standing (see `scene_import.commit`).
    """
    _campaign_root_or_404(cid)
    messages = [_dump(m) for m in body.messages]
    if not messages:
        raise HTTPException(status_code=400,
                            detail="an imported scene needs at least one message")
    # The parse route bounds its upload; this one is plain JSON and had no
    # bound at all. The cap is on the SCENE, not on the request: a transcript
    # of this length is already past what the app can render or absorb, and the
    # store's own guidance is to break a log into scenes. Without it a single
    # request could hold the campaign lock for minutes.
    if len(messages) > store.scene_import.MAX_MESSAGES:
        raise HTTPException(status_code=413, detail=store.scene_import.TOO_MANY)
    cast = [{"kind": ref.kind, "id": ref.id,
             "role": _stage._resolve_role(ref.kind, ref.role, body.pcless),
             "version": _stage._actor_version(cid, ref.kind, ref.id, ref.version)}
            for ref in body.cast]
    _resolved, model = _targets._chat_target(cid)    # `post_scene`'s stamp
    try:
        # The same hold, for the same reason, as `post_scene`: crossing the
        # number-width boundary repads every scene in the campaign, renaming
        # them all under any live turn. See that route.
        with store.locks.campaign_lock(cid):
            try:
                widens = store.scenes.create_would_repad(cid)
            except OSError as exc:
                # ONLY the boundary read. Spanning the create too would report a
                # full disk or a read-only store as a retryable "could not be
                # read", and the client retries `busy` forever over a condition
                # that never clears, named as the wrong operation.
                raise HTTPException(status_code=409, detail={
                    "kind": "busy",
                    "detail": f"the campaign could not be read: {exc}"}) from exc
            if widens:
                runs.require_campaign_free(request.app, cid)
            sid = store.scenes.create_scene(cid, body.title or "Imported scene",
                                            pcless=body.pcless, model=model)
    except store.campaigns.CampaignNotFound:
        # `from None`: the store's own exception says nothing the caller can act
        # on beyond the 404 -- the same reading `_campaign_root_or_404` takes.
        raise HTTPException(status_code=404, detail="campaign not found") from None
    try:
        # All or nothing: `commit` removes the scene again on any failure. It
        # has to be the one that does -- `set_datetime` renames the scene, so
        # after that step `sid` here is not the id the scene has.
        return store.scene_import.commit(cid, sid, messages, date=body.date,
                                         location=body.location, cast=cast,
                                         turn_sizes=body.turn_sizes)
    except (store.calendars.CalendarError, store.entities.EntityNotFound,
            store.appearances.AppearError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except store.scenes.SceneNotFound as exc:
        # The scene this call created was renamed or removed under it -- a
        # concurrent width-crossing repad is the reachable way. `commit` has
        # already discarded what it wrote (it follows the identity, not the
        # id), so this is retryable and says so rather than 500-ing: nothing
        # here is wrong with the request.
        raise HTTPException(status_code=409, detail={
            "kind": "busy",
            "detail": "the scene moved while it was being imported — try again"}) from exc


@router.post("/campaigns/{cid}/scenes/{sid}/branch")
def post_branch(cid: str, sid: str, body: BranchScene, request: Request) -> dict:
    """Branch a scene from a post into a sibling scene (play controls III).

    The sibling keeps the transcript through `through` and the records that go
    with it (`store/branch.py`). An absorbed scene is refused
    `absorbed_use_fork` -- its past lives in campaign files, so it branches by
    forking the campaign (`POST /fork` with `from_index`) -- and a closed one
    `branch_closed`. Held like any shape change: a live turn would append to
    the transcript mid-copy. The activity middleware stamps the revision.
    """
    _require_scene(cid, sid)
    try:
        with runs.scene_held_open(request.app, cid, sid):
            new = store.branch.branch_scene(cid, sid, body.through, title=body.title.strip())
        return {"id": new, "scene": store.scenes.read_scene(cid, new)}
    except store.branch.BranchRefused as exc:
        raise HTTPException(status_code=409,
                            detail={"kind": exc.kind, "detail": exc.detail}) from exc
    except IndexError as exc:
        raise HTTPException(status_code=400, detail="message index out of range") from exc
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound) as exc:
        raise HTTPException(status_code=404, detail="scene not found") from exc


@router.put("/campaigns/{cid}/scenes/{sid}")
def put_scene(cid: str, sid: str, body: RenameScene, request: Request):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    # A rename mints a new `sid`, and a detached turn holds the old one -- see
    # `require_scene_free`. Ahead of the title check would refuse a malformed
    # request with the wrong reason; behind the rename would be too late.
    try:
        with runs.scene_held_free(request.app, cid, sid):
            new_sid = store.scenes.rename_scene(cid, sid, title)
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound) as exc:
        # a scene path is built from campaign_root, so an unusable campaign id
        # surfaces here as CampaignNotFound -- still a 404, not a 500
        raise HTTPException(status_code=404, detail="scene not found") from exc
    return {"id": new_sid, "title": title}


@router.delete("/campaigns/{cid}/scenes/{sid}")
def delete_scene(cid: str, sid: str, request: Request):
    # Same reason as the rename above, one step worse: the transcript a live
    # turn is about to write into would be gone. The identity fence keeps the
    # reply off a REPLACEMENT scene, which is the corruption; this keeps the
    # reply from being thrown away in the first place.
    try:
        with runs.scene_held_free(request.app, cid, sid):
            # Read while the transcript that carries it is certainly there.
            identity = store.scenes.scene_identity(cid, sid)
            store.scenes.delete_scene(cid, sid)
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound) as exc:
        # a scene path is built from campaign_root, so an unusable campaign id
        # surfaces here as CampaignNotFound -- still a 404, not a 500
        raise HTTPException(status_code=404, detail="scene not found") from exc
    # The store phase's record, keyed by that identity. Here rather than in
    # `lifecycle.delete_scene` beside the tracker's, because `store.regex`
    # imports `store.scenes` and the reverse edge would close a cycle. After the
    # delete and fail-soft, for the tracker's reason: identities are never
    # reused, so an orphan can be adopted by no scene and is only disk.
    if identity:
        try:
            store.regex.rewrites.drop(cid, identity)
        except Exception:
            log.warning("delete_scene: could not drop the stored rewrites of %s/%s (%s)",
                        cid, sid, identity, exc_info=True)
    return {"ok": True}

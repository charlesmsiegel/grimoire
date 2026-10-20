"""Hold existing campaign and registry exclusions across scene and root changes."""

from __future__ import annotations

import contextlib

from fastapi import HTTPException

from ... import store
from ...store import scenes
from . import _contracts
from ._router import router as router


def require_scene_free(app, cid: str, sid: str) -> None:
    """Refuse a change to the SHAPE of a scene while a run is holding it.

    A `turn` or a `review`, and one check covers both: `exclusion_key` is built
    from the subject and both classes are in `_EXCLUSIVE`, so they name the
    same key and a scene can hold at most one of either. The rule is one
    sentence -- while a turn or a review holds a scene, that scene's shape does
    not change -- and a review is the case that makes it urgent rather than
    theoretical: an absorb is minutes long, and an edit, a retcon or a cut
    landing under one moves the transcript it was built from.

    Detaching a turn is what makes this necessary. A rename mints a new `sid`
    (the slug is part of it), and the run and every one of its persistence hooks
    hold the old one -- so after a rename the identity fence looks for a scene
    that is no longer at that path, decides it is gone, and discards a reply the
    provider may have spent minutes producing. That was survivable when a turn
    died with its socket, because the window was the length of one request;
    it is minutes now, and the rename button is one click away in another tab.

    Deliberately a refusal rather than a repoint. Following the scene means
    updating a live run's captured `sid` from outside it, which is the kind of
    change the fence exists to make impossible; refusing costs the user a few
    seconds and the composer already tells them a turn is in flight.
    """
    identity = scenes.scene_identity_strict(cid, sid)
    if identity is None:
        return                       # no identity, so no run can name this scene
    live = app.state.runs.live_for_key(_contracts.exclusion_key(("scene", cid, identity), "turn"))
    if live is not None:
        # `scene_busy`, not `run_in_flight`. They are different refusals and the
        # client acts on them differently: `run_in_flight` answers "you cannot
        # start another turn here", which resolves itself when this one lands;
        # this one answers "you cannot change the SHAPE of this scene while a
        # turn is reading it", which is about renaming, editing and cutting.
        raise HTTPException(status_code=409, detail={
            "kind": "scene_busy", "run_id": live.id,
            "detail": "a turn or review is running on this scene; stop it first"})


@contextlib.contextmanager
def scene_held_free(app, cid: str, sid: str):
    """Hold the campaign lock across BOTH the scene-free check and the mutation
    it guards.

    Every route that changes a scene's SHAPE goes through this -- rename,
    delete, message edit, message cut, retcon, replay begin/accept/cancel --
    because detaching a turn made all of them concurrent with generation. The
    doors that already open a campaign-lock hold of their own (alternate
    promotion, the manual roll, the manual check) call `require_scene_free`
    inside it instead: the same guarantee, without a second acquisition.

    A turn composes its prompt from the transcript as it stood and finalizes
    against the transcript as it is, so a post edited or cut underneath it
    produces a reply answering a question nobody asked any more, appended to
    history that has moved.

    Checking and then mutating is two steps, and a reservation fits between
    them: the check sees no run, a send reserves and detaches, and the rename
    lands anyway -- so the turn's fence later finds its scene gone and discards
    a finished reply. `reserve_turn` takes the same campaign lock across its
    identity capture and its reservation, which is what makes holding it here
    actually exclude one rather than merely narrow the gap before one.

    Lock order is campaign-then-registry on both sides (`live_for_key` and
    `start_or_existing` take the registry lock inside a campaign-lock hold, and
    nothing takes them the other way round), so this cannot invert.
    """
    with store.locks.campaign_lock(cid):
        require_scene_free(app, cid, sid)
        yield


def require_scene_open(cid: str, sid: str) -> None:
    """Refuse a change to a scene a sibling branch's absorb has closed.

    A scene is closed while another member of its branch group is absorbed
    (`scenes.read.closed_by`, derived rather than stored): the campaign's files
    now hold that member's past, so this one is read-only. Its own check rather
    than part of `require_scene_free` -- delete, rename and replay accept/cancel
    stay open on a closed scene, because deleting it is how a player discards a
    road not taken, and a replay running in it must still be stoppable.
    """
    by = scenes.read.closed_by(cid, sid)
    if by:
        raise HTTPException(status_code=409, detail={
            "kind": "branch_closed", "closed_by": by,
            "detail": "a sibling branch of this scene was absorbed; this branch is read-only"})


@contextlib.contextmanager
def scene_held_open(app, cid: str, sid: str):
    """`scene_held_free`, plus `require_scene_open` inside the same hold: a
    transcript-shaping door on a scene that may be a closed branch."""
    with store.locks.campaign_lock(cid):
        require_scene_free(app, cid, sid)
        require_scene_open(cid, sid)
        yield


def require_group_free(app, cid: str, sid: str) -> None:
    """`require_scene_free` for every OTHER member of this scene's branch group.

    Absorbing one member closes the rest, so it waits for all of them: a turn
    landing in a sibling just closed would be a write to a read-only scene.
    Call inside the campaign-lock hold that covers the absorb."""
    rows = scenes.read.list_scenes(cid)
    group = next((r.get("branch_group") for r in rows if r["id"] == sid), None)
    if not group:
        return
    for row in rows:
        if row["id"] != sid and row.get("branch_group") == group:
            require_scene_free(app, cid, row["id"])


@contextlib.contextmanager
def store_held_still(app):
    """Refuse an operation that moves the STORE ROOT while anything is running,
    and keep it refused for the whole of that operation.

    Detaching a turn is what makes this reachable: the run survives navigation,
    so the player can leave the scene, open Configuration and change the storage
    location while their turn is still generating. Every earlier version of this
    hazard was impossible -- a turn died with its request, and its request held
    the page.

    Campaign-agnostic, unlike `require_campaign_free`: the root is global, so a
    run in ANY campaign is a run that would be persisted into the wrong tree.

    A CONTEXT MANAGER and not a check, which is the correction review asked
    for: see `RunRegistry.hold_still` for the window a bare check leaves open.
    """
    try:
        with app.state.runs.hold_still():
            yield
    except _contracts.RunInFlightError as exc:
        raise HTTPException(status_code=409, detail={
            "kind": "runs_in_flight", "run_id": exc.run_id,
            "detail": "a run is still in progress; wait for it or stop it before "
                      "moving the storage location"}) from exc
    except _contracts.SyncRunningError as exc:
        raise HTTPException(status_code=409, detail={
            "kind": "busy",
            "detail": "a cache warm-up is running; try again in a moment"}) from exc


def require_campaign_free(app, cid: str) -> None:
    """Refuse an operation that reshapes EVERY scene in a campaign while any of
    them is generating -- see `RunRegistry.live_running_in`."""
    live = app.state.runs.live_running_in(cid)
    if live is not None:
        raise HTTPException(status_code=409, detail={
            "kind": "scene_busy", "run_id": live.id,
            "detail": "a turn is running in this campaign; stop it first"})

"""Explicit replay sessions rewind and regenerate through their own stored session state."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request

from ... import prompts, store
from ...llm import LLMClient
from .. import character_turns, runs, streaming
from .. import tracker as tracker_routes
from ..common import UsableInference, _record_prompt, _require_scene, get_llm, require_inference
from ..models import ReplayCancel, ReplayStart
from ..streaming import StreamOutcome, _chat_stream
from ._router import router as router


def _replay_session(cid: str, sid: str) -> dict:
    """This scene's live replay, or a 409 naming the scene that has one.

    A campaign holds at most one session (`store/replay.py`), so a request
    against the wrong scene is not a not-found — the session exists, it is just
    somewhere else, and saying which scene is what lets the client offer to go
    there.
    """
    session = store.replay.state(cid)
    if session is None:
        raise HTTPException(status_code=409,
                            detail={"detail": "no replay is running", "kind": "no_replay"})
    if session["scene"] != sid:
        raise HTTPException(
            status_code=409,
            detail={"detail": "a replay is running in another scene",
                    "kind": "replay_elsewhere", "scene": session["scene"]})
    return session


@router.get("/campaigns/{cid}/scenes/{sid}/replay/preview")
def get_replay_preview(cid: str, sid: str, index: int):
    """What replaying from `index` would cost, before anything is cut (#79/#80).

    Read-only and cheap — it parses the transcript and counts generations — so
    the client can ask on hover. `fork` is the nudge: over the configured
    threshold, offer to copy the campaign first (`POST /campaigns/{cid}/fork`)
    and replay in the copy. `blocked`, when non-empty, is why this span cannot
    be replayed at all, and the client shows it in place of the button.
    """
    _require_scene(cid, sid)
    try:
        return store.replay.preview(cid, sid, index)
    except IndexError as exc:
        raise HTTPException(status_code=400, detail="message index out of range") from exc
    except (store.SceneNotFound, store.CampaignNotFound) as exc:
        raise HTTPException(status_code=404, detail="scene not found") from exc


@router.get("/campaigns/{cid}/scenes/{sid}/replay")
def get_replay(cid: str, sid: str):
    """The live replay session for this scene, or null.

    Null rather than a 404 for "there is none": the client asks this on every
    scene open to decide whether to show the walk, and an absent session is the
    ordinary answer. A session belonging to a DIFFERENT scene is also null here
    — it is not this scene's state — and the campaign-wide banner is what
    surfaces it, from the 409 the acting routes give.
    """
    _require_scene(cid, sid)
    session = store.replay.state(cid)
    return session if session and session["scene"] == sid else None


@router.post("/campaigns/{cid}/scenes/{sid}/replay")
def post_replay(cid: str, sid: str, body: ReplayStart, request: Request):
    """Start a replay: cut the scene at `index` and hold the rest for review.

    The cut is the cascade's (#75), so everything the removed posts caused the
    absorb to write comes back out with them — the reply carries that report
    under `cascade`, which is the same one the gutter's delete returns.

    Deliberately NOT the same request as the retcon edit above. A retcon is
    worth doing on its own (re-absorb the scene and read the contradictions),
    replaying is the expensive escalation, and pricing it (`GET
    .../replay/preview`) is a step the client takes in between.
    """
    _require_scene(cid, sid)
    try:
        # The most destructive shape change there is -- it cuts the transcript
        # at `index` and reverses everything the removed posts caused. Under a
        # live turn that is history moving out from under a reply already being
        # written.
        with runs.scene_held_open(request.app, cid, sid):
            if body.branch:
                return _replay_in_branch(cid, sid, body.index)
            report = store.replay.begin(cid, sid, body.index)
            # The cut half of a replay: the held tail's records go with it, as
            # for any cut (a restore brings the posts back untracked, Retry).
            tracker_routes.after_cut(cid, sid)
            return report
    except IndexError as err:
        raise HTTPException(status_code=400, detail="message index out of range") from err
    except store.replay.ReplayError as exc:
        raise HTTPException(status_code=409,
                            detail={"detail": str(exc), "kind": "replay_refused"}) from exc
    except store.branch.BranchRefused as exc:
        raise HTTPException(status_code=409,
                            detail={"kind": exc.kind, "detail": exc.detail}) from exc
    except (store.SceneNotFound, store.CampaignNotFound) as err:
        raise HTTPException(status_code=404, detail="scene not found") from err


def _replay_in_branch(cid: str, sid: str, index: int) -> dict:
    """Branch the whole transcript and begin the replay inside the sibling, so
    the original is never touched (#151). Called inside the door's hold.

    Every refusal `begin` makes is evaluated FIRST, so a replay that would be
    refused leaves no sibling; if `begin` still refuses after the branch, the
    sibling is discarded (`branch.discard`, which also bumps the revision for
    the roll entries the branch appended). Answers the session with the
    sibling's id under `branched`, for the client to navigate to.
    """
    store.replay.check_begin(cid, sid, index)
    last = len(store.scenes.read_scene(cid, sid)["messages"]) - 1
    new = store.branch.branch_scene(cid, sid, last)
    try:
        report = store.replay.begin(cid, new, index)
    except BaseException:
        store.branch.discard(cid, new)
        raise
    tracker_routes.after_cut(cid, new)
    return {**report, "branched": new}


@router.post("/campaigns/{cid}/scenes/{sid}/replay/turn")
def post_replay_turn(cid: str, sid: str, request: Request,
                     client: LLMClient = Depends(get_llm),
                     x_grimoire_attempt: str | None = Header(default=None)):
    """Replay the next turn: re-post the originals that were the player's, then
    stream a fresh reply against the edited history.

    No new streaming machinery. The player's own posts go back verbatim — no
    model rewrites what the player said — and the reply is composed by the same
    `compose_turn` an ordinary turn uses, which reads the transcript as it now
    stands and so is already "replay" by construction. Rerolling the result is
    plain `POST .../regenerate`: the fresh reply is the trailing run.

    Staging is idempotent (`replay.stage`), so a turn whose stream died is
    retried by calling this again — it re-posts nothing and generates once more.
    """
    replay = runs.replay_attempt(request.app, cid, sid, x_grimoire_attempt)
    if replay is not None:
        return replay
    _require_scene(cid, sid)
    resolved = require_inference("replay", cid)
    _replay_session(cid, sid)
    run, fresh = runs.reserve_turn(request.app, cid, sid, "replay",
                                   x_grimoire_attempt)
    if not fresh:
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))
    with runs.reservation(request.app, run):
        return _replay_turn_run(cid, sid, request, client, resolved, run)


def _replay_turn_run(cid: str, sid: str, request: Request,
                     client: LLMClient, resolved: UsableInference, run):
    """The body of a replay turn, once the scene is reserved -- see `_chat_run`."""
    try:
        store.replay.stage(cid)
        session = store.replay.state(cid)
    except store.replay.ReplayError as exc:
        raise HTTPException(status_code=409,
                            detail={"detail": str(exc), "kind": "replay_refused"}) from exc
    if session is None or session["next"] != "generation":
        raise HTTPException(status_code=409,
                            detail={"detail": "this replay has no model turn left to run",
                                    "kind": "replay_done"})
    if character_turns.enabled():
        return character_turns.start(cid,sid,request,client,resolved,run=run,
            actor_ref=character_turns.replay_actor(cid,sid),automatic=False,
            note=prompts.render("scene/director_note.j2"))
    primary = resolved.chain.primary
    messages, breakdown = store.context.compose_turn(
        cid, sid, describe=store.prompt_log.capturing(), model=primary.model,
        images=store.post_images.images_for(primary))
    # No `undo_user_post` hook, unlike `post_chat`. The staged posts are not
    # this request's to take back: `stage` recorded them as staged, a retry
    # re-uses them, and cancelling the replay is what puts the scene back.
    # No `after_turn` hook either, and that is the one deliberate omission
    # among the turn producers (#397). A replay is a WALK: the client cuts,
    # regenerates and accepts turn by turn, so every step of it has a client
    # waiting on the step's own response by construction -- and folding a
    # summary between two steps would describe a transcript the next step is
    # about to rewrite, paying for prose that is stale before it lands.
    # `ReplayPanel` asks once when the walk ends, which is the boundary that
    # means anything here.
    outcome = StreamOutcome()
    stream = _chat_stream(cid, sid, messages, resolved, client, task="replay",
                          identity=run.scene_identity, outcome=outcome)
    _record_prompt(cid, sid, "replay", breakdown,
                   model=primary.model, kind=primary.kind, messages=messages,
                   conn=resolved.chain)
    runs.start_detached(request.app, run, lambda: stream.body_iterator,
                        outcome=outcome.result)
    return runs.tail_response(run, 0, lead=runs.lead_frame(run))


@router.post("/campaigns/{cid}/scenes/{sid}/replay/accept")
def post_replay_accept(cid: str, sid: str, request: Request):
    """Keep the replayed turn and step forward. Null when that was the last one."""
    _require_scene(cid, sid)
    _replay_session(cid, sid)
    try:
        # Stepping the walk forward moves the transcript: the accepted turn
        # stays and the next original comes back out of the held tail.
        with runs.scene_held_free(request.app, cid, sid):
            session = store.replay.accept(cid)
            # Accepting drops whatever the step superseded; its records go too.
            tracker_routes.after_cut(cid, sid)
            streaming._prune_rewrites(cid, sid)
    except store.replay.ReplayError as exc:
        raise HTTPException(status_code=409,
                            detail={"detail": str(exc), "kind": "replay_refused"}) from exc
    except (store.SceneNotFound, store.CampaignNotFound) as err:
        raise HTTPException(status_code=404, detail="scene not found") from err
    return store.replay.state(cid) if session else None


@router.post("/campaigns/{cid}/scenes/{sid}/replay/cancel")
def post_replay_cancel(cid: str, sid: str, request: Request,
                       body: ReplayCancel | None = None):
    """Stop the replay. By default the unreplayed originals go back.

    The scene the session names is trusted over the path's — a session whose
    scene was renamed mid-walk is followed by `scene_refs.repoint`, and the
    restore has to land in the scene that actually holds the transcript.
    """
    _require_scene(cid, sid)
    _replay_session(cid, sid)
    try:
        # Restoring the unreplayed originals appends them back, which is the
        # same shape change as the cut that removed them.
        with runs.scene_held_free(request.app, cid, sid):
            report = store.replay.cancel(cid, restore=body.restore if body else True)
            # The replayed posts a cancel takes back leave the transcript, and
            # their records go with them; restored originals come back
            # untracked (Retry). `_replay_session` has checked the session's
            # scene is this `sid`.
            tracker_routes.after_cut(cid, sid)
            # Stored rewrites go the same way, the restored originals keeping
            # theirs: they come back with the ids their records are under.
            streaming._prune_rewrites(cid, sid)
            return report
    except store.replay.ReplayError as exc:
        raise HTTPException(status_code=409,
                            detail={"detail": str(exc), "kind": "replay_refused"}) from exc
    except (store.SceneNotFound, store.CampaignNotFound) as err:
        raise HTTPException(status_code=404, detail="scene not found") from err

"""Pending reviews are generation-scoped records published behind cancellation and identity fences."""

from __future__ import annotations

import logging
import time

from fastapi import HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from ... import store
from .. import runs, streaming
from ..common import _require_scene, run_error
from . import _review_budget
from ._router import router as router

log = logging.getLogger('grimoire.routes.scenes')


class _ReviewCancelledError(Exception):
    """The reviewer dismissed this review while the run was still preparing it."""


class _SceneMovedError(Exception):
    """The scene this run captured is no longer the scene at that id."""


#: How many times a terminal persist waits out a contended campaign lock.
#: `campaign_lock` already blocks for `LOCK_TIMEOUT`, so this is a second and
#: third full wait rather than a busy loop -- worth it here and nowhere else,
#: because what is being written is ten minutes of generation and there is no
#: copy of it anywhere: a `StoreBusy` swallowed at this one line costs the
#: whole absorb.
_PERSIST_ATTEMPTS = 3
_PERSIST_BACKOFF = 0.5


def _review_abandoned(run):
    """"Is anyone still waiting for this?", asked of the RUN rather than the socket.

    `abandoned=request.is_disconnected` was right when a disconnect could only
    mean the reviewer walked away. It is exactly wrong once a disconnect
    routinely means the screen locked, which is the whole of #396: the work now
    outlives the request that asked for it, and the only thing that means
    "nobody wants this any more" is the reviewer's own Cancel.

    Both flags, because they are different intents that want the same answer: a
    Stop on the run (`POST .../runs/{id}/cancel`) and a Discard of the review
    (`DELETE .../pending-review`). An awaitable, because that is the shape
    `_watched` and the phase loops already expect -- a bare bool would make
    `Abandoned` unreachable without a word of warning.
    """
    async def gone() -> bool:
        return run.review_cancelled or run.cancel_requested
    return gone


# `run_error` under its old local name: the `draft` routes need exactly the
# same translation, so it moved to `common` and this alias keeps the dozen call
# sites below reading as they did.
_run_error = run_error


def _cancelled_error() -> dict:
    return {"kind": "review_cancelled", "detail": _review_budget._ABANDONED_REASON, "status": 409}


def _under_review_lock(cid: str, sid: str, run, write) -> None:
    """Run `write` under the campaign lock, fenced on the two things that make
    a terminal review write wrong rather than merely late.

    The cancellation check and the write are ONE hold, deliberately: split
    apart, the reviewer's Cancel lands between them and the run recreates the
    review they just dismissed. The identity check is in the same hold for the
    same reason -- a scene replaced between the two would receive this review.

    Retried on contention, and only here: everything else in this file answers
    a busy campaign with a 409 the caller can repeat, and this caller cannot --
    the payload exists nowhere else.
    """
    # `while` rather than `for`, so there is no falling out of it. A loop that
    # can end without either writing or raising would return `None` for a
    # review nobody wrote and nobody was told about, which is the worst outcome
    # this function has -- and a shape that cannot express it beats a comment
    # promising it will not happen.
    left = _PERSIST_ATTEMPTS
    while True:
        left -= 1
        try:
            with store.locks.campaign_lock(cid):
                # BOTH flags, exactly as `_review_abandoned` reads both. They
                # are different intents that want the same answer: a Discard of
                # the review (`DELETE .../pending-review`) and a Stop on the run
                # (`POST .../runs/{id}/cancel`). Reading only the first left the
                # second able to stop the generating and then publish anyway,
                # because a cancelled scope does not reach into a threadpool
                # worker that has already started this write.
                if run.review_cancelled or run.cancel_requested:
                    raise _ReviewCancelledError
                if streaming._scene_moved(cid, sid, run.scene_identity):
                    raise _SceneMovedError
                write()
                # The campaign's write token (#409), in the same hold as the
                # write it records. A review is the one durable campaign write
                # with no request behind it at the moment it lands: the route
                # that started it answered 202 and is marked `@computes_only`
                # (correctly -- at 202 nothing had been written yet), and the
                # run publishes minutes later with no response line for the
                # middleware to stamp. Without this, a token read before an
                # absorb landed stays current after it, and a clock advance or
                # a checkpoint priced against it proceeds as though the scene
                # had not gained a review at all.
                store.revision.bump(cid)
                return
        except store.locks.StoreBusy:
            if left <= 0:
                raise
            time.sleep(_PERSIST_BACKOFF)


def _accepted(run, generation: str) -> dict:
    """The 202 body: which run to poll, and which review it is preparing.

    The generation rather than only the run id, because Cancel is addressed to
    the REVIEW and not to one of the runs that build it -- an absorb and the
    retries of its phases all carry the same one.
    """
    return {"run": runs.run_payload(run), "generation": generation}


async def _persist_review(cid: str, sid: str, run, generation: str, write,
                          result: dict | None = None) -> dict:
    """The terminal write shared by an absorb and by both retries.

    Off the event loop, because the acquire blocks and this runs on the
    lifespan's loop: a campaign held by a save would otherwise stall every
    unrelated request and open stream in the process rather than just this
    review.
    """
    try:
        await run_in_threadpool(_under_review_lock, cid, sid, run, write)
    except _ReviewCancelledError:
        return {"state": "cancelled", "error": _cancelled_error()}
    except _SceneMovedError:
        return {"state": "failed", "error": {
            "kind": "scene_gone", "status": 404,
            "detail": "the scene this review was prepared for is gone"}}
    except store.pending_reviews.NoPendingReviewError:
        return {"state": "failed", "error": {
            "kind": "review_missing", "status": 409,
            "detail": "there is no review to fold this into any more"}}
    except store.pending_reviews.ReviewReplacedError:
        return {"state": "failed", "error": {
            "kind": "review_replaced", "status": 409,
            "detail": "a newer review of this scene replaced the one this was retrying"}}
    except (OSError, store.locks.StoreBusy, store.pending_reviews.CorruptReviewError) as exc:
        return {"state": "failed", "error": {
            "kind": "busy", "status": 409,
            "detail": f"the review could not be stored: {exc}"}}
    return {"state": "landed",
            "result": {"generation": generation, "sid": sid, **(result or {})}}


@router.get("/campaigns/{cid}/scenes/{sid}/pending-review")
def get_pending_review(cid: str, sid: str) -> dict:
    """The review waiting to be saved for this scene, if there is one.

    `{"review": None}` rather than a 404 for a scene with none: that is the
    ordinary case on every mount, and an error there would make every quiet
    scene look broken -- the same call `GET .../run` makes.

    **The watermark is checked here, under the campaign lock, and the review is
    withheld when it fails.** Once the absorb landed the scene stopped being
    held, so the player can have appended turns, cut posts or retconned since;
    none of that advances the commit epoch, so the review's token would still
    pass its supersession check and the save would mark the scene absorbed with
    a summary of posts that are no longer there. Saying so on the way in is
    what lets the panel offer "re-run" instead of letting the reviewer fill in
    a form that is going to be refused.

    A review whose token has ALREADY committed is withheld too, and cleaned up
    on the way past. `_clear_pending_review` is deliberately non-fatal -- a save
    that landed must not be turned into a 500 by a sidecar that will not unlink
    -- so a record can outlive the commit it belongs to, and nothing else here
    would notice: `mark_absorbed` writes frontmatter and the watermark digests
    messages, so the transcript still matches and the stale review reads as
    perfectly current. The reviewer reloads and is handed a form for work that
    is already in the chronicle.
    """
    with store.locks.campaign_lock(cid):
        scene = _require_scene(cid, sid)
        try:
            record = store.pending_reviews.read(cid, sid)
        except store.pending_reviews.CorruptReviewError as exc:
            # NOT "no review". A file that will not parse cannot be repaired by
            # asking again, and an empty panel reads as "the absorb never
            # happened" -- inviting the reviewer to spend the whole budget a
            # second time rather than telling them what is wrong.
            raise HTTPException(status_code=409, detail={
                "kind": "review_unreadable",
                "detail": f"the stored review could not be read: {exc}"}) from exc
        except OSError as exc:
            raise HTTPException(status_code=409, detail={
                "kind": "busy",
                "detail": f"the stored review could not be read: {exc}"}) from exc
        answer: dict = {"review": None, "generation": None, "stale": None}
        if record is not None and _already_committed(cid, record):
            # Under the same hold as the read, so nothing serves it in between.
            _clear_pending_review(cid, sid,
                                  record["review"].get("commit_token") or "")
            record = None
        if record is not None:
            mark = store.pending_reviews.watermark(scene["messages"])
            prepared = record.get("watermark") or {}
            answer["generation"] = record.get("generation")
            if mark == prepared:
                answer["review"] = record["review"]
            else:
                answer["stale"] = {"prepared_posts": prepared.get("count"),
                                   "current_posts": mark["count"]}
    return answer


def _already_committed(cid: str, record: dict) -> bool:
    """Whether this record's save has already landed.

    `done`, not merely present: an entry is reserved before the writes and
    marked done after them, and the in-between state is a commit that began and
    may yet be resumed by a retry carrying the same token -- which needs the
    record it is resuming, not a panel told there is nothing here.

    A caller-minted token that this app never reserved is simply unseen, which
    is the same answer as "not committed" and the right one.
    """
    entry = store.commits.lookup(cid, record["review"].get("commit_token") or "")
    return bool(entry and entry.get("done"))


@router.delete("/campaigns/{cid}/scenes/{sid}/pending-review")
def delete_pending_review(cid: str, sid: str, request: Request,
                          generation: str = Query(...)) -> dict:
    """Discard this scene's pending review, and stop whatever is still making it.

    **The run is flagged before the record is deleted, both under one hold.**
    Left unordered the two lose to each other: the reviewer cancels while an
    absorb is finishing, the DELETE lands, and the runner then publishes and
    recreates the review they just dismissed. A cancelled review that reappears
    minutes later is worse than one that was never saved.

    Idempotent, and named by generation rather than by run id: a DELETE for a
    generation that has already gone flags nothing, removes nothing and reports
    success, because the reviewer's intent is satisfied either way.

    **It answers only once the runs it flagged have really stopped**, and the
    wait is outside the lock. Answering earlier would let the caller's next act
    -- which is usually "end the scene again" -- race a retry that is still
    unwinding, and be refused with `run_in_flight` by a review it has just
    discarded. Cancel means stopped, the way it does for a turn.
    """
    _require_scene(cid, sid)
    removed, refused = False, None
    with store.locks.campaign_lock(cid):
        # The flag lands FIRST and stays landed even if the removal below
        # refuses: stopping the work is the half of Cancel that cannot be
        # retried into existence later, and a record that outlives its runs is
        # refused by its own watermark at save.
        flagged = runs.cancel_review(request.app, cid, sid, generation)
        try:
            removed = store.pending_reviews.clear(cid, sid, generation)
        except OSError as exc:
            # Carried OUT of the hold rather than raised inside it. The wait
            # below must happen outside the campaign lock -- a flagged run
            # reaches its terminal persist through that same lock, so waiting
            # for it while holding it is a deadlock with a thirty-second fuse
            # per run. There is exactly one `await_reviews_stopped` call in
            # this route for that reason, and it is after the `with`.
            refused = exc
    stopped = runs.await_reviews_stopped(flagged)
    if refused is not None:
        # Not a 500: `clear` declines to remove a record it could not identify
        # (the safe direction), and the condition is transient -- a sync client
        # holding the file, a sharing violation -- so the client is told to
        # retry rather than shown a crash for a button that half worked. The
        # runs it flagged are stopped either way, which is the half that
        # cannot be retried into existence later.
        raise HTTPException(status_code=409, detail={
            "kind": "busy",
            "detail": f"the review could not be removed: {refused}"}) from refused
    return {"removed": removed, "stopped": stopped}


def _pending_for_retry(cid: str, sid: str) -> dict:
    """The review a scoped retry is about to re-run one phase of.

    Refused up front rather than at the terminal write, and before a token is
    spent: a retry with nothing to merge into can only be dropped, and a retry
    of a review whose transcript has moved would be folded into a review the
    save is going to refuse anyway.
    """
    try:
        record = store.pending_reviews.read(cid, sid)
    except store.pending_reviews.CorruptReviewError as exc:
        raise HTTPException(status_code=409, detail={
            "kind": "review_unreadable",
            "detail": f"the stored review could not be read: {exc}"}) from exc
    except OSError as exc:
        raise HTTPException(status_code=409, detail={
            "kind": "busy",
            "detail": f"the stored review could not be read: {exc}"}) from exc
    if record is None:
        raise HTTPException(status_code=409, detail={
            "kind": "review_missing",
            "detail": "there is no open review for this scene to retry a step of"})
    scene = _require_scene(cid, sid)
    mark = store.pending_reviews.watermark(scene["messages"])
    if mark != record.get("watermark"):
        # The same refusal the retrieval and the save give, and it belongs here
        # too rather than only there: a phase folded into a review the save is
        # going to refuse anyway is the most expensive way to reach that
        # answer, and the reviewer would be shown a freshly-retried panel that
        # cannot be committed.
        raise HTTPException(status_code=409, detail={
            "kind": "review_stale",
            "detail": "the scene changed after this review was prepared — "
                      "re-run End Scene to review what is there now",
            "prepared_posts": (record.get("watermark") or {}).get("count"),
            "current_posts": mark["count"]})
    return record


def _retry_start(cid: str, sid: str, request: Request, kind: str, work_for) -> dict:
    """Reserve a `review` run for a scoped retry and hand it its work.

    The generation comes from the STORED review, not from this request: a retry
    belongs to the review it is retrying, so Cancel on that review stops it and
    a fresh absorb landing in the meantime makes its merge refuse rather than
    fold one review's phase into another's.
    """
    # ONE hold across the check and the reservation. Cancel flags and deletes
    # under this same lock, so split apart there is a window where the record
    # is read, the DELETE removes it and flags nothing (no run exists yet), and
    # this then reserves a run for a review that is gone: it holds the scene
    # and spends the whole retry budget before ending in `review_missing`, and
    # aborting the browser's request stops none of that. Reentrant, so
    # `reserve_review`'s own acquisition costs nothing.
    #
    # A closed branch first (play controls III): the review a retry merges into
    # is one the chronicle commit refuses on a closed scene, so the phase would
    # be spent for nothing -- the same reason a stale review is refused below.
    with store.locks.campaign_lock(cid):
        runs.require_scene_open(cid, sid)
        record = _pending_for_retry(cid, sid)
        generation = record.get("generation") or ""
        run = runs.reserve_review(request.app, cid, sid, kind, generation)
    with runs.reservation(request.app, run):
        runs.start_computing(request.app, run, work_for(run, generation))
        return _accepted(run, generation)


def _require_unmoved_review(cid: str, sid: str, token: str) -> None:
    """Refuse a save whose review was prepared against a transcript that moved.

    MUST be called inside the campaign-lock hold that covers the writes: read
    outside one, it answers a question that was true a moment ago.

    A scene with NO stored review falls through to the epoch check alone, and
    that is a KNOWN GAP rather than a claim that nothing can go wrong there: one
    tab discarding a review while another still has it open leaves the second
    tab holding a token whose watermark no longer exists, and play continuing
    advances no epoch, so it can commit a summary of posts nobody reviewed.

    It is left open deliberately, because nothing on disk can tell that case
    apart from the ordinary one. "The token was server-minted" does not do it --
    `commits.mint` stamps a token for any save that asks, including one for a
    scene that never had a review at all, so refusing on that reads a fresh
    hand-made save as a discarded review (`test_routes.py`'s
    `test_a_caller_minted_token_is_fenced_from_newer_saves_too` is exactly that
    shape). Closing it needs the discard to leave something behind -- a spent
    token recorded where the ledger can see it -- which is a durable structure
    with a lifecycle of its own, not a condition to add here.

    A save with **no token at all** falls through too. That is the documented
    opt-out from idempotency (`models.ChronicleSave`), not a claim about any
    review, so there is nothing for the stored record to contradict -- and
    reading the default `""` as "a different token" refused every one of them
    on any scene with a review waiting.

    Everything else refuses, and each for its own reason:

    * **A record for a DIFFERENT token.** Another device force-absorbed this
      scene while this review sat open, so the record describes a newer review
      of the same posts. Falling through committed this older one's summary and
      edits on the strength of an epoch check that a fresh scene passes anyway
      -- the exact stale write the watermark exists to stop, arrived at by
      declining to look. Only reachable for a *fresh* save: a replay of an
      already-recorded token returns its prior result well above this.
    * **Unreadable.** Not evidence that this save is stale, but not evidence
      that it is sound either, and the epoch check does not cover what this one
      does: play continuing advances no epoch. Refusing is recoverable -- a
      fresh absorb replaces the sidecar outright, which is what the message
      says to do -- where committing is not. `OSError` is split off from a
      garbled file because it says something different: a sync client holding
      the sidecar for a moment clears on its own, so that one is the ordinary
      retryable refusal rather than an instruction to re-run.
    """
    try:
        record = store.pending_reviews.read(cid, sid)
    except OSError as exc:
        log.warning("pending review for %s/%s could not be read at save",
                    cid, sid, exc_info=True)
        raise HTTPException(
            status_code=409,
            detail={"kind": "busy",
                    "detail": f"this scene's review could not be read: {exc}"}) from exc
    except store.pending_reviews.CorruptReviewError as exc:
        log.warning("pending review for %s/%s is corrupt at save",
                    cid, sid, exc_info=True)
        raise HTTPException(
            status_code=409,
            detail={"kind": "review_unreadable",
                    "detail": "this scene's stored review is unreadable, so this save "
                              "cannot be checked against it — re-run End Scene"}) from exc
    if record is None or not token:
        return
    if record["review"].get("commit_token") != token:
        raise HTTPException(
            status_code=409,
            detail={"kind": "review_superseded",
                    "detail": "a newer review of this scene is waiting to be saved — "
                              "reload the campaign to see it"})
    scene = _require_scene(cid, sid)
    mark = store.pending_reviews.watermark(scene["messages"])
    if mark != record.get("watermark"):
        raise HTTPException(
            status_code=409,
            detail={"detail": "the scene changed after this review was prepared — "
                              "re-run End Scene to review what is there now",
                    "kind": "review_stale",
                    "prepared_posts": (record.get("watermark") or {}).get("count"),
                    "current_posts": mark["count"]})


def _clear_pending_review(cid: str, sid: str, token: str) -> None:
    """Drop the pending review THIS save has made obsolete, and no other.

    Scoped to the committed token, because "whatever is stored" is a different
    record on the path that needs this most. The commit is idempotent by design
    (#235), so a duplicate PUT of an already-recorded token arrives long after
    its first response was lost -- by which time another device may have
    force-absorbed the scene and stored a *newer* review, whose own token
    carries the epoch that save advanced and is therefore still savable.
    Clearing that one deletes an entire end-of-scene generation for a save that
    wrote nothing: the loss this whole change exists to prevent, arrived at
    through its own cleanup.

    A **tokenless** save clears whatever is stored, and that is not a hole in
    the scoping. It has no ledger entry, so it cannot be the replay the
    paragraph above is about; and it advances the scene epoch like any other
    commit, so a review left behind by it is one whose own token can no longer
    pass -- a panel that could only ever be refused.

    Never fatal. The commit has landed by the time this runs, so a sidecar that
    will not go must not turn a successful save into a 500 -- the review it
    leaves behind is refused at its next retrieval by the watermark, and
    replaced outright by the next absorb.
    """
    try:
        record = store.pending_reviews.read(cid, sid)
        # `read` has already refused anything whose `review` is not a dict.
        if record is None:
            return
        if token and record["review"].get("commit_token") != token:
            return
        store.pending_reviews.clear(cid, sid, record.get("generation"))
    except (OSError, store.pending_reviews.CorruptReviewError):
        log.warning("pending review for %s/%s could not be cleared after the save",
                    cid, sid, exc_info=True)

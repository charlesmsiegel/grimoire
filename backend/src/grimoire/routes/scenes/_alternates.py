"""Legacy alternate promotion and restoration preserve the outgoing reply."""

from __future__ import annotations

import contextlib

from fastapi import HTTPException, Request

from ... import store
from .. import runs, streaming
from ..common import _require_scene
from ._router import router as router


def _put_back(cid: str, sid: str, showing: str | None) -> bool:
    """Promote the take that was in the transcript when a swap began.

    Addressed by content, like the request itself: retention shifts indices, so
    the position it sat at is not the position it sits at now. Reports whether
    the transcript really is showing it again, because the callers differ in
    what they do when it is not.

    Best-effort. Every failure it swallows is a second failure on top of the one
    being repaired, and that first one is the one worth reporting. Returns False
    for an empty slot too: `promote` can fill one but not empty one, and a
    decision derived from a reply a dead reroll already removed is not on screen
    whatever happens next.
    """
    if showing is None:
        return False
    try:
        back = store.alternates.state(cid, sid)
        at = next((i for i, r in enumerate(back["runs"])
                   if store.alternates.variant_id(r) == showing), None)
        if at is None:
            return False
        store.alternates.promote(cid, sid, at)
        streaming._prune_rewrites(cid, sid)
        return True
    except (OSError, store.scenes.TurnSizesDesynced,
            store.alternates.AlternateNotFound):
        return False


_PREVIEW_CHARS = 200


def _alternate(run: dict) -> dict:
    """One variant as the transcript would read it — the posts joined the way
    they render, clipped. The full text is never sent: the client's job is to
    pick a variant, and the one it picks arrives as transcript."""
    text = "\n\n".join(s["content"] for s in run["segments"])
    return {"id": store.alternates.variant_id(run),
            "created": run.get("created", ""), "guidance": run.get("guidance", ""),
            # "" for a variant with no record of its route — every one that
            # predates #77, and every one reconciled out of the transcript
            # rather than archived. The client reads that as "the scene's
            # model", which is what it was before an override could exist.
            "model": run.get("model", ""),
            "posts": len(run["segments"]),
            "preview": text[:_PREVIEW_CHARS] + ("…" if len(text) > _PREVIEW_CHARS else "")}


@router.get("/campaigns/{cid}/scenes/{sid}/alternates")
def get_scene_alternates(cid: str, sid: str):
    """Every variant of the generation a reroll would replace. `active` is the
    one in the transcript, and null means the slot is empty — what a reroll
    whose stream died leaves behind."""
    _require_scene(cid, sid)
    try:
        # The check above guards only itself. Resolving the set makes several
        # more reads, and the scene can go between them — see the swap below for
        # what actually reaches this, which is the sidecar outliving its
        # transcript rather than an ordinary rename or delete. A read for a
        # scene that is not there is a 404 however late it finds out.
        state = store.alternates.state(cid, sid)
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound) as exc:
        raise HTTPException(status_code=404, detail="scene not found") from exc
    return {"active": state["active"],
            "alternates": [_alternate(r) for r in state["runs"]]}


@router.post("/campaigns/{cid}/scenes/{sid}/alternates/{vid}")
def post_scene_alternate(cid: str, sid: str, vid: str, request: Request):
    """Cycle (or pin) a stored variant into the transcript, parking the live one.

    Addressed by `variant_id`, not by position: retention shifts every index
    when a full set gains a variant, and a client snapshot from before that
    shift would otherwise name a different take than the one it previewed.
    """
    # One lock over the whole check-retire-swap span, so nothing lands between
    # the three and the request is all-or-nothing.
    with store.locks.campaign_lock(cid):
        # The existence check belongs INSIDE the lock. Outside it, a rename or
        # delete could land while this request waited, and the reads below would
        # then fault on a scene that was there when they were authorized. Every
        # `store.scenes` mutator runs under this same lock (`@_serialized`), so
        # from here on the scene cannot go anywhere.
        #
        # A plain race with either already answered 404, because both carry the
        # sidecar with the transcript and the resolve then finds no set. What
        # this catches is the sidecar OUTLIVING its transcript — the stranded
        # source `repoint_scenes` leaves behind on purpose, and the over-long
        # sidecar name `delete_scene` is allowed to fail to unlink.
        _require_scene(cid, sid)
        # A promotion rewrites the post a live turn is about to append after,
        # and parks the current one as a variant. Inside the hold that already
        # covers the swap, so nothing reserves a turn between the two.
        runs.require_scene_free(request.app, cid, sid)
        runs.require_scene_open(cid, sid)
        # Resolve FIRST. The id comes from a client snapshot that another tab
        # may have moved on from, and retiring a decision is not something a
        # request that then 404s gets to do: a stale click would cancel a
        # proposal belonging to a turn it never swaps past. Position is safe
        # from here on — `promote` re-resolves under this same lock.
        state = store.alternates.state(cid, sid)
        index = next((i for i, r in enumerate(state["runs"])
                      if store.alternates.variant_id(r) == vid), None)
        if index is None:
            raise HTTPException(status_code=404, detail="alternate not found")
        if state["active"] == index:
            # Already showing. `promote` would return without touching the
            # transcript, so retiring anything below would be a side effect of a
            # request that changed nothing — and a delayed click for a variant
            # another tab has since promoted would cancel that tab's proposal.
            return {"ok": True}
        # What is on screen right now, addressed by content rather than by
        # position — the same rule the request itself follows, and what the
        # rollback below promotes if the retirement cannot be written.
        showing = (store.alternates.variant_id(state["runs"][state["active"]])
                   if state["active"] is not None else None)
        # Heal now, retire after. Healing is what can append a 🎲 line, and
        # `promote` has to reconcile against the transcript that leaves behind —
        # but a swap that fails must not take the decision with it. The sidecar
        # preflight is not enough on its own: `promote` writes the TRANSCRIPT
        # too, and that write can fail after the sidecar's has succeeded,
        # leaving the reader looking at the exact narration the proposal was
        # derived from with no way to resolve it. Same split as regenerate.
        store.proposals.heal(cid, sid)
        try:
            store.alternates.promote(cid, sid, index)
            # The run swapped out leaves its `post_id`s behind: the sidecar
            # keeps a variant's words, never its ids.
            streaming._prune_rewrites(cid, sid)
        except store.alternates.AlternateNotFound as exc:
            # Reachable despite the check above only if that heal appended a
            # roll line and moved the slot -- in which case the transcript now
            # ends on a roll and there is nothing to swap.
            raise HTTPException(status_code=404, detail="alternate not found") from exc
        except store.scenes.TurnSizesDesynced as exc:
            # Same refusal regenerate makes, for the same reason: boundaries that
            # do not fit the transcript cannot authorize replacing a run.
            raise HTTPException(
                status_code=400,
                detail="this scene's recorded turn boundaries no longer match its "
                       "transcript — delete the last reply manually to swap alternates") from exc
        except BaseException:
            # `promote` is TWO transcript writes — drop the live run, append the
            # chosen one — and failing between them is a third window: the slot
            # is empty, so the pending decision's narration is not on screen at
            # all, and neither refusal above applies because a write really did
            # land.
            _repair_a_broken_promotion(cid, sid, showing)
            raise
        # The transcript is now showing a different take, so the decision the
        # old narration produced is retired — and only now that it really is.
        # Accepting a proposal whose text is no longer on screen would continue
        # a mechanical decision nothing there asked for.
        #
        # Which is exactly why a failure HERE cannot simply be reported: the
        # swap has landed, so the reader would be shown new narration beside a
        # still-actionable decision the old narration produced. The transcript
        # and proposals.json cannot be written as one, so whichever goes second
        # leaves a window; this is the one that is worse to leave open, and the
        # only one with a compensating action available.
        try:
            store.proposals.supersede(cid, sid)
        except BaseException:
            _undo_a_promotion(cid, sid, showing)
            raise
    return {"ok": True}


def _repair_a_broken_promotion(cid: str, sid: str, showing: str | None) -> None:
    """Repair a promotion that raised, possibly between its two transcript
    writes with the slot left empty: put back the take that was `showing`, or,
    failing that, retire the decision its narration was derived from. Both
    repairs are correct; they just differ in what the reader ends up looking
    at. Best-effort -- the promotion's own failure is the one worth reporting.

    Only when there was a live run to lose, though. Filling an EMPTY slot
    appends and removes nothing, so a failed append leaves the transcript
    exactly as it was: the decision's narration never moved, and there is
    nothing to repair. Retiring it there would cancel a still-valid roll for a
    swap that did not happen — which is the state a reroll that emitted a fence
    and no narration deliberately leaves recoverable."""
    if showing is None or _put_back(cid, sid, showing):
        return
    # The put-back is preferred: it restores the state the request started
    # from, decision and narration together. Failing that, the decision has to
    # go, because what it was derived from is gone — an empty slot the ‹/›
    # control can refill, beside a proposal that would otherwise still be
    # acceptable.
    with contextlib.suppress(OSError, store.scenes.TurnSizesDesynced):
        store.proposals.supersede(cid, sid)


def _undo_a_promotion(cid: str, sid: str, showing: str | None) -> None:
    """Undo a promotion that landed when retiring its decision could not: put
    the take that was `showing` back, or empty the slot it filled. Called in
    the hold that promoted; best-effort.

    Putting it back is likely to work: a different file failed
    (proposals.json), so the write that just succeeded is likely to succeed
    again — unlike the reroll restore, where the same disk stopped both. No
    second repair, unlike `_repair_a_broken_promotion`: the fallback there is
    to retire the decision, and retiring it is precisely what has just
    failed."""
    if showing is not None:
        _put_back(cid, sid, showing)
        return
    # The slot was EMPTY and `promote` has just filled it, so putting it back
    # means emptying it again — there is no take to promote, and `_put_back`
    # correctly reports nothing to do. Without this the reader is left with the
    # archived take on screen beside a decision that was produced with the
    # slot empty: the same mismatch `_repair_a_broken_promotion` exists to
    # prevent, reached from the state a reroll that emitted a fence and no
    # narration leaves behind.
    #
    # Safe to aim at the tail: the lock has been held since before `promote`,
    # so the trailing run is the one it just appended.
    with contextlib.suppress(OSError, store.scenes.TurnSizesDesynced, IndexError):
        store.scenes.remove_trailing_assistant_run(cid, sid)

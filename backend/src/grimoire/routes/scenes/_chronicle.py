"""A reviewed chronicle commit journals non-idempotent work under its original campaign hold."""

from __future__ import annotations

import contextlib

from fastapi import Depends, HTTPException, Request

from ... import store
from ...llm import LLMClient
from ...store.absorb import membership as absorb_membership
from ...store.continuity import review as continuity_review
from .. import continuity as continuity_routes
from .. import runs
from ..common import _campaign_root_or_404, _page_window, _require_scene, get_llm
from ..models import ChronicleSave
from . import _review_state
from ._router import router as router

#: How many chronicle records this route returns when the caller names no
#: `limit`. The window has always been the newest `CHRONICLE_PAGE`, not the
#: whole file: the panel that reads it renders a recap, and a campaign accretes
#: one record per absorbed scene forever. `limit` is what an explicit caller
#: raises (or lowers); `offset` is how it reaches what this default cuts off.
CHRONICLE_PAGE = 50


@router.get("/campaigns/{cid}/chronicle")
def get_chronicle(cid: str, limit: int | None = None, offset: int | None = None):
    """The campaign's chronicle records, oldest-first, newest page by default.

    The one route here whose window is anchored at the *newest* end rather than
    at the front of what it prints: `offset` skips that many of the NEWEST
    records, so a reader pages backwards by raising it. `chronicle.page` holds
    that shape and says why it is the only one available here.

    Sending neither parameter returns the newest `CHRONICLE_PAGE`, byte for byte
    what this route returned before it could be paged (#216). `offset` on its
    own therefore slides that same default-sized window back, rather than
    returning everything after a skip.

    The anchor is what moves: absorbing a scene appends a record and shifts
    every offset by one, so a reader paging backwards across a campaign still
    being played can see a record twice. Same caveat as `GET .../scenes`, and
    the same answer — an index-anchored window (`GET .../scenes/{sid}`, which
    takes `before`) is what does not drift.
    """
    limit, offset = _page_window(limit, offset)
    _campaign_root_or_404(cid)
    return store.chronicle.page(cid, CHRONICLE_PAGE if limit is None else limit, offset)


def _merged_detail(cid: str, merged: list[dict]) -> str:
    """The sentence a save refused for merged records shows: which record went
    into which, and what to do about it. The panel shows it verbatim through
    its generic save-error path, so it has to say everything on its own."""
    names = "; ".join(
        f"{continuity_review.reader_name(cid, m['source'], start=True)} was merged into "
        f"{m['canonical_title']}" for m in merged)
    rows = "that row" if len(merged) == 1 else "those rows"
    return (f"{names} after this review was staged. "
            f"Reject {rows} (or re-absorb) and save again.")


def _changes_membership(body: ChronicleSave) -> bool:
    """Whether this save changes group membership -- refused without a commit
    token, whose journal is where its group targets are reserved."""
    has_membership = any(isinstance(edit, dict) and edit.get("kind") == "membership"
                         for edit in body.edits)
    if has_membership and not body.commit_token:
        raise HTTPException(status_code=409, detail={"kind": "membership_commit_token",
            "detail": "Membership changes need a durable review commit token; prepare a new review."})
    return has_membership


def _refuse_a_foreign_replay(sid: str, prior: dict, fp: str) -> None:
    """Refuse a repeat of a recorded token that is not a repeat of THIS save."""
    if prior.get("sid") and prior["sid"] != sid:
        # The review panel survives a scene switch, so a retry can carry
        # scene A's token to scene B's route. The ledger is
        # campaign-scoped; without this, B's save would return A's
        # result and write nothing.
        raise HTTPException(
            status_code=409,
            detail={"detail": "this save was already committed for a different "
                              "scene — reopen that scene's review",
                    "kind": "commit_scene_mismatch"})
    if prior.get("fingerprint") and prior["fingerprint"] != fp:
        # The review stayed editable after the failed save, and this
        # retry carries different content. Returning the first result
        # would report success while discarding the edits made since.
        raise HTTPException(
            status_code=409,
            detail={"detail": "this review changed after a save that already "
                              "committed — reload the campaign and edit the "
                              "records directly",
                    "kind": "commit_body_changed"})


def _refuse_an_unresumable_save(cid: str, sid: str, prior: dict) -> None:
    """Refuse to resume a reservation that never completed when its journal
    cannot say what it did, or a newer save of the scene has landed since."""
    if not prior["journalled"]:
        # Reserved before #271, so there is no account of what it did --
        # and it could have appended the timeline and applied any number
        # of edits before it died. Resuming it as fresh work would repeat
        # every one of them, so this keeps the pre-#271 refusal. Only
        # entries written by an older build can land here.
        raise HTTPException(
            status_code=409,
            detail={"detail": "an earlier save of this review started and did "
                              "not finish — reload the campaign to see what "
                              "landed",
                    "kind": "commit_incomplete"})
    claimed = prior.get("claimed")
    if claimed is not None and store.commits.scene_epoch(cid, sid) > claimed:
        # The epoch this reservation's own claim produced, recorded by
        # `reserve`. Anything past it is a LATER commit for this scene --
        # the re-absorb that the wedge deliberately leaves room for, or
        # the scene's deletion. Resuming now would rewrite the chronicle
        # entry and the scene summary with this older review on top of
        # that one. Being stranded half-applied is the better of the two:
        # the newer save is the current record, this one is history.
        #
        # Read from the entry rather than derived from the token, so a
        # caller-minted key -- which carries no epoch and is a supported
        # thing to send -- is fenced exactly like a server-minted one.
        raise HTTPException(
            status_code=409,
            detail={"detail": "a newer save of this scene completed while this "
                              "one was unfinished — reload the campaign to see "
                              "what landed",
                    "kind": "commit_incomplete"})


def _refuse_a_stale_review(cid: str, sid: str, token: str) -> None:
    """Refuse a fresh save whose review no longer describes the scene: play
    went on after it was prepared, or another review of the scene saved."""
    # The transcript watermark, and it does NOT overlap with the epoch
    # check below. The epoch guards against another REVIEW committing;
    # this guards against play continuing. `commits.reserve` is called
    # from exactly one place -- this route -- so appending a turn,
    # cutting a post or retconning one after the review landed advances
    # nothing, and the token minted from the older snapshot still
    # passes every check while the scene is marked absorbed with a
    # summary of posts nobody reviewed.
    #
    # Read under the hold that already covers the whole sequence, and
    # ahead of the first write, so a refusal leaves the chronicle
    # untouched and the commit token unspent.
    _review_state._require_unmoved_review(cid, sid, token)
    epoch = store.commits.token_epoch(token)
    if epoch is not None and epoch != store.commits.scene_epoch(cid, sid):
        # Two reviews of one scene carry different tokens, so the key
        # cannot order them: the second to save would append a second
        # set of timeline events and plot beats for the same scene. The
        # epoch stamped into the token is what the key is missing --
        # this review was prepared before some other save of this scene
        # completed, so it describes a state that no longer exists.
        raise HTTPException(
            status_code=409,
            detail={"detail": "another review of this scene was saved after "
                              "this one was prepared — re-absorb the scene to "
                              "review it against what is now recorded",
                    "kind": "commit_superseded"})


def _refuse_drifted_edits(cid: str, edits: list, reviewed_edits: list) -> None:
    """Refuse a fresh save some of whose staged edits no longer match what is
    stored, or target a record merged away since they were staged."""
    # Contradiction check (#111), before the first write and after the replay
    # branches of `put_chronicle` -- a replay of a save that already landed
    # must return its result, not be told the records it wrote now contradict
    # it.
    # On a FRESH commit only, for the same reason taken one step further: a
    # resume's own earlier writes have already moved the records its
    # remaining edits were staged against, so this pass would refuse the
    # very commit it is trying to finish. Those edits still meet
    # `apply_edits`' per-edit pass, which reports them as conflict failures
    # rather than as a clean refusal.
    #
    # This is the REVIEWER'S check, not the guard: it runs ahead of every
    # write so a 409 leaves the chronicle untouched and this commit token
    # unspent, letting the panel offer keep/replace/merge on a review that is
    # still intact. The guard is `apply_edits`' own pass, which re-judges the
    # batch immediately before it writes -- so a target that moves between
    # here and there is still caught, as a conflict failure rather than a
    # clean refusal.
    #
    # Neither pass makes check-and-write atomic, and the campaign lock does
    # not either: `overlay` (and most of `locks.UNREVIEWED`) mutates without
    # taking it, and the lock is machine-local, so a synced store sees none
    # of it. Closing the remaining window means compare-and-swap in each
    # mutator -- what `sheets.write(expected=...)` already does, and what the
    # other seven would need -- which is the concurrency change `locks.py`
    # says those modules are waiting on, not something to bolt on here.
    drifted = store.absorb.check_conflicts(cid, reviewed_edits)
    if drifted:
        raise HTTPException(
            status_code=409,
            detail={"detail": "some proposed changes no longer match what is "
                              "stored — review them and save again",
                    "kind": "edit_conflicts", "conflicts": drifted})
    # A row staged against a record merged away since (Slice D Decision
    # 19). Refused, never redirected: its `before` token was read off
    # the alias source, so writing its beat onto the canonical is a
    # write `check_conflicts` above never vouched for. Same place and
    # same reason as that check -- ahead of the first write, so the
    # reviewer rejects the named rows and the rest saves on this token.
    merged = continuity_review.merged_edit_targets(cid, edits)
    if merged:
        raise HTTPException(
            status_code=409,
            detail={"detail": _merged_detail(cid, merged),
                    "kind": "edits_target_merged", "edits": merged})


def _append_timeline_once(cid: str, body: ChronicleSave, progress: dict,
                          timeline: str | None) -> list[dict]:
    """Append this save's timeline events unless its journal says an earlier
    attempt already did (`timeline`, as the journal stood before this attempt
    reserved), and return what went wrong as failure rows.

    The one step of the save that appends, so the one that must not repeat: an
    append the journal marked attempted and never confirmed is reported, not
    made again."""
    started: list[dict] = []
    if timeline is None:
        try:
            store.chronicle.append_timeline(cid, body.timeline_events)
        except Exception as exc:  # noqa: BLE001 — unreadable timeline, full disk, ...
            # An exception means the append did NOT publish: atomic.write_text
            # replaces by rename as its last act. So this is an ordinary
            # reported failure, and the mark comes back off -- "unconfirmed"
            # is for a process that died without returning, not for a call
            # that returned by raising.
            progress.pop("timeline", None)
            started.append({"id": "timeline", "kind": "error",
                            "reason": f"the timeline events could not be "
                                      f"recorded: {exc}"})
        else:
            progress["timeline"] = "done"
        # Settled straight away rather than left to apply_edits' first
        # checkpoint: a failure in mark_absorbed below would otherwise leave
        # an append that demonstrably landed reading as unconfirmed forever.
        store.commits.checkpoint(cid, body.commit_token, progress)
    elif timeline == "pending" and body.timeline_events:
        # An earlier attempt journalled the append and never confirmed it.
        # Repeating it would double the campaign's timeline, so say so
        # instead -- the events are in the review and in the response.
        started.append({"id": "timeline", "kind": "error",
                        "reason": store.absorb.UNCONFIRMED})
    return started


@router.put("/campaigns/{cid}/scenes/{sid}/chronicle")
def put_chronicle(cid: str, sid: str, body: ChronicleSave, request: Request,
                  client: LLMClient = Depends(get_llm)):
    body.edits = absorb_membership.strip_private(body.edits)
    has_membership = _changes_membership(body)
    # Filled when this request reserves fresh membership identities: a row whose
    # group or lease could not be reached fails alone, with its own reason.
    membership_failures: dict[str, str] = {}
    _require_scene(cid, sid)
    facts = store.chronicle.scene_facts(cid, sid)
    # One hold across the whole persistence sequence (#234). These are four
    # independent writes; with a lock taken per write, contention arriving
    # partway returns 409 after the chronicle record and timeline events are
    # already durable -- and the retry that 409 invites appends the timeline
    # events a second time while the first attempt's approved edits were never
    # applied. Holding it here means a busy response is reported before the
    # first write, so a retry is safe. Reentrant, so the inner acquisitions
    # cost nothing.
    #
    # The same hold is what makes apply_edits' dossier branch safe (#235): it
    # compares the staged `before` with what is stored and then writes, and two
    # concurrent saves could otherwise both read a matching `before` before
    # either wrote -- a guard that stops neither.
    # The token names the attempt; the fingerprint names what it was for.
    fp = store.commits.fingerprint({
        "one_line": body.one_line, "summary": body.summary, "keywords": body.keywords,
        "timeline_events": body.timeline_events, "edits": body.edits})
    with (absorb_membership.held(cid) if has_membership else contextlib.nullcontext()), store.locks.campaign_lock(cid):
        # A review save writes back into the scene and marks it absorbed, so it
        # reshapes what a live turn is generating into. Inside the hold that
        # already covers the whole sequence, so nothing reserves a turn between
        # the check and the first write.
        runs.require_scene_free(request.app, cid, sid)
        # A closed branch cannot be absorbed, and absorbing one member closes
        # the rest -- so this waits for every member, or a turn landing in a
        # sibling just closed would write to a read-only scene.
        runs.require_scene_open(cid, sid)
        runs.require_group_free(request.app, cid, sid)
        # Inside the lock: two saves racing on one token must not both miss.
        prior = store.commits.lookup(cid, body.commit_token)
        progress: dict = {}
        if prior is not None:
            _refuse_a_foreign_replay(sid, prior, fp)
            if prior["done"]:
                # The review goes on the REPLAY path too, not only the
                # fresh-success tail. The commit is idempotent by design
                # (#235), so a save whose first response was lost returns the
                # recorded result through here -- and cleanup that lived only
                # on the first-execution path would never run for a process
                # that exited after recording the commit and before deleting
                # the review, leaving an obsolete review retrievable forever
                # for a scene that is demonstrably absorbed. Under the same
                # hold, so nothing reads it between the two.
                _review_state._clear_pending_review(cid, sid, body.commit_token)
                return prior["result"]
            _refuse_an_unresumable_save(cid, sid, prior)
            # Reserved and never completed: some of the four writes landed and
            # nobody knows which. Its journal does, so this attempt RESUMES it
            # (#271) -- every step the journal accounts for is skipped, and a
            # step it marked attempted without confirming is reported rather
            # than repeated.
            progress = prior["progress"]
            if has_membership:
                progress["membership_targets"] = absorb_membership.observe_targets(
                    cid, body.commit_token, progress.get("membership_targets", {}))
                # Lifecycle retirement also fences the durable deferred views.
                refreshed = store.commits.lookup(cid, body.commit_token)
                if refreshed is not None:
                    progress = refreshed["progress"]
        else:
            _refuse_a_stale_review(cid, sid, body.commit_token)
            if has_membership:
                progress["membership_targets"], membership_failures = absorb_membership.initial_targets(
                    cid, body.edits)
            _refuse_drifted_edits(cid, body.edits, absorb_membership.resumed_edits(
                body.edits, progress.get("membership_targets", {}), membership_failures))
        reviewed_edits = absorb_membership.resumed_edits(
            body.edits, progress.get("membership_targets", {}), membership_failures)
        # The one step of the four that appends -- the other three overwrite (the
        # chronicle entry is keyed by scene id, the frontmatter below is a
        # rewrite, and each edit has its own slot in the journal). So it is the
        # one that has to be durably *attempted* before it is attempted: the
        # journal entry rides along on the reservation below, which is claimed
        # BEFORE any non-idempotent write. `progress` carries a resumed
        # attempt's journal forward so this one adds to that account rather than
        # opening a second one.
        timeline = progress.get("timeline")
        if timeline is None:
            progress["timeline"] = "pending"
        store.commits.reserve(cid, body.commit_token, fp, sid, progress)
        record = store.chronicle.absorb(cid, {
            "id": sid, "one_line": body.one_line, "summary": body.summary,
            "keywords": body.keywords, **facts})
        started = _append_timeline_once(cid, body, progress, timeline)
        store.scenes.mark_absorbed(cid, sid, body.one_line, body.summary)
        def checkpoint() -> None:
            store.commits.checkpoint(cid, body.commit_token, progress)
            if has_membership:
                current = store.commits.lookup(cid, body.commit_token)
                if current is not None:
                    progress["membership_targets"] = current["progress"].get("membership_targets", {})

        applied, failures = store.absorb.apply_edits(
            cid, reviewed_edits, sid, progress=progress, checkpoint=checkpoint)
        # Rows the reviewer sent to the world library, promoted as a journalled
        # step of THIS commit rather than after it (spec §9.4): a retry after a
        # lost response must find its own world record in the journal, not
        # collide with it. Ahead of `record`, so a spent token replays both
        # lists with the rest of the result. Never fatal -- a failed publish
        # leaves the record in the campaign and says so.
        published, publish_failed = store.absorb.publish.publish_created(
            cid, body.edits, progress, checkpoint)
        result = {**record, "applied": applied, "failures": started + failures,
                  "published": published, "publish_failed": publish_failed}
        store.commits.record(cid, body.commit_token, result, fp, sid)
        # The scene is absorbed, so its pending review describes work that has
        # now landed. Inside the hold, after the ledger entry, so a save that
        # raised before this point leaves the review where the retry can find
        # it.
        _review_state._clear_pending_review(cid, sid, body.commit_token)
    # A commit completed in this request -- fresh, or a journalled resume
    # finishing one -- so the ledger moved: End Scene's incremental
    # reconciliation sweep (capstone §11.1, Decision 6). After the hold, so the
    # sweep's own persists never wait on it; the idempotent replay returned
    # inside it and never gets here. It never raises, and the records it
    # re-checks are read off `progress` inside its own `try`, so nothing
    # between the commit and this return can turn a landed save into a 500.
    continuity_routes.schedule_reconcile(request.app, cid, client, edits=body.edits,
                                         progress=progress)
    return result

"""Reserve scene turns, reviews, background work and explicit drafts."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable

from fastapi import HTTPException

from ... import store
from ...store import scenes
from ...store.campaigns.paths import CampaignNotFound
from . import _contracts, _events, _execution, _fences, _record, _subjects
from ._router import router as router


def replay_attempt(app, cid: str, sid: str, attempt_id: str | None):
    """The buffered response for an attempt that already ran, or `None`.

    Called at the very top of a producing route, ahead of its preflight checks
    -- see the call site for why that order matters. Resolves the subject the
    same way the run routes do, so a replacement scene that recycled the `sid`
    cannot replay the dead scene's turn.

    Answers `None` for anything it cannot resolve rather than raising: this runs
    before the route's own validation, and a malformed campaign or scene id must
    still get that route's 404, not a different one from here.
    """
    if not attempt_id:
        return None
    try:
        subject, identity = _subjects._scene_subject(cid, sid)
    except HTTPException:
        return None
    run = app.state.runs.for_attempt(subject, attempt_id, identity)
    if run is None:
        return None
    return _events.tail_response(run, 0, lead=_events.lead_frame(run))


def reserve_turn(app, cid: str, sid: str, kind: str,
                 attempt_id: str | None,
                 adopt_terminal: bool = True) -> tuple[_record.Run, bool]:
    """Reserve a `turn` run for this scene, or raise 409 if one is in flight.

    `attempt_id` comes from the `X-Grimoire-Attempt` header. Absent or
    malformed, one is generated: older clients and `curl` keep working, they
    simply get no idempotency, which is what they have today. Present, it is
    used verbatim -- the server never rewrites it, because the client has
    already stored it and will ask about it by that name.
    """
    # The closed check BEFORE reserving (a refusal after it would strand a run),
    # and in the same campaign-lock hold, so an absorb that closes this scene
    # cannot land between the check and the reservation. Reentrant.
    with store.locks.campaign_lock(cid):
        _fences.require_scene_open(cid, sid)
        run, fresh = _reserve(app, cid, sid, "turn", kind,
                              attempt_id or uuid.uuid4().hex,
                              adopt_terminal=adopt_terminal)
        # A recovery fence can beat reservation while the POST is in preflight.
        # Waiting for the runner to consume it is too late: route setup appends
        # the player's post and retires proposals before the producer starts.
        # Seal that outcome here and take the callers' existing replay branch,
        # leaving turn-owned state untouched and releasing scene exclusion.
        if fresh and run.cancel_requested:
            _execution.release_before_start(app, run, "cancelled")
            return run, False
        return run, fresh


def reserve_review(app, cid: str, sid: str, kind: str,
                   generation: str) -> _record.Run:
    """Reserve a `review` run for this scene, or raise 409 if one is in flight.

    `review` shares its exclusion key with `turn` (`exclusion_key` builds it
    from the subject, and `_EXCLUSIVE` holds both classes), so an absorb and a
    chat cannot hold one scene between them -- and every route that changes the
    scene's SHAPE is refused for the whole of a review, not just the whole of a
    turn. That is not decoration: an edit, a retcon or a cut landing under a
    ten-minute absorb would move the transcript the review is being built from,
    and the watermark would then refuse to save it -- after the entire budget
    had been spent, which is the most expensive way to discover a race that
    excluding prevents for free.

    **Reserved BEFORE the snapshot, which is why this returns before one is
    taken.** Snapshot-then-reserve leaves a gap in which a fast chat turn can
    reserve, append, finish and release, after which the absorb is accepted
    against a transcript that has already moved.

    No attempt id: a review carries no player text, so there is nothing for
    idempotency to protect, and the exclusion key already makes a duplicate
    POST a refusal rather than a second run. A client that lost the 202 finds
    its run through `GET .../run`.
    """
    run, _ = _reserve(app, cid, sid, "review", kind, None,
                      review_generation=generation)
    return run


def reserve_background(app, cid: str, sid: str, kind: str) -> _record.Run | None:
    """Reserve a `background` run for this scene, or `None` if it cannot be.

    The rolling summary and the scene-break check (#397). Fire-and-forget by
    class: `exclusion_key` gives `background` no key, so one can neither refuse
    a turn with `run_in_flight` nor hold the scene against an edit, a cut or an
    End Scene -- structural, rather than an exemption to remember.

    Every way a reservation can fail answers `None` rather than raising, and
    that is the difference from `reserve_turn`: nobody is waiting for this, and
    there is no request to answer. A scene deleted between the turn landing and
    this call, a store root mid-move, a contended campaign -- each means "not
    this one", and the next turn asks again.

    It is still a run rather than a bare task, for the two things a run buys
    that a task does not: it keeps the Android foreground service promoted
    while the work is live, which is the whole reason the trigger moved
    server-side, and `runner._guarded` gives it a failure boundary so one that
    dies takes no sibling with it.

    **It takes no campaign lock, which is what makes it safe on a turn's
    completion path.** `_reserve` takes one -- `ensure_identity` may mint and
    write an identity, and the hold is what lets `scene_held_free` exclude a
    reservation. Neither applies here: a `background` run holds no exclusion
    key, so nothing needs to exclude it, and the store-move refusal lives
    inside `start_or_existing` under the REGISTRY lock rather than this one.
    Left going through `_reserve`, an unrelated request holding the campaign
    lock would block this for `LOCK_TIMEOUT`, twice -- and this is awaited by
    the turn's own generator, so the SSE body would stay open and the composer
    locked for up to a minute after the reply was already on disk.

    So the identity is READ (`scene_identity`) rather than ensured, and a scene
    that has none yet -- the startup backfill skipped a contended campaign --
    simply gets no follow-up this turn. Nothing looks a background run up by
    identity; it is the subject key and nothing more.
    """
    try:
        identity = scenes.scene_identity(cid, sid)
    except OSError:
        return None
    if not identity:
        return None
    labels = {"campaign": _subjects._campaign_label(cid), "scene": _subjects._scene_label(cid, sid)}
    try:
        run, _ = app.state.runs.start_or_existing(
            ("scene", cid, identity), "background", kind, None, identity, labels=labels)
    except (_contracts.RunInFlightError, _contracts.StoreMovingError):
        # Neither is reachable for a keyless class today, and both are caught
        # rather than asserted away: this is fire-and-forget, so a reservation
        # that cannot happen is a skipped follow-up, never an exception on a
        # path where nobody is listening for one.
        return None
    return run


def reserve_campaign_background(app, cid: str, kind: str,
                                attempt_id: str | None = None,
                                *, labels: dict | None = None,
                                match: Callable[[_record.Run], bool] | None = None,
                                ) -> tuple[_record.Run, bool] | None:
    """Reserve the campaign's one live `background` run of this kind, or adopt
    the live one. ``None`` if the store is moving.

    Spec §11.1: at most one reconciliation sweep is live per campaign, and a
    start while one is live returns the live run -- so End Scene's automatic
    sweep and a Refresh pressed during it are one sweep. ``fresh`` comes back
    with the run because the caller must not wrap an ADOPTED run in
    `reservation` a second time: it already has a producer.

    It takes no campaign lock, for `reserve_background`'s reason: a
    `background` run holds no exclusion key, so nothing needs to exclude its
    reservation, and the store-move refusal lives inside `start_or_existing`
    under the registry lock. The adoption scan is in there too, which is what
    makes two simultaneous triggers get one run rather than two.

    ``labels`` add to the subject's own; ``match`` narrows adoption to a live
    run it accepts, and a live run it refuses raises `RunInFlightError`
    naming that run (`start_or_existing`'s ``single_match``).
    """
    subject = _subjects.campaign_subject(cid)
    try:
        return app.state.runs.start_or_existing(
            subject, "background", kind, attempt_id, None,
            labels={**_subjects._subject_labels(subject), **(labels or {})}, single_live=True,
            single_match=match)
    except _contracts.StoreMovingError:
        return None


def drop_pending_touched(app) -> None:
    """Forget every ref a trigger left pending, after the store root moved."""
    app.state.runs.drop_pending_touched()


def pin_root_until(app, run_id: str, drained: Callable[[float | None], bool]) -> None:
    """Hold the store root still for `run_id` until `drained(None)` returns,
    on a daemon thread of its own: a run whose tool thread outlived it ends
    without waiting on that thread, and the root still does not move under
    it (`RunRegistry.pin_root`)."""
    registry = app.state.runs
    registry.pin_root(run_id)

    def wait() -> None:
        try:
            drained(None)
        finally:
            registry.unpin_root(run_id)
    threading.Thread(target=wait, name=f"grimoire-root-pin-{run_id}", daemon=True).start()


def forget_root(app) -> int:
    """Drop every run from discovery after the store root moved -- see
    `RunRegistry.forget_root`."""
    return app.state.runs.forget_root()


def _reserve(app, cid: str, sid: str, cls: _contracts.RunClass, kind: str,
             attempt_id: str | None, adopt_terminal: bool = True,
             review_generation: str | None = None) -> tuple[_record.Run, bool]:
    """The shared half of `reserve_turn` and `reserve_review`.

    The scene's identity is captured here, via `ensure_identity` rather than a
    plain read: a campaign whose lock was contended at startup still has
    identity-less scenes, and capturing `None` would make the publish fence
    compare None with None -- which always matches, defeating the fence in
    exactly the case it exists for.
    """
    try:
        identity = scenes.ensure_identity(cid, sid)
    except CampaignNotFound as exc:
        raise _subjects._gone("campaign_gone", "no such campaign") from exc
    except scenes.SceneNotFound as exc:
        # `_require_scene` passed and then another request deleted or renamed
        # the scene before this took the campaign lock. An ordinary concurrent
        # mutation, and every other scene route answers it with a 404; leaving
        # it to fall through made a send 500 instead.
        raise _subjects._gone("scene_gone", "scene not found") from exc
    except OSError as exc:
        # The scene file could not be read or written just now -- a sync client
        # mid-write, a sharing violation (`identity.UnreadableError`), a device
        # that went away. `_require_scene` read this same file a moment ago, so
        # this is a transient condition and not a bad request; reported as the
        # store contention it is, which is a kind the client already retries,
        # rather than as the 500 an unhandled OSError would give it.
        raise HTTPException(status_code=409, detail={
            "kind": "busy", "detail": f"the scene could not be read: {exc}"}) from exc
    labels = {"campaign": _subjects._campaign_label(cid), "scene": _subjects._scene_label(cid, sid)}
    subject: _contracts.Subject = ("scene", cid, identity)
    try:
        # Under the campaign lock, which is what lets `scene_held_free` exclude
        # a reservation rather than merely narrow the window before one. The
        # identity capture above takes the same lock (`ensure_identity` is
        # serialized on it) and it is reentrant, so this costs nothing.
        with store.locks.campaign_lock(cid):
            run, fresh = app.state.runs.start_or_existing(
                subject, cls, kind, attempt_id, identity, labels=labels,
                adopt_terminal=adopt_terminal,
                # The subject a Stop would have built for this scene BEFORE
                # `ensure_identity` above minted one. Only different when the
                # startup backfill skipped this campaign, which is exactly when
                # a scene is still identity-less.
                also_precancelled=("scene", cid, _contracts.UNRESOLVED),
                review_generation=review_generation)
    except _contracts.RunInFlightError as exc:
        raise HTTPException(status_code=409, detail={
            "kind": "run_in_flight", "run_id": exc.run_id,
            "detail": "a turn or review is already running on this scene"}) from exc
    except _contracts.StoreMovingError as exc:
        # Distinct from `run_in_flight`, which resolves when somebody else's
        # turn finishes. This one resolves in milliseconds and the same send
        # will work, so the client is told to retry rather than to wait.
        raise HTTPException(status_code=409, detail={
            "kind": "busy",
            "detail": "the storage location is being changed; try again"}) from exc
    # A Stop that arrived while this route was still in setup is already on the
    # run: `start_or_existing` consumes the record under the same lock that
    # publishes it. `runner` reads the flag when it installs the cancel scope,
    # so the turn ends without ever reaching a provider.
    return run, fresh


def reserve_draft(app, subject: _contracts.Subject, kind: str,
                  attempt_id: str | None) -> tuple[_record.Run, bool]:
    """Reserve a `draft` run on a non-scene subject.

    **No exclusion key, and that is the point rather than an omission.** A
    tagline, a voice anchor, an image description and a scenario parse all run
    alongside whatever else the user is doing, including a turn in a campaign
    of the same world; giving one of them a key would let a preview nobody is
    waiting on refuse a chat, which is a far worse failure than the one
    detaching them fixes. `exclusion_key` returns `None` for `draft`, so this
    can never raise `RunInFlightError` -- there is nothing here to catch it.

    `attempt_id` is the client's own name for this piece of work, from
    `X-Grimoire-Attempt`. Absent, one is minted: the caller simply gets no
    idempotency and no way to re-find its run, which is exactly what it has
    today. Present, it is used verbatim and a duplicate delivery adopts the
    original run rather than spending a second call -- and it is what
    `GET .../runs?attempt=` matches, because on a subject where drafts overlap
    "the world's in-flight image-description run" names no one thing.

    No campaign lock, unlike `_reserve`. That hold exists so `scene_held_free`
    can exclude a reservation while it reshapes a scene; nothing reshapes a
    campaign or a world out from under a draft, and taking it here would make
    every one of these routes block behind a save.
    """
    try:
        return app.state.runs.start_or_existing(
            subject, "draft", kind, attempt_id or uuid.uuid4().hex, None,
            labels=_subjects._subject_labels(subject))
    except _contracts.StoreMovingError as exc:
        # The same refusal `_reserve` gives, for the same reason: the draft's
        # result is held on the run rather than written anywhere, but the work
        # itself still resolves the store root -- a scenario parse reads the
        # world's roster -- and a root that moves underneath it reads the wrong
        # tree. Milliseconds, so the client is told to retry rather than wait.
        raise HTTPException(status_code=409, detail={
            "kind": "busy",
            "detail": "the storage location is being changed; try again"}) from exc


def reserve_scene_draft(app, cid: str, sid: str, kind: str,
                        attempt_id: str | None) -> tuple[_record.Run, bool]:
    """Reserve a `draft` run on a SCENE, which today means the opener.

    Goes through `_reserve` rather than `reserve_draft` because a scene subject
    is the one that needs an identity: the `sid` moves on rename and is handed
    to the next scene on delete, so a run indexed by it could be adopted by a
    replacement. Everything else about it is a draft -- `exclusion_key` gives
    `draft` no key, so an opener neither holds the scene nor is refused by a
    turn, and re-generating an opener the player did not like is an ordinary
    thing to do twice.

    That non-exclusion is worth stating rather than inferring: an opener writes
    NOTHING. `post_first_post` is what puts the text in the transcript, and it
    takes `body.text` and makes no call -- so an opener running while the scene
    is frozen changes nothing about the scene, and freezing the scene while an
    opener runs would refuse a first-post the player is entitled to make.
    """
    return _reserve(app, cid, sid, "draft", kind, attempt_id or uuid.uuid4().hex)


def run_draft(app, subject: _contracts.Subject, kind: str, attempt_id: str | None,
              work, *, pass_run: bool = False) -> dict:
    """Reserve a computing `draft`, hand `work` to the runner, answer the 202.

    THE route-side half of the shared contract, so that the thirteen call sites
    are the three lines that differ between them -- what to generate and how to
    read it back -- and none of the five that do not: reserve, adopt a
    duplicate, guarantee the reservation reaches a terminal state, detach,
    shape the body. (The two scenario parses reserve through `reserve_draft`
    instead.) Written out per route, that is thirteen chances to forget
    `reservation` and leave a run `running` for the life of the process.

    `work` is `start_computing`'s: a zero-arg callable returning a coroutine
    that returns `{"state": ..., "result"?: ..., "error"?: ...}`. Build it
    AFTER every synchronous refusal the route can make -- a missing record, an
    unusable connection -- so those still reach the client as the status codes
    they are rather than as a run state read minutes later.

    A duplicate delivery (`fresh` false) is answered with the original run and
    nothing is generated again. That is the same promise `post_chat` makes and
    it costs more here than it looks: a draft is a whole provider call, so a
    retried POST that started a second one would be paid for twice and the two
    answers would disagree.

    `pass_run` hands `work` the reserved run (`work(run)` returns the
    coroutine) for a draft whose calls are filed under its id -- a tool loop
    runs under the caller's run id (roadmap 12's history question).
    """
    run, fresh = reserve_draft(app, subject, kind, attempt_id)
    if not fresh:
        return {"run": _events.run_payload(run)}
    with _execution.reservation(app, run):
        _execution.start_computing(app, run, (lambda: work(run)) if pass_run else work)
        return {"run": _events.run_payload(run)}


def forget_subject(app, subject: _contracts.Subject) -> int:
    """Drop a deleted record's runs from discovery -- see
    `RunRegistry.forget_subject` for why a delete has to do this at all."""
    return app.state.runs.forget_subject(subject)


def cancel_review(app, cid: str, sid: str, generation: str) -> list[_record.Run]:
    """Flag every run preparing this review as cancelled. Returns them.

    MUST be called inside the campaign-lock hold that then deletes the record,
    and BEFORE the delete. Split apart, the delete lands, the run publishes,
    and the review the player just dismissed comes back minutes later.

    Named by GENERATION rather than by run id or by recency, because neither of
    the obvious readings works: the stored payload names no producer, and
    "the scene's newest run" is as likely to be an unrelated live chat.

    Answers an empty list for a scene whose identity cannot be resolved rather
    than raising: the caller is deleting a record, and a Cancel that cannot
    find a run still has a record to remove.

    The runs come back so the caller can WAIT for them, which it must do
    outside this lock -- see `await_reviews_stopped`.
    """
    try:
        subject, _ = _subjects._scene_subject(cid, sid)
    except HTTPException:
        return []
    flagged = app.state.runs.reviews_for_generation(subject, generation)
    for run in flagged:
        run.review_cancelled = True
    return flagged


def await_reviews_stopped(flagged: list[_record.Run]) -> int:
    """Wait for flagged review runs to reach a terminal state. Returns how many
    were still live when asked.

    **Outside the campaign lock, always.** A run being cancelled reaches its
    terminal persist through `campaign_lock(cid)`; waiting for it while holding
    that lock is a deadlock with a thirty-second fuse.

    Waited on rather than left to unwind on its own, because the caller's very
    next act is usually to start a *fresh* absorb -- and `review` holds the
    scene's exclusion key exactly as a turn does, so a retry still unwinding
    would refuse it with `run_in_flight`. The phases notice the flag on their
    own abandonment poll, so this is normally well under a second; the bound is
    for a provider that will not unwind, which must not hold a worker forever.
    """
    live = [r for r in flagged if r.state == "running"]
    for run in live:
        run.terminal.wait(timeout=_contracts.CANCEL_TIMEOUT_SECONDS)
    return len(live)

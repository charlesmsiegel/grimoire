"""tracker: scheduling responsibilities."""

from __future__ import annotations

import logging

from ... import store
from ...llm import LLMClient
from .. import runs
from . import _fences, _persistence, _update_run
from ._router import router as router

log = logging.getLogger('grimoire.routes.tracker')


def mark(cid: str, sid: str, key: str) -> _fences.Mark | None:
    """The first half of scheduling: mark `key` `pending`, and return it with
    its generation -- or `None` when the tracker is off or the mark could not
    be written.

    **Meant to be called inside the campaign-lock hold that wrote the post**
    (the append, `_save`, `_pause`, `_accept_reroll`). There the acquisition
    below is reentrant and costs nothing; anywhere on a turn's path OUTSIDE
    such a hold it would be a fresh wait of up to `LOCK_TIMEOUT` in front of
    the player's terminal frames, which is exactly the cost `reserve_background`
    was built not to charge. `pending` before the run exists is also
    `records.save`'s caller contract (a crash between a snapshot and its index
    entry leaves `pending`, never an `ok` about a file that did not land).

    Never raises: the post is written, and tracking it is extra."""
    try:
        if not store.tracker.settings.enabled(cid):
            return None
        # A post hidden from context is paid no update: nothing reads its
        # state (`walk._latest_ok` steps over it), so marking it would buy a
        # call whose answer is never used.
        if store.tracker.walk.key_excluded(cid, sid, key):
            return None
        ident = store.scenes.ensure_identity(cid, sid)
        gen = store.tracker.records.mark_pending(cid, ident, key)
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        log.warning("tracker: could not mark %s pending in %s/%s -- %s", key, cid, sid, exc)
        return None
    return key, gen


def mark_response(cid: str, sid: str, rid: str) -> _fences.Mark | None:
    """`mark` for response `rid`'s ACTIVE variant -- the one the transcript now
    shows. `None` when it has none yet. Same calling rule as `mark`."""
    try:
        if not store.tracker.settings.enabled(cid):
            return None
        active, _ = store.responses.variants_by_response(cid, sid).get(rid, (None, []))
    except Exception as exc:  # noqa: BLE001 -- see `mark`
        log.warning("tracker: could not resolve response %s in %s/%s -- %s", rid, cid, sid, exc)
        return None
    return mark(cid, sid, store.tracker.paths.response_key(rid, active)) if active else None


def start(app, cid: str, sid: str, marked: _fences.Mark, client: LLMClient,
          identity: str | None = None, *, flag_later: bool = False,
          trust_base: bool = False) -> None:
    """The second half: start the run for what `mark` returned. Never raises.

    Takes no campaign lock on its success path (`reserve_background` reads the
    identity rather than ensuring it), so a turn calls it AFTER its terminal
    frames without anything left to wait on. Only a failure takes the lock, to
    mark the record `failed`: once `mark` has said `pending`, every way this
    can go wrong must say so rather than leave the record waiting on a run
    that does not exist.

    **Must be called from a worker thread, never the event loop:** reserving a
    run builds its handshake events through the lifespan's `BlockingPortal`,
    which raises from the loop thread.

    `identity` is the scene the key was marked on, when the caller knows it (a
    turn does: its fenced identity). A reservation made against a different
    scene -- the `sid` deleted and reissued in between -- is released, and the
    record is failed where it was marked rather than looked for in a stranger.

    `flag_later` is a Retry's: the record had no usable result, so every later
    record was built without this post's contribution and is stale once it
    lands (`_commit`). `trust_base` is the first key of an explicit re-run
    from here: the person chose that starting point with its base record's
    warning in view, so a stale base does not make the result stale
    (`_prepare`) -- a base that is not its predecessor's final state still
    does (`Base`)."""
    key, gen = marked
    try:
        run = runs.reserve_background(app, cid, sid, "tracker-update")
    except Exception as exc:  # noqa: BLE001 -- fail-soft by contract
        log.warning("tracker: could not reserve %s for %s/%s -- %s", key, cid, sid, exc)
        run = None
    if run is None or not run.scene_identity:
        # A store mid-move or a scene that vanished: no run will ever settle
        # this record, so it must not sit at `pending` for good.
        _persistence._fail_by_sid(cid, sid, key, "the update could not be scheduled", gen)
        return
    ident = run.scene_identity
    if identity is not None and ident != identity:
        runs.release_before_start(app, run, "failed",
                                  {"kind": "scene_replaced", "detail": "the scene was replaced"})
        _persistence._fail(cid, identity, sid, key, "the scene was replaced before the update started", gen)
        return
    try:
        runs.start_computing(app, run, lambda: _update_run._update(app, cid, sid, key, gen, client, ident,
                                                       flag_later, trust_base))
    except Exception as exc:  # noqa: BLE001 -- `scenes._start_background`'s reason
        # `runner.start` raises with no lifespan running, or when shutdown
        # closed the portal between the reservation and the handoff. A run
        # reserved and never entered stays `running` for the life of the
        # process (and keeps `PUT /config/data-dir` refused), so it is released
        # here, and the record that would otherwise wait on it is failed.
        log.warning("tracker: could not start %s for %s/%s -- %s", key, cid, sid, exc)
        runs.release_before_start(app, run, "failed",
                                  {"kind": "run_failed", "detail": str(exc)})
        _persistence._fail(cid, ident, sid, key, str(exc) or "the update could not be started", gen)


def schedule(app, cid: str, sid: str, key: str, client: LLMClient, *,
             flag_later: bool = False, trust_base: bool = False) -> None:
    """`mark` then `start`, for a caller with no write hold of its own to mark
    inside -- a user action (Retry, re-run from here) or an opening. Never
    raises. A turn uses the two halves instead (see `mark`)."""
    marked = mark(cid, sid, key)
    if marked:
        start(app, cid, sid, marked, client, flag_later=flag_later, trust_base=trust_base)


def schedule_response(app, cid: str, sid: str, rid: str, client: LLMClient) -> None:
    """`schedule` for a response's active variant. Never raises."""
    marked = mark_response(cid, sid, rid)
    if marked:
        start(app, cid, sid, marked, client)


def schedule_untracked(app, cid: str, sid: str, client: LLMClient) -> None:
    """`schedule` every tracked post that has no record yet, in transcript order.

    Only for a scene that was EMPTY before the call (an adopted opener, a
    greeting start), where every post on it is new. Anywhere else "no record"
    may be a record a cut or a prune removed on purpose, and recomputing a whole
    scene's worth of posts is not something to do as a side effect."""
    try:
        if not store.tracker.settings.enabled(cid):
            return
        ident = store.scenes.ensure_identity(cid, sid)
        have = store.tracker.records.read_index(cid, ident)
        keys = [k for _, k in store.tracker.walk.ordered_keys(cid, sid) if k not in have]
    except Exception as exc:  # noqa: BLE001 -- see `schedule`
        log.warning("tracker: could not list untracked posts in %s/%s -- %s", cid, sid, exc)
        return
    for key in keys:
        schedule(app, cid, sid, key, client)

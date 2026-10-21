"""tracker: persistence responsibilities."""

from __future__ import annotations

import logging

from ... import store
from . import _fences, _transcript
from ._router import router as router

log = logging.getLogger('grimoire.routes.tracker')


def _prepare(cid: str, identity: str, hint: str, key: str, gen: int | None = None,
             trust_base: bool = False) -> dict | None:
    """Everything the update call needs, read under one campaign-lock hold so it
    describes one transcript. `None` when there is nothing to do -- the scene
    or the post is gone, the tracker was switched off while this waited, or the
    run is obsolete (`Mark`) -- and the record has been settled accordingly.

    `stale_base`: the record this one is built on (the latest `ok` record
    before the post) carries a staleness flag of its own, so the result will
    be built on a state the transcript no longer stands behind -- `_commit`
    raises `upstream_changed` on it. Not for `trust_base` (the first key of an
    explicit re-run, started from that base on purpose).

    `stale_base` also when a post between that base and this one still has an
    update `pending`, `trust_base` or not: the base is then not its
    predecessor's final state, and the result lacks whatever that update
    changes (`_base_unsettled`). And `base` is the base's fingerprint, which
    `_commit` takes again (`_base_mark`)."""
    walk = store.tracker.walk
    records = store.tracker.records
    with store.locks.campaign_lock(cid):
        sid = _fences._current_sid(cid, hint, identity)
        if sid is None:
            return None         # deleted: `delete_scene` already dropped its records
        if _fences._obsolete(cid, identity, key, gen):
            # A newer run for this key was marked, or a person edited the
            # record, after this one was. The newer run settles the record; an
            # edit already did. Either way this one has nothing to add.
            return None
        if not store.tracker.settings.enabled(cid):
            # Switched off after this was scheduled. Turning it off is a choice
            # not to pay for updates, including the ones still queued.
            _settle_unwritten(cid, sid, identity, key,
                              "the tracker was switched off before this update ran")
            return None
        located = _transcript._locate(cid, sid, key)
        if located is None:
            _settle_unwritten(cid, sid, identity, key, "")
            return None
        if walk.key_excluded(cid, sid, key):
            # Marked before the post was hidden: nothing reads its state
            # (`walk._latest_ok`), so the call would be paid for nothing.
            _settle_unwritten(cid, sid, identity, key,
                              "the post was hidden from context before this update ran")
            return None
        index = located["index"]
        base_key, prior = walk.state_before(cid, sid, index)
        # Read in the hold that reads the post and its prior: the flags this
        # run answers are the ones raised up to here. Nothing is cleared now --
        # a run that fails or dies answered nothing -- and `_commit` clears
        # them only if none went up while the model answered.
        entries = records.read_index(cid, identity)
        seen = records.flag_seq(entries.get(key))
        base_flags = ((entries.get(base_key) or {}).get("flags") or {}) if base_key else {}
        stale_base = ((not trust_base and any(base_flags.get(f) for f in records.FLAGS))
                      or _fences._base_unsettled(cid, sid, index, base_key, entries))
        own = records.read_snapshot(cid, identity, key)
        present = walk.present_at(cid, sid, index)
        return {
            "sid": sid, "post": located["post"], "rid": located["rid"],
            "fields": store.tracker.fields.effective(cid, sid),
            "prior": prior, "roster": walk.roster(cid, sid), "present": present,
            "newcomers": [store.tracker.prompt.newcomer(cid, ref)
                          for ref in sorted(present) if ref not in prior],
            "context_posts": _transcript._context_posts(cid, sid, located["first"]),
            "post_msg": located["post_msg"],
            "seen_seq": seen,
            "stale_base": stale_base,
            "base": _fences._base_mark(base_key, entries),
            "own": own["snapshot"] if own else None,
            # The pairs this post's own record changed, and those a person
            # touched there without moving a value (an awareness-only edit is
            # in no change list) -- what was set AT it, as opposed to what it
            # inherited (`merge.keep_user_values`). Read in this hold, with
            # the snapshot it describes; the index is normalised on read, so
            # every change is a `[ref, field, value]`.
            "set_here": ({(c[0], c[1]) for c in (entries.get(key) or {}).get("changed") or []}
                         | {(p[0], p[1]) for p in records.pairs(
                             (entries.get(key) or {}).get(records.TOUCHED))}),
        }


def _settle_unwritten(cid: str, sid: str, identity: str, key: str, reason: str) -> None:
    """A `pending` record no result will reach. Called under the campaign lock.

    A post that is gone has no record to keep, so its entry (and any older
    snapshot) is discarded -- left behind, it would read as `pending` forever.
    A post still there is marked `failed` with `reason`, which keeps any older
    snapshot file and offers Retry."""
    if _transcript._locate(cid, sid, key) is None:
        store.tracker.records.discard(cid, identity, [key])
    else:
        store.tracker.records.mark_failed(cid, identity, key, reason)
    # A detached run writes after the activity middleware stamped its request,
    # so it stamps for itself (CLAUDE.md, the write token).
    store.revision.bump(cid)


def _commit(cid: str, identity: str, hint: str, key: str, snapshot: dict, *,
            changed: list, digest: str, model: str, flag_later: bool = False,
            seen_seq: int = 0, gen: int | None = None, stale_base: bool = False,
            touched: list | None = None, base: _fences.Base | None = None) -> bool:
    """Store the result, unless the post went away while the model answered.

    RE-CHECKED under the lock that covers the write, because the check in
    `_prepare` is a provider call old: a cut, a retcon or a failed turn's
    take-back may have removed the post since, and a record written for it
    would be a snapshot of something that no longer happened (and, keyed by an
    id rather than an index, would never be overwritten). A rename is not a
    reason to drop it -- the identity finds the scene under its new id.

    `flag_later` (a Retry of a record that had no result): every later record
    on the walk was built without this one, so each gets `upstream_changed` --
    in the same hold, so no reader sees the new result beside unflagged
    successors. Only when the key is ON the walk: an inactive variant's record
    is not what any later record was built on.

    `gen` is re-checked here too (`Mark`): a newer run marked, or a hand edit
    made, while the model answered means this result is not the one to keep.
    `stale_base` (`_prepare`) raises `upstream_changed` in the same write, and
    so does a `base` that has moved since `_prepare` read it (`Base`).
    `touched`: the pairs `merge.keep_user_values` restored, which stay the
    person's (`records.TOUCHED`) so the next re-run restores them too."""
    with store.locks.campaign_lock(cid):
        sid = _fences._current_sid(cid, hint, identity)
        if sid is None:
            # Deleted while the model answered. `delete_scene` dropped the
            # whole directory; writing now would resurrect it as an orphan.
            return False
        if _fences._obsolete(cid, identity, key, gen):
            return False
        located = _transcript._locate(cid, sid, key)
        if located is None:
            _settle_unwritten(cid, sid, identity, key, "")
            return False
        if base is not None and not stale_base:
            base_key, _ = store.tracker.walk.state_before(cid, sid, located["index"])
            entries = store.tracker.records.read_index(cid, identity)
            stale_base = _fences._base_mark(base_key, entries) != base
        # `seen_seq`: the flags are cleared only if none went up since
        # `_prepare` read this result's inputs -- one that did is a write the
        # result never saw, and the record must keep saying so.
        store.tracker.records.save(cid, identity, key, snapshot, changed=changed,
                                   fields_digest=digest, model=model, seen_seq=seen_seq,
                                   raise_upstream=stale_base, touched=touched)
        if flag_later:
            at = store.tracker.walk.index_of(cid, sid, key)
            if at is not None:
                store.tracker.walk.flag_after(cid, sid, at)
        # Inside the hold, for the write token's rule: a token minted after the
        # lock is released is one a reader can hold while this write is still
        # the newest thing in the campaign it has not seen.
        store.revision.bump(cid)
        return True


def _fail(cid: str, identity: str, hint: str, key: str, error: str,
          gen: int | None = None) -> None:
    """Mark `key` failed -- unless the run owning `gen` is obsolete, when the
    record belongs to whatever superseded it (`records.mark_failed`). Never
    raises: this runs on paths that are already reporting a failure, and a
    second one would only bury the first."""
    try:
        with store.locks.campaign_lock(cid):
            if _fences._current_sid(cid, hint, identity) is None:
                return          # deleted: nothing to mark, and nothing to resurrect
            store.tracker.records.mark_failed(cid, identity, key, error, gen)
            store.revision.bump(cid)
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        log.warning("tracker: could not mark %s failed in %s -- %s", key, cid, exc)


def _fail_by_sid(cid: str, sid: str, key: str, error: str, gen: int | None = None) -> None:
    """`_fail` for a caller that has no identity in hand -- a reservation that
    never happened. Never raises."""
    try:
        ident = store.scenes.scene_identity(cid, sid)
    except Exception as exc:  # noqa: BLE001 -- as `_fail`
        log.warning("tracker: could not mark %s failed in %s -- %s", key, cid, exc)
        return
    if ident:
        _fail(cid, ident, sid, key, error, gen)

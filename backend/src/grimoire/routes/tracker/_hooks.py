"""tracker: hooks responsibilities."""

from __future__ import annotations

import logging

from ... import store
from ._router import router as router

log = logging.getLogger('grimoire.routes.tracker')


# --- transcript hooks -----------------------------------------------------------
#
# Called by the routes that change a transcript's shape, after their store
# write and inside the hold that covers it, so the records move with the
# transcript rather than after another request could have read it. None of
# them raises: the write they follow is the user's and has landed, and stale
# tracker bookkeeping is worth a log line, not a failed edit.

def _soft(what: str, cid: str, sid: str, fn, *args) -> None:
    try:
        fn(cid, sid, *args)
    except Exception as exc:  # noqa: BLE001 -- see the section comment
        log.warning("tracker: could not %s in %s/%s -- %s", what, cid, sid, exc)


def after_text_edit(cid: str, sid: str, index: int) -> None:
    """The post at `index` was rewritten (an edit or a retcon): its record no
    longer matches its text, and every later one was built on it."""
    _soft("flag an edit", cid, sid, store.tracker.walk.flag_edited, index)


def after_cut(cid: str, sid: str, index: int | None = None) -> None:
    """Posts left the transcript: drop their records. `index`, when known, is
    the first position the removal renumbered -- every record from there on
    was built on something that is gone, so it is flagged."""
    _soft("prune records", cid, sid, store.tracker.walk.prune)
    if index is not None:
        _soft("flag later records", cid, sid, store.tracker.walk.flag_after, index - 1)


def after_take_back(cid: str, identity: str, key: str) -> None:
    """A failed turn took its unanswered player post `key` back off the
    transcript (`scenes._take_the_post_back`): its record goes with it.

    Whichever finished first. An update that already landed wrote a record for
    a post that is now gone -- keyed by an id nothing will show again, so it
    would sit hidden until some later cut happened to prune it. An update still
    queued or answering is made obsolete by the same discard: its mark's
    generation no longer matches an entry that is not there (`_obsolete`), so
    it neither prepares nor commits. Called inside the hold that removed the
    post, by identity because that is what the turn was fenced on. Never
    raises: the removal it follows has landed."""
    try:
        store.tracker.records.discard(cid, identity, [key])
    except Exception as exc:  # noqa: BLE001 -- see the section comment
        log.warning("tracker: could not discard %s in %s -- %s", key, cid, exc)


def after_swipe(cid: str, sid: str, rid: str) -> None:
    """Response `rid`'s active variant changed. Its own record is the
    variant's cached one (nothing re-runs), but every later record was built
    on the variant that was active before."""
    def flag(c: str, s: str) -> None:
        messages = store.scenes.read_scene(c, s)["messages"]
        parts = [i for i, m in enumerate(messages) if m.get("response_id") == rid]
        if parts:
            store.tracker.walk.flag_after(c, s, parts[-1])
    _soft("flag a swipe", cid, sid, flag)

"""Which sends are still in the transcript, durably enough to outlive a run.

The run registry answers "did my turn land?" for `REAP_SECONDS` and not one
second longer, and it is in memory, so a restart answers nothing at all. That
is fine for everything except one case, and the case is the whole of #95: a
turn that FAILED after the player's post was appended, whose rollback took the
post back off (`_chat_stream.on_error`, `post_returned: true`), recovered after
the record expired.

Then the refetched transcript is *correctly* missing the post, and "the post is
absent" means both "it was rolled back" and "it never landed". The client is
holding the only surviving copy of what the player typed, and has to decide
whether to give it back. Matching the text against the transcript cannot decide
it -- a player who repeats themselves matches an earlier turn, and a landed turn
ends with narration rather than the post -- because text is not an identifier.

So the attempt id is recorded here when the post is appended and cleared when
it is taken back, and recovery asks the question that is actually decisive: is
attempt X's post still in this scene?

Keyed by the scene's IDENTITY, not its `sid`: a rename moves the id, and this
record has to survive one. Per campaign rather than per scene so a scene that
is deleted takes its entries with it when the campaign is read next, without a
sidecar to keep in step with the file.
"""

from __future__ import annotations

import json

from . import atomic, locks
from .campaigns import paths as campaigns_paths
from .paths import now_iso

RETAIN = 500
"""How many entries a campaign keeps, oldest dropped first.

Bounded by count rather than age because the question is only ever asked about
a send the client still remembers making, and a client that has been away long
enough to fall off a 500-entry list has been away long enough that its held
text is gone too. Generous next to the handful that can plausibly be
outstanding, small enough that the file stays a few tens of kilobytes.
"""


def _path(cid: str):
    return campaigns_paths.campaign_root(cid) / "attempts.json"


def _read(cid: str) -> dict:
    p = _path(cid)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A missing file is the ordinary case, and a corrupt one must not stop
        # a turn: the worst this costs is a recovery that keeps the player's
        # text when it could have settled, which is the safe direction.
        return {}
    return data if isinstance(data, dict) else {}


def _write(cid: str, data: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True))


def _key(identity: str, attempt: str) -> str:
    return f"{identity}:{attempt}"


def remember(cid: str, identity: str | None, attempt: str | None) -> None:
    """Record that this attempt's post is in the transcript.

    MUST be called under the campaign lock, in the same hold as the append it
    describes: a record written after the lock is released can be read by a
    recovery that runs between the two, and it would claim a post that is not
    there yet.
    """
    if not identity or not attempt:
        return
    # Taken here as well as by the caller, and the lock is reentrant so that
    # costs a recursive acquire. The caller's hold is what brackets this with
    # the append; this one is what keeps two campaigns' worth of concurrent
    # sends from losing one of two read-modify-writes of the same file.
    with locks.campaign_lock(cid):
        data = _read(cid)
        data[_key(identity, attempt)] = now_iso()
        for stale in sorted(data, key=lambda k: data[k])[:max(0, len(data) - RETAIN)]:
            del data[stale]
        _write(cid, data)


def forget(cid: str, identity: str | None, attempt: str | None) -> None:
    """Record that this attempt's post is no longer in the transcript.

    Part of the rollback, in the same lock hold that removes the post, and
    BEFORE it -- see `routes.scenes._take_the_post_back` for why that order is
    the fail-safe one. The lock makes the pair atomic for a concurrent reader
    but not for a process that exits between two files, so the surviving
    inconsistency has to be the one that costs a visible duplicate rather than
    the one that costs the player's words.
    """
    if not identity or not attempt:
        return
    with locks.campaign_lock(cid):     # see `remember`
        data = _read(cid)
        if data.pop(_key(identity, attempt), None) is not None:
            _write(cid, data)


def retained(cid: str, identity: str | None, attempt: str | None) -> bool:
    """Whether this attempt's post is still in the scene.

    False for anything unresolved -- no identity, no record, an unreadable
    file. Every one of those means "I cannot say this is durable", and the
    caller's rule is that ambiguity keeps the player's text: a wrong answer
    this way costs one duplicate they can see and delete, and the other way
    costs them their words with no trace.
    """
    if not identity or not attempt:
        return False
    return _key(identity, attempt) in _read(cid)

"""The campaign-lock decorator every scene mutator wears.

Its own file rather than a corner of ``serialize.py``: the names are a
coincidence -- ``_serialized`` runs a mutation under the campaign lock, while
``serialize.py`` is transcript marshalling. It decorates functions in
``write.py``, ``moment.py``, ``lifecycle.py`` and (through them) the rest of
the package, all of which import this module; the lock it takes is reentrant,
so spreading those call sites across files changes no locking behavior.
"""

from __future__ import annotations

import functools

from .. import locks


def _serialized(fn):
    """Run a scene mutation under its campaign's lock, read included (#254).

    Every mutator below is an unlocked read-modify-write of a whole file:
    parse the frontmatter and body, change something, write it all back.
    Writer A reads v0; writer B reads v0, appends, publishes v1; A appends to
    *its* v0 and publishes — B's message is gone, with no error and no trace.
    Atomic publication (#233) does not help, because both writes are
    individually complete and well-formed; that is precisely why nothing
    notices. `append_message` runs on every chat message, and a scene
    transcript is the one piece of user data in this app that cannot be
    regenerated.

    The lock has to span the READ as well as the write — a lock around
    `atomic.write_text` alone serializes two publications of the same stale
    body, which loses exactly as much.

    Why the campaign lock rather than a per-scene one: scene writes already
    happen under it (``proposals.commit_narration`` persists a reply through a
    callback, and ``create_scene`` captures an audit baseline), so a separate
    scene lock would be a second domain nested inside this one — a lock
    ordering to get wrong for no gain.

    What that buys has to be paid for by keeping the critical sections short:
    a file read and a file write, nothing whose duration this codebase does
    not control. No LLM call is ever held across it, and the two mutators that
    need a calendar resolve it BEFORE delegating here, because that path
    executes user-authored plugin code (see `_date_hint`).

    No longer in-process only: #234 made this lock an OS file lock as well, so
    two grimoire processes running as the same OS user on one machine serialize
    here too. Two *devices* sharing a synced folder still race — no filesystem
    lock can cross them, and the sync client resolves it with conflict copies.
    So do two different OS accounts, whose lock directories differ. See
    `store/locks.py`.
    """
    @functools.wraps(fn)
    def locked(cid, *args, **kwargs):
        with locks.campaign_lock(cid):   # reentrant: nesting under a holder is fine
            return fn(cid, *args, **kwargs)
    return locked

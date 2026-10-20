"""tracker: locks responsibilities."""

from __future__ import annotations

import asyncio
import threading

from ._router import router as router

_LOCKS_GUARD = threading.Lock()


def _scene_lock(app, cid: str, identity: str) -> asyncio.Lock:
    """The one lock every update for this scene runs under.

    WHY: an update's input is the snapshot its predecessor wrote
    (`walk.state_before` reads the latest `ok` record above the post), so two
    updates for one scene running side by side would both start from the same
    prior and the later one would silently drop whatever the earlier one
    changed. The spec's rule is one at a time, in transcript order, and the
    order updates are SCHEDULED in is transcript order: a post's update is
    scheduled when the post lands. `asyncio.Lock` wakes its waiters first come,
    first served, so serialising on it keeps that order.

    An `asyncio.Lock`, not the campaign lock: the wait spans a provider call,
    and the campaign lock is held by every write in the campaign. Every
    `_update` runs on the lifespan loop, so one loop owns every lock here; they
    live on `app.state` (not module scope) for the reason the run registry does
    -- a `TestClient` builds an app per test. The `threading.Lock` only guards
    creating them: `schedule` runs on worker threads, but this is reached from
    `_update` on the loop, and a dict get-or-set is not atomic across both.

    Keyed by `(cid, identity)`, not the identity alone: a fork copies its
    source's scene files, identities included, and two campaigns' scenes
    sharing one lock would queue each other's updates for nothing.

    One PROCESS's lock: a second grimoire process on the same store runs its
    own updates beside these. What keeps that from landing a record built on
    a base the other process was replacing is `Base`, checked at commit."""
    with _LOCKS_GUARD:
        locks = getattr(app.state, "tracker_locks", None)
        if locks is None:
            locks = app.state.tracker_locks = {}
        lock = locks.get((cid, identity))
        if lock is None:
            lock = locks[(cid, identity)] = asyncio.Lock()
        return lock

"""Where a boundary's write set goes once it is closed (spec 05 section 5.3).

Every HTTP request (`main._WriteSetCollector`) and every detached run
(`runner._guarded`, `runner._guarded_thread`) collects the record paths it
published (`store.writeset`) and, at its end, closes that collection into a
frozen snapshot and hands it here. What receives it is the app's write-through
queue, `app.state.cache_warm`; an app with none (a `TestClient` that never ran
its lifespan, or a build where the queue is off) drops the snapshot, which
costs a lazy rebuild and nothing else.

A handoff never raises into its caller: it runs in a middleware's `finally`
and at a run's terminal point, and neither may turn a completed write -- or a
failure already on its way out -- into a different failure.
"""

from __future__ import annotations

import logging

import anyio

_log = logging.getLogger(__name__)


def hand_off(app, snapshot: frozenset[str]) -> None:
    """Give `snapshot` to the app's write-through queue, or drop it."""
    if not snapshot:
        return
    try:
        state = getattr(app, "state", None)
        queue = getattr(state, "cache_warm", None) if state is not None else None
        if queue is None:
            return
        queue.offer(snapshot)
    except Exception:                                      # noqa: BLE001 -- see the module docstring
        _log.debug("write-set handoff dropped", exc_info=True)


async def hand_off_async(app, snapshot: frozenset[str]) -> None:
    """`hand_off` from the event loop: off it, shielded, and only when there
    is something to give and somebody to give it to, so a request that wrote
    nothing -- or an app with no queue -- pays no thread hop."""
    state = getattr(app, "state", None)
    if not snapshot or getattr(state, "cache_warm", None) is None:
        return
    try:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(hand_off, app, snapshot)
    except Exception:                                      # noqa: BLE001 -- see the module docstring
        _log.debug("write-set handoff dropped", exc_info=True)

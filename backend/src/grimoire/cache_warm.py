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

import contextlib
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

import anyio

from .store import cache_sync, paths, vectors

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
    nothing -- or an app with no queue -- pays no thread hop.

    Also the moment a boundary's parked vector history is recorded (spec 05
    section 6.4): a turn embeds recall's candidates under its campaign lock,
    where the compiled cache takes no SQL, so their `materialized` rows wait
    until the boundary ends, off the loop and outside every lock."""
    if vectors.has_deferred():
        try:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(vectors.flush_deferred)
        except Exception:                                  # noqa: BLE001 -- see the module docstring
            _log.debug("vector history flush dropped", exc_info=True)
    state = getattr(app, "state", None)
    if not snapshot or getattr(state, "cache_warm", None) is None:
        return
    try:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(hand_off, app, snapshot)
    except Exception:                                      # noqa: BLE001 -- see the module docstring
        _log.debug("write-set handoff dropped", exc_info=True)


# -- the write-through queue (spec 05 section 5.4) ---------------------------

#: The env switch: "0" turns the queue off (the collector still runs, at the
#: cost of a set). `tests/conftest.py` sets it, as it sets the inference
#: automigrate switch, so no test sees a warm-up it did not ask for. Not a user
#: setting.
ENV = "GRIMOIRE_CACHE_WARM"

#: How long a path must go unwritten before it is warmed. Longer than one
#: logical edit -- a turn writes the player's post at send and the reply when
#: generation ends, and an editor save may write several files -- so a turn's
#: writes coalesce, and a scene being played is warmed when play pauses rather
#: than after every turn: its growing transcript is not re-derived once per
#: turn. On the order of a minute; justified structurally, tuned later.
WRITE_QUIET_S = 60.0

#: How often the worker wakes to look for quiet paths.
TICK_S = 5.0

#: Most paths the queue holds. The queue is memory an idle server keeps, so
#: past this new paths are dropped -- one log line per process -- and rebuilt
#: lazily. Structural; tuned later.
MAX_PENDING = 20_000


_WARNED: set[str] = set()
_WARNED_LOCK = threading.Lock()


def enabled() -> bool:
    return os.environ.get(ENV) != "0"


class WriteThroughQueue:
    """Paths Grimoire's own writes published, waiting to go quiet.

    On the app (`app.state.cache_warm`), never at module scope, for the run
    registry's reason: a `TestClient` builds an app per test. Not a run: it
    holds no exclusion key, creates no run record, never notifies and is
    invisible to `runs_in_flight`. Only a batch actually running holds a
    data-dir move off, through the registry's sync hold.

    Each entry is keyed by the root it was collected under and its
    store-relative path, and carries the monotonic time of its latest write.
    Paths outside the syncable record directories -- the usage ledger, logs,
    keys, top-level files, `.cache/` -- and write temps are dropped when they
    are offered, before any lookup, so the server's own bookkeeping never
    surfaces as a refusal."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._pending: dict[tuple[str, str], float] = {}
        self._clock = clock
        self.stopping = threading.Event()
        self._overflowed = False

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)

    def offer(self, snapshot: frozenset[str]) -> None:
        """Take a boundary's closed write set. Called off the event loop."""
        root = paths.home()
        keyed = [(str(root), rel) for rel in cache_sync.syncable(root, snapshot)]
        now = self._clock()
        with self._lock:
            for key in keyed:
                if key in self._pending or len(self._pending) < MAX_PENDING:
                    self._pending[key] = now
                elif not self._overflowed:
                    self._overflowed = True
                    _log.warning("write-through queue full at %d paths; later writes warm lazily",
                                 MAX_PENDING)

    def clear(self, keep_root: str | None = None) -> None:
        """Forget what was collected under any root but `keep_root` (all of it
        when None): the store root moved, and a request that finished against
        the new root in the meantime has already queued valid paths."""
        with self._lock:
            if keep_root is None:
                self._pending.clear()
            else:
                for key in [k for k in self._pending if k[0] != keep_root]:
                    del self._pending[key]

    def _take_quiet(self) -> dict[str, list[str]]:
        now = self._clock()
        with self._lock:
            # At most one batch's worth: `sync_paths` refuses past MAX_PATHS,
            # and a write batch is quiet about refusals, so a larger take
            # would lose the rest. They stay queued for the next pass.
            ready = [key for key, at in self._pending.items()
                     if now - at >= WRITE_QUIET_S][:cache_sync.MAX_PATHS]
            for key in ready:
                del self._pending[key]
        grouped: dict[str, list[str]] = {}
        for root, rel in ready:
            grouped.setdefault(root, []).append(rel)
        return grouped

    def _requeue(self, root: str, rels: list[str]) -> None:
        """Give a batch's paths back, already quiet, unless newer writes came."""
        at = self._clock() - WRITE_QUIET_S
        with self._lock:
            for rel in rels:
                self._pending.setdefault((root, rel), at)

    def _warn_once(self, kind: str) -> None:
        """One log line per failure kind per PROCESS (spec 05 section 5.4),
        not per app: a test suite or a reload builds several."""
        with _WARNED_LOCK:
            if kind in _WARNED:
                return
            _WARNED.add(kind)
        _log.warning("write-through warm-up failed: %s", kind)

    def drain(self, app) -> cache_sync.SyncReport | None:
        """One pass, synchronously: warm every path that has gone quiet.

        Entries collected under a root that is no longer the store's are
        dropped, never warmed against the new one. The batch runs in `write`
        mode under the registry's sync hold, and stops at its next check once
        `stopping` is set. A batch that could not take the sync lane, or
        found the root moving, gives its paths back. Never raises: nobody is
        waiting for a warm-up, and one that fails costs a lazy rebuild."""
        try:
            grouped = self._take_quiet()
            current = str(paths.home())
            rels = sorted(grouped.get(current, ()))
            if not rels or self.stopping.is_set():
                return None
            registry = getattr(app.state, "runs", None)
            with registry.sync_hold() if registry is not None else contextlib.nullcontext(True) as held:
                if not held:                   # the root is moving: try again later
                    self._requeue(current, rels)
                    return None
                report = cache_sync.sync_paths(rels, mode="write", root=Path(current),
                                               stop=self.stopping.is_set)
            if report.stopped == "lane_busy":
                self._requeue(current, rels)
            for item in report.paths:
                for outcome in item.kinds.values():
                    if outcome.startswith("failed"):
                        self._warn_once(outcome)
            return report
        except Exception as exc:                          # noqa: BLE001 -- see the docstring
            self._warn_once(type(exc).__name__)
            return None


async def _worker(app, queue: WriteThroughQueue) -> None:
    """Wake on a timer and drain, forever. Each pass runs in a worker thread,
    awaited SHIELDED: a cancelled awaiter must not abandon a thread that is
    still writing under a root. Shutdown sets `stopping` first (`stop`), so
    a running batch reaches its next check and returns."""
    while not queue.stopping.is_set():
        await anyio.sleep(TICK_S)
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(queue.drain, app)


def install(app, tg) -> WriteThroughQueue | None:
    """Put a queue on the app and its worker in the lifespan's task group.
    Nothing when `GRIMOIRE_CACHE_WARM` is "0"."""
    if not enabled():
        app.state.cache_warm = None
        return None
    queue = WriteThroughQueue()
    app.state.cache_warm = queue
    tg.start_soon(_worker, app, queue)
    return queue


def stop(app) -> None:
    """Lifespan shutdown, before the task group is cancelled: a running batch
    -- the queue's, or an API `cache-sync` run's -- stops at its next check.
    Pending paths are lost and rebuilt lazily."""
    state = getattr(app, "state", None)
    flag = getattr(getattr(state, "cache_warm", None), "stopping", None)
    if flag is not None:
        flag.set()
    registry = getattr(state, "runs", None)
    if registry is not None:
        for run in registry.live_of_class("background"):
            if run.kind == CACHE_SYNC_KIND:
                run.cancel_requested = True


#: `routes.runs.CACHE_SYNC_KIND`, spelled here because the run machinery
#: imports this module and the registry module imports the run machinery.
CACHE_SYNC_KIND = "cache-sync"


def clear(app) -> None:
    """The store root moved: drop every pending path (`PUT /config/data-dir`)."""
    queue = getattr(getattr(app, "state", None), "cache_warm", None)
    if isinstance(queue, WriteThroughQueue):
        queue.clear(keep_root=str(paths.home()))

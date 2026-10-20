"""One warm-up lane, deduplicated warnings and root/cancellation signals."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

from .. import paths

_log = logging.getLogger('grimoire.store.cache_sync')

#: How long a write-through batch waits for the sync lane before giving its
#: paths back to the queue. An explicit batch waits as long as it takes.
LANE_WAIT_S = 0.5
#: How often an explicit batch waiting for the lane asks its stop signal.
LANE_POLL_S = 0.5
_lane = threading.Lock()
_warned: set[str] = set()
_warned_lock = threading.Lock()


def _warn_once(kind: str) -> None:
    with _warned_lock:
        if kind in _warned:
            return
        _warned.add(kind)
    _log.warning("cache sync degraded: %s", kind)


#: The network phase's own stop: the root moved under an in-flight request.
_MOVED = "root_moved"


def _root_moved(root: Path) -> bool:
    return _norm(os.path.abspath(paths.home())) != _norm(os.path.abspath(root))


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _halted(root: Path, stop: Callable[[], bool]) -> str | None:
    try:
        if stop():
            return "cancelled"
    except Exception:                                     # noqa: BLE001 -- a broken stop is a stop
        return "cancelled"
    if _norm(os.path.abspath(paths.home())) != _norm(os.path.abspath(root)):
        return "root_moved"
    return None


def _root_source(root: Path) -> str:
    try:
        info = paths.data_dir_info()
    except Exception:                                     # noqa: BLE001 -- a label, not a decision
        return "unknown"
    if _norm(info.get("data_dir", "")) != _norm(os.path.abspath(root)):
        return "explicit"
    return str(info.get("source", "unknown"))


def _take_lane(root: Path, mode: str, stop: Callable[[], bool]) -> str | None:
    """Acquire the sync lane, or say why this batch gave up before acquiring it."""
    if mode == "explicit":
        # Waits for a write-through batch on the lane, asking the stop signal
        # between short waits, so a cancel or a moved root is heard here too.
        while not _lane.acquire(timeout=LANE_POLL_S):
            if (halt := _halted(root, stop)) is not None:
                return halt
    elif not _lane.acquire(timeout=LANE_WAIT_S):
        return "lane_busy"
    return None

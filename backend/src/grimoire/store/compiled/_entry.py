"""Public request and startup adapters over the owned runtime."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Iterator
from pathlib import Path

from .. import paths
from . import _batch, _runtime, _sqlite_support


def open_library(root: Path | None = None) -> None:
    """Act on what other devices published, at this open of the library.

    Spec 03 section 12 has another device purge its own file "at its next
    open", and a batch is what did that -- but only a consumer opens a batch,
    and a library with nothing to count may never open one. This opens one
    with nothing to read: pending deletes finish, this device's files are
    purged to the marker, and the lease renews, which runs the retention
    sweep. A library with no `.cache/compiled` has nothing published and is
    left exactly as it is, so this never creates the store. Off the event
    loop; never raises."""
    _runtime._process_state()
    try:
        root = Path(root) if root is not None else paths.home()
        if not _runtime.published(root):
            return
        with batch(root) as request:
            request._usable()
    except _sqlite_support._CACHE_ERRORS:
        _runtime._warn_once('open')


@contextlib.contextmanager
def batch(root: Path | None = None, *,
          keep: Callable[[], bool] | None = None) -> Iterator[_batch.Batch]:
    _runtime._process_state()
    # Resolve the authoritative root inside the lifecycle identity, not before
    # it. A slow pointer read crossing a switch cannot adopt the new epoch for
    # the old root (independent brutal review, Spec03 S1).
    identity = (os.getpid(), _runtime._state.epoch, bool(_runtime._state.transitions))
    request = _batch.Batch(Path(root) if root is not None else paths.home(), identity=identity)
    request._maintenance()
    try:
        yield request
    except BaseException:
        request._discard_staging()
        raise
    else:
        if keep is None:
            request.flush()
        else:
            request.flush(keep=keep)

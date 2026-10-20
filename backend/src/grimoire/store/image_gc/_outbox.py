"""Root/cancel guarded recycle-bin calls outside store locks and restart drain."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path

from .. import recycle_bin
from . import _contracts, _paths


class _BinGuard:
    """`recycle_bin.send_batch`'s `before`: ahead of each bin call, the live
    root is still the pinned one and the file is a regular file directly in
    this device's outbox, reached through no link. Records each file's size
    (before it is sent) and every file it was asked about, so a stop it raises
    still reports what the bin already took: every file before the last."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.outbox = _paths.outbox_dir(root)
        self.seen: list[Path] = []
        self.sizes: dict[Path, int] = {}

    def __call__(self, path: Path) -> None:
        self.seen.append(path)
        _paths._check_root(self.root)
        if Path(path).parent != self.outbox:
            raise _contracts._UnsafePathError(Path(path))
        if not _paths._real_dirs(self.root, self.outbox, create=False):
            return                 # gone: the bin refuses it, and the run stops
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(st.st_mode):
            raise _contracts._UnsafePathError(Path(path))
        self.sizes[path] = st.st_size


def _send(root: Path, report: dict, files: list[Path],
          origin: dict[Path, tuple[str | None, str | None]],
          cancel: Callable[[], bool]) -> str | None:
    """Send outbox `files` to the bin, never under a lock, asking `cancel`
    before each. Every file the bin took is in `recycled`; a stop returns its
    state (`RECYCLE_BIN_UNVERIFIED`, which outranks the others because it is
    the one the reader must act on, then `TRASH_UNAVAILABLE` or `CANCELLED`)
    and leaves the rest waiting in the outbox. A root change or an unsafe path
    raises, as anywhere else in a collection."""
    guard = _BinGuard(root)
    try:
        result = recycle_bin.send_batch(root, files, before=guard, cancel=cancel)
    except (_contracts._RootChangedError, _contracts._UnsafePathError):
        _recycled(report, guard.seen[:-1], guard.sizes, origin)
        raise
    _recycled(report, result.sent, guard.sizes, origin)
    if result.refused is not None:
        report["error"] = result.error or result.code
    if result.unverified:
        report["unverified"] += [p.name for p in result.unverified]
        return _contracts.RECYCLE_BIN_UNVERIFIED
    if result.refused is not None:
        return _contracts.TRASH_UNAVAILABLE
    return _contracts.CANCELLED if result.cancelled else None


def _recycled(report: dict, sent: list[Path], sizes: dict[Path, int],
              origin: dict[Path, tuple[str | None, str | None]]) -> None:
    for path in sent:
        image_id, key = origin.get(path, (None, None))
        report["recycled"].append({"id": image_id, "blob": key, "name": path.name,
                                   "bytes": sizes.get(path, 0)})


def _drain(root: Path, report: dict, cancel: Callable[[], bool]) -> str | None:
    """Send whatever already waits in this device's outbox -- a crash, a
    refusal or a cancel left it there -- before anything more is collected."""
    files = _paths._outbox_files(root)
    return _send(root, report, files, {}, cancel) if files else None

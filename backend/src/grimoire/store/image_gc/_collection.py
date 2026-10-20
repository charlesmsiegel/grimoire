"""Whole token-checked disposal and bounded ingest/GC batches, with under-stripe rechecks and deferred bin flush."""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import image_refs, image_store, locks, maintenance_reports, recycle_bin
from . import _contracts, _objects, _outbox, _paths, _reports, _roots, _tokens

# ---- collect ------------------------------------------------------------------------

def collect(root: Path, token: str, *, cancel: Callable[[], bool],
            run_id: str | None = None, now: float | None = None) -> dict:
    """Dispose of what the dry run that issued `token` found collectable, and
    nothing else (module docstring): pictures to the recycle bin through this
    device's outbox, the rest deleted. Refused, with the token unspent, where
    `recycle_bin.check` finds no bin. The token is consumed even when the
    collection then stops; a blocked walk, a root change, a cancel or a bin
    that refuses leaves the rest for the next scan."""
    root = Path(root)
    run_id = _reports._run_id(root, run_id)
    at = time.time() if now is None else now
    report = _reports._new_report("collect", run_id, at, None)
    try:
        report["state"] = _collect(root, token, report, cancel, at)
    except BaseException as exc:
        report["state"], report["error"] = _contracts.FAILED, type(exc).__name__
        raise
    finally:
        _reports._finish(root, run_id, report)
    return report


@dataclass
class _Run:
    root: Path
    report: dict
    roots: _roots._Roots
    side: _objects._Sidecars
    grace: float
    now: float
    #: `_unarrived`, recomputed after each refresh under the lock.
    unarrived: list[str] = field(default_factory=list)
    #: Outbox files this batch moved, sent to the bin once the lock is released.
    pending: list[Path] = field(default_factory=list)
    #: Each moved file's object id (None for an orphan blob) and blob key.
    origin: dict[Path, tuple[str | None, str | None]] = field(default_factory=dict)

    def skip(self, image_id: str | None, blob: str | None, reason: str) -> None:
        self.report["skipped"].append({"id": image_id, "blob": blob, "reason": reason})


def _collect(root: Path, token: str, report: dict, cancel: Callable[[], bool],
             at: float) -> str:
    availability = recycle_bin.check(root)
    report["recycle_bin"] = availability.to_dict()
    try:
        _paths._check_root(root)
    except _contracts._RootChangedError:
        return _contracts.ROOT_CHANGED
    if not availability.ok:
        report["error"] = "no_recycle_bin"      # before the token: it stays good
        return _contracts.REFUSED
    device = maintenance_reports.device_key(root)
    rec, why = _tokens._read_token(root, token, device, at)
    if rec is None or not _tokens._claim_token(root, token, rec):
        report["error"] = why or "unknown or already used"
        return _contracts.REFUSED
    report["token"] = token
    report["grace_hours"] = rec["grace_hours"]
    try:
        if (stop := _outbox._drain(root, report, cancel)) is not None:
            return stop                 # refused or cancelled: nothing more leaves
        return _walk_and_collect(root, report, rec, cancel, at)
    except _contracts._RootChangedError:
        return _contracts.ROOT_CHANGED
    except _contracts._UnsafePathError as exc:
        report["blocking"].append({"path": _paths._rel(root, exc.path), "reason": "unsafe-path"})
        return _contracts.BLOCKED


def _walk_and_collect(root: Path, report: dict, rec: dict, cancel: Callable[[], bool],
                      at: float) -> str:
    """A fresh strict walk, outside the lock, and then the token's candidates
    that it does not reach, in batches."""
    roots = _roots._walk_roots(root, at, cancel)
    if roots is None:
        return _contracts.CANCELLED
    report["staging"] = roots.staging
    side = _objects._list_objects(root)
    for path, why in [*_roots._store_blocks(root), *((p, image_refs.SYMLINK) for p in side.links)]:
        roots.block(root, path, why)
    if roots.blocking:
        report["blocking"] = roots.blocking
        return _contracts.BLOCKED
    run = _Run(root, report, roots, side, rec["grace_hours"] * 3600.0, at)
    queue: list[tuple[str, str]] = []
    for image_id in rec["ids"]:
        if image_id in roots.reachable:
            run.skip(image_id, None, "reachable")
        else:
            queue.append(("object", image_id))
    queue += [("blob", key) for key in rec["blobs"]]
    return _batches(run, queue, cancel)


def _batches(run: _Run, queue: list[tuple[str, str]], cancel: Callable[[], bool]) -> str:
    """Collect in batches, each one hold of the ingest/GC lock (M15), and
    send each batch's moved pictures to the bin once that hold is released.
    A cancel or a root change sends nothing more -- the bin calls ask too,
    before each file: what moved and was not sent waits in the outbox for the
    next collection's drain."""
    while queue:
        if cancel():
            return _contracts.CANCELLED
        with locks.image_ingest_gc_lock():
            _paths._check_root(run.root)
            run.side = _objects._list_objects(run.root, run.side)   # the refcount, under the lock
            if run.side.links:
                raise _contracts._UnsafePathError(run.side.links[0])
            run.unarrived = _objects._unarrived(run.roots, run.side)
            run.report["unarrived_objects"] = run.unarrived
            started, done = time.monotonic(), 0
            while queue:
                if cancel():
                    return _contracts.CANCELLED
                kind, item = queue.pop(0)
                if kind == "object":
                    _collect_object(run, item)
                else:
                    _collect_blob(run, item)
                done += 1
                if done >= _contracts.BATCH_OBJECTS or time.monotonic() - started >= _contracts.BATCH_SECONDS:
                    break
        if (stop := _flush(run, cancel)) is not None:
            return stop
    return _flush(run, cancel) or _contracts.COMPLETE


def _flush(run: _Run, cancel: Callable[[], bool]) -> str | None:
    """Send the pictures the last batch moved to the bin (`_send`)."""
    if not run.pending:
        return None
    pending, run.pending = run.pending, []
    return _outbox._send(run.root, run.report, pending, run.origin, cancel)


def _young(run: _Run, mtimes: list[float]) -> str | None:
    if any(m > run.now for m in mtimes):
        return "clock-skew"
    if any(run.now - m < run.grace for m in mtimes):
        return "grace"
    return None


def _collect_object(run: _Run, image_id: str) -> None:
    """One object, under its stripe: re-check; skip it whole when its blob is
    held; delete only its sidecar when another sidecar names its blob; else
    move the blob to the outbox, THEN delete the sidecar (the move is the
    commit), and best effort its index entry and thumbnails. Each step is in
    the report the moment it has happened, so a run stopped between two of
    them (a root change) never under-reports."""
    root = run.root
    with locks.image_object_lock(image_id):
        _paths._check_root(root)
        path = image_store.object_path(image_id, root=root)
        obj = image_store.read_fresh(image_id, root=root)
        if obj is None:
            run.skip(image_id, None, "unreadable" if path.exists() else "gone")
            return
        if image_id in run.roots.reachable:
            run.skip(image_id, None, "reachable")
            return
        key = _paths._blob_key(obj.blob_sha256, obj.ext)
        blob = image_store.blob_path(obj.blob_sha256, obj.ext, root=root)
        try:
            mtimes = [os.lstat(path).st_mtime]
            with contextlib.suppress(FileNotFoundError):
                mtimes.append(os.lstat(blob).st_mtime)
        except OSError:
            run.skip(image_id, key, "unreadable")
            return
        if (why := _young(run, mtimes)) is not None:
            run.skip(image_id, key, why)
            return
        if (hold := _objects._orphan_hold(run.side, run.unarrived)) is not None:
            # Deleting the sidecar would lose its description and bin nothing:
            # the blob may be the one an unarrived or unreadable object names.
            run.skip(image_id, key, hold)
            return
        if run.side.refs.get(key, set()) - {path}:
            _delete_sidecar(run, path, image_id, key)
            _released(run, image_id, key, "shared")
        else:
            raw = obj.raw
            text = None if "description_conflicts" in raw else raw.get("description")
            name = _paths.bin_name(text if isinstance(text, str) else None, obj.blob_sha256, obj.ext)
            moved = _paths._move_to_outbox(root, blob, name)
            if moved is not None:
                run.pending.append(moved)
                run.origin[moved] = (image_id, key)
            _delete_sidecar(run, path, image_id, key)
            if moved is None:
                _released(run, image_id, key, "blob-missing")
            _delete_thumbnails(run, obj.blob_sha256)
        _drop_index_entry(run, obj.blob_sha256, image_id)


def _delete_sidecar(run: _Run, path: Path, image_id: str, key: str) -> None:
    freed = _paths._delete_file(run.root, path)
    run.side.drop(path)
    run.report["deleted"]["objects"].append({"id": image_id, "blob": key, "bytes": freed})
    run.report["deleted"]["bytes"] += freed


def _released(run: _Run, image_id: str, key: str, reason: str) -> None:
    run.report["released"].append({"id": image_id, "blob": key, "reason": reason})


def _delete_thumbnails(run: _Run, sha: str) -> None:
    """A blob's thumbnails, once the blob has left the store -- or was gone
    already, since nothing else will ask for them."""
    for thumb in _paths.blob_thumbnails(run.root, sha):
        _cache_delete(run, thumb)


def _cache_delete(run: _Run, path: Path) -> None:
    """Delete one derived cache entry, best effort: a thumbnail or index entry
    that will not go is reported, never a reason to stop collecting. A root
    change still stops the run (`_delete_file` deletes nothing then)."""
    try:
        _paths._delete_file(run.root, path)
    except (OSError, _contracts._UnsafePathError) as exc:
        run.report["cache_cleanup"].append({"path": _paths._rel(run.root, path),
                                            "reason": type(exc).__name__})


def _drop_index_entry(run: _Run, sha: str, image_id: str) -> None:
    """The blob-index entry, only when it names the collected object."""
    path = image_store.index_path(sha, root=run.root)
    try:
        named = path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return
    if named == image_id:
        _cache_delete(run, path)


def _collect_blob(run: _Run, key: str) -> None:
    """One orphan blob, under the ingest/GC lock (an ingest publishing it
    holds the same lock until its sidecar is written): to the outbox, with no
    description, and its thumbnails deleted."""
    root = run.root
    _paths._check_root(root)
    split = _paths._split_key(key)
    if split is None:
        return
    sha, ext = split
    if (hold := _objects._orphan_hold(run.side, run.unarrived)) is not None:
        run.skip(None, key, hold)
        return
    if run.side.refs.get(key):
        run.skip(None, key, "named")
        return
    blob = image_store.blob_path(sha, ext, root=root)
    try:
        mtime = os.lstat(blob).st_mtime
    except FileNotFoundError:
        run.skip(None, key, "gone")
        return
    if (why := _young(run, [mtime])) is not None:
        run.skip(None, key, why)
        return
    moved = _paths._move_to_outbox(root, blob, _paths.bin_name(None, sha, ext))
    if moved is None:
        run.skip(None, key, "gone")
    else:
        run.pending.append(moved)
        run.origin[moved] = (None, key)
    _delete_thumbnails(run, sha)

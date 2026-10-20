"""Dry-run reachability and grace classification, exact reclaimable accounting and token issuance."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

from .. import image_refs, maintenance_reports, recycle_bin
from . import _contracts, _objects, _paths, _reports, _roots, _tokens

# ---- scan --------------------------------------------------------------------------

def scan(root: Path, *, cancel: Callable[[], bool], run_id: str | None = None,
         now: float | None = None) -> dict:
    """The dry run (module docstring): what is collectable now, what will be
    and when, and -- when something is and nothing blocked, or something
    waits in the outbox -- a token for `collect`. Writes nothing but its report and the token. `root` is the
    pinned store root (``paths.home().resolve()`` when the run started)."""
    root = Path(root)
    run_id = _reports._run_id(root, run_id)
    at = time.time() if now is None else now
    report = _reports._new_report("scan", run_id, at, _contracts.GRACE_HOURS)
    try:
        report["state"] = _scan(root, report, cancel, at)
    except BaseException as exc:
        report["state"], report["error"] = _contracts.FAILED, type(exc).__name__
        raise
    finally:
        _reports._finish(root, run_id, report)
    return report


def _classify(report: dict, roots: _roots._Roots, side: _objects._Sidecars,
              blobs: dict[str, os.stat_result], grace: float, at: float) -> None:
    """Fill the report's candidate lists. Both halves ask `_orphan_hold`, the
    question the collection asks, so the scan offers nothing it will skip."""
    hold = _objects._orphan_hold(side, report["unarrived_objects"])
    _classify_objects(report, roots, side, blobs, grace, at, hold)
    _classify_blobs(report, side, blobs, grace, at, hold)


def _classify_objects(report: dict, roots: _roots._Roots, side: _objects._Sidecars,
                      blobs: dict[str, os.stat_result], grace: float, at: float,
                      hold: str | None) -> None:
    collectable: dict[str, Path] = {}
    for image_id, path in sorted(side.ids.items()):
        key = side.names.get(path)
        if key is None or image_id in roots.reachable:
            continue
        report["counts"]["unreferenced"] += 1
        blob = blobs.get(key)
        mtimes = [side.sigs[path][0] / 1e9, *([blob.st_mtime] if blob else [])]
        v = _contracts._judge(at, grace, mtimes)
        if v.skew:
            report["clock_skew"].append({"id": image_id, "blob": None, "what": v.skew})
        if v.why is None and hold is not None:
            report["protected"].append({"id": image_id, "blob": key, "why": hold,
                                        "collectable_at": None})
        elif v.why is None:
            collectable[image_id] = path
        else:
            report["protected"].append({"id": image_id, "blob": key, "why": v.why,
                                        "collectable_at": v.at})
    _reclaimable(report, side, blobs, collectable)


def _reclaimable(report: dict, side: _objects._Sidecars, blobs: dict[str, os.stat_result],
                 collectable: dict[str, Path]) -> None:
    """The collectable rows and their exact bytes: a sidecar always, its blob
    only when every holder of it goes too, no sidecar is unreadable and no
    reachable object is unarrived (whose blob it may be)."""
    keep_all = side.keep_all_blobs() or bool(report["unarrived_objects"])
    gone = set(collectable.values())
    counted: set[str] = set()
    for image_id, path in collectable.items():
        key = side.names[path] or ""
        size = side.sigs[path][1]
        holders = side.refs.get(key, set())
        if not keep_all and key in blobs and key not in counted and holders <= gone:
            size += blobs[key].st_size
            counted.add(key)
        report["collectable"].append({"id": image_id, "blob": key, "bytes": size})
        report["reclaimable_bytes"] += size


def _classify_blobs(report: dict, side: _objects._Sidecars, blobs: dict[str, os.stat_result],
                    grace: float, at: float, hold: str | None) -> None:
    for key, st in sorted(blobs.items()):
        if side.refs.get(key):
            continue
        v = _contracts._judge(at, grace, [st.st_mtime])
        if v.skew:
            report["clock_skew"].append({"id": None, "blob": key, "what": v.skew})
        if hold is not None:
            report["protected"].append({"id": None, "blob": key,
                                        "why": hold, "collectable_at": None})
        elif v.why is None:
            report["collectable_blobs"].append({"id": None, "blob": key, "bytes": st.st_size})
            report["reclaimable_bytes"] += st.st_size
        else:
            report["protected"].append({"id": None, "blob": key, "why": v.why,
                                        "collectable_at": v.at})


def _scan(root: Path, report: dict, cancel: Callable[[], bool], at: float) -> str:
    report["recycle_bin"] = recycle_bin.check(root).to_dict()
    try:
        _paths._check_root(root)
    except _contracts._RootChangedError:
        return _contracts.ROOT_CHANGED
    device = maintenance_reports.device_key(root)
    roots = _roots._walk_roots(root, at, cancel)
    if roots is None:
        return _contracts.CANCELLED
    report["staging"] = roots.staging
    side = _objects._list_objects(root)
    blobs = _objects._list_blobs(root, side)
    if cancel():
        return _contracts.CANCELLED
    for path, why in [*_roots._store_blocks(root), *((p, image_refs.SYMLINK) for p in side.links)]:
        roots.block(root, path, why)
    report["blocking"] = roots.blocking
    report["unreadable_sidecars"] = [_paths._rel(root, p) for p in side.unreadable()]
    report["unreadable_sidecars"] += [_paths._rel(root, p) for p in side.unlisted]
    report["unarrived_objects"] = _objects._unarrived(roots, side)
    counts = report["counts"]
    counts.update(placements=len(roots.placements), objects=len(side.ids), blobs=len(blobs))
    if roots.blocking:
        # Nothing is offered past a blocked walk, but what already waits in the
        # outbox left the store before it: a token naming no candidate lets a
        # collection drain it and then stop at the same walk.
        report["outbox"], report["foreign_outboxes"] = _paths._outbox_listing(root)
        try:
            _paths._check_root(root)
        except _contracts._RootChangedError:
            return _contracts.ROOT_CHANGED
        _tokens._issue_token(root, report, device, at)
        return _contracts.BLOCKED
    _classify(report, roots, side, blobs, _contracts.GRACE_HOURS * 3600.0, at)
    counts.update(collectable=len(report["collectable"]),
                  collectable_blobs=len(report["collectable_blobs"]),
                  protected=len(report["protected"]))
    report["outbox"], report["foreign_outboxes"] = _paths._outbox_listing(root)
    try:
        _paths._check_root(root)
    except _contracts._RootChangedError:
        _clear_candidates(report)
        return _contracts.ROOT_CHANGED
    _tokens._issue_token(root, report, device, at)
    return _contracts.COMPLETE


def _clear_candidates(report: dict) -> None:
    report["collectable"], report["collectable_blobs"], report["protected"] = [], [], []
    report["reclaimable_bytes"] = 0

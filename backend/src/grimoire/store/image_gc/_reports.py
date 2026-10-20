"""Stable logical logger and reports recorded on every scan/collection ending."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from .. import maintenance_reports
from . import _contracts, _paths

_log = logging.getLogger('grimoire.store.image_gc')


def _new_report(mode: str | None, run_id: str, now: float,
                grace_hours: float | None) -> dict:
    return {
        "kind": _contracts.KIND, "mode": mode, "run_id": run_id, "state": "running",
        "started_at": now, "finished_at": None, "grace_hours": grace_hours,
        "counts": {"placements": 0, "objects": 0, "blobs": 0, "unreferenced": 0,
                   "collectable": 0, "collectable_blobs": 0, "protected": 0},
        "collectable": [], "collectable_blobs": [], "protected": [],
        "reclaimable_bytes": 0, "blocking": [], "staging": [], "unreadable_sidecars": [],
        "clock_skew": [], "token": None, "token_expires_at": None,
        "deleted": {"objects": [], "bytes": 0},
        "recycled": [], "released": [], "outbox": [], "foreign_outboxes": 0,
        "unverified": [], "recycle_bin": {"ok": None, "code": None, "message": None},
        "skipped": [], "unarrived_objects": [],
        "cache_cleanup": [], "error": None,
    }


def failed_report(mode: str | None, run_id: str, error: str) -> dict:
    """A scan's or a collection's report when the pass raised before it could
    write its own: `_new_report`'s full shape, every count zero and every list
    empty, ended as failed. `error` is the exception's type, never its
    message."""
    now = time.time()
    report = _new_report(mode, run_id, now, None)
    report.update(state=_contracts.FAILED, error=error, finished_at=now)
    return report


def _finish(root: Path, run_id: str, report: dict) -> None:
    """Write the report, whatever the ending, with what waits in the outboxes
    at that moment. A report that cannot be written is logged, never raised
    over the error that ended the run."""
    try:
        report["outbox"], report["foreign_outboxes"] = _paths._outbox_listing(root)
    except Exception:                                        # noqa: BLE001
        _log.warning("could not list the image collection outbox", exc_info=True)
    report["finished_at"] = time.time()
    try:
        maintenance_reports.write(root, run_id, report)
    except Exception:                                        # noqa: BLE001
        _log.warning("could not write the image collection report %s", run_id,
                     exc_info=True)


def _run_id(root: Path, run_id: str | None) -> str:
    run_id = run_id or uuid.uuid4().hex
    maintenance_reports.report_path(root, run_id)     # ValueError for a bad id
    return run_id

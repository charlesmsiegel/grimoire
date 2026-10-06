"""Image-store maintenance: migrate legacy images, collect unused ones (stage 4).

``/maintenance/images/...`` -- three routes over the ``maintenance`` run class
(`runs.run_maintenance`). The two that start work answer **202** with a run and
persist what it did to ``.cache/image-store/reports/<run id>.json``; the third
reads one of those reports back.

* ``POST /maintenance/images/migrate`` -- ``{"dry_run": true}``. A missing body,
  or a body that does not say, is a DRY run: the safe reading of a bare POST is
  the one that changes nothing.
* ``POST /maintenance/images/gc`` -- ``{"dry_run": true, "token": null}``. The
  dry run is the scan; a real run is the collection, and it needs the token a
  scan issued. A delete with no token is refused with a 400 *here*, before a run
  is reserved, rather than reported as a failed run minutes of nobody's
  attention later.
* ``GET /maintenance/images/reports/{run_id}`` -- the stored report. A run id
  that is not 32 lowercase hex characters is a 400 (it names a file, so it is
  validated before it is joined to anything), and one nobody wrote, or that the
  30-day prune took, is a 404.

The handlers are ``def``, never ``async def``: reserving a run builds its
handshake events through the lifespan's portal, which raises when called from
the loop thread (CLAUDE.md, "Detached runs").

Each pass is handed the run's PINNED root (``run.root``, captured at
reservation) and asks ``run.cancel_requested`` between items. Their reports
belong to them -- the collector writes its own in a ``finally``; the migration
returns its report and the work function here persists it, also in a
``finally``, so a cancelled or failed run still leaves one.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request

from .. import store
from ..store import maintenance_reports
from . import runs
from .models import ImageGcStart, ImageMigrationStart

router = APIRouter()

MIGRATION_KIND = store.image_migration.KIND


def _pinned(run: runs.Run) -> Path:
    """The root `run_maintenance` pinned at reservation. Always set for a run
    it reserved; a missing one is a bug, and the pass must not guess at the
    live root instead."""
    if run.root is None:
        raise RuntimeError("a maintenance run has no pinned root")
    return run.root


def _migration_work(dry_run: bool):
    """The work function for a migration run: the pass, then its report."""
    def work(run: runs.Run) -> dict:
        root = _pinned(run)
        mode = "plan" if dry_run else "migrate"
        try:
            report = {
                **store.image_migration.run(
                    root, dry_run=dry_run, cancel=lambda: run.cancel_requested),
                "kind": MIGRATION_KIND, "run_id": run.id, "mode": mode}
        except BaseException as exc:
            # The pass reports its own failures and never raises, so this is a
            # bug or a shutdown. The class of the exception only: its message
            # can carry a path or a name.
            maintenance_reports.write(root, run.id, {
                **store.image_migration.failed_report(type(exc).__name__,
                                                      dry_run=dry_run),
                "kind": MIGRATION_KIND, "run_id": run.id, "mode": mode,
                "state": "failed"})
            raise
        maintenance_reports.write(root, run.id, report)
        return report
    return work


def _scan_work():
    def work(run: runs.Run) -> dict:
        return store.image_gc.scan(
            _pinned(run), cancel=lambda: run.cancel_requested, run_id=run.id)
    return work


def _collect_work(token: str):
    def work(run: runs.Run) -> dict:
        return store.image_gc.collect(
            _pinned(run), token, cancel=lambda: run.cancel_requested, run_id=run.id)
    return work


@router.post("/maintenance/images/migrate", status_code=202)
def post_images_migrate(
    request: Request, body: ImageMigrationStart | None = None,
    x_grimoire_attempt: str | None = Header(default=None),
):
    dry_run = True if body is None else body.dry_run
    return runs.run_maintenance(request.app, "migrate", x_grimoire_attempt,
                                _migration_work(dry_run),
                                mode="plan" if dry_run else "migrate")


@router.post("/maintenance/images/gc", status_code=202)
def post_images_gc(
    request: Request, body: ImageGcStart | None = None,
    x_grimoire_attempt: str | None = Header(default=None),
):
    dry_run = True if body is None else body.dry_run
    if dry_run:
        return runs.run_maintenance(request.app, "gc", x_grimoire_attempt, _scan_work(),
                                    mode="scan")
    token = (body.token if body is not None else None) or ""
    if not token.strip():
        raise HTTPException(status_code=400, detail={
            "kind": "token_required",
            "detail": "deleting unused images needs the token a dry run issued; "
                      "run Find unused images first"})
    return runs.run_maintenance(request.app, "gc", x_grimoire_attempt,
                                _collect_work(token.strip()), mode="collect")


@router.get("/maintenance/images/reports/{run_id}")
def get_images_report(run_id: str) -> dict:
    root = store.paths.home().resolve()
    try:
        report = maintenance_reports.read(root, run_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="not a run id") from None
    if report is None:
        raise HTTPException(status_code=404, detail="no such report")
    return report

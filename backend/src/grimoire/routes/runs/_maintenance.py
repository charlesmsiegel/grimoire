"""Pin one maintenance root and release process exclusion after worker completion."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable
from pathlib import Path

from fastapi import HTTPException

from ... import runner, store
from ...store import maintenance_reports, paths, proclock
from . import _contracts, _events, _execution, _maintenance_claims, _record, _subjects
from ._router import router as router


@contextlib.contextmanager
def maintenance_excluded(app):
    """Hold the store against image-store maintenance for a whole operation.

    For the routes that fork, delete or export a tree: while one holds this, a
    `maintenance` run cannot be reserved (`busy`), and while a maintenance run
    is live, this is refused with `maintenance_running` naming it -- the run's
    id is what lets the client offer to stop it. Lives in the ROUTES and the
    backup ticker, never in store modules: it is a fact about this process's
    registry, which the store does not know about. The hold also takes the same
    machine-local process lock as maintenance, so a second backend cannot run
    one concurrently; contention there answers `maintenance_elsewhere`.

    A context manager rather than a check, for `store_held_still`'s reason: a
    check made first and acted on afterwards leaves room for a migration to
    reserve in between.
    """
    try:
        with app.state.runs.exclude_maintenance():
            yield
    except _contracts.MaintenanceRunningError as exc:
        raise HTTPException(status_code=409, detail={
            "kind": "maintenance_running", "run_id": exc.run_id,
            "detail": "image-store maintenance is running; wait for it or stop "
                      "it first"}) from exc


def maintenance_live(app) -> list[_record.Run]:
    """Every live `maintenance` run in this process."""
    return app.state.runs.live_of_class("maintenance")


def run_maintenance(app, kind: str, attempt_id: str | None,
                    work_sync: Callable[[_record.Run], dict], *, mode: str | None = None) -> dict:
    """Reserve a `maintenance` run, start ``work_sync`` in a worker thread, and
    answer the 202 body.

    ``work_sync(run)`` is SYNCHRONOUS and does the whole pass: it reads
    ``run.root`` (pinned here, at reservation) for every path, checks
    ``run.cancel_requested`` between items, writes its report with
    `maintenance_reports.write` in its own ``finally`` -- so a cancelled or
    failed run still leaves one -- and returns that report, which becomes the
    run's result. ``mode`` (``plan``/``migrate``, ``scan``/``collect``) is
    what the minimal report of a pass that raised before writing its own
    says it was (`_report_failure`).

    Refusals, each as the 409 it is:

    * another maintenance run is live -- ``run_in_flight``, naming it (a
      duplicate delivery of the same attempt adopts it instead);
    * the store root is being moved, or a fork, delete or export holds the
      store -- ``busy``: both finish on their own and the same start succeeds;
    * another device's marker is still beating, or another grimoire process
      on this machine holds the store's maintenance lock --
      ``maintenance_elsewhere``.

    A ``def`` caller only: reserving builds the run's events through the
    portal, which raises when called from the loop thread.
    """
    try:
        run, fresh = app.state.runs.start_or_existing(
            _subjects.GLOBAL_SUBJECT, "maintenance", kind, attempt_id or uuid.uuid4().hex,
            None, labels=_subjects._subject_labels(_subjects.GLOBAL_SUBJECT))
    except _contracts.RunInFlightError as exc:
        raise HTTPException(status_code=409, detail={
            "kind": "run_in_flight", "run_id": exc.run_id,
            "detail": "image-store maintenance is already running"}) from exc
    except _contracts.StoreMovingError as exc:
        raise HTTPException(status_code=409, detail={
            "kind": "busy",
            "detail": "the storage location is being changed; try again"}) from exc
    except _contracts.MaintenanceHeldError as exc:
        raise HTTPException(status_code=409, detail={
            "kind": "busy",
            "detail": "a fork, delete, export or backup is using the store; "
                      "try again when it finishes"}) from exc
    if not fresh:
        return {"run": _events.run_payload(run)}
    with _execution.reservation(app, run):
        # Pinned INSIDE the reservation: a store move cannot begin while this
        # run is live (`hold_still` refuses it), so the root read here is the
        # one the run keeps for its whole life.
        root = paths.home().resolve()
        run.root = root
        lock_fd, device = _maintenance_claims._claim_maintenance(root, run.id)

        def body(r: _record.Run) -> dict:
            # The heartbeat thread runs for the WHOLE of the work, independent
            # of its item boundaries, and clears the marker (and releases the
            # process lock) when it returns.
            #
            # Its first beat comes HEARTBEAT_SECONDS after the work starts, not
            # at reservation: until then the stamp `claim_marker` wrote above
            # stands in for it. The gap is the portal handing the task to a
            # worker thread -- normally milliseconds, and in any case far inside
            # MARKER_STALE_SECONDS, so another device never sees a live run's
            # marker as stale because of it.
            with maintenance_reports.heartbeat(root, r.id, device, lock_fd=lock_fd):
                try:
                    return work_sync(r)
                except BaseException as exc:
                    _report_failure(root, r, exc, mode)
                    raise

        try:
            start_maintenance(app, run, body)
        except BaseException:
            maintenance_reports.release_marker(root, run.id, device)
            proclock.release(lock_fd)
            raise
        return {"run": _events.run_payload(run)}


def _failure_report(run: _record.Run, mode: str | None, error: str) -> dict:
    """The stored report of a pass that raised before writing its own, in the
    full shape its kind's own reports have -- every count zero, every list
    empty -- so the Settings card reading it back meets no missing field. The
    ``kind`` is the report's (``image-migration``/``image-gc``), not the
    run's (``migrate``/``gc``)."""
    if run.kind == "migrate":
        return {**store.image_migration.failed_report(error, dry_run=mode != "migrate"),
                "kind": store.image_migration.KIND, "run_id": run.id, "mode": mode,
                "state": "failed"}
    return store.image_gc.failed_report(mode, run.id, error)


def _report_failure(root: Path, run: _record.Run, exc: BaseException,
                    mode: str | None = None) -> None:
    """Leave a minimal report for a pass that raised without writing its own.

    The work's own ``finally`` is what normally writes the report; this covers
    a pass that failed before reaching it. Deliberately says nothing a store
    holds -- the class of the exception only, never its message, which can
    carry a path or a name. Fail-soft: the run is already failing, and a report
    that cannot be written must not replace that failure.
    """
    try:
        if maintenance_reports.read(root, run.id) is None:
            maintenance_reports.write(
                root, run.id, _failure_report(run, mode, type(exc).__name__))
    except Exception:                                        # noqa: BLE001
        _contracts._log.warning("could not write a failure report for maintenance run %s",
                     run.id, exc_info=True)


def start_maintenance(app, run: _record.Run, work_sync: Callable[[_record.Run], dict]) -> None:
    """Run ``work_sync(run)`` detached in a worker thread the run outlives.

    `start_computing`'s ordering, for its reason: `run.started` after the
    handoff, so a handoff that raises is still released by `reservation`.
    """
    runner.start_thread(app, run, work_sync)
    run.started = True


def reserve_cache_sync(app, attempt_id: str | None) -> tuple[_record.Run, bool]:
    """Reserve the global `cache-sync` run, or answer 409.

    One at a time and never merged: while one is live a new request is
    refused with `sync_in_flight` naming it, whatever its options, so the
    caller can poll and retry; a repeat of the live run's own attempt id is
    handed that run back. Attempt ids are only unique within a subject, and
    the global subject also holds drafts, so the id is scoped to this kind:
    a repeat of a model refresh's id never hands a sync caller that refresh.
    A `def` caller only, like every reservation."""
    try:
        return app.state.runs.start_or_existing(
            _subjects.GLOBAL_SUBJECT, "background", _contracts.CACHE_SYNC_KIND,
            f"{_contracts.CACHE_SYNC_KIND}:{attempt_id or uuid.uuid4().hex}",
            None, labels=_subjects._subject_labels(_subjects.GLOBAL_SUBJECT), refuse_live=True)
    except _contracts.RunInFlightError as exc:
        raise HTTPException(status_code=409, detail={
            "kind": "sync_in_flight", "run_id": exc.run_id,
            "detail": "a cache sync is already running; poll it, then try again"}) from exc
    except _contracts.StoreMovingError as exc:
        raise HTTPException(status_code=409, detail={
            "kind": "busy",
            "detail": "the storage location is being changed; try again"}) from exc

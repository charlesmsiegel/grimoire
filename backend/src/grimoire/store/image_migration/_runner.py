"""Whole maintenance run orchestration over the same dry-run plan and all recorded stop/failure endings."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path

from .. import image_surfaces
from . import _collection_conversion, _contracts, _execution_state, _placements, _planning


def _execute(ex: _execution_state._Exec) -> None:
    ex.guard("start")
    ex.pending = _execution_state._safe_map(ex.root)
    for item in ex.plan.items:
        if _execution_state._stopped(ex):
            return
        _execution_state._attempt(ex, item.occurrence.path, partial(_placements._item, ex, item))
    for occ, image_id in ex.plan.metadata:
        if _execution_state._stopped(ex):
            return
        _execution_state._attempt(ex, occ.dir / (occ.sidecar or ""), partial(_placements._metadata_one, ex, occ, image_id))
    _collection_conversion._collections(ex)
    if ex.out["outcome"] != _contracts.DONE or _execution_state._stopped(ex):
        return
    _execution_state._attempt(ex, None, partial(_collection_conversion._journals, ex))
    # A cancel while the journals settled is a cancelled run, so the work map
    # stays: its entries are roots until a run completes (Codex review, P2c).
    if ex.out["outcome"] != _contracts.DONE or _execution_state._stopped(ex):
        return
    ex.guard("before-map-delete")
    if not image_surfaces.linked(ex.root, _execution_state.work_map_path(ex.root)):
        _execution_state.work_map_path(ex.root).unlink(missing_ok=True)


def run(root: Path, *, dry_run: bool, cancel: Callable[[], bool] | None = None) -> dict:
    """Migrate the legacy images under store root `root` (spec §11, M7-M10);
    the report, always -- on success, on cancel and on failure alike.

    `root` is the pinned root (``paths.home().resolve()`` as the run captured
    it, M4); a live root that is not it stops the run before anything is
    written, and one that moves mid-run stops it with nothing deleted after.
    A dry run is the plan's report and writes nothing. `cancel` is asked
    between files and between steps; a cancelled run reports what it did.

    The report is the plan's §11 fields plus what the run did
    (`_run_fields`), with ``outcome`` one of ``done``, ``cancelled``,
    ``failed`` (``error`` says why) or ``root-changed`` (``stopped_at`` says
    where). A step that fails is listed under ``errors`` and the run goes on.
    Persisting the report is the caller's (M2)."""
    root = Path(root)
    asked = cancel if cancel is not None else (lambda: False)
    fields = _planning._run_fields(dry_run)
    p = _contracts.MigrationPlan(root)
    try:
        if not _execution_state._same_root(root):
            raise _contracts._RootChangedError("start")
        p = _planning.plan(root, cancel=asked)
        if p.cancelled:
            fields["outcome"] = _contracts.CANCELLED
        elif not dry_run:
            ex = _execution_state._Exec(p, asked, fields)
            try:
                _execute(ex)
            finally:
                fields["campaigns_written"] = len(ex.written)
                # A real run reports what it freed; a dry run, the plan's
                # figure, which `report(p)` carries.
                fields["bytes_reclaimed"] = ex.bytes_reclaimed()
        fields["url_subject_keys_kept"] = _collection_conversion._url_keys_kept(root)
    except _contracts._RootChangedError as exc:
        fields["outcome"], fields["stopped_at"] = _contracts.ROOT_CHANGED, exc.step
    except Exception as exc:  # noqa: BLE001 -- a failed run still reports (M1)
        fields["outcome"], fields["error"] = _contracts.FAILED, _execution_state._error_text(exc)
    return {**_planning.report(p), **fields}

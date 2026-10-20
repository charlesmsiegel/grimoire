"""Acquire machine-local maintenance ownership before publishing a run."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from ...store import maintenance_reports, proclock
from ._router import router as router


def _maintenance_lock_path(root: Path) -> Path:
    """The lock shared by maintenance and whole-tree operations (#479)."""
    # Stabilize the directory identity BEFORE deriving the lock path: a hold
    # acquired for a missing root would otherwise use proclock's path fallback,
    # then stop excluding peers as soon as the operation creates the directory.
    root.mkdir(parents=True, exist_ok=True)
    return proclock.lock_path(root, "image-maintenance", "run")


def _acquire_maintenance_lock(lock_path: Path) -> int:
    """Take the shared process lock once, or describe its uncertain owner."""
    lock_fd = proclock.acquire(lock_path, proclock.NO_WAIT)
    if lock_fd is None:
        # An advisory lock carries no owner metadata. A tree operation may hold
        # it just as maintenance does; claiming to know which would mislead the
        # reader into looking for a maintenance run that does not exist (#479).
        raise HTTPException(status_code=409, detail={
            "kind": "maintenance_elsewhere",
            "detail": "another grimoire process is using this store for "
                      "image-store maintenance or a tree operation; wait for "
                      "it to finish"})
    return lock_fd


def _claim_maintenance(root: Path, run_id: str) -> tuple[int, str]:
    """Take the process lock and the marker for a maintenance run, or answer 409.

    Returns the lock's descriptor -- held until the work returns, released in
    `heartbeat`'s `finally` -- and this device's key for the store.

    The process lock is the cross-PROCESS half of the exclusion. The registry's
    key is this process's memory and the marker refuses only another DEVICE, so
    a second backend on this machine over the same store is stopped here.
    """
    lock_fd = _acquire_maintenance_lock(_maintenance_lock_path(root))
    try:
        device = maintenance_reports.device_key(root)
        maintenance_reports.claim_marker(root, run_id, device)
    except maintenance_reports.MaintenanceElsewhereError as exc:
        proclock.release(lock_fd)
        raise HTTPException(status_code=409, detail={
            "kind": "maintenance_elsewhere",
            "detail": "image-store maintenance is running on another device "
                      "that shares this library; wait for it to finish",
            "heartbeat": exc.marker.get("heartbeat")}) from exc
    except BaseException:
        proclock.release(lock_fd)
        raise
    return lock_fd, device

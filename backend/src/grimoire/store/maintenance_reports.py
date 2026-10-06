"""What an image-store maintenance run leaves behind, and who is running one.

Two jobs, both small and both about the ``maintenance`` run class (stage 4 of
the content-addressed image store, rulings M1-M3):

* **Reports.** A migration or a collection is minutes of work whose value is
  the account of what it did -- what it moved, what it left and why. The run
  record holding that account is reaped ten minutes after it ends and is gone
  with the process, so every run also writes it to
  ``.cache/image-store/reports/<run id>.json``. Losing one loses only a report:
  it is under ``.cache``, which backups skip, and nothing reads it but the
  Settings card. Reports older than ``REPORT_MAX_AGE_SECONDS`` are pruned when
  the next one is written.

* **The device and the marker.** ``.cache`` is NOT excluded from sync -- a
  library in a synced folder carries its cache to every device that opens it --
  so the registry's exclusion key, which is in-process memory, cannot stop a
  second device starting a migration over the same tree. The marker is the
  part that can: ``.cache/image-store/maintenance.json`` holds
  ``{device, run, heartbeat}``, a timer thread refreshes the heartbeat every
  ``HEARTBEAT_SECONDS`` for the whole run, and a device that finds another
  device's marker younger than ``MARKER_STALE_SECONDS`` refuses to start. It is
  advisory, and says so: two devices that start inside one sync interval both
  see no marker. What it closes is the ordinary case -- a laptop starting a
  collection while the desktop is half way through a migration.

  "Device" has to be something a synced folder cannot carry, so the id lives
  beside the process locks (``proclock.lock_dir()``), machine-local and outside
  every store. The key used everywhere -- in the marker, and for the collector's
  per-device sightings -- is ``sha256(device id + "\\0" + pinned root)``, so even
  one machine opening two copies of a library gets two keys.

Nothing here takes a campaign lock or writes campaign state; every path is
built from the ``root`` the caller pinned, never from the live ``home()``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

from . import atomic, proclock

_log = logging.getLogger(__name__)

REPORT_MAX_AGE_SECONDS = 30 * 24 * 3600
"""How long a report outlives its run. A month is long enough to come back to
Settings and read what last week's migration did, and short enough that a store
collected weekly does not grow a drawer of them."""

HEARTBEAT_SECONDS = 30.0
"""How often a live run re-stamps its marker. Read at call time, so a test can
shorten it. Ten beats fit in the staleness window, so a sync client that delays
a few of them does not make a live run look dead."""

MARKER_STALE_SECONDS = 5 * 60
"""How old another device's heartbeat may be before its marker is ignored. A
run whose device lost power never clears its marker; past this it is taken to
be dead rather than refusing every other device forever."""

DEVICE_ID_FILE = "device-id"
"""The machine-local id's file name, inside ``proclock.lock_dir()``."""

_RUN_ID = re.compile(r"[0-9a-f]{32}")
_DEVICE_ID = re.compile(r"[0-9a-f]{32}")
_DEVICE_LOCK = threading.Lock()


class MaintenanceElsewhereError(Exception):
    """Another device's run holds a live marker over this store."""

    def __init__(self, marker: dict) -> None:
        super().__init__("image-store maintenance is running on another device")
        self.marker = marker


# ---- paths -------------------------------------------------------------------

def _cache(root: Path) -> Path:
    return Path(root) / ".cache" / "image-store"


def reports_dir(root: Path) -> Path:
    """Where the reports live under the pinned root."""
    return _cache(root) / "reports"


def _check_run_id(run_id: object) -> str:
    """A run id is 32 lowercase hex characters, and nothing else reaches a path.

    `fullmatch`, not `match` with `$`: `$` also matches before a trailing
    newline, which would let ``"<id>\\n"`` through to the file name.
    """
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError(f"not a run id: {run_id!r}")
    return run_id


def report_path(root: Path, run_id: str) -> Path:
    """The report file for ``run_id``. Raises ``ValueError`` for a bad id."""
    return reports_dir(root) / f"{_check_run_id(run_id)}.json"


def marker_path(root: Path) -> Path:
    """The synced maintenance marker under the pinned root."""
    return _cache(root) / "maintenance.json"


# ---- reports -----------------------------------------------------------------

def write(root: Path, run_id: str, report: dict, now: float | None = None) -> Path:
    """Atomically write ``report`` as this run's report, then prune old ones.

    Called from the work function's own ``finally``, so a run that was
    cancelled or failed still leaves its account behind.
    """
    path = report_path(root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps(report, indent=2, sort_keys=True,
                                       ensure_ascii=False) + "\n")
    prune(root, now=now, keep=run_id)
    return path


def read(root: Path, run_id: str) -> dict | None:
    """The stored report, or ``None`` when there is none (never written, pruned,
    or not readable as one). Raises ``ValueError`` for a bad id."""
    path = report_path(root, run_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def prune(root: Path, now: float | None = None, keep: str | None = None) -> int:
    """Delete reports older than ``REPORT_MAX_AGE_SECONDS``. Returns how many.

    Only files named as a report (``<run id>.json``) are considered, so an
    atomic temp mid-write or anything a person put there is left alone. Age is
    the file's mtime. Fail-soft per file: a report that cannot be stat'ed or
    removed costs nothing but its space.
    """
    cutoff = (time.time() if now is None else now) - REPORT_MAX_AGE_SECONDS
    try:
        entries = list(reports_dir(root).iterdir())
    except OSError:
        return 0
    removed = 0
    for entry in entries:
        stem, dot, ext = entry.name.rpartition(".")
        if not dot or ext != "json" or not _RUN_ID.fullmatch(stem) or stem == keep:
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError:
            continue
    return removed


# ---- the device ----------------------------------------------------------------

def _read_device_id(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text if _DEVICE_ID.fullmatch(text) else None


def device_id() -> str:
    """This machine's id, minted on first use and kept beside the process locks.

    Never under a store: a store may be a synced folder, and an id that synced
    would make two devices one. Minted under a process lock (and this module's
    thread lock) so two backends starting together agree; re-read after the
    write so that, should the lock not come in time, the caller still returns
    what is on disk rather than its own losing guess.
    """
    path = proclock.lock_dir() / DEVICE_ID_FILE
    with _DEVICE_LOCK:
        found = _read_device_id(path)
        if found is not None:
            return found
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = proclock.acquire(path.with_name(DEVICE_ID_FILE + ".lock"),
                              time.monotonic() + 5.0)
        try:
            found = _read_device_id(path)
            if found is not None:
                return found
            atomic.write_text(path, uuid.uuid4().hex + "\n")
        finally:
            if fd is not None:
                proclock.release(fd)
        found = _read_device_id(path)
        if found is None:
            raise OSError(f"the device id at {path} could not be read back")
        return found


def device_key(root: Path) -> str:
    """The key this device uses for this store: ``sha256(id + "\\0" + root)``.

    The root is part of it so that a synced ``.cache`` -- which may arrive
    from another machine, or be copied to a second library on this one --
    never shares a key with anything it did not itself write.
    """
    raw = f"{device_id()}\0{Path(root)}".encode("utf-8", "surrogateescape")
    return hashlib.sha256(raw).hexdigest()


# ---- the marker ----------------------------------------------------------------

def read_marker(root: Path) -> dict | None:
    """The marker, or ``None`` when absent or not readable as one."""
    try:
        data = json.loads(marker_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_marker(root: Path, run_id: str, device: str,
                 now: float | None = None) -> None:
    """Stamp the marker as ``run_id`` on ``device``, heartbeat ``now``."""
    path = marker_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps({
        "device": device, "run": _check_run_id(run_id),
        "heartbeat": time.time() if now is None else now}) + "\n")


def _live_elsewhere(marker: dict | None, device: str, now: float) -> bool:
    """Whether ``marker`` belongs to another device's run that is still beating.

    A heartbeat from the FUTURE counts as live: the other device's clock is
    ahead of ours, and treating its run as dead would let both run at once. A
    heartbeat that is not a number cannot be aged, and is ignored like a
    missing marker.
    """
    if not marker or marker.get("device") == device:
        return False
    beat = marker.get("heartbeat")
    if not isinstance(beat, (int, float)) or isinstance(beat, bool):
        return False
    return now - beat < MARKER_STALE_SECONDS


def claim_marker(root: Path, run_id: str, device: str,
                 now: float | None = None) -> None:
    """Take the marker for this run, or raise ``MaintenanceElsewhereError``.

    This device's own marker never refuses: the registry's exclusion key is
    what stops a second run in this process, and a marker left by this device
    is one that crashed or was never cleared.
    """
    at = time.time() if now is None else now
    found = read_marker(root)
    if _live_elsewhere(found, device, at):
        raise MaintenanceElsewhereError(found or {})
    write_marker(root, run_id, device, now=at)


def release_marker(root: Path, run_id: str, device: str) -> None:
    """Remove the marker if it is still this run's. Fail-soft: a marker left
    behind goes stale on its own."""
    found = read_marker(root)
    if not found or found.get("device") != device or found.get("run") != run_id:
        return
    try:
        marker_path(root).unlink()
    except OSError:
        _log.warning("could not clear the maintenance marker for run %s", run_id)


@contextlib.contextmanager
def heartbeat(root: Path, run_id: str, device: str) -> Iterator[None]:
    """Keep this run's marker fresh for as long as the block runs, then clear it.

    A SEPARATE THREAD, not a beat at item boundaries: one item -- a large image
    decoded, hashed and verified -- can outlast the staleness window, and a
    marker that only moved between items would let another device start in the
    middle of it. Each beat is fail-soft; a run is not failed because a sync
    client held the marker for a moment.
    """
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                write_marker(root, run_id, device)
            except Exception:                                # noqa: BLE001
                _log.warning("maintenance heartbeat for run %s failed", run_id,
                             exc_info=True)

    thread = threading.Thread(target=beat, name=f"maintenance-heartbeat-{run_id[:8]}",
                              daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=HEARTBEAT_SECONDS + 5.0)
        release_marker(root, run_id, device)

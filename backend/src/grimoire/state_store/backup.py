"""Auto-backup scheduler for the State Store.

Zips a configured subset of the data root on a wall-clock interval, prunes
old archives to a retention cap, and emits a ``backup_complete`` event.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire import events
from grimoire.event_bus import Event, EventBus
from grimoire.state_store.config import AutoBackupConfig

logger = logging.getLogger(__name__)

_ARCHIVE_GLOB = "grimoire-backup-*.zip"
_BACKOFF_SECONDS = 5 * 60


def run_backup(
    *,
    data_root: Path,
    database_path: Path,
    config: AutoBackupConfig,
    now: datetime | None = None,
) -> Path | None:
    """Write one backup zip and return its path, or ``None`` if nothing was included."""
    if now is None:
        now = datetime.now(UTC)

    backup_dir = config.backup_dir or (data_root / "backups")
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    final_path = backup_dir / f"grimoire-backup-{timestamp}.zip"
    tmp_path = backup_dir / f"grimoire-backup-{timestamp}.zip.tmp"

    components: list[str] = []

    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for component in config.includes:
                if _add_component(zf, component, data_root=data_root, database_path=database_path):
                    components.append(component)
    except Exception:
        # Clean up partial tmp on failure.
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise

    if not components:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        return None

    os.replace(tmp_path, final_path)
    return final_path


def _add_component(
    zf: zipfile.ZipFile,
    component: str,
    *,
    data_root: Path,
    database_path: Path,
) -> bool:
    """Add one configured include to the archive; return whether anything was written."""
    if component == "library":
        return _add_dir_component(zf, data_root / "library", arcname_root="library")
    if component == "campaigns":
        return _add_dir_component(zf, data_root / "campaigns", arcname_root="campaigns")
    if component == "sqlite":
        return _add_sqlite_component(zf, database_path)
    return False


def _add_dir_component(zf: zipfile.ZipFile, directory: Path, *, arcname_root: str) -> bool:
    if not directory.is_dir():
        return False
    _add_directory(zf, directory, arcname_root=arcname_root)
    return True


def _add_sqlite_component(zf: zipfile.ZipFile, database_path: Path) -> bool:
    if not database_path.is_file():
        return False
    zf.write(database_path, arcname=database_path.name)
    # Include WAL/SHM so a restored backup is consistent.
    for suffix in ("-wal", "-shm"):
        sibling = database_path.parent / (database_path.name + suffix)
        if sibling.is_file():
            zf.write(sibling, arcname=sibling.name)
    return True


def _add_directory(zf: zipfile.ZipFile, directory: Path, *, arcname_root: str) -> None:
    for file_path in sorted(directory.rglob("*")):
        if file_path.is_file():
            relative = file_path.relative_to(directory)
            zf.write(file_path, arcname=f"{arcname_root}/{relative}")


def prune_old_backups(backup_dir: Path, retention_count: int) -> list[Path]:
    """Delete oldest backups beyond ``retention_count``; return deleted paths."""
    if not backup_dir.is_dir():
        return []
    archives = sorted(
        backup_dir.glob(_ARCHIVE_GLOB),
        key=lambda p: p.stat().st_mtime,
    )
    to_delete = archives[: max(0, len(archives) - retention_count)]
    deleted: list[Path] = []
    for path in to_delete:
        try:
            path.unlink()
            deleted.append(path)
        except OSError:
            logger.warning("Failed to delete old backup %s", path)
    return deleted


def _youngest_backup_mtime(backup_dir: Path) -> float | None:
    """Return the mtime of the most-recent existing archive, or ``None``."""
    if not backup_dir.is_dir():
        return None
    archives = list(backup_dir.glob(_ARCHIVE_GLOB))
    if not archives:
        return None
    return max(p.stat().st_mtime for p in archives)


class BackupScheduler:
    """Runs :func:`run_backup` on a wall-clock interval as a background task."""

    def __init__(
        self,
        *,
        data_root: Path,
        database_path: Path,
        config: AutoBackupConfig,
        bus: EventBus,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._data_root = data_root
        self._database_path = database_path
        self._config = config
        self._bus = bus
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._task: asyncio.Task[Any] | None = None

    def start(self) -> None:
        if not self._config.enabled:
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.get_event_loop().create_task(
            self._loop(), name="grimoire.backup_scheduler"
        )

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run_once(self) -> None:
        """Execute one backup cycle: backup + prune + emit."""
        now = self._clock()
        zip_path = run_backup(
            data_root=self._data_root,
            database_path=self._database_path,
            config=self._config,
            now=now,
        )
        if zip_path is None:
            return

        backup_dir = self._config.backup_dir or (self._data_root / "backups")
        pruned = prune_old_backups(backup_dir, self._config.retention_count)

        await self._bus.emit(
            Event(
                type=events.BACKUP_COMPLETE,
                payload={
                    "path": str(zip_path),
                    "size_bytes": zip_path.stat().st_size,
                    "components": list(self._config.includes),
                    "pruned": [str(p) for p in pruned],
                },
            )
        )

    async def _loop(self) -> None:
        interval_seconds = self._config.interval_hours * 3600
        backup_dir = self._config.backup_dir or (self._data_root / "backups")

        youngest_mtime = _youngest_backup_mtime(backup_dir)
        if youngest_mtime is not None:
            elapsed = self._clock().timestamp() - youngest_mtime
            wait = max(0.0, interval_seconds - elapsed)
        else:
            # No previous backup found — run immediately.
            wait = 0.0

        while True:
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Backup failed; retrying in %d seconds", _BACKOFF_SECONDS)
                wait = _BACKOFF_SECONDS
                continue
            wait = interval_seconds


__all__ = ["BackupScheduler", "prune_old_backups", "run_backup"]

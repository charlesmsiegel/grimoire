"""Bounded dormant-file retirement and session lease renewal."""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

from .. import atomic, proclock
from . import _contracts, _ownership, _path_checks, _runtime, _sqlite_support


def _renew_lease(session: _runtime._Session) -> None:
    # The lease needs a touch a day; a check per operation re-proved every
    # sidecar on the hot path. Skipping one only ever skips a write.
    now = time.time()
    if 0 <= now - session.leased < _contracts.LEASE_CHECK_S:
        return
    session.leased = now
    lease = session.path.with_suffix('.alive')
    try:
        if not _path_checks._cache_file_safe(session.path):
            return
        if not lease.exists():
            atomic.write_text(lease, '')
            os.utime(lease, (now, now))
        elif now - lease.stat().st_mtime >= _contracts.TOUCH_INTERVAL_S:
            os.utime(lease, (now, now))
        if now - session.retained >= _contracts.MAINTENANCE_INTERVAL_S:
            session.retained = now
            _sweep_retention(session)
    except _sqlite_support._CACHE_ERRORS:
        _runtime._warn_once('lease')


def _sweep_retention(session: _runtime._Session) -> None:
    # Only recognized, owned disposable files are eligible. The current
    # local schema is exempt even when an old mtime or clock skew suggests
    # dormancy; obsolete schemas keep their own independent lease.
    for path in session.path.parent.iterdir():
        original = path.with_name(_path_checks._cache_filename(path).removesuffix('.corrupt'))
        if original == session.path or not _contracts._CACHE_FILE.fullmatch(original.name):
            continue
        fd = None
        try:
            if not _path_checks._cache_file_safe(original) or not _path_checks._cache_file_safe(path):
                continue
            # Recovery keeps the canonical schema lease even when replacement
            # failed and only its aside remains. Dormancy must age that copy too.
            lease = original.with_suffix('.alive')
            used = _retention_used(lease, path)
            if time.time() - used <= _contracts.RETENTION_S:
                continue
            fd = _ownership._recovery_lock(session.root, original)
            if fd is None or not _path_checks._cache_file_safe(original) or not _path_checks._cache_file_safe(path) or not _ownership._probe_recognized(path):
                continue
            used = _retention_used(lease, path)
            if time.time() - used <= _contracts.RETENTION_S:
                continue  # Another process renewed the lease before exclusion.
            _retire_owned_files(path, original, lease)
        except _sqlite_support._CACHE_ERRORS:
            _runtime._warn_once('retention')
        finally:
            if fd is not None:
                with contextlib.suppress(*_sqlite_support._CACHE_ERRORS):
                    proclock.release(fd)


def _retention_used(lease: Path, path: Path) -> float:
    return (lease if lease.exists() else path).stat().st_mtime


def _retire_owned_files(path: Path, original: Path, lease: Path) -> None:
    if path != original:
        _retire_owned_aside(path, original, lease)
    else:
        for candidate in [path, lease, *(Path(str(path) + suffix)
                          for suffix in ('-journal', '-wal', '-shm'))]:
            candidate.unlink(missing_ok=True)
        _erase_recognized_aside(Path(str(path) + '.corrupt'))


def _retire_owned_aside(path: Path, original: Path, lease: Path) -> None:
    # Called only after canonical exclusion, current ownership and lease proof.
    if _erase_recognized_aside(path) and not original.exists():
        lease.unlink(missing_ok=True)


def _erase_recognized_aside(path: Path) -> bool:
    # A healthy owned neighbour never proves this separate file's ownership.
    # All callers already hold the canonical operation/recovery exclusion.
    try:
        if (_path_checks._cache_file_safe(path) and path.exists()
                and not Path(str(path) + '-journal').exists() and _ownership._probe_recognized(path)):
            path.unlink()
            return True
    except _sqlite_support._CACHE_ERRORS:
        _runtime._warn_once('aside')
    return False

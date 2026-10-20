"""Serialized owned-database opening and atomic corruption recovery."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path

from .. import locks, proclock
from . import _contracts, _deletion, _fences, _ownership, _retention, _runtime, _sqlite_support


def _discard_aside(root: Path, path: Path) -> None:
    aside = Path(str(path) + ".corrupt")
    if not aside.exists():
        return
    # Caller holds the operation/recovery exclusion throughout this unlink.
    if _fences._location(root, path.name.split("-v", 1)[0]) == path:
        _retention._erase_recognized_aside(aside)


def _replace_corrupt_locked(root: Path, path: Path) -> _runtime._Session | None:
    _runtime._close_locked(invalidate=False)
    if (_fences._location(root, path.name.split("-v", 1)[0]) != path
            or not _ownership._probe_recognized(path)):
        return None
    # Corruption classification never establishes ownership. An unreadable
    # metadata/header file stays a miss, even at our expected cache filename.
    # A journal can belong to a foreign before-image. An unreadable
    # ownership probe never licenses rolling it back or discarding it.
    if Path(str(path) + "-journal").exists():
        return None
    if not _ownership._still_corrupt(path):
        return _open_locked(root, recover=False, clean_aside=False, excluded=True)
    aside = Path(str(path) + ".corrupt")
    if aside.exists():
        return None
    path.replace(aside)
    session = _open_locked(root, recover=False, clean_aside=False, excluded=True)
    if session is not None:
        aside.unlink(missing_ok=True)
    return session


def _recover(root: Path, path: Path, error: BaseException, *, excluded: bool = False) -> _runtime._Session | None:
    if not _ownership._corruption(error):
        return None
    fd = None
    try:
        if not excluded:
            fd = _ownership._recovery_lock(root, path)
            if fd is None:
                return None
        return _replace_corrupt_locked(root, path)
    except _sqlite_support._CACHE_ERRORS:
        _runtime._warn_once("recovery")
        return None
    finally:
        if fd is not None:
            with contextlib.suppress(*_sqlite_support._CACHE_ERRORS):
                proclock.release(fd)


def _connect_owned(root: Path, path: Path, *, recover: bool, clean_aside: bool) -> _runtime._Session | None:
    if _sqlite_support._sqlite is None:
        return None
    conn: _sqlite_support._sqlite_available.Connection | None = None
    try:
        # A 0-byte file at the owned name with no hot journal beside it is a
        # first initialisation that never committed (a kill, a full disk):
        # it holds nothing to preserve, so it initialises like a missing one
        # rather than reading as foreign forever.
        existed = path.exists() and not _ownership._unborn(path)
        if existed and not _ownership._probe_owned(path):
            return None
        conn = _sqlite_support._sqlite.connect(path, timeout=0.005, check_same_thread=False,
                               isolation_level=None)
        # SQLite enforces this inside its engine, before returning a hostile
        # value to Python. A SQL length predicate alone cannot bound its own
        # internal allocation. Metadata/record overhead gets one extra MiB.
        conn.setlimit(_sqlite_support._sqlite.SQLITE_LIMIT_LENGTH, 2 * _contracts.MAX_PAYLOAD_BYTES)
        if not _ownership._owned(conn, existed):
            conn.close()
            return None
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5")
        session = _runtime._Session(root, os.getpid(), path, conn, _runtime._state.lock,
                           _runtime._state.epoch, _runtime._clock(), _ownership._identity(path))
        _runtime._state.session = session
        _retention._renew_lease(session)
        _runtime._schedule_idle(session)
        if clean_aside:
            with contextlib.suppress(*_sqlite_support._CACHE_ERRORS):
                _discard_aside(root, path)
        return session
    except _sqlite_support._CACHE_ERRORS as error:
        if _runtime._state.session is not None and _runtime._state.session.conn is conn:
            _runtime._close_locked(invalidate=False)
        elif conn is not None:
            with contextlib.suppress(*_sqlite_support._CACHE_ERRORS):
                conn.close()
        _runtime._warn_once("database")
        return _recover(root, path, error, excluded=True) if recover else None


@contextlib.contextmanager
def _database(root: Path, *, recover: bool = True, clean_aside: bool = True,
              expected_generation: str | None = None) -> Iterator[_runtime._Session | None]:
    # Ordinary SQL and replacement share one NO_WAIT machine-local lock.
    # Idle connections may coexist; before each operation their inode is
    # checked under that lock so a replacement cannot strand active users.
    fd, session = None, None
    try:
        if not (_runtime._state.transitions or _runtime._on_loop() or locks.campaign_locks_held() or _sqlite_support._sqlite is None
                or os.environ.get("GRIMOIRE_COMPILED_CACHE") == "0"):
            device = _runtime._device(root)
            path = _fences._location(root, device) if device is not None else None
            if path is not None:
                fd = _ownership._recovery_lock(root, path)
                if fd is not None:
                    session = _open_locked(root, recover=recover, clean_aside=clean_aside, excluded=True)
                    if (session is not None and expected_generation is not None
                            and _fences._read_generation(session.conn) != expected_generation):
                        session = None
                    if session is not None and not _deletion._synchronize(session):
                        session = None
    except _sqlite_support._CACHE_ERRORS:
        # Assignment can precede a failed synchronization: never expose that
        # still-unpurged connection as a usable optional cache session.
        session = None
        _runtime._warn_once("exclusion")
    try:
        yield session
    except _sqlite_support._CACHE_ERRORS as error:
        if session is not None:
            _failed_sql(session, error)
        raise
    finally:
        if fd is not None:
            with contextlib.suppress(*_sqlite_support._CACHE_ERRORS):
                proclock.release(fd)


def _failed_sql(session: _runtime._Session, error: BaseException) -> None:
    # Finish failed transactions before releasing the replacement exclusion.
    # Otherwise a recoverer could rename in the exception-to-rollback gap.
    if _ownership._corruption(error):
        _runtime._close_locked(invalidate=False)
        return
    try:
        session.conn.rollback()
    except _sqlite_support._CACHE_ERRORS:
        _runtime._close_locked(invalidate=False)


def _open_locked(root: Path, *, recover: bool = True, clean_aside: bool = True,
                 excluded: bool = False) -> _runtime._Session | None:
    """Private seam: the connection lock must already be held by the caller."""
    if _runtime._state.transitions or _runtime._on_loop() or locks.campaign_locks_held() or os.environ.get("GRIMOIRE_COMPILED_CACHE") == "0":
        return None
    if _sqlite_support._sqlite is None:
        _runtime._warn_once("sqlite_unavailable")
        return None
    if not excluded:
        with _database(root, recover=recover, clean_aside=clean_aside) as session:
            return session
    root = Path(root)
    device = _runtime._device(root)
    if device is None:
        return None
    path = _fences._location(root, device)
    if path is None:
        _runtime._close_locked()
        return None
    if _runtime._state.session is not None:
        same_root = _runtime._state.session.root == root and _runtime._state.session.path == path
        if same_root and _runtime._state.session.identity == _ownership._identity(path):
            _retention._renew_lease(_runtime._state.session)
            return _runtime._state.session
        _runtime._close_locked(invalidate=not same_root)
    return _connect_owned(root, path, recover=recover, clean_aside=clean_aside)

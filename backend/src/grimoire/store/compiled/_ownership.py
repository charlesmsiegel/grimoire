"""SQLite ownership/schema proofs and corruption predicates."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

from .. import proclock
from . import _contracts, _path_checks, _runtime, _sqlite_support


def _probe_recognized(path: Path) -> bool:
    match = _contracts._CACHE_FILE.fullmatch(_path_checks._cache_filename(path).removesuffix('.corrupt'))
    if _sqlite_support._sqlite is None or match is None:
        return False
    conn = _sqlite_support._sqlite.connect(path.absolute().as_uri() + '?mode=ro&immutable=1', uri=True,
                           timeout=0.005, isolation_level=None)
    try:
        conn.setlimit(_sqlite_support._sqlite.SQLITE_LIMIT_LENGTH, 2 * _contracts.MAX_PAYLOAD_BYTES)
        schema = conn.execute('SELECT value FROM cache_meta WHERE name=?', ('schema',)).fetchone()
        generation = conn.execute('SELECT value FROM cache_meta WHERE name=?', ('purge_generation',)).fetchone()
        return (conn.execute('PRAGMA application_id').fetchone()[0] == _contracts.APPLICATION_ID
                and schema == (match[2],) and generation is not None
                and isinstance(generation[0], str) and (generation[0] == '' or bool(_contracts._DEVICE_KEY.fullmatch(generation[0]))))
    finally:
        conn.close()


def _owned_tables(conn: _sqlite_support._sqlite_available.Connection, after: int | None) -> Iterator[tuple[int, str]]:
    # compiled-maintenance-sql: at most129 bounded owned schema probes; close before DROP
    for _ in range(_contracts.SOURCE_CHUNK + 1):
        cursor = conn.execute("SELECT rowid,name FROM sqlite_master WHERE type='table' AND (? IS NULL OR rowid>?) ORDER BY rowid LIMIT 1", (after, after))
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            return
        identity, name = row
        if type(identity) is not int or not isinstance(name, str) or '\x00' in name:
            raise ValueError('invalid owned schema name')
        yield identity, name
        after = identity


def _initialise(conn: _sqlite_support._sqlite_available.Connection) -> None:
    # auto_vacuum must precede tables. Bootstrapping metadata and the
    # application id in one transaction exposes no partially owned database
    # to an artifact writer.
    conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("PRAGMA application_id=1196575053")
    _create_schema(conn, str(_contracts.SCHEMA), '')
    conn.commit()


def _create_schema(conn: _sqlite_support._sqlite_available.Connection, schema: str, generation: str) -> None:
    # Both first ownership and final purge rebuild use the same public DDL.
    # The caller owns the transaction; obsolete filenames retain their proved
    # version marker while unsupported old layouts can rebuild or miss safely.
    conn.execute("CREATE TABLE cache_meta(name TEXT PRIMARY KEY,value TEXT NOT NULL)")
    conn.executemany("INSERT INTO cache_meta(name,value) VALUES (?,?)",
                     (("schema", schema), ("purge_generation", generation)))
    conn.execute('CREATE TABLE artifacts(kind TEXT NOT NULL,key_digest TEXT NOT NULL,'
                 'payload TEXT NOT NULL,"check" TEXT NOT NULL,last_used INTEGER NOT NULL,'
                 'PRIMARY KEY(kind,key_digest))')
    conn.execute('CREATE TABLE sources(path TEXT PRIMARY KEY,mtime_ns INTEGER NOT NULL,'
                 'ctime_ns INTEGER NOT NULL,size INTEGER NOT NULL,ino TEXT NOT NULL,'
                 'content_hash TEXT NOT NULL,"check" TEXT NOT NULL,last_used INTEGER NOT NULL)')
    conn.execute('CREATE TABLE materialized(path TEXT NOT NULL,kind TEXT NOT NULL,instance TEXT NOT NULL,'
                 'built_from TEXT NOT NULL,last_used INTEGER NOT NULL,"check" TEXT NOT NULL,'
                 'artifact_kind TEXT,artifact_key TEXT,PRIMARY KEY(path,kind,instance))')
    conn.execute('CREATE INDEX materialized_artifact ON materialized(artifact_kind,artifact_key)')


def _owned(conn: _sqlite_support._sqlite_available.Connection, existed: bool) -> bool:
    application_id = conn.execute("PRAGMA application_id").fetchone()[0]
    if application_id == _contracts.APPLICATION_ID:
        schema = conn.execute("SELECT value FROM cache_meta WHERE name=?",
                              ("schema",)).fetchone()
        generation = conn.execute("SELECT value FROM cache_meta WHERE name=?",
                                  ("purge_generation",)).fetchone()
        if schema == (str(_contracts.SCHEMA),) and generation is not None:
            return True
        _runtime._warn_once("schema")
        return False
    if (application_id == 0 and not existed
            and conn.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
            .fetchone() is None):
        _initialise(conn)
        return True
    _runtime._warn_once("ownership")
    return False


def _recovery_lock(root: Path, path: Path) -> int | None:
    return proclock.acquire(proclock.lock_path(root, "compiled-recovery", _path_checks._cache_filename(path)), proclock.NO_WAIT)


def _corruption(error: BaseException) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    return (_sqlite_support._sqlite is not None and type(code) is int
            and code & 0xFF in (_sqlite_support._sqlite.SQLITE_CORRUPT, _sqlite_support._sqlite.SQLITE_NOTADB))


def _still_corrupt(path: Path) -> bool:
    # An earlier error can outlive another backend's repair. Probe again
    # while holding the recovery lock, before renaming any current inode.
    if _sqlite_support._sqlite is None:
        return False
    conn = None
    try:
        conn = _sqlite_support._sqlite.connect(path, timeout=0.005, check_same_thread=False, isolation_level=None)
        conn.setlimit(_sqlite_support._sqlite.SQLITE_LIMIT_LENGTH, 2 * _contracts.MAX_PAYLOAD_BYTES)
        return conn.execute("PRAGMA quick_check(1)").fetchone() != ("ok",)
    except _sqlite_support._CACHE_ERRORS as error:
        return _corruption(error)
    finally:
        if conn is not None:
            with contextlib.suppress(*_sqlite_support._CACHE_ERRORS):
                conn.close()


def _probe_owned(path: Path) -> bool:
    if _sqlite_support._sqlite is None:
        return False
    # immutable bypasses rollback/WAL recovery entirely. mode=ro alone does
    # not: SQLite can still try to recover a foreign hot rollback journal.
    conn = _sqlite_support._sqlite.connect(path.absolute().as_uri() + "?mode=ro&immutable=1", uri=True,
                           timeout=0.005, check_same_thread=False, isolation_level=None)
    try:
        conn.setlimit(_sqlite_support._sqlite.SQLITE_LIMIT_LENGTH, 2 * _contracts.MAX_PAYLOAD_BYTES)
        return _owned(conn, True)
    finally:
        conn.close()


def _unborn(path: Path) -> bool:
    return path.stat().st_size == 0 and not Path(str(path) + "-journal").exists()


def _identity(path: Path) -> tuple[int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino

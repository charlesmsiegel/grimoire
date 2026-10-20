"""Bounded live-byte eviction and incremental vacuum."""

from __future__ import annotations

import time

from . import _contracts, _runtime, _sqlite_support


def _live_bytes(conn: _sqlite_support._sqlite_available.Connection) -> int:
    pages = conn.execute('PRAGMA page_count').fetchone()[0]
    free = conn.execute('PRAGMA freelist_count').fetchone()[0]
    return (pages - free) * conn.execute('PRAGMA page_size').fetchone()[0]


def _lru_victims(conn: _sqlite_support._sqlite_available.Connection) -> list[tuple]:
    # compiled-maintenance-sql: bounded private LRU metadata, never membership
    artifacts = conn.execute("SELECT rowid,CASE WHEN typeof(last_used)='integer' THEN last_used ELSE -1 END AS used FROM artifacts ORDER BY used,rowid LIMIT 128").fetchall()
    sources = conn.execute("SELECT rowid,CASE WHEN typeof(last_used)='integer' THEN last_used ELSE -1 END AS used FROM sources ORDER BY used,rowid LIMIT 128").fetchall()
    materialized = conn.execute("SELECT rowid,CASE WHEN typeof(last_used)='integer' THEN last_used ELSE -1 END AS used FROM materialized ORDER BY used,rowid LIMIT 128").fetchall()
    return sorted([*(('artifacts', *row) for row in artifacts), *(('sources', *row) for row in sources),
                   *(('materialized', *row) for row in materialized)],
                  key=lambda row: row[2] if type(row[2]) is int else -1)[:128]


def _evict(conn: _sqlite_support._sqlite_available.Connection, table: str, rowid: int) -> None:
    # compiled-maintenance-sql: remove only associations to an explicit victim rowid
    if table == 'artifacts':
        # Remove only the evicted association, so a newer projection at the
        # same path/instance survives replacement of its earlier artifact.
        conn.execute('DELETE FROM materialized WHERE (artifact_kind,artifact_key) IN '
                     '(SELECT kind,key_digest FROM artifacts WHERE rowid=?)', (rowid,))
        conn.execute('DELETE FROM artifacts WHERE rowid=?', (rowid,))
    elif table == 'sources':
        conn.execute('DELETE FROM sources WHERE rowid=?', (rowid,))
    else:
        conn.execute('DELETE FROM materialized WHERE rowid=?', (rowid,))


def _maintain(session: _runtime._Session) -> None:
    pending = session.conn.execute('SELECT value FROM cache_meta WHERE name=?', ('trimming',)).fetchone()
    session.trimming = session.trimming or pending == ('1',)
    now = time.time()
    if not session.trimming and now - session.maintained < _contracts.MAINTENANCE_INTERVAL_S:
        return
    conn = session.conn
    if not session.trimming and _live_bytes(conn) <= _contracts.SIZE_CAP_BYTES:
        session.maintained = now
        return
    session.trimming = True
    conn.execute('BEGIN IMMEDIATE')
    conn.execute("INSERT INTO cache_meta(name,value) VALUES ('trimming','1') "
                 "ON CONFLICT(name) DO UPDATE SET value='1'")
    for _ in range(_contracts.MAX_EVICTION_CHUNKS):
        victims = _lru_victims(conn)
        if not victims:
            break
        for table, rowid, _used in victims:
            _evict(conn, table, rowid)
            if _live_bytes(conn) <= _contracts.LOW_WATER_BYTES:
                break
        if _live_bytes(conn) <= _contracts.LOW_WATER_BYTES:
            break
    conn.commit()
    _incremental_vacuum(conn)
    if _live_bytes(conn) <= _contracts.LOW_WATER_BYTES:
        conn.execute("DELETE FROM cache_meta WHERE name='trimming'")
        session.trimming = False
        session.maintained = now
    # A bounded partial pass stays eligible at the next actual write, even
    # after it falls below the cap but has not reached low water (S5 review).


def _incremental_vacuum(conn: _sqlite_support._sqlite_available.Connection) -> None:
    # Return at most VACUUM_PAGES free pages to the filesystem. The pragma has
    # no result columns, and Python 3.11's sqlite3 resets such a statement
    # after its first step, so there one call frees ONE page however many it
    # asks for; 3.12+ runs every step. Repeat until the budget is spent or
    # the freelist stops shrinking, which bounds both.
    freed = 0
    while freed < _contracts.VACUUM_PAGES:
        before = conn.execute('PRAGMA freelist_count').fetchone()[0]
        if not before:
            return
        conn.execute('PRAGMA incremental_vacuum(256)').fetchall()
        step = before - conn.execute('PRAGMA freelist_count').fetchone()[0]
        if step <= 0:
            return
        freed += step

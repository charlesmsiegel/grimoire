"""Async SQLite connection pool with WAL, FTS5, and sqlite-vec."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
import sqlite_vec


class Database:
    """Async connection pool for the campaigns SQLite database.

    Each connection has WAL enabled (unless disabled), foreign keys on, and
    sqlite-vec loaded. FTS5 is compiled into the bundled SQLite build.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        pool_size: int = 5,
        enable_wal: bool = True,
        busy_timeout_ms: int = 5000,
    ) -> None:
        if pool_size < 1:
            raise ValueError("pool_size must be >= 1")
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be >= 0")
        self.path = Path(path)
        self.pool_size = pool_size
        self.enable_wal = enable_wal
        self.busy_timeout_ms = busy_timeout_ms
        self._pool: asyncio.Queue[aiosqlite.Connection] | None = None
        self._all: list[aiosqlite.Connection] = []
        self._open_lock = asyncio.Lock()
        self._closed = False

    async def connect(self) -> None:
        """Open the pool. Idempotent."""
        async with self._open_lock:
            if self._pool is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            pool: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue(maxsize=self.pool_size)
            for _ in range(self.pool_size):
                conn = await self._open_connection()
                self._all.append(conn)
                await pool.put(conn)
            self._pool = pool
            self._closed = False

    async def _open_connection(self) -> aiosqlite.Connection:
        # ``isolation_level=None`` puts the underlying sqlite3 connection in
        # autocommit mode so transactions must be started explicitly with
        # BEGIN. This is required for migration atomicity: implicit BEGINs +
        # ``executescript`` would silently commit mid-migration.
        conn = await aiosqlite.connect(self.path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        # WAL allows concurrent readers but serializes writers. Without a
        # busy_timeout, a writer that finds the slot taken returns SQLITE_BUSY
        # immediately — surfacing as "database is locked" when background
        # workers (health probes, retention sweeper, embedding worker) race
        # the startup library scan. Five seconds is plenty for any single
        # transaction the app issues; the timeout only ever matters under
        # contention.
        await conn.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
        if self.enable_wal:
            await conn.execute("PRAGMA journal_mode = WAL")
            await conn.execute("PRAGMA synchronous = NORMAL")
        await conn.enable_load_extension(True)
        await conn.load_extension(sqlite_vec.loadable_path())
        await conn.enable_load_extension(False)
        return conn

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[aiosqlite.Connection]:
        """Acquire a connection from the pool for the duration of the block.

        If the caller leaves the connection inside an open transaction (raw
        BEGIN without COMMIT/ROLLBACK, or an exception inside _txn), the
        connection is rolled back before returning to the pool. Without this
        the next consumer's BEGIN would see "cannot start a transaction
        within a transaction" or operate on partial state.
        """
        if self._pool is None:
            raise RuntimeError("Database is not connected; call connect() first")
        conn = await self._pool.get()
        try:
            yield conn
        finally:
            if not self._closed:
                if conn.in_transaction:
                    with contextlib.suppress(Exception):
                        await conn.rollback()
                self._pool.put_nowait(conn)

    async def execute(self, sql: str, params: tuple = ()) -> None:
        async with self.acquire() as conn:
            await conn.execute(sql, params)

    async def fetchone(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        async with self.acquire() as conn, conn.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        async with self.acquire() as conn, conn.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    async def close(self) -> None:
        """Close every pooled connection."""
        async with self._open_lock:
            self._closed = True
            for conn in self._all:
                with contextlib.suppress(Exception):
                    await conn.close()
            self._all.clear()
            self._pool = None

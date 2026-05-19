from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from grimoire.storage import Database, apply_migrations
from grimoire.storage.migrations import (
    DEFAULT_MIGRATIONS_DIR,
    current_version,
    discover_migrations,
)


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.sqlite", pool_size=2)
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


async def test_connection_loads_sqlite_vec_and_enables_wal(db: Database) -> None:
    row = await db.fetchone("SELECT vec_version() AS v")
    assert row is not None
    assert isinstance(row["v"], str) and row["v"].startswith("v")

    mode = await db.fetchone("PRAGMA journal_mode")
    assert mode is not None
    assert mode[0].lower() == "wal"

    fks = await db.fetchone("PRAGMA foreign_keys")
    assert fks is not None
    assert int(fks[0]) == 1


async def test_every_pooled_connection_has_busy_timeout(tmp_path: Path) -> None:
    """Without busy_timeout, concurrent writers hit SQLITE_BUSY immediately —
    which surfaces as "database is locked" when background workers race the
    startup library scan. Every connection the pool hands out must have it set.
    """
    database = Database(tmp_path / "db.sqlite", pool_size=3, busy_timeout_ms=5000)
    await database.connect()
    try:
        for _ in range(3):
            async with database.acquire() as conn, conn.execute("PRAGMA busy_timeout") as cur:
                row = await cur.fetchone()
                assert row is not None
                assert int(row[0]) == 5000
    finally:
        await database.close()


async def test_fts5_is_available(db: Database) -> None:
    await db.execute("CREATE VIRTUAL TABLE search USING fts5(body)")
    await db.execute("INSERT INTO search(body) VALUES ('hello world')")
    row = await db.fetchone("SELECT body FROM search WHERE search MATCH 'hello'")
    assert row is not None
    assert row["body"] == "hello world"


async def test_migration_runner_creates_schema_version(db: Database) -> None:
    """Default migrations apply cleanly and a re-run is a no-op."""
    applied = await apply_migrations(db)
    versions = [m.version for m in applied]
    assert versions == sorted(versions) and versions, "default migrations must apply"
    assert await current_version(db) == versions[-1]

    row = await db.fetchone(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    )
    assert row is not None

    second = await apply_migrations(db)
    assert second == []


async def test_migration_runner_applies_pending(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_first.sql").write_text(
        "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT);"
    )
    (migrations_dir / "002_second.sql").write_text(
        "ALTER TABLE widgets ADD COLUMN qty INTEGER NOT NULL DEFAULT 0;"
    )

    database = Database(tmp_path / "db.sqlite", pool_size=1)
    await database.connect()
    try:
        applied = await apply_migrations(database, directory=migrations_dir)
        assert [m.version for m in applied] == [1, 2]
        assert await current_version(database) == 2

        again = await apply_migrations(database, directory=migrations_dir)
        assert again == []

        cols = await database.fetchall("PRAGMA table_info(widgets)")
        names = {row["name"] for row in cols}
        assert names == {"id", "name", "qty"}
    finally:
        await database.close()


async def test_migration_runner_rolls_back_on_failure(tmp_path: Path) -> None:
    """A migration whose later statement fails must leave NO trace.

    Earlier statements in the same migration must roll back along with
    schema_version, so on next startup the runner re-applies the whole
    migration cleanly.
    """
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_ok.sql").write_text("CREATE TABLE t (id INTEGER PRIMARY KEY);")
    (migrations_dir / "002_partial.sql").write_text(
        "CREATE TABLE u (id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE t (id INTEGER PRIMARY KEY);\n"  # duplicate; fails
    )

    database = Database(tmp_path / "db.sqlite", pool_size=1)
    await database.connect()
    try:
        with pytest.raises(sqlite3.OperationalError):
            await apply_migrations(database, directory=migrations_dir)

        # Migration 001 stays applied; 002's first statement must NOT have
        # leaked: table `u` should not exist and schema_version is still 1.
        assert await current_version(database) == 1
        row = await database.fetchone(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='u'"
        )
        assert row is None
    finally:
        await database.close()


async def test_migration_gap_rejected(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_a.sql").write_text("CREATE TABLE a (id INTEGER);")
    (migrations_dir / "003_c.sql").write_text("CREATE TABLE c (id INTEGER);")

    database = Database(tmp_path / "db.sqlite", pool_size=1)
    await database.connect()
    try:
        with pytest.raises(ValueError, match="gap"):
            await apply_migrations(database, directory=migrations_dir)
    finally:
        await database.close()


def test_discover_migrations_ignores_non_sql_files(tmp_path: Path) -> None:
    (tmp_path / "001_ok.sql").write_text("-- ok")
    (tmp_path / "README.md").write_text("notes")
    (tmp_path / "002.sql").write_text("-- missing name, ignored")
    (tmp_path / "002_also_ok.sql").write_text("-- ok")

    migrations = discover_migrations(tmp_path)
    assert [(m.version, m.name) for m in migrations] == [(1, "ok"), (2, "also_ok")]


def test_default_migrations_dir_exists() -> None:
    assert DEFAULT_MIGRATIONS_DIR.is_dir()

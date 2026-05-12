"""Migration runner backed by a ``schema_version`` table.

Migrations live under ``grimoire/storage/migrations/`` as files named
``NNN_description.sql`` where ``NNN`` is a zero-padded integer. Each file is
applied inside a single transaction and recorded in ``schema_version``.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from grimoire.storage.db import Database

MIGRATION_FILENAME_RE = re.compile(r"^(\d+)_([A-Za-z0-9][\w\-]*)\.sql$")
DEFAULT_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")


def discover_migrations(directory: Path | None = None) -> list[Migration]:
    """Return migrations sorted by version. Versions must be unique."""
    base = directory or DEFAULT_MIGRATIONS_DIR
    if not base.is_dir():
        return []
    found: dict[int, Migration] = {}
    for entry in sorted(base.iterdir()):
        if not entry.is_file():
            continue
        match = MIGRATION_FILENAME_RE.match(entry.name)
        if not match:
            continue
        version = int(match.group(1))
        if version in found:
            raise ValueError(
                f"Duplicate migration version {version}: {found[version].path.name} vs {entry.name}"
            )
        found[version] = Migration(version=version, name=match.group(2), path=entry)
    return [found[v] for v in sorted(found)]


async def _ensure_schema_version_table(db: Database) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


async def current_version(db: Database) -> int:
    await _ensure_schema_version_table(db)
    row = await db.fetchone("SELECT MAX(version) AS v FROM schema_version")
    if row is None or row["v"] is None:
        return 0
    return int(row["v"])


async def apply_migrations(
    db: Database,
    directory: Path | None = None,
) -> list[Migration]:
    """Apply every pending migration. Returns the list of applied migrations.

    Each migration runs as one transaction: the SQL body and the
    ``schema_version`` insert commit together, so a partially-applied
    migration leaves the previous version intact.
    """
    await _ensure_schema_version_table(db)
    applied = await current_version(db)
    migrations = discover_migrations(directory)
    pending = [m for m in migrations if m.version > applied]
    if not pending:
        return []

    # Verify monotonic gap-free progression starting at applied + 1.
    expected = applied + 1
    for m in pending:
        if m.version != expected:
            raise ValueError(
                f"Migration version gap: expected {expected}, found {m.version} "
                f"({m.path.name}). Migrations must be sequential."
            )
        expected += 1

    run: list[Migration] = []
    async with db.acquire() as conn:
        for migration in pending:
            statements = _split_statements(migration.sql)
            try:
                await conn.execute("BEGIN")
                for stmt in statements:
                    await conn.execute(stmt)
                await conn.execute(
                    "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                    (migration.version, migration.name, datetime.now(UTC).isoformat()),
                )
                await conn.execute("COMMIT")
            except Exception:
                await conn.execute("ROLLBACK")
                raise
            run.append(migration)
    return run


def _split_statements(sql: str) -> list[str]:
    """Split a migration script into individual SQL statements.

    Uses ``sqlite3.complete_statement`` so quoted semicolons and ``BEGIN``
    blocks inside triggers are respected. Statements are returned trimmed;
    blank input yields an empty list.
    """
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            stmt = buffer.strip()
            if stmt:
                statements.append(stmt)
            buffer = ""
    trailing = buffer.strip()
    if trailing:
        statements.append(trailing)
    return statements

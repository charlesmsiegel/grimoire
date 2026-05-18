"""§8 imagegen_jobs table from migration 019."""

from __future__ import annotations

from pathlib import Path

from grimoire.storage import Database, apply_migrations


async def test_imagegen_jobs_table_exists(tmp_path: Path) -> None:
    db = Database(tmp_path / "x.sqlite", pool_size=1)
    await db.connect()
    try:
        await apply_migrations(db)
        rows = await db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='imagegen_jobs'"
        )
        assert rows
        cols = {row["name"] for row in await db.fetchall("PRAGMA table_info(imagegen_jobs)")}
        assert {
            "id",
            "campaign_id",
            "backend",
            "status",
            "priority",
            "request_json",
            "queued_at",
            "started_at",
            "finished_at",
        } <= cols
    finally:
        await db.close()

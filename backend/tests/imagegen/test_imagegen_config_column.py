"""Migration 018: per-campaign imagegen_config TEXT column."""

from __future__ import annotations

from pathlib import Path

from grimoire.storage import Database, apply_migrations


async def test_campaigns_table_has_imagegen_config_column(tmp_path: Path) -> None:
    db = Database(tmp_path / "x.sqlite", pool_size=1)
    await db.connect()
    try:
        await apply_migrations(db)
        rows = await db.fetchall("PRAGMA table_info(campaigns)")
        names = {row["name"] for row in rows}
        assert "imagegen_config" in names, f"expected imagegen_config in {names}"
    finally:
        await db.close()

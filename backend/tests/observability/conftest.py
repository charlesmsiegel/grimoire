"""Shared fixtures for observability tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "obs.sqlite", pool_size=2)
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()

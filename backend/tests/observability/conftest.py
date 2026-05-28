"""Shared fixtures for observability tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(stamp_migrated_db(tmp_path / "obs.sqlite"), pool_size=2)
    await database.connect()
    try:
        yield database
    finally:
        await database.close()

"""Fixtures for expression-state tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.expressions.service import ExpressionStateService
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(stamp_migrated_db(tmp_path / "expr.sqlite"), pool_size=2)
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def service(db: Database) -> ExpressionStateService:
    return ExpressionStateService(db)

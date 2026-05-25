"""Tests for delta_log utility functions."""

from pathlib import Path

import pytest

from grimoire.state_store.delta_log import _coerce_for_column, validate_table_columns
from grimoire.storage import Database, apply_migrations


class TestCoerceForColumn:
    def test_does_not_double_encode_strings(self):
        already_json = '{"key": "value"}'
        result = _coerce_for_column("character_state", "knowledge_state", already_json)
        assert result == already_json

    def test_plain_string_passes_through(self):
        result = _coerce_for_column("character_state", "location_ref", "some_ref")
        assert result == "some_ref"

    def test_serializes_dicts(self):
        result = _coerce_for_column("character_state", "knowledge_state", {"key": "value"})
        assert isinstance(result, str)
        assert '"key"' in result

    def test_serializes_lists(self):
        result = _coerce_for_column("facts", "tags", ["a", "b"])
        assert isinstance(result, str)
        assert '"a"' in result

    def test_converts_bools_to_int(self):
        assert _coerce_for_column("character_state", "visible_to_pc", True) == 1
        assert _coerce_for_column("character_state", "visible_to_pc", False) == 0

    def test_none_passes_through(self):
        assert _coerce_for_column("character_state", "location_ref", None) is None

    def test_int_passes_through(self):
        assert _coerce_for_column("scenes", "turn_number", 42) == 42


class TestValidateTableColumns:
    @pytest.fixture
    async def db(self, tmp_path: Path):
        db = Database(tmp_path / "test.sqlite", pool_size=1)
        await db.connect()
        await apply_migrations(db)
        try:
            yield db
        finally:
            await db.close()

    async def test_no_warnings_when_columns_match(self, db: Database):
        async with db.acquire() as conn:
            warnings = await validate_table_columns(conn)
        assert warnings == []

    async def test_warns_on_undeclared_column(self, db: Database):
        async with db.acquire() as conn:
            await conn.execute("ALTER TABLE character_state ADD COLUMN extra_col TEXT")
            warnings = await validate_table_columns(conn)
        assert any("extra_col" in w and "exist in DB" in w for w in warnings)

    async def test_warns_on_missing_table(self, db: Database):
        async with db.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS calendar")
            warnings = await validate_table_columns(conn)
        assert any("calendar" in w and "missing from database" in w for w in warnings)

    async def test_warns_on_declared_column_missing_from_db(self, db: Database):
        async with db.acquire() as conn:
            # Rename a column to simulate a declared column missing from DB
            await conn.execute(
                "ALTER TABLE character_state RENAME COLUMN drift_score TO drift_score_old"
            )
            warnings = await validate_table_columns(conn)
        assert any("drift_score" in w and "missing from DB" in w for w in warnings)

"""Regression test for migration 036_remove_branches.sql.

Pre-fix, this migration had two bugs:

1. FK ON DELETE CASCADE wiped child tables. ``DROP TABLE scenes`` /
   ``DROP TABLE facts`` did an implicit DELETE FROM with foreign_keys
   enabled, cascading to posts / knowledge_state which had FK
   ``ON DELETE CASCADE`` constraints.
2. ``WHERE branch_id LIKE '%:main'`` silently dropped rows where
   ``branch_id`` was the bare string ``'main'`` (no colon). Code paths
   like SceneInit, turn_auditor, and alternates wrote the bare form.

The test builds a pre-migration DB by applying migrations 001-035 only,
seeds rows that exercise both bugs, runs 036, and asserts the rows
survived.
"""

from __future__ import annotations

import pytest

from grimoire.storage.db import Database
from grimoire.storage.migrations import (
    DEFAULT_MIGRATIONS_DIR,
    discover_migrations,
)


@pytest.fixture
async def pre_migration_db(tmp_path):
    """A DB with migrations 001-035 applied (everything before 036)."""
    db_path = tmp_path / "pre036.sqlite"
    db = Database(db_path, pool_size=1)
    await db.connect()

    migs = discover_migrations(DEFAULT_MIGRATIONS_DIR)
    pre = [m for m in migs if m.version < 36]
    final = next(m for m in migs if m.version == 36)

    # Replicate apply_migrations but stop before 036.
    async with db.acquire() as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        for m in pre:
            await conn.execute("BEGIN")
            for stmt in _split(m.sql):
                await conn.execute(stmt)
            await conn.execute(
                "INSERT INTO schema_version (version, name, applied_at) "
                "VALUES (?, ?, datetime('now'))",
                (m.version, m.name),
            )
            await conn.execute("COMMIT")

    yield db, final
    await db.close()


def _split(sql: str):
    from grimoire.storage.migrations import _split_statements

    return _split_statements(sql)


async def test_036_preserves_posts_and_knowledge_state(pre_migration_db):
    """Bug 1 regression: posts and knowledge_state survive 036."""
    db, m036 = pre_migration_db
    async with db.acquire() as conn:
        # Seed minimum rows for FK chains.
        await conn.execute(
            "INSERT INTO campaigns (id, name, created_at) "
            "VALUES ('c1', 'Test', '2026-05-28T00:00:00Z')"
        )
        await conn.execute(
            "INSERT INTO branches "
            "(id, campaign_id, label, parent_branch_id, rng_seed, created_at) "
            "VALUES ('c1:main', 'c1', 'main', NULL, 42, '2026-05-28T00:00:00Z')"
        )
        await conn.execute(
            "INSERT INTO scenes (id, campaign_id, branch_id, ordinal, slug, file_path) "
            "VALUES ('s1', 'c1', 'c1:main', 1, 'test', '/tmp/s1.md')"
        )
        await conn.execute(
            "INSERT INTO posts (id, scene_id, campaign_id, branch_id, order_in_scene, body) "
            "VALUES ('p1', 's1', 'c1', 'c1:main', 1, 'hello')"
        )
        await conn.execute(
            "INSERT INTO facts (id, campaign_id, branch_id, text) "
            "VALUES ('f1', 'c1', 'c1:main', 'sky is blue')"
        )
        await conn.execute(
            "INSERT INTO knowledge_state "
            "(fact_id, character_ref, campaign_id, branch_id, knows) "
            "VALUES ('f1', 'char-a', 'c1', 'c1:main', 1)"
        )

    # Apply 036.
    async with db.acquire() as conn:
        await conn.execute("BEGIN")
        for stmt in _split(m036.sql):
            await conn.execute(stmt)
        await conn.execute(
            "INSERT INTO schema_version (version, name, applied_at) "
            "VALUES (?, ?, datetime('now'))",
            (m036.version, m036.name),
        )
        await conn.execute("COMMIT")

    # Both children survived their parents' DROP.
    async with db.acquire() as conn:
        async with conn.execute("SELECT COUNT(*) FROM posts") as cur:
            assert (await cur.fetchone())[0] == 1
        async with conn.execute("SELECT COUNT(*) FROM knowledge_state") as cur:
            assert (await cur.fetchone())[0] == 1
        # FK to rebuilt parents still intact (insert with bad FK should fail).
        async with conn.execute(
            "PRAGMA foreign_key_check('posts')"
        ) as cur:
            assert await cur.fetchall() == []
        async with conn.execute(
            "PRAGMA foreign_key_check('knowledge_state')"
        ) as cur:
            assert await cur.fetchall() == []


async def test_036_keeps_rows_with_bare_main(pre_migration_db):
    """Bug 2 regression: rows with branch_id = 'main' (bare) are kept."""
    db, m036 = pre_migration_db
    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO campaigns (id, name, created_at) "
            "VALUES ('c1', 'Test', '2026-05-28T00:00:00Z')"
        )
        await conn.execute(
            "INSERT INTO branches "
            "(id, campaign_id, label, parent_branch_id, rng_seed, created_at) "
            "VALUES ('main', 'c1', 'main', NULL, 42, '2026-05-28T00:00:00Z')"
        )
        # Six tables flagged in the review — write a row with bare 'main'.
        await conn.execute(
            "INSERT INTO character_state (character_ref, campaign_id, branch_id, "
            "location_ref, drift_score, visible_to_pc, "
            "appearances_since_last_drift_check) "
            "VALUES ('char-a', 'c1', 'main', 'loc-1', 0, 0, 0)"
        )
        await conn.execute(
            "INSERT INTO location_state (location_ref, campaign_id, branch_id) "
            "VALUES ('loc-1', 'c1', 'main')"
        )
        await conn.execute(
            "INSERT INTO faction_state (faction_ref, campaign_id, branch_id) "
            "VALUES ('fac-1', 'c1', 'main')"
        )
        await conn.execute(
            "INSERT INTO deltas (id, campaign_id, branch_id, kind, applied_at) "
            "VALUES ('d1', 'c1', 'main', 'fact_add', '2026-05-28T00:00:00Z')"
        )
        await conn.execute(
            "INSERT INTO turn_audits (turn_id, campaign_id, branch_id, created_at) "
            "VALUES ('t1', 'c1', 'main', '2026-05-28T00:00:00Z')"
        )
        await conn.execute(
            "INSERT INTO current_alternate_delta_sets "
            "(campaign_id, branch_id, post_id, delta_set_id, updated_at) "
            "VALUES ('c1', 'main', 'p1', 'ds1', '2026-05-28T00:00:00Z')"
        )

    # Apply 036.
    async with db.acquire() as conn:
        await conn.execute("BEGIN")
        for stmt in _split(m036.sql):
            await conn.execute(stmt)
        await conn.execute(
            "INSERT INTO schema_version (version, name, applied_at) "
            "VALUES (?, ?, datetime('now'))",
            (m036.version, m036.name),
        )
        await conn.execute("COMMIT")

    # All bare-main rows survived.
    async with db.acquire() as conn:
        for table in (
            "character_state",
            "location_state",
            "faction_state",
            "deltas",
            "turn_audits",
            "current_alternate_delta_sets",
        ):
            async with conn.execute(f"SELECT COUNT(*) FROM {table}") as cur:
                count = (await cur.fetchone())[0]
                assert count == 1, f"{table} lost its bare-main row"

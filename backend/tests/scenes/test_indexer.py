"""Tests for the SceneIndexer (§1) — keeps SQLite scenes/posts mirrored."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.scenes import (
    AuthorKind,
    InMemoryEventBus,
    SceneInit,
    SceneManager,
    SceneManagerConfig,
    new_post,
)
from grimoire.scenes.indexer import SceneIndexer
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.sqlite", pool_size=2)
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def setup(tmp_path: Path, db):
    bus = InMemoryEventBus()
    data_root = tmp_path / "data"
    data_root.mkdir()
    manager = SceneManager(
        data_root,
        config=SceneManagerConfig(running_summary_every_n_posts=0),
        event_bus=bus,
    )
    indexer = SceneIndexer(manager, db, bus)
    indexer.start()
    try:
        yield manager, indexer, db
    finally:
        await indexer.stop()


async def test_scene_start_creates_index_row(setup) -> None:
    manager, _, db = setup
    scene = await manager.start_scene(
        SceneInit(campaign_id="c", title="Elysium", present_pc_refs=["alistair"])
    )
    row = await db.fetchone("SELECT * FROM scenes WHERE id = ?", (scene.id,))
    assert row is not None
    assert row["campaign_id"] == "c"
    assert row["slug"] == "elysium"
    assert row["title"] == "Elysium"


async def test_post_append_creates_post_row(setup) -> None:
    manager, _, db = setup
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    post = new_post(author_kind=AuthorKind.NARRATOR, body="hello world", is_player=False)
    await manager.append_post(scene.id, post)

    row = await db.fetchone(
        "SELECT * FROM posts WHERE scene_id = ? ORDER BY order_in_scene", (scene.id,)
    )
    assert row is not None
    assert row["id"] == post.id
    assert row["body_excerpt"] == "hello world"
    assert row["body_hash"]
    assert row["order_in_scene"] == 1
    # Scene row's post_count should track the append.
    scene_row = await db.fetchone("SELECT post_count FROM scenes WHERE id = ?", (scene.id,))
    assert int(scene_row["post_count"]) == 1


async def test_post_delete_renumbers_index(setup) -> None:
    manager, _, db = setup
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    for i in range(3):
        await manager.append_post(
            scene.id,
            new_post(author_kind=AuthorKind.NARRATOR, body=f"line {i}", is_player=False),
        )
    second = (await manager.get_posts(scene.id))[1]
    await manager.delete_post(second.id, source="user")

    rows = await db.fetchall(
        "SELECT order_in_scene, body_excerpt FROM posts WHERE scene_id = ? ORDER BY order_in_scene",
        (scene.id,),
    )
    assert [int(r["order_in_scene"]) for r in rows] == [1, 2]
    assert [r["body_excerpt"] for r in rows] == ["line 0", "line 2"]


async def test_scene_close_marks_row_closed(setup) -> None:
    manager, _, db = setup
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    await manager.close_scene(scene.id, closed_at_turn="t-final")
    row = await db.fetchone("SELECT closed, closed_at_turn FROM scenes WHERE id = ?", (scene.id,))
    assert int(row["closed"]) == 1
    assert row["closed_at_turn"] == "t-final"


async def test_backfill_reconciles_disk(tmp_path: Path, db) -> None:
    """Direct edit while the indexer wasn't running gets picked up on startup."""
    bus = InMemoryEventBus()
    data_root = tmp_path / "data"
    data_root.mkdir()
    manager = SceneManager(
        data_root,
        config=SceneManagerConfig(running_summary_every_n_posts=0),
        event_bus=bus,
    )
    # Drive the manager directly without the indexer running.
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    await manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="silently appended", is_player=False),
    )
    # No indexer was attached, so the table is still empty.
    row = await db.fetchone("SELECT COUNT(*) AS n FROM scenes")
    assert int(row["n"]) == 0

    indexer = SceneIndexer(manager, db, bus)
    await indexer.backfill()

    row = await db.fetchone("SELECT post_count FROM scenes WHERE id = ?", (scene.id,))
    assert row is not None
    assert int(row["post_count"]) == 1
    post_row = await db.fetchone("SELECT body_excerpt FROM posts WHERE scene_id = ?", (scene.id,))
    assert post_row["body_excerpt"] == "silently appended"


async def test_scene_file_changed_resyncs_posts(setup) -> None:
    """An external .md edit (via reindex_from_disk) rewrites the index posts."""
    manager, _, db = setup
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    await manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="first", is_player=False),
    )
    md_path = await manager.get_scene_file_path(scene.id)
    # Simulate user adding a post by hand and the watcher firing reindex.
    md_path.write_text(
        "## Post 1 — narrator\n\nfirst\n\n## Post 2 — narrator\n\nadded by hand\n",
        encoding="utf-8",
    )
    await manager.reindex_from_disk(scene.id)

    rows = await db.fetchall(
        "SELECT order_in_scene, body_excerpt FROM posts WHERE scene_id = ? ORDER BY order_in_scene",
        (scene.id,),
    )
    assert [int(r["order_in_scene"]) for r in rows] == [1, 2]
    assert rows[1]["body_excerpt"] == "added by hand"

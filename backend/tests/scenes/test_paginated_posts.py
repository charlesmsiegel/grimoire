"""Paginated post loading from SQLite."""

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
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(stamp_migrated_db(tmp_path / "test.sqlite"), pool_size=2)
    await database.connect()
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


async def test_get_posts_paginated_returns_latest(setup) -> None:
    manager, _, db = setup
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    for i in range(10):
        post = new_post(
            author_kind=AuthorKind.NARRATOR,
            body=f"Post {i}",
            is_player=False,
        )
        await manager.append_post(scene.id, post)

    rows = await db.fetchall(
        """
        SELECT id, body, order_in_scene FROM posts
        WHERE scene_id = ?
        ORDER BY order_in_scene DESC
        LIMIT ?
        """,
        (scene.id, 5),
    )
    assert len(rows) == 5
    assert rows[0]["order_in_scene"] == 10
    assert rows[4]["order_in_scene"] == 6


async def test_get_posts_paginated_cursor(setup) -> None:
    manager, _, db = setup
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    for i in range(10):
        post = new_post(
            author_kind=AuthorKind.NARRATOR,
            body=f"Post {i}",
            is_player=False,
        )
        await manager.append_post(scene.id, post)

    rows = await db.fetchall(
        """
        SELECT id, body, order_in_scene FROM posts
        WHERE scene_id = ? AND order_in_scene < ?
        ORDER BY order_in_scene DESC
        LIMIT ?
        """,
        (scene.id, 6, 3),
    )
    assert len(rows) == 3
    assert rows[0]["order_in_scene"] == 5
    assert rows[2]["order_in_scene"] == 3


async def test_get_posts_paginated_via_manager(setup) -> None:
    manager, _, db = setup
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    for i in range(10):
        post = new_post(
            author_kind=AuthorKind.NARRATOR,
            body=f"Post {i}",
            is_player=False,
        )
        await manager.append_post(scene.id, post)

    posts = await manager.get_posts_paginated(scene.id, limit=5, db=db)
    assert len(posts) == 5
    assert posts[0].order_in_scene == 6
    assert posts[4].order_in_scene == 10
    assert posts[4].body == "Post 9"

    older = await manager.get_posts_paginated(scene.id, limit=3, before=6, db=db)
    assert len(older) == 3
    assert older[0].order_in_scene == 3
    assert older[2].order_in_scene == 5

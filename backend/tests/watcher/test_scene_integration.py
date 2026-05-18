"""Watcher → Scene Manager integration (§3).

When a scene's markdown or sidecar file changes on disk, the watcher forwards
the event to ``SceneManager.reindex_from_disk`` so post records are rebuilt
and the conflict flag fires when the on-disk body diverges from the last
app-written hash.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.event_bus import EventBus
from grimoire.scenes import (
    AuthorKind,
    SceneInit,
    SceneManager,
    SceneManagerConfig,
    new_post,
)
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.watcher import FileWatcher


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
    data_root = tmp_path / "data"
    (data_root / "library").mkdir(parents=True)
    (data_root / "campaigns").mkdir(parents=True)
    bus = EventBus()
    store = StateStore(db, data_root)
    manager = SceneManager(
        data_root,
        config=SceneManagerConfig(running_summary_every_n_posts=0),
        event_bus=bus,
    )
    watcher = FileWatcher(
        data_root=data_root, store=store, bus=bus, scene_manager=manager
    )
    events: list = []

    async def on_event(event):
        events.append(event)

    bus.subscribe("scene_file_changed", on_event)
    try:
        yield manager, watcher, events
    finally:
        await watcher.stop()


async def test_external_edit_routes_through_scene_manager(setup) -> None:
    manager, watcher, events = setup
    scene = await manager.start_scene(
        SceneInit(campaign_id="c", title="Scene", present_pc_refs=["pc"])
    )
    await manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="original", is_player=False),
    )
    md_path = await manager.get_scene_file_path(scene.id)
    # External edit: prepend a new post heading by hand.
    md_path.write_text(
        "## Post 1 — narrator\n\noriginal\n\n## Post 2 — narrator\n\nfresh\n",
        encoding="utf-8",
    )
    await watcher.process_path(md_path)

    refreshed = await manager.get_scene(scene.id)
    assert refreshed.post_count == 2
    # The manager's emit (not the watcher's) carries the conflict flag.
    scene_events = [e for e in events if e.type == "scene_file_changed"]
    assert scene_events, "scene_file_changed was not emitted"
    payload = scene_events[-1].payload
    assert payload.get("conflict") is True
    assert payload.get("post_count") == 2


async def test_self_write_is_not_a_conflict(setup) -> None:
    """Writes that flow through the manager have a matching hash — no conflict."""
    manager, watcher, events = setup
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    await manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="line 1", is_player=False),
    )
    md_path = await manager.get_scene_file_path(scene.id)
    # Re-fire the watcher event on the file we just wrote. The manager's
    # known-body-hash should match what's on disk; conflict must be False.
    await watcher.process_path(md_path)

    scene_events = [e for e in events if e.type == "scene_file_changed"]
    # The watcher dedupes identical-hash events at its own layer (so no event
    # at all is acceptable). What must NOT happen: a conflict=True emit.
    for evt in scene_events:
        assert evt.payload.get("conflict") is False

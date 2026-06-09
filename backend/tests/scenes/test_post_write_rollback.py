"""Two-file commit discipline for post mutations (#586).

A failure while writing one file of the ``.md`` + sidecar pair must leave the
files *and* the manager's in-memory caches at the pre-call state — all-old or
all-new, never half-applied — and the next mutation must succeed cleanly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import grimoire.scenes.manager as manager_module
from grimoire.scenes import (
    POST_APPENDED,
    POST_DELETED,
    AuthorKind,
    InMemoryEventBus,
    SceneInit,
    SceneManager,
    SceneManagerConfig,
    new_post,
)


def _manager(tmp_path: Path) -> tuple[SceneManager, InMemoryEventBus]:
    bus = InMemoryEventBus()
    manager = SceneManager(
        tmp_path,
        config=SceneManagerConfig(running_summary_every_n_posts=0),
        event_bus=bus,
    )
    return manager, bus


def _scene_paths(tmp_path: Path, basename: str) -> tuple[Path, Path]:
    md_path = tmp_path / "campaigns" / "c" / "scenes" / f"{basename}.md"
    return md_path, md_path.with_suffix(".yaml")


def _raise_sidecar_failure(*args: object, **kwargs: object) -> None:
    raise RuntimeError("sidecar write failed")


async def test_append_sidecar_failure_restores_files_and_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, bus = _manager(tmp_path)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Elysium"))
    await manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="The tower is candle-lit.", is_player=False),
    )
    md_path, yaml_path = _scene_paths(tmp_path, "0001-elysium")
    md_before = md_path.read_bytes()
    yaml_before = yaml_path.read_bytes()
    appended_before = sum(1 for e in bus.events if e.type == POST_APPENDED)

    monkeypatch.setattr(manager_module, "write_sidecar", _raise_sidecar_failure)
    with pytest.raises(RuntimeError, match="sidecar write failed"):
        await manager.append_post(
            scene.id,
            new_post(
                author_kind=AuthorKind.PC,
                author_pc_ref="alistair",
                body="I incline my head.",
                is_player=True,
            ),
        )

    # All-old: both files byte-identical, no event emitted for the failed post.
    assert md_path.read_bytes() == md_before
    assert yaml_path.read_bytes() == yaml_before
    assert sum(1 for e in bus.events if e.type == POST_APPENDED) == appended_before
    # Memory agrees with the files.
    assert (await manager.get_scene(scene.id)).post_count == 1
    assert [p.order_in_scene for p in await manager.get_posts(scene.id)] == [1]
    assert "2" not in manager._post_records.get(scene.id, {})
    assert (scene.campaign_id, "alistair") not in manager._pc_current_scene

    # Consistency restored: the next append lands cleanly at order 2.
    monkeypatch.undo()
    retry = new_post(
        author_kind=AuthorKind.PC,
        author_pc_ref="alistair",
        body="I incline my head.",
        is_player=True,
    )
    await manager.append_post(scene.id, retry)
    posts = await manager.get_posts(scene.id)
    assert [p.order_in_scene for p in posts] == [1, 2]
    assert posts[1].id == retry.id
    assert manager._pc_current_scene[(scene.campaign_id, "alistair")] == scene.id


async def test_delete_sidecar_failure_restores_files_and_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, bus = _manager(tmp_path)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Elysium"))
    for body in ("One.", "Two.", "Three."):
        await manager.append_post(
            scene.id, new_post(author_kind=AuthorKind.NARRATOR, body=body, is_player=False)
        )
    posts = await manager.get_posts(scene.id)
    md_path, yaml_path = _scene_paths(tmp_path, "0001-elysium")
    md_before = md_path.read_bytes()
    yaml_before = yaml_path.read_bytes()

    monkeypatch.setattr(manager_module, "write_sidecar", _raise_sidecar_failure)
    with pytest.raises(RuntimeError, match="sidecar write failed"):
        await manager.delete_post(posts[1].id, source="user")

    # All-old: the .md rewrite (which had already shifted orders) was rolled
    # back together with the sidecar; no POST_DELETED event fired.
    assert md_path.read_bytes() == md_before
    assert yaml_path.read_bytes() == yaml_before
    assert not any(e.type == POST_DELETED for e in bus.events)
    after = await manager.get_posts(scene.id)
    assert [(p.order_in_scene, p.id) for p in after] == [(p.order_in_scene, p.id) for p in posts]
    assert (await manager.get_scene(scene.id)).post_count == 3

    # Consistency restored: the delete now succeeds and shifts identity intact.
    monkeypatch.undo()
    await manager.delete_post(posts[1].id, source="user")
    remaining = await manager.get_posts(scene.id)
    assert [(p.order_in_scene, p.id) for p in remaining] == [
        (1, posts[0].id),
        (2, posts[2].id),
    ]
    assert (await manager.get_scene(scene.id)).post_count == 2


async def test_edit_md_write_failure_restores_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _ = _manager(tmp_path)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Elysium"))
    await manager.append_post(
        scene.id, new_post(author_kind=AuthorKind.NARRATOR, body="Original.", is_player=False)
    )
    posts = await manager.get_posts(scene.id)
    md_path, _ = _scene_paths(tmp_path, "0001-elysium")
    md_before = md_path.read_bytes()

    def torn_write(path: Path, *args: object, **kwargs: object) -> None:
        # Simulate a non-atomic writer dying mid-write: target corrupted.
        path.write_text("corrupted", encoding="utf-8")
        raise RuntimeError("md write failed")

    monkeypatch.setattr(manager_module, "write_body", torn_write)
    with pytest.raises(RuntimeError, match="md write failed"):
        await manager.edit_post(posts[0].id, "Edited.", source="user")

    assert md_path.read_bytes() == md_before
    assert (await manager.get_posts(scene.id))[0].body == "Original."

    monkeypatch.undo()
    await manager.edit_post(posts[0].id, "Edited.", source="user")
    assert (await manager.get_posts(scene.id))[0].body == "Edited."


async def test_truncate_sidecar_failure_restores_files_and_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, bus = _manager(tmp_path)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Elysium"))
    for body in ("One.", "Two.", "Three."):
        await manager.append_post(
            scene.id, new_post(author_kind=AuthorKind.NARRATOR, body=body, is_player=False)
        )
    posts = await manager.get_posts(scene.id)
    md_path, yaml_path = _scene_paths(tmp_path, "0001-elysium")
    md_before = md_path.read_bytes()
    yaml_before = yaml_path.read_bytes()

    monkeypatch.setattr(manager_module, "write_sidecar", _raise_sidecar_failure)
    with pytest.raises(RuntimeError, match="sidecar write failed"):
        await manager.truncate_scene_from(posts[1].id, source="user")

    assert md_path.read_bytes() == md_before
    assert yaml_path.read_bytes() == yaml_before
    assert not any(e.type == POST_DELETED for e in bus.events)
    after = await manager.get_posts(scene.id)
    assert [(p.order_in_scene, p.id) for p in after] == [(p.order_in_scene, p.id) for p in posts]
    assert (await manager.get_scene(scene.id)).post_count == 3

    monkeypatch.undo()
    removed = await manager.truncate_scene_from(posts[1].id, source="user")
    assert [p.id for p in removed] == [posts[1].id, posts[2].id]
    assert [(p.order_in_scene, p.id) for p in await manager.get_posts(scene.id)] == [
        (1, posts[0].id)
    ]

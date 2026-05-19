"""Per-post alternates on the scene sidecar + .md rebuild from primaries.

See ``docs/superpowers/specs/2026-05-19-swipes-alternates-design.md``.
"""

from __future__ import annotations

from datetime import datetime
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
from grimoire.scenes.storage import (
    read_sidecar_post_records,
    scene_paths,
)
from grimoire.scenes.types import Alternate


def _manager(tmp_path: Path) -> SceneManager:
    return SceneManager(
        tmp_path,
        config=SceneManagerConfig(running_summary_every_n_posts=0),
        event_bus=InMemoryEventBus(),
    )


async def _seed_scene_with_model_post(tmp_path: Path) -> tuple[SceneManager, str, str]:
    manager = _manager(tmp_path)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Test"))
    post = new_post(
        author_kind=AuthorKind.NARRATOR,
        body="Original prose.",
        is_player=False,
    )
    await manager.append_post(scene.id, post)
    return manager, scene.id, post.id


async def test_alternates_round_trip_through_sidecar(tmp_path: Path) -> None:
    manager, scene_id, post_id = await _seed_scene_with_model_post(tmp_path)
    alt = Alternate(
        id="a_regen1",
        post_id=post_id,
        text="A bolder rewrite.",
        delta_set_id="ds_99",
        author_kind=AuthorKind.NARRATOR,
        model="claude-opus-4-7",
        prompt_hash="ab12",
        tokens=420,
        created_at=datetime(2026, 5, 19, 14, 0, 0),
    )
    await manager.append_alternate(post_id, alt)

    # Two alternates now: the synthesized primary (original body) + the new one.
    scene = await manager.get_scene(scene_id)
    yaml_path = scene_paths(tmp_path, scene)[1]
    records = read_sidecar_post_records(yaml_path)
    rec = records["1"]
    assert len(rec.alternates) == 2
    assert rec.primary_alternate_id is not None
    primary = next(a for a in rec.alternates if a.is_primary)
    assert primary.text == "Original prose."
    other = next(a for a in rec.alternates if not a.is_primary)
    assert other.id == "a_regen1"
    assert other.delta_set_id == "ds_99"
    assert other.tokens == 420


async def test_set_primary_alternate_flips_flags(tmp_path: Path) -> None:
    manager, scene_id, post_id = await _seed_scene_with_model_post(tmp_path)
    alt = Alternate(
        id="a_new",
        post_id=post_id,
        text="Alternative.",
        delta_set_id="ds_x",
        author_kind=AuthorKind.NARRATOR,
    )
    await manager.append_alternate(post_id, alt)
    await manager.set_primary_alternate(post_id, "a_new")
    posts = await manager.get_posts(scene_id)
    rec_alts = posts[0].alternates
    assert posts[0].primary_alternate_id == "a_new"
    assert sum(1 for a in rec_alts if a.is_primary) == 1
    assert next(a for a in rec_alts if a.is_primary).id == "a_new"


async def test_rebuild_md_from_primaries_uses_primary_text(tmp_path: Path) -> None:
    manager, scene_id, post_id = await _seed_scene_with_model_post(tmp_path)
    alt = Alternate(
        id="a_new",
        post_id=post_id,
        text="The bolder rewrite.",
        delta_set_id="ds_x",
        author_kind=AuthorKind.NARRATOR,
    )
    await manager.append_alternate(post_id, alt)
    await manager.set_primary_alternate(post_id, "a_new")
    await manager.rebuild_md_from_primaries(scene_id)

    scene = await manager.get_scene(scene_id)
    md_path = scene_paths(tmp_path, scene)[0]
    text = md_path.read_text(encoding="utf-8")
    assert "The bolder rewrite." in text
    assert "Original prose." not in text


async def test_update_alternate_pin(tmp_path: Path) -> None:
    manager, _scene_id, post_id = await _seed_scene_with_model_post(tmp_path)
    alt = Alternate(
        id="a_new",
        post_id=post_id,
        text="x",
        delta_set_id="ds_x",
        author_kind=AuthorKind.NARRATOR,
    )
    await manager.append_alternate(post_id, alt)
    await manager.update_alternate(post_id, "a_new", pinned=True)
    posts = await manager.get_posts(_scene_id)
    new_alt = next(a for a in posts[0].alternates if a.id == "a_new")
    assert new_alt.pinned is True


async def test_remove_primary_alternate_rejected(tmp_path: Path) -> None:
    manager, _scene_id, post_id = await _seed_scene_with_model_post(tmp_path)
    alt = Alternate(
        id="a_new",
        post_id=post_id,
        text="x",
        delta_set_id="ds_x",
        author_kind=AuthorKind.NARRATOR,
    )
    await manager.append_alternate(post_id, alt)
    # Primary is the implicit one synthesized from the original body.
    posts = await manager.get_posts(_scene_id)
    primary_id = posts[0].primary_alternate_id
    assert primary_id is not None
    with pytest.raises(ValueError):
        await manager.remove_alternate(post_id, primary_id)


async def test_remove_non_primary_alternate(tmp_path: Path) -> None:
    manager, scene_id, post_id = await _seed_scene_with_model_post(tmp_path)
    alt = Alternate(
        id="a_new",
        post_id=post_id,
        text="x",
        delta_set_id="ds_x",
        author_kind=AuthorKind.NARRATOR,
    )
    await manager.append_alternate(post_id, alt)
    removed = await manager.remove_alternate(post_id, "a_new")
    assert removed.id == "a_new"
    posts = await manager.get_posts(scene_id)
    assert all(a.id != "a_new" for a in posts[0].alternates)

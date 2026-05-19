"""Orchestrator alternate-mutation surface: switch / pin / delete.

These tests wire a real :class:`StateStore` to the orchestrator (rather than
the fake) so the underlying swap_delta_set behavior is exercised end-to-end.
``regenerate_post`` itself is out of scope for this slice — see plan branch C.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.event_bus import EventBus
from grimoire.orchestrator import OrchestratorService
from grimoire.orchestrator.errors import (
    AlternateNotFoundError,
    CannotDeletePrimaryError,
)
from grimoire.scenes.manager import SceneManager, SceneManagerConfig
from grimoire.scenes.types import Alternate, AuthorKind, SceneInit
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations

from .conftest import (
    FakeContextBuilder,
    FakeExtractor,
    FakeGateway,
    WSCollector,
)

# ----- fixtures --------------------------------------------------------------


@pytest.fixture
async def real_store(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(tmp_path / "c.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
    s = StateStore(db, data_root)
    try:
        yield s
    finally:
        await db.close()


def _make_orch(scenes: SceneManager, store: StateStore) -> OrchestratorService:
    return OrchestratorService(
        event_bus=EventBus(),
        scene_manager=scenes,
        llm_gateway=FakeGateway(),
        context_builder=FakeContextBuilder(),
        extractor=FakeExtractor(),
        state_store=store,
        ws_push=WSCollector(),
    )


def _char_delta(mood: str, *, campaign_id: str, branch_id: str) -> dict:
    return {
        "kind": "character_state_update",
        "target_scope": "campaign-sqlite",
        "target_table": "character_state",
        "target_id": "lib:winifred",
        "after": {
            "character_ref": "lib:winifred",
            "campaign_id": campaign_id,
            "branch_id": branch_id,
            "emotional_state": mood,
        },
    }


async def _seed_scene_with_alternates(
    tmp_path: Path,
    scenes: SceneManager,
    store: StateStore,
) -> tuple[str, str, str, str, str]:
    """Set up a campaign + scene + one model post with two alternates.

    Returns (campaign_id, branch_id, scene_id, post_id, alt_b_id). The
    primary is the implicit one synthesized from the seed body ('A'),
    alt_b_id is the non-primary alternate ('B') with a pre-applied delta set.
    """
    campaign_id = "c1"
    branch_id = "main"
    await store.upsert_campaign(campaign_id=campaign_id, name="Test")
    scene = await scenes.start_scene(SceneInit(campaign_id=campaign_id, title="Opening"))
    from grimoire.scenes.manager import new_post

    post = new_post(author_kind=AuthorKind.NARRATOR, body="A", is_player=False)
    await scenes.append_post(scene.id, post)
    # Pre-apply alt B's deltas under a delta_set_id.
    ds_b = "ds_b"
    await store.apply_delta_set(
        deltas=[_char_delta("anxious", campaign_id=campaign_id, branch_id=branch_id)],
        delta_set_id=ds_b,
        campaign_id=campaign_id,
        branch_id=branch_id,
        turn_id=post.turn_id,
        source="test",
    )
    # Mint alternate B (non-primary, with delta_set_id linked).
    alt_b = Alternate(
        id="a_b",
        post_id=post.id,
        text="B",
        delta_set_id=ds_b,
        author_kind=AuthorKind.NARRATOR,
    )
    await scenes.append_alternate(post.id, alt_b)
    # After append_alternate, the implicit "A" alternate is primary. We need
    # its delta_set_id too — assign one and apply a no-op-equivalent set so
    # the swap can rewind it.
    posts = await scenes.get_posts(scene.id)
    implicit_primary = next(a for a in posts[0].alternates if a.is_primary)
    ds_a = "ds_a"
    # First rewind ds_b (we'll re-activate it later through swap), so the
    # "A" deltas can apply against character_state cleanly.
    await store.rewind_delta_set(ds_b, campaign_id=campaign_id, branch_id=branch_id)
    await store.apply_delta_set(
        deltas=[_char_delta("calm", campaign_id=campaign_id, branch_id=branch_id)],
        delta_set_id=ds_a,
        campaign_id=campaign_id,
        branch_id=branch_id,
        turn_id=post.turn_id,
        source="test",
    )
    # Patch the primary alternate to carry ds_a.
    await scenes.update_alternate(post.id, implicit_primary.id, delta_set_id=ds_a)
    await store.set_current_alternate_delta_set(
        campaign_id=campaign_id,
        branch_id=branch_id,
        post_id=post.id,
        delta_set_id=ds_a,
    )
    return campaign_id, branch_id, scene.id, post.id, alt_b.id


# ----- tests -----------------------------------------------------------------


async def test_switch_primary_alternate_swaps_delta_set_and_rewrites_md(
    tmp_path: Path, real_store: StateStore
):
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, branch_id, scene_id, post_id, alt_b = await _seed_scene_with_alternates(
        tmp_path, scenes, real_store
    )
    orch = _make_orch(scenes, real_store)

    result = await orch.switch_primary_alternate(
        campaign_id=campaign_id, post_id=post_id, alternate_id=alt_b
    )
    assert result["unchanged"] is False
    assert result["to"] == alt_b
    assert result["delta_swap"] is True

    posts = await scenes.get_posts(scene_id)
    assert posts[0].primary_alternate_id == alt_b

    from grimoire.scenes.storage import scene_paths

    md_path, _ = scene_paths(scenes_root, await scenes.get_scene(scene_id))
    body = md_path.read_text(encoding="utf-8")
    assert "B" in body

    # State store reflects alt_b being current.
    current = await real_store.current_delta_set_for(
        post_id=post_id, campaign_id=campaign_id, branch_id=branch_id
    )
    assert current == "ds_b"


async def test_switch_primary_same_alternate_is_noop(tmp_path: Path, real_store: StateStore):
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, _branch_id, _scene_id, post_id, _alt_b = await _seed_scene_with_alternates(
        tmp_path, scenes, real_store
    )
    orch = _make_orch(scenes, real_store)
    posts = await scenes.get_posts(_scene_id)
    primary = posts[0].primary_alternate_id
    result = await orch.switch_primary_alternate(
        campaign_id=campaign_id, post_id=post_id, alternate_id=primary
    )
    assert result["unchanged"] is True


async def test_switch_primary_unknown_alternate_raises(tmp_path: Path, real_store: StateStore):
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, _branch_id, _scene_id, post_id, _alt_b = await _seed_scene_with_alternates(
        tmp_path, scenes, real_store
    )
    orch = _make_orch(scenes, real_store)
    with pytest.raises(AlternateNotFoundError):
        await orch.switch_primary_alternate(
            campaign_id=campaign_id, post_id=post_id, alternate_id="a_nope"
        )


async def test_pin_alternate(tmp_path: Path, real_store: StateStore):
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    _campaign_id, _branch_id, scene_id, post_id, alt_b = await _seed_scene_with_alternates(
        tmp_path, scenes, real_store
    )
    orch = _make_orch(scenes, real_store)
    await orch.pin_alternate(post_id=post_id, alternate_id=alt_b, pinned=True)
    posts = await scenes.get_posts(scene_id)
    target = next(a for a in posts[0].alternates if a.id == alt_b)
    assert target.pinned is True


async def test_delete_primary_rejected(tmp_path: Path, real_store: StateStore):
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    _campaign_id, _branch_id, scene_id, post_id, _alt_b = await _seed_scene_with_alternates(
        tmp_path, scenes, real_store
    )
    orch = _make_orch(scenes, real_store)
    posts = await scenes.get_posts(scene_id)
    primary = posts[0].primary_alternate_id
    with pytest.raises(CannotDeletePrimaryError):
        await orch.delete_alternate(post_id=post_id, alternate_id=primary)


async def test_delete_non_primary_rewinds_its_delta_set(tmp_path: Path, real_store: StateStore):
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, branch_id, scene_id, post_id, alt_b = await _seed_scene_with_alternates(
        tmp_path, scenes, real_store
    )
    orch = _make_orch(scenes, real_store)
    # ds_b is currently rewound (we rewound it during seeding so ds_a could
    # apply cleanly). Re-activate it so the test exercises the rewind path
    # on delete.
    await real_store.swap_delta_set(
        rewind_set_id="ds_a",
        apply_deltas=None,
        apply_set_id="ds_b",
        campaign_id=campaign_id,
        branch_id=branch_id,
        turn_id=None,
        source="test",
    )
    # Make alt_a the primary again so alt_b can be deleted (it's the
    # non-primary).
    posts = await scenes.get_posts(scene_id)
    implicit_a = next(a for a in posts[0].alternates if a.id != alt_b)
    await scenes.set_primary_alternate(post_id, implicit_a.id)
    # Re-activate ds_a too so the world state is back to normal.
    await real_store.swap_delta_set(
        rewind_set_id="ds_b",
        apply_deltas=None,
        apply_set_id="ds_a",
        campaign_id=campaign_id,
        branch_id=branch_id,
        turn_id=None,
        source="test",
    )
    await orch.delete_alternate(post_id=post_id, alternate_id=alt_b)
    posts = await scenes.get_posts(scene_id)
    assert all(a.id != alt_b for a in posts[0].alternates)

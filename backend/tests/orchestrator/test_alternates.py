"""Orchestrator alternate-mutation surface: regenerate / switch / pin / delete.

These tests wire a real :class:`StateStore` to the orchestrator (rather than
the fake) so the underlying delta-set behavior is exercised end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.event_bus import EventBus
from grimoire.orchestrator import OrchestratorService
from grimoire.orchestrator.errors import (
    AlternateNotFoundError,
    CannotDeletePrimaryError,
    LatestPostOnlyError,
)
from grimoire.scenes.manager import SceneManager, SceneManagerConfig
from grimoire.scenes.types import Alternate, AuthorKind, SceneInit
from grimoire.state_store import StateStore
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db
from grimoire.types.state import DeltaKind, StateDelta

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
    db = Database(stamp_migrated_db(tmp_path / "c.sqlite"), pool_size=2)
    await db.connect()
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


def _char_delta(mood: str, *, campaign_id: str) -> dict:
    return {
        "kind": "character_state_update",
        "target_scope": "campaign-sqlite",
        "target_table": "character_state",
        "target_id": "lib:winifred",
        "after": {
            "character_ref": "lib:winifred",
            "campaign_id": campaign_id,
            "emotional_state": mood,
        },
    }


async def _seed_scene_with_alternates(
    tmp_path: Path,
    scenes: SceneManager,
    store: StateStore,
) -> tuple[str, str, str, str]:
    """Set up a campaign + scene + one model post with two alternates.

    Returns (campaign_id, scene_id, post_id, alt_b_id). The
    primary is the implicit one synthesized from the seed body ('A'),
    alt_b_id is the non-primary alternate ('B') with a pre-applied delta set.
    """
    campaign_id = "c1"
    await store.upsert_campaign(campaign_id=campaign_id, name="Test")
    scene = await scenes.start_scene(SceneInit(campaign_id=campaign_id, title="Opening"))
    from grimoire.scenes.manager import new_post

    post = new_post(author_kind=AuthorKind.NARRATOR, body="A", is_player=False)
    await scenes.append_post(scene.id, post)
    # Pre-apply alt B's deltas under a delta_set_id.
    ds_b = "ds_b"
    await store.apply_delta_set(
        deltas=[_char_delta("anxious", campaign_id=campaign_id)],
        delta_set_id=ds_b,
        campaign_id=campaign_id,
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
    await store.rewind_delta_set(ds_b, campaign_id=campaign_id)
    await store.apply_delta_set(
        deltas=[_char_delta("calm", campaign_id=campaign_id)],
        delta_set_id=ds_a,
        campaign_id=campaign_id,
        turn_id=post.turn_id,
        source="test",
    )
    # Patch the primary alternate to carry ds_a.
    await scenes.update_alternate(post.id, implicit_primary.id, delta_set_id=ds_a)
    await store.set_current_alternate_delta_set(
        campaign_id=campaign_id,
        post_id=post.id,
        delta_set_id=ds_a,
    )
    return campaign_id, scene.id, post.id, alt_b.id


# ----- tests -----------------------------------------------------------------


async def test_switch_primary_alternate_swaps_delta_set_and_rewrites_md(
    tmp_path: Path, real_store: StateStore
):
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, scene_id, post_id, alt_b = await _seed_scene_with_alternates(
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
    current = await real_store.current_delta_set_for(post_id=post_id, campaign_id=campaign_id)
    assert current == "ds_b"


async def test_switch_primary_same_alternate_is_noop(tmp_path: Path, real_store: StateStore):
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, _scene_id, post_id, _alt_b = await _seed_scene_with_alternates(
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
    campaign_id, _scene_id, post_id, _alt_b = await _seed_scene_with_alternates(
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
    _campaign_id, scene_id, post_id, alt_b = await _seed_scene_with_alternates(
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
    _campaign_id, scene_id, post_id, _alt_b = await _seed_scene_with_alternates(
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
    campaign_id, scene_id, post_id, alt_b = await _seed_scene_with_alternates(
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
        turn_id=None,
        source="test",
    )
    await orch.delete_alternate(post_id=post_id, alternate_id=alt_b)
    posts = await scenes.get_posts(scene_id)
    assert all(a.id != alt_b for a in posts[0].alternates)


# ----- regenerate_post -------------------------------------------------------


async def _seed_scene_with_one_model_post(
    scenes: SceneManager, store: StateStore
) -> tuple[str, str, str]:
    """Player post + model post (with primary alternate + delta set).

    Returns (campaign_id, scene_id, model_post_id).
    """
    campaign_id = "c1"
    await store.upsert_campaign(campaign_id=campaign_id, name="Test")
    scene = await scenes.start_scene(SceneInit(campaign_id=campaign_id, title="Opening"))
    from grimoire.scenes.manager import new_post

    player = new_post(author_kind=AuthorKind.PC, body="I knock.", is_player=True)
    await scenes.append_post(scene.id, player)
    model = new_post(author_kind=AuthorKind.NARRATOR, body="A door creaks.", is_player=False)
    await scenes.append_post(scene.id, model)
    # Seed the primary alternate with a tracked delta set so regenerate's
    # swap path is exercised (rather than the legacy plain-apply branch).
    ds_primary = "ds_primary"
    await store.apply_delta_set(
        deltas=[_char_delta("calm", campaign_id=campaign_id)],
        delta_set_id=ds_primary,
        campaign_id=campaign_id,
        turn_id=model.turn_id,
        source="test",
    )
    primary = Alternate(
        id="a_primary",
        post_id=model.id,
        text=model.body,
        delta_set_id=ds_primary,
        author_kind=AuthorKind.NARRATOR,
        is_primary=True,
    )
    await scenes.append_alternate(model.id, primary)
    # append_alternate synthesizes an implicit primary on first call and
    # appends the new alt as non-primary; promote ours.
    await scenes.set_primary_alternate(model.id, primary.id)
    await store.set_current_alternate_delta_set(
        campaign_id=campaign_id,
        post_id=model.id,
        delta_set_id=ds_primary,
    )
    return campaign_id, scene.id, model.id


def _new_delta(target: str, mood: str) -> StateDelta:
    return StateDelta(
        kind=DeltaKind.CHARACTER_STATE_UPDATE,
        target_scope="campaign-sqlite",
        target_table="character_state",
        target_id=target,
        after={
            "character_ref": target,
            "campaign_id": "c1",
            "emotional_state": mood,
        },
    )


async def test_regenerate_post_creates_non_primary_alternate(
    tmp_path: Path, real_store: StateStore
):
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, scene_id, post_id = await _seed_scene_with_one_model_post(scenes, real_store)
    gateway = FakeGateway(chunks=["A ", "new ", "rendering."])
    extractor = FakeExtractor(deltas=[_new_delta("lib:winifred", "anxious")])
    orch = OrchestratorService(
        event_bus=EventBus(),
        scene_manager=scenes,
        llm_gateway=gateway,
        context_builder=FakeContextBuilder(),
        extractor=extractor,
        state_store=real_store,
        ws_push=WSCollector(),
    )

    result = await orch.regenerate_post(campaign_id=campaign_id, post_id=post_id)

    posts = await scenes.get_posts(scene_id)
    target = posts[-1]
    new_alt = next(a for a in target.alternates if a.id == result.new_alternate_id)
    assert new_alt.is_primary is False
    assert new_alt.text == "A new rendering."
    assert new_alt.delta_set_id == result.delta_set_id
    # Primary pointer unchanged: user must accept via switch_primary.
    assert target.primary_alternate_id == "a_primary"
    # New set is the materialized current for this post.
    current = await real_store.current_delta_set_for(post_id=post_id, campaign_id=campaign_id)
    assert current == result.delta_set_id


async def test_regenerate_post_rejects_non_latest_post(tmp_path: Path, real_store: StateStore):
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, scene_id, first_model_id = await _seed_scene_with_one_model_post(
        scenes, real_store
    )
    # Append a second model post; the first one is no longer the latest.
    from grimoire.scenes.manager import new_post

    follow_up = new_post(author_kind=AuthorKind.NARRATOR, body="The door swings.", is_player=False)
    await scenes.append_post(scene_id, follow_up)
    orch = OrchestratorService(
        event_bus=EventBus(),
        scene_manager=scenes,
        llm_gateway=FakeGateway(),
        context_builder=FakeContextBuilder(),
        extractor=FakeExtractor(),
        state_store=real_store,
        ws_push=WSCollector(),
    )
    with pytest.raises(LatestPostOnlyError):
        await orch.regenerate_post(campaign_id=campaign_id, post_id=first_model_id)


async def test_regenerate_post_rollback_on_sidecar_failure(
    tmp_path: Path,
    real_store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
):
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, scene_id, post_id = await _seed_scene_with_one_model_post(scenes, real_store)
    gateway = FakeGateway(chunks=["chunk"])
    extractor = FakeExtractor(deltas=[_new_delta("lib:winifred", "anxious")])
    orch = OrchestratorService(
        event_bus=EventBus(),
        scene_manager=scenes,
        llm_gateway=gateway,
        context_builder=FakeContextBuilder(),
        extractor=extractor,
        state_store=real_store,
        ws_push=WSCollector(),
    )

    async def boom(*_args, **_kwargs):
        raise RuntimeError("sidecar write boom")

    monkeypatch.setattr(scenes, "append_alternate", boom)

    with pytest.raises(RuntimeError, match="sidecar write boom"):
        await orch.regenerate_post(campaign_id=campaign_id, post_id=post_id)

    # The pre-existing primary's deltas are active again; no orphan alternate.
    posts = await scenes.get_posts(scene_id)
    target = posts[-1]
    assert target.primary_alternate_id == "a_primary"
    current = await real_store.current_delta_set_for(post_id=post_id, campaign_id=campaign_id)
    assert current == "ds_primary"


# ----- eviction + vacuum -----------------------------------------------------


async def test_regenerate_post_evicts_oldest_when_over_cap(tmp_path: Path, real_store: StateStore):
    """When regenerate_post would exceed max_alternates_per_post, the oldest
    non-primary, non-pinned alternate is purged. The just-added alternate is
    the newest and is never the eviction target."""
    from datetime import UTC, datetime, timedelta

    from grimoire.orchestrator.config import OrchestratorConfig, SwipesConfig

    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, scene_id, post_id = await _seed_scene_with_one_model_post(scenes, real_store)
    # Pre-seed two old non-primary alternates with backdated created_at so we
    # can predict eviction order.
    base = datetime.now(UTC) - timedelta(days=10)
    for i, ds in enumerate(["ds_old1", "ds_old2"]):
        await real_store.apply_delta_set(
            deltas=[_char_delta(f"x{i}", campaign_id=campaign_id)],
            delta_set_id=ds,
            campaign_id=campaign_id,
            turn_id="t_seed",
            source="test",
        )
        # Rewind so the world state matches the primary at this point.
        await real_store.rewind_delta_set(ds, campaign_id=campaign_id)
        await scenes.append_alternate(
            post_id,
            Alternate(
                id=f"a_old_{i}",
                post_id=post_id,
                text=f"old {i}",
                delta_set_id=ds,
                author_kind=AuthorKind.NARRATOR,
                created_at=base + timedelta(seconds=i),
            ),
        )

    cfg = OrchestratorConfig(swipes=SwipesConfig(max_alternates_per_post=2))
    orch = OrchestratorService(
        event_bus=EventBus(),
        scene_manager=scenes,
        llm_gateway=FakeGateway(chunks=["fresh"]),
        context_builder=FakeContextBuilder(),
        extractor=FakeExtractor(deltas=[_new_delta("lib:winifred", "anxious")]),
        state_store=real_store,
        ws_push=WSCollector(),
        config=cfg,
    )

    result = await orch.regenerate_post(campaign_id=campaign_id, post_id=post_id)

    posts = await scenes.get_posts(scene_id)
    target = posts[-1]
    alt_ids = {a.id for a in target.alternates}
    # The oldest non-primary, non-pinned alternate (a_old_0) is gone.
    assert "a_old_0" not in alt_ids
    # Newer ones are kept, including the just-generated alternate.
    assert "a_old_1" in alt_ids
    assert result.new_alternate_id in alt_ids
    # Primary pointer untouched.
    assert target.primary_alternate_id == "a_primary"


async def test_regenerate_post_does_not_evict_pinned(tmp_path: Path, real_store: StateStore):
    """Pinned non-primary alternates survive eviction even if they're the oldest."""
    from datetime import UTC, datetime, timedelta

    from grimoire.orchestrator.config import OrchestratorConfig, SwipesConfig

    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, scene_id, post_id = await _seed_scene_with_one_model_post(scenes, real_store)
    base = datetime.now(UTC) - timedelta(days=10)
    await real_store.apply_delta_set(
        deltas=[_char_delta("xp", campaign_id=campaign_id)],
        delta_set_id="ds_pinned",
        campaign_id=campaign_id,
        turn_id="t_seed",
        source="test",
    )
    await real_store.rewind_delta_set("ds_pinned", campaign_id=campaign_id)
    await scenes.append_alternate(
        post_id,
        Alternate(
            id="a_pinned",
            post_id=post_id,
            text="pin me",
            delta_set_id="ds_pinned",
            author_kind=AuthorKind.NARRATOR,
            created_at=base,
            pinned=True,
        ),
    )

    cfg = OrchestratorConfig(swipes=SwipesConfig(max_alternates_per_post=1))
    orch = OrchestratorService(
        event_bus=EventBus(),
        scene_manager=scenes,
        llm_gateway=FakeGateway(chunks=["fresh"]),
        context_builder=FakeContextBuilder(),
        extractor=FakeExtractor(deltas=[_new_delta("lib:winifred", "anxious")]),
        state_store=real_store,
        ws_push=WSCollector(),
        config=cfg,
    )

    await orch.regenerate_post(campaign_id=campaign_id, post_id=post_id)
    posts = await scenes.get_posts(scene_id)
    alt_ids = {a.id for a in posts[-1].alternates}
    assert "a_pinned" in alt_ids  # pinned survived


async def test_purge_stale_alternates_deletes_only_old_unpinned_non_primary(
    tmp_path: Path, real_store: StateStore
):
    from datetime import UTC, datetime, timedelta

    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, scene_id, post_id = await _seed_scene_with_one_model_post(scenes, real_store)
    now = datetime.now(UTC)
    old = now - timedelta(days=45)
    fresh = now - timedelta(days=2)
    for alt_id, ts, pinned, ds in [
        ("a_stale", old, False, "ds_stale"),
        ("a_old_but_pinned", old, True, "ds_pin"),
        ("a_fresh", fresh, False, "ds_fresh"),
    ]:
        await real_store.apply_delta_set(
            deltas=[_char_delta(alt_id, campaign_id=campaign_id)],
            delta_set_id=ds,
            campaign_id=campaign_id,
            turn_id="t_seed",
            source="test",
        )
        await real_store.rewind_delta_set(ds, campaign_id=campaign_id)
        await scenes.append_alternate(
            post_id,
            Alternate(
                id=alt_id,
                post_id=post_id,
                text=alt_id,
                delta_set_id=ds,
                author_kind=AuthorKind.NARRATOR,
                created_at=ts,
                pinned=pinned,
            ),
        )

    orch = _make_orch(scenes, real_store)
    deleted = await orch.purge_stale_alternates(campaign_id, older_than_days=30, now=now)
    assert deleted == ["a_stale"]
    posts = await scenes.get_posts(scene_id)
    alt_ids = {a.id for a in posts[-1].alternates}
    assert "a_stale" not in alt_ids
    assert {"a_old_but_pinned", "a_fresh", "a_primary"}.issubset(alt_ids)


async def test_purge_stale_alternates_skips_primary(tmp_path: Path, real_store: StateStore):
    """Even a 'stale' alternate is preserved if it's the current primary."""
    from datetime import UTC, datetime, timedelta

    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, scene_id, post_id = await _seed_scene_with_one_model_post(scenes, real_store)
    # The seeded primary "a_primary" has created_at = now-ish from append_alternate;
    # force its created_at into the past via update_alternate.
    old = datetime.now(UTC) - timedelta(days=60)
    await scenes.update_alternate(post_id, "a_primary", created_at=old)

    orch = _make_orch(scenes, real_store)
    deleted = await orch.purge_stale_alternates(campaign_id, older_than_days=30)
    assert deleted == []
    posts = await scenes.get_posts(scene_id)
    alt_ids = {a.id for a in posts[-1].alternates}
    assert "a_primary" in alt_ids

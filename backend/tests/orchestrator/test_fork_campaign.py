"""End-to-end tests for ``OrchestratorService.fork_campaign``."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.event_bus import EventBus
from grimoire.orchestrator import OrchestratorConfig, OrchestratorService
from grimoire.orchestrator.config import HeartbeatConfig
from grimoire.orchestrator.errors import CampaignIdExists
from grimoire.scenes.manager import SceneManager, SceneManagerConfig
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations

from .conftest import FakeContextBuilder, FakeExtractor, FakeGateway


@pytest.fixture
async def real_store(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(tmp_path / "campaigns.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
    store = StateStore(db, data_root)
    try:
        yield store
    finally:
        await db.close()


@pytest.fixture
def orch(
    tmp_path: Path,
    real_store: StateStore,
    event_bus: EventBus,
    fake_gateway: FakeGateway,
    fake_extractor: FakeExtractor,
    fake_context_builder: FakeContextBuilder,
) -> OrchestratorService:
    sm = SceneManager(
        real_store.data_root, config=SceneManagerConfig(running_summary_every_n_posts=0)
    )
    return OrchestratorService(
        event_bus=event_bus,
        scene_manager=sm,
        llm_gateway=fake_gateway,
        context_builder=fake_context_builder,
        extractor=fake_extractor,
        state_store=real_store,
        config=OrchestratorConfig(heartbeat=HeartbeatConfig(enabled=False)),
    )


async def _seed_campaign(store: StateStore, cid: str = "c1") -> None:
    await store.upsert_campaign(campaign_id=cid, name="Original")
    await store.apply_delta(
        delta={
            "kind": "character_state_update",
            "target_scope": "campaign-sqlite",
            "target_table": "character_state",
            "after": {
                "character_ref": "lib:winifred",
                "campaign_id": cid,
                "emotional_state": "calm",
                "drift_score": 0.0,
            },
        },
        source="seed",
        turn_id="t1",
        campaign_id=cid,
    )
    # Campaign directory + a scene file so file-copy has something to chew on.
    cdir = store.data_root / "campaigns" / cid
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "campaign.yaml").write_text("name: Original\n")
    (cdir / "scenes").mkdir(exist_ok=True)
    (cdir / "scenes" / "0001-opening.md").write_text("# Opening\n")
    (cdir / "images").mkdir(exist_ok=True)
    (cdir / "images" / "img1.png").write_bytes(b"img")


async def test_fork_from_current_full_state(
    orch: OrchestratorService, real_store: StateStore
) -> None:
    await _seed_campaign(real_store, "c1")
    result = await orch.fork_campaign(
        campaign_id="c1",
        new_campaign_id="c1-divergent",
        new_name="Divergent path",
    )
    assert result.queued is False
    assert result.deltas_replayed == 0
    assert result.image_handling in ("hardlink", "deep_copy", "mixed")
    assert result.fingerprint_match is True
    assert result.degraded is False

    new_camp = await real_store.db.fetchone(
        "SELECT * FROM campaigns WHERE id = ?", ("c1-divergent",)
    )
    assert new_camp is not None
    assert new_camp["forked_from_campaign_id"] == "c1"
    assert new_camp["name"] == "Divergent path"
    assert new_camp["forked_image_handling"] in ("hardlink", "deep_copy", "mixed")

    # Files mirrored.
    new_dir = real_store.data_root / "campaigns" / "c1-divergent"
    assert (new_dir / "scenes" / "0001-opening.md").exists()
    assert (new_dir / "images" / "img1.png").exists()

    # State rows copied with rewritten campaign_id.
    chars = await real_store.db.fetchall(
        "SELECT character_ref FROM character_state WHERE campaign_id = ?",
        ("c1-divergent",),
    )
    assert [r["character_ref"] for r in chars] == ["lib:winifred"]


async def test_fork_id_collision_409(orch: OrchestratorService, real_store: StateStore) -> None:
    await _seed_campaign(real_store, "c1")
    with pytest.raises(CampaignIdExists):
        await orch.fork_campaign(campaign_id="c1", new_campaign_id="c1", new_name="dup")


async def test_fork_id_collision_via_integrity_error_preserves_existing(
    orch: OrchestratorService, real_store: StateStore
) -> None:
    """The second-line defense — when the pre-check ``_campaign_exists``
    is bypassed (simulating two concurrent forks racing past it) the
    INSERT in ``_clone_campaign_row`` raises ``IntegrityError``. The
    error handler must surface ``CampaignIdExists`` *without* running
    ``_wipe_failed_fork`` — otherwise the loser would delete the
    winner's rows."""
    await _seed_campaign(real_store, "c1")
    await orch.fork_campaign(campaign_id="c1", new_campaign_id="c1-twin", new_name="Twin original")

    # Bypass the pre-check by monkeypatching ``_campaign_exists`` so the
    # second fork attempts the INSERT and trips the integrity constraint.
    orch._campaign_exists = lambda _cid: _async_false()  # type: ignore[assignment,method-assign]
    with pytest.raises(CampaignIdExists):
        await orch.fork_campaign(campaign_id="c1", new_campaign_id="c1-twin", new_name="Loser")

    # Winner's row is intact.
    row = await real_store.db.fetchone("SELECT name FROM campaigns WHERE id = ?", ("c1-twin",))
    assert row is not None
    assert row["name"] == "Twin original"


async def _async_false():
    return False


async def test_fork_from_earlier_post_id(orch: OrchestratorService, real_store: StateStore) -> None:
    await _seed_campaign(real_store, "c1")
    # Add posts with distinct created_at so cutoff filtering bites.
    await real_store.db.execute(
        "INSERT INTO scenes (id, campaign_id, ordinal, slug, file_path) "
        "VALUES ('s1','c1',1,'one','/tmp/o.md')"
    )
    await real_store.db.execute(
        "INSERT INTO posts (id, scene_id, campaign_id, order_in_scene, "
        "turn_id, created_at) VALUES ('p1','s1','c1',0,'t1','2026-05-19T10:00:00')"
    )
    await real_store.db.execute(
        "INSERT INTO posts (id, scene_id, campaign_id, order_in_scene, "
        "turn_id, created_at) VALUES ('p2','s1','c1',1,'t2','2026-05-19T11:00:00')"
    )
    await real_store.db.execute(
        "INSERT INTO posts (id, scene_id, campaign_id, order_in_scene, "
        "turn_id, created_at) VALUES ('p3','s1','c1',2,'t3','2026-05-19T12:00:00')"
    )

    result = await orch.fork_campaign(
        campaign_id="c1",
        new_campaign_id="c1-earlier",
        new_name="Earlier",
        fork_at_post_id="p2",
    )
    assert result.queued is False

    new_camp = await real_store.db.fetchone(
        "SELECT forked_at_post_id, forked_at_turn_id FROM campaigns WHERE id = ?",
        ("c1-earlier",),
    )
    assert new_camp["forked_at_post_id"] == "p2"
    assert new_camp["forked_at_turn_id"] == "t2"

    # Only the first two posts make it.
    rows = await real_store.db.fetchall(
        "SELECT id FROM posts WHERE campaign_id = ? ORDER BY order_in_scene",
        ("c1-earlier",),
    )
    assert [r["id"] for r in rows] == ["c1-earlier::p1", "c1-earlier::p2"]

    # The safety-net fingerprint compares source-at-cutoff against
    # fork-after-replay; they must match so the fork is not flagged
    # degraded for the typical fork-from-earlier case.
    assert result.fingerprint_match is True
    assert result.degraded is False


async def test_lineage_tree(orch: OrchestratorService, real_store: StateStore) -> None:
    await _seed_campaign(real_store, "c1")
    await orch.fork_campaign(campaign_id="c1", new_campaign_id="c1-a", new_name="A")
    await orch.fork_campaign(campaign_id="c1", new_campaign_id="c1-b", new_name="B")
    await orch.fork_campaign(campaign_id="c1-a", new_campaign_id="c1-a-1", new_name="A1")

    tree = await orch.get_lineage("c1")
    depths = {d["id"]: d["depth"] for d in tree["descendants"]}
    assert depths["c1"] == 0
    assert depths["c1-a"] == 1
    assert depths["c1-b"] == 1
    assert depths["c1-a-1"] == 2
    # Ancestors of c1-a-1 walks up to c1.
    ancestors = await orch.get_lineage_ancestors("c1-a-1")
    assert [a["id"] for a in ancestors] == ["c1-a-1", "c1-a", "c1"]


async def test_fork_during_streaming_is_queued(
    orch: OrchestratorService, real_store: StateStore
) -> None:
    await _seed_campaign(real_store, "c1")
    # Simulate an active turn by injecting state.
    state = orch._state_for("c1")
    from grimoire.orchestrator.service import _ActiveTurn

    state.active = _ActiveTurn(
        turn_id="t_active",
        campaign_id="c1",
        scene_id="s1",
        started_at=orch._clock(),
        stage="streaming",
    )

    result = await orch.fork_campaign(
        campaign_id="c1", new_campaign_id="c1-later", new_name="Later"
    )
    assert result.queued is True
    pending = await orch.list_pending_forks("c1")
    assert len(pending) == 1
    assert pending[0]["new_campaign_id"] == "c1-later"

    # Once streaming ends, process_pending_forks completes the queued one.
    state.active = None
    results = await orch.process_pending_forks("c1")
    assert len(results) == 1
    assert results[0].new_campaign_id == "c1-later"
    assert not results[0].queued

    row = await real_store.db.fetchone(
        "SELECT id, completed_at FROM pending_forks WHERE source_campaign_id = ?",
        ("c1",),
    )
    assert row["completed_at"] is not None

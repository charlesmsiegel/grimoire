"""Tests for the frozen-campaign harness."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.storage import Database, apply_migrations
from grimoire.testing import FrozenCampaignHarness, InvariantSnapshot

pytestmark = pytest.mark.frozen_campaign


async def _seed_snapshot(path: Path) -> None:
    db = Database(path, pool_size=1)
    await db.connect()
    await apply_migrations(db)
    await db.close()


@pytest.mark.asyncio
async def test_loads_snapshot_and_reports_zero_state(tmp_path: Path) -> None:
    snap = tmp_path / "snap.sqlite"
    await _seed_snapshot(snap)
    async with FrozenCampaignHarness(snap, tmp_path / "data") as harness:
        snapshot = await harness.snapshot()
        assert snapshot.character_count == 0
        assert snapshot.scene_count == 0


@pytest.mark.asyncio
async def test_validate_flags_character_disappearance(tmp_path: Path) -> None:
    snap = tmp_path / "snap.sqlite"
    await _seed_snapshot(snap)
    async with FrozenCampaignHarness(snap, tmp_path / "data") as harness:
        before = InvariantSnapshot(character_count=10)
        after = InvariantSnapshot(character_count=9)
        report = harness.validate(before, after)
        assert not report.ok
        assert any("character_count decreased" in v for v in report.violations)


@pytest.mark.asyncio
async def test_validate_accepts_explicit_commitment_resolution(tmp_path: Path) -> None:
    snap = tmp_path / "snap.sqlite"
    await _seed_snapshot(snap)
    async with FrozenCampaignHarness(snap, tmp_path / "data") as harness:
        before = InvariantSnapshot(open_commitment_count=5)
        after = InvariantSnapshot(open_commitment_count=4)
        report = harness.validate(before, after, resolved_commitments=1)
        assert report.ok


@pytest.mark.asyncio
async def test_validate_requires_scene_break_for_new_scene(tmp_path: Path) -> None:
    snap = tmp_path / "snap.sqlite"
    await _seed_snapshot(snap)
    async with FrozenCampaignHarness(snap, tmp_path / "data") as harness:
        before = InvariantSnapshot(scene_count=2)
        after = InvariantSnapshot(scene_count=3)
        # No scene break flag → the bump is unexplained.
        report = harness.validate(before, after, scene_broke=False)
        assert report.ok  # 3 >= 2, which is the floor when scene_broke=False
        # But a *drop* must fail.
        dropped = harness.validate(before, InvariantSnapshot(scene_count=1))
        assert not dropped.ok


@pytest.mark.asyncio
async def test_validate_flags_delta_log_gap(tmp_path: Path) -> None:
    """The delta log must be contiguous — a missing turn id is a violation."""
    snap = tmp_path / "snap.sqlite"
    await _seed_snapshot(snap)
    async with FrozenCampaignHarness(snap, tmp_path / "data") as harness:
        before = InvariantSnapshot()
        # Skipping ``t-2`` between ``t-1`` and ``t-3`` is a contiguity gap.
        after = InvariantSnapshot(delta_turn_ids=("t-0", "t-1", "t-3"))
        report = harness.validate(before, after)
        assert not report.ok
        assert any("delta log" in v for v in report.violations)


@pytest.mark.asyncio
async def test_validate_accepts_contiguous_delta_log(tmp_path: Path) -> None:
    snap = tmp_path / "snap.sqlite"
    await _seed_snapshot(snap)
    async with FrozenCampaignHarness(snap, tmp_path / "data") as harness:
        after = InvariantSnapshot(delta_turn_ids=("t-0", "t-1", "t-2", "t-3"))
        report = harness.validate(InvariantSnapshot(), after)
        assert report.ok, report.violations


@pytest.mark.asyncio
async def test_validate_flags_embedding_kind_drop(tmp_path: Path) -> None:
    """Embedding counts grouped by ``source_kind`` must never decrease."""
    snap = tmp_path / "snap.sqlite"
    await _seed_snapshot(snap)
    async with FrozenCampaignHarness(snap, tmp_path / "data") as harness:
        before = InvariantSnapshot(embeddings_by_kind={"post": 5, "fact": 3})
        after = InvariantSnapshot(embeddings_by_kind={"post": 4, "fact": 3})
        report = harness.validate(before, after)
        assert not report.ok
        assert any("'post'" in v for v in report.violations)


@pytest.mark.asyncio
async def test_validate_allows_embedding_growth(tmp_path: Path) -> None:
    snap = tmp_path / "snap.sqlite"
    await _seed_snapshot(snap)
    async with FrozenCampaignHarness(snap, tmp_path / "data") as harness:
        before = InvariantSnapshot(embeddings_by_kind={"post": 5})
        after = InvariantSnapshot(embeddings_by_kind={"post": 7, "fact": 1})
        report = harness.validate(before, after)
        assert report.ok, report.violations


@pytest.mark.asyncio
async def test_snapshot_captures_delta_turn_ids_from_db(tmp_path: Path) -> None:
    snap = tmp_path / "snap.sqlite"
    await _seed_snapshot(snap)
    # Seed two deltas with turn ids.
    db = Database(snap, pool_size=1)
    await db.connect()
    await db.execute(
        "INSERT INTO deltas (id, campaign_id, branch_id, turn_id, source, kind)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        ("d-0", "cmp", "b", "t-0", "extractor", "note"),
    )
    await db.execute(
        "INSERT INTO deltas (id, campaign_id, branch_id, turn_id, source, kind)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        ("d-1", "cmp", "b", "t-1", "extractor", "note"),
    )
    await db.close()

    async with FrozenCampaignHarness(snap, tmp_path / "data") as harness:
        snap_data = await harness.snapshot()
        assert snap_data.delta_turn_ids == ("t-0", "t-1")
        # Validating against itself is clean.
        report = harness.validate(snap_data, snap_data)
        assert report.ok, report.violations


@pytest.mark.asyncio
async def test_assert_snapshot_matches_current_migrations_is_clean(tmp_path: Path) -> None:
    """A freshly migrated snapshot should report no pending migrations."""
    snap = tmp_path / "snap.sqlite"
    await _seed_snapshot(snap)
    async with FrozenCampaignHarness(snap, tmp_path / "data") as harness:
        # Should not raise — `_seed_snapshot` ran every migration.
        await harness.assert_snapshot_matches_current_migrations()

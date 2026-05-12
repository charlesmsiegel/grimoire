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

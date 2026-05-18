"""Frozen-snapshot regression tests (spec 17 §L4).

These tests load a checked-in SQLite snapshot via the
:class:`FrozenCampaignHarness` and exercise the invariant snapshot /
validate cycle. They also assert that the snapshot is up to date with
the migrations in the working tree — drift here is the canary for a
forgotten fixture refresh.
"""

from __future__ import annotations

import pytest

from grimoire.testing import FrozenCampaignHarness, SnapshotStaleError

pytestmark = pytest.mark.frozen_campaign


@pytest.mark.parametrize("frozen_campaign", ["minimal_test_campaign"], indirect=True)
async def test_snapshot_loads_and_reports_invariants(
    frozen_campaign: FrozenCampaignHarness,
) -> None:
    snapshot = await frozen_campaign.snapshot()
    # The synthetic campaign seeds two deltas with turn ids "t-0" and "t-1".
    assert snapshot.max_turn_id == "t-1"
    assert snapshot.delta_turn_ids == ("t-0", "t-1")
    # No embeddings in the synthetic fixture.
    assert snapshot.embeddings_by_kind == {}


@pytest.mark.parametrize("frozen_campaign", ["minimal_test_campaign"], indirect=True)
async def test_snapshot_validate_round_trip_clean(
    frozen_campaign: FrozenCampaignHarness,
) -> None:
    before = await frozen_campaign.snapshot()
    after = await frozen_campaign.snapshot()
    report = frozen_campaign.validate(before, after)
    assert report.ok, report.violations


@pytest.mark.parametrize("frozen_campaign", ["minimal_test_campaign"], indirect=True)
async def test_snapshot_matches_current_migrations(
    frozen_campaign: FrozenCampaignHarness,
) -> None:
    """The snapshot should already be at the head migration.

    If a new migration shipped but the snapshot wasn't regenerated, the
    harness raises :class:`SnapshotStaleError` so the maintainer knows
    to bump the fixture rather than silently skipping a schema change.
    """
    try:
        await frozen_campaign.assert_snapshot_matches_current_migrations()
    except SnapshotStaleError as exc:  # pragma: no cover - clear failure path
        pytest.fail(
            f"frozen snapshot is missing migrations — re-run "
            f"`uv run python scripts/export_snapshot.py --seed-minimal --output "
            f"tests/fixtures/campaigns/minimal_test_campaign.sqlite`: {exc}"
        )

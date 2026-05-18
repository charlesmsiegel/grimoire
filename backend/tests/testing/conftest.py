"""Test fixtures for the testing harness (spec 17 §L4).

A ``frozen_campaign`` fixture loads one of the checked-in SQLite
snapshots into a temp data root via :class:`FrozenCampaignHarness` and
yields the open harness. Tests pick a snapshot by parametrizing
indirectly::

    @pytest.mark.parametrize("frozen_campaign", ["minimal_test_campaign"], indirect=True)
    async def test_x(frozen_campaign): ...
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from grimoire.testing import FrozenCampaignHarness

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "campaigns"


@pytest.fixture(name="frozen_campaign")
async def frozen_campaign_fixture(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> AsyncIterator[FrozenCampaignHarness]:
    """Yield a :class:`FrozenCampaignHarness` for the named snapshot.

    Use indirect parametrization to pick the snapshot::

        @pytest.mark.parametrize(
            "frozen_campaign", ["minimal_test_campaign"], indirect=True
        )
    """
    name = getattr(request, "param", "minimal_test_campaign")
    snapshot_path = FIXTURE_ROOT / f"{name}.sqlite"
    if not snapshot_path.is_file():
        pytest.skip(f"snapshot {snapshot_path} not present")
    async with FrozenCampaignHarness(snapshot_path, tmp_path / "data") as harness:
        yield harness

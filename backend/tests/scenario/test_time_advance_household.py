"""L5 scenario: advance time 1 week with a household of 8 NPCs, verify
all significant NPCs ticked and the digest is coherent (spec 17 §L5).

Needs a pre-seeded campaign with a household of 8 significant NPCs;
spec 17 §L4 names ``minimal_test_campaign`` as a starting snapshot we
can extend. Until §4 ships those snapshots this test skips cleanly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.testing.scenario import ScenarioApp

from .conftest import frozen_snapshot_path


async def test_time_advance_household(tmp_path: Path) -> None:
    # The minimal §4 snapshot ships an empty migrated DB; this scenario
    # needs a dedicated 8-NPC household snapshot. Until that ships, skip.
    snapshot = frozen_snapshot_path("household_of_eight_npcs")
    if snapshot is None:
        pytest.skip(
            "frozen-campaign snapshot 'household_of_eight_npcs.sqlite' not found; "
            "spec 17 §L5 needs a campaign pre-seeded with 8 significant NPCs."
        )

    async with ScenarioApp(tmp_path, seed_db=snapshot) as app:
        assert app.client is not None

        resp = await app.client.get("/api/campaigns")
        assert resp.status_code == 200, resp.text
        campaigns = resp.json()
        assert campaigns
        campaign_id = campaigns[0]["id"]

        # Capture NPC roster before the tick.
        resp = await app.client.get(f"/api/campaigns/{campaign_id}/characters")
        assert resp.status_code == 200, resp.text
        npcs_before = [c for c in resp.json() if c.get("significant")]
        assert len(npcs_before) >= 8, (
            f"expected a household of 8+ significant NPCs; got {len(npcs_before)}"
        )

        # Advance 1 week.
        resp = await app.client.post(
            f"/api/campaigns/{campaign_id}/time/advance",
            json={"reason": "elapsed", "duration": {"days": 7}},
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()

        # Every significant NPC should have ticked. The TimeEngine result
        # exposes ticked-NPC ids in ``npc_ticks`` (see TimeEngineService).
        ticked = {t.get("character_id") for t in result.get("npc_ticks", [])}
        npc_ids = {c["id"] for c in npcs_before}
        missing = npc_ids - ticked
        assert not missing, f"significant NPCs missed the weekly tick: {missing}"

        # Digest sanity: non-empty string with no obvious failure markers.
        digest = result.get("digest")
        assert digest, "time-advance returned an empty digest"
        assert "ERROR" not in digest.upper()

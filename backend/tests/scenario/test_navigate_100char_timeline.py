"""L5 scenario: open campaign with 100 characters, navigate timeline,
jump to scene 23, run a turn (spec 17 §L5).

The 100-character campaign is the §4 ``opt_cohen_day_52``-style frozen
snapshot. §4 is a parallel workstream; until those snapshots land
this test skips cleanly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.testing.scenario import ScenarioApp

from .conftest import frozen_snapshot_path


async def test_navigate_100char_timeline(tmp_path: Path) -> None:
    snapshot = frozen_snapshot_path("opt_cohen_day_52")
    if snapshot is None:
        pytest.skip(
            "frozen-campaign snapshot 'opt_cohen_day_52.sqlite' not found; "
            "spec 17 §L4 fixtures are a parallel workstream (testing-design §4)"
        )

    async with ScenarioApp(tmp_path, seed_db=snapshot) as app:
        assert app.client is not None

        # 1. List campaigns (snapshot should expose at least one).
        resp = await app.client.get("/api/campaigns")
        assert resp.status_code == 200, resp.text
        campaigns = resp.json()
        assert campaigns, "frozen snapshot exposed no campaigns"
        campaign_id = campaigns[0]["id"]

        # 2. Snapshot invariants: ~100 characters expected.
        resp = await app.client.get(f"/api/campaigns/{campaign_id}/characters")
        assert resp.status_code == 200, resp.text
        characters = resp.json()
        assert len(characters) >= 50, (
            f"expected a populous campaign; got {len(characters)} characters"
        )

        # 3. Walk the scenes list and jump to scene 23.
        resp = await app.client.get(f"/api/campaigns/{campaign_id}/scenes")
        assert resp.status_code == 200, resp.text
        scenes = resp.json()
        assert len(scenes) >= 23, f"snapshot has only {len(scenes)} scenes"
        scene = scenes[22]
        scene_id = scene["scene_id"] if "scene_id" in scene else scene["id"]

        resp = await app.client.get(f"/api/campaigns/{campaign_id}/scenes/{scene_id}")
        assert resp.status_code == 200, resp.text

        # 4. Run a turn against the current scene.
        resp = await app.client.post(
            f"/api/campaigns/{campaign_id}/turns",
            json={"pc_ref": characters[0]["id"], "text": "I look around."},
        )
        assert resp.status_code in (200, 201), resp.text

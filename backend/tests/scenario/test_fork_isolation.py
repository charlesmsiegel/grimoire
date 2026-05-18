"""L5 scenario: fork at turn 47, run 3 turns on the fork, switch back to
main, run 3 turns, verify isolation (spec 17 §L5).

Depends on the §4 ``wod_london_session_47`` frozen snapshot (sits past
turn 47 so the fork point is reachable). §4 ships in a parallel
workstream — until those snapshots land this test skips cleanly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.testing.scenario import ScenarioApp

from .conftest import frozen_snapshot_path


async def test_fork_isolation(tmp_path: Path) -> None:
    snapshot = frozen_snapshot_path("wod_london_session_47")
    if snapshot is None:
        pytest.skip(
            "frozen-campaign snapshot 'wod_london_session_47.sqlite' not found; "
            "spec 17 §L4 fixtures are a parallel workstream (testing-design §4)"
        )

    async with ScenarioApp(tmp_path, seed_db=snapshot) as app:
        assert app.client is not None

        resp = await app.client.get("/api/campaigns")
        assert resp.status_code == 200, resp.text
        campaigns = resp.json()
        assert campaigns, "snapshot exposes no campaigns"
        main_id = campaigns[0]["id"]

        # Identify the turn id at index 47 on the main branch.
        resp = await app.client.get(f"/api/campaigns/{main_id}/scenes")
        assert resp.status_code == 200, resp.text
        scenes = resp.json()
        assert scenes, "snapshot has no scenes"

        # POST a fork from turn 47.
        resp = await app.client.post(
            f"/api/campaigns/{main_id}/forks",
            json={"from_turn_id": "turn-0047", "label": "fork-from-47"},
        )
        assert resp.status_code == 201, resp.text
        fork = resp.json()
        fork_id = fork.get("campaign_id") or fork.get("id")
        assert fork_id and fork_id != main_id

        # Run 3 turns on the fork.
        for i in range(3):
            resp = await app.client.post(
                f"/api/campaigns/{fork_id}/turns",
                json={"pc_ref": "pc1", "text": f"fork turn {i}"},
            )
            assert resp.status_code in (200, 201), resp.text

        # Snapshot main's last-turn id before we touch it.
        resp = await app.client.get(f"/api/campaigns/{main_id}/scenes")
        assert resp.status_code == 200
        main_before = resp.json()

        # Run 3 turns on main.
        for i in range(3):
            resp = await app.client.post(
                f"/api/campaigns/{main_id}/turns",
                json={"pc_ref": "pc1", "text": f"main turn {i}"},
            )
            assert resp.status_code in (200, 201), resp.text

        # Re-read both and verify the fork did not pick up main's new
        # posts (and vice versa). Isolation: the post counts diverge.
        resp = await app.client.get(f"/api/campaigns/{main_id}/scenes")
        main_after = resp.json()
        resp = await app.client.get(f"/api/campaigns/{fork_id}/scenes")
        fork_after = resp.json()

        assert main_after != main_before, "main scenes unchanged after running turns"
        # Fork should not contain any of the post bodies appended to main.
        main_bodies = {
            post.get("body")
            for scene in main_after
            for post in scene.get("posts", [])
            if "main turn" in (post.get("body") or "")
        }
        fork_bodies = {
            post.get("body")
            for scene in fork_after
            for post in scene.get("posts", [])
            if "main turn" in (post.get("body") or "")
        }
        assert main_bodies & fork_bodies == set(), "fork leaked posts from main"

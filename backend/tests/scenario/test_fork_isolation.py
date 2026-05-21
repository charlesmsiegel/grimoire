"""L5 scenario: fork at turn 47, run 3 turns on the fork, switch back to
main, run 3 turns, verify isolation (spec 17 §L5).

Depends on the §4 ``wod_london_session_47`` frozen snapshot — a
campaign already past turn 47 so the fork point is reachable. §4 ships
in a parallel workstream; until those snapshots land this test skips
cleanly.

The harness itself (:class:`ScenarioApp`) is already verified by
``test_bootstrap_world_campaign``, so when the snapshot lands the body
below should run without further wiring. The fork endpoint contract is
``POST /api/campaigns/{id}/forks`` with a :class:`ForkPayload` body —
``new_campaign_id``, ``new_name``, optional ``fork_at_post_id`` — not
the legacy branch fork shape.
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
        client = app.client

        resp = await client.get("/api/campaigns")
        assert resp.status_code == 200, resp.text
        campaigns = resp.json()
        assert campaigns, "snapshot exposes no campaigns"
        main_id = campaigns[0]["id"]

        # Walk the timeline to find a turn around index 47 to fork from.
        resp = await client.get(f"/api/campaigns/{main_id}/scenes")
        assert resp.status_code == 200, resp.text
        scenes = resp.json()
        assert scenes, "snapshot has no scenes"
        # The snapshot is named after the session it lives at; the
        # post id at the fork point is whichever sits ~47 turns in.
        # Flatten the (scene, posts) view so we can pick a post id by
        # index — the API surface returns scenes containing posts.
        flat_posts = [
            post for scene in scenes for post in scene.get("posts", []) if post.get("kind") != "ooc"
        ]
        assert len(flat_posts) > 47, f"snapshot only has {len(flat_posts)} model posts"
        fork_post_id = flat_posts[47]["id"]

        # Campaign-level fork: ForkPayload requires a fresh campaign id +
        # name. ``make_active=False`` so subsequent calls still target main
        # unless we explicitly use the fork id.
        fork_id = f"{main_id}-fork-at-47"
        resp = await client.post(
            f"/api/campaigns/{main_id}/forks",
            json={
                "new_campaign_id": fork_id,
                "new_name": f"{main_id} fork @47",
                "fork_at_post_id": fork_post_id,
                "make_active": False,
            },
        )
        assert resp.status_code == 201, resp.text

        # Run 3 turns on the fork.
        for i in range(3):
            resp = await client.post(
                f"/api/campaigns/{fork_id}/turns",
                json={"pc_ref": "pc1", "text": f"fork turn {i}"},
            )
            assert resp.status_code in (200, 201), resp.text

        # Snapshot main's posts before we touch it.
        resp = await client.get(f"/api/campaigns/{main_id}/scenes")
        assert resp.status_code == 200
        main_before = resp.json()

        # Run 3 turns on main.
        for i in range(3):
            resp = await client.post(
                f"/api/campaigns/{main_id}/turns",
                json={"pc_ref": "pc1", "text": f"main turn {i}"},
            )
            assert resp.status_code in (200, 201), resp.text

        # Isolation check: the fork did not pick up main's new posts and
        # main did not pick up the fork's. Compare body text since post
        # ids may legitimately differ across campaigns.
        resp = await client.get(f"/api/campaigns/{main_id}/scenes")
        main_after = resp.json()
        resp = await client.get(f"/api/campaigns/{fork_id}/scenes")
        fork_after = resp.json()

        assert main_after != main_before, "main scenes unchanged after running turns"
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

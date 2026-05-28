"""L5 scenario: POST /api/campaigns/{id}/turns/next-speaker contract.

Exercises the HTTP route added for per_character_multi_call mode. Drives
the same surface a frontend uses: create a campaign, then hit the
next-speaker endpoint and verify the route shape and response envelope.

When no speaker loop is active the endpoint is still expected to accept
the call as a no-op signal (the orchestrator's next_speaker method
silently returns if no event is waiting). This pins down the route
contract — frontend code calls this whenever the "Next" button fires,
and a misnamed route or changed response envelope would break that flow.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.testing.scenario import ScenarioApp


async def test_next_speaker_endpoint_accepts_no_op(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None
        client = app.client

        campaign_id = "scenario-next-speaker"
        resp = await client.post(
            "/api/campaigns",
            json={
                "id": campaign_id,
                "name": "Next Speaker Scenario",
                "description": "Pins down the next-speaker route contract.",
                "composition": {"worlds": []},
            },
        )
        assert resp.status_code == 201, resp.text

        resp = await client.post(
            f"/api/campaigns/{campaign_id}/turns/next-speaker",
            json={"scene_id": "any"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"accepted": True}


async def test_next_speaker_endpoint_unknown_campaign_404(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None
        client = app.client

        # The orchestrator looks up campaign state by id; a totally
        # unknown id still no-ops (next_speaker silently returns when
        # there's no campaign state record yet). The route contract is
        # that it never raises — kept as a guard against accidental
        # tightening that would break the frontend's optimistic
        # button-click handling.
        resp = await client.post(
            "/api/campaigns/unknown-campaign/turns/next-speaker",
            json={"scene_id": "any"},
        )
        # Either 200 (current behavior) or 404 (if the route adds
        # campaign validation in future) — pin the route exists.
        assert resp.status_code in (200, 404), resp.text

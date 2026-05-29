"""L5 scenario: cast-change REST endpoints end-to-end through the HTTP API (#464).

Bootstraps a world, campaign, and scene; queues a pending cast change through
the real SceneManager (the same store the orchestrator uses); then drives the
list / confirm / dismiss routes over HTTP and verifies the scene sidecar's cast
is updated on confirm and left untouched on dismiss.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.scenes import SceneInit
from grimoire.testing.scenario import ScenarioApp
from grimoire.types.scene import CastChange

NPC_REF = "campaign:emergent/character/reyes"


async def _bootstrap(client, world_id: str, campaign_id: str) -> None:
    resp = await client.post(
        "/api/library/worlds",
        json={
            "id": world_id,
            "meta": {
                "name": "Cast World",
                "description": "Cast-change scenario.",
                "atmosphere": {"themes": ["scenario"], "tone": "neutral"},
            },
        },
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        "/api/campaigns",
        json={
            "id": campaign_id,
            "name": "Cast Scenario",
            "description": "Drives cast-change endpoints.",
            "composition": {"worlds": [{"world_id": world_id, "priority": 0}]},
        },
    )
    assert resp.status_code == 201, resp.text


async def test_cast_change_confirm_updates_scene(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None and app.container is not None
        client = app.client
        campaign_id = "cast-scenario"
        await _bootstrap(client, "cast-world", campaign_id)

        scenes = app.container.scenes
        assert scenes is not None
        scene = await scenes.start_scene(SceneInit(campaign_id=campaign_id, title="The Dock"))
        change_id = await scenes.queue_cast_change(
            scene.id,
            character_ref=NPC_REF,
            change=CastChange.ENTER,
            is_pc=False,
            evidence="strides onto the dock",
            confidence=0.9,
        )

        # GET lists the pending change.
        resp = await client.get(f"/api/campaigns/{campaign_id}/scenes/{scene.id}/cast-changes")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [c["id"] for c in body] == [change_id]
        assert body[0]["character_ref"] == NPC_REF

        # Confirm applies it to the scene cast.
        resp = await client.post(
            f"/api/campaigns/{campaign_id}/scenes/{scene.id}/cast-changes/{change_id}/confirm"
        )
        assert resp.status_code == 200, resp.text

        resp = await client.get(f"/api/campaigns/{campaign_id}/scenes/{scene.id}")
        assert resp.status_code == 200, resp.text
        assert NPC_REF in resp.json()["scene"]["present_character_refs"]

        # The change is no longer pending.
        resp = await client.get(f"/api/campaigns/{campaign_id}/scenes/{scene.id}/cast-changes")
        assert resp.json() == []


async def test_cast_change_dismiss_leaves_cast_untouched(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None and app.container is not None
        client = app.client
        campaign_id = "cast-scenario-2"
        await _bootstrap(client, "cast-world-2", campaign_id)

        scenes = app.container.scenes
        assert scenes is not None
        scene = await scenes.start_scene(SceneInit(campaign_id=campaign_id, title="The Dock"))
        change_id = await scenes.queue_cast_change(
            scene.id, character_ref=NPC_REF, change=CastChange.ENTER, is_pc=False
        )

        resp = await client.post(
            f"/api/campaigns/{campaign_id}/scenes/{scene.id}/cast-changes/{change_id}/dismiss"
        )
        assert resp.status_code == 200, resp.text

        resp = await client.get(f"/api/campaigns/{campaign_id}/scenes/{scene.id}")
        assert NPC_REF not in resp.json()["scene"]["present_character_refs"]
        resp = await client.get(f"/api/campaigns/{campaign_id}/scenes/{scene.id}/cast-changes")
        assert resp.json() == []


async def test_cast_change_unknown_scene_returns_404(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None
        client = app.client
        campaign_id = "cast-scenario-3"
        await _bootstrap(client, "cast-world-3", campaign_id)
        resp = await client.get(f"/api/campaigns/{campaign_id}/scenes/does-not-exist/cast-changes")
        assert resp.status_code == 404

"""L5 scenario: the dramatis-personae cast endpoint end-to-end (#581).

Bootstraps a world with three characters, registers one as a PC, has a
second appear through a confirmed cast change and then leave the scene, and
asserts ``GET /api/campaigns/{id}/cast`` keeps the PC and the departed
character (the confirmed cast-change log is the durable appearance record)
while excluding the character that never appeared. The full composition
endpoint still lists everyone.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.scenes import SceneInit
from grimoire.testing.scenario import ScenarioApp
from grimoire.types.scene import CastChange

WORLD_ID = "cast-view-world"
CAMPAIGN_ID = "cast-view-scenario"


async def _create_character(client, char_id: str, name: str) -> None:
    resp = await client.post(
        f"/api/library/worlds/{WORLD_ID}/characters",
        json={
            "id": char_id,
            "frontmatter": {"id": char_id, "name": name, "role": "major_npc"},
            "body": f"{name} of the cast scenario.",
        },
    )
    assert resp.status_code == 201, resp.text


async def test_cast_lists_pcs_and_appeared_characters(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None and app.container is not None
        client = app.client

        resp = await client.post(
            "/api/library/worlds",
            json={
                "id": WORLD_ID,
                "meta": {
                    "name": "Cast View World",
                    "description": "Cast endpoint scenario.",
                    "atmosphere": {"themes": ["scenario"], "tone": "neutral"},
                },
            },
        )
        assert resp.status_code == 201, resp.text
        for char_id, name in [("alice", "Alice"), ("bram", "Bram"), ("celia", "Celia")]:
            await _create_character(client, char_id, name)

        resp = await client.post(
            "/api/campaigns",
            json={
                "id": CAMPAIGN_ID,
                "name": "Cast Scenario",
                "description": "Drives the /cast endpoint.",
                "composition": {"worlds": [{"world_id": WORLD_ID, "priority": 0}]},
            },
        )
        assert resp.status_code == 201, resp.text

        alice_ref = f"library:worlds/{WORLD_ID}/characters/alice"
        resp = await client.post(
            f"/api/campaigns/{CAMPAIGN_ID}/pcs",
            json={"character_ref": alice_ref, "name": "Alice"},
        )
        assert resp.status_code == 201, resp.text

        # Bram enters through a confirmed cast change, then leaves. Leaving
        # clears the scene's membership fields, so the confirmed log is the
        # only surviving evidence of the appearance.
        scenes = app.container.scenes
        assert scenes is not None
        scene = await scenes.start_scene(SceneInit(campaign_id=CAMPAIGN_ID, title="The Gate"))
        bram_ref = f"library:worlds/{WORLD_ID}/characters/bram"
        change_id = await scenes.queue_cast_change(
            scene.id,
            character_ref=bram_ref,
            change=CastChange.ENTER,
            is_pc=False,
            evidence="slips in through the gate",
            confidence=0.9,
        )
        resp = await client.post(
            f"/api/campaigns/{CAMPAIGN_ID}/scenes/{scene.id}/cast-changes/{change_id}/confirm"
        )
        assert resp.status_code == 200, resp.text
        await scenes.remove_present_character(scene.id, bram_ref)

        resp = await client.get(f"/api/campaigns/{CAMPAIGN_ID}/cast")
        assert resp.status_code == 200, resp.text
        cast_ids = sorted(row["character"]["id"] for row in resp.json())
        assert cast_ids == ["alice", "bram"]

        # The full composition (World → Characters) still lists everyone.
        resp = await client.get(f"/api/campaigns/{CAMPAIGN_ID}/characters")
        assert resp.status_code == 200, resp.text
        all_ids = sorted(row["character"]["id"] for row in resp.json())
        assert all_ids == ["alice", "bram", "celia"]

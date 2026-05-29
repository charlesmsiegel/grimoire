"""L5 scenario: backfill the scene ledger from greetings via the HTTP API (#472).

Exercises ``POST /api/campaigns/{id}/scene-ledger/backfill`` through the real
composed FastAPI app (``ScenarioApp``): create a world, attach greetings, create
a campaign that composes the world, register a PC with role tags, then drive the
backfill endpoint and assert it adds only the greetings that apply to the party
(role-tag match) and is idempotent.

Greetings have no HTTP create endpoint (they are library files), so they are
seeded through the booted app's ``LibraryService`` — the same file-mediated
write the watcher would index. The endpoint under test is still driven over
HTTP, which is the point of an L5 scenario.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.testing.scenario import ScenarioApp


async def _add_greeting(app: ScenarioApp, world_id: str, gid: str, *, role_tags: list[str]) -> None:
    assert app.container is not None
    await app.container.library.create_entity(
        world_id,
        "greeting",
        gid,
        {
            "id": gid,
            "name": gid.replace("-", " ").title(),
            "role_tags": role_tags,
            "starting_location": "The Harbor",
        },
        f"Opening prose for {gid}.",
        source="scenario",
    )


async def test_backfill_scene_ledger_endpoint(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None
        client = app.client

        world_id = "ledger-world"
        resp = await client.post(
            "/api/library/worlds",
            json={
                "id": world_id,
                "meta": {
                    "name": "Ledger World",
                    "description": "World for the scene-ledger backfill scenario.",
                    "atmosphere": {"themes": ["scenario"], "tone": "neutral"},
                },
            },
        )
        assert resp.status_code == 201, resp.text

        # Three greetings: universal (no tags), one matching the PC, one not.
        await _add_greeting(app, world_id, "gr-universal", role_tags=[])
        await _add_greeting(app, world_id, "gr-hero", role_tags=["hero"])
        await _add_greeting(app, world_id, "gr-villain", role_tags=["villain"])

        campaign_id = "ledger-campaign"
        resp = await client.post(
            "/api/campaigns",
            json={
                "id": campaign_id,
                "name": "Ledger Campaign",
                "description": "Created by the scene-ledger backfill scenario.",
                "composition": {"worlds": [{"world_id": world_id, "priority": 0}]},
            },
        )
        assert resp.status_code == 201, resp.text

        # A PC whose role tags match only the "hero" greeting.
        resp = await client.post(
            f"/api/campaigns/{campaign_id}/pcs",
            json={
                "character_ref": f"{world_id}/characters/protagonist",
                "name": "Protagonist",
                "role_tags": ["hero"],
            },
        )
        assert resp.status_code == 201, resp.text

        # Backfill: universal + hero apply; villain does not.
        resp = await client.post(f"/api/campaigns/{campaign_id}/scene-ledger/backfill")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"added": 2}

        resp = await client.get(f"/api/campaigns/{campaign_id}/scene-ledger?status=active")
        assert resp.status_code == 200, resp.text
        active = resp.json()
        assert {i["greeting_id"] for i in active} == {"gr-universal", "gr-hero"}
        assert all(i["source"] == "greeting" for i in active)

        # Idempotent: a second backfill adds nothing.
        resp = await client.post(f"/api/campaigns/{campaign_id}/scene-ledger/backfill")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"added": 0}

"""L5 scenario: inventory operations end-to-end through the HTTP API.

Bootstraps a world + campaign, enables the inventory subsystem, then drives
the inventory REST API: acquire a fungible resource, read it back, transfer
part of it to another holder, and confirm the destination holds it.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.testing.scenario import ScenarioApp


async def test_inventory_end_to_end(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None and app.container is not None
        client = app.client

        world_id = "inv-world"
        resp = await client.post(
            "/api/library/worlds",
            json={
                "id": world_id,
                "meta": {
                    "name": "Inventory World",
                    "description": "Inventory scenario.",
                    "atmosphere": {"themes": ["loot"], "tone": "neutral"},
                },
            },
        )
        assert resp.status_code == 201, resp.text

        campaign_id = "inv-campaign"
        resp = await client.post(
            "/api/campaigns",
            json={
                "id": campaign_id,
                "name": "Inventory Scenario",
                "description": "Drives the inventory API.",
                "composition": {"worlds": [{"world_id": world_id, "priority": 0}]},
            },
        )
        assert resp.status_code == 201, resp.text

        # Enable the inventory subsystem for this campaign.
        await app.container.state_store.set_campaign_config(
            campaign_id, {"inventory": {"enabled": True}}
        )

        # Acquire a fungible resource for pc-alice.
        resp = await client.post(
            f"/api/campaigns/{campaign_id}/inventory/operations",
            json={
                "action": "acquire",
                "item": "gold",
                "holder": "pc-alice",
                "quantity": 120,
                "confidence": 1.0,
            },
        )
        assert resp.status_code == 200, resp.text

        resp = await client.get(f"/api/campaigns/{campaign_id}/inventory")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        refs = [e["item_ref"] for h in body["holders"] for e in h["entries"]]
        assert "resource:gold" in refs

        # Transfer part of it to pc-bob.
        resp = await client.post(
            f"/api/campaigns/{campaign_id}/inventory/operations",
            json={
                "action": "transfer",
                "item": "gold",
                "holder": "pc-alice",
                "to": "pc-bob",
                "quantity": 20,
                "confidence": 1.0,
            },
        )
        assert resp.status_code == 200, resp.text

        resp = await client.get(
            f"/api/campaigns/{campaign_id}/inventory/holders/character/pc-bob"
        )
        assert resp.status_code == 200, resp.text
        entries = resp.json()["entries"]
        assert any(e["item_ref"] == "resource:gold" for e in entries)

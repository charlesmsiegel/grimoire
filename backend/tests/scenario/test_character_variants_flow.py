"""L5 scenario: author an in-world character variant and select it per campaign.

Drives the full HTTP surface a frontend would use:

1. create a world and a base character,
2. author a variant overlay (``PUT .../characters/<id>/variants/<vid>``),
3. create a campaign composing the world,
4. select the variant for the campaign (``PUT /api/campaigns/<id>/variants``),
5. read the campaign's resolved characters and see the overlay applied
   (name/age from the variant, body replaced, identity unchanged),
6. clear the selection and see the base again.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.testing.scenario import ScenarioApp


async def test_variant_authoring_and_campaign_selection(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None
        client = app.client

        world_id = "variant-world"
        resp = await client.post(
            "/api/library/worlds",
            json={
                "id": world_id,
                "meta": {
                    "name": "Variant World",
                    "description": "World for the variant L5 scenario.",
                    "atmosphere": {"themes": ["scenario"], "tone": "neutral"},
                },
            },
        )
        assert resp.status_code == 201, resp.text

        resp = await client.post(
            f"/api/library/worlds/{world_id}/characters",
            json={
                "id": "alistair",
                "frontmatter": {
                    "id": "alistair",
                    "name": "Alistair",
                    "role": "major_npc",
                    "age": "300",
                },
                "body": "An elder of the chantry.",
            },
        )
        assert resp.status_code == 201, resp.text

        # Author a variant overlay: only the diff fields live in the file.
        resp = await client.put(
            f"/api/library/worlds/{world_id}/characters/alistair/variants/young",
            json={
                "label": "Young Alistair",
                "frontmatter": {"name": "Young Alistair", "age": "25"},
                "body": "A brash newcomer to the chantry.",
            },
        )
        assert resp.status_code == 200, resp.text
        variant = resp.json()
        assert variant["id"] == "young"
        assert variant["label"] == "Young Alistair"

        resp = await client.get(f"/api/library/worlds/{world_id}/characters/alistair/variants")
        assert resp.status_code == 200, resp.text
        assert [v["id"] for v in resp.json()] == ["young"]

        campaign_id = "variant-campaign"
        resp = await client.post(
            "/api/campaigns",
            json={
                "id": campaign_id,
                "name": "Variant Campaign",
                "composition": {"worlds": [{"world_id": world_id, "priority": 0}]},
            },
        )
        assert resp.status_code == 201, resp.text

        # Selecting an unknown variant is rejected before anything persists.
        resp = await client.put(
            f"/api/campaigns/{campaign_id}/variants",
            json={"variants": {f"worlds/{world_id}/characters/alistair": "ghost"}},
        )
        assert resp.status_code == 422, resp.text

        resp = await client.put(
            f"/api/campaigns/{campaign_id}/variants",
            json={"variants": {f"worlds/{world_id}/characters/alistair": "young"}},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["variants"] == {f"worlds/{world_id}/characters/alistair": "young"}

        # The campaign-resolved character view shows the overlay.
        resp = await client.get(f"/api/campaigns/{campaign_id}/characters")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        match = [r for r in rows if r["character"]["id"] == "alistair"]
        assert match, rows
        character = match[0]["character"]
        assert character["name"] == "Young Alistair"
        assert character["age"] == "25"
        assert "brash newcomer" in character["body"]

        # Clearing the selection restores the base portrayal.
        resp = await client.put(f"/api/campaigns/{campaign_id}/variants", json={"variants": {}})
        assert resp.status_code == 200, resp.text
        resp = await client.get(f"/api/campaigns/{campaign_id}/characters")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        character = next(r["character"] for r in rows if r["character"]["id"] == "alistair")
        assert character["name"] == "Alistair"
        assert character["age"] == "300"

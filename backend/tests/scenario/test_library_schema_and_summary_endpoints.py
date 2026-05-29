"""L5 scenario: the structured-form support endpoints added for issue #441.

Exercises the two read endpoints the world-creation UI depends on, through
the real FastAPI ASGI stack (no mocked services), per CLAUDE.md's rule that a
new API endpoint gets a scenario-level test:

* ``GET /api/library/entity-schemas/{kind}`` — JSON schema that drives the
  front-end structured entity forms.
* ``GET /api/library/worlds/{world_id}/summary`` — per-kind entity counts and
  setup flags that drive the world hub.

The summary path is exercised end-to-end: create a world, create a character
through the same HTTP surface the frontend uses, then confirm the summary
reflects both the world meta and the new entity.

World creation would normally call the gateway for atmosphere auto-generation;
we pass an ``atmosphere`` block in ``meta`` so that branch is skipped (mirrors
``test_bootstrap_world_campaign``), keeping the scenario off the LLM path.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.testing.scenario import ScenarioApp


async def test_entity_schema_endpoint_through_stack(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None
        client = app.client

        resp = await client.get("/api/library/entity-schemas/character")
        assert resp.status_code == 200, resp.text
        props = resp.json()["properties"]
        for key in ("name", "role", "voice", "structural_relationships", "image"):
            assert key in props

        resp = await client.get("/api/library/entity-schemas/widget")
        assert resp.status_code == 404, resp.text


async def test_world_summary_endpoint_through_stack(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None
        client = app.client

        world_id = "scenario-summary-world"
        resp = await client.post(
            "/api/library/worlds",
            json={
                "id": world_id,
                "meta": {
                    "name": "Summary Scenario World",
                    "description": "A world used by the hub-summary L5 scenario.",
                    "genre": "Grimdark fantasy",
                    "atmosphere": {"tone": "neutral"},
                },
            },
        )
        assert resp.status_code == 201, resp.text

        # Empty world: every kind reports zero, but the meta flags are set.
        resp = await client.get(f"/api/library/worlds/{world_id}/summary")
        assert resp.status_code == 200, resp.text
        summary = resp.json()
        assert set(summary["counts"]) == {
            "characters",
            "locations",
            "items",
            "lore",
            "factions",
            "monsters",
            "greetings",
        }
        assert summary["counts"]["characters"] == 0
        assert summary["has_description"] is True
        assert summary["has_genre"] is True

        # Create a character through the same HTTP surface the frontend uses.
        resp = await client.post(
            f"/api/library/worlds/{world_id}/character",
            json={
                "id": "alistair",
                "frontmatter": {"name": "Alistair", "role": "major_npc"},
                "body": "A patient guide.",
            },
        )
        assert resp.status_code == 201, resp.text

        # The summary now reflects the new entity.
        resp = await client.get(f"/api/library/worlds/{world_id}/summary")
        assert resp.status_code == 200, resp.text
        assert resp.json()["counts"]["characters"] == 1

        # Unknown world surfaces a 404 rather than an empty summary.
        resp = await client.get("/api/library/worlds/no-such-world/summary")
        assert resp.status_code == 404, resp.text

"""L5 scenario: the style-guide delete endpoint added in #517.

Exercises ``DELETE /api/library/style-guides/{guide_id}`` through the real
FastAPI ASGI stack (no mocked services), per CLAUDE.md's rule that a new API
endpoint gets a scenario-level test. The guide is created and fetched through
the same HTTP surface the frontend uses, then deleted and confirmed gone.

Existence is checked via the item endpoint (``GET .../{guide_id}``), which is
store-backed and reflects a write synchronously; the collection listing is
driven by the kind-index the file watcher populates asynchronously, so it is
not a reliable signal within a single scenario request cycle.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.testing.scenario import ScenarioApp

pytestmark = pytest.mark.scenario


async def test_delete_style_guide_through_stack(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None
        client = app.client

        guide_id = "scenario-noir"
        resp = await client.post(
            "/api/library/style-guides",
            json={
                "id": guide_id,
                "name": "Scenario Noir",
                "description": "A style guide used by the delete L5 scenario.",
                "tags": ["noir"],
                "voice": ["terse"],
            },
        )
        assert resp.status_code == 201, resp.text

        # The guide is fetchable through the item endpoint.
        resp = await client.get(f"/api/library/style-guides/{guide_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["asset_id"] == guide_id

        # Delete it.
        resp = await client.delete(f"/api/library/style-guides/{guide_id}")
        assert resp.status_code == 204, resp.text

        # It is gone: the item endpoint now reports 404.
        resp = await client.get(f"/api/library/style-guides/{guide_id}")
        assert resp.status_code == 404, resp.text

        # Deleting a guide that does not exist surfaces a 404, not a 204.
        resp = await client.delete("/api/library/style-guides/no-such-guide")
        assert resp.status_code == 404, resp.text

"""L5 scenario: generated images are served over HTTP (#582).

Drives the loop the Images gallery and play view rely on: queue a
generation through the REST API, wait for the image row to land, then
fetch the PNG and its thumbnail from ``GET /api/files/{file_path}`` —
the URL shape the frontend builds from ``images.file_path``. Also
confirms the endpoint keeps the YAML metadata sidecar, the SQLite DB,
and traversal attempts private.

The in-memory diffusers backend stands in for an installed imagegen
plugin (bundled plugins are disabled in the test harness); registering
it on the running container is scenario-only setup, like the inventory
scenario's subsystem toggle.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from grimoire.imagegen import InMemoryDiffusersBackend
from grimoire.testing.scenario import ScenarioApp

pytestmark = [pytest.mark.scenario, pytest.mark.asyncio]


async def test_generated_image_served_over_http(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None and app.container is not None
        client = app.client

        world_id = "img-world"
        resp = await client.post(
            "/api/library/worlds",
            json={
                "id": world_id,
                "meta": {
                    "name": "Image World",
                    "description": "Image-serving scenario.",
                    "atmosphere": {"themes": ["noir"], "tone": "neutral"},
                },
            },
        )
        assert resp.status_code == 201, resp.text

        campaign_id = "img-campaign"
        resp = await client.post(
            "/api/campaigns",
            json={
                "id": campaign_id,
                "name": "Image Scenario",
                "description": "Serves a generated image over /api/files/.",
                "composition": {"worlds": [{"world_id": world_id, "priority": 0}]},
            },
        )
        assert resp.status_code == 201, resp.text

        app.container.imagegen.registry.register(InMemoryDiffusersBackend())
        resp = await client.put(
            f"/api/campaigns/{campaign_id}/imagegen/active",
            json={"backend_id": "diffusers-memory"},
        )
        assert resp.status_code == 200, resp.text

        resp = await client.post(
            f"/api/campaigns/{campaign_id}/images/generate",
            json={
                "request": {
                    "prompt": "a rain-slick alley",
                    "width": 32,
                    "height": 32,
                    "steps": 1,
                    "cfg_scale": 1.0,
                    "sampler": "UniPC",
                    "seed": 7,
                }
            },
        )
        assert resp.status_code == 202, resp.text

        image = None
        for _ in range(100):
            resp = await client.get(f"/api/campaigns/{campaign_id}/images")
            assert resp.status_code == 200, resp.text
            rows = resp.json()
            if rows:
                image = rows[0]
                break
            await asyncio.sleep(0.05)
        assert image is not None, "image row never appeared"

        # The gallery URL shape: /api/files/{data-root-relative file_path}.
        resp = await client.get(f"/api/files/{image['file_path']}")
        assert resp.status_code == 200, resp.text
        assert resp.content.startswith(b"\x89PNG")
        assert resp.headers["content-type"] == "image/png"

        resp = await client.get(f"/api/files/{image['thumbnail_path']}")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "image/jpeg"

        # The YAML metadata sidecar next to the PNG stays private...
        sidecar = image["file_path"].rsplit(".", 1)[0] + ".yaml"
        resp = await client.get(f"/api/files/{sidecar}")
        assert resp.status_code == 404

        # ...and so do the SQLite DB and traversal attempts.
        resp = await client.get("/api/files/campaigns.sqlite")
        assert resp.status_code == 404
        resp = await client.get(
            f"/api/files/campaigns/{campaign_id}/images/%2e%2e/%2e%2e/%2e%2e/campaigns.sqlite"
        )
        assert resp.status_code == 404

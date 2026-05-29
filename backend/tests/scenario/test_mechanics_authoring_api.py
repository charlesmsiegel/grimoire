"""L5 scenario: mechanics authoring write routes.

Drives the same surface the Library → Mechanics editor uses: create a module,
confirm it appears in the installed list and loads green, then write a sheet
schema and read it back. Also pins the error contract (409 duplicate, 422
invalid schema).
"""

from __future__ import annotations

from pathlib import Path

from grimoire.testing.scenario import ScenarioApp


async def test_create_then_edit_module(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None
        client = app.client

        resp = await client.post(
            "/api/library/mechanics",
            json={
                "id": "scn",
                "name": "Scenario System",
                "version": "1.0.0",
                "api_version": "1",
                "sheet_kinds": ["character"],
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "scn" in body["report"]["loaded"]

        installed = (await client.get("/api/mechanics/installed")).json()
        assert any(m["manifest"]["id"] == "scn" for m in installed)

        schema = {"type": "object", "properties": {"hp": {"type": "integer"}}}
        put = await client.put("/api/library/mechanics/scn/sheets/character", json=schema)
        assert put.status_code == 200, put.text

        got = (await client.get("/api/mechanics/scn/sheets/character")).json()
        assert got == schema


async def test_put_theme_css_accepts_raw_text(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None
        client = app.client
        spec = {
            "id": "themed",
            "name": "Themed",
            "version": "1.0.0",
            "api_version": "1",
            "ui": {"theme_css": "theme.css"},
        }
        assert (await client.post("/api/library/mechanics", json=spec)).status_code == 201

        css = ".sheet { color: rebeccapurple; }"
        put = await client.put(
            "/api/library/mechanics/themed/theme.css",
            content=css,
            headers={"Content-Type": "text/plain"},
        )
        assert put.status_code == 200, put.text

        got = await client.get("/api/library/mechanics/themed/theme.css")
        assert got.status_code == 200, got.text
        assert got.text == css


async def test_create_duplicate_is_conflict(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None
        client = app.client
        spec = {"id": "dupapi", "name": "Dup", "version": "1.0.0", "api_version": "1"}
        assert (await client.post("/api/library/mechanics", json=spec)).status_code == 201
        assert (await client.post("/api/library/mechanics", json=spec)).status_code == 409


async def test_invalid_schema_is_422(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None
        client = app.client
        spec = {
            "id": "valapi",
            "name": "Val",
            "version": "1.0.0",
            "api_version": "1",
            "sheet_kinds": ["character"],
        }
        assert (await client.post("/api/library/mechanics", json=spec)).status_code == 201
        bad = await client.put("/api/library/mechanics/valapi/sheets/character", json={"type": 123})
        assert bad.status_code == 422
        assert bad.json()["detail"]

"""REST contract tests for the narrative-extras routes.

Uses ``httpx.AsyncClient`` with an ``ASGITransport`` rather than FastAPI's
``TestClient``. The latter spawns a sync portal loop distinct from
pytest-asyncio's loop, which breaks aiosqlite connections (they bind to
the loop they were opened on -- the test then fails on teardown with
"Event loop is closed"). Running everything on a single async loop keeps
the db pool happy.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from grimoire.api.container import ServiceContainer
from grimoire.api.extras import router as extras_router
from grimoire.extras import ExtrasMirror, ExtrasService
from grimoire.library import LibraryService
from grimoire.state_store import StateStore
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db
from grimoire.types.composition import Composition, WorldRef


@pytest.fixture
async def client(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir(exist_ok=True)
    db = Database(stamp_migrated_db(tmp_path / "test.sqlite"), pool_size=2)
    await db.connect()
    store = StateStore(db=db, data_root=data_root)
    library = LibraryService(store)
    extras = ExtrasService(library=library, store=store, mirror=ExtrasMirror(db))

    # Seed a world + character so writes have a target.
    await store.write_library_file(
        library_id="worlds/wod/world",
        frontmatter={"id": "wod", "name": "WoD", "version": 1},
        body="",
        source="test",
    )
    await store.write_library_file(
        library_id="worlds/wod/characters/winifred",
        frontmatter={"id": "winifred", "name": "winifred"},
        body="",
        source="test",
    )
    await store.upsert_campaign(campaign_id="camp", name="Camp")
    await library.set_composition(
        "camp",
        Composition(worlds=[WorldRef(world_id="wod", priority=1, include=None)]),
    )

    container = ServiceContainer()
    container.db = db
    container.state_store = store
    container.library = library
    container.extras_service = extras

    app = FastAPI()
    app.include_router(extras_router, prefix="/api")
    app.state.container = container

    # Manual close order: yield → close httpx (drains in-flight requests) →
    # close db. Reversing this leaves aiosqlite futures bound to the loop
    # while httpx is still tearing down its anyio backend, which CI
    # surfaces as "Event loop is closed" on Python 3.12 + aiosqlite 0.22.
    transport = httpx.ASGITransport(app=app)
    ac = httpx.AsyncClient(transport=transport, base_url="http://test")
    try:
        yield ac
    finally:
        await ac.aclose()
        await db.close()


async def test_put_then_get_library_extra(client: httpx.AsyncClient):
    r = await client.put(
        "/api/library/wod/character/winifred/extras/favorite_drink",
        json={"value": "Glenfarclas 25"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["extra"]["value"] == "Glenfarclas 25"

    listing = await client.get("/api/library/wod/character/winifred/extras")
    assert listing.status_code == 200
    extras = listing.json()["extras"]
    assert "favorite_drink" in extras
    assert extras["favorite_drink"]["value"] == "Glenfarclas 25"


async def test_put_reserved_prefix_returns_422(client: httpx.AsyncClient):
    r = await client.put(
        "/api/library/wod/character/winifred/extras/_internal_secret",
        json={"value": "x"},
    )
    assert r.status_code == 422


async def test_delete_library_extra(client: httpx.AsyncClient):
    await client.put(
        "/api/library/wod/character/winifred/extras/scars",
        json={"value": ["above brow"]},
    )
    r = await client.delete("/api/library/wod/character/winifred/extras/scars")
    assert r.status_code == 204
    listing = await client.get("/api/library/wod/character/winifred/extras")
    assert "scars" not in listing.json()["extras"]


async def test_campaign_override_cascade(client: httpx.AsyncClient):
    await client.put(
        "/api/library/wod/character/winifred/extras/drink",
        json={"value": "wine"},
    )
    r = await client.put(
        "/api/campaigns/camp/character/winifred/extras/drink?world_id=wod",
        json={"value": "whisky"},
    )
    assert r.status_code == 200, r.text
    resolved = (
        await client.get("/api/campaigns/camp/character/winifred/extras?world_id=wod")
    ).json()["extras"]
    assert resolved["drink"]["value"] == "whisky"


async def test_search_endpoint_returns_hits(client: httpx.AsyncClient):
    await client.put(
        "/api/library/wod/character/winifred/extras/dialect_notes",
        json={"value": "drops aitches when angry"},
    )
    r = await client.get("/api/search/extras?q=aitches")
    assert r.status_code == 200
    body = r.json()
    assert any(hit["key"] == "dialect_notes" for hit in body["hits"])


async def test_promote_to_library(client: httpx.AsyncClient):
    await client.put(
        "/api/campaigns/camp/character/winifred/extras/ring_pattern?world_id=wod",
        json={"value": "signet, three diamonds"},
    )
    r = await client.post(
        "/api/campaigns/camp/character/winifred/extras/ring_pattern/promote-to-library?world_id=wod"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["extra"]["value"] == "signet, three diamonds"

    # Library now owns the value.
    lib = (await client.get("/api/library/wod/character/winifred/extras")).json()["extras"]
    assert "ring_pattern" in lib


async def test_promote_missing_returns_409(client: httpx.AsyncClient):
    r = await client.post(
        "/api/campaigns/camp/character/winifred/extras/no_such_key/promote-to-library?world_id=wod"
    )
    assert r.status_code == 409


async def test_unknown_kind_returns_404(client: httpx.AsyncClient):
    r = await client.get("/api/library/wod/dragon/winifred/extras")
    assert r.status_code == 404


async def test_promote_without_world_id_returns_422(client: httpx.AsyncClient):
    r = await client.post("/api/campaigns/camp/character/winifred/extras/foo/promote-to-library")
    assert r.status_code == 422


async def test_get_raw_specific_scope(client: httpx.AsyncClient):
    await client.put(
        "/api/library/wod/character/winifred/extras/lib_only",
        json={"value": "library"},
    )
    await client.put(
        "/api/campaigns/camp/character/winifred/extras/override_only?world_id=wod",
        json={"value": "override"},
    )
    lib = (
        await client.get(
            "/api/campaigns/camp/character/winifred/extras/raw?world_id=wod&scope=library"
        )
    ).json()["extras"]
    override = (
        await client.get(
            "/api/campaigns/camp/character/winifred/extras/raw?world_id=wod&scope=override"
        )
    ).json()["extras"]
    assert "lib_only" in lib and "override_only" not in lib
    assert "override_only" in override and "lib_only" not in override

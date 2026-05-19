"""REST contract tests for the narrative-extras routes.

Wires a real ExtrasService into the test container so we exercise the
service code path end-to-end. Most failures (422 reserved-prefix, 422 hard
cap, 404 unknown key) bubble up through the service's own validators.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grimoire.api.container import ServiceContainer
from grimoire.extras import ExtrasMirror, ExtrasService
from grimoire.library import LibraryService
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.types.composition import Composition, WorldRef


@pytest.fixture
async def wired_app(tmp_path: Path):
    """Build an isolated FastAPI app with just the extras router.

    Avoids the production lifespan (which creates its own database pool to
    settings.resolved_database_path and would race with the test's own
    services). Bypassing it keeps the test focused on the router contract.
    """
    from fastapi import FastAPI

    from grimoire.api.extras import router as extras_router

    data_root = tmp_path / "data"
    data_root.mkdir(exist_ok=True)
    db = Database(tmp_path / "test.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
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
    try:
        yield app
    finally:
        await db.close()


@pytest.fixture
def client(wired_app):
    with TestClient(wired_app) as test_client:
        yield test_client


def test_put_then_get_library_extra(client: TestClient):
    r = client.put(
        "/api/library/wod/character/winifred/extras/favorite_drink",
        json={"value": "Glenfarclas 25"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["extra"]["value"] == "Glenfarclas 25"

    listing = client.get("/api/library/wod/character/winifred/extras")
    assert listing.status_code == 200
    extras = listing.json()["extras"]
    assert "favorite_drink" in extras
    assert extras["favorite_drink"]["value"] == "Glenfarclas 25"


def test_put_reserved_prefix_returns_422(client: TestClient):
    r = client.put(
        "/api/library/wod/character/winifred/extras/_internal_secret",
        json={"value": "x"},
    )
    assert r.status_code == 422


def test_delete_library_extra(client: TestClient):
    client.put(
        "/api/library/wod/character/winifred/extras/scars",
        json={"value": ["above brow"]},
    )
    r = client.delete("/api/library/wod/character/winifred/extras/scars")
    assert r.status_code == 204
    listing = client.get("/api/library/wod/character/winifred/extras")
    assert "scars" not in listing.json()["extras"]


def test_campaign_override_cascade(client: TestClient):
    client.put(
        "/api/library/wod/character/winifred/extras/drink",
        json={"value": "wine"},
    )
    r = client.put(
        "/api/campaigns/camp/character/winifred/extras/drink?world_id=wod",
        json={"value": "whisky"},
    )
    assert r.status_code == 200, r.text
    resolved = client.get("/api/campaigns/camp/character/winifred/extras?world_id=wod").json()[
        "extras"
    ]
    assert resolved["drink"]["value"] == "whisky"


def test_search_endpoint_returns_hits(client: TestClient):
    client.put(
        "/api/library/wod/character/winifred/extras/dialect_notes",
        json={"value": "drops aitches when angry"},
    )
    r = client.get("/api/search/extras?q=aitches")
    assert r.status_code == 200
    body = r.json()
    assert any(hit["key"] == "dialect_notes" for hit in body["hits"])


def test_promote_to_library(client: TestClient):
    client.put(
        "/api/campaigns/camp/character/winifred/extras/ring_pattern?world_id=wod",
        json={"value": "signet, three diamonds"},
    )
    r = client.post(
        "/api/campaigns/camp/character/winifred/extras/ring_pattern/promote-to-library?world_id=wod"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["extra"]["value"] == "signet, three diamonds"

    # Library now owns the value.
    lib = client.get("/api/library/wod/character/winifred/extras").json()["extras"]
    assert "ring_pattern" in lib


def test_promote_missing_returns_409(client: TestClient):
    r = client.post(
        "/api/campaigns/camp/character/winifred/extras/no_such_key/promote-to-library?world_id=wod"
    )
    assert r.status_code == 409


def test_unknown_kind_returns_404(client: TestClient):
    r = client.get("/api/library/wod/dragon/winifred/extras")
    assert r.status_code == 404


def test_promote_without_world_id_returns_422(client: TestClient):
    r = client.post("/api/campaigns/camp/character/winifred/extras/foo/promote-to-library")
    assert r.status_code == 422


def test_get_raw_specific_scope(client: TestClient):
    client.put(
        "/api/library/wod/character/winifred/extras/lib_only",
        json={"value": "library"},
    )
    client.put(
        "/api/campaigns/camp/character/winifred/extras/override_only?world_id=wod",
        json={"value": "override"},
    )
    lib = client.get(
        "/api/campaigns/camp/character/winifred/extras/raw?world_id=wod&scope=library"
    ).json()["extras"]
    override = client.get(
        "/api/campaigns/camp/character/winifred/extras/raw?world_id=wod&scope=override"
    ).json()["extras"]
    assert "lib_only" in lib and "override_only" not in lib
    assert "override_only" in override and "lib_only" not in override

"""REST contract tests for the character-card import routes.

Spec: docs/superpowers/specs/2026-05-19-card-imports-design.md §REST.
"""

from __future__ import annotations

import base64
import json
import struct
import time
import zlib
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from grimoire.api import imports as imports_module
from grimoire.api.container import ServiceContainer
from grimoire.api.imports import router as imports_router
from grimoire.characters import CharactersService
from grimoire.library import LibraryService
from grimoire.mechanics import MechanicsConfig, MechanicsService
from grimoire.state_store import StateStore
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db


def _make_chunk(kind: bytes, data: bytes) -> bytes:
    length = struct.pack(">I", len(data))
    crc = struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return length + kind + data + crc


def _png_with_card(card: dict) -> bytes:
    payload_b64 = base64.b64encode(json.dumps(card).encode("utf-8"))
    ihdr = _make_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    text = _make_chunk(b"tEXt", b"chara\x00" + payload_b64)
    idat = _make_chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff"))
    end = _make_chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + text + idat + end


@pytest.fixture
async def client(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(stamp_migrated_db(tmp_path / "test.sqlite"), pool_size=2)
    await db.connect()
    store = StateStore(db=db, data_root=data_root)
    library = LibraryService(store)
    mechanics = MechanicsService(
        config=MechanicsConfig(root=tmp_path / "mech"),
        state_store=store,
    )
    (tmp_path / "mech").mkdir(exist_ok=True)
    characters = CharactersService(library, mechanics)

    await store.write_library_file(
        library_id="worlds/w1/world/w1",
        frontmatter={"id": "w1", "name": "w1", "version": 1},
        body="",
        source="test",
    )

    container = ServiceContainer()
    container.db = db
    container.state_store = store
    container.library = library
    container.characters = characters

    app = FastAPI()
    app.include_router(imports_router, prefix="/api")
    app.state.container = container

    transport = httpx.ASGITransport(app=app)
    ac = httpx.AsyncClient(transport=transport, base_url="http://test")
    try:
        yield ac, store
    finally:
        await ac.aclose()
        await db.close()


async def test_preview_returns_ingest_and_id(client) -> None:
    ac, _ = client
    card = {
        "spec": "chara_card_v2",
        "data": {
            "name": "Beatrice",
            "description": "A witch.",
            "first_mes": "Hi from {{char}}.",
            "alternate_greetings": ["Alt!"],
            "tags": ["witch"],
        },
    }
    png = _png_with_card(card)
    files = {"file": ("card.png", png, "image/png")}
    response = await ac.post("/api/library/worlds/w1/imports/sillytavern/preview", files=files)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["preview_id"]
    assert payload["ingested"]["data"]["name"] == "Beatrice"
    assert payload["ingested"]["greetings"]


async def test_preview_returns_lore_suggestions_parallel_to_entries(client) -> None:
    ac, _ = client
    card = {
        "spec": "chara_card_v2",
        "data": {
            "name": "Beatrice",
            "description": "A witch.",
            "first_mes": "Hi.",
            "character_book": {
                "entries": [
                    {
                        "name": "Brackhollow Cathedral",
                        "keys": ["cathedral"],
                        "content": "A village cathedral located on the hill.",
                    },
                    {"name": "obscure note", "keys": ["x"], "content": "tt."},
                ],
            },
        },
    }
    png = _png_with_card(card)
    response = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/preview",
        files={"file": ("card.png", png, "image/png")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    suggestions = payload["lore_suggestions"]
    assert len(suggestions) == 2
    by_index = {s["source_index"]: s for s in suggestions}
    # "Brackhollow Cathedral" has a place noun in the title → should suggest location.
    cathedral = by_index[0]
    assert cathedral["kind"] == "location"
    assert cathedral["confidence"] >= 0.6
    assert "place noun" in cathedral["reason"]
    # No-signal entry stays at lore.
    weak = by_index[1]
    assert weak["kind"] == "lore"
    assert weak["confidence"] == 0.0


async def test_preview_then_commit_writes_character(client) -> None:
    ac, _store = client
    card = {
        "spec": "chara_card_v2",
        "data": {
            "name": "Beatrice",
            "description": "A witch.",
            "first_mes": "Hi.",
        },
    }
    png = _png_with_card(card)
    response = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/preview",
        files={"file": ("card.png", png, "image/png")},
    )
    preview_id = response.json()["preview_id"]
    commit = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/commit",
        json={"preview_id": preview_id, "options": {}},
    )
    assert commit.status_code == 201, commit.text
    body = commit.json()
    assert "beatrice" in body["result"]["created"]
    assert any(ref.startswith("greeting:beatrice--") for ref in body["result"]["created"])


async def test_commit_rejects_unknown_preview_id(client) -> None:
    ac, _ = client
    response = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/commit",
        json={"preview_id": "deadbeef", "options": {}},
    )
    assert response.status_code == 404


async def test_commit_rejects_wrong_world(client) -> None:
    ac, _ = client
    card = {"spec": "chara_card_v2", "data": {"name": "X"}}
    png = _png_with_card(card)
    response = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/preview",
        files={"file": ("card.png", png, "image/png")},
    )
    preview_id = response.json()["preview_id"]
    bad = await ac.post(
        "/api/library/worlds/w2/imports/sillytavern/commit",
        json={"preview_id": preview_id, "options": {}},
    )
    assert bad.status_code == 400


def _card_with_two_lore() -> dict:
    return {
        "spec": "chara_card_v2",
        "data": {
            "name": "Beatrice",
            "description": "A witch.",
            "first_mes": "Hi.",
            "character_book": {
                "entries": [
                    {
                        "name": "Brackhollow Cathedral",
                        "keys": ["cathedral"],
                        "content": "A village cathedral located on the hill.",
                    },
                    {"name": "Some Note", "keys": ["note"], "content": "Random fact."},
                ],
            },
        },
    }


async def test_commit_rejects_unknown_lore_override_kind(client) -> None:
    ac, _ = client
    png = _png_with_card(_card_with_two_lore())
    response = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/preview",
        files={"file": ("card.png", png, "image/png")},
    )
    preview_id = response.json()["preview_id"]
    commit = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/commit",
        json={
            "preview_id": preview_id,
            "options": {},
            "lore_overrides": [{"source_index": 0, "kind": "quest"}],
        },
    )
    assert commit.status_code == 422  # pydantic Literal mismatch


async def test_commit_rejects_duplicate_source_index(client) -> None:
    ac, _ = client
    png = _png_with_card(_card_with_two_lore())
    preview = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/preview",
        files={"file": ("card.png", png, "image/png")},
    )
    preview_id = preview.json()["preview_id"]
    commit = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/commit",
        json={
            "preview_id": preview_id,
            "options": {},
            "lore_overrides": [
                {"source_index": 0, "kind": "skip"},
                {"source_index": 0, "kind": "character"},
            ],
        },
    )
    assert commit.status_code == 400
    assert "declared twice" in commit.text


async def test_commit_rejects_out_of_range_source_index(client) -> None:
    ac, _ = client
    png = _png_with_card(_card_with_two_lore())
    preview = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/preview",
        files={"file": ("card.png", png, "image/png")},
    )
    preview_id = preview.json()["preview_id"]
    commit = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/commit",
        json={
            "preview_id": preview_id,
            "options": {},
            "lore_overrides": [{"source_index": 99, "kind": "character"}],
        },
    )
    assert commit.status_code == 400
    assert "source_index" in commit.text


async def test_commit_rejects_missing_required_override(client) -> None:
    ac, _ = client
    png = _png_with_card(_card_with_two_lore())
    preview = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/preview",
        files={"file": ("card.png", png, "image/png")},
    )
    preview_id = preview.json()["preview_id"]
    commit = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/commit",
        json={
            "preview_id": preview_id,
            "options": {},
            "lore_overrides": [{"source_index": 0, "kind": "location"}],  # missing 'kind' override
        },
    )
    assert commit.status_code == 400
    assert "required override" in commit.text.lower() or "kind" in commit.text


async def test_commit_with_lore_override_promotes_entity(client) -> None:
    ac, _ = client
    png = _png_with_card(_card_with_two_lore())
    preview = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/preview",
        files={"file": ("card.png", png, "image/png")},
    )
    preview_id = preview.json()["preview_id"]
    commit = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/commit",
        json={
            "preview_id": preview_id,
            "options": {},
            "lore_overrides": [
                {"source_index": 0, "kind": "location", "overrides": {"kind": "building"}},
                {"source_index": 1, "kind": "skip"},
            ],
        },
    )
    assert commit.status_code == 201, commit.text
    created = commit.json()["result"]["created"]
    assert any(c.startswith("location:beatrice--brackhollow-cathedral") for c in created)
    assert not any(c.startswith("lore:") for c in created)


async def test_list_and_get_reports(client) -> None:
    ac, _store = client
    card = {"spec": "chara_card_v2", "data": {"name": "Beatrice", "first_mes": "Hi."}}
    png = _png_with_card(card)
    response = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/preview",
        files={"file": ("card.png", png, "image/png")},
    )
    preview_id = response.json()["preview_id"]
    await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/commit",
        json={"preview_id": preview_id, "options": {}},
    )
    listed = await ac.get("/api/library/imports")
    assert listed.status_code == 200
    reports = listed.json()["reports"]
    assert reports
    body = await ac.get(f"/api/library/imports/{reports[0]['id']}")
    assert body.status_code == 200
    assert "Beatrice" in body.json()["body"]


def test_store_preview_evicts_expired_entries() -> None:
    """``_store_preview`` sweeps already-expired slots before inserting."""
    imports_module._PREVIEW_CACHE.clear()
    now = time.time()
    expired = imports_module._PreviewSlot(
        ingested=object(),  # type: ignore[arg-type]  # slot only stores it
        world_id="w",
        filename="old",
        expires_at=now - 1,
    )
    imports_module._PREVIEW_CACHE["stale"] = expired
    fresh = imports_module._PreviewSlot(
        ingested=object(),  # type: ignore[arg-type]
        world_id="w",
        filename="new",
        expires_at=now + 1000,
    )
    imports_module._store_preview("fresh", fresh)
    assert "stale" not in imports_module._PREVIEW_CACHE
    assert "fresh" in imports_module._PREVIEW_CACHE
    imports_module._PREVIEW_CACHE.clear()


def test_store_preview_is_bounded_by_max_entries() -> None:
    """The preview cache can never grow past ``MAX_PREVIEW_ENTRIES``; the
    oldest (soonest-to-expire) slots are evicted when the cap is reached."""
    imports_module._PREVIEW_CACHE.clear()
    now = time.time()
    overflow = imports_module.MAX_PREVIEW_ENTRIES + 10
    for i in range(overflow):
        slot = imports_module._PreviewSlot(
            ingested=object(),  # type: ignore[arg-type]
            world_id="w",
            filename=f"f{i}",
            # Strictly increasing expiry → id0 is the oldest.
            expires_at=now + 1000 + i,
        )
        imports_module._store_preview(f"id{i}", slot)

    assert len(imports_module._PREVIEW_CACHE) == imports_module.MAX_PREVIEW_ENTRIES
    # The earliest-inserted (soonest-expiring) ids were evicted.
    assert "id0" not in imports_module._PREVIEW_CACHE
    assert f"id{overflow - 1}" in imports_module._PREVIEW_CACHE
    imports_module._PREVIEW_CACHE.clear()

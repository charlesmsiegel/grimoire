"""`image_usage.find` and `GET /api/images/{image_id}/usage`: where a picture is placed.

Derived on demand from the placements under every world and campaign, so there
is nothing stored to drift. The surfaces are the roster `test_image_surfaces`
drives; every name is invented.
"""

from __future__ import annotations

import json

import pytest

from grimoire.store import assets, entities, image_collections, image_refs, image_store, revision
from tests.collection_fixtures import format1
from tests.test_image_surfaces import COLLECTION, SURFACES, _png, _put
from tests.test_image_surfaces import ids as surface_ids  # noqa: F401  (the fixture)

BUCKETS = ["characters", "pcs", "entities", "greetings", "world_images",
           "campaign_images", "covers", "collections"]


def _usage(client, image_id: str) -> dict:
    r = client.get(f"/api/images/{image_id}/usage")
    assert r.status_code == 200, r.text
    return r.json()


def _key(entry: dict) -> str:
    return json.dumps(entry, sort_keys=True)


def _expected(surface_id: str, ids: dict, name: str) -> list[tuple[str, dict]]:
    """The (bucket, entry) pairs the surface's placement must produce."""
    wscope, cscope = f"world:{ids['wid']}", f"campaign:{ids['cid']}"
    scope_word, _, base = surface_id.partition("-")
    scope = wscope if scope_word == "world" else cscope
    if surface_id == "collection-member":
        return [("world_images", {"wid": ids["wid"], "name": name}),
                ("collections", {"wid": ids["wid"], "collection": COLLECTION})]
    if surface_id == "greeting":
        return [("greetings", {"scope": wscope, "id": ids["gid"], "name": name})]
    if base == "cover":
        return [("covers", {"scope": scope})]
    if base == "library":
        if scope_word == "world":
            return [("world_images", {"wid": ids["wid"], "name": name})]
        return [("campaign_images", {"cid": ids["cid"], "name": name})]
    if base in ("characters", "pcs"):
        rid = ids["char"] if base == "characters" else ids["pc"]
        return [(base, {"scope": scope, "id": rid, "vid": "default", "name": name})]
    assert base in entities.ENTITY_KINDS, surface_id
    return [("entities", {"scope": scope, "kind": base, "id": ids[base],
                          "vid": "default", "name": name})]


@pytest.mark.parametrize("surface", SURFACES, ids=[s.id for s in SURFACES])
def test_usage_finds_every_surface(client, surface_ids, surface):  # noqa: F811
    ids = surface_ids
    r = surface.upload(client, ids)
    assert 200 <= r.status_code < 300, getattr(r, "text", r)
    body = r.json()
    name = body.get("name") or "cover"
    got = _usage(client, body["image_id"])
    assert list(got) == BUCKETS
    for bucket, entry in _expected(surface.id, ids, name):
        assert entry in got[bucket], (bucket, got)
    for entries in got.values():
        assert entries == sorted(entries, key=_key)


def test_usage_is_empty_for_an_unknown_id(client):
    got = _usage(client, "px1-" + "0" * 64)
    assert got == {b: [] for b in BUCKETS}


@pytest.mark.parametrize("bad", ["nope", "px1-xyz", "px1-", "PX1-" + "0" * 64])
def test_usage_refuses_a_malformed_id(client, bad):
    assert client.get(f"/api/images/{bad}/usage").status_code == 400


def test_usage_ignores_image_less_placements(client, surface_ids):  # noqa: F811
    ids = surface_ids
    where = f"/api/worlds/{ids['wid']}/characters/{ids['char']}/versions/default/images"
    image_id = _put(client, f"{where}/gallery_1", ids["png"]).json()["image_id"]
    d = assets.version_dir(ids["wroot"], ids["char"], "default")
    image_refs.write(d, "gallery_2", None, focus=40)        # an occurrence override
    got = _usage(client, image_id)
    assert got["characters"] == [{"scope": f"world:{ids['wid']}", "id": ids["char"],
                                  "vid": "default", "name": "gallery_1"}]
    assert sum(len(v) for v in got.values()) == 1


def test_usage_lists_a_format1_collection_member(client, surface_ids):  # noqa: F811
    ids = surface_ids
    other = "fedcba9876543210fedcba9876543210"
    [name] = format1(ids["wid"], COLLECTION, ids["png"])
    format1(ids["wid"], other, ids["png"])
    # A manifest nobody can read is skipped, not a 500.
    (image_collections.directory(ids["wid"]) / ("a" * 32 + ".json")).write_text(
        "{", encoding="utf-8")
    placed = assets.resolve(image_collections.image_directory(ids["wid"]), name)
    assert placed is not None
    got = _usage(client, placed.image_id)
    assert got["collections"] == sorted(
        [{"wid": ids["wid"], "collection": c} for c in (COLLECTION, other)], key=_key)
    assert got["world_images"] == [{"wid": ids["wid"], "name": name}]


def test_usage_spans_worlds_and_campaigns(client, surface_ids):  # noqa: F811
    ids = surface_ids
    other = client.post("/api/worlds", json={"name": "Winifred"}).json()["id"]
    png = ids["png"]
    for wid in (ids["wid"], other):
        assert _put(client, f"/api/worlds/{wid}/images/harbour", png).status_code == 200
    assert _put(client, f"/api/campaigns/{ids['cid']}/images/quay", png).status_code == 200
    got = _usage(client, image_store.ingest(png, "png").id)
    assert got["world_images"] == sorted(
        [{"wid": ids["wid"], "name": "harbour"}, {"wid": other, "name": "harbour"}], key=_key)
    assert got["campaign_images"] == [{"cid": ids["cid"], "name": "quay"}]


def test_usage_skips_a_directory_that_matches_no_builder(client, surface_ids):  # noqa: F811
    ids = surface_ids
    image_id = image_store.ingest(_png(3), "png").id
    image_refs.write(ids["wroot"] / "notes" / "scratch", "gallery_1", image_id)
    assert _usage(client, image_id) == {b: [] for b in BUCKETS}


def test_usage_stamps_no_campaign_revision(client, surface_ids):  # noqa: F811
    ids = surface_ids
    before = revision.current(ids["cid"])
    _usage(client, "px1-" + "1" * 64)
    assert revision.current(ids["cid"]) == before

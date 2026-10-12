"""One Art read contract across all seven records and both scopes."""

import pytest

from grimoire.store import assets, overlay
from tests.record_art_fixtures import KINDS, art_url, image_url, png, setup_records, upload


@pytest.fixture
def records(client):
    return setup_records(client)


@pytest.mark.parametrize("scope", ("world", "campaign"))
@pytest.mark.parametrize("kind", KINDS)
def test_record_art_read_matrix(client, records, scope, kind):
    for name in ("gallery_10", "map10", "map2", "map", "gallery_2", "avatar"):
        upload(client, records, "world", kind, name)
    if scope == "campaign":
        upload(client, records, "campaign", kind, "gallery_2", 1)
    response = client.get(art_url(records, scope, kind))
    assert response.status_code == 200, response.text
    body = response.json()
    target = {"scope": {"kind": scope, "id": records[scope]},
              "kind": kind, "record_id": records["records"][kind]}
    if kind in ("characters", "pcs"):
        target["version"] = "default"
    assert body["target"] == target
    assert {"append", "reuse", "promote", "focus", "description", "remove"} <= set(body["operations"])
    assert [i["name"] for i in body["images"]] == ["avatar", "gallery_2", "gallery_10", "map", "map2", "map10"]
    for row in body["images"]:
        assert row["v"] and row["image_id"]
        assert row["url"].startswith(image_url(records, scope, kind, row["name"]) + "?v=")
        assert "w=" in row["thumb"]
        assert row["placement"] == {"surface": "record", "target": target,
                                     "name": row["name"], "expected_v": row["v"]}
        expected = "world" if scope == "world" else (
            "campaign" if row["name"] == "gallery_2" else "inherited")
        assert row["provenance"] == expected
        assert row["width"] == 320 and row["height"] == 200


@pytest.mark.parametrize("scope", ("world", "campaign"))
def test_record_art_rejects_greetings_and_bad_versions(client, records, scope):
    prefix = f"/api/{scope}s/{records[scope]}/record-art"
    for kind in ("greetings", "unknown"):
        assert client.get(f"{prefix}/{kind}/saltmarch").status_code == 404
    for kind in KINDS:
        rid = records["records"][kind]
        assert client.get(f"{prefix}/{kind}/missing?version=default").status_code == 404
        if kind in ("characters", "pcs"):
            assert client.get(f"{prefix}/{kind}/{rid}").status_code == 404
            assert client.get(f"{prefix}/{kind}/{rid}?version=missing").status_code == 404
        else:
            assert client.get(f"{prefix}/{kind}/{rid}?version=default").status_code == 404


@pytest.mark.parametrize("kind", KINDS)
def test_world_original_is_not_campaign_override(client, records, kind):
    world = upload(client, records, "world", kind, "avatar", 0)
    own = upload(client, records, "campaign", kind, "avatar", 1)
    response = client.get(art_url(records, "campaign", kind))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["images"][0]["image_id"] == own["image_id"]
    [original] = body["world_originals"]
    assert original["image_id"] == world["image_id"]
    assert original["provenance"] == "world-original"
    assert original["url"].startswith(image_url(records, "world", kind, "avatar"))
    assert original["placement"]["target"]["scope"]["kind"] == "world"
    # The preserved world picture already held in another campaign slot is
    # one visible picture, not a duplicate in the originals disclosure.
    upload(client, records, "campaign", kind, "gallery_1", 0)
    assert client.get(art_url(records, "campaign", kind)).json()["world_originals"] == []


@pytest.mark.parametrize("kind", KINDS)
def test_detached_and_tombstoned_art_are_absent(client, records, kind):
    upload(client, records, "world", kind, "avatar")
    response = client.delete(image_url(records, "campaign", kind, "avatar"))
    assert response.status_code == 200, response.text
    body = client.get(art_url(records, "campaign", kind)).json()
    assert body["images"] == [] and body["world_originals"] == []
    overlay.add_detached(records["campaign"], f"{kind}/{records['records'][kind]}")
    body = client.get(art_url(records, "campaign", kind)).json()
    assert body["images"] == [] and body["world_originals"] == []


def test_optional_related_failure_does_not_erase_effective_art(client, records, monkeypatch):
    upload(client, records, "world", "characters", "avatar")
    upload(client, records, "campaign", "characters", "avatar", 1)

    def unavailable(*args, **kwargs):
        raise OSError("world originals unavailable")

    monkeypatch.setattr(overlay, "shadowed_images", unavailable)
    response = client.get(art_url(records, "campaign", "characters"))
    assert response.status_code == 200, response.text
    assert len(response.json()["images"]) == 1
    assert response.json()["world_originals_error"]


@pytest.mark.parametrize("scope", ("world", "campaign"))
def test_gallery_carries_scoped_placements(client, records, scope):
    for kind in KINDS:
        upload(client, records, "world", kind, "avatar")
    upload(client, records, "campaign", "characters", "avatar", 1)
    rows = client.get(f"/api/{scope}s/{records[scope]}/gallery").json()
    assert len(rows) == len(KINDS)
    for row in rows:
        assert row["placement"]["target"]["scope"] == {"kind": scope, "id": records[scope]}
        assert row["placement"]["expected_v"] == row["v"]
        assert row["placement"]["target"]["kind"] == row["kind"]
        assert row["provenance"] == ("world" if scope == "world" else
                                     "campaign" if row["kind"] == "characters" else "inherited")


@pytest.mark.parametrize("scope", ("world", "campaign"))
def test_greeting_copy_becomes_normal_character_placement(client, records, scope):
    wid, char = records["world"], records["records"]["characters"]
    gid = client.post(f"/api/worlds/{wid}/greetings",
                      json={"name": "Saltmarch", "character": char, "version": "default"}).json()["id"]
    assets.put_image(records["wroot"], gid, "default", "embed-abc123def456",
                     png(), "png", base="greetings")
    source = f"/api/worlds/{wid}/greetings/{gid}/images/embed-abc123def456"
    assert client.put(source + "/subjects", json={"subjects": [char]}).status_code == 200
    copied = client.post(image_url(records, scope, "characters") + "/copy-from-greeting",
                         json={"gid": gid, "name": "embed-abc123def456", "slot": "gallery"})
    assert copied.status_code == 200, copied.text
    data = client.get(art_url(records, scope, "characters")).json()
    [image] = data["images"]
    assert image["name"] == "gallery_1"
    assert image["placement"]["target"] == data["target"]
    assert image["placement"]["expected_v"] == image["v"]
    assert client.delete(image_url(records, scope, "characters", image["name"]),
                         params={"expected_v": image["v"]}).status_code == 200
    assert client.get(source).content == png()
    assert client.get(source + "/subjects").json() == {"subjects": [char]}


@pytest.mark.parametrize("kind", KINDS)
def test_disappearing_world_original_keeps_effective_and_surviving_art(client, records, monkeypatch, kind):
    upload(client, records, "world", kind, "avatar")
    upload(client, records, "world", kind, "gallery_1", 1)
    own = upload(client, records, "campaign", kind, "avatar", 2)
    gallery = upload(client, records, "campaign", kind, "gallery_1", 3)
    real_shadowed = overlay.shadowed_images

    def removed_between_reads(*args, **kwargs):
        rows = real_shadowed(*args, **kwargs)
        assets.delete_image(records["wroot"], records["records"][kind], "default", "gallery_1", kind)
        return rows

    monkeypatch.setattr(overlay, "shadowed_images", removed_between_reads)
    response = client.get(art_url(records, "campaign", kind))
    assert response.status_code == 200, response.text
    body = response.json()
    assert {row["image_id"] for row in body["images"]} == {own["image_id"], gallery["image_id"]}
    assert [row["name"] for row in body["world_originals"]] == ["avatar"]
    assert body["world_originals_error"]

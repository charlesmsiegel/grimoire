"""Reuse is a validated visible placement, never arbitrary object-id access."""

import threading

import pytest

from grimoire.store import (
    assets,
    campaign_images,
    characters,
    entities,
    image_descriptions,
    image_refs,
    image_store,
    locks,
    overlay,
    pcs,
    world_images,
)
from grimoire.store.record_art import write
from tests.record_art_fixtures import KINDS, art_url, image_url, png, setup_records, upload


@pytest.fixture
def records(client):
    return setup_records(client)


def _url(records, scope, kind):
    path, sep, query = art_url(records, scope, kind).partition("?")
    return path + "/images/link" + (sep + query if sep else "")


def _source(client, records, scope, kind):
    return client.get(art_url(records, scope, kind)).json()["images"][0]["placement"]


@pytest.mark.parametrize("scope", ("world", "campaign"))
@pytest.mark.parametrize("kind", KINDS)
def test_reuse_cross_kind_matrix_and_library_sources(client, records, scope, kind):
    for index, source_kind in enumerate(KINDS):
        upload(client, records, "world", source_kind, "avatar", index + 1)
    primary = client.get(art_url(records, scope, kind)).json()["images"][0]
    for source_kind in KINDS:
        source = _source(client, records, scope, source_kind)
        source_row = client.get(art_url(records, scope, source_kind)).json()["images"][0]
        blobs = set(image_store.store_root().glob("blobs/*/*"))
        linked = client.post(_url(records, scope, kind), json={"source": source, "intent": "gallery"})
        assert linked.status_code == 200, linked.text
        assert linked.json()["created"]["image_id"] == source_row["image_id"]
        assert linked.json()["created"]["name"].startswith("gallery_")
        assert set(image_store.store_root().glob("blobs/*/*")) == blobs
    world_images.put_image(records["world"], "Saltmarch", png(20), "png")
    libraries = [("world", records["world"], world_images.list_images(records["world"])[0])]
    if scope == "campaign":
        campaign_images.put_image(records["campaign"], "Realm", png(21), "png")
        own = next(i for i in campaign_images.list_images(records["campaign"]) if not i["inherited"])
        libraries.append(("campaign", records["campaign"], own))
    for library_scope, sid, row in libraries:
        source = {"surface": "library", "scope": {"kind": library_scope, "id": sid},
                  "name": row["name"], "expected_v": row["v"]}
        response = client.post(_url(records, scope, kind), json={"source": source, "intent": "gallery"})
        assert response.status_code == 200, response.text
        assert response.json()["created"]["image_id"] == row["image_id"]
    rows = client.get(art_url(records, scope, kind)).json()["images"]
    assert rows[0]["image_id"] == primary["image_id"]
    assert all(row.get("focus") in (None, 50) for row in rows)


@pytest.mark.parametrize("scope", ("world", "campaign"))
def test_primary_reuse_preserves_old_primary_and_unchanged_primary_is_no_op(client, records, scope):
    upload(client, records, "world", "characters", "avatar", 1)
    source_image = upload(client, records, "world", "locations", "avatar", 2)
    primary = client.get(art_url(records, scope, "characters")).json()["images"][0]
    source = _source(client, records, scope, "locations")
    url = _url(records, scope, "characters")
    assert client.post(url, json={"source": source, "intent": "primary"}).status_code == 400
    assert client.post(url, json={"source": source, "intent": "primary",
                                 "expected_primary_v": None}).status_code == 409
    response = client.post(url, json={"source": source, "intent": "primary",
                                     "expected_primary_v": primary["v"]})
    assert response.status_code == 200, response.text
    [avatar, gallery] = client.get(art_url(records, scope, "characters")).json()["images"]
    assert avatar["image_id"] == source_image["image_id"]
    assert gallery["image_id"] == primary["image_id"]
    again = client.post(url, json={"source": source, "intent": "primary",
                                  "expected_primary_v": avatar["v"]}).json()
    assert again["status"] == "ok" and again["no_op"] is True
    assert len(client.get(art_url(records, scope, "characters")).json()["images"]) == 2


@pytest.mark.parametrize("failure", ("greeting", "foreign_world", "foreign_campaign", "stale",
                                     "hidden", "world_original", "unarrived", "corrupt", "missing_version",
                                     "detached", "deleted_record"))
def test_reuse_rejects_greeting_hidden_foreign_changed_and_unarrived_sources(client, records, failure):
    upload(client, records, "world", "characters", "avatar")
    source = _source(client, records, "campaign", "characters")
    target = source["target"]
    if failure in ("greeting", "foreign_world", "missing_version"):
        target.update({"greeting": {"kind": "greetings"},
                       "foreign_world": {"scope": {"kind": "world", "id": "Realm"}},
                       "missing_version": {"version": "absent"}}[failure])
    elif failure == "foreign_campaign":
        cid = client.post("/api/campaigns", json={"name": "Realm", "world": records["world"]}).json()["id"]
        target["scope"]["id"] = cid
    elif failure == "stale":
        upload(client, records, "world", "characters", "avatar", 2)
    elif failure == "hidden":
        overlay.delete_image(records["campaign"], records["records"]["characters"], "default", "avatar")
    elif failure == "world_original":
        upload(client, records, "campaign", "characters", "avatar", 2)
        source = _source(client, records, "world", "characters")
    elif failure == "detached":
        overlay.add_detached(records["campaign"], f'characters/{records["records"]["characters"]}')
    elif failure == "deleted_record":
        url = f'/api/campaigns/{records["campaign"]}/characters/{records["records"]["characters"]}'
        assert client.delete(url).status_code == 200
    else:
        d = records["wroot"] / "characters" / records["records"]["characters"] / "assets" / "default"
        ref = image_refs.read(d, "avatar")
        obj = image_store.read(ref.image)
        if failure == "unarrived":
            image_store.blob_path(obj.blob_sha256, obj.ext).unlink()
        else:
            image_store.blob_path(obj.blob_sha256, obj.ext).write_bytes(b"corrupt")
    before = client.get(art_url(records, "campaign", "locations")).json()["images"]
    response = client.post(_url(records, "campaign", "locations"),
                           json={"source": source, "intent": "gallery"})
    assert response.status_code in (400, 404, 409), response.text
    assert client.get(art_url(records, "campaign", "locations")).json()["images"] == before


def test_reuse_primary_partial_result_is_retryable_without_second_append(client, records, monkeypatch):
    upload(client, records, "world", "characters", "avatar", 1)
    upload(client, records, "world", "locations", "avatar", 2)
    source = _source(client, records, "world", "locations")
    primary = client.get(art_url(records, "world", "characters")).json()["images"][0]
    promote = assets.promote_image

    def fail(*args, **kwargs):
        raise OSError("promotion unavailable")

    monkeypatch.setattr(assets, "promote_image", fail)
    response = client.post(_url(records, "world", "characters"),
                           json={"source": source, "intent": "primary", "expected_primary_v": primary["v"]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "partial" and result["created"]["name"] == "gallery_1"
    monkeypatch.setattr(assets, "promote_image", promote)
    url = image_url(records, "world", "characters", result["created"]["name"]) + "/promote"
    assert client.post(url, json={"expected_v": result["created"]["v"],
                                 "expected_primary_v": primary["v"]}).status_code == 200
    assert len(client.get(art_url(records, "world", "characters")).json()["images"]) == 2


@pytest.mark.parametrize("caption", ("A portrait", ""))
@pytest.mark.parametrize("intent", ("gallery", "primary"))
def test_reuse_preserves_legacy_source_file_caption_and_centers_destination(client, records, caption, intent):
    d = records["wroot"] / "locations" / records["records"]["locations"] / "assets" / "default"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "avatar.png"
    path.write_bytes(png(2))
    image_descriptions.carry_legacy(d, "avatar", caption)
    assets.write_focus(records["wroot"], records["records"]["locations"], "default", 90, "locations")
    source = _source(client, records, "world", "locations")
    response = client.post(_url(records, "world", "characters"), json={"source": source, "intent": intent, "expected_primary_v": None})
    assert response.status_code == 200, response.text
    assert path.read_bytes() == png(2) and image_refs.read(d, "avatar") is None
    [destination] = client.get(art_url(records, "world", "characters")).json()["images"]
    assert destination["description"] == caption and destination["described"]
    assert destination["name"] == ("avatar" if intent == "primary" else "gallery_1")
    assert destination.get("focus") is None
    assert image_descriptions.read_raw(d)["avatar"] == caption


def test_reuse_source_change_during_legacy_ingest_is_refused(client, records, monkeypatch):
    d = records["wroot"] / "locations" / records["records"]["locations"] / "assets" / "default"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "avatar.png"
    path.write_bytes(png(1))
    source = _source(client, records, "world", "locations")
    ingest = image_store.ingest

    def changed_source(*args, **kwargs):
        path.write_bytes(png(2))
        return ingest(*args, **kwargs)

    monkeypatch.setattr(image_store, "ingest", changed_source)
    response = client.post(_url(records, "world", "characters"), json={"source": source, "intent": "gallery"})
    assert response.status_code == 409, response.text
    assert client.get(art_url(records, "world", "characters")).json()["images"] == []
    assert path.read_bytes() == png(2)


def test_reuse_gc_race_publishes_no_dangling_ref(client, records, monkeypatch):
    upload(client, records, "world", "locations", "avatar")
    source = _source(client, records, "world", "locations")
    publish = image_refs.write
    attempted = []

    def probe(*args, **kwargs):
        def collector():
            lock = locks.image_ingest_gc_lock()
            acquired = lock.acquire(blocking=False)
            attempted.append(acquired)
            if acquired:
                lock.release()

        thread = threading.Thread(target=collector)
        thread.start()
        thread.join(10)
        assert not thread.is_alive()
        return publish(*args, **kwargs)

    monkeypatch.setattr(image_refs, "write", probe)
    response = client.post(_url(records, "world", "characters"), json={"source": source, "intent": "gallery"})
    assert response.status_code == 200, response.text
    assert attempted == [False]
    obj = image_store.read_fresh(response.json()["created"]["image_id"])
    assert image_store.blob_intact(image_store.blob_path(obj.blob_sha256, obj.ext))


def test_reuse_reestablishes_prepared_legacy_object_collected_before_claim(client, records, monkeypatch):
    d = records["wroot"] / "locations" / records["records"]["locations"] / "assets" / "default"
    d.mkdir(parents=True, exist_ok=True)
    (d / "avatar.png").write_bytes(png(1))
    source = _source(client, records, "world", "locations")
    prepare = write._prepare_source

    def collected(*args):
        result = prepare(*args)
        obj = image_store.read_fresh(result.image_id)
        image_store.blob_path(obj.blob_sha256, obj.ext).unlink()
        image_store.object_path(obj.id).unlink()
        return result

    monkeypatch.setattr(write, "_prepare_source", collected)
    response = client.post(_url(records, "world", "characters"), json={"source": source, "intent": "gallery"})
    assert response.status_code == 200, response.text
    obj = image_store.read_fresh(response.json()["created"]["image_id"])
    assert obj is not None and image_store.blob_intact(image_store.blob_path(obj.blob_sha256, obj.ext))


@pytest.mark.parametrize("action", ("hidden", "shadowed"))
def test_campaign_reuse_cannot_bypass_world_library_visibility(client, records, action):
    world_images.put_image(records["world"], "Realm", png(1), "png")
    [row] = world_images.list_images(records["world"])
    source = {"surface": "library", "scope": {"kind": "world", "id": records["world"]},
              "name": "Realm", "expected_v": row["v"]}
    if action == "hidden":
        campaign_images.delete_image(records["campaign"], "Realm")
    else:
        # Ordinary uploads refuse world-name collisions. A sync or a legacy
        # store can still hold both, and source validation must honor which
        # image the campaign actually displays in that state.
        with locks.campaign_lock(records["campaign"]):
            assets.put_in(campaign_images.images_dir(records["campaign"]), "Realm", png(2), "png")
    response = client.post(_url(records, "campaign", "characters"), json={"source": source, "intent": "gallery"})
    assert response.status_code == 404, response.text
    assert client.get(art_url(records, "campaign", "characters")).json()["images"] == []


@pytest.mark.parametrize("scope", ("world", "campaign"))
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("gallery_only", (False, True))
def test_gallery_intent_reuse_never_claims_a_missing_primary(client, records, scope, kind, gallery_only):
    if gallery_only:
        upload(client, records, scope, kind, "gallery_5")
    world_images.put_image(records["world"], "Realm", png(20), "png")
    row = world_images.list_images(records["world"])[0]
    source = {"surface": "library", "scope": {"kind": "world", "id": records["world"]},
              "name": row["name"], "expected_v": row["v"]}
    response = client.post(_url(records, scope, kind), json={"source": source, "intent": "gallery"})
    assert response.status_code == 200, response.text
    assert response.json()["created"]["name"] == ("gallery_6" if gallery_only else "gallery_1")
    rows = client.get(art_url(records, scope, kind)).json()["images"]
    assert not any(image["name"] == "avatar" for image in rows)


@pytest.mark.parametrize("scope", ("world", "campaign"))
@pytest.mark.parametrize("kind,version_only", [
    *[(kind, False) for kind in KINDS],
    ("characters", True), ("pcs", True),
])
def test_reuse_rechecks_destination_after_legacy_object_restoration(client, records, monkeypatch, scope, kind, version_only):
    source_kind = "characters" if kind == "locations" else "locations"
    d = assets.version_dir(records["wroot"], records["records"][source_kind], "default", source_kind)
    d.mkdir(parents=True, exist_ok=True)
    (d / "avatar.png").write_bytes(png(2))
    source = _source(client, records, scope, source_kind)
    module = characters if kind == "characters" else pcs
    if version_only:
        original = module.require_version(records["wroot"], records["records"][kind], "default")
        original.with_name("veiled" + original.suffix).write_bytes(original.read_bytes())
    prepare, ingest = write._prepare_source, image_store.ingest

    def collected(*args):
        result = prepare(*args)
        obj = image_store.read_fresh(result.image_id)
        image_store.blob_path(obj.blob_sha256, obj.ext).unlink()
        image_store.object_path(obj.id).unlink()
        return result

    ingestions = []

    def restore_after_deletion(data, ext):
        obj = ingest(data, ext)
        ingestions.append(obj.id)
        if len(ingestions) == 2:
            root, rid = records["wroot"], records["records"][kind]
            if version_only:
                module.delete_version(root, rid, "default")
            elif kind == "characters":
                characters.delete_character(root, rid)
            elif kind == "pcs":
                pcs.delete_pc(root, rid)
            else:
                entities.delete_entity(root, kind, rid)
        return obj

    monkeypatch.setattr(write, "_prepare_source", collected)
    monkeypatch.setattr(image_store, "ingest", restore_after_deletion)
    response = client.post(_url(records, scope, kind), json={"source": source, "intent": "gallery"})
    assert len(ingestions) == 2
    assert response.status_code == 404, response.text
    root = records["wroot" if scope == "world" else "croot"]
    assert not assets.version_dir(root, records["records"][kind], "default", kind).exists()


@pytest.mark.parametrize("scope", ("world", "campaign"))
@pytest.mark.parametrize("kind,version_only", [
    *[(kind, False) for kind in KINDS],
    ("characters", True), ("pcs", True),
])
def test_reuse_rechecks_source_after_legacy_restoration(client, records, monkeypatch, scope, kind, version_only):
    destination_kind = "characters" if kind == "locations" else "locations"
    d = assets.version_dir(records["wroot"], records["records"][kind], "default", kind)
    d.mkdir(parents=True, exist_ok=True)
    (d / "avatar.png").write_bytes(png(2))
    source = _source(client, records, scope, kind)
    module = characters if kind == "characters" else pcs
    if version_only:
        original = module.require_version(records["wroot"], records["records"][kind], "default")
        original.with_name("veiled" + original.suffix).write_bytes(original.read_bytes())
    prepare, ingest = write._prepare_source, image_store.ingest

    def collected(*args):
        result = prepare(*args)
        obj = image_store.read_fresh(result.image_id)
        image_store.blob_path(obj.blob_sha256, obj.ext).unlink()
        image_store.object_path(obj.id).unlink()
        return result

    ingestions = []

    def restore_after_source_deletion(data, ext):
        obj = ingest(data, ext)
        ingestions.append(obj.id)
        if len(ingestions) == 2:
            root, rid = records["wroot"], records["records"][kind]
            if version_only:
                module.delete_version(root, rid, "default")
            elif kind == "characters":
                characters.delete_character(root, rid)
            elif kind == "pcs":
                pcs.delete_pc(root, rid)
            else:
                entities.delete_entity(root, kind, rid)
        return obj

    monkeypatch.setattr(write, "_prepare_source", collected)
    monkeypatch.setattr(image_store, "ingest", restore_after_source_deletion)
    response = client.post(_url(records, scope, destination_kind), json={"source": source, "intent": "gallery"})
    assert len(ingestions) == 2
    assert response.status_code == 404, response.text
    assert client.get(art_url(records, scope, destination_kind)).json()["images"] == []


@pytest.mark.parametrize("scope,change", [("world", "replace"), ("campaign", "replace"), ("campaign", "hide")])
def test_reuse_rechecks_source_token_and_visibility_after_restoration(client, records, monkeypatch, scope, change):
    d = assets.version_dir(records["wroot"], records["records"]["locations"], "default", "locations")
    d.mkdir(parents=True, exist_ok=True)
    (d / "avatar.png").write_bytes(png(2))
    source = _source(client, records, scope, "locations")
    prepare, ingest = write._prepare_source, image_store.ingest

    def collected(*args):
        result = prepare(*args)
        obj = image_store.read_fresh(result.image_id)
        image_store.blob_path(obj.blob_sha256, obj.ext).unlink()
        image_store.object_path(obj.id).unlink()
        return result

    ingestions = []

    def restore_after_source_change(data, ext):
        obj = ingest(data, ext)
        ingestions.append(obj.id)
        if len(ingestions) == 2:
            if change == "replace":
                (d / "avatar.png").write_bytes(png(3))
            else:
                overlay.delete_image(records["campaign"], records["records"]["locations"], "default", "avatar", "locations")
        return obj

    monkeypatch.setattr(write, "_prepare_source", collected)
    monkeypatch.setattr(image_store, "ingest", restore_after_source_change)
    response = client.post(_url(records, scope, "characters"), json={"source": source, "intent": "gallery"})
    assert len(ingestions) == 2
    assert response.status_code == (409 if change == "replace" else 404), response.text
    assert client.get(art_url(records, scope, "characters")).json()["images"] == []


@pytest.mark.parametrize("source_surface", ("record", "library"))
@pytest.mark.parametrize("intent", ("gallery", "primary"))
@pytest.mark.parametrize("repair_at", ("validation", "publication"))
def test_reuse_reallocates_after_recovery_fills_its_candidate(
        client, records, monkeypatch, source_surface, intent, repair_at):
    upload(client, records, "world", "characters", "avatar", 1)
    primary = client.get(art_url(records, "world", "characters")).json()["images"][0]
    if source_surface == "record":
        source_image = upload(client, records, "world", "locations", "avatar", 2)
        source = _source(client, records, "world", "locations")
    else:
        world_images.put_image(records["world"], "Saltmarch", png(2), "png")
        source_image = world_images.list_images(records["world"])[0]
        source = {"surface": "library", "scope": {"kind": "world", "id": records["world"]},
                  "name": source_image["name"], "expected_v": source_image["v"]}
    d = assets.version_dir(records["wroot"], records["records"]["characters"], "default")
    stranded = png(3)
    if repair_at == "validation":
        (d / "promote-tmp.png").write_bytes(stranded)
    else:
        check = write._check_images
        introduced = False

        def after_validation(*args):
            nonlocal introduced
            check(*args)
            if not introduced:
                introduced = True
                (d / "promote-tmp.png").write_bytes(stranded)

        # Publication reads the destination again. Even recovery deferred until
        # that final scan must not replace the newly restored gallery picture.
        monkeypatch.setattr(write, "_check_images", after_validation)
    body = {"source": source, "intent": intent}
    if intent == "primary":
        body["expected_primary_v"] = primary["v"]
    response = client.post(_url(records, "world", "characters"), json=body)
    assert response.status_code == 200, response.text
    assert response.json()["created"]["name"] == ("avatar" if intent == "primary" else "gallery_2")
    rows = client.get(art_url(records, "world", "characters")).json()["images"]
    assert [r["name"] for r in rows] == ["avatar", "gallery_1", "gallery_2"]
    assert (d / "gallery_1.png").read_bytes() == stranded
    assert {r["image_id"] for r in rows} >= {primary["image_id"], source_image["image_id"]}
    assert not (d / "promote-tmp.png").exists()

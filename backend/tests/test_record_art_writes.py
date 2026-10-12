"""Append allocates against the effective occupied slots, never overwrites."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from grimoire.store import (
    assets,
    characters,
    entities,
    image_descriptions,
    image_refs,
    image_store,
    locks,
    pcs,
    record_art,
)
from grimoire.store.record_art import write
from tests.record_art_fixtures import KINDS, art_url, png, setup_records, upload


@pytest.fixture
def records(client):
    return setup_records(client)


def append(client, records, scope, kind, seed=2):
    url, _, query = art_url(records, scope, kind).partition("?")
    return client.post(url + "/images" + ("?" + query if query else ""),
                       files={"file": ("upload.jpg", png(seed), "image/jpeg")})


@pytest.mark.parametrize("scope", ("world", "campaign"))
@pytest.mark.parametrize("kind", KINDS)
def test_append_matrix_preserves_existing_primary(client, records, scope, kind):
    primary = upload(client, records, "world", kind, "avatar")
    upload(client, records, "world", kind, "gallery_2")
    upload(client, records, scope, kind, "gallery_9", 1)
    response = append(client, records, scope, kind)
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["name"] == "gallery_10" and created["ext"] == "png"
    assert created["v"] and created["image_id"]
    rows = client.get(art_url(records, scope, kind)).json()["images"]
    assert next(i for i in rows if i["name"] == "avatar")["image_id"] == primary["image_id"]
    assert next(i for i in rows if i["name"] == "gallery_10")["image_id"] == created["image_id"]


@pytest.mark.parametrize("scope", ("world", "campaign"))
@pytest.mark.parametrize("kind", KINDS)
def test_append_first_success_uses_empty_primary(client, records, scope, kind):
    response = append(client, records, scope, kind)
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "avatar"
    assert append(client, records, scope, kind, 3).json()["name"] == "gallery_1"


@pytest.mark.parametrize("scope", ("world", "campaign"))
def test_unresolved_local_slots_are_occupied(client, records, scope):
    root = records["wroot" if scope == "world" else "croot"]
    d = assets.version_dir(root, records["records"]["characters"], "default")
    missing = "px1-" + "a" * 64
    assets.link_in(d, "avatar", missing)
    assets.link_in(d, "gallery_9", missing)
    response = append(client, records, scope, "characters")
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "gallery_10"


def test_invalid_append_writes_nothing(client, records):
    url = art_url(records, "world", "characters").replace("?version", "/images?version")
    response = client.post(url, files={"file": ("image.png", b"not an image", "image/png")})
    assert response.status_code == 400
    assert client.get(art_url(records, "world", "characters")).json()["images"] == []
    response = client.post(url.replace(records["records"]["characters"], "missing"),
                           files={"file": ("image.png", png(), "image/png")})
    assert response.status_code == 404


def test_two_appends_get_distinct_slots(client, records, monkeypatch):
    upload(client, records, "world", "characters", "avatar")
    barrier = threading.Barrier(2)
    local = threading.local()
    candidate = write._candidate

    def together(target):
        name = candidate(target)
        if not getattr(local, "started", False):
            local.started = True
            barrier.wait(10)
        return name

    monkeypatch.setattr(write, "_candidate", together)
    with ThreadPoolExecutor(2) as pool:
        results = [pool.submit(record_art.append_world, records["world"], "characters",
                               records["records"]["characters"], "default", png(seed), "png")
                   for seed in (1, 2)]
        created = [future.result(15) for future in results]
    assert {row["name"] for row in created} == {"gallery_1", "gallery_2"}
    assert len({row["image_id"] for row in created}) == 2


def test_world_append_racing_campaign_candidate_does_not_get_shadowed(client, records, monkeypatch):
    upload(client, records, "world", "characters", "avatar")
    chosen, release = threading.Event(), threading.Event()
    candidate = write._candidate
    local = threading.local()

    def pause_campaign(target):
        name = candidate(target)
        if target.scope == "campaign" and not getattr(local, "started", False):
            local.started = True
            chosen.set()
            assert release.wait(10)
        return name

    monkeypatch.setattr(write, "_candidate", pause_campaign)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(record_art.append_campaign, records["campaign"], "characters",
                              records["records"]["characters"], "default", png(2), "png")
        try:
            assert chosen.wait(10)
            world = record_art.append_world(records["world"], "characters",
                                             records["records"]["characters"], "default", png(1), "png")
        finally:
            release.set()
        own = pending.result(15)
    assert world["name"] == "gallery_1" and own["name"] == "gallery_2"
    images = client.get(art_url(records, "campaign", "characters")).json()["images"]
    assert next(i for i in images if i["name"] == "gallery_1")["image_id"] == world["image_id"]


def test_gc_cannot_collect_between_append_validation_and_reference(client, records, monkeypatch):
    publish = image_refs.write
    attempted = []

    def probe_at_publication(*args, **kwargs):
        # Another thread is essential: ingest exclusion is reentrant for the
        # writer. Nonblocking acquisition proves exclusion without a sleep.
        def collector_probe():
            lock = locks.image_ingest_gc_lock()
            acquired = lock.acquire(blocking=False)
            attempted.append(acquired)
            if acquired:
                lock.release()

        thread = threading.Thread(target=collector_probe)
        thread.start()
        thread.join(10)
        assert not thread.is_alive()
        return publish(*args, **kwargs)

    monkeypatch.setattr(image_refs, "write", probe_at_publication)
    created = record_art.append_world(records["world"], "characters",
                                      records["records"]["characters"], "default", png(1), "png")
    assert attempted == [False]
    obj = image_store.read_fresh(created["image_id"])
    assert obj is not None
    assert image_store.blob_intact(image_store.blob_path(obj.blob_sha256, obj.ext))


def test_append_reestablishes_prepared_object_collected_before_claim(client, records, monkeypatch):
    candidate = write._candidate
    removed = []

    def collect_prepared(target):
        if not removed:
            # The prepared object is still unplaced before the final claim.
            # Simulate a valid collector having removed it in that interval.
            for sidecar in image_store.store_root().glob("objects/*/*.json"):
                obj = image_store.read_fresh(sidecar.stem)
                if obj is not None:
                    image_store.blob_path(obj.blob_sha256, obj.ext).unlink()
                    sidecar.unlink()
                    removed.append(obj.id)
        return candidate(target)

    monkeypatch.setattr(write, "_candidate", collect_prepared)
    created = record_art.append_world(records["world"], "characters",
                                      records["records"]["characters"], "default", png(1), "png")
    assert removed == [created["image_id"]]
    assert client.get(art_url(records, "world", "characters")).json()["images"][0]["image_id"] == created["image_id"]


def test_new_append_does_not_inherit_a_caption_from_an_empty_slot(client, records):
    d = records["wroot"] / "characters" / records["records"]["characters"] / "assets" / "default"
    image_descriptions.carry_legacy(d, "avatar", "A portrait")
    image_descriptions.carry_legacy(d, "gallery_99", "A map")
    record_art.append_world(records["world"], "characters", records["records"]["characters"],
                            "default", png(1), "png")
    [row] = client.get(art_url(records, "world", "characters")).json()["images"]
    assert row["description"] == "" and not row["described"]
    assert image_descriptions.read_raw(d)["gallery_99"] == "A map"


@pytest.mark.parametrize("scope", ("world", "campaign"))
@pytest.mark.parametrize("kind", KINDS)
def test_append_rechecks_world_record_deleted_during_ingestion(client, records, monkeypatch, scope, kind):
    ingest = image_store.ingest
    deleted = []

    def remove_destination(data, ext):
        obj = ingest(data, ext)
        if not deleted:
            # A world record can disappear while its campaign upload holds the
            # campaign lock. Exercise the underlying deletion in this worker;
            # a nested HTTP request tests lock timeouts instead of publication.
            root, rid = records["wroot"], records["records"][kind]
            if kind == "characters":
                characters.delete_character(root, rid)
            elif kind == "pcs":
                pcs.delete_pc(root, rid)
            else:
                entities.delete_entity(root, kind, rid)
            deleted.append(rid)
        return obj

    monkeypatch.setattr(image_store, "ingest", remove_destination)
    response = append(client, records, scope, kind)
    assert response.status_code == 404, response.text
    root = records["wroot" if scope == "world" else "croot"]
    d = assets.version_dir(root, records["records"][kind], "default", kind)
    assert not d.exists()


@pytest.mark.parametrize("scope", ("world", "campaign"))
@pytest.mark.parametrize("kind", ("characters", "pcs"))
def test_append_rechecks_actor_version_deleted_during_ingestion(client, records, monkeypatch, scope, kind):
    ingest = image_store.ingest
    module = characters if kind == "characters" else pcs
    original = module.require_version(records["wroot"], records["records"][kind], "default")
    # Retain a second version so deletion respects the last-version invariant.
    original.with_name("veiled" + original.suffix).write_bytes(original.read_bytes())

    def remove_version(data, ext):
        obj = ingest(data, ext)
        module = characters if kind == "characters" else pcs
        module.delete_version(records["wroot"], records["records"][kind], "default")
        return obj

    monkeypatch.setattr(image_store, "ingest", remove_version)
    response = append(client, records, scope, kind)
    assert response.status_code == 404, response.text
    root = records["wroot" if scope == "world" else "croot"]
    assert not assets.version_dir(root, records["records"][kind], "default", kind).exists()

@pytest.mark.parametrize("operation", ("append", "reuse"))
def test_campaign_art_preparation_does_not_hold_campaign_lock(client, records, monkeypatch, operation):
    cid = records["campaign"]
    ingest = image_store.ingest
    link = assets.link_in
    prepared_under_lock, published_under_lock = [], []

    def observe_ingest(*args, **kwargs):
        prepared_under_lock.append(locks.holds_campaign(cid))
        return ingest(*args, **kwargs)

    def observe_link(*args, **kwargs):
        published_under_lock.append(locks.holds_campaign(cid))
        return link(*args, **kwargs)

    if operation == "reuse":
        d = assets.version_dir(records["wroot"], records["records"]["characters"], "default")
        d.mkdir(parents=True, exist_ok=True)
        (d / "avatar.png").write_bytes(png(2))
        source = client.get(art_url(records, "campaign", "characters")).json()["images"][0]["placement"]
    monkeypatch.setattr(image_store, "ingest", observe_ingest)
    monkeypatch.setattr(assets, "link_in", observe_link)
    if operation == "append":
        response = append(client, records, "campaign", "locations")
    else:
        url = art_url(records, "campaign", "locations")
        response = client.post(url + "/images/link", json={"source": source, "intent": "gallery"})
    assert response.status_code == 200, response.text
    assert prepared_under_lock == [False]
    assert published_under_lock == [True]

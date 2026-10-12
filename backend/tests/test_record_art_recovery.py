"""Recovery must settle bindings before Art checks vacancy or mutation tokens."""

import threading
from contextlib import contextmanager

import pytest

from grimoire.store import assets, image_refs, image_store, locks, overlay
from grimoire.store.record_art import write
from tests.record_art_fixtures import KINDS, art_url, image_url, png, setup_records, upload

SCOPES = (("world", "world"), ("campaign", "campaign"), ("campaign", "world"))


@pytest.fixture
def records(client, monkeypatch):
    # Pin independent stripes as the existing promotion-journal tests do. A
    # hash collision must not make the facade wait on the journal's other slot.
    stripes = {"avatar": 1, "gallery_1": 2, "gallery_2": 3}
    monkeypatch.setattr(locks, "_image_stripe", lambda dd, key: stripes.get(key.casefold(), 0))
    return setup_records(client)


def _dir(records, scope, kind="characters"):
    root = records["wroot" if scope == "world" else "croot"]
    return assets.version_dir(root, records["records"][kind], "default", kind)


def _journal(d, gallery, primary=None):
    old = primary["image_id"] if primary else None
    journal = {"name": "gallery_1", "pre": {"avatar": old, "gallery_1": gallery["image_id"]},
               "post": {"avatar": gallery["image_id"], "gallery_1": old},
               "desc": {"avatar": None, "gallery_1": None}}
    image_refs.write_journal(d, journal)
    return journal


@contextmanager
def _blocked_gallery(d):
    held, release = threading.Event(), threading.Event()

    def holder():
        with assets.name_locks_held((d, "gallery_1")):
            held.set()
            release.wait(timeout=15)

    thread = threading.Thread(target=holder)
    thread.start()

    def free():
        release.set()
        thread.join(timeout=5)
        assert not thread.is_alive()

    try:
        assert held.wait(timeout=5)
        yield free
    finally:
        free()


def _append(client, records, scope):
    path, sep, query = art_url(records, scope, "characters").partition("?")
    return client.post(path + "/images" + (sep + query if sep else ""),
                       files={"file": ("image.png", png(3), "image/png")})


def _mutate(client, records, scope, operation, selected, primary, kind="characters"):
    name = "gallery_2" if operation == "promote" else "avatar"
    url = image_url(records, scope, kind, name)
    guard = {"expected_v": selected["v"]}
    if operation == "delete":
        return client.delete(url, params=guard)
    if operation == "focus":
        return client.put(url + "/focus", json={**guard, "focus": 80})
    if operation == "description":
        return client.put(url + "/description", json={**guard, "description": "A portrait",
                                                      "expected_description_rev": selected["description_rev"]})
    return client.post(url + "/promote", json={**guard, "expected_primary_v": primary["v"]})


@pytest.mark.parametrize("scope,journal_scope", SCOPES)
def test_append_refuses_a_busy_promotion_before_claiming_empty_primary(client, records, monkeypatch, scope, journal_scope):
    gallery = upload(client, records, journal_scope, "characters", "gallery_1", 2)
    d = _dir(records, journal_scope)
    journal = _journal(d, gallery)
    link = assets.link_in
    with _blocked_gallery(d) as free:
        def release_before_writer(*args, **kwargs):
            free()
            return link(*args, **kwargs)

        monkeypatch.setattr(assets, "link_in", release_before_writer)
        response = _append(client, records, scope)
        assert response.status_code == 409, response.text
        assert "busy" in response.json().get("detail", ""), response.json()
        assert image_refs.read_journal(d) == journal
    rows = client.get(art_url(records, scope, "characters")).json()["images"]
    assert [(r["name"], r["image_id"]) for r in rows] == [("avatar", gallery["image_id"])]


@pytest.mark.parametrize("scope,journal_scope", SCOPES)
def test_append_chooses_a_vacant_slot_after_available_recovery(client, records, scope, journal_scope):
    gallery = upload(client, records, journal_scope, "characters", "gallery_1", 2)
    _journal(_dir(records, journal_scope), gallery)
    response = _append(client, records, scope)
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "gallery_1"
    rows = client.get(art_url(records, scope, "characters")).json()["images"]
    assert rows[0]["image_id"] == gallery["image_id"]
    assert len(rows) == 2


@pytest.mark.parametrize("scope,journal_scope", SCOPES)
@pytest.mark.parametrize("operation", ("delete", "focus", "promote", "description"))
@pytest.mark.parametrize("busy", (False, True))
def test_mutation_tokens_bind_to_recovered_art(client, records, monkeypatch, scope, journal_scope, operation, busy):
    primary = upload(client, records, journal_scope, "characters", "avatar", 1)
    gallery = upload(client, records, journal_scope, "characters", "gallery_1", 2)
    second = upload(client, records, journal_scope, "characters", "gallery_2", 3)
    snapshot = {r["name"]: r for r in client.get(art_url(records, scope, "characters")).json()["images"]}
    selected = snapshot["gallery_2" if operation == "promote" else "avatar"]
    d = _dir(records, journal_scope)
    journal = _journal(d, gallery, primary)
    if busy:
        perform = write._perform
        with _blocked_gallery(d) as free:
            def release_before_writer(*args, **kwargs):
                free()
                return perform(*args, **kwargs)

            monkeypatch.setattr(write, "_perform", release_before_writer)
            response = _mutate(client, records, scope, operation, selected, snapshot["avatar"])
            assert response.status_code == 409, response.text
            assert "busy" in response.json().get("detail", ""), response.json()
            assert image_refs.read_journal(d) == journal
    else:
        response = _mutate(client, records, scope, operation, selected, snapshot["avatar"])
        assert response.status_code == 409, response.text
        assert response.json()["kind"] == ("stale_primary" if operation == "promote" else "stale_image")
    rows = {r["name"]: r for r in client.get(art_url(records, scope, "characters")).json()["images"]}
    assert rows["avatar"]["image_id"] == gallery["image_id"]
    assert rows["gallery_1"]["image_id"] == primary["image_id"]
    assert rows["gallery_2"]["image_id"] == second["image_id"]
    assert rows["avatar"]["focus"] in (None, 50)
    assert all(image_store.read(row["image_id"]).raw.get("description") is None for row in rows.values())


@pytest.mark.parametrize("scope", ("world", "campaign"))
@pytest.mark.parametrize("pending_at", ("destination", "source"))
def test_primary_reuse_refuses_unfinished_source_or_destination_recovery(client, records, monkeypatch, scope, pending_at):
    source_image = upload(client, records, "world", "locations", "avatar", 1)
    source = client.get(art_url(records, scope, "locations")).json()["images"][0]["placement"]
    if pending_at == "destination":
        gallery = upload(client, records, scope, "characters", "gallery_1", 2)
        d = _dir(records, scope)
        journal = _journal(d, gallery)
    else:
        gallery = upload(client, records, "world", "locations", "gallery_1", 2)
        d = _dir(records, "world", "locations")
        journal = _journal(d, gallery, source_image)
    path, sep, query = art_url(records, scope, "characters").partition("?")
    link = assets.link_in
    with _blocked_gallery(d) as free:
        def release_before_writer(*args, **kwargs):
            free()
            return link(*args, **kwargs)

        monkeypatch.setattr(assets, "link_in", release_before_writer)
        response = client.post(path + "/images/link" + (sep + query if sep else ""),
                               json={"source": source, "intent": "primary", "expected_primary_v": None})
        assert response.status_code == 409, response.text
        assert "busy" in response.json().get("detail", ""), response.json()
        assert image_refs.read_journal(d) == journal
    if pending_at == "source":
        assert client.get(art_url(records, scope, "characters")).json()["images"] == []
    else:
        rows = client.get(art_url(records, scope, "characters")).json()["images"]
        assert len(rows) == 1 and rows[0]["image_id"] == gallery["image_id"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("operation", ("delete", "focus", "promote", "description"))
def test_detached_art_does_not_recover_an_unrelated_world_journal(client, records, kind, operation):
    upload(client, records, "campaign", kind, "avatar", 1)
    upload(client, records, "campaign", kind, "gallery_2", 3)
    # Owning the art is independent of materializing the record's text. The
    # existing detached-route fixtures use this same overlay marker.
    overlay.add_detached(records["campaign"], f"{kind}/{records['records'][kind]}")
    snapshot = {r["name"]: r for r in client.get(art_url(records, "campaign", kind)).json()["images"]}
    selected = snapshot["gallery_2" if operation == "promote" else "avatar"]
    gallery = upload(client, records, "world", kind, "gallery_1", 2)
    d = _dir(records, "world", kind)
    journal = _journal(d, gallery)
    with _blocked_gallery(d):
        response = _mutate(client, records, "campaign", operation, selected, snapshot["avatar"], kind)
        assert response.status_code == 200, response.text
        assert image_refs.read_journal(d) == journal
    # No mutation of the detached record should complete the stranger's swap.
    assert image_refs.read_journal(d) == journal

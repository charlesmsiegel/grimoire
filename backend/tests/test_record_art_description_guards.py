"""Stale browser actions cannot silently mutate a replacement or newer caption."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from grimoire.store import atomic, image_descriptions, image_store, locks
from tests.record_art_fixtures import art_url, image_url, png, setup_records, upload


@pytest.fixture
def records(client):
    return setup_records(client)


@pytest.mark.parametrize("initial,first,second", ((None, "A portrait", "A map"),
                                                (None, "", "A portrait"),
                                                ("A portrait", "A map", "")))
def test_cross_placement_description_save_conflicts(client, records, initial, first, second):
    upload(client, records, "world", "characters", "avatar")
    upload(client, records, "world", "locations", "avatar")
    if initial is not None:
        assert client.put(image_url(records, "world", "characters", "avatar") + "/description",
                          json={"description": initial}).status_code == 200
    actor = client.get(art_url(records, "world", "characters")).json()["images"][0]
    location = client.get(art_url(records, "world", "locations")).json()["images"][0]
    assert actor["image_id"] == location["image_id"]
    response = client.put(image_url(records, "world", "characters", "avatar") + "/description",
                           json={"description": first, "expected_v": actor["v"],
                                 "expected_description_rev": actor["description_rev"]})
    assert response.status_code == 200, response.text
    stale = client.put(image_url(records, "world", "locations", "avatar") + "/description",
                        json={"description": second, "expected_v": location["v"],
                              "expected_description_rev": location["description_rev"]})
    assert stale.status_code == 409, stale.text
    assert stale.json()["kind"] == "stale_description"
    assert stale.json()["write_target_description"]["text"] == first
    current = client.get(art_url(records, "world", "locations")).json()["images"][0]
    assert current["description"] == first and current["described"]
    assert current["v"] == location["v"]


def _dir(records, kind="characters", scope="world"):
    root = records["wroot"] if scope == "world" else records["croot"]
    return root / kind / records["records"][kind] / "assets" / "default"


def _row(client, records, kind="characters", scope="world"):
    return client.get(art_url(records, scope, kind)).json()["images"][0]


def _save(client, records, row, text, **extra):
    return client.put(image_url(records, "world", "characters", "avatar") + "/description",
                      json={"description": text, "expected_v": row["v"],
                            "expected_description_rev": row["description_rev"], **extra})


def test_legacy_masked_conflict_exposes_shared_write_target(client, records):
    upload(client, records, "world", "characters", "avatar")
    upload(client, records, "world", "locations", "avatar")
    image_descriptions.carry_legacy(_dir(records), "avatar", "A portrait")
    old = _row(client, records)
    url = image_url(records, "world", "locations", "avatar") + "/description"
    assert client.put(url, json={"description": "A map"}).status_code == 200
    conflict = _save(client, records, old, "A revised portrait")
    assert conflict.status_code == 409, conflict.text
    state = conflict.json()
    assert state["effective_description"]["text"] == "A portrait"
    assert state["write_target_description"]["text"] == "A map"
    assert state["write_target_description"]["scope"] == "shared"
    # Reconciliation only adopts the state actually displayed to the reader.
    assert client.put(url, json={"description": "A newer map"}).status_code == 200
    adopted = {**old, "description_rev": state["description_rev"]}
    assert _save(client, records, adopted, "A revised portrait").status_code == 409


def test_description_guard_uses_fresh_object_state_not_cached_projection(client, records, monkeypatch):
    upload(client, records, "world", "characters", "avatar")
    old = _row(client, records)
    cached = image_store.read(old["image_id"])
    raw = {**cached.raw, "description": "A map", "unrelated": "retained"}
    atomic.write_text(image_store.object_path(cached.id), json.dumps(raw))
    monkeypatch.setattr(image_store, "read", lambda image_id: cached)
    response = _save(client, records, old, "A portrait")
    assert response.status_code == 409, response.text
    current = _row(client, records)
    assert current["description"] == "A map"
    assert _save(client, records, current, "A revised portrait").status_code == 200
    assert image_store.read_fresh(cached.id).raw["unrelated"] == "retained"


def _partial(client, records, monkeypatch):
    upload(client, records, "world", "characters", "avatar")
    image_descriptions.carry_legacy(_dir(records), "avatar", "A portrait")
    row = _row(client, records)
    original = image_descriptions._clear_legacy

    def fail(d, name):
        raise OSError("legacy cleanup unavailable")

    monkeypatch.setattr(image_descriptions, "_clear_legacy", fail)
    response = _save(client, records, row, "A revised portrait")
    assert response.status_code == 500, response.text
    result = response.json()
    assert result["kind"] == "description_cleanup_incomplete"
    assert result["committed"] is True
    assert result["confirmed_text"] == "A revised portrait"
    return result, original


def test_shared_commit_then_legacy_cleanup_failure_is_partial(client, records, monkeypatch):
    result, _ = _partial(client, records, monkeypatch)
    recovery = result["recovery"]
    assert recovery["available"] is True
    assert recovery["effective_description"]["text"] == "A portrait"
    assert recovery["write_target_description"]["text"] == "A revised portrait"
    assert recovery["description_rev"] == _row(client, records)["description_rev"]


def test_unreadable_partial_recovery_does_not_invent_empty_state(client, records, monkeypatch):
    upload(client, records, "world", "characters", "avatar")
    image_descriptions.carry_legacy(_dir(records), "avatar", "A portrait")
    old = _row(client, records)
    state_in = image_descriptions.description_state_in
    failed = False

    def state(*args, **kwargs):
        if failed:
            raise OSError("metadata unavailable")
        return state_in(*args, **kwargs)

    def cleanup(d, name):
        nonlocal failed
        failed = True
        raise OSError("legacy cleanup unavailable")

    monkeypatch.setattr(image_descriptions, "description_state_in", state)
    monkeypatch.setattr(image_descriptions, "_clear_legacy", cleanup)
    result = _save(client, records, old, "A revised portrait").json()
    assert result["committed"] is True
    assert result["recovery"] == {"available": False}


def test_cleanup_only_retry_never_rewrites_shared_description(client, records, monkeypatch):
    result, original = _partial(client, records, monkeypatch)
    current = _row(client, records)
    monkeypatch.setattr(image_descriptions, "_clear_legacy", original)

    def never_update(*args, **kwargs):
        raise AssertionError("cleanup must not rewrite shared metadata")

    monkeypatch.setattr(image_store, "update", never_update)
    response = _save(client, records, current, result["confirmed_text"], cleanup_only=True)
    assert response.status_code == 200, response.text
    assert _row(client, records)["description"] == result["confirmed_text"]
    assert "avatar" not in image_descriptions.read_raw(_dir(records))


def test_shared_change_before_cleanup_retry_is_refused(client, records, monkeypatch):
    result, _ = _partial(client, records, monkeypatch)
    current = _row(client, records)
    image_store.update(current["image_id"], lambda raw: {**raw, "description": "A newer portrait"})
    response = _save(client, records, current, result["confirmed_text"], cleanup_only=True)
    assert response.status_code == 409, response.text
    assert response.json()["write_target_description"]["text"] == "A newer portrait"


def test_description_revision_ignores_focus_and_unrelated_metadata(client, records):
    upload(client, records, "world", "characters", "avatar")
    row = _row(client, records)
    url = image_url(records, "world", "characters", "avatar")
    assert client.put(url + "/focus", json={"focus": 80}).status_code == 200
    image_store.update(row["image_id"], lambda raw: {**raw, "unrelated": "retained"})
    assert _row(client, records)["description_rev"] == row["description_rev"]
    assert _save(client, records, row, "A portrait").status_code == 200


def test_guarded_inherited_legacy_caption_keeps_campaign_fallback(client, records):
    d = _dir(records)
    d.mkdir(parents=True, exist_ok=True)
    (d / "avatar.png").write_bytes(png())
    image_descriptions.carry_legacy(d, "avatar", "A portrait")
    old = _row(client, records, scope="campaign")
    response = client.put(image_url(records, "campaign", "characters", "avatar") + "/description",
                          json={"description": "A revised portrait", "expected_v": old["v"],
                                "expected_description_rev": old["description_rev"]})
    assert response.status_code == 200, response.text
    assert image_descriptions.read_raw(d)["avatar"] == "A portrait"
    assert _row(client, records, scope="campaign")["description"] == "A revised portrait"


def test_unreadable_caption_state_cannot_authorize_guarded_save(client, records):
    upload(client, records, "world", "characters", "avatar")
    old = _row(client, records)
    image_descriptions.path_in(_dir(records)).write_text("{unfinished", encoding="utf-8")
    current = _row(client, records)
    assert current["description_available"] is False and current["description_rev"] is None
    response = _save(client, records, old, "A portrait")
    assert response.status_code == 503, response.text
    assert response.json()["kind"] == "description_unavailable"
    assert "description" not in image_store.read_fresh(old["image_id"]).raw


@pytest.mark.parametrize("missing", ("expected_v", "expected_description_rev"))
def test_cleanup_requires_both_preconditions(client, records, missing):
    upload(client, records, "world", "characters", "avatar")
    row = _row(client, records)
    body = {"description": "A portrait", "cleanup_only": True,
            "expected_v": row["v"], "expected_description_rev": row["description_rev"]}
    del body[missing]
    response = client.put(image_url(records, "world", "characters", "avatar") + "/description",
                          json=body)
    assert response.status_code == 400, response.text
    assert not _row(client, records)["described"]


def _independent_location(client, records):
    # Deliberate stripe collisions serialize unrelated placements too. This
    # test must reach the shared object's lock, so select different placement
    # stripes before arranging its event handshake.
    first = _dir(records)
    for _ in range(64):
        second = _dir(records, "locations")
        if (locks.image_name_lock(first, "avatar") is not locks.image_name_lock(second, "avatar")
                and locks.image_sidecar_lock(first, image_descriptions.DESCRIPTIONS_FILE)
                is not locks.image_sidecar_lock(second, image_descriptions.DESCRIPTIONS_FILE)):
            return
        created = client.post(f'/api/worlds/{records["world"]}/locations', json={"name": "Saltmarch"})
        records["records"]["locations"] = created.json()["id"]
    raise AssertionError("could not select independent placement stripes")


def test_two_placement_writers_share_one_atomic_description_guard(client, records, monkeypatch):
    _independent_location(client, records)
    upload(client, records, "world", "characters", "avatar")
    upload(client, records, "world", "locations", "avatar")
    actor = _row(client, records)
    location = _row(client, records, "locations")
    checked = threading.Event()
    attempted = threading.Event()
    local = threading.local()
    original_lock = locks.image_object_lock
    original_guard = image_descriptions._guard_description

    @contextmanager
    def observed_lock(image_id):
        if getattr(local, "writer", None) == "second":
            attempted.set()
        with original_lock(image_id):
            yield

    def observed_guard(*args):
        original_guard(*args)
        if getattr(local, "writer", None) == "first":
            checked.set()
            assert attempted.wait(10), "second writer never reached the shared object"

    monkeypatch.setattr(locks, "image_object_lock", observed_lock)
    monkeypatch.setattr(image_descriptions, "_guard_description", observed_guard)

    # TestClient dispatches sync routes on a worker thread. Mark each writer
    # inside the store call, where the object lock instrumentation runs.
    set_in = image_descriptions.set_in

    def marked_set_in(d, *args, **kwargs):
        local.writer = "first" if d == _dir(records) else "second"
        return set_in(d, *args, **kwargs)

    monkeypatch.setattr(image_descriptions, "set_in", marked_set_in)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(_save, client, records, actor, "A portrait")
        assert checked.wait(10), "first writer never checked its revision"
        second = pool.submit(client.put, image_url(records, "world", "locations", "avatar") + "/description",
                             json={"description": "A map", "expected_v": location["v"],
                                   "expected_description_rev": location["description_rev"]})
        assert first.result().status_code == 200
        assert second.result().status_code == 409
    assert _row(client, records)["description"] == "A portrait"

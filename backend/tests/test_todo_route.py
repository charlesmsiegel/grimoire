"""GET /api/todo — everything the app noticed, and the ignore that silences one.

Two properties are the whole feature, and both are the kind that rot quietly:

  * a chore at zero is not in the list, so a label's number is always the one
    this request computed;
  * an ignored chore is counted nowhere, and is still there to be restored.

Neither survives a refactor on its own, so both are held here.
"""

from __future__ import annotations

import importlib
import inspect
import io
import json
import re
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import grimoire.store as store
from grimoire.main import create_app
from grimoire.routes import todo
from grimoire.routes.common import thumb_query
from grimoire.store import inference_keys
from grimoire.store.continuity import candidates, canon, involvement, pending, similarity
from grimoire.store.continuity import doc as continuity_doc
from tests import inference_fixtures
from tests.collection_fixtures import format1, format2

pytestmark = pytest.mark.upgraded_birth


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def campaign(client):
    wid = client.post("/api/worlds", json={"name": "Saltmarch"}).json()["id"]
    cid = client.post("/api/campaigns",
                      json={"name": "A Long Run", "world": wid}).json()["id"]
    # These tests isolate other chores. Cover gaps have their own cases below.
    store.covers.put_cover(cid, _png(), "png")
    store.covers.put_world_cover(wid, _png(), "png")
    return cid, wid


def _todo(client, cid: str) -> dict:
    r = client.get("/api/todo", params={"campaign": cid})
    assert r.status_code == 200, r.text
    return r.json()


def test_referenced_greeting_art_can_be_tagged_to_another_character_or_none(client, campaign):
    _cid, wid = campaign
    root = store.worlds.world_root(wid)
    owner, vid = store.characters.create_character(root, "Seraphine", "default")
    subject, _ = store.characters.create_character(root, "Mara", "default")
    store.assets.put_image(root, owner, vid, "embed-art", b"png", "png")
    url = f"/api/worlds/{wid}/characters/{owner}/versions/{vid}/images/embed-art"
    gid = store.greetings.create_greeting(root, "Saltmarch", owner, vid, f"![Art]({url}?v=old)")
    endpoint = f"/api/worlds/{wid}/greetings/{gid}/subjects"
    row = next(c for c in _todo(client, "")["chores"] if c["id"] == "world-subjects")
    assert row["n"] == 1
    queue = client.get(f"/api/worlds/{wid}/subjects/untagged").json()
    assert len(queue) == 1 and queue[0]["url"].startswith(url)
    assert client.put(endpoint, json={"image": url, "subjects": ["missing"]}).status_code == 400
    assert client.put(endpoint, json={"image": url + "-missing", "subjects": []}).status_code == 404
    assert client.put(endpoint, json={"image": url + "?v=new", "subjects": [subject]}).status_code == 200
    assert client.get(endpoint).json() == {url: [subject]}
    assert "world-subjects" not in {c["id"] for c in _todo(client, "")["chores"]}
    appearances = client.get(f"/api/worlds/{wid}/characters/{subject}/appearances").json()
    assert len(appearances) == 1 and appearances[0]["url"].startswith(url)
    copy = client.post(f"/api/worlds/{wid}/characters/{subject}/versions/default/images/copy-from-greeting",
                       json={"gid": gid, "name": url, "slot": "avatar"})
    assert copy.status_code == 200
    assert store.assets.image_path(root, subject, "default", "avatar").read_bytes() == b"png"
    assert client.put(endpoint, json={"image": url, "subjects": []}).status_code == 200
    assert client.get(endpoint).json() == {url: []}
    assert client.get(f"/api/worlds/{wid}/subjects/untagged").json() == []


def test_reference_tags_are_per_greeting_and_remote_query_variants_are_distinct(client, campaign):
    _cid, wid = campaign
    root = store.worlds.world_root(wid)
    owner, vid = store.characters.create_character(root, "Seraphine", "default")
    url = "https://example.test/art.png?variant=1"
    first = store.greetings.create_greeting(root, "Mara", owner, vid,
                                           f"![Art]({url})\n![Other]({url[:-1]}2)")
    second = store.greetings.create_greeting(root, "Winifred", owner, vid, f"![Art]({url})")
    assert len(client.get(f"/api/worlds/{wid}/subjects/untagged").json()) == 3
    endpoint = f"/api/worlds/{wid}/greetings/{first}/subjects"
    assert client.put(endpoint, json={"image": url, "subjects": [owner]}).status_code == 200
    queue = client.get(f"/api/worlds/{wid}/subjects/untagged").json()
    assert {(a["gid"], a["name"]) for a in queue} == {(first, url[:-1] + "2"), (second, url)}
    appearances = client.get(f"/api/worlds/{wid}/characters/{owner}/appearances").json()
    assert appearances[0]["url"] == url and appearances[0]["copyable"] is False


def test_cross_world_reference_resolves_the_world_named_in_the_image_url(client, campaign):
    _cid, wid = campaign
    other = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    root, other_root = store.worlds.world_root(wid), store.worlds.world_root(other)
    owner, vid = store.characters.create_character(root, "Seraphine", "default")
    store.characters.create_character(other_root, "Seraphine", "default")
    store.assets.put_image(root, owner, vid, "avatar", b"wrong-world", "png")
    store.assets.put_image(other_root, owner, vid, "avatar", b"other-world", "png")
    url = f"/api/worlds/{other}/characters/{owner}/versions/{vid}/images/avatar"
    gid = store.greetings.create_greeting(root, "Saltmarch", owner, vid, f"![Art]({url})")
    assert client.put(f"/api/worlds/{wid}/greetings/{gid}/subjects",
                      json={"image": url, "subjects": [owner]}).status_code == 200
    copied = client.post(f"/api/worlds/{wid}/characters/{owner}/versions/{vid}/images/copy-from-greeting",
                         json={"gid": gid, "name": url, "slot": "gallery"})
    assert copied.status_code == 200
    assert store.assets.image_path(root, owner, vid, copied.json()["name"]).read_bytes() == b"other-world"


def test_untagged_and_appearances_routes_expose_no_slot(client, campaign):
    """The catalog the store tags by carries each key's slot -- a filesystem
    directory -- and none of it may reach a response. Tagged through the route,
    the answer lives on the image object, not in the greeting's sidecar."""
    _cid, wid = campaign
    root = store.worlds.world_root(wid)
    owner, vid = store.characters.create_character(root, "Seraphine", "default")
    store.assets.put_image(root, owner, vid, "embed-art", b"png-ref", "png")
    url = f"/api/worlds/{wid}/characters/{owner}/versions/{vid}/images/embed-art"
    gid = store.greetings.create_greeting(root, "Saltmarch", owner, vid, f"![Art]({url})")
    store.assets.put_image(root, gid, "default", "art_1", b"png-own", "png", base="greetings")
    queue = client.get(f"/api/worlds/{wid}/subjects/untagged").json()
    assert {a["name"] for a in queue} == {url, "art_1"}
    endpoint = f"/api/worlds/{wid}/greetings/{gid}/subjects"
    for key in (url, "art_1"):
        assert client.put(endpoint, json={"image": key, "subjects": [owner]}).status_code == 200
    assert not store.image_subjects.subjects_path(root, gid).exists()
    appearances = client.get(f"/api/worlds/{wid}/characters/{owner}/appearances").json()
    assert {a["name"] for a in appearances} == {url, "art_1"}
    for row in [*queue, *appearances]:
        assert "slot" not in row and "target" not in row
        assert not any(str(root) in str(v) for v in row.values())


def test_the_subjects_chore_reads_no_placement_its_catalog_already_read(client, campaign, monkeypatch):
    """`/api/todo` sweeps every greeting of every world. Answering a key off
    its object uses the id the catalog carried; reading the placement again
    would double the cost of the sweep."""
    _cid, wid = campaign
    root = store.worlds.world_root(wid)
    owner, vid = store.characters.create_character(root, "Seraphine", "default")
    store.assets.put_image(root, owner, vid, "embed-art", b"png-ref", "png")
    url = f"/api/worlds/{wid}/characters/{owner}/versions/{vid}/images/embed-art"
    gid = store.greetings.create_greeting(root, "Saltmarch", owner, vid, f"![Art]({url})")
    for name in ("art_1", "art_2"):
        store.assets.put_image(root, gid, "default", name, f"png-{name}".encode(), "png",
                               base="greetings")
    endpoint = f"/api/worlds/{wid}/greetings/{gid}/subjects"
    for key in (url, "art_1"):
        assert client.put(endpoint, json={"image": key, "subjects": [owner]}).status_code == 200

    # Other chores read placements of their own (covers); only a slot some
    # catalog already listed is off limits, outside the building of a catalog.
    listed: set = set()
    building = False
    real_catalog = store.greeting_images.catalog_with_slots
    real_read = store.image_refs.read

    def catalog(root, gid):
        nonlocal building
        building = True
        try:
            items = real_catalog(root, gid)
        finally:
            building = False
        listed.update(target[1] for _entry, target in items.values() if target is not None)
        return items

    def read(d, name):
        if not building and (d, name) in listed:
            raise AssertionError(f"placement {name!r} read again after the catalog")
        return real_read(d, name)

    monkeypatch.setattr(store.greeting_images, "catalog_with_slots", catalog)
    monkeypatch.setattr(store.image_refs, "read", read)
    row = next(c for c in _todo(client, "")["chores"] if c["id"] == "world-subjects")
    assert row["n"] == 1
    # The queue itself, too. (Its ROUTE then builds image URLs per row, which
    # is presentation and resolves what it serves; the chore count does not.)
    assert [a["name"] for a in store.image_subjects.untagged(root)] == ["art_2"]


def test_collection_members_are_individually_reviewed_and_deduplicated(client, campaign):
    _cid, wid = campaign
    root = store.worlds.world_root(wid)
    owner, vid = store.characters.create_character(root, "Seraphine", "default")
    collection = "a" * 32
    first, second = format1(wid, collection, _png(), _png("white"))
    url = f"/api/worlds/{wid}/images/{first}"
    gid = store.greetings.create_greeting(root, "Saltmarch", owner, vid,
        f"![Art](/api/worlds/{wid}/image-collections/{collection}/image)\n![Again]({url}?v=old)")
    queue_url = f"/api/worlds/{wid}/subjects/untagged"
    queue = client.get(queue_url).json()
    assert len(queue) == 2
    assert {a["name"] for a in queue} == {url, f"/api/worlds/{wid}/images/{second}"}
    endpoint = f"/api/worlds/{wid}/greetings/{gid}/subjects"
    assert client.put(endpoint, json={"image": url, "subjects": []}).status_code == 200
    assert len(client.get(queue_url).json()) == 1
    # Available members, not the immutable manifest's missing bytes, are TODO.
    store.assets.path_in(root / "assets" / "images", second, supported_only=True).unlink()
    assert client.get(queue_url).json() == []
    assert client.get(endpoint).json() == {url: []}


def _member_blob(image_id: str):
    found = store.image_refs.resolve_ref(store.image_refs.Ref(name="", image=image_id, focus=None))
    assert found is not None
    return found


def test_untagged_lists_format_2_members_once_each(client, campaign):
    """The format-2 copy of the test above: each available member is one
    queue entry, keyed by its member URL without the query, whether the
    greeting shows it through the collection or by its own URL."""
    _cid, wid = campaign
    root = store.worlds.world_root(wid)
    owner, vid = store.characters.create_character(root, "Seraphine", "default")
    collection = "a" * 32
    first, second = format2(wid, collection, _png(), _png("white"))
    base = f"/api/worlds/{wid}/image-collections/{collection}"
    url = f"{base}/members/0"
    gid = store.greetings.create_greeting(root, "Saltmarch", owner, vid,
        f"![Art]({base}/image)\n![Again]({url}?v=old)")
    queue_url = f"/api/worlds/{wid}/subjects/untagged"
    queue = client.get(queue_url).json()
    assert len(queue) == 2
    assert {a["name"] for a in queue} == {url, f"{base}/members/1"}
    endpoint = f"/api/worlds/{wid}/greetings/{gid}/subjects"
    assert client.put(endpoint, json={"image": url, "subjects": []}).status_code == 200
    assert len(client.get(queue_url).json()) == 1
    # Available members, not the immutable manifest's missing pixels, are TODO.
    _member_blob(second).blob_path.unlink()
    assert client.get(queue_url).json() == []
    assert client.get(endpoint).json() == {url: []}
    assert store.image_store.read(first).raw["reviews"] == {"subjects": [f"world:{wid}"]}


def test_a_format_2_member_tile_has_v_thumb_and_image_id(client, campaign):
    _cid, wid = campaign
    root = store.worlds.world_root(wid)
    owner, vid = store.characters.create_character(root, "Seraphine", "default")
    collection = "b" * 32
    [image_id] = format2(wid, collection, _png("white"))
    url = f"/api/worlds/{wid}/image-collections/{collection}/members/0"
    store.greetings.create_greeting(root, "Saltmarch", owner, vid, f"![Art]({url})")
    [row] = client.get(f"/api/worlds/{wid}/subjects/untagged").json()
    v = _member_blob(image_id).blob_sha256
    assert row["url"] == f"{url}?v={v}"
    assert row["thumb"] == f"{url}{thumb_query(v)}"
    assert row["image_id"] == image_id
    assert "copyable" not in row
    assert client.get(row["thumb"]).status_code == 200


def test_copy_from_greeting_links_a_format_2_member(client, campaign):
    _cid, wid = campaign
    root = store.worlds.world_root(wid)
    owner, vid = store.characters.create_character(root, "Seraphine", "default")
    collection = "c" * 32
    _first, second = format2(wid, collection, _png(), _png("white"))
    url = f"/api/worlds/{wid}/image-collections/{collection}/members/1"
    gid = store.greetings.create_greeting(root, "Saltmarch", owner, vid,
        f"![Art](/api/worlds/{wid}/image-collections/{collection}/image)")
    blobs = sorted(p for p in (store.image_store.store_root() / "blobs").rglob("*") if p.is_file())
    copied = client.post(f"/api/worlds/{wid}/characters/{owner}/versions/{vid}/images/copy-from-greeting",
                         json={"gid": gid, "name": url, "slot": "gallery"})
    assert copied.status_code == 200, copied.text
    assert store.assets.image_id(root, owner, vid, copied.json()["name"]) == second
    assert sorted(p for p in (store.image_store.store_root() / "blobs").rglob("*") if p.is_file()) == blobs
    missing = client.post(f"/api/worlds/{wid}/characters/{owner}/versions/{vid}/images/copy-from-greeting",
                          json={"gid": gid, "name": url.replace("/members/1", "/members/2"),
                                "slot": "gallery"})
    assert missing.status_code == 404


def test_a_reference_removed_during_assignment_is_reported_missing(client, campaign, monkeypatch):
    _cid, wid = campaign
    root = store.worlds.world_root(wid)
    owner, vid = store.characters.create_character(root, "Seraphine", "default")
    url = "https://example.test/art.png"
    gid = store.greetings.create_greeting(root, "Saltmarch", owner, vid, f"![Art]({url})")
    real_set = store.image_subjects.set_image_subjects

    def remove_before_write(root, greeting, name, subjects):
        store.greetings.update_greeting(root, greeting, body="No image")
        real_set(root, greeting, name, subjects)

    monkeypatch.setattr(store.image_subjects, "set_image_subjects", remove_before_write)
    response = client.put(f"/api/worlds/{wid}/greetings/{gid}/subjects",
                          json={"image": url, "subjects": []})
    assert response.status_code == 404
    assert store.image_subjects.read_subjects(root, gid) == {}


def test_a_clean_campaign_has_no_chores(client, campaign):
    cid, _ = campaign
    body = _todo(client, cid)
    assert body["chores"] == []
    assert body["count"] == 0


def test_global_rows_keep_campaigns_separate_and_scoped_rows_exclude_library(client, campaign):
    first, wid = campaign
    second = client.post("/api/campaigns", json={"name": "Realm", "world": wid}).json()["id"]
    for cid in (first, second):
        for i in range(2):
            client.post(f"/api/campaigns/{cid}/scenes", json={"title": f"Scene {i}"})
    rows = [row for row in _todo(client, "")["chores"] if row["id"] == "open-scenes"]
    assert {row["campaign_id"] for row in rows} == {first, second}
    assert {row["campaign_name"] for row in rows} == {"A Long Run", "Realm"}
    scoped = _todo(client, first)
    assert all(row["campaign_id"] == first for row in scoped["chores"])
    assert all(row["scope"] == "campaign" for row in scoped["chores"])
    items = client.get("/api/todo/open-scenes/items", params={"campaign": second}).json()
    assert items["total"] == 2
    assert all(item["fix"].startswith(f"/campaigns/{second}/") for item in items["items"])


def test_campaign_ignore_is_scoped_and_legacy_ids_migrate_on_write(client, campaign):
    first, wid = campaign
    second = client.post("/api/campaigns", json={"name": "Realm", "world": wid}).json()["id"]
    for cid in (first, second):
        for i in range(2):
            client.post(f"/api/campaigns/{cid}/scenes", json={"title": f"Scene {i}"})
    client.put("/api/todo/open-scenes/ignored", json={"ignored": True, "campaign": first})
    assert "open-scenes" in {row["id"] for row in _todo(client, first)["ignored"]}
    assert "open-scenes" in {row["id"] for row in _todo(client, second)["chores"]}
    store.chores.set_ignored("open-scenes", True)
    assert "open-scenes" in {row["id"] for row in _todo(client, second)["ignored"]}
    client.put("/api/todo/open-scenes/ignored", json={"ignored": False, "campaign": first})
    assert "open-scenes" in {row["id"] for row in _todo(client, first)["chores"]}
    assert "open-scenes" in {row["id"] for row in _todo(client, second)["ignored"]}
    later = client.post("/api/campaigns", json={"name": "Winifred", "world": wid}).json()["id"]
    for i in range(2):
        client.post(f"/api/campaigns/{later}/scenes", json={"title": f"Scene {i}"})
    assert "open-scenes" in {row["id"] for row in _todo(client, later)["chores"]}


def test_a_chore_at_zero_leaves_the_list(client, campaign):
    """The property the whole page rests on.

    A list that can go stale teaches the reader to distrust it, and then the one
    entry that mattered is the one they scroll past. Nothing is stored: the
    chore is derived, so it disappears the moment its cause does.
    """
    cid, _ = campaign
    for i in range(2):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": f"Scene {i}"})
    ids = [c["id"] for c in _todo(client, cid)["chores"]]
    assert "open-scenes" in ids

    # Completing one leaves the remaining scene visible, even on its own.
    scenes = store.scenes.read.list_scenes(cid)
    store.scenes.mark_absorbed(cid, scenes[0]["id"], "It ended.", "It ended.")
    chore = next(c for c in _todo(client, cid)["chores"] if c["id"] == "open-scenes")
    assert chore["n"] == 1
    store.scenes.mark_absorbed(cid, scenes[1]["id"], "It ended.", "It ended.")
    assert "open-scenes" not in [c["id"] for c in _todo(client, cid)["chores"]]


def test_a_chore_carries_why_it_matters_not_just_a_count(client, campaign):
    cid, _ = campaign
    for i in range(2):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": f"Scene {i}"})
    chore = next(c for c in _todo(client, cid)["chores"] if c["id"] == "open-scenes")
    assert chore["why"]
    assert chore["fix"]


def test_ignoring_moves_a_chore_and_stops_counting_it(client, campaign):
    cid, _ = campaign
    for i in range(2):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": f"Scene {i}"})
    assert _todo(client, cid)["count"] == 1

    r = client.put("/api/todo/open-scenes/ignored", json={"ignored": True, "campaign": cid})
    assert r.status_code == 200

    body = _todo(client, cid)
    assert body["count"] == 0
    assert [c["id"] for c in body["chores"]] == []
    # ...and it is not gone. A dismissal that cannot be taken back is one
    # nobody dares make.
    assert [c["id"] for c in body["ignored"]] == ["open-scenes"]


def test_restoring_puts_it_back(client, campaign):
    cid, _ = campaign
    for i in range(2):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": f"Scene {i}"})
    client.put("/api/todo/open-scenes/ignored", json={"ignored": True, "campaign": cid})
    client.put("/api/todo/open-scenes/ignored", json={"ignored": False, "campaign": cid})
    assert _todo(client, cid)["count"] == 1


def test_the_shell_badge_does_not_count_an_ignored_chore(client, campaign):
    """The rail's number is what the reader still cares about.

    An ignore that silenced the page but left the badge lit would be worse than
    no ignore at all: the reader would have told the app to stop asking and it
    would still be asking, in the one place they cannot close.
    """
    cid, _ = campaign
    for i in range(2):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": f"Scene {i}"})
    assert client.get("/api/shell", params={"campaign": cid}).json()["todo"] is None
    client.put("/api/todo/open-scenes/ignored", json={"ignored": True, "campaign": cid})
    assert client.get("/api/shell", params={"campaign": cid}).json()["todo"] is None


def test_an_unknown_chore_id_is_refused(client):
    """An ignore set that accumulates ids nothing emits grows forever and
    silences things nobody can name."""
    r = client.put("/api/todo/not-a-chore/ignored", json={"ignored": True})
    assert r.status_code == 400


def test_a_malformed_ignore_file_is_an_empty_set_not_an_error(client, campaign, tmp_path):
    """This decides what a list SHOWS. A broken judgement file must not stop the
    app telling the user what is waiting."""
    cid, _ = campaign
    (tmp_path / "chores.json").write_text("{ not json", encoding="utf-8")
    assert store.chores.ignored() == set()
    assert _todo(client, cid)["count"] == 0


def test_no_campaign_asked_for_answers_an_empty_list(client):
    r = client.get("/api/todo")
    assert r.status_code == 200
    assert r.json() == {"chores": [], "ignored": [], "count": 0, "groups": []}


def _items(client, chore_id: str, cid: str) -> dict:
    r = client.get(f"/api/todo/{chore_id}/items", params={"campaign": cid})
    assert r.status_code == 200, r.text
    return r.json()


def test_expanding_a_chore_names_its_instances(client, campaign):
    """A count says how much is undone; it never says which.

    The whole reason to expand: "2 scenes are open at once" is a number, and
    the two titles are the thing a reader can act on.
    """
    cid, _ = campaign
    made = [client.post(f"/api/campaigns/{cid}/scenes",
                        json={"title": t}).json()["id"]
            for t in ("The Lower Step", "The Weir")]
    body = _items(client, "open-scenes", cid)
    assert body["total"] == 2
    assert {i["label"] for i in body["items"]} == {"The Lower Step", "The Weir"}
    # Each instance can be gone to, which is what makes the list worth opening
    # rather than reading.
    assert {i["fix"] for i in body["items"]} == {
        f"/campaigns/{cid}/scenes/{sid}" for sid in made}


def test_an_instance_carries_detail_not_just_a_name(client, campaign):
    """A list of bare names is the count again, spelled out."""
    cid, _ = campaign
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "The Lower Step"})
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "The Weir"})
    for item in _items(client, "open-scenes", cid)["items"]:
        assert item["detail"]


def test_the_counts_and_the_instances_agree(client, campaign):
    """Two computations of one fact, and the page shows them together -- a
    chore reading "2" that expands to three rows is worse than either."""
    cid, _ = campaign
    for t in ("A", "B", "C"):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": t})
    chore = next(c for c in _todo(client, cid)["chores"] if c["id"] == "open-scenes")
    assert chore["n"] == _items(client, "open-scenes", cid)["total"]


def test_items_for_an_unknown_chore_are_refused(client, campaign):
    cid, _ = campaign
    assert client.get("/api/todo/not-a-chore/items",
                      params={"campaign": cid}).status_code == 400


def test_items_with_no_campaign_are_empty_rather_than_an_error(client):
    r = client.get("/api/todo/open-scenes/items")
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0, "truncated": False}


def test_a_capped_list_reports_that_it_was_capped(client, campaign, monkeypatch):
    """A short list nobody labels reads as a complete one."""
    from grimoire.routes import todo as todo_routes
    monkeypatch.setattr(todo_routes, "ITEM_CAP", 2)
    cid, _ = campaign
    for t in ("A", "B", "C", "D"):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": t})
    body = _items(client, "open-scenes", cid)
    assert len(body["items"]) == 2
    assert body["total"] == 4
    assert body["truncated"] is True


# ---- the campaign is copy-on-write over its world (#248) ----
#
# These three are the bug this section exists to keep out, from its three
# sides. The chore walked the WORLD root, which gets all three wrong at once:
# it sees records the campaign deleted, and it cannot see the per-file sidecars
# that are where a played campaign's taglines and anchors actually live.

def _world_character(client, wid: str, name: str) -> str:
    return client.post(f"/api/worlds/{wid}/characters",
                       json={"name": name}).json()["character"]


def test_a_character_the_campaign_deleted_is_not_its_problem(client, campaign):
    """A chore about somebody who is not in the campaign is one the reader can
    neither act on nor dismiss."""
    cid, wid = campaign
    keep = _world_character(client, wid, "Mara Vance")
    gone = _world_character(client, wid, "Winifred Ash")
    store.overlay.add_deleted(cid, f"characters/{gone}")

    listed = {i["id"] for i in _items(client, "taglines", cid)["items"]}
    assert keep in listed
    assert gone not in listed


def test_a_tagline_written_campaign_side_counts(client, campaign):
    """`tagline.md` is a sidecar that overlays PER FILE.

    Reading the world's `character.md` frontmatter sees none of them, so every
    character whose tagline was written inside a campaign was reported as
    lacking one -- which, in a campaign that has been played, is most of them.
    """
    cid, wid = campaign
    aid = _world_character(client, wid, "Mara Vance")
    assert aid in {i["id"] for i in _items(client, "taglines", cid)["items"]}

    store.taglines.write(store.campaigns.paths.campaign_root(cid), aid,
                         "Harbour clerk who counts what the tide leaves.")
    assert aid not in {i["id"] for i in _items(client, "taglines", cid)["items"]}


def test_a_voice_anchor_written_campaign_side_counts(client, campaign):
    """Same shape, and `set_voice_anchor` is the one writer -- it exists so
    nothing outside the overlay hands `voice_anchors` a raw campaign root."""
    cid, wid = campaign
    aid = _world_character(client, wid, "Mara Vance")
    assert aid in {i["id"] for i in _items(client, "anchors", cid)["items"]}

    store.overlay.set_voice_anchor(cid, aid, "Clipped. Counts aloud.")
    assert aid not in {i["id"] for i in _items(client, "anchors", cid)["items"]}


# --- The library's own chores, and the two numbers that must not drift -------
#
# These arrived with the world chores (an undescribed image backlog, a world
# whose cast has no taglines). Each of the three tests below holds a place where
# the same fact is now computed twice, cheaply and honestly, and the cheap one
# is what ships on the hot path.


def _png(color: str = "black") -> bytes:
    """A real 2x2 PNG. `assets.put_image` sniffs the bytes for the extension."""
    output = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(output, "PNG")
    return output.getvalue()


def _add_images(client, wid: str, chid: str, *names: str) -> None:
    """Undescribed gallery art on a character, written through the store.

    Through `assets.put_image` rather than the upload route: the route is
    multipart and this is setting up a backlog, not exercising the upload.
    """
    root = store.worlds.paths.world_root(wid)
    vid = client.get(f"/api/worlds/{wid}/characters/{chid}").json()["meta"]["default_version"]
    for n in names:
        store.assets.put_image(root, chid, vid, n, _png(), "png")


def _world_with_a_character(client, name: str = "Realm") -> tuple[str, str]:
    wid = client.post("/api/worlds", json={"name": name}).json()["id"]
    r = client.post(f"/api/worlds/{wid}/characters", json={"name": "Winifred"})
    assert r.status_code == 200, r.text
    return wid, r.json()["character"]


def test_world_chores_answer_with_no_campaign_open(client):
    """The point of the whole scope split.

    Every chore used to be about a campaign, so `/todo` outside one said "open
    a campaign first" -- which is exactly wrong just after importing a world,
    when its backlog is largest and no campaign exists yet.
    """
    _world_with_a_character(client)
    body = _todo(client, "")
    ids = {c["id"] for c in body["chores"]}
    assert "world-taglines" in ids
    assert body["count"] == len(body["chores"])
    assert all(c["scope"] in ("world", "library") for c in body["chores"])


def test_a_campaigns_own_world_is_reported_once_not_twice(client, campaign):
    """`world-taglines` covers the worlds the open campaign does NOT use.

    Its own world is already answered by `taglines`, over the effective
    copy-on-write roster -- the more accurate of the two, since it can see a
    tagline written campaign-side and a character the campaign deleted.
    Reporting it from both sides would double-count the character and let the
    two rows disagree.
    """
    cid, wid = campaign
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Winifred"})
    body = _todo(client, cid)
    ids = {c["id"] for c in body["chores"]}
    assert "taglines" in ids
    assert "world-taglines" not in ids

    # A second world the campaign does not use is reported, from the world side.
    _world_with_a_character(client, "Elsewhere")
    ids = {c["id"] for c in _todo(client, "")["chores"]}
    assert {"taglines", "world-taglines"} <= ids


def test_the_image_backlog_is_not_excluded_for_the_campaigns_world(client, campaign):
    """The deliberate asymmetry beside the test above.

    `world-describe` covers EVERY world including the open campaign's, because
    nothing else reports an image backlog -- so excluding it there would hide
    the backlog rather than de-duplicate it. This is the rule a later reader is
    most likely to "fix" into consistency with `world-taglines`.
    """
    _cid, wid = campaign
    ch = client.post(f"/api/worlds/{wid}/characters",
                     json={"name": "Winifred"}).json()["character"]
    _add_images(client, wid, ch, "gallery_1")

    ids = {c["id"] for c in _todo(client, "")["chores"]}
    assert "world-describe" in ids


def test_the_badge_never_disagrees_with_the_page(client, campaign):
    """The compatibility count still agrees with the live report at either scope."""
    from grimoire.routes import todo as todo_routes

    cid, wid = campaign
    ch = client.post(f"/api/worlds/{wid}/characters",
                     json={"name": "Winifred"}).json()["character"]
    _add_images(client, wid, ch, "gallery_1")
    _world_with_a_character(client, "Elsewhere")

    for c in (cid, ""):
        assert todo_routes.badge_count(c) == todo_routes.live(c)["count"], c


def test_a_world_that_cannot_be_read_does_not_break_the_global_page(client, campaign,
                                                                     monkeypatch):
    """One unreadable world costs only its row, not the whole global report."""
    _cid, wid = campaign
    ch = client.post(f"/api/worlds/{wid}/characters",
                     json={"name": "Winifred"}).json()["character"]
    _add_images(client, wid, ch, "gallery_1")
    bad_wid, _ = _world_with_a_character(client, "Unreadable")
    bad_root = store.worlds.paths.world_root(bad_wid)

    real = store.image_descriptions.undescribed_count

    def explode(root, base="characters"):
        if root == bad_root:
            raise PermissionError(f"cannot read {root}")
        return real(root, base)

    monkeypatch.setattr(store.image_descriptions, "undescribed_count", explode)

    rows = _todo(client, "")["chores"]
    assert "world-describe" in {row["id"] for row in rows}


def test_expanding_a_world_chore_lists_worlds_not_images(client, campaign):
    """A row per image would be hundreds of rows and would hit `ITEM_CAP`.

    The world is the grain the fix is applied at -- the describe queue runs over
    a whole world from its cast page -- so it is the grain the reader gets.
    """
    cid, wid = campaign
    ch = client.post(f"/api/worlds/{wid}/characters",
                     json={"name": "Winifred"}).json()["character"]
    _add_images(client, wid, ch, "gallery_1", "gallery_2")

    r = client.get("/api/todo/world-describe/items", params={"campaign": cid})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["id"] for i in items] == [wid]
    assert items[0]["detail"] == "2 images"


def test_a_world_chore_expands_with_no_campaign_open(client):
    """The items route used to answer empty without a campaign. A library chore
    has instances regardless, and an expansion that silently returns nothing is
    a long way from the rule that caused it."""
    _world_with_a_character(client)
    r = client.get("/api/todo/world-taglines/items", params={"campaign": ""})
    assert r.status_code == 200, r.text
    labels = [i["label"] for i in r.json()["items"]]
    assert labels == ["Winifred"]


def test_a_world_chore_can_be_ignored(client):
    """The ignore set is keyed by chore id and the new ids are in `KNOWN`."""
    _world_with_a_character(client)
    assert "world-taglines" in {c["id"] for c in _todo(client, "")["chores"]}
    assert client.put("/api/todo/world-taglines/ignored",
                      json={"ignored": True}).status_code == 200
    body = _todo(client, "")
    assert "world-taglines" not in {c["id"] for c in body["chores"]}
    assert "world-taglines" in {c["id"] for c in body["ignored"]}
    assert body["count"] == len(body["chores"])


# ---- the headings, and who decides their order ----
def _with_two_groups(client, cid: str) -> None:
    """Enough outstanding work that more than one heading is on the page.

    Two open scenes provide outstanding story work; ordering tests add other
    kinds of work because a fixture with one chore proves nothing about order.
    """
    for title in ("The Lower Step", "The Weir"):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": title})


def test_groups_keep_their_thematic_order_in_campaign_and_global_reports(client, campaign):
    """The order must not move when the library does.

    The same themes apply to campaign and world records. Avatars belong with
    character identity, while covers belong with artwork. A group's position
    must not change with the scope or with the first chore emitted under it.
    """
    cid, wid = campaign
    _with_two_groups(client, cid)
    _world_character(client, wid, "Mara")
    store.covers.delete_cover(cid)
    expected = {
        "open-scenes": "Story & continuity",
        "anchors": "Character & voice",
        "taglines": "Character & voice",
        "avatars": "Character & voice",
        "cover": "Artwork",
    }
    for scope in (cid, ""):
        body = _todo(client, scope)
        wanted = expected if scope else {
            **expected,
            "world-anchors": "Character & voice",
            "world-taglines": "Character & voice",
            "world-avatars": "Character & voice",
            # No embeddings are configured and a campaign exists (§18.1).
            "embeddings": "Housekeeping",
        }
        assert {c["id"]: c["group"] for c in body["chores"]} == wanted
        assert body["groups"] == ["Story & continuity", "Character & voice", "Artwork",
                                  *([] if scope else ["Housekeeping"])]
        assert body["count"] == len(wanted)


def test_every_group_a_builder_can_emit_is_declared(client):
    """A heading missing from `GROUP_ORDER` is appended, not dropped -- but it
    should not be missing, and this is what says so.

    Read off the builders rather than off one library's output: a chore that
    only fires under conditions this test does not set up would otherwise be
    the one whose heading nobody declared.
    """
    emitted = set()
    for name in dir(todo):
        if not name.startswith("_chore_"):
            continue
        src = inspect.getsource(getattr(todo, name))
        for group in re.findall(r'"group":\s*"([^"]+)"', src):
            emitted.add(group)

    assert emitted, "no chore builder was found -- this test has gone stale"
    assert emitted <= set(todo.GROUP_ORDER), (
        f"undeclared heading(s): {sorted(emitted - set(todo.GROUP_ORDER))}")


def test_an_ignored_chore_takes_its_heading_with_it(client, campaign):
    """A heading over nothing is a heading the reader has to check.

    An ignored chore is counted nowhere -- that is the whole point of ignoring
    one -- so the group it was the only member of goes quiet too.
    """
    cid, _ = campaign
    _with_two_groups(client, cid)
    body = client.get("/api/todo", params={"campaign": cid}).json()
    assert body["chores"], "the fixture produced nothing to ignore"
    victim = body["chores"][0]
    same_group = [c for c in body["chores"] if c["group"] == victim["group"]]

    client.put(f"/api/todo/{victim['id']}/ignored", json={"ignored": True, "campaign": cid})
    after = client.get("/api/todo", params={"campaign": cid}).json()

    if len(same_group) == 1:
        assert victim["group"] not in after["groups"]
    else:
        assert victim["group"] in after["groups"]


def _todo_lib_png() -> bytes:
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def test_a_library_only_backlog_still_raises_the_describe_chore(client):
    """The count, the badge and the CHEAP presence probe are three call sites,
    and the probe is the one that decides whether the chore is computed at all.
    A world whose only undescribed art is library art exercises all three: miss
    the probe and the row never renders, however right the count is."""
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.put(f"/api/worlds/{wid}/images/coastline",
               files={"file": ("c.png", _todo_lib_png(), "image/png")})

    chores = client.get("/api/todo").json()["chores"]
    describe = next(c for c in chores if c["id"] == "world-describe")
    assert describe["n"] == 1
    # and it points where the describe QUEUE is -- `DescribeQueue` is mounted
    # only on the cast page (`components/CharacterGrid.tsx`), whose queue reads
    # `/images/undescribed` and so carries these library rows too. Pointing this
    # at the Images tab, where library art is edited, sent the reader to a page
    # with no describe queue on it at all.
    assert describe["fix_label"] == "The cast"
    assert describe["fix"].endswith(f"/worlds/{wid}")


def test_the_rail_badge_counts_library_art_too(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns",
                      json={"name": "Saltmarch Nights", "world": wid}).json()["id"]
    client.put(f"/api/worlds/{wid}/images/coastline",
               files={"file": ("c.png", _todo_lib_png(), "image/png")})

    shell = client.get(f"/api/shell?campaign={cid}").json()
    assert shell["campaign"]["images_undescribed"] == 1


def test_a_single_incomplete_scene_is_visible_in_both_scopes(client, campaign):
    cid, _ = campaign
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Saltmarch"}).json()["id"]
    for scope in (cid, ""):
        row = next(c for c in _todo(client, scope)["chores"] if c["id"] == "open-scenes")
        assert row["n"] == 1
        assert row["what"] == "1 incomplete scene"
        assert row["campaign_id"] == cid
    items = _items(client, "open-scenes", cid)
    assert items["total"] == 1
    assert items["items"][0]["fix"] == f"/campaigns/{cid}/scenes/{sid}"


def test_avatar_gaps_follow_the_default_version_and_link_to_the_character(client, campaign):
    cid, wid = campaign
    aid = _world_character(client, wid, "Mara")
    root = store.worlds.paths.world_root(wid)
    other = store.characters.create_version(root, aid, "Seraphine", store.characters.blank_card("Mara"))
    store.assets.put_image(root, aid, other, "avatar", _png(), "png")
    _add_images(client, wid, aid, "gallery_1")
    row = next(c for c in _todo(client, cid)["chores"] if c["id"] == "avatars")
    assert row["n"] == 1
    assert row["what"] == "1 character without an avatar"
    items = _items(client, "avatars", cid)
    assert items["total"] == 1
    assert items["items"][0]["label"] == "Mara"
    assert items["items"][0]["fix"] == f"/campaigns/{cid}/world/characters/{aid}"
    store.assets.put_image(root, aid, "default", "avatar", _png(), "png")
    assert "avatars" not in {c["id"] for c in _todo(client, cid)["chores"]}
    assert _items(client, "avatars", cid)["total"] == 0


def test_campaign_avatar_gaps_honor_image_tombstones_and_record_deletions(client, campaign):
    cid, wid = campaign
    aid = _world_character(client, wid, "Mara")
    root = store.worlds.paths.world_root(wid)
    store.assets.put_image(root, aid, "default", "avatar", _png(), "png")
    store.overlay.delete_image(cid, aid, "default", "avatar")
    assert [i["id"] for i in _items(client, "avatars", cid)["items"]] == [aid]
    # The world's avatar still exists; its own report must see it.
    assert _items(client, "world-avatars", "")["total"] == 0
    store.assets.put_image(store.campaigns.paths.campaign_root(cid), aid,
                           "default", "avatar", _png(), "png")
    assert _items(client, "avatars", cid)["total"] == 0
    store.assets.delete_image(store.campaigns.paths.campaign_root(cid), aid, "default", "avatar")
    assert [i["id"] for i in _items(client, "avatars", cid)["items"]] == [aid]
    store.overlay.add_deleted(cid, f"characters/{aid}")
    assert _items(client, "avatars", cid)["total"] == 0


def test_a_detached_character_does_not_take_an_unrelated_world_avatar(client, campaign):
    cid, wid = campaign
    aid, vid = store.overlay.create_character(cid, "Winifred")
    # Simulate a later world record reusing the slug. The HTTP create route
    # refuses that collision; hand-managed stores can still contain it.
    world_aid, _ = store.characters.create_character(store.worlds.paths.world_root(wid), "Winifred")
    assert aid == world_aid
    store.assets.put_image(store.worlds.paths.world_root(wid), aid, vid,
                           "avatar", _png(), "png")
    assert [i["id"] for i in _items(client, "avatars", cid)["items"]] == [aid]


def test_world_avatar_gaps_work_without_a_campaign_and_keep_worlds_distinct(client):
    first, aid = _world_with_a_character(client, "Realm")
    second, _ = _world_with_a_character(client, "Saltmarch")
    row = next(c for c in _todo(client, "")["chores"] if c["id"] == "world-avatars")
    assert row["n"] == 2
    assert row["what"] == "2 characters without an avatar"
    items = _items(client, "world-avatars", "")
    assert items["total"] == 2
    assert {i["fix"] for i in items["items"]} == {
        f"/worlds/{wid}/characters/{aid}" for wid in (first, second)}
    assert len({i["id"] for i in items["items"]}) == 2
    store.assets.put_image(store.worlds.paths.world_root(first), aid, "default",
                           "avatar", _png(), "png")
    assert _items(client, "world-avatars", "")["total"] == 1


def test_campaign_avatar_rows_do_not_duplicate_their_world_in_a_scoped_report(client, campaign):
    cid, wid = campaign
    _world_character(client, wid, "Mara")
    assert "avatars" in {c["id"] for c in _todo(client, cid)["chores"]}
    global_rows = _todo(client, "")["chores"]
    assert "world-avatars" in {c["id"] for c in global_rows}
    assert "world-avatars" not in {c["id"] for c in _todo(client, cid)["chores"]}


def test_campaign_cover_gaps_are_local_and_clear_when_uploaded(client, campaign):
    cid, _ = campaign
    store.covers.delete_cover(cid)
    for scope in (cid, ""):
        row = next(c for c in _todo(client, scope)["chores"] if c["id"] == "cover")
        assert row["n"] == 1
        assert row["campaign_id"] == cid
        assert row["fix"] == f"/campaigns/{cid}"
    items = _items(client, "cover", cid)
    assert items["total"] == 1
    assert items["items"][0]["fix"] == f"/campaigns/{cid}"
    store.covers.put_cover(cid, _png(), "png")
    assert "cover" not in {c["id"] for c in _todo(client, cid)["chores"]}
    assert _items(client, "cover", cid)["total"] == 0


def test_world_cover_gaps_include_campaign_worlds_and_link_to_the_images_page(client, campaign):
    cid, wid = campaign
    store.covers.delete_world_cover(wid)
    row = next(c for c in _todo(client, "")["chores"] if c["id"] == "world-covers")
    assert row["n"] == 1
    assert row["what"] == "1 world without a cover"
    items = _items(client, "world-covers", "")
    assert items["total"] == 1
    assert items["items"][0]["fix"] == f"/worlds/{wid}/images"
    assert "world-covers" not in {c["id"] for c in _todo(client, cid)["chores"]}
    store.covers.put_world_cover(wid, _png(), "png")
    assert "world-covers" not in {c["id"] for c in _todo(client, "")["chores"]}
    assert _items(client, "world-covers", "")["total"] == 0


@pytest.mark.parametrize("chore_id", ["avatars", "world-avatars", "cover", "world-covers", "world-subjects"])
def test_image_gap_chores_can_be_ignored_and_restored(client, campaign, chore_id):
    cid, wid = campaign
    _world_character(client, wid, "Mara")
    store.covers.delete_cover(cid)
    store.covers.delete_world_cover(wid)
    root = store.worlds.paths.world_root(wid)
    gid = store.greetings.create_greeting(root, "Saltmarch", "", "", "")
    store.assets.put_image(root, gid, "default", "art_1", _png(), "png", base="greetings")
    scope = cid if chore_id in ("avatars", "cover") else ""
    assert chore_id in {c["id"] for c in _todo(client, scope)["chores"]}
    for on in (True, False):
        body = {"ignored": on, "campaign": scope}
        assert client.put(f"/api/todo/{chore_id}/ignored", json=body).status_code == 200
        report = _todo(client, scope)
        assert (chore_id in {c["id"] for c in report["ignored"]}) == on
        assert (chore_id in {c["id"] for c in report["chores"]}) != on


def test_greeting_images_need_an_assignment_until_characters_or_none_are_saved(client, campaign):
    _cid, wid = campaign
    aid = _world_character(client, wid, "Mara")
    root = store.worlds.paths.world_root(wid)
    gid = store.greetings.create_greeting(root, "Saltmarch", aid, "default", "")
    # Three different pictures: one picture is one object, answered once.
    for image, color in (("art_1", "black"), ("art_2", "white"), ("art_3", "red")):
        store.assets.put_image(root, gid, "default", image, _png(color), "png", base="greetings")
    store.image_subjects.set_image_subjects(root, gid, "art_1", [aid])
    store.image_subjects.set_image_subjects(root, gid, "art_2", [])
    row = next(c for c in _todo(client, "")["chores"] if c["id"] == "world-subjects")
    assert row["n"] == 1
    assert row["what"] == "1 greeting image without a character assignment"
    items = _items(client, "world-subjects", "")
    assert items["total"] == 1
    assert items["items"][0]["label"] == "Saltmarch"
    assert "art_3" in items["items"][0]["detail"]
    assert items["items"][0]["fix"] == f"/worlds/{wid}/greetings/{gid}"
    # Use the existing API behind the tagging queue's "No subjects" action.
    saved = client.put(f"/api/worlds/{wid}/greetings/{gid}/images/art_3/subjects",
                       json={"subjects": []})
    assert saved.status_code == 200, saved.text
    assert "world-subjects" not in {c["id"] for c in _todo(client, "")["chores"]}
    assert _items(client, "world-subjects", "")["total"] == 0


def test_greeting_image_assignment_items_distinguish_worlds_and_removed_images(client):
    made = []
    for name in ("Realm", "Saltmarch"):
        wid = client.post("/api/worlds", json={"name": name}).json()["id"]
        root = store.worlds.paths.world_root(wid)
        gid = store.greetings.create_greeting(root, "Mara", "", "", "")
        for image in ("art_1", "art_2"):
            store.assets.put_image(root, gid, "default", image, _png(), "png", base="greetings")
        store.assets.delete_image(root, gid, "default", "art_2", base="greetings")
        made.append((wid, gid))
    row = next(c for c in _todo(client, "")["chores"] if c["id"] == "world-subjects")
    assert row["n"] == 2
    items = _items(client, "world-subjects", "")
    assert items["total"] == 2
    assert len({i["id"] for i in items["items"]}) == 2
    assert {i["fix"] for i in items["items"]} == {
        f"/worlds/{wid}/greetings/{gid}" for wid, gid in made}


def test_owed_is_pinned_for_an_ordinary_campaign(client, campaign):
    """A characterization of the `owed` chore for a campaign with no aliases:
    its count, its sentence, and each instance's exact strings. A commitment
    counts when its `due` is non-empty, parseable or not -- Todo does no
    calendar work -- and one with no due does not count at all."""
    cid, _ = campaign
    first = store.scenes.create_scene(cid, "The Pier at Dusk")
    second = store.scenes.create_scene(cid, "Saltmarch Eve")
    store.commitments.set_movement(cid, "maras-oath", "Mara's oath", "promise", "open",
                                   "2026-06-01", "Mara swore it on the quay.", first)
    store.commitments.set_movement(cid, "winifreds-promise", "Winifred's promise", "promise",
                                   "open", "before the bells stop", "", second)
    store.commitments.set_movement(cid, "seraphines-favour", "Seraphine's favour", "promise",
                                   "open", "", "Seraphine owes one.", second)

    chore = next(c for c in _todo(client, cid)["chores"] if c["id"] == "owed")
    assert chore["n"] == 2
    assert chore["what"] == "2 open commitments with a deadline"

    body = _items(client, "owed", cid)
    assert body["total"] == 2
    assert [{k: i[k] for k in ("id", "label", "detail")} for i in body["items"]] == [
        {"id": "maras-oath", "label": "Mara's oath",
         "detail": "due 2026-06-01 · promise · Mara swore it on the quay."},
        {"id": "winifreds-promise", "label": "Winifred's promise",
         "detail": "due before the bells stop · promise"},
    ]


def _alias(cid: str, src: str, to: str) -> None:
    continuity_doc.put_alias(cid, src, {"to": to, "created": "", "source": "manual",
                                        "note": ""})


def _owed(client, cid: str) -> dict | None:
    return next((c for c in _todo(client, cid)["chores"] if c["id"] == "owed"), None)


def test_owed_counts_canonical_commitments(client, campaign):
    """A merged-away source is never a second count, and its due is never
    inherited: the canonical's own due is the one that is stated."""
    cid, _ = campaign
    sid = store.scenes.create_scene(cid, "The Pier at Dusk")
    store.commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                                   "2026-06-01", "Mara swore it on the quay.", sid)
    store.commitments.set_movement(cid, "winifred-s-promise", "Winifred's promise", "promise",
                                   "open", "2026-06-03", "", sid)
    store.commitments.set_movement(cid, "seraphine-s-favour", "Seraphine's favour", "promise",
                                   "open", "", "Seraphine owes one.", sid)
    _alias(cid, "commitment:mara-s-oath", "commitment:winifred-s-promise")

    chore = _owed(client, cid)
    assert chore is not None
    assert chore["n"] == 1
    assert chore["what"] == "1 open commitment with a deadline"
    items = _items(client, "owed", cid)["items"]
    assert [i["id"] for i in items] == ["winifred-s-promise"]

    store.commitments.set_movement(cid, "winifred-s-promise", "", "", "", "", "", sid)
    assert _owed(client, cid) is None
    assert _items(client, "owed", cid)["items"] == []


def test_owed_counts_stated_dues_only(client, campaign):
    """Todo's set is not pressure's: a link deadline is not counted (links reach
    Todo with the pressure split), and a free-text due is."""
    cid, _ = campaign
    sid = store.scenes.create_scene(cid, "The Pier at Dusk")
    store.commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                                   "", "Mara swore it on the quay.", sid)
    store.events.create(cid, "The coronation", "2026-06-01")
    continuity_doc.put_link(cid, "l1", {"a": "commitment:mara-s-oath",
                                        "b": "event:the-coronation", "relation": "before",
                                        "created": "", "scene": "", "note": ""})
    assert _owed(client, cid) is None

    store.commitments.set_movement(cid, "winifred-s-promise", "Winifred's promise", "promise",
                                   "open", "before the bells stop", "", sid)
    chore = _owed(client, cid)
    assert chore is not None
    assert (chore["n"], chore["what"]) == (1, "1 open commitment with a deadline")
    assert [i["id"] for i in _items(client, "owed", cid)["items"]] == ["winifred-s-promise"]


def test_owed_does_no_calendar_work(client, campaign, monkeypatch):
    """The global To do page runs every builder for every campaign, and a
    provider is plugin code: `owed` resolves none."""
    cid, _ = campaign
    sid = store.scenes.create_scene(cid, "The Pier at Dusk")
    store.commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                                   "2026-06-01", "", sid)
    store.events.create(cid, "The coronation", "2026-06-03")
    continuity_doc.put_link(cid, "l1", {"a": "commitment:mara-s-oath",
                                        "b": "event:the-coronation", "relation": "by",
                                        "created": "", "scene": "", "note": ""})
    calls: list[tuple] = []
    real = store.calendars.primary_provider

    def recorder(*a, **kw):
        calls.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(store.calendars, "primary_provider", recorder)
    assert _owed(client, cid) is not None
    assert _items(client, "owed", cid)["total"] == 1
    assert calls == []


def test_owed_chore_and_items_agree(client, campaign):
    cid, _ = campaign
    sid = store.scenes.create_scene(cid, "The Pier at Dusk")
    for mid, title, due in (("mara-s-oath", "Mara's oath", "2026-06-01"),
                            ("winifred-s-promise", "Winifred's promise", "2026-06-03"),
                            ("seraphine-s-favour", "Seraphine's favour", "soon")):
        store.commitments.set_movement(cid, mid, title, "promise", "open", due, "", sid)
    _alias(cid, "commitment:mara-s-oath", "commitment:winifred-s-promise")
    chore = _owed(client, cid)
    assert chore is not None
    assert chore["n"] == _items(client, "owed", cid)["total"] == 2


def test_a_closed_branch_is_not_an_open_scene(client, campaign):
    cid, _ = campaign
    a = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Mara"}).json()["id"]
    b = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Winifred"}).json()["id"]
    g = store.scenes.ensure_identity(cid, a)
    store.scenes.write.set_branch_keys(cid, b, g, of=g)
    store.scenes.mark_absorbed(cid, a, "It ended.", "It ended, at length.")
    assert "open-scenes" not in {row["id"] for row in _todo(client, cid)["chores"]}
    items = client.get("/api/todo/open-scenes/items", params={"campaign": cid}).json()
    assert items["items"] == []


# ---- §18: the semantic-matching setup note and the continuity review chores ----


_EMBEDDINGS = {
    "id": "embeddings", "scope": "library", "group": "Housekeeping", "severity": "note", "n": 1,
    "what": "Semantic matching is not configured",
    "why": "Grimoire still uses basic matching to find possible overlaps in your ledgers. "
           "Semantic matching improves detection when the same story business is phrased "
           "differently.",
    "fix": "/models/role/embedding", "fix_label": "Embeddings",
}

_THREADS = {
    "mara-s-map": "Mara's map",
    "winifred-s-chart": "Winifred's chart",
    "seraphine-s-letter": "Seraphine's letter",
    "seraphine-s-reply": "Seraphine's reply",
    "saltmarch-bells": "The Saltmarch bells",
    "saltmarch-tide": "The Saltmarch tide",
    "realm-crown": "The Realm's crown",
    "the-coronation": "The coronation",
    "mara-s-errand": "Mara's errand",
    "winifred-s-errand": "Winifred's errand",
}

MAP, CHART = "thread:mara-s-map", "thread:winifred-s-chart"
OATH = "commitment:mara-s-oath"


def _configure_embeddings(client, depth: str = "2") -> None:
    conn = store.llm_connections.create_connection(
        "openai_compatible", "Vectors", base_url="https://vectors.example/v1",
        api_key="sk-x")
    got = client.put(f"/api/llm-connections/{conn}/facts",
                     json={"model": "embed-1", "post_process": "none"})
    assert got.status_code == 200, got.text
    inference_fixtures.put_settings(client, {"roles": {"embedding": {"selection": {
        "provider": conn, "model": "embed-1"}}}, "confirm_embedding": True})
    store.config.write_config(semantic_recall_depth=depth)


def _run(client) -> tuple[str, str]:
    """A campaign whose id is `run`, a scene, and the ten threads above."""
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    assert cid == "run"
    sid = store.scenes.create_scene(cid, "Saltmarch docks")
    for pid, title in _THREADS.items():
        store.plot.set_movement(cid, pid, title, "open", f"{title} came up.", sid)
    store.commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                                   "", "Mara swore it on the quay.", sid)
    return cid, sid


def _proposal(decision: str) -> dict:
    return {"decision": decision, "from": "", "to": "", "relation": "", "status": "",
            "reason": "", "evidence_scenes": []}


def _seed(cid: str, *found: tuple) -> list[str]:
    """Cache each `(kind, refs[, proposal])` with its CURRENT fingerprint, as a
    sweep that just ran would have; returns the candidate ids in order."""
    current = pending.Current.load(cid)
    data = candidates.empty()
    keys = []
    for kind, refs, *rest in found:
        key = canon.candidate_id(kind, refs)
        data["records"][key] = {"kind": kind, "refs": list(refs),
                                "fingerprint": pending.fingerprint(current, kind, refs),
                                "signals": {}, "proposal": rest[0] if rest else None,
                                "created": "2026-10-01T00:00:00Z"}
        keys.append(key)
    candidates.write(cid, data)
    return keys


def _chore(client, cid: str, chore_id: str) -> dict | None:
    return next((c for c in _todo(client, cid)["chores"] if c["id"] == chore_id), None)


def test_missing_embeddings_shows_one_library_chore_on_the_global_page(client, campaign):
    cid, _ = campaign
    rows = [c for c in _todo(client, "")["chores"] if c["id"] == "embeddings"]
    assert rows == [_EMBEDDINGS]
    assert _chore(client, cid, "embeddings") is None
    items = _items(client, "embeddings", "")
    assert items["items"] == [{"id": "embeddings", "label": "Semantic matching",
                               "detail": "No embeddings connection and model are set",
                               "fix": "/models/role/embedding"}]


def test_the_todo_embeddings_links_go_to_models(client, campaign):
    """The embedding model is a role on the Models screen now (slice C,
    Task 8): the chore and its item both link to it."""
    chore = next(c for c in _todo(client, "")["chores"] if c["id"] == "embeddings")
    assert chore["fix"] == "/models/role/embedding"
    assert [i["fix"] for i in _items(client, "embeddings", "")["items"]] == [
        "/models/role/embedding"]


def test_no_campaign_no_embeddings_chore(client):
    assert "embeddings" not in {c["id"] for c in _todo(client, "")["chores"]}


def test_embeddings_with_recall_depth_zero_show_no_chore(client, campaign):
    """It asks whether a connection and model are set, not whether recall
    uses them: semantic matching in a sweep does not read the recall depth."""
    _configure_embeddings(client, depth="0")
    assert store.embed_space.resolve() is not None
    assert "embeddings" not in {c["id"] for c in _todo(client, "")["chores"]}


def test_a_raising_resolve_is_not_not_configured(client, campaign, monkeypatch):
    def broken(*_a, **_kw):
        raise OSError("the store is mid-sync")

    monkeypatch.setattr(store.embed_space, "resolve", broken)
    assert "embeddings" not in {c["id"] for c in _todo(client, "")["chores"]}


def test_no_embeddings_still_shows_continuity_chores(client):
    cid, _ = _run(client)
    _seed(cid, ("possible_duplicate", [MAP, CHART]))
    assert store.embed_space.resolve() is None
    chore = _chore(client, cid, "continuity-overlaps")
    assert chore is not None
    assert {k: chore[k] for k in ("id", "scope", "group", "severity", "n", "what",
                                  "fix", "fix_label")} == {
        "id": "continuity-overlaps", "scope": "campaign", "group": "Continuity",
        "severity": "note", "n": 1, "what": "1 possible overlap to review",
        "fix": "/campaigns/run/ledger/continuity/overlaps", "fix_label": "Continuity review"}
    assert chore["why"]
    assert "embeddings" in {c["id"] for c in _todo(client, "")["chores"]}


def test_continuity_counts_come_from_the_cache_after_the_live_filter(client):
    """Live, suppressed, stale, merged-away and already-closed: one counts."""
    cid, sid = _run(client)
    _seed(cid,
          ("possible_duplicate", [MAP, CHART]),
          ("possible_duplicate", ["thread:seraphine-s-letter", "thread:seraphine-s-reply"]),
          ("possible_duplicate", ["thread:saltmarch-bells", "thread:saltmarch-tide"]),
          ("possible_duplicate", ["thread:realm-crown", "thread:the-coronation"]),
          ("possible_thread_closure", ["thread:winifred-s-errand"]))
    refs = ["thread:seraphine-s-letter", "thread:seraphine-s-reply"]
    fp = pending.fingerprint(pending.Current.load(cid), "possible_duplicate", refs)
    continuity_doc.put_suppression(cid, fp, {"kind": "possible_duplicate", "refs": refs,
                                             "decision": "dismiss", "created": ""})
    store.plot.set_movement(cid, "saltmarch-tide", "The Saltmarch spring tide", "", "", sid)
    _alias(cid, "thread:realm-crown", "thread:mara-s-errand")
    store.plot.set_movement(cid, "winifred-s-errand", "", "closed", "", sid)
    assert len(candidates.read(cid)["records"]) == 5

    for scope in (cid, ""):
        chore = _chore(client, scope, "continuity-overlaps")
        assert chore is not None
        assert (chore["n"], chore["what"]) == (1, "1 possible overlap to review")
        assert _chore(client, scope, "continuity-closures") is None


def test_a_pair_naming_one_record_twice_is_not_counted(client):
    """A hand-edited duplicate whose two refs are one record is no finding."""
    cid, _ = _run(client)
    _seed(cid, ("possible_duplicate", [MAP, MAP]))
    assert candidates.read(cid)["records"] == {}
    for scope in (cid, ""):
        assert _chore(client, scope, "continuity-overlaps") is None
    assert _items(client, "continuity-overlaps", cid)["items"] == []


def test_absent_cache_shows_no_continuity_chore(client):
    cid, _ = _run(client)
    assert not (store.campaigns.paths.campaign_root(cid) / "continuity_candidates.json").exists()
    ids = {c["id"] for c in _todo(client, cid)["chores"]}
    assert not ids & {"continuity-overlaps", "continuity-closures"}
    assert _items(client, "continuity-overlaps", cid)["items"] == []


class _Raising:
    def __getattr__(self, name):
        raise AssertionError("Todo must not reach an embeddings client")


def test_todo_makes_no_embedding_calendar_or_client_call(client, monkeypatch):
    """§25.4: the continuity chores read the cache through the live filter and
    nothing else -- no embedding, calendar, involvement or chronicle work."""
    cid, _ = _run(client)
    _seed(cid, ("possible_duplicate", [MAP, CHART]),
          ("possible_commitment_resolution", [OATH], _proposal("fulfilled")))

    def refuse(*_a, **_kw):
        raise AssertionError("Todo must not do this work")

    monkeypatch.setattr(similarity, "_CLIENT", _Raising())
    monkeypatch.setattr(store.calendars, "primary_provider", refuse)
    monkeypatch.setattr(involvement, "of", refuse)
    monkeypatch.setattr(store.chronicle, "read_chronicle", refuse)

    for scope in (cid, ""):
        ids = {c["id"] for c in _todo(client, scope)["chores"]}
        assert {"continuity-overlaps", "continuity-closures"} <= ids
    for chore_id in ("continuity-overlaps", "continuity-closures"):
        assert _items(client, chore_id, cid)["total"] == 1


def test_continuity_items_link_to_their_group_and_id(client):
    """Review Focus 1: an item opens its own finding in its own group."""
    cid, _ = _run(client)
    pair, resolve = _seed(cid, ("possible_duplicate", [MAP, CHART]),
                          ("possible_commitment_resolution", [OATH]))
    [item] = _items(client, "continuity-overlaps", cid)["items"]
    assert item["id"] == pair
    assert item["fix"] == f"/campaigns/run/ledger/continuity/overlaps/{pair}"
    assert "Mara's map" in item["label"] and "Winifred's chart" in item["label"]
    [item] = _items(client, "continuity-closures", cid)["items"]
    assert item["id"] == resolve
    assert item["fix"] == f"/campaigns/run/ledger/continuity/resolutions/{resolve}"
    assert item["label"] == "Mara's oath"


def test_a_resolutions_only_closures_chore_opens_resolutions(client):
    """The chore counts both lifecycle kinds; with no thread closure among them
    it must not land the reader on an empty group."""
    cid, _ = _run(client)
    _seed(cid, ("possible_commitment_resolution", [OATH]))
    chore = _chore(client, cid, "continuity-closures")
    assert chore is not None
    assert (chore["n"], chore["what"]) == (1, "1 record that may be finished")
    assert chore["fix"] == "/campaigns/run/ledger/continuity/resolutions"

    _seed(cid, ("possible_commitment_resolution", [OATH]),
          ("possible_thread_closure", [MAP]))
    chore = _chore(client, cid, "continuity-closures")
    assert chore is not None
    assert (chore["n"], chore["what"]) == (2, "2 records that may be finished")
    assert chore["fix"] == "/campaigns/run/ledger/continuity/closures"


def test_continuity_item_details_use_hedged_labels(client):
    """Decision 25: a raw decision word never reaches the page."""
    cid, _ = _run(client)
    _seed(cid, ("possible_duplicate", [MAP, CHART], _proposal("duplicate")),
          ("possible_thread_closure", ["thread:mara-s-errand"]))
    [item] = _items(client, "continuity-overlaps", cid)["items"]
    assert "Suggested: same business (merge)" in item["detail"]
    assert "duplicate" not in item["detail"]
    [item] = _items(client, "continuity-closures", cid)["items"]
    assert item["detail"]
    assert "Suggested" not in item["detail"]


def test_owed_items_link_to_their_commitment_row(client):
    cid, sid = _run(client)
    store.commitments.set_movement(cid, "mara-s-oath", "", "", "", "2026-06-01", "", sid)
    chore = _owed(client, cid)
    assert chore is not None
    assert chore["fix"] == "/campaigns/run/ledger/commitments"
    [item] = _items(client, "owed", cid)["items"]
    assert item["fix"] == "/campaigns/run/ledger/commitments/" + quote("mara-s-oath", safe="")


def test_ledger_href_encodes_every_segment():
    assert todo._ledger_href("run", "commitments", "mara/oath:2") == \
        "/campaigns/run/ledger/commitments/mara%2Foath%3A2"
    assert todo._ledger_href("a b", "continuity", "overlaps") == \
        "/campaigns/a%20b/ledger/continuity/overlaps"
    assert todo._ledger_href("run", "") == "/campaigns/run/ledger"


def test_a_hand_edited_cache_leaves_the_todo_page_200(client):
    cid, _ = _run(client)
    [good] = _seed(cid, ("possible_duplicate", [MAP, CHART], _proposal("duplicate")))
    path = store.campaigns.paths.campaign_root(cid) / "continuity_candidates.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    refs = ["thread:saltmarch-bells", "thread:saltmarch-tide"]
    data["records"][canon.candidate_id("possible_duplicate", refs)] = {
        "kind": "possible_duplicate", "refs": refs, "fingerprint": "hand-edited",
        "signals": [], "proposal": {"decision": 3}, "created": ""}
    path.write_text(json.dumps(data), encoding="utf-8")

    for scope in (cid, ""):
        chore = _chore(client, scope, "continuity-overlaps")
        assert chore is not None and chore["n"] == 1
    assert [i["id"] for i in _items(client, "continuity-overlaps", cid)["items"]] == [good]


@pytest.mark.parametrize("chore_id", ["continuity-overlaps", "continuity-closures"])
def test_chore_and_items_agree(client, chore_id):
    cid, sid = _run(client)
    _seed(cid,
          ("possible_duplicate", [MAP, CHART]),
          ("possible_duplicate", ["thread:saltmarch-bells", "thread:saltmarch-tide"]),
          ("possible_duplicate", ["thread:realm-crown", "thread:the-coronation"]),
          ("possible_thread_closure", ["thread:winifred-s-errand"]),
          ("possible_thread_closure", ["thread:mara-s-errand"]),
          ("possible_commitment_resolution", [OATH]))
    store.plot.set_movement(cid, "saltmarch-tide", "The Saltmarch spring tide", "", "", sid)
    store.plot.set_movement(cid, "winifred-s-errand", "", "closed", "", sid)
    chore = _chore(client, cid, chore_id)
    assert chore is not None
    assert chore["n"] == _items(client, chore_id, cid)["total"] == 2


def test_continuity_chores_can_be_ignored(client):
    cid, _ = _run(client)
    _seed(cid, ("possible_duplicate", [MAP, CHART]))
    r = client.put("/api/todo/continuity-overlaps/ignored",
                   json={"ignored": True, "campaign": cid})
    assert r.status_code == 200, r.text
    assert "continuity-overlaps" in {c["id"] for c in _todo(client, cid)["ignored"]}
    assert client.put("/api/todo/embeddings/ignored",
                      json={"ignored": True}).status_code == 200


def test_unpriced_fix_opens_pricing(client, monkeypatch):
    monkeypatch.setattr(store.usage, "unpriced_models",
                        lambda: [{"model": "z-ai/glm", "calls": 3}])
    chore = next(c for c in _todo(client, "")["chores"] if c["id"] == "unpriced")
    assert chore["fix"] == "/config?section=pricing"
    assert [i["fix"] for i in _items(client, "unpriced", "")["items"]] == [
        "/config?section=pricing"]


def _current() -> None:
    """Stamp the store at the current model-settings format: a model's own
    rates can be written only there (`PUT .../facts`)."""
    store.write_config(**{inference_keys.FORMAT_KEY: inference_keys.CURRENT_FORMAT})


def _provider(name: str = "Saltmarch") -> str:
    return store.llm_connections.create_connection(
        "openai_compatible", name, base_url="http://localhost:1/v1")


def test_an_unpriced_item_opens_the_models_rates(client, monkeypatch):
    _current()
    pid = _provider()
    monkeypatch.setattr(store.usage, "unpriced_models", lambda: [
        {"model": "gpt-4o-2024-08-06", "facts_model": "gpt-4o",
         "provider_id": pid, "calls": 3}])
    [item] = _items(client, "unpriced", "")["items"]
    assert item["id"] == f"{pid}:gpt-4o-2024-08-06:gpt-4o"
    assert item["label"] == "gpt-4o-2024-08-06 asked for as gpt-4o"
    # The rates are stated under the model that was ASKED for.
    assert item["fix"] == f"/providers/{pid}/models/gpt-4o?edit=rates"


def test_an_unpriced_item_with_a_slash_in_its_model_id_keeps_its_segments(
        client, monkeypatch):
    """Encoded as `ProvidersView.modelPath` encodes it: segment by segment, so
    the `models/*` splat reads the id back whole."""
    _current()
    pid = _provider()
    monkeypatch.setattr(store.usage, "unpriced_models", lambda: [
        {"model": "vendor/model a#1(x)", "facts_model": "vendor/model a#1(x)",
         "provider_id": pid, "calls": 1}])
    [item] = _items(client, "unpriced", "")["items"]
    # `encodeURIComponent` leaves `(` and `)` alone, and so does this.
    assert item["fix"] == f"/providers/{pid}/models/vendor/model%20a%231(x)?edit=rates"


def test_an_unpriced_item_without_a_provider_opens_the_pricing_table(client, monkeypatch):
    monkeypatch.setattr(store.usage, "unpriced_models", lambda: [
        {"model": "vendor/legacy", "facts_model": "vendor/legacy",
         "provider_id": "", "calls": 2},
        {"model": "vendor/gone", "facts_model": "vendor/gone",
         "provider_id": "deleted-provider", "calls": 1}])
    items = _items(client, "unpriced", "")["items"]
    assert [i["fix"] for i in items] == ["/config?section=pricing"] * 2
    assert [i["id"] for i in items] == [":vendor/legacy:vendor/legacy",
                                        "deleted-provider:vendor/gone:vendor/gone"]


def test_item_ids_do_not_collide_for_model_names_holding_a_colon(client, monkeypatch):
    """An Ollama-style name carries `:` (`llama3:8b`), so joining the three
    parts with `:` gave two different call shapes one id -- `(p, "a:b",
    asked "c")` and `(p, "a", asked "b:c")`. Each part escapes `:` (and `%`,
    so the escape cannot collide either); a name without one reads as before."""
    monkeypatch.setattr(store.usage, "unpriced_models", lambda: [
        {"model": "vendor/a:b", "facts_model": "c", "provider_id": "", "calls": 1},
        {"model": "vendor/a", "facts_model": "b:c", "provider_id": "", "calls": 1},
        {"model": "vendor/a%3Ab", "facts_model": "c", "provider_id": "", "calls": 1}])
    ids = [i["id"] for i in _items(client, "unpriced", "")["items"]]
    assert len(set(ids)) == 3, ids
    assert ids[0] == ":vendor/a%3Ab:c"


def test_one_model_on_two_providers_is_named_once_with_distinct_item_ids(
        client, monkeypatch):
    """M6: the chore names each recorded string once; the items stay apart,
    one per provider, so each opens its own provider's rates."""
    _current()
    a, b = _provider("Saltmarch"), _provider("Winifred")
    monkeypatch.setattr(store.usage, "unpriced_models", lambda: [
        {"model": "vendor/model-a", "facts_model": "vendor/model-a",
         "provider_id": a, "calls": 2},
        {"model": "vendor/model-a", "facts_model": "vendor/model-a",
         "provider_id": b, "calls": 1}])

    chore = next(c for c in _todo(client, "")["chores"] if c["id"] == "unpriced")
    assert chore["n"] == 3
    assert chore["why"].count("vendor/model-a") == 1
    assert "more" not in chore["why"]
    assert "rates" in chore["why"] and "table" in chore["why"]

    items = _items(client, "unpriced", "")["items"]
    assert [i["id"] for i in items] == [f"{a}:vendor/model-a:vendor/model-a",
                                        f"{b}:vendor/model-a:vendor/model-a"]
    assert len({i["id"] for i in items}) == 2
    assert [i["fix"] for i in items] == [
        f"/providers/{a}/models/vendor/model-a?edit=rates",
        f"/providers/{b}/models/vendor/model-a?edit=rates"]


def test_two_names_for_one_answer_are_two_items_with_distinct_ids(client, monkeypatch):
    """One provider asked for two names that both answered as one snapshot is
    two call shapes, each opening the rates of the model it asked for -- so
    each needs its own id, and the label says which name it was asked for as."""
    _current()
    pid = _provider()
    monkeypatch.setattr(store.usage, "unpriced_models", lambda: [
        {"model": "vendor/model-a-2026-08", "facts_model": "vendor/model-a",
         "provider_id": pid, "calls": 2},
        {"model": "vendor/model-a-2026-08", "facts_model": "vendor/model-a-latest",
         "provider_id": pid, "calls": 1}])
    items = _items(client, "unpriced", "")["items"]
    assert len({i["id"] for i in items}) == 2
    assert [i["label"] for i in items] == [
        "vendor/model-a-2026-08 asked for as vendor/model-a",
        "vendor/model-a-2026-08 asked for as vendor/model-a-latest"]
    assert [i["fix"] for i in items] == [
        f"/providers/{pid}/models/vendor/model-a?edit=rates",
        f"/providers/{pid}/models/vendor/model-a-latest?edit=rates"]


def test_on_a_store_not_yet_migrated_no_rates_link_leads_to_an_editor_that_cannot_save(
        client, monkeypatch):
    """`PUT .../facts` answers 409 `not_migrated` until the store is current, so
    both chores open the pricing table instead -- which can price any model --
    and say the model's own rates arrive after the upgrade."""
    inference_fixtures.legacy_store()
    pid = store.llm_connections.create_connection(
        "openai_compatible", "Saltmarch", base_url="http://localhost:1/v1",
        model="vendor/model-a")
    store.write_config(active_connection_id=pid)
    monkeypatch.setattr(store.usage, "unpriced_models", lambda: [
        {"model": "vendor/model-a", "facts_model": "vendor/model-a",
         "provider_id": pid, "calls": 2}])

    chore = _chore(client, "", "unpriced-models")
    assert chore["fix"] == "/config?section=pricing"
    assert chore["fix_label"] == "Pricing"
    assert "after the upgrade" in chore["why"]
    [item] = _items(client, "unpriced-models", "")["items"]
    assert item["fix"] == "/config?section=pricing"

    ledger = _chore(client, "", "unpriced")
    assert "after the upgrade" in ledger["why"]
    [item] = _items(client, "unpriced", "")["items"]
    assert item["fix"] == "/config?section=pricing"


def test_on_a_store_a_newer_build_wrote_no_rates_link_is_offered(client, monkeypatch):
    """That store is already past the upgrade, and this version will never
    write its rates, so the copy says a newer version wrote it -- never "after
    the upgrade"."""
    pid = store.llm_connections.create_connection(
        "openai_compatible", "Saltmarch", base_url="http://localhost:1/v1",
        model="vendor/model-a")
    store.write_config(active_connection_id=pid)
    store.write_config(**{inference_keys.FORMAT_KEY: str(int(inference_keys.CURRENT_FORMAT) + 1)})
    monkeypatch.setattr(store.usage, "unpriced_models", lambda: [
        {"model": "vendor/model-a", "facts_model": "vendor/model-a",
         "provider_id": pid, "calls": 2}])
    [item] = _items(client, "unpriced", "")["items"]
    assert item["fix"] == "/config?section=pricing"
    ledger = _chore(client, "", "unpriced")
    assert "newer version" in ledger["why"]
    assert "after the upgrade" not in ledger["why"]
    models = _chore(client, "", "unpriced-models")
    assert models is not None
    assert models["fix"] == "/config?section=pricing"
    assert "newer version" in models["why"]
    assert "after the upgrade" not in models["why"]


# ---- models in use with no price (slice E, Task 5) ----
def _format2_role(role: str, provider: str, model: str) -> None:
    store.write_config(**{inference_keys.FORMAT_KEY: inference_keys.CURRENT_FORMAT,
                          inference_keys.role_key(role, "provider"): provider,
                          inference_keys.role_key(role, "model"): model})


MODELS_WHY = ("Their provider reports no price, and neither the model's own rates nor "
              "your pricing table covers them, so their calls are counted rather than "
              "costed. A local model is free only once you enter zero rates for it.")


def test_the_models_chore_sits_in_housekeeping_and_opens_rates(client):
    pid = _provider()
    _format2_role("primary", pid, "vendor/model a")
    chore = _chore(client, "", "unpriced-models")
    assert chore == {
        "id": "unpriced-models", "scope": "library", "group": "Housekeeping",
        "severity": "note", "n": 1, "what": "1 model in use with no price",
        "why": MODELS_WHY,
        "fix": f"/providers/{pid}/models/vendor/model%20a?edit=rates",
        "fix_label": "Set rates"}
    assert "unpriced-models" in todo.LIBRARY_IDS and "unpriced-models" in todo.ITEMS
    order = [i for i, _b in todo.LIBRARY_BUILDERS]
    assert order.index("unpriced-models") == order.index("unpriced") + 1


def test_the_models_chore_lists_each_pair(client, campaign):
    cid, _ = campaign
    a, b = _provider("Saltmarch"), _provider("Winifred")
    _format2_role("primary", a, "vendor/model-a")
    store.write_config(**{inference_keys.role_key("fast", "provider"): b,
                          inference_keys.role_key("fast", "model"): "vendor/model-a"})
    store.campaigns.set_campaign_inference(cid, {
        inference_keys.role_key("decision", "provider"): a,
        inference_keys.role_key("decision", "model"): "vendor/model-a"})

    chore = _chore(client, "", "unpriced-models")
    assert chore["n"] == 2 and chore["what"] == "2 models in use with no price"
    items = _items(client, "unpriced-models", "")["items"]
    assert items == [
        {"id": f"{a}:vendor/model-a", "label": "vendor/model-a on Saltmarch",
         "detail": "Used by Primary, Decision (A Long Run)",
         "fix": f"/providers/{a}/models/vendor/model-a?edit=rates"},
        {"id": f"{b}:vendor/model-a", "label": "vendor/model-a on Winifred",
         "detail": "Used by Fast",
         "fix": f"/providers/{b}/models/vendor/model-a?edit=rates"},
    ]


def test_the_models_chore_can_be_ignored(client):
    pid = _provider()
    _format2_role("primary", pid, "vendor/model-a")
    r = client.put("/api/todo/unpriced-models/ignored", json={"ignored": True})
    assert r.status_code == 200, r.text
    body = _todo(client, "")
    assert "unpriced-models" not in {c["id"] for c in body["chores"]}
    assert "unpriced-models" in {c["id"] for c in body["ignored"]}


@pytest.mark.parametrize("error", [
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
    store.locks.StoreBusy("config"),
])
def test_an_unreadable_config_drops_the_models_chore_not_the_page(client, monkeypatch, error):
    """M11: one unreadable `config.md` costs this chore, never `/todo`."""
    pid = _provider()
    _format2_role("primary", pid, "vendor/model-a")
    assert _chore(client, "", "unpriced-models") is not None

    def unreadable(*_a, **_k):
        raise error

    monkeypatch.setattr(store.config, "read_config", unreadable)
    body = _todo(client, "")
    assert "unpriced-models" not in {c["id"] for c in body["chores"]}
    assert _items(client, "unpriced-models", "") == {"items": [], "total": 0, "truncated": False}

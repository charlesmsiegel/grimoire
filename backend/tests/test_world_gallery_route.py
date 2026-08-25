"""`GET /worlds/{wid}/gallery` — every image in a world, in one response (#200).

The route exists so a gallery is one request rather than one per record per
version, so what it is held to here is coverage (each base reachable), honesty
(what a row says about an image is what the store says) and the two ways a
listing can offer a tile nothing will serve.
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def world(client):
    return client.post("/api/worlds", json={"name": "Realm"}).json()["id"]


def _upload(client, url):
    r = client.put(url, files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    return r


def _character(client, wid, name="Seraphine"):
    made = client.post(f"/api/worlds/{wid}/characters", json={"name": name}).json()
    return made["character"], made["version"]


def _greeting(client, wid, cid, vid, *, images=(), name="Saltmarch dawn"):
    """A greeting, plus any art it carries.

    The art goes in through the store rather than an upload route because there
    is no upload route: greeting images arrive by localization, which downloads
    what the greeting body links to (`store.localize`). Every other test that
    needs greeting art writes it the same way.
    """
    r = client.post(f"/api/worlds/{wid}/greetings",
                    json={"name": name, "character": cid, "version": vid, "body": "..."})
    assert r.status_code == 200, r.text
    gid = r.json()["id"]
    for img in images:
        store.assets.put_image(store.worlds.world_root(wid), gid, "default", img,
                               PNG, "png", base="greetings")
    return gid


def _gallery(client, wid):
    r = client.get(f"/api/worlds/{wid}/gallery")
    assert r.status_code == 200, r.text
    return r.json()


def test_unknown_world_is_a_404(client):
    assert client.get("/api/worlds/nope/gallery").status_code == 404


def test_a_world_with_no_art_lists_nothing(client, world):
    assert _gallery(client, world) == []


def test_every_base_reaches_the_listing(client, world):
    """Characters, PCs, all five entity kinds and greetings. The point of the
    route is that a reader does not have to know which of eight places a
    picture lives in, so a base missing here is the whole feature missing for
    that kind of record."""
    cid, vid = _character(client, world)
    _upload(client, f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images/gallery_1")

    pc = client.post(f"/api/worlds/{world}/pcs", json={"name": "Mara"}).json()
    _upload(client, f"/api/worlds/{world}/pcs/{pc['pc']}/versions/{pc['version']}/images/avatar")

    for kind in ("locations", "lore", "items", "groups", "creatures"):
        eid = client.post(f"/api/worlds/{world}/{kind}",
                          json={"name": f"A {kind[:-1]}"}).json()["id"]
        _upload(client, f"/api/worlds/{world}/{kind}/{eid}/images/gallery_1")

    _greeting(client, world, cid, vid, images=("gallery_1",))

    assert {row["kind"] for row in _gallery(client, world)} == {
        "characters", "pcs", "locations", "lore", "items", "groups", "creatures", "greetings"}


def test_a_row_carries_what_it_takes_to_render_and_place_the_tile(client, world):
    cid, vid = _character(client, world)
    _upload(client, f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images/gallery_1")
    (row,) = _gallery(client, world)
    assert (row["kind"], row["id"], row["vid"], row["name"]) == ("characters", cid, vid, "gallery_1")
    # The stored format, which the detail sidebar renders as a chip. Catalogued
    # and then dropped on the way out once, so it is asserted at the boundary
    # that actually serves it rather than only in the store test.
    assert row["ext"] == "png"
    # The record's display name, so a tile can say whose picture it is without
    # a second request per record.
    assert row["record_name"] == "Seraphine"
    base = f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images/gallery_1"
    # Both URLs carry `?v=`, which is what makes them cacheable immutable; a
    # grid of bare URLs revalidates every tile on every render.
    assert row["url"].startswith(f"{base}?v=")
    assert row["thumb"].startswith(f"{base}?w=320&v=")
    # And both are URLs this app actually answers.
    assert client.get(row["url"]).status_code == 200
    assert client.get(row["thumb"]).status_code == 200


def test_described_is_key_presence_not_text(client, world):
    """The distinction `image_descriptions` turns on, carried through to the
    tile: an image reviewed and deliberately left blank is finished, and a
    gallery that showed it as a gap would be re-asking a question the author
    has already answered."""
    cid, vid = _character(client, world)
    base = f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images"
    for name in ("avatar", "gallery_1", "gallery_2"):
        _upload(client, f"{base}/{name}")
    client.put(f"{base}/avatar/description", json={"description": "In half-plate."})
    client.put(f"{base}/gallery_1/description", json={"description": ""})

    rows = {r["name"]: r for r in _gallery(client, world)}
    assert (rows["avatar"]["described"], rows["avatar"]["description"]) == (True, "In half-plate.")
    assert (rows["gallery_1"]["described"], rows["gallery_1"]["description"]) == (True, "")
    assert (rows["gallery_2"]["described"], rows["gallery_2"]["description"]) == (False, "")


def test_greeting_tiles_carry_their_subjects_and_nothing_else_does(client, world):
    """Who is in the picture is the sidecar that governs greeting art, and the
    one the tagging queue works through. `None` is untagged and `[]` is "tagged,
    nobody in it" — the same absent-versus-empty distinction descriptions draw,
    and the queue empties only because of it."""
    cid, vid = _character(client, world)
    gid = _greeting(client, world, cid, vid,
                    images=("gallery_1", "gallery_2", "gallery_3"))
    base = f"/api/worlds/{world}/greetings/{gid}/images"
    client.put(f"{base}/gallery_1/subjects", json={"subjects": [cid]})
    client.put(f"{base}/gallery_2/subjects", json={"subjects": []})

    rows = {r["name"]: r for r in _gallery(client, world) if r["kind"] == "greetings"}
    assert rows["gallery_1"]["subjects"] == [cid]
    assert rows["gallery_2"]["subjects"] == []
    assert rows["gallery_3"]["subjects"] is None
    # A character's art has no subjects sidecar, so the key is absent rather
    # than present and empty — which would read as "nobody is in this".
    _upload(client, f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images/avatar")
    char_row = next(r for r in _gallery(client, world) if r["kind"] == "characters")
    assert "subjects" not in char_row


def test_a_malformed_subjects_entry_is_answered_here_because_the_queue_says_so(client, world):
    """The gallery's "unfinished" and the tagging queue's backlog have to be the
    same set, or a tile is flagged that the queue will never offer -- unfinished
    forever, with nothing the reader can do about it.

    `read_subjects` drops an entry whose value is not a list, and `untagged`
    counts raw key presence. Answered follows the QUEUE; the rendered list
    follows the tolerant read, so a value nothing can render comes back as
    "answered: nobody" rather than as a broken chip."""
    cid, vid = _character(client, world)
    gid = _greeting(client, world, cid, vid, images=("gallery_1",))
    store.image_subjects.subjects_path(store.worlds.world_root(world), gid).write_text(
        '{"gallery_1": "not-a-list"}', encoding="utf-8")

    (row,) = [r for r in _gallery(client, world) if r["kind"] == "greetings"]
    assert row["subjects"] == []
    # ...which is exactly what the queue thinks, and the two must not disagree.
    assert client.get(f"/api/worlds/{world}/subjects/untagged").json() == []


def test_one_malformed_subject_member_cannot_take_down_the_whole_gallery(client, world):
    """`read_subjects` tests membership against a SET, and an unhashable member
    raises rather than comparing false. That was survivable while every caller
    read one greeting; this route reads every greeting in the world, so one
    nested list in one sidecar would 500 the entire Images view."""
    cid, vid = _character(client, world)
    gid = _greeting(client, world, cid, vid, images=("gallery_1",))
    store.image_subjects.subjects_path(store.worlds.world_root(world), gid).write_text(
        json.dumps({"gallery_1": [["not"], {"a": "string"}, cid]}), encoding="utf-8")

    (row,) = [r for r in _gallery(client, world) if r["kind"] == "greetings"]
    # The members it can read survive; the ones it cannot are dropped, not fatal.
    assert row["subjects"] == [cid]


def test_art_whose_record_is_gone_is_not_offered(client, world):
    """An asset folder can outlive the record it hung off. There is no route
    that serves those bytes, so a tile over them is a broken image with nothing
    the reader can do about it."""
    cid, vid = _character(client, world)
    _upload(client, f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images/gallery_1")
    assert len(_gallery(client, world)) == 1
    (store.worlds.world_root(world) / "characters" / cid / "character.md").unlink()
    assert _gallery(client, world) == []


def test_art_whose_version_is_gone_is_not_offered(client, world):
    """The narrower half of the same rule: the character is still there, but the
    version its assets are filed under is not. Reachable in ordinary use --
    `appearances.import_version` removes the card and leaves the folder -- and
    the reason `_record_name_and_versions` reads versions at all."""
    cid, vid = _character(client, world)
    older = client.post(f"/api/worlds/{world}/characters/{cid}/versions",
                        json={"name": "older", "card": {"data": {"name": "Seraphine"}}})
    assert older.status_code == 200, older.text
    ovid = older.json()["version"]
    _upload(client, f"/api/worlds/{world}/characters/{cid}/versions/{ovid}/images/gallery_1")
    assert len(_gallery(client, world)) == 1
    assert client.delete(
        f"/api/worlds/{world}/characters/{cid}/versions/{ovid}").status_code == 200
    assert vid and _gallery(client, world) == []


def test_the_listing_is_ordered_and_stable(client, world):
    """Grouped by base in one fixed order, then by (id, vid, name) inside it —
    a grid that reshuffles between two identical reads is one nobody can point
    at a tile in."""
    cid, vid = _character(client, world)
    for name in ("gallery_2", "avatar", "gallery_1"):
        _upload(client, f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images/{name}")
    eid = client.post(f"/api/worlds/{world}/locations", json={"name": "Saltmarch"}).json()["id"]
    _upload(client, f"/api/worlds/{world}/locations/{eid}/images/gallery_1")

    got = [(r["kind"], r["name"]) for r in _gallery(client, world)]
    assert got == [("characters", "avatar"), ("characters", "gallery_1"),
                   ("characters", "gallery_2"), ("locations", "gallery_1")]
    assert got == [(r["kind"], r["name"]) for r in _gallery(client, world)]

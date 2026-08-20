"""The description write routes, on all seven surfaces, and the reads that ride
along with the image listings the editors already fetch."""

import importlib
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return TestClient(create_app())


@pytest.fixture
def world(client):
    return client.post("/api/worlds", json={"name": "Realm"}).json()["id"]


def _char(client, wid):
    made = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"}).json()
    cid, vid = made["character"], made["version"]
    client.put(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/images/gallery_1",
               files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    return cid, vid


# ---- characters, world side ------------------------------------------------

def test_world_character_description_roundtrip(client, world):
    cid, vid = _char(client, world)
    base = f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images"
    r = client.put(f"{base}/gallery_1/description", json={"description": "A grey quay."})
    assert r.status_code == 200
    detail = client.get(f"/api/worlds/{world}/characters/{cid}").json()
    version = next(v for v in detail["versions"] if v["id"] == vid)
    assert version["image_descriptions"] == {"gallery_1": "A grey quay."}


def test_describing_an_image_that_is_not_there_is_a_404(client, world):
    cid, vid = _char(client, world)
    r = client.put(
        f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images/nope/description",
        json={"description": "x"})
    assert r.status_code == 404


def test_empty_description_persists_as_reviewed(client, world):
    """`""` is not the same as never having written one — it is what takes an
    image out of the queue without offering it to the model."""
    cid, vid = _char(client, world)
    base = f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images"
    assert client.put(f"{base}/gallery_1/description", json={"description": ""}).status_code == 200
    detail = client.get(f"/api/worlds/{world}/characters/{cid}").json()
    version = next(v for v in detail["versions"] if v["id"] == vid)
    assert version["image_descriptions"] == {"gallery_1": ""}


# ---- characters, campaign side --------------------------------------------

def test_campaign_character_description_lands_campaign_side(client, world):
    cid, vid = _char(client, world)
    client.put(f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images/gallery_1/description",
               json={"description": "The world's take."})
    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": world}).json()["id"]
    detail = client.get(f"/api/campaigns/{camp}/characters/{cid}").json()
    version = next(v for v in detail["versions"] if v["id"] == vid)
    assert version["image_descriptions"] == {"gallery_1": "The world's take."}

    r = client.put(
        f"/api/campaigns/{camp}/characters/{cid}/versions/{vid}/images/gallery_1/description",
        json={"description": "This campaign's take."})
    assert r.status_code == 200
    detail = client.get(f"/api/campaigns/{camp}/characters/{cid}").json()
    version = next(v for v in detail["versions"] if v["id"] == vid)
    assert version["image_descriptions"] == {"gallery_1": "This campaign's take."}
    # ...and the world is untouched
    detail = client.get(f"/api/worlds/{world}/characters/{cid}").json()
    version = next(v for v in detail["versions"] if v["id"] == vid)
    assert version["image_descriptions"] == {"gallery_1": "The world's take."}


# ---- pcs -------------------------------------------------------------------

def test_pc_description_roundtrip_both_scopes(client, world):
    made = client.post(f"/api/worlds/{world}/pcs", json={"name": "Mara"}).json()
    pid, vid = made["pc"], made["version"]
    client.put(f"/api/worlds/{world}/pcs/{pid}/versions/{vid}/images/avatar",
               files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    r = client.put(f"/api/worlds/{world}/pcs/{pid}/versions/{vid}/images/avatar/description",
                   json={"description": "Mara, in travelling clothes."})
    assert r.status_code == 200
    version = client.get(f"/api/worlds/{world}/pcs/{pid}").json()["versions"][0]
    assert version["image_descriptions"] == {"avatar": "Mara, in travelling clothes."}

    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": world}).json()["id"]
    r = client.put(f"/api/campaigns/{camp}/pcs/{pid}/versions/{vid}/images/avatar/description",
                   json={"description": "Campaign-side."})
    assert r.status_code == 200
    version = client.get(f"/api/campaigns/{camp}/pcs/{pid}").json()["versions"][0]
    assert version["image_descriptions"] == {"avatar": "Campaign-side."}


# ---- entities --------------------------------------------------------------

def test_entity_description_roundtrip_both_scopes(client, world):
    eid = client.post(f"/api/worlds/{world}/locations",
                      json={"name": "Saltmarch Harbour", "body": "A grey quay."}).json()["id"]
    client.put(f"/api/worlds/{world}/locations/{eid}/images/gallery_1",
               files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    r = client.put(f"/api/worlds/{world}/locations/{eid}/images/gallery_1/description",
                   json={"description": "Fishing boats under fog."})
    assert r.status_code == 200
    assert store.image_descriptions.read(
        store.worlds.world_root(world), eid, "default", "gallery_1",
        base="locations") == "Fishing boats under fog."

    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": world}).json()["id"]
    r = client.put(f"/api/campaigns/{camp}/locations/{eid}/images/gallery_1/description",
                   json={"description": "Campaign-side."})
    assert r.status_code == 200
    assert store.overlay.read_description(camp, eid, "default", "gallery_1",
                                          base="locations") == "Campaign-side."


# ---- the campaign's own library -------------------------------------------

def test_library_description_roundtrip(client, world):
    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": world}).json()["id"]
    client.put(f"/api/campaigns/{camp}/images/coastline",
               files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    r = client.put(f"/api/campaigns/{camp}/images/coastline/description",
                   json={"description": "A hand-drawn map."})
    assert r.status_code == 200
    listing = client.get(f"/api/campaigns/{camp}/images").json()
    assert [i["name"] for i in listing] == ["coastline"]
    assert store.image_descriptions.read_in(
        store.campaign_images.images_dir(camp)) == {"coastline": "A hand-drawn map."}


def test_library_description_for_a_missing_image_is_a_404(client, world):
    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": world}).json()["id"]
    r = client.put(f"/api/campaigns/{camp}/images/nope/description",
                   json={"description": "x"})
    assert r.status_code == 404


# ---- the describe backlog --------------------------------------------------

def test_undescribed_lists_every_surface_and_drops_reviewed_images(client, world):
    cid, vid = _char(client, world)
    made = client.post(f"/api/worlds/{world}/pcs", json={"name": "Mara"}).json()
    pid, pvid = made["pc"], made["version"]
    client.put(f"/api/worlds/{world}/pcs/{pid}/versions/{pvid}/images/avatar",
               files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    eid = client.post(f"/api/worlds/{world}/locations",
                      json={"name": "Saltmarch Harbour"}).json()["id"]
    client.put(f"/api/worlds/{world}/locations/{eid}/images/gallery_1",
               files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")})

    queue = client.get(f"/api/worlds/{world}/images/undescribed").json()
    assert {(i["kind"], i["id"], i["name"]) for i in queue} == {
        ("characters", cid, "gallery_1"), ("pcs", pid, "avatar"),
        ("locations", eid, "gallery_1")}
    # The URL is the one that serves the image, and its shape follows the
    # surface: actors carry a version, entities do not.
    by_kind = {i["kind"]: i for i in queue}
    assert by_kind["characters"]["url"] == (
        f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images/gallery_1")
    assert by_kind["locations"]["url"] == (
        f"/api/worlds/{world}/locations/{eid}/images/gallery_1")
    assert by_kind["characters"]["record_name"] == "Seraphine"
    assert client.get(by_kind["characters"]["url"]).status_code == 200

    # Describing one retires it; so does reviewing one and saying nothing.
    client.put(f"/api/worlds/{world}/characters/{cid}/versions/{vid}"
               f"/images/gallery_1/description", json={"description": "A grey quay."})
    client.put(f"/api/worlds/{world}/pcs/{pid}/versions/{pvid}/images/avatar/description",
               json={"description": ""})
    queue = client.get(f"/api/worlds/{world}/images/undescribed").json()
    assert {(i["kind"], i["name"]) for i in queue} == {("locations", "gallery_1")}


def test_undescribed_is_not_swallowed_by_the_generic_entity_routes(client, world):
    """`/{kind}/{eid}` would match `images/undescribed` if the generic entity
    router were reached first. It is included last precisely so it is not."""
    r = client.get(f"/api/worlds/{world}/images/undescribed")
    assert r.status_code == 200
    assert r.json() == []


def test_the_backlog_reads_each_record_once_not_once_per_image(client, world, monkeypatch):
    """A character with a gallery contributes one queue entry per picture, and
    the name lookup opens a card file -- so the naive loop re-read one card once
    per image in it (395ms for a 300-character world, on a route that fires
    whenever the character page mounts)."""
    made = client.post(f"/api/worlds/{world}/characters", json={"name": "Seraphine"}).json()
    cid, vid = made["character"], made["version"]
    for name in ("avatar", "gallery_1", "gallery_2"):
        client.put(f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images/{name}",
                   files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")})

    reads = []
    real = store.characters.read_character
    monkeypatch.setattr(store.characters, "read_character",
                        lambda root, rid: (reads.append(rid), real(root, rid))[1])
    queue = client.get(f"/api/worlds/{world}/images/undescribed").json()
    assert len(queue) == 3                     # three images...
    assert reads.count(cid) == 1               # ...one card read


def test_the_campaign_queue_covers_its_library_and_its_diverged_art(client, world):
    """The half the world's queue cannot reach. A campaign's own library hangs
    off no record at all, so those images were previously unreachable from any
    queue -- and art a campaign has diverged has bytes of its own that need
    words of their own."""
    cid, vid = _char(client, world)
    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": world}).json()["id"]
    client.put(f"/api/campaigns/{camp}/images/coastline",
               files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    # ...and a diverged character image
    client.put(f"/api/campaigns/{camp}/characters/{cid}/versions/{vid}/images/gallery_1",
               files={"file": ("b.png", b"\x89PNG\r\n\x1a\n", "image/png")})

    queue = client.get(f"/api/campaigns/{camp}/images/undescribed").json()
    assert {(i["kind"], i["name"]) for i in queue} == {
        ("campaign", "coastline"), ("characters", "gallery_1")}
    lib = next(i for i in queue if i["kind"] == "campaign")
    assert lib["record_name"] == "Campaign library"
    assert client.get(lib["url"]).status_code == 200
    assert client.get(next(i for i in queue if i["kind"] == "characters")["url"]).status_code == 200


def test_the_campaign_queue_leaves_inherited_art_to_the_world(client, world):
    """Describing inherited art once, world-side, serves every campaign on that
    world -- so the campaign queue must not offer it again."""
    _cid, _vid = _char(client, world)
    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": world}).json()["id"]
    assert client.get(f"/api/campaigns/{camp}/images/undescribed").json() == []
    # the world's own queue still has it
    assert len(client.get(f"/api/worlds/{world}/images/undescribed").json()) == 1


def test_undescribed_is_not_swallowed_by_the_library_image_route(client, world):
    """`/campaigns/{cid}/images/{name}` would match "undescribed" as an image
    name if it were registered first."""
    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": world}).json()["id"]
    r = client.get(f"/api/campaigns/{camp}/images/undescribed")
    assert r.status_code == 200
    assert r.json() == []


def test_describing_a_library_image_survives_a_concurrent_sibling_write(client, world):
    """The sidecar is read-modify-written whole, so the write must be locked --
    what is lost otherwise is a sentence somebody sat and wrote."""
    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": world}).json()["id"]
    for name in ("coastline", "the-inn"):
        client.put(f"/api/campaigns/{camp}/images/{name}",
                   files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")})
        client.put(f"/api/campaigns/{camp}/images/{name}/description",
                   json={"description": f"A picture of {name}."})
    assert store.image_descriptions.read_in(store.campaign_images.images_dir(camp)) == {
        "coastline": "A picture of coastline.", "the-inn": "A picture of the-inn."}


def test_deleting_a_library_image_takes_its_description_with_it(client, world):
    """A kept entry would caption the NEXT image uploaded under this name --
    different art, immediately visible and immediately offerable."""
    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": world}).json()["id"]
    client.put(f"/api/campaigns/{camp}/images/coastline",
               files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    client.put(f"/api/campaigns/{camp}/images/coastline/description",
               json={"description": "A hand-drawn map."})
    client.delete(f"/api/campaigns/{camp}/images/coastline")
    client.put(f"/api/campaigns/{camp}/images/coastline",
               files={"file": ("b.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    listing = client.get(f"/api/campaigns/{camp}/images").json()
    assert [(i["name"], i["description"], i["described"]) for i in listing] == [
        ("coastline", "", False)]


def test_the_world_queue_previews_a_link_breaking_name(client, world):
    """`assets.storable` accepts names URL syntax owns — `a#b` truncates at the
    fragment, `100%25` decodes to something else. Unquoted, the queue showed a
    broken preview for exactly the images whose (encoded) PUT would have
    worked, so the reader was asked to describe a picture they could not see."""
    cid, vid = _char(client, world)
    base = f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images"
    for name in ("a#b", "my art"):
        client.put(f"{base}/{quote(name, safe='')}",
                   files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")})

    queue = client.get(f"/api/worlds/{world}/images/undescribed").json()
    urls = {i["name"]: i["url"] for i in queue}
    assert urls["a#b"].endswith("/a%23b")
    for name in ("a#b", "my art"):
        assert client.get(urls[name]).status_code == 200


def test_the_world_queue_drops_an_asset_folder_whose_version_is_gone(client, world):
    """An asset directory can outlive its version: uploading campaign-side art
    to a locked actor and then importing a different world version leaves the
    old folder behind. An image queued from it can never be described — every
    PUT 404s on the version gate — so it would be re-offered forever."""
    cid, vid = _char(client, world)
    root = store.worlds.world_root(world)
    store.assets.put_image(root, cid, "a-version-that-went-away", "gallery_1",
                           b"\x89PNG\r\n\x1a\n", "png")

    queue = client.get(f"/api/worlds/{world}/images/undescribed").json()
    assert [(i["vid"], i["name"]) for i in queue] == [(vid, "gallery_1")]
    # ...and the route really would refuse it, which is why listing it is a trap
    assert client.put(f"/api/worlds/{world}/characters/{cid}"
                      f"/versions/a-version-that-went-away/images/gallery_1/description",
                      json={"description": "x"}).status_code == 404


def test_the_campaign_queue_drops_an_asset_folder_whose_version_is_gone(client, world):
    """The campaign half of the same trap — and the one that actually produces
    it: uploading campaign-side art to a locked actor, then importing a
    different world version, leaves the old version's folder behind."""
    cid, vid = _char(client, world)
    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": world}).json()["id"]
    client.put(f"/api/campaigns/{camp}/characters/{cid}/versions/{vid}/images/gallery_1",
               files={"file": ("b.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    store.assets.put_image(store.overlay.croot_of(camp), cid, "a-version-that-went-away",
                           "gallery_1", b"\x89PNG\r\n\x1a\n", "png")

    queue = client.get(f"/api/campaigns/{camp}/images/undescribed").json()
    assert [(i["vid"], i["name"]) for i in queue] == [(vid, "gallery_1")]


def test_the_library_refuses_the_name_the_backlog_route_owns(client, world):
    """`/campaigns/{cid}/images/undescribed` is registered before
    `/images/{name}` so the backlog is reachable at all -- which leaves that one
    name unserveable as an image. Reserved rather than tolerated: stored, it
    would be listed and offered by the picker while every URL for it answered
    with the backlog JSON."""
    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": world}).json()["id"]
    for name in ("undescribed", "Undescribed"):
        r = client.put(f"/api/campaigns/{camp}/images/{name}",
                       files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")})
        assert r.status_code == 400
    assert client.get(f"/api/campaigns/{camp}/images").json() == []

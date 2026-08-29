"""The campaign gallery: every image a campaign sees, not every image its world has.

`/worlds/{wid}/gallery` is world-scoped by design, and that is the right answer
to the world's own question. It is the wrong answer to "what art does this
campaign use", which is what a reader arriving from a campaign's Images row is
asking -- the campaign's diverged and added art lives in the campaign root and
no world sweep can see it.
"""
from fastapi.testclient import TestClient

from grimoire.main import app
from grimoire.store import assets, campaigns, characters, entities, overlay, worlds

client = TestClient(app)


def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def _world_with_hero(name="W"):
    wid = worlds.create_world(name)
    wroot = worlds.world_root(wid)
    aid, vid = characters.create_character(wroot, "Hero")
    assets.put_image(wroot, aid, vid, "avatar", b"worldavatar", "png")
    return wid, wroot, aid, vid


def _rows(cid):
    r = client.get(f"/api/campaigns/{cid}/gallery")
    assert r.status_code == 200, r.text
    return r.json()


def _named(rows, name):
    return [r for r in rows if r["name"] == name]


def test_inherited_art_is_listed_for_a_campaign_that_has_none_of_its_own(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid, _wroot, aid, vid = _world_with_hero()
    cid = campaigns.create_campaign("C", wid)

    rows = _rows(cid)
    got = _named(rows, "avatar")
    assert len(got) == 1
    assert got[0]["kind"] == "characters" and got[0]["id"] == aid
    assert got[0]["record_name"] == "Hero"
    # Campaign-scoped even though the bytes are the world's: the campaign serve
    # route resolves through the same overlay, so one URL shape is right for both.
    assert got[0]["url"].startswith(f"/api/campaigns/{cid}/characters/{aid}/versions/{vid}/images/avatar")


def test_campaign_only_art_is_listed(monkeypatch, tmp_path):
    """The gap this route exists for: a gallery the campaign added to a record
    it inherits is in the campaign root, and a world sweep cannot see it."""
    home(monkeypatch, tmp_path)
    wid, _wroot, aid, vid = _world_with_hero()
    cid = campaigns.create_campaign("C", wid)
    croot = campaigns.campaign_root(cid)
    assets.put_image(croot, aid, vid, "gallery_1", b"campaignonly", "webp")

    rows = _rows(cid)
    assert len(_named(rows, "gallery_1")) == 1
    # and the world's own gallery still cannot -- the split is intact
    world = client.get(f"/api/worlds/{wid}/gallery").json()
    assert _named(world, "gallery_1") == []
    assert _named(world, "avatar") != []


def test_a_diverged_copy_shadows_the_world_once(monkeypatch, tmp_path):
    """Same logical name, different format -- the shape found in a real store.
    One tile, not two, and it is the campaign's."""
    home(monkeypatch, tmp_path)
    wid, _wroot, aid, vid = _world_with_hero()
    cid = campaigns.create_campaign("C", wid)
    croot = campaigns.campaign_root(cid)
    assets.put_image(croot, aid, vid, "avatar", b"campaignavatar", "jpg")

    got = _named(_rows(cid), "avatar")
    assert len(got) == 1
    assert got[0]["ext"] == "jpg"


def test_a_tombstoned_image_is_absent(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid, _wroot, aid, vid = _world_with_hero()
    cid = campaigns.create_campaign("C", wid)
    overlay.add_deleted(cid, f"assets/characters/{aid}/{vid}/avatar")

    assert _named(_rows(cid), "avatar") == []


def test_a_campaign_side_description_of_inherited_art_is_the_one_reported(monkeypatch, tmp_path):
    """Describing inherited art writes campaign-side without diverging the art,
    so a world-rooted read would report it as never reviewed -- and the
    Unfinished filter would offer a picture that has already been done."""
    home(monkeypatch, tmp_path)
    wid, _wroot, aid, vid = _world_with_hero()
    cid = campaigns.create_campaign("C", wid)
    overlay.set_description(cid, aid, vid, "avatar", "In half-plate, at the quay.")

    got = _named(_rows(cid), "avatar")
    assert len(got) == 1
    assert got[0]["described"] is True
    assert got[0]["description"] == "In half-plate, at the quay."


def test_entity_art_carries_no_version_segment(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    eid = entities.create_entity(wroot, "locations", "Saltmarch", "body")
    assets.put_image(wroot, eid, "default", "gallery_1", b"quay", "png", base="locations")
    cid = campaigns.create_campaign("C", wid)

    got = _named(_rows(cid), "gallery_1")
    assert len(got) == 1
    assert got[0]["kind"] == "locations"
    assert "/versions/" not in got[0]["url"]


def test_an_unknown_campaign_is_a_404(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    assert client.get("/api/campaigns/nope/gallery").status_code == 404

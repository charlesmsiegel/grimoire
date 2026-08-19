"""The campaign's own image library (`store/campaign_images.py`, #376)."""

import io

import pytest
from PIL import Image

from grimoire.store import assets, campaign_images, campaigns, worlds


def _png(size=(4, 4), color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Saltmarch Nights", wid)


def test_put_list_read_delete_round_trip(cid):
    assert campaign_images.list_images(cid) == []
    assert campaign_images.image_path(cid, "coastline") is None

    data = _png()
    assert campaign_images.put_image(cid, "coastline", data, "png") == "png"
    p = campaign_images.image_path(cid, "coastline")
    assert p is not None and p.read_bytes() == data
    assert p == campaigns.campaign_root(cid) / "assets" / "images" / "coastline.png"

    listed = campaign_images.list_images(cid)
    assert [i["name"] for i in listed] == ["coastline"]
    assert listed[0]["ext"] == "png" and listed[0]["v"]

    campaign_images.delete_image(cid, "coastline")
    assert campaign_images.image_path(cid, "coastline") is None
    assert campaign_images.list_images(cid) == []


def test_many_images_coexist_and_are_listed_by_name(cid):
    for name in ("harbour", "coastline", "the-inn"):
        campaign_images.put_image(cid, name, _png(), "png")
    assert [i["name"] for i in campaign_images.list_images(cid)] == [
        "coastline", "harbour", "the-inn"]

    # deleting one leaves the others: the cardinality is the whole difference
    # between this and `store.covers`
    campaign_images.delete_image(cid, "harbour")
    assert [i["name"] for i in campaign_images.list_images(cid)] == [
        "coastline", "the-inn"]


def test_replacing_across_extensions_leaves_one_file(cid):
    first = _png(color=(10, 20, 30))
    second = _png((5, 5), color=(200, 100, 50))  # visibly different from `first`
    campaign_images.put_image(cid, "map", first, "png")
    campaign_images.put_image(cid, "map", second, "jpg")
    d = campaigns.campaign_root(cid) / "assets" / "images"
    assert [p.name for p in sorted(d.iterdir())] == ["map.jpg"]
    read_back = campaign_images.image_path(cid, "map").read_bytes()
    assert read_back == second and read_back != first
    # and one entry, not two, so a caller cannot read a cache token off the
    # sibling the serve route will not return
    assert [i["name"] for i in campaign_images.list_images(cid)] == ["map"]


def test_unsupported_extension_rejected(cid):
    with pytest.raises(ValueError):
        campaign_images.put_image(cid, "map", _png(), "svg")


@pytest.mark.parametrize("name", [
    "coast line",       # whitespace ends a markdown link destination
    "map(1)",           # parentheses close it
    "<map>",            # the alternate destination syntax
    "map#2", "map?v",   # truncate the path in a URL
    "50%-map",          # ambiguous about being already encoded
    'say"cheese"', "it's", "back`tick", "back\\slash",
    "",
])
def test_a_name_no_post_could_link_to_is_refused(cid, name):
    """#373's lesson, in the place the next instance of it would start: a name
    that gets stored but cannot go in `![alt](url)` is bytes the app can never
    show, filed under a token it can never insert."""
    assert not campaign_images.addressable(name)
    with pytest.raises(ValueError):
        campaign_images.put_image(cid, name, _png(), "png")
    d = campaigns.campaign_root(cid) / "assets" / "images"
    assert not d.exists() or list(d.iterdir()) == []


def test_a_name_in_any_script_is_addressable(cid):
    """The rule is about the punctuation the surrounding syntax owns, not about
    ASCII: a library is not English, and a name in any script survives both a
    URL path and a markdown link."""
    assert campaign_images.addressable("海岸線")
    assert campaign_images.put_image(cid, "海岸線", _png(), "png") == "png"
    assert [i["name"] for i in campaign_images.list_images(cid)] == ["海岸線"]


def test_the_listing_hides_a_dropped_file_the_picker_could_not_insert(cid):
    """This directory is one a human browses and a sync client writes into.

    A file dropped under a name the app cannot put in a markdown link is left
    exactly where it is -- and is not offered, because offering it would insert
    a broken image. `image_path` still resolves it: that answers "what is on
    disk", which is a different question.
    """
    campaign_images.put_image(cid, "coastline", _png(), "png")
    d = campaigns.campaign_root(cid) / "assets" / "images"
    (d / "holiday snap.png").write_bytes(_png())

    assert [i["name"] for i in campaign_images.list_images(cid)] == ["coastline"]
    assert campaign_images.image_path(cid, "holiday snap") is not None
    assert (d / "holiday snap.png").exists()


def test_a_file_that_is_not_ours_is_neither_listed_nor_swept(cid):
    """`supported_only`, the same call `store.covers` makes: a `notes.txt` beside
    the art must not win resolution, must not be listed, and must survive a
    replace and a remove of the image it shares a stem with."""
    d = campaigns.campaign_root(cid) / "assets" / "images"
    campaign_images.put_image(cid, "map", _png(), "png")
    (d / "map.txt").write_text("where the bodies are", encoding="utf-8")
    (d / "readme.txt").write_text("my art folder", encoding="utf-8")

    assert [i["name"] for i in campaign_images.list_images(cid)] == ["map"]
    assert campaign_images.image_path(cid, "map").suffix == ".png"

    campaign_images.put_image(cid, "map", _png((6, 6)), "jpg")
    assert (d / "map.txt").exists()
    campaign_images.delete_image(cid, "map")
    assert (d / "map.txt").exists() and (d / "readme.txt").exists()
    assert campaign_images.image_path(cid, "map") is None


def test_the_byte_cap_is_re_checked_on_what_was_actually_received(cid, monkeypatch):
    """The route refuses an oversized upload from `UploadFile.size` before it
    reads the body — but `size` is Optional in the ASGI contract, so a client
    that omits it would otherwise buy an unbounded read. This is the belt."""
    monkeypatch.setattr(campaign_images, "MAX_BYTES", 8)
    campaign_images.validate_size(b"12345678")            # exactly at the cap
    with pytest.raises(campaign_images.ImageTooLarge) as exc:
        campaign_images.validate_size(b"123456789")
    assert str(exc.value) == campaign_images.TOO_LARGE


def test_a_version_token_survives_the_file_going_away(cid, monkeypatch):
    """`image_version` resolves a path and then stats it, and a synced store can
    lose the file between the two. A write that already landed must not answer
    500 because its cache token could not be read."""
    assert campaign_images.image_version(cid, "map") == ""      # nothing stored
    campaign_images.put_image(cid, "map", _png(), "png")
    assert campaign_images.image_version(cid, "map") != ""
    monkeypatch.setattr(assets, "image_version",
                        lambda p: (_ for _ in ()).throw(OSError("gone")))
    assert campaign_images.image_version(cid, "map") == ""


def test_delete_confirms_the_removal(cid, monkeypatch):
    """`assets.delete_in` swallows a failed unlink by design; here the unlink IS
    the operation, so a Remove that removed nothing must not answer OK."""
    campaign_images.put_image(cid, "map", _png(), "png")
    monkeypatch.setattr(assets, "delete_in", lambda *a, **k: None)
    with pytest.raises(OSError):
        campaign_images.delete_image(cid, "map")


def test_unknown_campaign_raises_and_creates_nothing(monkeypatch, tmp_path):
    """#360/#373: `assets.put_in` creates the directory it writes into, so an
    id nothing can reach would otherwise become a permanent, unlisted folder of
    orphaned bytes reported to the caller as a successful upload."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    worlds.create_world("Realm")
    for call in (lambda: campaign_images.images_dir("ghost"),
                 lambda: campaign_images.list_images("ghost"),
                 lambda: campaign_images.image_path("ghost", "map"),
                 lambda: campaign_images.put_image("ghost", "map", _png(), "png"),
                 lambda: campaign_images.delete_image("ghost", "map")):
        with pytest.raises(campaigns.CampaignNotFound):
            call()
    assert not (tmp_path / "campaigns" / "ghost").exists()


def test_the_library_is_campaign_local_and_not_inherited(cid, monkeypatch, tmp_path):
    """Not under the overlay, by design: there is no world-side copy to shadow,
    so a second campaign on the same world starts empty."""
    campaign_images.put_image(cid, "map", _png(), "png")
    wid = campaigns.read_campaign(cid)["meta"]["world"]
    other = campaigns.create_campaign("Another Chronicle", wid)
    assert campaign_images.list_images(other) == []

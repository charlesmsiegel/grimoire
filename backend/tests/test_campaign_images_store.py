"""The campaign's own image library (`store/campaign_images.py`, #376)."""

import io

import pytest
from PIL import Image

from grimoire.store import (assets, campaign_images, campaigns,
                            image_library, overlay, world_images, worlds)


def _png(size=(4, 4), color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def wid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return worlds.create_world("Realm")


@pytest.fixture
def cid(wid):
    """Split from `wid` so a test can reach the world this campaign reads
    through to; every existing test in this file still asks only for `cid`."""
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
    assert not image_library.addressable(name)
    with pytest.raises(ValueError):
        campaign_images.put_image(cid, name, _png(), "png")
    d = campaigns.campaign_root(cid) / "assets" / "images"
    assert not d.exists() or list(d.iterdir()) == []


def test_a_name_in_any_script_is_addressable(cid):
    """The rule is about the punctuation the surrounding syntax owns, not about
    ASCII: a library is not English, and a name in any script survives both a
    URL path and a markdown link."""
    assert image_library.addressable("海岸線")
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


@pytest.mark.parametrize("name", ["promote-tmp", "Promote-TMP", "map.2", "map*", "a/b"])
def test_a_name_this_store_would_not_serve_back_is_refused_and_hidden(cid, name):
    """The gate is a conjunction, and the half `assets` owns is the one easy to
    drop. `assets.list_in` shows a stranded `promote-tmp` on purpose -- in a
    per-version folder it is crash residue worth seeing -- but `put_in` and
    `path_in` both refuse the name, so in this flat directory it is a file the
    server answers 404 to. Offering it would be #373 with a new hat on."""
    assert not image_library.addressable(name)
    with pytest.raises(ValueError):
        campaign_images.put_image(cid, name, _png(), "png")

    d = campaigns.campaign_root(cid) / "assets" / "images"
    d.mkdir(parents=True, exist_ok=True)
    if "/" not in name:                        # a path separator names no file here
        (d / f"{name}.png").write_bytes(_png())
        assert [i["name"] for i in campaign_images.list_images(cid)] == []
        assert campaign_images.image_path(cid, name) is None    # and would not serve


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
    monkeypatch.setattr(image_library, "MAX_BYTES", 8)
    image_library.validate_size(b"12345678")            # exactly at the cap
    with pytest.raises(image_library.ImageTooLarge) as exc:
        image_library.validate_size(b"123456789")
    assert str(exc.value) == image_library.TOO_LARGE


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
    """A campaign's OWN images stay its own.

    What a campaign shares with its siblings is the *world's* library, not each
    other's uploads -- so a second campaign on the same world sees the world's
    images (none here) and none of this one's.
    """
    campaign_images.put_image(cid, "map", _png(), "png")
    wid = campaigns.read_campaign(cid)["meta"]["world"]
    other = campaigns.create_campaign("Another Chronicle", wid)
    assert campaign_images.list_images(other) == []


# ---- the world's library, read through ------------------------------------
#
# Read-through plus hiding, and NOT shadowing. A campaign sees every world
# library image, may hide one it does not want, and may not replace one under
# the same name -- "a different picture in this campaign" is answered by a
# different name, and copy-on-write for bytes buys nothing.

def test_a_campaign_sees_its_worlds_library(cid, wid):
    world_images.put_image(wid, "coastline", _png(), "png")

    rows = campaign_images.list_images(cid)
    assert [(r["name"], r["inherited"]) for r in rows] == [("coastline", True)]
    assert campaign_images.image_path(cid, "coastline") == \
        worlds.world_root(wid) / "assets" / "images" / "coastline.png"


def test_own_and_inherited_images_are_listed_together_by_name(cid, wid):
    world_images.put_image(wid, "coastline", _png(), "png")
    campaign_images.put_image(cid, "handout", _png(), "png")

    assert [(r["name"], r["inherited"]) for r in campaign_images.list_images(cid)] \
        == [("coastline", True), ("handout", False)]


def test_a_campaign_may_not_take_a_name_the_world_holds(cid, wid):
    """No shadowing: the refusal is what keeps the accidental collision below
    rare rather than routine."""
    world_images.put_image(wid, "coastline", _png(), "png")
    with pytest.raises(ValueError):
        campaign_images.put_image(cid, "coastline", _png(), "png")


def test_a_logical_name_is_one_image_across_extensions(cid, wid):
    """A `.webp` in the world and a `.png` here are the SAME name. Shipping the
    other rule was a real bug (`campaigns/lifecycle.py`)."""
    world_images.put_image(wid, "coastline", _png(), "webp")
    with pytest.raises(ValueError):
        campaign_images.put_image(cid, "coastline", _png(), "png")


def test_a_campaign_keeps_its_own_name_when_the_world_later_takes_it(cid, wid):
    """The accidental collision. Nothing can stop a world adding a name a
    campaign already holds, so the campaign's own file wins: it is theirs, and
    its posts already point at it."""
    own = _png(color=(90, 90, 90))
    campaign_images.put_image(cid, "coastline", own, "png")
    world_images.put_image(wid, "coastline", _png(), "png")

    assert [(r["name"], r["inherited"]) for r in campaign_images.list_images(cid)] \
        == [("coastline", False)]
    assert campaign_images.image_path(cid, "coastline").read_bytes() == own


def test_hiding_an_inherited_image_and_getting_it_back(cid, wid):
    world_images.put_image(wid, "coastline", _png(), "png")

    campaign_images.delete_image(cid, "coastline")
    assert campaign_images.list_images(cid) == []
    # Stops at the tombstone rather than falling through to the world's file.
    assert campaign_images.image_path(cid, "coastline") is None
    assert campaign_images.list_hidden(cid) == ["coastline"]
    # and the world still has its picture -- hiding is per campaign
    assert world_images.image_path(wid, "coastline") is not None

    campaign_images.restore_image(cid, "coastline")
    assert [r["name"] for r in campaign_images.list_images(cid)] == ["coastline"]
    assert campaign_images.list_hidden(cid) == []


def test_a_campaign_image_under_a_hidden_name_is_listed_and_served(cid, wid):
    """The tombstone filter is the INHERITED half's, never the union's.

    Subtracting tombstones from the whole union hides the campaign's own bytes:
    they serve but never list, so no picker tile, no gallery row, no describe
    row -- and no way to clear the tombstone that hid them. That is #373's
    lesson (a token naming a file the server would not serve) inverted.
    """
    world_images.put_image(wid, "coastline", _png(), "png")
    campaign_images.delete_image(cid, "coastline")     # hide the world's
    world_images.delete_image(wid, "coastline")        # and the world drops it

    own = _png(color=(90, 90, 90))
    campaign_images.put_image(cid, "coastline", own, "png")

    assert [(r["name"], r["inherited"]) for r in campaign_images.list_images(cid)] \
        == [("coastline", False)]
    p = campaign_images.image_path(cid, "coastline")
    assert p is not None and p.read_bytes() == own


def test_deleting_an_image_the_campaign_owns_over_a_world_name_does_not_revert(cid, wid):
    """The accidental collision's delete. Unlink then tombstone, which is
    `overlay.delete_image`'s order: with only the unlink this is a Revert, and
    the world's picture reappears under a name the user just deleted."""
    campaign_images.put_image(cid, "coastline", _png(color=(90, 90, 90)), "png")
    world_images.put_image(wid, "coastline", _png(), "png")

    campaign_images.delete_image(cid, "coastline")
    assert campaign_images.image_path(cid, "coastline") is None
    assert campaign_images.list_images(cid) == []


def test_hidden_entries_are_cleared_when_the_world_drops_the_image(cid, wid):
    world_images.put_image(wid, "coastline", _png(), "png")
    campaign_images.delete_image(cid, "coastline")
    assert campaign_images.list_hidden(cid) == ["coastline"]

    world_images.delete_image(wid, "coastline")
    assert campaign_images.list_hidden(cid) == []

    # so a re-upload under that name is visible again, rather than hidden by a
    # tombstone aimed at a picture that no longer exists
    world_images.put_image(wid, "coastline", _png(), "png")
    assert [r["name"] for r in campaign_images.list_images(cid)] == ["coastline"]


def test_an_inherited_image_is_versioned_by_the_file_that_backs_it(cid, wid):
    """`?v=` is answered immutable for a year, so a token that did not follow
    the world's bytes would pin a reader to art that is gone."""
    world_images.put_image(wid, "coastline", _png(), "png")
    assert campaign_images.image_version(cid, "coastline") == \
        world_images.image_version(wid, "coastline") != ""


def test_descriptions_come_from_whichever_side_owns_the_image(cid, wid):
    world_images.put_image(wid, "coastline", _png(), "png")
    world_images.set_description(wid, "coastline", "a rocky shore")
    campaign_images.put_image(cid, "handout", _png(), "png")
    campaign_images.set_description(cid, "handout", "the party's map")

    assert campaign_images.read_descriptions(cid) == {
        "coastline": "a rocky shore", "handout": "the party's map"}


def test_a_campaign_may_not_describe_an_inherited_image(cid, wid):
    """Inherited art is described in the WORLD's queue, where describing it
    once serves every campaign on that world."""
    world_images.put_image(wid, "coastline", _png(), "png")
    with pytest.raises(ValueError):
        campaign_images.set_description(cid, "coastline", "mine")


def test_the_campaign_backlog_is_its_own_images_only(cid, wid):
    """Inherited art belongs to the world's queue. Reusing the merged listing
    here would re-offer every world image in every campaign's queue."""
    world_images.put_image(wid, "coastline", _png(), "png")
    campaign_images.put_image(cid, "handout", _png(), "png")
    campaign_images.put_image(cid, "sketch", _png(), "png")

    assert [i["name"] for i in campaign_images.own_undescribed(cid)] == [
        "handout", "sketch"]

    campaign_images.set_description(cid, "handout", "the party's map")
    assert [i["name"] for i in campaign_images.own_undescribed(cid)] == ["sketch"]


def test_a_campaign_whose_world_is_gone_reads_as_an_empty_world_half(cid, wid):
    """Not an exception. `GET /images`, the gallery, the narrator's art pool and
    an EPUB export all run through here, and a missing world must not become
    four 500s.

    The app itself refuses to delete a world a campaign is using, so the way
    this state is actually reached is a store that is half-synced or hand-edited
    -- which is exactly why `overlay.wroot_of` documents never raising for it.
    """
    import shutil

    world_images.put_image(wid, "coastline", _png(), "png")
    campaign_images.put_image(cid, "handout", _png(), "png")
    shutil.rmtree(worlds.world_root(wid))

    assert [r["name"] for r in campaign_images.list_images(cid)] == ["handout"]
    assert campaign_images.image_path(cid, "coastline") is None
    assert campaign_images.read_descriptions(cid) == {}
    assert campaign_images.list_hidden(cid) == []

"""The world's own image library (`store/world_images.py`).

Art that belongs to the world and to none of its records -- a regional map, a
banner, establishing art for the setting. The campaign's read-through view over
it lives in `test_campaign_images_store.py`; this file is the world side alone.
"""

import io

import pytest
from PIL import Image

from grimoire.store import image_descriptions, world_images, worlds


def _png(size=(4, 4), color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def wid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return worlds.create_world("Realm")


def test_put_list_serve_delete_round_trip(wid):
    assert world_images.list_images(wid) == []
    assert world_images.image_path(wid, "coastline") is None

    data = _png()
    assert world_images.put_image(wid, "coastline", data, "png") == "png"

    p = world_images.image_path(wid, "coastline")
    assert p is not None and p.read_bytes() == data
    assert p == worlds.world_root(wid) / "assets" / "images" / "coastline.png"

    listed = world_images.list_images(wid)
    assert [i["name"] for i in listed] == ["coastline"]
    assert listed[0]["ext"] == "png" and listed[0]["v"]
    assert world_images.image_version(wid, "coastline") == listed[0]["v"]

    world_images.delete_image(wid, "coastline")
    assert world_images.image_path(wid, "coastline") is None
    assert world_images.list_images(wid) == []
    assert world_images.image_version(wid, "coastline") == ""


def test_many_images_coexist_and_are_listed_by_name(wid):
    for name in ("harbour", "coastline", "the-inn"):
        world_images.put_image(wid, name, _png(), "png")
    assert [i["name"] for i in world_images.list_images(wid)] == [
        "coastline", "harbour", "the-inn"]


def test_replacing_across_extensions_leaves_one_file(wid):
    first = _png(color=(10, 20, 30))
    second = _png((5, 5), color=(200, 100, 50))
    world_images.put_image(wid, "map", first, "png")
    world_images.put_image(wid, "map", second, "jpg")

    d = worlds.world_root(wid) / "assets" / "images"
    assert [p.name for p in sorted(d.iterdir())] == ["map.jpg"]
    assert world_images.image_path(wid, "map").read_bytes() == second
    assert [i["name"] for i in world_images.list_images(wid)] == ["map"]


@pytest.mark.parametrize("name", ["my map", "map(1)", "a#b", "undescribed",
                                  "Undescribed", "promote-tmp", ""])
def test_a_name_a_link_or_a_route_cannot_carry_is_refused(wid, name):
    """The same conjunction the campaign library gates on, because it is one
    rule in one module -- `undescribed` included, which the world backlog route
    has already spent."""
    with pytest.raises(ValueError):
        world_images.put_image(wid, name, _png(), "png")
    d = worlds.world_root(wid) / "assets" / "images"
    assert not d.exists() or list(d.iterdir()) == []


def test_a_file_that_is_not_ours_is_neither_listed_nor_swept(wid):
    d = worlds.world_root(wid) / "assets" / "images"
    world_images.put_image(wid, "map", _png(), "png")
    (d / "map.txt").write_text("where the bodies are", encoding="utf-8")

    assert [i["name"] for i in world_images.list_images(wid)] == ["map"]
    world_images.put_image(wid, "map", _png((6, 6)), "jpg")
    assert (d / "map.txt").exists()
    world_images.delete_image(wid, "map")
    assert (d / "map.txt").exists()


def test_the_describe_backlog_reports_only_unreviewed_images(wid):
    world_images.put_image(wid, "coastline", _png(), "png")
    world_images.put_image(wid, "banner", _png(), "png")
    assert world_images.has_undescribed(wid)
    assert world_images.undescribed_count(wid) == 2

    world_images.set_description(wid, "coastline", "a rocky shore")
    assert [i["name"] for i in world_images.undescribed(wid)] == ["banner"]
    assert world_images.read_descriptions(wid) == {"coastline": "a rocky shore"}
    assert world_images.undescribed_count(wid) == 1

    # Reviewed and deliberately left blank is FINISHED, not unreviewed --
    # key presence is the distinction the sidecar turns on, and re-offering a
    # blank one is how a queue never empties.
    world_images.set_description(wid, "banner", "")
    assert world_images.undescribed(wid) == []
    assert not world_images.has_undescribed(wid)
    assert world_images.undescribed_count(wid) == 0
    assert world_images.read_descriptions(wid) == {
        "coastline": "a rocky shore", "banner": ""}


def test_describing_an_image_that_is_not_there_is_refused(wid):
    with pytest.raises(ValueError):
        world_images.set_description(wid, "nope", "a shore")


def test_the_description_goes_with_the_bytes(wid):
    """A kept entry would caption the NEXT image uploaded under this name --
    different art, immediately visible and immediately eligible for the
    narrator's art section."""
    world_images.put_image(wid, "coastline", _png(), "png")
    world_images.set_description(wid, "coastline", "a rocky shore")
    world_images.delete_image(wid, "coastline")

    d = worlds.world_root(wid) / "assets" / "images"
    assert image_descriptions.read_raw(d) == {}


def test_an_empty_library_answers_rather_than_raising(wid):
    assert world_images.list_images(wid) == []
    assert world_images.read_descriptions(wid) == {}
    assert world_images.undescribed(wid) == []
    assert not world_images.has_undescribed(wid)
    assert world_images.undescribed_count(wid) == 0


def test_an_unknown_world_raises_and_creates_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    with pytest.raises(worlds.WorldNotFound):
        world_images.put_image("nope", "map", _png(), "png")
    with pytest.raises(worlds.WorldNotFound):
        world_images.list_images("nope")
    assert not (tmp_path / "worlds" / "nope").exists()

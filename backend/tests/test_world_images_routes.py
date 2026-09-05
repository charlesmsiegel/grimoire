"""The world's cover and image library over HTTP (`routes/world_images.py`)."""

import io

import pytest
from PIL import Image


def _png(size=(4, 4), color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def wid(client):
    return client.post("/api/worlds", json={"name": "Realm"}).json()["id"]


def _put(client, wid, name, data=None, filename="anything.jpg"):
    return client.put(f"/api/worlds/{wid}/images/{name}",
                      files={"file": (filename, data or _png(), "image/jpeg")})


# ---- the library ----------------------------------------------------------

def test_the_library_round_trips_over_http(client, wid):
    assert client.get(f"/api/worlds/{wid}/images").json() == []

    r = _put(client, wid, "coastline")
    assert r.status_code == 200
    # The BYTES name the type, never the filename (#321): uploaded as .jpg,
    # stored as .png, so every consumer that reads a media type off the suffix
    # tells the truth.
    assert r.json()["ext"] == "png" and r.json()["v"]

    assert client.get(f"/api/worlds/{wid}/images/coastline").status_code == 200
    listed = client.get(f"/api/worlds/{wid}/images").json()
    assert [i["name"] for i in listed] == ["coastline"]
    assert listed[0]["described"] is False and listed[0]["description"] == ""

    assert client.delete(f"/api/worlds/{wid}/images/coastline").status_code == 200
    assert client.get(f"/api/worlds/{wid}/images/coastline").status_code == 404


def test_the_describe_backlog_is_not_shadowed_by_the_name_route(client, wid):
    """`/images/undescribed` must keep answering the backlog.

    Asserting a NON-EMPTY backlog on purpose: `[]` is what a shadowed route and
    an empty backlog both look like, which is the one distinction this exists
    to make.
    """
    _put(client, wid, "coastline")
    r = client.get(f"/api/worlds/{wid}/images/undescribed")
    assert r.status_code == 200
    assert [i["name"] for i in r.json()] == ["coastline"]


def test_a_description_round_trips_and_empties_the_backlog(client, wid):
    _put(client, wid, "coastline")
    assert client.put(f"/api/worlds/{wid}/images/coastline/description",
                      json={"description": "a rocky shore"}).status_code == 200

    listed = client.get(f"/api/worlds/{wid}/images").json()
    assert listed[0]["description"] == "a rocky shore" and listed[0]["described"]
    assert client.get(f"/api/worlds/{wid}/images/undescribed").json() == []


def test_describing_an_image_that_is_not_there_is_a_404(client, wid):
    assert client.put(f"/api/worlds/{wid}/images/nope/description",
                      json={"description": "x"}).status_code == 404


def test_a_name_a_link_cannot_carry_is_refused_before_any_byte_is_written(client, wid):
    r = client.put(f"/api/worlds/{wid}/images/my%20map",
                   files={"file": ("m.png", _png(), "image/png")})
    assert r.status_code == 400
    assert client.get(f"/api/worlds/{wid}/images").json() == []


def test_a_name_the_backlog_route_owns_is_refused(client, wid):
    """`undescribed` is reserved: stored under it, every URL for the image would
    answer with the backlog JSON instead of the picture."""
    r = _put(client, wid, "undescribed")
    assert r.status_code == 400


def test_an_oversized_upload_is_refused_before_it_is_read(client, wid):
    huge = b"\x89PNG" + b"\0" * (25 * 1024 * 1024)
    r = client.put(f"/api/worlds/{wid}/images/big",
                   files={"file": ("big.png", huge, "image/png")})
    assert r.status_code == 413


def test_bytes_in_no_format_we_can_name_are_refused(client, wid):
    r = client.put(f"/api/worlds/{wid}/images/junk",
                   files={"file": ("j.png", b"not an image at all", "image/png")})
    assert r.status_code == 400


def test_a_stray_the_picker_would_not_offer_can_still_be_deleted(client, wid):
    """The DELETE is deliberately ungated: a file a sync client dropped under an
    unofferable name must have a way out of the app."""
    from grimoire.store import world_images, worlds
    _put(client, wid, "coastline")
    (worlds.world_root(wid) / "assets" / "images" / "holiday snap.png").write_bytes(_png())

    assert client.delete(f"/api/worlds/{wid}/images/holiday snap").status_code == 200
    assert world_images.image_path(wid, "holiday snap") is None


# ---- the cover ------------------------------------------------------------

def test_the_cover_round_trips_and_shows_on_the_world_payloads(client, wid):
    assert client.get(f"/api/worlds/{wid}").json()["meta"]["cover"] == ""
    assert [w["cover"] for w in client.get("/api/worlds").json()] == [""]

    r = client.put(f"/api/worlds/{wid}/cover",
                   files={"file": ("c.png", _png(), "image/png")})
    assert r.status_code == 200 and r.json()["v"]

    assert client.get(f"/api/worlds/{wid}/cover").status_code == 200
    assert client.get(f"/api/worlds/{wid}").json()["meta"]["cover"] == r.json()["v"]
    assert [w["cover"] for w in client.get("/api/worlds").json()] == [r.json()["v"]]

    assert client.delete(f"/api/worlds/{wid}/cover").status_code == 200
    assert client.get(f"/api/worlds/{wid}/cover").status_code == 404
    assert client.get(f"/api/worlds/{wid}").json()["meta"]["cover"] == ""


def test_an_image_that_cannot_be_labelled_honestly_is_refused_as_a_cover(client, wid):
    r = client.put(f"/api/worlds/{wid}/cover",
                   files={"file": ("c.png", b"not an image", "image/png")})
    assert r.status_code == 400


# ---- unknown ids ----------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("get", "/api/worlds/nope/images"),
    ("get", "/api/worlds/nope/images/x"),
    ("delete", "/api/worlds/nope/images/x"),
    ("get", "/api/worlds/nope/cover"),
    ("delete", "/api/worlds/nope/cover"),
])
def test_an_unknown_world_is_a_404_not_a_500(client, method, path):
    assert getattr(client, method)(path).status_code == 404


def test_an_unknown_world_cannot_be_written_into(client, tmp_path):
    r = client.put("/api/worlds/nope/images/x",
                   files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 404
    r = client.put("/api/worlds/nope/cover",
                   files={"file": ("c.png", _png(), "image/png")})
    assert r.status_code == 404

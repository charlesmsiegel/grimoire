import io
import pathlib

import pytest
from PIL import Image

from grimoire.store import campaigns, covers, worlds


def _png(size=(4, 4), color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Saltmarch Nights", wid)


def test_put_read_delete_round_trip(cid):
    assert covers.cover_path(cid) is None
    assert covers.cover_version(cid) == ""

    data = _png()
    assert covers.put_cover(cid, data, "png") == "png"
    p = covers.cover_path(cid)
    assert p is not None and p.read_bytes() == data
    assert p == campaigns.campaign_root(cid) / "assets" / "cover.png"
    assert covers.cover_version(cid) != ""

    covers.delete_cover(cid)
    assert covers.cover_path(cid) is None
    assert covers.cover_version(cid) == ""


def test_replacing_across_extensions_leaves_one_file(cid):
    first = _png(color=(10, 20, 30))
    second = _png((5, 5), color=(200, 100, 50))  # visibly different from `first`
    covers.put_cover(cid, first, "png")
    covers.put_cover(cid, second, "jpg")
    d = campaigns.campaign_root(cid) / "assets"
    assert [p.name for p in sorted(d.iterdir())] == ["cover.jpg"]
    read_back = covers.cover_path(cid).read_bytes()
    assert read_back == second
    assert read_back != first


def test_unsupported_extension_rejected(cid):
    with pytest.raises(ValueError):
        covers.put_cover(cid, _png(), "svg")


def test_unknown_campaign_raises_and_creates_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    for call in (lambda: covers.cover_path("ghost"),
                 lambda: covers.cover_version("ghost"),
                 lambda: covers.put_cover("ghost", _png(), "png"),
                 lambda: covers.delete_cover("ghost")):
        with pytest.raises(campaigns.CampaignNotFound):
            call()
    assert not (tmp_path / "campaigns" / "ghost").exists()


def test_foreign_sibling_is_ignored_and_kept(cid):
    covers.put_cover(cid, _png(), "png")
    stray = campaigns.campaign_root(cid) / "assets" / "cover.txt"
    stray.write_text("sync conflict", encoding="utf-8")
    import os
    os.utime(stray, (2 ** 31, 2 ** 31))  # newest, so a naive glob would pick it

    assert covers.cover_path(cid).name == "cover.png"
    # A replace's stale-sibling cleanup must stay scoped to supported
    # extensions too -- pin that at the `covers` layer, not only at `assets`.
    covers.put_cover(cid, _png((5, 5)), "jpg")
    assert stray.exists()
    covers.delete_cover(cid)
    assert stray.exists()


def test_delete_raises_when_the_file_survives(cid, monkeypatch):
    """A held file on Windows must not answer 'removed'."""
    covers.put_cover(cid, _png(), "png")
    monkeypatch.setattr("pathlib.Path.unlink",
                        lambda self, *a, **k: (_ for _ in ()).throw(OSError("held")))
    with pytest.raises(OSError, match="could not be removed"):
        covers.delete_cover(cid)


def test_cover_version_survives_a_vanishing_file(cid, monkeypatch):
    """It runs once per row in GET /campaigns; a stat race may not 500 the list.

    Patches `Path.stat` itself, not `assets.image_version` -- patching
    `image_version` only proves `cover_version`'s `except OSError` fires, not
    that it survives the actual race the spec describes (the file vanishing
    between `cover_path`'s resolution and the `stat()` call inside
    `image_version`). `_mtime_ns`, which `cover_path` -> `path_in` uses to rank
    siblings, already swallows a stat failure on its own (that's what lets
    resolution tolerate a concurrent unlink at all), so patching every
    `Path.stat` call still lets resolution succeed and only trips the
    unguarded `stat()` inside `image_version`.
    """
    covers.put_cover(cid, _png(), "png")
    p = covers.cover_path(cid)
    real_stat = pathlib.Path.stat

    def flaky_stat(self, *a, **k):
        if self == p:
            raise OSError("gone")
        return real_stat(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "stat", flaky_stat)
    assert covers.cover_version(cid) == ""


def test_validate_returns_the_extension_of_the_decoded_format(cid):
    """The bytes name the extension, so the caller never has to trust a filename."""
    assert covers.validate(_png()) == "png"

    for fmt, ext in (("JPEG", "jpg"), ("GIF", "gif"), ("WEBP", "webp")):
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, fmt)
        assert covers.validate(buf.getvalue()) == ext


def test_validate_rejects_a_decodable_image_in_an_unsupported_format(cid):
    """A BMP decodes fine, so only the format check can stop it -- and it must:
    stored as `cover.bmp` it would be served as octet-stream and packed into
    the EPUB manifest with a media type nothing declares."""
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, "BMP")
    with pytest.raises(covers.CoverInvalid, match="bmp"):
        covers.validate(buf.getvalue())


def test_validate_rejects_non_image_bytes(cid):
    with pytest.raises(covers.CoverInvalid):
        covers.validate(b"not an image at all")


def test_validate_rejects_an_oversized_body(cid):
    with pytest.raises(covers.CoverTooLarge):
        covers.validate(b"\x89PNG" + b"\0" * covers.MAX_BYTES)


def test_validate_rejects_an_absurd_raster(cid, monkeypatch):
    """A few hundred KB of PNG can describe a billion pixels, and store.thumbs
    is what eventually decodes it -- inside the Android process."""
    data = _png()
    monkeypatch.setattr(covers, "MAX_PIXELS", 4)  # our 4x4 fixture is 16px
    with pytest.raises(covers.CoverInvalid):
        covers.validate(data)

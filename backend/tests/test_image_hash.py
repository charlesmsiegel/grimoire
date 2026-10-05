"""Pixel identity, version 1 (`store/image_hash.py`).

Every input is a committed synthetic fixture (tests/fixtures/images/), generated
once by `make_fixtures.py` and never re-encoded here: a Pillow encoder upgrade
must not be able to move a pinned id. `pixel_ids.json` is the pin. A pinned id
that moves after a dependency upgrade is the signal to decide, not to re-pin
(spec 1.3).
"""

from __future__ import annotations

import hashlib
import io
import json
import random
from pathlib import Path

import pytest
from PIL import Image

from grimoire.store import image_hash, image_sanitize, thumbs
from tests.fixtures.images import make_fixtures

FIX = Path(__file__).parent / "fixtures" / "images"
_FIXTURE_FILES = sorted(make_fixtures.NAMES)


def _read(name: str) -> bytes:
    return (FIX / name).read_bytes()


def _ident(data: bytes) -> image_hash.PixelIdentity:
    return image_hash.pixel_identity(data, hashlib.sha256(data).hexdigest())


def _id(name: str) -> str:
    return _ident(_read(name)).id


def _png_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


# --- the id shape and the orientation rule moved here -------------------------


def test_id_shape():
    good = "px1-" + "a" * 64
    assert image_hash.ID_RE.match(good)
    assert image_hash.is_image_id(good)
    assert not image_hash.is_image_id(good + "\n")  # \Z, not $
    assert not image_hash.is_image_id("px1-" + "A" * 64)
    assert not image_hash.is_image_id("px2-" + "a" * 64)
    assert not image_hash.is_image_id("px1-" + "a" * 63)
    assert not image_hash.is_image_id(None)
    assert not image_hash.is_image_id(b"px1-" + b"a" * 64)


def test_orientation_moved_not_changed():
    # The three names left thumbs; thumbs asks image_hash. (thumbs' own behaviour
    # is held by tests/test_thumbs.py, which must stay green unchanged.)
    for gone in ("_UPRIGHT", "_ORIENTS", "_orientation"):
        assert not hasattr(thumbs, gone)
    assert {"JPEG", "MPO", "PNG"} == image_hash.ORIENTS
    with Image.open(FIX / "jpeg_rot6.jpg") as im:
        assert image_hash.orientation(im) == Image.Transpose.ROTATE_270
    with Image.open(FIX / "webp_exif6.webp") as im:
        assert image_hash.orientation(im) is None  # browsers ignore WebP EXIF
    with Image.open(FIX / "png_xmp_orient.png") as im:
        assert image_hash.orientation(im) is None  # nor XMP
    with Image.open(FIX / "base.png") as im:
        assert image_hash.orientation(im) is None


# --- what must not move the id ------------------------------------------------


def test_same_pixels_different_png_text_same_id():
    assert _id("png_text.png") == _id("base.png")


def test_jpeg_exif_rotated_equals_physically_rotated():
    # The upright PNG was made from the very raster the JPEG decodes to, so JPEG
    # loss cannot interfere.
    assert _id("jpeg_rot6.jpg") == _id("jpeg_rot6_upright.png")
    # ... and the rotation is what did it: the unrotated raster is another id.
    assert _id("jpeg_rot6.jpg") != _id("jpeg_plain.jpg")


def test_webp_exif_orientation_ignored():
    assert _id("webp_exif6.webp") == _id("webp_plain.webp")
    assert _id("webp_rotated.webp") != _id("webp_plain.webp")


def test_xmp_orientation_ignored():
    assert _id("png_xmp_orient.png") == _id("base.png")


def test_invisible_rgb_normalized():
    assert _id("png_invisible_a.png") == _id("png_invisible_b.png")


def test_png_and_jpeg_same_pixels_share_id():
    # A PNG re-saved from a JPEG's decoded raster, no colour chunks on either.
    assert _id("jpeg_plain.jpg") == _id("jpeg_plain_as.png")


# --- what must move it ---------------------------------------------------------


def test_one_pixel_change_changes_id():
    assert _id("png_onepixel.png") != _id("base.png")


def test_icc_profile_changes_id():
    assert _id("png_icc.png") != _id("base.png")


def test_cicp_changes_id():
    assert _id("png_cicp.png") != _id("base.png")


def test_colour_descriptor_is_tagged_with_its_source():
    # A PNG's colour descriptor is its raw colour chunks; a JPEG's is its ICC
    # bytes. Untagged, an ICC profile crafted to equal a PNG's chunk bytes
    # would merge two pictures a browser reads under different colour rules.
    from tests.fixtures.images.make_fixtures import _noise, insert_before_idat

    gama = (45455).to_bytes(4, "big")
    buf = io.BytesIO()
    _noise(4).save(buf, "JPEG", quality=90, icc_profile=b"gAMA" + gama)
    jpg = buf.getvalue()
    with Image.open(io.BytesIO(jpg)) as im:
        raster = im.convert("RGB")
    raster.info.clear()  # no iCCP carried over: the PNG's only colour chunk is gAMA
    png = insert_before_idat(_png_bytes(raster), b"gAMA", gama)
    with Image.open(io.BytesIO(jpg)) as j, Image.open(io.BytesIO(png)) as p:
        assert j.info["icc_profile"] == image_hash._png_colour(png)  # the probe holds
        assert j.convert("RGBA").tobytes() == p.convert("RGBA").tobytes()
    assert _ident(jpg).id != _ident(png).id
    # Without colour data on either side the descriptor is empty, untagged,
    # and the two formats still share an id.
    assert _id("jpeg_plain.jpg") == _id("jpeg_plain_as.png")


def test_visible_rgb_still_counts():
    # The normalization only blanks RGB under alpha 0.
    assert _id("png_invisible_a.png") != _id("base.png")


# --- animation ----------------------------------------------------------------


def test_animated_frame_change_and_timing_change():
    base = _ident(_read("gif_anim.gif"))
    assert base.animated and base.identity == "pixels"
    assert _id("gif_anim_frame_changed.gif") != base.id
    assert _id("gif_anim_duration_changed.gif") != base.id
    # A missing loop count is not "loop forever": it is one play.
    assert _id("gif_anim_noloop.gif") != base.id


def _two_frames() -> list[Image.Image]:
    """Two flat frames a GIF palette holds exactly, so every format decodes
    them to the same RGBA."""
    return [Image.new("RGB", (6, 4), c) for c in ((255, 0, 0), (0, 0, 255))]


def _anim(fmt: str, **kw) -> bytes:
    f = _two_frames()
    buf = io.BytesIO()
    f[0].save(buf, fmt, save_all=True, append_images=f[1:], duration=[100, 200], **kw)
    return buf.getvalue()


def test_loop_count_is_hashed_as_total_plays():
    # GIF NETSCAPE loop=N repeats N times after the first play; APNG num_plays
    # and WebP's ANIM loop count are the total. 0 is forever in all three.
    gif = {n: _ident(_anim("GIF", loop=n)) for n in (0, 1, 2)}
    apng = {n: _ident(_anim("PNG", loop=n)) for n in (0, 1, 2, 3)}
    webp = {n: _ident(_anim("WEBP", loop=n, lossless=True)) for n in (0, 2)}
    assert all(i.animated for d in (gif, apng, webp) for i in d.values())
    assert gif[1].id == apng[2].id == webp[2].id
    assert gif[2].id == apng[3].id
    assert gif[0].id == apng[0].id == webp[0].id
    assert gif[1].id != apng[1].id
    # No loop extension at all: a GIF plays once.
    assert _ident(_anim("GIF")).id == apng[1].id


def test_a_late_gif_loop_extension_is_opaque():
    # Pillow reads a NETSCAPE loop count only before frame 0; a browser reads
    # it anywhere. Hashing the late one as "no loop" would give a GIF that
    # loops forever the id of one that plays once, so it is opaque instead.
    late = _ident(_read("gif_anim_late_loop.gif"))
    assert late.identity == "bytes" and late.reason == "gif-late-loop"
    assert late.id != _id("gif_anim_noloop.gif")
    # The probe pair, built here from the committed no-extension fixture.
    from tests.fixtures.images.make_fixtures import NETSCAPE_LOOP_0, insert_after_first_image
    plain = _read("gif_anim_noloop.gif")
    probe = insert_after_first_image(plain, NETSCAPE_LOOP_0)
    assert _ident(probe).id != _ident(plain).id
    assert _ident(probe).reason == "gif-late-loop"
    # An ANIMEXTS extension is read the same way.
    animexts = insert_after_first_image(
        plain, b"\x21\xff\x0bANIMEXTS1.0\x03\x01\x00\x00\x00")
    assert _ident(animexts).reason == "gif-late-loop"
    # A loop extension before the first image is the ordinary case.
    assert _ident(_read("gif_anim.gif")).identity == "pixels"


def test_single_frame_gif_is_static_domain():
    single = _ident(_read("gif_single.gif"))
    assert single.identity == "pixels"
    assert single.animated is False
    # The same pixels as a static PNG share the static domain's id.
    with Image.open(FIX / "gif_single.gif") as im:
        png = _png_bytes(im.convert("RGB"))
    assert _ident(png).id == single.id


def test_apng_and_animated_webp_are_animated():
    for name in ("apng_anim.png", "webp_anim.webp"):
        got = _ident(_read(name))
        assert got.animated, name
        assert got.identity == "pixels", name
        assert (got.width, got.height) == (24, 16)


def test_static_dimensions_reported():
    got = _ident(_read("base.png"))
    assert (got.identity, got.reason, got.width, got.height, got.animated) == (
        "pixels", None, 24, 16, False,
    )
    # Dimensions are the displayed ones: orientation 6 swaps them.
    rot = _ident(_read("jpeg_rot6.jpg"))
    assert (rot.width, rot.height) == (16, 24)


# --- opaque identity -----------------------------------------------------------


def _assert_opaque(data: bytes) -> image_hash.PixelIdentity:
    sha = hashlib.sha256(data).hexdigest()
    got = image_hash.pixel_identity(data, sha)
    assert got.identity == "bytes"
    assert got.id == image_hash.opaque_id(sha)
    assert got.reason
    return got


def test_opaque_cases(monkeypatch):
    # 16-bit RGB PNG: Pillow opens it as 8-bit RGB, already truncated, so only
    # the IHDR bytes can say so.
    with Image.open(FIX / "png16_rgb.png") as im:
        assert im.mode == "RGB"
    assert _assert_opaque(_read("png16_rgb.png")).reason == "png-16-bit"
    # CMYK JPEG.
    assert _assert_opaque(_read("jpeg_cmyk.jpg")).reason == "mode-CMYK"
    # Oversized static image.
    monkeypatch.setattr(image_hash, "STATIC_BUDGET", 10)
    assert _assert_opaque(_read("base.png")).reason == "over-budget"
    monkeypatch.undo()
    # PNG magic, no picture.
    assert _assert_opaque(b"\x89PNG\r\n\x1a\n" + b"garbage" * 20).reason == "undecodable"
    assert _assert_opaque(b"a").reason == "undecodable"
    assert _assert_opaque(b"").reason == "undecodable"


def _bomb_warnings(caught) -> list:
    return [w for w in caught if issubclass(w.category, Image.DecompressionBombWarning)]


def test_the_decompression_bomb_warning_is_filtered_at_import():
    # One targeted, import-time filter rather than a per-call catch_warnings
    # (which swaps process-wide state from many threads): identity's own
    # budgets bound every decode, so Pillow's warning is noise. Checked in a
    # fresh interpreter, since pytest restores the warning filters around
    # collection and every test, which would discard an import-time one here.
    import subprocess
    import sys

    probe = (
        "import io, warnings, hashlib\n"
        "from PIL import Image\n"
        "from grimoire.store import image_hash\n"
        "assert any(f[0] == 'ignore' and f[2] is Image.DecompressionBombWarning\n"
        "           for f in warnings.filters)\n"
        "Image.MAX_IMAGE_PIXELS = 24 * 16 - 1\n"
        "b = io.BytesIO(); Image.new('RGB', (24, 16)).save(b, 'PNG')\n"
        "got = image_hash.pixel_identity(b.getvalue(), hashlib.sha256(b.getvalue()).hexdigest())\n"
        "assert got.identity == 'pixels', got\n"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                          timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    assert "DecompressionBombWarning" not in done.stderr


def test_no_decompression_bomb_warning_escapes(monkeypatch):
    # Between Pillow's warning and error thresholds Image.open (and each
    # band's crop) warns on stderr; under the import-time filter none of it
    # escapes, and the picture is still named by its pixels.
    import warnings

    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 24 * 16 - 1)  # warn, not raise
    with warnings.catch_warnings(record=True) as caught:
        image_hash._ignore_bomb_warnings()   # pytest dropped the import-time one
        got = _ident(_read("base.png"))
    assert got.identity == "pixels"
    assert got.id == _id_unpatched("base.png")
    assert not _bomb_warnings(caught)


def _gif_frame_grown_to(w: int, h: int) -> bytes:
    """A two-frame 4x4 GIF whose second frame's descriptor claims `w` x `h` --
    which Pillow answers by growing the canvas when it seeks there."""
    import struct

    frames = [Image.new("P", (4, 4), c) for c in (1, 2)]
    for im in frames:
        im.putpalette([0, 0, 0, 255, 0, 0, 0, 255, 0] + [0] * 759)
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    data = bytearray(buf.getvalue())
    at = [i for i in range(len(data))
          if data[i] == 0x2C and data[i + 5:i + 9] == struct.pack("<HH", 4, 4)][-1]
    data[at + 5:at + 9] = struct.pack("<HH", w, h)
    return bytes(data)


def test_a_gif_frame_that_grows_the_canvas_past_the_budget_is_over_budget():
    # The header says 4x4; the budget check on it passes. A later frame grows
    # Pillow's canvas to 100 MP on seek -- past Pillow's warning threshold
    # (silenced) and the static budget, which the frame walk enforces per frame.
    import warnings

    for w, h in ((10000, 10000), (20000, 20000)):   # Pillow's warning, then its error
        data = _gif_frame_grown_to(w, h)
        with warnings.catch_warnings(record=True) as caught:
            image_hash._ignore_bomb_warnings()   # pytest dropped the import-time one
            got = _ident(data)
        assert (got.identity, got.reason) == ("bytes", "over-budget"), (w, h)
        assert not _bomb_warnings(caught)


def _id_unpatched(name: str) -> str:
    return json.loads((FIX / "pixel_ids.json").read_text())[name]


def test_opaque_modes_other_than_cmyk():
    for mode in ("I", "F", "I;16"):
        buf = io.BytesIO()
        Image.new(mode, (4, 4)).save(buf, "TIFF")
        # TIFF is not a format the store ingests, but the decoder-side check is
        # by mode and must not depend on the container.
        assert _assert_opaque(buf.getvalue()).reason == f"mode-{mode}"


def test_animation_budgets(monkeypatch):
    monkeypatch.setattr(image_hash, "MAX_FRAMES", 1)
    assert _assert_opaque(_read("gif_anim.gif")).reason == "over-budget"
    monkeypatch.undo()
    monkeypatch.setattr(image_hash, "ANIM_AREA_BUDGET", 24 * 16 * 2 - 1)
    assert _assert_opaque(_read("gif_anim.gif")).reason == "over-budget"
    monkeypatch.undo()
    monkeypatch.setattr(image_hash, "ANIM_AREA_BUDGET", 24 * 16 * 2)
    assert _ident(_read("gif_anim.gif")).identity == "pixels"


def test_animated_frame_over_the_static_budget_is_opaque(monkeypatch):
    # Each frame is held to the still budget as well as the total area.
    monkeypatch.setattr(image_hash, "STATIC_BUDGET", 24 * 16 - 1)
    assert _assert_opaque(_read("gif_anim.gif")).reason == "over-budget"
    monkeypatch.setattr(image_hash, "STATIC_BUDGET", 24 * 16)
    assert _ident(_read("gif_anim.gif")).identity == "pixels"


def test_oriented_apng_is_opaque():
    data = _read("apng_oriented.png")
    with Image.open(io.BytesIO(data)) as im:
        assert im.is_animated and image_hash.orientation(im) is not None
    assert _assert_opaque(data).reason == "animated-oriented"
    # The same APNG without the Orientation chunk is hashed by its pixels.
    assert _ident(_read("apng_anim.png")).identity == "pixels"


@pytest.mark.parametrize(
    "name", ["base.png", "png_invisible_a.png", "jpeg_rot6.jpg", "gif_single.gif",
             "gif_anim.gif", "webp_anim.webp", "apng_anim.png"],
)
def test_banded_hashing_is_byte_identical(name, monkeypatch):
    # Rows are hashed in bands so no whole-frame RGBA copy is held; a band
    # boundary (here every row, and a ragged 3 rows) must not move the id.
    pinned = json.loads((FIX / "pixel_ids.json").read_text())[name]
    for pixels in (1, 24 * 3 + 5):
        monkeypatch.setattr(image_hash, "_BAND_PIXELS", pixels)
        assert _id(name) == pinned


def test_animated_webp_without_support_is_opaque(monkeypatch):
    monkeypatch.setattr(image_hash, "_webp_animates", lambda: False)
    assert _assert_opaque(_read("webp_anim.webp")).reason == "webp-animation-unsupported"
    # A still WebP never needed it.
    assert _ident(_read("webp_plain.webp")).identity == "pixels"


def test_webp_support_probe_survives_pillow_12():
    # Pillow 12 has no "webp_anim" feature name (check_feature raises
    # ValueError); 11.x warns. Neither may escape or report "no support" while
    # the WebP codec is there.
    assert image_hash._webp_animates() is True


def test_opaque_id_domain():
    sha = hashlib.sha256(b"x").hexdigest()
    want = "px1-" + hashlib.sha256(b"grimoire-pixels-v1-opaque\0" + sha.encode()).hexdigest()
    assert image_hash.opaque_id(sha) == want
    # Distinct from the pixel domain, and from another input's.
    assert image_hash.opaque_id(sha) != image_hash.opaque_id(hashlib.sha256(b"y").hexdigest())


# --- pins ----------------------------------------------------------------------


def test_fixture_ids_pinned():
    pinned = json.loads((FIX / "pixel_ids.json").read_text())
    assert sorted(pinned) == _FIXTURE_FILES  # a fixture with no pin is a hole
    # ... and a committed image the generator does not own is a stray.
    on_disk = {p.name for p in FIX.iterdir() if p.suffix in {".png", ".jpg", ".gif", ".webp"}}
    assert on_disk == make_fixtures.NAMES
    for name in _FIXTURE_FILES:
        assert _id(name) == pinned[name], name


@pytest.mark.parametrize("name", _FIXTURE_FILES)
def test_sanitize_preserves_identity(name):
    # Spec 1.1: sanitizing is lossless, so it must not change the id.
    data = _read(name)
    clean = image_sanitize.sanitize(data)
    before = _ident(data)
    after = _ident(clean)
    if before.identity == "pixels":
        assert after.id == before.id, name
    else:
        # An opaque id names the bytes; sanitized bytes are the ones ingest
        # would name, and they stay opaque for the same reason.
        assert after.identity == "bytes" and after.reason == before.reason, name


# --- robustness ----------------------------------------------------------------


def test_never_raises():
    rng = random.Random(7)
    samples = [rng.randbytes(rng.randrange(0, 400)) for _ in range(60)]
    samples += [b"\x89PNG\r\n\x1a\n" + rng.randbytes(50), b"\xff\xd8\xff" + rng.randbytes(50),
                b"GIF89a" + rng.randbytes(50), b"RIFF\0\0\0\0WEBP" + rng.randbytes(50)]
    for name in _FIXTURE_FILES:  # truncations of real files
        data = _read(name)
        samples += [data[: len(data) // 2], data[:30]]
    for data in samples:
        got = _ident(data)
        assert image_hash.is_image_id(got.id)
    for data in samples[:60]:
        assert _ident(data).identity == "bytes"


def test_make_fixtures_pins_only_its_own_names(tmp_path, monkeypatch):
    # A stray image beside the fixtures is neither picked up nor pinned.
    for name in make_fixtures.NAMES:
        (tmp_path / name).write_bytes(_read(name))
    (tmp_path / "stray.png").write_bytes(_read("base.png"))
    monkeypatch.setattr(make_fixtures, "HERE", tmp_path)
    make_fixtures.write()
    pinned = json.loads((tmp_path / "pixel_ids.json").read_text())
    assert set(pinned) == make_fixtures.NAMES
    # The committed pins are what the generator writes.
    assert pinned == json.loads((FIX / "pixel_ids.json").read_text())

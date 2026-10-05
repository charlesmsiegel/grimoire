"""Synthetic image fixtures for the pixel-identity (px1) tests.

`build()` returns {filename: bytes} for every fixture. `--write` writes them
next to this file, plus `pixel_ids.json` (filename -> expected id) computed by
`grimoire.store.image_hash`. The files are COMMITTED and the tests read them;
nothing re-encodes at test time, so a Pillow encoder upgrade cannot move a
pinned id. Re-running `--write` is a deliberate act: a changed id is the signal
that a dependency changed a decode (spec 1.3), not something to re-pin quietly.

    cd backend && PYTHONPATH=src .venv/bin/python -m tests.fixtures.images.make_fixtures --write

All content is synthetic: seeded pseudo-random noise and flat colours.
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import struct
import sys
import zlib
from pathlib import Path

from PIL import Image, ImageCms

HERE = Path(__file__).parent
W, H = 24, 16
ORIENT_TAG = 0x0112
SUFFIXES = {".png", ".jpg", ".gif", ".webp"}


def _noise(seed: int, w: int = W, h: int = H, mode: str = "RGB") -> Image.Image:
    rng = random.Random(seed)
    n = len(mode)
    raw = bytes(rng.randrange(256) for _ in range(w * h * n))
    return Image.frombytes(mode, (w, h), raw)


def _palette_frame(seed: int) -> Image.Image:
    """A frame of four flat colours, so a GIF palette holds it exactly."""
    rng = random.Random(seed)
    colours = [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]
    im = Image.new("RGB", (W, H))
    im.putdata([colours[rng.randrange(4)] for _ in range(W * H)])
    return im


def _png(im: Image.Image, **kw) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", **kw)
    return buf.getvalue()


def _chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def insert_before_idat(png: bytes, kind: bytes, payload: bytes) -> bytes:
    """`png` with one extra chunk placed immediately before its first IDAT."""
    pos = 8
    while pos < len(png):
        (length,) = struct.unpack(">I", png[pos : pos + 4])
        if png[pos + 4 : pos + 8] == b"IDAT":
            return png[:pos] + _chunk(kind, payload) + png[pos:]
        pos += 12 + length
    raise ValueError("no IDAT")


def raw_png16_rgb(seed: int) -> bytes:
    """A 16-bit-per-channel RGB PNG built by hand (Pillow cannot write one)."""
    rng = random.Random(seed)
    rows = []
    for _ in range(H):
        row = bytes(rng.randrange(256) for _ in range(W * 6))
        rows.append(b"\x00" + row)  # filter type 0
    ihdr = struct.pack(">IIBBBBB", W, H, 16, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
        + _chunk(b"IEND", b"")
    )


def _exif(orientation: int | None, extra: bool = False) -> bytes:
    ex = Image.Exif()
    if orientation is not None:
        ex[ORIENT_TAG] = orientation
    if extra:
        ex[0x010F] = "Synthetic Camera Co"  # Make
        ex[0x0110] = "Model One"  # Model
    return ex.tobytes()


def _jpeg(im: Image.Image, exif: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    kw = {"exif": exif} if exif else {}
    im.save(buf, "JPEG", quality=90, subsampling=0, **kw)
    return buf.getvalue()


def _webp(im: Image.Image, **kw) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "WEBP", lossless=True, **kw)
    return buf.getvalue()


def _gif(frames: list[Image.Image], durations: list[int], loop: int | None = 0) -> bytes:
    buf = io.BytesIO()
    kw = {"loop": loop} if loop is not None else {}
    frames[0].save(
        buf, "GIF", save_all=True, append_images=frames[1:], duration=durations, **kw
    )
    return buf.getvalue()


def _srgb_icc() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


XMP_ORIENT = (
    b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
    b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about="" '
    b'xmlns:tiff="http://ns.adobe.com/tiff/1.0/" tiff:Orientation="6"/>'
    b"</rdf:RDF></x:xmpmeta><?xpacket end=\"w\"?>"
)


def build() -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    base = _noise(1)
    out["base.png"] = _png(base)
    out["png_text.png"] = insert_before_idat(
        out["base.png"], b"tEXt", b"Comment\x00synthetic note for the fixture"
    )
    out["png_cicp.png"] = insert_before_idat(out["base.png"], b"cICP", b"\x01\x0d\x00\x01")
    out["png_xmp_orient.png"] = insert_before_idat(
        out["base.png"], b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + XMP_ORIENT
    )
    one = base.copy()
    one.putpixel((5, 7), (base.getpixel((5, 7))[0] ^ 1, *base.getpixel((5, 7))[1:]))
    out["png_onepixel.png"] = _png(one)
    out["png_icc.png"] = _png(base, icc_profile=_srgb_icc())

    # Two RGBA images that differ only in the RGB of fully transparent pixels.
    rgba = _noise(2, mode="RGBA")
    px = rgba.load()
    for y in range(H):
        for x in range(W):
            r, g, b, _a = px[x, y]
            px[x, y] = (r, g, b, 0 if (x + y) % 3 == 0 else 255)
    other = rgba.copy()
    opx = other.load()
    for y in range(H):
        for x in range(W):
            if opx[x, y][3] == 0:
                opx[x, y] = (255 - opx[x, y][0], 17, 99, 0)
    out["png_invisible_a.png"] = _png(rgba)
    out["png_invisible_b.png"] = _png(other)

    out["png16_rgb.png"] = raw_png16_rgb(3)

    # JPEG: EXIF Orientation 6 against the same raster stored upright.
    photo = _noise(4)
    out["jpeg_rot6.jpg"] = _jpeg(photo, _exif(6))
    decoded = Image.open(io.BytesIO(out["jpeg_rot6.jpg"]))
    decoded.load()
    out["jpeg_rot6_upright.png"] = _png(decoded.transpose(Image.Transpose.ROTATE_270))
    out["jpeg_plain.jpg"] = _jpeg(photo)
    out["jpeg_plain_as.png"] = _png(Image.open(io.BytesIO(out["jpeg_plain.jpg"])).convert("RGB"))
    out["jpeg_camera_exif.jpg"] = _jpeg(photo, _exif(None, extra=True))
    out["jpeg_cmyk.jpg"] = _jpeg(_noise(5, mode="CMYK"))

    # WebP: EXIF orientation is ignored by browsers, so it must not move the id.
    wp = _noise(6)
    out["webp_plain.webp"] = _webp(wp)
    out["webp_exif6.webp"] = _webp(wp, exif=_exif(6))
    out["webp_rotated.webp"] = _webp(wp.transpose(Image.Transpose.ROTATE_270))

    # GIF: two frames; one changed frame; one changed duration; one frame.
    f1, f2, f3 = _palette_frame(10), _palette_frame(11), _palette_frame(12)
    out["gif_anim.gif"] = _gif([f1, f2], [80, 120])
    out["gif_anim_frame_changed.gif"] = _gif([f1, f3], [80, 120])
    out["gif_anim_duration_changed.gif"] = _gif([f1, f2], [80, 140])
    out["gif_anim_noloop.gif"] = _gif([f1, f2], [80, 120], loop=None)
    buf = io.BytesIO()
    f1.save(buf, "GIF")
    out["gif_single.gif"] = buf.getvalue()

    # APNG and animated WebP.
    a1, a2 = _noise(20), _noise(21)
    buf = io.BytesIO()
    a1.save(buf, "PNG", save_all=True, append_images=[a2], duration=[100, 100], loop=0)
    out["apng_anim.png"] = buf.getvalue()
    buf = io.BytesIO()
    a1.save(
        buf, "WEBP", save_all=True, append_images=[a2], duration=[100, 100], loop=0, lossless=True
    )
    out["webp_anim.webp"] = buf.getvalue()
    # An APNG carrying an eXIf Orientation=6 chunk before its pixel data.
    # (a PNG eXIf chunk holds the bare TIFF block, without the "Exif\0\0" lead)
    out["apng_oriented.png"] = insert_before_idat(
        out["apng_anim.png"], b"eXIf", _exif(6)[len(b"Exif\x00\x00") :]
    )
    return out


def ids(files: dict[str, bytes]) -> dict[str, str]:
    from grimoire.store import image_hash

    return {
        name: image_hash.pixel_identity(data, hashlib.sha256(data).hexdigest()).id
        for name, data in sorted(files.items())
    }


def write() -> None:
    """Write the fixtures that are not on disk yet, then the id pins.

    An existing file is never overwritten: the committed bytes are the fixture
    (the sRGB profile lcms builds carries its creation time, so a rebuild is not
    byte-identical anyway). Ids are computed from what is on disk."""
    for name, data in build().items():
        if not (HERE / name).exists():
            (HERE / name).write_bytes(data)
    on_disk = {p.name: p.read_bytes() for p in HERE.iterdir() if p.suffix in SUFFIXES}
    (HERE / "pixel_ids.json").write_text(json.dumps(ids(on_disk), indent=2) + "\n")


if __name__ == "__main__":
    if "--write" in sys.argv[1:]:
        write()
    else:
        print(__doc__)

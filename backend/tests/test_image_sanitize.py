"""The lossless metadata sanitizer (`store/image_sanitize.py`).

Every input is generated with Pillow here -- nothing is a real person's photo --
and every output is checked two ways: the metadata that must go is gone, and the
decoded pixels did not move.
"""

from __future__ import annotations

import io
import struct
import zlib

from PIL import Image, ImageCms
from PIL.PngImagePlugin import PngInfo

from grimoire.store import image_sanitize
from grimoire.store.image_sanitize import sanitize


def _img(size=(16, 12)) -> Image.Image:
    im = Image.new("RGB", size)
    im.putdata([(x * 9 % 256, y * 17 % 256, (x * y) % 256)
                for y in range(size[1]) for x in range(size[0])])
    return im


def _save(im: Image.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    im.save(buf, fmt, **kw)
    return buf.getvalue()


def _pixels(raw: bytes) -> bytes:
    return Image.open(io.BytesIO(raw)).convert("RGBA").tobytes()


def _exif(orientation: int, gps: bool = True) -> Image.Exif:
    e = Image.Exif()
    e[0x0112] = orientation
    e[0x010F] = "AcmeCam"
    if gps:
        g = e.get_ifd(0x8825)
        g[1] = "N"
        g[2] = (51.0, 30.0, 12.0)
    return e


def _parse_exif(blob: bytes) -> tuple[dict, dict]:
    e = Image.Exif()
    e.load(blob)
    return dict(e), dict(e.get_ifd(0x8825))


def _png_chunks(raw: bytes) -> list[tuple[bytes, bytes]]:
    out, pos = [], 8
    while pos < len(raw):
        (n,) = struct.unpack(">I", raw[pos:pos + 4])
        out.append((raw[pos + 4:pos + 8], raw[pos + 8:pos + 8 + n]))
        pos += 12 + n
    return out


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data)))


def _png_insert(raw: bytes, chunk: bytes, before: bytes) -> bytes:
    pos = 8
    while raw[pos + 4:pos + 8] != before:
        pos += 12 + struct.unpack(">I", raw[pos:pos + 4])[0]
    return raw[:pos] + chunk + raw[pos:]


# --- PNG ---------------------------------------------------------------------

def test_png_text_chunks_dropped():
    info = PngInfo()
    info.add_text("chara", "eyJuYW1lIjoiU2VyYXBoaW5lIn0=")
    info.add_text("ccv3", "eyJzcGVjIjoiY2hhcmFfY2FyZF92MyJ9")
    info.add_text("parameters", "a prompt, steps: 20")
    info.add_text("Comment", "unicode ☃ note", zip=False)  # iTXt
    info.add_itxt("Description", "lang text", lang="en", tkey="Beschreibung")
    src = _save(_img(), "PNG", pnginfo=info)
    assert b"tEXt" in src and b"iTXt" in src
    out = sanitize(src)
    assert b"tEXt" not in out and b"iTXt" not in out and b"zTXt" not in out
    assert b"chara" not in out and b"prompt" not in out
    assert _pixels(out) == _pixels(src)
    assert sanitize(out) == out


def test_png_colour_and_animation_chunks_kept():
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    src = _save(_img(), "PNG", icc_profile=icc)
    out = sanitize(src)
    assert b"iCCP" in out
    assert Image.open(io.BytesIO(out)).info["icc_profile"] == icc

    frames = [_img(), _img().transpose(Image.FLIP_LEFT_RIGHT)]
    apng = _save(frames[0], "PNG", save_all=True, append_images=frames[1:])
    out = sanitize(apng)
    for kind in (b"acTL", b"fcTL", b"fdAT"):
        assert kind in out
    a, b = Image.open(io.BytesIO(apng)), Image.open(io.BytesIO(out))
    assert b.n_frames == a.n_frames == 2
    b.seek(1), a.seek(1)
    assert b.tobytes() == a.tobytes()
    assert sanitize(out) == out


def test_png_other_ancillary_chunks_dropped_and_trailing_data_cut():
    src = _save(_img(), "PNG")
    src = _png_insert(src, _png_chunk(b"tIME", b"\x07\xe8\x01\x01\x00\x00\x00"), b"IDAT")
    src = _png_insert(src, _png_chunk(b"pHYs", struct.pack(">IIB", 2835, 2835, 1)), b"IDAT")
    src = _png_insert(src, _png_chunk(b"prVt", b"private"), b"IDAT")
    out = sanitize(src + b"TRAILING-SECRET")
    kinds = [k for k, _ in _png_chunks(out)]
    assert b"tIME" not in kinds and b"prVt" not in kinds
    assert b"pHYs" in kinds
    assert b"TRAILING-SECRET" not in out
    assert _pixels(out) == _pixels(src)


def test_png_exif_before_idat_keeps_only_orientation():
    src = _save(_img(), "PNG", exif=_exif(6))
    assert b"eXIf" in src and b"AcmeCam" in src
    out = sanitize(src)
    chunks = _png_chunks(out)
    kinds = [k for k, _ in chunks]
    assert kinds.count(b"eXIf") == 1
    assert kinds.index(b"eXIf") < kinds.index(b"IDAT")
    exif = dict(chunks)[b"eXIf"]
    tags, gps = _parse_exif(exif)
    assert tags == {0x0112: 6} and gps == {}
    assert b"AcmeCam" not in out
    assert dict(chunks)[b"IDAT"] == dict(_png_chunks(src))[b"IDAT"]
    assert sanitize(out) == out


def test_png_exif_orientation_1_dropped():
    src = _save(_img(), "PNG", exif=_exif(1))
    assert b"eXIf" in src
    assert b"eXIf" not in sanitize(src)


def test_png_exif_after_idat_dropped():
    src = _save(_img(), "PNG")
    exif = _exif(6).tobytes()[6:]
    src = _png_insert(src, _png_chunk(b"eXIf", exif), b"IEND")
    assert b"eXIf" in src
    out = sanitize(src)
    assert b"eXIf" not in out
    assert _pixels(out) == _pixels(src)


def test_png_bad_crc_returned_as_received():
    src = bytearray(_save(_img(), "PNG", pnginfo=_text_info()))
    pos = bytes(src).index(b"tEXt")
    src[pos + 6] ^= 0xFF  # corrupt the text payload; its CRC no longer matches
    assert sanitize(bytes(src)) == bytes(src)


def _text_info() -> PngInfo:
    info = PngInfo()
    info.add_text("chara", "abc")
    return info


# --- JPEG --------------------------------------------------------------------

def _jpeg_segments(raw: bytes) -> list[tuple[int, bytes]]:
    """(marker, payload) up to and including SOS; payload excludes the length."""
    assert raw[:2] == b"\xff\xd8"
    out, pos = [], 2
    while True:
        marker = raw[pos + 1]
        (n,) = struct.unpack(">H", raw[pos + 2:pos + 4])
        out.append((marker, raw[pos + 4:pos + 2 + n]))
        pos += 2 + n
        if marker == 0xDA:
            return out


def _jpeg_insert(raw: bytes, marker: int, payload: bytes) -> bytes:
    seg = b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload
    return raw[:2] + seg + raw[2:]


def _scan_data(raw: bytes) -> bytes:
    pos = 2
    while True:
        marker = raw[pos + 1]
        (n,) = struct.unpack(">H", raw[pos + 2:pos + 4])
        pos += 2 + n
        if marker == 0xDA:
            return raw[pos:]


def _make_jpeg() -> tuple[bytes, bytes]:
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    src = _save(_img((40, 30)), "JPEG", exif=_exif(3), icc_profile=icc,
                comment=b"shot at the lighthouse", quality=90)
    src = _jpeg_insert(src, 0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>Seraphine</x:xmpmeta>")
    src = _jpeg_insert(src, 0xED, b"Photoshop 3.0\x008BIM-iptc-caption")
    src = _jpeg_insert(src, 0xFE, b"another comment")
    return src, icc


def test_jpeg_strips_xmp_iptc_com_keeps_icc_and_orientation():
    src, icc = _make_jpeg()
    markers = [m for m, _ in _jpeg_segments(src)]
    assert 0xFE in markers and 0xED in markers
    out = sanitize(src)
    segs = _jpeg_segments(out)
    markers = [m for m, _ in segs]
    assert 0xFE not in markers and 0xED not in markers
    app1 = [p for m, p in segs if m == 0xE1]
    assert len(app1) == 1 and app1[0].startswith(b"Exif\x00\x00")
    tags, gps = _parse_exif(app1[0])
    assert tags == {0x0112: 3} and gps == {}
    for secret in (b"AcmeCam", b"xmpmeta", b"lighthouse", b"iptc", b"another comment"):
        assert secret not in out
    # ICC survives and is intact
    assert Image.open(io.BytesIO(out)).info["icc_profile"] == icc
    # pixels, and the scan data itself, are untouched
    assert _pixels(out) == _pixels(src)
    assert _scan_data(out) == _scan_data(src)
    assert sanitize(out) == out


def test_jpeg_orientation_1_drops_exif_entirely():
    src = _save(_img(), "JPEG", exif=_exif(1))
    assert 0xE1 in [m for m, _ in _jpeg_segments(src)]
    out = sanitize(src)
    assert 0xE1 not in [m for m, _ in _jpeg_segments(out)]
    assert b"AcmeCam" not in out
    assert _pixels(out) == _pixels(src)


def test_jpeg_keeps_jfif_and_adobe_and_drops_trailing_data():
    src = _save(_img(), "JPEG")
    src = _jpeg_insert(src, 0xEE, b"Adobe\x00\x64\x00\x00\x00\x00\x01")
    src = _jpeg_insert(src, 0xE5, b"vendor-note")
    out = sanitize(src + b"\xff\xe1\x00\x0aExif\x00\x00zz")
    segs = _jpeg_segments(out)
    markers = [m for m, _ in segs]
    assert 0xE0 in markers and 0xEE in markers and 0xE5 not in markers
    assert out.endswith(b"\xff\xd9")
    assert _pixels(out) == _pixels(src)


def _with_app0(raw: bytes, payload: bytes) -> bytes:
    """`raw` with its leading APP0 replaced by one carrying `payload`."""
    assert raw[2:4] == b"\xff\xe0"
    (n,) = struct.unpack(">H", raw[4:6])
    seg = b"\xff\xe0" + struct.pack(">H", len(payload) + 2) + payload
    return raw[:2] + seg + raw[4 + n:]


_JFIF_HEAD = b"JFIF\x00\x01\x02\x01\x00\x48\x00\x48"   # v1.02, dpi, 72x72


def test_jpeg_jfif_thumbnail_dropped_header_kept():
    thumb = b"THUMBNAILRGB"                             # 2x2 RGB: a picture of its own
    src = _with_app0(_save(_img(), "JPEG"), _JFIF_HEAD + b"\x02\x02" + thumb)
    app0 = [p for m, p in _jpeg_segments(src) if m == 0xE0]
    assert len(app0[0]) == 14 + 12
    out = sanitize(src)
    assert out[2:4] == b"\xff\xe0"
    assert struct.unpack(">H", out[4:6])[0] == 16       # the minimal JFIF segment
    app0 = [p for m, p in _jpeg_segments(out) if m == 0xE0]
    assert app0 == [_JFIF_HEAD + b"\x00\x00"]           # version, units, density kept
    assert thumb not in out
    assert _pixels(out) == _pixels(src)
    assert _scan_data(out) == _scan_data(src)
    assert sanitize(out) == out


def test_jpeg_jfxx_thumbnail_extension_dropped():
    src = _save(_img(), "JPEG")
    jfxx = b"JFXX\x00\x13\x02\x02" + bytes(range(12))   # RGB thumbnail extension
    src = src[:20] + b"\xff\xe0" + struct.pack(">H", len(jfxx) + 2) + jfxx + src[20:]
    assert [p[:5] for m, p in _jpeg_segments(src) if m == 0xE0] == [b"JFIF\x00", b"JFXX\x00"]
    out = sanitize(src)
    assert [p[:5] for m, p in _jpeg_segments(out) if m == 0xE0] == [b"JFIF\x00"]
    assert _pixels(out) == _pixels(src)
    assert sanitize(out) == out


# --- WebP --------------------------------------------------------------------

def _riff_chunks(raw: bytes) -> list[tuple[bytes, bytes]]:
    out, pos = [], 12
    while pos < len(raw):
        (n,) = struct.unpack("<I", raw[pos + 4:pos + 8])
        out.append((raw[pos:pos + 4], raw[pos + 8:pos + 8 + n]))
        pos += 8 + n + (n & 1)
    return out


def test_webp_drops_exif_xmp_and_clears_vp8x_flags():
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    src = _save(_img(), "WEBP", lossless=True, exif=_exif(6).tobytes(),
                xmp=b"<x:xmpmeta>Seraphine</x:xmpmeta>", icc_profile=icc)
    kinds = [k for k, _ in _riff_chunks(src)]
    assert b"EXIF" in kinds and b"XMP " in kinds
    out = sanitize(src)
    chunks = _riff_chunks(out)
    kinds = [k for k, _ in chunks]
    assert b"EXIF" not in kinds and b"XMP " not in kinds and b"ICCP" in kinds
    flags = dict(chunks)[b"VP8X"][0]
    assert flags & 0x08 == 0 and flags & 0x04 == 0
    assert flags & 0x20  # ICC flag survives
    assert struct.unpack("<I", out[4:8])[0] == len(out) - 8
    assert b"Seraphine" not in out and b"AcmeCam" not in out
    assert _pixels(out) == _pixels(src)
    assert sanitize(out) == out


def test_webp_animation_kept():
    frames = [_img(), _img().transpose(Image.FLIP_TOP_BOTTOM)]
    src = _save(frames[0], "WEBP", save_all=True, append_images=frames[1:],
                lossless=True, exif=_exif(6).tobytes())
    out = sanitize(src)
    kinds = [k for k, _ in _riff_chunks(out)]
    assert b"ANIM" in kinds and kinds.count(b"ANMF") == 2 and b"EXIF" not in kinds
    a, b = Image.open(io.BytesIO(src)), Image.open(io.BytesIO(out))
    assert b.n_frames == a.n_frames == 2
    b.seek(1), a.seek(1)
    assert b.convert("RGBA").tobytes() == a.convert("RGBA").tobytes()


def _riff(chunks: list[tuple[bytes, bytes]]) -> bytes:
    body = b"WEBP" + b"".join(k + struct.pack("<I", len(p)) + p + b"\x00" * (len(p) & 1)
                              for k, p in chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_webp_unknown_chunks_dropped():
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    src = _save(_img(), "WEBP", lossless=True, icc_profile=icc)   # extended: VP8X
    chunks = _riff_chunks(src)
    assert chunks[0][0] == b"VP8X"
    src = _riff([*chunks, (b"ABCD", b"Seraphine's note"), (b"abcd", b"odd")])
    assert _pixels(src) == _pixels(_save(_img(), "WEBP", lossless=True, icc_profile=icc))
    out = sanitize(src)
    assert [k for k, _ in _riff_chunks(out)] == [k for k, _ in chunks]
    assert b"Seraphine" not in out and b"odd" not in out
    assert _pixels(out) == _pixels(src)
    assert sanitize(out) == out


def test_webp_unknown_chunk_inside_a_frame_dropped():
    frames = [_img(), _img().transpose(Image.FLIP_TOP_BOTTOM)]
    clean = _save(frames[0], "WEBP", save_all=True, append_images=frames[1:], lossless=True)
    chunks = _riff_chunks(clean)
    i = [k for k, _ in chunks].index(b"ANMF")
    note = b"ABCD" + struct.pack("<I", 9) + b"Seraphine\x00"     # odd size: padded
    chunks[i] = (b"ANMF", chunks[i][1] + note)
    src = _riff(chunks)
    a = Image.open(io.BytesIO(src))
    assert a.n_frames == 2
    out = sanitize(src)
    assert b"Seraphine" not in out and b"ABCD" not in out
    assert out == clean
    b = Image.open(io.BytesIO(out))
    for n in range(2):
        a.seek(n), b.seek(n)
        assert b.convert("RGBA").tobytes() == a.convert("RGBA").tobytes()
    assert sanitize(out) == out


# --- GIF ---------------------------------------------------------------------

def test_gif_drops_comments_keeps_loop():
    frames = [_img((8, 8)).convert("P"), _img((8, 8)).transpose(Image.FLIP_LEFT_RIGHT).convert("P")]
    src = _save(frames[0], "GIF", save_all=True, append_images=frames[1:],
                comment=b"x", loop=0, duration=100)
    assert b"\x21\xfe" in src and b"NETSCAPE2.0" in src
    # a foreign application extension (XMP-style) rides along too
    xmp = b"\x21\xff\x0bXMP DataXMP" + b"\x0d<x>secret</x>" + b"\x00"
    pos = src.index(b"\x21\xff\x0bNETSCAPE2.0")
    src = src[:pos] + xmp + src[pos:]
    out = sanitize(src)
    assert b"\x21\xfe" not in out
    assert b"XMP Data" not in out and b"secret" not in out
    assert b"NETSCAPE2.0" in out
    a, b = Image.open(io.BytesIO(src)), Image.open(io.BytesIO(out))
    assert a.n_frames == b.n_frames == 2
    assert b.info["loop"] == 0
    for i in range(2):
        a.seek(i), b.seek(i)
        assert a.convert("RGBA").tobytes() == b.convert("RGBA").tobytes()
    assert sanitize(out) == out


# --- fallbacks ---------------------------------------------------------------

def test_garbage_and_truncated_returned_as_received():
    assert sanitize(b"a") == b"a"
    assert sanitize(b"") == b""
    assert sanitize(b"\x89PNG") == b"\x89PNG"
    png = _save(_img(), "PNG", pnginfo=_text_info())
    cut = png[:png.index(b"IDAT") + 10]
    assert sanitize(cut) == cut
    jpg = _save(_img(), "JPEG", exif=_exif(3))
    assert sanitize(jpg[:30]) == jpg[:30]
    webp = _save(_img(), "WEBP", lossless=True)
    assert sanitize(webp[:-5]) == webp[:-5]
    gif = _save(_img((8, 8)).convert("P"), "GIF", comment=b"x")
    assert sanitize(gif[:-3]) == gif[:-3]
    not_an_image = b"%PDF-1.7 " + b"x" * 64
    assert sanitize(not_an_image) == not_an_image


def test_never_raises_on_internal_failure(monkeypatch):
    png = _save(_img(), "PNG", pnginfo=_text_info())

    def boom(_raw):
        raise RuntimeError("parser bug")

    monkeypatch.setattr(image_sanitize, "_png", boom)
    assert sanitize(png) == png

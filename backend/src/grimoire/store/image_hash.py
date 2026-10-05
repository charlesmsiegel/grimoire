"""Pixel identity for stored images: `px1-<sha256>` (spec section 1.3).

Two files that show the same picture get the same id, however their containers
differ: text chunks, EXIF camera data, XMP, and the RGB hiding under fully
transparent pixels are not part of what is drawn, so they are not part of the
identity. What *is* drawn is: the raster, the way a browser orients it, and the
colour description that says how its samples are to be read (an ICC profile, a
PNG gAMA/cHRM/sRGB/cICP chunk). The same samples under another profile are a
different picture and a different id.

The leading `1` is the canonicalization version. A change to any rule here is a
`px2`; it never quietly reinterprets `px1`, and tests/fixtures/images/
pixel_ids.json pins the ids so that a dependency upgrade which changes a decode
fails the build rather than redefining identity.

Three domains, each behind its own hash prefix so one can never collide with
another:

- **Static** (`grimoire-pixels-v1`): the oriented RGBA8 raster, with RGB zeroed
  wherever alpha is exactly 0.
- **Animated** (`grimoire-anim-v1`): a GIF, APNG or WebP of more than one frame,
  hashed one composited frame at a time (never all of them in memory) together
  with each frame's duration and the loop count.
- **Opaque** (`grimoire-pixels-v1-opaque`): an input that cannot be reduced to
  canonical pixels without risking a merge of pictures that display differently
  (a many-to-one decode such as CMYK or 16-bit samples), or without unbounded
  memory (a raster over the budget), or that will not decode at all. Its id
  names the exact bytes, so it dedupes only on identical bytes.

Orientation follows the browser, which is `orientation()`: the EXIF block the
file opened with, and only for JPEG, MPO and PNG. It is never
`ImageOps.exif_transpose`, which also honours XMP, WebP EXIF and a PNG `eXIf`
after the pixel data, none of which a browser applies -- honouring them would
merge pictures that display differently. Thumbnails share this rule, which is
why it lives here and `store/thumbs.py` calls it.

`pixel_identity` never raises: any failure is the opaque domain with the reason
`undecodable`. Nothing in this module writes a file.
"""

from __future__ import annotations

import hashlib
import io
import re
import warnings
from dataclasses import dataclass
from typing import Literal

from PIL import Image, features

ID_RE = re.compile(r"px1-[0-9a-f]{64}\Z")

#: A static raster above this many pixels is opaque. 16 MP is 64 MB per RGBA
#: copy, and the transpose plus the conversion can briefly hold two. Set
#: structurally for Android; tune against real devices later.
STATIC_BUDGET = 16_000_000
#: An animated image's total frame area (width x height x frames) above this is
#: opaque; so is one with more than MAX_FRAMES frames.
ANIM_AREA_BUDGET = 64_000_000
MAX_FRAMES = 1000

#: EXIF Orientation -> the transpose that shows the picture upright, as
#: `ImageOps.exif_transpose` maps it (and as a browser draws the original).
_UPRIGHT = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}
#: The formats a browser animates. Only these count as animated: an MPO -- the
#: multi-picture JPEG some cameras write, its second frame a preview or a
#: depth map -- reports several frames too, and a browser draws it as the
#: plain JPEG it starts with. (The same set as `thumbs._ANIMATES`.)
_ANIMATES = frozenset({"GIF", "PNG", "WEBP"})
#: The formats whose EXIF Orientation Chromium -- the Android WebView, and
#: most desktop browsers -- applies when it draws the original. Not WebP: an
#: EXIF chunk in a WebP is drawn as stored, so a thumbnail that turned it
#: would disagree with the original it stands in for.
ORIENTS = frozenset({"JPEG", "MPO", "PNG"})

#: PNG chunks that change what the same samples display as. Read from the raw
#: bytes, before the first IDAT: Pillow 12 drops cICP, and reports the others
#: only partly.
_PNG_COLOUR_CHUNKS = frozenset({b"iCCP", b"gAMA", b"cHRM", b"sRGB", b"cICP"})
_NO_LOOP = 0xFFFFFFFF
_ALPHA_ON = [0] + [255] * 255


@dataclass(frozen=True)
class PixelIdentity:
    id: str
    #: "pixels" for the static and animated domains, "bytes" for opaque.
    identity: Literal["pixels", "bytes"]
    #: Why an opaque id is opaque; None for pixels.
    reason: str | None
    #: The displayed size (after orientation) where the header was readable.
    width: int | None
    height: int | None
    animated: bool


def is_image_id(s: object) -> bool:
    return isinstance(s, str) and ID_RE.match(s) is not None


def opaque_id(byte_sha256: str) -> str:
    return "px1-" + hashlib.sha256(
        b"grimoire-pixels-v1-opaque\0" + byte_sha256.encode("ascii")
    ).hexdigest()


def orientation(im: Image.Image) -> Image.Transpose | None:
    """The transpose that stands `im` upright, read before anything shrinks it.

    Read from the EXIF block the file opened with, and only where a browser
    reads it (`ORIENTS`). Not `im.getexif()`: that also takes an orientation
    from XMP (`tiff:Orientation`), which no browser applies, and on a PNG it
    decodes the whole picture to reach an `eXIf` chunk after the pixel data,
    which a browser does not apply either.

    None for an upright picture -- and for EXIF too damaged to read, which a
    browser shows as stored as well, rather than costing the thumbnail."""
    raw = im.info.get("exif") if im.format in ORIENTS else None
    if not raw or not isinstance(raw, bytes):
        return None
    try:
        exif = Image.Exif()
        exif.load(raw)
        tag = exif.get(0x0112)
    except Exception:  # noqa: BLE001 — a malformed EXIF block is an upright picture, not a failed tile
        return None
    return _UPRIGHT.get(tag) if isinstance(tag, int) else None


def pixel_identity(data: bytes, byte_sha256: str) -> PixelIdentity:
    """The id of `data` -- by its pixels where they can be canonicalized, by its
    bytes (`byte_sha256`) where they cannot. Never raises."""
    try:
        return _identify(data, byte_sha256)
    except Exception:  # noqa: BLE001 — anything that goes wrong is an image we can only name by its bytes
        return _opaque(byte_sha256, "undecodable")


def _opaque(
    byte_sha256: str, reason: str, size: tuple[int, int] | None = None
) -> PixelIdentity:
    w, h = size or (None, None)
    return PixelIdentity(opaque_id(byte_sha256), "bytes", reason, w, h, False)


def _u32(n: int) -> bytes:
    return (n & 0xFFFFFFFF).to_bytes(4, "big")


def _webp_animates() -> bool:
    """Whether this Pillow build decodes animated WebP rather than frame 0 only.

    Pillow 12 has no `webp_anim` feature name (`check_feature` raises
    ValueError, since animation ships wherever WebP does); 11.x warns and
    answers. Both are handled, and an unknown name falls back to the codec."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            f = features.check_feature("webp_anim")
        except ValueError:
            f = None
        return bool(features.check("webp")) if f is None else bool(f)


def _riff_has_anim(data: bytes) -> bool:
    """Whether a RIFF/WebP file declares an `ANIM` chunk -- read from the bytes,
    because a Pillow without animation support reports one frame."""
    pos = 12
    while pos + 8 <= len(data):
        tag = data[pos : pos + 4]
        if tag == b"ANIM":
            return True
        pos += 8 + int.from_bytes(data[pos + 4 : pos + 8], "little") + (data[pos + 4] & 1)
    return False


def _png_colour(data: bytes) -> bytes:
    """`type + payload` of every colour chunk before the first IDAT, in file
    order. Empty with none, which is what lets a PNG with no colour data share
    an id with the same pixels in any other format."""
    out = bytearray()
    pos = 8
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos : pos + 4], "big")
        kind = data[pos + 4 : pos + 8]
        if kind == b"IDAT" or kind == b"IEND":
            break
        end = pos + 8 + length
        if end + 4 > len(data):
            raise ValueError("truncated PNG chunk")
        if kind in _PNG_COLOUR_CHUNKS:
            out += kind + data[pos + 8 : end]
        pos = end + 4
    return bytes(out)


def _colour(im: Image.Image, data: bytes) -> bytes:
    if im.format == "PNG":
        return _png_colour(data)
    profile = im.info.get("icc_profile")
    return profile if isinstance(profile, bytes) else b""


def _normalized(rgba: Image.Image) -> Image.Image:
    """`rgba` with RGB set to (0,0,0) wherever alpha is exactly 0. A mask paste
    rather than a blend, so every other pixel is copied exactly."""
    alpha = rgba.getchannel("A")
    if alpha.getextrema()[0] != 0:
        return rgba  # nothing is transparent
    out = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    out.paste(rgba, mask=alpha.point(_ALPHA_ON))
    return out


def _identify(data: bytes, sha: str) -> PixelIdentity:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        try:
            im = Image.open(io.BytesIO(data))
        except Image.DecompressionBombError:
            return _opaque(sha, "over-budget")
    with im:
        fmt = im.format
        size = im.size
        # Pillow opens a 16-bit RGB PNG already truncated to 8-bit RGB, so the
        # depth can only be read from the IHDR bytes (the byte at offset 24).
        if fmt == "PNG" and data[12:16] == b"IHDR" and data[24] == 16:
            return _opaque(sha, "png-16-bit", size)
        mode = im.mode
        if mode == "CMYK" or mode in ("I", "F") or mode.startswith("I;16"):
            return _opaque(sha, f"mode-{mode}", size)

        # A Pillow without animated-WebP support reads frame 0 only, so the
        # animation is detected from the bytes and refused rather than hashed
        # as the still it would otherwise look like.
        if fmt == "WEBP" and _riff_has_anim(data) and not _webp_animates():
            return _opaque(sha, "webp-animation-unsupported", size)
        n = getattr(im, "n_frames", 1) if fmt in _ANIMATES else 1
        animated = fmt in _ANIMATES and getattr(im, "is_animated", False) and n > 1
        if animated:
            if n > MAX_FRAMES or size[0] * size[1] * n > ANIM_AREA_BUDGET:
                return _opaque(sha, "over-budget", size)
            return _animated(im, data, size, n)
        if size[0] * size[1] > STATIC_BUDGET:
            return _opaque(sha, "over-budget", size)
        return _static(im, data)


def _static(im: Image.Image, data: bytes) -> PixelIdentity:
    color = _colour(im, data)
    turn = orientation(im)
    shown = im.transpose(turn) if turn is not None else im
    out = _normalized(shown.convert("RGBA"))
    w, h = out.size
    digest = hashlib.sha256(
        b"grimoire-pixels-v1\0" + _u32(w) + _u32(h) + _u32(len(color)) + color + out.tobytes()
    ).hexdigest()
    return PixelIdentity("px1-" + digest, "pixels", None, w, h, False)


def _animated(im: Image.Image, data: bytes, size: tuple[int, int], n: int) -> PixelIdentity:
    color = _colour(im, data)
    loop = im.info.get("loop")
    w, h = size
    hasher = hashlib.sha256(
        b"grimoire-anim-v1\0"
        + _u32(w)
        + _u32(h)
        + _u32(_NO_LOOP if loop is None else int(loop))
        + _u32(n)
        + _u32(len(color))
        + color
    )
    for i in range(n):
        im.seek(i)
        frame = _normalized(im.convert("RGBA"))
        if frame.size != size:
            raise ValueError("frame size differs from the canvas")
        hasher.update(_u32(int(im.info.get("duration", 0))) + frame.tobytes())
    return PixelIdentity("px1-" + hasher.hexdigest(), "pixels", None, w, h, True)

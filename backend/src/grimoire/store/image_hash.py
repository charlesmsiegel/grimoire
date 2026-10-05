"""Pixel identity for stored images: `px1-<sha256>` (spec section 1.3).

Two files that show the same picture get the same id, however their containers
differ: text chunks, EXIF camera data, XMP, and the RGB hiding under fully
transparent pixels are not part of what is drawn, so they are not part of the
identity. What *is* drawn is: the raster, the way a browser orients it, and the
colour description that says how its samples are to be read (an ICC profile, a
PNG gAMA/cHRM/sRGB/cICP chunk). The same samples under another profile are a
different picture and a different id. That descriptor is tagged with its source
-- `png\\0` before a PNG's raw colour chunks, `icc\\0` before any other format's
ICC profile -- so the two can never be confused; an image with no colour data
has an empty, untagged descriptor, which is what lets the same pixels share an
id across formats.

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
  with each frame's duration and the total number of plays (0 for forever),
  which is how a browser reads each container's own loop field.
- **Opaque** (`grimoire-pixels-v1-opaque`): an input that cannot be reduced to
  canonical pixels without risking a merge of pictures that display differently
  (a many-to-one decode such as CMYK or 16-bit samples), or without unbounded
  memory (a raster over the budget), or that will not decode at all. Its id
  names the exact bytes, so it dedupes only on identical bytes. A GIF whose
  loop extension comes after its first image is opaque too (`gif-late-loop`):
  Pillow never reads that loop count and a browser does, so its pixels alone
  cannot say how it plays. The image store adds one case of its own, a
  container the sanitizer could not parse (`unsanitizable`, via `opaque`).

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

import functools
import hashlib
import io
import re
import threading
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from PIL import Image, features

from . import image_sanitize

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
#: plain JPEG it starts with. `thumbs` asks this set too.
ANIMATES = frozenset({"GIF", "PNG", "WEBP"})
#: The formats whose EXIF Orientation Chromium -- the Android WebView, and
#: most desktop browsers -- applies when it draws the original. Not WebP: an
#: EXIF chunk in a WebP is drawn as stored, so a thumbnail that turned it
#: would disagree with the original it stands in for.
ORIENTS = frozenset({"JPEG", "MPO", "PNG"})

#: PNG chunks that change what the same samples display as. Read from the raw
#: bytes, before the first IDAT: Pillow 12 drops cICP, and reports the others
#: only partly.
_PNG_COLOUR_CHUNKS = frozenset({b"iCCP", b"gAMA", b"cHRM", b"sRGB", b"cICP"})
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
        return opaque(byte_sha256, "undecodable")


def opaque(
    byte_sha256: str, reason: str, size: tuple[int, int] | None = None
) -> PixelIdentity:
    """The opaque-domain identity of the bytes `byte_sha256` names, for a
    caller that already knows they cannot be named by their pixels (the image
    store, for a container the sanitizer returned as received)."""
    w, h = size or (None, None)
    return PixelIdentity(opaque_id(byte_sha256), "bytes", reason, w, h, False)


def _u32(n: int) -> bytes:
    return (n & 0xFFFFFFFF).to_bytes(4, "big")


@functools.cache
def _webp_animates() -> bool:
    """Whether this Pillow build decodes animated WebP rather than frame 0 only.

    Pillow 12 has no `webp_anim` feature name (`check_feature` raises
    ValueError, since animation ships wherever WebP does); 11.x warns and
    answers. Both are handled, and an unknown name falls back to the codec.

    Computed once per process: `warnings.catch_warnings` swaps process-wide
    state and is not thread-safe, and the answer cannot change."""
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
    """The colour descriptor: `png\\0` + a PNG's raw colour chunks, or `icc\\0` +
    any other format's ICC profile. The tag keeps the two sources apart -- an
    ICC profile crafted to equal a PNG's chunk bytes would otherwise merge
    pictures read under different colour rules. With no colour data it is
    `b""`, untagged, so the same pixels share an id across formats."""
    if im.format == "PNG":
        chunks = _png_colour(data)
        return b"png\0" + chunks if chunks else b""
    profile = im.info.get("icc_profile")
    return b"icc\0" + profile if isinstance(profile, bytes) and profile else b""


def _normalized(rgba: Image.Image) -> Image.Image:
    """`rgba` with RGB set to (0,0,0) wherever alpha is exactly 0. A mask paste
    rather than a blend, so every other pixel is copied exactly."""
    alpha = rgba.getchannel("A")
    if alpha.getextrema()[0] != 0:
        return rgba  # nothing is transparent
    out = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    out.paste(rgba, mask=alpha.point(_ALPHA_ON))
    return out


#: Rows hashed at a time. A whole-frame RGBA copy of a 16 MP image is 64 MB, and
#: the decoded source is already held, so the raster is converted, normalized
#: and hashed in bands of about this many pixels instead.
_BAND_PIXELS = 1 << 20


def _feed(update: Callable[[bytes], object], im: Image.Image) -> None:
    """Feed `im` to `update` (a hash's) as RGBA8 rows with RGB zeroed under alpha 0.

    Both steps are per pixel, so doing them a band at a time yields exactly the
    bytes a whole-image conversion would, without ever holding that copy."""
    w, h = im.size
    step = max(1, _BAND_PIXELS // max(1, w))
    for y in range(0, h, step):
        band = _normalized(im.crop((0, y, w, min(h, y + step))).convert("RGBA"))
        update(band.tobytes())


#: Serializes the warnings-filter swap in `_open`. `catch_warnings` replaces
#: process-wide state, so two of ours interleaving could restore each other's
#: filters; this keeps our own entries ordered. It covers only the header parse.
_WARNINGS_LOCK = threading.Lock()


def _open(data: bytes) -> Image.Image:
    """`Image.open` without Pillow's `DecompressionBombWarning`.

    A raster between Pillow's warning and error thresholds would otherwise
    print to stderr before `STATIC_BUDGET` makes it opaque -- or, under
    `-W error`, raise and turn a decodable picture into an "undecodable" one.
    Over-budget input is this module's own decision, so the warning is noise.
    Only the open is covered: every later check Pillow makes is on a size
    already held far below its threshold -- a GIF frame's under `STATIC_BUDGET`,
    a band's crop under `_BAND_PIXELS`."""
    with _WARNINGS_LOCK, warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        return Image.open(io.BytesIO(data))


def _identify(data: bytes, sha: str) -> PixelIdentity:
    try:
        im = _open(data)
    except Image.DecompressionBombError:
        return opaque(sha, "over-budget")
    with im:
        fmt = im.format
        size = im.size
        # Pillow opens a 16-bit RGB PNG already truncated to 8-bit RGB, so the
        # depth can only be read from the IHDR bytes (the byte at offset 24).
        if fmt == "PNG" and data[12:16] == b"IHDR" and data[24] == 16:
            return opaque(sha, "png-16-bit", size)
        # Pillow reads a GIF's loop count only before frame 0; a browser reads
        # it anywhere. One written later would hash as "plays once" while the
        # page loops it forever, so it is named by its bytes instead.
        if fmt == "GIF" and image_sanitize.gif_loop_after_image(data):
            return opaque(sha, "gif-late-loop", size)
        mode = im.mode
        if mode == "CMYK" or mode in ("I", "F") or mode.startswith("I;16"):
            return opaque(sha, f"mode-{mode}", size)
        # The header's size, before anything is loaded. For an animation this is
        # the canvas, which is every frame's size in Pillow, so it holds each
        # frame to the still budget too.
        if size[0] * size[1] > STATIC_BUDGET:
            return opaque(sha, "over-budget", size)

        # A Pillow without animated-WebP support reads frame 0 only, so the
        # animation is detected from the bytes and refused rather than hashed
        # as the still it would otherwise look like.
        if fmt == "WEBP" and _riff_has_anim(data) and not _webp_animates():
            return opaque(sha, "webp-animation-unsupported", size)
        n = getattr(im, "n_frames", 1) if fmt in ANIMATES else 1
        animated = fmt in ANIMATES and getattr(im, "is_animated", False) and n > 1
        if not animated:
            return _static(im, data)
        if n > MAX_FRAMES or size[0] * size[1] * n > ANIM_AREA_BUDGET:
            return opaque(sha, "over-budget", size)
        # Whether a browser rotates an animated PNG by its EXIF is unverified,
        # and an opaque id can never merge two pictures that display apart.
        if orientation(im) is not None:
            return opaque(sha, "animated-oriented", size)
        return _animated(im, data, size, n)


def _static(im: Image.Image, data: bytes) -> PixelIdentity:
    color = _colour(im, data)
    turn = orientation(im)
    shown = im.transpose(turn) if turn is not None else im
    w, h = shown.size
    hasher = hashlib.sha256(
        b"grimoire-pixels-v1\0" + _u32(w) + _u32(h) + _u32(len(color)) + color
    )
    _feed(hasher.update, shown)
    return PixelIdentity("px1-" + hasher.hexdigest(), "pixels", None, w, h, False)


def _total_plays(fmt: str | None, loop: int | None) -> int:
    """How many times a browser plays the animation through, 0 for forever.

    Pillow's `loop` is each container's own field, and they count differently:
    a GIF's NETSCAPE loop count is the repeats AFTER the first play (so N > 0
    is N + 1 plays), and a GIF without that extension plays once; an APNG's
    `num_plays` and a WebP's ANIM loop count are already the total. 0 is
    forever in all three. Both of the latter fields are mandatory wherever the
    file animates at all, so a missing one takes the value Pillow's encoders
    write by default, 0."""
    if fmt == "GIF":
        if loop is None:
            return 1
        n = int(loop)
        return 0 if n == 0 else n + 1
    return 0 if loop is None else int(loop)


def _animated(im: Image.Image, data: bytes, size: tuple[int, int], n: int) -> PixelIdentity:
    color = _colour(im, data)
    plays = _total_plays(im.format, im.info.get("loop"))
    w, h = size
    hasher = hashlib.sha256(
        b"grimoire-anim-v1\0"
        + _u32(w)
        + _u32(h)
        + _u32(plays)
        + _u32(n)
        + _u32(len(color))
        + color
    )
    for i in range(n):
        im.seek(i)
        if im.size != size:
            raise ValueError("frame size differs from the canvas")
        # Loaded first: a WebP frame's duration lands in `info` only once its
        # pixels are decoded, and before that it is the previous frame's.
        im.load()
        hasher.update(_u32(int(im.info.get("duration", 0))))
        _feed(hasher.update, im)
    return PixelIdentity("px1-" + hasher.hexdigest(), "pixels", None, w, h, True)

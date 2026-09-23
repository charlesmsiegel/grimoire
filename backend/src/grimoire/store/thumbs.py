"""Lazily generated, cached downscales of stored images.

The "Appears in" gallery (and other tile shelves) render 96-154px tiles, but
the stored greeting art runs to several MB per file — a character page could
pull 100MB+ of pixels. A tile asks for `?w=320` instead and gets a small WebP.

The cache lives under home()/.cache/thumbs/<generation>/: derived data, safe to
delete, never scanned by the store. Entries are keyed by the source's identity
and stat, so an edited source simply maps to a new entry and the old one goes
unreferenced -- and by the encoder settings, so a change to those does too.

This function honours whatever width it is handed; keeping the number of
distinct widths small is the route's job (`routes.common.THUMB_BUCKETS`),
since every distinct width is another entry and another cold resize per
picture.

A cold tile is paid while a grid waits on it, which is what the rest of this
module is arranged around:

- **The key travels with the library.** It names the source by its path
  *relative to home()*, not its absolute path. The cache sits inside the
  library, so a library moved to another folder (the Storage-location setting)
  or synced to another device carries its thumbnails along -- and with an
  absolute key it could hit none of them: every device regenerated every
  picture, and the synced cache filled with entries no device could reach. A
  source outside home() has nothing to share and keeps its absolute path.
- **Entries are grouped by generation** (`generation()`), and a generation
  older than this process's REVISION is garbage by construction. The first
  miss for a library starts one background `sweep` that retires them, so an
  encoder or key change stops costing disk forever. A generation at this
  revision or a later one is left alone: it is another device's, on a library
  synced between them, and retiring it would cost that device its whole cache
  at every one of this process's starts. The current generation is not
  pruned: an edited source's old entry still sits there unreferenced, since
  telling which entries nothing names any more takes a pass over the whole
  library that no request has a reason to pay for.
- **One decode per key.** Concurrent requests for one entry -- srcset
  candidates, a second tab, a re-render -- wait for the first to publish it
  instead of each decoding the same source (`_single_flight`).
- **Downscale, then convert** (`_downscale`), wherever the order does not
  change the picture: a full-resolution conversion to RGBA is the costliest
  pass there is, and doing it first also loaded a JPEG outright, which is what
  defeats the reduced-size draft decode `thumbnail` asks the JPEG reader for.

A thumbnail stands in for the original on every grid, rail and portrait, so it
has to be the picture a browser would have drawn from the original, not merely
a smaller raster of its bytes:

- **Upright.** A phone photo is stored as the sensor saw it, with an EXIF
  Orientation tag that browsers apply. A thumbnail carries no EXIF, so the
  orientation is applied here (`_UPRIGHT`) -- otherwise a portrait taken on a
  phone lies on its side on every surface that draws the thumbnail.
- **In its own colours.** An RGB colour profile (Display P3, Adobe RGB) is
  embedded in the thumbnail as it was in the original; dropped, the browser
  reads the pixels as sRGB and every colour shifts.
- **Moving, if it moved.** An animated GIF, PNG or WebP gets no thumbnail at
  all, and the route serves the original: a still of its first frame would
  stop it animating, and a downscale of every frame is a cold cost no grid
  should wait on.
- **Encodable here.** Not every Pillow can write WebP -- Chaquopy's Android
  wheel has no libwebp -- so a build without it writes JPEG, or PNG where
  there is alpha (`_encoding`), rather than decoding every source only to fail
  the save and serve the original anyway.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import io
import logging
import os
import re
import threading
from collections.abc import Iterator
from pathlib import Path

from PIL import Image, features

from . import atomic
from .paths import home

_log = logging.getLogger(__name__)

QUALITY = 80
#: libwebp's effort setting, 0 (fastest) to 6. Pillow defaults to 4; 2 costs
#: markedly less CPU per cold tile for a few percent more bytes, and a cold
#: tile is paid while a grid is waiting on it -- a few KB more per tile is the
#: cheaper side of that trade.
METHOD = 2
#: Part of every cache key. The key otherwise names only the source (path,
#: mtime, size) and the width, none of which an encoder change moves, so
#: without this a cache written under old settings would be served as though
#: it were the new. Derived from the two settings rather than written beside
#: them, so bumping either one is a new entry by construction.
ENCODER = f"webp-q{QUALITY}-m{METHOD}"
#: The JPEG quality where there is no WebP encoder (`_encoding`) -- about what
#: WebP's 80 looks like, at a few more bytes.
JPEG_QUALITY = 85
#: ENCODER's counterpart on a Pillow without WebP: JPEG for an opaque picture,
#: PNG (lossless, so it has no setting to name) for one with alpha.
FALLBACK_ENCODER = f"jpeg-q{JPEG_QUALITY}-png"
#: The pipeline's revision, bumped whenever what a `?w=` request answers for
#: an unchanged source changes: an encoder setting, what a key names, or what
#: the decode does to the picture. 2 made the key store-relative; 3 turned a
#: thumbnail upright, kept its colour profile and left animation to the
#: original.
#:
#: It does two jobs. It orders generations, so a sweep retires only those
#: OLDER than its own (`sweep`): two devices on one synced library, one a
#: build behind, would otherwise each delete the other's cache at every
#: process start. And the client puts it in every `?w=` URL
#: (`THUMB_REV` in `frontend/src/api/thumbs.ts`, held equal by
#: test_thumbs.py), since a `?v=` thumbnail is cached immutable and a browser
#: that holds one made the old way would never ask for the new.
REVISION = 3

#: The shape of an entry's name.
_ENTRY = re.compile(r"[0-9a-f]{32}\.(?:webp|jpg|png)")
#: The shape of a generation's name: its revision first, so any build can tell
#: whether a generation it did not write is older than its own. Every
#: generation from REVISION 3 on is named this way; a directory that is not is
#: from before that, and is retired as such.
_GENERATION = re.compile(r"r(\d+)-.+")


def _ours(name: str) -> bool:
    """Is `name` something this module writes under a generation?

    An entry, or the temp `atomic` stages one in (`.<entry>.<random>.tmp`),
    which a crash mid-write leaves behind. The sweep deletes nothing else --
    this cache sits in a folder the user may sync or put things in, and a file
    of any other shape is not ours to remove. What a temp looks like is asked
    of `atomic.is_write_temp`, declared beside the code that makes one, rather
    than spelled again here to drift the day that suffix changes."""
    if _ENTRY.fullmatch(name):
        return True
    return atomic.is_write_temp(Path(name)) and bool(_ENTRY.fullmatch(name[1:].rsplit(".", 2)[0]))


@functools.cache
def _encodes_webp() -> bool:
    """Can this Pillow write WebP?

    Pillow registers the WebP writer only when it was built against libwebp,
    and the Android build is not: Chaquopy's wheel ships the plugin's Python
    but not its `_webp` module. Asked once, since it is a property of the
    build rather than of any picture."""
    return features.check_module("webp")


def _encoder() -> str:
    """The encoder this process writes with (ENCODER, or FALLBACK_ENCODER
    where Pillow has no WebP)."""
    return ENCODER if _encodes_webp() else FALLBACK_ENCODER


def generation() -> str:
    """The directory, under the cache root, this process writes entries to.

    Read live rather than frozen at import, so it follows ENCODER. A phone and
    a desktop sharing a library write different generations at one REVISION
    -- one of them cannot make the other's format -- and neither retires the
    other's."""
    return f"r{REVISION}-{_encoder()}"


#: The cache root, under a library's home().
_CACHE = (".cache", "thumbs")


def _key(src: Path, st: os.stat_result, width: int, root: Path) -> str:
    """The entry name for `src` at `width`: its identity, stat and the encoder.

    The source is named relative to the library root, in posix form, so the
    same picture has the same key from any folder the library is opened from,
    on any OS. Its mtime is taken to the microsecond: NTFS keeps 100 ns ticks
    where ext4 and APFS keep nanoseconds, so a copy synced across that boundary
    loses its last two digits, and a key that named them would miss on every
    file the sync carried. A microsecond still tells apart any two writes this
    app can make to one path, and the size rides along with it. A sync that
    rounds harder than that (whole seconds, as some do) costs that device one
    regeneration per picture -- what every device paid before -- not a wrong
    thumbnail.

    The prefix is compared as a string rather than with `Path.relative_to`,
    which costs several times the rest of a warm lookup: every path the routes
    hand in was built from this same home(), and one spelled differently only
    gets the absolute key -- non-portable, never wrong.
    """
    name, prefix = str(src), f"{root}{os.sep}"
    if name.startswith(prefix):
        name = name[len(prefix):].replace(os.sep, "/")
    # else: outside the library, where no other device will ask for it
    ident = f"{name}|{st.st_mtime_ns // 1000}|{st.st_size}|{width}|{_encoder()}"
    return hashlib.sha256(ident.encode()).hexdigest()[:32]


# ---- one decode per key ----
class _Flight:
    """One entry being generated: its lock, how many requests hold or wait on
    it, and whether the request that held it gave up.

    `failed` is what keeps a source that will not decode from being decoded
    once per waiter, one after another: without it, six askers for a truncated
    picture would each take the lock in turn and fail it again -- serially,
    where before this lock they at least failed side by side. It lives only as
    long as the flight, so the next request after it retries."""

    __slots__ = ("failed", "lock", "users")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.users = 0
        self.failed = False


#: Output path -> its flight. An entry lives exactly as long as someone is
#: using it, so the map is bounded by the requests in flight, not by the number
#: of pictures ever asked for.
_flights: dict[str, _Flight] = {}
_flights_guard = threading.Lock()


@contextlib.contextmanager
def _single_flight(key: str) -> Iterator[_Flight]:
    """Hold `key`'s lock: one request generates an entry while any other that
    wants the same one waits, then finds it published -- or finds the flight
    marked failed, and serves the original as the request it waited on did.

    Per key, so two different tiles never wait on each other -- a grid's cold
    pass runs as wide as the server's threadpool lets it. In-process only, like
    every lock in this app; two devices on one synced library can still both
    generate an entry, which costs a duplicate decode and publishes equal bytes.
    """
    with _flights_guard:
        flight = _flights.setdefault(key, _Flight())
        flight.users += 1
    try:
        with flight.lock:
            yield flight
    finally:
        with _flights_guard:
            flight.users -= 1
            if not flight.users:
                del _flights[key]


# ---- retired generations ----
#: Library roots this process has already started a sweep for. Per root rather
#: than once per process, because the Storage-location setting repoints home()
#: without a restart and the library it lands on has its own old generations.
_swept: set[str] = set()
_swept_guard = threading.Lock()


def _sweep_in_background(root: Path) -> None:
    """Start `sweep(root)` on a daemon thread, the first time this process
    misses the cache of `root`.

    On a miss because that is when the cache is being written anyway, and a
    library that only ever hits has nothing new to sweep. In the background
    because an old generation holds an entry per picture per bucket, and the
    request that found the miss is a tile a grid is waiting on. A daemon
    because a sweep cut short by shutdown loses nothing: whatever it did not get
    to is swept by the next process.
    """
    with _swept_guard:
        if str(root) in _swept:
            return
        _swept.add(str(root))
    # No thread to be had is no reason to fail the tile; the next process sweeps.
    with contextlib.suppress(RuntimeError):
        threading.Thread(target=sweep, args=(root,), name="thumb-sweep", daemon=True).start()


def _drop(entry: os.DirEntry[str]) -> int:
    """Delete one of our files; its size, or 0 if it is not ours or would not go.

    `is_file(follow_symlinks=False)` is false for a symlink, so a link is never
    removed and never followed -- what it points at is somewhere this cache
    does not own."""
    try:
        if not _ours(entry.name) or not entry.is_file(follow_symlinks=False):
            return 0
        size = entry.stat(follow_symlinks=False).st_size
        os.unlink(entry.path)
    except OSError:
        return 0
    return size


def _retired(name: str) -> bool:
    """Is the generation directory `name` older than this process's?

    Older is a lower REVISION, or a name from before generations carried one.
    The same revision under another encoder is a sibling -- a phone's JPEG
    beside a desktop's WebP -- and a higher one is a newer build's."""
    m = _GENERATION.fullmatch(name)
    return m is None or int(m.group(1)) < REVISION


def sweep(root: Path) -> int:
    """Delete what earlier generations left in `root`'s cache; the bytes freed.

    Retired means the entries the flat layout before generations wrote
    directly under the cache root, and each generation directory older than
    this process's (`_retired`), which is removed once it is empty. Only files
    of the cache's own shape (`_ours`) are deleted, one level deep, and no
    symlink is followed -- a directory or file this module would never have
    written stays, and so does the directory holding it.

    Fail-soft throughout: an entry that will not go is skipped, and a cache
    that cannot be listed is left as it is. A failed sweep costs disk, which
    the next process's sweep gets another try at; it never costs a request.

    Two devices sharing a synced library on different versions: the newer
    never loses its cache to the older, which reads a higher revision in its
    generation's name and leaves it be. The older still loses its own to
    the newer, one regeneration per picture viewed per process start, until it
    is updated too -- which is the one way round that ends.
    """
    base = root.joinpath(*_CACHE)
    current = generation()
    freed = removed = 0
    try:
        with os.scandir(base) as it:
            top = list(it)
    except OSError:
        return 0
    for entry in top:
        if entry.name == current:
            continue
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        if not is_dir:
            n = _drop(entry)
            freed, removed = freed + n, removed + bool(n)
            continue
        if not _retired(entry.name):
            continue
        try:
            with os.scandir(entry.path) as it:
                inner = list(it)
        except OSError:
            continue
        for sub in inner:
            n = _drop(sub)
            freed, removed = freed + n, removed + bool(n)
        with contextlib.suppress(OSError):  # not empty: something here is not ours
            os.rmdir(entry.path)
    if removed:
        _log.info("thumbnail cache: retired %d entries (%d KB) from earlier generations",
                  removed, freed // 1024)
    return freed


# ---- the downscale ----
#: Modes a downscale can run in directly, converting only the small result.
#: Resampling works per channel, so shrinking L and converting to RGB is the
#: same picture as converting and then shrinking -- at a fraction of the cost,
#: since the full-resolution pass is the one that goes.
#:
#: CMYK is not one of them, although it is four plain channels. Pillow takes it
#: to RGB as (255-C)(255-K)/255 per channel: a product of ink and black, not a
#: sum, so an average of CMYK pixels converts to something other than the
#: average of their colours, off by how much C and K vary *together* under the
#: kernel. Photographs barely move; line work does -- a dark line over a pale
#: ground is high C and high K against low and low, and shrinking it in CMYK
#: comes out uniformly darker (several levels of mean luminance at the tile
#: buckets). Pillow's own RGB->CMYK writes K=0, so art made that way cannot
#: show it; any real print file can. The JPEG draft goes with it, since DCT
#: scaling averages in the stored colour space too.
_DOWNSCALE_FIRST = frozenset({"RGB", "RGBA", "L", "LA"})
#: The modes whose alpha must be premultiplied for a resample to be correct:
#: an unpremultiplied average bleeds the colour of transparent pixels into the
#: edge. Pillow's own resize does this for RGBA and LA but then skips the
#: reducing pass `thumbnail` gives every other mode; premultiplying here first
#: lets the reduce run on the premultiplied pixels, where it is correct.
_PREMULTIPLIED = {"RGBA": "RGBa", "LA": "La"}


def _has_alpha(im: Image.Image) -> bool:
    """Pillow 10.1's `has_transparency_data`, spelled out: pyproject allows 10.0."""
    if im.mode in ("LA", "La", "PA", "RGBA", "RGBa") or "transparency" in im.info:
        return True
    palette = im.palette if im.mode == "P" else None
    return palette is not None and palette.mode.endswith("A")


def _downscale(im: Image.Image, width: int) -> Image.Image:
    """`im` fitted inside width x width (never upscaled), in RGB or RGBA.

    Downscales first wherever that gives the same picture (`_DOWNSCALE_FIRST`).
    Converts first only where resampling in the source mode would be wrong:
    a palette (P, PA) or bilevel ("1") image resizes nearest-neighbour; I;16
    and the other wide modes reach 8 bits by clipping, and CMYK reaches RGB by
    a product (`_DOWNSCALE_FIRST`), both of which a resample in between would
    move; and a colour key ("transparency" outside RGB/RGBA) names exact values
    that a resample blends away. RGB keeps ignoring a colour key, as it did
    when everything else converted first. The target is RGBA only where there
    is alpha to keep -- an opaque palette image went to RGBA for nothing,
    paying a fourth channel and the premultiplied resample.
    """
    if im.mode in ("RGB", "RGBA"):
        target = im.mode
    else:
        target = "RGBA" if _has_alpha(im) else "RGB"
        if im.mode not in _DOWNSCALE_FIRST or "transparency" in im.info:
            im = im.convert(target)
    straight, premultiplied = im.mode, _PREMULTIPLIED.get(im.mode)
    if premultiplied and max(im.size) > width:
        # Only when something shrinks: a picture already inside the box would
        # pay premultiplication's precision loss (wherever alpha is low) for
        # nothing. Back to straight alpha before anything else: Pillow has no
        # conversion from La to RGBA.
        im = im.convert(premultiplied)
        im.thumbnail((width, width))
        im = im.convert(straight)
    else:
        im.thumbnail((width, width))  # in place, keeps aspect, never upscales; drafts a JPEG
    return im if im.mode == target else im.convert(target)


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
#: plain JPEG it starts with.
_ANIMATES = frozenset({"GIF", "PNG", "WEBP"})


def _orientation(im: Image.Image) -> Image.Transpose | None:
    """The transpose that stands `im` upright, read before anything shrinks it.

    None for an upright picture -- and for EXIF too damaged to read, which a
    browser shows as stored as well, rather than costing the thumbnail."""
    try:
        tag = im.getexif().get(0x0112)
    except Exception:  # noqa: BLE001 — a malformed EXIF block is an upright picture, not a failed tile
        return None
    return _UPRIGHT.get(tag) if isinstance(tag, int) else None


def _rgb_profile(im: Image.Image) -> bytes | None:
    """`im`'s embedded colour profile, if it describes RGB.

    The thumbnail is always RGB, so a profile for any other space would be a
    lie about its pixels: a CMYK print profile, or a Gray one, reaches RGB by
    Pillow's plain conversion and has nothing left to describe. The data colour
    space is the four bytes at offset 16 of every ICC header."""
    icc = im.info.get("icc_profile")
    return icc if isinstance(icc, bytes) and icc[16:20] == b"RGB " else None


def _encoding(small: Image.Image) -> tuple[str, str, dict]:
    """The format, entry suffix and save options for the downscaled `small`.

    WebP wherever Pillow can write it. Where it cannot, JPEG for an opaque
    picture -- a PNG of a photograph is many times the size -- and PNG where
    there is alpha to keep, which JPEG has no way to carry."""
    if _encodes_webp():
        return "WEBP", ".webp", {"quality": QUALITY, "method": METHOD}
    if small.mode == "RGB":
        return "JPEG", ".jpg", {"quality": JPEG_QUALITY}
    return "PNG", ".png", {}


def _published(stem: Path) -> Path | None:
    """The entry already written for `stem`, in whichever format it took.

    One suffix where WebP is written. Two where it is not, since whether a
    picture has alpha -- JPEG or PNG -- is only known once it is decoded."""
    for suffix in (".webp",) if _encodes_webp() else (".jpg", ".png"):
        out = stem.with_name(stem.name + suffix)
        if out.exists():
            return out
    return None


def thumbnail(src: Path, width: int) -> Path | None:
    """Path to a cached downscale of `src` fitted in width x width (never
    upscaled), upright and in its own colours, generating it on first request.
    None if the source is missing, not a decodable image, or animated -- each
    a case where the caller serves the original."""
    try:
        st = src.stat()
    except OSError:
        return None
    root = home()
    # One join, not four: a warm hit is little more than this and a stat, and
    # each pathlib join re-parses the whole path.
    stem = root.joinpath(*_CACHE, generation(), _key(src, st, width, root))
    if out := _published(stem):
        return out
    _sweep_in_background(root)
    with _single_flight(str(stem)) as flight:
        if out := _published(stem):  # published by the request this one waited behind
            return out
        if flight.failed:  # ... or given up on by it: no second decode to fail
            return None
        try:
            with Image.open(src) as im:
                if im.format in _ANIMATES and getattr(im, "is_animated", False):
                    return None
                # Both read off the source before the downscale, which builds
                # new images that need not carry its metadata along.
                upright, profile = _orientation(im), _rgb_profile(im)
                small = _downscale(im, width)
                if upright is not None:
                    small = small.transpose(upright)
                fmt, suffix, options = _encoding(small)
                # Encode to memory, then publish through the shared writer. PIL
                # accepts a file object, so nothing ever hands out the temp's
                # *pathname* -- which is what let an attacker with write access
                # to the cache dir swap a symlink in before im.save() opened it
                # (PR review). A tile is a few KB; buffering it is free.
                #
                # The profile and the EXIF are both said outright: PNG would
                # otherwise copy whatever profile the image still carries, and
                # an orientation tag on a picture already turned upright would
                # turn it again.
                buf = io.BytesIO()
                small.save(buf, format=fmt, icc_profile=profile, exif=b"", **options)
            out = stem.with_name(stem.name + suffix)
            out.parent.mkdir(parents=True, exist_ok=True)
            atomic.write_bytes(out, buf.getvalue())
        except Exception:  # noqa: BLE001 — undecodable/corrupt image: no thumb, caller serves original
            flight.failed = True
            return None
    return out

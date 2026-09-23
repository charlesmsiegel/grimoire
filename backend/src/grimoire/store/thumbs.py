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
- **Entries are grouped by generation** (`generation()`), and a generation this
  process does not write is garbage by construction. The first miss for a
  library starts one background `sweep` that retires them, so an encoder or
  key change stops costing disk forever. The current generation is not
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
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
import os
import re
import threading
from collections.abc import Iterator
from pathlib import Path

from PIL import Image

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
#: What a key *names*, as ENCODER is what an entry's bytes are: bumped when the
#: key changes meaning (2 is the store-relative path), so that what the old
#: scheme wrote lands outside the current generation and gets swept rather
#: than sitting beside the new entries unreachable.
KEY_SCHEME = 2

#: The shape of everything this module writes under a generation: an entry,
#: and the temp `atomic` stages one in (`.<entry>.<8 chars>.tmp`), which a
#: crash mid-write leaves behind. The sweep deletes nothing else -- this cache
#: sits in a folder the user may sync or put things in, and a file of any other
#: shape is not ours to remove.
_OURS = re.compile(r"[0-9a-f]{32}\.webp|\.[0-9a-f]{32}\.webp\.[a-z0-9_]{8}\.tmp")


def generation() -> str:
    """The directory, under the cache root, this process writes entries to.

    Read live rather than frozen at import, so it follows ENCODER."""
    return f"{ENCODER}-k{KEY_SCHEME}"


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
    ident = f"{name}|{st.st_mtime_ns // 1000}|{st.st_size}|{width}|{ENCODER}"
    return hashlib.sha256(ident.encode()).hexdigest()[:32]


# ---- one decode per key ----
#: Output path -> [its lock, how many requests hold or wait on it]. An entry
#: lives exactly as long as someone is using it, so the map is bounded by the
#: requests in flight, not by the number of pictures ever asked for.
_flights: dict[str, list] = {}
_flights_guard = threading.Lock()


@contextlib.contextmanager
def _single_flight(key: str) -> Iterator[None]:
    """Hold `key`'s lock: one request generates an entry while any other that
    wants the same one waits, then finds it published.

    Per key, so two different tiles never wait on each other -- a grid's cold
    pass runs as wide as the server's threadpool lets it. In-process only, like
    every lock in this app; two devices on one synced library can still both
    generate an entry, which costs a duplicate decode and publishes equal bytes.
    """
    with _flights_guard:
        slot = _flights.setdefault(key, [threading.Lock(), 0])
        slot[1] += 1
    try:
        with slot[0]:
            yield
    finally:
        with _flights_guard:
            slot[1] -= 1
            if not slot[1]:
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
        if not _OURS.fullmatch(entry.name) or not entry.is_file(follow_symlinks=False):
            return 0
        size = entry.stat(follow_symlinks=False).st_size
        os.unlink(entry.path)
    except OSError:
        return 0
    return size


def sweep(root: Path) -> int:
    """Delete what earlier generations left in `root`'s cache; the bytes freed.

    Retired means everything under the cache root except the current
    generation: the entries the flat layout before generations wrote directly
    under it, and each other generation's directory, which is removed once it
    is empty. Only files of the cache's own shape (`_OURS`) are deleted, one
    level deep, and no symlink is followed -- a directory or file this module
    would never have written stays, and so does the directory holding it.

    Fail-soft throughout: an entry that will not go is skipped, and a cache
    that cannot be listed is left as it is. A failed sweep costs disk, which
    the next process's sweep gets another try at; it never costs a request.

    Two devices sharing a synced library on different versions each retire
    what the other writes (a version from before generations writes the flat
    layout, which counts as retired too): one regeneration per picture viewed,
    per process start, on each, until both run the same version.
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
#: since the full-resolution pass is the one that goes. CMYK's conversion to
#: RGB is linear until ink clips, so the order moves only pixels at the edge of
#: a clipped region -- and that conversion is Pillow's naive, profile-less one,
#: approximate colour to begin with.
_DOWNSCALE_FIRST = frozenset({"RGB", "RGBA", "L", "LA", "CMYK"})
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
    and the other wide modes reach 8 bits by clipping, which a resample in
    between would move; and a colour key ("transparency" outside RGB/RGBA)
    names exact values that a resample blends away. RGB keeps ignoring a colour
    key, as it did when everything else converted first. The target is RGBA
    only where there is alpha to keep -- an opaque palette image went to RGBA
    for nothing, paying a fourth channel and the premultiplied resample.
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


def thumbnail(src: Path, width: int) -> Path | None:
    """Path to a cached WebP of `src` scaled to fit in width x width (never
    upscaled), generating it on first request. None if the source is missing
    or not a decodable image."""
    try:
        st = src.stat()
    except OSError:
        return None
    root = home()
    # One join, not four: a warm hit is little more than this and a stat, and
    # each pathlib join re-parses the whole path.
    out = root.joinpath(*_CACHE, generation(), f"{_key(src, st, width, root)}.webp")
    if out.exists():
        return out
    _sweep_in_background(root)
    with _single_flight(str(out)):
        if out.exists():  # published by the request this one waited behind
            return out
        try:
            with Image.open(src) as im:
                small = _downscale(im, width)
                # Encode to memory, then publish through the shared writer. PIL
                # accepts a file object, so nothing ever hands out the temp's
                # *pathname* -- which is what let an attacker with write access
                # to the cache dir swap a symlink in before im.save() opened it
                # (PR review). A tile is a few KB; buffering it is free.
                buf = io.BytesIO()
                small.save(buf, format="WEBP", quality=QUALITY, method=METHOD)
            out.parent.mkdir(parents=True, exist_ok=True)
            atomic.write_bytes(out, buf.getvalue())
        except Exception:  # noqa: BLE001 — undecodable/corrupt image: no thumb, caller serves original
            return None
    return out

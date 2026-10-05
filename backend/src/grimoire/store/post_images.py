"""Whether a connection may be sent post images, and how many (#377).

A post can carry a picture (#376): a reader inserts one, or the narrator's art
handle resolves to one. By default every one reaches the model as its alt text
(`context.story._project_history`). With `send_images` on, and a connection
that can read images, the newest `send_images_limit` of them in the history
that is actually sent go out as image parts instead -- see
docs/superpowers/specs/2026-10-05-post-images-to-model-design.md for the whole
design, and `grimoire.content_parts` for the message shape.

## Who can read images

`capability` answers "yes", "no" or "unknown":

- a kind whose client cannot carry a content part (`claude`, which flattens
  every message into one string) is "no", whatever its override says;
- the connection's own `vision` override ("on" / "off") wins otherwise;
- "auto" asks the cached model catalog, which records what the provider
  publishes (`catalog.entry`'s `vision`). No catalog, no matching row, or a
  provider that did not say: "unknown".

"unknown" sends nothing. An image part sent to a text-only endpoint is a 400,
not a graceful degradation, so the cost of guessing wrong runs the other way.
This module never imports the gateway (`llm.py`) -- the store does not (#239)
-- so the store-side list of kinds that can carry a part is
`image_drafts.SUPPORTED_KINDS`, the same list the description drafts use.

The decision to compose a prompt with images is made for the PRIMARY
connection; a vision-capable fallback behind a text-only primary receives text.
At dispatch the same answer is re-asked per attempt, so a turn replayed from a
frozen snapshot sends images only if the setting is on now.

## Prompt caching

An image is part of the prompt prefix a provider caches. While the set of the
newest N images is stable, nothing changes between turns. When a new image
arrives, the oldest sent one reverts to alt text (and a carried one's carrier
message changes), which invalidates the cache from that message on. How far
back that is depends on how sparse the images are: with a picture every few
posts it is near the tail; with N spread across a long scene, the oldest can
sit near the start of the window and one new image invalidates most of the
cached history once. Turning the setting on, changing N, or a budget forcing
an image out has the same one-off effect. Nothing here tries to pin images to
keep a prefix stable.
"""

from __future__ import annotations

import base64
import io
import logging
import threading
from collections import OrderedDict
from pathlib import Path

from PIL import Image, ImageOps, features

from . import config, export, image_drafts, llm_connections

log = logging.getLogger(__name__)

YES, NO, UNKNOWN, OFF = "yes", "no", "unknown", "off"


def capability(conn: dict | None) -> str:
    """"yes", "no" or "unknown" -- whether `conn`'s model reads images. Never
    raises: a catalog that cannot be read is "unknown"."""
    if conn is None or conn.get("kind", "openrouter") not in image_drafts.SUPPORTED_KINDS:
        return NO
    override = conn.get("vision", "")
    if override == "on":
        return YES
    if override == "off":
        return NO
    return _catalog_says(conn)


def _catalog_says(conn: dict) -> str:
    try:
        rows = llm_connections.cached_models(conn.get("id", ""))["models"]
    except Exception:  # noqa: BLE001 - see `capability`: unreadable is unknown
        return UNKNOWN
    model = conn.get("model", "")
    row = next((r for r in rows if isinstance(r, dict) and r.get("id") == model), None)
    vision = row.get("vision") if row is not None else None
    if vision is True:
        return YES
    return NO if vision is False else UNKNOWN


def limit() -> int:
    """`send_images_limit` while `send_images` is on, else 0. A malformed or
    negative limit reads as the default rather than raising, like every other
    numeric knob in config.md."""
    cfg = config.read_config()
    if cfg.get("send_images") != "on":
        return 0
    try:
        n = int(cfg.get("send_images_limit", config.DEFAULT_SEND_IMAGES_LIMIT))
    except (TypeError, ValueError):
        return int(config.DEFAULT_SEND_IMAGES_LIMIT)
    return n if n >= 0 else int(config.DEFAULT_SEND_IMAGES_LIMIT)


def images_for(conn: dict | None) -> int:
    """How many images a prompt for `conn` may carry right now -- the one
    number composition takes, and dispatch re-asks per attempt."""
    n = limit()
    return n if n and capability(conn) == YES else 0


def reach(conn: dict | None) -> str:
    """"off" when the setting sends nothing, else `capability(conn)` -- what
    the Configuration page tells a reader about the connection a turn uses."""
    return OFF if limit() == 0 else capability(conn)


# ---- what gets sent ----
#: Longest side, in pixels, of an image as it is sent. Providers downscale to
#: roughly this before tokenising, so more pixels buy upload time and nothing
#: else -- and it is what keeps N images affordable in memory on a phone.
SEND_EDGE = 1024

#: Ceiling on one encoded image. Its base64 is just under the 5 MB per-image
#: limit Anthropic applies, the tightest of the common providers'.
MAX_SEND_BYTES = 3_750_000

#: Ceiling on the encode cache, in bytes rather than entries: one PNG with
#: alpha can be close to `MAX_SEND_BYTES`, and this also runs on Android.
CACHE_BYTES = 16 * 1024 * 1024

#: `(path, mtime_ns, size)` -> `(media type, encoded bytes)`, least recently
#: used first. A picture stays in the window for many turns, and decoding it
#: again on every one is pointless. Filled from `asyncio.to_thread` workers,
#: hence the lock.
_CACHE: OrderedDict[tuple[str, int, int], tuple[str, bytes]] = OrderedDict()
_LOCK = threading.Lock()


def decodable(ext: str) -> bool:
    """Whether this build's Pillow can decode a file `fetch` sniffed as `ext`.
    WebP is optional in Pillow, and Chaquopy's Android wheel has no libwebp: a
    slot given to a picture that cannot be decoded is a slot an older, sendable
    one was denied."""
    if ext == "webp":
        return bool(features.check("webp"))
    return ext in image_drafts.MEDIA


def eligible(cid: str, url: str) -> bool:
    """Whether `url` names a picture this campaign can send: one of the app's
    own image URLs, resolving to a file whose first bytes sniff as an image this
    build can decode. Checked at composition, so an image that could never be
    sent does not take one of the N slots."""
    path = export.resolve_url(cid, url)
    if path is None:
        return False
    try:
        with path.open("rb") as fh:
            head = fh.read(64)
    except OSError:
        return False
    ext = export.packed_ext(head)
    return ext is not None and decodable(ext)


def _read_capped(path: Path) -> bytes | None:
    """`path`'s bytes, or None past `image_drafts.MAX_BYTES`: one handle and a
    one-byte-past-the-cap read, as `image_drafts.data_uri` does, so a file
    replaced or growing under the read still cannot get past the bound."""
    with path.open("rb") as fh:
        data = fh.read(image_drafts.MAX_BYTES + 1)
    return None if len(data) > image_drafts.MAX_BYTES else data


def _encode(data: bytes) -> tuple[str, bytes]:
    """The first frame, upright, fitted within `SEND_EDGE`, as JPEG -- or PNG
    when it has alpha. Never WebP: not every OpenAI-compatible server decodes
    it, which is why sending does not reuse `thumbs`' cache."""
    with Image.open(io.BytesIO(data)) as src:
        if src.format == "JPEG":
            src.draft("RGB", (SEND_EDGE, SEND_EDGE))
        src.seek(0)
        im = ImageOps.exif_transpose(src)
        im.thumbnail((SEND_EDGE, SEND_EDGE))
        alpha = "A" in im.getbands() or "transparency" in im.info
        out = io.BytesIO()
        if alpha:
            im.convert("RGBA").save(out, "PNG", optimize=True)
            return "image/png", out.getvalue()
        im.convert("RGB").save(out, "JPEG", quality=85)
        return "image/jpeg", out.getvalue()


def _cached(key: tuple[str, int, int]) -> tuple[str, bytes] | None:
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            _CACHE.move_to_end(key)
        return hit


def _remember(key: tuple[str, int, int], value: tuple[str, bytes]) -> None:
    if len(value[1]) > CACHE_BYTES:
        return
    with _LOCK:
        _CACHE[key] = value
        while sum(len(v[1]) for v in _CACHE.values()) > CACHE_BYTES:
            _CACHE.popitem(last=False)


def _encoded(path: Path) -> tuple[str, bytes] | None:
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    hit = _cached(key)
    if hit is not None:
        return hit
    data = _read_capped(path)
    if data is None:
        return None
    media, body = _encode(data)
    if len(body) > MAX_SEND_BYTES:
        return None
    _remember(key, (media, body))
    return media, body


def load(cid: str, part: dict) -> str | None:
    """An `image_ref` part as a `data:` URI for campaign `cid`, or None.

    A `data:` URI rather than a URL because the provider cannot reach this
    machine (`image_drafts`' reason). Never raises: a missing file, an
    undecodable one, Pillow's decompression-bomb refusal or an encoding past
    `MAX_SEND_BYTES` costs that one picture, never the turn -- its alt text is
    already in the prompt.
    """
    try:
        path = export.resolve_url(cid, part.get("url", ""))
        encoded = _encoded(path) if path is not None else None
    except Exception:  # see the docstring: logged, and costs one picture
        log.warning("could not prepare a post image to send", exc_info=True)
        return None
    if encoded is None:
        return None
    media, body = encoded
    return f"data:{media};base64,{base64.b64encode(body).decode('ascii')}"

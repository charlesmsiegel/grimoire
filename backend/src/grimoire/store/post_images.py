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

from . import config, image_drafts, llm_connections

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

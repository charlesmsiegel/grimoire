"""The pictures a greeting can ask the reader to identify.

Ownership describes where bytes live, not who appears in them. Keep the old
asset-name identity for a greeting's own art; other pictures use their URL,
with local cache parameters removed. Assignments remain per greeting, so an
image reused by two greetings does not silently change both. No remote bytes
are fetched here. Collections contribute each available member independently.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from markdown_it import MarkdownIt

from . import assets, characters, entities, greetings, image_collections, pcs, statcache
from .paths import safe_id
from .worlds import paths as worlds_paths


def image_key(root: Path, gid: str, url: str) -> str:
    """A local image's query is cache metadata; a remote query can select art."""
    if url.startswith("/api/worlds/"):
        path = "/".join(quote(unquote(segment), safe="") for segment in urlsplit(url).path.split("/"))
        prefix = f"/api/worlds/{quote(root.name, safe='')}/greetings/{quote(gid, safe='')}/images/"
        return unquote(path.removeprefix(prefix)) if path.startswith(prefix) else path
    return re.sub(r"^https?:", lambda match: match[0].lower(), MarkdownIt().normalizeLink(url), flags=re.IGNORECASE)


def local_path(root: Path, url: str) -> Path | None:
    """Resolve only supported world image routes, never an arbitrary pathname."""
    parts = [unquote(segment) for segment in urlsplit(url).path.split("/")]
    if len(parts) < 6 or parts[:3] != ["", "api", "worlds"] or not safe_id(parts[3]):
        return None
    target = root
    if parts[3] != root.name:
        if not worlds_paths.world_exists(parts[3]):
            return None
        target = worlds_paths.world_root(parts[3])
    try:
        return _record_image_path(target, parts[4:])
    except (OSError, characters.CharacterNotFound, characters.VersionNotFound,
            greetings.GreetingNotFound, entities.EntityNotFound, pcs.PCNotFound, pcs.PCVersionNotFound):
        return None


def _record_image_path(root: Path, parts: list[str]) -> Path | None:
    if len(parts) == 2 and parts[0] == "images":
        return assets.path_in(root / "assets" / "images", parts[1], supported_only=True)
    if (len(parts) == 6 and parts[0] in ("characters", "pcs")
            and parts[2] == "versions" and parts[4] == "images"):
        if not (safe_id(parts[1]) and safe_id(parts[3])):
            return None
        require = characters.require_version if parts[0] == "characters" else pcs.require_version
        require(root, parts[1], parts[3])
        return assets.image_path(root, parts[1], parts[3], parts[5], base=parts[0])
    if len(parts) == 4 and parts[2] == "images" and safe_id(parts[1]):
        if parts[0] == "greetings":
            greetings.read_greeting(root, parts[1])
        elif parts[0] in entities.ENTITY_KINDS:
            entities.read_entity(root, parts[0], parts[1])
        else:
            return None
        return assets.image_path(root, parts[1], "default", parts[3], base=parts[0])
    return None


def _references(root: Path, gid: str) -> list[str]:
    # CommonMark is the frontend's grammar. Inspect image tokens only: raw
    # HTML and code tokens can contain image syntax that is never rendered.
    # Python Markdown's fences/HTML blocks differ, even for ordinary nesting.
    md = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    return [str(image.attrGet("src") or "") for token in md.parse(greetings.read_greeting(root, gid)["body"])
            for image in (token.children or []) if image.type == "image"]


# The shell's TODO count sweeps every greeting. Cache only parsing, under the
# greeting file's stat; image/record existence is still checked on every read.
# A dedicated pool avoids evicting unrelated record caches during that sweep.
_REF_POOL: dict = {}


def _collection_members(url: str) -> list[str]:
    try:
        path = urlsplit(url).path
    except ValueError:
        return []
    collection = re.fullmatch(r"/api/worlds/([^/]+)/image-collections/([0-9a-f]{32})/image", path)
    if not collection or not url.startswith("/api/worlds/"):
        return [url]
    try:
        return [member["url"] for member in image_collections.available(*collection.groups())]
    except (OSError, ValueError, worlds_paths.WorldNotFound):
        return []


def catalog(root: Path, gid: str) -> dict[str, dict]:
    """Legacy stored art plus currently referenced pictures, deduplicated by key.

    Stored art stays taggable even if it is not embedded in the body. Referenced
    art disappears when its reference or its local serving record disappears.
    Markdown is parsed once per greeting, not once per image.
    """
    out: dict[str, dict] = {image["name"]: {} for image in assets.list_images(root, gid, "default", base="greetings")}
    try:
        sig = statcache.signature(root / "greetings" / f"{gid}.md") if safe_id(gid) else None
        refs = statcache.memo("greeting_image_refs", sig, lambda: _references(root, gid),
                              pool=_REF_POOL, max_entries=characters.POOL_ENTRIES)
    except (OSError, greetings.GreetingNotFound):
        return out
    for url in refs:
        for source in _collection_members(url):
            key = image_key(root, gid, source)
            if source.startswith("/api/worlds/"):
                if local_path(root, source) is None:
                    continue
                if key.startswith("/"):
                    out[key] = {"url": key}
            elif urlsplit(source).scheme in ("http", "https") and urlsplit(source).netloc:
                out[key] = {"url": key}
    return out

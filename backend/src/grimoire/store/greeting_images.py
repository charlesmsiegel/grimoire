"""The pictures a greeting can ask the reader to identify.

Ownership describes where bytes live, not who appears in them. Keep the old
asset-name identity for a greeting's own art; other pictures use their URL,
with local cache parameters removed. No remote bytes are fetched here.
Collections contribute each available member independently.

Where an assignment is kept is `image_subjects`' business, and this module
only tells it where each key's picture is (`catalog_with_slots`): a picture
placed in this world, or a member of one of this world's format-2 collections
(which is its object, with no placement), is answered on its image object, so
tagging it in one greeting tags it in every greeting of the world that shows
it. A per-greeting answer in that greeting's sidecar is kept for everything
else: a remote URL, a picture placed in another world or a member of another
world's collection, legacy art with no placement, a placement
whose object or blob has not arrived (the legacy file of the same name beside
it is the picture shown), and an answer whose object write was not confirmed
(R8). A sidecar answer, while present, wins over the object's.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from urllib.parse import quote, unquote, urlsplit

from markdown_it import MarkdownIt

from . import (
    assets,
    characters,
    entities,
    greetings,
    image_collections,
    image_refs,
    pcs,
    statcache,
)
from .paths import safe_id
from .worlds import paths as worlds_paths


def image_key(root: Path, gid: str, url: str) -> str:
    """A local image's query is cache metadata; a remote query can select art."""
    if url.startswith("/api/worlds/"):
        path = "/".join(quote(unquote(segment), safe="") for segment in urlsplit(url).path.split("/"))
        prefix = f"/api/worlds/{quote(root.name, safe='')}/greetings/{quote(gid, safe='')}/images/"
        return unquote(path.removeprefix(prefix)) if path.startswith(prefix) else path
    return re.sub(r"^https?:", lambda match: match[0].lower(), MarkdownIt().normalizeLink(url), flags=re.IGNORECASE)


def _world_target(root: Path, url: str) -> tuple[Path, list[str]] | None:
    """The world root a supported world image URL names and the route parts
    after it, or None -- never an arbitrary pathname."""
    parts = [unquote(segment) for segment in urlsplit(url).path.split("/")]
    if len(parts) < 6 or parts[:3] != ["", "api", "worlds"] or not safe_id(parts[3]):
        return None
    target = root
    if parts[3] != root.name:
        if not worlds_paths.world_exists(parts[3]):
            return None
        target = worlds_paths.world_root(parts[3])
    return target, parts[4:]


_MISSING = (OSError, characters.CharacterNotFound, characters.VersionNotFound,
            greetings.GreetingNotFound, entities.EntityNotFound, pcs.PCNotFound,
            pcs.PCVersionNotFound)


def local_path(root: Path, url: str) -> Path | None:
    """Resolve only supported world image routes, never an arbitrary pathname.
    A collection member's URL is served by `image_collections.member_path`."""
    member = _member_route(url)
    if member is not None:
        try:
            return image_collections.member_path(*member)
        except _MEMBER_MISSING:
            return None
    record = _record(root, url)
    return _record_path(*record) if record is not None else None


def local_target(root: Path, url: str) -> Target:
    """Where the picture behind a supported world image route is, or None
    when nothing is served there: `("slot", (dir, name), image_id)` for a
    record's image (the world library's, for a format-1 collection member),
    `("object", image_id)` for a format-2 member, which is its object.

    Where the picture IS, in any world: whether its tags may be kept there is
    the catalog's question (`catalog_with_slots`), not this one's."""
    member = _member_route(url)
    if member is not None:
        try:
            return _member_target(*member)
        except _MEMBER_MISSING:
            return None
    record = _record(root, url)
    found = _record_file(*record) if record is not None else None
    return ("slot", found[0], found[1]) if found is not None else None


def resolve(target: Target) -> image_refs.ResolvedImage | None:
    """The image behind a target, resolved NOW (object and blob): a slot's
    placement, or the object itself. None for no target, a legacy slot, and
    a picture whose object or blob is not there."""
    if target is None:
        return None
    if target[0] == "object":
        return image_refs.resolve_ref(image_refs.Ref(name="", image=target[1], focus=None))
    return assets.resolve(*target[1])


_Slot = tuple[str, str, str, str]


def _record(root: Path, url: str) -> tuple[Path, _Slot] | None:
    """The world root a supported world image route serves from, and the
    `_record_slot` it names there -- one read of that record -- or None."""
    found = _world_target(root, url)
    if found is None:
        return None
    try:
        slot = _record_slot(*found)
    except _MISSING:
        return None
    return (found[0], slot) if slot is not None else None


def _record_dir(root: Path, slot: _Slot) -> tuple[Path, str]:
    base, owner, vid, name = slot
    if base == "images":
        return _library_dir(root), name
    return assets.version_dir(root, owner, vid, base), name


def _record_path(root: Path, slot: _Slot) -> Path | None:
    base, owner, vid, name = slot
    try:
        if base == "images":
            return assets.path_in(_library_dir(root), name, supported_only=True)
        return assets.image_path(root, owner, vid, name, base=base)
    except _MISSING:
        return None


def _record_file(root: Path, slot: _Slot) -> tuple[tuple[Path, str], str | None] | None:
    """`(the slot's dir and name, its image id)` when the slot holds something
    servable -- the id None for a legacy file -- else None.

    The placement is resolved ONCE, here: a resolving one answers with its own
    id; only one that does not falls back to the legacy file rule
    (`_record_path`), so a catalog never asks the same placement twice.

    A record's directory first finishes any interrupted promotion, as
    `image_path` would have: resolving mid-swap would hand back the picture
    the slot is about to stop holding. The world library has no promotions."""
    d, name = _record_dir(root, slot)
    if slot[0] != "images":
        assets.recover_promotion(d)
    placed = assets.resolve(d, name)
    if placed is not None:
        return (d, name), placed.image_id
    return ((d, name), None) if _record_path(root, slot) is not None else None


def _library_dir(root: Path) -> Path:
    """The world image library's directory under `root` (`world_images.images_dir`,
    for a root already in hand)."""
    return root / "assets" / "images"


def _record_slot(root: Path, parts: list[str]) -> _Slot | None:
    """`(base, owner, vid, name)` of the record image `parts` names, checked
    against the record (a missing one raises its own not-found); base
    ``"images"``, with no owner or version, is the world library."""
    if len(parts) == 2 and parts[0] == "images":
        return "images", "", "", parts[1]
    if (len(parts) == 6 and parts[0] in ("characters", "pcs")
            and parts[2] == "versions" and parts[4] == "images"):
        if not (safe_id(parts[1]) and safe_id(parts[3])):
            return None
        require = characters.require_version if parts[0] == "characters" else pcs.require_version
        require(root, parts[1], parts[3])
        return parts[0], parts[1], parts[3], parts[5]
    if len(parts) == 4 and parts[2] == "images" and safe_id(parts[1]):
        if parts[0] == "greetings":
            greetings.read_greeting(root, parts[1])
        elif parts[0] in entities.ENTITY_KINDS:
            entities.read_entity(root, parts[0], parts[1])
        else:
            return None
        return parts[0], parts[1], "default", parts[3]
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

#: What a collection that is not there, or does not read, raises.
_MEMBER_MISSING = (OSError, ValueError, worlds_paths.WorldNotFound)


def _member_route(url: str) -> tuple[str, str, int] | None:
    """`(wid, collection id, index)` of a collection member URL,
    ``/api/worlds/{wid}/image-collections/{id}/members/{n}``, or None. The
    index is read by `image_collections.member_index`, the one parser of it;
    the collection id is proved by whatever is asked of it next."""
    try:
        parts = [unquote(segment) for segment in urlsplit(url).path.split("/")]
    except ValueError:
        return None
    if (len(parts) != 8 or parts[:3] != ["", "api", "worlds"] or not safe_id(parts[3])
            or parts[4] != "image-collections" or parts[6] != "members"):
        return None
    index = image_collections.member_index(parts[7])
    return None if index is None else (parts[3], parts[5], index)


def _member_target(wid: str, collection_id: str, index: int) -> Target:
    """`local_target` for member `index`: a format-2 member is its object
    (resolved, as `image_collections.available` resolves one), a format-1
    member the world-library slot its name places."""
    manifest = image_collections.read(wid, collection_id)
    members = manifest["members"]
    if not 0 <= index < len(members):
        return None
    if manifest["format"] == 2:
        found = image_refs.resolve_ref(image_refs.Ref(name="", image=members[index], focus=None))
        return ("object", found.image_id) if found is not None else None
    filed = _record_file(worlds_paths.world_root(wid), ("images", "", "", members[index]))
    return ("slot", filed[0], filed[1]) if filed is not None else None


def _collection_members(url: str) -> list[tuple[str, Target]]:
    """The pictures one body reference shows, as `(url, target)`: each
    available member of a collection (`.../image`), else the reference itself.
    A format-2 member carries its object target from the listing, so nothing
    resolves it twice; every other target is None here, for the caller to
    find (`local_target`)."""
    try:
        path = urlsplit(url).path
    except ValueError:
        return []
    collection = re.fullmatch(r"/api/worlds/([^/]+)/image-collections/([0-9a-f]{32})/image", path)
    if not collection or not url.startswith("/api/worlds/"):
        return [(url, None)]
    try:
        rows = image_collections.available(*collection.groups())
    except _MEMBER_MISSING:
        return []
    return [(row["url"], ("object", row["image_id"]) if "image_id" in row else None)
            for row in rows]


#: Where a picture is: ``("slot", (dir, name), image_id)`` for a key whose
#: picture is a slot in some record's image directory (the greeting's own art,
#: or a local reference the route names), with `image_id` the image the slot's
#: placement resolved to while the catalog was built -- None when it did not
#: resolve (legacy art, or a placement whose object or blob has not arrived) --
#: carried so a reader never reads that placement a second time;
#: ``("object", image_id)`` for a format-2 collection member, which is its
#: image object and has no slot. In a catalog it is where the key's tags can
#: be kept: None there for a key with neither (a remote URL, or a member of
#: another world's collection, R11).
Target = (tuple[Literal["slot"], tuple[Path, str], str | None]
          | tuple[Literal["object"], str] | None)


def catalog(root: Path, gid: str) -> dict[str, dict]:
    """Legacy stored art plus currently referenced pictures, deduplicated by key.

    Stored art stays taggable even if it is not embedded in the body. Referenced
    art disappears when its reference or its local serving record disappears.
    Markdown is parsed once per greeting, not once per image.

    The public shape: entries only. `catalog_with_slots` is the same inventory
    with each key's `Target`, which is a filesystem location and so never
    belongs in anything a route answers with.
    """
    return {key: entry for key, (entry, _target) in catalog_with_slots(root, gid).items()}


def catalog_with_slots(root: Path, gid: str) -> dict[str, tuple[dict, Target]]:
    """`catalog`, with each key's `Target` beside its entry.

    A local reference's slot is the one its existence check already found
    (`_record`), so this reads each referenced record once, as `catalog`
    always has, and resolves its placement once (`_record_file`). Own art's
    image id is the listing's own. The slot may lie in ANOTHER world's root --
    a reference names the world it serves from -- and a caller that keeps
    something per world has to check which root it is under.

    A format-2 collection member's key is its member URL without the query.
    Its target is `("object", image_id)` only when the collection is this
    world's (C6): a member of another world's collection has no target, so
    its tags stay in the greeting's sidecar, as any cross-world picture's do.
    """
    out: dict[str, tuple[dict, Target]] = {}
    rows = assets.list_images(root, gid, "default", base="greetings")
    if rows:    # `list_images` answering at all proves `gid` safe
        own = assets.version_dir(root, gid, "default", base="greetings")
        out = {image["name"]: ({}, ("slot", (own, image["name"]), image.get("image_id")))
               for image in rows}
    try:
        sig = statcache.signature(root / "greetings" / f"{gid}.md") if safe_id(gid) else None
        refs = statcache.memo("greeting_image_refs", sig, lambda: _references(root, gid),
                              pool=_REF_POOL, max_entries=characters.POOL_ENTRIES)
    except (OSError, greetings.GreetingNotFound):
        return out
    same_world: dict[str, bool] = {}
    for url in refs:
        for source, listed in _collection_members(url):
            key = image_key(root, gid, source)
            if source.startswith("/api/worlds/"):
                if not key.startswith("/"):
                    continue    # this greeting's own art, listed above
                target = listed if listed is not None else local_target(root, source)
                if target is None:
                    continue
                if target[0] == "object" and not _same_world(root, source, same_world):
                    target = None
                out[key] = ({"url": key}, target)
            elif urlsplit(source).scheme in ("http", "https") and urlsplit(source).netloc:
                out[key] = ({"url": key}, None)
    return out


def _same_world(root: Path, url: str, seen: dict[str, bool]) -> bool:
    """Is the world a local URL names `root`'s world? Canonical ids compared,
    so `REALM` is `realm` where the filesystem says so; answered once per
    spelling per catalog (`seen`), since canonicalizing lists the worlds."""
    wid = unquote(urlsplit(url).path.split("/")[3])
    if wid not in seen:
        seen[wid] = wid == root.name or (worlds_paths.canonical_id(wid)
                                         == worlds_paths.canonical_id(root.name))
    return seen[wid]

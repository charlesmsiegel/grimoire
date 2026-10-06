"""Finite image pools, independent of the service which originally supplied them.

A manifest is an immutable ordered list of images; sampling provenance belongs
to a separate temporary import journal. This keeps rendering and exports
offline, while a future collection author can publish already-local members
without implementing a downloader.

Two manifest formats are read, and only format 2 is written. Format 1 names
world-library images (`collection-image-<sha256 of the received bytes>`), and
its members are served under the library's URLs; an older grimoire wrote it.
Format 2 names image ids, so a member is its object and never a library file
(`put_member` places nothing); member `n` is served at
`/api/worlds/{wid}/image-collections/{id}/members/{n}`. An index is never
reused: a member whose picture is missing is skipped, and its own URL answers
404 rather than another picture, because those URLs are cached immutable.

`guard_write` and `check_member_name` protect format-1 names only, so they act
only while the world holds format-1 state (`has_format1`), failing closed on a
manifest or harvest journal that does not read.

The member index in a URL is canonical ASCII decimal, parsed only by
`member_index`, so one picture has one URL. `validate(raw)` is the public,
content-only manifest rule (world bundles call it), and `journal_directory(wid)`
is the single spelling of where a world's harvest journals live. Locks are taken
in the order job, then collection, then any image lock.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import (
    assets,
    atomic,
    covers,
    fetch,
    image_hash,
    image_library,
    image_refs,
    image_store,
    locks,
    paths,
)
from .worlds import paths as worlds_paths

MEMBER_PREFIX = "collection-image-"
MAX_MEMBERS = 10000
_MEMBER = re.compile(r"collection-image-[0-9a-f]{64}\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")
_INDEX = re.compile(r"(?:0|[1-9][0-9]*)\Z")
#: Each format's member rule: a library name, or an image id.
_MEMBER_RULES = {1: _MEMBER, 2: image_hash.ID_RE}


class CollectionInvalidError(ValueError):
    """An invalid collection must not be published or treated as unreferenced."""


class CollectionIdError(CollectionInvalidError):
    """An invalid address is distinct from corrupt stored collection data."""


class ImageInCollectionError(ValueError):
    """Changing these bytes would change an accepted local collection."""


def directory(wid: str) -> Path:
    if not worlds_paths.world_exists(wid):
        raise worlds_paths.WorldNotFound(wid)
    return worlds_paths.world_root(wid) / "assets" / "image-collections"


def image_directory(wid: str) -> Path:
    return directory(wid).parent / "images"


def manifest_path(wid: str, collection_id: str) -> Path:
    if not isinstance(collection_id, str) or not _ID.fullmatch(collection_id):
        raise CollectionIdError("invalid image collection id")
    return directory(wid) / f"{collection_id}.json"


def validate(raw: object) -> dict:
    """A manifest's content as `{"format": 1 | 2, "members": [...]}`, or
    `CollectionInvalidError`: the one manifest rule, for the store and for a
    bundle's manifests alike. Content only: no file is consulted."""
    # `type(...) is int`, so JSON `true` (which equals 1) is not a format.
    if (not isinstance(raw, dict) or set(raw) != {"format", "members"}
            or type(raw["format"]) is not int or raw["format"] not in _MEMBER_RULES):
        raise CollectionInvalidError("unsupported image collection manifest")
    fmt = raw["format"]
    rule = _MEMBER_RULES[fmt]
    members = raw["members"]
    if (not isinstance(members, list) or not members or len(members) > MAX_MEMBERS
            or any(not isinstance(n, str) or not rule.fullmatch(n) for n in members)
            or len(set(members)) != len(members)):
        raise CollectionInvalidError("invalid image collection members")
    return {"format": fmt, "members": list(members)}


def read(wid: str, collection_id: str) -> dict:
    """`{"format": 1 | 2, "members": [...]}`. Raises `FileNotFoundError` for no
    such collection, and `CollectionInvalidError` for one that does not read."""
    path = manifest_path(wid, collection_id)
    try:
        return validate(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CollectionInvalidError("image collection manifest is unreadable") from exc


def _resolve_member(wid: str, fmt: int, member: str) -> tuple[Path, str, str | None] | None:
    """(path, version token, image id or None) for one member, or None when
    its picture is not there. A format-1 member keeps the library's version
    token and URL; a format-2 member is its object's blob."""
    if fmt == 1:
        path = assets.path_in(image_directory(wid), member, supported_only=True)
        return None if path is None else (path, assets.image_version(path), None)
    found = image_refs.resolve_ref(image_refs.Ref(name="", image=member, focus=None))
    return None if found is None else (found.blob_path, found.blob_sha256, found.image_id)


def member_index(n: str) -> int | None:
    """A member URL's `{n}` as an index, or None; the one parser for the route, `local_path`, `local_target` and export."""
    # Canonical ASCII decimal only, so one member has one URL (`str.isdigit`
    # and `int` accept other scripts' digits and leading zeros), and no longer
    # than the largest index, so `int` never sees a huge string.
    if not isinstance(n, str) or len(n) > len(str(MAX_MEMBERS)) or not _INDEX.match(n):
        return None
    return int(n)


def member_url(wid: str, collection_id: str, index: int, v: str) -> str:
    manifest_path(wid, collection_id)
    return f"/api/worlds/{wid}/image-collections/{collection_id}/members/{index}?v={v}"


def available(wid: str, collection_id: str) -> list[dict]:
    """The members whose pictures are there, in order, as rows
    `{index, path, url}` (plus `image_id` for format 2). `index` is the
    member's place in the manifest, so a missing member leaves a gap rather
    than renumbering the rest."""
    manifest = read(wid, collection_id)
    fmt = manifest["format"]
    out = []
    for index, member in enumerate(manifest["members"]):
        found = _resolve_member(wid, fmt, member)
        if found is None:
            continue
        path, v, image_id = found
        if fmt == 1:
            out.append({"index": index, "path": path,
                        "url": f"/api/worlds/{wid}/images/{member}?v={v}"})
        else:
            # Built here, not by `member_url`: `read` already proved the world
            # and the id, and that would re-prove them for every row.
            out.append({"index": index, "path": path, "image_id": image_id, "url":
                        f"/api/worlds/{wid}/image-collections/{collection_id}/members/{index}?v={v}"})
    return out


def member_path(wid: str, collection_id: str, index: int) -> Path | None:
    """The file serving member `index`, in either format; None when the index
    is out of range or that member's picture is missing -- never another
    member's."""
    manifest = read(wid, collection_id)
    members = manifest["members"]
    if type(index) is not int or not 0 <= index < len(members):
        return None
    found = _resolve_member(wid, manifest["format"], members[index])
    return None if found is None else found[0]


def journal_directory(wid: str) -> Path:
    """The harvest journals' home, the one spelling of it:
    `image_collection_imports.job_path` builds its paths from this."""
    directory(wid)  # prove world existence and id safety
    return paths.home() / ".cache" / "image-collection-imports" / wid


def _is_format2_journal(path: Path) -> bool:
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(job, dict) and type(job.get("format")) is int and job["format"] == 2


def has_format1(wid: str) -> bool:
    """Does this world hold any format-1 state the guard must protect?

    True for a format-1 manifest, a harvest journal that is not format 2, and
    -- failing closed, as `referenced` does -- any manifest or journal that
    does not read: a half-synced file may be a format-1 one."""
    for path in directory(wid).glob("*.json"):
        try:
            if read(wid, path.stem)["format"] == 1:
                return True
        except (CollectionInvalidError, OSError):
            return True
    return any(not _is_format2_journal(p) for p in journal_directory(wid).glob("*.json"))


def referenced(wid: str, name: str) -> bool:
    """Does a format-1 manifest name this library image? Format-2 members are
    image ids, never library names, so their manifests are not consulted."""
    # Do not fail open on malformed manifests: an unknown reference is not
    # evidence that a user's image is safe to remove. Reads remain unlocked;
    # writers bracket this scan and their write with image_collection_lock.
    found = False
    folded = name.casefold()
    for path in directory(wid).glob("*.json"):
        manifest = read(wid, path.stem)
        if manifest["format"] == 1:
            found |= folded in {member.casefold() for member in manifest["members"]}
    return found


def _placed_image(wid: str, name: str) -> str | None:
    """The image id the member's placement names, None when it has none (a
    legacy file, or nothing at all). The placement alone, resolved or not: a
    member whose object has not arrived yet is still that image."""
    ref = image_refs.read(image_directory(wid), name)
    return ref.image if ref is not None else None


def guard_write(wid: str, name: str, data: bytes | None = None) -> None:
    """Called under the collection lock by world-image write and delete paths.

    A placement-backed member is its image, not its bytes: stored bytes are
    sanitised, so an upload is harmless exactly when it is the same image.

    Inert while the world holds no format-1 state: a format-2 member is not a
    library name, so nothing in the library is a member.
    """
    if not has_format1(wid) or not referenced(wid, name):
        return
    if data is not None:
        placed = _placed_image(wid, name)
        if placed is not None:
            # Identified, not ingested: a refused upload must not be stored.
            if image_store.identify(data, fetch.sniff_ext(data) or "png") == placed:
                return
            raise ImageInCollectionError("image is a member of an image collection")
    path = assets.path_in(image_directory(wid), name, supported_only=True)
    if data is not None and path is not None and path.read_bytes() == data:
        return
    raise ImageInCollectionError("image is a member of an image collection")


def check_member_name(wid: str, name: str, data: bytes) -> None:
    """Refuse a world-library write whose name claims to be a collection member
    but is not the hash of the bytes as received.

    Only for a name no collection references yet: once published, `guard_write`
    is the rule, and it admits the same pixels under other metadata.

    Converting a format-1 harvest journal (`format1_member_id`) trusts a
    placement as the member's identity and skips the byte hash, so the name
    must never be fileable over other pixels through the ordinary write path.

    Inert while the world holds no format-1 state, as `guard_write` is.
    """
    if not has_format1(wid):
        return
    lowered = name.casefold()
    if _MEMBER.fullmatch(lowered) and not referenced(wid, name) and lowered != MEMBER_PREFIX + hashlib.sha256(data).hexdigest():
        raise CollectionInvalidError("collection member names are reserved for their own bytes")


def put_member(wid: str, data: bytes) -> str:
    """Ingest one harvested picture and return its image id. Validated as a
    cover is; nothing is placed in the world library, so a member is its
    object and the same pixels under other metadata are the same member."""
    image_library.validate_size(data)
    try:
        ext = covers.validate(data)
    except (covers.CoverInvalid, covers.CoverTooLarge) as exc:
        raise CollectionInvalidError(str(exc)) from exc
    directory(wid)  # a member belongs to a world that exists
    return image_store.ingest(data, ext).id


def format1_member_id(wid: str, name: str) -> str:
    """The image id a format-1 member name stands for: its library
    placement's, or -- for a legacy file whose bytes still hash to the name,
    the rule format-1 publication applied -- the id of those bytes, ingested.
    Raises `CollectionInvalidError` for a name that maps to nothing or to
    other bytes. Takes the collection lock (callers holding a job lock take
    that first)."""
    if not isinstance(name, str) or not _MEMBER.fullmatch(name):
        raise CollectionInvalidError("invalid image collection members")
    with locks.image_collection_lock(wid):
        placed = _placed_image(wid, name)
        if placed is not None:
            return placed
        path = assets.path_in(image_directory(wid), name, supported_only=True)
        if path is None:
            raise CollectionInvalidError("collection member is missing")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != name[len(MEMBER_PREFIX):]:
            raise CollectionInvalidError("collection member has changed")
        return image_store.ingest(data, path.suffix[1:].lower()).id


def publish(wid: str, collection_id: str, members: list[str]) -> dict:
    """Publish image ids as a format-2 manifest. Every member must resolve to
    its picture, and a published manifest is immutable: one already there must
    be this one, format and members alike."""
    manifest = validate({"format": 2, "members": members})
    with locks.image_collection_lock(wid):
        target = manifest_path(wid, collection_id)
        for image_id in manifest["members"]:
            if _resolve_member(wid, 2, image_id) is None:
                raise CollectionInvalidError("collection member is missing")
        if target.exists():
            if read(wid, collection_id) != manifest:
                raise CollectionInvalidError("published image collections are immutable")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic.write_text(target, json.dumps(manifest, indent=2) + "\n")
    return manifest


def url_for(wid: str, collection_id: str) -> str:
    manifest_path(wid, collection_id)
    return f"/api/worlds/{wid}/image-collections/{collection_id}/image"

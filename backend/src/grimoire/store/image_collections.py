"""Finite image pools, independent of the service which originally supplied them.

A manifest is an immutable ordered list of content-addressed world images;
sampling provenance belongs to a separate temporary import journal. This keeps
rendering and exports offline, while a future collection author can publish
already-local members without implementing a downloader.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import assets, atomic, covers, fetch, image_library, image_refs, image_store, locks
from .worlds import paths as worlds_paths

MEMBER_PREFIX = "collection-image-"
_MEMBER = re.compile(r"collection-image-[0-9a-f]{64}\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")


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


def _validated(raw: object) -> dict:
    if not isinstance(raw, dict) or set(raw) != {"format", "members"} or type(raw["format"]) is not int or raw["format"] != 1:
        raise CollectionInvalidError("unsupported image collection manifest")
    members = raw["members"]
    if (not isinstance(members, list) or not members or len(members) > 10000
            or any(not isinstance(n, str) or not _MEMBER.fullmatch(n) for n in members)
            or len(set(members)) != len(members)):
        raise CollectionInvalidError("invalid image collection members")
    return {"format": 1, "members": list(members)}


def read(wid: str, collection_id: str) -> dict:
    path = manifest_path(wid, collection_id)
    try:
        return _validated(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CollectionInvalidError("image collection manifest is unreadable") from exc


def available(wid: str, collection_id: str) -> list[dict]:
    members = read(wid, collection_id)["members"]
    out = []
    for name in members:
        path = assets.path_in(image_directory(wid), name, supported_only=True)
        if path is not None:
            out.append({"name": name, "path": path, "url":
                        f"/api/worlds/{wid}/images/{name}?v={assets.image_version(path)}"})
    return out


def referenced(wid: str, name: str) -> bool:
    # Do not fail open on malformed manifests: an unknown reference is not
    # evidence that a user's image is safe to remove. Reads remain unlocked;
    # writers bracket this scan and their write with image_collection_lock.
    found = False
    for path in directory(wid).glob("*.json"):
        found |= name.casefold() in {member.casefold() for member in read(wid, path.stem)["members"]}
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
    """
    if not referenced(wid, name):
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

    `publish` trusts a placement as the member's identity and skips the byte
    hash, so the name must never be fileable over other pixels through the
    ordinary write path. `put_member` links directly and is not subject to it.
    """
    lowered = name.casefold()
    if _MEMBER.fullmatch(lowered) and not referenced(wid, name) and lowered != MEMBER_PREFIX + hashlib.sha256(data).hexdigest():
        raise CollectionInvalidError("collection member names are reserved for their own bytes")


def put_member(wid: str, data: bytes) -> str:
    image_library.validate_size(data)
    try:
        ext = covers.validate(data)
    except (covers.CoverInvalid, covers.CoverTooLarge) as exc:
        raise CollectionInvalidError(str(exc)) from exc
    name = MEMBER_PREFIX + hashlib.sha256(data).hexdigest()
    with locks.image_collection_lock(wid):
        d = image_directory(wid)
        # Compared before anything is stored, so a refusal leaves no orphan
        # object or blob behind. A member that matches is still ingested: that
        # restores a blob gone missing and touches the object off GC.
        placed = _placed_image(wid, name)
        if placed is not None:
            if placed != image_store.identify(data, ext):
                raise CollectionInvalidError("stored collection member has changed")
            image_store.ingest(data, ext)
            return name
        legacy = assets.path_in(d, name, supported_only=True)
        if legacy is not None:
            if legacy.read_bytes() != data:
                raise CollectionInvalidError("stored collection member has changed")
        else:
            assets.link_in(d, name, image_store.ingest(data, ext).id)
    return name


def publish(wid: str, collection_id: str, members: list[str]) -> dict:
    manifest = _validated({"format": 1, "members": members})
    with locks.image_collection_lock(wid):
        target = manifest_path(wid, collection_id)
        for name in members:
            d = image_directory(wid)
            if assets.resolve(d, name) is not None:
                continue  # a placement is its own identity; its blob is sanitised
            path = assets.path_in(d, name, supported_only=True)
            if path is None or hashlib.sha256(path.read_bytes()).hexdigest() != name[len(MEMBER_PREFIX):]:
                raise CollectionInvalidError("collection member is missing or has changed")
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

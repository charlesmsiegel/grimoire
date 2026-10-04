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

from . import assets, atomic, covers, image_library, locks
from .worlds import paths as worlds_paths

MEMBER_PREFIX = "collection-image-"
_MEMBER = re.compile(r"collection-image-[0-9a-f]{64}\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")


class CollectionInvalidError(ValueError):
    """An invalid collection must not be published or treated as unreferenced."""


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
        raise CollectionInvalidError("invalid image collection id")
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
        found |= name in read(wid, path.stem)["members"]
    return found


def guard_write(wid: str, name: str, data: bytes | None = None) -> None:
    """Called under the collection lock by world-image write and delete paths."""
    if not referenced(wid, name):
        return
    path = assets.path_in(image_directory(wid), name, supported_only=True)
    if data is not None and path is not None and path.read_bytes() == data:
        return
    raise ImageInCollectionError("image is a member of an image collection")


def put_member(wid: str, data: bytes) -> str:
    image_library.validate_size(data)
    try:
        ext = covers.validate(data)
    except (covers.CoverInvalid, covers.CoverTooLarge) as exc:
        raise CollectionInvalidError(str(exc)) from exc
    name = MEMBER_PREFIX + hashlib.sha256(data).hexdigest()
    with locks.image_collection_lock(wid):
        d = image_directory(wid)
        existing = assets.path_in(d, name, supported_only=True)
        if existing is not None:
            if existing.read_bytes() != data:
                raise CollectionInvalidError("stored collection member has changed")
        else:
            assets.put_in(d, name, data, ext, supported_only=True)
    return name


def publish(wid: str, collection_id: str, members: list[str]) -> dict:
    manifest = _validated({"format": 1, "members": members})
    with locks.image_collection_lock(wid):
        target = manifest_path(wid, collection_id)
        for name in members:
            path = assets.path_in(image_directory(wid), name, supported_only=True)
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

"""characters: hashes responsibilities."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .. import _actor_hash as actor_hash
from .. import paths as _store_paths
from .. import statcache
from . import _paths


def card_hash(root: Path, cid: str, vid: str) -> str | None:
    if not _store_paths.safe_id(cid) or not _store_paths.safe_id(vid):
        return None
    p = _paths._card_path(root, cid, vid)
    sig = statcache.signature(p)
    if sig is None:
        return None
    return statcache.memo(
        "card_hash", sig,
        lambda: hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest())


dir_content_hash = actor_hash.content_hash


def snapshot(root: Path, cid: str) -> tuple[str, list[tuple[str, str]]] | None:
    """One read of the whole actor: its `dir_hash` and the (name, text) pairs
    that hash covers, meta first. A copier writes exactly these and records
    exactly that hash, so the sync base cannot describe content the copy never
    got even if the source moves mid-copy (#247). None when not an actor."""
    if not _store_paths.safe_id(cid) or not _paths._meta_path(root, cid).exists():
        return None
    version_ids = _paths._version_ids(root, cid)
    if not version_ids:
        # read_character/read_pc refuse an actor with no addressable version,
        # so reporting a hash makes sync see a changed record it cannot then
        # read. `snapshot` and `dir_hash` have to agree (#247), so both say
        # absent -- the same answer as for an actor that isn't there (#259 review)
        return None
    files = [_paths._meta_path(root, cid)] + [_paths._card_path(root, cid, v) for v in version_ids]
    pairs = [(p.name, p.read_text(encoding="utf-8")) for p in files]
    return dir_content_hash(pairs), pairs


def _dir_hash_compute(files: list[Path]) -> str:
    return dir_content_hash([(p.name, p.read_text(encoding="utf-8")) for p in files])


def dir_hash(root: Path, cid: str) -> str | None:
    """Whole-actor content hash: character.md plus every version card, name-tagged.
    Assets are excluded so an image-only change never surfaces in sync."""
    if not _store_paths.safe_id(cid) or not _paths._meta_path(root, cid).exists():
        return None
    version_ids = _paths._version_ids(root, cid)
    if not version_ids:
        # read_character/read_pc refuse an actor with no addressable version,
        # so reporting a hash makes sync see a changed record it cannot then
        # read. `snapshot` and `dir_hash` have to agree (#247), so both say
        # absent -- the same answer as for an actor that isn't there (#259 review)
        return None
    files = [_paths._meta_path(root, cid)] + [_paths._card_path(root, cid, v) for v in version_ids]
    # the signature spans the whole file set, so adding/removing a version invalidates too
    return statcache.memo("dir_hash", statcache.signature(*files),
                          lambda: _dir_hash_compute(files))

"""Content hashing and directory listing shared by the two actor stores.

characters.py and pcs.py lay out actors the same way — one directory per actor
holding a meta markdown file plus one file per version — and sync compares them
by content hash. Only the meta filename, the version file extension, and the
statcache memo prefix differ, so the hashing itself lives here once.

Assets are never hashed: an image-only change must not surface as a content
change in sync.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import statcache


def file_hash(path: Path, memo_key: str) -> str | None:
    """sha256 of one UTF-8 file, memoized on its stat signature. None when the
    file is missing (the caller's not-found path)."""
    sig = statcache.signature(path)
    if sig is None:
        return None
    return statcache.memo(
        memo_key, sig,
        lambda: hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest())


def _files_hash_compute(files: list[Path]) -> str:
    h = hashlib.sha256()
    for p in files:
        h.update(p.name.encode("utf-8"))
        h.update(p.read_text(encoding="utf-8").encode("utf-8"))
    return h.hexdigest()


def files_hash(files: list[Path], memo_key: str) -> str:
    """Name-tagged sha256 over an ordered file set. The signature spans the whole
    set, so adding or removing a version invalidates the memo too."""
    return statcache.memo(memo_key, statcache.signature(*files),
                          lambda: _files_hash_compute(files))


def actor_ids(parent: Path, meta_name: str) -> list[str]:
    """Sorted ids of every child directory of `parent` holding `meta_name`."""
    if not parent.exists():
        return []
    return sorted(p.name for p in parent.iterdir() if p.is_dir() and (p / meta_name).exists())

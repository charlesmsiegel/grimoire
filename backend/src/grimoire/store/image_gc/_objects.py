"""Readable sidecar/blob inventory, exact refcounts and fail-closed unreadable/unarrived holds."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .. import atomic, image_hash, image_refs, image_store
from . import _paths, _roots

# ---- the store ------------------------------------------------------------------

def _sig(st: os.stat_result) -> tuple:
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _names_blob(path: Path) -> str | None:
    """The blob key a sidecar-shaped file names, or None."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    blob = raw.get("blob") if isinstance(raw, dict) else None
    if not isinstance(blob, dict):
        return None
    sha, ext = blob.get("sha256"), blob.get("ext")
    if not isinstance(sha, str) or not isinstance(ext, str):
        return None
    key = _paths._blob_key(sha, ext)
    return key if _paths._split_key(key) else None


@dataclass
class _Sidecars:
    """Every file under ``objects/`` and the blob each names.

    `ids` holds the well-formed sidecars (the only possible candidates);
    `names` every file considered, with the blob it names or None when it does
    not read -- a sync-conflict copy of a sidecar counts as a holder of its
    blob. `unlisted` folders and unreadable files keep every blob."""

    ids: dict[str, Path] = field(default_factory=dict)
    sigs: dict[Path, tuple] = field(default_factory=dict)
    names: dict[Path, str | None] = field(default_factory=dict)
    refs: dict[str, set[Path]] = field(default_factory=dict)
    unlisted: list[Path] = field(default_factory=list)
    links: list[Path] = field(default_factory=list)

    def keep_all_blobs(self) -> bool:
        return bool(self.unlisted) or any(k is None for k in self.names.values())

    def unreadable(self) -> list[Path]:
        return sorted(p for p, k in self.names.items() if k is None)

    def drop(self, path: Path) -> None:
        key = self.names.pop(path, None)
        self.sigs.pop(path, None)
        if key is not None:
            self.refs.get(key, set()).discard(path)


def _unarrived(roots: _roots._Roots, side: _Sidecars) -> list[str]:
    """Reachable ids with no readable sidecar: a placement whose object has
    not arrived yet (mid-sync). Its blob may well be here already, named by
    nothing that reads, so while there is one no orphan blob is collected --
    the same rule as an unreadable sidecar."""
    return sorted(i for i in roots.reachable
                  if side.ids.get(i) is None or side.names.get(side.ids[i]) is None)


def _orphan_hold(side: _Sidecars, unarrived: list[str]) -> str | None:
    """Why no orphan blob may go this run, or None."""
    if side.keep_all_blobs():
        return "unreadable-sidecars"
    if unarrived:
        return "unarrived-objects"
    return None


def _shards(base: Path, s: _Sidecars) -> Iterator[Path]:
    try:
        with os.scandir(base) as it:
            entries = sorted(it, key=lambda e: e.name)
    except FileNotFoundError:
        return
    except OSError:
        s.unlisted.append(base)
        return
    for e in entries:
        if e.is_symlink():
            s.links.append(Path(e.path))
        elif e.is_dir(follow_symlinks=False):
            yield Path(e.path)


def _sidecar_entry(root: Path, shard: Path, e: os.DirEntry, s: _Sidecars,
                   prev: _Sidecars | None) -> None:
    path = Path(e.path)
    if e.is_symlink():
        s.links.append(path)
        return
    if not e.is_file(follow_symlinks=False) or e.name.casefold() in image_refs.OS_LITTER:
        return
    sig = _sig(e.stat(follow_symlinks=False))
    stem, dot, ext = e.name.rpartition(".")
    well_formed = (bool(dot) and ext == "json" and image_hash.is_image_id(stem)
                   and stem[4:6] == shard.name)
    if prev is not None and prev.sigs.get(path) == sig and path in prev.names:
        key = prev.names[path]
    elif well_formed:
        obj = image_store.read_fresh(stem, root=root)
        key = None if obj is None else _paths._blob_key(obj.blob_sha256, obj.ext)
    else:
        key = _names_blob(path)
        if key is None and atomic.is_write_temp(path):
            return                      # a temp mid-write: its writer owns it
    if well_formed:
        s.ids[stem] = path
    s.sigs[path] = sig
    s.names[path] = key
    if key is not None:
        s.refs.setdefault(key, set()).add(path)


def _list_objects(root: Path, prev: _Sidecars | None = None) -> _Sidecars:
    """Every file under ``objects/``. With `prev`, a file whose stat is
    unchanged is not parsed again -- the refresh each batch runs under the
    ingest/GC lock costs a listing and a stat per file."""
    s = _Sidecars()
    for shard in _shards(image_store.store_root(root) / "objects", s):
        try:
            with os.scandir(shard) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            s.unlisted.append(shard)
            continue
        for e in entries:
            try:
                _sidecar_entry(root, shard, e, s, prev)
            except OSError:
                s.names[Path(e.path)] = None     # cannot read: keeps every blob
    return s


def _list_blobs(root: Path, s: _Sidecars) -> dict[str, os.stat_result]:
    """Every blob file, by key, with its stat. Files of any other name are not
    the store's and are left alone; a link is recorded in `s.links`."""
    out: dict[str, os.stat_result] = {}
    for shard in _shards(image_store.store_root(root) / "blobs", s):
        try:
            with os.scandir(shard) as it:
                entries = list(it)
        except OSError:
            continue                    # its blobs simply are not candidates
        for e in entries:
            key = _paths._split_key(e.name)
            if key is None or key[0][:2] != shard.name:
                continue
            if e.is_symlink():
                s.links.append(Path(e.path))
                continue
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                out[e.name] = st
    return out

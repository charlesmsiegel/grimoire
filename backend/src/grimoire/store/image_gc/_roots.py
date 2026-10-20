"""Strict reachability through placements, manifests, journals, migration work maps and active staging."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import (
    atomic,
    image_collections,
    image_hash,
    image_migration,
    image_refs,
    image_store,
    image_surfaces,
)
from . import _contracts, _paths

# ---- roots ------------------------------------------------------------------

@dataclass
class _Roots:
    reachable: set[str] = field(default_factory=set)
    placements: set[Path] = field(default_factory=set)
    blocking: list[dict] = field(default_factory=list)
    staging: list[dict] = field(default_factory=list)

    def block(self, root: Path, path: Path, reason: str) -> None:
        # One row per (path, reason): the same link is reached from several
        # reserved paths' checks, and the report should say it once.
        row = {"path": _paths._rel(root, path), "reason": reason}
        if row not in self.blocking:
            self.blocking.append(row)


def _lstat_dir(root: Path, d: Path, r: _Roots) -> bool:
    """Whether `d` is a real directory to read; a link or a non-directory is
    blocking, absence is not."""
    try:
        st = os.lstat(d)
    except FileNotFoundError:
        return False
    except OSError:
        r.block(root, d, image_refs.UNREADABLE)
        return False
    if stat.S_ISLNK(st.st_mode):
        r.block(root, d, image_refs.SYMLINK)
        return False
    if not stat.S_ISDIR(st.st_mode):
        r.block(root, d, image_refs.NOT_A_DIRECTORY)
        return False
    return True


def _refs(root: Path, tree: Path, r: _Roots, *, count: bool) -> None:
    try:
        for path, image_id in image_refs.walk_strict(tree):
            r.reachable.add(image_id)
            if count and path.name != image_refs.JOURNAL and not atomic.is_write_temp(path):
                r.placements.add(path)
    except image_refs.ImageRefParseError as exc:
        r.block(root, exc.path, exc.reason)


def _manifest(root: Path, path: Path, r: _Roots) -> None:
    """One collection manifest: a format-2 one's members are roots; a
    format-1 one names library placements, which are roots already."""
    try:
        raw = image_refs.load_json_strict(path)
    except image_refs.ImageRefParseError as exc:
        r.block(root, exc.path, exc.reason)
        return
    try:
        manifest = image_collections.validate(raw)
    except image_collections.CollectionInvalidError:
        fmt = raw.get("format") if isinstance(raw, dict) else None
        known = type(fmt) is int and fmt in (1, 2)
        r.block(root, path, image_refs.UNPARSEABLE if known else image_refs.UNKNOWN_FORMAT)
        return
    if manifest["format"] == 2:
        r.reachable.update(manifest["members"])


def _manifest_entries(root: Path, entries: list[os.DirEntry], r: _Roots) -> None:
    for e in entries:
        path = Path(e.path)
        if not _json_root(root, path, r):
            continue
        try:
            regular = e.is_file(follow_symlinks=False)
        except OSError:
            r.block(root, path, image_refs.UNREADABLE)
            continue
        if not regular:
            r.block(root, path, image_refs.NOT_A_FILE)
            continue
        _manifest(root, path, r)



def _json_root(root: Path, path: Path, r: _Roots) -> bool:
    """A persisted manifest or journal, refusing extension aliases (#479).

    Bundles may preserve `.JSON`, which an insensitive device reads as a
    manifest. A sensitive device must block rather than hide its members.
    """
    if atomic.is_write_temp(path):
        return False
    if path.name.endswith(".json"):
        return True
    if image_refs._fold(path.name).endswith(".json"):
        r.block(root, path, image_refs.CASE_VARIANT)
    return False


def _strict_listing(root: Path, d: Path, r: _Roots) -> list[os.DirEntry] | None:
    """`d`'s entries, or None (and `d` blocking) when it does not list or
    holds a link."""
    try:
        with os.scandir(d) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError:
        r.block(root, d, image_refs.UNREADABLE)
        return None
    for e in entries:
        if image_refs._linkish(e):
            r.block(root, Path(e.path), image_refs.SYMLINK)
            return None
    return entries


def _spelling_variants(root: Path, parent: Path, name: str,
                       r: _Roots) -> list[os.DirEntry] | None:
    """Entries of `parent` that fold to `name` without being spelled as it,
    or None (and `parent` blocking) when `parent` does not list. Unlike
    `_strict_listing`, a link among the other names is not a block here."""
    try:
        with os.scandir(parent) as it:
            entries = list(it)
    except OSError:
        r.block(root, parent, image_refs.UNREADABLE)
        return None
    folded = image_refs._fold(name)
    return [e for e in entries
            if e.name != name and image_refs._fold(e.name) == folded]


def _strict_path(root: Path, path: Path, r: _Roots, *, base: Path | None = None,
                 directory: bool = True) -> bool:
    """A reserved root path, with its spelling checked from directory entries.

    #479: asking for the canonical path alone either silently aliases a case
    variant (Windows) or misses its roots (a case-sensitive device). Check each
    reserved component before reading it, reusing the placement walk's fold
    and link rules. Callers supply only structural paths, never record slugs
    whose case is the user's choice.
    """
    parent = root if base is None else base
    for name in path.relative_to(parent).parts:
        if not _lstat_dir(root, parent, r):
            return False
        # Only the names that this component could alias are read. A link beside
        # the reserved path (a user's own folder shortcut at the store root) is
        # not on any path this walk follows, so it must not stop collection.
        variants = _spelling_variants(root, parent, name, r)
        if variants is None:
            return False
        for entry in variants:
            r.block(root, Path(entry.path), image_refs.CASE_VARIANT)
        if variants:
            return False
        parent = parent / name
    # A file's parser owns its type/absence checks; its spelling needs the
    # same comparison as the directories on the way to it (map.json).
    return _lstat_dir(root, path, r) if directory else True


def _manifests(root: Path, r: _Roots) -> None:
    worlds = root / "worlds"
    if not _strict_path(root, worlds, r):
        return
    for w in _strict_listing(root, worlds, r) or []:
        if not w.is_dir(follow_symlinks=False):
            continue
        d = Path(w.path).joinpath(*image_surfaces.COLLECTIONS_DIR)
        if _strict_path(root, d, r, base=Path(w.path)):
            entries = _strict_listing(root, d, r)
            if entries is not None:
                _manifest_entries(root, entries, r)


def _journal(root: Path, path: Path, r: _Roots) -> None:
    """One harvest journal: a format-2 journal's ids are roots; a format-1
    one names library members, which are placements already."""
    try:
        job = image_refs.load_json_strict(path)
    except image_refs.ImageRefParseError as exc:
        r.block(root, exc.path, exc.reason)
        return
    fmt = job.get("format") if isinstance(job, dict) else None
    if not isinstance(job, dict) or type(fmt) is not int or fmt not in (1, 2):
        r.block(root, path, image_refs.UNKNOWN_FORMAT if isinstance(job, dict)
                else image_refs.UNPARSEABLE)
        return
    members = job.get("members")
    if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
        r.block(root, path, image_refs.UNPARSEABLE)
        return
    if fmt == 2:
        if not all(image_hash.is_image_id(m) for m in members):
            r.block(root, path, image_refs.UNPARSEABLE)
            return
        r.reachable.update(image_refs.ids_in(job))


def _journals(root: Path, r: _Roots) -> None:
    """Every world's journals, from the raw directory (M10): a world deleted
    with a harvest unaccepted still has members nobody has published."""
    base = image_collections.raw_journals_root(root)
    if not _strict_path(root, base, r):
        return
    for w in _strict_listing(root, base, r) or []:
        if not w.is_dir(follow_symlinks=False):
            continue
        try:
            folder = image_collections.raw_journal_directory(root, w.name)
        except ValueError:
            r.block(root, Path(w.path), "unsafe-name")
            continue
        for e in _strict_listing(root, folder, r) or []:
            path = Path(e.path)
            if not _json_root(root, path, r):
                continue                # `.json.retired`, a temp mid-write
            if not e.is_file(follow_symlinks=False):
                r.block(root, path, image_refs.NOT_A_FILE)
                continue
            _journal(root, path, r)


def _work_map(root: Path, r: _Roots) -> None:
    """The migration work map's pending entries (M12), as the migration
    itself reads them (`image_migration.pending_ids`, a module attribute so a
    test can stand one in). A map it cannot read -- ValueError for a garbled
    one, OSError for one that does not open -- blocks: an ingested picture not
    yet verified into its placement is reachable from nowhere else."""
    path = image_migration.work_map_path(root)
    before = len(r.blocking)
    _strict_path(root, path, r, directory=False)
    if len(r.blocking) != before:
        return
    try:
        # Task 4's reader; ignore kept valid whether or not this tree has it yet.
        pending = image_migration.pending_ids(root)
    except (ValueError, OSError):
        r.block(root, path, image_refs.UNPARSEABLE)
        return
    r.reachable.update(i for i in pending if image_hash.is_image_id(i))


def _staged(root: Path, work: Path, now: float, r: _Roots) -> None:
    """One staged work directory: its placements and manifests are roots,
    and one touched within `STAGING_MIN_AGE_SECONDS` blocks."""
    row = {"path": _paths._rel(root, work), "reason": "staging-leftover"}
    r.staging.append(row)
    try:
        newest = os.lstat(work).st_mtime
        for d, entries in image_refs.strict_dirs(work):
            for e in entries:
                newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
            assets, collections = image_surfaces.COLLECTIONS_DIR
            if image_refs._fold(d.name) == image_refs._fold(collections) \
                    and image_refs._fold(d.parent.name) == image_refs._fold(assets):
                if d.name != collections or d.parent.name != assets:
                    r.block(root, d, image_refs.CASE_VARIANT)
                else:
                    _manifest_entries(root, entries, r)
    except image_refs.ImageRefParseError as exc:
        r.block(root, exc.path, exc.reason)
        return
    except OSError:
        r.block(root, work, image_refs.UNREADABLE)
        return
    _refs(root, work, r, count=False)
    if newest > now - _contracts.STAGING_MIN_AGE_SECONDS:
        row["reason"] = "staging-in-progress"
        r.block(root, work, "staging-in-progress")


def _staging(root: Path, now: float, r: _Roots) -> None:
    for name in _contracts.STAGING_DIRS:
        base = root / name
        if not _strict_path(root, base, r):
            continue
        for e in _strict_listing(root, base, r) or []:
            if e.is_dir(follow_symlinks=False):
                _staged(root, Path(e.path), now, r)
            # A file here is bookkeeping (a module edit's journal), not a tree.


def _walk_roots(root: Path, now: float, cancel: Callable[[], bool]) -> _Roots | None:
    """Every root, strictly; None when cancelled part-way."""
    r = _Roots()
    for top in ("worlds", "campaigns"):
        tree = root / top
        if _strict_path(root, tree, r):
            _refs(root, tree, r, count=True)
        if cancel():
            return None
    for step in (_manifests, _journals, _work_map):
        step(root, r)
    _staging(root, now, r)
    return None if cancel() else r


def _store_blocks(root: Path) -> list[tuple[Path, str]]:
    """What about the store's own folders blocks a run: a link between the
    root and them, or the cache this writes and deletes in, or the outbox it
    moves pictures into -- a deletion or a move through one would land outside
    the pinned root -- and an ``objects/``
    that is absent while ``blobs/`` is there, which is a store whose
    sidecars have not arrived (mid-sync), not one whose blobs nothing names."""
    store = image_store.store_root(root)
    cache = Path(root) / ".cache"
    candidates = [store.parent, store, store / "objects", store / "blobs",
                  cache, cache / "image-store", _paths._gc_dir(root),
                  image_store.index_path("0" * 64, root=root).parent.parent,
                  Path(root).joinpath(*_contracts._THUMBS_DIR),
                  Path(root).joinpath(*_contracts._RECYCLE_DIR), _paths.outbox_dir(root)]
    out = [(p, image_refs.SYMLINK) for p in candidates if p.is_symlink()]
    if not os.path.lexists(store / "objects") and os.path.lexists(store / "blobs"):
        out.append((store / "objects", "objects-missing"))
    return out

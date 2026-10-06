"""Image garbage collection: remove what nothing anywhere reaches (stage 4).

Spec section 12 and stage-4 rulings M12-M15. Deleting a placement deletes only
its ref; there is no reference count, so an object nothing names any more
stays until this collects it. Two calls, both run as the ``maintenance`` run
class and both fail closed:

* **`scan`** is the dry run. It walks every root strictly, lists the store,
  records this device's sightings of what is unreachable, and -- when
  something is collectable and nothing blocked the walk -- persists a
  single-use token naming exactly that.
* **`collect`** takes such a token and deletes at most what it names,
  intersected with a fresh strict walk, in batches under the ingest/GC lock.

**Roots** (M12) are where a reference is kept that is not itself an image:
every file in every ``image-refs/`` folder under ``worlds/`` and
``campaigns/`` (`image_refs.walk_strict`: sync-conflict copies, both sides of
a promotion journal, readable atomic temps), format-2 collection manifests,
harvest journals (read from the raw directory, so a deleted world's still
count), the migration work map's pending entries, and the same placements and
manifests inside staged work directories (``.world-staging``,
``.module-staging``). Anything among them that does not list, parse or name a
known format -- and any symlink -- BLOCKS: the walk is all or nothing, the
report names each blocking path, and nothing is deleted. So does a staged work
directory touched within the last hour (`STAGING_MIN_AGE_SECONDS`): a fork or
an import is still filling it, and what it will place is not on disk yet.

**Candidates** (M13). An object is collectable only when all of these hold:

- no root reaches it;
- its sidecar's and its blob's mtimes are both older than the grace period
  (ingest touches the sidecar, so an object somebody ingested and has not
  placed yet -- a Chub gallery mid-import, a harvested member, a staged
  bundle -- is never collected under them);
- THIS device first saw it unreachable at least a grace period ago, on an
  earlier scan, and sees it unreachable again now, at least `SCAN_GAP_HOURS`
  after that.

Sightings live in ``.cache/image-store/gc/<device key>.json``. ``.cache``
syncs, so the file is keyed by `maintenance_reports.device_key` and another
device's sightings are never this one's evidence. A sighting is dropped the
moment its object is seen reachable. An mtime or a sighting in the future is
never collectable and is reported as clock skew; a future sighting restarts.
The accepted residual risk: a device offline for longer than the grace period
can bring back a placement whose image was collected meanwhile.

**Blobs** (M14). A blob goes only when no surviving readable sidecar names it:
the ``blob -> sidecars`` map is built over every file in ``objects/`` and
rebuilt under the ingest/GC lock at each batch. One sidecar that does not
read (or an ``objects/`` folder that does not list) keeps EVERY blob, because
it may name any of them. A blob nothing names is a candidate under the same
grace and sighting rules as an object. A blob-index entry is deleted only if
it names the object being collected; a blob's thumbnails go with the blob.

**Deleting** (M15) needs the token of a dry run made on THIS device for THIS
root, at most `TOKEN_HOURS` old, and consumes it whether or not the collection
completes. Each batch -- up to `BATCH_OBJECTS` objects or `BATCH_SECONDS` --
holds `locks.image_ingest_gc_lock` and then, per object, its stripe, the order
ingest takes them in. Under them every candidate is re-checked (mtime, root,
reachability by the fresh walk, the blob refcount) and then the sidecar goes,
then -- refcount permitting -- the blob, its index entry and its thumbnails.
An ingest that waited on the lock finds no sidecar and recreates the object;
one that got in first touched the mtime, and the re-check skips the object.

**The root is pinned** (M4). Every path is built from the `root` the caller
captured, and ``paths.home().resolve() == root`` is asserted before every
destructive step: on a mismatch the run stops, deletes nothing more, and its
report says ``root-changed``. Nothing outside ``assets/image-store/`` is ever
deleted, apart from the collected blobs' index entries and thumbnails under
``.cache`` and this module's own tokens; `_delete_file` refuses any other path
and any path reached through a link.

Both calls leave a report (`maintenance_reports.write`) on every ending --
complete, blocked, refused, cancelled, root-changed or failed -- and return it.
The two share one shape. Nothing here takes a campaign lock or writes campaign
state.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import stat
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from . import (
    atomic,
    image_collections,
    image_hash,
    image_migration,
    image_refs,
    image_store,
    image_surfaces,
    locks,
    maintenance_reports,
    paths,
    thumbs,
)

_log = logging.getLogger(__name__)

GRACE_DAYS_DEFAULT = 30
"""How old an unreachable object (and this device's first sighting of it) must
be before it is collected. Long enough for a sync client to deliver a
placement another device made while this one was away."""

GRACE_DAYS_MIN = 7
"""No grace period below this is accepted."""

SCAN_GAP_HOURS = 24
"""How far apart the two scans that saw an object unreachable must be."""

BATCH_OBJECTS = 100
"""The most objects one hold of the ingest/GC lock deletes."""

BATCH_SECONDS = 2.0
"""The longest one hold of the ingest/GC lock lasts, give or take an object:
an upload waits on it."""

STAGING_MIN_AGE_SECONDS = 3600
"""A staged work directory touched more recently than this blocks deletion."""

TOKEN_HOURS = 24
"""How long a dry run's token stays good."""

THUMB_WIDTHS = (128, 256, 320, 512, 1024)
"""The widths a thumbnail is ever made at (`routes.common.THUMB_BUCKETS`,
which a store module may not import; the test holds the two equal)."""

STAGING_DIRS = (".world-staging", ".module-staging")
"""Where whole trees are built before they are published
(`worlds.staging.staging_root`, `module_edit.staging`)."""

JOURNALS_DIR = (".cache", "image-collection-imports")
"""Where harvest journals live, one folder per world
(`image_collections.journal_directory`)."""

FORMAT = 1

_DAY = 24 * 3600.0
_GC_DIR = (".cache", "image-store", "gc")
_THUMBS_DIR = (".cache", "thumbs")
_TOKEN = re.compile(r"[0-9a-f]{32}")
_BLOB_NAME = re.compile(r"([0-9a-f]{64})\.([a-z]+)")
#: The only trees a collection deletes in, relative to the root.
_DELETABLE = (("assets", "image-store", "objects"), ("assets", "image-store", "blobs"),
              (".cache", "image-store", "blob-index"), _THUMBS_DIR)

#: A report's `state`.
COMPLETE = "complete"
BLOCKED = "blocked"
CANCELLED = "cancelled"
REFUSED = "refused"
ROOT_CHANGED = "root-changed"
FAILED = "failed"


class _RootChangedError(Exception):
    """The live store root is no longer the one this run pinned."""


class _UnsafePathError(Exception):
    """A path the collector was about to delete runs through a link or is not
    a regular file. Stops the run: the tree is not what the scan saw."""

    def __init__(self, path: Path) -> None:
        super().__init__(str(path))
        self.path = path


# ---- paths -----------------------------------------------------------------

def _gc_dir(root: Path) -> Path:
    return Path(root).joinpath(*_GC_DIR)


def sightings_path(root: Path) -> Path:
    """This device's sightings for the store at `root`."""
    return _gc_dir(root) / f"{maintenance_reports.device_key(root)}.json"


def _tokens_dir(root: Path) -> Path:
    return _gc_dir(root) / "tokens"


def token_path(root: Path, token: str) -> Path:
    """Where the dry run that issued `token` recorded it. ValueError for a
    string that is not a token, so nothing else reaches a path."""
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise ValueError("not a collection token")
    return _tokens_dir(root) / f"{token}.json"


def blob_thumbnails(root: Path, sha: str) -> list[Path]:
    """Every thumbnail path `thumbs` could have made of blob `sha` at this
    revision: each bucket width, both encoders, every suffix. Most are not
    there; the caller deletes the ones that are."""
    out: list[Path] = []
    for enc in (thumbs.ENCODER, thumbs.FALLBACK_ENCODER):
        gen = Path(root).joinpath(*_THUMBS_DIR, f"r{thumbs.REVISION}-{enc}")
        for width in THUMB_WIDTHS:
            key = hashlib.sha256(f"cas|{sha}|{width}|{enc}".encode()).hexdigest()[:32]
            out.extend(gen / f"{key}{suffix}" for suffix in (".webp", ".jpg", ".png"))
    return out


def _rel(root: Path, path: Path) -> str:
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _blob_key(sha: str, ext: str) -> str:
    return f"{sha}.{ext}"


def _split_key(key: str) -> tuple[str, str] | None:
    m = _BLOB_NAME.fullmatch(key)
    return (m.group(1), m.group(2)) if m and m.group(2) in image_store.MIME else None


# ---- the pinned root ----------------------------------------------------------

def _live_root() -> Path | None:
    try:
        return paths.home().resolve()
    except (OSError, RuntimeError):
        return None


def _check_root(root: Path) -> None:
    if _live_root() != root:
        raise _RootChangedError()


def _delete_file(root: Path, path: Path) -> int:
    """Unlink one file the collector built the path of, after checking the
    live root is still `root`. Returns the bytes freed, 0 when it was not
    there. Only under `_DELETABLE`, never through a link, never anything but a
    regular file (`_UnsafePathError` otherwise)."""
    _check_root(root)
    rel = Path(path).relative_to(root).parts      # ValueError: not ours at all
    if not any(rel[:len(p)] == p for p in _DELETABLE):
        raise _UnsafePathError(Path(path))
    cur = Path(root)
    for part in rel[:-1]:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            return 0
        if not stat.S_ISDIR(st.st_mode):        # a link, or anything else
            raise _UnsafePathError(Path(path))
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return 0
    if not stat.S_ISREG(st.st_mode):
        raise _UnsafePathError(Path(path))
    try:
        os.unlink(path)
    except FileNotFoundError:
        return 0
    return st.st_size


# ---- roots ------------------------------------------------------------------

@dataclass
class _Roots:
    reachable: set[str] = field(default_factory=set)
    placements: set[Path] = field(default_factory=set)
    blocking: list[dict] = field(default_factory=list)

    def block(self, root: Path, path: Path, reason: str) -> None:
        self.blocking.append({"path": _rel(root, path), "reason": reason})


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
        if not e.name.endswith(".json") or atomic.is_write_temp(path):
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
        if e.is_symlink():
            r.block(root, Path(e.path), image_refs.SYMLINK)
            return None
    return entries


def _manifests(root: Path, r: _Roots) -> None:
    worlds = root / "worlds"
    if not _lstat_dir(root, worlds, r):
        return
    for w in _strict_listing(root, worlds, r) or []:
        if not w.is_dir(follow_symlinks=False):
            continue
        d = Path(w.path).joinpath(*image_surfaces.COLLECTIONS_DIR)
        if _lstat_dir(root, d, r):
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
    base = root.joinpath(*JOURNALS_DIR)
    if not _lstat_dir(root, base, r):
        return
    for w in _strict_listing(root, base, r) or []:
        if not w.is_dir(follow_symlinks=False):
            continue
        for e in _strict_listing(root, Path(w.path), r) or []:
            path = Path(e.path)
            if not e.name.endswith(".json") or atomic.is_write_temp(path):
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
    try:
        newest = os.lstat(work).st_mtime
        for d, entries in image_refs.strict_dirs(work):
            for e in entries:
                newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
            if d.name == image_surfaces.COLLECTIONS_DIR[-1] \
                    and d.parent.name == image_surfaces.COLLECTIONS_DIR[0]:
                _manifest_entries(root, entries, r)
    except image_refs.ImageRefParseError as exc:
        r.block(root, exc.path, exc.reason)
        return
    except OSError:
        r.block(root, work, image_refs.UNREADABLE)
        return
    _refs(root, work, r, count=False)
    if newest > now - STAGING_MIN_AGE_SECONDS:
        r.block(root, work, "staging-in-progress")


def _staging(root: Path, now: float, r: _Roots) -> None:
    for name in STAGING_DIRS:
        base = root / name
        if not _lstat_dir(root, base, r):
            continue
        for e in _strict_listing(root, base, r) or []:
            if e.is_dir(follow_symlinks=False):
                _staged(root, Path(e.path), now, r)
            # A file here is bookkeeping (a module edit's journal), not a tree.


def _walk_roots(root: Path, now: float, cancel: Callable[[], bool]) -> _Roots | None:
    """Every root, strictly; None when cancelled part-way."""
    r = _Roots()
    for top in ("worlds", "campaigns"):
        _refs(root, root / top, r, count=True)
        if cancel():
            return None
    for step in (_manifests, _journals, _work_map):
        step(root, r)
    _staging(root, now, r)
    return None if cancel() else r


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
    key = _blob_key(sha, ext)
    return key if _split_key(key) else None


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


def _store_links(root: Path) -> list[Path]:
    """Links between the root and the store's own folders: a deletion
    through one would land outside the pinned root."""
    store = image_store.store_root(root)
    candidates = [store.parent, store, store / "objects", store / "blobs"]
    return [p for p in candidates if p.is_symlink()]


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
        key = None if obj is None else _blob_key(obj.blob_sha256, obj.ext)
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
            key = _split_key(e.name)
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


# ---- sightings ------------------------------------------------------------------

def _number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _as_number(v: object) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _load_sightings(root: Path) -> dict[str, dict[str, float]]:
    """This device's first unreachable sightings. Missing or garbled reads as
    none: every clock restarts, which only ever delays a collection."""
    out: dict[str, dict[str, float]] = {"objects": {}, "blobs": {}}
    try:
        raw = json.loads(sightings_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return out
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        return out
    for kind, valid in (("objects", image_hash.is_image_id),
                        ("blobs", lambda k: _split_key(k) is not None)):
        got = raw.get(kind)
        if isinstance(got, dict):
            out[kind] = {k: float(v) for k, v in got.items() if valid(k) and _number(v)}
    return out


def _save_sightings(root: Path, sightings: dict[str, dict[str, float]]) -> None:
    path = sightings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps({"format": FORMAT, **sightings},
                                       indent=1, sort_keys=True) + "\n")


@dataclass
class _Verdict:
    why: str | None             # None: collectable now
    at: float | None            # when it becomes collectable (None: cannot say)
    skew: str | None = None     # "mtime" or "sighting"
    first: float = 0.0          # the sighting to keep


def _judge(now: float, grace: float, mtimes: list[float], prior: float | None) -> _Verdict:
    """M13 for one object or blob, given its mtimes and this device's
    earlier first sighting of it (None: this scan is the first)."""
    skew = None
    if prior is not None and prior > now:
        prior, skew = None, "sighting"          # a clock that ran ahead: restart
    first = now if prior is None else prior
    if any(m > now for m in mtimes):
        return _Verdict("clock-skew", None, "mtime", first)
    gap = SCAN_GAP_HOURS * 3600.0
    if prior is None:
        due = max([*(m + grace for m in mtimes), now + grace, now + gap])
        return _Verdict("first-sighting", due, skew, first)
    th = {"grace": max(m + grace for m in mtimes) if mtimes else 0.0,
          "sighting": prior + grace, "second-scan": prior + gap}
    due = max(th.values())
    if now >= due:
        return _Verdict(None, due, skew, first)
    return _Verdict(max(th, key=lambda k: th[k]), due, skew, first)


# ---- reports and tokens ----------------------------------------------------------

def _new_report(mode: str, run_id: str, now: float, grace_days: float | None) -> dict:
    return {
        "kind": "image-gc", "mode": mode, "run_id": run_id, "state": "running",
        "started_at": now, "finished_at": None, "grace_days": grace_days,
        "counts": {"placements": 0, "objects": 0, "blobs": 0, "unreferenced": 0,
                   "collectable": 0, "collectable_blobs": 0, "protected": 0},
        "collectable": [], "collectable_blobs": [], "protected": [],
        "reclaimable_bytes": 0, "blocking": [], "unreadable_sidecars": [],
        "clock_skew": [], "token": None, "token_expires_at": None,
        "deleted": {"objects": [], "blobs": [], "bytes": 0},
        "skipped": [], "kept_blobs": [], "error": None,
    }


def _finish(root: Path, run_id: str, report: dict) -> None:
    """Write the report, whatever the ending. A report that cannot be
    written is logged, never raised over the error that ended the run."""
    report["finished_at"] = time.time()
    try:
        maintenance_reports.write(root, run_id, report)
    except Exception:                                        # noqa: BLE001
        _log.warning("could not write the image collection report %s", run_id,
                     exc_info=True)


def _run_id(root: Path, run_id: str | None) -> str:
    run_id = run_id or uuid.uuid4().hex
    maintenance_reports.report_path(root, run_id)     # ValueError for a bad id
    return run_id


def _issue_token(root: Path, report: dict, device: str, now: float,
                 grace_days: float) -> None:
    ids = [row["id"] for row in report["collectable"]]
    blobs = [row["blob"] for row in report["collectable_blobs"]]
    if not ids and not blobs:
        return
    token = secrets.token_hex(16)
    path = token_path(root, token)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps({
        "format": FORMAT, "token": token, "ids": ids, "blobs": blobs,
        "root": str(root), "device": device, "scanned_at": now,
        "grace_days": grace_days, "run_id": report["run_id"]}, indent=1) + "\n")
    report["token"] = token
    report["token_expires_at"] = now + TOKEN_HOURS * 3600.0
    _prune_tokens(root, device, now)


def _read_token(root: Path, token: object, device: str, now: float
                ) -> tuple[dict | None, str]:
    """The token's record when it may be used here and now, else None and
    why. Read-only: a token another device issued is never consumed here."""
    try:
        path = token_path(root, token)          # type: ignore[arg-type]
    except ValueError:
        return None, "not a token"
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None, "unknown or already used"
    if not isinstance(rec, dict) or rec.get("format") != FORMAT or rec.get("token") != token:
        return None, "unknown or already used"
    if rec.get("device") != device:
        return None, "issued on another device"
    if rec.get("root") != str(root):
        return None, "issued for another store"
    at, grace = _as_number(rec.get("scanned_at")), _as_number(rec.get("grace_days"))
    if at is None or grace is None or grace < GRACE_DAYS_MIN:
        return None, "unknown or already used"
    if at > now:
        return None, "issued in the future (clock skew)"
    if now - at > TOKEN_HOURS * 3600.0:
        return None, "expired"
    ids, blobs = rec.get("ids"), rec.get("blobs")
    if (not isinstance(ids, list) or not all(image_hash.is_image_id(i) for i in ids)
            or not isinstance(blobs, list)
            or not all(isinstance(b, str) and _split_key(b) for b in blobs)):
        return None, "unknown or already used"
    return rec, ""


def _claim_token(root: Path, token: str, rec: dict) -> bool:
    """Consume the token, atomically: of two claims, one rename wins."""
    path = token_path(root, token)
    claimed = path.with_name(f".{token}.{uuid.uuid4().hex}.claimed")
    try:
        os.replace(path, claimed)
    except OSError:
        return False
    try:
        same = json.loads(claimed.read_text(encoding="utf-8")) == rec
    except (OSError, ValueError, RecursionError):
        same = False
    with contextlib.suppress(OSError):
        claimed.unlink()
    return same


def _prune_tokens(root: Path, device: str, now: float) -> None:
    """Drop this device's expired tokens. Fail-soft, and nobody else's."""
    try:
        entries = list(_tokens_dir(root).iterdir())
    except OSError:
        return
    for p in entries:
        stem, dot, ext = p.name.rpartition(".")
        if not dot or ext != "json" or not _TOKEN.fullmatch(stem):
            continue
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            if (isinstance(rec, dict) and rec.get("device") == device
                    and _number(rec.get("scanned_at"))
                    and now - rec["scanned_at"] > TOKEN_HOURS * 3600.0):
                p.unlink()
        except (OSError, ValueError, RecursionError):
            continue


# ---- scan --------------------------------------------------------------------------

def _check_grace(grace_days: object) -> float:
    if not _number(grace_days) or grace_days < GRACE_DAYS_MIN:  # type: ignore[operator]
        raise ValueError(f"the grace period is at least {GRACE_DAYS_MIN} days")
    return float(grace_days)                                    # type: ignore[arg-type]


def scan(root: Path, *, cancel: Callable[[], bool], run_id: str | None = None,
         grace_days: float = GRACE_DAYS_DEFAULT, now: float | None = None) -> dict:
    """The dry run (module docstring): what is collectable now, what will be
    and when, and -- when something is and nothing blocked -- a token for
    `collect`. Records this device's sightings unless blocked or cancelled.
    Writes nothing else but its report and the token. `root` is the pinned
    store root (``paths.home().resolve()`` when the run started)."""
    grace_days = _check_grace(grace_days)
    root = Path(root)
    run_id = _run_id(root, run_id)
    at = time.time() if now is None else now
    report = _new_report("scan", run_id, at, grace_days)
    try:
        report["state"] = _scan(root, report, cancel, grace_days, at)
    except BaseException as exc:
        report["state"], report["error"] = FAILED, type(exc).__name__
        raise
    finally:
        _finish(root, run_id, report)
    return report


def _classify(root: Path, report: dict, roots: _Roots, side: _Sidecars,
              blobs: dict[str, os.stat_result], sightings: dict, grace: float,
              at: float) -> dict[str, dict[str, float]]:
    """Fill the report's candidate lists; return the sightings to keep."""
    seen: dict[str, dict[str, float]] = {
        "objects": _classify_objects(report, roots, side, blobs, sightings["objects"],
                                     grace, at),
        "blobs": _classify_blobs(report, side, blobs, sightings["blobs"], grace, at)}
    return seen


def _classify_objects(report: dict, roots: _Roots, side: _Sidecars,
                      blobs: dict[str, os.stat_result], sightings: dict[str, float],
                      grace: float, at: float) -> dict[str, float]:
    seen: dict[str, float] = {}
    collectable: dict[str, Path] = {}
    for image_id, path in sorted(side.ids.items()):
        key = side.names.get(path)
        if key is None or image_id in roots.reachable:
            continue
        report["counts"]["unreferenced"] += 1
        blob = blobs.get(key)
        mtimes = [side.sigs[path][0] / 1e9, *([blob.st_mtime] if blob else [])]
        v = _judge(at, grace, mtimes, sightings.get(image_id))
        seen[image_id] = v.first
        if v.skew:
            report["clock_skew"].append({"id": image_id, "blob": None, "what": v.skew})
        if v.why is None:
            collectable[image_id] = path
        else:
            report["protected"].append({"id": image_id, "blob": key, "why": v.why,
                                        "collectable_at": v.at})
    _reclaimable(report, side, blobs, collectable)
    return seen


def _reclaimable(report: dict, side: _Sidecars, blobs: dict[str, os.stat_result],
                 collectable: dict[str, Path]) -> None:
    """The collectable rows and their exact bytes: a sidecar always, its blob
    only when every holder of it goes too and no sidecar is unreadable."""
    keep_all = side.keep_all_blobs()
    gone = set(collectable.values())
    counted: set[str] = set()
    for image_id, path in collectable.items():
        key = side.names[path] or ""
        size = side.sigs[path][1]
        holders = side.refs.get(key, set())
        if not keep_all and key in blobs and key not in counted and holders <= gone:
            size += blobs[key].st_size
            counted.add(key)
        report["collectable"].append({"id": image_id, "blob": key, "bytes": size})
        report["reclaimable_bytes"] += size


def _classify_blobs(report: dict, side: _Sidecars, blobs: dict[str, os.stat_result],
                    sightings: dict[str, float], grace: float, at: float
                    ) -> dict[str, float]:
    keep_all = side.keep_all_blobs()
    seen: dict[str, float] = {}
    for key, st in sorted(blobs.items()):
        if side.refs.get(key):
            continue
        v = _judge(at, grace, [st.st_mtime], sightings.get(key))
        seen[key] = v.first
        if v.skew:
            report["clock_skew"].append({"id": None, "blob": key, "what": v.skew})
        if keep_all:
            report["protected"].append({"id": None, "blob": key,
                                        "why": "unreadable-sidecars", "collectable_at": None})
        elif v.why is None:
            report["collectable_blobs"].append({"id": None, "blob": key, "bytes": st.st_size})
            report["reclaimable_bytes"] += st.st_size
        else:
            report["protected"].append({"id": None, "blob": key, "why": v.why,
                                        "collectable_at": v.at})
    return seen


def _scan(root: Path, report: dict, cancel: Callable[[], bool], grace_days: float,
          at: float) -> str:
    try:
        _check_root(root)
    except _RootChangedError:
        return ROOT_CHANGED
    device = maintenance_reports.device_key(root)
    roots = _walk_roots(root, at, cancel)
    if roots is None:
        return CANCELLED
    side = _list_objects(root)
    blobs = _list_blobs(root, side)
    if cancel():
        return CANCELLED
    for link in [*_store_links(root), *side.links]:
        roots.block(root, link, image_refs.SYMLINK)
    report["blocking"] = roots.blocking
    report["unreadable_sidecars"] = [_rel(root, p) for p in side.unreadable()]
    report["unreadable_sidecars"] += [_rel(root, p) for p in side.unlisted]
    counts = report["counts"]
    counts.update(placements=len(roots.placements), objects=len(side.ids), blobs=len(blobs))
    if roots.blocking:
        return BLOCKED
    seen = _classify(root, report, roots, side, blobs, _load_sightings(root),
                     grace_days * _DAY, at)
    counts.update(collectable=len(report["collectable"]),
                  collectable_blobs=len(report["collectable_blobs"]),
                  protected=len(report["protected"]))
    try:
        _check_root(root)
    except _RootChangedError:
        _clear_candidates(report)
        return ROOT_CHANGED
    _save_sightings(root, seen)
    _issue_token(root, report, device, at, grace_days)
    return COMPLETE


def _clear_candidates(report: dict) -> None:
    report["collectable"], report["collectable_blobs"], report["protected"] = [], [], []
    report["reclaimable_bytes"] = 0


# ---- collect ------------------------------------------------------------------------

def collect(root: Path, token: str, *, cancel: Callable[[], bool],
            run_id: str | None = None, now: float | None = None) -> dict:
    """Delete what the dry run that issued `token` found collectable, and
    nothing else (module docstring). The token is consumed even when the
    collection then stops; a blocked walk, a root change or a cancel leaves
    the rest for the next scan."""
    root = Path(root)
    run_id = _run_id(root, run_id)
    at = time.time() if now is None else now
    report = _new_report("collect", run_id, at, None)
    try:
        report["state"] = _collect(root, token, report, cancel, at)
    except BaseException as exc:
        report["state"], report["error"] = FAILED, type(exc).__name__
        raise
    finally:
        _finish(root, run_id, report)
    return report


@dataclass
class _Run:
    root: Path
    report: dict
    roots: _Roots
    side: _Sidecars
    grace: float
    now: float

    def skip(self, image_id: str | None, blob: str | None, reason: str) -> None:
        self.report["skipped"].append({"id": image_id, "blob": blob, "reason": reason})


def _collect(root: Path, token: str, report: dict, cancel: Callable[[], bool],
             at: float) -> str:
    try:
        _check_root(root)
    except _RootChangedError:
        return ROOT_CHANGED
    device = maintenance_reports.device_key(root)
    rec, why = _read_token(root, token, device, at)
    if rec is None or not _claim_token(root, token, rec):
        report["error"] = why or "unknown or already used"
        return REFUSED
    report["token"] = token
    report["grace_days"] = rec["grace_days"]
    roots = _walk_roots(root, at, cancel)          # fresh, and outside the lock
    if roots is None:
        return CANCELLED
    side = _list_objects(root)
    for link in [*_store_links(root), *side.links]:
        roots.block(root, link, image_refs.SYMLINK)
    if roots.blocking:
        report["blocking"] = roots.blocking
        return BLOCKED
    run = _Run(root, report, roots, side, rec["grace_days"] * _DAY, at)
    queue: list[tuple[str, str]] = []
    for image_id in rec["ids"]:
        if image_id in roots.reachable:
            run.skip(image_id, None, "reachable")
        else:
            queue.append(("object", image_id))
    queue += [("blob", key) for key in rec["blobs"]]
    try:
        return _batches(run, queue, cancel)
    except _RootChangedError:
        return ROOT_CHANGED
    except _UnsafePathError as exc:
        report["blocking"].append({"path": _rel(root, exc.path), "reason": "unsafe-path"})
        return BLOCKED


def _batches(run: _Run, queue: list[tuple[str, str]], cancel: Callable[[], bool]) -> str:
    """Delete in batches, each one hold of the ingest/GC lock (M15)."""
    while queue:
        if cancel():
            return CANCELLED
        with locks.image_ingest_gc_lock():
            _check_root(run.root)
            run.side = _list_objects(run.root, run.side)   # the refcount, under the lock
            if run.side.links:
                raise _UnsafePathError(run.side.links[0])
            started, done = time.monotonic(), 0
            while queue:
                if cancel():
                    return CANCELLED
                kind, item = queue.pop(0)
                if kind == "object":
                    _collect_object(run, item)
                else:
                    _collect_blob(run, item)
                done += 1
                if done >= BATCH_OBJECTS or time.monotonic() - started >= BATCH_SECONDS:
                    break
    return COMPLETE


def _young(run: _Run, mtimes: list[float]) -> str | None:
    if any(m > run.now for m in mtimes):
        return "clock-skew"
    if any(run.now - m < run.grace for m in mtimes):
        return "grace"
    return None


def _collect_object(run: _Run, image_id: str) -> None:
    """One object, under its stripe: re-check, then the sidecar, then -- when
    nothing else names it -- the blob, its index entry and thumbnails."""
    root = run.root
    with locks.image_object_lock(image_id):
        _check_root(root)
        path = image_store.object_path(image_id, root=root)
        obj = image_store.read_fresh(image_id, root=root)
        if obj is None:
            run.skip(image_id, None, "unreadable" if path.exists() else "gone")
            return
        if image_id in run.roots.reachable:
            run.skip(image_id, None, "reachable")
            return
        key = _blob_key(obj.blob_sha256, obj.ext)
        blob = image_store.blob_path(obj.blob_sha256, obj.ext, root=root)
        try:
            mtimes = [os.lstat(path).st_mtime]
            with contextlib.suppress(FileNotFoundError):
                mtimes.append(os.lstat(blob).st_mtime)
        except OSError:
            run.skip(image_id, key, "unreadable")
            return
        if (why := _young(run, mtimes)) is not None:
            run.skip(image_id, key, why)
            return
        freed = _delete_file(root, path)
        run.side.drop(path)
        blob_freed = 0
        if run.side.keep_all_blobs():
            run.report["kept_blobs"].append({"id": image_id, "blob": key,
                                             "reason": "unreadable-sidecars"})
        elif run.side.refs.get(key):
            run.report["kept_blobs"].append({"id": image_id, "blob": key, "reason": "shared"})
        elif len(mtimes) > 1:             # the blob was there when re-checked
            blob_freed = _delete_blob(root, obj.blob_sha256, obj.ext)
            if not blob.exists():
                run.report["deleted"]["blobs"].append(
                    {"id": image_id, "blob": key, "bytes": blob_freed})
        _drop_index_entry(root, obj.blob_sha256, image_id)
        run.report["deleted"]["objects"].append(
            {"id": image_id, "blob": key, "bytes": freed + blob_freed})
        run.report["deleted"]["bytes"] += freed + blob_freed


def _delete_blob(root: Path, sha: str, ext: str) -> int:
    freed = _delete_file(root, image_store.blob_path(sha, ext, root=root))
    for thumb in blob_thumbnails(root, sha):
        _delete_file(root, thumb)
    return freed


def _drop_index_entry(root: Path, sha: str, image_id: str) -> None:
    """The blob-index entry, only when it names the collected object."""
    path = image_store.index_path(sha, root=root)
    try:
        named = path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return
    if named == image_id:
        _delete_file(root, path)


def _collect_blob(run: _Run, key: str) -> None:
    """One orphan blob, under the ingest/GC lock (an ingest publishing it
    holds the same lock until its sidecar is written)."""
    root = run.root
    _check_root(root)
    split = _split_key(key)
    if split is None:
        return
    sha, ext = split
    if run.side.keep_all_blobs():
        run.skip(None, key, "unreadable-sidecars")
        return
    if run.side.refs.get(key):
        run.skip(None, key, "named")
        return
    blob = image_store.blob_path(sha, ext, root=root)
    try:
        mtime = os.lstat(blob).st_mtime
    except FileNotFoundError:
        run.skip(None, key, "gone")
        return
    if (why := _young(run, [mtime])) is not None:
        run.skip(None, key, why)
        return
    freed = _delete_blob(root, sha, ext)
    run.report["deleted"]["blobs"].append({"id": None, "blob": key, "bytes": freed})
    run.report["deleted"]["bytes"] += freed

"""Image garbage collection: dispose of what nothing anywhere reaches (stage 4).

Spec section 12 and stage-4 rulings M12-M15, amended by the viewer-removal
spec (sections 2 and 3). Deleting a placement deletes only its ref; there is
no reference count, so an object nothing names any more stays until this
collects it. Two calls, both run as the ``maintenance`` run class and both
fail closed:

* **`scan`** is the dry run. It walks every root strictly, lists the store,
  and -- when something is collectable and nothing blocked the walk, or
  something waits in the outbox -- persists a single-use token naming exactly
  that (a blocked walk's names only the outbox: it drains, nothing more).
* **`collect`** takes such a token and disposes of at most what it names,
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
report names each blocking path, and nothing is collected. So does a staged
work directory touched within the last hour (`STAGING_MIN_AGE_SECONDS`): a
fork or an import is still filling it, and what it will place is not on disk
yet.

**Candidates** (M13; viewer-removal spec 2.1). An object is collectable only
when all of these hold:

- no root reaches it;
- its sidecar's and its blob's mtimes are both at least one hour old
  (`GRACE_HOURS`; ingest touches the sidecar, so an object somebody ingested
  and has not placed yet -- a Chub gallery mid-import, a harvested member, a
  staged bundle -- is never collected under them);
- and the collection carries a token from a scan on this device for this
  root, at most `TOKEN_HOURS` old.

The decision is made on mtimes alone: there is no per-device sightings file
(one an older build left under ``.cache/image-store/gc/`` is neither read nor
written). An mtime in the future is never collectable and is reported as
clock skew. The scan agrees with the collection: an otherwise collectable
object whose blob would be held (`_orphan_hold`: an unreadable sidecar, or an
unarrived object) is protected with that reason and is not offered or named
by the token.

**Blobs** (M14). A blob leaves the store only when no surviving readable
sidecar names it: the ``blob -> sidecars`` map is built over every file in
``objects/`` and rebuilt under the ingest/GC lock at each batch. One sidecar
that does not read (or an ``objects/`` folder that does not list) holds EVERY
blob, because it may name any of them. A blob nothing names is a candidate
under the same grace rule as an object -- except while some placement names
an object with no readable sidecar (one that has not arrived yet, mid-sync),
when every orphan blob is held: the unarrived object's blob may be among
them. An ``objects/`` folder that is absent beside a ``blobs/`` one blocks
outright. A blob-index entry is deleted only if it names the object being
collected; a blob's thumbnails go with the blob.

**Disposal** (M15; viewer-removal spec 3). It needs the token of a dry run
made on THIS device for THIS root, at most `TOKEN_HOURS` old, and consumes it
whether or not the collection completes -- unless `recycle_bin.check` finds no
real bin here, which refuses the collection (``no_recycle_bin``) before the
token is touched. A picture is never deleted: its blob goes to the operating
system's recycle bin, through this device's **outbox**
(`outbox_dir`, ``.cache/image-store/recycle/<device key>/``), under a readable
name (`bin_name`). Its sidecar, index entry and thumbnails are deleted.

Each batch -- up to `BATCH_OBJECTS` objects or `BATCH_SECONDS` -- holds
`locks.image_ingest_gc_lock` and then, per object, its stripe, the order
ingest takes them in. Under them every candidate is re-checked (mtime, root,
reachability by the fresh walk, the blob refcount), and then:

1. an object whose blob is held (an unreadable sidecar, an unarrived object)
   is skipped whole, sidecar included -- deleting the sidecar would lose its
   description and bin nothing;
2. an object whose blob another sidecar names loses only its sidecar, and is
   reported ``released`` (``shared``): the picture is still in the store;
3. otherwise the blob is moved into the outbox with one ``os.replace`` and
   THEN the sidecar is deleted. The move is the commit: a crash after it
   leaves a sidecar naming a missing blob, which no placement reaches and the
   next collection releases (``blob-missing``) -- the same as a blob already
   gone at this step. This reverses ingest's order (blob, then sidecar) and is
   safe for the same reason: both steps happen under the lock and the stripe,
   so an ingest of the same id waits, finds no sidecar and recreates the
   object with a fresh blob. A placement of that id made through
   ``assets.link_in`` (which needs neither ingest nor this lock) after the
   roots walk and before the batch is a race that predates the bin; its
   outcome is now a recoverable file in the bin instead of a deletion.
   An orphan blob is moved the same way, with no description.

Only after the lock is released are the batch's moved files sent to the bin
(`recycle_bin.send_batch`), the root checked before each, so a slow shell or
Finder call never holds up an upload. A file the bin takes is ``recycled``; a
file it refuses stays in the outbox under its readable name and stops the run
``trash-unavailable``; a batch Windows did not confirm stops it
``recycle-bin-unverified`` (see `recycle_bin`). A cancel or a root change sends
nothing more -- the bin calls ask before each file: what moved and was not sent
waits in the outbox. A collection therefore begins, once
its token is claimed and before its first batch, by sending this device's
outbox to the bin, and a refusal there stops it before anything else leaves
the store. A scan reports what waits (``outbox``) and issues a token for it
alone -- even when the walk is blocked, a token naming no candidate, so the
collection drains and then stops at the same walk -- so waiting files can
always be drained; files in another device's
outbox (or this one's under an earlier store path) are counted in
``foreign_outboxes`` and never touched. The outbox syncs with ``.cache``,
which is accepted traffic.

**Accepted risk.** A placement made on another device that has not synced here
yet does not reach its picture, so this device can bin it after an hour; the
deletion then syncs back and that placement resolves to nothing. Uploading the
binned file again, on the same decoder, lands under the same object id and
heals it -- without its description and tags.

**The root is pinned** (M4). Every path is built from the `root` the caller
captured, and ``paths.home().resolve() == root`` is asserted before every
destructive step -- each deletion, each move and each bin call: on a mismatch
the run stops, disposes of nothing more, and its report says
``root-changed``. Nothing outside ``assets/image-store/`` is ever deleted,
apart from the collected blobs' index entries and thumbnails under ``.cache``
and this module's own tokens; `_delete_file` refuses any other path and any
path reached through a link, and the outbox is not among the trees it deletes
in. A blob is moved only from ``blobs/`` into the outbox, and a file is sent
to the bin only from the outbox, each a regular file reached through no link.

Both calls leave a report (`maintenance_reports.write`) on every ending --
complete, blocked, refused, cancelled, root-changed, trash-unavailable,
recycle-bin-unverified or failed -- and return it. The two share one shape,
and each carries ``recycle_bin`` (the availability answer), ``outbox`` and
``foreign_outboxes``. Nothing here takes a campaign lock or writes campaign
state.
"""

from __future__ import annotations

import contextlib
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
    recycle_bin,
    thumbs,
)

_log = logging.getLogger(__name__)

GRACE_HOURS = 1
"""How old an unreachable object's sidecar and blob must be before it is
collected: a structural margin over the longest request or run that ingests a
picture and has not placed it yet, not a measured one."""

BATCH_OBJECTS = 100
"""The most objects one hold of the ingest/GC lock deletes."""

BATCH_SECONDS = 2.0
"""The longest one hold of the ingest/GC lock lasts, give or take an object:
an upload waits on it."""

STAGING_MIN_AGE_SECONDS = 3600
"""A staged work directory touched more recently than this blocks deletion."""

TOKEN_HOURS = 24
"""How long a dry run's token stays good."""

STAGING_DIRS = (".world-staging", ".module-staging")
"""Where whole trees are built before they are published
(`worlds.staging.staging_root`, `module_edit.staging`)."""

FORMAT = 2

_GC_DIR = (".cache", "image-store", "gc")
_THUMBS_DIR = (".cache", "thumbs")
_RECYCLE_DIR = (".cache", "image-store", "recycle")
_BLOBS_DIR = ("assets", "image-store", "blobs")
_TOKEN = re.compile(r"[0-9a-f]{32}")
_BLOB_NAME = re.compile(r"([0-9a-f]{64})\.([a-z]+)")
#: The only trees a collection deletes in, relative to the root. The outbox is
#: deliberately not one: a picture waiting there leaves only for the bin.
_DELETABLE = (("assets", "image-store", "objects"), _BLOBS_DIR,
              (".cache", "image-store", "blob-index"), _THUMBS_DIR)

#: What a bin name may carry of a description, in characters.
BIN_DESCRIPTION_CHARS = 60
#: ... and in UTF-8 bytes, so the whole name (about 30 bytes besides) stays
#: under the 255 a file name may take: 60 four-byte characters would not.
BIN_DESCRIPTION_BYTES = 200
#: Characters a Windows file name may not hold, every control character, and
#: lone surrogates (which no file system can encode).
_NOT_IN_A_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f-\x9f\ud800-\udfff]')

#: A report's `state`.
COMPLETE = "complete"
BLOCKED = "blocked"
CANCELLED = "cancelled"
REFUSED = "refused"
ROOT_CHANGED = "root-changed"
FAILED = "failed"
#: The bin refused a file: it waits in the outbox, and nothing more is collected.
TRASH_UNAVAILABLE = "trash-unavailable"
#: Windows did not confirm a batch reached the bin (`recycle_bin.send_batch`).
RECYCLE_BIN_UNVERIFIED = "recycle-bin-unverified"


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
    return [Path(root) / rel for rel in thumbs.blob_keys(sha)]


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


def outbox_dir(root: Path) -> Path:
    """This device's outbox for this store: where a collected picture waits,
    under its bin name, between leaving ``blobs/`` and reaching the recycle
    bin. Per device key, so a synced ``.cache`` never hands one device's
    waiting pictures to another's collector."""
    return Path(root).joinpath(*_RECYCLE_DIR, maintenance_reports.device_key(root))


def bin_name(description: str | None, sha: str, ext: str) -> str:
    """The file name a collected picture reaches the bin under:
    ``grimoire - <description> - <sha8>.<ext>``, or ``grimoire - <sha8>.<ext>``
    when there is no description or it sanitises to nothing. The description
    loses what a Windows file name cannot hold and its control characters, has
    its whitespace collapsed, is cut to `BIN_DESCRIPTION_CHARS` at a word
    boundary where one exists (and to `BIN_DESCRIPTION_BYTES`), and loses
    trailing dots and spaces."""
    text = ""
    if isinstance(description, str):
        text = " ".join(description.split())
        text = " ".join(_NOT_IN_A_NAME.sub("", text).split())
        if len(text) > BIN_DESCRIPTION_CHARS:
            head = text[:BIN_DESCRIPTION_CHARS]
            if text[BIN_DESCRIPTION_CHARS] != " " and " " in head:
                head = head[:head.rindex(" ")]
            text = head
        while len(text.encode("utf-8")) > BIN_DESCRIPTION_BYTES:
            text = text[:-1]
        text = text.rstrip(". ")
    sha8 = sha[:8]
    return f"grimoire - {text} - {sha8}.{ext}" if text else f"grimoire - {sha8}.{ext}"


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


# ---- the outbox -------------------------------------------------------------------

def _real_dirs(root: Path, target: Path, *, create: bool) -> bool:
    """Whether every component from `root` down to `target` is a real
    directory (never a link). A missing one is made when `create`, else the
    answer is False; anything else there raises `_UnsafePathError`."""
    cur = Path(root)
    for part in Path(target).relative_to(root).parts:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            if not create:
                return False
            with contextlib.suppress(FileExistsError):
                os.mkdir(cur)
            st = os.lstat(cur)
        if not stat.S_ISDIR(st.st_mode):       # a link, or anything else
            raise _UnsafePathError(cur)
    return True


def _free_name(outbox: Path, name: str) -> Path:
    """`name` in `outbox`, or the first of ``<stem> (2).<ext>``,
    ``<stem> (3).<ext>``, ... that nothing there holds yet."""
    stem, dot, ext = name.rpartition(".")
    target, n = outbox / name, 2
    while os.path.lexists(target):
        target = outbox / (f"{stem} ({n}).{ext}" if dot else f"{name} ({n})")
        n += 1
    return target


def _move_to_outbox(root: Path, blob: Path, name: str) -> Path | None:
    """Move one blob into this device's outbox under bin name `name` (or a
    suffixed one), after checking the live root is still `root`: the blob a
    regular file under ``blobs/`` and the outbox a real directory, neither
    reached through a link (`_UnsafePathError` otherwise). Returns where it
    went, or None when the blob was already gone. The move is one
    ``os.replace`` on one volume, so the picture is in exactly one place."""
    _check_root(root)
    rel = Path(blob).relative_to(root).parts     # ValueError: not ours at all
    if rel[:len(_BLOBS_DIR)] != _BLOBS_DIR or len(rel) != len(_BLOBS_DIR) + 2:
        raise _UnsafePathError(Path(blob))
    if not _real_dirs(root, Path(blob).parent, create=False):
        return None
    try:
        st = os.lstat(blob)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise _UnsafePathError(Path(blob))
    outbox = outbox_dir(root)
    _real_dirs(root, outbox, create=True)
    target = _free_name(outbox, name)
    try:
        os.replace(blob, target)
    except FileNotFoundError:
        if os.path.lexists(blob):
            raise                  # the outbox went, not the blob: stop, blob in place
        return None
    return target


def _outbox_files(root: Path) -> list[Path]:
    """The regular files waiting in this device's outbox, by name. Raises
    `_UnsafePathError` when the way to it runs through a link."""
    outbox = outbox_dir(root)
    if not _real_dirs(root, outbox, create=False):
        return []
    with os.scandir(outbox) as it:
        return sorted(Path(e.path) for e in it
                      if e.is_file(follow_symlinks=False)
                      and e.name.casefold() not in image_refs.OS_LITTER)


def _outbox_listing(root: Path) -> tuple[list[str], int]:
    """(this device's waiting file names, how many files wait in other
    devices' outboxes). Read-only and fail-soft: an outbox reached through a
    link lists nothing (the scan blocks on it), and one that does not list
    counts as empty."""
    try:
        mine = [p.name for p in _outbox_files(root)]
    except (OSError, _UnsafePathError):
        mine = []
    recycle = Path(root).joinpath(*_RECYCLE_DIR)
    own = outbox_dir(root).name
    foreign = 0
    try:
        if not _real_dirs(root, recycle, create=False):
            return mine, 0
        with os.scandir(recycle) as it:
            others = [Path(e.path) for e in it
                      if e.name != own and e.is_dir(follow_symlinks=False)]
        for d in others:
            with os.scandir(d) as it:
                foreign += sum(1 for e in it if e.is_file(follow_symlinks=False))
    except (OSError, _UnsafePathError):
        pass
    return mine, foreign


class _BinGuard:
    """`recycle_bin.send_batch`'s `before`: ahead of each bin call, the live
    root is still the pinned one and the file is a regular file directly in
    this device's outbox, reached through no link. Records each file's size
    (before it is sent) and every file it was asked about, so a stop it raises
    still reports what the bin already took: every file before the last."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.outbox = outbox_dir(root)
        self.seen: list[Path] = []
        self.sizes: dict[Path, int] = {}

    def __call__(self, path: Path) -> None:
        self.seen.append(path)
        _check_root(self.root)
        if Path(path).parent != self.outbox:
            raise _UnsafePathError(Path(path))
        if not _real_dirs(self.root, self.outbox, create=False):
            return                 # gone: the bin refuses it, and the run stops
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(st.st_mode):
            raise _UnsafePathError(Path(path))
        self.sizes[path] = st.st_size


def _send(root: Path, report: dict, files: list[Path],
          origin: dict[Path, tuple[str | None, str | None]],
          cancel: Callable[[], bool]) -> str | None:
    """Send outbox `files` to the bin, never under a lock, asking `cancel`
    before each. Every file the bin took is in `recycled`; a stop returns its
    state (`RECYCLE_BIN_UNVERIFIED`, which outranks the others because it is
    the one the reader must act on, then `TRASH_UNAVAILABLE` or `CANCELLED`)
    and leaves the rest waiting in the outbox. A root change or an unsafe path
    raises, as anywhere else in a collection."""
    guard = _BinGuard(root)
    try:
        result = recycle_bin.send_batch(root, files, before=guard, cancel=cancel)
    except (_RootChangedError, _UnsafePathError):
        _recycled(report, guard.seen[:-1], guard.sizes, origin)
        raise
    _recycled(report, result.sent, guard.sizes, origin)
    if result.refused is not None:
        report["error"] = result.error or result.code
    if result.unverified:
        report["unverified"] += [p.name for p in result.unverified]
        return RECYCLE_BIN_UNVERIFIED
    if result.refused is not None:
        return TRASH_UNAVAILABLE
    return CANCELLED if result.cancelled else None


def _recycled(report: dict, sent: list[Path], sizes: dict[Path, int],
              origin: dict[Path, tuple[str | None, str | None]]) -> None:
    for path in sent:
        image_id, key = origin.get(path, (None, None))
        report["recycled"].append({"id": image_id, "blob": key, "name": path.name,
                                   "bytes": sizes.get(path, 0)})


def _drain(root: Path, report: dict, cancel: Callable[[], bool]) -> str | None:
    """Send whatever already waits in this device's outbox -- a crash, a
    refusal or a cancel left it there -- before anything more is collected."""
    files = _outbox_files(root)
    return _send(root, report, files, {}, cancel) if files else None


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
    base = image_collections.raw_journals_root(root)
    if not _lstat_dir(root, base, r):
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


def _unarrived(roots: _Roots, side: _Sidecars) -> list[str]:
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
                  cache, cache / "image-store", _gc_dir(root),
                  image_store.index_path("0" * 64, root=root).parent.parent,
                  Path(root).joinpath(*_THUMBS_DIR),
                  Path(root).joinpath(*_RECYCLE_DIR), outbox_dir(root)]
    out = [(p, image_refs.SYMLINK) for p in candidates if p.is_symlink()]
    if not os.path.lexists(store / "objects") and os.path.lexists(store / "blobs"):
        out.append((store / "objects", "objects-missing"))
    return out


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


# ---- judging ---------------------------------------------------------------------

def _number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _as_number(v: object) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


@dataclass
class _Verdict:
    why: str | None             # None: collectable now; else "grace" or "clock-skew"
    at: float | None            # when it becomes collectable (None: cannot say)
    skew: str | None = None     # "mtime"


def _judge(now: float, grace: float, mtimes: list[float]) -> _Verdict:
    """M13 for one object or blob, on its mtimes alone."""
    if any(m > now for m in mtimes):
        return _Verdict("clock-skew", None, "mtime")
    due = max((m + grace for m in mtimes), default=0.0)
    return _Verdict(None if now >= due else "grace", due)


# ---- reports and tokens ----------------------------------------------------------

#: The ``kind`` a stored collection report carries (`maintenance_reports`).
KIND = "image-gc"


def _new_report(mode: str | None, run_id: str, now: float,
                grace_hours: float | None) -> dict:
    return {
        "kind": KIND, "mode": mode, "run_id": run_id, "state": "running",
        "started_at": now, "finished_at": None, "grace_hours": grace_hours,
        "counts": {"placements": 0, "objects": 0, "blobs": 0, "unreferenced": 0,
                   "collectable": 0, "collectable_blobs": 0, "protected": 0},
        "collectable": [], "collectable_blobs": [], "protected": [],
        "reclaimable_bytes": 0, "blocking": [], "unreadable_sidecars": [],
        "clock_skew": [], "token": None, "token_expires_at": None,
        "deleted": {"objects": [], "bytes": 0},
        "recycled": [], "released": [], "outbox": [], "foreign_outboxes": 0,
        "unverified": [], "recycle_bin": {"ok": None, "code": None, "message": None},
        "skipped": [], "unarrived_objects": [],
        "cache_cleanup": [], "error": None,
    }


def failed_report(mode: str | None, run_id: str, error: str) -> dict:
    """A scan's or a collection's report when the pass raised before it could
    write its own: `_new_report`'s full shape, every count zero and every list
    empty, ended as failed. `error` is the exception's type, never its
    message."""
    now = time.time()
    report = _new_report(mode, run_id, now, None)
    report.update(state=FAILED, error=error, finished_at=now)
    return report


def _finish(root: Path, run_id: str, report: dict) -> None:
    """Write the report, whatever the ending, with what waits in the outboxes
    at that moment. A report that cannot be written is logged, never raised
    over the error that ended the run."""
    try:
        report["outbox"], report["foreign_outboxes"] = _outbox_listing(root)
    except Exception:                                        # noqa: BLE001
        _log.warning("could not list the image collection outbox", exc_info=True)
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


def _issue_token(root: Path, report: dict, device: str, now: float) -> None:
    ids = [row["id"] for row in report["collectable"]]
    blobs = [row["blob"] for row in report["collectable_blobs"]]
    if not ids and not blobs and not report["outbox"]:
        return                      # waiting files alone earn a token: they drain
    token = secrets.token_hex(16)
    path = token_path(root, token)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps({
        "format": FORMAT, "token": token, "ids": ids, "blobs": blobs,
        "root": str(root), "device": device, "scanned_at": now,
        "grace_hours": GRACE_HOURS, "run_id": report["run_id"]}, indent=1) + "\n")
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
    at, grace = _as_number(rec.get("scanned_at")), _as_number(rec.get("grace_hours"))
    if at is None or grace != GRACE_HOURS:
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

def scan(root: Path, *, cancel: Callable[[], bool], run_id: str | None = None,
         now: float | None = None) -> dict:
    """The dry run (module docstring): what is collectable now, what will be
    and when, and -- when something is and nothing blocked, or something
    waits in the outbox -- a token for `collect`. Writes nothing but its report and the token. `root` is the
    pinned store root (``paths.home().resolve()`` when the run started)."""
    root = Path(root)
    run_id = _run_id(root, run_id)
    at = time.time() if now is None else now
    report = _new_report("scan", run_id, at, GRACE_HOURS)
    try:
        report["state"] = _scan(root, report, cancel, at)
    except BaseException as exc:
        report["state"], report["error"] = FAILED, type(exc).__name__
        raise
    finally:
        _finish(root, run_id, report)
    return report


def _classify(report: dict, roots: _Roots, side: _Sidecars,
              blobs: dict[str, os.stat_result], grace: float, at: float) -> None:
    """Fill the report's candidate lists. Both halves ask `_orphan_hold`, the
    question the collection asks, so the scan offers nothing it will skip."""
    hold = _orphan_hold(side, report["unarrived_objects"])
    _classify_objects(report, roots, side, blobs, grace, at, hold)
    _classify_blobs(report, side, blobs, grace, at, hold)


def _classify_objects(report: dict, roots: _Roots, side: _Sidecars,
                      blobs: dict[str, os.stat_result], grace: float, at: float,
                      hold: str | None) -> None:
    collectable: dict[str, Path] = {}
    for image_id, path in sorted(side.ids.items()):
        key = side.names.get(path)
        if key is None or image_id in roots.reachable:
            continue
        report["counts"]["unreferenced"] += 1
        blob = blobs.get(key)
        mtimes = [side.sigs[path][0] / 1e9, *([blob.st_mtime] if blob else [])]
        v = _judge(at, grace, mtimes)
        if v.skew:
            report["clock_skew"].append({"id": image_id, "blob": None, "what": v.skew})
        if v.why is None and hold is not None:
            report["protected"].append({"id": image_id, "blob": key, "why": hold,
                                        "collectable_at": None})
        elif v.why is None:
            collectable[image_id] = path
        else:
            report["protected"].append({"id": image_id, "blob": key, "why": v.why,
                                        "collectable_at": v.at})
    _reclaimable(report, side, blobs, collectable)


def _reclaimable(report: dict, side: _Sidecars, blobs: dict[str, os.stat_result],
                 collectable: dict[str, Path]) -> None:
    """The collectable rows and their exact bytes: a sidecar always, its blob
    only when every holder of it goes too, no sidecar is unreadable and no
    reachable object is unarrived (whose blob it may be)."""
    keep_all = side.keep_all_blobs() or bool(report["unarrived_objects"])
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
                    grace: float, at: float, hold: str | None) -> None:
    for key, st in sorted(blobs.items()):
        if side.refs.get(key):
            continue
        v = _judge(at, grace, [st.st_mtime])
        if v.skew:
            report["clock_skew"].append({"id": None, "blob": key, "what": v.skew})
        if hold is not None:
            report["protected"].append({"id": None, "blob": key,
                                        "why": hold, "collectable_at": None})
        elif v.why is None:
            report["collectable_blobs"].append({"id": None, "blob": key, "bytes": st.st_size})
            report["reclaimable_bytes"] += st.st_size
        else:
            report["protected"].append({"id": None, "blob": key, "why": v.why,
                                        "collectable_at": v.at})


def _scan(root: Path, report: dict, cancel: Callable[[], bool], at: float) -> str:
    report["recycle_bin"] = recycle_bin.check(root).to_dict()
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
    for path, why in [*_store_blocks(root), *((p, image_refs.SYMLINK) for p in side.links)]:
        roots.block(root, path, why)
    report["blocking"] = roots.blocking
    report["unreadable_sidecars"] = [_rel(root, p) for p in side.unreadable()]
    report["unreadable_sidecars"] += [_rel(root, p) for p in side.unlisted]
    report["unarrived_objects"] = _unarrived(roots, side)
    counts = report["counts"]
    counts.update(placements=len(roots.placements), objects=len(side.ids), blobs=len(blobs))
    if roots.blocking:
        # Nothing is offered past a blocked walk, but what already waits in the
        # outbox left the store before it: a token naming no candidate lets a
        # collection drain it and then stop at the same walk.
        report["outbox"], report["foreign_outboxes"] = _outbox_listing(root)
        try:
            _check_root(root)
        except _RootChangedError:
            return ROOT_CHANGED
        _issue_token(root, report, device, at)
        return BLOCKED
    _classify(report, roots, side, blobs, GRACE_HOURS * 3600.0, at)
    counts.update(collectable=len(report["collectable"]),
                  collectable_blobs=len(report["collectable_blobs"]),
                  protected=len(report["protected"]))
    report["outbox"], report["foreign_outboxes"] = _outbox_listing(root)
    try:
        _check_root(root)
    except _RootChangedError:
        _clear_candidates(report)
        return ROOT_CHANGED
    _issue_token(root, report, device, at)
    return COMPLETE


def _clear_candidates(report: dict) -> None:
    report["collectable"], report["collectable_blobs"], report["protected"] = [], [], []
    report["reclaimable_bytes"] = 0


# ---- collect ------------------------------------------------------------------------

def collect(root: Path, token: str, *, cancel: Callable[[], bool],
            run_id: str | None = None, now: float | None = None) -> dict:
    """Dispose of what the dry run that issued `token` found collectable, and
    nothing else (module docstring): pictures to the recycle bin through this
    device's outbox, the rest deleted. Refused, with the token unspent, where
    `recycle_bin.check` finds no bin. The token is consumed even when the
    collection then stops; a blocked walk, a root change, a cancel or a bin
    that refuses leaves the rest for the next scan."""
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
    #: `_unarrived`, recomputed after each refresh under the lock.
    unarrived: list[str] = field(default_factory=list)
    #: Outbox files this batch moved, sent to the bin once the lock is released.
    pending: list[Path] = field(default_factory=list)
    #: Each moved file's object id (None for an orphan blob) and blob key.
    origin: dict[Path, tuple[str | None, str | None]] = field(default_factory=dict)

    def skip(self, image_id: str | None, blob: str | None, reason: str) -> None:
        self.report["skipped"].append({"id": image_id, "blob": blob, "reason": reason})


def _collect(root: Path, token: str, report: dict, cancel: Callable[[], bool],
             at: float) -> str:
    availability = recycle_bin.check(root)
    report["recycle_bin"] = availability.to_dict()
    try:
        _check_root(root)
    except _RootChangedError:
        return ROOT_CHANGED
    if not availability.ok:
        report["error"] = "no_recycle_bin"      # before the token: it stays good
        return REFUSED
    device = maintenance_reports.device_key(root)
    rec, why = _read_token(root, token, device, at)
    if rec is None or not _claim_token(root, token, rec):
        report["error"] = why or "unknown or already used"
        return REFUSED
    report["token"] = token
    report["grace_hours"] = rec["grace_hours"]
    try:
        if (stop := _drain(root, report, cancel)) is not None:
            return stop                 # refused or cancelled: nothing more leaves
        return _walk_and_collect(root, report, rec, cancel, at)
    except _RootChangedError:
        return ROOT_CHANGED
    except _UnsafePathError as exc:
        report["blocking"].append({"path": _rel(root, exc.path), "reason": "unsafe-path"})
        return BLOCKED


def _walk_and_collect(root: Path, report: dict, rec: dict, cancel: Callable[[], bool],
                      at: float) -> str:
    """A fresh strict walk, outside the lock, and then the token's candidates
    that it does not reach, in batches."""
    roots = _walk_roots(root, at, cancel)
    if roots is None:
        return CANCELLED
    side = _list_objects(root)
    for path, why in [*_store_blocks(root), *((p, image_refs.SYMLINK) for p in side.links)]:
        roots.block(root, path, why)
    if roots.blocking:
        report["blocking"] = roots.blocking
        return BLOCKED
    run = _Run(root, report, roots, side, rec["grace_hours"] * 3600.0, at)
    queue: list[tuple[str, str]] = []
    for image_id in rec["ids"]:
        if image_id in roots.reachable:
            run.skip(image_id, None, "reachable")
        else:
            queue.append(("object", image_id))
    queue += [("blob", key) for key in rec["blobs"]]
    return _batches(run, queue, cancel)


def _batches(run: _Run, queue: list[tuple[str, str]], cancel: Callable[[], bool]) -> str:
    """Collect in batches, each one hold of the ingest/GC lock (M15), and
    send each batch's moved pictures to the bin once that hold is released.
    A cancel or a root change sends nothing more -- the bin calls ask too,
    before each file: what moved and was not sent waits in the outbox for the
    next collection's drain."""
    while queue:
        if cancel():
            return CANCELLED
        with locks.image_ingest_gc_lock():
            _check_root(run.root)
            run.side = _list_objects(run.root, run.side)   # the refcount, under the lock
            if run.side.links:
                raise _UnsafePathError(run.side.links[0])
            run.unarrived = _unarrived(run.roots, run.side)
            run.report["unarrived_objects"] = run.unarrived
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
        if (stop := _flush(run, cancel)) is not None:
            return stop
    return _flush(run, cancel) or COMPLETE


def _flush(run: _Run, cancel: Callable[[], bool]) -> str | None:
    """Send the pictures the last batch moved to the bin (`_send`)."""
    if not run.pending:
        return None
    pending, run.pending = run.pending, []
    return _send(run.root, run.report, pending, run.origin, cancel)


def _young(run: _Run, mtimes: list[float]) -> str | None:
    if any(m > run.now for m in mtimes):
        return "clock-skew"
    if any(run.now - m < run.grace for m in mtimes):
        return "grace"
    return None


def _collect_object(run: _Run, image_id: str) -> None:
    """One object, under its stripe: re-check; skip it whole when its blob is
    held; delete only its sidecar when another sidecar names its blob; else
    move the blob to the outbox, THEN delete the sidecar (the move is the
    commit), and best effort its index entry and thumbnails. Each step is in
    the report the moment it has happened, so a run stopped between two of
    them (a root change) never under-reports."""
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
        if (hold := _orphan_hold(run.side, run.unarrived)) is not None:
            # Deleting the sidecar would lose its description and bin nothing:
            # the blob may be the one an unarrived or unreadable object names.
            run.skip(image_id, key, hold)
            return
        if run.side.refs.get(key, set()) - {path}:
            _delete_sidecar(run, path, image_id, key)
            _released(run, image_id, key, "shared")
        else:
            raw = obj.raw
            text = None if "description_conflicts" in raw else raw.get("description")
            name = bin_name(text if isinstance(text, str) else None, obj.blob_sha256, obj.ext)
            moved = _move_to_outbox(root, blob, name)
            if moved is not None:
                run.pending.append(moved)
                run.origin[moved] = (image_id, key)
            _delete_sidecar(run, path, image_id, key)
            if moved is None:
                _released(run, image_id, key, "blob-missing")
            _delete_thumbnails(run, obj.blob_sha256)
        _drop_index_entry(run, obj.blob_sha256, image_id)


def _delete_sidecar(run: _Run, path: Path, image_id: str, key: str) -> None:
    freed = _delete_file(run.root, path)
    run.side.drop(path)
    run.report["deleted"]["objects"].append({"id": image_id, "blob": key, "bytes": freed})
    run.report["deleted"]["bytes"] += freed


def _released(run: _Run, image_id: str, key: str, reason: str) -> None:
    run.report["released"].append({"id": image_id, "blob": key, "reason": reason})


def _delete_thumbnails(run: _Run, sha: str) -> None:
    """A blob's thumbnails, once the blob has left the store -- or was gone
    already, since nothing else will ask for them."""
    for thumb in blob_thumbnails(run.root, sha):
        _cache_delete(run, thumb)


def _cache_delete(run: _Run, path: Path) -> None:
    """Delete one derived cache entry, best effort: a thumbnail or index entry
    that will not go is reported, never a reason to stop collecting. A root
    change still stops the run (`_delete_file` deletes nothing then)."""
    try:
        _delete_file(run.root, path)
    except (OSError, _UnsafePathError) as exc:
        run.report["cache_cleanup"].append({"path": _rel(run.root, path),
                                            "reason": type(exc).__name__})


def _drop_index_entry(run: _Run, sha: str, image_id: str) -> None:
    """The blob-index entry, only when it names the collected object."""
    path = image_store.index_path(sha, root=run.root)
    try:
        named = path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return
    if named == image_id:
        _cache_delete(run, path)


def _collect_blob(run: _Run, key: str) -> None:
    """One orphan blob, under the ingest/GC lock (an ingest publishing it
    holds the same lock until its sidecar is written): to the outbox, with no
    description, and its thumbnails deleted."""
    root = run.root
    _check_root(root)
    split = _split_key(key)
    if split is None:
        return
    sha, ext = split
    if (hold := _orphan_hold(run.side, run.unarrived)) is not None:
        run.skip(None, key, hold)
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
    moved = _move_to_outbox(root, blob, bin_name(None, sha, ext))
    if moved is None:
        run.skip(None, key, "gone")
    else:
        run.pending.append(moved)
        run.origin[moved] = (None, key)
    _delete_thumbnails(run, sha)

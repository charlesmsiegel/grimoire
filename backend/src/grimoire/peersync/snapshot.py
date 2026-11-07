"""The peer's library as a local stand-in (028): what the serving device sends, and the mirror built from it.

019 synchronizes two libraries that are both open locally. A transfer keeps that core unchanged:
the serving device sends its synchronized folders exactly as they are on disk, its two ``.sync``
files for this pair, the grants it gives the client's peer principal, and its own canonical scan.
The client rebuilds those folders as a scratch library (the *mirror*) and refuses to use it unless
the mirror scans exactly as the server's own scan does: the same record files with the same
bytes, the same blocked paths, blobs and cross-kind pairs. Everything 019 plans from is in that
scan, so a mirror that passes the check is the peer's library as 019 sees it.

Nothing here writes to either real library. The mirror is deleted after each pass.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from grimoire.security import Authorizer, Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, StoreError, etag
from grimoire.store.diskops import is_link
from grimoire.store.jsonfmt import MAX_FILE_BYTES
from grimoire.store.layout import name_key
from grimoire.store.media import MAX_BLOB_BYTES
from grimoire.store.replica import CanonicalScan
from grimoire.sync.bookkeeping import pair_files, read_document
from grimoire.sync.documents import DEVICE_FILE, keeps_name, pair_state, parse_device

from .errors import MirrorMismatch, TransportError
from .pairing import peer_principal
from .wire import Channel

#: The folders a snapshot carries: 019's synchronized roots and the kept versions beside them
#: (keep-both picks a kept-version slot by what ``.conflicts`` already holds).
ROOTS = ("sources", "campaigns", "assets", ".conflicts")
_BLOB_ROOT = "assets"
_KEPT_ROOT = ".conflicts"
_KEPT_FOLDER = re.compile(r"[0-9a-f]{16}")
_KEPT_FILE = re.compile(r".+~[0-9a-f]{12}(?:\.[1-9][0-9]{0,8})?")
_MAX_DEPTH = 64
#: A stand-in owner for the mirror's in-memory authority (never written anywhere).
_MIRROR_OWNER = "human:mirror"
_BLOCKED_TARGET = ".grimoire-blocked-entry"  # what a mirror link points at: nothing
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
#: The scan summary travels in pieces of at most this size (one frame holds 36 MiB of body).
_SUMMARY_CHUNK = 16 * 1024 * 1024
#: The most scan summary a client keeps in memory (about 1.5 million records' worth).
MAX_SUMMARY_BYTES = 256 * 1024 * 1024
#: Characters no synchronized path holds, and which a client must never take as a path part.
_UNSAFE = (":", "\\", "\0")


@dataclass(frozen=True)
class Limits:
    """How much one snapshot may hold; a larger one is refused rather than half-built."""

    entries: int = 250_000
    total_bytes: int = 8 * 1024 * 1024 * 1024


# -- serving side -----------------------------------------------------------------------------

def _in_roots(path: str) -> bool:
    """Whether 019 can plan from ``path`` here: under a synchronized root, and spelled with no
    character that never names a record (a Finder ``notes 10:30.txt`` that the scan reports as a
    look-alike, say), and no deeper than the walk goes (a record path has at most 32 parts). The
    walk never sends such a path, so neither end may compare it."""
    return (path.split("/", 1)[0] in ROOTS and path.count("/") <= _MAX_DEPTH
            and not any(char in path for char in _UNSAFE))


def scan_summary(store: LibraryStore) -> dict[str, object]:
    """What 019 plans from, as comparable data: record etags, blocked paths and why, blob sizes, id pairs.

    Only entries under the snapshot's roots count. 019 reports a blocked entry anywhere in the
    library (a dangling link beside ``sources``, say), but plans nothing from one outside its
    synchronized roots, and a snapshot never carries it: comparing it would refuse every transfer
    of a library that syncs fine locally.
    """
    found = store.scan()
    return {"files": {path: etag(data) for path, data in sorted(found.files.items()) if _in_roots(path)},
            "blocked": {path: reason for path, reason in sorted(found.blocked.items()) if _in_roots(path)},
            "blobs": dict(sorted((path, size) for path, size in found.blobs.items() if _in_roots(path))),
            "pairs": sorted(found.pairs)}


def _cap(parts: tuple[str, ...]) -> int:
    return MAX_BLOB_BYTES if parts[0] == _BLOB_ROOT else MAX_FILE_BYTES


def walk(root: Path, blocked: Collection[str] = frozenset()) -> Iterator[tuple[str, str, bytes]]:
    """Every entry under the synchronized roots: ``(kind, path, bytes)``, never following a link.

    ``kind`` is ``dir``, ``file`` or ``blocked``. An entry the walk cannot send as itself (a link,
    anything that is not a regular file or folder, a folder that cannot be listed or is nested too
    deep, a file that cannot be read or is larger than anything the store accepts there) is sent
    as ``blocked`` when the serving library's own scan reports it blocked (``blocked``), and left
    out otherwise. The mirror reproduces a blocked entry as a dangling link, which every scan
    reports as blocked whatever its name; a stray original or a pipe beside a source's records,
    which the serving scan simply skips because 019 plans nothing from it, would then make the
    two scans differ on every pass, while a local 019 sync of the same libraries converges.
    """
    try:
        present = set(os.listdir(root))
    except OSError:  # the serving scan fails the same way, so the snapshot never gets this far
        present = set()
    for name in ROOTS:
        if name in blocked:  # a link, or a look-alike (``Campaigns``) the scan reports in its place
            yield "blocked", name, b""
        elif name not in present:
            continue  # never ``lstat`` it: a case-insensitive disk would answer with the look-alike
        elif name == _KEPT_ROOT:
            yield from _walk_kept(root)
        else:
            yield from _walk(root, (name,), 0, blocked)


def _walk_kept(root: Path) -> Iterator[tuple[str, str, bytes]]:
    """Only the kept-version layout of ``.conflicts``: ``<16 hex>/<file>~<12 hex>[.N]``.

    ``.conflicts`` also holds recovery leftovers: a discarded journal's staged files (which can be
    a pairing record with its key), ledger batches moved aside by a repair. 019 reads none of
    them; it reads the kept-version slots, to pick the next free one. So only those are sent, with
    every name the store would take for one (it compares names as a case-insensitive filesystem
    might, so ``.conflicts/<16 HEX>`` holds the slot ``<16 hex>`` too). What 019's slot reads find
    is sent as a stand-in that they find the same way: a slot that is not a file it can send goes
    as an empty file, whose bytes never match a version being kept, and a link or other entry in
    place of ``.conflicts`` or of a slot folder goes as a blocked entry or an empty file, so the
    mirror refuses a keep there exactly as this library will.
    """
    kind = _entry(root / _KEPT_ROOT)
    if kind != "dir":
        if kind is not None:
            yield _stand_in(kind, _KEPT_ROOT)
        return
    try:
        folders = sorted(os.listdir(root / _KEPT_ROOT))
    except OSError:
        return
    yield "dir", _KEPT_ROOT, b""
    for folder in folders:
        if not _is_kept(_KEPT_FOLDER, folder):
            continue
        relative = f"{_KEPT_ROOT}/{folder}"
        kind = _entry(root / _KEPT_ROOT / folder)
        if kind != "dir":
            if kind is not None:
                yield _stand_in(kind, relative)
            continue
        try:
            names = sorted(os.listdir(root / _KEPT_ROOT / folder))
        except OSError:
            continue
        yield "dir", relative, b""
        for name in names:
            if not _is_kept(_KEPT_FILE, name):
                continue
            slot = f"{relative}/{name}"
            kind = _entry(root / _KEPT_ROOT / folder / name)
            found = list(_walk(root, (_KEPT_ROOT, folder, name), 2, frozenset())) if kind == "file" else []
            if kind is not None:  # a slot is taken whatever stands in it: an empty file says so
                yield found[0] if found else ("file", slot, b"")


def _is_kept(layout: re.Pattern[str], name: str) -> bool:
    return bool(layout.fullmatch(name_key(name))) and not any(char in name for char in _UNSAFE)


def _entry(path: Path) -> str | None:
    """``dir``, ``file``, ``link`` (a symlink or junction) or ``other``; ``None`` if nothing is there."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        return "other"
    if stat.S_ISLNK(info.st_mode) or _REPARSE & (getattr(info, "st_file_attributes", 0) or 0):
        return "link"
    return "dir" if stat.S_ISDIR(info.st_mode) else "file" if stat.S_ISREG(info.st_mode) else "other"


def _stand_in(kind: str, relative: str) -> tuple[str, str, bytes]:
    """A link (which the store refuses to list through) as a link; anything else as an empty file."""
    return ("blocked", relative, b"") if kind == "link" else ("file", relative, b"")


def _unsendable(relative: str, blocked: Collection[str]) -> Iterator[tuple[str, str, bytes]]:
    if relative in blocked:
        yield "blocked", relative, b""


def _walk(root: Path, parts: tuple[str, ...], depth: int,
          blocked: Collection[str]) -> Iterator[tuple[str, str, bytes]]:
    path = root.joinpath(*parts)
    relative = "/".join(parts)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError:
        yield from _unsendable(relative, blocked)
        return
    if (stat.S_ISLNK(info.st_mode) or _REPARSE & (getattr(info, "st_file_attributes", 0) or 0)
            or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
        # A Windows junction lstats as a folder; only its reparse attribute says it leads
        # elsewhere (the store's own ``is_link`` test). Walking it would send files from outside
        # the library to the peer before any check could refuse the snapshot.
        yield from _unsendable(relative, blocked)
        return
    if stat.S_ISREG(info.st_mode):
        if info.st_size > _cap(parts):
            yield from _unsendable(relative, blocked)
            return
        try:  # never through a link put in its place since the lstat (O_NOFOLLOW where it exists)
            descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW | getattr(os, "O_BINARY", 0))
            with os.fdopen(descriptor, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise OSError("not a regular file")
                data = handle.read(_cap(parts) + 1)
        except OSError:
            yield from _unsendable(relative, blocked)
            return
        if len(data) > _cap(parts):  # it grew since the lstat
            yield from _unsendable(relative, blocked)
        else:
            yield "file", relative, data
        return
    if depth >= _MAX_DEPTH:
        yield from _unsendable(relative, blocked)
        return
    try:
        names = sorted(os.listdir(path))
    except OSError:
        yield from _unsendable(relative, blocked)
        return
    yield "dir", relative, b""
    for name in names:
        if any(char in name for char in _UNSAFE):
            # A Finder name like "notes 10:30.txt". No record, blob or kept version is ever
            # spelled so (IDs cannot hold these characters), so 019 plans nothing from it, and
            # the client could not tell it from a path that escapes its mirror: it stays here.
            continue
        yield from _walk(root, (*parts, name), depth + 1, blocked)


def send_snapshot(channel: Channel, store: LibraryStore, authority, peer: str) -> None:  # type: ignore[no-untyped-def]
    """Stream this library's snapshot for the pair (this device, ``peer``) to the authenticated peer."""
    principal = peer_principal(peer)
    channel.send({"t": "grants", "grants": [
        {"scope": str(grant.scope), "permissions": sorted(permission.value for permission in grant.permissions)}
        for grant in authority.active_grants() if grant.principal == principal]})
    # the pair's own files as 019 reads them (028): its archive and marker, each a root and the shards it
    # lists and, while those form a valid document, never another shard (a stale or planted file is not
    # read here, nor sent), and its
    # kept-version record. A read error stops the snapshot: unreadable is never absent.
    states: dict[str, bytes] = {}
    for name in (DEVICE_FILE, keeps_name(peer)):
        data = store.read_sync_state(name)
        if data is not None:
            states[name] = data
    device = parse_device(states.get(DEVICE_FILE))
    for kind in ("archive", "pending"):
        # what this device's own plan reads: with a root lost or damaged while shards stand, they are
        # sent as they stand, so the mirror's document is ignored with the same paths uncertain
        states.update(pair_files(store, kind, peer) if device is None  # the mirror refuses it anyway
                      else read_document(store, kind, peer, tuple(sorted((device, peer)))).files)  # type: ignore[arg-type]
    for name, data in states.items():
        channel.send({"t": "state", "name": name}, data)
    summary = scan_summary(store)  # before the walk: a file changed after it fails the client's check
    count = 0
    for kind, path, data in walk(store.root, frozenset(summary["blocked"])):  # type: ignore[arg-type]
        channel.send({"t": kind, "path": path}, data)
        count += 1
    encoded = json.dumps(summary, sort_keys=True).encode()  # a large library's runs past one frame
    for start in range(0, len(encoded), _SUMMARY_CHUNK):
        channel.send({"t": "scan"}, encoded[start:start + _SUMMARY_CHUNK])
    channel.send({"t": "end", "entries": count})


# -- client side ------------------------------------------------------------------------------

class MirrorStore(LibraryStore):
    """The mirror library, whose scan gives each blocked entry the serving device's own reason.

    The mirror reproduces every blocked entry as a dangling link, so its own scan says ``link``
    for an oversized record or a look-alike folder alike, and 019 decides some things by the
    reason (a look-alike is a name collision; a device's own unreadable file is never kept). Once
    the mirror's blocked paths are checked to be the server's, each one still blocked as a link
    reads with the reason the server's scan gave it.
    """

    peer_reasons: Mapping[str, str] = {}
    #: asks the serving device itself (``replay.ask_absent``), set once the session can
    peer_check: Callable[[list[str]], None] | None = None

    def confirm_absent(self, paths: list[str]) -> None:
        """019 checks, under both libraries' locks, that the device a deletion came from still lacks
        each path (028). This mirror is a snapshot of that device, so the device answers instead."""
        if self.peer_check is None:
            raise StoreError("this mirror has no connection to confirm a deletion with")
        self.peer_check(paths)

    def scan(self) -> CanonicalScan:
        found = super().scan()
        blocked = {path: self.peer_reasons.get(path, reason) if reason == "link" else reason
                   for path, reason in found.blocked.items()}
        return replace(found, blocked=MappingProxyType(blocked))


def _comparable(summary: Mapping[str, object]) -> dict[str, object]:
    """A summary with its blocked reasons left out: the mirror cannot reproduce them on disk."""
    blocked = summary.get("blocked")
    return {**summary, "blocked": sorted(blocked) if isinstance(blocked, Mapping) else blocked}


@dataclass
class Mirror:
    """The peer's library rebuilt locally, its scan checked, and a handle that writes as the peer would."""

    store: LibraryStore
    device: str
    handle: object


def _parts(path: object) -> tuple[str, ...]:
    if type(path) is not str:
        raise TransportError("a snapshot path is not text")
    parts = tuple(path.split("/"))
    if parts[0] not in ROOTS or len(parts) > _MAX_DEPTH + 1 or any(
            not part or part in (".", "..") or any(char in part for char in _UNSAFE) for part in parts):
        raise TransportError(f"a snapshot path is outside the synchronized roots: {path!r}")
    if parts[0] == _KEPT_ROOT and not (
            len(parts) == 1 or (_is_kept(_KEPT_FOLDER, parts[1]) and (len(parts) == 2 or (
                len(parts) == 3 and _is_kept(_KEPT_FILE, parts[2]))))):
        raise TransportError(f"a snapshot path is not a kept version: {path!r}")
    if os.name == "nt" and any(part.strip(".") == "" or part[-1] in ". " for part in parts):
        # Windows drops trailing dots and spaces from a name, so "..." or "x." would name another
        # entry (or a folder above) here: a name this filesystem cannot hold as itself.
        raise MirrorMismatch(f"{path} cannot be held as itself on this filesystem")
    return parts


def _target(root: Path, parts: tuple[str, ...]) -> Path:
    for depth in range(1, len(parts)):  # every folder above is one this snapshot made, never a link
        above = root.joinpath(*parts[:depth])
        if is_link(above) or not os.path.isdir(above):
            raise MirrorMismatch(f"{'/'.join(parts)} arrived outside a folder the snapshot listed")
    target = root.joinpath(*parts)
    parent = os.path.realpath(target.parent)
    if os.path.commonpath([parent, os.path.realpath(root)]) != os.path.realpath(root):
        raise MirrorMismatch(f"{'/'.join(parts)} would land outside the mirror on this filesystem")
    return target


def _block(path: Path) -> None:
    """Reproduce a blocked entry as a dangling link, which every scan reports as blocked."""
    try:
        os.symlink(_BLOCKED_TARGET, path)
    except (OSError, NotImplementedError) as error:  # no links here (Windows without the privilege)
        raise MirrorMismatch(f"cannot reproduce the peer's blocked entry {path.name}: {error}") from error


def _grant_handle(grants: object, principal: str):  # type: ignore[no-untyped-def]
    if not isinstance(grants, list):
        raise TransportError("the peer's grants are malformed")
    kernel = Authorizer([_MIRROR_OWNER])
    for number, grant in enumerate(grants):
        try:
            permissions = frozenset(Permission(value) for value in grant["permissions"])
            kernel.add_grant(Grant(f"peer-{number}", principal, permissions, ResourcePath.parse(grant["scope"]),
                                   _MIRROR_OWNER))
        except (KeyError, TypeError, ValueError) as error:
            raise TransportError(f"the peer's grants are malformed: {error}") from None
    return kernel.handle_for(principal, trusted=True)


def _materialize(root: Path, kind: str, parts: tuple[str, ...], body: bytes) -> None:
    """Create one snapshot entry in the mirror; ``MirrorMismatch`` if this filesystem cannot."""
    target = _target(root, parts)
    if os.path.lexists(target):  # listed twice, or two names one case-insensitive filesystem merges
        raise MirrorMismatch(f"{'/'.join(parts)} cannot be held apart from another entry here")
    try:
        if kind == "dir":
            os.mkdir(target)
        elif kind == "file":
            with open(target, "xb") as handle:
                handle.write(body)
        else:
            _block(target)
    except OSError as error:  # a reserved name, a full disk, a path too long for this system
        raise MirrorMismatch(f"cannot reproduce {'/'.join(parts)} here: {error}") from error


def receive_mirror(channel: Channel, root: Path, local: str, limits: Limits = Limits(), *,
                   store_type: type[MirrorStore] = MirrorStore) -> Mirror:
    """Read one snapshot from ``channel`` into a new library at ``root`` and check its scan.

    A snapshot is always read to its ``end``, even once the mirror is known to be unusable (an
    entry this filesystem cannot hold, a path that resolves elsewhere): the next pass asks for a
    new snapshot on the same connection, and leftover frames of this one would be read as the
    start of that. Only ``MirrorMismatch`` is raised after draining; a frame that breaks the
    protocol, or a snapshot over ``limits`` (which cannot be drained safely), ends the session
    with ``TransportError``. ``store_type`` opens the mirror (resolve's relays its commits to the peer).
    """
    peer = channel.peer
    LibraryStore.create(root).close()
    states: dict[str, bytes] = {}
    grants: object = None
    summary_parts: list[bytes] = []
    entries = total = summary_bytes = 0
    failure: MirrorMismatch | None = None
    while True:
        header, body = channel.recv()
        kind = header["t"]
        entries += 1
        total += len(body)
        if entries > limits.entries or total > limits.total_bytes:
            raise TransportError("the peer's snapshot is larger than this device accepts")
        if kind == "end":
            break
        if kind == "scan":
            summary_bytes += len(body)
            if summary_bytes > MAX_SUMMARY_BYTES:
                raise TransportError("the peer's scan summary is larger than this device accepts")
            summary_parts.append(body)
            continue
        if kind not in ("grants", "state", "dir", "file", "blocked"):
            raise TransportError(f"unexpected snapshot message {kind!r}")
        if failure is not None:
            continue  # draining to the end: nothing more is written
        try:
            if kind == "grants":
                grants = header.get("grants")
            elif kind == "state":
                name = header.get("name")
                if (name != DEVICE_FILE and not pair_state(name, local)) or name in states:
                    raise TransportError(f"the snapshot carries an unexpected sync file {name!r}")
                states[name] = body  # type: ignore[index]
            else:
                _materialize(root, kind, _parts(header.get("path")), body)
        except MirrorMismatch as error:
            failure = error
    if failure is not None:
        raise failure
    try:
        summary = json.loads(b"".join(summary_parts)) if summary_parts else None
    except (ValueError, RecursionError) as error:
        raise TransportError(f"the peer's scan is not usable JSON: {error}") from None
    if parse_device(states.get(DEVICE_FILE)) != peer:
        raise MirrorMismatch("the peer's snapshot does not carry its own device identity")
    sync_root = root / ".sync"
    try:
        for name, data in states.items():
            target = sync_root.joinpath(*name.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    except OSError as error:  # a full disk, say: this pass cannot use the mirror
        raise MirrorMismatch(f"cannot write the peer's sync state here: {error}") from error
    store = store_type.open(root)
    try:
        if not isinstance(summary, Mapping) or _comparable(scan_summary(store)) != _comparable(summary):
            raise MirrorMismatch("the rebuilt library does not scan as the peer's own scan")
        reasons = summary["blocked"]
        if not isinstance(reasons, Mapping) or not all(type(reason) is str for reason in reasons.values()):
            raise MirrorMismatch("the peer's scan gives no reason for a blocked entry")
        store.peer_reasons = dict(reasons)
        handle = _grant_handle(grants, peer_principal(local))
    except BaseException:
        store.close()
        raise
    return Mirror(store, peer, handle)

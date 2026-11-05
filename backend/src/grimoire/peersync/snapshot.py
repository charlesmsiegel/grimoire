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
import stat
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from grimoire.security import Authorizer, Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, etag
from grimoire.store.jsonfmt import MAX_FILE_BYTES
from grimoire.store.media import MAX_BLOB_BYTES
from grimoire.sync.documents import DEVICE_FILE, archive_name, parse_device, pending_name

from .errors import MirrorMismatch, TransportError
from .pairing import peer_principal
from .wire import Channel

#: The folders a snapshot carries: 019's synchronized roots and the kept versions beside them
#: (keep-both picks a kept-version slot by what ``.conflicts`` already holds).
ROOTS = ("sources", "campaigns", "assets", ".conflicts")
_BLOB_ROOT = "assets"
_MAX_DEPTH = 64
#: A stand-in owner for the mirror's in-memory authority (never written anywhere).
_MIRROR_OWNER = "human:mirror"
_BLOCKED_TARGET = ".grimoire-blocked-entry"  # what a mirror link points at: nothing
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
#: The scan summary travels in pieces of at most this size (one frame holds 36 MiB of body).
_SUMMARY_CHUNK = 16 * 1024 * 1024


@dataclass(frozen=True)
class Limits:
    """How much one snapshot may hold; a larger one is refused rather than half-built."""

    entries: int = 250_000
    total_bytes: int = 8 * 1024 * 1024 * 1024


# -- serving side -----------------------------------------------------------------------------

def _in_roots(path: str) -> bool:
    return path.split("/", 1)[0] in ROOTS


def scan_summary(store: LibraryStore) -> dict[str, object]:
    """What 019 plans from, as comparable data: record etags, blocked paths, blob sizes, id pairs.

    Only entries under the snapshot's roots count. 019 reports a blocked entry anywhere in the
    library (a dangling link beside ``sources``, say), but plans nothing from one outside its
    synchronized roots, and a snapshot never carries it: comparing it would refuse every transfer
    of a library that syncs fine locally.
    """
    found = store.scan()
    return {"files": {path: etag(data) for path, data in sorted(found.files.items()) if _in_roots(path)},
            "blocked": sorted(path for path in found.blocked if _in_roots(path)),
            "blobs": dict(sorted((path, size) for path, size in found.blobs.items() if _in_roots(path))),
            "pairs": sorted(found.pairs)}


def _cap(parts: tuple[str, ...]) -> int:
    return MAX_BLOB_BYTES if parts[0] == _BLOB_ROOT else MAX_FILE_BYTES


def walk(root: Path) -> Iterator[tuple[str, str, bytes]]:
    """Every entry under the synchronized roots: ``(kind, path, bytes)``, never following a link.

    ``kind`` is ``dir``, ``file`` or ``blocked``: a link, anything that is not a regular file or
    folder, a folder that cannot be listed, or a file that cannot be read or is larger than
    anything the store accepts there. A blocked entry is sent as one, never as absent.
    """
    for name in ROOTS:
        yield from _walk(root, (name,), 0)


def _walk(root: Path, parts: tuple[str, ...], depth: int) -> Iterator[tuple[str, str, bytes]]:
    path = root.joinpath(*parts)
    relative = "/".join(parts)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError:
        yield "blocked", relative, b""
        return
    if (stat.S_ISLNK(info.st_mode) or _REPARSE & (getattr(info, "st_file_attributes", 0) or 0)
            or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
        # A Windows junction lstats as a folder; only its reparse attribute says it leads
        # elsewhere (the store's own ``is_link`` test). Walking it would send files from outside
        # the library to the peer before any check could refuse the snapshot.
        yield "blocked", relative, b""
        return
    if stat.S_ISREG(info.st_mode):
        if info.st_size > _cap(parts):
            yield "blocked", relative, b""
            return
        try:  # never through a link put in its place since the lstat (O_NOFOLLOW where it exists)
            descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW | getattr(os, "O_BINARY", 0))
            with os.fdopen(descriptor, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise OSError("not a regular file")
                data = handle.read(_cap(parts) + 1)
        except OSError:
            yield "blocked", relative, b""
            return
        yield ("blocked", relative, b"") if len(data) > _cap(parts) else ("file", relative, data)
        return
    if depth >= _MAX_DEPTH:
        yield "blocked", relative, b""
        return
    try:
        names = sorted(os.listdir(path))
    except OSError:
        yield "blocked", relative, b""
        return
    yield "dir", relative, b""
    for name in names:
        yield from _walk(root, (*parts, name), depth + 1)


def send_snapshot(channel: Channel, store: LibraryStore, authority, peer: str) -> None:  # type: ignore[no-untyped-def]
    """Stream this library's snapshot for the pair (this device, ``peer``) to the authenticated peer."""
    principal = peer_principal(peer)
    channel.send({"t": "grants", "grants": [
        {"scope": str(grant.scope), "permissions": sorted(permission.value for permission in grant.permissions)}
        for grant in authority.active_grants() if grant.principal == principal]})
    for name in (DEVICE_FILE, archive_name(peer), pending_name(peer)):
        data = store.read_sync_state(name)  # a read error stops the snapshot: unreadable is never absent
        if data is not None:
            channel.send({"t": "state", "name": name}, data)
    summary = scan_summary(store)  # before the walk: a file changed after it fails the client's check
    count = 0
    for kind, path, data in walk(store.root):
        channel.send({"t": kind, "path": path}, data)
        count += 1
    encoded = json.dumps(summary, sort_keys=True).encode()  # a large library's runs past one frame
    for start in range(0, len(encoded), _SUMMARY_CHUNK):
        channel.send({"t": "scan"}, encoded[start:start + _SUMMARY_CHUNK])
    channel.send({"t": "end", "entries": count})


# -- client side ------------------------------------------------------------------------------

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
            not part or part in (".", "..") or "\\" in part or "\0" in part or ":" in part for part in parts):
        raise TransportError(f"a snapshot path is outside the synchronized roots: {path!r}")
    return parts


def _target(root: Path, parts: tuple[str, ...]) -> Path:
    for depth in range(1, len(parts)):  # every folder above is one this snapshot made, never a link
        above = root.joinpath(*parts[:depth])
        if os.path.islink(above) or not os.path.isdir(above):
            raise MirrorMismatch(f"{'/'.join(parts)} arrived outside a folder the snapshot listed")
    return root.joinpath(*parts)


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


def receive_mirror(channel: Channel, root: Path, local: str, limits: Limits = Limits()) -> Mirror:
    """Read one snapshot from ``channel`` into a new library at ``root`` and check its scan."""
    peer = channel.peer
    LibraryStore.create(root).close()
    states: dict[str, bytes] = {}
    grants: object = None
    summary_parts: list[bytes] = []
    entries = total = 0
    while True:
        header, body = channel.recv()
        kind = header["t"]
        entries += 1
        total += len(body)
        if entries > limits.entries or total > limits.total_bytes:
            raise MirrorMismatch("the peer's snapshot is larger than this device accepts")
        if kind == "end":
            break
        if kind == "grants":
            grants = header.get("grants")
        elif kind == "state":
            name = header.get("name")
            if name not in (DEVICE_FILE, archive_name(local), pending_name(local)) or name in states:
                raise TransportError(f"the snapshot carries an unexpected sync file {name!r}")
            states[name] = body  # type: ignore[index]
        elif kind == "scan":
            summary_parts.append(body)
        elif kind in ("dir", "file", "blocked"):
            parts = _parts(header.get("path"))
            target = _target(root, parts)
            if os.path.lexists(target):
                raise TransportError(f"the snapshot lists {'/'.join(parts)} twice")
            try:
                if kind == "dir":
                    os.mkdir(target)
                elif kind == "file":
                    with open(target, "xb") as handle:
                        handle.write(body)
                else:
                    _block(target)
            except MirrorMismatch:
                raise
            except OSError as error:  # a name this filesystem cannot hold (a look-alike, a reserved name)
                raise MirrorMismatch(f"cannot reproduce {'/'.join(parts)} here: {error}") from error
        else:
            raise TransportError(f"unexpected snapshot message {kind!r}")
    try:
        summary = json.loads(b"".join(summary_parts)) if summary_parts else None
    except (ValueError, RecursionError) as error:
        raise TransportError(f"the peer's scan is not usable JSON: {error}") from None
    if parse_device(states.get(DEVICE_FILE)) != peer:
        raise MirrorMismatch("the peer's snapshot does not carry its own device identity")
    sync_root = root / ".sync"
    for name, data in states.items():
        target = sync_root.joinpath(*name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    store = LibraryStore.open(root)
    try:
        if not isinstance(summary, Mapping) or scan_summary(store) != dict(summary):
            raise MirrorMismatch("the rebuilt library does not scan as the peer's own scan")
        handle = _grant_handle(grants, peer_principal(local))
    except BaseException:
        store.close()
        raise
    return Mirror(store, peer, handle)

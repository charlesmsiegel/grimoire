"""A library's device identity (019): ``device_of`` reads it, ``identify`` gives or replaces it.

Replacing a valid identity retires every pair's bookkeeping in the same commit (see ``identify``),
so this module also knows which ``.sync`` files a pair has: its archive and marker roots, the
shards each root lists (028; no other shard is ever read), and its kept-version record.
"""

from __future__ import annotations

from grimoire.store import LibraryStore, LinkRefused, NameCollision, UnstorableIdentifier, etag
from grimoire.store.syncstate import sync_peers

from . import bookkeeping
from .documents import (
    DEVICE_FILE, KeptEntry, check_device_id, is_retired, keeps_name, named_paths, parse_device, parse_keeps,
    render_device, render_keeps, render_retired,
)
from .errors import IdentityExists, NoDeviceIdentity, SyncInterrupted


def device_of(store: LibraryStore) -> str:
    try:
        data = store.read_sync_state(DEVICE_FILE)
    except (UnstorableIdentifier, LinkRefused) as error:  # not a file: no usable identity
        raise NoDeviceIdentity(f"{store.root}/.sync/{DEVICE_FILE} is not a readable file: {error}") from error
    except (OSError, NameCollision) as error:  # a look-alike spelling must never read as "no identity"
        raise SyncInterrupted(f"cannot read .sync/{DEVICE_FILE} on {store.root}: {error}") from error
    device = parse_device(data)
    if device is None:
        raise NoDeviceIdentity(f"{store.root} has no readable .sync/{DEVICE_FILE}; call identify first")
    return device


def identify(store: LibraryStore, device_id: str, *, replace: bool = False) -> str:
    """Give a library its device ID (an explicit human or app action; IDs are never generated here).

    A damaged identity is replaced. Changing a valid ID needs ``replace=True`` (the fix for a
    library copied with its ``.sync``). In the same commit every peer archive and marker of the old
    ID is *retired*: kept present, so the next sync of the new ID is conservative, but never valid
    again, so renaming the devices back cannot revive a stale base. If both devices take new IDs,
    their next sync is a true first sync. A kept-version record keeps only its ``resolve`` holds.
    """
    check_device_id(device_id)
    current = store.read_sync_state(DEVICE_FILE)  # a read error propagates as itself
    existing = parse_device(current)
    if existing == device_id:
        return device_id
    if existing is not None and not replace:
        raise IdentityExists(f"{store.root} is already device {existing!r}")
    txn = store.transaction()
    txn.put_sync_state(DEVICE_FILE, render_device(device_id), None if current is None else etag(current))
    if existing is not None:
        for peer in sync_peers(store):
            try:
                check_device_id(peer)
            except ValueError:  # not a device name: never read as state, nothing to retire
                continue
            for kind in ("archive", "pending"):
                _retire(txn, store, existing, kind, peer)
            data = store.read_sync_state(keeps_name(peer))
            if data is not None and not is_retired(data):
                txn.put_sync_state(keeps_name(peer), _keeps_after(data, existing, device_id, peer), etag(data))
    txn.commit(None)
    return device_id


def _keeps_after(data: bytes, existing: str, device_id: str, peer: str) -> bytes:
    """The kept-version record under the new ID: the old pair's deliveries and dismissals end with it,
    but this device's own versions that ``resolve`` held here are still its own, perhaps their only
    copy, so the new ID's record still lists them and they never become dismissable."""
    view = parse_keeps(data, tuple(sorted((existing, peer))))  # type: ignore[arg-type]
    held = {path: KeptEntry(None, frozenset(), held=entry.held) for path, entry in view.entries.items() if entry.held}
    if view.state != "valid" or not held or device_id == peer:
        return render_retired(existing)
    return render_keeps(tuple(sorted((device_id, peer))), held)  # type: ignore[arg-type]


def _retire(txn, store: LibraryStore, existing: str, kind: str, peer: str) -> None:  # type: ignore[no-untyped-def]
    """Retire a pair's archive or marker: its root and the shards a sync would read with it, each
    keeping what it named. Those are the shards it lists, or every shard on disk when the root and
    its listed shards are no valid document (a damaged root would otherwise retire naming nothing,
    leaving the paths its shards hold to be found only by listing the folder). Shards a valid root
    does not list are never read (stale or planted files), and stay unlisted."""
    root = store.read_sync_state(bookkeeping.root_name(kind, peer))
    if root is None or is_retired(root):
        return
    files = bookkeeping.read_document(store, kind, peer, tuple(sorted((existing, peer)))).files  # type: ignore[arg-type]
    shards = tuple(sorted(name for name in files if name != bookkeeping.root_name(kind, peer)))
    for name in shards:
        data = store.read_sync_state(name)
        if data is not None and not is_retired(data):
            txn.put_sync_state(name, render_retired(existing, named_paths(data)), etag(data))
    txn.put_sync_state(bookkeeping.root_name(kind, peer), render_retired(existing, named_paths(root), shards),
                       etag(root))

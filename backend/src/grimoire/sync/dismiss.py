"""Dismissing a kept version (028): the supported way to say "I have seen the other device's version".

Keep-both delivers the peer's version of a conflict under ``.conflicts/``, and 019 delivers it
again whenever its slot is empty, so deleting a kept file by hand never sticks. ``dismiss`` records
the decision instead. Like every 019 decision it is made from bytes, never from timestamps:

- it is keyed by the **path and the etag of the dismissed bytes**, in this device's own
  ``.sync/peers/<peer>/dismissed.json`` for the pair (``grimoire.sync.dismissed/1``, with the
  pair's devices and a ``check`` digest; an invalid one is ignored and reported, and then every
  kept version is delivered as before: the safe direction);
- a sync never keeps a dismissed version for that device again, and reports it in
  ``Conflict.dismissed``; a **different** version from the peer (other bytes) is kept as usual;
- it never touches the other device: the peer's file stays its canonical record, the conflict is
  still reported until a human ends it, and only this device's keep is withheld;
- a dismissal lasts as long as its conflict: once a completed sync finds the path no longer in
  conflict, it is dropped in that sync's last (pending) step, so the record stays small and the
  same bytes in a later conflict are kept again.

The record is written in one 010 transaction, authorized like a keep: ``APPLY`` on the kept
version's own address (``kept/<hash16>/<digest12>/<name>``), so a handle that may not write a
kept version may not dismiss one either, and an approval policy applies as it does to any write.
It is journaled as ``.sync`` state, so a crash leaves it either written or not. The kept files
themselves are not deleted (010 commits never delete); ``Dismissal.slots`` lists the ones still
holding those bytes, which the app or the person may now delete and which no sync rewrites.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from grimoire.store import LibraryStore, NameCollision, StaleWrite, StoreError, WriteConflict, WriteDenied, etag
from grimoire.store.engine import MAX_CONFLICT_ATTEMPTS
from grimoire.store.jsonfmt import MAX_FILE_BYTES, is_etag
from grimoire.store.layout import conflict_parts, kept_address

from .bookkeeping import read_state
from .documents import check_device_id, dismissed_name, parse_dismissed, render_dismissed, syncable
from .errors import DismissRefused, SyncInterrupted
from .session import device_of


@dataclass(frozen=True)
class Dismissal:
    """What ``dismiss`` recorded: ``wrote`` is false when it was already dismissed. ``slots`` are the
    kept-version files on this device that still hold the dismissed bytes (safe to delete now)."""

    peer: str
    path: str
    etag: str
    slots: tuple[str, ...]
    wrote: bool


def dismiss(store: LibraryStore, peer: str, path: str, kept: str, handle: object,
            approvals: Mapping[str, str] | None = None) -> Dismissal:
    """Never keep the version of ``path`` whose etag is ``kept`` on this device again, from ``peer``.

    Refused (``DismissRefused``, nothing written): a ``peer`` that is not another device, a path
    that is not a synchronized record that can have kept versions (a blob never has one), an etag
    that is not one, this device's own current version of ``path``, a record that would exceed
    010's file cap, or a write the handle may not make. A dismissal record that is unreadable stops
    it (``SyncInterrupted``); one that is invalid is replaced, since none of it was honoured.
    """
    device = device_of(store)
    try:
        check_device_id(peer)
    except ValueError as error:
        raise DismissRefused(str(error)) from None
    if peer == device:
        raise DismissRefused(f"{peer} is this device; a kept version comes from another device")
    kind = syncable(path)
    if kind is None or kind == "blob":
        raise DismissRefused(f"{path} is not a synchronized record with kept versions")
    if not is_etag(kept):
        raise DismissRefused("kept must be the etag of the kept version's bytes")
    parts = tuple(path.split("/"))
    try:
        mine = store.read_exact(path)
    except (NameCollision, StoreError) as error:
        raise DismissRefused(f"{path} cannot be read here: {error}") from error
    if mine is not None and etag(mine) == kept:
        raise DismissRefused(f"{kept} is this device's own version of {path}, not a kept one")
    devices: tuple[str, str] = tuple(sorted((device, peer)))  # type: ignore[assignment]
    old = read_state(store, dismissed_name(peer))
    view = parse_dismissed(old, devices)
    slots = _slots(store, parts, kept)
    entries = dict(view.entries) if view.state == "valid" else {}
    if kept in entries.get(path, frozenset()):
        return Dismissal(peer, path, kept, slots, False)
    entries[path] = entries.get(path, frozenset()) | {kept}
    try:
        data = render_dismissed(devices, entries)
    except StoreError as error:  # larger than any reader accepts
        raise DismissRefused(f"the dismissal record would be too large: {error}") from error
    if len(data) > MAX_FILE_BYTES:
        raise DismissRefused("the dismissal record would exceed the file size limit")
    txn = store.transaction()
    txn.put_sync_state(dismissed_name(peer), data, None if old is None else etag(old),
                       authorize=kept_address(parts, kept))
    try:
        txn.commit(handle, dict(approvals or {}))
    except WriteDenied as error:
        raise DismissRefused(f"dismissing {path} was not authorized: {error}") from error
    except (StaleWrite, WriteConflict) as error:  # a sync rewrote the record meanwhile: nothing was written
        raise SyncInterrupted(f"the dismissal record changed while it was written; try again: {error}") from error
    return Dismissal(peer, path, kept, slots, True)


def _slots(store: LibraryStore, parts: tuple[str, ...], kept: str) -> tuple[str, ...]:
    """The kept-version files of ``parts`` on this device that hold exactly the dismissed bytes."""
    found = []
    for attempt in range(MAX_CONFLICT_ATTEMPTS):
        slot = "/".join(conflict_parts(parts, kept, attempt))
        try:
            data = store.read_exact(slot)
        except (NameCollision, StoreError, OSError):  # unreadable or a look-alike: not listed, never deleted here
            continue
        if data is not None and etag(data) == kept:
            found.append(slot)
    return tuple(found)

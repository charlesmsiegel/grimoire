"""Ending one conflict by choosing a version (019's ``resolve``, docs/SYNC.md "Ending a conflict").

The device that does not hold the chosen version receives it, keeps its replaced version under
``.conflicts`` in the same transaction (a hold: it may exist nowhere else), and marks the copy in
its pending marker; both archives then record the agreement. Moved out of ``session`` unchanged
in behaviour; it shares the plan's private helpers so that a resolve and a sync judge a record by
the same rules.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from typing import cast

from grimoire.store import LibraryStore, NameCollision, StaleWrite, StoreError, WriteConflict, WriteDenied, etag
from grimoire.store.layout import definition_parts, manifest_parts
from grimoire.store.replica import CanonicalScan, pinned_by

from . import bookkeeping, keeps as kept_record, session
from .art import art_needs, art_unmet
from .decide import archive_candidate
from .documents import ArchiveView, PendingView, syncable
from .errors import ResolveRefused, SameDevice, SyncInterrupted
from .model import ASSET_KINDS, HELD_BACK_KINDS
from .session import (
    _HELD, PER_PATH_ERRORS, Copy, _archived, _blob_etag, _campaigns_with_state, _current, _held_campaigns,
    _held_relation, _home_scope, _kind, _knowledge_campaign, _locked, _manifest_heads, _named_paths, _side,
    _source_of, _unknown_logs, device_of,
)


def _archive(store: LibraryStore, peer: str, pair: tuple[str, str]) -> ArchiveView:
    return cast(ArchiveView, bookkeeping.read_document(store, "archive", peer, pair))


def _pending(store: LibraryStore, peer: str, pair: tuple[str, str]) -> PendingView:
    return cast(PendingView, bookkeeping.read_document(store, "pending", peer, pair))


def resolve(left: LibraryStore, right: LibraryStore, path: str, keep: str, expected: Mapping[str, str | None],
            handles: Mapping[str, object], approvals: Mapping[str, Mapping[str, str]] | None = None) -> str:
    """End one conflict by choosing the version whose etag is ``keep``; returns the device that changed.

    The other device receives those bytes and keeps its replaced version under ``.conflicts``,
    in one transaction; both archives then record the agreement. See docs/SYNC.md for refusals.
    """
    if _kind(path) in HELD_BACK_KINDS:  # before anything else: these paths are never synced at all
        raise ResolveRefused(f"{path} is {_HELD} (021/023 commit several files as one unit); the conflict "
                             "ends when both devices hold identical bytes")
    kind = syncable(path)
    if kind is None:
        raise ResolveRefused(f"{path} is not a synchronized record")
    ids = device_of(left), device_of(right)
    if ids[0] == ids[1]:
        raise SameDevice(f"both libraries are device {ids[0]!r}")
    stores = dict(zip(ids, (left, right)))
    pair: tuple[str, str] = tuple(sorted(ids))  # type: ignore[assignment]
    scans = {device: stores[device].scan() for device in pair}
    if kind == "relation" and _held_relation(path, _held_campaigns(scans.values())):  # the scans decided from
        raise ResolveRefused(f"{path} is {_HELD}: its campaign has accepted events (021), and a relation they "
                             "changed must never arrive without them; the conflict ends when both devices hold "
                             "identical bytes")
    sides = {device: _side(scans[device], path) for device in pair}
    for device, side in sides.items():
        if side.state == "blocked":
            raise ResolveRefused(f"{path} is {side.reason} on {device}")
        if set(expected) != set(pair) or _current(side) != expected[device]:
            raise ResolveRefused(f"{path} changed on {device} since the conflict was reported")
    other = {pair[0]: pair[1], pair[1]: pair[0]}
    pendings, archived = {}, []
    for device in pair:  # a rebuilt archive or marker would forget why every other path is uncertain
        archive = _archive(stores[device], other[device], pair)
        archived.append(archive)
        pendings[device] = _pending(stores[device], other[device], pair)
        for what, state in (("archive", archive.state), ("pending marker", pendings[device].state)):
            if state == "ignored" or (what == "archive" and state != "valid"):
                raise ResolveRefused(f"the {what} on {device} for this pair is {state}; sync the devices "
                                     "once first, so their uncertainty is recorded")
    # An absent side's current etag is None, so keep=None would otherwise "match" the device that
    # lacks the record and crown it the winner; choosing a deletion is refused (docs/SYNC.md).
    winners = [device for device in pair if keep is not None and _current(sides[device]) == keep]
    if not winners:
        raise ResolveRefused("keep must be the current etag of one device's version (deletions cannot be chosen)")
    if len(winners) == 2:
        raise ResolveRefused(f"{path} is already identical on both devices")
    winner = winners[0]
    receiver = pair[1] if winner == pair[0] else pair[0]
    txn = stores[receiver].transaction()
    resolved = [path]
    data: bytes | None = None
    mine: bytes | None = None
    if kind == "blob":
        if sides[receiver].state != "absent":  # unreachable by etag choice; kept as a guard
            raise ResolveRefused(f"{path} is a content-addressed blob {receiver} already holds; repair_blob "
                                 "fixes a corrupt one")
        size = scans[winner].blobs[path]  # type: ignore[attr-defined]
        _verified(stores[winner], path, winner)
        txn.put_blob_exact(path, functools.partial(stores[winner].read_blob_exact, path, size), size=size)
    else:
        data = scans[winner].files[path]
        mine = scans[receiver].files.get(path)
    if kind == "knowledge":
        resolved = _knowledge_log_copies(scans, winner, receiver, path)  # the whole log unit, create-only
        for done in resolved:
            txn.put_exact(done, scans[winner].files[done], None, regenerable=True)
    elif kind == "definition":
        # A manifest byte-identical on both devices names the same revision id on both: once the
        # receiver holds the winner's bytes, that reference means the same thing everywhere.
        manifest = "/".join(manifest_parts(_source_of(path)))  # type: ignore[arg-type]
        same = scans[receiver].files.get(manifest)
        allowed = frozenset({etag(same)}) if same is not None and same == scans[winner].files.get(manifest) else frozenset()
        txn.put_exact(path, data, None if mine is None else etag(mine), replace_unreferenced=mine is not None,  # type: ignore[arg-type]
                      regenerable=True, allowed_manifests=allowed)
    elif kind == "image":
        if mine is not None:
            raise ResolveRefused(f"{path} is a create-only image object both devices hold differently: a "
                                 "different picture is a new image object")
        txn.put_exact(path, data, None, regenerable=True)  # type: ignore[arg-type]
    elif kind != "blob":
        txn.put_exact(path, data, None if mine is None else etag(mine), regenerable=True)  # type: ignore[arg-type]
    if kind in ("image", "placement"):  # sync's dependency rule for art, on the receiver
        _resolve_art(stores, scans, winner, receiver, path, data)  # type: ignore[arg-type]
    if kind == "manifest":
        for head in _manifest_heads(Copy(path, winner, receiver, data, None)):
            theirs, ours = scans[winner].files.get(head), scans[receiver].files.get(head)
            if theirs is None:
                raise ResolveRefused(f"{head} is not readable on {winner}")
            if ours is None:
                txn.put_exact(head, theirs, None, regenerable=True)
                resolved.append(head)
            elif ours != theirs:
                raise ResolveRefused(f"{head} differs between the devices; resolve it first")
    for done in resolved:  # every record resolve writes, head revisions included: sync's dependency rule
        if syncable(done) in ASSET_KINDS:
            continue  # art never pins a revision or names an actor (``_resolve_art`` checked its needs)
        written = scans[winner].files[done]
        for pinned in sorted({"/".join(definition_parts(ref)) for ref in pinned_by(tuple(done.split("/")), written)}
                             - set(resolved)):
            if scans[winner].files.get(pinned) != scans[receiver].files.get(pinned) or any(
                    scans[device].blocked_reason(pinned) is not None for device in pair):
                raise ResolveRefused(f"{pinned}, which {done} pins, differs between the devices; resolve it first")
        for named in [*_named_paths(tuple(done.split("/")), written), *_home_scope(done)]:
            if (named in scans[winner].files and named not in scans[receiver].files) or any(
                    scans[device].blocked_reason(named) is not None for device in pair):
                raise ResolveRefused(f"{named}, which {done} names, is missing on {receiver}; sync or resolve it first")
        campaign = _knowledge_campaign(done)
        if campaign is not None and campaign not in _campaigns_with_state(scans[receiver].files) and (
                campaign in _campaigns_with_state(scans[winner].files) or any(
                    scans[device].blocked_reason(f"campaigns/{campaign}/scope.json") is not None for device in pair)):
            raise ResolveRefused(f"campaign {campaign}, which {done} belongs to, is missing on {receiver}; sync it first")
    if mine is not None:
        txn.keep_version(path, mine)
        # 028: the hold is recorded in the same commit, so neither a peer's sync nor ``dismiss`` can ever
        # take this device's own version (perhaps its only copy) for the peer's delivered one
        held = [kept_record.with_held(kept_record.read_keeps(stores[receiver], other[receiver], pair),
                                      other[receiver], pair, path, etag(mine))]
        for peer in kept_record.other_peers(stores[receiver], receiver):  # the hold may reuse another pair's slot
            if peer != other[receiver]:
                devices: tuple[str, str] = tuple(sorted((receiver, peer)))  # type: ignore[assignment]
                held.append(kept_record.not_delivered(kept_record.read_keeps(stores[receiver], peer, devices),
                                                      peer, devices, path, etag(mine)))
        bookkeeping.stage(txn, [state for state in held if state is not None])
    # Like a sync's copy, the resolve's copy is marked in the same commit: if the archives are not
    # written after it (a crash, a held file), the next sync still knows the peer held these bytes,
    # and a revert on either device is a conflict rather than an "unchanged" side.
    tags = {done: _blob_etag(done) if syncable(done) == "blob" else etag(scans[winner].files[done])
            for done in resolved}
    marked = dict(pendings[receiver].entries)
    for done in resolved:
        ours = scans[receiver].files.get(done)
        marked[done] = {"from": None if ours is None else etag(ours), "to": tags[done],
                        "base": _archived(archive_candidate(done, *archived))}
    files, _ = bookkeeping.render_files("pending", other[receiver], pair, marked, session.ARCHIVE_LIMIT)
    bookkeeping.stage(txn, bookkeeping.changes(stores[receiver], files, pendings[receiver].files))
    try:
        txn.check()
    except (StaleWrite, *PER_PATH_ERRORS) as error:
        raise ResolveRefused(f"{path} cannot be resolved on {receiver}: {error}") from error
    try:
        with _locked(*stores.values()):  # a blob is loaded from the winner inside this commit
            txn.commit(handles[receiver], dict((approvals or {}).get(receiver, {})))
    except WriteDenied as error:  # nothing was written: the grant refuses it, like any other refusal
        raise ResolveRefused(f"{path} cannot be resolved on {receiver}: {error}") from error
    try:
        found = stores[receiver].read_exact(path)
    except NameCollision:  # renamed to a look-alike since it was written
        found = None
    if found is None or etag(found) != keep:
        raise SyncInterrupted(f"verification failed on {receiver}: {path} does not hold the chosen bytes")
    for device in reversed(pair):
        store = stores[device]
        archive = _archive(store, other[device], pair)
        if archive.state != "valid":  # damaged since the check: never rebuild it from nothing
            raise SyncInterrupted(f"the archive on {device} changed during resolve")
        entries = dict(archive.entries)
        for done in resolved:
            entries[done] = ("agreed", tags[done])
        files, _ = bookkeeping.render_files("archive", other[device], pair, entries, session.ARCHIVE_LIMIT)
        states = bookkeeping.changes(store, files, archive.files)
        # the conflicts this resolve ended take their kept-version entries with them (028): a dismissal
        # never outlives its conflict, even before the next sync completes
        record = kept_record.without(kept_record.read_keeps(store, other[device], pair), other[device], pair, resolved)
        if record is not None:
            states.append(record)
        if states:
            writes = store.transaction()
            bookkeeping.stage(writes, states)
            _commit_state(writes, device)
    for device in reversed(pair):
        store = stores[device]
        pending = _pending(store, other[device], pair)
        if pending.state != "valid" or not set(resolved) & set(pending.entries):
            continue
        files, _ = bookkeeping.render_files("pending", other[device], pair, {
            key: value for key, value in pending.entries.items() if key not in resolved}, session.ARCHIVE_LIMIT)
        writes = store.transaction()
        bookkeeping.stage(writes, bookkeeping.changes(store, files, pending.files))
        _commit_state(writes, device)
    return receiver


def _verified(store: LibraryStore, path: str, device: str) -> str:
    """A blob's etag after hashing it; ``ResolveRefused`` if it is corrupt or unreadable."""
    try:
        return store.verify_blob(path)
    except (StoreError, OSError, ValueError) as error:
        raise ResolveRefused(f"{path} is not a readable, intact blob on {device}: {error}") from error


def _resolve_art(stores: Mapping[str, LibraryStore], scans: Mapping[str, CanonicalScan], winner: str, receiver: str,
                 path: str, data: bytes) -> None:
    """``resolve`` delivers an image object or placement only where sync would copy it (``art_needs``):
    every file it needs identical on both devices and blocked on neither (blobs hashed on the
    receiver), every owner or subject it needs present on the receiver, and every source it belongs
    to or shows a picture of admitted by ``load()`` on both devices (each store's cached
    ``excluded_sources``, read now, as a sync's plan reads it)."""
    needs = art_needs(path, data, scans[winner].files, scans[winner].blobs,  # type: ignore[attr-defined]
                      stores[winner].excluded_sources())

    def held(where: str) -> bool:
        return any(scans[device].blocked_reason(where) is not None for device in scans)

    def tag(device: str, where: str) -> str | None:
        if where in scans[device].blobs:  # type: ignore[attr-defined]
            return _verified(stores[device], where, device) if device == receiver else _blob_etag(where)
        found = scans[device].files.get(where)
        return None if found is None else etag(found)
    here = _campaigns_with_state(scans[receiver].files)
    why = art_unmet(needs, held, lambda where: tag(receiver, where), lambda where: tag(winner, where), here, receiver,
                    stores[receiver].excluded_sources())
    if why:
        raise ResolveRefused(f"{path} cannot be resolved on {receiver} yet: {why}; sync or resolve those first")


def _knowledge_log_copies(scans: Mapping[str, CanonicalScan], winner: str, receiver: str, path: str) -> list[str]:
    """What resolving one knowledge path delivers: every knowledge record of its campaign that the
    winner holds and the receiver lacks (020 writes records and their batch as one unit), never a
    replacement. Two logs that diverged (a path both hold with different bytes) cannot be merged yet."""
    if scans[receiver].files.get(path) is not None:
        raise ResolveRefused(f"{path} is a create-only knowledge record both devices hold differently: two "
                             "devices' knowledge logs cannot be merged yet. If one copy was edited by hand, "
                             "delete that copy, sync, and resolve the resulting deleted conflict")
    campaign = _knowledge_campaign(path)
    unknown = _unknown_logs(scans.values())
    if campaign in unknown or "*" in unknown:
        raise ResolveRefused(f"the knowledge log of campaign {campaign} cannot be seen whole on both devices "
                             "(a folder is unreadable or spelled differently); fix it first")
    log = sorted({candidate for scan in scans.values() for candidate in (*scan.files, *scan.blocked)
                  if _knowledge_campaign(candidate) == campaign})
    copies = []
    for candidate in log:
        if any(scan.blocked_reason(candidate) is not None for scan in scans.values()):
            raise ResolveRefused(f"{candidate}, in the same knowledge log, is blocked; fix it first")
        theirs, ours = scans[winner].files.get(candidate), scans[receiver].files.get(candidate)
        if theirs is not None and ours is not None and theirs != ours:
            raise ResolveRefused(f"{candidate} differs between the devices: their knowledge logs diverged and "
                                 "cannot be merged yet")
        if theirs is not None and ours is None:
            copies.append(candidate)
    return copies


def _commit_state(txn, device: str) -> None:  # type: ignore[no-untyped-def]
    """A trusted ``.sync`` commit after resolve's data landed; a race is ``SyncInterrupted`` (the next
    sync sees equal bytes and records the agreement)."""
    try:
        txn.commit(None)
    except (StaleWrite, WriteConflict) as error:
        raise SyncInterrupted(f"the sync state on {device} changed during resolve: {error}") from error


__all__ = ["resolve"]

"""Ending one conflict over the peer connection (028): 019's ``resolve`` with one library on the other device.

A transfer reports a keep-both conflict; ``resolve_conflict`` ends it from either device without
opening both libraries in one process. As a transfer does for a sync, it keeps 019's core
unchanged: the serving device sends a snapshot (asked for with a ``resolve`` request that names
the path and the etags the user saw), the client rebuilds it as a checked mirror, and runs
019's own ``resolve`` between its library and the mirror. Every commit ``resolve`` makes on the
mirror is replayed on the serving device before ``resolve`` goes on, as one transaction there
under the client's peer principal, re-read and confirmed. So the real devices are written in
``resolve``'s own order (the receiver's hold record, its data with its kept version and pending
marker, verify, archives, pending markers) and a failure at any point leaves what a crash at that
point of a local resolve leaves (docs/DEVICE_SYNC.md).

The serving device does not take the requester's word for any of it (``ResolveRequest``): it
admits only a choice of a version it already holds, keeps beside the path or agreed on, checks
its own expected etag when the request arrives and before every step, accepts at the path only
the chosen bytes, with its own version held, and never replaces a definition revision for a peer. While a resolve of its own library is in flight it
refuses what could wait on that resolve (``busy``), so two devices resolving against each other
never wait on each other's library until the connection times out.

Whichever device receives records its replaced version as ``held`` in its kept-version records in
a commit of its own just before the commit that writes the hold (028), exactly as a local
``resolve`` does, so ``dismiss`` never takes it for a delivered keep, even after 010's recovery
finished the hold's commit without a staged ``.sync`` file: the client in its own library; the
serving device in the step ``resolve`` relays for that record (this pair's record alone, adding
no hold but its own current version: ``check_step``), and in a commit of its own for its other
pairs' records, which the peer never sees, just before the hold's step; the hold's step itself
is refused unless the pair's record already holds it (``peersync.replay``).

Choosing the deletion (``keep=None``) is started only on the device that holds the record: that
device receives (it holds its version, deletes the record and marks it, in its own library), and
the serving device is the winner, written only bookkeeping, as for any other choice of the
serving device's version. The serving device refuses a deletion it would have to make itself.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from grimoire.security import ResourcePath
from grimoire.store import LibraryStore, RecordNotFound, StoreError, Transaction, etag
from grimoire.store.engine import MAX_CONFLICT_ATTEMPTS
from grimoire.store.jsonfmt import is_etag
from grimoire.sync import ResolveRefused, SyncError, SyncInterrupted, device_of, resolve, syncable
from grimoire.sync.bookkeeping import read_document
from grimoire.sync.documents import keeps_name
from grimoire.sync.keeps import kept_slots

from .busy import outgoing
from .errors import MirrorMismatch, PeerRefused, TransportError
from .replay import Op, absent_here, ask_absent, kept_target, send_ops
from .session import _check_paired, dial, hang_up, pairing_for
from .snapshot import Limits, MirrorStore, receive_mirror
from .wire import Channel, client_handshake


@dataclass(frozen=True)
class ResolveReport:
    """What one resolve over the transport did.

    ``status`` is ``resolved`` (``receiver`` now holds the chosen version, its own kept under
    ``.conflicts``, and both archives record the agreement), ``refused`` (nothing was written on
    either device but the kept-version bookkeeping a local resolve also commits before its data:
    the ended conflict's deliveries and dismissals cleared, and the receiver's hold recorded,
    which only fails closed; ``reason`` says why) or ``incomplete`` (the connection or pairing
    ended, or a step was refused after the chosen version landed: each device is as a crash at
    that point of a local resolve leaves it, and the next transfer converges).
    """

    peer: str
    path: str
    status: str
    reason: str | None = None
    receiver: str | None = None


def resolve_conflict(store: LibraryStore, authority, peer: str, address: tuple[str, int], path: str,  # type: ignore[no-untyped-def]
                     keep: str | None, expected: Mapping[str, str | None], *, timeout: float = 600.0,
                     workdir: str | Path | None = None, limits: Limits = Limits()) -> ResolveReport:
    """End the conflict at ``path`` with the paired device ``peer`` at ``address`` by keeping the
    version whose current etag is ``keep`` (``None``: the deletion, started on the device that holds
    the record); ``expected`` gives each device's etag (``None``: absent) as the conflict was
    reported, exactly as 019's ``resolve`` takes them.

    Raises ``NotPaired`` or ``AuthenticationFailed`` before anything is exchanged, as ``transfer``
    does; every other outcome is a report.
    """
    pairing, local_handle = pairing_for(store, authority, peer, address)
    local = device_of(store)
    why = _unusable(local, peer, path, keep, expected)
    if why is not None:
        return ResolveReport(peer, path, "refused", f"ResolveRefused: {why}")
    if (syncable(path) == "definition" and keep == expected[local] and expected[peer] is not None
            and keep != expected[peer]):
        # The peer would be the receiver of a revision replace. A serving device never lets a peer
        # replace an immutable revision (nothing it holds could prove the chosen bytes were ever
        # agreed: a plain sync step can put any bytes under its .conflicts), so the device holding
        # the conflicted revision starts the resolve and replaces it as the owner itself. Only a
        # choice of this device's own version is redirected; any other (a version neither holds, a
        # stale etag) goes on to be refused as usual (by the serving device's admission or by 019),
        # so following the advice never sends the user back here.
        return ResolveReport(peer, path, "refused", f"ResolveRefused: {path} is a definition revision {peer} "
                                                    f"would replace; start this resolve on {peer}")
    with outgoing(store):  # this library's own server now refuses what could wait on this resolve
        try:
            connection = dial(address, timeout)
        except OSError as error:
            return ResolveReport(peer, path, "incomplete", f"unreachable: {error}")
        channel: Channel | None = None
        try:
            channel = client_handshake(connection, local, peer, pairing.key)
            return _resolve(store, channel, local, local_handle, pairing.key, path, keep, expected, workdir, limits)
        except TransportError as error:  # whatever landed is whole, as after a crash between two commits
            return ResolveReport(peer, path, "incomplete", f"disconnected: {error}")
        finally:
            hang_up(channel, connection)


def _unusable(local: str, peer: str, path: object, keep: object, expected: object) -> str | None:
    """Why the request cannot be a resolve at all, before anything is sent.

    ``keep=None`` chooses the deletion (028). The device that holds the record deletes it, and a
    serving device never deletes a record for a peer, so it is started there.
    """
    if type(path) is not str or syncable(path) is None:
        return f"{path!r} is not a synchronized record"
    if keep is not None and not is_etag(keep):
        return "keep must be the current etag of one device's version, or None for the deletion"
    if not isinstance(expected, Mapping) or set(expected) != {local, peer} or not all(
            value is None or is_etag(value) for value in expected.values()):
        return f"expected must give the etag (or None) of {local} and {peer}"
    if keep is None and expected[local] is None and expected[peer] is not None:
        return f"{path} is held by {peer}, which deletes it: start this resolve on {peer}"
    return None


def _resolve(store: LibraryStore, channel: Channel, local: str, local_handle: object, key: bytes, path: str,
             keep: str, expected: Mapping[str, str | None], workdir: str | Path | None,
             limits: Limits) -> ResolveReport:
    peer = channel.peer
    try:
        _check_paired(store, peer, key)
        channel.send({"t": "resolve", "path": path, "keep": keep, "expected": dict(expected)})
    except PeerRefused as error:
        return ResolveReport(peer, path, "incomplete", error.code)
    # A file an indexer still holds open must not turn a finished resolve into a failure: the
    # scratch mirror is only ever deleted.
    with tempfile.TemporaryDirectory(prefix="grimoire-peer-", dir=workdir, ignore_cleanup_errors=True) as scratch:
        try:
            mirror = receive_mirror(channel, Path(scratch) / "peer", local, limits, store_type=RelayingMirror)
        except PeerRefused as error:  # the request itself: nothing was written anywhere
            if error.code == "not_paired":
                return ResolveReport(peer, path, "incomplete", "not_paired")
            if error.code == "protocol":  # a peer that predates resolve requests (it ended the session)
                return ResolveReport(peer, path, "refused", f"unsupported: {error}")
            return ResolveReport(peer, path, "refused", str(error))
        except MirrorMismatch as error:
            return ResolveReport(peer, path, "incomplete", f"MirrorMismatch: {error}")
        relay = _Relay(channel, store, key, keeps_name(local) if expected[local] == keep else None)
        mirror.store.relay = relay  # type: ignore[attr-defined]
        mirror.store.peer_check = relay.confirm_absent  # type: ignore[attr-defined]
        try:
            receiver = resolve(store, mirror.store, path, keep, expected, {local: local_handle, peer: mirror.handle})
            relay.flush()
        except ResolveRefused as error:  # 019 refused before its data commit (its bookkeeping may stand)
            return ResolveReport(peer, path, "refused", f"ResolveRefused: {error}")
        except PeerRefused as error:
            if error.code == "not_paired":
                return ResolveReport(peer, path, "incomplete", "not_paired")
            # ``refused`` promises that the chosen version landed on neither device: a refused step
            # up to and including the receiver's data leaves only the bookkeeping resolve commits
            # first (the ended conflict's deliveries cleared, the hold recorded), which fails closed,
            # as a local resolve refused at its data commit does. A refused later step left the data
            # landed and the agreement unrecorded, as a crash between them would: ``incomplete``.
            # When this device receives, the serving device's first step (its record's cleared
            # deliveries) comes before this device's own data commit.
            first = not relay.landed and error.code != "verification" and (
                expected[local] == keep or not _holds(store, path, keep))
            return ResolveReport(peer, path, "refused" if first else "incomplete", str(error))
        except (SyncInterrupted, MirrorMismatch) as error:  # the data may have landed; the next sync records it
            return ResolveReport(peer, path, "incomplete", f"{type(error).__name__}: {error}")
        except (SyncError, StoreError, OSError, ValueError) as error:
            return ResolveReport(peer, path, "incomplete", f"{type(error).__name__}: {error}")
        finally:
            mirror.store.close()
    return ResolveReport(peer, path, "resolved", None, receiver)


class _Relay:
    """Sends one mirror commit to the serving device and waits for it to commit and confirm it.

    ``hold_record`` is this pair's kept-version record on the mirror when the serving device
    receives. ``resolve`` commits that record on its own before its data commit, first with the
    ended conflict's deliveries cleared, then with the hold recorded (028), and the mirror then
    checks the data commit against the serving device's grants: a denied write is refused there,
    before anything is relayed. So those record steps are held back until the next step goes (the
    data's), then sent in order, each a commit of its own: a resolve the mirror refuses writes
    nothing on the serving device. The serving device still commits the hold's record before the
    data, so no recovery of that commit can lose it. Its own cleared deliveries need not come
    sooner: once it receives, it holds the winner's version live, so its kept copy of that version
    is never the last one. When this device receives, the serving device is the winner and its
    record is relayed at once, before this device's data commit: its kept copy of this device's
    version may become that version's last copy, and must never be dismissable by then.
    """

    def __init__(self, channel: Channel, store: LibraryStore, key: bytes, hold_record: str | None) -> None:
        self.channel, self.store, self.key, self.hold_record = channel, store, key, hold_record
        self.landed = False  # whether a step that wrote more than .sync state (the data) committed there
        self._held_back: list[list[tuple[str, Callable[[], Op]]]] = []

    def __call__(self, ops: list[tuple[str, Callable[[], Op]]]) -> None:
        if (self.hold_record is not None and not self.landed and ops
                and all(kind == "state" and make()[0].get("name") == self.hold_record for kind, make in ops)):
            self._held_back.append(ops)
            return
        self.flush()
        self._send(ops)
        self.landed = self.landed or any(kind != "state" for kind, _ in ops)

    def flush(self) -> None:
        """Send the steps held back, if any, in order."""
        held, self._held_back = self._held_back, []
        for ops in held:
            self._send(ops)

    def confirm_absent(self, paths: list[str]) -> None:
        """Have the serving device confirm, under its own lock, that it still lacks each path a
        deletion removes (028); a refusal (``stale``) ends the resolve before the deletion."""
        _check_paired(self.store, self.channel.peer, self.key)
        ask_absent(self.channel, paths)

    def _send(self, ops: list[tuple[str, Callable[[], Op]]]) -> None:
        # An owner who unpairs mid-resolve must not have anything more sent (as a transfer checks).
        _check_paired(self.store, self.channel.peer, self.key)
        send_ops(self.channel, "resolve", (make() for _, make in ops))


class RelayingMirror(MirrorStore):
    """The mirror for a resolve: every transaction it commits is replayed on the serving device."""

    relay: _Relay | None = None

    def transaction(self) -> Transaction:
        super().transaction()  # its checks: an open, writable library
        return _RelayedTransaction(self)


class _RelayedTransaction(Transaction):
    """A mirror transaction that records each write as it is staged and, once the mirror has
    committed it (every store check passed, authorized by the peer's own grants for this
    device), replays the same writes on the serving device.

    Only the exact writes 019's ``resolve`` stages are relayed; any other kind of write is
    refused, because the serving device takes nothing else from a peer.
    """

    def __init__(self, store: RelayingMirror) -> None:
        super().__init__(store)
        self._mirror = store
        self._ops: list[tuple[str, Callable[[], Op]]] = []

    def put(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise StoreError("a resolve over the transport relays only exact writes")

    def put_secret(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise StoreError("a resolve over the transport relays only exact writes")

    def put_exact(self, path: str, data: bytes, expected: str | None, *, replace_unreferenced: bool = False,
                  regenerable: bool = False, allowed_manifests: frozenset[str] = frozenset()) -> str:
        if replace_unreferenced:  # resolve_conflict refuses this before connecting; never relayed
            raise ResolveRefused(f"{path} is a definition revision the serving device would replace; "
                                 "start this resolve on that device")
        key = super().put_exact(path, data, expected, replace_unreferenced=replace_unreferenced,
                                regenerable=regenerable, allowed_manifests=allowed_manifests)
        header: dict[str, object] = {"op": "put", "path": path, "expected": expected}
        body = bytes(data)
        self._ops.append(("put", lambda: (header, body, path, etag(body))))
        return key

    def delete_exact(self, path: str, expected: str) -> str:
        # resolve_conflict lets only the device holding the record choose its deletion, so the
        # mirror (the serving device) is never the one that deletes; never relayed
        raise ResolveRefused(f"{path} would be deleted on the serving device; start this resolve on that device")

    def put_blob_exact(self, path: str, read: Callable[[], bytes | None], *, size: int) -> str:
        key = super().put_blob_exact(path, read, size=size)

        def op() -> Op:  # loaded when sent, from the mirror, which has just verified it
            data = self._mirror.read_blob_exact(path, size)
            if data is None:
                raise MirrorMismatch(f"the blob {path} vanished from the mirror before it could be sent")
            return {"op": "blob", "path": path, "size": size}, data, path, etag(data)
        self._ops.append(("blob", op))
        return key

    def keep_version(self, path: str, data: bytes, slot: int | None = None, *, regenerable: bool = False) -> str:
        target = super().keep_version(path, data, slot, regenerable=regenerable)
        # The slot the mirror chose, from the serving device's own kept versions: sent explicitly,
        # so a slot taken there since the snapshot is a stale step, never a second slot.
        chosen = next(number for number in range(MAX_CONFLICT_ATTEMPTS) if kept_target(path, data, number) == target)
        header: dict[str, object] = {"op": "keep", "path": path, "slot": chosen}
        if not regenerable:
            header["hold"] = True
        body = bytes(data)
        self._ops.append(("keep", lambda: (header, body, target, etag(body))))
        return target

    def put_sync_state(self, name: str, data: bytes, expected: str | None, *,
                       authorize: ResourcePath | None = None) -> str:
        if authorize is not None:  # a dismissal's authorized record: the user's own, never a peer's write
            raise StoreError("a resolve over the transport relays only its own bookkeeping")
        key = super().put_sync_state(name, data, expected)
        body = bytes(data)
        self._ops.append(("state", lambda: ({"op": "state", "name": name, "expected": expected}, body,
                                            f".sync/{name}", etag(body))))
        return key

    def commit(self, handle: object, approvals: Mapping[str, str] | None = None, *,  # type: ignore[override]
               on_denied: Callable[[frozenset[str]], Mapping[str, bytes | None]] | None = None):
        if on_denied is not None:  # partial commits are sync's; the serving device commits a step whole
            raise StoreError("a resolve over the transport commits each step whole")
        result = super().commit(handle, approvals)
        if self._mirror.relay is None:
            raise StoreError("this mirror has no peer to relay its commits to")
        self._mirror.relay(self._ops)
        return result


# -- serving side -----------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolveRequest:
    """A resolve request as the serving device accepts it: the path, the chosen etag, and the
    etags expected here and on the requesting device."""

    path: str
    keep: str | None  # None: the deletion, which only the requester (holding the record) makes
    expected: str | None
    theirs: str | None
    device: str
    peer: str

    @classmethod
    def parse(cls, header: Mapping[str, object], device: str, peer: str) -> ResolveRequest:
        path, keep, expected = header.get("path"), header.get("keep"), header.get("expected")
        if type(path) is not str or syncable(path) is None:
            raise PeerRefused("invalid", "a resolve request names a synchronized record")
        if keep is not None and not is_etag(keep):  # None: the deletion (028)
            raise PeerRefused("invalid", "a resolve request keeps the current etag of one device's version, "
                                         "or none for the deletion")
        if not isinstance(expected, dict) or set(expected) != {device, peer} or not all(
                value is None or is_etag(value) for value in expected.values()):
            raise PeerRefused("invalid", "a resolve request gives the expected etag of both devices")
        return cls(path, keep, expected[device], expected[peer], device, peer)  # type: ignore[arg-type]

    def admit(self, store: LibraryStore) -> None:
        """Refuse a request this device cannot honour, before anything is sent.

        A peer is not an owner, so a resolve may write here only a version this device already
        has or had: its own current one (it is the winner, and only bookkeeping is written here),
        one it keeps under ``.conflicts`` beside the path, which the keep-both transfer that
        reported the conflict left, the version this pair's archive records both devices agreed
        on (an edit of a mutable record whose copy to a device still at the agreed version
        failed, as ``head_dependency``, ``unapplicable`` or ``denied``, keeps nothing on the
        device that made it), or a blob, whose name is its content. A kept version counts only when
        its file reads (an unreadable one may hold anything). When this device receives, the chosen
        version must also be the requester's own current one: it is the version resolve copies.
        Anything else would let a paired device write chosen bytes over a record. The deletion
        (``keep=None``) is admitted only where this device lacks the record: it is the winner.
        """
        found = _current(store, self.path)
        if found != self.expected:
            raise PeerRefused("stale", f"{self.path} changed on this device since the conflict was reported")
        if self.keep is None and found is not None:  # a peer never deletes a record here (028)
            raise PeerRefused("invalid", f"{self.path} is held on this device, which deletes it: start this "
                                         "resolve here")
        if self.keep == found:
            if self.theirs == found:
                raise PeerRefused("no_conflict", f"{self.path} is identical on both devices")
            return
        if self.keep != self.theirs:
            raise PeerRefused("unknown_version", f"the chosen version of {self.path} is neither device's current one")
        if syncable(self.path) == "blob":
            if found is None and self.keep == "sha256:" + Path(self.path).stem:
                return  # content-addressed: the bytes are checked against the name as they land
        elif kept_slots(store, self.path, self.keep)[0] or self._agreed(store):
            # only a kept file read whole proves this device had those bytes: one that cannot be read is
            # unknown (028), and as with every other use of an unreadable slot, unknown fails closed
            return
        raise PeerRefused("unknown_version", f"the chosen version of {self.path} is not one this device holds "
                                             "or keeps beside it")

    def _agreed(self, store: LibraryStore) -> bool:
        """Whether this pair's archive here records ``keep`` as the version both devices agreed on."""
        try:  # read as a sync reads it: a root and the shards it lists, once it outgrows one file
            archive = read_document(store, "archive", self.peer, tuple(sorted((self.device, self.peer))))  # type: ignore[arg-type]
        except (StoreError, OSError, ValueError, SyncInterrupted):
            return False  # unreadable is never an agreement
        return archive.state == "valid" and archive.entries.get(self.path) == ("agreed", self.keep)

    def confirm_absent(self, store: LibraryStore, absent: object) -> None:
        """A deletion's last check before the requester deletes (028), under this library's lock:
        this device, the winner, still lacks the path and every revision the deletion takes along.
        A record written here since the snapshot is refused ``stale``, so no tombstone is archived
        for a version this device holds."""
        if self.keep is not None or not isinstance(absent, list) or self.path not in absent:
            raise PeerRefused("invalid", "a check confirms that the paths a deletion removes are absent here")
        absent_here(store, absent)

    def check_step(self, store: LibraryStore, ops: list[tuple[dict, bytes | Path]]) -> str | None:  # type: ignore[type-arg]
        """Refuse a step of this resolve that does not do what the request said; return the etag of
        the version the step may newly record as held at the path, if it is the hold's record step.

        ``ops`` are all the step's writes. The step that writes the path finds the etag the user saw
        here, writes exactly the chosen version there, and holds this device's own version beside
        it; every later step finds the chosen version, so an archive never records an agreement
        this device no longer holds. A hold is taken only in the step that writes the path: in any
        other step this device's own version is still its live record, and a hold of those bytes
        (with a record calling them held) would only keep a version from ever being dismissed.

        When this device receives, the steps before the data are resolve's kept-version
        bookkeeping (028), each a commit of its own: the pair's record with the ended conflict's
        deliveries cleared, then the record holding this device's own version, before the data
        commit so that no recovery of that commit can lose it (``grimoire.sync.resolution``).
        Each writes this pair's ``keeps.json`` and nothing else, while the path still holds the
        etag the user saw here; what one may add as held is exactly that version
        (``peer_may_write``), and a sync drops the entry if the data never lands and no slot holds
        those bytes. When this device is the winner, its record's step (deliveries cleared) finds
        the chosen version already here, as every later step does.
        """
        writes = [(header, body) for header, body in ops if header.get("path") == self.path]
        found = _current(store, self.path)
        held = [body for header, body in writes if header.get("op") == "keep" and header.get("hold") is True]
        if any(found is None or type(body) is not bytes or etag(body) != found for body in held):
            raise PeerRefused("invalid", f"a resolve holds only this device's own version of {self.path}")
        record = [(header, body) for header, body in writes if header.get("op") in ("put", "blob", "delete")]
        if not record:
            if held:
                raise PeerRefused("invalid", f"a resolve holds this device's own version of {self.path} only in "
                                             "the step that replaces it")
            if found == self.keep:
                return None
            if (found is not None and found == self.expected and ops
                    and all(header.get("op") == "state" and header.get("name") == keeps_name(self.peer)
                            for header, _ in ops)):
                return found
            raise PeerRefused("stale", f"{self.path} changed on this device during the resolve")
        if found == self.keep:  # this device is the winner: resolve writes only its bookkeeping here
            raise PeerRefused("invalid", f"{self.path} already holds the chosen version on this device")
        if found != self.expected:
            raise PeerRefused("stale", f"{self.path} changed on this device since the conflict was reported")
        if any(header.get("op") == "put" and (type(body) is not bytes or etag(body) != self.keep)
               for header, body in record):
            raise PeerRefused("invalid", f"the bytes written to {self.path} are not the chosen version")
        if found is not None and not held:
            raise PeerRefused("invalid", f"replacing {self.path} must hold this device's own version beside it")
        return None


def _holds(store: LibraryStore, path: str, keep: str) -> bool:
    """Whether this library holds ``keep`` at ``path``; when that cannot be read, it might."""
    try:
        return _current(store, path) == keep
    except (StoreError, OSError, ValueError):
        return True


def _current(store: LibraryStore, path: str) -> str | None:
    """This library's etag for ``path``: a blob's is its name, once its bytes are verified."""
    if syncable(path) == "blob":
        try:
            return store.verify_blob(path)
        except RecordNotFound:
            return None
    data = store.read_exact(path)
    return None if data is None else etag(data)

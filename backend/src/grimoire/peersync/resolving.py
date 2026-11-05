"""Ending one conflict over the peer connection (028): 019's ``resolve`` with one library on the other device.

A transfer reports a keep-both conflict; ``resolve_conflict`` ends it from either device without
opening both libraries in one process. As a transfer does for a sync, it keeps 019's core
unchanged: the serving device sends a snapshot (asked for with a ``resolve`` request that names
the path and the etags the user saw), the client rebuilds it as a checked mirror, and runs
019's own ``resolve`` between its library and the mirror. Every commit ``resolve`` makes on the
mirror is replayed on the serving device before ``resolve`` goes on, as one transaction there
under the client's peer principal, re-read and confirmed. So the real devices are written in
``resolve``'s own order (the receiver's data with its kept version and pending marker, verify,
archives, pending markers) and a failure at any point leaves what a crash at that point of a
local resolve leaves (docs/DEVICE_SYNC.md).

The serving device checks the user's expected etag of its own copy too: when the request
arrives, and again before every step it commits (the step that writes the path expects that
etag, every later one the chosen etag).
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from grimoire.store import LibraryStore, RecordNotFound, StoreError, Transaction, etag
from grimoire.store.engine import MAX_CONFLICT_ATTEMPTS
from grimoire.store.jsonfmt import is_etag
from grimoire.sync import ResolveRefused, SyncError, SyncInterrupted, device_of, resolve, syncable

from .errors import MirrorMismatch, PeerRefused, TransportError
from .replay import Op, kept_target, send_ops
from .session import _check_paired, dial, hang_up, pairing_for
from .snapshot import Limits, MirrorStore, receive_mirror
from .wire import Channel, client_handshake

@dataclass(frozen=True)
class ResolveReport:
    """What one resolve over the transport did.

    ``status`` is ``resolved`` (``receiver`` now holds the chosen version, its own kept under
    ``.conflicts``, and both archives record the agreement), ``refused`` (nothing was written on
    either device; ``reason`` says why) or ``incomplete`` (the connection or pairing ended, or a
    step after the receiver's data was refused: each device is as a crash at that point of a
    local resolve leaves it, and the next transfer converges).
    """

    peer: str
    path: str
    status: str
    reason: str | None = None
    receiver: str | None = None


def resolve_conflict(store: LibraryStore, authority, peer: str, address: tuple[str, int], path: str,  # type: ignore[no-untyped-def]
                     keep: str, expected: Mapping[str, str | None], *, timeout: float = 600.0,
                     workdir: str | Path | None = None, limits: Limits = Limits()) -> ResolveReport:
    """End the conflict at ``path`` with the paired device ``peer`` at ``address`` by keeping the
    version whose current etag is ``keep``; ``expected`` gives each device's etag (``None``:
    absent) as the conflict was reported, exactly as 019's ``resolve`` takes them.

    Raises ``NotPaired`` or ``AuthenticationFailed`` before anything is exchanged, as ``transfer``
    does; every other outcome is a report.
    """
    pairing, local_handle = pairing_for(store, authority, peer, address)
    local = device_of(store)
    why = _unusable(local, peer, path, keep, expected)
    if why is not None:
        return ResolveReport(peer, path, "refused", f"ResolveRefused: {why}")
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

    ``keep=None`` would choose a deletion: resolve delivers a version, and an absent one is no
    version to deliver (deletion propagation belongs to a later 028 slice).
    """
    if type(path) is not str or syncable(path) is None:
        return f"{path!r} is not a synchronized record"
    if keep is None:
        return "a deletion cannot be chosen: keep must be the current etag of one device's version"
    if not is_etag(keep):
        return "keep must be the current etag of one device's version"
    if not isinstance(expected, Mapping) or set(expected) != {local, peer} or not all(
            value is None or is_etag(value) for value in expected.values()):
        return f"expected must give the etag (or None) of {local} and {peer}"
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
        relay = _Relay(channel, store, key)
        mirror.store.relay = relay  # type: ignore[attr-defined]
        try:
            receiver = resolve(store, mirror.store, path, keep, expected, {local: local_handle, peer: mirror.handle})
        except ResolveRefused as error:  # 019 refused before its first commit
            return ResolveReport(peer, path, "refused", f"ResolveRefused: {error}")
        except PeerRefused as error:
            if error.code == "not_paired":
                return ResolveReport(peer, path, "incomplete", "not_paired")
            # The receiver's data is resolve's first commit. A refused step that carried it wrote
            # nothing on either device (the store commits a step whole); a refused later step
            # left the data landed and the agreement unrecorded, as a crash between them would.
            first = relay.records and error.code != "verification"
            return ResolveReport(peer, path, "refused" if first else "incomplete", str(error))
        except (SyncInterrupted, MirrorMismatch) as error:  # the data may have landed; the next sync records it
            return ResolveReport(peer, path, "incomplete", f"{type(error).__name__}: {error}")
        except (SyncError, StoreError, OSError, ValueError) as error:
            return ResolveReport(peer, path, "incomplete", f"{type(error).__name__}: {error}")
        finally:
            mirror.store.close()
    return ResolveReport(peer, path, "resolved", None, receiver)


class _Relay:
    """Sends one mirror commit to the serving device and waits for it to commit and confirm it."""

    def __init__(self, channel: Channel, store: LibraryStore, key: bytes) -> None:
        self.channel, self.store, self.key = channel, store, key
        self.records = False  # whether the step last sent wrote a record or kept version (not only .sync)

    def __call__(self, ops: list[tuple[str, Callable[[], Op]]]) -> None:
        # An owner who unpairs mid-resolve must not have anything more sent (as a transfer checks).
        _check_paired(self.store, self.channel.peer, self.key)
        self.records = any(kind != "state" for kind, _ in ops)
        send_ops(self.channel, "resolve", (make() for _, make in ops))


class RelayingMirror(MirrorStore):
    """The mirror for a resolve: every transaction it commits is replayed on the serving device."""

    relay: Callable[[list[tuple[str, Callable[[], Op]]]], None] | None = None

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
        key = super().put_exact(path, data, expected, replace_unreferenced=replace_unreferenced,
                                regenerable=regenerable, allowed_manifests=allowed_manifests)
        header: dict[str, object] = {"op": "put", "path": path, "expected": expected}
        if replace_unreferenced:
            header.update({"replace": True, "allowed": sorted(allowed_manifests)})
        body = bytes(data)
        self._ops.append(("put", lambda: (header, body, path, etag(body))))
        return key

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

    def put_sync_state(self, name: str, data: bytes, expected: str | None) -> str:
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
    """A resolve request as the serving device accepts it: the path, the chosen etag and its own expected one."""

    path: str
    keep: str
    expected: str | None

    @classmethod
    def parse(cls, header: Mapping[str, object], device: str, peer: str) -> ResolveRequest:
        path, keep, expected = header.get("path"), header.get("keep"), header.get("expected")
        if type(path) is not str or syncable(path) is None:
            raise PeerRefused("invalid", "a resolve request names a synchronized record")
        if not is_etag(keep):  # a deletion (None) is never a version to keep
            raise PeerRefused("invalid", "a resolve request keeps the current etag of one device's version")
        if not isinstance(expected, dict) or set(expected) != {device, peer} or not all(
                value is None or is_etag(value) for value in expected.values()):
            raise PeerRefused("invalid", "a resolve request gives the expected etag of both devices")
        return cls(path, keep, expected[device])  # type: ignore[arg-type]

    def check(self, store: LibraryStore, *, writes: bool) -> None:
        """Refuse (``stale``) unless this library's copy of the path is what the resolve expects here.

        Before the step that writes the path (and when the request arrives), that is the etag
        the user saw; before every later step, the chosen one. A local edit since makes the
        resolve stale here exactly as a local ``resolve`` refuses a device that changed, and an
        archive never records an agreement this device no longer holds.
        """
        want = self.expected if writes else self.keep
        found = _current(store, self.path)
        if found != want:
            raise PeerRefused("stale", f"{self.path} changed on this device since the conflict was reported")


def _current(store: LibraryStore, path: str) -> str | None:
    """This library's etag for ``path``: a blob's is its name, once its bytes are verified."""
    if syncable(path) == "blob":
        try:
            return store.verify_blob(path)
        except RecordNotFound:
            return None
    data = store.read_exact(path)
    return None if data is None else etag(data)

"""The serving end of a peer transfer (028): authenticate a paired device, then answer its requests.

A session is one connection. After the handshake the client asks for snapshots (``snapshot``) and
sends 019 steps to commit here (``op`` frames, then ``commit``); ``bye`` ends it. A ``resolve``
request is a snapshot to end one conflict from, and that resolve's steps follow it (``resolving``);
a server that predates it answers ``protocol`` and ends the session, as for any request it does
not know. A refused step is answered with a named error and changes nothing here; the session
goes on. This slice listens on loopback only: frames are authenticated but not encrypted
(docs/DEVICE_SYNC.md).
"""

from __future__ import annotations

import hmac
import socket
import threading

from grimoire.store import LibraryStore, StoreError
from grimoire.sync import SyncInterrupted, device_of
from grimoire.sync.documents import check_device_id

from .errors import AuthenticationFailed, PeerRefused, TransportError
from .pairing import peer_principal, read_pairing
from .replay import StepBuffer, confirmation
from .resolving import ResolveRequest, committing, resolving_out
from .snapshot import send_snapshot
from .wire import Channel, is_loopback, no_delay, server_handshake


class PeerServer:
    """Serve ``store`` to paired devices, one connection at a time, on a loopback address."""

    def __init__(self, store: LibraryStore, authority, *, host: str = "127.0.0.1", port: int = 0,  # type: ignore[no-untyped-def]
                 timeout: float = 600.0, handshake_timeout: float = 10.0) -> None:
        """``timeout`` is how long an authenticated client may go quiet: long, because between its
        snapshot and its first step the client rebuilds the mirror, plans and commits its own side
        and the mirror's (minutes for a first sync of many pictures). ``handshake_timeout`` bounds
        the whole handshake, so a local process that never authenticates frees the slot quickly."""
        if not is_loopback(host):
            raise ValueError("this slice serves loopback addresses only: its frames are not encrypted")
        self.store, self.authority = store, authority
        self.timeout, self.handshake_timeout = timeout, handshake_timeout
        self.device = device_of(store)
        family = socket.AF_INET6 if ":" in host else socket.AF_INET  # "::1" needs the IPv6 family
        self._socket = socket.create_server((host, port), family=family)
        self._socket.settimeout(0.2)
        self._stop = threading.Event()

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._socket.getsockname()[:2]
        return host, port

    def serve_one(self) -> bool:
        """Wait for one connection (until ``close``) and serve it to its end; ``False`` if closed first."""
        while not self._stop.is_set():
            try:
                connection, _ = self._socket.accept()
            except TimeoutError:
                continue
            except OSError:
                return False
            with connection:
                no_delay(connection)
                try:
                    self._session(connection)
                except Exception:  # noqa: BLE001 - one connection, whatever it sent, never stops the server
                    pass
            return True
        return False

    def serve_forever(self) -> None:
        while self.serve_one():
            pass

    def close(self) -> None:
        self._stop.set()
        self._socket.close()

    def _key(self, peer: object) -> bytes | None:
        try:
            pairing = read_pairing(self.store, check_device_id(peer))
        except ValueError:
            return None
        return None if pairing is None else pairing.key

    def _session(self, connection: socket.socket) -> None:
        try:
            channel = server_handshake(connection, self.device, self._key, within=self.handshake_timeout)
        except (AuthenticationFailed, TransportError, PeerRefused):
            return  # refused (or gone) before it was authenticated: nothing was read or written
        connection.settimeout(self.timeout)
        try:
            self._serve(channel, channel_key=channel.pairing_key)
        except TransportError:
            return  # the client went away; every commit already made is whole

    def _still_paired(self, peer: str, key: bytes | None) -> bool:
        """Whether this session's key is still the pairing's, and the peer still holds a grant.

        Checked before every snapshot and every commit, not only at the handshake: ``unpair`` (or
        pairing again with a new key) ends an open session at its next request, so neither the
        library's contents nor this pair's ``.sync`` state (which needs no grant to write) stays
        reachable through a connection that was authenticated earlier.
        """
        current = self._key(peer)
        if key is None or current is None or not hmac.compare_digest(current, key):
            return False
        principal = peer_principal(peer)
        return any(grant.principal == principal for grant in self.authority.active_grants())

    def _serve(self, channel: Channel, *, channel_key: bytes | None) -> None:
        peer = channel.peer
        handle = self.authority.handle_for(peer_principal(peer), trusted=True)
        buffer = StepBuffer(peer)
        resolving: ResolveRequest | None = None
        while True:
            header, body = channel.recv()
            kind = header["t"]
            try:
                if kind == "bye":
                    return
                if kind == "commit":
                    # Held from the pairing check to the last write; while a resolve of this
                    # library's own is in flight, the wait for it is bounded (``committing``).
                    with committing(self.store):
                        if not self._still_paired(peer, channel_key):
                            buffer.reset()
                            channel.refuse("not_paired", "this device is no longer paired with yours")
                            return
                        may_hold = None
                        if resolving is not None and buffer.poisoned is None:
                            may_hold = resolving.check_step(self.store, buffer.ops)
                        etags = buffer.commit(self.store, handle, may_hold=may_hold)
                    channel.send({"t": "committed", "count": len(etags), "digest": confirmation(etags)})
                    continue
                if kind in ("snapshot", "resolve") and not self._still_paired(peer, channel_key):
                    buffer.reset()
                    channel.refuse("not_paired", "this device is no longer paired with yours")
                    return
                if kind == "snapshot":
                    buffer.reset()
                    buffer.resolving = resolving = None
                    send_snapshot(channel, self.store, self.authority, peer)
                elif kind == "resolve":  # a snapshot to resolve one path from, then that resolve's steps
                    buffer.reset()
                    buffer.resolving = resolving = None
                    if resolving_out(self.store):  # its steps could wait on that resolve, and it on them
                        raise PeerRefused("busy", "this library is resolving a conflict of its own; try again")
                    request = ResolveRequest.parse(header, self.device, peer)
                    request.admit(self.store)  # before anything is sent
                    buffer.resolving, resolving = request.path, request
                    send_snapshot(channel, self.store, self.authority, peer)
                elif kind == "op":
                    buffer.add(header, body)  # a refusal here is held and answered at "commit"
                else:  # a client this confused cannot be kept in step: refuse and end the session
                    channel.refuse("protocol", f"unexpected request {kind!r}")
                    return
            except PeerRefused as refusal:
                buffer.reset()
                channel.refuse(refusal.code, str(refusal))
            except (OSError, ValueError, StoreError, SyncInterrupted) as error:  # an unreadable .sync file, say
                # Refused as a whole: the client reads one reply per request, so a refusal is
                # sent only for a request that expects one (a snapshot or a commit). A snapshot
                # half-sent when the error came is ended by the refusal, which the client's
                # mirror builder reads as PeerRefused and discards the mirror.
                buffer.reset()
                channel.refuse("unavailable", f"{type(error).__name__}: {error}")

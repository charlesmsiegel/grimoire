"""The serving end of a peer transfer (028): authenticate a paired device, then answer its requests.

A session is one connection. After the handshake the client asks for snapshots (``snapshot``) and
sends 019 steps to commit here (``op`` frames, then ``commit``); ``bye`` ends it. A refused step
is answered with a named error and changes nothing here; the session goes on. This slice listens
on loopback only: frames are authenticated but not encrypted (docs/DEVICE_SYNC.md).
"""

from __future__ import annotations

import ipaddress
import socket
import threading

from grimoire.store import LibraryStore
from grimoire.sync import device_of
from grimoire.sync.documents import check_device_id

from .errors import AuthenticationFailed, PeerRefused, TransportError
from .pairing import peer_principal, read_pairing
from .replay import StepBuffer
from .snapshot import send_snapshot
from .wire import Channel, no_delay, server_handshake


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class PeerServer:
    """Serve ``store`` to paired devices, one connection at a time, on a loopback address."""

    def __init__(self, store: LibraryStore, authority, *, host: str = "127.0.0.1", port: int = 0,  # type: ignore[no-untyped-def]
                 timeout: float = 30.0) -> None:
        if not _loopback(host):
            raise ValueError("this slice serves loopback addresses only: its frames are not encrypted")
        self.store, self.authority, self.timeout = store, authority, timeout
        self.device = device_of(store)
        self._socket = socket.create_server((host, port))
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
                connection.settimeout(self.timeout)
                no_delay(connection)
                self._session(connection)
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
            channel = server_handshake(connection, self.device, self._key)
        except (AuthenticationFailed, TransportError, PeerRefused):
            return  # refused (or gone) before it was authenticated: nothing was read or written
        try:
            self._serve(channel)
        except TransportError:
            return  # the client went away; every commit already made is whole

    def _serve(self, channel: Channel) -> None:
        peer = channel.peer
        handle = self.authority.handle_for(peer_principal(peer), trusted=True)
        buffer = StepBuffer(peer)
        while True:
            header, body = channel.recv()
            kind = header["t"]
            try:
                if kind == "bye":
                    return
                if kind == "snapshot":
                    send_snapshot(channel, self.store, self.authority, peer)
                elif kind == "op":
                    buffer.add(header, body)
                elif kind == "commit":
                    channel.send({"t": "committed", "etags": buffer.commit(self.store, handle)})
                else:
                    raise PeerRefused("protocol", f"unexpected request {kind!r}")
            except PeerRefused as refusal:
                buffer.ops, buffer.size = [], 0
                channel.refuse(refusal.code, str(refusal))
            except (OSError, ValueError) as error:  # an unreadable .sync file, say: refused, never half-sent
                buffer.ops, buffer.size = [], 0
                channel.refuse("unavailable", f"{type(error).__name__}: {error}")

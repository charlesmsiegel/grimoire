"""Authenticated framing for one peer connection (028): mutual proof of the pairing key, then MACs.

Handshake (plain frames): the client sends its device id, the device it wants and a fresh nonce;
the server answers with its own nonce and an HMAC proof over both nonces and both ids; the
client checks it and answers with its own proof; the server checks that and confirms. Each side
proves it holds the pairing key without sending it, and nothing that either side sent before can
be replayed into another session (both nonces are in every proof and key).

After the handshake every frame carries a sequence number and an HMAC-SHA256 tag under a
per-direction session key derived from the pairing key and the transcript: a frame that is
forged, altered, replayed, reordered or reflected is a ``TransportError``. Frames are *not*
encrypted in this slice: a reader on the path sees the library's records. That is why the
server listens on loopback only (encrypted transport is a later milestone).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import socket
import struct

from .errors import AuthenticationFailed, PeerRefused, TransportError

PROTOCOL = "grimoire.peersync/1"
NONCE_BYTES = 32
_TAG = 32
#: A frame holds one header and at most one body: a record (8 MiB) or a blob (32 MiB) with room.
MAX_FRAME = 40 * 1024 * 1024
MAX_HEADER = 4 * 1024 * 1024
_LENGTH = struct.Struct(">I")
_SEQ = struct.Struct(">Q")


def no_delay(sock: socket.socket) -> None:
    """Send each frame at once: a step is a run of small frames answered by one reply, and Nagle's
    algorithm holding the tail of one frame back for a delayed ACK costs ~40 ms per round trip."""
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:  # not TCP (a socketpair in tests): there is nothing to tune
        pass


def _transcript(client: str, server: str, client_nonce: bytes, server_nonce: bytes) -> bytes:
    return b"|".join((PROTOCOL.encode(), client.encode(), server.encode(), client_nonce, server_nonce))


def _mac(key: bytes, label: bytes, data: bytes) -> bytes:
    return hmac.new(key, label + b"|" + data, hashlib.sha256).digest()


def _read_exact(sock: socket.socket, size: int) -> bytes:
    chunks, remaining = [], size
    while remaining:
        try:
            chunk = sock.recv(min(remaining, 1 << 20))
        except OSError as error:  # a timeout too
            raise TransportError(f"the connection failed: {error}") from error
        if not chunk:
            raise TransportError("the connection closed early")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _send_raw(sock: socket.socket, payload: bytes) -> None:
    try:
        sock.sendall(_LENGTH.pack(len(payload)) + payload)
    except OSError as error:
        raise TransportError(f"the connection failed: {error}") from error


def _recv_raw(sock: socket.socket) -> bytes:
    (size,) = _LENGTH.unpack(_read_exact(sock, _LENGTH.size))
    if size > MAX_FRAME:
        raise TransportError(f"a frame of {size} bytes is over the {MAX_FRAME}-byte limit")
    return _read_exact(sock, size)


def _encode(header: dict, body: bytes) -> bytes:  # type: ignore[type-arg]
    data = json.dumps(header, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    if len(data) > MAX_HEADER:
        raise TransportError("a frame header is over its size limit")
    return _LENGTH.pack(len(data)) + data + body


def _decode(payload: bytes) -> tuple[dict, bytes]:  # type: ignore[type-arg]
    if len(payload) < _LENGTH.size:
        raise TransportError("a frame is too short")
    (size,) = _LENGTH.unpack(payload[:_LENGTH.size])
    if size > MAX_HEADER or _LENGTH.size + size > len(payload):
        raise TransportError("a frame header is malformed")
    try:
        header = json.loads(payload[_LENGTH.size:_LENGTH.size + size])
    except (UnicodeDecodeError, ValueError) as error:
        raise TransportError(f"a frame header is not JSON: {error}") from None
    if not isinstance(header, dict) or type(header.get("t")) is not str:
        raise TransportError("a frame header names no message type")
    return header, payload[_LENGTH.size + size:]


def _plain(sock: socket.socket, header: dict) -> None:  # type: ignore[type-arg]
    _send_raw(sock, _encode(header, b""))


def _plain_recv(sock: socket.socket) -> dict:  # type: ignore[type-arg]
    header, body = _decode(_recv_raw(sock))
    if body:
        raise TransportError("a handshake frame carries no body")
    if header["t"] == "error":
        raise PeerRefused(str(header.get("code", "refused")), str(header.get("message", "")))
    return header


def _hex(header: dict, name: str, size: int) -> bytes:  # type: ignore[type-arg]
    value = header.get(name)
    try:
        raw = bytes.fromhex(value) if type(value) is str else b""
    except ValueError:
        raw = b""
    if len(raw) != size:
        raise TransportError(f"the handshake's {name} is malformed")
    return raw


class Channel:
    """One authenticated connection: ``send``/``recv`` (header, body) frames, MAC-checked in order."""

    def __init__(self, sock: socket.socket, peer: str, send_key: bytes, recv_key: bytes) -> None:
        self.sock = sock
        self.peer = peer  # the authenticated device id at the other end
        self._send_key, self._recv_key = send_key, recv_key
        self._sent = self._received = 0

    def send(self, header: dict, body: bytes = b"") -> None:  # type: ignore[type-arg]
        if len(body) > MAX_FRAME - MAX_HEADER:
            raise TransportError("a frame body is over its size limit")
        payload = _SEQ.pack(self._sent) + _encode(header, body)
        self._sent += 1
        _send_raw(self.sock, payload + _mac(self._send_key, b"frame", payload))

    def recv(self) -> tuple[dict, bytes]:  # type: ignore[type-arg]
        frame = _recv_raw(self.sock)
        if len(frame) < _SEQ.size + _TAG:
            raise TransportError("a frame is too short")
        payload, tag = frame[:-_TAG], frame[-_TAG:]
        if not hmac.compare_digest(tag, _mac(self._recv_key, b"frame", payload)):
            raise TransportError("a frame failed its authentication check")
        (seq,) = _SEQ.unpack(payload[:_SEQ.size])
        if seq != self._received:
            raise TransportError(f"frame {seq} arrived where {self._received} was expected")
        self._received += 1
        header, body = _decode(payload[_SEQ.size:])
        if header["t"] == "error":
            raise PeerRefused(str(header.get("code", "refused")), str(header.get("message", "")))
        return header, body

    def refuse(self, code: str, message: str) -> None:
        """Send a named refusal (the other end raises ``PeerRefused``)."""
        self.send({"t": "error", "code": code, "message": message[:2000]})

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _keys(key: bytes, transcript: bytes) -> tuple[bytes, bytes]:
    return _mac(key, b"client-to-server", transcript), _mac(key, b"server-to-client", transcript)


def client_handshake(sock: socket.socket, device: str, peer: str, key: bytes) -> Channel:
    """Authenticate to ``peer`` as ``device``; raises ``AuthenticationFailed`` or ``PeerRefused``."""
    client_nonce = secrets.token_bytes(NONCE_BYTES)
    _plain(sock, {"t": "hello", "proto": PROTOCOL, "from": device, "to": peer, "nonce": client_nonce.hex()})
    try:
        answer = _plain_recv(sock)
    except PeerRefused as error:  # not paired here, or not that device: a refusal before any proof
        raise AuthenticationFailed(f"{peer} refused this device: {error}", error.code) from error
    if answer.get("t") != "challenge" or answer.get("from") != peer or answer.get("proto") != PROTOCOL:
        raise AuthenticationFailed(f"the other end is not {peer} speaking {PROTOCOL}")
    server_nonce = _hex(answer, "nonce", NONCE_BYTES)
    transcript = _transcript(device, peer, client_nonce, server_nonce)
    if not hmac.compare_digest(_hex(answer, "proof", _TAG), _mac(key, b"server-proof", transcript)):
        raise AuthenticationFailed(f"{peer} could not prove it holds this pairing's key")
    _plain(sock, {"t": "proof", "proof": _mac(key, b"client-proof", transcript).hex()})
    send_key, recv_key = _keys(key, transcript)
    channel = Channel(sock, peer, send_key, recv_key)
    try:
        header, _ = channel.recv()
    except PeerRefused as error:
        raise AuthenticationFailed(f"{peer} refused this device's proof: {error}", error.code) from error
    if header.get("t") != "welcome":
        raise TransportError("the handshake did not finish")
    return channel


def server_handshake(sock: socket.socket, device: str, keys) -> Channel:  # type: ignore[no-untyped-def]
    """Authenticate a client to this ``device``. ``keys(peer)`` is the pairing key, or ``None``.

    A client that names another device, is not paired, or cannot prove the key is refused with a
    named error frame, and ``AuthenticationFailed`` is raised here; nothing else is read from it.
    """
    hello = _plain_recv(sock)
    client = hello.get("from")
    if hello.get("t") != "hello" or hello.get("proto") != PROTOCOL or type(client) is not str:
        _plain(sock, {"t": "error", "code": "protocol", "message": f"expected a {PROTOCOL} hello"})
        raise AuthenticationFailed("the client did not open with a hello")
    if hello.get("to") != device:
        _plain(sock, {"t": "error", "code": "wrong_device", "message": "this is not that device"})
        raise AuthenticationFailed(f"the client asked for {hello.get('to')!r}, not {device!r}")
    client_nonce = _hex(hello, "nonce", NONCE_BYTES)
    key = keys(client)
    if key is None:
        _plain(sock, {"t": "error", "code": "not_paired", "message": "this device is not paired with yours"})
        raise AuthenticationFailed(f"{client!r} is not paired with {device}")
    server_nonce = secrets.token_bytes(NONCE_BYTES)
    transcript = _transcript(client, device, client_nonce, server_nonce)
    _plain(sock, {"t": "challenge", "proto": PROTOCOL, "from": device, "nonce": server_nonce.hex(),
                  "proof": _mac(key, b"server-proof", transcript).hex()})
    proof = _plain_recv(sock)
    recv_key, send_key = _keys(key, transcript)
    channel = Channel(sock, client, send_key, recv_key)
    if proof.get("t") != "proof" or not hmac.compare_digest(_hex(proof, "proof", _TAG),
                                                             _mac(key, b"client-proof", transcript)):
        channel.refuse("unauthenticated", "the proof does not match this pairing's key")
        raise AuthenticationFailed(f"{client} could not prove it holds this pairing's key")
    channel.send({"t": "welcome"})
    return channel

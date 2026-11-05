"""028's wire and serving-side refusals: what a forged frame or a hostile paired device can and cannot do.

A paired peer is a non-owner principal: its writes go through this library's grants for it, it
can never supply an approval, and it can touch no reserved root. Every server here is loopback.
"""

from __future__ import annotations

import socket

import pytest

from grimoire.peersync import PeerRefused, PeerServer, TransportError, read_pairing
from grimoire.peersync.wire import Channel, client_handshake
from grimoire.store import RecordEnvelope, SourceManifest

from chat_fixtures import CARDS, MARA
from peersync_helpers import DESKTOP, PHONE, paired, serving
from store_helpers import OWNER


class _Recording:
    """A socket stand-in for the sending side that records each frame's bytes and can alter them."""

    def __init__(self, sock: socket.socket, alter=lambda data: data) -> None:  # type: ignore[no-untyped-def]
        self.sock, self.alter, self.frames = sock, alter, []  # type: ignore[var-annotated]

    def sendall(self, data: bytes) -> None:
        self.frames.append(data)
        self.sock.sendall(self.alter(data))


SEND, RECV = b"k" * 32, b"j" * 32


def _pair_of_channels(alter=lambda data: data):  # type: ignore[no-untyped-def]
    left, right = socket.socketpair()
    wire = _Recording(left, alter)
    return Channel(wire, "b", SEND, RECV), Channel(right, "a", RECV, SEND), wire, right  # type: ignore[arg-type]


def test_frames_arrive_in_order_with_their_bodies():
    sender, receiver, *_ = _pair_of_channels()
    sender.send({"t": "file", "path": "sources/x"}, b"\x00body")
    sender.send({"t": "end"})
    assert receiver.recv() == ({"t": "file", "path": "sources/x"}, b"\x00body")
    assert receiver.recv() == ({"t": "end"}, b"")


def test_an_altered_frame_is_refused():
    sender, receiver, *_ = _pair_of_channels(lambda data: data[:-40] + bytes([data[-40] ^ 1]) + data[-39:])
    sender.send({"t": "file", "path": "sources/x"}, b"payload")
    with pytest.raises(TransportError, match="authentication"):
        receiver.recv()


def test_a_replayed_frame_is_refused():
    sender, receiver, wire, _ = _pair_of_channels()
    sender.send({"t": "op"}, b"once")
    assert receiver.recv() == ({"t": "op"}, b"once")
    wire.sock.sendall(wire.frames[0])  # the same bytes again: a valid tag on a stale sequence number
    with pytest.raises(TransportError, match="expected"):
        receiver.recv()


def test_a_frame_reflected_back_to_its_sender_is_refused():
    sender, _, wire, right = _pair_of_channels()
    sender.send({"t": "op"})
    right.recv(1 << 16)
    right.sendall(wire.frames[0])  # each direction has its own key, so the sender cannot accept it
    reader = Channel(wire.sock, "b", SEND, RECV)
    with pytest.raises(TransportError, match="authentication"):
        reader.recv()


def test_an_oversized_frame_is_refused_before_it_is_read():
    _, receiver, wire, _ = _pair_of_channels()
    wire.sock.sendall((2**31).to_bytes(4, "big"))
    with pytest.raises(TransportError, match="limit"):
        receiver.recv()


def test_a_server_only_listens_on_loopback(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    for host in ("0.0.0.0", "::", "192.168.1.20", "localhost"):
        with pytest.raises(ValueError, match="loopback"):
            PeerServer(phone.store, phone.authority, host=host)


# -- a hostile but authenticated peer -----------------------------------------------------------

def _connect(desktop, server):  # type: ignore[no-untyped-def]
    pairing = read_pairing(desktop.store, PHONE)
    return client_handshake(socket.create_connection(server.address, timeout=10), DESKTOP, PHONE, pairing.key)


def _commit(channel: Channel, *ops: tuple[dict, bytes]) -> dict:  # type: ignore[type-arg]
    for header, body in ops:
        channel.send({"t": "op", **header}, body)
    channel.send({"t": "commit", "step": "data"})
    return channel.recv()[0]


def _record(store, envelope) -> tuple[str, bytes]:  # type: ignore[no-untyped-def]
    txn = store.transaction()
    txn.put(envelope, None)
    return next(iter(txn.preview().items()))


@pytest.mark.parametrize("op", [
    {"op": "state", "name": "device.json", "expected": None},          # its identity
    {"op": "state", "name": f"peers/{PHONE}/archive.json", "expected": None},  # another pair's state
    {"op": "put", "path": "secrets/peers/desktop.json", "expected": None},     # the pairing key
    {"op": "put", "path": "authority/security/grants.json", "expected": None},   # a grant for itself
    {"op": "rename", "path": "sources/x"},
])
def test_a_peer_cannot_write_outside_what_pairing_granted(tmp_path, op):
    desktop, phone, _ = paired(tmp_path)
    before = sorted(path for path in phone.store.root.rglob("*"))
    with serving(phone) as server:
        channel = _connect(desktop, server)
        with pytest.raises(PeerRefused) as refused:
            _commit(channel, (op, b"{}"))
        assert refused.value.code in ("invalid", "refused", "denied")
        channel.send({"t": "bye"})
        channel.close()
    assert sorted(path for path in phone.store.root.rglob("*")) == before


def test_a_peer_whose_grants_were_revoked_cannot_write_records_even_while_connected(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    path, data = _record(phone.store, SourceManifest(CARDS, OWNER, "Cards"))
    with serving(phone) as server:
        channel = _connect(desktop, server)
        for grant in phone.authority.active_grants():
            if grant.principal != OWNER:
                phone.authority.revoke_grant(grant.grant_id)
        with pytest.raises(PeerRefused) as refused:
            _commit(channel, ({"op": "put", "path": path, "expected": None}, data))
        assert refused.value.code == "denied"
        channel.close()
    assert not (phone.store.root / path).exists()


def test_a_refused_step_writes_none_of_its_other_records(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    manifest = _record(phone.store, SourceManifest(CARDS, OWNER, "Cards"))
    card = _record(phone.store, RecordEnvelope(MARA, {"bio": "a smuggler"}, name="Mara"))
    with serving(phone) as server:
        channel = _connect(desktop, server)
        with pytest.raises(PeerRefused):
            _commit(channel, ({"op": "put", "path": manifest[0], "expected": None}, manifest[1]),
                    ({"op": "put", "path": card[0], "expected": None}, card[1]),
                    ({"op": "state", "name": "device.json", "expected": None}, b"{}"))
        # the session goes on after a refusal, and an honest step then commits whole
        reply = _commit(channel, ({"op": "put", "path": manifest[0], "expected": None}, manifest[1]))
        assert reply["t"] == "committed" and set(reply["etags"]) == {manifest[0]}
        channel.close()
    assert (phone.store.root / manifest[0]).exists() and not (phone.store.root / card[0]).exists()

"""028's wire and serving-side refusals: what a forged frame or a hostile paired device can and cannot do.

A paired peer is a non-owner principal: its writes go through this library's grants for it, it
can never supply an approval, and it can touch no reserved root. Every server here is loopback.
"""

from __future__ import annotations

import os
import socket

import pytest

from grimoire.peersync import PeerRefused, PeerServer, TransportError, read_pairing, transfer, unpair
from grimoire.peersync import new_pairing_key, pair
from grimoire.peersync import replay as replay_module
from grimoire.peersync import server as server_module
from grimoire.peersync.wire import Channel, client_handshake
from grimoire.store import RecordEnvelope, SourceManifest, etag

from chat_fixtures import ANYA, CARDS, MARA
from peersync_helpers import DESKTOP, PHONE, paired, serving, start_scene
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
        assert refused.value.code == "not_paired"  # a peer with no grant left is no longer paired here
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


def test_unpairing_ends_an_open_session_at_its_next_request(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    with serving(phone) as server:
        channel = _connect(desktop, server)
        unpair(phone.store, phone.authority, DESKTOP)
        channel.send({"t": "snapshot"})
        with pytest.raises(PeerRefused) as refused:
            channel.recv()
        assert refused.value.code == "not_paired"
        with pytest.raises(TransportError):  # and the session is over
            channel.recv()
        channel.close()


def test_a_step_refused_while_its_writes_arrive_gets_one_answer_and_writes_nothing(tmp_path, monkeypatch):
    desktop, phone, _ = paired(tmp_path)
    monkeypatch.setattr(replay_module, "MAX_STEP_OPS", 2)
    manifest = _record(phone.store, SourceManifest(CARDS, OWNER, "Cards"))
    card = _record(phone.store, RecordEnvelope(MARA, {"bio": "a smuggler"}, name="Mara"))
    anya = _record(phone.store, RecordEnvelope(ANYA, {"bio": "a harbor pilot"}, name="Anya"))
    with serving(phone) as server:
        channel = _connect(desktop, server)
        with pytest.raises(PeerRefused) as refused:  # three writes where two fit
            _commit(channel, *(({"op": "put", "path": path, "expected": None}, data) for path, data in (manifest, card, anya)))
        assert refused.value.code == "too_large"
        reply = _commit(channel, ({"op": "put", "path": manifest[0], "expected": None}, manifest[1]))
        assert reply == {"t": "committed", "etags": {manifest[0]: etag(manifest[1])}}  # still in step
        channel.close()
    assert not (phone.store.root / card[0]).exists() and not (phone.store.root / anya[0]).exists()


def _framed(header: bytes) -> bytes:
    payload = len(header).to_bytes(4, "big") + header
    return len(payload).to_bytes(4, "big") + payload


@pytest.mark.parametrize("opening", [
    _framed(b"[" * 3000),                       # nested past any parser's patience, under 4 KiB
    _framed(b"{" * 3000),
    (10 * 1024 * 1024).to_bytes(4, "big"),      # a 10 MiB "hello" from a stranger
    b"\xff\xff",                                 # half a length, then silence
])
def test_nothing_a_stranger_sends_before_authenticating_stops_the_server(tmp_path, opening):
    desktop, phone, _ = paired(tmp_path)
    start_scene(desktop)
    with serving(phone, handshake_timeout=0.5) as server:
        with socket.create_connection(server.address, timeout=5) as stranger:
            stranger.sendall(opening)
            stranger.settimeout(5)
            try:
                while stranger.recv(4096):  # the server answers or hangs up; it does not wait forever
                    pass
            except OSError:
                pass
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"


def test_a_library_the_server_cannot_read_is_refused_and_the_server_keeps_serving(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    start_scene(desktop)
    look_alike = phone.store.root / ".sync" / "peers" / "DESKTOP"
    look_alike.mkdir(parents=True)
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
        assert report.status == "incomplete" and "unavailable" in report.reason, report
        look_alike.rmdir()
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"


def _can_link(root) -> bool:  # type: ignore[no-untyped-def]
    try:
        os.symlink(root / "nowhere", root / "probe")
    except (OSError, NotImplementedError):
        return False
    os.unlink(root / "probe")
    return True


def test_a_link_inside_the_library_never_sends_what_it_points_at(tmp_path):
    if not _can_link(tmp_path):
        pytest.skip("this filesystem cannot make links")
    desktop, phone, _ = paired(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "diary.json").write_bytes(b"private words of Winifred")
    (phone.store.root / "sources").mkdir(exist_ok=True)
    os.symlink(outside, phone.store.root / "sources" / "catalog", target_is_directory=True)
    os.symlink(tmp_path / "gone", phone.store.root / "worlds")  # a dangling link beside the roots
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.status != "incomplete", report  # a link outside the roots blocks nothing
    for path in desktop.store.root.rglob("*"):
        if path.is_file():
            assert b"Winifred" not in path.read_bytes(), path



def test_a_session_stays_bound_to_the_key_it_proved_when_the_owner_pairs_again(tmp_path, monkeypatch):
    desktop, phone, _ = paired(tmp_path)
    real = server_module.PeerServer._key
    swapped = []

    def key_then_repair(self, peer):  # type: ignore[no-untyped-def]
        found = real(self, peer)
        if not swapped:  # the owner rotates the key while this hello is being answered
            swapped.append(1)
            pair(phone.store, phone.authority, DESKTOP, new_pairing_key(), granted_by=OWNER)
        return found

    monkeypatch.setattr(server_module.PeerServer, "_key", key_then_repair)
    with serving(phone) as server:
        channel = _connect(desktop, server)  # proves the old key, which was current at its hello
        channel.send({"t": "snapshot"})
        with pytest.raises(PeerRefused) as refused:
            channel.recv()
        assert refused.value.code == "not_paired"
        channel.close()


def test_a_file_named_with_a_colon_stays_on_its_device_and_blocks_nothing(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    start_scene(phone)
    notes = phone.store.root / "sources" / "notes 10:30.txt"
    try:
        notes.write_bytes(b"a note")
    except OSError:
        pytest.skip("this filesystem cannot hold a colon in a name")
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.status == "synced", report
    assert not (desktop.store.root / "sources" / "notes 10:30.txt").exists()

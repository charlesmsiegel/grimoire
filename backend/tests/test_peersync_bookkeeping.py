"""028 over the transport: a sharded archive and marker, and dismissed kept versions, travel between
paired devices exactly as they behave in a local sync. Every library is synthetic (peersync_helpers'
desktop and phone, serving on loopback)."""

from __future__ import annotations

import json
import socket

import pytest

from grimoire.core.identity import InstanceRef
from grimoire.peersync import PeerRefused, read_pairing, transfer
from grimoire.peersync.wire import client_handshake
from grimoire.store import RecordEnvelope, etag
from grimoire.sync import bookkeeping, dismiss
from grimoire.sync import session as sync_session
from grimoire.sync.documents import render_dismissed

from peersync_helpers import DESKTOP, PHONE, Device, paired, serving

PAIR = (DESKTOP, PHONE)
LIMIT = 12_000  # one file holds about 30 paths at this cap; the library below has 120
WREN = InstanceRef("c3", "character", "wren")
WREN_PATH = "campaigns/c3/instances/character/wren.json"


def _put(device: Device, ref: InstanceRef, payload: dict) -> None:  # type: ignore[type-arg]
    current = device.store.root / f"campaigns/{ref.campaign_id}/instances/{ref.kind}/{ref.instance_id}.json"
    txn = device.store.transaction()
    txn.put(RecordEnvelope(ref, payload), etag(current.read_bytes()) if current.exists() else None)
    txn.commit(device.handle)


def _kept(device: Device) -> list[bytes]:
    return [path.read_bytes() for path in sorted((device.store.root / ".conflicts").rglob("*")) if path.is_file()]


def _conflict(tmp_path):  # type: ignore[no-untyped-def]
    """Wren reached both devices, then each edited it offline: a transfer keeps both versions."""
    desktop, phone, _ = paired(tmp_path)
    _put(desktop, WREN, {"hp": 1})
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
    _put(desktop, WREN, {"hp": 9})
    _put(phone, WREN, {"hp": 4})
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.status == "conflicts" and report.passes[-1].conflict(WREN_PATH).kept == {DESKTOP: True, PHONE: True}
    return desktop, phone


def test_a_sharded_archive_travels_and_the_next_transfer_has_nothing_to_do(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_session, "ARCHIVE_LIMIT", LIMIT)
    desktop, phone, _ = paired(tmp_path)
    txn = desktop.store.transaction()
    for number in range(120):
        txn.put(RecordEnvelope(InstanceRef("c9", "character", f"extra-{number:03d}"), {"n": number}), None)
    txn.commit(desktop.handle)
    with serving(phone) as server:
        first = transfer(desktop.store, desktop.authority, PHONE, server.address)
        again = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert first.status == again.status == "synced" and len(first.copied) == 120 and again.copied == ()
    for device, peer in ((desktop, PHONE), (phone, DESKTOP)):
        root = json.loads((device.store.root / f".sync/peers/{peer}/archive.json").read_bytes())
        assert root["format"] == "grimoire.sync.archive/2"
        archive = bookkeeping.parse("archive", bookkeeping.pair_files(device.store, "archive", peer), peer, PAIR)
        assert archive.state == "valid" and len(archive.entries) == 120


def test_a_dismissal_on_the_serving_device_is_honoured_and_forgotten_with_its_conflict(tmp_path):
    desktop, phone = _conflict(tmp_path)
    mine = (desktop.store.root / WREN_PATH).read_bytes()
    assert mine in _kept(phone)
    done = dismiss(phone.store, DESKTOP, WREN_PATH, etag(mine), phone.handle)
    for slot in done.slots:  # the person removes the copy they dismissed
        (phone.store.root / slot).unlink()
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    conflict = report.passes[-1].conflict(WREN_PATH)
    assert conflict.dismissed == (PHONE,) and conflict.kept == {DESKTOP: True}
    assert mine not in _kept(phone)
    # the phone settles on the desktop's version: the conflict ends, and its dismissal goes with it
    (phone.store.root / WREN_PATH).write_bytes(mine)
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.status == "synced" and report.conflicts == ()
    record = json.loads((phone.store.root / f".sync/peers/{DESKTOP}/dismissed.json").read_bytes())
    assert record["entries"] == {}


def test_a_peer_may_not_add_a_dismissal_for_the_device_it_writes_to(tmp_path):
    """Only this device decides what it no longer wants delivered; a peer could otherwise hide its
    own version of a conflict from it."""
    desktop, phone = _conflict(tmp_path)
    mine = (desktop.store.root / WREN_PATH).read_bytes()
    name = f"peers/{DESKTOP}/dismissed.json"
    body = render_dismissed(PAIR, {WREN_PATH: frozenset({etag(mine)})})
    with serving(phone) as server:
        pairing = read_pairing(desktop.store, PHONE)
        channel = client_handshake(socket.create_connection(server.address, timeout=10), DESKTOP, PHONE, pairing.key)
        channel.send({"t": "op", "op": "state", "name": name, "expected": None}, body)
        channel.send({"t": "commit", "step": "pending"})
        with pytest.raises(PeerRefused) as refused:
            channel.recv()
        assert refused.value.code == "invalid"
        channel.close()
    assert not (phone.store.root / ".sync" / name).exists()
    # and the phone still receives the desktop's later version as before
    _put(desktop, WREN, {"hp": 10})
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.passes[-1].conflict(WREN_PATH).dismissed == ()
    assert (desktop.store.root / WREN_PATH).read_bytes() in _kept(phone)

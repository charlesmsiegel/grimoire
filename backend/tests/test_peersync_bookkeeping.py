"""028 over the transport: a sharded archive and marker, and dismissed kept versions, travel between
paired devices exactly as they behave in a local sync, and a paired peer can write neither a shard
its root does not list nor a dismissal. Every library is synthetic (peersync_helpers' desktop and
phone, serving on loopback)."""

from __future__ import annotations

import json
import socket

import pytest

from grimoire.core.identity import InstanceRef
from grimoire.peersync import PeerRefused, read_pairing, transfer
from grimoire.peersync.wire import client_handshake
from grimoire.store import RecordEnvelope, etag
from grimoire.store.jsonfmt import render
from grimoire.sync import DismissRefused, bookkeeping, dismiss, resolve
from grimoire.sync import session as sync_session
from grimoire.sync.documents import KeptEntry, parse_keeps, render_keeps

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
        archive = bookkeeping.read_document(device.store, "archive", peer, PAIR)
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
    record = json.loads((phone.store.root / f".sync/peers/{DESKTOP}/keeps.json").read_bytes())
    assert record["entries"] == {}


def _raw_step(desktop, server, *ops: tuple[str, bytes]) -> None:  # type: ignore[no-untyped-def]
    """Send one step of ``.sync`` writes as a hostile but authenticated desktop would; it must be refused."""
    pairing = read_pairing(desktop.store, PHONE)
    channel = client_handshake(socket.create_connection(server.address, timeout=10), DESKTOP, PHONE, pairing.key)
    for name, body in ops:
        channel.send({"t": "op", "op": "state", "name": name, "expected": None}, body)
    channel.send({"t": "commit", "step": "pending"})
    with pytest.raises(PeerRefused) as refused:
        channel.recv()
    assert refused.value.code == "invalid"
    channel.close()


def _steps(desktop, server, *steps):  # type: ignore[no-untyped-def]
    """Send steps of raw ops (``(header, body)``) as an authenticated desktop would, on one connection;
    returns each step's reply, ``PeerRefused`` for a refused one."""
    pairing = read_pairing(desktop.store, PHONE)
    channel = client_handshake(socket.create_connection(server.address, timeout=10), DESKTOP, PHONE, pairing.key)
    replies = []
    for kind, ops in steps:
        for header, body in ops:
            channel.send({"t": "op", **header}, body)
        channel.send({"t": "commit", "step": kind})
        try:
            replies.append(channel.recv()[0])
        except PeerRefused as refused:
            replies.append(refused)
    channel.close()
    return replies


@pytest.mark.parametrize("forged", ["relabelled", "delivered again"])
def test_a_peer_may_never_label_a_version_resolve_held_here_as_delivered(tmp_path, forged):
    """The person picked the desktop's version, so the phone holds its own: perhaps the only copy. A
    hostile desktop may not record it as a version it delivered (so dismissable), neither by relabelling
    the hold nor by first writing the same bytes to a second slot of its own."""
    desktop, phone = _conflict(tmp_path)
    mine, theirs = (desktop.store.root / WREN_PATH).read_bytes(), (phone.store.root / WREN_PATH).read_bytes()
    resolve(desktop.store, phone.store, WREN_PATH, etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)},
            {DESKTOP: desktop.handle, PHONE: phone.handle})
    name = f"peers/{DESKTOP}/keeps.json"
    record = phone.store.root / ".sync" / name
    before = record.read_bytes()
    assert parse_keeps(before, PAIR).entries[WREN_PATH].held == {etag(theirs)}
    base = "{}:{}".format(*bookkeeping.read_document(phone.store, "archive", DESKTOP, PAIR).entries[WREN_PATH])
    claim = render_keeps(PAIR, {WREN_PATH: KeptEntry(base, frozenset({etag(theirs)}))})
    steps = [("pending", [({"op": "state", "name": name, "expected": etag(before)}, claim)])]
    if forged == "delivered again":
        steps.insert(0, ("data", [({"op": "keep", "path": WREN_PATH, "slot": 1}, theirs)]))
    with serving(phone) as server:
        replies = _steps(desktop, server, *steps)
    assert isinstance(replies[-1], PeerRefused) and replies[-1].code == "invalid"
    assert record.read_bytes() == before
    with pytest.raises(DismissRefused):
        dismiss(phone.store, DESKTOP, WREN_PATH, etag(theirs), phone.handle)
    assert theirs in _kept(phone)


@pytest.mark.parametrize("document", ["junk shards", "a listed shard missing"])
def test_a_peer_may_not_write_an_archive_that_is_not_valid(tmp_path, monkeypatch, document):
    """Every shard is one its root lists, yet the whole is no valid archive of this pair: refused before
    anything is staged, so no later plan ever reads (in full) a document it would then ignore."""
    desktop, phone, _ = paired(tmp_path)
    root = f"peers/{DESKTOP}/archive.json"
    if document == "junk shards":
        junk = render({"x": "a" * 1024})
        files = {f"peers/{DESKTOP}/archive.{prefix:x}.json": junk for prefix in range(16)}
        files[root] = render({"format": "grimoire.sync.archive/2", "devices": list(PAIR),
                              "shards": {f"{prefix:x}": etag(junk) for prefix in range(16)}})
    else:
        entries = {f"campaigns/c9/instances/character/extra-{number:03d}.json": ("agreed", etag(b"%d" % number))
                   for number in range(120)}
        files, width = bookkeeping.render_files("archive", DESKTOP, PAIR, entries, LIMIT)
        assert width > 0
        del files[max(name for name in files if name != root)]
    def on_phone() -> dict[str, bytes | None]:
        return {name: phone.store.read_sync_state(name) for name in files}
    before = on_phone()
    with serving(phone) as server:
        reply, = _steps(desktop, server, ("archive", [({"op": "state", "name": name, "expected": None}, body)
                                                       for name, body in files.items()]))
    assert isinstance(reply, PeerRefused) and reply.code == "invalid"
    assert on_phone() == before


@pytest.mark.parametrize("claim", ["a dismissal", "a kept version it lacks"])
def test_a_peer_may_not_dismiss_for_the_device_it_writes_to_nor_claim_a_keep(tmp_path, claim):
    """Only this device decides what it no longer wants delivered (a peer could otherwise hide its own
    version of a conflict from it), and only a version it holds kept can ever be dismissed here."""
    desktop, phone = _conflict(tmp_path)
    mine = (desktop.store.root / WREN_PATH).read_bytes()
    name = f"peers/{DESKTOP}/keeps.json"
    before = (phone.store.root / ".sync" / name).read_bytes()
    entry = parse_keeps(before, PAIR).entries[WREN_PATH]
    claimed = {
        "a dismissal": KeptEntry(entry.base, entry.delivered, frozenset({etag(mine)})),
        "a kept version it lacks": KeptEntry(entry.base, entry.delivered | {etag(b"never kept here")}),
    }[claim]
    with serving(phone) as server:
        _raw_step(desktop, server, (name, render_keeps(PAIR, {WREN_PATH: claimed})))
    assert (phone.store.root / ".sync" / name).read_bytes() == before
    # and the phone still receives the desktop's later version as before
    _put(desktop, WREN, {"hp": 10})
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.passes[-1].conflict(WREN_PATH).dismissed == ()
    assert (desktop.store.root / WREN_PATH).read_bytes() in _kept(phone)


def test_a_peer_may_not_plant_a_shard_no_root_of_its_step_lists(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    junk = render({"x": "a" * 1024})
    names = [f"peers/{DESKTOP}/archive.{prefix:02x}.json" for prefix in range(3)]
    with serving(phone) as server:
        _raw_step(desktop, server, *((name, junk) for name in names))
    assert not any((phone.store.root / ".sync" / name).exists() for name in names)


def test_a_shard_no_root_lists_is_never_read_nor_sent(tmp_path, monkeypatch):
    """A stale shard (here one that cannot even be read: a folder in its place) blocks nothing: only
    the shards a root lists are read, locally and for the snapshot, and a later write of its name
    still replaces it."""
    monkeypatch.setattr(sync_session, "ARCHIVE_LIMIT", LIMIT)
    desktop, phone, _ = paired(tmp_path)
    _put(desktop, WREN, {"hp": 1})
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
    (phone.store.root / f".sync/peers/{DESKTOP}/archive.ff.json").mkdir()  # unreadable, and unlisted
    stale = phone.store.root / f".sync/peers/{DESKTOP}/archive.3.json"
    stale.write_bytes(b"left over from an earlier width")  # unlisted while the archive is one file
    txn = desktop.store.transaction()
    for number in range(120):
        txn.put(RecordEnvelope(InstanceRef("c9", "character", f"extra-{number:03d}"), {"n": number}), None)
    txn.commit(desktop.handle)
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.status == "synced" and len(report.copied) == 120
    archive = bookkeeping.read_document(phone.store, "archive", DESKTOP, PAIR)
    assert archive.state == "valid" and len(archive.entries) == 121
    assert json.loads(stale.read_bytes())["shard"] == "3"  # overwritten as a listed shard of the new width

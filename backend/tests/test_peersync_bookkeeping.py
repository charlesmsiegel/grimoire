"""028 over the transport: a sharded archive and marker, and dismissed kept versions, travel between
paired devices exactly as they behave in a local sync, and a paired peer can write neither a shard
its root does not list nor a dismissal. Every library is synthetic (peersync_helpers' desktop and
phone, serving on loopback)."""

from __future__ import annotations

import errno
import json
import socket
from pathlib import Path

import pytest

from grimoire.core.identity import InstanceRef
from grimoire.peersync import PeerRefused, read_pairing, resolve_conflict, transfer
from grimoire.peersync import session as session_module
from grimoire.peersync.pairing import peer_principal
from grimoire.peersync.replay import StepBuffer
from grimoire.peersync.wire import client_handshake
from grimoire.store import RecordEnvelope, etag
from grimoire.store.jsonfmt import render
from grimoire.store.layout import conflict_parts
from grimoire.sync import DismissRefused, bookkeeping, dismiss, identify, resolve
from grimoire.sync import keeps as sync_keeps
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


def _base(device: Device) -> str:
    """The archive base of wren's conflict, as a sync reads it (and a dismissal records it)."""
    return "{}:{}".format(*bookkeeping.read_document(device.store, "archive", DESKTOP, PAIR).entries[WREN_PATH])


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


def test_a_dismissal_on_the_serving_device_after_its_snapshot_is_never_undone(tmp_path, monkeypatch):
    """The phone's copy of the desktop's version is gone, so a transfer plans to keep it again; the
    person dismisses it on the phone after the snapshot went out. The phone's record is no longer
    the one the plan read, so its data step is refused instead of writing the copy back, and the
    transfer's next pass plans from the dismissal."""
    desktop, phone = _conflict(tmp_path)
    mine = (desktop.store.root / WREN_PATH).read_bytes()
    for slot in (phone.store.root / ".conflicts").rglob("*"):
        if slot.is_file() and slot.read_bytes() == mine:
            slot.unlink()
    real, dismissed = session_module.send_step, []

    def dismiss_first(channel, step, reader):  # type: ignore[no-untyped-def]
        if step.kind == "data" and not dismissed:
            dismissed.append(dismiss(phone.store, DESKTOP, WREN_PATH, etag(mine), phone.handle).wrote)
        return real(channel, step, reader)
    monkeypatch.setattr(session_module, "send_step", dismiss_first)
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert dismissed == [True] and mine not in _kept(phone)
    assert report.passes[-1].conflict(WREN_PATH).dismissed == (PHONE,)  # the next pass read the dismissal


def test_a_keep_that_refills_a_recorded_slot_rewrites_the_same_record_over_the_transport(tmp_path):
    """As above with no dismissal: the data step keeps the desktop's version again, adding no delivery,
    and still guards the phone's record by rewriting its own bytes (compare-and-swap against what the
    plan read). The phone takes that unchanged record, and the copy stays the one it delivered."""
    desktop, phone = _conflict(tmp_path)
    mine = (desktop.store.root / WREN_PATH).read_bytes()
    for slot in (phone.store.root / ".conflicts").rglob("*"):
        if slot.is_file() and slot.read_bytes() == mine:
            slot.unlink()
    before = phone.store.read_sync_state(f"peers/{DESKTOP}/keeps.json")
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.status == "conflicts" and report.passes[-1].conflict(WREN_PATH) is not None, report
    assert mine in _kept(phone) and phone.store.read_sync_state(f"peers/{DESKTOP}/keeps.json") == before
    assert dismiss(phone.store, DESKTOP, WREN_PATH, etag(mine), phone.handle).wrote


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
    claim = render_keeps(PAIR, {WREN_PATH: KeptEntry(None, frozenset({etag(theirs)}))})
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


@pytest.mark.parametrize("reidentified", [False, True])
def test_a_keep_that_writes_nothing_never_makes_a_hold_delivered(tmp_path, reidentified):
    """A keep op of the hold's own bytes into the hold's own slot writes nothing (beside one real keep,
    so the step commits), so the step may not record those bytes as delivered. ``identify`` carries
    the hold into the new ID's record, so a re-identified device refuses it the same way."""
    desktop, phone = _conflict(tmp_path)
    mine, theirs = (desktop.store.root / WREN_PATH).read_bytes(), (phone.store.root / WREN_PATH).read_bytes()
    resolve(desktop.store, phone.store, WREN_PATH, etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)},
            {DESKTOP: desktop.handle, PHONE: phone.handle})
    pair = PAIR
    if reidentified:
        identify(phone.store, "phone-two", replace=True)
        pair = tuple(sorted((DESKTOP, "phone-two")))  # type: ignore[assignment]
    record = phone.store.root / f".sync/peers/{DESKTOP}/keeps.json"
    before = record.read_bytes()
    assert parse_keeps(before, pair).entries[WREN_PATH].held == {etag(theirs)}
    hold = "/".join(conflict_parts(tuple(WREN_PATH.split("/")), etag(theirs), 0))
    assert (phone.store.root / hold).read_bytes() == theirs
    buffer = StepBuffer(DESKTOP)
    for header, body in (({"op": "keep", "path": WREN_PATH, "slot": 0}, theirs),
                         ({"op": "keep", "path": "campaigns/c3/instances/character/other.json", "slot": 0},
                          render({"anything": 1})),
                         ({"op": "state", "name": f"peers/{DESKTOP}/keeps.json", "expected": etag(before)},
                          render_keeps(pair, {WREN_PATH: KeptEntry(None, frozenset({etag(theirs)}))}))):
        buffer.add({"t": "op", **header}, body)
    with pytest.raises(PeerRefused) as refused:
        buffer.commit(phone.store, phone.authority.handle_for(peer_principal(DESKTOP), trusted=True))
    assert refused.value.code == "invalid" and record.read_bytes() == before
    with pytest.raises(DismissRefused):
        dismiss(phone.store, DESKTOP, WREN_PATH, etag(theirs), phone.handle)
    assert theirs in _kept(phone)


@pytest.mark.parametrize("forged", ["relabelled", "relabelled over a damaged record", "dropped"])
def test_a_hold_whose_slot_cannot_be_read_is_never_given_up(tmp_path, monkeypatch, forged):
    """The phone's hold sits in slot 0, which fails with an I/O error while a step arrives that writes the
    same bytes to slot 1 and records them as delivered in place of the hold, or simply drops the hold.
    An unreadable slot may hold those bytes, so they are no first copy and the hold may not be
    dropped: the step is refused, and once the slot reads again the hold is still never dismissable.
    With the record damaged the hold is unknown, so only the first-copy rule stands between the step
    and a dismissable hold; with nothing delivered, only the rule on dropping a hold does."""
    desktop, phone = _conflict(tmp_path)
    mine, theirs = (desktop.store.root / WREN_PATH).read_bytes(), (phone.store.root / WREN_PATH).read_bytes()
    resolve(desktop.store, phone.store, WREN_PATH, etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)},
            {DESKTOP: desktop.handle, PHONE: phone.handle})
    keeps = phone.store.root / f".sync/peers/{DESKTOP}/keeps.json"
    hold = "/".join(conflict_parts(tuple(WREN_PATH.split("/")), etag(theirs), 0))
    assert parse_keeps(keeps.read_bytes(), PAIR).entries[WREN_PATH].held == {etag(theirs)}
    if forged == "relabelled over a damaged record":
        keeps.write_bytes(b"{not json")
    before = keeps.read_bytes()
    ops, real = phone.store._access.ops, phone.store._access.ops.read_bytes

    def read_bytes(path):  # type: ignore[no-untyped-def]
        if Path(path).as_posix().endswith(hold):
            raise OSError(errno.EIO, "simulated I/O error", str(path))
        return real(path)
    monkeypatch.setattr(ops, "read_bytes", read_bytes)
    state = {"op": "state", "name": f"peers/{DESKTOP}/keeps.json", "expected": etag(before)}
    if forged == "dropped":
        step = [(state, render_keeps(PAIR, {}))]
    else:
        step = [({"op": "keep", "path": WREN_PATH, "slot": 1}, theirs),
                (state, render_keeps(PAIR, {WREN_PATH: KeptEntry(_base(phone), frozenset({etag(theirs)}))}))]
    buffer = StepBuffer(DESKTOP)
    for header, body in step:
        buffer.add({"t": "op", **header}, body)
    with pytest.raises(PeerRefused) as refused:
        buffer.commit(phone.store, phone.authority.handle_for(peer_principal(DESKTOP), trusted=True))
    assert refused.value.code == "invalid" and keeps.read_bytes() == before
    monkeypatch.undo()  # the slot reads again
    with pytest.raises(DismissRefused):
        dismiss(phone.store, DESKTOP, WREN_PATH, etag(theirs), phone.handle)
    assert (phone.store.root / hold).read_bytes() == theirs


def test_a_transfer_that_ends_a_conflict_clears_its_record_before_either_archive(tmp_path, monkeypatch):
    """The phone takes the desktop's version by hand; the next transfer ends the conflict. Its keeps
    steps travel ahead of both archive steps and the phone accepts them, so a transfer cut off after an
    archive step never leaves a kept copy dismissable on either device."""
    desktop, phone = _conflict(tmp_path)
    former = (phone.store.root / WREN_PATH).read_bytes()
    (phone.store.root / WREN_PATH).write_bytes((desktop.store.root / WREN_PATH).read_bytes())
    real, sent = session_module.send_step, []

    def record(channel, step, reader):  # type: ignore[no-untyped-def]
        sent.append(step.kind)
        return real(channel, step, reader)
    monkeypatch.setattr(session_module, "send_step", record)
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
    assert "keeps" in sent and sent.index("keeps") < sent.index("archive")
    assert WREN_PATH not in parse_keeps((phone.store.root / f".sync/peers/{DESKTOP}/keeps.json").read_bytes(), PAIR).entries
    with pytest.raises(DismissRefused):
        dismiss(desktop.store, PHONE, WREN_PATH, etag(former), desktop.handle)
    assert former in _kept(desktop)


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
        "a dismissal": KeptEntry(_base(phone), entry.delivered, frozenset({etag(mine)})),
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


# -- resolving over the transport holds the replaced version, exactly as a local resolve does -----

def _held(device: Device, peer: str) -> KeptEntry:
    """Wren's entry in ``device``'s record of its pair with ``peer``."""
    pair = tuple(sorted((device.name, peer)))
    return parse_keeps(device.store.read_sync_state(f"peers/{peer}/keeps.json"), pair).entries[WREN_PATH]  # type: ignore[arg-type]


@pytest.mark.parametrize("receiver", [PHONE, DESKTOP])
def test_a_conflict_resolved_over_the_transport_holds_the_replaced_version_on_its_receiver(tmp_path, receiver):
    """The desktop resolves wren against the phone over the connection, choosing either version: the
    device that receives keeps its own replaced version held (the serving phone included), so it is
    never dismissable: not now, not after the next transfer, and not once the other device comes back
    to those very bytes and keep-both finds them standing in the hold's slot."""
    desktop, phone = _conflict(tmp_path)
    devices = {DESKTOP: desktop, PHONE: phone}
    versions = {name: (device.store.root / WREN_PATH).read_bytes() for name, device in devices.items()}
    winner = DESKTOP if receiver == PHONE else PHONE
    held_on, replaced = devices[receiver], versions[receiver]
    with serving(phone) as server:
        report = resolve_conflict(desktop.store, desktop.authority, PHONE, server.address, WREN_PATH,
                                  etag(versions[winner]), {name: etag(data) for name, data in versions.items()})
    assert (report.status, report.receiver) == ("resolved", receiver), report
    assert _held(held_on, winner).held == {etag(replaced)} and etag(replaced) not in _held(held_on, winner).delivered
    with pytest.raises(DismissRefused):
        dismiss(held_on.store, winner, WREN_PATH, etag(replaced), held_on.handle)
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
    with pytest.raises(DismissRefused):
        dismiss(held_on.store, winner, WREN_PATH, etag(replaced), held_on.handle)
    # a new conflict: the winner comes back to the replaced bytes while the receiver edits on
    (devices[winner].store.root / WREN_PATH).write_bytes(replaced)
    _put(held_on, WREN, {"hp": 12})
    for _ in range(2):
        with serving(phone) as server:
            report = transfer(desktop.store, desktop.authority, PHONE, server.address)
        assert report.passes[-1].conflict(WREN_PATH).kept[receiver]
    entry = _held(held_on, winner)
    assert entry.held == {etag(replaced)} and etag(replaced) not in entry.delivered
    with pytest.raises(DismissRefused):
        dismiss(held_on.store, winner, WREN_PATH, etag(replaced), held_on.handle)
    assert replaced in _kept(held_on)


class _PowerLoss(Exception):
    """The receiving device's process dies here: nothing after it runs, and the next open recovers."""


@pytest.mark.parametrize("receiver", [PHONE, DESKTOP])
def test_a_hold_over_a_delivered_copy_survives_recovery_that_loses_the_staged_record(tmp_path, monkeypatch,
                                                                                      receiver):
    """The receiver's own version is bytes the other device once delivered there (the person copied the
    kept file into place), and the other device has moved on to a newer version. The desktop resolves
    over the connection, keeping that newer version, so the receiver (the serving phone, or the desktop
    itself) holds those bytes in the slot that already holds them. Power is lost on the receiver once
    the chosen version is in place, and any kept-version record staged with it is gone before the app
    opens again (010's recovery then finishes the commit without it). The hold is still never
    dismissable there: it is the only copy."""
    desktop, phone = _conflict(tmp_path)
    devices = {DESKTOP: desktop, PHONE: phone}
    winner = DESKTOP if receiver == PHONE else PHONE
    delivered = (devices[winner].store.root / WREN_PATH).read_bytes()
    (devices[receiver].store.root / WREN_PATH).write_bytes(delivered)
    _put(devices[winner], WREN, {"hp": 12})
    newer = (devices[winner].store.root / WREN_PATH).read_bytes()
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "conflicts"
    entry = _held(devices[receiver], winner)
    assert etag(delivered) in entry.delivered and entry.base is not None
    assert _kept(devices[receiver]).count(delivered) == 1
    ops, real = devices[receiver].store._access.ops, devices[receiver].store._access.ops.replace

    def replace(source, target):  # type: ignore[no-untyped-def]
        real(source, target)
        if Path(target).as_posix().endswith(WREN_PATH) and ".txn" in Path(source).parts:
            raise _PowerLoss("power lost after the chosen version was published")
    monkeypatch.setattr(ops, "replace", replace)
    chosen = {winner: etag(newer), receiver: etag(delivered)}
    with serving(phone) as server:
        if receiver == PHONE:  # the serving phone's connection dies with it
            report = resolve_conflict(desktop.store, desktop.authority, PHONE, server.address, WREN_PATH,
                                      etag(newer), chosen)
            assert report.status == "incomplete", report
        else:
            with pytest.raises(_PowerLoss):
                resolve_conflict(desktop.store, desktop.authority, PHONE, server.address, WREN_PATH, etag(newer), chosen)
    monkeypatch.undo()
    devices[receiver].close()
    staging = devices[receiver].store.root / ".txn"
    for staged in json.loads((staging / "journal.json").read_bytes())["entries"]:
        if Path(staged["target"]).name == "keeps.json":  # a record staged by that commit is lost
            (staging / staged["temp"]).unlink(missing_ok=True)
    held_on = devices[receiver] = Device.reopen(tmp_path, receiver)
    assert (held_on.store.root / WREN_PATH).read_bytes() == newer and delivered in _kept(held_on)
    with pytest.raises(DismissRefused):
        dismiss(held_on.store, winner, WREN_PATH, etag(delivered), held_on.handle)
    for _ in range(2):  # the next transfers record the agreement; the hold stays held
        with serving(devices[PHONE]) as server:
            transfer(devices[DESKTOP].store, devices[DESKTOP].authority, PHONE, server.address)
    with pytest.raises(DismissRefused):
        dismiss(held_on.store, winner, WREN_PATH, etag(delivered), held_on.handle)
    assert delivered in _kept(held_on) and etag(delivered) in _held(held_on, winner).held


def test_a_hold_on_the_serving_device_takes_its_bytes_out_of_its_other_pairs_deliveries(tmp_path):
    """The phone is paired with a laptop too, whose record there (written as its sync would) delivered the
    very bytes the phone holds when the desktop's resolve replaces them. The phone drops them from that
    record itself, in a commit of its own just before the hold's, as a local resolve does: the desktop
    never sees the phone's records of its other pairs, and may write none."""
    desktop, phone = _conflict(tmp_path)
    mine, theirs = (desktop.store.root / WREN_PATH).read_bytes(), (phone.store.root / WREN_PATH).read_bytes()
    other = phone.store.root / ".sync/peers/laptop/keeps.json"
    other.parent.mkdir()
    other.write_bytes(render_keeps(("laptop", PHONE), {WREN_PATH: KeptEntry(None, frozenset({etag(theirs)}))}))
    with serving(phone) as server:
        report = resolve_conflict(desktop.store, desktop.authority, PHONE, server.address, WREN_PATH, etag(mine),
                                  {DESKTOP: etag(mine), PHONE: etag(theirs)})
    assert (report.status, report.receiver) == ("resolved", PHONE), report
    assert json.loads(other.read_bytes())["entries"] == {} and _held(phone, DESKTOP).held == {etag(theirs)}
    for peer in (DESKTOP, "laptop"):
        with pytest.raises(DismissRefused):
            dismiss(phone.store, peer, WREN_PATH, etag(theirs), phone.handle)
    assert theirs in _kept(phone)


def test_a_hold_the_grants_deny_leaves_the_serving_devices_other_pairs_untouched(tmp_path):
    """As above, but the phone's owner withdraws campaigns from the desktop once its hold is recorded. The
    hold's step is denied before the phone touches its laptop record: otherwise a peer the path denies
    could, request after request, leave the phone's copies another pair delivered never dismissable."""
    desktop, phone = _conflict(tmp_path)
    mine, theirs = (desktop.store.root / WREN_PATH).read_bytes(), (phone.store.root / WREN_PATH).read_bytes()
    other = phone.store.root / ".sync/peers/laptop/keeps.json"
    other.parent.mkdir()
    other.write_bytes(render_keeps(("laptop", PHONE), {WREN_PATH: KeptEntry(None, frozenset({etag(theirs)}))}))
    laptop = other.read_bytes()
    name = f"peers/{DESKTOP}/keeps.json"
    before = phone.store.read_sync_state(name)
    entry = parse_keeps(before, PAIR).entries[WREN_PATH]
    record = ({"op": "state", "name": name, "expected": etag(before)},
              render_keeps(PAIR, {WREN_PATH: KeptEntry(entry.base, entry.delivered, frozenset(), frozenset({etag(theirs)}))}))
    with serving(phone) as server:
        channel = _resolving(desktop, server, WREN_PATH, mine, {DESKTOP: mine, PHONE: theirs})
        assert _send(channel, record)["t"] == "committed"
        for grant in phone.authority.active_grants():
            if grant.principal == peer_principal(DESKTOP) and str(grant.scope) == "campaigns":
                phone.authority.revoke_grant(grant.grant_id)
        refused = _send(channel, ({"op": "put", "path": WREN_PATH, "expected": etag(theirs)}, mine),
                        ({"op": "keep", "path": WREN_PATH, "slot": 0, "hold": True}, theirs))
        assert isinstance(refused, PeerRefused) and refused.code == "denied", refused
        channel.close()
    assert other.read_bytes() == laptop and (phone.store.root / WREN_PATH).read_bytes() == theirs


def test_the_serving_device_takes_no_kept_version_record_past_the_cap(tmp_path, monkeypatch):
    """This device never writes a record past ``keeps.RECORD_LIMIT`` (``keeps.fit`` leaves excess
    deliveries unrecorded), so it takes none from a peer either: the hold's record step is refused
    whole and the record stays as it was. Under the cap the same step is taken."""
    desktop, phone = _conflict(tmp_path)
    mine, theirs = (desktop.store.root / WREN_PATH).read_bytes(), (phone.store.root / WREN_PATH).read_bytes()
    name = f"peers/{DESKTOP}/keeps.json"
    before = phone.store.read_sync_state(name)
    entry = parse_keeps(before, PAIR).entries[WREN_PATH]
    record = ({"op": "state", "name": name, "expected": etag(before)},
              render_keeps(PAIR, {WREN_PATH: KeptEntry(entry.base, entry.delivered, frozenset(), frozenset({etag(theirs)}))}))
    monkeypatch.setattr(sync_keeps, "RECORD_LIMIT", len(record[1]) - 1)
    with serving(phone) as server:
        channel = _resolving(desktop, server, WREN_PATH, mine, {DESKTOP: mine, PHONE: theirs})
        refused = _send(channel, record)
        assert isinstance(refused, PeerRefused) and refused.code == "invalid", refused
        assert phone.store.read_sync_state(name) == before
        monkeypatch.setattr(sync_keeps, "RECORD_LIMIT", len(record[1]))
        assert _send(channel, record)["t"] == "committed"
        channel.close()


def test_a_chosen_version_whose_kept_file_cannot_be_read_is_not_admitted(tmp_path, monkeypatch):
    """The phone keeps the desktop's version beside wren, and that file fails with an I/O error when the
    desktop asks the phone to resolve keeping it. A file that cannot be read is unknown, never proof that
    the phone ever had those bytes: the request is refused before anything is sent, and admitted once
    the file reads again."""
    desktop, phone = _conflict(tmp_path)
    mine, theirs = (desktop.store.root / WREN_PATH).read_bytes(), (phone.store.root / WREN_PATH).read_bytes()
    slot = "/".join(conflict_parts(tuple(WREN_PATH.split("/")), etag(mine), 0))
    assert (phone.store.root / slot).read_bytes() == mine
    ops, real = phone.store._access.ops, phone.store._access.ops.read_bytes

    def read_bytes(path):  # type: ignore[no-untyped-def]
        if Path(path).as_posix().endswith(slot):
            raise OSError(errno.EIO, "simulated I/O error", str(path))
        return real(path)
    monkeypatch.setattr(ops, "read_bytes", read_bytes)
    expected = {DESKTOP: etag(mine), PHONE: etag(theirs)}
    with serving(phone) as server:
        report = resolve_conflict(desktop.store, desktop.authority, PHONE, server.address, WREN_PATH, etag(mine), expected)
    assert report.status == "refused" and "unknown_version" in report.reason, report
    assert (phone.store.root / WREN_PATH).read_bytes() == theirs
    monkeypatch.undo()  # the file reads again
    with serving(phone) as server:
        report = resolve_conflict(desktop.store, desktop.authority, PHONE, server.address, WREN_PATH, etag(mine), expected)
    assert (report.status, report.receiver) == ("resolved", PHONE), report


def _resolving(desktop: Device, server, path: str, keep: bytes, expected: dict[str, bytes]):  # type: ignore[no-untyped-def]
    """An authenticated desktop's connection with a resolve request made (its snapshot read to the end)."""
    pairing = read_pairing(desktop.store, PHONE)
    channel = client_handshake(socket.create_connection(server.address, timeout=10), DESKTOP, PHONE, pairing.key)
    channel.send({"t": "resolve", "path": path, "keep": etag(keep),
                  "expected": {device: etag(data) for device, data in expected.items()}})
    while channel.recv()[0]["t"] != "end":
        pass
    return channel


def _send(channel, *ops: tuple[dict, bytes]):  # type: ignore[no-untyped-def,type-arg]
    """One step of raw ops on ``channel``: its reply, or ``PeerRefused``."""
    for header, body in ops:
        channel.send({"t": "op", **header}, body)
    channel.send({"t": "commit", "step": "resolve"})
    try:
        return channel.recv()[0]
    except PeerRefused as refused:
        return refused


@pytest.mark.parametrize("forged", ["unrecorded", "recorded in the hold's own step", "recorded as delivered",
                                    "another version held", "in a sync step"])
def test_the_serving_device_takes_a_resolves_hold_only_recorded_before_and_only_its_own(tmp_path, forged):
    """The person picked the desktop's version, so the phone holds its own. The hold's record must land
    in a step of its own before the step that writes the hold, as a local resolve commits it: staged in
    the hold's own commit, 010's recovery could finish that commit without it. That record step may
    hold nothing but the phone's own current version: an unrecorded hold could later be dismissed as a
    delivered keep, and a forged one would keep a delivered version from ever being dismissed. No
    sync step holds anything."""
    desktop, phone = _conflict(tmp_path)
    mine, theirs = (desktop.store.root / WREN_PATH).read_bytes(), (phone.store.root / WREN_PATH).read_bytes()
    name = f"peers/{DESKTOP}/keeps.json"
    before = phone.store.read_sync_state(name)
    entry = parse_keeps(before, PAIR).entries[WREN_PATH]  # the desktop's version, delivered by keep-both
    assert entry.delivered == {etag(mine)} and entry.held == frozenset()

    def record(delivered: set[str], held: set[str]) -> tuple[dict, bytes]:  # type: ignore[type-arg]
        return ({"op": "state", "name": name, "expected": etag(before)},
                render_keeps(PAIR, {WREN_PATH: KeptEntry(entry.base, frozenset(delivered), frozenset(), frozenset(held))}))
    put = ({"op": "put", "path": WREN_PATH, "expected": etag(theirs)}, mine)
    hold = ({"op": "keep", "path": WREN_PATH, "slot": 0, "hold": True}, theirs)
    honest = record({etag(mine)}, {etag(theirs)})
    step = {"unrecorded": [put, hold],
            "recorded in the hold's own step": [put, hold, honest],
            "recorded as delivered": [record({etag(mine), etag(theirs)}, set())],
            "another version held": [record(set(), {etag(theirs), etag(mine)})],
            "in a sync step": [record(set(), {etag(mine)})]}[forged]
    untouched = {path: (phone.store.root / path).read_bytes() for path in (WREN_PATH, f".sync/{name}")}
    with serving(phone) as server:
        if forged == "in a sync step":
            _raw_step(desktop, server, (name, step[0][1]))
        else:
            channel = _resolving(desktop, server, WREN_PATH, mine, {DESKTOP: mine, PHONE: theirs})
            refused = _send(channel, *step)
            assert isinstance(refused, PeerRefused) and refused.code == "invalid", refused
            assert {path: (phone.store.root / path).read_bytes() for path in untouched} == untouched
            # the hold recorded as resolve records it, on its own and first, then the hold: both taken
            assert _send(channel, honest)["t"] == "committed"
            assert parse_keeps(phone.store.read_sync_state(name), PAIR).entries[WREN_PATH].held == {etag(theirs)}
            assert _send(channel, put, hold)["t"] == "committed"
            channel.close()
    if forged == "in a sync step":  # the desktop's version stays what keep-both delivered: dismissable
        assert phone.store.read_sync_state(name) == before
        assert dismiss(phone.store, DESKTOP, WREN_PATH, etag(mine), phone.handle).wrote
        return
    assert (phone.store.root / WREN_PATH).read_bytes() == mine
    assert parse_keeps(phone.store.read_sync_state(name), PAIR).entries[WREN_PATH].held == {etag(theirs)}
    with pytest.raises(DismissRefused):
        dismiss(phone.store, DESKTOP, WREN_PATH, etag(theirs), phone.handle)
    assert theirs in _kept(phone)


@pytest.mark.parametrize("sent", ["in one step", "record first", "already recorded"])
def test_a_device_that_keeps_its_own_version_never_holds_its_live_bytes(tmp_path, sent):
    """The desktop asks the phone to resolve keeping the phone's own version, so the phone is the winner
    and the desktop receives. It then sends a hold of the phone's live bytes with a record calling them
    held: in one step, the record first, or the hold alone once an abandoned resolve the phone was to
    receive left that record. Nothing at the path is replaced, so nothing is held: the hold would only
    put a copy of the live record under ``.conflicts`` that is never dismissable. Refused."""
    desktop, phone = _conflict(tmp_path)
    mine, theirs = (desktop.store.root / WREN_PATH).read_bytes(), (phone.store.root / WREN_PATH).read_bytes()
    name = f"peers/{DESKTOP}/keeps.json"
    before = phone.store.read_sync_state(name)
    entry = parse_keeps(before, PAIR).entries[WREN_PATH]
    record = ({"op": "state", "name": name, "expected": etag(before)},
              render_keeps(PAIR, {WREN_PATH: KeptEntry(entry.base, entry.delivered, frozenset(), frozenset({etag(theirs)}))}))
    hold = ({"op": "keep", "path": WREN_PATH, "slot": 0, "hold": True}, theirs)
    if sent == "already recorded":  # a resolve keeping the desktop's version recorded the hold, then went away
        with serving(phone) as server:
            channel = _resolving(desktop, server, WREN_PATH, mine, {DESKTOP: mine, PHONE: theirs})
            assert _send(channel, record)["t"] == "committed"
            channel.close()
    untouched = _kept(phone), phone.store.read_sync_state(name), (phone.store.root / WREN_PATH).read_bytes()
    with serving(phone) as server:
        channel = _resolving(desktop, server, WREN_PATH, theirs, {DESKTOP: mine, PHONE: theirs})
        steps = {"in one step": [[hold, record]], "record first": [[record], [hold]], "already recorded": [[hold]]}[sent]
        for ops in steps:
            refused = _send(channel, *ops)
            assert isinstance(refused, PeerRefused) and refused.code == "invalid", refused
        channel.close()
    assert (_kept(phone), phone.store.read_sync_state(name), (phone.store.root / WREN_PATH).read_bytes()) == untouched


@pytest.mark.parametrize("step", ["keeps", "holds"])
def test_only_a_step_named_holds_may_hold_this_devices_own_version(tmp_path, step):
    """028: a sync's holds step (its record alone, before the data commit that deletes) may record this
    device's own current version as held; any other keeps-only step may not, though it looks the same."""
    desktop, phone, _ = paired(tmp_path)
    _put(desktop, WREN, {"hp": 1})
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
    own = etag((phone.store.root / WREN_PATH).read_bytes())
    record = phone.store.root / f".sync/peers/{DESKTOP}/keeps.json"
    before = record.read_bytes() if record.exists() else None
    buffer = StepBuffer(DESKTOP)
    buffer.add({"t": "op", "op": "state", "name": f"peers/{DESKTOP}/keeps.json",
                "expected": None if before is None else etag(before)},
               render_keeps(PAIR, {WREN_PATH: KeptEntry(None, frozenset(), held=frozenset({own}))}))
    handle = phone.authority.handle_for(peer_principal(DESKTOP), trusted=True)
    if step == "keeps":
        with pytest.raises(PeerRefused) as refused:
            buffer.commit(phone.store, handle, step=step)
        assert refused.value.code == "invalid"
        assert (record.read_bytes() if record.exists() else None) == before
    else:
        buffer.commit(phone.store, handle, step=step)
        assert parse_keeps(record.read_bytes(), PAIR).entries[WREN_PATH].held == {own}

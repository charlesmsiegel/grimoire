"""028: ending a conflict over the peer connection. Either paired device chooses the version to keep
for one conflicted path, with 019's ``resolve`` guarantees, and the next transfer finds the
conflict gone on both.

Every library here is synthetic and lives under the test's temporary directory; every server
listens on loopback, one of them in its own process.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from grimoire.core.identity import SourceRef
from grimoire.peersync import PeerRefused, read_pairing, resolve_conflict, transfer, unpair
from grimoire.peersync import resolving as resolving_module
from grimoire.peersync import server as server_module
from grimoire.peersync.pairing import peer_principal
from grimoire.peersync.replay import StepBuffer
from grimoire.peersync.wire import Channel, client_handshake, server_handshake
from grimoire.store import RecordEnvelope, SourceManifest, etag
from grimoire.sync.documents import pending_name

from chat_fixtures import CARDS
from peersync_helpers import DESKTOP, PHONE, Device, paired, serving, start_scene
from store_helpers import OWNER

BACKEND = Path(__file__).resolve().parents[1]
DESKTOP_LABEL, PHONE_LABEL = "Saltmarch docks", "The night crossing"


def _tree(root: Path) -> dict[str, bytes]:
    """Every file a sync or resolve could touch on a library, by path, sync bookkeeping included."""
    found = {}
    for top in ("sources", "campaigns", "assets", ".conflicts", ".sync"):
        for path in sorted((root / top).rglob("*")):
            if path.is_file():
                found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


def _kept(root: Path) -> list[bytes]:
    return [data for path, data in _tree(root).items() if path.startswith(".conflicts/")]


def _relabel(device: Device, chat, label: str) -> None:  # type: ignore[no-untyped-def]
    _, current = device.chats.read_chat(chat)
    device.chats.set_label(chat, label, expected=current).commit()


class Conflict:
    """A chat both devices relabelled offline, reported by a transfer and kept on both."""

    def __init__(self, root: Path) -> None:
        self.desktop, self.phone, _ = paired(root)
        started, _ = start_scene(self.desktop)
        self.chat = started.chat
        self.path = f"campaigns/{self.chat.campaign_id}/chats/{self.chat.chat_id}.json"
        with serving(self.phone) as server:
            assert transfer(self.desktop.store, self.desktop.authority, PHONE, server.address).status == "synced"
            _relabel(self.desktop, self.chat, DESKTOP_LABEL)
            _relabel(self.phone, self.chat, PHONE_LABEL)
            report = transfer(self.desktop.store, self.desktop.authority, PHONE, server.address)
        assert report.status == "conflicts" and report.passes[-1].conflict(self.path) is not None, report
        self.versions = {DESKTOP: self.bytes(self.desktop), PHONE: self.bytes(self.phone)}
        self.expected = {device: etag(data) for device, data in self.versions.items()}

    def bytes(self, device: Device) -> bytes:
        return (device.store.root / self.path).read_bytes()

    def labels(self) -> tuple[str, str]:
        return self.desktop.chats.read_chat(self.chat)[0].label, self.phone.chats.read_chat(self.chat)[0].label

    def resolve(self, server, keep: str, *, start: Device | None = None, expected=None):  # type: ignore[no-untyped-def]
        start = start or self.desktop
        other = PHONE if start is self.desktop else DESKTOP
        return resolve_conflict(start.store, start.authority, other, server.address, self.path,
                                self.expected[keep] if keep in self.expected else keep,
                                self.expected if expected is None else expected)

    def sync_again(self):  # type: ignore[no-untyped-def]
        with serving(self.phone) as server:
            return transfer(self.desktop.store, self.desktop.authority, PHONE, server.address)


# -- the proof: either device ends a conflict, and the next transfer finds it gone on both -------

@pytest.mark.parametrize("keep", [DESKTOP, PHONE])
def test_resolving_from_the_starting_device_ends_the_conflict_on_both(tmp_path, keep):
    case = Conflict(tmp_path)
    receiver = PHONE if keep == DESKTOP else DESKTOP
    with serving(case.phone) as server:
        report = case.resolve(server, keep)
    assert (report.status, report.reason, report.receiver) == ("resolved", None, receiver), report
    chosen = DESKTOP_LABEL if keep == DESKTOP else PHONE_LABEL
    assert case.labels() == (chosen, chosen)
    # the receiver's own version is kept beside it, never lost
    assert case.versions[receiver] in _kept((case.phone if receiver == PHONE else case.desktop).store.root)
    again = case.sync_again()
    assert again.status == "synced" and again.conflicts == () and again.copied == (), again
    assert case.labels() == (chosen, chosen)


def test_resolving_from_the_other_device_against_a_server_in_its_own_process(tmp_path):
    case = Conflict(tmp_path)
    case.desktop.close()  # the desktop's library is now served by its own process
    env = {**os.environ, "PYTHONPATH": str(BACKEND / "src")}
    serve = subprocess.Popen(
        [sys.executable, "-m", "grimoire.peersync", "serve", str(tmp_path / DESKTOP), "--owner", OWNER, "--once"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=BACKEND)
    try:
        line = serve.stdout.readline().split()  # type: ignore[union-attr]
        assert line[:1] == ["listening"], serve.stderr.read()  # type: ignore[union-attr]
        report = resolve_conflict(case.phone.store, case.phone.authority, DESKTOP, (line[1], int(line[2])),
                                  case.path, case.expected[PHONE], case.expected)
        assert serve.wait(30) == 0, serve.stderr.read()  # type: ignore[union-attr]
    finally:
        serve.kill()
        serve.wait()
    assert (report.status, report.receiver) == ("resolved", DESKTOP), report
    case.desktop = Device.reopen(tmp_path, DESKTOP)
    assert case.labels() == (PHONE_LABEL, PHONE_LABEL)
    assert case.versions[DESKTOP] in _kept(case.desktop.store.root)
    again = case.sync_again()
    assert again.status == "synced" and again.conflicts == () and again.copied == (), again


def test_a_definition_revision_is_replaced_on_the_peer_with_its_old_bytes_held(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    start_scene(desktop)
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
    for device, bio in ((desktop, "a smuggler turned captain"), (phone, "a smuggler turned pirate")):
        txn = device.store.transaction()  # each adds a revision nothing pins yet, offline
        txn.put(RecordEnvelope(SourceRef(CARDS, "character", "mara", "r9"), {"bio": bio}, name="Mara"), None)
        source = device.store.read_source(CARDS)
        manifest = source.manifest
        txn.put(SourceManifest(manifest.source, manifest.owner, manifest.title,
                               {"character/mara": "r1", "character/anya": "r1"}, manifest.links,
                               extra=manifest.extra), source.etag)
        txn.commit(device.handle)
    path = "sources/catalog/cards/_source/records/character/mara/r9.json"
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
        assert report.status == "conflicts" and report.passes[-1].conflict(path) is not None, report
        mine, theirs = (desktop.store.root / path).read_bytes(), (phone.store.root / path).read_bytes()
        resolved = resolve_conflict(desktop.store, desktop.authority, PHONE, server.address, path, etag(mine),
                                    {DESKTOP: etag(mine), PHONE: etag(theirs)})
        assert resolved.status == "resolved", resolved
        assert (phone.store.root / path).read_bytes() == mine and theirs in _kept(phone.store.root)
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"


# -- what is refused, with nothing written on either device --------------------------------------

@pytest.mark.parametrize("changed", [DESKTOP, PHONE])
def test_a_stale_expected_etag_is_refused_and_nothing_changes(tmp_path, changed):
    case = Conflict(tmp_path)
    device = case.desktop if changed == DESKTOP else case.phone
    _relabel(device, case.chat, "Changed again")  # after the user looked at the conflict
    before = _tree(case.desktop.store.root), _tree(case.phone.store.root)
    with serving(case.phone) as server:
        report = case.resolve(server, DESKTOP if changed == PHONE else PHONE)
    assert report.status == "refused" and "changed" in report.reason, report
    assert (_tree(case.desktop.store.root), _tree(case.phone.store.root)) == before


def test_choosing_a_deletion_is_refused(tmp_path):
    case = Conflict(tmp_path)
    (case.desktop.store.root / case.path).unlink()  # the desktop's user deletes the chat by hand
    with serving(case.phone) as server:
        report = transfer(case.desktop.store, case.desktop.authority, PHONE, server.address)
        assert report.passes[-1].conflict(case.path) is not None, report
        before = _tree(case.desktop.store.root), _tree(case.phone.store.root)
        expected = {DESKTOP: None, PHONE: case.expected[PHONE]}
        for keep in (None, etag(b"")):  # the absent version, or an etag no device holds
            refused = case.resolve(server, keep, expected=expected)  # type: ignore[arg-type]
            assert refused.status == "refused" and "ResolveRefused" in refused.reason, refused
        assert (_tree(case.desktop.store.root), _tree(case.phone.store.root)) == before
        # keeping the version that still exists restores it
        assert case.resolve(server, PHONE, expected=expected).status == "resolved"
    assert case.bytes(case.desktop) == case.versions[PHONE]


def test_a_pending_marker_the_peer_ignores_refuses_the_resolve(tmp_path):
    case = Conflict(tmp_path)
    marker = case.phone.store.root / ".sync" / Path(pending_name(DESKTOP))
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(b"{not a marker")
    before = _tree(case.desktop.store.root), _tree(case.phone.store.root)
    with serving(case.phone) as server:
        report = case.resolve(server, DESKTOP)
    assert report.status == "refused" and "ignored" in report.reason, report
    assert (_tree(case.desktop.store.root), _tree(case.phone.store.root)) == before


def test_the_peer_cannot_resolve_into_what_its_grants_do_not_reach(tmp_path):
    case = Conflict(tmp_path)
    for grant in case.phone.authority.active_grants():  # the phone's owner withdraws campaigns from the desktop
        if grant.principal == peer_principal(DESKTOP) and str(grant.scope) == "campaigns":
            case.phone.authority.revoke_grant(grant.grant_id)
    before = _tree(case.phone.store.root)
    with serving(case.phone) as server:
        report = case.resolve(server, DESKTOP)
    assert report.status == "refused" and "not authorized" in report.reason, report
    assert _tree(case.phone.store.root) == before
    assert case.labels() == (DESKTOP_LABEL, PHONE_LABEL)


def test_unpairing_on_the_starting_device_mid_resolve_sends_nothing_more(tmp_path, monkeypatch):
    case = Conflict(tmp_path)
    real = resolving_module.receive_mirror

    def unpair_once_mirrored(*args, **kwargs):  # type: ignore[no-untyped-def]
        mirror = real(*args, **kwargs)
        unpair(case.desktop.store, case.desktop.authority, PHONE)  # the owner unpairs a lost phone
        return mirror

    monkeypatch.setattr(resolving_module, "receive_mirror", unpair_once_mirrored)
    before = _tree(case.phone.store.root)
    with serving(case.phone) as server:
        report = case.resolve(server, DESKTOP)
    assert (report.status, report.reason) == ("incomplete", "not_paired"), report
    assert _tree(case.phone.store.root) == before


# -- a resolve the connection drops ----------------------------------------------------------------

@pytest.mark.parametrize("step,landed", [(1, False), (1, True), (2, True), (3, True)])
def test_a_dropped_connection_mid_resolve_loses_nothing_and_the_devices_converge(tmp_path, monkeypatch, step, landed):
    case = Conflict(tmp_path)
    commits = []

    class Dropping(StepBuffer):
        """The phone's side of the resolve's ``step``-th commit: the cable comes out before or after it lands."""

        def commit(self, store, handle):  # type: ignore[no-untyped-def]
            commits.append(1)
            if len(commits) == step and not landed:
                raise server_module.TransportError("the cable came out")
            found = super().commit(store, handle)
            if len(commits) == step:
                raise server_module.TransportError("the cable came out")
            return found

    monkeypatch.setattr(server_module, "StepBuffer", Dropping)
    with serving(case.phone) as server:
        report = case.resolve(server, DESKTOP)
    monkeypatch.undo()
    assert report.status == "incomplete" and report.reason.startswith("disconnected"), report
    phone_now = case.bytes(case.phone)
    if landed:  # as a local resolve leaves the phone after a crash past its data commit
        assert phone_now == case.versions[DESKTOP] and case.versions[PHONE] in _kept(case.phone.store.root)
        if step == 1:  # the agreement is not recorded yet, but the copy is marked
            assert case.path.encode() in case.phone.store.read_sync_state(pending_name(DESKTOP))
    else:
        assert phone_now == case.versions[PHONE]
    with serving(case.phone) as server:  # the same resolve again: done if nothing landed, stale if it did
        retry = case.resolve(server, DESKTOP)
    assert retry.status == ("refused" if landed else "resolved"), retry
    again = case.sync_again()
    assert again.status == "synced" and again.conflicts == (), again
    assert case.labels() == (DESKTOP_LABEL, DESKTOP_LABEL)
    assert case.versions[PHONE] in _kept(case.phone.store.root)


# -- the wire: what the serving device itself refuses ---------------------------------------------

def _connect(device: Device, server) -> Channel:  # type: ignore[no-untyped-def]
    pairing = read_pairing(device.store, PHONE)
    return client_handshake(socket.create_connection(server.address, timeout=10), DESKTOP, PHONE, pairing.key)


def _request(channel: Channel, request: dict) -> None:  # type: ignore[type-arg]
    """Send a request that answers with a snapshot, and read that snapshot to its end."""
    channel.send(request)
    while channel.recv()[0]["t"] != "end":
        pass


def _step(channel: Channel, *ops: tuple[dict, bytes]) -> dict:  # type: ignore[type-arg]
    for header, body in ops:
        channel.send({"t": "op", **header}, body)
    channel.send({"t": "commit", "step": "resolve"})
    return channel.recv()[0]


def test_the_serving_device_refuses_resolve_writes_beyond_the_path_and_the_grants(tmp_path):
    case = Conflict(tmp_path)
    ours, theirs = case.versions[DESKTOP], case.versions[PHONE]
    request = {"t": "resolve", "path": case.path, "keep": etag(ours), "expected": case.expected}
    other_chat = case.path.replace(case.chat.chat_id, "t-desktop-9")
    attempts = [
        ({"op": "keep", "path": other_chat, "slot": 0, "hold": True}, theirs),          # a hold beside another path
        ({"op": "put", "path": other_chat, "expected": None, "replace": True, "allowed": []}, ours),  # a replace there
        ({"op": "keep", "path": case.path, "slot": 0, "hold": "yes"}, theirs),           # a malformed power
        ({"op": "put", "path": "secrets/peers/desktop.json", "expected": None}, b"{}"),  # a reserved root
        ({"op": "state", "name": f"peers/{DESKTOP}/../../device.json", "expected": None}, b"{}"),
    ]
    before = _tree(case.phone.store.root)
    with serving(case.phone) as server:
        channel = _connect(case.desktop, server)
        _request(channel, request)
        honest = ({"op": "put", "path": case.path, "expected": etag(theirs)}, ours)  # the write it may make
        for op in attempts:
            with pytest.raises(PeerRefused) as refused:
                _step(channel, honest, op)
            assert refused.value.code in ("invalid", "refused", "denied"), op
        # resolve's powers belong to a resolve request: a sync snapshot ends them
        _request(channel, {"t": "snapshot"})
        with pytest.raises(PeerRefused) as refused:
            _step(channel, honest, ({"op": "keep", "path": case.path, "slot": 0, "hold": True}, theirs))
        assert refused.value.code == "invalid"
        # and an honest resolve write still needs the grant: withdrawn, it is denied
        for grant in case.phone.authority.active_grants():
            if grant.principal == peer_principal(DESKTOP) and str(grant.scope) == "campaigns":
                case.phone.authority.revoke_grant(grant.grant_id)
        _request(channel, request)
        with pytest.raises(PeerRefused) as refused:
            _step(channel, honest)
        assert refused.value.code == "denied"
        channel.send({"t": "bye"})
        channel.close()
    assert _tree(case.phone.store.root) == before


@pytest.mark.parametrize("change", [
    {"keep": None},                                   # a deletion
    {"expected": {DESKTOP: None}},                    # one device's etag missing
    {"path": "secrets/peers/desktop.json"},           # not a synchronized record
])
def test_a_malformed_resolve_request_is_refused_before_anything_is_sent(tmp_path, change):
    case = Conflict(tmp_path)
    request = {"t": "resolve", "path": case.path, "keep": case.expected[DESKTOP], "expected": case.expected, **change}
    with serving(case.phone) as server:
        channel = _connect(case.desktop, server)
        channel.send(request)
        with pytest.raises(PeerRefused) as refused:
            channel.recv()
        assert refused.value.code == "invalid"
        channel.send({"t": "snapshot"})  # the session goes on, in step
        assert channel.recv()[0]["t"] == "grants"
        channel.close()


def test_a_peer_that_predates_resolving_refuses_it_and_nothing_changes(tmp_path):
    case = Conflict(tmp_path)
    phone_key = read_pairing(case.phone.store, DESKTOP).key
    listener = socket.create_server(("127.0.0.1", 0))
    seen = []

    def old_peer() -> None:
        """What a server without resolve does with any request it does not know: refuse, and end the session."""
        connection, _ = listener.accept()
        with connection:
            channel = server_handshake(connection, PHONE, lambda peer: phone_key if peer == DESKTOP else None)
            header, _ = channel.recv()
            seen.append(header["t"])
            channel.refuse("protocol", f"unexpected request {header['t']!r}")

    thread = threading.Thread(target=old_peer, daemon=True)
    thread.start()
    before = _tree(case.desktop.store.root), _tree(case.phone.store.root)
    try:
        report = resolve_conflict(case.desktop.store, case.desktop.authority, PHONE, listener.getsockname(),
                                  case.path, case.expected[DESKTOP], case.expected)
    finally:
        thread.join(10)
        listener.close()
    assert seen == ["resolve"]
    assert report.status == "refused" and report.reason.startswith("unsupported"), report
    assert (_tree(case.desktop.store.root), _tree(case.phone.store.root)) == before

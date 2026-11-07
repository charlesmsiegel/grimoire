"""028: ending a conflict over the peer connection. Either paired device chooses the version to keep
for one conflicted path, with 019's ``resolve`` guarantees, and the next transfer finds the
conflict gone on both.

Every library here is synthetic and lives under the test's temporary directory; every server
listens on loopback, one of them in its own process.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.peersync import PeerRefused, read_pairing, resolve_conflict, transfer, unpair
from grimoire.peersync import resolving as resolving_module
from grimoire.peersync import server as server_module
from grimoire.peersync.pairing import peer_principal
from grimoire.peersync.replay import StepBuffer
from grimoire.peersync.wire import Channel, client_handshake, server_handshake
from grimoire.store import RecordEnvelope, SourceManifest, etag
from grimoire.sync import DismissRefused, dismiss
from grimoire.sync import resolution, session as sync_session
from grimoire.sync.documents import KeptEntry, keeps_name, parse_keeps, pending_name, render_keeps

from chat_fixtures import CARDS
from peersync_helpers import DESKTOP, PHONE, Device, paired, seed_cards, serving, start_scene
from store_helpers import OWNER

BACKEND = Path(__file__).resolve().parents[1]
DESKTOP_LABEL, PHONE_LABEL = "Saltmarch docks", "The night crossing"


def _unkept(tree: dict[str, bytes]) -> dict[str, bytes]:
    """``tree`` without the kept-version records, which a resolve clears before its data (028)."""
    return {path: data for path, data in tree.items() if not path.endswith("/keeps.json")}


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


def test_a_definition_revision_is_replaced_only_by_a_resolve_its_own_device_starts(tmp_path):
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
        expected = {DESKTOP: etag(mine), PHONE: etag(theirs)}
        before = _tree(desktop.store.root), _tree(phone.store.root)
        # the desktop's choice would replace the phone's revision: only the phone may do that
        refused = resolve_conflict(desktop.store, desktop.authority, PHONE, server.address, path, etag(mine), expected)
        assert refused.status == "refused" and "start this resolve on phone" in refused.reason, refused
        assert (_tree(desktop.store.root), _tree(phone.store.root)) == before
    with serving(desktop) as server:  # the same choice, started on the phone
        resolved = resolve_conflict(phone.store, phone.authority, DESKTOP, server.address, path, etag(mine), expected)
    assert (resolved.status, resolved.receiver) == ("resolved", PHONE), resolved
    assert (phone.store.root / path).read_bytes() == mine and theirs in _kept(phone.store.root)
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"


class EditedRevision:
    """An immutable revision the phone edited in place after both devices agreed on it, reported
    by a transfer as ``revision_modified``: the phone keeps nothing (the desktop's version is the
    one both agreed on), the desktop keeps the edited bytes."""

    path = "sources/catalog/cards/_source/records/character/mara/r1.json"

    def __init__(self, root: Path) -> None:
        self.desktop, self.phone, _ = paired(root)
        seed_cards(self.desktop)  # no chat binds the revision, so it may be replaced
        with serving(self.phone) as server:
            assert transfer(self.desktop.store, self.desktop.authority, PHONE, server.address).status == "synced"
        self.agreed = (self.phone.store.root / self.path).read_bytes()
        self.edited = _forged(self.agreed, "bio", "edited in place")
        (self.phone.store.root / self.path).write_bytes(self.edited)
        with serving(self.phone) as server:
            report = transfer(self.desktop.store, self.desktop.authority, PHONE, server.address)
        conflict = report.passes[-1].conflict(self.path)
        assert conflict is not None and conflict.code == "revision_modified", report
        self.expected = {DESKTOP: etag(self.agreed), PHONE: etag(self.edited)}


def test_a_choice_neither_device_holds_is_not_redirected_to_the_other_device(tmp_path):
    case = EditedRevision(tmp_path)
    before = _tree(case.desktop.store.root), _tree(case.phone.store.root)
    for start, other, peer in ((case.desktop, case.phone, PHONE), (case.phone, case.desktop, DESKTOP)):
        with serving(other) as server:
            report = resolve_conflict(start.store, start.authority, peer, server.address, case.path, etag(b"{}"),
                                      case.expected)
        assert report.status == "refused" and "start this resolve" not in report.reason, report
        assert "neither device's current one" in report.reason, report
    assert (_tree(case.desktop.store.root), _tree(case.phone.store.root)) == before


@pytest.mark.parametrize("start", [DESKTOP, PHONE])
def test_an_edited_revision_returns_to_the_agreed_version_from_the_device_that_holds_it(tmp_path, start):
    case = EditedRevision(tmp_path)
    before = _tree(case.desktop.store.root), _tree(case.phone.store.root)
    if start == DESKTOP:  # the phone would receive a revision replace: it must start the resolve itself
        with serving(case.phone) as server:
            report = resolve_conflict(case.desktop.store, case.desktop.authority, PHONE, server.address, case.path,
                                      etag(case.agreed), case.expected)
        assert report.status == "refused" and "start this resolve on phone" in report.reason, report
        assert (_tree(case.desktop.store.root), _tree(case.phone.store.root)) == before
        return
    with serving(case.desktop) as server:
        report = resolve_conflict(case.phone.store, case.phone.authority, DESKTOP, server.address, case.path,
                                  etag(case.agreed), case.expected)
    assert (report.status, report.receiver) == ("resolved", PHONE), report
    assert (case.phone.store.root / case.path).read_bytes() == case.agreed
    assert case.edited in _kept(case.phone.store.root)  # the edit is held, never lost
    with serving(case.phone) as server:
        again = transfer(case.desktop.store, case.desktop.authority, PHONE, server.address)
    assert again.status == "synced" and again.conflicts == (), again


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


def test_the_deletion_is_chosen_only_on_the_device_that_holds_the_record(tmp_path):
    """028: the device still holding the record deletes it (keeping its version as a hold), so a
    deletion chosen on the other device is sent there; the serving device only writes bookkeeping."""
    case = Conflict(tmp_path)
    (case.desktop.store.root / case.path).unlink()  # the desktop's user deletes the chat by hand
    expected = {DESKTOP: None, PHONE: case.expected[PHONE]}
    with serving(case.phone) as server:
        report = transfer(case.desktop.store, case.desktop.authority, PHONE, server.address)
        assert report.passes[-1].conflict(case.path) is not None, report
        before = _tree(case.desktop.store.root), _tree(case.phone.store.root)
        refused = case.resolve(server, None, expected=expected)  # type: ignore[arg-type]
        assert refused.status == "refused" and refused.reason.endswith(f"start this resolve on {PHONE}"), refused
        unknown = case.resolve(server, etag(b""), expected=expected)  # an etag no device holds
        assert unknown.status == "refused", unknown
        assert (_tree(case.desktop.store.root), _tree(case.phone.store.root)) == before
    with serving(case.desktop) as server:
        chosen = case.resolve(server, None, start=case.phone, expected=expected)  # type: ignore[arg-type]
    assert (chosen.status, chosen.receiver) == ("resolved", PHONE), chosen
    assert not (case.phone.store.root / case.path).exists()
    assert case.versions[PHONE] in _kept(case.phone.store.root)
    again = case.sync_again()
    assert again.status == "synced" and again.conflicts == () and again.copied == () and again.deleted == (), again


def test_a_deletion_is_refused_once_the_serving_device_holds_the_record_again(tmp_path, monkeypatch):
    """The serving device (the winner, without the record) gets the record back after it sent its
    snapshot and took the resolve's last step before the deletion: the device that deletes asks it
    again, under both devices' locks, just before deleting, and the resolve is refused with nothing
    deleted and no tombstone archived on either device."""
    case = Conflict(tmp_path)
    data = case.versions[DESKTOP]
    (case.desktop.store.root / case.path).unlink()
    expected = {DESKTOP: None, PHONE: case.expected[PHONE]}
    commit_holds = resolution._commit_holds

    def then_restored(*args):  # the desktop's user puts the chat back, once every step there has gone
        commit_holds(*args)
        restore = case.desktop.store.transaction()
        restore.put_exact(case.path, data, None)
        restore.commit(case.desktop.handle)
    with serving(case.desktop) as server:
        assert transfer(case.phone.store, case.phone.authority, DESKTOP, server.address).passes[-1].conflict(
            case.path) is not None
        monkeypatch.setattr(resolution, "_commit_holds", then_restored)
        report = case.resolve(server, None, start=case.phone, expected=expected)  # type: ignore[arg-type]
        monkeypatch.undo()
    assert report.status == "refused" and f"{case.path} changed" in report.reason, report
    assert case.bytes(case.phone) == case.versions[PHONE] and case.bytes(case.desktop) == data
    for device in (case.desktop, case.phone):
        archive = json.loads((device.store.root / ".sync").joinpath(
            *next(path for path in _tree(device.store.root) if path.endswith("/archive.json")).split("/")[1:]
        ).read_bytes())
        assert "deleted" not in json.dumps(archive.get("entries", {}).get(case.path)), archive


@pytest.mark.parametrize("fresh", ["the deleted version", "a new version"])
def test_a_record_written_on_the_serving_device_after_its_check_is_never_deleted_or_lost(tmp_path, monkeypatch, fresh):
    """The window ``check`` leaves: the serving device takes a write of the record after it confirmed
    the deletion and before the requester's data commit. The requester deletes (its version held) and
    the resolve is ``incomplete``. The next transfer derives ``deleted E`` from the requester's marker
    and judges the fresh write by the decision table: the deleted bytes again are ``resurrected``
    (row 12), new bytes a new record at a deleted path, copied (row 13). Never a deletion over it."""
    case = Conflict(tmp_path)
    (case.desktop.store.root / case.path).unlink()
    expected = {DESKTOP: None, PHONE: case.expected[PHONE]}
    deleted = case.versions[PHONE]
    data = deleted if fresh == "the deleted version" else case.versions[DESKTOP].replace(b"Saltmarch", b"Brineholt")
    confirm = resolving_module._Relay.confirm_absent

    def then_written(self, paths):  # the desktop's user writes the chat once it has answered
        confirm(self, paths)
        write = case.desktop.store.transaction()
        write.put_exact(case.path, data, None)
        write.commit(case.desktop.handle)
    with serving(case.desktop) as server:
        assert transfer(case.phone.store, case.phone.authority, DESKTOP, server.address).passes[-1].conflict(
            case.path) is not None
        monkeypatch.setattr(resolving_module._Relay, "confirm_absent", then_written)
        report = case.resolve(server, None, start=case.phone, expected=expected)  # type: ignore[arg-type]
        monkeypatch.undo()
        assert report.status == "incomplete" and "stale" in report.reason, report
        assert not (case.phone.store.root / case.path).exists() and deleted in _kept(case.phone.store.root)
        again = transfer(case.phone.store, case.phone.authority, DESKTOP, server.address)
        assert case.bytes(case.desktop) == data and all(not p.deleted for p in again.passes), again
        if fresh == "the deleted version":
            assert again.status == "conflicts" and again.passes[-1].conflict(case.path).code == "resurrected"
            assert not (case.phone.store.root / case.path).exists()
        else:
            assert again.status == "synced" and case.bytes(case.phone) == data, again
        assert deleted in _kept(case.phone.store.root)  # the version the phone deleted stays held
        quiet = transfer(case.phone.store, case.phone.authority, DESKTOP, server.address)
        assert all(not p.deleted and not p.copied for p in quiet.passes), quiet


def test_a_serving_device_without_the_check_refuses_the_deletion_and_nothing_is_deleted(tmp_path, monkeypatch):
    """A serving device that predates ``check`` answers it ``protocol``: the resolve is ``refused`` and the
    requester still holds its record (every paired device runs the same version for 028's deletions)."""
    case = Conflict(tmp_path)
    (case.desktop.store.root / case.path).unlink()
    expected = {DESKTOP: None, PHONE: case.expected[PHONE]}

    def unknown(self, store, absent):  # what a server without ``check`` answers any request it does not know
        raise PeerRefused("protocol", "unexpected request 'check'")
    with serving(case.desktop) as server:
        assert transfer(case.phone.store, case.phone.authority, DESKTOP, server.address).passes[-1].conflict(
            case.path) is not None
        monkeypatch.setattr(resolving_module.ResolveRequest, "confirm_absent", unknown)
        report = case.resolve(server, None, start=case.phone, expected=expected)  # type: ignore[arg-type]
    assert report.status == "refused" and "protocol" in report.reason, report
    assert case.bytes(case.phone) == case.versions[PHONE] and not (case.desktop.store.root / case.path).exists()


def test_a_serving_device_never_deletes_its_own_record_for_a_peer(tmp_path):
    """A request that would have the serving device delete (it holds the record) is refused there,
    and no step of a resolve may delete anything on it."""
    case = Conflict(tmp_path)
    theirs = case.versions[PHONE]
    (case.desktop.store.root / case.path).unlink()
    with serving(case.phone) as server:
        transfer(case.desktop.store, case.desktop.authority, PHONE, server.address)
    before = _tree(case.phone.store.root)
    expected = {DESKTOP: None, PHONE: case.expected[PHONE]}
    with serving(case.phone) as server:
        channel = _connect(case.desktop, server)
        with pytest.raises(PeerRefused) as refused:  # past the client's own check, as a modified client would
            _request(channel, {"t": "resolve", "path": case.path, "keep": None, "expected": expected})
        assert refused.value.code == "invalid" and "start this resolve here" in str(refused.value)
        channel.close()
        channel = _connect(case.desktop, server)
        _request(channel, {"t": "resolve", "path": case.path, "keep": etag(theirs), "expected": expected})
        with pytest.raises(PeerRefused) as refused:  # the winner's steps write bookkeeping only
            _step(channel, ({"op": "delete", "path": case.path, "expected": etag(theirs), "slot": 0}, theirs))
        assert refused.value.code == "invalid"
        channel.close()
    assert _tree(case.phone.store.root) == before


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


@pytest.mark.parametrize("step,landed", [(1, False), (1, True), (2, False), (2, True), (3, False), (3, True),
                                         (4, True), (5, True)])
def test_a_dropped_connection_mid_resolve_loses_nothing_and_the_devices_converge(tmp_path, monkeypatch, step, landed):
    """The phone receives: its commits are its kept-version record with the conflict's deliveries
    cleared, that record holding its own version, the data, its archive and its marker. The desktop
    clears its own record before anything is sent, so a drop after the data (between the two
    archives included) never leaves the desktop's copy of the phone's version dismissable."""
    case = Conflict(tmp_path)
    commits = []

    class Dropping(StepBuffer):
        """The phone's side of the resolve's ``step``-th commit: the cable comes out before or after it lands."""

        def commit(self, store, handle, **options):  # type: ignore[no-untyped-def]
            commits.append(1)
            if len(commits) == step and not landed:
                raise server_module.TransportError("the cable came out")
            found = super().commit(store, handle, **options)
            if len(commits) == step:
                raise server_module.TransportError("the cable came out")
            return found

    monkeypatch.setattr(server_module, "StepBuffer", Dropping)
    with serving(case.phone) as server:
        report = case.resolve(server, DESKTOP)
    monkeypatch.undo()
    assert report.status == "incomplete" and report.reason.startswith("disconnected"), report
    phone_now, data = case.bytes(case.phone), landed and step >= 3
    if data:  # as a local resolve leaves the phone after a crash past its data commit
        assert phone_now == case.versions[DESKTOP] and case.versions[PHONE] in _kept(case.phone.store.root)
        if step == 3:  # the agreement is not recorded yet, but the copy is marked
            assert case.path.encode() in case.phone.store.read_sync_state(pending_name(DESKTOP))
    else:
        assert phone_now == case.versions[PHONE]
    if step <= 3:  # the hold's record lands before the data: never a hold without it (a record alone fails closed)
        record = parse_keeps(case.phone.store.read_sync_state(keeps_name(DESKTOP)), (DESKTOP, PHONE))
        entry = record.entries.get(case.path)
        held = entry is not None and case.expected[PHONE] in entry.held
        assert held == (step == 3 or (step == 2 and landed))
        if landed or step > 1:  # its first commit cleared the conflict's deliveries
            assert entry is None or not entry.delivered
    if data:  # the phone holds the desktop's version; the desktop's archive may still show the old base
        with pytest.raises(DismissRefused):
            dismiss(case.desktop.store, PHONE, case.path, case.expected[PHONE], case.desktop.handle)
    with serving(case.phone) as server:  # the same resolve again: done if the data did not land, stale if it did
        retry = case.resolve(server, DESKTOP)
    assert retry.status == ("refused" if data else "resolved"), retry
    again = case.sync_again()
    assert again.status == "synced" and again.conflicts == (), again
    assert case.labels() == (DESKTOP_LABEL, DESKTOP_LABEL)
    assert case.versions[PHONE] in _kept(case.phone.store.root)


def test_a_drop_after_the_starting_devices_data_never_leaves_the_winners_copy_dismissable(tmp_path, monkeypatch):
    """The desktop receives the phone's version; the cable comes out at the phone's next step.
    The phone's archive and marker still read as the open conflict, and its kept copy of the
    desktop's former version is now that version's only copy beside the desktop's hold: the phone
    cleared its record's deliveries in its first served step, before the desktop's data commit."""
    case = Conflict(tmp_path)

    class Dropping(StepBuffer):
        def commit(self, store, handle, **options):  # type: ignore[no-untyped-def]
            if case.bytes(case.desktop) == case.versions[PHONE]:  # the first served step after the data
                raise server_module.TransportError("the cable came out")
            return super().commit(store, handle, **options)

    monkeypatch.setattr(server_module, "StepBuffer", Dropping)
    with serving(case.phone) as server:
        report = case.resolve(server, PHONE)
    monkeypatch.undo()
    assert report.status == "incomplete" and report.reason.startswith("disconnected"), report
    assert case.bytes(case.desktop) == case.versions[PHONE]
    with pytest.raises(DismissRefused):
        dismiss(case.phone.store, DESKTOP, case.path, case.expected[DESKTOP], case.phone.handle)
    again = case.sync_again()
    assert again.status == "synced" and again.conflicts == (), again
    with pytest.raises(DismissRefused):
        dismiss(case.phone.store, DESKTOP, case.path, case.expected[DESKTOP], case.phone.handle)
    assert case.versions[DESKTOP] in _kept(case.phone.store.root)


def test_a_data_step_refused_after_the_holds_record_committed_leaves_only_bookkeeping(tmp_path, monkeypatch):
    """The phone receives: its record steps (the conflict's deliveries cleared, then its own version
    held) land; then its chat changes before the data step, which is refused as stale. ``refused``:
    the chosen version landed nowhere, and what stays is bookkeeping a local resolve also commits
    before its data, which only fails closed (the hold names bytes the phone still has)."""
    case = Conflict(tmp_path)
    commits, raced = [], case.versions[PHONE] + b"\n"

    class Racing(StepBuffer):
        def commit(self, store, handle, **options):  # type: ignore[no-untyped-def]
            commits.append(1)
            if len(commits) == 3:  # an edit on the phone itself, just before the data step
                (case.phone.store.root / case.path).write_bytes(raced)
            return super().commit(store, handle, **options)

    monkeypatch.setattr(server_module, "StepBuffer", Racing)
    with serving(case.phone) as server:
        report = case.resolve(server, DESKTOP)
    monkeypatch.undo()
    assert report.status == "refused" and report.reason.startswith("stale"), report
    assert case.bytes(case.phone) == raced and case.bytes(case.desktop) == case.versions[DESKTOP]
    record = parse_keeps(case.phone.store.read_sync_state(keeps_name(DESKTOP)), (DESKTOP, PHONE))
    assert case.expected[PHONE] in record.entries[case.path].held


# -- the wire: what the serving device itself refuses ---------------------------------------------

def _connect(device: Device, server) -> Channel:  # type: ignore[no-untyped-def]
    pairing = read_pairing(device.store, PHONE)
    return client_handshake(socket.create_connection(server.address, timeout=10), DESKTOP, PHONE, pairing.key)


def _request(channel: Channel, request: dict) -> None:  # type: ignore[type-arg]
    """Send a request that answers with a snapshot, and read that snapshot to its end."""
    channel.send(request)
    while channel.recv()[0]["t"] != "end":
        pass


def _held_record(device: Device, path: str, held: bytes) -> tuple[dict, bytes]:  # type: ignore[type-arg]
    """The phone's record of its pair with the desktop as a resolve that holds ``held`` writes it."""
    name = keeps_name(DESKTOP)
    before = device.store.read_sync_state(name)
    entries = dict(parse_keeps(before, (DESKTOP, PHONE)).entries)
    entry = entries.get(path, KeptEntry(None, frozenset()))
    entries[path] = KeptEntry(entry.base, entry.delivered - {etag(held)}, entry.dismissed, entry.held | {etag(held)})
    return ({"op": "state", "name": name, "expected": None if before is None else etag(before)},
            render_keeps((DESKTOP, PHONE), entries))


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
        # the writes it may make: the phone's own version recorded as held, in a step of its own, then
        # the chosen version with that version held beside it
        record = _held_record(case.phone, case.path, theirs)
        assert _step(channel, record)["t"] == "committed"
        honest = (({"op": "put", "path": case.path, "expected": etag(theirs)}, ours),
                  ({"op": "keep", "path": case.path, "slot": 0, "hold": True}, theirs))
        for op in attempts:
            with pytest.raises(PeerRefused) as refused:
                _step(channel, *honest, op)
            assert refused.value.code in ("invalid", "refused", "denied"), op
        # resolve's powers belong to a resolve request: a sync snapshot ends them
        _request(channel, {"t": "snapshot"})
        with pytest.raises(PeerRefused) as refused:
            _step(channel, *honest)
        assert refused.value.code == "invalid"
        # and an honest resolve write still needs the grant: withdrawn, it is denied
        for grant in case.phone.authority.active_grants():
            if grant.principal == peer_principal(DESKTOP) and str(grant.scope) == "campaigns":
                case.phone.authority.revoke_grant(grant.grant_id)
        _request(channel, request)
        with pytest.raises(PeerRefused) as refused:
            _step(channel, *honest)
        assert refused.value.code == "denied"
        channel.send({"t": "bye"})
        channel.close()
    # nothing but the hold's record, which holds bytes the phone still has: that only fails closed
    assert _tree(case.phone.store.root) == {**before, f".sync/{keeps_name(DESKTOP)}": record[1]}


def _forged(data: bytes, field: str, value: str) -> bytes:
    """The same record with one string field changed: still a valid record at the same address."""
    def swap(found):  # type: ignore[no-untyped-def]
        if isinstance(found, dict):
            return {key: (value if key == field else swap(item)) for key, item in found.items()}
        return found
    forged = json.dumps(swap(json.loads(data)), sort_keys=True, separators=(",", ":")).encode()
    return forged + b"\n" if data.endswith(b"\n") else forged


def test_a_peer_cannot_use_resolve_to_rewrite_a_record_in_no_conflict(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    start_scene(desktop)
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
    path = "sources/catalog/cards/_source/records/character/mara/r1.json"  # an immutable revision
    manifest = "sources/catalog/cards/_source/manifest.json"
    real = (phone.store.root / path).read_bytes()
    evil = _forged(real, "bio", "a traitor")
    before = _tree(phone.store.root)
    with serving(phone) as server:
        channel = _connect(desktop, server)
        for keep, mine, code in ((real, real, "no_conflict"),     # identical on both devices: nothing to resolve
                                 (evil, evil, "unknown_version")):  # bytes the phone neither holds nor keeps
            channel.send({"t": "resolve", "path": path, "keep": etag(keep),
                          "expected": {DESKTOP: etag(mine), PHONE: etag(real)}})
            with pytest.raises(PeerRefused) as refused:
                channel.recv()
            assert refused.value.code == code
        # and the forged revision replace a refused request would have carried is not resolve's
        with pytest.raises(PeerRefused) as refused:
            _step(channel, ({"op": "put", "path": path, "expected": etag(real), "replace": True,
                             "allowed": [etag((phone.store.root / manifest).read_bytes())]}, evil),
                  ({"op": "keep", "path": path, "slot": 0, "hold": True}, real))
        assert refused.value.code == "invalid"
        channel.close()
    assert _tree(phone.store.root) == before


def test_a_resolve_step_writes_only_the_chosen_version_with_the_replaced_one_held(tmp_path):
    case = Conflict(tmp_path)
    ours, theirs = case.versions[DESKTOP], case.versions[PHONE]
    forged = _forged(ours, "label", "Forged label")
    before = _tree(case.phone.store.root)
    with serving(case.phone) as server:
        channel = _connect(case.desktop, server)
        # a version no device holds is not one to keep, though the request is otherwise sound
        channel.send({"t": "resolve", "path": case.path, "keep": etag(forged), "expected": case.expected})
        with pytest.raises(PeerRefused) as refused:
            channel.recv()
        assert refused.value.code == "unknown_version"
        _request(channel, {"t": "resolve", "path": case.path, "keep": etag(ours), "expected": case.expected})
        put = {"op": "put", "path": case.path, "expected": etag(theirs)}
        hold = {"op": "keep", "path": case.path, "slot": 0, "hold": True}
        for step in (((put, forged), (hold, theirs)),   # other bytes than the chosen version
                     ((put, ours),),                    # the phone's own version not held
                     ((put, ours), (hold, forged))):    # something else held in its place
            with pytest.raises(PeerRefused) as refused:
                _step(channel, *step)
            assert refused.value.code == "invalid", step
        channel.close()
    assert _tree(case.phone.store.root) == before


def test_a_forged_version_planted_by_a_sync_step_never_replaces_a_revision(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    start_scene(desktop)
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
    path = "sources/catalog/cards/_source/records/character/mara/r1.json"
    manifest = "sources/catalog/cards/_source/manifest.json"
    real = (phone.store.root / path).read_bytes()
    evil = _forged(real, "bio", "a traitor")
    with serving(phone) as server:
        channel = _connect(desktop, server)
        _request(channel, {"t": "snapshot"})
        # a plain sync step may keep the peer's version beside a path: the forged bytes now sit there
        assert _step(channel, ({"op": "keep", "path": path, "slot": 0}, evil))["t"] == "committed"
        _request(channel, {"t": "resolve", "path": path, "keep": etag(evil),
                           "expected": {DESKTOP: etag(evil), PHONE: etag(real)}})
        before = _tree(phone.store.root)
        with pytest.raises(PeerRefused) as refused:  # but no step may replace the revision with them
            _step(channel, ({"op": "put", "path": path, "expected": etag(real), "replace": True,
                             "allowed": [etag((phone.store.root / manifest).read_bytes())]}, evil),
                  ({"op": "keep", "path": path, "slot": 0, "hold": True}, real))
        assert refused.value.code == "invalid"
        channel.close()
    assert _tree(phone.store.root) == before and (phone.store.root / path).read_bytes() == real


class UncopiedEdit:
    """A chat both devices agreed on, relabelled on the phone only, whose copy to the desktop the
    desktop's owner does not authorize: a transfer reports it ``denied``, and keep-both keeps
    nothing on either device (a denied write is never kept, and the desktop's copy is the agreed
    version the phone moved on from)."""

    def __init__(self, root: Path, *, extra: int = 0) -> None:
        self.desktop, self.phone, _ = paired(root)
        started, _ = start_scene(self.desktop)
        if extra:  # more records than one archive file holds at the test's cap: the archive is sharded
            txn = self.desktop.store.transaction()
            for number in range(extra):
                txn.put(RecordEnvelope(InstanceRef("c9", "character", f"extra-{number:03d}"), {"n": number}), None)
            txn.commit(self.desktop.handle)
        self.chat = started.chat
        self.path = f"campaigns/{self.chat.campaign_id}/chats/{self.chat.chat_id}.json"
        with serving(self.phone) as server:
            assert transfer(self.desktop.store, self.desktop.authority, PHONE, server.address).status == "synced"
        for grant in self.desktop.authority.active_grants():  # the desktop's owner withdraws campaigns from the phone
            if grant.principal == peer_principal(PHONE) and str(grant.scope) == "campaigns":
                self.desktop.authority.revoke_grant(grant.grant_id)
        self.agreed = (self.desktop.store.root / self.path).read_bytes()
        _relabel(self.phone, self.chat, PHONE_LABEL)
        self.edited = (self.phone.store.root / self.path).read_bytes()
        with serving(self.phone) as server:
            report = transfer(self.desktop.store, self.desktop.authority, PHONE, server.address)
        conflict = report.passes[-1].conflict(self.path)
        assert conflict is not None and conflict.code == "denied", report
        assert _kept(self.phone.store.root) == []  # the phone holds nothing but its own edit
        self.expected = {DESKTOP: etag(self.agreed), PHONE: etag(self.edited)}


@pytest.mark.parametrize("sharded", [False, True])
def test_the_agreed_version_ends_a_conflict_that_kept_nothing_on_the_receiver(tmp_path, monkeypatch, sharded):
    """The serving phone reads its archive as a sync does, so the agreed version is still one it admits
    once the pair's archive outgrows one file and is sharded."""
    if sharded:
        monkeypatch.setattr(sync_session, "ARCHIVE_LIMIT", 12_000)  # about 30 paths a file; 120 extra records
    case = UncopiedEdit(tmp_path, extra=120 if sharded else 0)
    if sharded:
        root = json.loads(case.phone.store.read_sync_state(f"peers/{DESKTOP}/archive.json"))  # type: ignore[arg-type]
        assert root["format"] == "grimoire.sync.archive/2"
    with serving(case.phone) as server:  # the desktop keeps the version both agreed on: the phone receives it
        report = resolve_conflict(case.desktop.store, case.desktop.authority, PHONE, server.address, case.path,
                                  etag(case.agreed), case.expected)
    assert (report.status, report.receiver) == ("resolved", PHONE), report
    assert (case.phone.store.root / case.path).read_bytes() == case.agreed
    assert case.edited in _kept(case.phone.store.root)  # the phone's edit is held, never lost
    with serving(case.phone) as server:
        again = transfer(case.desktop.store, case.desktop.authority, PHONE, server.address)
    assert again.status == "synced" and again.conflicts == (), again


def test_the_agreed_version_is_admitted_only_as_the_requesters_own(tmp_path):
    case = UncopiedEdit(tmp_path)
    before = _tree(case.phone.store.root)
    with serving(case.phone) as server:
        channel = _connect(case.desktop, server)
        # the archive's agreed version, while the desktop claims to hold another one: refused
        for claimed in (case.edited, _forged(case.agreed, "label", "Forged label")):
            channel.send({"t": "resolve", "path": case.path, "keep": etag(case.agreed),
                          "expected": {DESKTOP: etag(claimed), PHONE: etag(case.edited)}})
            with pytest.raises(PeerRefused) as refused:
                channel.recv()
            assert refused.value.code == "unknown_version"
        # as the desktop's own current version, which the archive records both agreed on: admitted
        _request(channel, {"t": "resolve", "path": case.path, "keep": etag(case.agreed), "expected": case.expected})
        channel.send({"t": "bye"})
        channel.close()
    assert _tree(case.phone.store.root) == before


@pytest.mark.parametrize("meet", ["request", "commit"])
def test_two_devices_resolving_against_each_other_at_once_neither_hangs_nor_half_applies(tmp_path, monkeypatch, meet):
    case = Conflict(tmp_path)
    lined_up = threading.Barrier(2, timeout=120)
    if meet == "request":  # both resolves are under way before either request arrives
        real_dial = resolving_module.dial

        def dial_together(*args, **kwargs):  # type: ignore[no-untyped-def]
            lined_up.wait()
            return real_dial(*args, **kwargs)
        monkeypatch.setattr(resolving_module, "dial", dial_together)
    else:  # both requests were accepted, and each device's first served step waits on the other's library
        monkeypatch.setattr(server_module, "resolving_out", lambda store: False)
        # Both snapshots are read first: a resolve waiting below holds its own library, which the other
        # device's snapshot of it would otherwise wait for, until this barrier timed out.
        snapshots = threading.Barrier(2, timeout=120)
        real_resolve = resolving_module.resolve

        def resolve_together(*args, **kwargs):  # type: ignore[no-untyped-def]
            snapshots.wait()
            return real_resolve(*args, **kwargs)
        monkeypatch.setattr(resolving_module, "resolve", resolve_together)
        real_relay = resolving_module._Relay.__call__

        def relay_together(self, ops):  # type: ignore[no-untyped-def]
            if not getattr(self, "lined_up", False):  # each resolve's first mirror commit
                self.lined_up = True
                lined_up.wait()
            return real_relay(self, ops)
        monkeypatch.setattr(resolving_module._Relay, "__call__", relay_together)
    before = _tree(case.desktop.store.root), _tree(case.phone.store.root)
    reports = {}
    with serving(case.phone) as on_phone, serving(case.desktop) as on_desktop:
        def from_desktop() -> None:  # keeps its own version: the phone would receive it
            reports[DESKTOP] = resolve_conflict(case.desktop.store, case.desktop.authority, PHONE, on_phone.address,
                                                case.path, case.expected[DESKTOP], case.expected, timeout=120)

        def from_phone() -> None:  # keeps its own version: the desktop would receive it
            reports[PHONE] = resolve_conflict(case.phone.store, case.phone.authority, DESKTOP, on_desktop.address,
                                              case.path, case.expected[PHONE], case.expected, timeout=120)
        threads = [threading.Thread(target=from_desktop), threading.Thread(target=from_phone)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(150)
    monkeypatch.undo()
    # neither waits for the other's connection to time out: both end, on the busy wait
    assert not any(thread.is_alive() for thread in threads)
    # Each one is refused with no data written (busy), or at most one of them ends the conflict whole: one whose
    # server took its library just as the other gave up (the busy wait is bounded, not a turn).
    resolved = [device for device, report in reports.items() if report.status == "resolved"]
    assert len(reports) == 2 and len(resolved) <= 1, reports
    for device, report in reports.items():
        if device not in resolved:
            # busy; or stale, when the other resolve gave up only after its own records changed
            assert report.status == "refused" and report.reason.startswith(("busy", "stale")), report
    if not resolved:  # no data anywhere; each device's own record may have ended the conflict's deliveries
        assert (_unkept(_tree(case.desktop.store.root)), _unkept(_tree(case.phone.store.root))) == tuple(
            _unkept(tree) for tree in before)
        assert case.sync_again().status == "conflicts"  # still the one conflict, on both devices
        with serving(case.phone) as server:  # and one resolve on its own then ends it
            assert case.resolve(server, DESKTOP).status == "resolved"
        resolved = [DESKTOP]
    chosen = DESKTOP_LABEL if resolved == [DESKTOP] else PHONE_LABEL
    assert case.labels() == (chosen, chosen)  # never swapped into a fresh conflict
    again = case.sync_again()
    assert again.status == "synced" and again.conflicts == (), again
    assert case.labels() == (chosen, chosen)


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

"""028's first proof: two paired devices synchronize a scene, its character and its picture over an
authenticated loopback connection, and report ``synced``, ``conflicts`` or ``incomplete`` truthfully.

Every library here is synthetic and lives under the test's temporary directory; every server
listens on 127.0.0.1. The wire's own refusals (forged, replayed and unauthorized frames) are in
test_peersync_wire.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from grimoire.chats.model import Launch
from grimoire.chatstore import InvalidBinding
from grimoire.core.identity import InstanceRef, ScopeRef
from grimoire.peersync import AuthenticationFailed, NotPaired, new_pairing_key, pair, transfer, unpair
from grimoire.peersync import server as server_module
from grimoire.peersync import snapshot as snapshot_module
from grimoire.peersync import replay as replay_module
from grimoire.peersync.pairing import peer_principal
from grimoire.peersync.replay import StepBuffer
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest

from chat_fixtures import ANYA, MARA
from peersync_helpers import DESKTOP, PHONE, Device, paired, seed_cards, serving, start_scene
from store_helpers import OWNER

BACKEND = Path(__file__).resolve().parents[1]


def _files(root: Path) -> dict[str, bytes]:
    """Every synchronized file under a library, by path: what a user would see as its contents."""
    found = {}
    for top in ("sources", "campaigns", "assets", ".conflicts"):
        for path in sorted((root / top).rglob("*")):
            if path.is_file():
                found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


def _actor_ids(device: Device, campaign: str) -> set[str]:
    return {found.instance_id for found in device.store.campaign_instances(campaign)}


# -- the proof: a scene, its character and its picture reach the other device ------------------

def test_a_scene_with_its_character_and_picture_reaches_a_device_in_another_process(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    started, image = start_scene(desktop)
    chat = started.chat
    phone.close()  # the phone's library is now served by its own process
    env = {**os.environ, "PYTHONPATH": str(BACKEND / "src")}
    serve = subprocess.Popen(
        [sys.executable, "-m", "grimoire.peersync", "serve", str(tmp_path / PHONE), "--owner", OWNER, "--once"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=BACKEND)
    try:
        line = serve.stdout.readline().split()  # type: ignore[union-attr]
        assert line[:1] == ["listening"], serve.stderr.read()  # type: ignore[union-attr]
        report = transfer(desktop.store, desktop.authority, PHONE, (line[1], int(line[2])))
        assert serve.wait(30) == 0, serve.stderr.read()  # type: ignore[union-attr]
    finally:
        serve.kill()
        serve.wait()
    assert report.status == "synced", report
    assert report.reason is None and report.conflicts == ()

    phone = Device.reopen(tmp_path, PHONE)
    try:
        view = phone.chats.open_chat(chat)
        assert view.record.ref == chat and view.broken == () and view.problems == ()
        actor = started.actors[0]
        assert actor.instance_id in _actor_ids(phone, actor.campaign_id)
        files = _files(tmp_path / PHONE)
        assert f"campaigns/{actor.campaign_id}/images/{image.image_id}.json" in files
        assert f"campaigns/{actor.campaign_id}/placements/character/{actor.instance_id}/avatar.json" in files
        assert any(path.startswith("assets/blobs/") for path in files)
        # the phone's own list by character and the desktop's list by campaign find the same chat
        assert phone.chats.navigation().by_source(MARA) == desktop.chats.navigation().by_scope(chat.campaign_id) == (chat,)
        assert files == _files(tmp_path / DESKTOP)
    finally:
        phone.close()


def test_the_next_transfer_has_nothing_to_do(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    start_scene(desktop)
    with serving(phone) as server:
        first = transfer(desktop.store, desktop.authority, PHONE, server.address)
        again = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert first.status == again.status == "synced"
    assert first.copied and again.copied == ()


def test_either_device_can_start_the_transfer(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    start_scene(phone)
    with serving(desktop) as server:
        report = transfer(phone.store, phone.authority, DESKTOP, server.address)
    assert report.status == "synced"
    assert desktop.chats.navigation().all_chats() == phone.chats.navigation().all_chats()


def test_writes_from_the_peer_are_authorized_as_the_peer_and_never_as_the_owner(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    start_scene(desktop)
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
    principals = {grant.principal for grant in phone.authority.active_grants() if grant.principal != OWNER}
    assert principals == {peer_principal(DESKTOP)}
    assert peer_principal(DESKTOP) not in phone.authority.owners


# -- conflicts and offline work ----------------------------------------------------------------

def _relabel(device: Device, chat, label: str) -> None:  # type: ignore[no-untyped-def]
    _, current = device.chats.read_chat(chat)
    device.chats.set_label(chat, label, expected=current).commit()


def _add_source(device: Device, source: str) -> None:
    txn = device.store.transaction()
    txn.put(SourceManifest(source, OWNER, "Extra cards"), None)
    txn.commit(device.handle)


def test_both_devices_changing_one_chat_offline_is_a_conflict_with_both_versions_kept(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    started, _ = start_scene(desktop)
    chat = started.chat
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
        _relabel(desktop, chat, "Saltmarch docks")
        _relabel(phone, chat, "The night crossing")
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.status == "conflicts", report
    path = f"campaigns/{chat.campaign_id}/chats/{chat.chat_id}.json"
    conflict = report.passes[-1].conflict(path)
    assert conflict is not None and conflict.kept == {DESKTOP: True, PHONE: True}, report.conflicts
    # each device still shows its own version and holds the other one under .conflicts
    assert desktop.chats.read_chat(chat)[0].label == "Saltmarch docks"
    assert phone.chats.read_chat(chat)[0].label == "The night crossing"
    for device in (desktop, phone):
        assert len([path for path in _files(device.store.root) if path.startswith(".conflicts/")]) == 1


def test_a_kept_slot_the_server_cannot_send_still_counts_as_taken(tmp_path):
    from grimoire.store import etag
    from grimoire.store.layout import conflict_parts
    desktop, phone, _ = paired(tmp_path)
    started, _ = start_scene(desktop)
    chat = started.chat
    path = f"campaigns/{chat.campaign_id}/chats/{chat.chat_id}.json"
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
        _relabel(desktop, chat, "Saltmarch docks")
        _relabel(phone, chat, "The night crossing")
        ours = (desktop.store.root / path).read_bytes()
        slot = phone.store.root.joinpath(*conflict_parts(tuple(path.split("/")), etag(ours), 0))
        slot.parent.mkdir(parents=True, exist_ok=True)
        slot.write_bytes(b"\0" * (9 * 1024 * 1024))  # something already there, too big to send
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.status == "conflicts", report  # the mirror picked the next slot, as the phone did
    assert slot.read_bytes() == b"\0" * (9 * 1024 * 1024)


def _slot_in_another_spelling(slot: Path) -> None:
    slot.parent.mkdir(parents=True, exist_ok=True)
    (slot.parent / slot.name.upper().replace(".JSON", ".json")).write_bytes(b"{}")


def _slot_folder_in_another_spelling(slot: Path) -> None:
    folder = slot.parent.parent / slot.parent.name.upper()
    folder.mkdir(parents=True)
    (folder / "note.txt").write_bytes(b"left here")


def _slot_folder_is_a_file(slot: Path) -> None:
    slot.parent.parent.mkdir(parents=True, exist_ok=True)
    slot.parent.write_bytes(b"left here")


@pytest.mark.parametrize("obstruction", [_slot_in_another_spelling, _slot_folder_in_another_spelling,
                                         _slot_folder_is_a_file])
def test_whatever_stands_where_a_kept_version_would_go_is_seen_as_the_server_sees_it(tmp_path, obstruction):
    from grimoire.store import etag
    from grimoire.store.layout import conflict_parts
    desktop, phone, _ = paired(tmp_path)
    started, _ = start_scene(desktop)
    chat = started.chat
    path = f"campaigns/{chat.campaign_id}/chats/{chat.chat_id}.json"
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
        _relabel(desktop, chat, "Saltmarch docks")
        _relabel(phone, chat, "The night crossing")
        ours = (desktop.store.root / path).read_bytes()
        slot = phone.store.root.joinpath(*conflict_parts(tuple(path.split("/")), etag(ours), 0))
        try:
            obstruction(slot)
        except FileExistsError:  # a case-insensitive disk: the other spelling is this one
            pytest.skip("this filesystem cannot hold two spellings of one name")
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    # the phone keeps the desktop's version where its own 019 would, or not at all: never a
    # refused step on every pass
    assert report.status == "conflicts", report
    assert report.passes[-1].conflict(path) is not None, report.conflicts


def test_a_record_too_big_to_read_on_the_server_is_never_kept_there(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    started, _ = start_scene(desktop)
    chat = started.chat
    path = f"campaigns/{chat.campaign_id}/chats/{chat.chat_id}.json"
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
        _relabel(desktop, chat, "Saltmarch docks")
        (phone.store.root / path).write_bytes(b" " * (9 * 1024 * 1024))
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    conflict = report.passes[-1].conflict(path)
    # as a local 019 sync decides it: the phone's own copy is unreadable, so nothing is kept
    assert conflict is not None and conflict.detail == f"unreadable on {PHONE}" and not conflict.kept, conflict
    assert not [name for name in _files(phone.store.root) if name.startswith(".conflicts/")]


def test_offline_scenes_on_both_devices_get_different_actors_in_one_campaign(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    started, _ = start_scene(desktop)
    scope = ScopeRef(started.chat.campaign_id)
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
        # offline: each device continues the same campaign with a new scene for the same character
        on_desktop = desktop.chats.start_chat(Launch("character", MARA), scope=scope).commit()
        on_phone = phone.chats.start_chat(Launch("character", MARA), scope=scope).commit()
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.status == "synced", report
    new = {on_desktop.actors[0].instance_id, on_phone.actors[0].instance_id}
    assert len(new) == 2  # without device-qualified numbers both would be the next free "mara-N"
    for device in (desktop, phone):
        assert new <= _actor_ids(device, scope.campaign_id)
        assert set(device.chats.navigation().by_scope(scope.campaign_id)) == {
            started.chat, on_desktop.chat, on_phone.chat}


def test_a_library_without_a_device_identity_keeps_plain_actor_numbers(tmp_path):
    from chat_fixtures import chat_library, seed_sources
    from store_helpers import owner_handle
    store = LibraryStore.create(tmp_path / "solo")
    seed_sources(store)
    started = chat_library(store, owner_handle()).start_chat(Launch("character", MARA)).commit()
    assert started.actors[0].instance_id == "mara-1"


def test_a_damaged_device_identity_stops_actor_creation_rather_than_reusing_plain_numbers(tmp_path):
    device = Device(tmp_path, DESKTOP)
    seed_cards(device)
    (device.store.root / ".sync" / "device.json").write_bytes(b"{not json")
    with pytest.raises(InvalidBinding) as refused:
        device.chats.start_chat(Launch("character", ANYA)).commit()
    assert refused.value.reason == "device_identity"


def test_a_linked_device_identity_stops_actor_creation_too(tmp_path):
    device = Device(tmp_path, DESKTOP)
    seed_cards(device)
    identity = device.store.root / ".sync" / "device.json"
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_bytes(identity.read_bytes())
    identity.unlink()
    try:
        identity.symlink_to(elsewhere)
    except OSError:
        pytest.skip("this filesystem cannot hold a link")
    with pytest.raises(InvalidBinding) as refused:
        device.chats.start_chat(Launch("character", ANYA)).commit()
    assert refused.value.reason == "device_identity"


# -- incomplete transfers ----------------------------------------------------------------------

def test_an_unreachable_peer_is_incomplete_and_nothing_changes(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    start_scene(desktop)
    with serving(phone) as server:
        address = server.address
    before = _files(desktop.store.root)
    report = transfer(desktop.store, desktop.authority, PHONE, address, timeout=2.0)
    assert report.status == "incomplete" and report.reason.startswith("unreachable")
    assert _files(desktop.store.root) == before
    assert _files(phone.store.root) == {}


def test_a_connection_lost_mid_transfer_is_incomplete_and_the_next_transfer_finishes(tmp_path, monkeypatch):
    desktop, phone, _ = paired(tmp_path)
    start_scene(desktop)
    calls = []

    class Dropping(StepBuffer):
        def commit(self, store, handle, **options):  # type: ignore[no-untyped-def]
            calls.append(1)
            found = super().commit(store, handle, **options)
            if len(calls) == 1:  # the peer committed the first step, then the connection drops
                raise server_module.TransportError("the cable came out")
            return found

    monkeypatch.setattr(server_module, "StepBuffer", Dropping)
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.status == "incomplete" and report.reason.startswith("disconnected"), report
    monkeypatch.undo()
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.status == "synced", report
    assert _files(phone.store.root) == _files(desktop.store.root)
    assert phone.chats.navigation().all_chats() == desktop.chats.navigation().all_chats()


def test_a_connection_lost_after_a_pass_keeps_that_pass_in_the_report(tmp_path, monkeypatch):
    desktop, phone, _ = paired(tmp_path)
    start_scene(desktop)
    real, snapshots = server_module.send_snapshot, []

    def dropping(*args, **kwargs):  # type: ignore[no-untyped-def]
        snapshots.append(1)
        if len(snapshots) == 2:  # the first pass wrote to the phone; the second snapshot never comes
            raise server_module.TransportError("the cable came out")
        return real(*args, **kwargs)

    monkeypatch.setattr(server_module, "send_snapshot", dropping)
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.status == "incomplete" and report.reason.startswith("disconnected"), report
    assert len(report.passes) == 1 and report.copied, report  # what landed is still reported


def test_a_peer_that_changes_during_a_pass_is_planned_again_and_converges(tmp_path, monkeypatch):
    desktop, phone, _ = paired(tmp_path)
    start_scene(desktop)
    changed = []

    class Busy(StepBuffer):
        def commit(self, store, handle, **options):  # type: ignore[no-untyped-def]
            if not changed:  # the phone's own user adds a source between the snapshot and the commit
                changed.append(1)
                _add_source(phone, "catalog:extra")
            return super().commit(store, handle, **options)

    monkeypatch.setattr(server_module, "StepBuffer", Busy)
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.status == "synced", report
    assert len(report.passes) >= 2  # the refused step was planned again from both devices as they now were
    assert "sources/catalog/extra/_source/manifest.json" in _files(desktop.store.root)
    assert _files(phone.store.root) == _files(desktop.store.root)


def test_a_snapshot_that_does_not_match_the_peers_own_scan_is_never_used(tmp_path, monkeypatch):
    desktop, phone, _ = paired(tmp_path)
    start_scene(phone)
    real = snapshot_module.walk

    def altered(root, blocked):  # type: ignore[no-untyped-def]
        for kind, path, data in real(root, blocked):
            yield kind, path, (data + b" " if kind == "file" and path.endswith("manifest.json") else data)

    monkeypatch.setattr(snapshot_module, "walk", altered)
    before = _files(phone.store.root)
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.status == "incomplete" and "MirrorMismatch" in report.reason, report
    assert _files(desktop.store.root) == {}
    assert _files(phone.store.root) == before


def test_a_snapshot_this_device_cannot_rebuild_is_read_to_its_end_and_the_next_pass_succeeds(tmp_path, monkeypatch):
    desktop, phone, _ = paired(tmp_path)
    start_scene(phone)
    real = snapshot_module._materialize
    calls = []

    def once_unholdable(root, kind, parts, body):  # type: ignore[no-untyped-def]
        calls.append(1)
        if len(calls) == 3:  # say, a name this filesystem cannot hold, early in the first snapshot
            raise snapshot_module.MirrorMismatch("cannot reproduce it here")
        return real(root, kind, parts, body)

    monkeypatch.setattr(snapshot_module, "_materialize", once_unholdable)
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.status == "synced", report  # the same connection stayed in step for the next pass
    assert _files(desktop.store.root) == _files(phone.store.root)


def _stray_large_file(root: Path) -> None:
    (root / "sources" / "catalog" / "original.bin").write_bytes(b"\0" * (9 * 1024 * 1024))


def _stray_pipe(root: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("this system has no named pipes")
    os.mkfifo(root / "sources" / "catalog" / "pipe")


def _stray_deep_folder(root: Path) -> None:
    folder = root / "sources" / "catalog" / "notes"
    for depth in range(70):
        folder = folder / f"d{depth}"
    try:
        (folder / "Readme").mkdir(parents=True)  # a spelling the serving scan reports as blocked
    except OSError:  # Windows: past the path length this system allows
        pytest.skip("this filesystem cannot nest folders that deep")


@pytest.mark.parametrize("stray", [_stray_large_file, _stray_pipe, _stray_deep_folder])
def test_a_stray_file_beside_the_records_that_019_ignores_blocks_nothing(tmp_path, stray):
    desktop, phone, _ = paired(tmp_path)
    start_scene(phone)
    stray(phone.store.root)
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.status == "synced", report
    assert desktop.chats.navigation().all_chats() == phone.chats.navigation().all_chats()


def test_unpairing_on_the_starting_device_stops_what_it_sends_mid_transfer(tmp_path, monkeypatch):
    from grimoire.peersync import replay as replay_module
    from grimoire.peersync import session as session_module
    desktop, phone, _ = paired(tmp_path)
    start_scene(desktop)
    real = replay_module.send_step
    sent = []

    def unpair_first(channel, step, reader):  # type: ignore[no-untyped-def]
        sent.append(step.kind)
        return real(channel, step, reader)

    monkeypatch.setattr(session_module, "send_step", unpair_first)
    real_check = session_module._check_paired
    checks = []

    def unpair_before_the_first_step(store, peer, key):  # type: ignore[no-untyped-def]
        checks.append(1)
        if len(checks) == 2:  # after the pass's own check: the owner unpairs a lost phone
            unpair(desktop.store, desktop.authority, PHONE)
        return real_check(store, peer, key)

    monkeypatch.setattr(session_module, "_check_paired", unpair_before_the_first_step)
    with serving(phone) as server:
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.status == "incomplete" and report.reason == "not_paired", report
    assert sent == []  # nothing of the desktop's library was sent
    assert not (phone.store.root / "campaigns").exists()


# -- pairing ----------------------------------------------------------------------------------

def test_a_device_with_another_key_is_refused_before_anything_is_exchanged(tmp_path):
    desktop, phone = Device(tmp_path, DESKTOP), Device(tmp_path, PHONE)
    pair(desktop.store, desktop.authority, PHONE, new_pairing_key(), granted_by=OWNER)
    pair(phone.store, phone.authority, DESKTOP, new_pairing_key(), granted_by=OWNER)
    start_scene(desktop)
    start_scene(phone)
    before = _files(desktop.store.root), _files(phone.store.root)
    with serving(phone) as server, pytest.raises(AuthenticationFailed):
        transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert (_files(desktop.store.root), _files(phone.store.root)) == before


def test_a_device_this_library_is_not_paired_with_is_refused(tmp_path):
    desktop, phone = Device(tmp_path, DESKTOP), Device(tmp_path, PHONE)
    with pytest.raises(NotPaired):
        transfer(desktop.store, desktop.authority, PHONE, ("127.0.0.1", 9))
    pair(desktop.store, desktop.authority, PHONE, new_pairing_key(), granted_by=OWNER)
    with serving(phone) as server, pytest.raises(AuthenticationFailed) as refused:
        transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert refused.value.code == "not_paired"


def test_unpairing_revokes_the_peers_grants_and_ends_its_connections(tmp_path):
    desktop, phone, _ = paired(tmp_path)
    start_scene(desktop)
    unpair(phone.store, phone.authority, DESKTOP)
    assert not any(grant.principal == peer_principal(DESKTOP) for grant in phone.authority.active_grants())
    with serving(phone) as server, pytest.raises(AuthenticationFailed) as refused:
        transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert refused.value.code == "not_paired"
    assert _files(phone.store.root) == {}


def test_pairing_keys_and_ledgers_never_leave_their_device(tmp_path):
    desktop, phone, key = paired(tmp_path)
    start_scene(desktop)
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
    secrets = sorted(path.name for path in (phone.store.root / "secrets").rglob("*") if path.is_file())
    assert secrets == [f"{DESKTOP}.json"]  # its own record of the pairing, never the desktop's
    for path, data in _files(phone.store.root).items():
        assert key.hex().encode() not in data, path


# -- 028: deletions over the transport ---------------------------------------------------------------

@pytest.mark.parametrize("deleter", [DESKTOP, PHONE])
def test_a_deletion_on_either_device_is_made_on_the_other_and_its_version_kept_there(tmp_path, deleter):
    """The phone serves. Deleted on the desktop, the phone deletes it in a replayed step under the
    desktop's peer principal; deleted on the phone, the desktop deletes it in its own library."""
    desktop, phone, _ = paired(tmp_path)
    start_scene(desktop)
    _add_source(desktop, "catalog:extra")
    path = "sources/catalog/extra/_source/manifest.json"
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
        original = (desktop.store.root / path).read_bytes()
        source, receiver = (desktop, phone) if deleter == DESKTOP else (phone, desktop)
        (source.store.root / path).unlink()
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
        assert report.status == "synced" and (path, receiver.name) in report.deleted, report
        assert not (receiver.store.root / path).exists()
        assert original in [data for where, data in _files(receiver.store.root).items() if where.startswith(".conflicts/")]
        assert "catalog:extra" not in receiver.store.load().sources
        again = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert again.status == "synced" and again.copied == () and again.deleted == (), again
    assert {key: value for key, value in _files(desktop.store.root).items() if not key.startswith(".conflicts/")} == {
        key: value for key, value in _files(phone.store.root).items() if not key.startswith(".conflicts/")}


KIT = "campaigns/c5/instances/character/kit.json"


def test_a_record_written_on_the_serving_device_after_its_snapshot_stops_a_deletion_it_needs(tmp_path, monkeypatch):
    """The serving device judges a replayed deletion again under its commit's lock: a record written
    there since its snapshot that reads the source being deleted refuses the step as stale, nothing
    is removed, and the transfer's next pass holds the deletion back."""
    desktop, phone, _ = paired(tmp_path)
    _add_source(desktop, "catalog:extra")
    path = "sources/catalog/extra/_source/manifest.json"
    commit = StepBuffer._commit

    def meanwhile(self, store, *args):  # type: ignore[no-untyped-def]
        if any(header.get("op") == "delete" for header, _ in self.ops) and not (store.root / KIT).exists():
            txn = store.transaction()  # the person adds a card on the phone that reads the source
            txn.put(RecordEnvelope(InstanceRef("c5", "character", "kit"), {"from": "catalog:extra/character/kit"}), None)
            txn.commit(phone.handle)
        return commit(self, store, *args)
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
        (desktop.store.root / path).unlink()
        monkeypatch.setattr(replay_module.StepBuffer, "_commit", meanwhile)
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
    assert report.passes[-1].conflict(path).code == "delete_dependency" and report.deleted == ()
    assert (phone.store.root / path).exists() and (phone.store.root / KIT).exists()

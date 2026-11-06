"""028: dismissing a kept version. A dismissed version is not delivered again for the same bytes; a
different later version still is; the other device's record is never touched; only a version a sync
delivered from the peer can be dismissed (never one ``resolve`` held); a dismissal ends with its
conflict; a crash leaves the dismissal either recorded or not. Every library is synthetic
(sync_helpers' desktop and phone)."""

from __future__ import annotations

import errno
import functools
import json
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from grimoire.store import LibraryStore, ProcessLocker, etag
from grimoire.sync import (
    DismissRefused, SyncInterrupted, apply_sync, dismiss, identify, plan_sync, resolve, sync, sync_until_quiet,
)
from grimoire.sync import resolution as sync_resolution
from grimoire.sync import session as sync_session
from grimoire.sync.documents import KeptEntry, parse_keeps, render_keeps
from grimoire.testing import CrashScenario, Invariant, explore_crashes, idempotent_recovery, no_residue, run_plans
from grimoire.testing.fileops import snapshot

from store_helpers import drop_empty_dirs, owner_handle
from sync_helpers import DESKTOP, PATHS, PHONE, WREN_A, WREN_B, edit_instance, handle, handles, kept, make_pair

PATH = PATHS["wren_a"]
RECORD = f".sync/peers/{PHONE}/keeps.json"  # the desktop's kept versions from the phone, and its dismissals


def _conflict(root: Path):
    """Both devices edited wren-a offline; one sync made it a conflict that keeps both versions."""
    desktop, phone = make_pair(root)
    edit_instance(desktop, WREN_A, 9)
    edit_instance(phone, WREN_A, 4)
    conflict = sync(desktop, phone, handles()).conflict(PATH)
    assert conflict is not None and conflict.code == "concurrent" and conflict.kept == {DESKTOP: True, PHONE: True}
    return desktop, phone


def _theirs(phone: LibraryStore) -> bytes:
    return (phone.root / PATH).read_bytes()


def _delete(store: LibraryStore, slots) -> None:
    for slot in slots:
        (store.root / slot).unlink()


def _entries(store: LibraryStore, peer: str = PHONE) -> dict:  # type: ignore[type-arg]
    return json.loads((store.root / f".sync/peers/{peer}/keeps.json").read_bytes())["entries"]


def _copies(stores, data: bytes) -> list[str]:  # type: ignore[no-untyped-def]
    """Every file on either device that holds ``data``: a version is lost once this is empty."""
    return [str(path) for store in stores for path in store.root.rglob("*")
            if path.is_file() and ".txn" not in path.parts and path.read_bytes() == data]


def test_a_dismissed_kept_version_is_not_delivered_again(tmp_path):
    desktop, phone = _conflict(tmp_path)
    theirs, mine = _theirs(phone), (desktop.root / PATH).read_bytes()
    assert theirs in kept(desktop)
    done = dismiss(desktop, PHONE, PATH, etag(theirs), handle())
    assert done.wrote and len(done.slots) == 1 and (desktop.root / done.slots[0]).read_bytes() == theirs
    _delete(desktop, done.slots)  # the app (or the person) removes the file once it is dismissed
    for _ in range(2):  # neither the next sync nor any later one delivers it again
        report = sync(desktop, phone, handles())
        conflict = report.conflict(PATH)
        assert conflict.code == "concurrent" and conflict.dismissed == (DESKTOP,)
        assert conflict.kept == {PHONE: True}  # the phone still keeps the desktop's version
        assert theirs not in kept(desktop)
    # nothing about the phone changed: its record is still its own, and it still keeps the desktop's
    assert _theirs(phone) == theirs and mine in kept(phone)
    assert (desktop.root / PATH).read_bytes() == mine


def test_dismissing_before_deleting_the_file_works_the_same(tmp_path):
    desktop, phone = _conflict(tmp_path)
    theirs = _theirs(phone)
    done = dismiss(desktop, PHONE, PATH, etag(theirs), handle())
    assert sync(desktop, phone, handles()).conflict(PATH).dismissed == (DESKTOP,)
    assert [(desktop.root / slot).read_bytes() for slot in done.slots] == [theirs]  # never rewritten nor deleted
    _delete(desktop, done.slots)
    sync(desktop, phone, handles())
    assert theirs not in kept(desktop)


def test_a_different_later_version_from_the_peer_is_still_kept(tmp_path):
    desktop, phone = _conflict(tmp_path)
    first = _theirs(phone)
    _delete(desktop, dismiss(desktop, PHONE, PATH, etag(first), handle()).slots)
    edit_instance(phone, WREN_A, 2)  # the phone moves on while the conflict stays open
    second = _theirs(phone)
    conflict = sync(desktop, phone, handles()).conflict(PATH)
    assert conflict.dismissed == () and conflict.kept == {DESKTOP: True, PHONE: True}
    assert second in kept(desktop) and first not in kept(desktop)
    # the old dismissal still holds while the conflict is open: the phone going back is not redelivered
    (phone.root / PATH).write_bytes(first)
    assert sync(desktop, phone, handles()).conflict(PATH).dismissed == (DESKTOP,)
    assert first not in kept(desktop)


def test_a_dismissal_ends_with_its_conflict_and_never_costs_the_other_devices_record(tmp_path):
    desktop, phone = _conflict(tmp_path)
    theirs, mine = _theirs(phone), (desktop.root / PATH).read_bytes()
    _delete(desktop, dismiss(desktop, PHONE, PATH, etag(theirs), handle()).slots)
    sync(desktop, phone, handles())
    # the dismissed version is still the phone's canonical record, so the person can still choose it
    resolve(desktop, phone, PATH, etag(theirs), {DESKTOP: etag((desktop.root / PATH).read_bytes()),
                                                 PHONE: etag(theirs)}, handles())
    assert (desktop.root / PATH).read_bytes() == theirs
    report = sync_until_quiet(desktop, phone, handles())
    assert report.conflicts == ()
    # forgotten with its conflict; only the desktop's own replaced version, which resolve held, stays recorded
    assert _entries(desktop) == {PATH: {"base": None, "delivered": [], "dismissed": [], "held": [etag(mine)]}}
    # the same bytes in a later conflict belong to that conflict: kept again
    (desktop.root / PATH).unlink()
    conflict = sync(desktop, phone, handles()).conflict(PATH)
    assert conflict.code == "deleted" and conflict.dismissed == () and theirs in kept(desktop)


def test_a_version_of_a_path_no_longer_in_conflict_is_never_dismissable(tmp_path):
    """The desktop keeps the phone's version; then both devices settle on the same new bytes and the
    conflict ends. The kept copy may now be the last copy of the phone's old version anywhere, so
    it is not dismissed (the app would delete it): the record says the path is not in conflict."""
    desktop, phone = _conflict(tmp_path)
    theirs = _theirs(phone)
    edit_instance(desktop, WREN_A, 20)
    (phone.root / PATH).write_bytes((desktop.root / PATH).read_bytes())
    assert sync(desktop, phone, handles()).conflict(PATH) is None
    assert _copies((desktop, phone), theirs) == [str(slot) for slot in (desktop.root / ".conflicts").rglob("*")
                                                 if slot.is_file() and slot.read_bytes() == theirs]
    assert etag(theirs) in _entries(desktop)[PATH]["delivered"] and _entries(desktop)[PATH]["base"] is None
    with pytest.raises(DismissRefused, match="no longer in conflict"):
        dismiss(desktop, PHONE, PATH, etag(theirs), handle())
    assert theirs in kept(desktop)


def test_dismissing_twice_records_once(tmp_path):
    desktop, phone = _conflict(tmp_path)
    tag = etag(_theirs(phone))
    assert dismiss(desktop, PHONE, PATH, tag, handle()).wrote
    record = (desktop.root / RECORD).read_bytes()
    assert not dismiss(desktop, PHONE, PATH, tag, handle()).wrote
    assert (desktop.root / RECORD).read_bytes() == record


@pytest.mark.parametrize("case", ["own version", "never delivered", "self", "not a device", "blob", "not a record",
                                  "bad etag"])
def test_requests_that_are_not_a_kept_version_are_refused_and_write_nothing(tmp_path, case):
    desktop, phone = _conflict(tmp_path)
    tag = etag(_theirs(phone))
    record = (desktop.root / RECORD).read_bytes()
    args = {
        "own version": (PHONE, PATH, etag((desktop.root / PATH).read_bytes())),
        "never delivered": (PHONE, PATH, etag(b"bytes no sync ever kept here")),
        "self": (DESKTOP, PATH, tag),
        "not a device": ("Not A Device", PATH, tag),
        "blob": (PHONE, "assets/blobs/ab/" + "ab" * 32 + ".png", tag),
        "not a record": (PHONE, "notes.txt", tag),
        "bad etag": (PHONE, PATH, "sha256:nope"),
    }[case]
    with pytest.raises(DismissRefused):
        dismiss(desktop, *args, handle())
    assert (desktop.root / RECORD).read_bytes() == record


def test_a_handle_that_may_not_write_kept_versions_may_not_dismiss_one(tmp_path):
    desktop, phone = _conflict(tmp_path)
    record = (desktop.root / RECORD).read_bytes()
    with pytest.raises(DismissRefused, match="not authorized"):
        dismiss(desktop, PHONE, PATH, etag(_theirs(phone)), owner_handle("sources", "campaigns"))
    assert (desktop.root / RECORD).read_bytes() == record


def test_a_version_resolve_held_is_never_dismissable(tmp_path):
    """The desktop's own version, held by ``resolve`` and kept nowhere else (the phone may not keep
    versions), is refused: dismissing it would name its only copy as safe to delete."""
    desktop, phone = make_pair(tmp_path)
    edit_instance(desktop, WREN_A, 9)
    edit_instance(phone, WREN_A, 4)
    mine, theirs = (desktop.root / PATH).read_bytes(), _theirs(phone)
    sync(desktop, phone, {DESKTOP: handle(), PHONE: owner_handle("sources", "campaigns")})
    assert mine not in kept(phone)
    resolve(desktop, phone, PATH, etag(theirs), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
    assert _copies((desktop, phone), mine) and mine in kept(desktop)  # held: the only copy
    with pytest.raises(DismissRefused, match="sync kept here"):
        dismiss(desktop, PHONE, PATH, etag(mine), handle())
    sync_until_quiet(desktop, phone, handles())
    with pytest.raises(DismissRefused):  # nor after the next syncs: a hold is never a delivered version
        dismiss(desktop, PHONE, PATH, etag(mine), handle())
    assert mine in kept(desktop)


def test_a_version_one_device_dismissed_is_never_lost_by_the_other_dismissing_its_hold(tmp_path):
    """The phone dismisses the desktop's version; the person then picks the phone's, so the desktop
    holds its own. The desktop may not dismiss that hold: it is the version's last copy."""
    desktop, phone = _conflict(tmp_path)
    mine, theirs = (desktop.root / PATH).read_bytes(), _theirs(phone)
    _delete(phone, dismiss(phone, DESKTOP, PATH, etag(mine), handle()).slots)
    resolve(desktop, phone, PATH, etag(theirs), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
    with pytest.raises(DismissRefused):
        dismiss(desktop, PHONE, PATH, etag(mine), handle())
    assert _copies((desktop, phone), mine)


def test_resolve_ends_a_dismissal_before_any_sync_so_a_new_conflict_keeps_the_same_bytes(tmp_path):
    desktop, phone = _conflict(tmp_path)
    mine, theirs = (desktop.root / PATH).read_bytes(), _theirs(phone)
    _delete(desktop, dismiss(desktop, PHONE, PATH, etag(theirs), handle()).slots)
    resolve(desktop, phone, PATH, etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
    assert not any(entry["dismissed"] for entry in _entries(desktop).values())  # dropped by resolve, in its own commit
    # a new conflict: the phone restores its old version, the desktop edits on
    (phone.root / PATH).write_bytes(theirs)
    edit_instance(desktop, WREN_A, 11)
    conflict = sync(desktop, phone, handles()).conflict(PATH)
    assert conflict.code == "concurrent" and conflict.dismissed == () and theirs in kept(desktop)


def test_a_resolve_that_crashed_after_its_data_never_lets_the_old_dismissal_through(tmp_path, monkeypatch):
    """resolve's copy landed and its marker moved the base, but the crash came before its archive step
    dropped the dismissal: the next conflict stands on another base, so the version is kept again."""
    desktop, phone = _conflict(tmp_path)
    mine, theirs = (desktop.root / PATH).read_bytes(), _theirs(phone)
    _delete(desktop, dismiss(desktop, PHONE, PATH, etag(theirs), handle()).slots)

    def crash(*args, **kwargs):
        raise RuntimeError("power loss after resolve's data commit")
    monkeypatch.setattr(sync_resolution, "_commit_state", crash)
    with pytest.raises(RuntimeError):
        resolve(desktop, phone, PATH, etag(mine), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
    monkeypatch.undo()
    assert _theirs(phone) == mine and etag(theirs) in _entries(desktop)[PATH]["dismissed"]  # the record survived
    # a new conflict: the phone restores its own held version, the desktop edits on
    (phone.root / PATH).write_bytes(theirs)
    edit_instance(desktop, WREN_A, 11)
    conflict = sync(desktop, phone, handles()).conflict(PATH)
    assert conflict.code == "missing_ancestor"  # the marker leaves the base uncertain
    assert conflict.dismissed == () and theirs in kept(desktop)


class _PowerLoss(Exception):
    """The process dies here: nothing after it runs, and the next open recovers."""


def test_a_hold_over_a_delivered_copy_survives_recovery_that_loses_the_staged_record(tmp_path, monkeypatch):
    """The desktop's own version is bytes the phone once delivered (the person copied the kept file into
    place), and the phone no longer has them anywhere. The person picks the phone's newer version, so
    resolve holds those bytes in the slot that already holds them. Power is lost once the chosen version
    is in place, and the kept-version record staged with it is gone before the app opens again (010's
    recovery then finishes the commit without it). The hold is still never dismissable: it is the only copy."""
    desktop, phone = _conflict(tmp_path)
    delivered = _theirs(phone)
    (desktop.root / PATH).write_bytes(delivered)
    edit_instance(phone, WREN_A, 2)
    newer = _theirs(phone)
    sync(desktop, phone, {DESKTOP: handle(), PHONE: owner_handle("sources", "campaigns")})  # the phone keeps nothing
    entry = _entries(desktop)[PATH]
    assert etag(delivered) in entry["delivered"] and entry["base"] is not None
    assert _copies((desktop, phone), delivered) == [str(desktop.root / PATH)] + [
        str(slot) for slot in (desktop.root / ".conflicts").rglob("*") if slot.is_file() and slot.read_bytes() == delivered]
    ops, real = desktop._access.ops, desktop._access.ops.replace

    def replace(source, target):  # type: ignore[no-untyped-def]
        real(source, target)
        if str(target).endswith(PATH) and ".txn" in str(source):
            raise _PowerLoss("power lost after the chosen version was published")
    monkeypatch.setattr(ops, "replace", replace)
    with pytest.raises(_PowerLoss):
        resolve(desktop, phone, PATH, etag(newer), {DESKTOP: etag(delivered), PHONE: etag(newer)}, handles())
    monkeypatch.undo()
    desktop.close()
    staging = desktop.root / ".txn"
    for staged in json.loads((staging / "journal.json").read_bytes())["entries"]:
        if staged["target"].endswith("/keeps.json"):  # the record staged by that commit is lost
            (staging / staged["temp"]).unlink(missing_ok=True)
    desktop = LibraryStore.open(desktop.root)
    assert (desktop.root / PATH).read_bytes() == newer and delivered in kept(desktop)
    with pytest.raises(DismissRefused):
        dismiss(desktop, PHONE, PATH, etag(delivered), handle())
    sync_until_quiet(desktop, phone, handles())
    with pytest.raises(DismissRefused):
        dismiss(desktop, PHONE, PATH, etag(delivered), handle())
    assert delivered in kept(desktop)


def _unreadable(monkeypatch, store: LibraryStore, data: bytes) -> list[str]:
    """Every kept file of ``store`` holding ``data`` fails with an I/O error until ``monkeypatch.undo``."""
    slots = [str(slot) for slot in (store.root / ".conflicts").rglob("*") if slot.is_file() and slot.read_bytes() == data]
    ops, real = store._access.ops, store._access.ops.read_bytes

    def read_bytes(path):  # type: ignore[no-untyped-def]
        if str(path) in slots:
            raise OSError(errno.EIO, "simulated I/O error", str(path))
        return real(path)
    monkeypatch.setattr(ops, "read_bytes", read_bytes)
    return slots


def test_a_version_whose_kept_file_cannot_be_read_is_not_dismissed(tmp_path, monkeypatch):
    """dismiss names the files that are safe to delete; one it cannot read might be anything (a hold,
    even), so it refuses and writes nothing until every one reads again."""
    desktop, phone = _conflict(tmp_path)
    theirs = _theirs(phone)
    record = (desktop.root / RECORD).read_bytes()
    assert _unreadable(monkeypatch, desktop, theirs)
    with pytest.raises(DismissRefused, match="cannot be read"):
        dismiss(desktop, PHONE, PATH, etag(theirs), handle())
    assert (desktop.root / RECORD).read_bytes() == record
    monkeypatch.undo()
    assert dismiss(desktop, PHONE, PATH, etag(theirs), handle()).wrote


def test_a_hold_whose_file_cannot_be_read_stays_recorded(tmp_path, monkeypatch):
    """A sync drops a hold only once its slot is confirmed gone; one that fails to read may still hold
    the bytes, so the hold stays recorded through the sync and is never dismissable afterwards."""
    desktop, phone = _conflict(tmp_path)
    mine, theirs = (desktop.root / PATH).read_bytes(), _theirs(phone)
    resolve(desktop, phone, PATH, etag(theirs), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
    assert _unreadable(monkeypatch, desktop, mine)
    sync(desktop, phone, handles())
    monkeypatch.undo()
    assert _entries(desktop)[PATH]["held"] == [etag(mine)]
    with pytest.raises(DismissRefused):
        dismiss(desktop, PHONE, PATH, etag(mine), handle())
    assert mine in kept(desktop)


def test_a_hold_stays_recorded_until_its_slot_is_gone(tmp_path):
    desktop, phone = _conflict(tmp_path)
    mine, theirs = (desktop.root / PATH).read_bytes(), _theirs(phone)
    resolve(desktop, phone, PATH, etag(theirs), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
    assert sync_until_quiet(desktop, phone, handles()).conflicts == ()
    assert _entries(desktop)[PATH]["held"] == [etag(mine)]  # the conflict ended; the hold did not
    for slot in (desktop.root / ".conflicts").rglob("*"):  # the person deletes it by hand
        if slot.is_file() and slot.read_bytes() == mine:
            slot.unlink()
    sync(desktop, phone, handles())
    assert _entries(desktop).get(PATH, {}).get("held", []) == []


def test_the_peers_version_found_in_a_hold_is_never_recorded_as_delivered(tmp_path):
    """The phone comes back to the very bytes the desktop's hold keeps: keep-both finds them standing in
    the hold's slot and writes nothing, so they are not delivered, and the hold is never dismissable."""
    desktop, phone = _conflict(tmp_path)
    mine, theirs = (desktop.root / PATH).read_bytes(), _theirs(phone)
    resolve(desktop, phone, PATH, etag(theirs), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
    (phone.root / PATH).write_bytes(mine)
    edit_instance(desktop, WREN_A, 11)
    for _ in range(2):
        conflict = sync(desktop, phone, handles()).conflict(PATH)
        assert conflict.code == "concurrent" and conflict.kept[DESKTOP]
    assert etag(mine) not in _entries(desktop)[PATH]["delivered"] and _entries(desktop)[PATH]["held"] == [etag(mine)]
    with pytest.raises(DismissRefused):
        dismiss(desktop, PHONE, PATH, etag(mine), handle())
    assert mine in kept(desktop)


def test_a_hold_of_one_pair_is_never_dismissed_through_another(tmp_path):
    """A device paired twice keeps every pair's kept versions in one ``.conflicts``: a third device that
    delivered the same bytes (its record written here as its sync would) still cannot have them dismissed."""
    desktop, phone = _conflict(tmp_path)
    mine, theirs = (desktop.root / PATH).read_bytes(), _theirs(phone)
    other = desktop.root / ".sync/peers/laptop/keeps.json"
    other.parent.mkdir()
    delivered = render_keeps((DESKTOP, "laptop"), {PATH: KeptEntry(None, frozenset({etag(mine)}))})
    other.write_bytes(delivered)
    resolve(desktop, phone, PATH, etag(theirs), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
    # the hold takes those bytes out of every pair's delivered versions, in its own commit
    assert other.read_bytes() != delivered and _entries(desktop, "laptop") == {}
    with pytest.raises(DismissRefused):
        dismiss(desktop, "laptop", PATH, etag(mine), handle())
    other.write_bytes(delivered)  # and were the laptop's record to claim them again, the hold still refuses
    with pytest.raises(DismissRefused, match="resolve held"):
        dismiss(desktop, "laptop", PATH, etag(mine), handle())
    assert mine in kept(desktop)


def test_a_third_device_delivering_a_holds_bytes_never_makes_them_dismissable(tmp_path):
    """The desktop holds its old version after resolving with the phone; a tablet then comes back to those
    very bytes. Keep-both finds them already in a slot here, so the tablet delivered nothing."""
    desktop, phone = make_pair(tmp_path)
    tablet = LibraryStore.create(tmp_path / "tablet")
    identify(tablet, "tablet")
    pair = {DESKTOP: handle(), "tablet": handle()}
    sync(desktop, tablet, pair)
    edit_instance(desktop, WREN_A, 9)
    edit_instance(phone, WREN_A, 4)
    mine, theirs = (desktop.root / PATH).read_bytes(), _theirs(phone)
    sync(desktop, phone, handles())
    resolve(desktop, phone, PATH, etag(theirs), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
    (tablet.root / PATH).write_bytes(mine)
    edit_instance(desktop, WREN_A, 12)
    conflict = sync(desktop, tablet, pair).conflict(PATH)
    assert conflict is not None and conflict.kept[DESKTOP]
    with pytest.raises(DismissRefused):
        dismiss(desktop, "tablet", PATH, etag(mine), handle())
    assert mine in kept(desktop)


def test_a_new_identity_keeps_every_hold_recorded(tmp_path):
    desktop, phone = _conflict(tmp_path)
    mine, theirs = (desktop.root / PATH).read_bytes(), _theirs(phone)
    resolve(desktop, phone, PATH, etag(theirs), {DESKTOP: etag(mine), PHONE: etag(theirs)}, handles())
    identify(desktop, "desk-two", replace=True)
    record = parse_keeps((desktop.root / RECORD).read_bytes(), ("desk-two", PHONE))
    assert record.state == "valid" and dict(record.entries) == {PATH: KeptEntry(None, frozenset(), held=frozenset({etag(mine)}))}
    sync_until_quiet(desktop, phone, {"desk-two": handle(), PHONE: handle()})
    with pytest.raises(DismissRefused):
        dismiss(desktop, PHONE, PATH, etag(mine), handle())
    assert mine in kept(desktop)


def test_a_dismissal_holds_only_on_the_archive_base_its_conflict_stands_on(tmp_path):
    """A record whose base the archives no longer hold (here written by hand, as a crash between a
    resolve's commits could leave it) dismisses nothing: the version is delivered again."""
    desktop, phone = _conflict(tmp_path)
    theirs = _theirs(phone)
    _delete(desktop, dismiss(desktop, PHONE, PATH, etag(theirs), handle()).slots)
    record = desktop.root / RECORD
    view = parse_keeps(record.read_bytes(), (DESKTOP, PHONE))
    moved = replace(view.entries[PATH], base="agreed:" + etag(b"another conflict's base"))
    record.write_bytes(render_keeps((DESKTOP, PHONE), {PATH: moved}))
    report = sync(desktop, phone, handles())
    assert report.notices == () and report.conflict(PATH).dismissed == () and theirs in kept(desktop)


def test_a_keep_is_recorded_in_the_commit_that_writes_it(tmp_path, monkeypatch):
    """A crash after the data commit, before the sync's later steps: the keep and its delivery
    landed together, so the version can be dismissed at once, and the next sync honours it."""
    desktop, phone = make_pair(tmp_path)
    edit_instance(desktop, WREN_A, 9)
    edit_instance(phone, WREN_A, 4)
    theirs = _theirs(phone)

    def crash(*args, **kwargs):
        raise RuntimeError("power loss before the pending step")
    monkeypatch.setattr(sync_session, "_commit_pending", crash)
    with pytest.raises(RuntimeError):
        sync(desktop, phone, handles())
    monkeypatch.undo()
    assert theirs in kept(desktop)
    _delete(desktop, dismiss(desktop, PHONE, PATH, etag(theirs), handle()).slots)
    assert sync(desktop, phone, handles()).conflict(PATH).dismissed == (DESKTOP,) and theirs not in kept(desktop)


def test_a_sync_interrupted_after_its_data_commit_leaves_its_keeps_dismissable(tmp_path):
    """No crash: the person edits a copied record while the sync verifies it, so the sync stops after
    its data commits. Its keeps are recorded already; nothing a later sync does unrecords them."""
    desktop, phone = make_pair(tmp_path)
    edit_instance(desktop, WREN_A, 9)
    edit_instance(phone, WREN_A, 4)
    edit_instance(desktop, WREN_B, 7)  # a copy to the phone, so there is something to verify
    theirs, copied = _theirs(phone), phone.root / PATHS["wren_b"]

    def observer(step):  # type: ignore[no-untyped-def]
        if step.kind == "data" and step.device == PHONE:
            copied.write_bytes(copied.read_bytes().replace(b"7", b"8"))
    with pytest.raises(SyncInterrupted):
        apply_sync(plan_sync(desktop, phone, handles()), handles(), observer=observer)
    assert theirs in kept(desktop) and etag(theirs) in _entries(desktop)[PATH]["delivered"]
    sync_until_quiet(desktop, phone, handles())
    assert dismiss(desktop, PHONE, PATH, etag(theirs), handle()).wrote


def test_a_new_identity_leaves_no_dismissal_behind_and_no_notice(tmp_path):
    desktop, phone = _conflict(tmp_path)
    theirs = _theirs(phone)
    dismiss(desktop, PHONE, PATH, etag(theirs), handle())
    identify(desktop, "desk-two", replace=True)
    for _ in range(2):
        report = sync(desktop, phone, {"desk-two": handle(), PHONE: handle()})
        assert not any("dismissed kept versions" in notice for notice in report.notices)
    (phone.root / PATH).write_bytes((desktop.root / PATH).read_bytes())  # the conflict ends
    report = sync(desktop, phone, {"desk-two": handle(), PHONE: handle()})
    assert report.conflicts == () and not any("dismissed kept versions" in notice for notice in report.notices)


def test_a_damaged_dismissal_record_is_reported_and_honours_nothing(tmp_path):
    desktop, phone = _conflict(tmp_path)
    theirs = _theirs(phone)
    _delete(desktop, dismiss(desktop, PHONE, PATH, etag(theirs), handle()).slots)
    record = desktop.root / RECORD
    record.write_bytes(record.read_bytes().replace(b"wren-a", b"wren-b"))  # its check digest no longer holds
    report = sync(desktop, phone, handles())
    assert any("dismissed kept versions ignored" in notice for notice in report.notices)
    assert report.conflict(PATH).dismissed == () and theirs in kept(desktop)  # delivered, as before 028
    # dismissing again replaces the record nothing was taken from
    _delete(desktop, dismiss(desktop, PHONE, PATH, etag(theirs), handle()).slots)
    report = sync(desktop, phone, handles())
    assert report.notices == () and theirs not in kept(desktop)


def test_a_dismissal_on_the_phone_is_its_own(tmp_path):
    """Each device dismisses only what it keeps: the desktop still receives the phone's version."""
    desktop, phone = _conflict(tmp_path)
    mine = (desktop.root / PATH).read_bytes()
    _delete(phone, dismiss(phone, DESKTOP, PATH, etag(mine), handle()).slots)
    conflict = sync(desktop, phone, handles()).conflict(PATH)
    assert conflict.dismissed == (PHONE,) and conflict.kept == {DESKTOP: True}
    assert mine not in kept(phone) and _theirs(phone) in kept(desktop)


# -- under the fault harness: a crash or an I/O error at the dismissal's commit -------------------

DEVICES = (DESKTOP, PHONE)
RESIDUE = no_residue(*(f"{device}/.txn/{name}" for device in DEVICES for name in ("*.new", "journal.json")))


@functools.cache
def _fault_fixture():
    """The conflict, with the desktop's kept copy of the phone's version already deleted by the person."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        desktop, phone = _conflict(root)
        theirs = _theirs(phone)
        for slot in desktop.conflicts():
            if (desktop.root / slot).read_bytes() == theirs:
                (desktop.root / slot).unlink()
        desktop.close(), phone.close()
        return tuple(snapshot(root).items()), theirs


def _fault_scenario():
    tree, theirs = _fault_fixture()
    locker = ProcessLocker()

    def setup(ops, root):
        for key, data in tree:
            ops.make_dirs(root / key) if data is None else ops.write_bytes(root / key, data)

    def opened(ops, root, function):
        desktop = LibraryStore.open(root / DESKTOP, ops=ops, locker=locker)
        try:
            phone = LibraryStore.open(root / PHONE, ops=ops, locker=locker)
            try:
                function(desktop, phone)
            finally:
                phone.close()
        finally:
            desktop.close()

    def action(ops, root):
        opened(ops, root, lambda desktop, phone: dismiss(desktop, PHONE, PATH, etag(theirs), handle()))

    def recover(ops, root):  # a restarted app: reopen (010 recovery), then sync
        opened(ops, root, lambda desktop, phone: sync(desktop, phone, handles()))

    def before_or_after(outcome):
        """Recorded, and the version is not delivered; or not recorded, and it is delivered as before.
        The phone's record and the conflict are untouched either way."""
        record = outcome.recovered.get(f"{DESKTOP}/{RECORD}")
        kept_here = {value for key, value in outcome.recovered.items()
                     if key.startswith(f"{DESKTOP}/.conflicts/") and value is not None}
        if outcome.recovered.get(f"{PHONE}/{PATH}") != theirs:
            return "the phone's own record changed"
        entry = json.loads(record)["entries"].get(PATH) if record is not None else None
        if entry is None or entry["delivered"] != [etag(theirs)]:
            return f"the record of the delivered version was lost: {entry}"
        if entry["dismissed"] == []:
            return None if theirs in kept_here else "nothing was recorded, yet the version was not delivered"
        if entry["dismissed"] != [etag(theirs)]:
            return f"the record is neither before nor after: {entry}"
        return "a dismissed version was delivered" if theirs in kept_here else None
    return CrashScenario("dismiss", setup, action, recover,
                         (Invariant("before_or_after", before_or_after), idempotent_recovery(), RESIDUE),
                         normalize=drop_empty_dirs)


def test_a_crash_or_error_at_the_dismissal_commit_leaves_it_recorded_or_not(tmp_path):
    with tempfile.TemporaryDirectory() as folder:
        baseline = run_plans(_fault_scenario(), [], Path(folder))
        baseline.assert_ok()
        trace = baseline.runs[0].action_trace
    commit = [op.index for op in trace if op.kind == "replace" and (op.target or "").endswith("/.txn/journal.json")]
    assert len(commit) == 1  # one 010 transaction
    publish = next(op.index for op in trace[commit[0] + 1:] if op.kind == "replace" and "/.txn/" in op.path)
    points = {commit[0]: frozenset({"before", "after", "fail"}), publish: frozenset({"before", "fail"})}
    report = explore_crashes(_fault_scenario(), tmp_path, errors=True, torn=False,
                             select=lambda op: points.get(op.index, frozenset()))
    report.assert_ok()
    outcomes = {json.loads(run.recovered[f"{DESKTOP}/{RECORD}"])["entries"][PATH]["dismissed"] == []
                for run in report.runs}
    assert outcomes == {True, False}  # some points land before the commit, others after it

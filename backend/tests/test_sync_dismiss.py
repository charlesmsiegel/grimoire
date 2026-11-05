"""028: dismissing a kept version. A dismissed version is not delivered again for the same bytes; a
different later version still is; the other device's record is never touched; only a version a sync
delivered from the peer can be dismissed (never one ``resolve`` held); a dismissal ends with its
conflict; a crash leaves the dismissal either recorded or not. Every library is synthetic
(sync_helpers' desktop and phone)."""

from __future__ import annotations

import functools
import json
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from grimoire.store import LibraryStore, ProcessLocker, etag
from grimoire.sync import DismissRefused, dismiss, identify, resolve, sync, sync_until_quiet
from grimoire.sync import session as sync_session
from grimoire.sync.documents import parse_keeps, render_keeps
from grimoire.testing import CrashScenario, Invariant, explore_crashes, idempotent_recovery, no_residue, run_plans
from grimoire.testing.fileops import snapshot

from store_helpers import drop_empty_dirs, owner_handle
from sync_helpers import DESKTOP, PATHS, PHONE, WREN_A, edit_instance, handle, handles, kept, make_pair

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
    theirs = _theirs(phone)
    _delete(desktop, dismiss(desktop, PHONE, PATH, etag(theirs), handle()).slots)
    sync(desktop, phone, handles())
    # the dismissed version is still the phone's canonical record, so the person can still choose it
    resolve(desktop, phone, PATH, etag(theirs), {DESKTOP: etag((desktop.root / PATH).read_bytes()),
                                                 PHONE: etag(theirs)}, handles())
    assert (desktop.root / PATH).read_bytes() == theirs
    report = sync_until_quiet(desktop, phone, handles())
    assert report.conflicts == ()
    assert _entries(desktop) == {}  # forgotten with its conflict
    # the same bytes in a later conflict belong to that conflict: kept again
    (desktop.root / PATH).unlink()
    conflict = sync(desktop, phone, handles()).conflict(PATH)
    assert conflict.code == "deleted" and conflict.dismissed == () and theirs in kept(desktop)


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
    with pytest.raises(DismissRefused, match="completed sync kept here"):
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
    assert _entries(desktop) == {}  # dropped by resolve itself, in its own commit
    # a new conflict: the phone restores its old version, the desktop edits on
    (phone.root / PATH).write_bytes(theirs)
    edit_instance(desktop, WREN_A, 11)
    conflict = sync(desktop, phone, handles()).conflict(PATH)
    assert conflict.code == "concurrent" and conflict.dismissed == () and theirs in kept(desktop)


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


def test_a_keep_is_dismissable_only_once_a_completed_sync_recorded_it(tmp_path, monkeypatch):
    """A crash after the keep but before the sync's last step leaves it unrecorded: dismissing is
    refused (the safe direction) until the next sync records it."""
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
    with pytest.raises(DismissRefused):
        dismiss(desktop, PHONE, PATH, etag(theirs), handle())
    sync(desktop, phone, handles())  # the slot already holds it: recorded now
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

"""028: dismissing a kept version. A dismissed version is not delivered again for the same bytes; a
different later version still is; the other device's record is never touched; a crash leaves the
dismissal either recorded or not. Every library is synthetic (sync_helpers' desktop and phone)."""

from __future__ import annotations

import functools
import json
import tempfile
from pathlib import Path

import pytest

from grimoire.store import LibraryStore, ProcessLocker, etag
from grimoire.sync import DismissRefused, dismiss, resolve, sync, sync_until_quiet
from grimoire.testing import CrashScenario, Invariant, explore_crashes, idempotent_recovery, no_residue, run_plans
from grimoire.testing.fileops import snapshot

from store_helpers import drop_empty_dirs, owner_handle
from sync_helpers import DESKTOP, PATHS, PHONE, WREN_A, edit_instance, handle, handles, kept, make_pair

PATH = PATHS["wren_a"]
RECORD = f".sync/peers/{PHONE}/dismissed.json"  # the desktop's dismissals for its pair with the phone


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
    assert json.loads((desktop.root / RECORD).read_bytes())["entries"] == {}  # forgotten with its conflict
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


@pytest.mark.parametrize("case", ["own version", "self", "not a device", "blob", "not a record", "bad etag"])
def test_requests_that_are_not_a_kept_version_are_refused_and_write_nothing(tmp_path, case):
    desktop, phone = _conflict(tmp_path)
    tag = etag(_theirs(phone))
    args = {
        "own version": (PHONE, PATH, etag((desktop.root / PATH).read_bytes())),
        "self": (DESKTOP, PATH, tag),
        "not a device": ("Not A Device", PATH, tag),
        "blob": (PHONE, "assets/blobs/ab/" + "ab" * 32 + ".png", tag),
        "not a record": (PHONE, "notes.txt", tag),
        "bad etag": (PHONE, PATH, "sha256:nope"),
    }[case]
    with pytest.raises(DismissRefused):
        dismiss(desktop, *args, handle())
    assert not (desktop.root / RECORD).exists()


def test_a_handle_that_may_not_write_kept_versions_may_not_dismiss_one(tmp_path):
    desktop, phone = _conflict(tmp_path)
    with pytest.raises(DismissRefused, match="not authorized"):
        dismiss(desktop, PHONE, PATH, etag(_theirs(phone)), owner_handle("sources", "campaigns"))
    assert not (desktop.root / RECORD).exists()


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
        if record is None:
            return None if theirs in kept_here else "nothing was recorded, yet the version was not delivered"
        entries = json.loads(record)["entries"]
        if entries != {PATH: [etag(theirs)]}:
            return f"the record is neither before nor after: {entries}"
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
    outcomes = {run.recovered.get(f"{DESKTOP}/{RECORD}") is None for run in report.runs}
    assert outcomes == {True, False}  # some points land before the commit, others after it

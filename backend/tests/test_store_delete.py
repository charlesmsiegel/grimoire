"""The store's deletion (028): ``Transaction.delete_exact``, always paired with a hold of what it removes."""

from __future__ import annotations

import pytest

from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.store import (
    InvalidEnvelope, LibraryStore, ProcessLocker, RecordNotFound, RevisionReferenced, StaleWrite, WriteConflict, WriteDenied, etag,
)
from grimoire.store.journal import Entry, Journal, parse
from grimoire.store.errors import RecoveryFailed
from grimoire.testing import (
    CrashScenario, Interleave, Invariant, Target, atomic_outcome, completes_after, explore_crashes, idempotent_recovery,
    no_residue, preserves_interleave, run_plans,
)
from grimoire.testing.fileops import snapshot

from store_helpers import OWNER, WREN_A, drop_empty_dirs, owner_handle, seed

ALL = ("sources", "campaigns", "kept")
WREN_A_PATH = "campaigns/c1/instances/character/wren-a.json"
WREN_B_PATH = "campaigns/c2/instances/character/wren-b.json"
R1_PATH = "sources/world/harbor/_source/records/character/wren/r1.json"
MANIFEST_PATH = "sources/world/harbor/_source/manifest.json"


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as library:
        seed(library)
        yield library


def _kept(store: LibraryStore) -> list[bytes]:
    return sorted((store.root / path).read_bytes() for path in store.conflicts())


def _delete(store: LibraryStore, *paths: str, handle=None) -> None:
    txn = store.transaction()
    for path in paths:
        data = (store.root / path).read_bytes()
        txn.keep_version(path, data, regenerable=False)
        txn.delete_exact(path, etag(data))
    txn.commit(handle or owner_handle(*ALL))


def test_a_deletion_removes_the_record_and_keeps_its_bytes_as_a_hold(store):
    data = (store.root / WREN_A_PATH).read_bytes()
    _delete(store, WREN_A_PATH)
    assert not (store.root / WREN_A_PATH).exists()
    with pytest.raises(RecordNotFound):
        store.read(WREN_A)
    assert WREN_A not in store.load().instances
    assert _kept(store) == [data]  # recoverable beside where it was, never just unlinked


def test_a_source_is_deleted_as_a_whole_in_one_transaction(store):
    paths = (R1_PATH, MANIFEST_PATH, WREN_A_PATH, WREN_B_PATH)  # both wrens pin r1, so they go in the same transaction
    removed = sorted((store.root / path).read_bytes() for path in paths)
    _delete(store, *paths)
    assert not any((store.root / path).exists() for path in paths)
    assert _kept(store) == removed
    assert "world:harbor" not in store.load().sources


def test_a_revision_a_record_staying_here_pins_is_never_deleted(store):
    """Checked under the lock at commit, whatever a sync planned earlier: wren-a and wren-b pin r1."""
    with pytest.raises(RevisionReferenced):
        _delete(store, R1_PATH, MANIFEST_PATH)
    assert (store.root / R1_PATH).exists() and (store.root / MANIFEST_PATH).exists() and _kept(store) == []


@pytest.mark.parametrize("keep", ["none", "regenerable", "other bytes"])
def test_a_deletion_without_a_hold_of_exactly_its_bytes_is_refused(store, keep):
    data = (store.root / WREN_A_PATH).read_bytes()
    txn = store.transaction()
    if keep == "regenerable":
        txn.keep_version(WREN_A_PATH, data, regenerable=True)
    elif keep == "other bytes":
        txn.keep_version(WREN_A_PATH, data + b" ", regenerable=False)
    txn.delete_exact(WREN_A_PATH, etag(data))
    with pytest.raises(InvalidEnvelope):
        txn.check()
    with pytest.raises(InvalidEnvelope):
        txn.commit(owner_handle(*ALL))
    assert (store.root / WREN_A_PATH).read_bytes() == data


def test_a_deletion_of_bytes_no_longer_there_is_stale(store):
    data = (store.root / WREN_A_PATH).read_bytes()
    txn = store.transaction()
    txn.keep_version(WREN_A_PATH, data, regenerable=False)
    txn.delete_exact(WREN_A_PATH, etag(data))
    (store.root / WREN_A_PATH).write_bytes(data.replace(b'"hp": 3', b'"hp": 8'))  # edited since
    with pytest.raises((StaleWrite, WriteConflict)):
        txn.commit(owner_handle(*ALL))
    assert b'"hp": 8' in (store.root / WREN_A_PATH).read_bytes()


@pytest.mark.parametrize("path", [
    "campaigns/c1/events/e1.json",  # 020 knowledge: create-only, a missing one is damage
    "campaigns/c1/transcripts/talk/0000000001.json",  # 036 transcript page: create-only
    "campaigns/c1/images/wren.json",  # 018 image object: create-only
    "assets/blobs/00/" + "0" * 64 + ".png",  # content-addressed
    "campaigns/c1/accepted/a1.json",  # held families are never touched
    ".sync/peers/phone/archive.json", "notes.txt", 7,
])
def test_only_the_families_sync_may_delete_can_be_deleted(store, path):
    with pytest.raises(InvalidEnvelope):
        store.transaction().delete_exact(path, etag(b"x"))


def test_a_deletion_names_an_etag(store):
    with pytest.raises(InvalidEnvelope):
        store.transaction().delete_exact(WREN_A_PATH, "not an etag")


def test_a_deletion_is_authorized_on_its_own_address_and_never_by_an_approval_to_write(store):
    """``preview`` shows the deletion document, which an approval must cover: one for the record's
    bytes (an approval to write it) never removes it."""
    data = (store.root / WREN_A_PATH).read_bytes()

    def staged():
        txn = store.transaction()
        txn.keep_version(WREN_A_PATH, data, regenerable=False)
        return txn, txn.delete_exact(WREN_A_PATH, etag(data))
    txn, key = staged()
    document = txn.preview()[key]
    assert key == WREN_A_PATH and document != data  # the same address a write of the record is approved at
    assert b"grimoire.deletion/1" in document and etag(data).encode() in document
    with pytest.raises(WriteDenied):  # no grant over campaigns
        txn.commit(owner_handle("sources", "kept"))
    assert (store.root / WREN_A_PATH).read_bytes() == data
    authority = Authorizer([OWNER])
    for number, scope in enumerate(ALL):
        authority.add_grant(Grant(f"g{number}", "app:sync", frozenset({Permission.APPLY}), ResourcePath.parse(scope),
                                  OWNER))
    txn, _ = staged()
    hold = next(other for other in txn.staged() if other != key)
    held = txn.preview()[hold]
    for number, (where, payload) in enumerate(((key, data), (key, document), (hold, held), (hold, held))):
        authority.register_approval(Approval(f"a{number}", "app:sync", Permission.APPLY, ResourcePath.parse(where),
                                             payload_digest(payload), OWNER))
    with pytest.raises(WriteDenied):
        txn.commit(authority.handle_for("app:sync"), {key: "a0", hold: "a2"})
    assert (store.root / WREN_A_PATH).read_bytes() == data
    txn, _ = staged()
    txn.commit(authority.handle_for("app:sync"), {key: "a1", hold: "a3"})
    assert not (store.root / WREN_A_PATH).exists()


def test_a_removal_entry_is_journaled_only_for_a_deletable_record():
    tag = etag(b"x")
    data = Journal("commit", (Entry(tuple(WREN_A_PATH.split("/")), "rec~tmp1.new", tag, etag(b"d"), "remove"),)).render()
    assert parse(data).entries[0].kind == "remove"
    for target, old in [("campaigns/c1/events/e1.json", tag), (WREN_A_PATH, None),
                        (".sync/peers/phone/archive.json", tag)]:
        bad = Journal("commit", (Entry(tuple(target.split("/")), "rec~tmp1.new", old, etag(b"d"), "remove"),)).render()
        with pytest.raises(RecoveryFailed):
            parse(bad)


# -- crashes and external edits ---------------------------------------------------------------

def _replay(ops, root, template):
    for key, data in sorted(template.items()):
        if data is None:
            ops.make_dirs(root / "lib" / key)
        else:
            ops.write_bytes(root / "lib" / key, data)


def _scenario(tmp_path, paths, invariants):
    locker = ProcessLocker()
    tmp_path.mkdir(parents=True, exist_ok=True)
    with LibraryStore.create(tmp_path / "lib", locker=locker) as library:
        seed(library)
    template = snapshot(tmp_path / "lib")

    def action(ops, root):
        with LibraryStore.open(root / "lib", ops=ops, locker=locker) as library:
            _delete(library, *paths)

    return CrashScenario(
        "delete", lambda ops, root: _replay(ops, root, template), action,
        lambda ops, root: LibraryStore.open(root / "lib", ops=ops, locker=locker).close(),
        invariants, normalize=drop_empty_dirs), template


@pytest.mark.parametrize("paths", [(WREN_A_PATH,), (R1_PATH, MANIFEST_PATH, WREN_A_PATH, WREN_B_PATH)], ids=["instance", "source"])
def test_a_deletion_survives_every_crash_torn_write_and_error(tmp_path, paths):
    """Before its commit point nothing happened; after it, recovery finishes it: the record gone and
    its hold in place. Never the record gone without its hold."""
    def kept_somewhere(outcome):
        for path in paths:
            data = outcome.before[f"lib/{path}"]
            if outcome.recovered.get(f"lib/{path}") != data and data not in [
                    value for key, value in outcome.recovered.items() if key.startswith("lib/.conflicts/")]:
                return f"{path} is gone and its bytes are kept nowhere"
        return None
    scenario, _ = _scenario(tmp_path / "setup", paths, (
        atomic_outcome(), idempotent_recovery(), no_residue("lib/.txn/*.new", "lib/.txn/journal.json"),
        completes_after(Target("replace", target="lib/.txn/journal.json")), Invariant("kept", kept_somewhere)))
    report = explore_crashes(scenario, tmp_path / "errors", errors=True)
    report.assert_ok()
    assert report.points_explored > 10
    explore_crashes(scenario, tmp_path / "recovery", recovery_crashes=True,
                    select=lambda op: op.kind in ("replace", "remove")).assert_ok()


def test_an_edit_racing_the_removal_is_kept_and_the_hold_stays(tmp_path):
    """A human edit after the commit point, up to the removal's guard read: the edit is the human's
    file, never removed (``WriteConflict``), and the hold of the removed bytes stays. Between that
    read and the unlink is 010's pinned R7 window, the same as for a replace."""
    scenario, template = _scenario(tmp_path / "setup", (WREN_A_PATH,), (preserves_interleave(), idempotent_recovery()))
    data = template[WREN_A_PATH]
    human = data.replace(b'"hp": 3', b'"hp": 11')
    trace = run_plans(scenario, [], tmp_path / "base").runs[0].action_trace
    commit = next(op.index for op in trace if op.kind == "replace" and op.target == "lib/.txn/journal.json")
    removal = next(op.index for op in trace if op.kind == "remove" and op.path == f"lib/{WREN_A_PATH}")

    def edit(ops, root):
        ops.write_bytes(root / "lib" / WREN_A_PATH, human)
    report = run_plans(scenario, [[Interleave(index, edit)] for index in range(commit + 1, removal)], tmp_path / "race")
    report.assert_ok()
    for run in report.runs[1:]:
        assert "WriteConflict" in run.action_error and run.recovered[f"lib/{WREN_A_PATH}"] == human
        assert data in [value for key, value in run.recovered.items() if key.startswith("lib/.conflicts/")]
    window = run_plans(scenario, [[Interleave(removal, edit)]], tmp_path / "r7")
    assert "preserves_interleave" in {violation.invariant for violation in window.violations}  # R7, pinned


def test_an_edit_racing_a_source_deletion_keeps_all_of_that_source(tmp_path):
    """A source goes as a whole or not at all: a human edit to its manifest after the commit point, up
    to the read just before its first removal, keeps every record of it (its revision too). After
    that read only reads come before the removal: 010's R7 window, pinned, the removed bytes held."""
    paths = (R1_PATH, MANIFEST_PATH, WREN_A_PATH, WREN_B_PATH)
    scenario, template = _scenario(tmp_path / "setup", paths, (preserves_interleave(), idempotent_recovery()))
    human = template[MANIFEST_PATH].replace(b'"Harbor"', b'"Harbour"')
    trace = run_plans(scenario, [], tmp_path / "base").runs[0].action_trace
    commit = next(op.index for op in trace if op.kind == "replace" and op.target == "lib/.txn/journal.json")
    removal = next(op.index for op in trace if op.kind == "remove" and op.path == f"lib/{R1_PATH}")
    check = max(op.index for op in trace if op.kind == "read_bytes" and op.path == f"lib/{MANIFEST_PATH}"
                and op.index < removal)
    assert all(op.kind in ("read_bytes", "list_dir", "exists") for op in trace if check <= op.index < removal)  # nothing written

    def edit(ops, root):
        ops.write_bytes(root / "lib" / MANIFEST_PATH, human)
    report = run_plans(scenario, [[Interleave(index, edit)] for index in range(commit + 1, check + 1)], tmp_path / "race")
    report.assert_ok()
    for run in report.runs[1:]:
        assert "WriteConflict" in run.action_error and run.recovered[f"lib/{MANIFEST_PATH}"] == human
        assert run.recovered[f"lib/{R1_PATH}"] == template[R1_PATH]
    window = run_plans(scenario, [[Interleave(check + 1, edit)]], tmp_path / "r7")
    run = window.runs[1]
    assert run.recovered[f"lib/{MANIFEST_PATH}"] == human and f"lib/{R1_PATH}" not in run.recovered
    assert template[R1_PATH] in [value for key, value in run.recovered.items() if key.startswith("lib/.conflicts/")]

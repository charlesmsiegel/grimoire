"""The store's deletion (028): ``Transaction.delete_exact``, always paired with a hold of what it removes."""

from __future__ import annotations

import shutil
from dataclasses import replace

import pytest

from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.store import (
    InvalidEnvelope, LibraryStore, ProcessLocker, RecordNotFound, RevisionReferenced, StaleWrite, WriteConflict, WriteDenied, etag,
)
from grimoire.store.journal import Entry, Journal, parse
from grimoire.store.errors import RecoveryFailed
from grimoire.testing import (
    Crash, CrashScenario, Interleave, Invariant, Target, atomic_outcome, completes_after, explore_crashes,
    idempotent_recovery, no_residue, preserves_interleave, preserves_meddle, run_plans,
)
from grimoire.testing.fileops import snapshot

from store_helpers import (
    HARBOR, OWNER, WREN, WREN_A, WREN_B, WREN_R2, drop_empty_dirs, harbor_manifest, instance, owner_handle, seed, wren,
)

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
    """A commit's removals happen all or none: a human edit to the manifest after the commit point, up
    to the read just before the first removal, keeps every record it deletes (the revision too). After
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
    for run in report.runs[1:]:  # nothing of the batch is removed, the records that used the source included
        assert "WriteConflict" in run.action_error and run.recovered[f"lib/{MANIFEST_PATH}"] == human
        assert all(run.recovered[f"lib/{path}"] == template[path] for path in (R1_PATH, WREN_A_PATH, WREN_B_PATH))
    window = run_plans(scenario, [[Interleave(check + 1, edit)]], tmp_path / "r7")
    run = window.runs[1]
    assert run.recovered[f"lib/{MANIFEST_PATH}"] == human and f"lib/{R1_PATH}" not in run.recovered
    assert template[R1_PATH] in [value for key, value in run.recovered.items() if key.startswith("lib/.conflicts/")]


def test_a_damaged_removal_keeps_all_of_its_source(tmp_path):
    """A manifest's removal whose staged document is damaged once the holds are published (past the
    all-or-nothing check) is dropped, and so is every removal of that source: never its revisions
    gone while its manifest stays."""
    paths = (R1_PATH, MANIFEST_PATH, WREN_A_PATH, WREN_B_PATH)
    scenario, template = _scenario(tmp_path / "setup", paths, (idempotent_recovery(),))
    trace = run_plans(scenario, [], tmp_path / "base").runs[0].action_trace
    removal = next(op.index for op in trace if op.kind == "remove" and op.path == f"lib/{MANIFEST_PATH}")
    published = max(op.index for op in trace if op.kind == "replace" and op.target.startswith("lib/.conflicts/"))
    temp = next(op.path for op in reversed(trace) if op.index < removal and op.kind == "read_bytes"
                and op.path.startswith("lib/.txn/rec~"))

    def damage(ops, root):
        ops.write_bytes(root / temp, b"{}")
    report = run_plans(scenario, [[Interleave(published + 1, damage)]], tmp_path / "damaged")
    report.assert_ok()
    run = report.runs[1]
    assert all(run.recovered[f"lib/{path}"] == template[path] for path in (R1_PATH, MANIFEST_PATH))


def test_a_removal_never_outlives_a_hold_that_did_not_land(tmp_path):
    """A hold damaged after another entry is published (past the all-or-nothing check) is preserved as
    the damaged bytes it holds: then no removal of the commit happens, so the record stays."""
    paths = (R1_PATH, MANIFEST_PATH, WREN_A_PATH, WREN_B_PATH)
    scenario, template = _scenario(tmp_path / "setup", paths, (idempotent_recovery(),))
    trace = run_plans(scenario, [], tmp_path / "base").runs[0].action_trace
    holds = [op for op in trace if op.kind == "replace" and op.target.startswith("lib/.conflicts/")]

    def damage(ops, root):
        ops.write_bytes(root / holds[1].path, b"{}")
    report = run_plans(scenario, [[Interleave(holds[0].index + 1, damage)]], tmp_path / "damaged")
    report.assert_ok()
    run = report.runs[1]
    assert all(run.recovered[f"lib/{path}"] == template[path] for path in paths)


def test_a_removal_recovery_cannot_finish_is_restored_from_its_hold(tmp_path):
    """All or none across a crash: the app dies after some of a source's records are unlinked, and the
    manifest is edited while it is down. Recovery can no longer remove the rest, so it puts back each
    record it removed, from its hold, and keeps the human's edit: never instances pinning a revision
    that is gone. Crashed at every unlink of the batch, and recovery crashed in turn after each."""
    paths = (R1_PATH, MANIFEST_PATH, WREN_A_PATH, WREN_B_PATH)
    probe, template = _scenario(tmp_path / "setup", paths, ())
    human = template[MANIFEST_PATH].replace(b'"Harbor"', b'"Harbour"')

    def edit(ops, root):
        if (root / "lib" / MANIFEST_PATH).exists():
            ops.write_bytes(root / "lib" / MANIFEST_PATH, human)

    def all_or_none(outcome):
        present = {path: outcome.recovered.get(f"lib/{path}") for path in paths}
        if all(data is None for data in present.values()):
            return None  # the whole batch removed: crashed after its last unlink, nothing left to edit
        wanted = {**{path: template[path] for path in paths}, MANIFEST_PATH: human}
        if present != wanted:
            return f"part of the batch is gone: {sorted(path for path in paths if present[path] != wanted[path])}"
        kept = [value for key, value in outcome.recovered.items() if key.startswith("lib/.conflicts/")]
        return None if all(template[path] in kept for path in paths) else "a hold is gone"

    scenario = replace(probe, meddle=edit, invariants=(
        preserves_meddle(), idempotent_recovery(), Invariant("all_or_none", all_or_none)))
    trace = run_plans(probe, [], tmp_path / "base").runs[0].action_trace
    first = min(op.index for op in trace if op.kind == "remove" and op.path in {f"lib/{path}" for path in paths})
    unlinks = [op.index for op in trace if op.kind == "remove" and op.index >= first]
    report = run_plans(scenario, [[Crash(index, "after")] for index in unlinks], tmp_path / "crashed")
    report.assert_ok()
    assert sum(run.recovered.get(f"lib/{MANIFEST_PATH}") == human for run in report.runs[1:]) >= 3
    explore_crashes(scenario, tmp_path / "recovery", torn=False, recovery_crashes=True,
                    select=lambda op: {"after"} if op.kind == "remove" and op.index >= first else False).assert_ok()


def test_a_completed_removal_whose_record_is_recreated_cancels_the_rest(tmp_path):
    """All or none across a crash, the other way: the app dies once an instance's removal is done,
    and the instance is written again (other bytes) while it is down. Recovery keeps that record and
    removes nothing more of the batch, putting back what it removed from the holds: never the
    manifest and the revision the recreated instance pins deleted under it. (A crash after the
    batch's last removal leaves nothing to decide: the instance is then written after it.)"""
    paths = (R1_PATH, MANIFEST_PATH, WREN_A_PATH, WREN_B_PATH)
    probe, template = _scenario(tmp_path / "setup", paths, ())
    human = template[WREN_A_PATH].replace(b'"hp": 3', b'"hp": 11')
    assert human != template[WREN_A_PATH]

    def recreate(ops, root):
        if not (root / "lib" / WREN_A_PATH).exists():
            ops.write_bytes(root / "lib" / WREN_A_PATH, human)

    def all_or_none(outcome):
        present = {path: outcome.recovered.get(f"lib/{path}") for path in paths}
        if present[WREN_A_PATH] is None:  # crashed before the instance's removal: nothing recreated
            return None
        if all(outcome.after_action.get(f"lib/{path}") is None for path in paths):
            return None  # the whole batch went before the crash: the instance is written after it
        wanted = {**{path: template[path] for path in paths}, WREN_A_PATH: present[WREN_A_PATH]}
        if present != wanted:
            return f"part of the batch is gone: {sorted(path for path in paths if present[path] != wanted[path])}"
        kept = [value for key, value in outcome.recovered.items() if key.startswith("lib/.conflicts/")]
        return None if all(template[path] in kept for path in paths) else "a hold is gone"

    scenario = replace(probe, meddle=recreate, invariants=(
        preserves_meddle(), idempotent_recovery(), Invariant("all_or_none", all_or_none)))
    trace = run_plans(probe, [], tmp_path / "base").runs[0].action_trace
    removal = next(op.index for op in trace if op.kind == "remove" and op.path == f"lib/{WREN_A_PATH}")
    after = [op.index for op in trace if op.kind == "remove" and op.index >= removal]
    report = run_plans(scenario, [[Crash(index, "after")] for index in after], tmp_path / "crashed")
    report.assert_ok()
    assert sum(run.recovered.get(f"lib/{WREN_A_PATH}") == human for run in report.runs[1:]) >= 2


def _moved_scenario(tmp_path, invariants):
    """A sync batch over a source whose head moved to r2, one entry of every journal kind: both
    instances copied onto r2 (replicas), a regenerable kept version of the old wren-a (keep), a copy
    of the manifest (publish), the peer's archive (state), and r1, which nothing pins once the copies
    land, deleted (remove) with its hold. Returns the scenario, the library before it and the bytes
    each copy plans."""
    locker = ProcessLocker()
    tmp_path.mkdir(parents=True, exist_ok=True)
    with LibraryStore.create(tmp_path / "lib", locker=locker) as library:
        seed(library)
        txn = library.transaction()
        txn.put(wren("r2", bio="a captain"), None)
        txn.put(harbor_manifest(heads={"character/wren": "r2"}), library.read_source(HARBOR).etag)
        txn.commit(owner_handle())
    template = snapshot(tmp_path / "lib")
    shutil.copytree(tmp_path / "lib", tmp_path / "planned")  # the other device, where the copies come from
    with LibraryStore.open(tmp_path / "planned", locker=locker) as other:
        txn = other.transaction()
        txn.put(instance(WREN_A, WREN_R2, base=WREN_R2), other.read(WREN_A).etag)
        txn.put(instance(WREN_B, WREN, base=WREN_R2), other.read(WREN_B).etag)
        txn.put(harbor_manifest(heads={"character/wren": "r2"}, **{"x-notes": "moved on"}),
                other.read_source(HARBOR).etag)
        txn.commit(owner_handle())
    planned = {path: (tmp_path / "planned" / path).read_bytes() for path in (WREN_A_PATH, WREN_B_PATH, MANIFEST_PATH)}

    def action(ops, root):
        with LibraryStore.open(root / "lib", ops=ops, locker=locker) as library:
            txn = library.transaction()
            for path in (WREN_A_PATH, WREN_B_PATH):
                txn.put_exact(path, planned[path], etag(template[path]), regenerable=True)
            txn.put_exact(MANIFEST_PATH, planned[MANIFEST_PATH], etag(template[MANIFEST_PATH]))
            txn.keep_version(WREN_A_PATH, template[WREN_A_PATH], regenerable=True)
            txn.keep_version(R1_PATH, template[R1_PATH], regenerable=False)
            txn.put_sync_state("peers/phone/archive.json", b'{"format": "x"}\n', None)
            txn.delete_exact(R1_PATH, etag(template[R1_PATH]))
            try:
                txn.commit(owner_handle(*ALL))
            except WriteConflict:  # an edit racing the batch: the commit reports what it kept
                pass

    scenario = CrashScenario(
        "delete-moved", lambda ops, root: _replay(ops, root, template), action,
        lambda ops, root: LibraryStore.open(root / "lib", ops=ops, locker=locker).close(),
        invariants, normalize=drop_empty_dirs)
    return scenario, template, planned


def test_a_copy_edited_while_its_batch_settles_cancels_the_batchs_removals(tmp_path):
    """Recovery revalidates every other effect of the batch on disk before any removal: wren-a is
    edited (still pinning r1, other hp) while the app is down, or while the batch settles so its
    copy is dropped, and the app dies at every step after. The edit is kept, and r1, which it pins,
    is never deleted: the in-memory record that the copy did not land dies with the process."""
    scenario, template, planned = _moved_scenario(tmp_path / "setup", ())
    human = template[WREN_A_PATH].replace(b'"hp": 3', b'"hp": 11')
    assert human != template[WREN_A_PATH] and b"wren@r1" in human

    def edit(ops, root):
        ops.write_bytes(root / "lib" / WREN_A_PATH, human)

    def pinned_kept(outcome):
        if outcome.recovered.get(f"lib/{R1_PATH}") is not None:
            return None
        if outcome.after_action.get(f"lib/{R1_PATH}") is None:
            return None  # the batch was done before the crash: the edit is then made after it
        if any(outcome.recovered.get(f"lib/{path}") != planned[path] for path in (WREN_A_PATH, WREN_B_PATH)):
            return "r1 was deleted though a record that did not move off it still pins it"
        return None

    def kept_somewhere(outcome):
        if outcome.recovered.get(f"lib/{R1_PATH}") == template[R1_PATH]:
            return None
        held = [value for key, value in outcome.recovered.items() if key.startswith("lib/.conflicts/")]
        return None if template[R1_PATH] in held else "r1 is gone and its bytes are kept nowhere"

    kinds = set()

    def journaled(outcome):  # which journal kinds the crashes left for recovery to settle
        journal = outcome.after_action.get("lib/.txn/journal.json")
        if journal is not None:
            kinds.update(entry.kind for entry in parse(journal).entries)

    common = (idempotent_recovery(), Invariant("pinned_kept", pinned_kept), Invariant("kept", kept_somewhere),
              Invariant("journaled", journaled))
    meddled = replace(scenario, meddle=edit, invariants=(preserves_meddle(), *common))
    explore_crashes(meddled, tmp_path / "down", torn=False, recovery_crashes=True).assert_ok()
    assert kinds == {"publish", "replica", "keep", "hold", "state", "remove"}  # every kind, crashed between each step
    racing = replace(scenario, invariants=(preserves_interleave(), *common))
    trace = run_plans(scenario, [], tmp_path / "base").runs[0].action_trace
    first = next(op.index for op in trace if op.kind == "replace" and op.target == f"lib/{WREN_A_PATH}")
    later = next(op.index for op in trace if op.kind == "replace" and op.target == f"lib/{WREN_B_PATH}")
    assert first < later  # past the all-or-nothing check, before wren-b's copy: that copy is dropped
    human_b = template[WREN_B_PATH].replace(b'"hp": 3', b'"hp": 12')
    assert human_b != template[WREN_B_PATH] and b"wren@r1" in human_b

    def edit_b(ops, root):
        ops.write_bytes(root / "lib" / WREN_B_PATH, human_b)
    report = explore_crashes(racing, tmp_path / "racing", torn=False, recovery_crashes=True,
                             interleaves=[Interleave(first + 1, edit_b)])
    report.assert_ok()
    assert any(run.recovered.get(f"lib/{WREN_B_PATH}") == human_b and run.recovered.get(f"lib/{R1_PATH}")
               == template[R1_PATH] for run in report.runs[1:])

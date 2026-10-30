"""Native store crash safety: the real writer under the fault harness (A7, A8, F7, M7–M9)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.security import Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.security import Approval
from grimoire.store import (
    CommitIncomplete, LibraryLocked, LibraryStore, PendingRecovery, ProcessLocker, RecordEnvelope,
    RecoveryBlocked, RecoveryFailed, SourceManifest, WriteConflict,
)
from grimoire.testing import (
    Crash, CrashScenario, Fail, Interleave, Invariant, RootedFileOps, Target, atomic_outcome,
    completes_after, explore_crashes, idempotent_recovery, no_residue, preserves_meddle, run_with_fault,
    untouched,
)
from grimoire.testing.fileops import snapshot

from store_helpers import (
    HARBOR, OWNER, WREN, WREN_R1, drop_empty_dirs, harbor_manifest, instance, make_dir_link, owner_handle,
    seed, wren,
)

LIB = "lib"
JOURNAL = Target("replace", target="lib/.txn/journal.json")
RESIDUE = no_residue("lib/.txn/*.new", "lib/.txn/journal.json")
OTHER = InstanceRef("other", "character", "bystander")


def _setup(locker):
    def setup(ops, root):
        with LibraryStore.create(root / LIB, ops=ops, locker=locker) as store:
            seed(store)
            txn = store.transaction()
            txn.put(RecordEnvelope(OTHER, {"n": 1}), None)
            txn.commit(owner_handle())
    return setup


def _recover(locker):
    def recover(ops, root):
        LibraryStore.open(root / LIB, ops=ops, locker=locker).close()
    return recover


def head_move(ops, root, locker):
    """A2's change: add wren@r2 and move the head, in one transaction."""
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        source = store.read_source(HARBOR)
        txn = store.transaction()
        txn.put(wren("r2", "a captain"), None)
        txn.put(harbor_manifest({"character/wren": "r2"}), source.etag)
        txn.commit(owner_handle())


def new_directories(ops, root, locker):
    """A new source and an instance in a new campaign: every target needs new directories."""
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        txn = store.transaction()
        txn.put(SourceManifest("catalog:beasts", OWNER, "Beasts"), None)
        txn.put(RecordEnvelope(SourceRef("catalog:beasts", "species", "selkie", "v1"), {"habitat": "sea"},
                               name="Selkie"), None)
        txn.put(instance(InstanceRef("c3", "creature", "pup"), "catalog:beasts/species/selkie@v1",
                         base="catalog:beasts/species/selkie@v1"), None)
        txn.commit(owner_handle())


def scenario(action, *, invariants=None, meddle=None, name="native store commit"):
    locker = ProcessLocker()  # one per scenario: a leaked lock from a crashed phase is visible
    return CrashScenario(
        name=name,
        setup=_setup(locker),
        action=lambda ops, root: action(ops, root, locker),
        recover=_recover(locker),
        invariants=invariants if invariants is not None else (
            atomic_outcome(), idempotent_recovery(), RESIDUE, completes_after(JOURNAL),
            untouched("lib/campaigns/other/*")),
        meddle=meddle,
        normalize=drop_empty_dirs,
    )


@pytest.mark.parametrize("action", [head_move, new_directories], ids=["head-move", "new-directories"])
def test_commit_survives_every_crash_error_and_recovery_crash(tmp_path, action):
    report = explore_crashes(scenario(action), tmp_path, errors=True, recovery_crashes=True)
    report.assert_ok()
    assert report.points_explored > 40
    assert any(run.recover_trace for run in report.runs[1:])  # recovery work was actually explored


def test_residue_and_untouched_globs_are_not_vacuous(tmp_path):
    report = explore_crashes(scenario(head_move, invariants=()), tmp_path)
    trees = [run.after_action for run in report.runs]
    assert any(key.startswith("lib/.txn/") and key.endswith(".new") for tree in trees for key in tree)
    assert any(key == "lib/.txn/journal.json" for tree in trees for key in tree)
    assert any(key.startswith("lib/campaigns/other/") and value is not None
               for tree in trees for key, value in tree.items())

    def lazy_recover(ops, root):  # forgets staging cleanup
        pass

    broken = CrashScenario("lazy recovery", _setup(ProcessLocker()), lambda ops, root: head_move(
        ops, root, ProcessLocker()), lazy_recover, (RESIDUE,), normalize=drop_empty_dirs)
    assert "no_residue" in {violation.invariant for violation in explore_crashes(broken, tmp_path / "b").violations}


# -- external edits (A8) --------------------------------------------------------------

MANIFEST = "lib/sources/world/harbor/_source/manifest.json"
R2 = "lib/sources/world/harbor/_source/records/character/wren/r2.json"
HUMAN = b'{"edited by": "a human"}\n'


def _conflict_key(target: str, new: bytes) -> str:
    from grimoire.store.layout import conflict_parts
    from grimoire.store import etag
    return "lib/" + "/".join(conflict_parts(tuple(target.split("/")[1:]), etag(new)))


def _journal_entries(tree):
    raw = tree.get("lib/.txn/journal.json")
    if raw is None:
        return None
    return [("lib/" + entry["target"], "lib/.txn/" + entry["temp"]) for entry in json.loads(raw)["entries"]]


def _all_or_nothing(outcome):
    """M5: while nothing was published an edit aborts everything; afterwards, the rest completes."""
    entries = _journal_entries(outcome.after_action)
    if entries is None:
        return None
    staged = {target: outcome.after_action.get(temp) for target, temp in entries}
    if all(data is not None for data in staged.values()):
        for target, data in staged.items():
            if outcome.recovered.get(target) == data:
                return f"{target} was published although the commit was aborted"
            if outcome.recovered.get(_conflict_key(target, data)) != data:
                return f"the store's version of {target} was not preserved"
        return None
    for target, data in staged.items():
        if target in outcome.meddled_paths:
            if data is not None and outcome.recovered.get(_conflict_key(target, data)) != data:
                return f"the store's version of meddled {target} was not preserved"
        elif outcome.recovered.get(target) != outcome.completed.get(target):
            return f"{target} did not complete"
    return None


def _edit_manifest_if_pending(ops, root):
    if ops.exists(root / LIB / ".txn" / "journal.json"):
        ops.write_bytes(root / MANIFEST, HUMAN)


def test_an_edit_made_while_down_is_kept_and_never_half_published(tmp_path):
    sc = scenario(head_move, meddle=_edit_manifest_if_pending, invariants=(
        preserves_meddle(), idempotent_recovery(), Invariant("all_or_nothing", _all_or_nothing)))
    report = explore_crashes(sc, tmp_path, recovery_crashes=True)  # I/O errors: explored above
    report.assert_ok()
    assert any(run.meddled_paths for run in report.runs)


def _baseline_trace(sc, workdir):
    return run_with_fault(sc, [], workdir).action_trace


def _after_commit(trace, kind, path, nth=1):
    commit = next(op.index for op in trace if op.kind == "replace" and op.target == "lib/.txn/journal.json")
    matches = [op.index for op in trace if op.index > commit and op.kind == kind and op.path == path]
    return matches[nth - 1]


def _human_edit(ops, root):
    ops.write_bytes(root / MANIFEST, HUMAN)


def _nothing_published(outcome):
    recovered = {key: value for key, value in outcome.recovered.items()
                 if key not in outcome.interleaved_paths and key.split("/")[:2] != ["lib", ".conflicts"]}
    before = {key: value for key, value in outcome.before.items() if key not in outcome.interleaved_paths}
    if recovered == before:
        conflicts = [key for key, value in outcome.recovered.items()
                     if key.startswith("lib/.conflicts/") and value is not None]
        return None if len(conflicts) == 2 else f"preserved {conflicts}, expected both staged versions"
    completed = {key: value for key, value in outcome.completed.items() if key not in outcome.interleaved_paths}
    return None if recovered == completed else "neither completed nor nothing published"


def test_an_edit_racing_the_pre_publish_guard_aborts_the_whole_commit(tmp_path):
    sc = scenario(head_move, invariants=(Invariant("all_or_nothing", _nothing_published),
                                         idempotent_recovery(), RESIDUE))
    guard_read = _after_commit(_baseline_trace(sc, tmp_path), "read_bytes", MANIFEST)
    run = run_with_fault(sc, [Interleave(guard_read, _human_edit)], tmp_path)
    assert run.violations == ()
    assert "WriteConflict" in run.action_error
    assert run.recovered[MANIFEST] == HUMAN and R2 not in run.recovered
    # M4: crash at every later operation of the abort (abort journal, preserves, cleanup)
    later = [op for op in run.action_trace if op.index > guard_read and op.mutating]
    assert any(op.kind == "replace" and op.target == "lib/.txn/journal.json" for op in later)
    for op in later:
        for timing in ("before", "after"):
            crashed = run_with_fault(sc, [Interleave(guard_read, _human_edit), Crash(op.index, timing)], tmp_path)
            assert crashed.violations == (), crashed.violations


def test_an_edit_racing_one_entry_after_a_partial_publish_is_kept(tmp_path):
    sc = scenario(head_move, invariants=(idempotent_recovery(), RESIDUE))
    second_guard = _after_commit(_baseline_trace(sc, tmp_path), "read_bytes", MANIFEST, nth=2)
    run = run_with_fault(sc, [Interleave(second_guard, _human_edit)], tmp_path)
    assert run.violations == ()
    assert "WriteConflict" in run.action_error and "r2.json" in run.action_error
    assert run.recovered[MANIFEST] == HUMAN and R2 in run.recovered
    assert any(key.startswith("lib/.conflicts/") for key in run.recovered)


def test_residual_window_between_guard_and_replace_is_pinned(tmp_path):
    """R7, documented: an edit landing between the per-entry guard read and its replace is lost."""
    sc = scenario(head_move, invariants=())
    trace = _baseline_trace(sc, tmp_path)
    publish = next(op.index for op in trace if op.kind == "replace" and op.target == MANIFEST)
    run = run_with_fault(sc, [Interleave(publish, _human_edit)], tmp_path)
    assert run.action_outcome == "returned" and run.recovered[MANIFEST] != HUMAN


def test_reverting_a_published_target_while_down_does_not_block_recovery(tmp_path):
    """A8(b): `git checkout` of a published file before recovery: the human's state wins."""
    saved = {}
    locker = ProcessLocker()

    def setup(ops, root):
        _setup(locker)(ops, root)
        saved["old"] = ops.read_bytes(root / MANIFEST)

    def revert(ops, root):
        if ops.exists(root / LIB / ".txn" / "journal.json"):
            ops.write_bytes(root / MANIFEST, saved["old"])

    sc = CrashScenario("revert while down", setup, lambda ops, root: head_move(ops, root, locker),
                       _recover(locker), (idempotent_recovery(), RESIDUE), meddle=revert, normalize=drop_empty_dirs)
    run = run_with_fault(sc, [Crash(Target("replace", target=MANIFEST), "after")], tmp_path)
    assert run.violations == ()
    assert run.recovered[MANIFEST] == saved["old"]


def test_recovery_reports_staging_that_vanished(tmp_path):
    locker = ProcessLocker()
    root = tmp_path / "root"
    root.mkdir()
    _setup(locker)(RootedFileOps(root, record=False), root)
    with pytest.raises(BaseException):
        head_move(RootedFileOps(root, plan=[Crash(JOURNAL, "after")], record=False), root, locker)
    for temp in (root / LIB / ".txn").glob("*.new"):
        temp.unlink()
    with LibraryStore.open(root / LIB, locker=locker) as store:
        assert set(store.last_recovery.incomplete) == {MANIFEST[4:], R2[4:]}


# -- journal safety (F7) and failure paths (M8, M9) ------------------------------------------

def _library(tmp_path, locker=None):
    store = LibraryStore.create(tmp_path / LIB, locker=locker or ProcessLocker())
    seed(store)
    return store


@pytest.mark.parametrize("journal", [
    b"not json",
    json.dumps({"format": "grimoire.journal/9", "state": "commit", "entries": []}).encode(),
    json.dumps({"format": "grimoire.journal/1", "state": "commit", "entries": [
        {"kind": "publish", "target": "../escape.json", "temp": "rec1.new", "old": None,
         "new": "sha256:" + "0" * 64}]}).encode(),
    json.dumps({"format": "grimoire.journal/1", "state": "commit", "entries": [
        {"kind": "publish", "target": "campaigns/c1/instances/character/wren-a.json", "temp": "../../y.new",
         "old": None, "new": "sha256:" + "0" * 64}]}).encode(),
], ids=["garbage", "format", "escaping-target", "escaping-temp"])
def test_an_untrustworthy_journal_changes_nothing_and_can_be_discarded(tmp_path, journal):
    locker = ProcessLocker()
    _library(tmp_path, locker).close()
    (tmp_path / LIB / ".txn" / "journal.json").write_bytes(journal)
    (tmp_path / LIB / ".txn" / "rec1.new").write_bytes(b"staged")
    before = snapshot(tmp_path / LIB)
    with pytest.raises(RecoveryFailed):
        LibraryStore.open(tmp_path / LIB, locker=locker)
    assert snapshot(tmp_path / LIB) == before
    assert locker.held == set()  # a failed open releases the lock
    moved = LibraryStore.discard_journal(tmp_path / LIB, locker=locker)
    assert {path.rsplit("/", 1)[1] for path in moved} == {"journal.json", "rec1.new"}
    with LibraryStore.open(tmp_path / LIB, locker=locker) as store:
        assert set(store.conflicts()) == set(moved)
        assert store.load().problems == ()


def test_discard_journal_waits_for_the_writer(tmp_path):
    locker = ProcessLocker()
    with _library(tmp_path, locker):
        with pytest.raises(LibraryLocked):
            LibraryStore.discard_journal(tmp_path / LIB, locker=locker)


def _crash_after_commit_point(root, locker, action=head_move):
    with pytest.raises(BaseException):
        action(RootedFileOps(root, plan=[Crash(JOURNAL, "after")], record=False), root, locker)


def test_persistent_recovery_failure_is_blocked_not_bricked(tmp_path):
    locker = ProcessLocker()
    root = tmp_path / "root"
    root.mkdir()
    _setup(locker)(RootedFileOps(root, record=False), root)
    _crash_after_commit_point(root, locker)
    failing = RootedFileOps(root, plan=[Fail(Target("replace", path="lib/.txn/*"))], record=False)
    with pytest.raises(RecoveryBlocked) as blocked:
        LibraryStore.open(root / LIB, ops=failing, locker=locker)
    assert isinstance(blocked.value.__cause__, OSError)
    assert locker.held == set()
    with LibraryStore.open(root / LIB, locker=locker, writable=False) as reader:
        with pytest.raises(PendingRecovery):
            reader.load()
    with LibraryStore.open(root / LIB, locker=locker) as store:  # same locker: the lock was released
        assert store.last_recovery.published
        assert store.load().index.resolve(WREN).ref == WREN.at("r2")


def test_failure_after_the_commit_point_is_incomplete_until_recovered(tmp_path):
    locker = ProcessLocker()
    root = tmp_path / "root"
    root.mkdir()
    _setup(locker)(RootedFileOps(root, record=False), root)
    ops = RootedFileOps(root, plan=[Fail(Target("replace", target=MANIFEST))], record=False)
    store = LibraryStore.open(root / LIB, ops=ops, locker=locker)
    source = store.read_source(HARBOR)
    txn = store.transaction()
    txn.put(wren("r2", "a captain"), None)
    txn.put(harbor_manifest({"character/wren": "r2"}), source.etag)
    with pytest.raises(CommitIncomplete):
        txn.commit(owner_handle())
    with pytest.raises(PendingRecovery):
        store.read(WREN_R1)
    retry = store.transaction()
    retry.put(wren("r3"), None)
    with pytest.raises(PendingRecovery):
        retry.commit(owner_handle())
    store.recover()
    assert store.load().index.resolve(WREN).ref == WREN.at("r2")
    store.close()


def test_failure_writing_the_journal_publishes_nothing_and_names_consumed_approvals(tmp_path):
    locker = ProcessLocker()
    root = tmp_path / "root"
    root.mkdir()
    _setup(locker)(RootedFileOps(root, record=False), root)
    before = snapshot(root / LIB)
    authority = Authorizer([OWNER])
    authority.add_grant(Grant("g", "app:importer", frozenset({Permission.APPLY}),
                              ResourcePath.parse("campaigns"), OWNER))
    ops = RootedFileOps(root, plan=[Fail(Target("write_bytes", path="lib/.txn/journal*"))], record=False)
    store = LibraryStore.open(root / LIB, ops=ops, locker=locker)
    txn = store.transaction()
    address = txn.put(RecordEnvelope(InstanceRef("c9", "character", "new"), {"n": 1}), None)
    authority.register_approval(Approval("a1", "app:importer", Permission.APPLY, ResourcePath.parse(address),
                                         payload_digest(txn.preview()[address]), OWNER))
    with pytest.raises(OSError) as failed:
        txn.commit(authority.handle_for("app:importer"), {address: "a1"})
    assert not isinstance(failed.value, CommitIncomplete)
    assert any("a1" in note for note in getattr(failed.value, "__notes__", []))
    store.close()
    assert drop_empty_dirs(snapshot(root / LIB)) == drop_empty_dirs(before)


def test_interrupted_create_can_be_retried(tmp_path):
    locker = ProcessLocker()

    def action(ops, root):
        LibraryStore.create(root / LIB, ops=ops, locker=locker).close()

    def recover(ops, root):
        if ops.exists(root / LIB / "grimoire-library.json"):
            LibraryStore.open(root / LIB, ops=ops, locker=locker).close()
        elif ops.exists(root / LIB):
            LibraryStore.create(root / LIB, ops=ops, locker=locker).close()

    sc = CrashScenario("create", lambda ops, root: None, action, recover,
                       (idempotent_recovery(), RESIDUE), normalize=drop_empty_dirs)
    explore_crashes(sc, tmp_path, errors=True, recovery_crashes=True).assert_ok()
    assert locker.held == set()


def test_a_commit_whose_staging_vanished_is_not_reported_as_success(tmp_path):
    locker = ProcessLocker()
    root = tmp_path / "root"
    root.mkdir()
    _setup(locker)(RootedFileOps(root, record=False), root)

    def cleanup_tool(ops, root):  # another program empties the staging folder
        for name in ops.list_dir(root / LIB / ".txn"):
            if name.startswith("rec"):
                ops.remove(root / LIB / ".txn" / name)

    ops = RootedFileOps(root, plan=[Interleave(JOURNAL, cleanup_tool)], record=False)
    with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
        txn = store.transaction()
        txn.put(RecordEnvelope(InstanceRef("c9", "character", "x"), {"n": 1}), None)
        with pytest.raises(CommitIncomplete, match="not written"):
            txn.commit(owner_handle())
        assert not (root / LIB / "campaigns" / "c9" / "instances" / "character" / "x.json").exists()


def test_a_lookalike_created_while_down_is_never_overwritten(tmp_path):
    """On a case-insensitive filesystem `X.json` *is* `x.json`; recovery must preserve, not publish."""
    locker = ProcessLocker()
    root = tmp_path / "root"
    root.mkdir()
    _setup(locker)(RootedFileOps(root, record=False), root)
    folder = root / LIB / "campaigns" / "other" / "instances" / "character"

    def create_x(ops, root, locker):
        with LibraryStore.open(root / LIB, ops=ops, locker=locker) as store:
            txn = store.transaction()
            txn.put(RecordEnvelope(InstanceRef("other", "character", "x"), {"n": 1}), None)
            txn.commit(owner_handle())

    _crash_after_commit_point(root, locker, create_x)
    (folder / "X.json").write_bytes(HUMAN)
    with LibraryStore.open(root / LIB, locker=locker) as store:
        assert store.last_recovery.preserved and not store.last_recovery.published
    assert (folder / "X.json").read_bytes() == HUMAN
    assert sorted(path.name for path in folder.iterdir()) == ["X.json", "bystander.json"]

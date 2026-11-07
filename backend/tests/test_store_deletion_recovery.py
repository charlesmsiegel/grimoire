"""Crash, torn-write, error and race explorations of the store's deletion (028), apart from its unit
tests (``test_store_delete.py``) so that they run in an explorations CI shard: named for the store
and for deletion, they run in ``deletion-explorations``, apart from the other store explorations
(docs/TESTING.md#ci-gate).

The explorations that crash the recovery after every crash of the action are split by where the
batch stands when the app dies (`PHASES`), and crash only where the files differ
(``store_helpers.distinct_crashes``): a crash before an operation leaves the files a crash after the
one before it leaves, so recovering from both only repeats the same runs. Each also has a test that
crashes the action at every point, both timings, and checks that claim on its runs."""

from __future__ import annotations

import shutil
from dataclasses import replace

import pytest

from grimoire.store import LibraryStore, ProcessLocker, WriteConflict, etag
from grimoire.store.journal import parse
from grimoire.testing import (
    Crash, CrashScenario, Interleave, Invariant, Target, atomic_outcome, completes_after, explore_crashes,
    idempotent_recovery, no_residue, preserves_interleave, preserves_meddle, run_plans,
)
from grimoire.testing.fileops import snapshot

from grimoire.core.identity import InstanceRef

from store_helpers import (
    DELETE_SCOPES as ALL, HARBOR, MANIFEST_PATH, R1_PATH, WREN, WREN_A, WREN_A_PATH, WREN_B, WREN_B_PATH, WREN_R1,
    WREN_R2, assert_distinct_crashes_repeat, distinct_crashes, distinct_recovery_crashes, drop_empty_dirs,
    harbor_manifest, instance, owner_handle, seed, wren,
)
from store_helpers import delete_held as _delete

SOURCE = (R1_PATH, MANIFEST_PATH, WREN_A_PATH, WREN_B_PATH)

#: Where a batch stands when the app dies: ``staging`` up to its commit point (recovery rolls it
#: back), ``publishing`` from there up to its first removal of a record, ``removing`` the rest.
PHASES = ("staging", "publishing", "removing")


def _phase(trace, phase: str, select):
    """``select`` narrowed to the crashes of one of `PHASES`, which together are all of them. The
    files change in this order: a crash before an operation, then after it (or a torn write or an
    error at it). A batch that removes no record has no ``removing`` crashes."""
    commit = next(op.index for op in trace if op.kind == "replace" and op.target == "lib/.txn/journal.json")
    removal = next((op.index for op in trace if op.kind == "remove" and op.index > commit
                    and not op.path.startswith("lib/.txn/")), len(trace))
    low, high = {"staging": (0, 2 * commit), "publishing": (2 * commit + 1, 2 * removal),
                 "removing": (2 * removal + 1, 2 * len(trace))}[phase]

    def chosen(op):
        timings = select(op) or ()
        return frozenset(timing for timing in timings
                         if low <= 2 * op.index + (timing != "before") <= high) or False
    return chosen


def _baseline(scenario, workdir, interleaves=()):
    """The action's trace without faults (with ``interleaves``, as an exploration's baseline runs)."""
    report = explore_crashes(scenario, workdir, torn=False, interleaves=interleaves, select=lambda op: False)
    report.assert_ok()
    return report.runs[0].action_trace


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


def _survival_scenario(tmp_path, paths):
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
    return scenario


DELETIONS = pytest.mark.parametrize("paths", [(WREN_A_PATH,), SOURCE], ids=["instance", "source"])


@DELETIONS
def test_a_deletion_survives_every_crash_torn_write_and_error(tmp_path, paths):
    report = explore_crashes(_survival_scenario(tmp_path, paths), tmp_path / "errors", errors=True)
    report.assert_ok()
    assert report.points_explored > 10


@pytest.mark.parametrize("phase", PHASES)
@DELETIONS
def test_a_deletion_survives_a_crash_while_it_recovers(tmp_path, paths, phase):
    """The same, the app crashed at each publish and removal and its recovery crashed in turn at each
    of its own steps."""
    scenario = _survival_scenario(tmp_path, paths)
    trace = _baseline(scenario, tmp_path / "base")
    select = _phase(trace, phase, distinct_crashes(trace, lambda op: op.kind in ("replace", "remove"), torn=True))
    report = explore_crashes(scenario, tmp_path / "recovery", recovery_crashes=True, select=select,
                             recovery_select=distinct_recovery_crashes(torn=True))
    report.assert_ok()
    assert any(" > recover:" in run.point for run in report.runs)


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


@pytest.mark.parametrize("when", ["holding", "held"])
def test_an_edit_racing_a_source_deletion_keeps_all_of_that_source(tmp_path, when):
    """A commit's removals happen all or none: a human edit to the manifest after the commit point, up
    to the read just before the first removal, keeps every record it deletes (the revision too). After
    that read only reads come before the removal: 010's R7 window, pinned, the removed bytes held.
    Edited while the holds are published, or once they all are."""
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
    held = max(op.index for op in trace if op.kind == "replace" and op.target.startswith("lib/.conflicts/"))
    assert commit < held < check
    edits = range(commit + 1, held + 1) if when == "holding" else range(held + 1, check + 1)
    report = run_plans(scenario, [[Interleave(index, edit)] for index in edits], tmp_path / "race")
    report.assert_ok()
    for run in report.runs[1:]:  # nothing of the batch is removed, the records that used the source included
        assert "WriteConflict" in run.action_error and run.recovered[f"lib/{MANIFEST_PATH}"] == human
        assert all(run.recovered[f"lib/{path}"] == template[path] for path in (R1_PATH, WREN_A_PATH, WREN_B_PATH))
    if when == "holding":
        return
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
                    select=lambda op: {"after"} if op.kind == "remove" and op.index >= first else False,
                    recovery_select=distinct_recovery_crashes()).assert_ok()


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


def _copy_edits(tmp_path):
    """The moved batch with wren-a edited (still pinning r1, other hp) while the app is down
    (``down``), or with wren-b edited while the batch settles so its copy is dropped (``racing``):
    each scenario and its interleaves, the journal kinds its crashes leave, and the edits."""
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
    racing = replace(scenario, invariants=(preserves_interleave(), *common))
    trace = _baseline(scenario, tmp_path / "base")
    first = next(op.index for op in trace if op.kind == "replace" and op.target == f"lib/{WREN_A_PATH}")
    later = next(op.index for op in trace if op.kind == "replace" and op.target == f"lib/{WREN_B_PATH}")
    assert first < later  # past the all-or-nothing check, before wren-b's copy: that copy is dropped
    human_b = template[WREN_B_PATH].replace(b'"hp": 3', b'"hp": 12')
    assert human_b != template[WREN_B_PATH] and b"wren@r1" in human_b

    def edit_b(ops, root):
        ops.write_bytes(root / "lib" / WREN_B_PATH, human_b)
    edits = {"down": (meddled, ()), "racing": (racing, (Interleave(first + 1, edit_b),))}
    return edits, kinds, template, human_b


@pytest.mark.parametrize("when", ["down", "racing"])
def test_a_copy_edited_while_its_batch_settles_cancels_the_batchs_removals(tmp_path, when):
    """Recovery revalidates every other effect of the batch on disk before any removal: wren-a is
    edited (still pinning r1, other hp) while the app is down, or wren-b while the batch settles so
    its copy is dropped, and the app dies at every step after. The edit is kept, and r1, which it
    pins, is never deleted: the in-memory record that the copy did not land dies with the process."""
    edits, kinds, template, human_b = _copy_edits(tmp_path)
    scenario, interleaves = edits[when]
    report = explore_crashes(scenario, tmp_path / when, torn=False, interleaves=interleaves)
    report.assert_ok()
    assert assert_distinct_crashes_repeat(
        report, distinct_crashes(report.runs[0].action_trace, interleaves=interleaves)) > 10
    if when == "down":
        assert kinds == {"publish", "replica", "keep", "hold", "state", "remove"}  # every kind, crashed between each step
    else:
        assert any(run.recovered.get(f"lib/{WREN_B_PATH}") == human_b and run.recovered.get(f"lib/{R1_PATH}")
                   == template[R1_PATH] for run in report.runs[1:])
        removing = _phase(report.runs[0].action_trace, "removing", lambda op: {"before", "after"})
        assert not any(removing(op) for op in report.runs[0].action_trace)  # so the test below has no such phase


@pytest.mark.parametrize(("when", "phase"), [("down", phase) for phase in PHASES] + [
    ("racing", "staging"), ("racing", "publishing")])  # the racing batch, its copy dropped, removes nothing
def test_a_copy_edited_while_its_batch_settles_is_kept_when_its_recovery_crashes_too(tmp_path, when, phase):
    """The same, the recovery after each of those crashes crashed in turn at each of its steps."""
    edits, _, _, _ = _copy_edits(tmp_path)
    scenario, interleaves = edits[when]
    trace = _baseline(scenario, tmp_path / "trace", interleaves)
    select = _phase(trace, phase, distinct_crashes(trace, interleaves=interleaves))
    report = explore_crashes(scenario, tmp_path / when, torn=False, recovery_crashes=True, interleaves=interleaves,
                             select=select, recovery_select=distinct_recovery_crashes())
    report.assert_ok()
    assert any(" > recover:" in run.point for run in report.runs)


WREN_C_PATH = "campaigns/c1/instances/character/wren-c.json"


def _added_scenario(tmp_path):
    """A whole-source deletion, and a new instance pinning r1 written while the app is down after a
    crash in it: the scenario, the library before it and the new record's bytes."""
    probe, template = _scenario(tmp_path / "setup", SOURCE, ())
    shutil.copytree(tmp_path / "setup" / "lib", tmp_path / "other")
    with LibraryStore.open(tmp_path / "other") as other:  # the new record's canonical bytes
        txn = other.transaction()
        txn.put(instance(InstanceRef("c1", "character", "wren-c"), WREN_R1, hp=5), None)
        txn.commit(owner_handle())
        added = (tmp_path / "other" / WREN_C_PATH).read_bytes()
    assert b"wren@r1" in added

    def add(ops, root):
        if not (root / "lib" / WREN_C_PATH).exists():
            ops.write_bytes(root / "lib" / WREN_C_PATH, added)

    def all_kept(outcome):
        if not outcome.meddled_paths:
            return None  # the baseline: nothing added
        if outcome.recovered.get(f"lib/{WREN_C_PATH}") != added:
            return "the added record is gone"
        if all(outcome.after_action.get(f"lib/{path}") is None for path in SOURCE):
            return None  # the whole batch went before the crash: the record is added after it
        lost = sorted(path for path in SOURCE if outcome.recovered.get(f"lib/{path}") != template[path])
        if lost:
            return f"part of the batch is gone though the added record needs it: {lost}"
        removed = [path for path in SOURCE if outcome.after_action.get(f"lib/{path}") is None]
        kept = [value for key, value in outcome.recovered.items() if key.startswith("lib/.conflicts/")]
        return None if all(template[path] in kept for path in removed) else "the hold of a removed record is gone"

    scenario = replace(probe, meddle=add, invariants=(
        preserves_meddle(), idempotent_recovery(), Invariant("all_kept", all_kept)))
    return scenario, template, added


def test_a_record_added_while_the_app_is_down_that_needs_a_removal_cancels_the_batch(tmp_path):
    """Recovery judges the batch's removals again over the library as it now is, as the commit did
    under its lock: a new instance pinning r1, written while the app is down after a crash anywhere
    in a whole-source deletion, keeps r1, and so all of the batch (what was removed comes back from
    its hold). Crashed at every step."""
    scenario, template, added = _added_scenario(tmp_path)
    report = explore_crashes(scenario, tmp_path / "crashed", torn=False)
    report.assert_ok()
    assert sum(run.recovered.get(f"lib/{R1_PATH}") == template[R1_PATH]
               and run.recovered.get(f"lib/{WREN_C_PATH}") == added for run in report.runs[1:]) >= 3
    assert assert_distinct_crashes_repeat(report, distinct_crashes(report.runs[0].action_trace)) > 10


@pytest.mark.parametrize("phase", PHASES)
def test_a_record_added_while_the_app_is_down_cancels_the_batch_when_its_recovery_crashes_too(tmp_path, phase):
    """The same, the recovery after each of those crashes crashed in turn at each of its own steps."""
    scenario, _, _ = _added_scenario(tmp_path)
    trace = _baseline(scenario, tmp_path / "trace")
    report = explore_crashes(scenario, tmp_path / "crashed", torn=False, recovery_crashes=True,
                             select=_phase(trace, phase, distinct_crashes(trace)),
                             recovery_select=distinct_recovery_crashes())
    report.assert_ok()
    assert any(" > recover:" in run.point for run in report.runs)

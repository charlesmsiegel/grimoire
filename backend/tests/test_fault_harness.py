"""Fault-injection harness: units for the confined seam and acceptance scenarios.

The writers below are synthetic *reference fixtures* that exercise the harness;
they are not a storage contract or journal format.
"""

import hashlib
import itertools
import json
import os
import random
from pathlib import Path

import pytest

from grimoire.testing import (
    Crash, CrashScenario, Fail, Interleave, Invariant, OperationBudgetExceeded, RootedFileOps,
    SandboxViolation,
    SimulatedCrash, Target, atomic_outcome, completes_after, explore_crashes,
    idempotent_recovery, no_residue, preserves_interleave, preserves_meddle, run_plans, run_with_fault,
    untouched,
)
from grimoire.testing.explore import HARNESS_INVARIANTS

OLD = {"a.json": b'{"rev": 1, "who": "a"}', "b.json": b'{"rev": 1, "who": "b"}'}
NEW = {"a.json": b'{"rev": 2, "who": "a"}', "b.json": b'{"rev": 2, "who": "b"}'}
COMMIT = Target("replace", target=".journal")
RESIDUE = no_residue("*.new", ".journal*", ".recovering")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


# -- reference writers -------------------------------------------------------

def write_files(files, folder=""):
    def setup(ops, root):
        if folder:
            ops.make_dirs(root / folder)
        for name, data in files.items():
            ops.write_bytes(root / folder / name, data)
    return setup


def journaled_publish(files, folder=""):
    """Stage temps, commit a journal by atomic replace, publish, drop the journal."""
    def action(ops, root):
        base = root / folder
        entries = {}
        for name, data in files.items():
            temp = ops.make_temp(base, prefix=f"{name}.", suffix=".new")
            ops.write_bytes(temp, data)
            target = base / name
            old = _sha(ops.read_bytes(target)) if ops.exists(target) else None
            entries[name] = {"temp": temp, "old": old, "new": _sha(data)}  # absolute temp paths
        journal = ops.make_temp(base, prefix="journal.", suffix=".new")
        ops.write_bytes(journal, json.dumps(entries, sort_keys=True).encode())
        ops.replace(journal, base / ".journal")
        for name, entry in entries.items():
            ops.replace(entry["temp"], base / name)
        ops.remove(base / ".journal")
    return action


def journaled_recover(folder="", guarded=False):
    """Roll forward a committed journal, else discard staging. Restart-safe."""
    def recover(ops, root):
        base = root / folder
        if ops.exists(base / ".journal"):
            entries = json.loads(ops.read_bytes(base / ".journal"))
            for name, entry in entries.items():
                if not ops.exists(entry["temp"]):
                    continue
                target = base / name
                current = _sha(ops.read_bytes(target)) if ops.exists(target) else None
                if guarded and current not in (entry["old"], entry["new"]):
                    ops.replace(entry["temp"], base / f"{name}.conflict")  # keep both
                else:
                    ops.replace(entry["temp"], target)
            ops.remove(base / ".journal")
        for name in ops.list_dir(base):
            if name.endswith(".new"):
                ops.remove(base / name)
    return recover


def marker_recover(ops, root):
    """Not restart-safe: a stale marker from an interrupted recovery is fatal."""
    if ops.exists(root / ".recovering"):
        raise RuntimeError("stale recovery marker")
    ops.write_bytes(root / ".recovering", b"")
    journaled_recover()(ops, root)
    ops.remove(root / ".recovering")


def rollback_recover(ops, root):
    """Broken: always discards a committed journal instead of rolling forward."""
    for name in ops.list_dir(root):
        if name.endswith(".new") or name == ".journal":
            ops.remove(root / name)


def naive_publish(files):
    def action(ops, root):
        for name, data in files.items():
            temp = ops.make_temp(root, prefix=f"{name}.", suffix=".new")
            ops.write_bytes(temp, data)
            ops.replace(temp, root / name)
    return action


def discard_staging(ops, root):
    for name in ops.list_dir(root):
        if name.endswith(".new"):
            ops.remove(root / name)


def scenario(action, recover=None, invariants=None, **kwargs):
    return CrashScenario(
        name=kwargs.pop("name", "synthetic two-record publish"),
        setup=kwargs.pop("setup", write_files(OLD)),
        action=action,
        recover=recover or journaled_recover(),
        invariants=(atomic_outcome(), idempotent_recovery(), RESIDUE) if invariants is None else invariants,
        **kwargs,
    )


def names(violations):
    return sorted({violation.invariant for violation in violations})


# -- the confined seam ----------------------------------------------------------

def test_plan_validation():
    with pytest.raises(ValueError):
        Crash(0, "sideways")
    with pytest.raises(ValueError):
        Crash(-1)
    with pytest.raises(ValueError):
        Crash(Target("replace"), "torn")
    with pytest.raises(ValueError):
        Target("chmod")
    with pytest.raises(ValueError):
        Target("remove", nth=0)
    with pytest.raises(ValueError):
        RootedFileOps(Path("."), plan=[Crash(0), Fail(1)])
    with pytest.raises(ValueError):
        Fail(0, error=RuntimeError("not an OSError"))
    with pytest.raises(ValueError):
        Interleave(0, action="not callable")


def test_descriptors_are_root_relative_and_fixed_format(tmp_path):
    ops = RootedFileOps(tmp_path)
    ops.make_dirs("records")
    ops.write_bytes(tmp_path / "records" / "a.json", b"abc")
    ops.replace("records/a.json", "records/b.json")
    assert [op.describe() for op in ops.trace] == [
        "#0 make_dirs records",
        f"#1 write_bytes records/a.json sha={_sha(b'abc')[:12]} len=3",
        "#2 replace records/a.json -> records/b.json",
    ]


@pytest.mark.parametrize("escape", ["../outside.json", "records/../../outside.json"])
def test_relative_escapes_are_rejected(tmp_path, escape):
    root = tmp_path / "root"
    root.mkdir()
    ops = RootedFileOps(root)
    with pytest.raises(SandboxViolation):
        ops.write_bytes(escape, b"x")
    with pytest.raises(SandboxViolation):
        ops.write_bytes(tmp_path / "outside.json", b"x")
    with pytest.raises(SandboxViolation):
        ops.remove(root)
    with pytest.raises(SandboxViolation):
        ops.make_temp(root, prefix="../")
    assert ops.sandbox_violation and not (tmp_path / "outside.json").exists()


def _symlink_or_skip(source, link):
    try:
        os.symlink(source, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")


def test_links_inside_root_are_rejected_even_when_dotted_away(tmp_path):
    outside, root = tmp_path / "outside", tmp_path / "root"
    outside.mkdir()
    (root / "sub").mkdir(parents=True)
    _symlink_or_skip(outside / "..", root / "sub" / "link")
    ops = RootedFileOps(root, record=False)
    for path in ("sub/link/x", "sub/link/../x", "sub/missing/../link/x"):
        with pytest.raises(SandboxViolation):
            ops.write_bytes(path, b"x")
    assert not (tmp_path / "x").exists() and os.listdir(outside) == []


def test_exists_through_a_file_and_makedirs_of_root_behave_like_the_os(tmp_path):
    ops = RootedFileOps(tmp_path)
    ops.write_bytes("a.json", b"{}")
    assert ops.exists("a.json/child") is False
    ops.make_dirs(tmp_path)  # idiomatic and harmless
    assert ops.sandbox_violation is None and ops.shadow == {"a.json": b"{}"}


def test_harness_refuses_to_wipe_a_root_it_did_not_create(tmp_path):
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "precious.txt").write_bytes(b"synthetic")
    with pytest.raises(ValueError):
        explore_crashes(scenario(journaled_publish(NEW)), tmp_path)
    assert (tmp_path / "root" / "precious.txt").exists()


def test_torn_target_skips_ineligible_matches(tmp_path):
    run = run_with_fault(scenario(naive_read_modify_write, discard_staging, invariants=()),
                         [Crash(Target(path="a.json.*"), "torn")], tmp_path)
    assert run.fired and run.point.startswith("action:#2 write_bytes a.json.~tmp1.new") and run.violations == ()


def test_harness_temp_names_are_deterministic(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    (second / "a.~tmp1.new").write_bytes(b"leftover")
    a = RootedFileOps(first).make_temp(first, prefix="a.", suffix=".new")
    b = RootedFileOps(second).make_temp(second, prefix="a.", suffix=".new")
    assert Path(a).name == "a.~tmp1.new" and Path(b).name == "a.~tmp2.new"
    assert Path(a).read_bytes() == b""


def test_dead_process_does_no_io_and_interleave_runs_first(tmp_path):
    order = []
    ops = RootedFileOps(tmp_path, plan=[
        Crash(Target("write_bytes", "b.json"), "before"),
        Interleave(Target("write_bytes", "b.json"), lambda o, root: order.append("interleave")),
    ])
    ops.write_bytes("a.json", b"a")
    with pytest.raises(SimulatedCrash):
        try:
            ops.write_bytes("b.json", b"b")
        finally:
            order.append("crash")
            ops.write_bytes("cleanup.json", b"never")
    assert order == ["interleave", "crash"]
    assert sorted(os.listdir(tmp_path)) == ["a.json"]
    assert [op.index for op in ops.trace] == [0, 1] and ops.fired


# -- acceptance scenarios ----------------------------------------------------------

def test_a1_journaled_writer_survives_every_crash_point(tmp_path):
    invariants = (atomic_outcome(), idempotent_recovery(), RESIDUE, completes_after(COMMIT))
    report = explore_crashes(scenario(journaled_publish(NEW), invariants=invariants), tmp_path,
                             errors=True, recovery_crashes=True)
    report.assert_ok()
    baseline = report.runs[0]
    mutating = [op for op in baseline.action_trace if op.mutating]
    writes = [op for op in mutating if op.kind == "write_bytes" and op.size]
    action_runs = [run for run in report.runs[1:] if " > " not in run.point]
    assert len(mutating) == 10 and len(writes) == 3
    assert len(action_runs) == 2 * len(mutating) + len(writes) + len(mutating) == 33
    recovery_runs = [run for run in report.runs if " > " in run.point]
    crashed = [run for run in action_runs if not run.point.endswith("@fail")]
    predicted = sum(2 * sum(op.mutating for op in run.recover_trace)
                    + sum(op.kind == "write_bytes" and bool(op.size) for op in run.recover_trace)
                    for run in crashed)
    assert len(recovery_runs) == predicted == 84  # pinned: catches silently shrinking exploration
    assert report.points_explored == len(action_runs) + len(recovery_runs)
    points = [run.point for run in report.runs]
    assert len(points) == len(set(points))
    journal = next(op for op in baseline.action_trace if op.kind == "write_bytes" and "journal" in op.path)
    assert journal.path == "journal.~tmp1.new"  # the journal records temp names and absolute paths


def test_a2_crash_after_one_publication_is_recovered(tmp_path):
    run = run_with_fault(scenario(journaled_publish(NEW)),
                         [Crash(Target("replace", target="*.json"), "after")], tmp_path)
    assert run.fired and run.action_outcome == "crashed" and run.violations == ()
    assert run.point == "action:#11 replace a.json.~tmp1.new -> a.json @after"
    published = [name for name in OLD if run.after_action[name] == NEW[name]]
    assert published == ["a.json"]  # a real partial publication
    assert {name: run.recovered[name] for name in NEW} == NEW
    assert set(run.recovered) == set(NEW)


def test_a3_naive_writer_violation_names_invariant_and_point(tmp_path):
    report = explore_crashes(scenario(naive_publish(NEW), discard_staging), tmp_path)
    atomic = [v for v in report.violations if v.invariant == "atomic_outcome"]
    assert names(report.violations) == ["atomic_outcome"]
    assert {v.point for v in atomic} == {
        "action:#2 replace a.json.~tmp1.new -> a.json @after",
        "action:#3 make_temp b.json.~tmp1.new @before",
        "action:#3 make_temp b.json.~tmp1.new @after",
        "action:#4 write_bytes b.json.~tmp1.new sha=%s len=%d @before" % (_sha(NEW["b.json"])[:12], len(NEW["b.json"])),
        "action:#4 write_bytes b.json.~tmp1.new sha=%s len=%d @after" % (_sha(NEW["b.json"])[:12], len(NEW["b.json"])),
        "action:#4 write_bytes b.json.~tmp1.new sha=%s len=%d @torn" % (_sha(NEW["b.json"])[:12], len(NEW["b.json"])),
        "action:#5 replace b.json.~tmp1.new -> b.json @before",
    }
    with pytest.raises(AssertionError) as raised:
        report.assert_ok()
    message = str(raised.value)
    assert "[atomic_outcome] at action:#2 replace a.json.~tmp1.new -> a.json @after" in message
    assert "action:#2 replace a.json.~tmp1.new -> a.json" in message.split("trace:")[1]


def test_a4_exploration_is_repeatable_and_nondeterminism_is_named(tmp_path):
    first = explore_crashes(scenario(naive_publish(NEW), discard_staging), tmp_path / "one")
    second = explore_crashes(scenario(naive_publish(NEW), discard_staging), tmp_path / "two")
    assert first.violations == second.violations
    assert [run.action_trace for run in first.runs] == [run.action_trace for run in second.runs]

    def noisy(ops, root):
        temp = ops.make_temp(root, prefix="a.json.", suffix=".new")
        ops.write_bytes(temp, random.randbytes(8))
        ops.replace(temp, root / "a.json")

    report = explore_crashes(scenario(noisy, discard_staging, invariants=()), tmp_path / "three")
    assert DETERMINISTIC_NAME in names(report.violations)


DETERMINISTIC_NAME = "deterministic action"


def test_a5_torn_write_is_half_written_and_never_published(tmp_path):
    run = run_with_fault(scenario(journaled_publish(NEW)),
                         [Crash(Target("write_bytes", "a.json.*"), "torn")], tmp_path)
    data = NEW["a.json"]
    assert run.after_action["a.json.~tmp1.new"] == data[: len(data) // 2]
    assert run.recovered == run.before and run.violations == ()


def test_a6_dead_process_cleanup_does_nothing_and_swallowing_is_named(tmp_path):
    def cleanup_in_finally(ops, root):
        temp = ops.make_temp(root, prefix="a.json.", suffix=".new")
        try:
            ops.write_bytes(temp, NEW["a.json"])
            ops.replace(temp, root / "a.json")
        finally:
            if ops.exists(temp):
                ops.remove(temp)

    def converts(ops, root):
        try:
            cleanup_in_finally(ops, root)
        except SimulatedCrash as crash:
            raise RuntimeError("wrapped") from crash

    def swallows(ops, root):
        try:
            cleanup_in_finally(ops, root)
        except BaseException:
            return

    crash = [Crash(Target("replace"), "before")]
    run = run_with_fault(scenario(cleanup_in_finally, discard_staging), crash, tmp_path / "plain")
    assert "a.json.~tmp1.new" in run.after_action and run.violations == ()
    converted = run_with_fault(scenario(converts, discard_staging), crash, tmp_path / "converts")
    assert converted.action_outcome == "crashed" and converted.violations == ()
    swallowed = run_with_fault(scenario(swallows, discard_staging), crash, tmp_path / "swallows")
    assert [(v.invariant, v.point) for v in swallowed.violations] == [
        ("simulated crash propagated", "action:#2 replace a.json.~tmp1.new -> a.json @before")]
    report = explore_crashes(scenario(swallows, discard_staging), tmp_path / "explore")
    points = [v.point for v in report.violations if v.invariant == "simulated crash propagated"]
    assert len(points) == len(set(points)) > 1


def test_a7_runaway_writer_is_bounded_by_name(tmp_path):
    def forever(ops, root):
        while True:
            ops.write_bytes(root / "a.json", b"again")

    report = explore_crashes(scenario(forever, invariants=()), tmp_path, max_ops=5)
    assert [(v.invariant, v.point) for v in report.violations] == [("bounded file operations", "baseline")]
    trace = report.runs[0].action_trace
    assert len(trace) == 5 and trace[-1].index == 4

    def swallows_budget(ops, root):
        try:
            forever(ops, root)
        except BaseException:
            return

    report = explore_crashes(scenario(swallows_budget, invariants=()), tmp_path, max_ops=5)
    assert names(report.violations) == ["bounded file operations"]


def test_a8_a_fault_that_never_fires_fails(tmp_path):
    missing = Target("remove", "no-such-file")
    for plan in ([Crash(missing)], [Interleave(missing, lambda ops, root: None)], [Crash(0, "torn")]):
        run = run_with_fault(scenario(journaled_publish(NEW)), plan, tmp_path)
        assert "planned fault fired" in names(run.violations), plan
        assert not run.fired


def naive_read_modify_write(ops, root):
    data = ops.read_bytes(root / "a.json")
    temp = ops.make_temp(root, prefix="a.json.", suffix=".new")
    ops.write_bytes(temp, data + b" +writer")
    ops.replace(temp, root / "a.json")


def test_a9_interleaved_external_edit_is_lost_and_named(tmp_path):
    edited = {}

    def external_edit(ops, root):
        ops.write_bytes(root / "a.json", b"external edit")
        edited["yes"] = True

    def preserved(outcome):
        if edited and b"external edit" not in outcome.recovered["a.json"]:
            return "the concurrent external edit was overwritten"
        return None

    invariants = (Invariant("external edit preserved", preserved), atomic_outcome())
    run = run_with_fault(
        scenario(naive_read_modify_write, discard_staging, invariants=invariants),
        [Interleave(Target("replace"), external_edit)], tmp_path,
    )
    assert names(run.violations) == ["external edit preserved"]  # interleaved paths are not the writer's
    assert all(op.path != "a.json" or op.kind != "write_bytes" for op in run.action_trace)
    assert run.point.endswith("@interleave")


def test_a9_a_runaway_interleave_is_a_raw_fixture_bug(tmp_path):
    def runaway(ops, root):
        while True:
            ops.exists(root / "a.json")

    with pytest.raises(OperationBudgetExceeded):
        run_with_fault(scenario(journaled_publish(NEW)), [Interleave(0, runaway)], tmp_path, max_ops=50)


@pytest.mark.parametrize("phase", ["action", "recover"])
def test_a10_link_escape_is_a_sandbox_violation_in_any_phase(tmp_path, phase):
    outside = tmp_path / "outside"
    outside.mkdir()

    def escape(ops, root):
        try:
            os.symlink(outside, root / "link", target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable on this platform")
        ops.write_bytes("link/x", b"x")

    def harmless(ops, root):
        ops.write_bytes(root / "a.json", NEW["a.json"])

    action, recover = (escape, discard_staging) if phase == "action" else (harmless, escape)
    report = explore_crashes(scenario(action, recover, invariants=()), tmp_path / "work")
    sandbox = [v for v in report.violations if v.invariant == "sandbox"]
    assert len(sandbox) == 1 and sandbox[0].point == "baseline" and phase in sandbox[0].detail
    assert os.listdir(outside) == []


def test_a11_injected_io_error_must_surface(tmp_path):
    run = run_with_fault(scenario(journaled_publish(NEW)), [Fail(COMMIT)], tmp_path)
    assert run.action_outcome == "raised" and "OSError" in run.action_error
    assert run.violations == () and run.recovered == run.before

    def hides_errors(ops, root):
        try:
            journaled_publish(NEW)(ops, root)
        except OSError:
            pass

    hidden = run_with_fault(scenario(hides_errors), [Fail(COMMIT)], tmp_path)
    assert [(v.invariant, v.point) for v in hidden.violations] == [
        ("failure surfaced", "action:#10 replace journal.~tmp1.new -> .journal @fail")]


def test_a12_interrupted_recovery_is_explored(tmp_path):
    fragile = scenario(journaled_publish(NEW), marker_recover)
    assert explore_crashes(fragile, tmp_path).violations == ()
    report = explore_crashes(fragile, tmp_path, recovery_crashes=True)
    assert names(report.violations) == ["recovery completes"]
    assert all(" > recover:" in v.point and "stale recovery marker" in v.detail for v in report.violations)
    assert any("recover:#" in step for step in report.violations[0].trace)


def test_a12_nondeterministic_recovery_is_named_at_a_recovery_point(tmp_path):
    def noisy_recover(ops, root):
        recovering = ops.exists(root / ".journal")
        journaled_recover()(ops, root)
        if recovering:
            ops.write_bytes(root / "recovery.log", random.randbytes(8))

    report = explore_crashes(scenario(journaled_publish(NEW), noisy_recover, invariants=()), tmp_path,
                             torn=False, recovery_crashes=True)
    flagged = [v for v in report.violations if v.invariant == DETERMINISTIC_NAME]
    assert flagged and all(" > recover:" in v.point and "recover op #" in v.detail for v in flagged)


def test_a12_always_rollback_recovery_breaks_commit_durability(tmp_path):
    invariants = (atomic_outcome(), completes_after(COMMIT))
    report = explore_crashes(scenario(journaled_publish(NEW), rollback_recover, invariants=invariants), tmp_path)
    baseline = report.runs[0].action_trace
    commit = next(op.index for op in baseline if op.kind == "replace" and op.target == ".journal")
    first_publish = commit + 1
    durability = [v for v in report.violations if v.invariant == "completes_after"]
    assert durability and all(int(v.point.split("#")[1].split()[0]) >= commit for v in durability)
    early_atomic = [v for v in report.violations if v.invariant == "atomic_outcome"
                    and int(v.point.split("#")[1].split()[0]) < first_publish]
    assert early_atomic == []


def test_a13_writes_that_bypass_the_seam_are_named(tmp_path):
    def bypass(ops, root):
        ops.write_bytes(root / "a.json", NEW["a.json"])
        with open(root / "b.json", "wb") as handle:
            handle.write(NEW["b.json"])

    report = explore_crashes(scenario(bypass, invariants=()), tmp_path)
    assert [(v.invariant, v.point) for v in report.violations] == [("all mutations mediated", "baseline")]
    assert "b.json" in report.violations[0].detail

    def bypass_then_replace(ops, root):
        with open(root / "a.json.tmp", "wb") as handle:
            handle.write(NEW["a.json"])
        ops.replace(root / "a.json.tmp", root / "a.json")

    report = explore_crashes(scenario(bypass_then_replace, invariants=()), tmp_path)
    assert names(report.violations) == ["all mutations mediated"]


def test_a13_bypass_before_an_interleave_is_not_absorbed(tmp_path):
    def conditional_bypass(ops, root):
        ops.write_bytes(root / "a.json", NEW["a.json"])
        if ops.exists(root / "ext.json"):
            (root / "b.json").write_bytes(NEW["b.json"])  # bypasses the seam
        ops.write_bytes(root / "c.json", b"c")

    plan = [Interleave(1, lambda ops, root: ops.write_bytes(root / "ext.json", b"external")),
            Interleave(Target("write_bytes", "c.json"), lambda ops, root: None)]
    run = run_with_fault(scenario(conditional_bypass, invariants=()), plan, tmp_path)
    flagged = [v for v in run.violations if v.invariant == "all mutations mediated"]
    assert flagged and any("b.json" in v.detail for v in flagged)


def test_baseline_must_complete_and_mutate(tmp_path):
    def raises(ops, root):
        ops.write_bytes(root / "a.json", NEW["a.json"])
        raise RuntimeError("synthetic writer bug")

    def reads_only(ops, root):
        ops.read_bytes(root / "a.json")

    for action, detail in ((raises, "synthetic writer bug"), (reads_only, "no mutating")):
        report = explore_crashes(scenario(action, invariants=()), tmp_path)
        assert [(v.invariant, v.point) for v in report.violations] == [("baseline completes", "baseline")]
        assert detail in report.violations[0].detail and report.points_explored == 0


def test_commit_point_is_resolved_in_the_faulted_trace(tmp_path):
    def reacts_to_external_file(ops, root):
        if ops.exists(root / "ext.json"):
            ops.read_bytes(root / "ext.json")  # an interleave shifts later operation indices
        temp = ops.make_temp(root, prefix="data.", suffix=".new")
        ops.write_bytes(temp, b"new")
        ops.replace(temp, root / "data")

    commit = Target("replace", target="data")
    plan = [Interleave(0, lambda ops, root: ops.write_bytes(root / "ext.json", b"external")),
            Crash(commit, "before")]
    run = run_with_fault(scenario(reacts_to_external_file, discard_staging, invariants=(completes_after(commit),),
                                  setup=write_files({"data": b"old"})), plan, tmp_path)
    assert run.point.startswith("action:#4 replace") and run.violations == ()


def test_a12_torn_recovery_writes_are_explored(tmp_path):
    def in_place_recover(ops, root):
        if ops.exists(root / ".journal"):
            for name, entry in json.loads(ops.read_bytes(root / ".journal")).items():
                current = _sha(ops.read_bytes(root / name))
                if ops.exists(entry["temp"]) and current == entry["old"]:
                    ops.write_bytes(root / name, ops.read_bytes(entry["temp"]))  # not atomic
            ops.remove(root / ".journal")
        discard_staging(ops, root)

    fragile = scenario(journaled_publish(NEW), in_place_recover)
    assert explore_crashes(fragile, tmp_path, torn=False, recovery_crashes=True).violations == ()
    report = explore_crashes(fragile, tmp_path, recovery_crashes=True)
    assert names(report.violations) == ["atomic_outcome"]
    assert all(" > recover:" in v.point and v.point.endswith("@torn") for v in report.violations)


def test_recovery_cannot_redefine_the_completed_state(tmp_path):
    def publish(ops, root):
        temp = ops.make_temp(root, prefix="data.", suffix=".new")
        ops.write_bytes(temp, b"new")
        ops.replace(temp, root / "data")

    def restores_old(ops, root):
        discard_staging(ops, root)
        ops.write_bytes(root / "data", b"old")

    invariants = (atomic_outcome(), idempotent_recovery(), completes_after(Target("replace", target="data")))
    report = explore_crashes(scenario(publish, restores_old, invariants=invariants,
                                      setup=write_files({"data": b"old"})), tmp_path, recovery_crashes=True)
    assert [(v.invariant, v.point) for v in report.violations] == [
        ("recovery preserves completion", "baseline"), ("completes_after", "baseline")]
    assert report.points_explored == 0


def test_interleave_only_runs_still_check_commit_durability(tmp_path):
    def publish(ops, root):
        temp = ops.make_temp(root, prefix="data.", suffix=".new")
        ops.write_bytes(temp, b"new")
        ops.replace(temp, root / "data")

    def rolls_back_if_marked(ops, root):
        discard_staging(ops, root)
        if ops.exists(root / "marker"):
            ops.write_bytes(root / "data", b"old")

    commit = Target("replace", target="data")
    invariants = (atomic_outcome(), idempotent_recovery(), completes_after(commit))
    run = run_with_fault(scenario(publish, rolls_back_if_marked, invariants=invariants,
                                  setup=write_files({"data": b"old"})),
                         [Interleave(0, lambda ops, root: ops.write_bytes(root / "marker", b"x"))], tmp_path)
    assert names(run.violations) == ["completes_after"]
    assert "undid a committed" in run.violations[0].detail


def test_combined_plan_is_labelled_by_its_crash(tmp_path):
    plan = [Interleave(0, lambda ops, root: None), Crash(Target("replace", target="*.json"), "after")]
    run = run_with_fault(scenario(journaled_publish(NEW), rollback_recover,
                                  invariants=(completes_after(COMMIT),)), plan, tmp_path)
    assert run.point == "action:#11 replace a.json.~tmp1.new -> a.json @after"
    assert names(run.violations) == ["completes_after"]


def replica_scenario(guarded, meddle=True):
    def setup(ops, root):
        write_files({"rec.json": b'{"rev": 2, "from": "desktop"}'}, "desktop")(ops, root)
        write_files({"rec.json": b'{"rev": 1, "from": "phone"}'}, "phone")(ops, root)

    def copy_desktop_to_phone(ops, root):
        journaled_publish({"rec.json": ops.read_bytes(root / "desktop" / "rec.json")}, "phone")(ops, root)

    def phone_edit(ops, root):
        ops.write_bytes(root / "phone" / "rec.json", b'{"rev": 1, "edited": "on phone"}')

    invariants = ((preserves_meddle(), untouched("desktop/*"), idempotent_recovery()) if meddle
                  else (atomic_outcome(), untouched("desktop/*"), no_residue("phone/*.new", "phone/.journal")))
    return CrashScenario(
        name="replica copy", setup=setup, action=copy_desktop_to_phone,
        recover=journaled_recover("phone", guarded=guarded), invariants=invariants,
        meddle=phone_edit if meddle else None,
    )


def test_a14_replica_copy_is_atomic_and_leaves_the_source_untouched(tmp_path):
    explore_crashes(replica_scenario(guarded=False, meddle=False), tmp_path).assert_ok()


def test_a14_blind_roll_forward_clobbers_an_edit_made_while_down(tmp_path):
    report = explore_crashes(replica_scenario(guarded=False), tmp_path)
    baseline = report.runs[0].action_trace
    commit = next(op.index for op in baseline if op.target == "phone/.journal")
    publish = next(op.index for op in baseline if op.target == "phone/rec.json")
    expected = {run.point for run in report.runs[1:]
                if commit < _index(run.point) < publish
                or (_index(run.point) == commit and run.point.endswith("@after"))
                or (_index(run.point) == publish and not run.point.endswith("@after"))}
    expected = {point for point in expected if not point.endswith("@fail")}
    assert names(report.violations) == ["preserves_meddle"]
    assert {v.point for v in report.violations} == expected and expected
    guarded = explore_crashes(replica_scenario(guarded=True), tmp_path)
    guarded.assert_ok()
    kept = [run for run in guarded.runs if run.point in expected]
    assert kept and all(run.recovered["phone/rec.json.conflict"] == run.before["desktop/rec.json"]
                        and run.recovered["phone/rec.json"] == run.after_meddle["phone/rec.json"] for run in kept)


def _index(point):
    return int(point.split("#")[1].split()[0])


def test_a15_corrupt_journal_is_a_named_recovery_failure(tmp_path):
    def setup(ops, root):
        write_files(OLD)(ops, root)
        ops.write_bytes(root / ".journal", b"{not json")

    report = explore_crashes(scenario(journaled_publish(NEW), setup=setup), tmp_path)
    assert names(report.violations) == ["recovery completes"]
    assert all("JSONDecodeError" in v.detail for v in report.violations)


def test_harness_invariant_names_are_stable():
    assert HARNESS_INVARIANTS == (
        "baseline completes", "planned fault fired", "deterministic action",
        "simulated crash propagated", "failure surfaced", "bounded file operations",
        "all mutations mediated", "recovery completes", "recovery preserves completion", "sandbox",
    )



# -- interleaves under explore_crashes (019) ---------------------------------------

HUMAN = b"external edit"


def _external_edit(ops, root):
    ops.write_bytes(root / "a.json", HUMAN)


def guarded_read_modify_write(ops, root):
    """Compare-and-swap: an edit that lands before the re-check is kept and ours is set aside."""
    data = ops.read_bytes(root / "a.json")
    temp = ops.make_temp(root, prefix="a.json.", suffix=".new")
    ops.write_bytes(temp, data + b" +writer")
    if ops.read_bytes(root / "a.json") != data:
        ops.replace(temp, root / "a.json.theirs")
        return
    ops.replace(temp, root / "a.json")


def _interleave_scenario(action, **kwargs):
    return scenario(action, discard_staging,
                    invariants=(preserves_interleave(), idempotent_recovery(), RESIDUE), **kwargs)


def test_explore_with_interleaves_proves_a_guarded_writer(tmp_path):
    edit = Interleave(Target("write_bytes", "a.json.~tmp1.new"), _external_edit)
    report = explore_crashes(_interleave_scenario(guarded_read_modify_write), tmp_path, errors=True,
                             interleaves=[edit])
    report.assert_ok()
    assert report.runs[0].recovered["a.json"] == HUMAN  # the baseline itself raced
    assert report.points_explored >= 6
    # crashes before the interleaved operation never reach it: excused, not "never triggered"
    assert any(run.point.startswith("action:#1 make_temp") for run in report.runs)


def test_explore_with_interleaves_names_a_lost_edit(tmp_path):
    edit = Interleave(Target("make_temp"), _external_edit)
    report = explore_crashes(_interleave_scenario(naive_read_modify_write), tmp_path, interleaves=[edit])
    assert names(report.violations) == ["preserves_interleave"]
    assert report.violations[0].point == "baseline"


def test_a_shared_interleave_must_fire_in_the_baseline(tmp_path):
    never = Interleave(Target("remove", "no-such-file"), _external_edit)
    report = explore_crashes(_interleave_scenario(guarded_read_modify_write), tmp_path, interleaves=[never])
    assert names(report.violations) == ["planned fault fired"]
    assert len(report.runs) == 1
    with pytest.raises(ValueError):
        explore_crashes(_interleave_scenario(guarded_read_modify_write), tmp_path, interleaves=[Crash(0)])


def test_nondeterminism_is_still_named_with_interleaves(tmp_path):
    counter = itertools.count()

    def drifting(ops, root):
        guarded_read_modify_write(ops, root)
        ops.write_bytes(root / f"log{next(counter)}", b"x")

    edit = Interleave(Target("write_bytes", "a.json.~tmp1.new"), _external_edit)
    report = explore_crashes(_interleave_scenario(drifting), tmp_path, interleaves=[edit])
    assert "deterministic action" in names(report.violations)


def test_select_shards_the_points_and_run_plans_shares_one_baseline(tmp_path):
    sc = scenario(journaled_publish(NEW), invariants=(atomic_outcome(), idempotent_recovery(), RESIDUE))
    whole = explore_crashes(sc, tmp_path)
    replaces = explore_crashes(sc, tmp_path, select=lambda op: op.kind == "replace")
    rest = explore_crashes(sc, tmp_path, select=lambda op: op.kind != "replace")
    whole.assert_ok(), replaces.assert_ok(), rest.assert_ok()
    assert replaces.points_explored + rest.points_explored == whole.points_explored
    assert {run.point for run in whole.runs[1:]} == {run.point for run in replaces.runs[1:] + rest.runs[1:]}
    plans = [[Interleave(index, lambda ops, root: None)] for index in range(3)]
    report = run_plans(sc, plans, tmp_path)
    report.assert_ok()
    assert len(report.runs) == 4 and report.runs[0].point == "baseline"


def test_select_asking_for_a_disabled_fault_timing_is_an_error(tmp_path):
    """Asking for ``fail`` without ``errors=True`` (or ``torn`` without ``torn=True``) would explore nothing."""
    import pytest as _pytest
    from grimoire.testing.explore import _timings
    from grimoire.testing import FileOp
    op = FileOp(0, "write_bytes", "a.json", None, None, 3)
    with _pytest.raises(ValueError, match="not enabled"):
        _timings(op, lambda _: {"fail"}, torn=True, errors=False)
    with _pytest.raises(ValueError, match="not enabled"):
        _timings(op, lambda _: {"torn"}, torn=False, errors=True)
    assert _timings(op, lambda _: {"fail"}, torn=False, errors=True) == {"fail"}



def test_an_interleaved_write_then_remove_is_not_a_lost_edit(tmp_path):
    """faults: a path an interleave removed stays absent (TESTING.md), so the earlier write is not "lost"."""
    from grimoire.testing import CrashScenario, Interleave, preserves_interleave, run_plans

    def setup(ops, root):
        ops.write_bytes(root / "a.json", b"1")

    def action(ops, root):
        ops.write_bytes(root / "a.json", b"2")
        ops.write_bytes(root / "a.json", b"3")

    scenario = CrashScenario("write-then-remove", setup, action, lambda ops, root: None, (preserves_interleave(),))
    plan = [Interleave(0, lambda ops, root: ops.write_bytes(root / "ext.json", b"x")),
            Interleave(1, lambda ops, root: ops.remove(root / "ext.json"))]
    run_plans(scenario, [plan], tmp_path).assert_ok()


def test_a_sibling_whose_name_starts_with_the_root_is_outside_the_sandbox(tmp_path):
    """The string-prefix confinement must not treat ``root-evil`` as inside ``root``."""
    from grimoire.testing import SandboxViolation
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "root-evil").mkdir()
    with pytest.raises(SandboxViolation):
        RootedFileOps(root).write_bytes(tmp_path / "root-evil" / "f.json", b"x")
    assert not (tmp_path / "root-evil" / "f.json").exists()


@pytest.mark.parametrize("chosen", ["before", {"later"}])
def test_select_returning_an_unknown_timing_is_an_error(tmp_path, chosen):
    """A bare string (read as its letters) or a misspelt timing would explore nothing and still pass."""
    from grimoire.testing import CrashScenario, atomic_outcome, explore_crashes

    def setup(ops, root):
        ops.write_bytes(root / "a.json", b"1")

    def action(ops, root):
        ops.write_bytes(root / "a.json", b"2")
    scenario = CrashScenario("s", setup, action, lambda ops, root: None, (atomic_outcome(),))
    with pytest.raises(ValueError, match="unknown fault timings"):
        explore_crashes(scenario, tmp_path, select=lambda op: chosen)


def test_interleaves_also_join_the_recovery_crash_runs(tmp_path):
    from grimoire.testing import Interleave, Target, explore_crashes
    edit = Interleave(Target("write_bytes", "a.json.~tmp1.new"), _external_edit)
    explore_crashes(_interleave_scenario(guarded_read_modify_write), tmp_path, interleaves=[edit],
                    recovery_crashes=True).assert_ok()

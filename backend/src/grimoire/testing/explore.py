"""Run a writer under planned faults, restart it, and check named invariants.

Each run happens sequentially at one fixed root (``<workdir>/root``): the root is
wiped, ``setup`` builds the initial state, ``action`` runs under the fault plan,
an optional ``meddle`` edits the disk while the process is "down", and
``recover`` runs twice as a fresh process. A failure is reported as a named
:class:`Violation` at a precise fault point, never as a hang.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from .faults import (
    At, Crash, Fail, Fault, FileOp, OperationBudgetExceeded, RootedFileOps, SimulatedCrash,
    nth_match,
)
from .fileops import FileOps, SandboxViolation, Tree, snapshot, wipe

Phase = Callable[[FileOps, Path], object]

_MISSING = object()

# Harness invariant names (always checked).
BASELINE_COMPLETES = "baseline completes"
FAULT_FIRED = "planned fault fired"
DETERMINISTIC = "deterministic action"
CRASH_PROPAGATED = "simulated crash propagated"
FAILURE_SURFACED = "failure surfaced"
BOUNDED = "bounded file operations"
MEDIATED = "all mutations mediated"
RECOVERY_COMPLETES = "recovery completes"
SANDBOX = "sandbox"
HARNESS_INVARIANTS = (
    BASELINE_COMPLETES, FAULT_FIRED, DETERMINISTIC, CRASH_PROPAGATED, FAILURE_SURFACED,
    BOUNDED, MEDIATED, RECOVERY_COMPLETES, SANDBOX,
)


@dataclass(frozen=True)
class CrashOutcome:
    """What an invariant may inspect about one run (trees already normalized)."""

    point: str
    before: Tree
    completed: Tree
    recovered: Tree
    recovered_twice: Tree
    after_action: Tree = field(default_factory=dict)
    after_meddle: Tree = field(default_factory=dict)
    meddled_paths: frozenset[str] = frozenset()
    action_outcome: str = "returned"
    action_index: int | None = None
    action_timing: str | None = None
    baseline_trace: tuple[FileOp, ...] = ()


@dataclass(frozen=True)
class Invariant:
    """``check`` returns ``None`` when the invariant holds, else a description."""

    name: str
    check: Callable[[CrashOutcome], str | None]


@dataclass(frozen=True)
class Violation:
    invariant: str
    point: str
    detail: str
    trace: tuple[str, ...] = ()

    def format(self) -> str:
        lines = [f"[{self.invariant}] at {self.point}", f"    {self.detail}"]
        if self.trace:
            lines.append("    trace:")
            lines.extend(f"      {step}" for step in self.trace)
        return "\n".join(lines)


@dataclass(frozen=True)
class CrashScenario:
    name: str
    setup: Phase
    action: Phase
    recover: Phase
    invariants: Sequence[Invariant] = ()
    meddle: Phase | None = None
    normalize: Callable[[Tree], Tree] | None = None


@dataclass(frozen=True)
class FaultRun:
    point: str
    fired: bool
    action_trace: tuple[FileOp, ...]
    recover_trace: tuple[FileOp, ...]
    action_outcome: str
    action_error: str | None
    before: Tree
    after_action: Tree
    after_meddle: Tree
    meddled_paths: frozenset[str]
    recovered: Tree
    recovered_twice: Tree
    recovery_error: str | None
    violations: tuple[Violation, ...]


@dataclass(frozen=True)
class CrashReport:
    scenario: str
    runs: tuple[FaultRun, ...]
    violations: tuple[Violation, ...]

    @property
    def points_explored(self) -> int:
        """Fault points run, excluding the baseline."""
        return max(len(self.runs) - 1, 0)

    def assert_ok(self) -> None:
        if self.violations:
            body = "\n".join(violation.format() for violation in self.violations)
            raise AssertionError(
                f"crash scenario {self.scenario!r}: {len(self.violations)} invariant "
                f"violation(s) across {self.points_explored} fault points\n{body}"
            )


# -- built-in invariants ---------------------------------------------------

def _without(tree: Tree, excluded: frozenset[str]) -> Tree:
    return {key: value for key, value in tree.items() if key not in excluded}


def _difference(actual: Tree, expected: Tree, limit: int = 4) -> str:
    keys = sorted(key for key in set(actual) | set(expected)
                  if actual.get(key, _MISSING) != expected.get(key, _MISSING))
    shown = ", ".join(
        f"{key}: {_show(actual.get(key, _MISSING))} (expected {_show(expected.get(key, _MISSING))})"
        for key in keys[:limit]
    )
    return shown + (f", … {len(keys) - limit} more" if len(keys) > limit else "")


def _show(value: object) -> str:
    if value is _MISSING:
        return "absent"
    if value is None:
        return "directory"
    assert isinstance(value, bytes)
    return repr(value if len(value) <= 24 else value[:24] + b"...")


def atomic_outcome() -> Invariant:
    def check(outcome: CrashOutcome) -> str | None:
        excluded = outcome.meddled_paths
        recovered = _without(outcome.recovered, excluded)
        before, completed = _without(outcome.before, excluded), _without(outcome.completed, excluded)
        if recovered in (before, completed):
            return None
        return ("recovered state is neither the pre-action nor the completed state; vs completed: "
                + _difference(recovered, completed))
    return Invariant("atomic_outcome", check)


def idempotent_recovery() -> Invariant:
    def check(outcome: CrashOutcome) -> str | None:
        if outcome.recovered_twice == outcome.recovered:
            return None
        return "a second recovery changed the disk: " + _difference(outcome.recovered_twice, outcome.recovered)
    return Invariant("idempotent_recovery", check)


def no_residue(*globs: str) -> Invariant:
    def check(outcome: CrashOutcome) -> str | None:
        residue = sorted(key for key in outcome.recovered if any(fnmatchcase(key, g) for g in globs))
        return f"left after recovery: {residue}" if residue else None
    return Invariant("no_residue", check)


def completes_after(at: At) -> Invariant:
    """Once the commit operation has executed, recovery must finish the action."""
    def check(outcome: CrashOutcome) -> str | None:
        commit = nth_match(outcome.baseline_trace, at)
        if commit is None:
            return f"commit operation {at!r} not found in the baseline trace"
        index, timing = outcome.action_index, outcome.action_timing
        if index is None or index < commit or (index == commit and timing != "after"):
            return None
        excluded = outcome.meddled_paths
        recovered, completed = _without(outcome.recovered, excluded), _without(outcome.completed, excluded)
        if recovered == completed:
            return None
        return f"fault after commit op #{commit} was not rolled forward: " + _difference(recovered, completed)
    return Invariant("completes_after", check)


def untouched(glob: str) -> Invariant:
    """Paths matching ``glob`` (e.g. a source replica) never change."""
    def check(outcome: CrashOutcome) -> str | None:
        keys = sorted(key for key in set(outcome.before) | set(outcome.recovered) if fnmatchcase(key, glob))
        changed = [key for key in keys if outcome.before.get(key, _MISSING) != outcome.recovered.get(key, _MISSING)]
        return f"modified: {changed}" if changed else None
    return Invariant("untouched", check)


def preserves_meddle() -> Invariant:
    """An external edit made while the process was down survives recovery."""
    def check(outcome: CrashOutcome) -> str | None:
        clobbered = [key for key in sorted(outcome.meddled_paths)
                     if outcome.recovered.get(key, _MISSING) != outcome.after_meddle.get(key, _MISSING)]
        if not clobbered:
            return None
        return "recovery overwrote an external edit: " + _difference(
            {key: outcome.recovered.get(key) for key in clobbered if key in outcome.recovered},
            {key: outcome.after_meddle.get(key) for key in clobbered if key in outcome.after_meddle},
        )
    return Invariant("preserves_meddle", check)


# -- running ---------------------------------------------------------------

class _Abort(Exception):
    """Stop the current run; its violation has been recorded."""


@dataclass
class _Phase:
    outcome: str
    error: str | None


class _Run:
    def __init__(self, scenario: CrashScenario, workdir: Path, *, action_plan: Sequence[Fault],
                 recover_plan: Sequence[Fault], meddle: bool, max_ops: int,
                 baseline: FaultRun | None, expected_recover: tuple[FileOp, ...] | None) -> None:
        self.scenario = scenario
        self.workdir = Path(workdir)
        self.action_plan = tuple(action_plan)
        self.recover_plan = tuple(recover_plan)
        self.meddle = meddle
        self.max_ops = max_ops
        self.baseline = baseline
        self.expected_recover = expected_recover
        self.violations: list[Violation] = []
        self.point = "baseline" if baseline is None else "action:(not started)"
        self.action_ops: RootedFileOps | None = None
        self.recover_ops: RootedFileOps | None = None
        self.action = _Phase("returned", None)
        self.recovery_error: str | None = None
        self.trees: dict[str, Tree] = {}
        self.meddled: frozenset[str] = frozenset()

    # violations ----------------------------------------------------------
    def _trace(self) -> tuple[str, ...]:
        steps = [f"action:{op.describe()}" for op in (self.action_ops.trace if self.action_ops else ())]
        if self.recover_plan and self.recover_ops is not None:
            steps += [f"recover:{op.describe()}" for op in self.recover_ops.trace]
        return tuple(steps)

    def flag(self, invariant: str, detail: str) -> None:
        if any(v.invariant == invariant and v.detail == detail for v in self.violations):
            return
        self.violations.append(Violation(invariant, self.point, detail, self._trace()))

    # guarded helpers -----------------------------------------------------
    def snap(self, label: str) -> Tree:
        try:
            tree = snapshot(self.root)
        except SandboxViolation as error:
            self.flag(SANDBOX, str(error))
            raise _Abort from error
        self.trees[label] = tree
        return tree

    def ops(self, phase: str, plan: Sequence[Fault] = (), record: bool = True) -> RootedFileOps:
        try:
            return RootedFileOps(self.root, phase=phase, plan=plan, max_ops=self.max_ops, record=record)
        except SandboxViolation as error:
            self.flag(SANDBOX, str(error))
            raise _Abort from error

    def run_phase(self, ops: RootedFileOps, function: Phase) -> _Phase:
        returned, error = True, None
        try:
            function(ops, Path(self.root))
        except (SimulatedCrash, OperationBudgetExceeded) as exc:
            returned, error = False, repr(exc)
        except Exception as exc:
            returned, error = False, repr(exc)
        if ops.interleave_error is not None:
            raise ops.interleave_error
        if ops.sandbox_violation is not None:
            self.flag(SANDBOX, f"{ops.phase}: {ops.sandbox_violation}")
            raise _Abort
        if ops.budget_exceeded:
            self.flag(BOUNDED, f"{ops.phase} exceeded {ops.max_ops} file operations")
            raise _Abort
        if ops.crashed:
            if returned:
                self.flag(CRASH_PROPAGATED, f"{ops.phase} returned normally after the simulated crash")
            return _Phase("crashed", error)
        if ops.failed and returned:
            self.flag(FAILURE_SURFACED, f"{ops.phase} returned normally after an injected I/O error")
        return _Phase("returned" if returned else "raised", error)

    def check_mediated(self, ops: RootedFileOps, label: str) -> Tree:
        actual = self.snap(label)
        if ops.shadow is not None and ops.shadow != actual:
            self.flag(MEDIATED, f"{ops.phase} changed files outside the FileOps seam: "
                      + _difference(actual, ops.shadow))
        return actual

    # the run ---------------------------------------------------------------
    def execute(self) -> FaultRun:
        root_dir = self.workdir / "root"
        wipe(root_dir)
        root_dir.mkdir(parents=True)
        self.root = Path(os.path.realpath(root_dir))
        try:
            self._execute()
        except _Abort:
            pass
        return self._result()

    def _execute(self) -> None:
        setup_ops = RootedFileOps(self.root, phase="setup", max_ops=self.max_ops, record=False)
        self.scenario.setup(setup_ops, Path(self.root))  # fixture bugs propagate
        self.snap("before")

        self.action_ops = self.ops("action", self.action_plan)
        self.action = self.run_phase(self.action_ops, self.scenario.action)
        self.point = self._point()
        after_action = self.check_mediated(self.action_ops, "after_action")
        self._check_action()

        after_meddle = after_action
        if self.meddle and self.scenario.meddle is not None:
            self.scenario.meddle(self.ops("meddle", record=False), Path(self.root))
            after_meddle = self.snap("after_meddle")
        self.trees["after_meddle"] = after_meddle
        self.meddled = frozenset(
            key for key in set(after_action) | set(after_meddle)
            if after_action.get(key, _MISSING) != after_meddle.get(key, _MISSING)
        )

        if self.recover_plan:
            self.recover_ops = self.ops("recover", self.recover_plan)
            faulted = self.run_phase(self.recover_ops, self.scenario.recover)
            self.point = f"{self.point} > {self._fault_label(self.recover_ops)}"
            self._check_fired(self.recover_ops)
            self._check_deterministic(self.recover_ops, self.expected_recover or ())
            self.check_mediated(self.recover_ops, "after_faulted_recover")
            if faulted.outcome != "crashed":
                self._recovery_failed(faulted)
        else:
            self.recover_ops = self._clean_recover("recovered")
        if self.recover_plan:
            self._clean_recover("recovered")
        self._clean_recover("recovered_twice")
        if self.recovery_error is None:
            self._check_invariants()

    def _clean_recover(self, label: str) -> RootedFileOps:
        ops = self.ops("recover")
        result = self.run_phase(ops, self.scenario.recover)
        self.check_mediated(ops, label)
        if result.outcome != "returned":
            self._recovery_failed(result)
        return ops

    def _recovery_failed(self, result: _Phase) -> None:
        self.recovery_error = result.error or result.outcome
        self.flag(RECOVERY_COMPLETES, f"recover raised {self.recovery_error}")
        raise _Abort

    def _point(self) -> str:
        if self.baseline is None:
            return "baseline"
        assert self.action_ops is not None
        return self._fault_label(self.action_ops)

    @staticmethod
    def _fault_label(ops: RootedFileOps) -> str:
        if ops.fault_index is None:
            return f"{ops.phase}:(planned fault not reached)"
        timing = ops.fault_timing or "interleave"
        return f"{ops.phase}:{ops.trace[ops.fault_index].describe()} @{timing}"

    def _check_action(self) -> None:
        assert self.action_ops is not None
        if self.baseline is None:
            if self.action.outcome != "returned":
                self.flag(BASELINE_COMPLETES, f"action without faults ended {self.action.outcome}: {self.action.error}")
                raise _Abort
            if not any(op.mutating for op in self.action_ops.trace):
                self.flag(BASELINE_COMPLETES, "action performed no mutating file operation")
                raise _Abort
            return
        self._check_fired(self.action_ops)
        self._check_deterministic(self.action_ops, self.baseline.action_trace)

    def _check_fired(self, ops: RootedFileOps) -> None:
        if not ops.fired:
            self.flag(FAULT_FIRED, f"{ops.phase} faults never triggered: {list(ops.unfired)!r}")

    def _check_deterministic(self, ops: RootedFileOps, expected: tuple[FileOp, ...]) -> None:
        trace = ops.trace
        length = ops.fault_index + 1 if ops.fault_index is not None else max(len(trace), len(expected))
        for index in range(length):
            actual = trace[index] if index < len(trace) else None
            wanted = expected[index] if index < len(expected) else None
            if actual != wanted:
                self.flag(DETERMINISTIC, f"{ops.phase} op #{index} was "
                          f"{actual.describe() if actual else 'absent'}; baseline "
                          f"{wanted.describe() if wanted else 'absent'}")
                return

    def _check_invariants(self) -> None:
        normalize = self.scenario.normalize or (lambda tree: tree)
        completed = self.baseline.recovered if self.baseline else self.trees["recovered"]
        action_ops = self.action_ops
        assert action_ops is not None
        outcome = CrashOutcome(
            point=self.point,
            before=normalize(self.trees["before"]),
            completed=normalize(completed),
            recovered=normalize(self.trees["recovered"]),
            recovered_twice=normalize(self.trees["recovered_twice"]),
            after_action=normalize(self.trees["after_action"]),
            after_meddle=normalize(self.trees["after_meddle"]),
            meddled_paths=self.meddled,
            action_outcome=self.action.outcome,
            action_index=action_ops.fault_index if self.baseline else None,
            action_timing=action_ops.fault_timing if self.baseline else None,
            baseline_trace=self.baseline.action_trace if self.baseline else tuple(action_ops.trace),
        )
        for invariant in self.scenario.invariants:
            detail = invariant.check(outcome)
            if detail is not None:
                self.flag(invariant.name, detail)

    def _result(self) -> FaultRun:
        action_ops = self.action_ops
        return FaultRun(
            point=self.point,
            fired=bool(action_ops and action_ops.fired) and (self.recover_ops is None or self.recover_ops.fired),
            action_trace=tuple(action_ops.trace) if action_ops else (),
            recover_trace=tuple(self.recover_ops.trace) if self.recover_ops else (),
            action_outcome=self.action.outcome,
            action_error=self.action.error,
            before=self.trees.get("before", {}),
            after_action=self.trees.get("after_action", {}),
            after_meddle=self.trees.get("after_meddle", {}),
            meddled_paths=self.meddled,
            recovered=self.trees.get("recovered", {}),
            recovered_twice=self.trees.get("recovered_twice", {}),
            recovery_error=self.recovery_error,
            violations=tuple(self.violations),
        )


def _run(scenario: CrashScenario, workdir: Path, *, action_plan: Sequence[Fault] = (),
         recover_plan: Sequence[Fault] = (), meddle: bool = False, max_ops: int,
         baseline: FaultRun | None = None, expected_recover: tuple[FileOp, ...] | None = None) -> FaultRun:
    return _Run(scenario, workdir, action_plan=action_plan, recover_plan=recover_plan, meddle=meddle,
                max_ops=max_ops, baseline=baseline, expected_recover=expected_recover).execute()


def run_with_fault(scenario: CrashScenario, plan: Sequence[Fault], workdir: Path, *,
                   max_ops: int = 10_000) -> FaultRun:
    """Run the baseline, then one run under ``plan``; returns the baseline if it failed."""
    baseline = _run(scenario, workdir, max_ops=max_ops)
    if baseline.violations:
        return baseline
    return _run(scenario, workdir, action_plan=plan, meddle=True, max_ops=max_ops, baseline=baseline)


def explore_crashes(scenario: CrashScenario, workdir: Path, *, torn: bool = True, errors: bool = False,
                    recovery_crashes: bool = False, max_ops: int = 10_000) -> CrashReport:
    """Crash (and optionally fail) every mutating operation of the action in turn."""
    baseline = _run(scenario, workdir, max_ops=max_ops)
    runs = [baseline]
    if not baseline.violations:
        for op in baseline.action_trace:
            if not op.mutating:
                continue
            plans: list[Fault] = [Crash(op.index, "before"), Crash(op.index, "after")]
            if torn and op.kind == "write_bytes" and op.size:
                plans.append(Crash(op.index, "torn"))
            if errors:
                plans.append(Fail(op.index))
            for fault in plans:
                run = _run(scenario, workdir, action_plan=[fault], meddle=True, max_ops=max_ops, baseline=baseline)
                runs.append(run)
                if recovery_crashes and isinstance(fault, Crash) and run.recovery_error is None:
                    for recover_op in run.recover_trace:
                        if not recover_op.mutating:
                            continue
                        for timing in ("before", "after"):
                            runs.append(_run(
                                scenario, workdir, action_plan=[fault],
                                recover_plan=[Crash(recover_op.index, timing)], meddle=True,
                                max_ops=max_ops, baseline=baseline, expected_recover=run.recover_trace,
                            ))
    violations = tuple(violation for run in runs for violation in run.violations)
    return CrashReport(scenario.name, tuple(runs), violations)

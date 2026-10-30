"""Deterministic crash, I/O-error and external-edit injection for file-backed writers.

Test infrastructure only: production packages must not import it. See
``docs/TESTING.md`` for the fault model, its limits and the invariant catalog.
"""

from .explore import (
    CrashOutcome, CrashReport, CrashScenario, FaultRun, Invariant, Violation,
    atomic_outcome, completes_after, explore_crashes, idempotent_recovery, no_residue,
    preserves_meddle, run_with_fault, untouched,
)
from .faults import (
    Crash, Fail, FileOp, Interleave, OperationBudgetExceeded, RootedFileOps, SimulatedCrash,
    Target, validate_plan,
)
from .fileops import FileOps, OsFileOps, SandboxViolation, Tree, snapshot, wipe

__all__ = [
    "CrashOutcome", "CrashReport", "CrashScenario", "FaultRun", "Invariant", "Violation",
    "atomic_outcome", "completes_after", "explore_crashes", "idempotent_recovery", "no_residue",
    "preserves_meddle", "run_with_fault", "untouched",
    "Crash", "Fail", "FileOp", "Interleave", "OperationBudgetExceeded", "RootedFileOps",
    "SimulatedCrash", "Target", "validate_plan",
    "FileOps", "OsFileOps", "SandboxViolation", "Tree", "snapshot", "wipe",
]

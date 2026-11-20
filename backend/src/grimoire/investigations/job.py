"""Run one bounded investigation: ask the worker for an action, run it read-only, record it, repeat (082).

The loop is the whole authority of a job. It offers only the tools its spec names, counts every action
against the budget, checks cancellation before every action and again before running it, refuses any
finding whose evidence it did not itself read, and keeps what was found when it stops early. It writes
nothing and holds no lock between steps: each tool call is its own read, so a job explores without holding
the library, and a review later checks whether the evidence it cited is still current
(``investigationstore``). The clock and the cancellation token are passed in; this module reads neither
a clock nor the environment.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Protocol

from grimoire.core.identity import validate_segment
from grimoire.tracing import TraceBuilder

from .evidence import check_finding, detach, evidence_key
from .model import (
    AcceptedFinding,
    Finish,
    InvestigationReport,
    InvestigationSpec,
    Listed,
    ListRecords,
    Observation,
    ReadRecord,
    RecordRead,
    RejectedFinding,
    Remaining,
    Report,
    Step,
    ToolResult,
)


class Worker(Protocol):
    """Chooses the next action from the last observation (``None`` before the first action)."""

    def next_action(self, observation: Observation | None) -> object: ...


class Tools(Protocol):
    """The read-only toolbox a job runs actions through (``investigationstore.WorldToolbox``)."""

    def list(self) -> ToolResult: ...

    def read(self, kind: str, entity_id: str) -> ToolResult: ...


class Cancel(Protocol):
    @property
    def cancelled(self) -> bool: ...


class _Run:
    def __init__(self, spec: InvestigationSpec) -> None:
        self.spec = spec
        self.steps: list[Step] = []
        self.reads: dict[tuple[str, str], RecordRead] = {}
        self.listed: tuple[Listed, ...] = ()
        self.findings: list[AcceptedFinding] = []
        self.rejected: list[RejectedFinding] = []
        self.seen: set[tuple[str, frozenset[tuple[str, str, str | None]]]] = set()
        self.reads_used = 0

    def remaining(self) -> Remaining:
        budget = self.spec.budget
        return Remaining(max(0, budget.max_steps - len(self.steps)), max(0, budget.max_reads - self.reads_used),
                         max(0, budget.max_findings - len(self.findings)))

    def record(self, tool: str, target: str, outcome: str, detail: str = "") -> int:
        number = len(self.steps) + 1
        self.steps.append(Step(number, tool, target, outcome, detail))
        return number


def _cancelled(cancel: Cancel | None) -> bool:
    return cancel is not None and cancel.cancelled is True


def run_job(spec: InvestigationSpec, worker: Worker, tools: Tools, *, clock: Callable[[], float],
            cancel: Cancel | None = None) -> InvestigationReport:
    """Run ``worker`` against ``tools`` within ``spec``'s budget and return the reviewable report.

    The job ends ``finished`` when the worker finishes, ``cancelled`` when ``cancel`` is set, ``budget_exhausted``
    (``reason`` names the limit: ``steps``, ``reads``, ``findings`` or ``seconds``) at the first limit, and
    ``failed`` when the worker raises or the toolbox fails unexpectedly (``reason`` is ``worker_error`` or
    ``tool_error``; an exception's message is never kept, since it may carry anything). Every ending keeps the
    findings accepted so far."""
    if type(spec) is not InvestigationSpec:
        raise TypeError("run_job takes an InvestigationSpec")
    run = _Run(spec)
    builder = TraceBuilder(clock)
    started = clock()
    status, reason, note = "finished", "", ""
    observation: Observation | None = None
    with builder.span("investigation") as root:
        while True:
            if _cancelled(cancel):
                status, reason = "cancelled", "cancelled"
                break
            limit = _limit(run, spec, clock, started)
            if limit is not None:
                status, reason = "budget_exhausted", limit
                break
            try:
                action = worker.next_action(observation)
            except Exception as error:  # a worker is untrusted code: its failure ends the job, kept findings stay
                status, reason, note = "failed", "worker_error", type(error).__name__
                break
            if _cancelled(cancel):  # cancelled while the worker was choosing: run nothing more
                status, reason = "cancelled", "cancelled"
                break
            limit = _limit(run, spec, clock, started)  # time also runs out while the worker is choosing
            if limit is not None:
                status, reason = "budget_exhausted", limit
                break
            try:
                with builder.span("investigation.step"):
                    ended, observation = _act(run, action, tools)
            except _ToolFailure as failure:
                status, reason, note = "failed", "tool_error", failure.kind
                break
            except Exception as error:  # a malformed action from the worker (a forged value): end, keep findings
                status, reason, note = "failed", "worker_error", type(error).__name__
                break
            if ended is not None:
                status, reason, note = ended
                break
        root.count(steps=len(run.steps), reads=len(run.reads), findings=len(run.findings),
                   rejected=len(run.rejected))
    unread = tuple(sorted(item_key for item_key in ((item.kind, item.entity_id) for item in run.listed)
                          if item_key not in run.reads))
    return InvestigationReport(spec, status, reason, tuple(run.findings), tuple(run.rejected), tuple(run.steps),
                               run.reads, unread, builder.trace, note)


class _ToolFailure(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _limit(run: _Run, spec: InvestigationSpec, clock: Callable[[], float], started: float) -> str | None:
    if len(run.steps) >= spec.budget.max_steps:
        return "steps"
    if spec.budget.max_seconds is not None and clock() - started >= spec.budget.max_seconds:
        return "seconds"
    return None


def _call(tool: Callable[..., ToolResult], *args: str) -> ToolResult:
    try:
        result = tool(*args)
    except Exception as error:
        raise _ToolFailure(type(error).__name__) from None
    if type(result) is not ToolResult:
        raise _ToolFailure("not_a_tool_result")
    return result


def _act(run: _Run, action: object, tools: Tools) -> tuple[tuple[str, str, str] | None, Observation]:
    """Run one action. Returns ``(ending, observation)``; ``ending`` is ``(status, reason, note)`` or ``None``."""
    spec = run.spec
    finding_id: str | None = None
    if type(action) is ListRecords:
        if "list" not in spec.tools:
            result = ToolResult("tool_not_allowed", detail="list")
            run.record("list", "", result.outcome)
        else:
            result = _call(tools.list)
            if result.outcome == "ok":
                run.listed = copy.deepcopy(result.listed)  # the job's own copy: the worker gets the other
            run.record("list", "", result.outcome, result.detail)
    elif type(action) is ReadRecord:
        target = _target(action)
        if "read" not in spec.tools:
            result = ToolResult("tool_not_allowed", detail="read")
            run.record("read", target or "", result.outcome)
        elif target is None:
            result = ToolResult("invalid_target")
            run.record("read", "", result.outcome)
        elif run.reads_used >= spec.budget.max_reads:
            run.record("read", target, "rejected", "reads")
            return ("budget_exhausted", "reads", ""), _observe(run, action, ToolResult("rejected", detail="reads"))
        else:
            run.reads_used += 1
            result = _call(tools.read, action.kind, action.entity_id)
            if result.outcome == "ok":
                if type(result.read) is not RecordRead or result.read.record != (action.kind, action.entity_id):
                    raise _ToolFailure("wrong_record")
                # The evidence is the job's own copy, taken before the worker sees the result: re-running the
                # worker's copy's __init__ cannot change what citations are checked against.
                run.reads[result.read.record] = copy.deepcopy(result.read)
            run.record("read", target, result.outcome, result.detail)
    elif type(action) is Report:
        if len(run.findings) >= spec.budget.max_findings:
            run.record("report", "", "rejected", "findings")
            return ("budget_exhausted", "findings", ""), _observe(run, action, ToolResult("rejected",
                                                                                           detail="findings"))
        finding_id, result = _report(run, action.finding)
    elif type(action) is Finish:
        run.record("finish", "", "finished")
        return ("finished", "", action.note), _observe(run, action, ToolResult("finished"))
    else:
        result = ToolResult("unknown_action")
        run.record("unknown", "", result.outcome)
    return None, _observe(run, action, result, finding_id)


def _target(action: ReadRecord) -> str | None:
    if type(action.kind) is not str or type(action.entity_id) is not str:
        return None
    try:
        validate_segment(action.kind, "kind")
        validate_segment(action.entity_id, "entity_id")
    except ValueError:
        return None
    return f"{action.kind}/{action.entity_id}"


def _report(run: _Run, finding: object) -> tuple[str | None, ToolResult]:
    number = len(run.steps) + 1
    try:
        finding = detach(finding)  # check and keep a copy the worker cannot reach, never its own object
    except Exception:  # a forged or re-initialized value: whatever it raises, it is not a finding
        run.rejected.append(RejectedFinding(number, None, "not_a_finding"))
        run.record("report", "", "rejected", "not_a_finding")
        return None, ToolResult("rejected", detail="not_a_finding")
    problem = check_finding(finding, run.reads)
    if problem is None and evidence_key(finding) in run.seen:
        problem = ("duplicate_finding", "the same category over the same evidence was already reported")
    if problem is not None:
        reason, detail = problem
        run.rejected.append(RejectedFinding(number, finding, reason, detail))
        run.record("report", "", "rejected", reason)
        return None, ToolResult("rejected", detail=reason)
    finding_id = f"f{len(run.findings) + 1}"
    run.seen.add(evidence_key(finding))
    run.findings.append(AcceptedFinding(finding_id, finding))
    run.record("report", finding_id, "accepted")
    return finding_id, ToolResult("accepted", detail=finding_id)


def _observe(run: _Run, action: object, result: ToolResult, finding_id: str | None = None) -> Observation:
    return Observation(len(run.steps), action, result, run.remaining(), finding_id)


__all__ = ["Cancel", "Tools", "Worker", "run_job"]

"""Values of one bounded, read-only investigation job (082): its goal, budget, actions, evidence and report.

A job investigates exactly one source (normally a world). A *worker* (the built-in consistency auditor, an
external script or, later, a model) chooses one action at a time; the job runs it through a read-only
toolbox, records the step and hands the worker what it observed. There is no write action in the
vocabulary: an investigation can read, report a finding and finish. Turning a finding into a change is a
separate, reviewed World Change Set (043) that a human drafts and approves.

Everything a worker says is untrusted. A finding's summary is its claim; what makes it reviewable is the
citations, which the job checks against the exact records it read in this run (``evidence.check``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from grimoire.core.identity import validate_segment, validate_source_id
from grimoire.core.json_value import JsonValue

_JOB_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,47}")
_CATEGORY = re.compile(r"[a-z][a-z0-9_]{0,47}")
#: The tools a job may offer. ``list`` reads the source's manifest and revision listing; ``read`` reads one
#: entity's head revision. Neither writes, and no other tool exists in this slice.
TOOLS: frozenset[str] = frozenset({"list", "read"})
MAX_GOAL = 2000
MAX_SUMMARY = 500
MAX_QUOTE = 200
MAX_CITATIONS = 16
MAX_FIELD = 512

Status = Literal["finished", "cancelled", "budget_exhausted", "failed"]
STATUSES: frozenset[str] = frozenset({"finished", "cancelled", "budget_exhausted", "failed"})


class InvestigationError(ValueError):
    """A job or value that cannot be built as given; ``reason`` is a short stable code."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(f"{reason}: {message}" if message else reason)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, str(self).partition(": ")[2]))


def _count(value: object, label: str, *, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise InvestigationError("invalid_field", f"{label} must be an int >= {minimum}")
    return value


def _text(value: object, label: str, limit: int, *, blank: bool = False) -> str:
    if type(value) is not str or len(value) > limit or (not blank and not value.strip()):
        raise InvestigationError("invalid_field", f"{label} must be {'' if blank else 'nonblank '}text of at "
                                                  f"most {limit} characters")
    return value


@dataclass(frozen=True, slots=True)
class Budget:
    """How far one job may go. Every action the worker returns is one step, whatever it does; a ``read`` that
    reaches the store counts against ``max_reads``; an accepted finding against ``max_findings``. ``max_seconds``
    is measured on the clock the caller passes in (``None``: no time limit). The first limit reached ends the
    job as ``budget_exhausted``, keeping what it found."""

    max_steps: int = 400
    max_reads: int = 200
    max_findings: int = 50
    max_seconds: float | None = None

    def __post_init__(self) -> None:
        _count(self.max_steps, "max_steps")
        _count(self.max_reads, "max_reads", minimum=0)
        _count(self.max_findings, "max_findings", minimum=0)
        if self.max_seconds is not None and (type(self.max_seconds) not in (int, float)
                                             or not self.max_seconds > 0 or self.max_seconds != self.max_seconds):
            raise InvestigationError("invalid_field", "max_seconds must be a positive number or None")


@dataclass(frozen=True, slots=True)
class InvestigationSpec:
    """What a human asked for: one job over one source, with a goal, a budget and the tools it may use."""

    job_id: str
    source: str
    goal: str
    budget: Budget = field(default_factory=Budget)
    tools: frozenset[str] = TOOLS

    def __post_init__(self) -> None:
        if type(self.job_id) is not str or _JOB_ID.fullmatch(self.job_id) is None:
            raise InvestigationError("invalid_field", "job_id must match [a-z0-9][a-z0-9._-]{0,47}")
        try:
            validate_source_id(self.source)
        except ValueError as error:
            raise InvestigationError("invalid_field", str(error)) from None
        _text(self.goal, "goal", MAX_GOAL)
        if type(self.budget) is not Budget:
            raise InvestigationError("invalid_field", "budget must be a Budget")
        if type(self.tools) is not frozenset or not self.tools or not self.tools <= TOOLS:
            raise InvestigationError("invalid_field", f"tools must be a nonempty subset of {sorted(TOOLS)}")


# -- actions: what a worker may ask for --------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ListRecords:
    """Tool ``list``: every entity of the job's source with its revisions and resolved head."""


@dataclass(frozen=True, slots=True)
class ReadRecord:
    """Tool ``read``: one entity's head revision (the declared head, or its only revision)."""

    kind: str
    entity_id: str


@dataclass(frozen=True, slots=True)
class Citation:
    """One piece of evidence: a record this job read, at the exact revision and etag it read.

    ``field`` is an RFC 6901 JSON pointer into ``{"name": <display name>, "payload": <payload>}`` (``/name``,
    ``/payload/sections/history``), or ``None`` to cite the whole record, as a finding of absence does ("the
    guild's record never names Wren" holds for that exact revision). ``quote``, with a field, is text that
    must appear verbatim in that field's string value."""

    kind: str
    entity_id: str
    revision: str
    etag: str
    field: str | None = None
    quote: str | None = None

    def __post_init__(self) -> None:
        try:
            for value, label in ((self.kind, "kind"), (self.entity_id, "entity_id"), (self.revision, "revision")):
                if type(value) is not str:  # a str subclass could compare equal to a revision never read
                    raise ValueError(f"{label} must be a str")
                validate_segment(value, label)
        except ValueError as error:
            raise InvestigationError("invalid_field", str(error)) from None
        _text(self.etag, "etag", 80)
        if self.field is not None:
            _text(self.field, "field", MAX_FIELD)
            if not self.field.startswith("/"):
                raise InvestigationError("invalid_field", "field is a JSON pointer starting with /")
        if self.quote is not None:
            _text(self.quote, "quote", MAX_QUOTE)
            if self.field is None:
                raise InvestigationError("invalid_field", "a quote needs the field it is quoted from")

    @property
    def record(self) -> tuple[str, str]:
        return (self.kind, self.entity_id)


@dataclass(frozen=True, slots=True)
class Finding:
    """A worker's claim and its evidence. ``category`` is a short code (``possible_duplicate``,
    ``missing_link``, ``dangling_reference`` ...); the job does not interpret it."""

    category: str
    summary: str
    citations: tuple[Citation, ...]

    def __post_init__(self) -> None:
        if type(self.category) is not str or _CATEGORY.fullmatch(self.category) is None:
            raise InvestigationError("invalid_field", "category must match [a-z][a-z0-9_]{0,47}")
        _text(self.summary, "summary", MAX_SUMMARY)
        if (type(self.citations) is not tuple or len(self.citations) > MAX_CITATIONS
                or not all(type(item) is Citation for item in self.citations)):
            raise InvestigationError("invalid_field", f"citations must be a tuple of at most {MAX_CITATIONS} "
                                                      "Citation values")


@dataclass(frozen=True, slots=True)
class Report:
    """Report one finding. It is accepted only when every citation is evidence this job read."""

    finding: Finding


@dataclass(frozen=True, slots=True)
class Finish:
    """The worker is done."""

    note: str = ""

    def __post_init__(self) -> None:
        _text(self.note, "note", MAX_SUMMARY, blank=True)


Action = ListRecords | ReadRecord | Report | Finish
ACTIONS = (ListRecords, ReadRecord, Report, Finish)


# -- what the toolbox returns ------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Listed:
    """One entity of the listing: its revision files and the head ``read`` would resolve (``None`` when it
    has several revisions and no declared head, or the declared head has no file)."""

    kind: str
    entity_id: str
    revisions: tuple[str, ...]
    head: str | None


@dataclass(frozen=True, slots=True)
class RecordRead:
    """One revision as read: exact revision and etag, display name and frozen payload."""

    kind: str
    entity_id: str
    revision: str
    etag: str
    name: str | None
    payload: JsonValue

    @property
    def record(self) -> tuple[str, str]:
        return (self.kind, self.entity_id)

    def cite(self, field: str | None = None, quote: str | None = None) -> Citation:
        return Citation(self.kind, self.entity_id, self.revision, self.etag, field, quote)


#: A tool's outcome. ``ok`` carried data; the others are refusals that read nothing or nothing usable.
Outcome = Literal["ok", "denied", "missing", "no_head", "unreadable", "missing_source", "excluded_source",
                  "tool_not_allowed", "invalid_target", "unknown_action", "accepted", "rejected", "finished"]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What the toolbox answered: ``listed`` for ``list``, ``read`` for ``read``, ``detail`` for a refusal."""

    outcome: Outcome
    listed: tuple[Listed, ...] = ()
    read: RecordRead | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Remaining:
    steps: int
    reads: int
    findings: int


@dataclass(frozen=True, slots=True)
class Observation:
    """What the worker sees after its action: the result, the finding id or rejection, and what is left."""

    step: int
    action: object
    result: ToolResult
    remaining: Remaining
    finding_id: str | None = None


# -- the job's record --------------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Step:
    """One action as the job recorded it: never the worker's free text, only what was asked and the outcome."""

    number: int
    tool: str  # "list", "read", "report", "finish" or "unknown"
    target: str  # "<kind>/<entity_id>" for a read, a finding id or "" otherwise
    outcome: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class AcceptedFinding:
    finding_id: str
    finding: Finding


@dataclass(frozen=True, slots=True)
class RejectedFinding:
    """A finding the job refused, and why (``unread_evidence``, ``quote_not_found`` ...). Shown to the reviewer
    as refused, never as a finding."""

    step: int
    finding: Finding | None
    reason: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class InvestigationReport:
    """The reviewable output of one run. ``reads`` is every record the job read, keyed by ``(kind, entity_id)``
    at the revision and etag it was read; ``unread`` lists the entities the listing named that were never
    read, so a reviewer sees what the audit did not cover. ``trace`` is the 048 timing trace."""

    spec: InvestigationSpec
    status: Status
    reason: str
    findings: tuple[AcceptedFinding, ...]
    rejected: tuple[RejectedFinding, ...]
    steps: tuple[Step, ...]
    reads: Mapping[tuple[str, str], RecordRead]
    unread: tuple[tuple[str, str], ...]
    trace: object = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise InvestigationError("invalid_field", f"unknown status {self.status!r}")
        object.__setattr__(self, "reads", MappingProxyType(dict(self.reads)))

    def finding(self, finding_id: str) -> Finding:
        for item in self.findings:
            if item.finding_id == finding_id:
                return item.finding
        raise KeyError(finding_id)

    def describe(self) -> tuple[str, ...]:
        """Plain lines a reviewer reads: the job, its end, each finding with its evidence, and its coverage."""
        lines = [f"investigation {self.spec.job_id} of {self.spec.source}: {self.status}"
                 + (f" ({self.reason})" if self.reason else ""),
                 f"goal: {self.spec.goal}",
                 f"{len(self.steps)} steps, {len(self.reads)} records read, {len(self.unread)} listed but unread"]
        for item in self.findings:
            lines.append(f"{item.finding_id} [{item.finding.category}] {item.finding.summary}")
            for citation in item.finding.citations:
                where = f" {citation.field}" if citation.field else ""
                said = f' "{citation.quote}"' if citation.quote else ""
                lines.append(f"  cites {citation.kind}/{citation.entity_id}@{citation.revision}{where}{said}")
        for item in self.rejected:
            lines.append(f"refused at step {item.step}: {item.reason}")
        return tuple(lines)


__all__ = [
    "ACTIONS",
    "STATUSES",
    "TOOLS",
    "AcceptedFinding",
    "Action",
    "Budget",
    "Citation",
    "Finding",
    "Finish",
    "InvestigationError",
    "InvestigationReport",
    "InvestigationSpec",
    "ListRecords",
    "Listed",
    "Observation",
    "Outcome",
    "ReadRecord",
    "RecordRead",
    "RejectedFinding",
    "Remaining",
    "Report",
    "Status",
    "Step",
    "ToolResult",
]

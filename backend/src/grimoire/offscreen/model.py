"""Faction plans that progress in campaign time, and the receipts that record each skip (076).

A ``FactionPlan`` is a director-authored sequence of offscreen steps for one institution, adopted
under one recorded 057 leader decision. Each step takes an exact number of campaign minutes. A
``Skip`` names one explicit 055 calendar advance; applying it flags every pending step due by the
end of that advance, oldest first, within a bounded deterministic relevance budget. Nothing here
delivers knowledge, commits an accepted effect or calls a model: a flagged step is a director-facing
development, like a crossed calendar deadline.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import islice

from grimoire.calendars import Moment
from grimoire.core.identity import InstanceRef, validate_segment

TEXT_CHARS = 400
MAX_PLANS = 32
MAX_STEPS = 16
MAX_BUDGET = MAX_PLANS
PRIORITY_MIN, PRIORITY_MAX = 1, 100
#: One step is at most about a century of campaign minutes; a plan cannot run past the calendar range.
MAX_STEP_MINUTES = 100 * 366 * 1440
OUTCOMES = ("met", "missed")


class OffscreenError(ValueError):
    """A refused plan or skip. ``reason`` is machine-readable; messages cite ids, never authored text."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(f"{reason}: {message}" if message else reason)
        self.reason = reason
        self.message = message

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, self.message))


def identifier(value: object, label: str) -> str:
    if type(value) is not str or len(value) > 128:
        raise OffscreenError("invalid_field", f"{label} must be an id")
    try:
        validate_segment(value, label)
    except (ValueError, TypeError):
        raise OffscreenError("invalid_field", f"{label} must be an id") from None
    return value


def text(value: object, label: str) -> str:
    if (type(value) is not str or not value.strip() or len(value) > TEXT_CHARS
            or any(ord(c) < 32 or c in "\x7f  " for c in value)):
        raise OffscreenError("invalid_field", f"{label} must be one line of at most {TEXT_CHARS} characters")
    return value


def integer(value: object, low: int, high: int, label: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise OffscreenError("invalid_field", f"{label} must be an integer in {low}..{high}")
    return value


def bounded(value: object, maximum: int, label: str) -> tuple:
    if isinstance(value, (str, bytes, Mapping)):
        raise OffscreenError("invalid_field", f"{label} must be a sequence")
    try:
        result = tuple(islice(iter(value), maximum + 1))
    except TypeError:
        raise OffscreenError("invalid_field", f"{label} must be a sequence") from None
    if len(result) > maximum:
        raise OffscreenError("limit", f"at most {maximum} {label}")
    return result


def moment(value: object, label: str) -> Moment:
    if type(value) is not Moment:
        raise OffscreenError("invalid_field", f"{label} must be a calendar Moment")
    return value


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One offscreen step: what it achieves (shown to the director) and how long it takes."""

    step_id: str
    label: str
    minutes: int

    def __post_init__(self) -> None:
        identifier(self.step_id, "step id")
        text(self.label, "step label")
        integer(self.minutes, 1, MAX_STEP_MINUTES, "step minutes")


@dataclass(frozen=True, slots=True)
class FactionPlan:
    """An institution's adopted course, as one ordered chain of exact-duration steps.

    ``decision_id`` and ``choice`` name the 057 ``CollectiveDecision`` that authorized it; adoption
    checks that receipt. ``deadline_id`` optionally names the 055 calendar deadline the plan races.
    ``priority`` orders plans when a skip's relevance budget cannot cover every due plan.
    """

    plan_id: str
    group: InstanceRef
    decision_id: str
    choice: str
    goal: str
    steps: tuple[PlanStep, ...]
    priority: int = 50
    deadline_id: str | None = None

    def __post_init__(self) -> None:
        identifier(self.plan_id, "plan id")
        if type(self.group) is not InstanceRef or self.group.kind != "group":
            raise OffscreenError("identity", "a plan belongs to one campaign group")
        identifier(self.decision_id, "decision id")
        text(self.choice, "choice")
        text(self.goal, "goal")
        steps = bounded(self.steps, MAX_STEPS, "steps")
        if not steps or not all(type(s) is PlanStep for s in steps):
            raise OffscreenError("invalid_field", "a plan has one or more PlanStep values")
        if len({s.step_id for s in steps}) != len(steps):
            raise OffscreenError("duplicate_step", "step ids are unique within a plan")
        integer(self.priority, PRIORITY_MIN, PRIORITY_MAX, "priority")
        if self.deadline_id is not None:
            identifier(self.deadline_id, "deadline id")
        object.__setattr__(self, "steps", steps)


@dataclass(frozen=True, slots=True)
class Adopt:
    """Start a plan at the current campaign time. Its id is campaign-local and never reused."""

    operation_id: str
    plan: FactionPlan

    def __post_init__(self) -> None:
        identifier(self.operation_id, "operation id")
        if type(self.plan) is not FactionPlan:
            raise OffscreenError("invalid_field", "adopt needs a FactionPlan")


@dataclass(frozen=True, slots=True)
class Skip:
    """Progress plans across one calendar advance. ``operation_id`` is that advance's own id.

    ``budget`` is how many plans this skip may advance; plans beyond it are deferred, not dropped.
    """

    operation_id: str
    budget: int

    def __post_init__(self) -> None:
        identifier(self.operation_id, "calendar advance id")
        integer(self.budget, 1, MAX_BUDGET, "budget")


Operation = Adopt | Skip


@dataclass(frozen=True, slots=True)
class Development:
    """One step that completed offscreen: hidden, director-facing, delivered to nobody."""

    plan_id: str
    step_id: str
    due: Moment


@dataclass(frozen=True, slots=True)
class DeadlineOutcome:
    """A plan's calendar deadline crossed by this skip: ``met`` if its last step was due by then."""

    plan_id: str
    deadline_id: str
    outcome: str


@dataclass(frozen=True, slots=True)
class Adopted:
    operation: Adopt
    at: Moment


@dataclass(frozen=True, slots=True)
class Advanced:
    operation: Skip
    before: Moment
    after: Moment
    developments: tuple[Development, ...] = ()
    deferred: tuple[str, ...] = ()
    deadlines: tuple[DeadlineOutcome, ...] = ()


Receipt = Adopted | Advanced


@dataclass(frozen=True, slots=True)
class PlanProgress:
    """A plan as of the state's time: when it started, each step's due moment and how many are flagged."""

    plan: FactionPlan
    started: Moment
    dues: tuple[Moment, ...]
    completed: int = 0

    @property
    def complete(self) -> bool:
        return self.completed == len(self.plan.steps)

    @property
    def pending(self) -> tuple[tuple[PlanStep, Moment], ...]:
        return tuple(zip(self.plan.steps[self.completed:], self.dues[self.completed:]))


@dataclass(frozen=True, slots=True)
class OffscreenState:
    """One campaign's adopted plans, the campaign time they have been progressed to, and every receipt."""

    campaign_id: str
    as_of: Moment
    plans: tuple[PlanProgress, ...]
    receipts: tuple[Receipt, ...]

    def adoption(self, operation_id: str) -> Adopted | None:
        return next((r for r in self.receipts if type(r) is Adopted and r.operation.operation_id == operation_id), None)

    def skip(self, operation_id: str) -> Advanced | None:
        """The receipt of a calendar advance's skip. Skip ids are calendar ids, a namespace of their own."""
        return next((r for r in self.receipts if type(r) is Advanced and r.operation.operation_id == operation_id),
                    None)

    def receipt(self, operation: Adopt | Skip) -> Receipt | None:
        """The retained receipt for an operation's id, looked up among receipts of its own kind."""
        if type(operation) is Adopt:
            return self.adoption(operation.operation_id)
        if type(operation) is Skip:
            return self.skip(operation.operation_id)
        raise OffscreenError("invalid_field", "receipt lookup needs an Adopt or Skip operation")

    def progress(self, plan_id: str) -> PlanProgress | None:
        return next((p for p in self.plans if p.plan.plan_id == plan_id), None)

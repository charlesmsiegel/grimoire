"""Pure immutable campaign time, explicit durations, one-shot clocks and retained receipts (055)."""
from __future__ import annotations

from dataclasses import dataclass, replace

from grimoire.core.identity import validate_segment

from .providers import MAX_DAY, CalendarDefinition, CalendarError, integer, text

MINUTES_PER_DAY = 1440


def identifier(value: object, label: str) -> None:
    text(value, label, maximum=128)
    try:
        validate_segment(value, label)
    except ValueError as error:
        raise CalendarError(str(error)) from None


@dataclass(frozen=True, order=True)
class Moment:
    fixed_day: int
    minute: int = 0

    def __post_init__(self) -> None:
        integer(self.fixed_day, 1, MAX_DAY, "fixed day")
        integer(self.minute, 0, 1439, "minute")

    def advance(self, minutes: int) -> Moment:
        integer(minutes, 0, MAX_DAY * MINUTES_PER_DAY, "duration minutes")
        days, minute = divmod(self.minute + minutes, MINUTES_PER_DAY)
        return Moment(self.fixed_day + days, minute)


@dataclass(frozen=True)
class Duration:
    minimum: int
    maximum: int | None = None
    selected: int | None = None
    note: str = ""

    def __post_init__(self) -> None:
        integer(self.minimum, 0, MAX_DAY * MINUTES_PER_DAY, "minimum")
        maximum = self.minimum if self.maximum is None else self.maximum
        integer(maximum, self.minimum, MAX_DAY * MINUTES_PER_DAY, "maximum")
        object.__setattr__(self, "maximum", maximum)
        text(self.note, "duration note", empty=self.minimum == maximum)
        selected = self.minimum if self.selected is None and maximum == self.minimum else self.selected
        if selected is not None:
            integer(selected, self.minimum, maximum, "selected duration")
        object.__setattr__(self, "selected", selected)

    @property
    def exact_minutes(self) -> int:
        if self.selected is None:
            raise CalendarError("an uncertain duration needs an explicitly selected duration", "unresolved_duration")
        return self.selected


@dataclass(frozen=True)
class Deadline:
    deadline_id: str
    due: Moment
    label: str
    kind: str = "deadline"
    triggered_by: str | None = None

    def __post_init__(self) -> None:
        identifier(self.deadline_id, "deadline id")
        if type(self.due) is not Moment:
            raise CalendarError("deadline due must be a Moment")
        text(self.label, "deadline label")
        if type(self.kind) is not str or self.kind not in {"deadline", "reminder", "trigger"}:
            raise CalendarError("unknown deadline kind")
        if self.triggered_by is not None:
            identifier(self.triggered_by, "triggering operation")


@dataclass(frozen=True)
class Initialize:
    operation_id: str
    calendar: CalendarDefinition
    at: Moment

    def __post_init__(self) -> None:
        identifier(self.operation_id, "operation id")
        if type(self.calendar) is not CalendarDefinition or type(self.at) is not Moment:
            raise CalendarError("initialization needs a calendar and moment")
        self.calendar.format(self.at.fixed_day)


@dataclass(frozen=True)
class Schedule:
    operation_id: str
    deadline: Deadline

    def __post_init__(self) -> None:
        identifier(self.operation_id, "operation id")
        if type(self.deadline) is not Deadline or self.deadline.triggered_by is not None:
            raise CalendarError("schedule needs an untriggered deadline")


@dataclass(frozen=True)
class Advance:
    operation_id: str
    duration: Duration

    def __post_init__(self) -> None:
        identifier(self.operation_id, "operation id")
        if type(self.duration) is not Duration:
            raise CalendarError("advance needs a Duration")


Operation = Initialize | Schedule | Advance


@dataclass(frozen=True)
class Receipt:
    operation: Operation
    before: Moment | None
    after: Moment
    triggered: tuple[str, ...] = ()


@dataclass(frozen=True)
class CalendarState:
    campaign_id: str
    calendar: CalendarDefinition
    now: Moment
    deadlines: tuple[Deadline, ...]
    receipts: tuple[Receipt, ...]

    def receipt(self, operation_id: str) -> Receipt | None:
        return next((r for r in self.receipts if r.operation.operation_id == operation_id), None)


def apply(campaign_id: str, state: CalendarState | None, operation: Operation) -> tuple[CalendarState, Receipt, bool]:
    """Plan one explicit operation, retaining its original receipt on retries. Never invokes a trigger."""
    identifier(campaign_id, "campaign id")
    if type(operation) not in (Initialize, Schedule, Advance):
        raise CalendarError("unknown calendar operation")
    if state is not None:
        if state.campaign_id != campaign_id:
            raise CalendarError("calendar belongs to another campaign", "campaign_mismatch")
        old = state.receipt(operation.operation_id)
        if old is not None:
            if old.operation != operation:
                raise CalendarError("operation id already belongs to a different request", "operation_mismatch")
            return state, old, True
    if isinstance(operation, Initialize):
        if state is not None:
            raise CalendarError("campaign calendar already initialized", "already_initialized")
        receipt = Receipt(operation, None, operation.at)
        return CalendarState(campaign_id, operation.calendar, operation.at, (), (receipt,)), receipt, False
    if state is None:
        raise CalendarError("campaign calendar is not initialized", "uninitialized")
    now, deadlines, triggered = state.now, state.deadlines, ()
    if isinstance(operation, Schedule):
        deadline = operation.deadline
        state.calendar.format(deadline.due.fixed_day)
        if deadline.due <= now:
            raise CalendarError("deadline must be in the future", "past_deadline")
        if any(d.deadline_id == deadline.deadline_id for d in deadlines):
            raise CalendarError("deadline id already exists", "duplicate_deadline")
        deadlines += (deadline,)
    else:
        now = now.advance(operation.duration.exact_minutes)
        state.calendar.format(now.fixed_day)
        triggered = tuple(d.deadline_id for d in deadlines if d.triggered_by is None and state.now < d.due <= now)
        deadlines = tuple(replace(d, triggered_by=operation.operation_id) if d.deadline_id in triggered else d
                          for d in deadlines)
    receipt = Receipt(operation, state.now, now, triggered)
    return replace(state, now=now, deadlines=deadlines, receipts=state.receipts + (receipt,)), receipt, False

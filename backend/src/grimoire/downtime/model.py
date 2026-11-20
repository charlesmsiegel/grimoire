"""Pure long activities: a bounded intent, its effort and its progress, spent on campaign time (074).

An activity is one actor's declared intent (study a language, recover from a wound, research a
lead) with an explicit effort requirement. Pursuing it is one explicit operation that spends an
exact span of campaign time, credits up to that much effort and, when the requirement is met,
completes the activity with an optional fictional outcome. Every operation keeps its receipt, so a
retried request, even after a crash and reopen, returns its original receipt instead of adding
progress or spending time twice. The calendar side of a pursuit is a 055 ``Advance`` with the same
operation id, planned here and stored with the activity in the same record (``downtimestore``).

This first slice resolves activities in compressed narration. It does not track food or supplies,
pause for interruptions, run several activities in one stretch or consult a ruleset's study tables:
a ruleset or the director may decide the effort requirement before an activity begins.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from grimoire.calendars import Advance, CalendarError, CalendarState, Duration, Moment, Receipt
from grimoire.calendars import apply as apply_calendar
from grimoire.calendars.model import MINUTES_PER_DAY, identifier
from grimoire.calendars.providers import MAX_DAY, integer, text
from grimoire.core.identity import InstanceRef

#: The kinds of long activity this slice can record. The kind is descriptive: every kind resolves the
#: same way here, and later rules (seasonal study, crafting) may treat kinds differently.
KINDS = frozenset({"study", "research", "training", "recovery", "crafting", "travel", "other"})
ACTIVE, COMPLETE, ABANDONED = "active", "complete", "abandoned"
MAX_EFFORT = MAX_DAY * MINUTES_PER_DAY


class DowntimeError(ValueError):
    """A refused downtime operation, with a stable machine-readable ``reason``."""

    def __init__(self, message: str, reason: str = "invalid_field") -> None:
        super().__init__(message)
        self.reason = reason


def _checked(call):
    """Re-raise a calendar refusal as a downtime refusal with the same reason and message."""
    try:
        return call()
    except CalendarError as error:
        raise DowntimeError(str(error), error.reason) from None


@dataclass(frozen=True)
class Activity:
    """One actor's bounded intent. ``required_minutes`` is effort, not elapsed time: travel may leave
    two hours a day for study, so twenty days of study effort can take longer than twenty days."""
    activity_id: str
    actor: InstanceRef
    kind: str
    intent: str
    required_minutes: int

    def __post_init__(self) -> None:
        _checked(lambda: identifier(self.activity_id, "activity id"))
        if type(self.actor) is not InstanceRef:
            raise DowntimeError("an activity's actor must be a campaign instance reference")
        if type(self.kind) is not str or self.kind not in KINDS:
            raise DowntimeError(f"activity kind must be one of {sorted(KINDS)}")
        _checked(lambda: text(self.intent, "activity intent"))
        _checked(lambda: integer(self.required_minutes, 1, MAX_EFFORT, "required effort minutes"))


@dataclass(frozen=True)
class Begin:
    operation_id: str
    activity: Activity

    def __post_init__(self) -> None:
        _checked(lambda: identifier(self.operation_id, "operation id"))
        if type(self.activity) is not Activity:
            raise DowntimeError("begin needs an Activity")


@dataclass(frozen=True)
class Pursue:
    """Spend ``elapsed`` campaign time on one activity, crediting ``effort_minutes`` of it (default: all).

    ``elapsed`` may be an estimate with bounds, but it must have an explicitly selected exact value
    before it can be committed; no midpoint is guessed. ``note`` is a fictional summary of the stretch.
    """
    operation_id: str
    activity_id: str
    elapsed: Duration
    effort_minutes: int | None = None
    note: str = ""

    def __post_init__(self) -> None:
        _checked(lambda: identifier(self.operation_id, "operation id"))
        _checked(lambda: identifier(self.activity_id, "activity id"))
        if type(self.elapsed) is not Duration:
            raise DowntimeError("pursue needs an elapsed Duration")
        if self.effort_minutes is not None:
            _checked(lambda: integer(self.effort_minutes, 1, MAX_EFFORT, "effort minutes"))
            if self.effort_minutes > self.elapsed.maximum:
                raise DowntimeError("effort cannot exceed the time that passes", "effort_exceeds_elapsed")
        _checked(lambda: text(self.note, "pursuit note", empty=True))


@dataclass(frozen=True)
class Abandon:
    """Stop an activity, keeping its progress. Spends no time."""
    operation_id: str
    activity_id: str
    note: str = ""

    def __post_init__(self) -> None:
        _checked(lambda: identifier(self.operation_id, "operation id"))
        _checked(lambda: identifier(self.activity_id, "activity id"))
        _checked(lambda: text(self.note, "abandon note", empty=True))


Operation = Begin | Pursue | Abandon


@dataclass(frozen=True)
class Progress:
    """An activity and what has been achieved on it. ``settled_by`` names the completing/abandoning
    operation; ``outcome`` is that operation's note; ``settled_at`` is the campaign time it happened."""
    activity: Activity
    progress_minutes: int = 0
    status: str = ACTIVE
    settled_by: str | None = None
    settled_at: Moment | None = None
    outcome: str = ""

    @property
    def remaining_minutes(self) -> int:
        return self.activity.required_minutes - self.progress_minutes


@dataclass(frozen=True)
class DowntimeReceipt:
    """What one downtime operation did. A pursuit also names its calendar receipt's span.

    ``credited`` is the effort actually added (capped by what remained); ``completed`` is true only for
    the pursuit that met the requirement. ``triggered`` is the deadlines the time span crossed.
    """
    operation: Operation
    credited: int = 0
    completed: bool = False
    before: Moment | None = None
    after: Moment | None = None
    triggered: tuple[str, ...] = ()



@dataclass(frozen=True)
class DowntimeState:
    campaign_id: str
    activities: tuple[Progress, ...]
    receipts: tuple[DowntimeReceipt, ...]

    def receipt(self, operation_id: str) -> DowntimeReceipt | None:
        return next((r for r in self.receipts if r.operation.operation_id == operation_id), None)

    def activity(self, activity_id: str) -> Progress | None:
        return next((p for p in self.activities if p.activity.activity_id == activity_id), None)


def empty(campaign_id: str) -> DowntimeState:
    _checked(lambda: identifier(campaign_id, "campaign id"))
    return DowntimeState(campaign_id, (), ())


def apply(state: DowntimeState, calendar: CalendarState | None, operation: Operation
          ) -> tuple[DowntimeState, CalendarState | None, DowntimeReceipt, bool]:
    """Plan one explicit operation against the campaign's downtime and calendar state.

    Returns the new downtime state, the new calendar state (changed only by a pursuit), the receipt
    and whether this is a retry. A retry with the identical request returns the retained receipt and
    both states unchanged; a different request reusing an operation id is refused. Never invokes a
    deadline's trigger: crossed deadlines are flagged by the calendar and reported on the receipt.
    """
    if type(state) is not DowntimeState:
        raise DowntimeError("downtime state is required")
    if calendar is not None and calendar.campaign_id != state.campaign_id:
        raise DowntimeError("calendar belongs to another campaign", "campaign_mismatch")
    if type(operation) not in (Begin, Pursue, Abandon):
        raise DowntimeError("unknown downtime operation")
    old = state.receipt(operation.operation_id)
    if old is not None:
        if old.operation != operation:
            raise DowntimeError("operation id already belongs to a different request", "operation_mismatch")
        return state, calendar, old, True
    if not isinstance(operation, Pursue):
        at = None if calendar is None else calendar.now
        new_state, receipt = record(state, operation, at=at)
        return new_state, calendar, receipt, False
    _open_activity(state, operation.activity_id)
    if calendar is None:
        raise DowntimeError("campaign calendar is not initialized", "uninitialized")
    if calendar.receipt(operation.operation_id) is not None:
        # The calendar's own retry rule would report this id as already applied: crediting progress
        # for it would record study that spent no new time.
        raise DowntimeError("operation id already belongs to a calendar operation", "operation_mismatch")
    _pursuit_effort(operation, _checked(lambda: operation.elapsed.exact_minutes))
    calendar_after, calendar_receipt, _ = _checked(
        lambda: apply_calendar(state.campaign_id, calendar, Advance(operation.operation_id, operation.elapsed)))
    new_state, receipt = record(state, operation, calendar_receipt=calendar_receipt)
    return new_state, calendar_after, receipt, False


def record(state: DowntimeState, operation: Operation, *, calendar_receipt: Receipt | None = None,
           at: Moment | None = None) -> tuple[DowntimeState, DowntimeReceipt]:
    """Record one new operation, given the calendar receipt a pursuit advanced time with.

    ``apply`` plans that receipt; replay (the codec) supplies the retained one, so a stored history is
    proved by the same rules that wrote it. ``at`` is the campaign time a begin/abandon happened.
    """
    if state.receipt(operation.operation_id) is not None:
        raise DowntimeError("operation id already exists", "operation_mismatch")
    if isinstance(operation, Begin):
        activity = operation.activity
        if activity.actor.campaign_id != state.campaign_id:
            raise DowntimeError("the actor belongs to another campaign", "campaign_mismatch")
        if state.activity(activity.activity_id) is not None:
            raise DowntimeError("activity id already exists", "duplicate_activity")
        receipt = DowntimeReceipt(operation, before=at, after=at)
        return (replace(state, activities=state.activities + (Progress(activity),),
                        receipts=state.receipts + (receipt,)), receipt)
    current = _open_activity(state, operation.activity_id)
    if isinstance(operation, Abandon):
        settled = replace(current, status=ABANDONED, settled_by=operation.operation_id, settled_at=at,
                          outcome=operation.note)
        receipt = DowntimeReceipt(operation, before=at, after=at)
        return _with(state, settled, receipt), receipt
    if type(operation) is not Pursue:
        raise DowntimeError("unknown downtime operation")
    if (type(calendar_receipt) is not Receipt or calendar_receipt.operation != Advance(operation.operation_id,
                                                                                      operation.elapsed)):
        raise DowntimeError("a pursuit needs the calendar advance it spent", "invalid_history")
    elapsed = calendar_receipt.operation.duration.exact_minutes
    effort = _pursuit_effort(operation, elapsed)
    credited = min(effort, current.remaining_minutes)
    progressed = replace(current, progress_minutes=current.progress_minutes + credited)
    completed = progressed.remaining_minutes == 0
    if completed:
        # Completion is recorded at the end of the stretch that finished it: with partial effort the
        # exact hour the requirement was met within the stretch is not known.
        progressed = replace(progressed, status=COMPLETE, settled_by=operation.operation_id,
                             settled_at=calendar_receipt.after, outcome=operation.note)
    receipt = DowntimeReceipt(operation, credited, completed, calendar_receipt.before, calendar_receipt.after,
                              calendar_receipt.triggered)
    return _with(state, progressed, receipt), receipt


def _open_activity(state: DowntimeState, activity_id: str) -> Progress:
    current = state.activity(activity_id)
    if current is None:
        raise DowntimeError("no such activity", "unknown_activity")
    if current.status != ACTIVE:
        raise DowntimeError(f"activity is already {current.status}", "activity_settled")
    return current


def _pursuit_effort(operation: Pursue, elapsed: int) -> int:
    if elapsed == 0:
        raise DowntimeError("a pursuit must spend some time", "no_time")
    effort = elapsed if operation.effort_minutes is None else operation.effort_minutes
    if effort > elapsed:
        raise DowntimeError("effort cannot exceed the time that passes", "effort_exceeds_elapsed")
    return effort


def _with(state: DowntimeState, progress: Progress, receipt: DowntimeReceipt) -> DowntimeState:
    activities = tuple(progress if p.activity.activity_id == progress.activity.activity_id else p
                       for p in state.activities)
    return replace(state, activities=activities, receipts=state.receipts + (receipt,))

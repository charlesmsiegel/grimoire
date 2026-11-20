"""Adopt a faction plan, or progress every adopted plan across one explicit calendar advance (076).

The scheduler is event driven: a skip reads only the adopted plans, never the campaign roster, so a
distant shopkeeper with no plan costs nothing and receives no model call. No model is ever called
here; relevance is a deterministic rule (a crossed plan deadline first, then priority, then plan id)
under a caller-chosen budget. A skip is one calendar advance, identified by that advance's own
operation id, so a retried skip returns its original receipt and time is never counted twice.
"""
from __future__ import annotations

from dataclasses import replace

from grimoire.calendars import Advance as CalendarAdvance
from grimoire.calendars import CalendarError, CalendarState, Moment
from grimoire.institutions import InstitutionState

from .model import (
    MAX_PLANS, Adopt, Adopted, Advanced, DeadlineOutcome, Development, OffscreenError, OffscreenState,
    PlanProgress, Skip,
)


def apply(campaign_id: str, state: OffscreenState | None, operation: Adopt | Skip, *,
          calendar: CalendarState, institution: InstitutionState | None = None):
    """Return ``(state, receipt, already_applied)`` for one operation, without side effects.

    ``calendar`` is the campaign's current 055 calendar. Adopting needs the ``institution`` whose
    recorded decision authorized the plan, and starts the plan at the calendar's current time. A
    skip needs the calendar advance it names. A retry returns the retained receipt unchanged; an
    adoption retry still checks the decision, since a receipt lookup is not an authority bypass.
    """
    if type(operation) is Adopt:
        return _apply(campaign_id, state, operation, calendar=calendar, institution=institution,
                      at=None if type(calendar) is not CalendarState else calendar.now)
    return _apply(campaign_id, state, operation, calendar=calendar, institution=None, at=None)


def pending_skips(state: OffscreenState | None, calendar: CalendarState) -> tuple[str, ...]:
    """Calendar advances, in order, that moved time past ``state.as_of`` and are not applied yet."""
    if state is None or type(calendar) is not CalendarState:
        return ()
    return tuple(r.operation.operation_id for r in calendar.receipts
                 if type(r.operation) is CalendarAdvance and r.after > state.as_of
                 and state.receipt(r.operation.operation_id) is None)


def _apply(campaign_id, state, operation, *, calendar, institution, at, replay=False):
    """``replay`` rebuilds a stored history: the decision was checked when the plan was adopted
    (institutions are not persisted yet), and later calendar advances are not yet "pending"."""
    if type(campaign_id) is not str or type(calendar) is not CalendarState:
        raise OffscreenError("invalid_field", "apply needs a campaign id and its calendar state")
    if calendar.campaign_id != campaign_id:
        raise OffscreenError("campaign_mismatch", "calendar belongs to another campaign")
    if state is not None and (type(state) is not OffscreenState or state.campaign_id != campaign_id):
        raise OffscreenError("campaign_mismatch", "offscreen state belongs to another campaign")
    if type(operation) not in (Adopt, Skip):
        raise OffscreenError("invalid_field", "unknown offscreen operation")
    old = None if state is None else state.receipt(operation.operation_id)
    if old is not None and old.operation != operation:
        raise OffscreenError("operation_mismatch", "operation id already belongs to a different request")
    if type(operation) is Adopt:
        if not replay:
            _check_decision(operation.plan, institution)
        if old is not None:
            return state, old, True
        return _adopt(campaign_id, state, operation, calendar, at, replay)
    if old is not None:
        return state, old, True
    return _skip(state, operation, calendar)


def _check_decision(plan, institution) -> None:
    if type(institution) is not InstitutionState:
        raise OffscreenError("decision_missing", "adoption needs the institution that decided")
    if institution.institution.group != plan.group:
        raise OffscreenError("decision_mismatch", "the institution is not the plan's group")
    decision = next((d for d in institution.decisions if d.operation_id == plan.decision_id), None)
    if decision is None:
        raise OffscreenError("decision_missing", "the institution recorded no such decision")
    if decision.choice != plan.choice:
        raise OffscreenError("decision_mismatch", "the plan is not the decision's choice")


def _adopt(campaign_id, state, operation, calendar, at, replay):
    plan = operation.plan
    if type(at) is not Moment:
        raise OffscreenError("invalid_field", "adoption needs a calendar moment")
    if plan.group.campaign_id != campaign_id:
        raise OffscreenError("identity", "the plan's group belongs to another campaign")
    if state is None and replay and all(r.after != at for r in calendar.receipts):
        raise OffscreenError("calendar_mismatch", "the first plan did not start at a recorded calendar time")
    if state is not None:
        if state.as_of != at or (not replay and pending_skips(state, calendar)):
            # Starting a plan "now" while earlier time is unapplied would let it skip or double count.
            raise OffscreenError("pending_skips", "apply the calendar's pending advances first")
        if state.progress(plan.plan_id) is not None:
            raise OffscreenError("duplicate_plan", f"plan {plan.plan_id} is already adopted")
        if len(state.plans) >= MAX_PLANS:
            raise OffscreenError("limit", f"at most {MAX_PLANS} adopted plans")
    if plan.deadline_id is not None:
        deadline = next((d for d in calendar.deadlines if d.deadline_id == plan.deadline_id), None)
        if deadline is None:
            raise OffscreenError("unknown_deadline", f"no calendar deadline {plan.deadline_id}")
        if deadline.due <= at:
            raise OffscreenError("past_deadline", f"deadline {plan.deadline_id} is not in the future")
    dues, total = [], 0
    try:
        for step in plan.steps:
            total += step.minutes
            due = at.advance(total)
            calendar.calendar.format(due.fixed_day)
            dues.append(due)
    except CalendarError:
        raise OffscreenError("out_of_range", "the plan runs past the calendar's supported range") from None
    receipt = Adopted(operation, at)
    progress = PlanProgress(plan, at, tuple(dues))
    if state is None:
        return OffscreenState(campaign_id, at, (progress,), (receipt,)), receipt, False
    return replace(state, plans=state.plans + (progress,), receipts=state.receipts + (receipt,)), receipt, False


def _skip(state, operation, calendar):
    if state is None:
        raise OffscreenError("no_plans", "no plan has been adopted")
    advance = calendar.receipt(operation.operation_id)
    if advance is None:
        raise OffscreenError("unknown_skip", f"the calendar has no operation {operation.operation_id}")
    if type(advance.operation) is not CalendarAdvance:
        raise OffscreenError("not_an_advance", f"calendar operation {operation.operation_id} moves no time")
    if advance.before != state.as_of:
        raise OffscreenError("skip_out_of_order", "skips apply in calendar order, from the plans' time")
    after = advance.after
    crossed = frozenset(advance.triggered)
    due = [p for p in state.plans if p.pending and p.pending[0][1] <= after]
    due.sort(key=lambda p: (p.plan.deadline_id not in crossed, -p.plan.priority, p.plan.plan_id))
    selected, deferred = due[:operation.budget], due[operation.budget:]
    chosen = {p.plan.plan_id for p in selected}
    developments, plans = [], []
    for progress in state.plans:
        if progress.plan.plan_id in chosen:
            done = sum(1 for _, step_due in progress.pending if step_due <= after)
            for index in range(progress.completed, progress.completed + done):
                developments.append((progress.dues[index], progress.plan.plan_id, index,
                                     Development(progress.plan.plan_id, progress.plan.steps[index].step_id,
                                                 progress.dues[index])))
            progress = replace(progress, completed=progress.completed + done)
        plans.append(progress)
    developments.sort(key=lambda item: item[:3])
    deadline_due = {d.deadline_id: d.due for d in calendar.deadlines}
    if not all(p.plan.deadline_id in deadline_due for p in state.plans if p.plan.deadline_id is not None):
        raise OffscreenError("calendar_mismatch", "a plan's calendar deadline no longer exists")
    outcomes = tuple(DeadlineOutcome(p.plan.plan_id, p.plan.deadline_id,
                                     "met" if p.dues[-1] <= deadline_due[p.plan.deadline_id] else "missed")
                     for p in sorted(state.plans, key=lambda p: p.plan.plan_id) if p.plan.deadline_id in crossed)
    receipt = Advanced(operation, advance.before, after, tuple(item[3] for item in developments),
                       tuple(p.plan.plan_id for p in deferred), outcomes)
    return (OffscreenState(state.campaign_id, after, tuple(plans), state.receipts + (receipt,)),
            receipt, False)

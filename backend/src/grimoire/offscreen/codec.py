"""Strict readable offscreen authority: the receipt log is stored, and replay proves it (076).

The stored extension holds only operations and their receipts. Reading replays each one against the
campaign's current calendar and refuses any receipt that replay does not reproduce, so a replaced
calendar history, or a hand edit that contradicts a later receipt, fails closed. Replay cannot check
what no later receipt observed: an adoption's decision provenance (institutions are not stored yet),
or a step that no skip has reached. Those are the user's canonical JSON, as authored.
"""
from __future__ import annotations

from collections.abc import Mapping

from grimoire.calendars import CalendarError, CalendarState, Moment
from grimoire.core.identity import InstanceRef

from .model import (
    Adopt,
    Adopted,
    Advanced,
    DeadlineOutcome,
    Development,
    FactionPlan,
    OffscreenError,
    OffscreenState,
    PlanStep,
    Skip,
)
from .schedule import _apply

OFFSCREEN_SCHEMA = "grimoire.offscreen/1"
OFFSCREEN_KEY = "offscreen"


def _object(value: object, fields: str) -> Mapping:
    if not isinstance(value, Mapping) or set(value) != set(fields.split()):
        raise OffscreenError("invalid_record", f"object must have exactly these fields: {fields}")
    return value


def _array(value: object) -> tuple:
    if not isinstance(value, (tuple, list)):
        raise OffscreenError("invalid_record", "expected a list")
    return tuple(value)


def _moment(value: object) -> Moment:
    raw = _object(value, "fixed_day minute")
    try:
        return Moment(raw["fixed_day"], raw["minute"])
    except CalendarError:
        raise OffscreenError("invalid_record", "invalid moment") from None


def _moment_out(moment: Moment) -> dict:
    return {"fixed_day": moment.fixed_day, "minute": moment.minute}


def _ref(value: object) -> InstanceRef:
    try:
        return InstanceRef.parse(value)
    except (ValueError, TypeError):
        raise OffscreenError("invalid_record", "invalid group reference") from None


def plan_to_payload(plan: FactionPlan) -> dict:
    return {"plan_id": plan.plan_id, "group": str(plan.group), "decision_id": plan.decision_id,
            "choice": plan.choice, "goal": plan.goal, "priority": plan.priority, "deadline_id": plan.deadline_id,
            "steps": [{"step_id": s.step_id, "label": s.label, "minutes": s.minutes} for s in plan.steps]}


def plan_from_payload(value: object) -> FactionPlan:
    raw = _object(value, "plan_id group decision_id choice goal priority deadline_id steps")
    steps = tuple(PlanStep(s["step_id"], s["label"], s["minutes"])
                  for s in (_object(item, "step_id label minutes") for item in _array(raw["steps"])))
    return FactionPlan(raw["plan_id"], _ref(raw["group"]), raw["decision_id"], raw["choice"], raw["goal"], steps,
                       raw["priority"], raw["deadline_id"])


def receipt_to_payload(receipt) -> dict:
    if type(receipt) is Adopted:
        return {"kind": "adopt", "operation_id": receipt.operation.operation_id,
                "plan": plan_to_payload(receipt.operation.plan), "at": _moment_out(receipt.at)}
    return {"kind": "skip", "operation_id": receipt.operation.operation_id, "budget": receipt.operation.budget,
            "before": _moment_out(receipt.before), "after": _moment_out(receipt.after),
            "developments": [{"plan_id": d.plan_id, "step_id": d.step_id, "due": _moment_out(d.due)}
                             for d in receipt.developments],
            "deferred": list(receipt.deferred),
            "deadlines": [{"plan_id": d.plan_id, "deadline_id": d.deadline_id, "outcome": d.outcome}
                          for d in receipt.deadlines]}


def _receipt_from_payload(value: object):
    """Return ``(operation, stored receipt, adoption moment or None)``."""
    if not isinstance(value, Mapping):
        raise OffscreenError("invalid_record", "receipt must be an object")
    if value.get("kind") == "adopt":
        raw = _object(value, "kind operation_id plan at")
        operation = Adopt(raw["operation_id"], plan_from_payload(raw["plan"]))
        at = _moment(raw["at"])
        return operation, Adopted(operation, at), at
    if value.get("kind") == "skip":
        raw = _object(value, "kind operation_id budget before after developments deferred deadlines")
        operation = Skip(raw["operation_id"], raw["budget"])
        developments = tuple(Development(d["plan_id"], d["step_id"], _moment(d["due"]))
                             for d in (_object(i, "plan_id step_id due") for i in _array(raw["developments"])))
        outcomes = tuple(DeadlineOutcome(d["plan_id"], d["deadline_id"], d["outcome"])
                         for d in (_object(i, "plan_id deadline_id outcome") for i in _array(raw["deadlines"])))
        receipt = Advanced(operation, _moment(raw["before"]), _moment(raw["after"]), developments,
                           _array(raw["deferred"]), outcomes)
        return operation, receipt, None
    raise OffscreenError("invalid_record", "unknown offscreen receipt")


def state_to_payload(state: OffscreenState) -> dict:
    return {"schema": OFFSCREEN_SCHEMA, "campaign_id": state.campaign_id, "as_of": _moment_out(state.as_of),
            "receipts": [receipt_to_payload(r) for r in state.receipts]}


def state_from_payload(campaign_id: str, value: object, calendar: CalendarState | None) -> OffscreenState:
    raw = _object(value, "schema campaign_id as_of receipts")
    if raw["schema"] != OFFSCREEN_SCHEMA:
        raise OffscreenError("unsupported_schema", "unsupported offscreen schema")
    if raw["campaign_id"] != campaign_id:
        raise OffscreenError("campaign_mismatch", "offscreen state belongs to another campaign")
    if calendar is None:
        raise OffscreenError("calendar_mismatch", "offscreen plans need the campaign calendar")
    state = None
    for item in _array(raw["receipts"]):
        operation, stored, at = _receipt_from_payload(item)
        try:
            state, expected, duplicate = _apply(campaign_id, state, operation, calendar=calendar,
                                                institution=None, at=at, replay=True)
        except OffscreenError as error:
            raise OffscreenError("invalid_history", f"replay refused a receipt ({error.reason})") from None
        if duplicate or expected != stored:
            raise OffscreenError("invalid_history", "an offscreen receipt contradicts replay")
    if state is None:
        raise OffscreenError("invalid_history", "offscreen state has no adoption receipt")
    if _moment(raw["as_of"]) != state.as_of:
        raise OffscreenError("invalid_history", "offscreen time contradicts its receipts")
    return state

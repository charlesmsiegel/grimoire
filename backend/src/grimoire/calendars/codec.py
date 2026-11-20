"""Strict readable calendar authority. Replay proves every materialized clock/flag (055).

Unknown outer scope fields are preserved by the adapter; unknown calendar fields or schemas
fail closed, since dropping one could discard an authority rule from a newer writer.
"""
from __future__ import annotations

from collections.abc import Mapping

from .model import Advance, CalendarState, Deadline, Duration, Initialize, Moment, Receipt, Schedule, apply
from .providers import CalendarDefinition, CalendarError, Month

CALENDAR_SCHEMA = "grimoire.calendar/1"
CALENDAR_KEY = "calendar"


def _object(value: object, fields: str) -> Mapping:
    if not isinstance(value, Mapping) or set(value) != set(fields.split()):
        raise CalendarError(f"calendar object must have exactly these fields: {fields}", "invalid_record")
    return value


def _array(value: object) -> tuple:
    if not isinstance(value, (tuple, list)):
        raise CalendarError("calendar array must be a list", "invalid_record")
    return tuple(value)


def calendar_to_payload(calendar: CalendarDefinition) -> dict:
    return {"provider": calendar.provider, "custom_id": calendar.custom_id, "epoch_fixed": calendar.epoch_fixed,
            "months": [{"name": month.name, "days": month.days} for month in calendar.months]}


def calendar_from_payload(value: object) -> CalendarDefinition:
    raw = _object(value, "provider custom_id epoch_fixed months")
    months = []
    for item in _array(raw["months"]):
        month = _object(item, "name days")
        months.append(Month(month["name"], month["days"]))
    return CalendarDefinition(raw["provider"], raw["custom_id"], tuple(months), raw["epoch_fixed"])


def moment_to_payload(moment: Moment | None) -> dict | None:
    return None if moment is None else {"fixed_day": moment.fixed_day, "minute": moment.minute}


def moment_from_payload(value: object) -> Moment:
    raw = _object(value, "fixed_day minute")
    return Moment(raw["fixed_day"], raw["minute"])


def deadline_to_payload(deadline: Deadline) -> dict:
    return {"deadline_id": deadline.deadline_id, "due": moment_to_payload(deadline.due),
            "label": deadline.label, "kind": deadline.kind, "triggered_by": deadline.triggered_by}


def deadline_from_payload(value: object) -> Deadline:
    raw = _object(value, "deadline_id due label kind triggered_by")
    return Deadline(raw["deadline_id"], moment_from_payload(raw["due"]), raw["label"], raw["kind"], raw["triggered_by"])


def operation_to_payload(operation) -> dict:
    common = {"operation_id": operation.operation_id}
    if isinstance(operation, Initialize):
        return {**common, "kind": "initialize", "calendar": calendar_to_payload(operation.calendar),
                "at": moment_to_payload(operation.at)}
    if isinstance(operation, Schedule):
        return {**common, "kind": "schedule", "deadline": deadline_to_payload(operation.deadline)}
    duration = operation.duration
    return {**common, "kind": "advance", "duration": {"minimum": duration.minimum, "maximum": duration.maximum,
                                                     "selected": duration.selected, "note": duration.note}}


def operation_from_payload(value: object):
    if not isinstance(value, Mapping):
        raise CalendarError("calendar operation must be an object", "invalid_record")
    kind = value.get("kind")
    if kind == "initialize":
        raw = _object(value, "operation_id kind calendar at")
        return Initialize(raw["operation_id"], calendar_from_payload(raw["calendar"]), moment_from_payload(raw["at"]))
    if kind == "schedule":
        raw = _object(value, "operation_id kind deadline")
        return Schedule(raw["operation_id"], deadline_from_payload(raw["deadline"]))
    if kind == "advance":
        raw = _object(value, "operation_id kind duration")
        duration = _object(raw["duration"], "minimum maximum selected note")
        return Advance(raw["operation_id"], Duration(duration["minimum"], duration["maximum"],
                                                      duration["selected"], duration["note"]))
    raise CalendarError("unknown calendar operation", "invalid_record")


def receipt_to_payload(receipt: Receipt) -> dict:
    return {"operation": operation_to_payload(receipt.operation), "before": moment_to_payload(receipt.before),
            "after": moment_to_payload(receipt.after), "triggered": list(receipt.triggered)}


def state_to_payload(state: CalendarState) -> dict:
    return {"schema": CALENDAR_SCHEMA, "campaign_id": state.campaign_id, "calendar": calendar_to_payload(state.calendar),
            "now": moment_to_payload(state.now), "deadlines": [deadline_to_payload(d) for d in state.deadlines],
            "receipts": [receipt_to_payload(r) for r in state.receipts]}


def state_from_payload(campaign_id: str, value: object) -> CalendarState:
    raw = _object(value, "schema campaign_id calendar now deadlines receipts")
    if raw["schema"] != CALENDAR_SCHEMA:
        raise CalendarError("unsupported calendar schema", "unsupported_schema")
    if raw["campaign_id"] != campaign_id:
        raise CalendarError("calendar belongs to another campaign", "campaign_mismatch")
    state = None
    for item in _array(raw["receipts"]):
        receipt = _object(item, "operation before after triggered")
        operation = operation_from_payload(receipt["operation"])
        state, expected, duplicate = apply(campaign_id, state, operation)
        before = None if receipt["before"] is None else moment_from_payload(receipt["before"])
        after = moment_from_payload(receipt["after"])
        triggered = _array(receipt["triggered"])
        if duplicate or expected != Receipt(operation, before, after, triggered):
            raise CalendarError("calendar receipt contradicts replay", "invalid_history")
    if state is None:
        raise CalendarError("calendar has no initialization receipt", "invalid_history")
    calendar = calendar_from_payload(raw["calendar"])
    now = moment_from_payload(raw["now"])
    deadlines = tuple(deadline_from_payload(item) for item in _array(raw["deadlines"]))
    if (calendar, now, deadlines) != (state.calendar, state.now, state.deadlines):
        raise CalendarError("calendar state contradicts its receipts", "invalid_history")
    return state

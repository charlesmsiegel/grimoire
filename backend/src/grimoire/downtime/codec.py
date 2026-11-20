"""Strict readable downtime authority, proved by replay against the same record's calendar (074).

The ``downtime`` object sits beside 055's ``calendar`` in a campaign scope record. Reading it replays
every retained receipt through the same rules that wrote it, and checks each pursuit against the
calendar receipt that spent its time: a hand edit that claims study without the calendar moving,
or moves the calendar without the study, fails closed. Unknown fields or schemas fail closed too,
since dropping one could discard an authority rule from a newer writer.
"""
from __future__ import annotations

from collections.abc import Mapping

from grimoire.calendars import CalendarError, CalendarState, Duration, Moment
from grimoire.core.identity import InstanceRef

from .model import (
    Abandon, Activity, Begin, DowntimeError, DowntimeReceipt, DowntimeState, Progress, Pursue, empty, record,
)

DOWNTIME_SCHEMA = "grimoire.downtime/1"
DOWNTIME_KEY = "downtime"


def _object(value: object, fields: str) -> Mapping:
    if not isinstance(value, Mapping) or set(value) != set(fields.split()):
        raise DowntimeError(f"downtime object must have exactly these fields: {fields}", "invalid_record")
    return value


def _array(value: object) -> tuple:
    if not isinstance(value, (tuple, list)):
        raise DowntimeError("downtime array must be a list", "invalid_record")
    return tuple(value)


def _moment(value: object) -> Moment | None:
    if value is None:
        return None
    raw = _object(value, "fixed_day minute")
    return Moment(raw["fixed_day"], raw["minute"])


def _moment_payload(moment: Moment | None) -> dict | None:
    return None if moment is None else {"fixed_day": moment.fixed_day, "minute": moment.minute}


def activity_to_payload(activity: Activity) -> dict:
    return {"activity_id": activity.activity_id, "actor": str(activity.actor), "kind": activity.kind,
            "intent": activity.intent, "required_minutes": activity.required_minutes}


def activity_from_payload(value: object) -> Activity:
    raw = _object(value, "activity_id actor kind intent required_minutes")
    try:
        actor = InstanceRef.parse(raw["actor"])
    except (TypeError, ValueError):
        raise DowntimeError("activity actor is not a campaign instance reference", "invalid_record") from None
    return Activity(raw["activity_id"], actor, raw["kind"], raw["intent"], raw["required_minutes"])


def operation_to_payload(operation) -> dict:
    common = {"operation_id": operation.operation_id}
    if isinstance(operation, Begin):
        return {**common, "kind": "begin", "activity": activity_to_payload(operation.activity)}
    if isinstance(operation, Abandon):
        return {**common, "kind": "abandon", "activity_id": operation.activity_id, "note": operation.note}
    elapsed = operation.elapsed
    return {**common, "kind": "pursue", "activity_id": operation.activity_id,
            "elapsed": {"minimum": elapsed.minimum, "maximum": elapsed.maximum, "selected": elapsed.selected,
                        "note": elapsed.note},
            "effort_minutes": operation.effort_minutes, "note": operation.note}


def operation_from_payload(value: object):
    if not isinstance(value, Mapping):
        raise DowntimeError("downtime operation must be an object", "invalid_record")
    kind = value.get("kind")
    if kind == "begin":
        raw = _object(value, "operation_id kind activity")
        return Begin(raw["operation_id"], activity_from_payload(raw["activity"]))
    if kind == "abandon":
        raw = _object(value, "operation_id kind activity_id note")
        return Abandon(raw["operation_id"], raw["activity_id"], raw["note"])
    if kind == "pursue":
        raw = _object(value, "operation_id kind activity_id elapsed effort_minutes note")
        elapsed = _object(raw["elapsed"], "minimum maximum selected note")
        try:
            duration = Duration(elapsed["minimum"], elapsed["maximum"], elapsed["selected"], elapsed["note"])
        except CalendarError as error:
            raise DowntimeError(str(error), "invalid_record") from None
        return Pursue(raw["operation_id"], raw["activity_id"], duration, raw["effort_minutes"], raw["note"])
    raise DowntimeError("unknown downtime operation", "invalid_record")


def receipt_to_payload(receipt: DowntimeReceipt) -> dict:
    return {"operation": operation_to_payload(receipt.operation), "credited": receipt.credited,
            "completed": receipt.completed, "before": _moment_payload(receipt.before),
            "after": _moment_payload(receipt.after), "triggered": list(receipt.triggered)}


def progress_to_payload(progress: Progress) -> dict:
    return {"activity": activity_to_payload(progress.activity), "progress_minutes": progress.progress_minutes,
            "status": progress.status, "settled_by": progress.settled_by,
            "settled_at": _moment_payload(progress.settled_at), "outcome": progress.outcome}


def progress_from_payload(value: object) -> Progress:
    raw = _object(value, "activity progress_minutes status settled_by settled_at outcome")
    return Progress(activity_from_payload(raw["activity"]), raw["progress_minutes"], raw["status"],
                    raw["settled_by"], _moment(raw["settled_at"]), raw["outcome"])


def state_to_payload(state: DowntimeState) -> dict:
    return {"schema": DOWNTIME_SCHEMA, "campaign_id": state.campaign_id,
            "activities": [progress_to_payload(p) for p in state.activities],
            "receipts": [receipt_to_payload(r) for r in state.receipts]}


def state_from_payload(campaign_id: str, value: object, calendar: CalendarState | None) -> DowntimeState:
    """Replay the stored receipts against ``calendar``, the same record's 055 state."""
    try:
        return _replay(campaign_id, value, calendar)
    except CalendarError as error:
        raise DowntimeError(str(error), "invalid_record") from None


def _replay(campaign_id: str, value: object, calendar: CalendarState | None) -> DowntimeState:
    raw = _object(value, "schema campaign_id activities receipts")
    if raw["schema"] != DOWNTIME_SCHEMA:
        raise DowntimeError("unsupported downtime schema", "unsupported_schema")
    if raw["campaign_id"] != campaign_id:
        raise DowntimeError("downtime belongs to another campaign", "campaign_mismatch")
    calendar_receipts = () if calendar is None else calendar.receipts
    state = empty(campaign_id)
    # Index of the calendar receipt the last replayed operation happened at; -1 before any calendar.
    position = -1
    for item in _array(raw["receipts"]):
        stored = _object(item, "operation credited completed before after triggered")
        operation = operation_from_payload(stored["operation"])
        before, after = _moment(stored["before"]), _moment(stored["after"])
        if isinstance(operation, Pursue):
            index = next((i for i, r in enumerate(calendar_receipts)
                          if r.operation.operation_id == operation.operation_id), None)
            if index is None or index <= position:
                raise DowntimeError("a pursuit's time is missing from or out of order in the calendar",
                                    "invalid_history")
            state, expected = record(state, operation, calendar_receipt=calendar_receipts[index])
            position = index
        else:
            if before != after:
                raise DowntimeError("a begin or abandon spends no time", "invalid_history")
            position = _instant(calendar_receipts, position, after)
            state, expected = record(state, operation, at=after)
        if expected != DowntimeReceipt(operation, stored["credited"], stored["completed"], before, after,
                                       _array(stored["triggered"])):
            raise DowntimeError("downtime receipt contradicts replay", "invalid_history")
    activities = tuple(progress_from_payload(item) for item in _array(raw["activities"]))
    if activities != state.activities:
        raise DowntimeError("downtime activities contradict their receipts", "invalid_history")
    return state


def _instant(receipts: tuple, position: int, at: Moment | None) -> int:
    """Where a timeless operation stood in the calendar's history: before it began (``at`` is None), or
    at a time the calendar actually reached no earlier than the previous operation."""
    if at is None:
        if position != -1:
            raise DowntimeError("an operation after the calendar began has no campaign time", "invalid_history")
        return -1
    index = next((i for i in range(max(position, 0), len(receipts)) if receipts[i].after == at), None)
    if index is None:
        raise DowntimeError("an operation's campaign time is not one the calendar reached", "invalid_history")
    return index

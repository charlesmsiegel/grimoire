"""Strict readable payload of the commitment ledger, one ``scope.json`` extension (077).

Unknown fields or schemas fail closed: dropping a field a newer writer added could drop an authority
record. The adapter preserves every other scope field.
"""
from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.identity import InstanceRef
from grimoire.core.knowledge import Moment

from .model import Authorship, ClockTime, Commitment, CommitmentError, CommitmentLedger, Offer, Transition, Void

COMMITMENTS_SCHEMA = "grimoire.commitments/1"
COMMITMENTS_KEY = "commitments"

_OFFER = "op operation_id commitment cites at clock_at schedules"
_TRANSITION = "op operation_id commitment_id status cites at note clock_at controls authorship"
_VOID = "op operation_id target reason authorship"
_COMMITMENT = "commitment_id kind summary owed_by owed_to deadline_id"
_AUTHORSHIP = "contribution_id actor_id submitted_by kind scene_id turn_id text fingerprint"


def _object(value: object, fields: str, optional: str = "") -> Mapping:
    if not isinstance(value, Mapping) or not set(fields.split()) <= set(value) <= set(fields.split()) | set(
            optional.split()):
        raise CommitmentError(f"a commitment object must have exactly these fields: {fields}")
    return value


def _array(value: object) -> tuple:
    if type(value) not in (list, tuple):  # the store hands back frozen payloads, with tuples for arrays
        raise CommitmentError("a commitment array must be a list")
    return tuple(value)


def _ref(value: object) -> InstanceRef:
    try:
        return InstanceRef.parse(value)
    except (TypeError, ValueError):
        raise CommitmentError("not a campaign instance reference") from None


def _moment_to(moment: Moment) -> dict:
    data: dict = {"sequence": moment.sequence}
    if moment.scene_id is not None:
        data["scene_id"] = moment.scene_id
    if moment.turn_id is not None:
        data["turn_id"] = moment.turn_id
    return data


def _moment_from(value: object) -> Moment:
    raw = _object(value, "sequence", "scene_id turn_id")
    if any(raw.get(key, "") is None for key in ("scene_id", "turn_id")):  # absent, never null: one spelling
        raise CommitmentError("a moment's scene or turn is omitted, never null")
    if type(raw["sequence"]) is not int:
        raise CommitmentError("a moment's sequence must be an integer")
    try:
        return Moment(raw["sequence"], raw.get("scene_id"), raw.get("turn_id"))
    except (TypeError, ValueError):
        raise CommitmentError("malformed moment") from None


def _clock_to(moment: ClockTime | None) -> dict | None:
    return None if moment is None else {"fixed_day": moment.fixed_day, "minute": moment.minute}


def _clock_from(value: object) -> ClockTime | None:
    if value is None:
        return None
    raw = _object(value, "fixed_day minute")
    return ClockTime(raw["fixed_day"], raw["minute"])


def _commitment_to(commitment: Commitment) -> dict:
    return {"commitment_id": commitment.commitment_id, "kind": commitment.kind, "summary": commitment.summary,
            "owed_by": [str(ref) for ref in commitment.owed_by], "owed_to": [str(ref) for ref in commitment.owed_to],
            "deadline_id": commitment.deadline_id}


def _authorship_to(item: Authorship) -> dict:
    return {name: getattr(item, name) for name in _AUTHORSHIP.split()}


def operation_to_payload(op: Offer | Transition | Void) -> dict:
    if isinstance(op, Void):
        return {"op": "void", "operation_id": op.operation_id, "target": op.target, "reason": op.reason,
                "authorship": [_authorship_to(a) for a in op.authorship]}
    if isinstance(op, Offer):
        return {"op": "offer", "operation_id": op.operation_id, "commitment": _commitment_to(op.commitment),
                "cites": list(op.cites), "at": _moment_to(op.at), "clock_at": _clock_to(op.clock_at),
                "schedules": op.schedules}
    return {"op": "transition", "operation_id": op.operation_id, "commitment_id": op.commitment_id,
            "status": op.status, "cites": list(op.cites), "at": _moment_to(op.at), "note": op.note,
            "clock_at": _clock_to(op.clock_at), "controls": [[actor, who] for actor, who in op.controls],
            "authorship": [_authorship_to(a) for a in op.authorship]}


def _authorships(value: object) -> tuple[Authorship, ...]:
    return tuple(Authorship(**dict(_object(item, _AUTHORSHIP))) for item in _array(value))


def _operation_from(campaign_id: str, value: object) -> Offer | Transition | Void:
    if not isinstance(value, Mapping) or value.get("op") not in ("offer", "transition", "void"):
        raise CommitmentError("unknown commitment operation")
    if value["op"] == "void":
        raw = _object(value, _VOID)
        return Void(raw["operation_id"], raw["target"], raw["reason"], _authorships(raw["authorship"]))
    if value["op"] == "offer":
        raw = _object(value, _OFFER)
        item = _object(raw["commitment"], _COMMITMENT)
        commitment = Commitment(campaign_id, item["commitment_id"], item["kind"], item["summary"],
                                tuple(_ref(r) for r in _array(item["owed_by"])),
                                tuple(_ref(r) for r in _array(item["owed_to"])), item["deadline_id"])
        return Offer(raw["operation_id"], commitment, _array(raw["cites"]), _moment_from(raw["at"]),
                     _clock_from(raw["clock_at"]), raw["schedules"])
    raw = _object(value, _TRANSITION)
    controls = tuple(tuple(_array(pair)) for pair in _array(raw["controls"]))
    return Transition(raw["operation_id"], raw["commitment_id"], raw["status"], _array(raw["cites"]),
                      _moment_from(raw["at"]), raw["note"], _clock_from(raw["clock_at"]), controls,
                      _authorships(raw["authorship"]))


def ledger_to_payload(ledger: CommitmentLedger) -> dict:
    return {"schema": COMMITMENTS_SCHEMA, "operations": [operation_to_payload(op) for op in ledger.operations]}


def ledger_from_payload(campaign_id: str, value: object) -> CommitmentLedger:
    """Decode and structurally validate; every failure is a ``CommitmentError`` (never repaired)."""
    raw = _object(value, "schema operations")
    if raw["schema"] != COMMITMENTS_SCHEMA:
        raise CommitmentError("unknown commitments schema", "unknown_schema")
    try:
        return CommitmentLedger(campaign_id, tuple(_operation_from(campaign_id, op) for op in _array(raw["operations"])))
    except CommitmentError:
        raise
    except (TypeError, ValueError) as error:  # a field of the wrong type, caught by a value's own checks
        raise CommitmentError(f"malformed commitment record: {type(error).__name__}") from None

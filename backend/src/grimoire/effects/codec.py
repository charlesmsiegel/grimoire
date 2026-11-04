"""Accepted-event and wrap-up payloads to and from plain JSON-compatible data (Sprint 021).

The store's ``grimoire.scope-record/1`` envelope owns the address and the schema id, so neither is
repeated inside a payload (``identity_in_payload``). Unknown *top-level* keys survive in ``extras``;
inside a known structure an unknown, missing or malformed field is refused. Decoding re-checks the
record's structural invariants, so a hand edit that breaks them makes the file undecodable rather
than silently trusted. Re-encoding is canonical: decode(encode(x)) == x.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.identity import AcceptedRef, ChatRef, InstanceRef, RelationRef, WrapUpRef
from grimoire.core.json_value import JsonObject, JsonValueError, thaw
from grimoire.core.knowledge import KnowledgeError, Moment

from .model import (
    AcceptedEvent, AcceptedOperation, ContributionSnapshot, ControlSnapshot, EffectError, EndorsementRecord, Op,
    Receipt, Revocation, SceneWrapUp, Skipped,
)

ACCEPTED_SCHEMA = "grimoire.accepted-event/0"
WRAPUP_SCHEMA = "grimoire.scene-wrapup/0"
_ACCEPTED_IDENTITY = frozenset({"ref", "address", "campaign_id", "event_id", "schema"})
_WRAPUP_IDENTITY = frozenset({"ref", "address", "campaign_id", "wrapup_id", "schema"})
ACCEPTED_FIELDS = frozenset({"chat", "moment", "summary", "proposed_by", "operations", "contributions",
                             "endorsements", "controls", "committed_by", "accepted_at", "ordinal", "receipts",
                             "revocation"})
WRAPUP_FIELDS = frozenset({"chat", "scene_id", "through", "summary", "cites", "written_by", "written_at"})
_MOMENT = frozenset({"sequence", "scene_id", "turn_id"})
_OP = frozenset({"kind", "predicate", "subject", "object", "agent", "domain", "backed_by"})
_CONTRIBUTION = frozenset({"contribution_id", "actor_id", "submitted_by", "kind", "scene_id", "turn_id", "text"})
_ENDORSEMENT = frozenset({"contribution_id", "principal", "fingerprint"})
_CONTROL = frozenset({"actor_id", "controller_id"})
_RECEIPT = frozenset({"op", "relation"})
_REVOCATION = frozenset({"reason", "at", "committed_by", "endorsements", "skipped"})
_SKIPPED = frozenset({"op", "why"})


class EffectCodecError(ValueError):
    """A payload this version refuses to interpret; ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


def _invalid(message: str, reason: str = "invalid_field") -> EffectCodecError:
    return EffectCodecError(message, reason)


# -- encoding ---------------------------------------------------------------------------------------

def _moment(moment: Moment) -> dict:
    data: dict = {"sequence": moment.sequence}
    if moment.scene_id is not None:
        data["scene_id"] = moment.scene_id
    if moment.turn_id is not None:
        data["turn_id"] = moment.turn_id
    return data


def _endorsement(item: EndorsementRecord) -> dict:
    return {"contribution_id": item.contribution_id, "principal": item.principal, "fingerprint": item.fingerprint}


def _op(item: AcceptedOperation) -> dict:
    op = item.op
    data: dict = {"kind": op.kind, "predicate": op.predicate, "subject": str(op.subject), "object": str(op.object),
                  "domain": item.domain}
    if op.agent is not None:
        data["agent"] = str(op.agent)
    if op.backed_by is not None:
        data["backed_by"] = op.backed_by
    return data


def _extras_clash(extras: JsonObject, identity: frozenset[str], fields: frozenset[str]) -> None:
    clash = sorted(set(extras) & (identity | fields))
    if clash:
        reason = "identity_in_payload" if set(clash) & identity else "invalid_field"
        raise EffectCodecError(f"extras may not use the keys {clash}", reason)


def accepted_to_payload(event: AcceptedEvent) -> dict:
    _extras_clash(event.extras, _ACCEPTED_IDENTITY, ACCEPTED_FIELDS)
    payload: dict = {
        "chat": str(event.chat), "moment": _moment(event.moment), "summary": event.summary,
        "proposed_by": event.proposed_by, "operations": [_op(item) for item in event.operations],
        "contributions": [{"contribution_id": c.contribution_id, "actor_id": c.actor_id,
                           "submitted_by": c.submitted_by, "kind": c.kind, "scene_id": c.scene_id,
                           "turn_id": c.turn_id, "text": c.text} for c in event.contributions],
        "endorsements": [_endorsement(e) for e in event.endorsements],
        "controls": [{"actor_id": c.actor_id, "controller_id": c.controller_id} for c in event.controls],
        "committed_by": event.committed_by, "accepted_at": event.accepted_at, "ordinal": event.ordinal,
        "receipts": [{"op": r.op, "relation": str(r.relation)} for r in event.receipts],
    }
    if event.revocation is not None:
        revocation = event.revocation
        payload["revocation"] = {
            "reason": revocation.reason, "at": revocation.at, "committed_by": revocation.committed_by,
            "endorsements": [_endorsement(e) for e in revocation.endorsements],
            "skipped": [{"op": s.op, "why": s.why} for s in revocation.skipped]}
    return {**thaw(event.extras), **payload}  # type: ignore[dict-item]


def wrapup_to_payload(wrapup: SceneWrapUp) -> dict:
    return {"chat": str(wrapup.chat), "scene_id": wrapup.scene_id, "through": _moment(wrapup.through),
               "summary": wrapup.summary, "cites": list(wrapup.cites), "written_by": wrapup.written_by,
               "written_at": wrapup.written_at}


# -- decoding ---------------------------------------------------------------------------------------

def _object(value: object, label: str, allowed: frozenset[str], required: frozenset[str]) -> Mapping:
    if not isinstance(value, Mapping) or not all(type(key) is str for key in value):
        raise _invalid(f"{label} must be a JSON object")
    if set(value) - allowed:
        raise _invalid(f"{label} has unknown fields {sorted(set(value) - allowed)}", "unknown_field")
    if required - set(value):
        raise _invalid(f"{label} is missing {sorted(required - set(value))}", "missing_field")
    return value


def _list(value: object, label: str) -> tuple:
    if not isinstance(value, (list, tuple)):  # a stored payload is frozen: lists arrive as tuples
        raise _invalid(f"{label} must be a list")
    return tuple(value)


def _no_nulls(data: Mapping, label: str) -> None:
    """An explicit ``null`` is not canonical: an absent optional field is simply not written."""
    if any(value is None for value in data.values()):
        raise _invalid(f"{label} holds an explicit null")


def _decode_moment(value: object, label: str) -> Moment:
    data = _object(value, label, _MOMENT, frozenset({"sequence"}))
    _no_nulls(data, label)
    try:
        return Moment(data["sequence"], data.get("scene_id"), data.get("turn_id"))
    except (KnowledgeError, ValueError, TypeError):
        raise _invalid(f"{label} is not a valid moment") from None


def _ref(parse, value: object, label: str):  # type: ignore[no-untyped-def]
    try:
        return parse(value)
    except (ValueError, TypeError):
        raise _invalid(f"{label} is not a valid reference") from None


def _build(make):  # type: ignore[no-untyped-def]
    """Model validation errors become codec errors with the same reason, so a bad file is undecodable."""
    try:
        return make()
    except EffectError as error:
        reason = error.reason
    raise EffectCodecError(f"the record is invalid: {reason}", reason)


def _extras(payload: Mapping, fields: frozenset[str]) -> JsonObject:
    try:
        return JsonObject({key: value for key, value in payload.items() if key not in fields})
    except JsonValueError:
        raise _invalid("unknown fields are not JSON") from None


def _start(payload: object, schema: str, expected: str, identity: frozenset[str]) -> Mapping:
    if not isinstance(payload, Mapping) or not all(type(key) is str for key in payload):
        raise _invalid("a payload must be a JSON object")
    if set(payload) & identity:
        raise EffectCodecError(f"the envelope owns {sorted(set(payload) & identity)}", "identity_in_payload")
    if schema != expected:
        raise EffectCodecError(f"schema {schema!r} is not {expected}", "unsupported_schema")
    return payload


def _decode_op(value: object, index: int) -> AcceptedOperation:
    label = f"operations[{index}]"
    data = _object(value, label, _OP, frozenset({"kind", "predicate", "subject", "object", "domain"}))
    _no_nulls(data, label)
    agent = None if "agent" not in data else _ref(InstanceRef.parse, data["agent"], f"{label}.agent")
    op = _build(lambda: Op(data["kind"], data["predicate"], _ref(InstanceRef.parse, data["subject"], label),
                           _ref(InstanceRef.parse, data["object"], label), agent, data.get("backed_by")))
    return _build(lambda: AcceptedOperation(op, data["domain"]))


def _decode_endorsements(value: object, label: str) -> tuple[EndorsementRecord, ...]:
    found = []
    for index, item in enumerate(_list(value, label)):
        data = _object(item, f"{label}[{index}]", _ENDORSEMENT, _ENDORSEMENT)
        found.append(_build(lambda: EndorsementRecord(data["contribution_id"], data["principal"],
                                                      data["fingerprint"])))
    return tuple(found)


def accepted_from_payload(ref: AcceptedRef, schema: str, payload: object) -> AcceptedEvent:
    """Decode an accepted-event payload; refuse with ``EffectCodecError`` rather than guess."""
    data = _start(payload, schema, ACCEPTED_SCHEMA, _ACCEPTED_IDENTITY)
    missing = sorted((ACCEPTED_FIELDS - {"revocation"}) - set(data))
    if missing:
        raise _invalid(f"missing {missing}", "missing_field")
    if type(data["ordinal"]) is not int:
        raise _invalid("ordinal must be an integer")
    operations = tuple(_decode_op(item, i) for i, item in enumerate(_list(data["operations"], "operations")))
    contributions = []
    for index, item in enumerate(_list(data["contributions"], "contributions")):
        c = _object(item, f"contributions[{index}]", _CONTRIBUTION, _CONTRIBUTION)
        contributions.append(_build(lambda: ContributionSnapshot(
            c["contribution_id"], c["actor_id"], c["submitted_by"], c["kind"], c["scene_id"], c["turn_id"],
            c["text"])))
    controls = []
    for index, item in enumerate(_list(data["controls"], "controls")):
        c = _object(item, f"controls[{index}]", _CONTROL, _CONTROL)
        controls.append(_build(lambda: ControlSnapshot(c["actor_id"], c["controller_id"])))
    receipts = []
    for index, item in enumerate(_list(data["receipts"], "receipts")):
        r = _object(item, f"receipts[{index}]", _RECEIPT, _RECEIPT)
        relation = _ref(RelationRef.parse, r["relation"], f"receipts[{index}].relation")
        receipts.append(_build(lambda: Receipt(r["op"], relation)))
    revocation = None
    if "revocation" in data:
        v = _object(data["revocation"], "revocation", _REVOCATION, _REVOCATION)
        skipped = []
        for index, item in enumerate(_list(v["skipped"], "revocation.skipped")):
            s = _object(item, f"revocation.skipped[{index}]", _SKIPPED, _SKIPPED)
            skipped.append(_build(lambda: Skipped(s["op"], s["why"])))
        endorsements = _decode_endorsements(v["endorsements"], "revocation.endorsements")
        revocation = _build(lambda: Revocation(v["reason"], v["at"], v["committed_by"], endorsements,
                                               tuple(skipped)))
    chat = _ref(ChatRef.parse, data["chat"], "chat")
    moment = _decode_moment(data["moment"], "moment")
    endorsements = _decode_endorsements(data["endorsements"], "endorsements")
    extras = _extras(data, ACCEPTED_FIELDS)
    return _build(lambda: AcceptedEvent(
        ref, chat, moment, data["summary"], data["proposed_by"], operations, tuple(contributions), endorsements,
        tuple(controls), data["committed_by"], data["accepted_at"], data["ordinal"], tuple(receipts), revocation,
        extras))


def wrapup_from_payload(ref: WrapUpRef, schema: str, payload: object) -> SceneWrapUp:
    """Decode a wrap-up payload. It is strict at every level: any key outside the schema (``operations``,
    ``receipts`` ...) is ``unknown_field``, so nothing in a wrap-up file can ever be read as an operation."""
    data = _start(payload, schema, WRAPUP_SCHEMA, _WRAPUP_IDENTITY)
    _object(data, "payload", WRAPUP_FIELDS, WRAPUP_FIELDS)
    chat = _ref(ChatRef.parse, data["chat"], "chat")
    through = _decode_moment(data["through"], "through")
    cites = _list(data["cites"], "cites")
    return _build(lambda: SceneWrapUp(ref, chat, data["scene_id"], through, data["summary"], cites,
                                      data["written_by"], data["written_at"]))

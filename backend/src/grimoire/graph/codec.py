"""Relation payloads to and from plain JSON-compatible data (Sprint 017).

The store's ``grimoire.scope-record/1`` envelope carries the payload opaquely and owns the
address and the schema id, so neither is repeated inside a payload (``address_in_payload``).
Unknown top-level keys survive a read/modify/write in ``extras``; inside a known structure an
unknown or malformed field is refused (``invalid_field``). A predicate this version does not
know decodes to an ``UnsupportedRelation`` that is kept verbatim and never rewritten.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.identity import InstanceRef, RelationRef
from grimoire.core.json_value import JsonObject, JsonValueError, thaw
from grimoire.core.knowledge import KnowledgeError, Moment

from .model import PREDICATES, Ending, GraphError, Relation, Reopening, Retraction, UnsupportedRelation

RELATION_SCHEMA = "grimoire.relation/0"
ADDRESS_KEYS = frozenset({"ref", "address", "campaign_id", "relation_id", "schema"})
FIELDS = frozenset({"predicate", "subject", "object", "since", "provenance", "recorded_at", "ending",
                    "retraction", "reopenings"})
_REQUIRED = frozenset({"predicate", "subject", "object", "since", "provenance", "recorded_at"})
_MOMENT = frozenset({"sequence", "scene_id", "turn_id"})


class RelationCodecError(ValueError):
    """A payload this version refuses to interpret; ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


def _invalid(message: str) -> RelationCodecError:
    return RelationCodecError(message, "invalid_field")


# -- encoding ---------------------------------------------------------------------------------------

def _moment(moment: Moment) -> dict:
    data: dict = {"sequence": moment.sequence}
    if moment.scene_id is not None:
        data["scene_id"] = moment.scene_id
    if moment.turn_id is not None:
        data["turn_id"] = moment.turn_id
    return data


def _ending(ending: Ending) -> dict:
    return {"until": _moment(ending.until), "provenance": ending.provenance, "at": ending.at}


def relation_to_payload(relation: Relation) -> dict:
    clash = sorted(set(relation.extras) & (ADDRESS_KEYS | FIELDS))
    if clash:
        reason = "address_in_payload" if set(clash) & ADDRESS_KEYS else "invalid_field"
        raise RelationCodecError(f"extras may not use the keys {clash}", reason)
    payload: dict = {
        "predicate": relation.predicate, "subject": str(relation.subject), "object": str(relation.object),
        "since": _moment(relation.since), "provenance": relation.provenance, "recorded_at": relation.recorded_at,
    }
    if relation.ending is not None:
        payload["ending"] = _ending(relation.ending)
    if relation.retraction is not None:
        payload["retraction"] = {"provenance": relation.retraction.provenance, "at": relation.retraction.at}
    if relation.reopenings:  # written only when nonempty, so a never-reopened file is byte-identical to 017's
        payload["reopenings"] = [
            {"ending": _ending(r.ending), "provenance": r.provenance, "at": r.at} for r in relation.reopenings]
    return {**thaw(relation.extras), **payload}  # type: ignore[dict-item]


# -- decoding ---------------------------------------------------------------------------------------

def _object(value: object, label: str, allowed: frozenset[str], required: frozenset[str]) -> Mapping:
    if not isinstance(value, Mapping) or not all(type(key) is str for key in value):
        raise _invalid(f"{label} must be a JSON object")
    if set(value) - allowed:
        raise _invalid(f"{label} has unknown fields {sorted(set(value) - allowed)}")
    if required - set(value):
        raise _invalid(f"{label} is missing {sorted(required - set(value))}")
    return value


def _decode_moment(value: object, label: str) -> Moment:
    data = _object(value, label, _MOMENT, frozenset({"sequence"}))
    try:
        return Moment(data["sequence"], data.get("scene_id"), data.get("turn_id"))
    except (KnowledgeError, ValueError, TypeError):
        raise _invalid(f"{label} is not a valid moment") from None


def _endpoint(text: object, label: str) -> InstanceRef:
    try:
        return InstanceRef.parse(text)
    except ValueError:
        raise _invalid(f"{label} is not an instance reference") from None


def _supported_endpoints(payload: Mapping, campaign_id: str) -> tuple[InstanceRef, ...]:
    found = []
    for key in ("subject", "object"):
        try:
            ref = InstanceRef.parse(payload.get(key))
        except ValueError:
            continue
        if ref.campaign_id == campaign_id:
            found.append(ref)
    return tuple(found)


def relation_from_payload(ref: RelationRef, schema: str, payload: object) -> Relation | UnsupportedRelation:
    """Decode a relation payload; refuse with ``RelationCodecError`` rather than guess."""
    if not isinstance(payload, Mapping) or not all(type(key) is str for key in payload):
        raise _invalid("a relation payload must be a JSON object")
    if set(payload) & ADDRESS_KEYS:
        raise RelationCodecError(f"the envelope owns {sorted(set(payload) & ADDRESS_KEYS)}", "address_in_payload")
    if schema != RELATION_SCHEMA:
        raise RelationCodecError(f"schema {schema!r} is not {RELATION_SCHEMA}", "unsupported_schema")
    predicate = payload.get("predicate")
    if type(predicate) is not str:
        raise _invalid("predicate must be text")
    if predicate not in PREDICATES:  # written by a newer version: keep it, never interpret it
        try:
            raw = JsonObject(payload)
        except JsonValueError:
            raise _invalid("the payload is not JSON") from None
        return UnsupportedRelation(ref, predicate, raw, _supported_endpoints(payload, ref.campaign_id))
    missing = sorted(_REQUIRED - set(payload))
    if missing:
        raise _invalid(f"missing {missing}")
    subject, obj = _endpoint(payload["subject"], "subject"), _endpoint(payload["object"], "object")
    since = _decode_moment(payload["since"], "since")
    ending = retraction = None
    if payload.get("ending") is not None:
        ending = _decode_ending(payload["ending"], "ending")
    reopenings = _decode_reopenings(payload["reopenings"]) if "reopenings" in payload else ()
    if payload.get("retraction") is not None:
        data = _object(payload["retraction"], "retraction", frozenset({"provenance", "at"}),
                       frozenset({"provenance", "at"}))
        retraction = _build(lambda: Retraction(data["provenance"], data["at"]))
    try:
        extras = JsonObject({key: value for key, value in payload.items() if key not in FIELDS})
    except JsonValueError:
        raise _invalid("unknown fields are not JSON") from None
    return _build(lambda: Relation(ref, predicate, subject, obj, since, payload["provenance"],
                                   payload["recorded_at"], ending, retraction, extras, reopenings))


_ENDING = frozenset({"until", "provenance", "at"})
_REOPENING = frozenset({"ending", "provenance", "at"})


def _decode_ending(value: object, label: str) -> Ending:
    data = _object(value, label, _ENDING, _ENDING)
    return _build(lambda: Ending(_decode_moment(data["until"], f"{label}.until"), data["provenance"], data["at"]))


def _decode_reopenings(value: object) -> tuple[Reopening, ...]:
    """A nonempty list in the order the reopenings were made; an explicit empty list is not canonical."""
    if not isinstance(value, (list, tuple)) or not value:  # a stored payload is frozen: lists arrive as tuples
        raise _invalid("reopenings must be a nonempty list")
    found = []
    for index, item in enumerate(value):
        data = _object(item, f"reopenings[{index}]", _REOPENING, _REOPENING)
        ending = _decode_ending(data["ending"], f"reopenings[{index}].ending")
        found.append(_build(lambda: Reopening(ending, data["provenance"], data["at"])))
    return tuple(found)  # list order is the order they were made; clock text is never compared


def _build(make):  # type: ignore[no-untyped-def]
    """Model validation errors become codec errors with the same reason, so a bad file is undecodable."""
    try:
        return make()
    except GraphError as error:
        reason = error.reason
    raise RelationCodecError(f"the relation is invalid: {reason}", reason)

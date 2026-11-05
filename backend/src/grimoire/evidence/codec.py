"""Payload codec for stored events, evidence and the knowledge log (Sprint 020).

One canonical JSON form per 012 record. The store's envelope owns the address
(``KnowledgeRef``: campaign, family and id) and the schema id, so a payload never repeats
them. Decoding is strict and accepts only the form ``encode`` writes. Lists keep 012's tuple
order, and defaults are omitted. Every refusal is an ``EvidenceCodecError`` naming a reason and
the record, never payload text, and it is raised outside any ``except`` block, so neither its
cause nor its context can carry a rejected value.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from grimoire.core.identity import ChatRef, InstanceRef, KnowledgeRef, validate_segment
from grimoire.core.knowledge import (
    Belief, Disclosure, Event, KnowledgeError, Moment, Perception, Proposition, Report, Ruling, Subject,
)
from grimoire.graph import ClaimShape, GraphError

TEXT_LIMIT = 20_000
LABEL_LIMIT = 200
BATCH_LIMIT = 500
BATCH_SCHEMA = "grimoire.knowledge-batch/1"

Record = Event | Proposition | Ruling | Perception | Report | Belief | Disclosure

SCHEMAS: Mapping[type, str] = MappingProxyType({
    Event: "grimoire.event/1", Proposition: "grimoire.proposition/1", Ruling: "grimoire.ruling/1",
    Perception: "grimoire.perception/1", Report: "grimoire.report/1", Belief: "grimoire.belief/1",
    Disclosure: "grimoire.disclosure/1",
})
FAMILIES: Mapping[type, str] = MappingProxyType({
    Event: "event", Proposition: "proposition", Ruling: "ruling", Perception: "evidence", Report: "evidence",
    Belief: "belief", Disclosure: "disclosure",
})
#: a proposition written with its 017 claim shape (an extra ``claim`` key); unshaped ones stay ``/1``
CLAIMED_PROPOSITION_SCHEMA = "grimoire.proposition/2"
_CLASS_OF_SCHEMA = {schema: cls for cls, schema in SCHEMAS.items()}
_CLASS_OF_SCHEMA[CLAIMED_PROPOSITION_SCHEMA] = Proposition
_CLAIM_KEYS = frozenset({"predicate", "subject", "object"})
_ID_FIELD = {Event: "event_id", Proposition: "proposition_id", Ruling: "ruling_id", Perception: "evidence_id",
             Report: "evidence_id", Belief: "belief_id", Disclosure: "disclosure_id"}
_ETAG = re.compile(r"sha256:[0-9a-f]{64}")


class EvidenceCodecError(ValueError):
    """A record or batch this version cannot store or read; ``reason`` is machine-readable.

    ``record`` is the record's ``"<family>:<id>"`` name, ``"batch"`` for a batch that could not be
    built, or ``None``; the message holds nothing else.
    """

    def __init__(self, reason: str, record: str | None = None) -> None:
        self.reason = reason
        self.record = record
        super().__init__(f"{reason}: {record}" if record else reason)

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, self.record))


class _Refused(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


# -- field kinds ------------------------------------------------------------------------------
# (attribute, kind, required). Optional fields are omitted when they hold their default.

_SPEC: Mapping[type, tuple[tuple[str, str, bool], ...]] = MappingProxyType({
    Event: (("summary", "text", True), ("moment", "moment", True), ("provenance", "label", True),
            ("participants", "actors", False)),
    Proposition: (("text", "text", True), ("moment", "moment", True), ("provenance", "label", True),
                  ("about", "subjects", False)),
    Ruling: (("proposition_id", "id", True), ("standing", "word", True), ("moment", "moment", True),
             ("provenance", "label", True), ("grounds", "subjects", False)),
    Perception: (("holder", "actor", True), ("event_id", "id", True), ("channel", "label", True),
                 ("percept", "text", True), ("moment", "moment", True), ("provenance", "label", True),
                 ("confirmed", "bool", True)),
    Report: (("holder", "actor", True), ("proposition_id", "id", True), ("channel", "label", True),
             ("moment", "moment", True), ("provenance", "label", True), ("confirmed", "bool", True),
             ("sender", "actor", False), ("attributed_to", "actor", False), ("addressed", "flag", False),
             ("sent_at", "moment", False)),
    Belief: (("holder", "actor", True), ("proposition_id", "id", True), ("stance", "word", True),
             ("moment", "moment", True), ("authored_by", "label", True), ("evidence_ids", "ids", True),
             ("supersedes", "id", False), ("contribution_id", "label", False)),
    Disclosure: (("event_id", "id", True), ("moment", "moment", True), ("provenance", "label", True)),
})

_EMPTY = (None, False, ())


def _storable(text: str) -> str:
    """Text that UTF-8 can hold (no lone surrogate), else ``invalid_field``."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise _Refused("invalid_field") from None
    return text


def _encode_value(kind: str, value: object) -> object:
    if isinstance(value, str):
        _storable(value)
    if kind == "moment":
        assert isinstance(value, Moment)
        moment: dict[str, object] = {"sequence": value.sequence}
        for label in ("scene_id", "turn_id"):
            text = getattr(value, label)
            if text is not None:
                if len(text) > LABEL_LIMIT:
                    raise _Refused("too_long")
                moment[label] = _storable(text)
        return moment
    if kind in ("actor",):
        return str(value)
    if kind == "actors":
        return [str(item) for item in value]  # type: ignore[attr-defined]
    if kind == "subjects":
        return [{"kind": item.kind, "ref": item.ref} for item in value]  # type: ignore[attr-defined]
    if kind == "ids":
        return list(value)  # type: ignore[call-overload]
    if kind == "text" and len(value) > TEXT_LIMIT:  # type: ignore[arg-type]
        raise _Refused("too_long")
    if kind == "label" and len(value) > LABEL_LIMIT:  # type: ignore[arg-type]
        raise _Refused("too_long")
    return value


def _check_claim(record: Record, claim: object) -> ClaimShape:
    """``claim`` as the shape of ``record``: same campaign and proposition, endpoints overt (017)."""
    if type(claim) is not ClaimShape or not isinstance(record, Proposition):
        raise _Refused("invalid_claim")
    if claim.campaign_id != record.campaign_id:
        raise _Refused("campaign_mismatch")
    if claim.proposition_id != record.proposition_id:
        raise _Refused("invalid_claim")
    overt = set(record.about)
    if not all(Subject("instance", str(end)) in overt for end in (claim.subject, claim.object)):
        raise _Refused("not_overt")
    return claim


def _encode_payload(record: Record, chat: ChatRef | None, claim: ClaimShape | None = None) -> dict[str, object]:
    payload: dict[str, object] = {}
    for attribute, kind, required in _SPEC[type(record)]:
        value = getattr(record, attribute)
        if not required and value in _EMPTY:
            continue
        payload[attribute] = _encode_value(kind, value)
    if claim is not None:
        _check_claim(record, claim)
        payload["claim"] = {"predicate": claim.predicate, "subject": _storable(str(claim.subject)),
                            "object": _storable(str(claim.object))}
    if chat is not None:
        if not isinstance(chat, ChatRef):
            raise _Refused("invalid_field")
        if chat.campaign_id != record.campaign_id:
            raise _Refused("campaign_mismatch")
        payload["chat"] = str(chat)
    return payload


def _attempt(work: Callable[[], object]) -> tuple[bool, object]:
    """Run ``work``; a refusal becomes a stored reason so it can be raised outside the handler."""
    try:
        return True, work()
    except _Refused as refused:
        reason = refused.reason
    except (KnowledgeError, GraphError) as error:
        reason = error.reason
    except (ValueError, TypeError, KeyError, AttributeError):
        reason = "invalid_field"
    return False, reason


def record_ref(record: Record) -> KnowledgeRef:
    return KnowledgeRef(record.campaign_id, FAMILIES[type(record)], getattr(record, _ID_FIELD[type(record)]))


def encode(record: Record, chat: ChatRef | None = None,
           claim: ClaimShape | None = None) -> tuple[KnowledgeRef, str, dict[str, object]]:
    """The address, schema id and canonical payload of one 012 record.

    ``claim`` is a proposition's 017 shape, stored with it under ``grimoire.proposition/2``.
    """
    if type(record) not in SCHEMAS:
        raise EvidenceCodecError("invalid_record")
    ref = record_ref(record)
    ok, outcome = _attempt(lambda: _encode_payload(record, chat, claim))
    if not ok:
        raise EvidenceCodecError(outcome, ref.name)  # type: ignore[arg-type]
    schema = CLAIMED_PROPOSITION_SCHEMA if claim is not None else SCHEMAS[type(record)]
    return ref, schema, outcome  # type: ignore[return-value]


# -- decoding ---------------------------------------------------------------------------------

def _text(value: object, limit: int) -> str:
    if type(value) is not str or len(value) > limit:
        raise _Refused("invalid_field")
    return value


def _sequence(value: object) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise _Refused("invalid_field")
    return tuple(value)


def _decode_moment(value: object) -> Moment:
    if not isinstance(value, Mapping) or not set(value) <= {"sequence", "scene_id", "turn_id"}:
        raise _Refused("invalid_field")
    if type(value.get("sequence")) is not int:
        raise _Refused("invalid_field")
    labels = {label: _text(value[label], LABEL_LIMIT) for label in ("scene_id", "turn_id") if label in value}
    return Moment(value["sequence"], **labels)


def _decode_value(kind: str, value: object) -> object:
    if kind == "text":
        return _text(value, TEXT_LIMIT)
    if kind in ("label", "word", "id"):
        return _text(value, LABEL_LIMIT)
    if kind == "moment":
        return _decode_moment(value)
    if kind == "actor":
        return InstanceRef.parse(_text(value, LABEL_LIMIT * 4))
    if kind == "actors":
        return tuple(InstanceRef.parse(_text(item, LABEL_LIMIT * 4)) for item in _sequence(value))
    if kind == "ids":
        return tuple(_text(item, LABEL_LIMIT) for item in _sequence(value))
    if kind == "subjects":
        subjects = []
        for item in _sequence(value):
            if not isinstance(item, Mapping) or set(item) != {"kind", "ref"}:
                raise _Refused("invalid_field")
            subjects.append(Subject(_text(item["kind"], LABEL_LIMIT), _text(item["ref"], LABEL_LIMIT * 4)))
        return tuple(subjects)
    if kind in ("bool", "flag"):
        if type(value) is not bool:
            raise _Refused("invalid_field")
        return value
    raise AssertionError(kind)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(child) for child in value]
    return value


def _same(left: object, right: object) -> bool:
    """Exact JSON equality: ``True`` is not ``1`` and ``1.0`` is not ``1``."""
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return type(left) is type(right) and left == right


def _decode_claim(ref: KnowledgeRef, value: object) -> ClaimShape:
    if not isinstance(value, Mapping) or set(value) != _CLAIM_KEYS:
        raise _Refused("invalid_field")
    predicate = _text(value["predicate"], LABEL_LIMIT)
    subject, object_ = (InstanceRef.parse(_text(value[end], LABEL_LIMIT * 4)) for end in ("subject", "object"))
    return ClaimShape(ref.campaign_id, ref.record_id, predicate, subject, object_)


def _decode(ref: KnowledgeRef, schema: object,
            payload: object) -> tuple[Record, ChatRef | None, ClaimShape | None]:
    if not isinstance(schema, str) or schema not in _CLASS_OF_SCHEMA:
        raise _Refused("unsupported_schema")
    cls = _CLASS_OF_SCHEMA[schema]
    if FAMILIES[cls] != ref.family:
        raise _Refused("schema_mismatch")
    if not isinstance(payload, Mapping):
        raise _Refused("invalid_field")
    spec = _SPEC[cls]
    claimed = schema == CLAIMED_PROPOSITION_SCHEMA
    known = {attribute for attribute, _, _ in spec} | {"chat"} | ({"claim"} if claimed else set())
    if "campaign_id" in payload or _ID_FIELD[cls] in payload:  # no spec lists its own record's id
        raise _Refused("identity_in_payload")
    if not set(payload) <= known:
        raise _Refused("unknown_field")
    if claimed and "claim" not in payload:
        raise _Refused("missing_field")
    fields: dict[str, object] = {}
    for attribute, kind, required in spec:
        if attribute not in payload:
            if required:
                raise _Refused("missing_field")
            continue
        fields[attribute] = _decode_value(kind, payload[attribute])
    chat = None
    if "chat" in payload:
        chat = ChatRef.parse(_text(payload["chat"], LABEL_LIMIT * 4))
        if chat.campaign_id != ref.campaign_id:
            raise _Refused("campaign_mismatch")
    values = (("campaign_id", ref.campaign_id), (_ID_FIELD[cls], ref.record_id))
    record = _construct(cls, dict(values), fields)
    claim = _check_claim(record, _decode_claim(ref, payload["claim"])) if claimed else None
    if not _same(_plain(payload), _encode_payload(record, chat, claim)):
        raise _Refused("non_canonical")
    return record, chat, claim


def _construct(cls: type, identity: dict[str, object], fields: dict[str, object]) -> Record:
    try:
        return cls(**identity, **fields)
    except TypeError:
        raise _Refused("invalid_field") from None


def decode_claimed(ref: KnowledgeRef, schema: object,
                   payload: object) -> tuple[Record, ChatRef | None, ClaimShape | None]:
    """The 012 record, its ``chat`` provenance and (for a ``/2`` proposition) its claim shape."""
    if not isinstance(ref, KnowledgeRef) or ref.family == "batch":
        raise EvidenceCodecError("invalid_record")
    ok, outcome = _attempt(lambda: _decode(ref, schema, payload))
    if not ok:
        raise EvidenceCodecError(outcome, ref.name)  # type: ignore[arg-type]
    return outcome  # type: ignore[return-value]


def decode(ref: KnowledgeRef, schema: object, payload: object) -> tuple[Record, ChatRef | None]:
    """The 012 record and its ``chat`` provenance, or ``EvidenceCodecError``.

    A shaped proposition (``grimoire.proposition/2``) is refused with ``claimed_proposition``, so no
    caller can read a proposition without the shape it was written with; use ``decode_claimed``.
    """
    record, chat, claim = decode_claimed(ref, schema, payload)
    if claim is not None:
        raise EvidenceCodecError("claimed_proposition", ref.name)
    return record, chat


# -- chain keys and the batch log ---------------------------------------------------------------

ChainKey = tuple[str, ...]


def chain_key(record: Record) -> ChainKey | None:
    """What a record's meaning depends on beside itself: one proposition's rulings, one (holder,
    proposition)'s beliefs, or one event's disclosures. Quarantine removes a whole chain."""
    if isinstance(record, Ruling):
        return ("ruling", record.proposition_id)
    if isinstance(record, Belief):
        return ("belief", str(record.holder), record.proposition_id)
    if isinstance(record, Disclosure):
        return ("disclosure", record.event_id)
    return None


@dataclass(frozen=True)
class BatchEntry:
    """One record a batch accounts for: its file's etag, and its chain key without reading it."""

    etag: str
    proposition: str | None = None
    holder: str | None = None
    event: str | None = None

    def key(self, family: str) -> ChainKey | None:
        if family == "ruling":
            return ("ruling", self.proposition)  # type: ignore[return-value]
        if family == "belief":
            return ("belief", self.holder, self.proposition)  # type: ignore[return-value]
        if family == "disclosure":
            return ("disclosure", self.event)  # type: ignore[return-value]
        return None


@dataclass(frozen=True)
class Batch:
    seq: int
    previous: str | None
    entries: Mapping[str, BatchEntry]


def batch_entry(record: Record, etag: str) -> BatchEntry:
    if isinstance(record, Ruling):
        return BatchEntry(etag, proposition=record.proposition_id)
    if isinstance(record, Belief):
        return BatchEntry(etag, proposition=record.proposition_id, holder=str(record.holder))
    if isinstance(record, Disclosure):
        return BatchEntry(etag, event=record.event_id)
    return BatchEntry(etag)


_ENTRY_KEYS = {"ruling": {"etag", "proposition"}, "belief": {"etag", "proposition", "holder"},
               "disclosure": {"etag", "event"}}


def _entry_payload(entry: BatchEntry) -> dict[str, object]:
    payload: dict[str, object] = {"etag": entry.etag}
    for label in ("proposition", "holder", "event"):
        if getattr(entry, label) is not None:
            payload[label] = getattr(entry, label)
    return payload


def _batch_payload(seq: int, previous: str | None, entries: Mapping[str, BatchEntry]) -> dict[str, object]:
    payload: dict[str, object] = {"seq": seq,
                                  "records": {name: _entry_payload(entries[name]) for name in sorted(entries)}}
    if previous is not None:
        payload["previous"] = previous
    return payload


def _decode_entry(campaign_id: str, name: object, raw: object) -> BatchEntry:
    ref = KnowledgeRef.from_name(campaign_id, name)
    if ref.family == "batch" or not isinstance(raw, Mapping):
        raise _Refused("invalid_field")
    if set(raw) != _ENTRY_KEYS.get(ref.family, {"etag"}):
        raise _Refused("invalid_field")
    if type(raw["etag"]) is not str or not _ETAG.fullmatch(raw["etag"]):
        raise _Refused("invalid_field")
    values = {label: _text(raw[label], LABEL_LIMIT * 4) for label in ("proposition", "holder", "event") if label in raw}
    for label in ("proposition", "event"):  # a chain key is an id, as in 012
        if label in values:
            validate_segment(values[label], label)
    if "holder" in values and InstanceRef.parse(values["holder"]).campaign_id != campaign_id:
        raise _Refused("campaign_mismatch")
    return BatchEntry(raw["etag"], **values)


def _decode_batch(ref: KnowledgeRef, schema: object, payload: object) -> Batch:
    if schema != BATCH_SCHEMA:
        raise _Refused("unsupported_schema")
    if not isinstance(payload, Mapping):
        raise _Refused("invalid_field")
    if not set(payload) <= {"seq", "previous", "records"}:
        raise _Refused("unknown_field")
    seq, previous, records = payload.get("seq"), payload.get("previous"), payload.get("records")
    if type(seq) is not int or seq != ref.seq:
        raise _Refused("invalid_field")
    if (seq == 1) != ("previous" not in payload):
        raise _Refused("invalid_field")
    if previous is not None and (type(previous) is not str or not _ETAG.fullmatch(previous)):
        raise _Refused("invalid_field")
    if not isinstance(records, Mapping) or not 1 <= len(records) <= BATCH_LIMIT:
        raise _Refused("invalid_field")
    entries = {name: _decode_entry(ref.campaign_id, name, raw) for name, raw in records.items()}
    if not _same(_plain(payload), _batch_payload(seq, previous, entries)):  # type: ignore[arg-type]
        raise _Refused("non_canonical")
    return Batch(seq, previous, MappingProxyType(entries))  # type: ignore[arg-type]


def encode_batch(campaign_id: str, seq: int, previous: str | None,
                 entries: Mapping[str, BatchEntry]) -> tuple[KnowledgeRef, str, dict[str, object]]:
    def build() -> tuple[KnowledgeRef, dict[str, object]]:
        ref = KnowledgeRef.batch(campaign_id, seq)
        payload = _batch_payload(seq, previous, entries)
        _decode_batch(ref, BATCH_SCHEMA, payload)  # write only what reads back
        return ref, payload

    ok, outcome = _attempt(build)
    if not ok:  # named "batch": the ref itself may be what is invalid
        raise EvidenceCodecError(outcome, "batch")  # type: ignore[arg-type]
    ref, payload = outcome  # type: ignore[misc]
    return ref, BATCH_SCHEMA, payload


def decode_batch(ref: KnowledgeRef, schema: object, payload: object) -> Batch:
    if not isinstance(ref, KnowledgeRef) or ref.family != "batch":
        raise EvidenceCodecError("invalid_record")
    ok, outcome = _attempt(lambda: _decode_batch(ref, schema, payload))
    if not ok:
        raise EvidenceCodecError(outcome, ref.name)  # type: ignore[arg-type]
    return outcome  # type: ignore[return-value]

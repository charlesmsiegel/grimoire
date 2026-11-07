"""Accepted-event and wrap-up payloads to and from plain JSON-compatible data (Sprint 021).

The store's ``grimoire.scope-record/1`` envelope owns the address and the schema id, so neither is
repeated inside a payload (``identity_in_payload``). Unknown *top-level* keys survive in ``extras``;
inside a known structure an unknown, missing or malformed field is refused. Decoding re-checks the
record's structural invariants, so a hand edit that breaks them makes the file undecodable rather
than silently trusted. Re-encoding is canonical: decode(encode(x)) == x.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.ability_use import AbilityAttempt
from grimoire.core.identity import AcceptedRef, ChatRef, InstanceRef, RelationRef, WrapUpRef
from grimoire.core.json_value import JsonObject, JsonValueError, thaw
from grimoire.core.knowledge import KnowledgeError, Moment
from grimoire.holdings import HoldingsError, Line

from .model import (
    AcceptedEvent, AcceptedOperation, ContributionSnapshot, ControlSnapshot, EffectError, EffectProposal,
    EndorsementRecord, Op, Receipt, Revocation, SceneWrapUp, Skipped,
)
from .parts import AbilityCharge, AcceptedHoldings, ActedLine, EffectHoldings

#: an event with no attempt, holdings or ability use is written exactly as before the next slice
ACCEPTED_SCHEMA = "grimoire.accepted-event/0"
#: an event that changes holdings or charges ability uses
ACCEPTED_SCHEMA_V1 = "grimoire.accepted-event/1"
#: an event that records what it was accepted after (028); it may hold the /1 parts. A build before it refuses
#: the schema rather than read ``after`` as an unknown field and lose the event's place
ACCEPTED_SCHEMA_V2 = "grimoire.accepted-event/2"
WRAPUP_SCHEMA = "grimoire.scene-wrapup/0"
_ACCEPTED_IDENTITY = frozenset({"ref", "address", "campaign_id", "event_id", "schema"})
_WRAPUP_IDENTITY = frozenset({"ref", "address", "campaign_id", "wrapup_id", "schema"})
ACCEPTED_FIELDS = frozenset({"chat", "moment", "summary", "proposed_by", "operations", "contributions",
                             "endorsements", "controls", "committed_by", "accepted_at", "ordinal", "receipts",
                             "revocation"})
#: the next slice's fields: only a /1 payload may hold them (a /0 one holding them is refused)
PART_FIELDS = frozenset({"attempt_id", "holdings", "abilities"})
#: only a /2 payload holds it, and every /2 payload does
AFTER_FIELD = "after"
WRAPUP_FIELDS = frozenset({"chat", "scene_id", "through", "summary", "cites", "written_by", "written_at"})
_MOMENT = frozenset({"sequence", "scene_id", "turn_id"})
_OP = frozenset({"kind", "predicate", "subject", "object", "agent", "domain", "backed_by"})
_CONTRIBUTION = frozenset({"contribution_id", "actor_id", "submitted_by", "kind", "scene_id", "turn_id", "text"})
_ENDORSEMENT = frozenset({"contribution_id", "principal", "fingerprint"})
_CONTROL = frozenset({"actor_id", "controller_id"})
_RECEIPT = frozenset({"op", "relation"})
_REVOCATION = frozenset({"reason", "at", "committed_by", "endorsements", "skipped"})
_REVOCATION_V1 = _REVOCATION | {"holdings_skipped", "holdings_seqs"}
_HOLDINGS = frozenset({"lines", "note", "op_id", "seqs"})
_HOLDINGS_REQUIRED = frozenset({"lines", "op_id", "seqs"})
_LINE_FIELDS = {"add": ("amount",), "consume": ("amount",), "adjust": ("seen", "to"), "move": ("amount", "target")}
_LINE_OPTIONAL = frozenset({"agent", "backed_by"})
_ABILITY = frozenset({"attempt_id", "actor", "ability_id", "needs", "provenance", "effect_id", "conditions_met",
                      "classification", "ruling_id", "charges", "domain"})
_ABILITY_REQUIRED = _ABILITY - {"effect_id", "ruling_id"}
_SKIPPED = frozenset({"op", "why"})
#: a proposal as 035 keeps it on its turn before accepting it (030's obligation): no domain, receipt or key yet
_PROPOSAL = frozenset({"chat", "moment", "summary", "operations", "proposed_by", "attempt_id", "holdings"})
_PROPOSAL_REQUIRED = frozenset({"chat", "moment", "summary", "operations", "proposed_by"})
_PROPOSED_OP = _OP - {"domain"}
_PROPOSED_HOLDINGS = frozenset({"lines", "note"})


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


def _op(op: Op, domain: str | None) -> dict:
    """One operation; ``domain`` is ``None`` for a proposal's, which only acceptance decides."""
    data: dict = {"kind": op.kind, "predicate": op.predicate, "subject": str(op.subject), "object": str(op.object)}
    if domain is not None:
        data["domain"] = domain
    if op.agent is not None:
        data["agent"] = str(op.agent)
    if op.backed_by is not None:
        data["backed_by"] = op.backed_by
    return data


def _line(item: ActedLine, domain: str | None) -> dict:
    line = item.line
    data: dict = {"line": line.kind, "holder": str(line.holder), "item": line.item}
    if domain is not None:
        data["domain"] = domain
    if line.kind == "adjust":
        data.update(seen=line.seen, to=line.to)
    else:
        data["amount"] = line.amount
    if line.target is not None:
        data["target"] = str(line.target)
    if item.agent is not None:
        data["agent"] = str(item.agent)
    if item.backed_by is not None:
        data["backed_by"] = item.backed_by
    return data


def _seqs(seqs: Mapping[InstanceRef, int]) -> dict:
    return {str(holder): seq for holder, seq in sorted(seqs.items(), key=lambda pair: str(pair[0]))}


def _holdings(accepted: AcceptedHoldings) -> dict:
    part = accepted.holdings
    data: dict = {"lines": [_line(item, domain) for item, domain in zip(part.lines, accepted.domains)],
                  "op_id": accepted.op_id, "seqs": _seqs(accepted.seqs)}
    if part.note is not None:
        data["note"] = part.note
    return data


def _ability(charge: AbilityCharge) -> dict:
    attempt = charge.attempt
    data: dict = {"attempt_id": attempt.attempt_id, "actor": str(attempt.actor), "ability_id": attempt.ability_id,
                  "needs": sorted(attempt.needs), "provenance": attempt.provenance,
                  "conditions_met": sorted(attempt.conditions_met), "classification": charge.classification,
                  "charges": [list(pair) for pair in charge.charges], "domain": charge.domain}
    if attempt.effect_id is not None:
        data["effect_id"] = attempt.effect_id
    if charge.ruling_id is not None:
        data["ruling_id"] = charge.ruling_id
    return data


def accepted_schema(event: AcceptedEvent) -> str:
    """The schema an event is stored under: /2 when it records ``after``; else /0 unless it changes holdings or
    charges an ability use."""
    if event.after is not None:
        return ACCEPTED_SCHEMA_V2
    return ACCEPTED_SCHEMA_V1 if event.has_parts else ACCEPTED_SCHEMA


def _extras_clash(extras: JsonObject, identity: frozenset[str], fields: frozenset[str]) -> None:
    clash = sorted(set(extras) & (identity | fields))
    if clash:
        reason = "identity_in_payload" if set(clash) & identity else "invalid_field"
        raise EffectCodecError(f"extras may not use the keys {clash}", reason)


def accepted_to_payload(event: AcceptedEvent) -> dict:
    """The payload of ``event``, to be stored under ``accepted_schema(event)``."""
    _extras_clash(event.extras, _ACCEPTED_IDENTITY, ACCEPTED_FIELDS | PART_FIELDS | {AFTER_FIELD})
    payload: dict = {
        "chat": str(event.chat), "moment": _moment(event.moment), "summary": event.summary,
        "proposed_by": event.proposed_by, "operations": [_op(item.op, item.domain) for item in event.operations],
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
        if revocation.holdings_skipped is not None:
            payload["revocation"]["holdings_skipped"] = revocation.holdings_skipped
        if revocation.holdings_seqs:
            payload["revocation"]["holdings_seqs"] = _seqs(revocation.holdings_seqs)
    if event.attempt_id is not None:
        payload["attempt_id"] = event.attempt_id
    if event.holdings is not None:
        payload["holdings"] = _holdings(event.holdings)
    if event.abilities:
        payload["abilities"] = [_ability(charge) for charge in event.abilities]
    if event.after is not None:
        payload[AFTER_FIELD] = list(event.after)
    return {**thaw(event.extras), **payload}  # type: ignore[dict-item]


def proposal_to_payload(proposal: EffectProposal) -> dict:
    """A proposal as 035 keeps it on its turn, so a restart can present it again (030's obligation).

    Every field of ``EffectProposal`` but its campaign, which the chat names. Ability uses are refused
    (``not_in_this_slice``): 035 proposes none yet, and their 025 assessments have no stored form here.
    """
    if type(proposal) is not EffectProposal:
        raise _invalid("a proposal must be an EffectProposal")
    if proposal.abilities:
        raise _invalid("a proposal's ability uses are not stored in this slice", "not_in_this_slice")
    data: dict = {"chat": str(proposal.chat), "moment": _moment(proposal.moment), "summary": proposal.summary,
                  "operations": [_op(op, None) for op in proposal.operations],
                  "proposed_by": proposal.proposed_by}
    if proposal.attempt_id is not None:
        data["attempt_id"] = proposal.attempt_id
    if proposal.holdings is not None:
        part = proposal.holdings
        data["holdings"] = {"lines": [_line(item, None) for item in part.lines]}
        if part.note is not None:
            data["holdings"]["note"] = part.note
    return data


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


def _start(payload: object, schema: str, expected: str | frozenset[str], identity: frozenset[str]) -> Mapping:
    if not isinstance(payload, Mapping) or not all(type(key) is str for key in payload):
        raise _invalid("a payload must be a JSON object")
    if set(payload) & identity:
        raise EffectCodecError(f"the envelope owns {sorted(set(payload) & identity)}", "identity_in_payload")
    if schema not in (expected if isinstance(expected, frozenset) else {expected}):
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


def _ref_text(value: object, label: str) -> InstanceRef:
    if type(value) is not str:
        raise _invalid(f"{label} is not a valid reference")
    return _ref(InstanceRef.parse, value, label)


def _decode_line(value: object, label: str, *, domain: bool = True) -> tuple[ActedLine, object]:
    """One holdings line and its recorded domain (``domain=False``: a proposal's line, which has none yet)."""
    kind = value.get("line") if isinstance(value, Mapping) else None
    if type(kind) is not str or kind not in _LINE_FIELDS:
        raise _invalid(f"{label}.line is not a line kind")
    required = frozenset({"line", "holder", "item", *(("domain",) if domain else ()), *_LINE_FIELDS[kind]})
    data = _object(value, label, required | _LINE_OPTIONAL, required)
    _no_nulls(data, label)
    target = _ref_text(data["target"], f"{label}.target") if "target" in data else None
    holder = _ref_text(data["holder"], f"{label}.holder")
    for key in ("amount", "seen", "to"):
        if key in data and type(data[key]) is not int:
            raise _invalid(f"{label}.{key} must be an integer")
    try:
        line = Line(kind, holder, data["item"], amount=data.get("amount"), seen=data.get("seen"), to=data.get("to"),
                    target=target)
    except HoldingsError:
        raise _invalid(f"{label} is not a valid line") from None
    agent = None if "agent" not in data else _ref_text(data["agent"], f"{label}.agent")
    return _build(lambda: ActedLine(line, agent, data.get("backed_by"))), data.get("domain")


def _decode_seqs(value: object, label: str) -> dict[InstanceRef, int]:
    if not isinstance(value, Mapping) or not all(type(key) is str for key in value):
        raise _invalid(f"{label} must be a JSON object")
    found = {}
    for key, seq in value.items():
        if type(seq) is not int:
            raise _invalid(f"{label} holds a non-integer seq", "invalid_receipts")
        found[_ref_text(key, label)] = seq
    if list(value) != sorted(value):
        raise _invalid(f"{label} is not in canonical order")
    return found


def _decode_holdings(value: object) -> AcceptedHoldings:
    data = _object(value, "holdings", _HOLDINGS, _HOLDINGS_REQUIRED)
    _no_nulls(data, "holdings")
    decoded = [_decode_line(item, f"holdings.lines[{i}]") for i, item in enumerate(_list(data["lines"],
                                                                                           "holdings.lines"))]
    part = _build(lambda: EffectHoldings(tuple(line for line, _ in decoded), data.get("note")))
    seqs = _decode_seqs(data["seqs"], "holdings.seqs")
    return _build(lambda: AcceptedHoldings(part, tuple(domain for _, domain in decoded), data["op_id"], seqs))


def _names(value: object, label: str) -> frozenset[str]:
    items = _list(value, label)
    if not all(type(item) is str for item in items) or list(items) != sorted(set(items)):
        raise _invalid(f"{label} must be a sorted list of distinct names")
    return frozenset(items)


def _decode_ability(value: object, label: str) -> AbilityCharge:
    data = _object(value, label, _ABILITY, _ABILITY_REQUIRED)
    _no_nulls(data, label)
    actor = _ref_text(data["actor"], f"{label}.actor")
    needs = _names(data["needs"], f"{label}.needs")
    conditions = _names(data["conditions_met"], f"{label}.conditions_met")
    try:
        attempt = AbilityAttempt(data["attempt_id"], actor, data["ability_id"], needs, data["provenance"],
                                 data.get("effect_id"), conditions)
    except (ValueError, TypeError):
        raise _invalid(f"{label} is not a valid attempt") from None
    charges = [_list(pair, f"{label}.charges[{index}]")
               for index, pair in enumerate(_list(data["charges"], f"{label}.charges"))]
    return _build(lambda: AbilityCharge(attempt, data["classification"], data.get("ruling_id"), tuple(charges),
                                        data["domain"]))


def accepted_from_payload(ref: AcceptedRef, schema: str, payload: object) -> AcceptedEvent:
    """Decode an accepted-event payload; refuse with ``EffectCodecError`` rather than guess.

    A /0 payload holding any of the next slice's fields is refused (``invalid_field``) rather than read
    with those fields as extras: no older writer wrote them, and ignoring a holdings part would hide it.
    A /1 payload with no holdings and no ability use is refused too: /0 is that event's one form.
    """
    data = _start(payload, schema, frozenset({ACCEPTED_SCHEMA, ACCEPTED_SCHEMA_V1, ACCEPTED_SCHEMA_V2}),
                  _ACCEPTED_IDENTITY)
    v2 = schema == ACCEPTED_SCHEMA_V2
    v1 = schema == ACCEPTED_SCHEMA_V1 or v2
    if not v1 and set(data) & PART_FIELDS:
        raise _invalid(f"schema {ACCEPTED_SCHEMA} may not hold {sorted(set(data) & PART_FIELDS)}")
    if (AFTER_FIELD in data) != v2:  # never read as an extra: ignoring it would lose the event's place
        raise _invalid(f"{AFTER_FIELD} is held by every {ACCEPTED_SCHEMA_V2} payload and no other",
                       "invalid_field" if AFTER_FIELD in data else "missing_field")
    after = None
    if v2:
        after = _list(data[AFTER_FIELD], AFTER_FIELD)
        if not all(type(item) is str for item in after):
            raise _invalid(f"{AFTER_FIELD} must list event ids")
    missing = sorted((ACCEPTED_FIELDS - {"revocation"}) - set(data))
    if missing:
        raise _invalid(f"missing {missing}", "missing_field")
    if "attempt_id" in data and type(data["attempt_id"]) is not str:
        raise _invalid("attempt_id must be text")
    holdings = None if "holdings" not in data else _decode_holdings(data["holdings"])
    abilities = () if "abilities" not in data else tuple(
        _decode_ability(item, f"abilities[{index}]")
        for index, item in enumerate(_list(data["abilities"], "abilities")))
    if "abilities" in data and not abilities:
        raise _invalid("an empty abilities list is not canonical")
    if [a.attempt.attempt_id for a in abilities] != sorted(a.attempt.attempt_id for a in abilities):
        raise _invalid("abilities are not in canonical order")
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
        v = _object(data["revocation"], "revocation", _REVOCATION_V1 if v1 else _REVOCATION, _REVOCATION)
        _no_nulls(v, "revocation")
        skipped = []
        for index, item in enumerate(_list(v["skipped"], "revocation.skipped")):
            s = _object(item, f"revocation.skipped[{index}]", _SKIPPED, _SKIPPED)
            skipped.append(_build(lambda: Skipped(s["op"], s["why"])))
        endorsements = _decode_endorsements(v["endorsements"], "revocation.endorsements")
        undo_seqs = {} if "holdings_seqs" not in v else _decode_seqs(v["holdings_seqs"], "revocation.holdings_seqs")
        if "holdings_seqs" in v and not undo_seqs:
            raise _invalid("an empty holdings_seqs is not canonical")
        revocation = _build(lambda: Revocation(v["reason"], v["at"], v["committed_by"], endorsements,
                                               tuple(skipped), v.get("holdings_skipped"), undo_seqs))
    chat = _ref(ChatRef.parse, data["chat"], "chat")
    moment = _decode_moment(data["moment"], "moment")
    endorsements = _decode_endorsements(data["endorsements"], "endorsements")
    extras = _extras(data, ACCEPTED_FIELDS | PART_FIELDS | {AFTER_FIELD})
    event = _build(lambda: AcceptedEvent(
        ref, chat, moment, data["summary"], data["proposed_by"], operations, tuple(contributions), endorsements,
        tuple(controls), data["committed_by"], data["accepted_at"], data["ordinal"], tuple(receipts), revocation,
        extras, data.get("attempt_id"), holdings, abilities, None if after is None else tuple(after)))
    if accepted_schema(event) != schema:
        raise _invalid(f"an event with no holdings or ability use is stored as {ACCEPTED_SCHEMA}")  # one form
    return event


def proposal_from_payload(campaign_id: str, payload: object) -> EffectProposal:
    """A proposal kept by ``proposal_to_payload``, in ``campaign_id``. Strict at every level, so a hand edit
    that breaks it makes it unreadable (``EffectCodecError``) rather than another effect."""
    data = _object(payload, "proposal", _PROPOSAL, _PROPOSAL_REQUIRED)
    _no_nulls(data, "proposal")
    if type(data["chat"]) is not str:
        raise _invalid("proposal.chat is not a valid reference")
    chat = _ref(ChatRef.parse, data["chat"], "proposal.chat")
    moment = _decode_moment(data["moment"], "proposal.moment")
    operations = []
    for index, item in enumerate(_list(data["operations"], "proposal.operations")):
        label = f"proposal.operations[{index}]"
        op = _object(item, label, _PROPOSED_OP, _PROPOSED_OP - {"agent", "backed_by"})
        _no_nulls(op, label)
        agent = None if "agent" not in op else _ref_text(op["agent"], f"{label}.agent")
        subject, other = _ref_text(op["subject"], label), _ref_text(op["object"], label)
        operations.append(_build(lambda: Op(op["kind"], op["predicate"], subject, other, agent, op.get("backed_by"))))
    holdings = None
    if "holdings" in data:
        part = _object(data["holdings"], "proposal.holdings", _PROPOSED_HOLDINGS, frozenset({"lines"}))
        _no_nulls(part, "proposal.holdings")
        lines = [_decode_line(item, f"proposal.holdings.lines[{index}]", domain=False)[0]
                 for index, item in enumerate(_list(part["lines"], "proposal.holdings.lines"))]
        holdings = _build(lambda: EffectHoldings(tuple(lines), part.get("note")))
    return _build(lambda: EffectProposal(campaign_id, chat, moment, data["summary"], tuple(operations),
                                         data["proposed_by"], data.get("attempt_id"), holdings))


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

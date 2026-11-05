"""The ``possession`` record inside an item's ``grimoire.entity/0`` payload: strict, campaign-relative.

The store's envelope owns the item's address. Inside the record, every party is written
``<kind>/<instance_id>`` within the item's own campaign, so a copy of the campaign under another id
(a future fork, 054) rebinds every party with it and nothing is keyed by a name. Encoding is
canonical: fits sorted, log by rev, absent fields omitted. Unknown top-level fields of the record are
kept verbatim; inside known structures, unknowns are refused with a reason.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.core.json_value import thaw

from .model import (
    IDENTITY_KEYS, POSSESSION_SCHEMA, RECORD_FIELDS, LogEntry, Origin, Possession, PossessionCodecError,
    PossessionError, State, Step,
)

_REQUIRED = frozenset({"schema", "rev", "holder", "origin", "evicted_through", "log"})
_ORIGIN = frozenset({"op", "at", "holder", "owner", "name", "source", "fits", "note", "cause"})
_ORIGIN_REQUIRED = frozenset({"op", "at", "holder"})
_ENTRY = frozenset({"op", "rev", "at", "steps", "before", "after", "note", "cause"})
_ENTRY_REQUIRED = frozenset({"op", "rev", "at", "steps", "before", "after"})
_STATE = frozenset({"holder", "owner", "equipped"})
_STEP_FIELDS = {"move": ("to",), "own": ("to",), "equip": ("by", "slot"), "unequip": ()}


def party_text(party: InstanceRef) -> str:
    return f"{party.kind}/{party.instance_id}"


def _state_payload(state: State) -> dict:
    payload: dict = {"holder": party_text(state.holder)}
    if state.owner is not None:
        payload["owner"] = party_text(state.owner)
    if state.equipped is not None:
        payload["equipped"] = state.equipped
    return payload


def _step_payload(step: Step) -> dict:
    payload: dict = {"step": step.kind}
    if step.kind == "move":
        payload["to"] = party_text(step.to)  # type: ignore[arg-type]
    elif step.kind == "own":
        payload["to"] = None if step.to is None else party_text(step.to)
    elif step.kind == "equip":
        payload.update(by=party_text(step.by), slot=step.slot)  # type: ignore[arg-type]
    return payload


def entry_to_payload(entry: LogEntry) -> dict:
    payload: dict = {"op": entry.op, "rev": entry.rev, "at": entry.at,
                     "steps": [_step_payload(step) for step in entry.steps],
                     "before": _state_payload(entry.before), "after": _state_payload(entry.after)}
    if entry.note is not None:
        payload["note"] = entry.note
    if entry.cause is not None:
        payload["cause"] = entry.cause
    return payload


def _origin_payload(origin: Origin) -> dict:
    payload: dict = {"op": origin.op, "at": origin.at, "holder": party_text(origin.holder)}
    if origin.owner is not None:
        payload["owner"] = party_text(origin.owner)
    if origin.name is not None:
        payload["name"] = origin.name
    if origin.source is not None:
        payload["source"] = str(origin.source)
    if origin.fits:
        payload["fits"] = list(origin.fits)
    if origin.note is not None:
        payload["note"] = origin.note
    if origin.cause is not None:
        payload["cause"] = origin.cause
    return payload


def possession_to_payload(possession: Possession) -> dict:
    """The record as written under the instance payload's ``possession`` key."""
    payload: dict = dict(thaw(possession.extras))  # type: ignore[arg-type]
    payload.update(schema=POSSESSION_SCHEMA, rev=possession.rev, evicted_through=possession.evicted_through,
                   origin=_origin_payload(possession.origin), log=[entry_to_payload(e) for e in possession.log],
                   **_state_payload(possession.state))
    if possession.fits:
        payload["fits"] = list(possession.fits)
    return payload


# -- decoding ------------------------------------------------------------------------------------

def _fail(reason: str, detail: str) -> PossessionCodecError:
    return PossessionCodecError(reason, detail)


def _object(value: object, label: str, allowed: frozenset[str], required: frozenset[str]) -> Mapping:
    if not isinstance(value, Mapping) or not all(type(key) is str for key in value):
        raise _fail("invalid_field", f"{label} must be a JSON object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _fail("unknown_field", f"{label} has unknown fields {unknown}")
    missing = sorted(required - set(value))
    if missing:
        raise _fail("invalid_field", f"{label} is missing {missing}")
    return value


def _list(value: object, label: str) -> list:
    if not isinstance(value, (list, tuple)):
        raise _fail("invalid_field", f"{label} must be a list")
    return list(value)


def _party(text: object, campaign: str, label: str) -> InstanceRef:
    parts = text.split("/") if type(text) is str else []
    if len(parts) != 2:
        raise _fail("invalid_field", f"{label} must be <kind>/<instance_id>, not {text!r}")
    try:
        return InstanceRef(campaign, parts[0], parts[1])
    except ValueError as error:
        raise _fail("invalid_field", f"{label}: {error}") from None


def _optional_party(value: object, campaign: str, label: str) -> InstanceRef | None:
    return None if value is None else _party(value, campaign, label)


def _state(value: object, campaign: str, label: str) -> State:
    data = _object(value, label, _STATE, frozenset({"holder"}))
    return State(_party(data["holder"], campaign, f"{label}.holder"),
                 _optional_party(data.get("owner"), campaign, f"{label}.owner"),
                 data.get("equipped"))  # type: ignore[arg-type]


def _step(value: object, campaign: str, label: str) -> Step:
    kind = value.get("step") if isinstance(value, Mapping) else None
    if type(kind) is not str or kind not in _STEP_FIELDS:  # a hand edit can put any JSON value here
        raise _fail("invalid_field", f"{label}.step must be one of {sorted(_STEP_FIELDS)}, not {kind!r}")
    fields = frozenset({"step", *_STEP_FIELDS[kind]})  # type: ignore[index]
    data = _object(value, label, fields, fields)
    if kind == "equip":
        return Step.equip(_party(data["by"], campaign, f"{label}.by"), data["slot"])  # type: ignore[arg-type]
    if kind == "move":
        return Step.move(_party(data["to"], campaign, f"{label}.to"))
    if kind == "own":
        return Step.own(_optional_party(data["to"], campaign, f"{label}.to"))
    return Step.unequip()


def _entry(value: object, campaign: str, label: str) -> LogEntry:
    data = _object(value, label, _ENTRY, _ENTRY_REQUIRED)
    steps = tuple(_step(step, campaign, f"{label}.steps[{n}]")
                  for n, step in enumerate(_list(data["steps"], f"{label}.steps")))
    return LogEntry(data["op"], data["rev"], data["at"], steps,  # type: ignore[arg-type]
                    _state(data["before"], campaign, f"{label}.before"),
                    _state(data["after"], campaign, f"{label}.after"),
                    data.get("note"), data.get("cause"))  # type: ignore[arg-type]


def _origin(value: object, campaign: str) -> Origin:
    data = _object(value, "origin", _ORIGIN, _ORIGIN_REQUIRED)
    source = data.get("source")
    if source is not None:
        try:
            source = SourceRef.parse(source)
        except ValueError as error:
            raise _fail("invalid_field", f"origin.source: {error}") from None
    return Origin(data["op"], data["at"], _party(data["holder"], campaign, "origin.holder"),  # type: ignore[arg-type]
                  _optional_party(data.get("owner"), campaign, "origin.owner"), data.get("name"),  # type: ignore[arg-type]
                  source, tuple(_list(data.get("fits", []), "origin.fits")),  # type: ignore[arg-type]
                  data.get("note"), data.get("cause"))  # type: ignore[arg-type]


def party_ids(record: object) -> frozenset[str]:
    """Every instance id a raw ``possession`` record names as ``<kind>/<instance_id>`` text, at any depth.

    Read raw, so an undecodable record still protects its ids: an allocator must never give a
    hand-deleted owner's or holder's id to a different individual, who would read as this item's party.
    Every string that is exactly ``<kind>/<instance_id>`` counts, wherever it stands (over-reserving only
    skips an id); the record's own ``schema`` tag is the one string known not to name a party.
    """
    taken: set[str] = set()
    top = dict(record) if isinstance(record, Mapping) else None
    if top is not None and top.get("schema") == POSSESSION_SCHEMA:
        del top["schema"]
    stack: list[object] = [record if top is None else top]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            kind, _, instance_id = value.partition("/")
            try:
                InstanceRef("c", kind, instance_id)  # any valid campaign id: only the two segments are checked
            except ValueError:
                continue
            taken.add(instance_id)
        elif isinstance(value, Mapping):
            stack.extend(value.keys())
            stack.extend(value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
    return frozenset(taken)


def possession_from_payload(item: InstanceRef, payload: object) -> Possession:
    """Decode one item's ``possession`` record; any refusal is a ``PossessionCodecError`` with a reason."""
    if type(item) is not InstanceRef:
        raise TypeError(f"an item is an InstanceRef, not {type(item).__name__}")
    if not isinstance(payload, Mapping) or not all(type(key) is str for key in payload):
        raise _fail("invalid_field", "the possession record must be a JSON object")
    if payload.get("schema") != POSSESSION_SCHEMA:
        raise _fail("unsupported_schema", f"not {POSSESSION_SCHEMA}: {payload.get('schema')!r}")
    clash = sorted(IDENTITY_KEYS & set(payload))
    if clash:
        raise _fail("identity_in_payload", f"the envelope owns {clash}")
    missing = sorted(_REQUIRED - set(payload))
    if missing:
        raise _fail("invalid_field", f"the possession record is missing {missing}")
    campaign = item.campaign_id
    try:
        state = State(_party(payload["holder"], campaign, "holder"),
                      _optional_party(payload.get("owner"), campaign, "owner"),
                      payload.get("equipped"))  # type: ignore[arg-type]
        entries = [_entry(value, campaign, f"log[{n}]") for n, value in enumerate(_list(payload["log"], "log"))]
        entries.sort(key=lambda entry: entry.rev)  # out of order by hand: accepted, re-sorted on the next write
        extras = {key: value for key, value in payload.items() if key not in RECORD_FIELDS}
        return Possession(item, state, _origin(payload["origin"], campaign), payload["rev"],  # type: ignore[arg-type]
                          tuple(_list(payload.get("fits", []), "fits")), payload["evicted_through"],  # type: ignore[arg-type]
                          tuple(entries), extras)  # type: ignore[arg-type]
    except PossessionCodecError:
        raise
    except PossessionError as error:
        raise _fail(error.reason, error.detail) from None
    except (TypeError, ValueError) as error:  # a backstop: an unexpected JSON shape is undecodable, never a crash
        raise _fail("invalid_field", f"unexpected value: {error}") from None

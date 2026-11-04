"""The ``grimoire.holdings/1`` payload: strict inside known structures, unknown top-level fields kept.

The store's envelope owns the address (``HoldingsRef``) and the schema id, so a payload that repeats
identity is refused. Inside a file, holders are written ``<kind>/<instance_id>`` within the file's
own campaign. Encoding is canonical: stacks sorted by item, ops by seq, absent fields omitted.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.identity import InstanceRef, validate_segment
from grimoire.core.json_value import thaw

from .model import (
    HOLDINGS_SCHEMA, IDENTITY_KEYS, PAYLOAD_FIELDS, Holdings, HoldingsCodecError, HoldingsError, Line, LogEntry,
)

_TOP = PAYLOAD_FIELDS
_STACK = frozenset({"item", "count"})
_ENTRY_REQUIRED = frozenset({"op", "seq", "at", "lines", "peers", "after"})
_ENTRY = _ENTRY_REQUIRED | {"note", "cause"}
_LINE_FIELDS = {"add": ("amount",), "consume": ("amount",), "adjust": ("seen", "to"), "move": ("amount", "target")}


def holder_text(holder: InstanceRef) -> str:
    return f"{holder.kind}/{holder.instance_id}"


def _line_payload(line: Line) -> dict:
    payload: dict = {"line": line.kind, "holder": holder_text(line.holder), "item": line.item}
    if line.kind == "adjust":
        payload.update(seen=line.seen, to=line.to)
    else:
        payload["amount"] = line.amount
    if line.target is not None:
        payload["target"] = holder_text(line.target)
    return payload


def entry_to_payload(holder: InstanceRef, entry: LogEntry) -> dict:
    """One op entry exactly as ``holdings_to_payload`` writes it (``holder`` is the file's holder)."""
    payload: dict = {
        "op": entry.op, "seq": entry.seq, "at": entry.at, "lines": [_line_payload(line) for line in entry.lines],
        "peers": {holder_text(peer): seq for peer, seq in entry.peers.items()}, "after": dict(entry.after),
    }
    if entry.note is not None:
        payload["note"] = entry.note
    if entry.cause is not None:
        payload["cause"] = entry.cause
    return payload


def holdings_to_payload(holdings: Holdings) -> dict:
    payload: dict = dict(thaw(holdings.extras))  # type: ignore[arg-type]
    payload.update(
        seq=holdings.seq, evicted_through=holdings.evicted_through,
        stacks=[{"item": item, "count": count} for item, count in holdings.stacks.items()],
        ops=[entry_to_payload(holdings.holder, entry) for entry in holdings.log],
    )
    return payload


# -- decoding ------------------------------------------------------------------------------------

def _fail(reason: str, detail: str) -> HoldingsCodecError:
    return HoldingsCodecError(reason, detail)


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


def _holder(text: object, campaign: str, label: str) -> InstanceRef:
    parts = text.split("/") if type(text) is str else []
    if len(parts) != 2:
        raise _fail("invalid_field", f"{label} must be <kind>/<instance_id>, not {text!r}")
    try:
        return InstanceRef(campaign, parts[0], parts[1])
    except ValueError as error:
        raise _fail("invalid_field", f"{label}: {error}") from None


def _line(value: object, campaign: str, label: str) -> Line:
    kind = value.get("line") if isinstance(value, Mapping) else None
    if type(kind) is not str or kind not in _LINE_FIELDS:  # a hand edit can put any JSON value here
        raise _fail("invalid_field", f"{label}.line must be one of {sorted(_LINE_FIELDS)}, not {kind!r}")
    extra = _LINE_FIELDS[kind]  # type: ignore[index]
    fields = frozenset({"line", "holder", "item", *extra})
    data = _object(value, label, fields, fields)
    target = _holder(data["target"], campaign, f"{label}.target") if "target" in data else None
    return Line(kind, _holder(data["holder"], campaign, f"{label}.holder"), data["item"],  # type: ignore[arg-type]
                amount=data.get("amount"), seen=data.get("seen"), to=data.get("to"), target=target)


def _entry(value: object, campaign: str, label: str) -> LogEntry:
    data = _object(value, label, _ENTRY, _ENTRY_REQUIRED)
    lines = tuple(_line(line, campaign, f"{label}.lines[{n}]") for n, line in enumerate(_list(data["lines"],
                                                                                            f"{label}.lines")))
    raw_peers, after = data["peers"], data["after"]
    if not isinstance(raw_peers, Mapping) or not isinstance(after, Mapping):
        raise _fail("invalid_field", f"{label}.peers and {label}.after must be JSON objects")
    peers = {_holder(key, campaign, f"{label}.peers"): seq for key, seq in raw_peers.items()}
    return LogEntry(data["op"], data["seq"], data["at"], lines, peers, dict(after),  # type: ignore[arg-type]
                    data.get("note"), data.get("cause"))  # type: ignore[arg-type]


def holdings_from_payload(holder: InstanceRef, schema: object, payload: object) -> Holdings:
    """Decode one holder's file; any refusal is a ``HoldingsCodecError`` with a reason."""
    if schema != HOLDINGS_SCHEMA:
        raise _fail("unsupported_schema", f"not {HOLDINGS_SCHEMA}: {schema!r}")
    if not isinstance(payload, Mapping) or not all(type(key) is str for key in payload):
        raise _fail("invalid_field", "the payload must be a JSON object")
    clash = sorted(IDENTITY_KEYS & set(payload))
    if clash:
        raise _fail("identity_in_payload", f"the envelope owns {clash}")
    missing = sorted(_TOP - set(payload))
    if missing:
        raise _fail("invalid_field", f"the payload is missing {missing}")
    try:
        stacks: dict[str, object] = {}
        seen: set[str] = set()
        for n, value in enumerate(_list(payload["stacks"], "stacks")):
            stack = _object(value, f"stacks[{n}]", _STACK, _STACK)
            if not isinstance(stack["item"], str) or stack["item"] in seen:
                raise _fail("invalid_field", f"stacks[{n}]: a missing or duplicate item {stack['item']!r}")
            seen.add(stack["item"])
            if type(stack["count"]) is int and stack["count"] == 0:
                try:  # a hand edit to 0 means "has none" (dropped on the next write), but only for a valid key
                    validate_segment(stack["item"], "item")
                except ValueError as error:
                    raise _fail("invalid_field", f"stacks[{n}]: {error}") from None
                continue
            stacks[stack["item"]] = stack["count"]
        entries = [_entry(value, holder.campaign_id, f"ops[{n}]")
                   for n, value in enumerate(_list(payload["ops"], "ops"))]
        entries.sort(key=lambda entry: entry.seq)
        extras = {key: value for key, value in payload.items() if key not in _TOP}
        return Holdings(holder, payload["seq"], payload["evicted_through"], stacks,  # type: ignore[arg-type]
                        tuple(entries), extras)  # type: ignore[arg-type]
    except HoldingsCodecError:
        raise
    except HoldingsError as error:
        raise _fail(error.reason, error.detail) from None
    except (TypeError, ValueError) as error:  # a backstop: an unexpected JSON shape is undecodable, never a crash
        raise _fail("invalid_field", f"unexpected value: {error}") from None

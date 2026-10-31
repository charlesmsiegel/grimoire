"""Chat and play-scope payloads: conversion to and from plain JSON-compatible data (Sprint 107).

The store's ``grimoire.scope-record/1`` envelope carries these payloads opaquely and owns the address
and the schema id, so neither is repeated inside a payload (``identity_in_payload``). Unknown
top-level payload keys survive a read/modify/write in ``extras``. Inside a known structure,
an unknown or malformed field is refused with a reason rather than dropped. An unknown schema
is refused (``unsupported_schema``), so a newer chat is never rewritten by this version.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.identity import ChatRef, InstanceRef, ScopeRef, SourceRef
from grimoire.core.json_value import thaw

from .model import (
    CHAT_FIELDS, IDENTITY_KEYS, SCOPE_FIELDS, ChatModelError, ChatRecord, Launch, PlayScope, RosterEntry,
    SourceBinding, WorldBinding,
)

CHAT_SCHEMA = "grimoire.chat/1"
SCOPE_SCHEMA = "grimoire.play-scope/1"


class ChatCodecError(ValueError):
    """A payload this version refuses to interpret; ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


def _invalid(message: str) -> ChatCodecError:
    return ChatCodecError(message, "invalid_field")


# -- encoding -------------------------------------------------------------------------------------

def _binding(binding: SourceBinding) -> dict:
    data: dict = {"ref": str(binding.ref), "role": binding.role, "resolved_revision": binding.resolved_revision,
                  "introduced_seq": binding.introduced_seq, "introduced_at": binding.introduced_at}
    if binding.actor is not None:
        data["actor"] = str(binding.actor)
    if binding.detached_seq is not None:
        data["detached_seq"] = binding.detached_seq
        data["detached_at"] = binding.detached_at
    return data


def _roster(entry: RosterEntry) -> dict:
    data: dict = {"actor": str(entry.actor), "role": entry.role, "introduced_seq": entry.introduced_seq,
                  "introduced_at": entry.introduced_at, "prior_history": entry.prior_history}
    if entry.source is not None:
        data["source"] = str(entry.source)
    return data


def chat_to_payload(chat: ChatRecord) -> dict:
    launch: dict = {"kind": chat.launch.kind}
    if chat.launch.ref is not None:
        launch["ref"] = str(chat.launch.ref)
    if chat.launch.premise is not None:
        launch["premise"] = chat.launch.premise
    payload: dict = {
        "launch": launch, "created_at": chat.created_at, "last_activity": chat.last_activity, "seq": chat.seq,
        "bindings": [_binding(b) for b in chat.bindings], "roster": [_roster(r) for r in chat.roster],
    }
    if chat.label is not None:
        payload["label"] = chat.label
    return {**thaw(chat.extras), **payload}  # type: ignore[dict-item]


def scope_to_payload(scope: PlayScope) -> dict:
    payload: dict = {"created_at": scope.created_at}
    if scope.title is not None:
        payload["title"] = scope.title
    if scope.world is not None:
        payload["world"] = {"source": scope.world.source,
                            "observed_manifest_etag": scope.world.observed_manifest_etag,
                            "attached_at": scope.world.attached_at}
    if scope.session_zero is not None:
        payload["session_zero"] = thaw(scope.session_zero)
    return {**thaw(scope.extras), **payload}  # type: ignore[dict-item]


# -- decoding -------------------------------------------------------------------------------------

def _object(value: object, label: str, allowed: frozenset[str], required: frozenset[str]) -> Mapping:
    if not isinstance(value, Mapping) or not all(type(key) is str for key in value):
        raise _invalid(f"{label} must be a JSON object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ChatCodecError(f"{label} has unknown fields {unknown}", "unknown_field")
    missing = sorted(required - set(value))
    if missing:
        raise _invalid(f"{label} is missing {missing}")
    return value


def _ref(text: object, label: str) -> SourceRef:
    try:
        return SourceRef.parse(text)
    except ValueError as error:
        raise _invalid(f"{label}: {error}") from None


def _instance(text: object, label: str) -> InstanceRef:
    try:
        return InstanceRef.parse(text)
    except ValueError as error:
        raise _invalid(f"{label}: {error}") from None


def _list(value: object, label: str) -> list:
    if not isinstance(value, (list, tuple)):
        raise _invalid(f"{label} must be a list")
    return list(value)


_BINDING = frozenset({"ref", "role", "resolved_revision", "introduced_seq", "introduced_at", "actor",
                      "detached_seq", "detached_at"})
_ROSTER = frozenset({"actor", "role", "introduced_seq", "introduced_at", "prior_history", "source"})


def _open(payload: object, schema: object, expected: str, fields: frozenset[str]) -> dict:
    if schema != expected:
        raise ChatCodecError(f"unsupported schema {schema!r}; expected {expected}", "unsupported_schema")
    if not isinstance(payload, Mapping) or not all(type(key) is str for key in payload):
        raise _invalid("a payload must be a JSON object")
    repeated = sorted(IDENTITY_KEYS & set(payload))
    if repeated:
        raise ChatCodecError(f"the envelope owns {repeated}; the payload may not repeat them",
                             "identity_in_payload")
    return dict(payload)


def chat_from_payload(ref: ChatRef, schema: object, payload: object) -> ChatRecord:
    data = _open(payload, schema, CHAT_SCHEMA, CHAT_FIELDS)
    try:
        raw_launch = _object(data.get("launch"), "launch", frozenset({"kind", "ref", "premise"}),
                             frozenset({"kind"}))
        launch = Launch(raw_launch["kind"], _ref(raw_launch["ref"], "launch.ref") if "ref" in raw_launch else None,
                        raw_launch.get("premise"))
        bindings = []
        for raw in _list(data.get("bindings", []), "bindings"):
            raw = _object(raw, "binding", _BINDING,
                          frozenset({"ref", "role", "resolved_revision", "introduced_seq", "introduced_at"}))
            bindings.append(SourceBinding(
                _ref(raw["ref"], "binding.ref"), raw["role"], raw["resolved_revision"], raw["introduced_seq"],
                raw["introduced_at"], _instance(raw["actor"], "binding.actor") if "actor" in raw else None,
                raw.get("detached_seq"), raw.get("detached_at")))
        roster = []
        for raw in _list(data.get("roster", []), "roster"):
            raw = _object(raw, "roster entry", _ROSTER,
                          frozenset({"actor", "role", "introduced_seq", "introduced_at", "prior_history"}))
            roster.append(RosterEntry(
                _instance(raw["actor"], "roster.actor"), raw["role"], raw["introduced_seq"], raw["introduced_at"],
                _ref(raw["source"], "roster.source") if "source" in raw else None, raw["prior_history"]))
        missing = sorted({"created_at", "last_activity", "seq"} - set(data))
        if missing:
            raise _invalid(f"a chat is missing {missing}")
        extras = {key: value for key, value in data.items() if key not in CHAT_FIELDS}
        return ChatRecord(ref, launch, data["created_at"], data["last_activity"], data["seq"], tuple(bindings),
                          tuple(roster), data.get("label"), extras)  # type: ignore[arg-type]
    except ChatModelError as error:
        raise ChatCodecError(str(error), error.reason) from None


def scope_from_payload(ref: ScopeRef, schema: object, payload: object) -> PlayScope:
    data = _open(payload, schema, SCOPE_SCHEMA, SCOPE_FIELDS)
    try:
        world = None
        if data.get("world") is not None:
            raw = _object(data["world"], "world", frozenset({"source", "observed_manifest_etag", "attached_at"}),
                          frozenset({"source", "observed_manifest_etag", "attached_at"}))
            world = WorldBinding(raw["source"], raw["observed_manifest_etag"], raw["attached_at"])
        if "created_at" not in data:
            raise _invalid("a scope is missing created_at")
        extras = {key: value for key, value in data.items() if key not in SCOPE_FIELDS}
        return PlayScope(ref, data["created_at"], data.get("title"), world, data.get("session_zero"),
                         extras)  # type: ignore[arg-type]
    except ChatModelError as error:
        raise ChatCodecError(str(error), error.reason) from None

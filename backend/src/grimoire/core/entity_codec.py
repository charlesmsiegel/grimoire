"""Provisional conversion between entity values and plain JSON-compatible payloads.

The generic record store (Native File Store, 010) carries these payloads
opaquely in its envelope, which owns the address and the schema id; neither is
repeated inside the payload. Field spellings here are provisional, not a frozen
on-disk format, and this module is deliberately not exported from
``grimoire.core``.

Extension points preserve what this version does not know: unknown top-level
fields become ``extras`` and unknown facet names become ``UnknownFacet``.
Inside a known typed structure an unknown or malformed field is refused with a
reason instead of being dropped or coerced; the envelope still holds the
original payload, so refusing to interpret it loses nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields

from .entity import ADDRESS_KEYS, STATE_TYPES, EntityDefinition, EntityInstance, StateValue
from .facets import KNOWN_FACETS, Communicative, Facet, FacetBundle, FacetSet, UnknownFacet
from .identity import InstanceRef, SourceRef
from .json_value import JsonValueError, freeze_object, thaw

ENTITY_SCHEMA = "grimoire.entity/0"
_CHANNEL_FIELDS = frozenset({"speaks", "understands"})


class CodecError(ValueError):
    """A payload this version refuses to interpret; ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


def _invalid(message: str) -> CodecError:
    return CodecError(message, "invalid_field")


# --- encoding -------------------------------------------------------------------

def _facet_payload(facet: Facet) -> dict:
    if isinstance(facet, UnknownFacet):
        return {"facet": facet.name, **thaw(facet.data)}
    payload: dict = {"facet": facet.name}
    for item in fields(facet):
        value = getattr(facet, item.name)
        if value is None:
            continue
        payload[item.name] = sorted(value) if isinstance(value, frozenset) else value
    return payload


def _state_payload(value: StateValue) -> dict:
    name = next(key for key, cls in STATE_TYPES.items() if type(value) is cls)
    return {"state": name, **{item.name: getattr(value, item.name) for item in fields(value)}}


def definition_to_payload(definition: EntityDefinition) -> dict:
    payload: dict = {"name": definition.name, "nature": definition.nature}
    if definition.description:
        payload["description"] = definition.description
    payload["traits"] = [_facet_payload(facet) for facet in definition.traits]
    return {**payload, **thaw(definition.extras)}


def instance_to_payload(instance: EntityInstance) -> dict:
    payload: dict = {}
    if instance.source is not None:
        payload["source"] = str(instance.source)
    if instance.local_name is not None:
        payload["local_name"] = instance.local_name
    for label in ("added", "replaced"):
        bundle = getattr(instance, label)
        if bundle:
            payload[label] = [_facet_payload(facet) for facet in bundle]
    if instance.state:
        payload["state"] = [_state_payload(value) for value in instance.state]
    return {**payload, **thaw(instance.extras)}


# --- decoding -------------------------------------------------------------------

def _open(payload: object, schema: object, address: object, address_type: type) -> dict:
    if not isinstance(address, address_type):
        raise _invalid(f"the envelope address must be a {address_type.__name__}")
    if not isinstance(payload, Mapping):
        raise _invalid("a payload must be a JSON object")
    if any(not isinstance(key, str) for key in payload):
        raise _invalid("payload keys must be text")
    if schema != ENTITY_SCHEMA:
        raise CodecError(f"unsupported entity schema {schema!r}; expected {ENTITY_SCHEMA}",
                         "unsupported_schema")
    duplicated = sorted(ADDRESS_KEYS & set(payload))
    if duplicated:
        raise CodecError(f"the envelope owns {duplicated}; the payload may not repeat them",
                         "address_in_payload")
    return dict(payload)


def _entries(value: object, label: str) -> list:
    if not isinstance(value, (list, tuple)):
        raise _invalid(f"{label} must be a list")
    return list(value)


def _channels(value: object, label: str) -> frozenset:
    entries = _entries(value, label)
    if not all(isinstance(entry, str) for entry in entries):
        raise _invalid(f"{label} must list channel names")
    if len(set(entries)) != len(entries):
        raise _invalid(f"{label} lists a channel twice")
    return frozenset(entries)


def _facet(entry: object) -> Facet:
    if not isinstance(entry, Mapping) or not isinstance(entry.get("facet"), str):
        raise _invalid(f"a facet entry must be an object with a text 'facet' name: {entry!r}")
    data = {key: value for key, value in entry.items() if key != "facet"}
    cls = KNOWN_FACETS.get(entry["facet"])
    if cls is None:
        return UnknownFacet(entry["facet"], data)
    known = {item.name for item in fields(cls)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise CodecError(f"{entry['facet']} has unknown fields {unknown}", "unknown_facet_field")
    if cls is Communicative:
        data = {key: _channels(value, key) if key in _CHANNEL_FIELDS else value for key, value in data.items()}
    return cls(**data)


def _facets(value: object, label: str) -> tuple[Facet, ...]:
    return tuple(_facet(entry) for entry in _entries(value, label))


def _state(entry: object) -> StateValue:
    if not isinstance(entry, Mapping) or not isinstance(entry.get("state"), str):
        raise _invalid(f"a state entry must be an object with a text 'state' type: {entry!r}")
    cls = STATE_TYPES.get(entry["state"])
    if cls is None:
        raise CodecError(f"unknown state type {entry['state']!r}", "unknown_state")
    data = {key: value for key, value in entry.items() if key != "state"}
    unknown = sorted(set(data) - {item.name for item in fields(cls)})
    if unknown:
        raise CodecError(f"{entry['state']} has unknown fields {unknown}", "unknown_state_field")
    return cls(**data)


def _extras(rest: dict) -> object:
    try:
        return freeze_object(rest)
    except JsonValueError as error:
        raise _invalid(f"unknown fields: {error}") from None


def _required(payload: dict, key: str) -> object:
    if key not in payload:
        raise _invalid(f"payload is missing {key!r}")
    return payload.pop(key)


def definition_from_payload(address: SourceRef, schema: str, payload: object) -> EntityDefinition:
    rest = _open(payload, schema, address, SourceRef)
    name, nature = _required(rest, "name"), _required(rest, "nature")
    traits = FacetSet(_facets(_required(rest, "traits"), "traits"))
    description = rest.pop("description", "")
    return EntityDefinition(address, name, nature, traits, description, _extras(rest))


def instance_from_payload(address: InstanceRef, schema: str, payload: object) -> EntityInstance:
    rest = _open(payload, schema, address, InstanceRef)
    source = rest.pop("source", None)
    if source is not None:
        try:
            source = SourceRef.parse(source)
        except ValueError as error:
            raise _invalid(f"source: {error}") from None
    local_name = rest.pop("local_name", None)
    added = FacetBundle(_facets(rest.pop("added", []), "added"))
    replaced = FacetBundle(_facets(rest.pop("replaced", []), "replaced"))
    state = tuple(_state(entry) for entry in _entries(rest.pop("state", []), "state"))
    return EntityInstance(address, source, local_name, added, replaced, state, _extras(rest))


__all__ = [
    "ENTITY_SCHEMA", "CodecError", "definition_from_payload", "definition_to_payload",
    "instance_from_payload", "instance_to_payload",
]

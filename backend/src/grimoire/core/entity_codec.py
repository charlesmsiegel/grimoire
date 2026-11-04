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
original payload, so refusing to interpret it loses nothing. The one exception
is the ``abilities`` facet (025): unknown keys on the facet object and inside
an ability, effect, limit, cost or prerequisite are kept as that value's
``extras``, and the ability assessment fails closed on them (``needs_upgrade``).
Malformed known fields are still refused, with the ability values' own
``FacetError`` reasons, and a name listed twice is a ``CodecError``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import MISSING, fields

from .abilities import Abilities, Ability, Cost, Effect, Limit, Prerequisite
from .entity import (
    ADDRESS_KEYS, STATE_TYPES, EntityDefinition, EntityInstance, Individuality, StateValue,
)
from .facets import KNOWN_FACETS, Communicative, Facet, FacetBundle, FacetSet, UnknownFacet
from .identity import InstanceRef, SourceRef
from .json_value import JsonValueError, freeze_object, thaw

ENTITY_SCHEMA = "grimoire.entity/0"
_CHANNEL_FIELDS = frozenset({"speaks", "understands"})
#: Nested lists of ability values, by field name.
_ABILITY_PARTS: dict[str, type] = {"abilities": Ability, "effects": Effect, "limits": Limit,
                                   "costs": Cost, "prerequisites": Prerequisite}
#: Fields holding sets of id segments.
_ABILITY_NAMES = frozenset({"tags", "excludes", "applies_to"})
#: Optional prose whose explicit null decodes as absent. A null list or ``certainty`` is refused:
#: reading it as empty or ``reliable`` would loosen the power.
_ABILITY_NULLABLE = frozenset({"description", "range"})
#: Field values that are the default and so are omitted when encoding.
_ABILITY_DEFAULTS = {"certainty": "reliable", "description": ""}
_INDIVIDUALITY_FIELDS = frozenset({"decision_id", "given_name", "decided_by", "scene_id", "template",
                                   "event_ids"})


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

def _ability_payload(value: object) -> dict:
    """One ability value (or the facet) without defaults; extras become top-level keys."""
    payload: dict = {}
    for item in fields(value):
        if item.name == "extras":
            continue
        field_value = getattr(value, item.name)
        if (field_value is None or field_value in ((), frozenset())
                or _ABILITY_DEFAULTS.get(item.name, object()) == field_value):
            continue
        if isinstance(field_value, frozenset):
            field_value = sorted(field_value)
        elif isinstance(field_value, tuple):
            field_value = [_ability_payload(part) for part in field_value]
        payload[item.name] = field_value
    return {**payload, **thaw(value.extras)}


def _facet_payload(facet: Facet) -> dict:
    if isinstance(facet, UnknownFacet):
        return {"facet": facet.name, **thaw(facet.data)}
    if isinstance(facet, Abilities):
        return {"facet": facet.name, **_ability_payload(facet)}
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
    if instance.individuality is not None:
        payload["individuality"] = _individuality_payload(instance.individuality)
    return {**payload, **thaw(instance.extras)}


def _individuality_payload(value: Individuality) -> dict:
    payload: dict = {"decision_id": value.decision_id, "given_name": value.given_name,
                     "decided_by": value.decided_by, "scene_id": value.scene_id,
                     "template": str(value.template)}
    if value.event_ids:
        payload["event_ids"] = list(value.event_ids)
    return payload


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
    data = _data(entry, "facet")
    cls = KNOWN_FACETS.get(entry["facet"])
    if cls is None:
        return UnknownFacet(entry["facet"], data)
    if cls is Abilities:
        return _ability_value(Abilities, data, "abilities")
    known = {item.name for item in fields(cls)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise CodecError(f"{entry['facet']} has unknown fields {unknown}", "unknown_facet_field")
    _require_fields(cls, data, entry["facet"])
    if cls is Communicative:
        data = {key: _channels(value, key) if key in _CHANNEL_FIELDS else value for key, value in data.items()}
    return cls(**data)


def _names(value: object, label: str) -> frozenset:
    entries = _entries(value, label)
    if not all(isinstance(entry, str) for entry in entries):
        raise _invalid(f"{label} must list names")
    if len(set(entries)) != len(entries):
        raise _invalid(f"{label} lists a name twice")
    return frozenset(entries)


def _ability_value(cls: type, entry: object, label: str) -> object:
    """An ability value (or the facet): known fields decoded, unknown keys kept as extras."""
    if not isinstance(entry, Mapping) or any(not isinstance(key, str) for key in entry):
        raise _invalid(f"{label} must be an object with text keys: {entry!r}")
    rest = dict(entry)  # a stored payload is read-only
    known: dict = {}
    for item in fields(cls):
        if item.name == "extras" or item.name not in rest:
            continue
        value = rest.pop(item.name)
        if value is None and item.name in _ABILITY_NULLABLE:
            continue  # an explicit null of optional prose means "absent", as encoding omits it
        if item.name in _ABILITY_NAMES:
            value = _names(value, f"{label} {item.name}")
        elif item.name in _ABILITY_PARTS:
            value = tuple(_ability_value(_ABILITY_PARTS[item.name], part, f"{label} {item.name}")
                          for part in _entries(value, f"{label} {item.name}"))
        known[item.name] = value
    _require_fields(cls, known, label)
    return cls(**known, extras=_extras(rest))


def _facets(value: object, label: str) -> tuple[Facet, ...]:
    return tuple(_facet(entry) for entry in _entries(value, label))


def _state(entry: object) -> StateValue:
    if not isinstance(entry, Mapping) or not isinstance(entry.get("state"), str):
        raise _invalid(f"a state entry must be an object with a text 'state' type: {entry!r}")
    cls = STATE_TYPES.get(entry["state"])
    if cls is None:
        raise CodecError(f"unknown state type {entry['state']!r}", "unknown_state")
    data = _data(entry, "state")
    unknown = sorted(set(data) - {item.name for item in fields(cls)})
    if unknown:
        raise CodecError(f"{entry['state']} has unknown fields {unknown}", "unknown_state_field")
    _require_fields(cls, data, entry["state"])
    return cls(**data)


def _data(entry: Mapping, discriminator: str) -> dict:
    if any(not isinstance(key, str) for key in entry):
        raise _invalid(f"{entry[discriminator]} entry keys must be text")
    return {key: value for key, value in entry.items() if key != discriminator}


def _require_fields(cls: type, data: Mapping, label: str) -> None:
    missing = sorted(item.name for item in fields(cls) if item.name not in data
                     and item.default is MISSING and item.default_factory is MISSING)
    if missing:
        raise _invalid(f"{label} is missing {missing}")


def _extras(rest: dict) -> object:
    try:
        return freeze_object(rest)
    except JsonValueError as error:
        raise _invalid(f"unknown fields: {error}") from None


def _required(payload: dict, key: str) -> object:
    if key not in payload:
        raise _invalid(f"payload is missing {key!r}")
    return payload.pop(key)


def _individuality(entry: object) -> Individuality | None:
    if entry is None:
        return None
    if not isinstance(entry, Mapping) or any(not isinstance(key, str) for key in entry):
        raise _invalid(f"individuality must be an object with text keys: {entry!r}")
    unknown = sorted(set(entry) - _INDIVIDUALITY_FIELDS)
    if unknown:
        raise CodecError(f"individuality has unknown fields {unknown}", "unknown_individuality_field")
    missing = sorted(_INDIVIDUALITY_FIELDS - {"event_ids"} - set(entry))
    if missing:
        raise _invalid(f"individuality is missing {missing}")
    try:
        template = SourceRef.parse(entry["template"])
    except ValueError as error:
        raise _invalid(f"individuality template: {error}") from None
    return Individuality(entry["decision_id"], entry["given_name"], entry["decided_by"],
                         entry["scene_id"], template,
                         tuple(_entries(entry.get("event_ids", []), "event_ids")))


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
    individuality = _individuality(rest.pop("individuality", None))
    return EntityInstance(address, source, local_name, added, replaced, state, _extras(rest),
                          individuality)


__all__ = [
    "ENTITY_SCHEMA", "CodecError", "definition_from_payload", "definition_to_payload",
    "instance_from_payload", "instance_to_payload",
]

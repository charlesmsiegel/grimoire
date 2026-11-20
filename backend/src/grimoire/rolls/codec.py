"""Exact JSON forms of what the roll ledger records (Sprint 075): the request, its backing and the faces.

Encoding is deterministic and every key is always present (``None`` where a value is absent), so decoding a
stored entry and encoding it again reproduces it. Decoding is strict: exact keys, exact JSON types, then the
value's own constructor. Anything else is a ``RollError`` with reason ``undecodable_entry``; the store turns
that into ``LedgerInvalid`` and fails closed. No result is stored: replay derives it from the faces again.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields

from grimoire.core.authority import PlayerContribution
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.rulesets import ActionRequest, ModuleBinding, RulesetModule
from grimoire.rulesets.families import ACTION_TYPES

from .model import RecordedRoll, RollError

ROLL_KEYS = frozenset({"request", "contribution", "faces"})
_REQUEST_KEYS = frozenset({"request_id", "actor", "module", "binding", "action"})
_MODULE_KEYS = frozenset({"ref", "bindings", "components"})
_BINDING_KEYS = frozenset({"family", "edition"})
_CONTRIBUTION_KEYS = frozenset({"contribution_id", "scene_id", "turn_id", "actor_id", "submitted_by", "kind",
                                "text"})


def _bad(message: str) -> RollError:
    return RollError(message, "undecodable_entry")


def _object(value: object, keys: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise _bad(f"{label} must be an object with exactly {sorted(keys)}")
    return value


def _str(value: object, label: str) -> str:
    if type(value) is not str:
        raise _bad(f"{label} must be text")
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, (list, tuple)):
        raise _bad(f"{label} must be a list")
    return list(value)


def _build(factory, *args, **kwargs):
    try:
        return factory(*args, **kwargs)
    except (ValueError, TypeError) as error:
        raise _bad(f"{getattr(factory, '__name__', factory)}: {error}") from None


def _ref(value: object, parse, label: str):
    text = _str(value, label)
    try:
        return parse(text)
    except ValueError:
        raise _bad(f"{label} is not a reference: {text!r}") from None


# --- requests ----------------------------------------------------------------------------------

def _binding_to_payload(binding: ModuleBinding) -> dict[str, object]:
    return {"family": binding.family, "edition": binding.edition}


def _binding_from_payload(value: object) -> ModuleBinding:
    data = _object(value, _BINDING_KEYS, "binding")
    return _build(ModuleBinding, _str(data["family"], "family"), _str(data["edition"], "edition"))


def request_to_payload(request: ActionRequest) -> dict[str, object]:
    if type(request) is not ActionRequest:
        raise TypeError("expected an ActionRequest")
    module = request.module
    return {
        "request_id": request.request_id, "actor": str(request.actor),
        "module": {"ref": str(module.ref), "bindings": [_binding_to_payload(b) for b in module.bindings],
                   "components": [str(ref) for ref in module.components]},
        "binding": _binding_to_payload(request.binding),
        "action": {field.name: getattr(request.action, field.name) for field in fields(request.action)},
    }


def request_from_payload(campaign_id: str, value: object) -> ActionRequest:
    data = _object(value, _REQUEST_KEYS, "request")
    actor = _ref(data["actor"], InstanceRef.parse, "actor")
    if actor.campaign_id != campaign_id:
        raise _bad("the request belongs to another campaign")
    module_data = _object(data["module"], _MODULE_KEYS, "module")
    module = _build(RulesetModule, _ref(module_data["ref"], SourceRef.parse, "module ref"),
                    tuple(_binding_from_payload(b) for b in _list(module_data["bindings"], "bindings")),
                    tuple(_ref(c, SourceRef.parse, "component") for c in _list(module_data["components"],
                                                                                  "components")))
    binding = _binding_from_payload(data["binding"])
    kind = ACTION_TYPES[binding.family]  # ModuleBinding admits only the four profiles
    names = frozenset(field.name for field in fields(kind))
    action_data = _object(data["action"], names, "action")
    for name, item in action_data.items():
        if type(item) not in (str, int):  # the constructor checks which; ``true`` is never an integer
            raise _bad(f"action field {name} must be text or an integer")
    action = _build(kind, **action_data)
    return _build(ActionRequest, _str(data["request_id"], "request_id"), actor, module, binding, action)


# --- contributions -----------------------------------------------------------------------------

def contribution_to_payload(contribution: PlayerContribution | None) -> dict[str, object] | None:
    if contribution is None:
        return None
    return {label: getattr(contribution, label) for label in sorted(_CONTRIBUTION_KEYS)}


def contribution_from_payload(campaign_id: str, value: object) -> PlayerContribution | None:
    if value is None:
        return None
    data = _object(value, _CONTRIBUTION_KEYS, "contribution")
    return _build(PlayerContribution, campaign_id, *(_str(data[label], label) for label in (
        "contribution_id", "scene_id", "turn_id", "actor_id", "submitted_by", "kind", "text")))


# --- rolls -------------------------------------------------------------------------------------

def roll_to_payload(request: ActionRequest, contribution: PlayerContribution | None,
                    faces: tuple[int, ...]) -> dict[str, object]:
    return {"request": request_to_payload(request), "contribution": contribution_to_payload(contribution),
            "faces": list(faces)}


def roll_from_payload(campaign_id: str, value: object, recorded_by: str) -> RecordedRoll:
    """A stored roll, its result derived from its faces again; an impossible record is refused."""
    data = _object(value, ROLL_KEYS, "roll")
    request = request_from_payload(campaign_id, data["request"])
    contribution = contribution_from_payload(campaign_id, data["contribution"])
    faces = _list(data["faces"], "faces")
    if not all(type(face) is int for face in faces):
        raise _bad("faces must be integers")
    try:
        return RecordedRoll(request, contribution, tuple(faces), recorded_by)  # type: ignore[arg-type]
    except RollError as error:
        raise _bad(f"not a roll this request can have: {error} ({error.reason})") from None

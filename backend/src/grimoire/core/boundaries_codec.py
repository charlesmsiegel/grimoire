"""016's session-zero configuration (``grimoire.session-zero/1``) to and from plain JSON data (035 milestone 2).

The configuration lives verbatim in a chat's play scope (107's ``PlayScope.session_zero``), which stores it
without interpreting it; this codec is the only reader. It is strict, so a configuration this version cannot
vouch for is never played with:

- the schema id is required, and any other id is ``unsupported_schema`` (a newer configuration is never read
  as an older one, or rewritten by it);
- every key is required, and an unknown key at any level is ``invalid_field``, never dropped;
- every value is checked by 016's own ``Boundary`` and ``SessionZero``, and bounded here, so a stored
  configuration can never hold what 016 refuses to build (a player boundary set by someone else, an agreement
  recorded on a player boundary, a duplicate id, a director as setter);
- the payload must be exactly the canonical form of what it decodes to (``tampered``): a hand edit that 016
  would silently normalise (a group boundary whose ``agreed_by`` leaves out its setter, an unsorted or
  repeated agreement) is refused rather than reinterpreted;
- a ``setting`` boundary is ``invalid_field`` both ways: no setting exists for a chat to be in yet, so one could
  never be resolved, played with or withdrawn.

``revision`` counts the changes written through 035's runner, starting at 1. Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .boundaries import Boundary, SessionZero
from .json_value import JsonValueError, freeze, thaw

SESSION_ZERO_SCHEMA = "grimoire.session-zero/1"
#: The most boundaries one configuration holds: a scope file stays small, and a reader's cost bounded.
MAX_BOUNDARIES = 256
#: The longest id, principal or scope id stored.
MAX_TEXT = 200
#: The longest theme slug.
MAX_THEME = 64
#: The longest private note.
MAX_NOTE = 2000
_KEYS = frozenset({"schema", "revision", "boundaries"})
_BOUNDARY_KEYS = frozenset({"boundary", "scope", "scope_id", "theme", "treatment", "set_by", "attributed",
                            "agreed_by", "private_note"})


class SessionZeroCodecError(Exception):
    """A configuration that cannot be read or written; ``reason`` is ``unsupported_schema``, ``invalid_field``,
    ``tampered`` or ``too_many_boundaries``. The message never names a boundary's owner, id or note."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, str(self)))


@dataclass(frozen=True, slots=True, repr=False)
class StoredSessionZero:
    """A decoded configuration and the revision it was stored at."""

    config: SessionZero
    revision: int

    def __repr__(self) -> str:
        return f"StoredSessionZero(revision={self.revision}, {self.config!r})"


def _invalid(message: str) -> SessionZeroCodecError:
    return SessionZeroCodecError("invalid_field", message)


def _text(value: object, label: str, limit: int = MAX_TEXT) -> str:
    if type(value) is not str or not value or len(value) > limit:
        raise _invalid(f"{label} must be a nonempty string of at most {limit} characters")
    return value


def _revision(value: object) -> int:
    if type(value) is not int or value < 1:
        raise _invalid("revision must be a positive integer")
    return value


def _boundary_payload(boundary: Boundary) -> dict[str, object]:
    return {"boundary": boundary.boundary_id, "scope": boundary.scope, "scope_id": boundary.scope_id,
            "theme": boundary.theme, "treatment": boundary.treatment, "set_by": boundary.set_by,
            "attributed": boundary.attributed, "agreed_by": sorted(boundary.agreed_by),
            "private_note": boundary.private_note}


def _bounded(boundary: Boundary) -> None:
    if boundary.scope == "setting":  # no setting exists for a chat to be in yet: never written, never read
        raise _invalid("a setting boundary cannot be stored until settings exist")
    for label, value in (("boundary", boundary.boundary_id), ("scope_id", boundary.scope_id),
                         ("set_by", boundary.set_by)):
        _text(value, label)
    _text(boundary.theme, "theme", MAX_THEME)
    for person in boundary.agreed_by:
        _text(person, "agreed_by")
    if len(boundary.private_note) > MAX_NOTE:
        raise _invalid(f"a private note is at most {MAX_NOTE} characters")


def session_zero_to_payload(config: SessionZero, revision: int) -> dict[str, object]:
    """The stored form of ``config`` at ``revision``. Refuses what no reader would accept."""
    if not isinstance(config, SessionZero):
        raise _invalid("expected a SessionZero")
    _revision(revision)
    if len(config.boundaries) > MAX_BOUNDARIES:
        raise SessionZeroCodecError("too_many_boundaries", f"a session zero holds at most {MAX_BOUNDARIES} boundaries")
    for boundary in config.boundaries:
        _bounded(boundary)
    return {"schema": SESSION_ZERO_SCHEMA, "revision": revision,
            "boundaries": [_boundary_payload(boundary) for boundary in config.boundaries]}


def _object(value: object, keys: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(type(key) is str for key in value):
        raise _invalid(f"{label} must be a JSON object")
    unknown, missing = set(value) - keys, keys - set(value)
    if unknown:
        raise _invalid(f"{label} has unknown fields {sorted(unknown)}")
    if missing:
        raise _invalid(f"{label} is missing {sorted(missing)}")
    return value


def _boundary(value: object) -> Boundary:
    data = _object(value, _BOUNDARY_KEYS, "a boundary")
    agreed = data["agreed_by"]
    if isinstance(agreed, (str, bytes, Mapping)) or not isinstance(agreed, (list, tuple)):
        raise _invalid("agreed_by must be a list")
    if type(data["attributed"]) is not bool or type(data["private_note"]) is not str:
        raise _invalid("attributed must be a bool and private_note a string")
    try:
        boundary = Boundary(_text(data["boundary"], "boundary"), _text(data["scope"], "scope"),
                            _text(data["scope_id"], "scope_id"), _text(data["theme"], "theme", MAX_THEME),
                            _text(data["treatment"], "treatment"), _text(data["set_by"], "set_by"),
                            data["attributed"], frozenset(_text(person, "agreed_by") for person in agreed),
                            data["private_note"])
    except ValueError as error:  # 016's own checks; its messages name no owner or id
        raise _invalid(f"a boundary is invalid: {error}") from None
    _bounded(boundary)
    return boundary


def session_zero_from_payload(payload: object) -> StoredSessionZero:
    """The configuration ``payload`` stores (a JSON object, frozen or plain). See the module docstring."""
    if not isinstance(payload, Mapping):
        raise _invalid("a session zero is a JSON object")
    if payload.get("schema") != SESSION_ZERO_SCHEMA:
        raise SessionZeroCodecError("unsupported_schema", "this version cannot read that session zero")
    data = _object(payload, _KEYS, "a session zero")
    revision = _revision(data["revision"])
    items = data["boundaries"]
    if isinstance(items, (str, bytes, Mapping)) or not isinstance(items, (list, tuple)):
        raise _invalid("boundaries must be a list")
    if len(items) > MAX_BOUNDARIES:
        raise SessionZeroCodecError("too_many_boundaries", f"a session zero holds at most {MAX_BOUNDARIES} boundaries")
    boundaries = tuple(_boundary(item) for item in items)
    try:
        config = SessionZero(boundaries)
    except ValueError as error:
        raise _invalid(f"a session zero is invalid: {error}") from None
    try:
        stored = thaw(freeze(payload))
    except JsonValueError:
        raise _invalid("a session zero must be JSON data") from None
    if stored != session_zero_to_payload(config, revision):
        raise SessionZeroCodecError("tampered", "a session zero is not in the form this version writes")
    return StoredSessionZero(config, revision)


__all__ = ["MAX_BOUNDARIES", "MAX_NOTE", "MAX_TEXT", "MAX_THEME", "SESSION_ZERO_SCHEMA", "SessionZeroCodecError",
           "StoredSessionZero", "session_zero_from_payload", "session_zero_to_payload"]

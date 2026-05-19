"""Narrative Extras: structured per-key data tier on library/campaign entities.

Sits alongside frontmatter core (Grimoire-owned) and mechanics sheets
(module-owned). Used for color details (favorite drink, scars), cross-mechanics
consistency (extras travel when mechanics changes), and ``mechanics: null``
campaigns wanting richer profiles.

Values live in entity frontmatter (file SSOT). A SQLite mirror powers query
(substring search, listing pinned). Cascade and pinning are layered on by
``grimoire.extras.ExtrasService`` and the HUD config.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, field_validator


class ExtraScope(StrEnum):
    LIBRARY = "library"
    CAMPAIGN_LOCAL = "campaign-local"
    OVERRIDE = "override"


RESERVED_KEY_PREFIXES: tuple[str, ...] = ("_internal_", "mechanics_", "system_")

# Limits (mirror config defaults in design doc / extras.hard_caps / soft_caps).
HARD_CAP_PER_ENTITY = 50
HARD_CAP_CHARS_PER_STRING = 1000
SOFT_CAP_PER_ENTITY = 20
SOFT_CAP_CHARS_PER_STRING = 200
SOFT_CAP_LIST_ITEMS = 20
SOFT_CAP_TOTAL_BYTES = 4096


class ExtrasKeyError(ValueError):
    """Raised when an extras key is invalid (reserved prefix, bad chars, length)."""


class ExtrasCapError(ValueError):
    """Raised when a write would exceed a hard cap (per-entity, per-string)."""


def validate_extras_key(key: str) -> None:
    """Raise ``ExtrasKeyError`` if ``key`` is not a valid extras key.

    Rules (mirror DB CHECK constraint):
    - 1 <= len(key) <= 40
    - snake_case alphanumeric (a-z, 0-9, underscore)
    - reserved prefixes: ``_internal_``, ``mechanics_``, ``system_``
    """
    if not isinstance(key, str):
        raise ExtrasKeyError(f"extras key must be a string, got {type(key).__name__}")
    if not (1 <= len(key) <= 40):
        raise ExtrasKeyError(f"extras key length must be 1-40, got {len(key)}: {key!r}")
    for prefix in RESERVED_KEY_PREFIXES:
        if key.startswith(prefix):
            raise ExtrasKeyError(f"reserved prefix on extras key: {key!r}")
    if not key.replace("_", "").isalnum() or not key.replace("_", "").islower():
        raise ExtrasKeyError(f"extras key must be snake_case alphanumeric: {key!r}")


def validate_extras_value(value: Any) -> None:
    """Raise ``ExtrasCapError`` / ``TypeError`` if ``value`` is not a permitted shape.

    Permitted:
    - scalars: str, int, float, bool, None
    - list[scalar]
    - dict[str, scalar] (single level only)
    """
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if len(value) > HARD_CAP_CHARS_PER_STRING:
            raise ExtrasCapError(
                f"extras string value exceeds hard cap of {HARD_CAP_CHARS_PER_STRING} chars"
            )
        return
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, (str, int, float, bool)) and item is not None:
                raise TypeError(
                    f"extras list items must be scalar, got {type(item).__name__}"
                )
            if isinstance(item, str) and len(item) > HARD_CAP_CHARS_PER_STRING:
                raise ExtrasCapError(
                    f"extras list string exceeds hard cap of {HARD_CAP_CHARS_PER_STRING} chars"
                )
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"extras dict keys must be strings, got {type(k).__name__}")
            if not isinstance(v, (str, int, float, bool)) and v is not None:
                raise TypeError(
                    f"extras dict values must be scalar (single level only), got {type(v).__name__}"
                )
            if isinstance(v, str) and len(v) > HARD_CAP_CHARS_PER_STRING:
                raise ExtrasCapError(
                    f"extras dict string exceeds hard cap of {HARD_CAP_CHARS_PER_STRING} chars"
                )
        return
    raise TypeError(
        "extras value must be scalar | list[scalar] | dict[str, scalar], "
        f"got {type(value).__name__}"
    )


class ExtraValue(BaseModel):
    """One extras key's value plus provenance metadata."""

    value: Any = None
    """Scalar (str/int/float/bool/None), list[scalar], or dict[str, scalar]."""

    set_at: datetime
    """When this value was written."""

    set_by: str
    """Actor: ``user``, ``extractor:reviewed``, ``import:sillytavern``, etc."""

    source_evidence: str | None = None
    """Post excerpt for extractor-proposed values; free text otherwise."""

    scope: ExtraScope = ExtraScope.CAMPAIGN_LOCAL
    """Cascade tier this value belongs to."""

    @field_validator("value")
    @classmethod
    def _check_value(cls, v: Any) -> Any:
        validate_extras_value(v)
        return v


ExtrasDict = dict[str, ExtraValue]


def validate_extras_dict(value: Any) -> Any:
    """Validator wired into entity models' ``extras`` field.

    Raises ``ExtrasKeyError`` for bad keys. Pydantic handles value coercion
    when entries arrive as plain dicts (frontmatter round-trip).
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"extras must be a dict, got {type(value).__name__}")
    for key in value:
        validate_extras_key(key)
    return value


def flatten_extras_value_for_search(value: Any) -> str:
    """Project a value to the FTS5 ``value_text`` cell.

    Lists are space-joined; dicts are rendered ``key:value`` pairs. None/bool/
    numbers stringify directly.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    if isinstance(value, list):
        return " ".join(flatten_extras_value_for_search(v) for v in value)
    if isinstance(value, dict):
        return " ".join(f"{k}:{flatten_extras_value_for_search(v)}" for k, v in value.items())
    return str(value)

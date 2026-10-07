"""The one place the new inference settings' key names are spelled.

Everything that reads or writes a role, a route choice or the format marker
builds the key here, so a spelling cannot drift between the resolver, the
translation and (later) the migration. Pure: no imports from the store.
"""

from __future__ import annotations

ROLES: tuple[str, ...] = ("primary", "fast", "decision", "embedding")
GENERATIVE_ROLES: tuple[str, ...] = ("primary", "fast", "decision")

#: An unset role inherits from the one named here (Embedding inherits nothing).
INHERITS: dict[str, str] = {"fast": "primary", "decision": "fast"}

#: The `use_<route>` value that means "this route carries its own selection".
PIN = "model"
#: The fields of a selection.
PARTS: tuple[str, ...] = ("provider", "model", "preset")

FORMAT_KEY = "inference_format"
CURRENT_FORMAT = "2"


def role_key(role: str, part: str) -> str:
    return f"role_{role}_{part}"


def fallback_key(role: str, part: str) -> str:
    return f"role_{role}_fallback_{part}"


def use_key(route_key: str) -> str:
    return f"use_{route_key}"


def pin_key(route_key: str, part: str) -> str:
    return f"use_{route_key}_{part}"


def preset_key(route_key: str) -> str:
    return f"preset_{route_key}"

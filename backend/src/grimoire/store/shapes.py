"""Shape coercions for hand-editable JSON.

Module packs, sheet files and layouts are user-editable JSON, so every nested
container has to be treated as "the right shape, or absent". The
`x.get(k) if isinstance(x.get(k), dict) else {}` spelling of that was repeated
across the store; these two helpers say it once, look the key up once, and give
the type checker a concrete return type instead of `Any | ... | None`.
"""

from __future__ import annotations


def dict_at(data: dict, key: str) -> dict:
    """The dict at data[key] — {} when it is missing or another shape."""
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def list_at(data: dict, key: str) -> list:
    """The list at data[key] — [] when it is missing or another shape."""
    value = data.get(key)
    return value if isinstance(value, list) else []
